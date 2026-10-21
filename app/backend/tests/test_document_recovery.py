from __future__ import annotations

from uuid import uuid4

import pytest
from conftest import FixtureRuntime, run_operation
from test_workflows import approved_application, finish_preparation

from ai_job_search.applications import ApplicationService
from ai_job_search.documents import DocumentService
from ai_job_search.errors import AppError
from ai_job_search.jobs import JobService
from ai_job_search.models import (
    Decision,
    DocumentBlock,
    DocumentContent,
    DocumentIssue,
    DocumentReviewCheckpoint,
    DraftPair,
    GroundingReview,
    ReviewFinding,
    VisualReview,
)
from ai_job_search.profile import ProfileService
from ai_job_search.workflows import WorkflowService

BAD_WORDING = "I owned every Python service and trained the entire team."
SUPPORTED_WORDING = "Built Python services for an internal reporting team."


@pytest.mark.parametrize("stage", ["draft", "independent_review_1", "repair_2", "independent_review_3"])
def test_boilerplate_revisions_retain_drafts_and_identify_the_failing_output(confirmed, fixture_documents, stage):
    class DestructiveRuntime(WordingRuntime):
        def __init__(self):
            super().__init__(repair=False)

        async def generate(self, run_id, current_stage, data, *args, **kwargs):
            result = await super().generate(run_id, current_stage, data, *args, **kwargs)
            if current_stage == stage or current_stage == stage + "_output_retry":
                pair = result.revised if current_stage.startswith("independent_review_") else result
                pair.cv.blocks = [DocumentBlock(text="Available upon request.")]
                pair.cover_letter.blocks = [DocumentBlock(text="I look forward to hearing from you.")]
            return result

    runtime = DestructiveRuntime()
    response, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, response)
    if stage == "draft":
        assert result["status"] == "awaiting_user", result
        approval = result["approvals"][0]
        assert approval["kind"] == "document_review" and approval["choices"] == ["reject"]
        assert {issue["document"] for issue in approval["proposal"]["issues"]} == {"cv", "cover_letter"}
    else:
        assert result["status"] == "failed" and result["error_code"] == "ai_incomplete_documents", result
        assert not result["approvals"]
        failure = result["result"]["ai_failure"]
        assert failure["stage"] == stage + "_output_retry"
        assert set(failure["documents"]) == {"cv", "cover_letter"}
        assert not ApplicationService(confirmed).get(response["application_id"])["content_issues"]
    assert not result["artifacts"] and not ApplicationService(confirmed).tracker()[1]
    assert not any(call["stage"].startswith("visual_") for call in runtime.calls)
    application = ApplicationService(confirmed).get(response["application_id"])
    if stage != "draft":
        assert len(application["documents"]["cv"]["content"]["blocks"]) == 2
        assert application["documents"]["cover_letter"]["content"]["blocks"][1]["text"] == BAD_WORDING
    with confirmed.store.connect() as connection:
        assert not connection.execute(
            "SELECT 1 FROM steps WHERE run_id=? AND stage LIKE 'compile_%'", (result["id"],),
        ).fetchone()


@pytest.mark.parametrize("stage", ["independent_review_1", "repair_2"])
def test_empty_ai_revision_fails_without_treating_it_as_no_changes(confirmed, fixture_documents, stage):
    class EmptyRuntime(WordingRuntime):
        def __init__(self):
            super().__init__(repair=False)

        async def generate(self, run_id, current_stage, data, *args, **kwargs):
            result = await super().generate(run_id, current_stage, data, *args, **kwargs)
            if current_stage == stage or current_stage == stage + "_output_retry":
                pair = result.revised if current_stage.startswith("independent_review_") else result
                pair.cv.blocks = []
                pair.cover_letter.blocks = []
            return result

    runtime = EmptyRuntime()
    response, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, response)
    assert result["status"] == "failed" and result["error_code"] == "ai_incomplete_documents", result
    assert not result["approvals"] and not result["artifacts"]
    application = ApplicationService(confirmed).get(response["application_id"])
    assert all(document["content"]["blocks"] for document in application["documents"].values())
    assert not any("incomplete" in issue["message"] for issue in application.get("content_issues", []))
    calls = [call for call in runtime.calls if call["stage"].startswith(stage)]
    assert len(calls) == 2 and calls[-1]["stage"] == stage + "_output_retry"


