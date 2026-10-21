from conftest import FixtureRuntime
from test_workflows import approved_application, finish_preparation

from ai_job_search.applications import ApplicationService
from ai_job_search.models import Decision, DocumentContent
from ai_job_search.policy import Policy
from ai_job_search.profile import ProfileService
from ai_job_search.workflows import WorkflowService


def prepared(workspace):
    runtime = FixtureRuntime()
    response, _, _ = approved_application(workspace, runtime)
    result = finish_preparation(workspace, runtime, response)
    assert result["status"] == "succeeded", result
    return ApplicationService(workspace), response["application_id"]


def test_check_results_keep_their_revision_after_manual_edits(confirmed, fixture_documents):
    applications, application_id = prepared(confirmed)
    original = applications.get(application_id)["documents"]["cv"]
    assert original["checks_revision"] == original["compiled_revision"] == original["revision"]
    assert original["checked_at"] and not original["checks_stale"]
    content = DocumentContent.model_validate(original["content"])
    content.blocks[0].text += " Updated."
    edited = applications.save_content("edit-readiness", application_id, "cv", content, original["revision"])
    assert edited["checks"] == original["checks"]
    assert edited["checks_revision"] == original["revision"] != edited["revision"]
    assert edited["checks_stale"] and edited["stale"]
    assert "The saved text changed after these checks." in edited["checks_stale_reasons"]
    assert "The saved text changed after the PDF was verified." in edited["stale_reasons"]
    assert not applications.get(application_id)["documents"]["cover_letter"]["checks_stale"]


def test_current_failed_checks_are_not_confused_with_an_outdated_pdf(confirmed, fixture_documents):
    applications, application_id = prepared(confirmed)
    application = applications.get(application_id)
    original = application["documents"]["cv"]
    content = DocumentContent.model_validate(original["content"])
    content.blocks[0].text += " Changed."
    applications.save_content("edit-current-checks", application_id, "cv", content, original["revision"])
    application = applications.get(application_id)
    checks = [{"id": "pages", "label": "Page count", "status": "fail", "message": "Expected 2; found 3."}]
    applications.save_checks("current-failed-checks", application_id, {"cv": checks},
                             expected_revision=application["revision"],
                             input_hashes=ProfileService(confirmed).snapshot(),
                             policy_hash=Policy(confirmed).bundle("apply")["hash"])
    document = applications.get(application_id)["documents"]["cv"]
    assert document["stale"]
    assert not document["checks_stale"]
    assert document["checks_revision"] == document["revision"]
    assert document["checks"] == checks


def test_profile_changes_explain_why_check_results_are_outdated(confirmed, fixture_documents):
    applications, application_id = prepared(confirmed)
    profiles = ProfileService(confirmed)
    current = profiles.read()
    changed = current.data.model_copy(update={"phone": "+45 99999999"})
    profiles.apply("updated-phone-readiness", profiles.propose(changed, current.revision))
    document = applications.get(application_id)["documents"]["cv"]
    assert document["checks_stale"]
    assert "The profile changed after these checks." in document["checks_stale_reasons"]


def test_dismissing_a_document_review_does_not_claim_verification_or_start_checks(confirmed, fixture_documents):
    applications, application_id = prepared(confirmed)
    application = applications.get(application_id)
    runtime = FixtureRuntime()
    workflow = WorkflowService(confirmed, runtime)
    run_id = "dismiss-review-readiness"
    workflow.enqueue(run_id, "documents", {"application_id": application_id})
    approval_id = confirmed.store.create_approval(
        run_id, "document_review", "Review", {"application_id": application_id},
        {}, workflow.bundle(run_id)["hash"], ["reject"],
    )
    approval = confirmed.store.approval(approval_id)
    result = workflow.decide(approval_id, Decision(decision="reject", expected_revision=approval["revision"]))
    assert result["outcome"] == "review_dismissed"
    assert "no checks were started" in result["message"]
    assert result.get("preparation_run_id") is None
    assert applications.get(application_id)["documents"] == application["documents"]
    assert not runtime.calls
