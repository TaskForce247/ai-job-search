from __future__ import annotations

import asyncio
from datetime import date
from uuid import uuid4

import pytest
from conftest import FixtureRuntime, run_operation
from test_api import POSTING

from ai_job_search.applications import ApplicationService
from ai_job_search.documents import DocumentService
from ai_job_search.errors import AppError
from ai_job_search.jobs import JobService
from ai_job_search.models import Decision, DocumentContent, DraftPair, OutcomeInput, Review, SubmissionInput
from ai_job_search.profile import ProfileService
from ai_job_search.storage import encoded
from ai_job_search.workflows import WorkflowService


def imported_job(workspace):
    return JobService(workspace).ingest("posting", [{
        **POSTING, "description": POSTING["text"], "url": "", "portal": "manual", "source": "pasted",
    }])[0]


def approved_application(workspace, runtime):
    job_id = imported_job(workspace)
    run = run_operation(workspace, runtime, "evaluate", {"job_id": job_id})
    assert run["status"] == "awaiting_user", run
    approval = run["approvals"][0]
    response = WorkflowService(workspace, runtime).decide(
        approval["id"], Decision(decision="prepare", expected_revision=approval["revision"]),
    )
    return response, run, approval


def finish_preparation(workspace, runtime, response):
    workspace.store.claim("fixture")
    asyncio.run(WorkflowService(workspace, runtime).execute(response["preparation_run_id"]))
    return workspace.store.run(response["preparation_run_id"])


def timed_out_preparation(workspace, profile):
    profiles = ProfileService(workspace)
    profiles.apply("confirm-profile", profiles.propose(profile, profiles.read().revision))

    class ReviewTimeoutRuntime(FixtureRuntime):
        async def generate(self, run_id, stage, *args, **kwargs):
            if stage == "independent_review_1":
                raise AppError("ai_timeout", "Synthetic independent review timeout.", 504)
            return await super().generate(run_id, stage, *args, **kwargs)

    runtime = ReviewTimeoutRuntime()
    response, _, _ = approved_application(workspace, runtime)
    failed = finish_preparation(workspace, runtime, response)
    assert failed["status"] == "failed" and failed["error_code"] == "ai_timeout"
    return response, failed


class EvidenceRuntime(FixtureRuntime):
    def __init__(self, *, bad_draft=False, bad_reviews=0, bad_repairs=False):
        super().__init__()
        self.bad_draft = bad_draft
        self.bad_reviews = bad_reviews
        self.bad_repairs = bad_repairs

    async def generate(self, run_id, stage, data, *args, **kwargs):
        result = await super().generate(run_id, stage, data, *args, **kwargs)
        if stage == "draft" and self.bad_draft:
            result.cv.blocks[1].evidence_ids = []
        if stage.startswith("independent_review_") and self.bad_reviews:
            self.bad_reviews -= 1
            result.revised.cv.blocks[1].evidence_ids = []
        if stage.startswith("repair_") and not self.bad_repairs:
            for content in (result.cv, result.cover_letter):
                for block in content.blocks:
                    if block.kind in ("entry", "bullet") and not block.evidence_ids:
                        block.evidence_ids = [data["evidence"][0]["id"]]
                    for claim in block.bullets:
                        if not claim.evidence_ids:
                            claim.evidence_ids = [data["evidence"][0]["id"]]
        return result


def test_repairs_evidence_before_independent_review(confirmed, fixture_documents):
    runtime = EvidenceRuntime(bad_draft=True)
    response, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, response)
    assert result["status"] == "succeeded", result
    stages = [call["stage"] for call in runtime.calls]
    assert "independent_review_1" not in stages
    assert stages.index("repair_2") < stages.index("independent_review_2") < stages.index("grounding_2")
    repair = next(call for call in runtime.calls if call["stage"] == "repair_2")
    assert repair["data"]["actual_verification_findings"][0]["code"] == "missing_evidence"
    with confirmed.store.connect() as connection:
        step = connection.execute(
            "SELECT status,output_json FROM steps WHERE run_id=? AND stage='evidence_preflight_1'",
            (result["id"],),
        ).fetchone()
    assert step["status"] == "failed" and "missing_evidence" in step["output_json"]
    for call in runtime.calls:
        if call["stage"].startswith("independent_review_"):
            DocumentService.evidence_check(
                DraftPair.model_validate(call["data"]["drafts"]),
                {item["id"] for item in call["data"]["evidence"]},
            )


