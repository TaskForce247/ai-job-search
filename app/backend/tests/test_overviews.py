from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ai_job_search.applications import ApplicationService
from ai_job_search.errors import AppError
from ai_job_search.jobs import JobService
from ai_job_search.models import ApplicationInput
from ai_job_search.storage import digest, encoded
from ai_job_search.watcher import ApplicationWatcher
from ai_job_search.workspace import JOBS_PATH, TRACKER_PATH


def test_overviews_reuse_metadata_without_rendering_documents_and_detect_external_edits(workspace, monkeypatch):
    service = ApplicationService(workspace)
    application = service.create_external("external", str(uuid4()), ApplicationInput(
        company="Synthetic Employer", role="Developer", applied_on="2020-01-01",
    ))
    manifest = workspace.path(service.manifest_location(application))
    original = ApplicationService._view
    calls = []

    def record(self, *args, **kwargs):
        calls.append(args)
        return original(self, *args, **kwargs)

    def no_document_reads(*args):
        raise AssertionError("An overview must not render or verify all application documents.")

    monkeypatch.setattr(ApplicationService, "_view", record)
    monkeypatch.setattr(service, "_document_views", no_document_reads)
    first = service.overview()[0]
    assert first["revision"] == application["revision"] and first["documents"] == {}
    assert len(calls) == 1
    assert ApplicationService(workspace).overview() == [first]
    assert len(calls) == 1
    data = json.loads(manifest.read_bytes())
    data["timeline"].append({"date": "2020-01-02", "status": "interview", "stage": "Phone", "notes": ""})
    manifest.write_bytes(encoded(data).encode())
    changed = service.overview()[0]
    assert len(calls) == 2
    assert changed["timeline"][-1]["status"] == "interview"
    assert changed["revision"] == ApplicationService(workspace).get(application["id"])["revision"]
    tracker = workspace.path(TRACKER_PATH)
    tracker.write_text(tracker.read_text(encoding="utf-8").replace(",applied,", ",rejected,"), encoding="utf-8")
    assert service.overview()[0]["status"] == "rejected"
    manifest.write_text("not valid JSON", encoding="utf-8")
    for _ in range(2):
        with pytest.raises(AppError) as caught:
            service.overview()
        assert caught.value.code == "invalid_application"
    manifest.write_bytes(b"\xff\xfe")
    for _ in range(2):
        with pytest.raises(AppError) as caught:
            service.overview()
        assert caught.value.code == "invalid_encoding"
    manifest.write_bytes(encoded(data).encode())
    assert service.overview()[0]["timeline"][-1]["status"] == "interview"


def test_legacy_outcome_edits_refresh_the_overview_and_removed_records_leave_the_cache(workspace):
    workspace.path(TRACKER_PATH).write_text(
        "date,company,role,status\n2020-01-01,Legacy Employer,Developer,rejected\n", encoding="utf-8",
    )
    service = ApplicationService(workspace)
    original = service.overview()[0]
    assert original["timeline"] == []
    outcome = workspace.path(str(Path(original["archive_dir"], "outcome.md")))
    outcome.parent.mkdir(parents=True)
    outcome.write_text("- [x] Phone interview\n", encoding="utf-8")
    assert service.overview()[0]["timeline"][0]["status"] == "interview"
    workspace.path(TRACKER_PATH).write_text("date,company,role,status\n", encoding="utf-8")
    assert service.overview() == []
    assert workspace.application_overviews == {}


@pytest.mark.skipif(os.name != "nt", reason="Native Windows change notifications.")
def test_idle_overviews_do_not_rescan_every_archive_and_ignore_operational_database_activity(workspace, monkeypatch):
    service = ApplicationService(workspace)
    application = service.create_external("external", str(uuid4()), ApplicationInput(
        company="Synthetic Employer", role="Developer", applied_on="2020-01-01",
    ))
    first = service.overview()
    original = workspace.stat_paths

    def no_bulk_scan(relatives):
        assert len(relatives) <= 1, "An unchanged overview rescanned the entire archive."
        return original(relatives)

    monkeypatch.setattr(workspace, "stat_paths", no_bulk_scan)
    workspace.store.save_setting("synthetic-test", {"changed": True})
    assert service.overview() == first
    assert first[0]["id"] == application["id"]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows change notifications.")
