from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import zipfile
from uuid import uuid4

import pytest

from ai_job_search.applications import ApplicationService
from ai_job_search.backups import BackupService, allowed_backup_path
from ai_job_search.errors import AppError
from ai_job_search.models import ApplicationInput, Decision, OutcomeInput
from ai_job_search.profile import ProfileService
from ai_job_search.storage import digest, encoded
from ai_job_search.workspace import TRACKER_PATH


def test_legacy_import_keeps_rows_columns_aliases_and_dates(workspace):
    content = (
        "date,company,sector,role,role_type,channel,status,contact_person,fit_rating,notes,cv_file,cover_letter_file,source,extra\n"
        "2020-01-01,Alpha,,Developer,,,no response,,82,old note,,,https://example.invalid,keep me\n"
        "2020-02-02,Beta,,Analyst,,,offer declined,,70,,,,,another value\n"
    )
    workspace.path(TRACKER_PATH).write_bytes(content.encode())
    service = ApplicationService(workspace)
    assert [record["status"] for record in service.all()] == ["no_response", "offer_declined"]
    proposal = service.migration_proposal()
    assert workspace.text(TRACKER_PATH) == content
    workspace.commit("migration", {TRACKER_PATH: proposal["files"][0]["after"].encode()}, proposal["expected"])
    fields, rows = service.tracker()
    assert fields[-3:] == ["deadline", "application_id", "archive_dir"]
    assert [row["extra"] for row in rows] == ["keep me", "another value"]
    assert [row["date"] for row in rows] == ["2020-01-01", "2020-02-02"]
    assert all(row["application_id"] for row in rows)
    assert [row["status"] for row in rows] == ["no response", "offer declined"]


def test_ambiguous_legacy_archive_stops_instead_of_sharing(workspace):
    workspace.path("documents\\applications\\alpha_developer").mkdir(parents=True)
    workspace.path(TRACKER_PATH).write_text(
        "date,company,role,status\n2020-01-01,Alpha,Developer,rejected\n2021-01-01,Alpha,Developer,applied\n",
        encoding="utf-8",
    )
    with pytest.raises(AppError) as caught:
        ApplicationService(workspace).migration_proposal()
    assert caught.value.code == "ambiguous_legacy_archive"


def test_archive_link_cannot_redirect_writes_into_policy(workspace):
    workspace.path(TRACKER_PATH).write_text(
        "date,company,role,status,application_id,archive_dir\n"
        f"2020-01-01,Alpha,Developer,applied,{uuid4()},.claude\\commands\n",
        encoding="utf-8",
    )
    with pytest.raises(AppError) as caught:
        ApplicationService(workspace).all()
    assert caught.value.code == "unsafe_archive_link"


def test_csv_unsafe_notes_are_rejected(api):
    client, _ = api
    response = client.post("/api/v1/applications", json={
        "company": "Example", "role": "External", "applied_on": "2020-01-01",
    })
    application = response.json()
    client.headers["Idempotency-Key"] = str(uuid4())
    response = client.post(f"/api/v1/applications/{application['id']}/outcomes", json={
        "expected_revision": application["revision"], "status": "interview", "occurred_on": "2020-01-10",
        "notes": "contains, a comma",
    })
    assert response.status_code == 400 and response.json()["error"]["code"] == "unsafe_tracker_note"


def test_historical_funnel_includes_rejected_interview_not_drafted(api):
    client, app = api
    response = client.post("/api/v1/applications", json={
        "company": "Example", "role": "External", "applied_on": "2020-01-01",
    })
    application = response.json()
    service = ApplicationService(app.state.workspace)
    interview = service.outcome("interview", application["id"], OutcomeInput(
        expected_revision=application["revision"], status="interview", occurred_on="2020-01-10", stage="Phone screen",
    ))
    service.outcome("rejection", application["id"], OutcomeInput(
        expected_revision=interview["revision"], status="rejected", occurred_on="2020-01-15",
    ))
    result = client.get("/api/v1/dashboard").json()
    assert result["counts"]["submitted"] == 1
    assert result["counts"]["interview"] == 1
    assert result["counts"]["active"] == 0