def test_repairs_legacy_saved_evidence_before_retry_review(api, profile, fixture_documents):
    client, app = api
    workspace = app.state.workspace
    response, failed = timed_out_preparation(workspace, profile)
    applications = ApplicationService(workspace)
    document = applications.get(response["application_id"])["documents"]["cv"]
    content = DocumentContent.model_validate(document["content"])
    content.blocks[1].evidence_ids = []
    applications.save_content("legacy-evidence", response["application_id"], "cv", content, document["revision"])
    queued = client.post(f"/api/v1/runs/{failed['id']}/retry", json={})
    assert queued.status_code == 202, queued.text
    runtime = EvidenceRuntime()
    result = finish_preparation(workspace, runtime, {"preparation_run_id": queued.json()["run_id"]})
    assert result["status"] == "succeeded", result
    stages = [call["stage"] for call in runtime.calls]
    assert "draft" not in stages and "independent_review_1" not in stages
    assert stages.index("repair_2") < stages.index("independent_review_2")


def test_reviewer_missing_evidence_is_repaired_and_independently_reviewed_again(confirmed, fixture_documents):
    runtime = EvidenceRuntime(bad_reviews=1)
    response, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, response)
    assert result["status"] == "succeeded", result
    stages = [call["stage"] for call in runtime.calls]
    assert "grounding_1" not in stages
    assert stages.index("independent_review_1") < stages.index("repair_2") < stages.index("independent_review_2")
    assert "grounding_2" in stages
    with confirmed.store.connect() as connection:
        assert connection.execute(
            "SELECT status FROM steps WHERE run_id=? AND stage='evidence_review_1'", (result["id"],),
        ).fetchone()["status"] == "failed"
        assert connection.execute(
            "SELECT COUNT(*) FROM steps WHERE run_id=? AND stage LIKE 'compile_%_1'", (result["id"],),
        ).fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["draft", "review"])
def test_invalid_evidence_stops_after_bounded_repairs_without_publication(confirmed, fixture_documents, failure):
    runtime = EvidenceRuntime(bad_draft=failure == "draft", bad_reviews=3 if failure == "review" else 0,
                              bad_repairs=failure == "draft")
    response, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, response)
    assert result["status"] == "awaiting_user" and result["error_code"] is None, result
    assert result["approvals"][0]["kind"] == "document_review"
    assert result["result"]["issues"][0]["kind"] == "evidence"
    stages = [call["stage"] for call in runtime.calls]
    assert len([stage for stage in stages if stage.startswith("repair_")]) == 2
    assert len([stage for stage in stages if stage.startswith("independent_review_")]) == (3 if failure == "review" else 0)
    assert not any(stage.startswith(("grounding_", "visual_")) for stage in stages)
    applications = ApplicationService(confirmed)
    saved = applications.get(response["application_id"])
    assert saved["documents"] and not saved["published"] and saved["status"] == "preparing"
    assert not applications.tracker()[1]
    with confirmed.store.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM steps WHERE run_id=? AND stage LIKE 'compile_%'", (result["id"],),
        ).fetchone()[0] == 0


def test_document_retry_does_not_replay_a_legacy_rejected_review(api, profile, fixture_documents):
    client, app = api
    workspace = app.state.workspace
    response, failed = timed_out_preparation(workspace, profile)
    application = ApplicationService(workspace).get(response["application_id"])
    pair = DraftPair.model_validate({kind: item["content"] for kind, item in application["documents"].items()})
    pair.cv.blocks[1].evidence_ids = []
    with workspace.store.connect() as connection:
        legacy_step = connection.execute(
            "SELECT id FROM steps WHERE run_id=? AND stage='independent_review_1'", (failed["id"],),
        ).fetchone()["id"]
    workspace.store.finish_step(legacy_step, Review(revised=pair).model_dump(mode="json"))
    queued = client.post(f"/api/v1/runs/{failed['id']}/retry", json={})
    assert queued.status_code == 202, queued.text
    runtime = FixtureRuntime()
    result = finish_preparation(workspace, runtime, {"preparation_run_id": queued.json()["run_id"]})
    assert result["status"] == "succeeded", result
    assert any(call["stage"] == "independent_review_1" for call in runtime.calls)
    with workspace.store.connect() as connection:
        hashes = connection.execute(
            "SELECT input_hash FROM steps WHERE run_id IN (?,?) AND stage='independent_review_1'",
            (failed["id"], result["id"]),
        ).fetchall()
    assert len(hashes) == 2 and hashes[0]["input_hash"] == hashes[1]["input_hash"]


