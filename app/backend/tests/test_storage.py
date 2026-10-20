from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from conftest import RESOURCES

from ai_job_search.applications import ApplicationService
from ai_job_search.errors import AppError
from ai_job_search.models import ApplicationInput, OutcomeInput
from ai_job_search.storage import Store
from ai_job_search.workspace import Workspace


def test_commit_and_idempotent_replay(workspace):
    changes = {"first.txt": b"one", "second.txt": b"two"}
    expected = workspace.snapshot(list(changes))
    workspace.commit("operation-one", changes, expected)
    workspace.commit("operation-one", changes, expected)
    assert workspace.read("first.txt") == b"one"
    assert workspace.read("second.txt") == b"two"
    with workspace.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM file_transactions").fetchone()[0] == 1


def test_external_edit_is_not_overwritten(workspace):
    path = workspace.path("external.txt")
    path.write_bytes(b"initial")
    expected = workspace.snapshot(["external.txt"])
    path.write_bytes(b"external change")
    with pytest.raises(AppError, match="changed") as caught:
        workspace.commit("conflict", {"external.txt": b"replacement"}, expected)
    assert caught.value.code == "external_edit"
    assert path.read_bytes() == b"external change"


CRASH_PROGRAM = """
import os, sys
from pathlib import Path
from ai_job_search.workspace import Workspace
workspace = Workspace(Path(sys.argv[1]), Path(sys.argv[2]))
original = workspace._replace_entry
calls = 0
def interrupt(directory, entry, version):
    global calls
    original(directory, entry, version)
    calls += 1
    if version == "after" and calls == 1:
        os._exit(73)
workspace._replace_entry = interrupt
workspace.commit("interrupted-publication", {"a.txt": b"new A", "b.txt": b"new B"},
                 workspace.snapshot(["a.txt", "b.txt"]))
"""


def test_forced_process_exit_recovers_entire_publication(workspace):
    workspace.path("a.txt").write_bytes(b"old A")
    workspace.path("b.txt").write_bytes(b"old B")
    result = subprocess.run([sys.executable, "-c", CRASH_PROGRAM, str(workspace.root), str(RESOURCES)],
                            capture_output=True, timeout=30)
    assert result.returncode == 73, result.stderr.decode(errors="replace")
    assert workspace.read("a.txt") == b"new A"
    reopened = Workspace(workspace.root, RESOURCES)
    assert reopened.read("a.txt") == b"old A"
    assert reopened.read("b.txt") == b"old B"


def test_recovery_does_not_destroy_an_external_edit(workspace):
    workspace.path("a.txt").write_bytes(b"old A")
    workspace.path("b.txt").write_bytes(b"old B")
    result = subprocess.run([sys.executable, "-c", CRASH_PROGRAM, str(workspace.root), str(RESOURCES)],
                            capture_output=True, timeout=30)
    assert result.returncode == 73
    workspace.path("a.txt").write_bytes(b"external after crash")
    with pytest.raises(AppError) as caught:
        Workspace(workspace.root, RESOURCES)
    assert caught.value.code == "recovery_conflict"
    assert workspace.path("a.txt").read_bytes() == b"external after crash"


@pytest.mark.parametrize("path", ["..\\outside.txt", "C:\\outside.txt", "\\outside.txt", "nested\\..\\escape.txt", "bad:file"])
def test_unsafe_paths_are_rejected(workspace, path):
    with pytest.raises(AppError):
        workspace.path(path)


def test_symlink_boundary(workspace, tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    link = workspace.root / "linked"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links.")
    with pytest.raises(AppError) as caught:
        workspace.path(str(Path("linked", "secret.txt")))
    assert caught.value.code == "unsafe_path"


def test_transactional_queue_claim_has_one_owner(workspace):
    run_id = str(uuid4())
    workspace.store.enqueue(run_id, "test", {}, "policy")
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda owner: Store(workspace.store.path).claim(owner), map(str, range(8))))
    assert len([claim for claim in claims if claim is not None]) == 1