@pytest.mark.parametrize("stage", ["independent_review_1", "repair_2"])
@pytest.mark.parametrize("empty", [False, True])
def test_malformed_document_revision_is_retried_with_full_original_drafts(
    confirmed, fixture_documents, stage, empty,
):
    requests = []

    class RetryRuntime(WordingRuntime):
        async def generate(self, run_id, current_stage, data, *args, **kwargs):
            result = await super().generate(run_id, current_stage, data, *args, **kwargs)
            if current_stage.startswith(stage):
                requests.append(data)
            if current_stage == stage:
                pair = result.revised if current_stage.startswith("independent_review_") else result
                pair.cv.blocks = [] if empty else [DocumentBlock(text="Available upon request.")]
            return result

    runtime = RetryRuntime()
    response, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, response)
    assert result["status"] == "awaiting_user", result
    assert result["approvals"][0]["kind"] == "document_correction"
    assert len(requests) == 2
    assert requests[0]["drafts"] == requests[1]["drafts"]
    assert len(requests[1]["drafts"]["cv"]["blocks"]) > 1
    assert requests[1]["rejected_output_findings"][0]["document"] == "cv"
    assert "BOTH full documents" in requests[1]["task"]
    with confirmed.store.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM events WHERE run_id=? AND type='ai_output_rejected' AND stage=?",
            (result["id"], stage),
        ).fetchone()


def test_completeness_rejects_large_deletions_even_with_substantive_content():
    pair = DraftPair(
        cv=DocumentContent(blocks=[DocumentBlock(kind="entry", text=SUPPORTED_WORDING)]),
        cover_letter=DocumentContent(blocks=[DocumentBlock(text=SUPPORTED_WORDING)]),
    )
    original = pair.model_copy(deep=True)
    for kind in ("cv", "cover_letter"):
        getattr(original, kind).blocks[0].text = SUPPORTED_WORDING * 3
    findings = DocumentService.completeness_findings(pair, original)
    assert {finding.document for finding in findings} == {"cv", "cover_letter"}
    assert all("more than half" in finding.message for finding in findings)
    for kind in ("cv", "cover_letter"):
        getattr(original, kind).blocks[0].text = SUPPORTED_WORDING * 2
    assert not DocumentService.completeness_findings(pair, original)


@pytest.mark.parametrize("kind", ["cv", "cover_letter"])
@pytest.mark.parametrize("blocks", [
    [],
    [DocumentBlock(kind="heading", heading="Professional Experience")],
    [DocumentBlock(text="Available upon request.")],
    [DocumentBlock(text="Dear Hiring Manager,"), DocumentBlock(text="Kind regards,\nCandidate")],
])
def test_completeness_does_not_confuse_boilerplate_with_a_document(kind, blocks):
    pair = DraftPair(
        cv=DocumentContent(blocks=[DocumentBlock(kind="entry", text=SUPPORTED_WORDING)]),
        cover_letter=DocumentContent(blocks=[DocumentBlock(text=SUPPORTED_WORDING)]),
    )
    getattr(pair, kind).blocks = blocks
    findings = DocumentService.completeness_findings(pair)
    assert len(findings) == 1 and findings[0].document == kind


def test_incomplete_saved_drafts_cannot_reuse_a_successful_review(confirmed, fixture_documents):
    runtime = FixtureRuntime()
    response, _, _ = approved_application(confirmed, runtime)
    assert finish_preparation(confirmed, runtime, response)["status"] == "succeeded"
    applications = ApplicationService(confirmed)
    original = applications.get(response["application_id"])
    applications.save_content(
        "incomplete-legacy-content", original["id"], "cv",
        DocumentContent(blocks=[DocumentBlock(text="Available upon request.")]),
        original["documents"]["cv"]["revision"],
    )
    current = applications.get(original["id"])
    runtime = FixtureRuntime()
    result = run_operation(confirmed, runtime, "documents", {
        "application_id": current["id"], "expected_revision": current["revision"],
        "_retry_of": response["preparation_run_id"],
    })
    assert result["status"] == "awaiting_user" and not result["artifacts"]
    assert result["approvals"][0]["proposal"]["issues"][0]["document"] == "cv"
    assert not runtime.calls