def test_repaired_drafts_survive_a_later_independent_review_timeout(confirmed, fixture_documents):
    class RepairThenTimeout(EvidenceRuntime):
        async def generate(self, run_id, stage, *args, **kwargs):
            if stage == "independent_review_2":
                raise AppError("ai_timeout", "Synthetic review timeout after evidence repair.", 504)
            return await super().generate(run_id, stage, *args, **kwargs)

    runtime = RepairThenTimeout(bad_draft=True)
    response, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, response)
    assert result["status"] == "failed" and result["error_code"] == "ai_timeout", result
    application = ApplicationService(confirmed).get(response["application_id"])
    assert application["documents"]["cv"]["content"]["blocks"][1]["evidence_ids"]
    assert application["documents"]["cv"]["stale"] and not application["published"]
    assert application["last_run_id"] == result["id"]


def test_evidence_repair_does_not_overwrite_concurrent_document_edits(confirmed, fixture_documents):
    class ConcurrentRepair(EvidenceRuntime):
        async def generate(self, run_id, stage, *args, **kwargs):
            result = await super().generate(run_id, stage, *args, **kwargs)
            if stage == "repair_2":
                application_id = confirmed.store.run(run_id)["input"]["application_id"]
                applications = ApplicationService(confirmed)
                document = applications.get(application_id)["documents"]["cv"]
                content = DocumentContent.model_validate(document["content"])
                content.blocks[0].heading = "Concurrent manual correction"
                applications.save_content("concurrent-repair-edit", application_id, "cv", content, document["revision"])
            return result

    runtime = ConcurrentRepair(bad_draft=True)
    response, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, response)
    assert result["status"] == "failed" and result["error_code"] == "external_edit", result
    assert not any(call["stage"].startswith("independent_review_") for call in runtime.calls)
    saved = ApplicationService(confirmed).get(response["application_id"])
    assert saved["documents"]["cv"]["content"]["blocks"][0]["heading"] == "Concurrent manual correction"


@pytest.mark.parametrize("status", ["failed", "cancelled", "succeeded", "queued"])
@pytest.mark.parametrize("published", [False, True])
def test_latest_document_attempt_is_separate_from_draft_provenance(api, profile, fixture_documents, status, published):
    client, app = api
    workspace = app.state.workspace
    response, _ = timed_out_preparation(workspace, profile)
    applications = ApplicationService(workspace)
    if published:
        current = applications.get(response["application_id"])
        prepared = run_operation(workspace, FixtureRuntime(), "documents", {
            "application_id": current["id"], "expected_revision": current["revision"],
        })
        assert prepared["status"] == "succeeded", prepared
    current = applications.get(response["application_id"])
    manifest_path = applications.manifest_location(current)
    manifest = workspace.read(manifest_path)
    attempt_id = str(uuid4())
    workspace.store.enqueue(attempt_id, "documents", {"application_id": current["id"]}, "test-policy")
    if status != "queued":
        workspace.store.finish(attempt_id, status=status)
    workspace.store.enqueue(str(uuid4()), "documents", {"application_id": str(uuid4())}, "test-policy")
    workspace.store.enqueue(str(uuid4()), "evaluate", {"application_id": current["id"]}, "test-policy")
    result = client.get(f"/api/v1/applications/{current['id']}")
    assert result.status_code == 200, result.text
    view = result.json()
    assert view["latest_run_id"] == attempt_id
    assert view["last_run_id"] == current["last_run_id"]
    assert view["revision"] == current["revision"]
    assert workspace.read(manifest_path) == manifest