def test_worker_lease_recovery_and_event_order(workspace):
    run_id = str(uuid4())
    workspace.store.enqueue(run_id, "test", {}, "policy")
    workspace.store.claim("lost")
    with workspace.store.connect(write=True) as connection:
        connection.execute("UPDATE runs SET lease_until=0 WHERE id=?", (run_id,))
    workspace.store.recover_runs()
    assert workspace.store.run(run_id)["status"] == "interrupted"
    with workspace.store.connect() as connection:
        rows = connection.execute("SELECT sequence,type,payload FROM events WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
    assert [row["type"] for row in rows] == ["run.queued", "run.started", "run.interrupted"]
    assert len({row["sequence"] for row in rows}) == 3
    assert json.loads(rows[-1]["payload"])["retry_available"] is True


def test_stage_and_artifact_events_are_durable_and_recovery_closes_active_steps(workspace):
    store = workspace.store
    run_id = str(uuid4())
    store.enqueue(run_id, "test", {}, "policy")
    store.claim("test-worker")
    step = store.stage(run_id, "compile", "Compiling a synthetic document.")
    store.finish_step(step, {"compiled": True})
    artifact_id = store.register_artifact(".app\\fixture.txt", b"fixture", "text/plain", "fixture.txt", run_id=run_id)
    interrupted = store.stage(run_id, "verify", "Verifying the document.")
    store.recover_runs(all_running=True)
    with store.connect() as connection:
        rows = connection.execute("SELECT * FROM events WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
        recovered = connection.execute("SELECT status,completed_at FROM steps WHERE id=?", (interrupted,)).fetchone()
    assert [row["type"] for row in rows] == [
        "run.queued", "run.started", "stage.started", "stage.completed",
        "artifact.created", "stage.started", "run.interrupted",
    ]
    assert json.loads(rows[4]["payload"])["artifact_id"] == artifact_id
    assert recovered["status"] == "interrupted" and recovered["completed_at"]


def test_failed_validation_step_is_durable_without_stopping_repair(workspace):
    store = workspace.store
    run_id = str(uuid4())
    store.enqueue(run_id, "test", {}, "policy")
    store.claim("test-worker")
    rejected = store.stage(run_id, "evidence_preflight_1", "Checking evidence links.")
    store.finish_step(rejected, {"findings": [{"code": "missing_evidence"}]}, status="failed")
    assert store.run(run_id)["status"] == "running"
    repaired = store.stage(run_id, "repair_2", "Repairing evidence links.")
    store.finish_step(repaired, {"repaired": True})
    store.finish(run_id, {"verified": True})
    with store.connect() as connection:
        failed = connection.execute("SELECT status,output_json,completed_at FROM steps WHERE id=?", (rejected,)).fetchone()
        events = connection.execute(
            "SELECT type,stage FROM events WHERE run_id=? ORDER BY sequence", (run_id,),
        ).fetchall()
    assert failed["status"] == "failed" and failed["completed_at"]
    assert json.loads(failed["output_json"])["findings"][0]["code"] == "missing_evidence"
    assert ("stage.failed", "evidence_preflight_1") in [(item["type"], item["stage"]) for item in events]
    assert ("stage.completed", "evidence_preflight_1") not in [(item["type"], item["stage"]) for item in events]
    assert store.run(run_id)["status"] == "succeeded"


def test_readers_do_not_observe_a_half_published_tracker_and_archive(workspace, monkeypatch):
    writer = ApplicationService(workspace)
    application = writer.create_external("external", str(uuid4()), ApplicationInput(
        company="Synthetic Employer", role="Developer", applied_on="2020-01-01",
    ))
    reader = ApplicationService(Workspace(workspace.root, workspace.resources))
    paused, release, reading = threading.Event(), threading.Event(), threading.Event()
    original = workspace._replace_entry

    def pause(directory, entry, version):
        original(directory, entry, version)
        if version == "after" and not paused.is_set():
            paused.set()
            assert release.wait(5)

    monkeypatch.setattr(workspace, "_replace_entry", pause)

    def read():
        reading.set()
        return reader.get(application["id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        write = pool.submit(writer.outcome, "interview", application["id"], OutcomeInput(
            expected_revision=application["revision"], status="interview", occurred_on="2020-01-02",
        ))
        try:
            assert paused.wait(5)
            result = pool.submit(read)
            assert reading.wait(5)
            time.sleep(0.1)
            assert not result.done()
        finally:
            release.set()
        write.result(timeout=10)
        observed = result.result(timeout=10)
    assert observed["status"] == "interview"
    assert observed["timeline"][-1]["status"] == "interview"