def test_destructive_rewrite_is_not_offered_for_acceptance(confirmed, fixture_documents):
    runtime = FixtureRuntime()
    response, _, _ = approved_application(confirmed, runtime)
    assert finish_preparation(confirmed, runtime, response)["status"] == "succeeded"
    applications = ApplicationService(confirmed)
    original = applications.get(response["application_id"])

    class DestructiveRewriteRuntime(FixtureRuntime):
        async def generate(self, run_id, stage, data, *args, **kwargs):
            result = await super().generate(run_id, stage, data, *args, **kwargs)
            if stage.startswith("rewrite"):
                result.cv.blocks = [DocumentBlock(text="Available upon request.")]
            return result

    result = run_operation(confirmed, DestructiveRewriteRuntime(), "documents", {
        "application_id": original["id"], "expected_revision": original["revision"],
        "instruction": "Tighten the wording.",
    })
    assert result["status"] == "failed" and result["error_code"] == "ai_incomplete_documents"
    assert not result["artifacts"] and not result["approvals"]
    assert applications.get(original["id"])["documents"] == original["documents"]


def test_explicit_rewrite_can_propose_recovery_of_an_incomplete_saved_draft(confirmed, fixture_documents):
    runtime = FixtureRuntime()
    response, _, _ = approved_application(confirmed, runtime)
    assert finish_preparation(confirmed, runtime, response)["status"] == "succeeded"
    applications = ApplicationService(confirmed)
    original = applications.get(response["application_id"])
    complete_pair = DraftPair.model_validate({
        kind: document["content"] for kind, document in original["documents"].items()
    })
    applications.save_content(
        "incomplete-cv", original["id"], "cv",
        DocumentContent(blocks=[DocumentBlock(text="Available upon request.")]),
        original["documents"]["cv"]["revision"],
    )
    current = applications.get(original["id"])

    class RecoveryRuntime(FixtureRuntime):
        async def generate(self, run_id, stage, data, *args, **kwargs):
            if stage == "rewrite":
                return complete_pair.model_copy(deep=True)
            return await super().generate(run_id, stage, data, *args, **kwargs)

    result = run_operation(confirmed, RecoveryRuntime(), "documents", {
        "application_id": current["id"], "expected_revision": current["revision"],
        "instruction": "Rebuild the full CV from my supported profile.",
    })
    assert result["status"] == "awaiting_user"
    approval = result["approvals"][0]
    assert approval["kind"] == "document_rewrite" and approval["choices"] == ["approve", "reject"]
    assert approval["proposal"]["after"] == complete_pair.model_dump(mode="json")
    assert applications.get(original["id"])["documents"] == current["documents"]
    assert not result["artifacts"]


@pytest.mark.parametrize("failed_check", ["mechanical", "visual"])
def test_failed_pdf_checks_do_not_expose_unpublished_downloads(
    confirmed, fixture_documents, monkeypatch, failed_check,
):
    class FailedVisualRuntime(FixtureRuntime):
        async def generate(self, run_id, stage, data, *args, **kwargs):
            result = await super().generate(run_id, stage, data, *args, **kwargs)
            if failed_check == "visual" and stage.startswith("visual_"):
                return VisualReview(passed=False, findings=["Synthetic visual defect."])
            return result

    if failed_check == "mechanical":
        monkeypatch.setattr(DocumentService, "verify", lambda *args: (
            [{"id": "text", "status": "fail", "message": "Synthetic missing body text."}], "", [],
        ))
    runtime = FailedVisualRuntime()
    response, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, response)
    assert result["status"] == "awaiting_user" and not result["artifacts"]
    application = ApplicationService(confirmed).get(response["application_id"])
    assert not application["published"] and not ApplicationService(confirmed).tracker()[1]
    with confirmed.store.connect() as connection:
        assert not connection.execute(
            "SELECT 1 FROM artifacts WHERE run_id=?", (result["id"],),
        ).fetchone()