def test_latest_document_attempt_is_found_beyond_the_activity_page(api, profile, fixture_documents):
    client, app = api
    workspace = app.state.workspace
    response, failed = timed_out_preparation(workspace, profile)
    with workspace.store.connect(write=True) as connection:
        for index in range(55):
            connection.execute(
                "INSERT INTO runs(id,kind,status,input_json,policy_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (str(uuid4()), "documents", "succeeded", encoded({"application_id": str(uuid4())}),
                 "test-policy", f"2099-01-01T00:00:{index:02d}.000+00:00", "2099-01-01T00:01:00.000+00:00"),
            )
    recent = client.get("/api/v1/runs?limit=50").json()
    assert len(recent["items"]) == 50 and failed["id"] not in {item["id"] for item in recent["items"]}
    view = client.get(f"/api/v1/applications/{response['application_id']}").json()
    assert view["latest_run_id"] == failed["id"]


@pytest.mark.parametrize("retry_stale_run", [False, True])
@pytest.mark.parametrize("edit_before_retry", [False, True])
def test_document_retry_binds_current_saved_drafts(api, profile, fixture_documents, retry_stale_run, edit_before_retry):
    client, app = api
    workspace = app.state.workspace
    response, failed = timed_out_preparation(workspace, profile)
    applications = ApplicationService(workspace)
    source = failed
    if retry_stale_run:
        source = run_operation(workspace, FixtureRuntime(), "documents", {**failed["input"], "_retry_of": failed["id"]})
        assert source["status"] == "failed" and source["error_code"] == "revision_conflict"
    if edit_before_retry:
        document = applications.get(response["application_id"])["documents"]["cv"]
        content = DocumentContent.model_validate(document["content"])
        content.blocks[0].text = "Saved correction before retry"
        applications.save_content("saved-correction", response["application_id"], "cv", content, document["revision"])
    application = applications.get(response["application_id"])
    saved_drafts = {kind: document["content"] for kind, document in application["documents"].items()}
    original_inputs = source["input"].copy()
    assert application["revision"] != original_inputs["expected_revision"]
    queued = client.post(f"/api/v1/runs/{source['id']}/retry", json={})
    assert queued.status_code == 202, queued.text
    assert client.post(f"/api/v1/runs/{source['id']}/retry", json={}).json() == queued.json()
    retry = workspace.store.run(queued.json()["run_id"])
    assert retry["input"]["expected_revision"] == application["revision"]
    assert retry["input"]["_retry_of"] == source["id"]
    assert retry["policy_hash"] == source["policy_hash"]
    assert workspace.store.run(source["id"])["input"] == original_inputs
    assert applications.get(application["id"])["revision"] == application["revision"]
    runtime = FixtureRuntime()
    result = finish_preparation(workspace, runtime, {"preparation_run_id": retry["id"]})
    assert result["status"] == "succeeded", result
    assert not any(call["stage"] == "draft" for call in runtime.calls)
    review = next(call for call in runtime.calls if call["stage"] == "independent_review_1")
    assert review["data"]["drafts"] == saved_drafts
    assert {kind: document["content"] for kind, document in applications.get(application["id"])["documents"].items()} == saved_drafts


@pytest.mark.parametrize("lose_receipt", [False, True])
def test_document_retry_still_rejects_edits_after_queueing(api, profile, fixture_documents, lose_receipt):
    client, app = api
    workspace = app.state.workspace
    response, failed = timed_out_preparation(workspace, profile)
    applications = ApplicationService(workspace)
    application = applications.get(response["application_id"])
    queued = client.post(f"/api/v1/runs/{failed['id']}/retry", json={})
    assert queued.status_code == 202, queued.text
    retry_id = queued.json()["run_id"]
    document = application["documents"]["cv"]
    content = DocumentContent.model_validate(document["content"])
    content.blocks[0].text = "Correction after retry was queued"
    applications.save_content("concurrent-correction", application["id"], "cv", content, document["revision"])
    if lose_receipt:
        with workspace.store.connect(write=True) as connection:
            connection.execute("DELETE FROM idempotency")
    repeated = client.post(f"/api/v1/runs/{failed['id']}/retry", json={})
    assert repeated.status_code == 202, repeated.text
    assert repeated.json() == queued.json()
    assert workspace.store.run(retry_id)["input"]["expected_revision"] == application["revision"]
    runtime = FixtureRuntime()
    result = finish_preparation(workspace, runtime, {"preparation_run_id": retry_id})
    assert result["status"] == "failed" and result["error_code"] == "revision_conflict"
    assert runtime.calls == []
    assert applications.get(application["id"])["documents"]["cv"]["content"] == content.model_dump(mode="json")