def test_a_single_archive_edit_does_not_rescan_unchanged_archives(workspace, monkeypatch):
    service = ApplicationService(workspace)
    applications = [
        service.create_external(f"external-{index}", str(uuid4()), ApplicationInput(
            company=f"Synthetic Employer {index}", role="Developer", applied_on="2020-01-01",
        )) for index in range(3)
    ]
    service.overview()
    path = workspace.path(service.manifest_location(applications[1]))
    data = json.loads(path.read_bytes())
    data["timeline"].append({"date": "2020-01-02", "status": "interview", "stage": "Phone", "notes": ""})
    path.write_bytes(encoded(data).encode())
    original = workspace.stat_paths

    def only_changed_archive(relatives):
        assert len(relatives) <= 2, "A single change rescanned unrelated archives."
        return original(relatives)

    monkeypatch.setattr(workspace, "stat_paths", only_changed_archive)
    result = service.overview()
    assert len(result) == 3 and result[1]["timeline"][-1]["status"] == "interview"


@pytest.mark.parametrize(("name", "action", "expected"), [
    ("job_search_tracker.csv", 3, True),
    ("DOCUMENTS\\applications\\one\\application.json", 3, True),
    (".app\\preparations\\one.json", 1, True),
    ("documents\\applications", 5, True),
    (".app", 3, False),
    (".app", 5, True),
    (".app\\state.sqlite3-wal", 3, False),
    (".app\\runs\\one\\policy.json", 1, False),
])
def test_monitor_ignores_runtime_noise_but_not_career_record_changes(name, action, expected):
    assert ApplicationWatcher.relevant(name, action) is expected


@pytest.mark.skipif(os.name != "nt", reason="Native Windows change notifications.")
def test_monitor_survives_the_request_thread_ending(workspace):
    created = []
    thread = threading.Thread(target=lambda: created.append(ApplicationWatcher(workspace.root)))
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive() and len(created) == 1
    watcher = created[0]
    try:
        workspace.path(TRACKER_PATH).write_text("date,company,role,status\n", encoding="utf-8")
        assert watcher.changed()
    finally:
        watcher.close()


@pytest.mark.parametrize("overflow_error", [False, True])
def test_monitor_overflow_requires_full_reconciliation_and_other_errors_surface(overflow_error):
    class NativeError(Exception):
        def __init__(self, code):
            self.winerror = code

    results = iter([NativeError(1022) if overflow_error else 0, NativeError(996), NativeError(5)])

    def native_result(*args):
        result = next(results)
        if isinstance(result, NativeError):
            raise result
        return result

    watcher = object.__new__(ApplicationWatcher)
    watcher.native = True
    watcher._file = SimpleNamespace(GetOverlappedResult=native_result)
    watcher._errors = SimpleNamespace(error=NativeError)
    watcher._codes = SimpleNamespace(ERROR_IO_INCOMPLETE=996, ERROR_NOTIFY_ENUM_DIR=1022)
    watcher._handle = watcher._overlap = None
    armed = []
    watcher._arm = lambda: armed.append(True)
    assert watcher.changed() and armed == [True]
    assert watcher.affects("documents\\applications\\any\\application.json")
    with pytest.raises(AppError) as caught:
        watcher.changed()
    assert caught.value.code == "workspace_monitor_unavailable"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows change notifications.")
def test_native_notifications_detect_edits_that_preserve_metadata(workspace):
    service = ApplicationService(workspace)
    application = service.create_external("external", str(uuid4()), ApplicationInput(
        company="Synthetic Employer", role="Developer", applied_on="2020-01-01",
    ))
    first = service.overview()[0]
    path = workspace.path(service.manifest_location(application))
    before = path.stat()
    data = path.read_bytes()
    changed = data.replace(b'"status":"applied"', b'"status":"pending"')
    assert changed != data and len(changed) == len(data)
    path.write_bytes(changed)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert service.overview()[0]["timeline"][0]["status"] == "pending"
    assert service.overview()[0]["revision"] != first["revision"]
    before = path.stat()
    path.write_bytes(b"x" * before.st_size)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    for _ in range(2):
        with pytest.raises(AppError) as caught:
            service.overview()
        assert caught.value.code == "invalid_application"