class WordingRuntime(FixtureRuntime):
    def __init__(self, *, repair=True, confirmation=False):
        super().__init__()
        self.repair = repair
        self.confirmation = confirmation

    async def generate(self, run_id, stage, data, *args, **kwargs):
        result = await super().generate(run_id, stage, data, *args, **kwargs)
        if stage == "draft":
            result.cover_letter.blocks[1].text = BAD_WORDING
        if stage.startswith("repair_") and self.repair:
            result.cover_letter.blocks[1].text = SUPPORTED_WORDING
        if stage.startswith("grounding_"):
            pair = data["final_documents"]
            block = pair["cover_letter"]["blocks"][1]
            if block["text"] == BAD_WORDING:
                evidence_id = next(item["id"] for item in data["evidence"] if SUPPORTED_WORDING in item["text"])
                return GroundingReview(supported=False, issues=[DocumentIssue(
                    document="cover_letter", block_id=block["id"], quote=BAD_WORDING,
                    message="Ownership and training are not supported by the approved source.",
                    suggestion=SUPPORTED_WORDING, evidence_ids=[evidence_id], needs_confirmation=self.confirmation,
                )])
            return GroundingReview(supported=True, notes=["Candidate claims match the approved evidence."])
        return result


def correction(workspace, runtime=None):
    runtime = runtime or WordingRuntime()
    response, _, _ = approved_application(workspace, runtime)
    result = finish_preparation(workspace, runtime, response)
    assert result["status"] == "awaiting_user", result
    assert result["approvals"][0]["kind"] == "document_correction", result
    return response, result, result["approvals"][0], runtime


def test_grounding_correction_is_checked_but_never_applied_without_acceptance(confirmed, fixture_documents):
    profiles = ProfileService(confirmed)
    before = profiles.snapshot()
    response, run, approval, runtime = correction(confirmed)
    application = ApplicationService(confirmed).get(response["application_id"])
    assert application["documents"]["cover_letter"]["content"]["blocks"][1]["text"] == BAD_WORDING
    assert application["content_issues"][0]["quote"] == BAD_WORDING
    assert approval["proposal"]["after"]["cover_letter"]["blocks"][1]["text"] == SUPPORTED_WORDING
    assert approval["proposal"]["checked"]["grounding"]["supported"]
    assert approval["choices"] == ["approve", "reject"]
    assert not run["artifacts"] and not application["published"]
    assert profiles.snapshot() == before
    stages = [call["stage"] for call in runtime.calls]
    assert stages.index("grounding_1") < stages.index("repair_2") < stages.index("independent_review_2") < stages.index("grounding_2")


def test_accepted_correction_resumes_pdf_checks_without_repaying_for_valid_review(confirmed, fixture_documents):
    response, run, approval, _ = correction(confirmed)
    service = WorkflowService(confirmed, FixtureRuntime())
    decision = Decision(decision="approve", expected_revision=approval["revision"])
    accepted = service.decide(approval["id"], decision)
    assert service.decide(approval["id"], decision) == accepted
    current = ApplicationService(confirmed).get(response["application_id"])
    assert current["documents"]["cover_letter"]["content"]["blocks"][1]["text"] == SUPPORTED_WORDING
    assert current["review_checkpoint_available"] and not current["published"]
    queued = confirmed.store.run(accepted["preparation_run_id"])
    assert queued["input"]["_retry_of"] == run["id"]
    runtime = FixtureRuntime()
    completed = finish_preparation(confirmed, runtime, accepted)
    assert completed["status"] == "succeeded", completed
    assert {call["stage"] for call in runtime.calls} == {"visual_cv_1", "visual_cover_letter_1"}
    assert ApplicationService(confirmed).get(current["id"])["status"] == "drafted"
    assert ApplicationService(confirmed).get(current["id"])["applied_on"] is None


def test_independent_reviewer_factual_corrections_also_require_explicit_acceptance(confirmed, fixture_documents):
    class ReviewerCorrectionRuntime(WordingRuntime):
        async def generate(self, run_id, stage, data, *args, **kwargs):
            result = await super().generate(run_id, stage, data, *args, **kwargs)
            if stage.startswith("independent_review_"):
                block = result.revised.cover_letter.blocks[1]
                result.findings = [ReviewFinding(
                    kind="grounding", block_id=block.id,
                    message="The original ownership and training claims exceeded the approved evidence.",
                )]
                block.text = SUPPORTED_WORDING
            return result

    response, _, approval, runtime = correction(confirmed, ReviewerCorrectionRuntime())
    saved = ApplicationService(confirmed).get(response["application_id"])
    assert saved["documents"]["cover_letter"]["content"]["blocks"][1]["text"] == BAD_WORDING
    assert approval["proposal"]["after"]["cover_letter"]["blocks"][1]["text"] == SUPPORTED_WORDING
    assert approval["proposal"]["issues"][0]["document"] == "cover_letter"
    assert approval["proposal"]["issues"][0]["quote"] == BAD_WORDING
    assert approval["proposal"]["checked"]["grounding"]["supported"]
    assert not any(call["stage"].startswith("repair_") for call in runtime.calls)
    assert not saved["published"]