@pytest.mark.parametrize("change", ["profile", "posting"])
def test_document_retry_still_requires_current_evaluation(api, profile, fixture_documents, change):
    client, app = api
    workspace = app.state.workspace
    response, failed = timed_out_preparation(workspace, profile)
    applications = ApplicationService(workspace)
    application = applications.get(response["application_id"])
    if change == "profile":
        profiles = ProfileService(workspace)
        current = profiles.read()
        changed = current.data.model_copy(deep=True)
        changed.email = "corrected@example.invalid"
        profiles.apply("corrected-contact", profiles.propose(changed, current.revision))
    else:
        jobs = JobService(workspace)
        job = jobs.get(application["job_id"])
        jobs.update("changed-posting", job["id"], {"description": job["description"] + "\nChanged requirements."}, job["revision"])
    queued = client.post(f"/api/v1/runs/{failed['id']}/retry", json={})
    assert queued.status_code == 202, queued.text
    runtime = FixtureRuntime()
    result = finish_preparation(workspace, runtime, {"preparation_run_id": queued.json()["run_id"]})
    assert result["status"] == "failed" and result["error_code"] == "stale_evaluation"
    assert runtime.calls == []
    assert applications.get(application["id"])["documents"] == application["documents"]


def test_evaluation_waits_and_survives_a_new_service(confirmed):
    runtime = FixtureRuntime()
    job_id = imported_job(confirmed)
    run = run_operation(confirmed, runtime, "evaluate", {"job_id": job_id})
    assert run["status"] == "awaiting_user"
    assert not ApplicationService(confirmed).all()
    assert not any(call["stage"] == "draft" for call in runtime.calls)
    from ai_job_search.workspace import Workspace
    reopened = Workspace(confirmed.root, confirmed.resources)
    assert reopened.store.run(run["id"])["approvals"][0]["status"] == "pending"


def test_required_undeclared_language_blocks_without_scoring_or_drafting(confirmed):
    runtime = FixtureRuntime(failed_gate=True)
    run = run_operation(confirmed, runtime, "evaluate", {"job_id": imported_job(confirmed)})
    assert run["status"] == "succeeded", run
    assert run["result"]["blocked"] is True
    assert run["result"]["score"] is None
    assert run["result"]["evaluation"]["scores"] is None
    assert '"Fluent Polish required"' in run["result"]["evaluation"]["language"]["note"]
    assert not run["approvals"]
    assert not ApplicationService(confirmed).all()


def test_declared_higher_language_level_is_flagged_not_dropped(confirmed):
    run = run_operation(confirmed, FixtureRuntime(flagged_gate=True), "evaluate", {"job_id": imported_job(confirmed)})
    assert run["status"] == "awaiting_user", run
    assert run["result"]["blocked"] is False
    assert run["result"]["evaluation"]["language"]["verdict"] == "FLAG"
    assert run["result"]["score"] == 83


def test_approval_repetition_starts_one_preparation(confirmed):
    runtime = FixtureRuntime()
    response, _, approval = approved_application(confirmed, runtime)
    repeated = WorkflowService(confirmed, runtime).decide(
        approval["id"], Decision(decision="prepare", expected_revision=approval["revision"]),
    )
    assert repeated["application_id"] == response["application_id"]
    with confirmed.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs WHERE kind='documents'").fetchone()[0] == 1
    assert len(ApplicationService(confirmed).all()) == 1


