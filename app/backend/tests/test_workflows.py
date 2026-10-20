from __future__ import annotations

import asyncio
from datetime import date

import pytest
from conftest import FixtureRuntime, run_operation
from test_api import POSTING

from ai_job_search.applications import ApplicationService
from ai_job_search.documents import DocumentService
from ai_job_search.errors import AppError
from ai_job_search.jobs import JobService
from ai_job_search.models import Decision, DocumentContent, OutcomeInput, SubmissionInput
from ai_job_search.profile import ProfileService
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
    assert result["status"] == "failed" and result["error_code"] == "grounding_failed"
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
    assert result["status"] == "failed" and result["error_code"] == "repair_limit_reached"
    assert len([call for call in runtime.calls if call["stage"].startswith("independent_review_")]) == 3
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
    runtime = FixtureRuntime()
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
    assert result["status"] == "failed" and result["error_code"] == "company_claims_unverified"
    assert result["result"]["claims"] == ["Second independent claim"]
    assert not service.applications.tracker()[1]