def test_rejecting_correction_keeps_the_original_draft_and_profile(confirmed, fixture_documents):
    response, _, approval, _ = correction(confirmed)
    applications = ApplicationService(confirmed)
    original = applications.get(response["application_id"])
    profile_hashes = ProfileService(confirmed).snapshot()
    rejected = WorkflowService(confirmed).decide(
        approval["id"], Decision(decision="reject", expected_revision=approval["revision"]),
    )
    assert rejected["application_id"] == original["id"]
    assert applications.get(original["id"])["documents"] == original["documents"]
    assert ProfileService(confirmed).snapshot() == profile_hashes
    assert not applications.tracker()[1]


@pytest.mark.parametrize("change,code", [
    ("profile", "external_edit"), ("posting", "posting_changed"),
    ("policy", "policy_changed"), ("document", "external_edit"),
])
def test_correction_acceptance_rejects_changed_inputs(confirmed, fixture_documents, change, code):
    response, _, approval, _ = correction(confirmed)
    applications = ApplicationService(confirmed)
    original = applications.get(response["application_id"])
    if change == "profile":
        profiles = ProfileService(confirmed)
        current = profiles.read()
        changed = current.data.model_copy(deep=True)
        changed.phone = "+45 99999999"
        profiles.apply("changed-phone", profiles.propose(changed, current.revision))
    elif change == "posting":
        jobs = JobService(confirmed)
        job = jobs.get(original["job_id"])
        jobs.update("changed-requirements", job["id"], {"description": job["description"] + "\nChanged."}, job["revision"])
    elif change == "policy":
        policy = confirmed.path(".claude\\commands\\apply.md")
        policy.write_text(policy.read_text(encoding="utf-8") + "\nChanged policy.\n", encoding="utf-8")
    else:
        document = original["documents"]["cover_letter"]
        content = DocumentContent.model_validate(document["content"])
        content.blocks[1].text = "A manual correction that must not be overwritten."
        applications.save_content("manual-correction", original["id"], "cover_letter", content, document["revision"])
    before = applications.get(original["id"])["documents"]["cover_letter"]["content"]
    with pytest.raises(AppError) as caught:
        WorkflowService(confirmed).decide(approval["id"], Decision(decision="approve", expected_revision=approval["revision"]))
    assert caught.value.code == code
    assert applications.get(original["id"])["documents"]["cover_letter"]["content"] == before
    assert confirmed.store.approval(approval["id"])["status"] == "invalidated"


@pytest.mark.parametrize("confirmation", [False, True])
def test_unresolved_facts_pause_for_review_instead_of_failing_or_publishing(confirmed, fixture_documents, confirmation):
    runtime = WordingRuntime(repair=False, confirmation=confirmation)
    response, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, response)
    assert result["status"] == "awaiting_user" and result["error_code"] is None, result
    approval = result["approvals"][0]
    assert approval["kind"] == "document_review" and approval["choices"] == ["reject"]
    assert result["result"]["checked"] is None
    assert len([call for call in runtime.calls if call["stage"].startswith("repair_")]) == (0 if confirmation else 2)
    assert not result["artifacts"] and not ApplicationService(confirmed).tracker()[1]
    assert ApplicationService(confirmed).get(response["application_id"])["documents"]["cover_letter"]["stale"]