def test_plain_backup_and_encrypted_backup_restore_consistent_career_data(confirmed):
    service = BackupService(confirmed)
    before = confirmed.text(".claude\\skills\\job-application-assistant\\01-candidate-profile.md")
    artifact_id = service.create("plain-backup")
    artifact = confirmed.store.artifact(artifact_id)
    archive = confirmed.read(artifact["relative_path"])
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        assert ".app/state.sqlite3" in zipped.namelist()
        assert not any("api_key" in name or ".env" in name for name in zipped.namelist())
    encrypted_id = service.create("encrypted-backup", "synthetic passphrase only")
    encrypted = confirmed.read(confirmed.store.artifact(encrypted_id)["relative_path"])
    with pytest.raises(AppError) as caught:
        service.inspect(encrypted, "incorrect passphrase")
    assert caught.value.code == "backup_decryption_failed"
    proposal = service.inspect(encrypted, "synthetic passphrase only")
    path = confirmed.path(".claude\\skills\\job-application-assistant\\01-candidate-profile.md")
    path.write_text(before + "\n## Added after backup\nSynthetic addition.\n", encoding="utf-8")
    # A changed restore target requires a fresh reviewed proposal.
    with pytest.raises(AppError) as caught:
        service.restore("stale-restore", proposal)
    assert caught.value.code == "external_edit"
    proposal = service.inspect(encrypted, "synthetic passphrase only")
    service.restore("approved-restore", proposal)
    assert path.read_text(encoding="utf-8").replace("\r\n", "\n") == before.replace("\r\n", "\n")
    assert not confirmed.path(".app\\restore-pending.json").exists()


@pytest.mark.parametrize("name", [
    "../outside.txt", "/outside.txt", "C:\\outside.txt", "documents/../../outside.txt",
    ".claude/settings.json", "app/backend/evil.py", ".app/runs/one/runtime-stage/config.json",
])
def test_restore_rejects_unsafe_or_executable_paths(confirmed, name):
    assert not allowed_backup_path(name)
    payload = b"untrusted"
    with io.BytesIO() as buffer:
        with zipfile.ZipFile(buffer, "w") as zipped:
            zipped.writestr(name, payload)
            zipped.writestr("manifest.json", encoded({"format": 1, "files": {name: digest(payload)}}))
        with pytest.raises(AppError):
            BackupService(confirmed).inspect(buffer.getvalue())


def test_backup_integrity_mismatch_is_explicit(confirmed):
    service = BackupService(confirmed)
    artifact = confirmed.store.artifact(service.create("backup"))
    data = confirmed.read(artifact["relative_path"])
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as original, zipfile.ZipFile(output, "w") as changed:
        for name in original.namelist():
            content = original.read(name)
            if name == "CLAUDE.md":
                content += b"\nUnreviewed change."
            changed.writestr(name, content)
    with pytest.raises(AppError) as caught:
        service.inspect(output.getvalue())
    assert caught.value.code == "backup_integrity_failed"


def test_restore_removes_later_career_files_but_preserves_unrelated_files(confirmed):
    service = BackupService(confirmed)
    artifact = confirmed.store.artifact(service.create("snapshot"))
    later = confirmed.path("cv\\later-draft.tex")
    later.write_text("Created after backup", encoding="utf-8")
    unrelated = confirmed.path("unrelated.txt")
    unrelated.write_text("Keep this", encoding="utf-8")
    proposal = service.inspect(confirmed.read(artifact["relative_path"]))
    assert {"path": "cv/later-draft.tex", "action": "delete"} in proposal["files"]
    service.restore("restore-snapshot", proposal)
    assert not later.exists()
    assert unrelated.read_text(encoding="utf-8") == "Keep this"


def test_new_career_file_after_restore_review_requires_new_review(confirmed):
    service = BackupService(confirmed)
    artifact = confirmed.store.artifact(service.create("snapshot"))
    proposal = service.inspect(confirmed.read(artifact["relative_path"]))
    confirmed.path("cv\\new.tex").write_bytes(b"New external file")
    with pytest.raises(AppError) as caught:
        service.restore("stale-file-list", proposal)
    assert caught.value.code == "external_edit"
    assert confirmed.read("cv\\new.tex") == b"New external file"