def test_stale_policy_invalidates_decision(confirmed):
    runtime = FixtureRuntime()
    job_id = imported_job(confirmed)
    run = run_operation(confirmed, runtime, "evaluate", {"job_id": job_id})
    approval = run["approvals"][0]
    source = confirmed.path(".claude\\commands\\apply.md")
    source.write_text(confirmed.text(".claude\\commands\\apply.md") + "\nChanged policy.\n", encoding="utf-8")
    import pytest

    from ai_job_search.errors import AppError
    with pytest.raises(AppError) as caught:
        WorkflowService(confirmed, runtime).decide(approval["id"], Decision(decision="prepare", expected_revision=approval["revision"]))
    assert caught.value.code == "policy_changed"
    assert confirmed.store.approval(approval["id"])["status"] == "invalidated"


def test_full_fixture_journey_records_draft_then_immutable_submission_and_outcome(confirmed, fixture_documents):
    runtime = FixtureRuntime()
    response, _, _ = approved_application(confirmed, runtime)
    preparation = finish_preparation(confirmed, runtime, response)
    assert preparation["status"] == "succeeded", preparation
    applications = ApplicationService(confirmed)
    application = applications.get(response["application_id"])
    assert application["status"] == "drafted"
    assert application["applied_on"] is None
    stages = [call["stage"] for call in runtime.calls]
    assert stages.index("draft") < stages.index("independent_review_1") < stages.index("grounding_1") < stages.index("visual_cv_1")
    directories = [str(call["directory"]) for call in runtime.calls]
    assert len(directories) == len(set(directories))
    submitted = applications.submit("submission", application["id"], SubmissionInput(
        expected_revision=application["revision"], submitted_on=date(2020, 1, 1), notes="Sent via employer portal",
    ))
    assert submitted["status"] == "applied" and submitted["applied_on"] == "2020-01-01"
    frozen = submitted["submitted_revisions"][0]
    cv_path = frozen["documents"]["cv"]["pdf_path"]
    frozen_content = confirmed.read(cv_path)
    current = submitted["documents"]["cv"]
    edited = DocumentContent.model_validate(current["content"])
    edited.blocks[0].text = "Relevant Professional Experience"
    document = applications.save_content("edit", submitted["id"], "cv", edited, current["revision"])
    assert document["stale"] is True
    assert document["pdf_artifact_id"] == current["pdf_artifact_id"]
    assert confirmed.read(cv_path) == frozen_content
    current_app = applications.get(submitted["id"])
    interview = applications.outcome("interview", submitted["id"], OutcomeInput(
        expected_revision=current_app["revision"], status="interview", occurred_on=date(2020, 1, 10),
        stage="Technical interview", notes="Discussed service reliability",
    ))
    rejected = applications.outcome("reject", submitted["id"], OutcomeInput(
        expected_revision=interview["revision"], status="rejected", occurred_on=date(2020, 1, 20),
        notes="Selected another candidate",
    ))
    assert rejected["status"] == "rejected"
    assert any(stage["status"] == "interview" for stage in rejected["timeline"])
    assert confirmed.read(cv_path) == frozen_content
    assert "**Status:** rejected" in confirmed.text(rejected["archive_dir"] + "\\outcome.md")


def test_reapplications_get_distinct_archives_without_overwriting(confirmed, fixture_documents):
    runtime = FixtureRuntime()
    first, _, _ = approved_application(confirmed, runtime)
    assert finish_preparation(confirmed, runtime, first)["status"] == "succeeded"
    before = ApplicationService(confirmed).get(first["application_id"])
    first_posting = confirmed.read(before["archive_dir"] + "\\job_posting.md")
    second, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, second)
    assert result["status"] == "succeeded", result
    after = ApplicationService(confirmed).get(second["application_id"])
    assert before["id"] != after["id"]
    assert before["archive_dir"] != after["archive_dir"]
    assert confirmed.read(before["archive_dir"] + "\\job_posting.md") == first_posting
    assert len(ApplicationService(confirmed).tracker()[1]) == 2