def test_manual_edits_can_resume_and_close_the_old_proposal_idempotently(api, profile, fixture_documents):
    client, app = api
    workspace = app.state.workspace
    profiles = ProfileService(workspace)
    profiles.apply("confirm", profiles.propose(profile, profiles.read().revision))
    response, run, approval, _ = correction(workspace)
    applications = ApplicationService(workspace)
    original = applications.get(response["application_id"])
    blocked = client.post(f"/api/v1/applications/{original['id']}/document-runs", json={
        "expected_revision": original["revision"], "resume_from": run["id"],
    })
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "document_correction_required"
    document = original["documents"]["cover_letter"]
    content = DocumentContent.model_validate(document["content"])
    content.blocks[1].text = "I built Python services for an internal reporting team."
    applications.save_content("manual-wording", original["id"], "cover_letter", content, document["revision"])
    current = applications.get(original["id"])
    payload = {"expected_revision": current["revision"], "resume_from": run["id"]}
    client.headers["Idempotency-Key"] = str(uuid4())
    queued = client.post(f"/api/v1/applications/{original['id']}/document-runs", json=payload)
    assert queued.status_code == 202, queued.text
    assert workspace.store.approval(approval["id"])["status"] == "rejected"
    assert workspace.store.run(run["id"])["result"]["manual_edits_preserved"]
    runtime = WordingRuntime()
    completed = finish_preparation(workspace, runtime, {"preparation_run_id": queued.json()["run_id"]})
    assert completed["status"] == "succeeded", completed
    assert any(call["stage"] == "independent_review_1" for call in runtime.calls)
    assert applications.get(original["id"])["documents"]["cover_letter"]["content"]["blocks"][1]["text"] == content.blocks[1].text
    with workspace.store.connect(write=True) as connection:
        connection.execute("DELETE FROM idempotency")
    repeated = client.post(f"/api/v1/applications/{original['id']}/document-runs", json=payload)
    assert repeated.status_code == 202 and repeated.json() == queued.json()


def test_technical_failure_resumes_unchanged_review_and_reports_cumulative_cost(api, profile, fixture_documents, monkeypatch):
    client, app = api
    workspace = app.state.workspace
    profiles = ProfileService(workspace)
    profiles.apply("confirm", profiles.propose(profile, profiles.read().revision))
    compile_document = DocumentService.compile

    def missing_tool(*args, **kwargs):
        raise AppError("document_tool_missing", "Synthetic missing PDF tool.", 409)

    monkeypatch.setattr(DocumentService, "compile", missing_tool)
    response, _, _ = approved_application(workspace, FixtureRuntime())
    failed = finish_preparation(workspace, FixtureRuntime(), response)
    assert failed["status"] == "failed" and failed["error_code"] == "document_tool_missing"
    monkeypatch.setattr(DocumentService, "compile", compile_document)
    application = ApplicationService(workspace).get(response["application_id"])
    queued = client.post(f"/api/v1/applications/{application['id']}/document-runs", json={
        "expected_revision": application["revision"], "resume_from": failed["id"],
    })
    assert queued.status_code == 202, queued.text
    runtime = FixtureRuntime()
    completed = finish_preparation(workspace, runtime, {"preparation_run_id": queued.json()["run_id"]})
    assert completed["status"] == "succeeded", completed
    assert {call["stage"] for call in runtime.calls} == {"visual_cv_1", "visual_cover_letter_1"}
    with workspace.store.connect(write=True) as connection:
        connection.execute("UPDATE runs SET cost_usd=0.06 WHERE id=?", (failed["id"],))
        connection.execute("UPDATE runs SET cost_usd=0.02 WHERE id=?", (completed["id"],))
    view = client.get(f"/api/v1/applications/{application['id']}").json()
    assert view["preparation_attempts"] == 2 and view["preparation_cost_usd"] == pytest.approx(0.08)
    assert view["review_checkpoint_available"] and view["next_run_limit_usd"] == 3


def test_failed_compilation_is_not_marked_successful_when_content_review_pauses(confirmed, fixture_documents, monkeypatch):
    def failed_compile(*args, **kwargs):
        raise AppError("tool_failed", "Synthetic typesetting failure.", 422)

    monkeypatch.setattr(DocumentService, "compile", failed_compile)
    runtime = FixtureRuntime()
    response, _, _ = approved_application(confirmed, runtime)
    result = finish_preparation(confirmed, runtime, response)
    assert result["status"] == "awaiting_user" and result["approvals"][0]["kind"] == "document_review"
    with confirmed.store.connect() as connection:
        steps = connection.execute(
            "SELECT status FROM steps WHERE run_id=? AND stage LIKE 'compile_%'", (result["id"],),
        ).fetchall()
    assert len(steps) == 6 and all(step["status"] == "failed" for step in steps)
    repair = next(call for call in runtime.calls if call["stage"] == "repair_2")
    assert all("Synthetic typesetting failure." in finding for finding in repair["data"]["actual_verification_findings"])
    assert not result["artifacts"] and not ApplicationService(confirmed).tracker()[1]