def test_closing_and_reopening_a_workspace_discards_the_read_model(workspace):
    service = ApplicationService(workspace)
    application = service.create_external("external", str(uuid4()), ApplicationInput(
        company="Synthetic Employer", role="Developer", applied_on="2020-01-01",
    ))
    service.overview()
    watcher = workspace.application_watcher
    workspace.close()
    assert workspace.application_watcher is None and workspace.overview_revision is None
    assert workspace.application_overviews == {} and workspace.overview_records == []
    path = workspace.path(service.manifest_location(application))
    data = json.loads(path.read_bytes())
    data["timeline"].append({"date": "2020-01-02", "status": "interview", "stage": "Phone", "notes": ""})
    path.write_bytes(encoded(data).encode())
    assert service.overview()[0]["timeline"][-1]["status"] == "interview"
    assert workspace.application_watcher is not watcher
    workspace.close()
    workspace.close()


def test_overviews_detect_new_and_changed_unpublished_preparations(workspace):
    service = ApplicationService(workspace)
    assert service.overview() == []
    application_id = str(uuid4())
    service.prepare("preparation", application_id, {"score": 80}, {
        "id": "synthetic-job", "title": "Developer", "company": "Synthetic Employer",
        "url": "", "description": "Synthetic offline posting.",
    })
    first = service.overview()[0]
    assert first["status"] == "preparing" and not first["published"]
    assert first["revision"] == service.get(application_id)["revision"]
    path = workspace.path(service.manifest_location(first))
    data = json.loads(path.read_bytes())
    data["deadline"] = "2030-01-01"
    path.write_bytes(encoded(data).encode())
    assert service.overview()[0]["deadline"] == "2030-01-01"
    path.unlink()
    assert service.overview() == []


def test_bulk_path_checks_validate_shared_parents_once_without_following_links(workspace, monkeypatch):
    directory = workspace.path("documents\\synthetic")
    directory.mkdir()
    calls = []
    original = os.scandir

    def record(path, *args, **kwargs):
        calls.append(Path(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr("ai_job_search.workspace.os.scandir", record)
    result = workspace.stat_paths(["documents\\synthetic\\first.json", "documents\\synthetic\\second.json"])
    assert all(info is None for info in result.values())
    assert calls.count(workspace.root) == 1 and calls.count(directory) == 1


@pytest.mark.skipif(os.name != "nt", reason="Native Windows junction boundary.")
def test_bulk_metadata_never_follows_a_junction(workspace, tmp_path):
    import _winapi

    outside = tmp_path / "Outside"
    outside.mkdir()
    (outside / "private.txt").write_text("Do not read through the junction.", encoding="utf-8")
    junction = workspace.root / "junction"
    _winapi.CreateJunction(str(outside), str(junction))
    try:
        assert junction.is_junction()
        with pytest.raises(AppError) as caught:
            workspace.stat_paths(["junction\\private.txt", "junction\\another.txt"])
        assert caught.value.code == "unsafe_path"
    finally:
        junction.rmdir()


def test_paginated_job_reads_only_hydrate_the_selected_page(api, monkeypatch):
    client, app = api
    records = {f"job-{index:04d}": {
        "company": "Synthetic Employer", "title": f"Developer {index}", "description": "Complete saved posting.",
        "location": "Remote", "status": "new", "saved": index % 2 == 0,
    } for index in range(1000)}
    app.state.workspace.path(JOBS_PATH).write_bytes(encoded({"seen": records}).encode())
    original = JobService.view
    calls = []

    def record(key, value):
        calls.append(key)
        return original(key, value)

    monkeypatch.setattr(JobService, "view", staticmethod(record))
    response = client.get("/api/v1/jobs?limit=3&saved=true&q=Developer")
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 500 and page["next_cursor"]
    assert len(calls) == 3
    assert all(item["revision"] == digest(records[item["id"]]) for item in page["items"])
    assert all(item["description"] == "Complete saved posting." for item in page["items"])
