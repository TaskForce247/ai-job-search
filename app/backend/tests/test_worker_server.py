from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest

from ai_job_search.config import Config
from ai_job_search.errors import AppError
from ai_job_search.models import Decision
from ai_job_search.profile import ProfileService
from ai_job_search.runtime import minimal_environment
from ai_job_search.server import available_socket, selected_workspace, start_server
from ai_job_search.server import main as server_main
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
    monkeypatch.setattr("ai_job_search.server.available_socket", lambda port: listener)

    def fail(*args, **kwargs):
        raise AppError("fixture_failure", "Expected startup failure.")

    monkeypatch.setattr("ai_job_search.server.create_app", fail)
    with pytest.raises(AppError, match="Expected startup failure"):
        start_server(Config(workspace=workspace.root))
    assert listener.fileno() == -1


@pytest.mark.parametrize("frontend_port", [None, 5173])
def test_terminal_entrypoint_prints_a_working_one_use_link_and_stops_cleanly(workspace, frontend_port):
    arguments = [sys.executable, "-u", "-m", "ai_job_search", "--workspace", str(workspace.root), "--port", "0"]
    if frontend_port is not None:
        arguments.extend(["--frontend-port", str(frontend_port)])
    process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    lines = queue.Queue()

    def read_output():
        for line in process.stdout:
            lines.put(line.strip())

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    details = {}

    def launched():
        while not lines.empty():
            line = lines.get_nowait()
            if line.startswith("Backend ready: "):
                details["origin"] = line.removeprefix("Backend ready: ")
            if re.fullmatch(r"http://127\.0\.0\.1:\d+/#token=[\w-]+", line):
                details["url"] = line
        if process.poll() is not None:
            raise AssertionError("The terminal backend stopped before printing its login link.")
        return details if "origin" in details and "url" in details else None

    try:
        wait_until(launched)
        parsed = urlsplit(details["url"])
        assert parsed.hostname == "127.0.0.1"
        assert parsed.port == (frontend_port or urlsplit(details["origin"]).port)
        token = parse_qs(parsed.fragment)["token"][0]
        with httpx.Client(base_url=details["origin"], trust_env=False, timeout=10) as client:
            assert client.get("/api/v1/profile").status_code == 401
            response = client.post("/api/v1/session/bootstrap", json={"token": token},
                                   headers={"Origin": details["origin"]})
            assert response.status_code == 200
            assert client.get("/api/v1/profile").status_code == 200
            assert client.post("/api/v1/session/bootstrap", json={"token": token},
                               headers={"Origin": details["origin"]}).status_code == 401
            response = client.post("/api/v1/system/shutdown", json={}, headers={
                "Origin": details["origin"], "X-CSRF-Token": response.json()["csrf_token"],
                "Idempotency-Key": str(uuid4()),
            })
            assert response.status_code == 200
        assert process.wait(timeout=20) == 0
        assert "Traceback" not in process.stderr.read()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        reader.join(timeout=5)
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize("arguments", [
    ["--port", "-1"], ["--port", "65536"], ["--frontend-port", "0"], ["--frontend-port", "65536"],
])
def test_invalid_terminal_ports_fail_before_startup(monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", ["ai-job-search", *arguments])
    with pytest.raises(SystemExit) as error:
        server_main()
    assert error.value.code == 2


def test_busy_terminal_port_reports_an_actionable_error(workspace, monkeypatch, capsys):
    listener = available_socket(0)
    try:
        monkeypatch.setattr(sys, "argv", [
            "ai-job-search", "--workspace", str(workspace.root), "--port", str(listener.getsockname()[1]),
        ])
        with pytest.raises(SystemExit) as error:
            server_main()
        assert error.value.code == 1
        assert "Check the port and workspace permissions" in capsys.readouterr().err
    finally:
        listener.close()


def test_saved_workspace_selection_has_no_desktop_dependency(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_job_search.server.data_home", lambda: tmp_path)
    assert selected_workspace() == tmp_path / "workspace"
    selected = tmp_path / "My workspace"
    (tmp_path / "workspace.json").write_text(json.dumps({"workspace": str(selected)}), encoding="utf-8")
    assert selected_workspace() == selected
    (tmp_path / "workspace.json").write_text('{"workspace":"relative-folder"}', encoding="utf-8")
    with pytest.raises(AppError, match="use --workspace"):
        selected_workspace()