def test_grounding_failure_never_publishes_or_marks_drafted(confirmed, fixture_documents):
    runtime = FixtureRuntime(unsupported=True)
    response, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, response)
    assert result["status"] == "awaiting_user" and result["error_code"] is None
    assert result["approvals"][0]["kind"] == "document_review"
    assert result["result"]["issues"][0]["message"] == "Unsupported metric."
    assert len([call for call in runtime.calls if call["stage"].startswith("repair_")]) == 2
    assert ApplicationService(confirmed).tracker()[1] == []
    application = ApplicationService(confirmed).get(response["application_id"])
    assert application["documents"]["cv"]["stale"] is True


def test_skipped_layout_is_not_success_and_does_not_burn_three_repairs(confirmed, fixture_documents, monkeypatch):
    runtime = FixtureRuntime()
    def unavailable(self, *args):
        return [{"id": "layout", "label": "Layout", "status": "skipped", "message": "No Poppler."}], "text", []
    monkeypatch.setattr(DocumentService, "verify", unavailable)
    response, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, response)
    assert result["status"] == "failed" and result["error_code"] == "pdf_tool_missing"
    assert not any(call["stage"].startswith("repair_") for call in runtime.calls)
    assert not ApplicationService(confirmed).tracker()[1]
    document = ApplicationService(confirmed).get(response["application_id"])["documents"]["cv"]
    assert document["checks"][0]["status"] == "skipped"


def test_actual_failure_stops_after_three_compile_cycles(confirmed, fixture_documents, monkeypatch):
    runtime = FixtureRuntime()
    def failed(self, *args):
        return [{"id": "layout", "label": "Layout", "status": "fail", "message": "Synthetic internal gap."}], "text", []
    monkeypatch.setattr(DocumentService, "verify", failed)
    response, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, response)
    assert result["status"] == "awaiting_user" and result["approvals"][0]["kind"] == "document_review"
    assert len([call for call in runtime.calls if call["stage"].startswith("repair_")]) == 2
    assert len([call for call in runtime.calls if call["stage"].startswith("independent_review_")]) == 1
    with confirmed.store.connect() as connection:
        steps = connection.execute(
            "SELECT status FROM steps WHERE run_id=? AND stage LIKE 'compile_%'", (result["id"],),
        ).fetchall()
    assert len(steps) == 6 and all(step["status"] == "failed" for step in steps)
    assert not ApplicationService(confirmed).tracker()[1]


def test_rewrite_waits_for_comparison_acceptance(confirmed, fixture_documents):
    runtime = FixtureRuntime()
    response, _, _ = approved_application(confirmed, runtime)
    assert finish_preparation(confirmed, runtime, response)["status"] == "succeeded"
    application = ApplicationService(confirmed).get(response["application_id"])
    run = run_operation(confirmed, runtime, "documents", {
        "application_id": application["id"], "expected_revision": application["revision"],
        "instruction": "Make the opening more concise without adding facts.",
    })
    assert run["status"] == "awaiting_user", run
    assert run["approvals"][0]["kind"] == "document_rewrite"
    assert run["result"]["before"] and run["result"]["after"]
    assert ApplicationService(confirmed).get(application["id"])["revision"] == application["revision"]


def test_inferred_posting_metadata_is_reviewed_before_saving(confirmed):
    runtime = FixtureRuntime()
    run = run_operation(confirmed, runtime, "posting_import", {"text": POSTING["text"]})
    assert run["status"] == "awaiting_user", run
    assert not JobService(confirmed).all()
    approval = run["approvals"][0]
    result = WorkflowService(confirmed, runtime).decide(
        approval["id"], Decision(decision="approve", expected_revision=approval["revision"]),
    )
    assert JobService(confirmed).get(result["job_id"])["company"] == "Example Hiring"


def test_unreachable_retirement_keeps_manual_check_reason(confirmed):
    from ai_job_search.errors import AppError
    job_id = imported_job(confirmed)
    jobs = JobService(confirmed)
    job = jobs.get(job_id)
    jobs.update("seed-failures", job_id, {"fetch_attempts": 2, "first_unreachable": "2020-01-01T00:00:00Z"}, job["revision"])
    service = WorkflowService(confirmed, FixtureRuntime())
    service.record_fetch_failure("fixture-run", jobs.get(job_id), AppError("posting_unreachable", "HTTP 404; not closure."))
    retired = jobs.get(job_id)
    assert retired["status"] == "expired" and retired["expired_reason"] == "unreachable"
    assert any("check manually" in warning for warning in retired["warnings"])