@pytest.mark.parametrize("source_kind,code", [
    ("wrong_application", "invalid_document_resume"),
    ("wrong_kind", "invalid_document_resume"),
    ("earlier_failure", "operation_in_progress"),
])
def test_document_resume_rejects_unrelated_sources_and_duplicate_active_work(api, profile, source_kind, code):
    client, app = api
    workspace = app.state.workspace
    profiles = ProfileService(workspace)
    profiles.apply("confirm", profiles.propose(profile, profiles.read().revision))
    response, _, _ = approved_application(workspace, FixtureRuntime())
    original_id = response["preparation_run_id"]
    workspace.store.finish(original_id, status="failed", code="ai_timeout", error="Synthetic interruption.")
    application = ApplicationService(workspace).get(response["application_id"])
    source_id = str(uuid4())
    service = WorkflowService(workspace)
    if source_kind == "wrong_kind":
        service.enqueue(source_id, "connection_test", {})
    else:
        service.enqueue(source_id, "documents", {
            "application_id": application["id"] if source_kind == "earlier_failure" else str(uuid4()),
            "expected_revision": application["revision"], "instruction": "",
        })
    if source_kind == "earlier_failure":
        source_id = original_id
    with workspace.store.connect() as connection:
        before = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    result = client.post(f"/api/v1/applications/{application['id']}/document-runs", json={
        "expected_revision": application["revision"], "resume_from": source_id,
    })
    assert result.status_code == 409 and result.json()["error"]["code"] == code, result.text
    with workspace.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == before


def test_company_claims_are_verified_before_candidate_grounding(confirmed, fixture_documents, monkeypatch):
    claim = "The target employer builds reporting tools."
    order = []

    class CompanyRuntime(FixtureRuntime):
        async def generate(self, run_id, stage, data, *args, **kwargs):
            result = await super().generate(run_id, stage, data, *args, **kwargs)
            if stage.startswith("independent_review_"):
                result.company_claims = [claim]
                result.revised.cover_letter.blocks[1].text += " " + claim
                order.append("review")
            if stage.startswith("grounding_"):
                assert "evaluation" not in data
                assert data["posting"]
                assert "application role title" in data["company_claim_scope"]
                assert data["scope"] == "candidate_claims_in_final_documents_only"
                assert data["verified_company_research"]["sources"][0]["claim"] == claim
                order.append("grounding")
                return GroundingReview(supported=True, company_claims=[claim])
            return result

    async def company_research(self, run_id, company, stage="company_research", claims=None):
        if stage == "company_research":
            return {"company": company, "sources": [], "limitations": []}
        assert claims == [claim]
        order.append("company_verification")
        return {"company": company, "sources": [{
            "url": "https://example.invalid/independent-source", "claim": claim,
            "quote": "An independently verified synthetic quote about reporting tools.",
        }], "limitations": []}

    monkeypatch.setattr(WorkflowService, "company_research", company_research)
    runtime = CompanyRuntime()
    response, _, _ = approved_application(confirmed, runtime)
    completed = finish_preparation(confirmed, runtime, response)
    assert completed["status"] == "succeeded", completed
    assert order == ["review", "company_verification", "grounding"]