def test_restore_rejects_case_collisions_before_extracting(confirmed):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("cv/duplicate.tex", b"one")
        archive.writestr("cv/Duplicate.tex", b"two")
    with pytest.raises(AppError) as caught:
        BackupService(confirmed).inspect(output.getvalue())
    assert caught.value.code == "invalid_backup"


@pytest.mark.parametrize("legacy", [False, True])
def test_restore_checks_schema_and_accepts_backups_before_ai_traces(confirmed, tmp_path, legacy):
    service = BackupService(confirmed)
    artifact = confirmed.store.artifact(service.create("snapshot"))
    database = tmp_path / "modified.sqlite3"
    with zipfile.ZipFile(io.BytesIO(confirmed.read(artifact["relative_path"]))) as archive:
        files = {name: archive.read(name) for name in archive.namelist() if name != "manifest.json"}
        manifest = json.loads(archive.read("manifest.json"))
    database.write_bytes(files[".app/state.sqlite3"])
    connection = sqlite3.connect(database)
    try:
        if legacy:
            connection.execute("DROP TABLE ai_traces")
        else:
            connection.execute("CREATE TRIGGER unexpected AFTER INSERT ON runs BEGIN DELETE FROM settings; END")
        connection.commit()
    finally:
        connection.close()
    files[".app/state.sqlite3"] = database.read_bytes()
    manifest["files"] = {name: digest(content) for name, content in files.items()}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("manifest.json", encoded(manifest))
    if legacy:
        assert service.inspect(output.getvalue())["confirmation"] == "RESTORE"
    else:
        with pytest.raises(AppError) as caught:
            service.inspect(output.getvalue())
        assert caught.value.code == "unsupported_backup"


def test_external_tracker_edit_during_outcome_is_not_lost(confirmed, monkeypatch):
    service = ApplicationService(confirmed)
    application = service.create_external("external", str(uuid4()), ApplicationInput(
        company="Example", role="Developer", applied_on="2020-01-01",
    ))
    original = service.outcome_text
    extra = "2020-02-02,Second Employer,,,,,applied,,,,,,,,,\n"

    def concurrent_edit(*args, **kwargs):
        confirmed.path(TRACKER_PATH).write_text(confirmed.text(TRACKER_PATH) + extra, encoding="utf-8")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "outcome_text", concurrent_edit)
    with pytest.raises(AppError) as caught:
        service.outcome("record-stage", application["id"], OutcomeInput(
            expected_revision=application["revision"], status="interview", occurred_on="2020-01-10",
        ))
    assert caught.value.code == "external_edit"
    assert "Second Employer" in confirmed.text(TRACKER_PATH)


def test_restore_retains_safety_backup_receipt_and_monotonic_events(confirmed):
    from conftest import FixtureRuntime, run_operation

    from ai_job_search.workflows import WorkflowService
    service = BackupService(confirmed)
    profile = ProfileService(confirmed).read()
    run = run_operation(confirmed, FixtureRuntime(), "profile_change", {
        "profile": profile.data.model_dump(mode="json"), "expected_revision": profile.revision,
    })
    artifact = confirmed.store.artifact(service.create("snapshot"))
    proposal = service.inspect(confirmed.read(artifact["relative_path"]))
    restore_id = str(uuid4())
    path = f".app\\runs\\{restore_id}\\restore-proposal.json"
    confirmed._durable_write(confirmed.path(path), encoded(proposal).encode())
    workflow = WorkflowService(confirmed, FixtureRuntime())
    workflow.enqueue(restore_id, "restore", {"proposal_path": path})
    confirmed.store.claim("fixture")
    asyncio.run(workflow.execute(restore_id))
    approval = confirmed.store.run(restore_id)["approvals"][0]
    floor = confirmed.store.last_sequence()
    result = workflow.decide(approval["id"], Decision(
        decision="approve", expected_revision=approval["revision"], confirmation="RESTORE",
    ))
    safety = confirmed.store.artifact(result["backup_artifact_id"])
    assert confirmed.read(safety["relative_path"]).startswith(b"PK")
    assert confirmed.store.run(restore_id)["status"] == "succeeded"
    assert confirmed.store.last_sequence() > floor
    assert confirmed.store.run(run["id"])["status"] == "interrupted"
    assert confirmed.store.approval(run["approvals"][0]["id"])["status"] == "invalidated"