def test_external_document_edit_during_compilation_is_not_verified_or_overwritten(confirmed, fixture_documents, monkeypatch):
    runtime = FixtureRuntime()
    response, _, _ = approved_application(confirmed, runtime)
    applications = ApplicationService(confirmed)
    compile_document = DocumentService.compile
    edited = False

    def concurrent_edit(self, kind, *args):
        nonlocal edited
        if not edited:
            application = applications.get(response["application_id"])
            document = application["documents"]["cv"]
            content = DocumentContent.model_validate(document["content"])
            content.blocks[0].text = "User correction made while the worker was compiling"
            applications.save_content("concurrent-user-edit", application["id"], "cv", content, document["revision"])
            edited = True
        return compile_document(self, kind, *args)

    monkeypatch.setattr(DocumentService, "compile", concurrent_edit)
    result = finish_preparation(confirmed, runtime, response)
    assert result["status"] == "failed" and result["error_code"] == "external_edit"
    application = applications.get(response["application_id"])
    assert application["documents"]["cv"]["content"]["blocks"][0]["text"].startswith("User correction")
    assert application["documents"]["cv"]["stale"]
    assert not applications.tracker()[1]


@pytest.mark.parametrize("change", ["profile", "pdf", "policy"])
def test_changed_verification_inputs_prevent_submission(confirmed, fixture_documents, change):
    runtime = FixtureRuntime()
    response, _, _ = approved_application(confirmed, runtime)
    assert finish_preparation(confirmed, runtime, response)["status"] == "succeeded"
    applications = ApplicationService(confirmed)
    application = applications.get(response["application_id"])
    if change == "profile":
        profiles = ProfileService(confirmed)
        current = profiles.read()
        changed = current.data.model_copy(deep=True)
        changed.email = "corrected@example.invalid"
        profiles.apply("corrected-contact", profiles.propose(changed, current.revision))
    elif change == "policy":
        path = ".claude\\commands\\apply.md"
        confirmed.path(path).write_text(confirmed.text(path) + "\nChanged verification rule.\n", encoding="utf-8")
    else:
        confirmed.path(application["documents"]["cv"]["pdf_path"]).write_bytes(b"Externally replaced PDF")
    current = applications.get(application["id"])
    assert current["documents"]["cv"]["stale"]
    with pytest.raises(AppError) as caught:
        applications.submit("submit-stale", current["id"], SubmissionInput(
            expected_revision=current["revision"], submitted_on=date(2020, 1, 1),
        ))
    assert caught.value.code == "documents_not_current"
    assert applications.get(current["id"])["status"] == "drafted"


def test_every_company_claim_needs_separate_independent_verification(confirmed, fixture_documents, monkeypatch):
    class CompanyClaimsRuntime(FixtureRuntime):
        async def generate(self, run_id, stage, *args, **kwargs):
            result = await super().generate(run_id, stage, *args, **kwargs)
            if stage.startswith("independent_review_"):
                result.company_claims = ["First independently verified claim", "Second independent claim"]
                result.revised.cover_letter.blocks[1].text = ". ".join(result.company_claims)
            return result

    runtime = CompanyClaimsRuntime()
    service = WorkflowService(confirmed, runtime)
    sources = [{"id": f"company:{index}", "claim": claim, "quote": "A sufficiently long synthetic evidence quote.",
                "url": f"https://example.invalid/source-{index}"}
               for index, claim in enumerate(["First independently verified claim", "Second independent claim"])]

    async def company_research(self, run_id, company, stage="company_research", claims=None):
        return {"company": company, "sources": sources if stage == "company_research" else sources[:1],
                "limitations": ["Only one claim was independently reverified."] if stage != "company_research" else []}

    monkeypatch.setattr(WorkflowService, "company_research", company_research)
    response, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, response)
    assert result["status"] == "awaiting_user" and result["approvals"][0]["kind"] == "document_review"
    assert [issue["quote"] for issue in result["result"]["issues"]] == ["Second independent claim"]
    assert not service.applications.tracker()[1]