def test_additional_company_evidence_retains_stage_limits_and_survives_resume(api, profile, fixture_documents, monkeypatch):
    client, app = api
    workspace = app.state.workspace
    profiles = ProfileService(workspace)
    profiles.apply("confirm", profiles.propose(profile, profiles.read().revision))
    claims = [f"The target employer builds reporting product {index}." for index in range(16)]

    class CompanyRuntime(FixtureRuntime):
        async def generate(self, run_id, stage, data, *args, **kwargs):
            result = await super().generate(run_id, stage, data, *args, **kwargs)
            if stage.startswith("independent_review_"):
                result.company_claims = claims[:8]
                result.revised.cover_letter.blocks[1].text += " " + " ".join(claims)
            if stage.startswith("grounding_"):
                return GroundingReview(supported=True, company_claims=claims[8:])
            return result

    async def company_research(self, run_id, company, stage="company_research", claims=None):
        return {
            "company": company,
            "sources": [{"url": f"https://example.invalid/verified/{index}", "claim": claim,
                         "quote": f"Synthetic independently verified statement: {claim}"}
                        for index, claim in enumerate(claims or [])],
            "limitations": [f"{stage}: synthetic research limitation {index}." for index in range(15)],
        }

    compile_document = DocumentService.compile

    def missing_tool(*args, **kwargs):
        raise AppError("document_tool_missing", "Synthetic missing PDF tool.", 409)

    monkeypatch.setattr(WorkflowService, "company_research", company_research)
    monkeypatch.setattr(DocumentService, "compile", missing_tool)
    runtime = CompanyRuntime()
    response, _, _ = approved_application(workspace, runtime)
    failed = finish_preparation(workspace, runtime, response)
    assert failed["status"] == "failed" and failed["error_code"] == "document_tool_missing", failed
    with workspace.store.connect() as connection:
        saved = connection.execute(
            "SELECT output_json FROM steps WHERE run_id=? AND stage='document_review' AND status='succeeded'",
            (failed["id"],),
        ).fetchone()
    checked = DocumentReviewCheckpoint.model_validate_json(saved["output_json"])
    assert len(checked.company_research.sources) == len(checked.additional_company_research.sources) == 8
    assert len(checked.company_research.limitations) == len(checked.additional_company_research.limitations) == 15
    assert len(checked.company_sources) == 16
    applications = ApplicationService(workspace)
    application = applications.get(response["application_id"])
    assert len(application["review"]["sources"]) == 16
    monkeypatch.setattr(DocumentService, "compile", compile_document)
    queued = client.post(f"/api/v1/applications/{application['id']}/document-runs", json={
        "expected_revision": application["revision"], "resume_from": failed["id"],
    })
    assert queued.status_code == 202, queued.text
    resumed_runtime = FixtureRuntime()
    completed = finish_preparation(workspace, resumed_runtime, {"preparation_run_id": queued.json()["run_id"]})
    assert completed["status"] == "succeeded", completed
    assert {call["stage"] for call in resumed_runtime.calls} == {"visual_cv_1", "visual_cover_letter_1"}
    assert len(applications.get(application["id"])["review"]["sources"]) == 16


@pytest.mark.parametrize("passed,findings,expected_calls", [
    (True, [], 1), (False, ["Synthetic failed inspection."], 2),
    (True, ["Synthetic unresolved inspection finding."], 2),
])
def test_checkpoints_only_reuse_positive_inspections_and_survive_repeated_continuation(
    confirmed, passed, findings, expected_calls,
):
    class InspectionRuntime(FixtureRuntime):
        async def generate(self, *args, **kwargs):
            result = await super().generate(*args, **kwargs)
            if len(self.calls) == 1:
                return VisualReview(passed=passed, findings=findings)
            return result

    runtime = InspectionRuntime()
    previous = run_operation(confirmed, runtime, "connection_test", {})
    for _ in range(2):
        previous = run_operation(confirmed, runtime, "connection_test", {"_retry_of": previous["id"]})
        assert previous["status"] == "succeeded", previous
    assert len(runtime.calls) == expected_calls
    with confirmed.store.connect() as connection:
        copied = connection.execute(
            "SELECT status FROM steps WHERE run_id=? AND stage='connection_test'", (previous["id"],),
        ).fetchone()
    assert copied["status"] == "succeeded"


def test_assessment_warnings_are_separate_from_truthful_document_grounding(confirmed, fixture_documents):
    class AssessmentRuntime(FixtureRuntime):
        async def generate(self, run_id, stage, data, *args, **kwargs):
            result = await super().generate(run_id, stage, data, *args, **kwargs)
            if stage.startswith("independent_review_"):
                result.assessment_warnings = ["The earlier assessment overstated ownership; the document does not."]
            return result

    runtime = AssessmentRuntime()
    response, _, _ = approved_application(confirmed, runtime)
    completed = finish_preparation(confirmed, runtime, response)
    assert completed["status"] == "succeeded", completed
    review = ApplicationService(confirmed).get(response["application_id"])["review"]
    assert review["assessment_warnings"] == ["The earlier assessment overstated ownership; the document does not."]
    assert not any(call["stage"].startswith("repair_") for call in runtime.calls)
