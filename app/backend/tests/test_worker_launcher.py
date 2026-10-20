from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from ai_job_search.config import Config
from ai_job_search.errors import AppError
from ai_job_search.launcher import available_socket, start_server
from ai_job_search.models import Decision
from ai_job_search.profile import ProfileService
from ai_job_search.runtime import minimal_environment
from ai_job_search.worker import WorkerManager
from ai_job_search.workflows import WorkflowService
from ai_job_search.workspace import Workspace


def wait_until(predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("The owned local process did not reach the expected state.")


def blocking_worker(root, resources, owner, stop, ready):
    if not ready.wait(15):
        return
    if os.name != "nt":
        os.setsid()
    environment = minimal_environment()
    os.environ.clear()
    os.environ.update(environment)
    workspace = Workspace(Path(root), Path(resources))
    workspace.store.heartbeat(owner, os.getpid())
    run = workspace.store.claim(owner)
    if not run:
        stop.wait(25)
        return
    workspace.store.stage(run["id"], "blocking_fixture", "Waiting in an owned test process.")
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    workspace.path(".app\\owned-child.pid").write_text(str(child.pid), encoding="ascii")
    try:
        while not stop.wait(0.1):
            workspace.store.heartbeat(owner, os.getpid())
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)


def process_alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259
    finally:
        kernel.CloseHandle(handle)


def test_real_worker_claims_and_persists_an_approval_without_paid_ai(workspace, profile):
    service = WorkflowService(workspace)
    current = ProfileService(workspace).read()
    run_id = service.enqueue(str(uuid4()), "profile_change", {
        "profile": profile.model_dump(mode="json"), "expected_revision": current.revision,
    })
    manager = WorkerManager(workspace)
    try:
        manager.start()
        process = manager.process
        wait_until(lambda: workspace.store.run(run_id)["status"] == "awaiting_user")
        run = workspace.store.run(run_id)
        assert process.is_alive() and run["attempt"] == 1
        assert not ProfileService(workspace).read().data.name
        approval = run["approvals"][0]
        service.decide(approval["id"], Decision(decision="approve", expected_revision=approval["revision"]))
        assert ProfileService(workspace).read().data.name == profile.name
        assert workspace.store.run(run_id)["status"] == "succeeded"
    finally:
        manager.stop()
    assert not process.is_alive()
    assert not manager.thread.is_alive()


def test_cancel_stops_owned_descendants_without_killing_an_unrelated_process(workspace, monkeypatch):
    monkeypatch.setattr("ai_job_search.worker.worker_main", blocking_worker)
    run_id = str(uuid4())
    workspace.store.enqueue(run_id, "blocking_fixture", {}, "test-policy")
    sentinel = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    manager = WorkerManager(workspace)
    try:
        manager.start()
        first_pid = manager.process.pid
        pid_path = workspace.path(".app\\owned-child.pid")
        wait_until(pid_path.exists)
        child_pid = int(pid_path.read_text(encoding="ascii"))
        assert process_alive(child_pid)
        with workspace.store.connect(write=True) as connection:
            connection.execute("UPDATE runs SET cancel_requested=1 WHERE id=?", (run_id,))
        wait_until(lambda: workspace.store.run(run_id)["status"] == "cancelled")
        wait_until(lambda: not process_alive(child_pid))
        wait_until(lambda: manager.process is not None and manager.process.pid != first_pid)
        assert sentinel.poll() is None
    finally:
        manager.stop()
        sentinel.terminate()
        sentinel.wait(timeout=5)


def test_live_http_bootstrap_event_replay_and_shutdown_with_an_open_stream(workspace):
    config = Config(workspace=workspace.root, resources=workspace.resources,
                    origin="http://127.0.0.1:0", start_worker=False)
    app, server, thread, listener = start_server(config)
    try:
        with httpx.Client(base_url=config.origin, trust_env=False, timeout=10) as client:
            assert client.get("/healthz").json()["ready"]
            response = client.post("/api/v1/session/bootstrap", json={"token": app.state.sessions.mint()},
                                   headers={"Origin": config.origin})
            assert response.status_code == 200
            assert client.get("/api/v1/profile").status_code == 200
            run_id = str(uuid4())
            app.state.workspace.store.enqueue(run_id, "fixture", {}, "fixture-policy")
            app.state.workspace.store.event(run_id, "fixture_progress", {"message": "Saved before delivery."})
            with client.stream("GET", f"/api/v1/runs/{run_id}/events",
                               headers={"Last-Event-ID": "1"}) as stream:
                lines = stream.iter_lines()
                assert next(lines) == "id: 2"
                assert "fixture_progress" in next(lines)
                app.state.stopping = True
                server.should_exit = True
                thread.join(timeout=10)
                assert not thread.is_alive()
    finally:
        app.state.stopping = True
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()


def test_failed_startup_closes_its_reserved_socket(workspace, monkeypatch):
    listener = available_socket(0)
    monkeypatch.setattr("ai_job_search.launcher.available_socket", lambda port: listener)

    def fail(*args, **kwargs):
        raise AppError("fixture_failure", "Expected startup failure.")

    monkeypatch.setattr("ai_job_search.launcher.create_app", fail)
    with pytest.raises(AppError, match="Expected startup failure"):
        start_server(Config(workspace=workspace.root))
    assert listener.fileno() == -1
