from __future__ import annotations

import asyncio
import multiprocessing
import os
import threading
import time
from pathlib import Path
from uuid import uuid4

from .processes import ProcessJob
from .readiness import tool_directories
from .runtime import minimal_environment
from .workflows import WorkflowService
from .workspace import Workspace


def worker_main(root: str, resources: str, owner: str, stop, ready) -> None:
    if not ready.wait(timeout=15):
        return
    if os.name != "nt":
        os.setsid()
    environment = minimal_environment()
    os.environ.clear()
    os.environ.update(environment)
    os.environ["PATH"] = os.pathsep.join([*(str(path) for path in tool_directories(Path(resources))), os.environ.get("PATH", "")])
    workspace = Workspace(Path(root), Path(resources))
    store = workspace.store
    service = WorkflowService(workspace)

    def heartbeat() -> None:
        while not stop.wait(2):
            store.heartbeat(owner, os.getpid())

    pulse = threading.Thread(target=heartbeat, daemon=True, name="worker-heartbeat")
    pulse.start()
    store.heartbeat(owner, os.getpid())
    while not stop.is_set():
        run = store.claim(owner)
        if not run:
            stop.wait(0.25)
            continue
        asyncio.run(service.execute(run["id"]))


class WorkerManager:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.owner = str(uuid4())
        self.context = multiprocessing.get_context("spawn")
        self._stop = threading.Event()
        self._mutex = threading.RLock()
        self.process = None
        self.job = None
        self.child_stop = None
        self.thread = None

    def start(self) -> None:
        self.workspace.store.recover_runs(all_running=True)
        self._stop.clear()
        self._spawn()
        self.thread = threading.Thread(target=self._supervise, name="worker-supervisor", daemon=True)
        self.thread.start()

    def _spawn(self) -> None:
        with self._mutex:
            self.child_stop = self.context.Event()
            ready = self.context.Event()
            process = self.context.Process(
                target=worker_main,
                args=(str(self.workspace.root), str(self.workspace.resources), self.owner, self.child_stop, ready),
                daemon=False, name="AIJobSearchWorker",
            )
            job = ProcessJob()
            process.start()
            try:
                job.assign(process.pid)
            except BaseException:
                process.terminate()
                process.join(timeout=5)
                job.close()
                raise
            self.process, self.job = process, job
            ready.set()

    def _supervise(self) -> None:
        while not self._stop.wait(0.5):
            with self._mutex:
                if self.process is None:
                    return
                with self.workspace.store.connect() as connection:
                    run = connection.execute(
                        "SELECT id,cancel_requested,lease_until FROM runs WHERE owner=? AND status='running'",
                        (self.owner,),
                    ).fetchone()
                cancelled = bool(run and run["cancel_requested"])
                stale = bool(run and run["lease_until"] and run["lease_until"] < time.time())
                if cancelled or stale or not self.process.is_alive():
                    self.job.terminate()
                    self.process.join(timeout=5)
                    self.job.close()
                    if cancelled:
                        self.workspace.store.finish(run["id"], status="cancelled",
                                                    error="Cancelled. Completed files and saved drafts were retained.",
                                                    code="cancelled")
                    with self.workspace.lock():
                        self.workspace.recover()
                    self.workspace.store.recover_runs(all_running=True)
                    if not self._stop.is_set():
                        self._spawn()

    def stop(self) -> None:
        self._stop.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)
        with self._mutex:
            if self.process:
                self.child_stop.set()
                self.process.join(timeout=3)
                if self.process.is_alive():
                    self.job.terminate()
                    self.process.join(timeout=5)
                self.job.close()
                self.process = None
                with self.workspace.lock():
                    self.workspace.recover()
                self.workspace.store.recover_runs(all_running=True)
