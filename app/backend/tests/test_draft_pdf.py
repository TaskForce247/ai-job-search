from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from conftest import RESOURCES, FixtureRuntime
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from test_document_recovery import SUPPORTED_WORDING, correction
from test_workflows import finish_preparation

from ai_job_search.applications import ApplicationService
from ai_job_search.documents import DocumentService, render_cover, render_cv, tex_text
from ai_job_search.errors import AppError
from ai_job_search.models import Claim, Decision, DocumentBlock, DocumentContent
from ai_job_search.profile import ProfileService
from ai_job_search.workflows import WorkflowService


def test_renderers_preserve_multiline_text_fields_and_nested_bullets(profile):
    content = DocumentContent(blocks=[
        DocumentBlock(kind="heading", heading="Application", text="A separate introduction."),
        DocumentBlock(kind="paragraph", heading="Python experience", text="First line.\nSecond line.",
                      bullets=[Claim(text="[Python] Built reporting tools with 30% lower latency.")]),
        DocumentBlock(kind="entry", heading="Engineer", organization="Example Systems", location="Example City",
                      start="2020", end="2025", text="Worked on SQL reports.",
                      bullets=[Claim(text="A nested achievement & result.")]),
        DocumentBlock(kind="bullet", text="[SQL] Optimized database queries.",
                      bullets=[Claim(text="Another nested achievement.")]),
        DocumentBlock(text="Kind regards,\nSynthetic Candidate"),
    ])
    for source in (render_cover(profile, content, "Example"), render_cv(profile, content, RESOURCES)):
        for text in (
            "Application", "A separate introduction.", "Python experience", "First line.\\newline{} Second line.",
            "[Python] Built reporting tools with 30\\% lower latency.", "Engineer", "Example Systems",
            "Example City", "2020", "2025", "Worked on SQL reports.", "A nested achievement \\& result.",
            "[SQL] Optimized database queries.", "Another nested achievement.",
            "Kind regards,\\newline{} Synthetic Candidate",
        ):
            assert text in source
        assert "\\item {[Python]" in source and "\\item {[SQL]" in source
    assert tex_text("A\n\nB\r\nC") == "A\\par\nB\\newline{} C"


def test_pdf_content_check_catches_dropped_fields_and_bullets():
    service = DocumentService(RESOURCES)
    content = DocumentContent(blocks=[
        DocumentBlock(kind="entry", heading="Engineer", organization="Example Systems",
                      text="Built services.", bullets=[Claim(text="Reduced latency by 50%.")]),
        DocumentBlock(text="Kind regards,\nCandidate"),
    ])
    assert service.content_check(content, "Engineer Example Systems Built services. Kind regards, Candidate")["status"] == "fail"
    assert service.content_check(
        content, "Engineer Example Systems Built services. Reduced latency by 50%. Kind regards, Candidate",
    )["status"] == "pass"
    assert service.content_check(
        DocumentContent(blocks=[DocumentBlock(text="Data-processing automation.")]),
        "Data-proces-\nsing auto-\nmation.",
    )["status"] == "pass"
    assert service.content_check(
        DocumentContent(blocks=[DocumentBlock(text="Data-processing automation.")]),
        "Data-\nprocessing automation.",
    )["status"] == "pass"


@pytest.fixture
def local_pdf_compile(fixture_documents, monkeypatch):
    def compile_document(self, kind, profile, content, company, directory, *, draft=False):
        assert draft
        directory.mkdir(parents=True, exist_ok=True)
        text = "UNVERIFIED DRAFT " + " ".join(
            " ".join([block.heading, block.organization, block.location, block.start, block.end,
                      block.text, *(bullet.text for bullet in block.bullets)])
            for block in content.blocks
        ).replace("\n", " ")
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        writer = PdfWriter()
        page = writer.add_blank_page(595, 842)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): DictionaryObject({
                    NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }),
            }),
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 9 Tf 30 800 Td ({escaped}) Tj ET".encode("ascii"))
        page.replace_contents(stream)
        pdf = directory / f"{kind}.pdf"
        writer.write(pdf)
        return pdf

    monkeypatch.setattr(DocumentService, "compile", compile_document)


def prepare_for_api(client, app, profile):
    workspace = app.state.workspace
    profiles = ProfileService(workspace)
    profiles.apply("confirm", profiles.propose(profile, profiles.read().revision))
    response, _, approval, _ = correction(workspace)
    application = ApplicationService(workspace).get(response["application_id"])
    client.headers["Idempotency-Key"] = str(uuid4())
    return workspace, application, approval


def execute_pdf(client, workspace, application, kind="cover_letter"):
    response = client.post(f"/api/v1/applications/{application['id']}/documents/{kind}/pdf-runs",
                           json={"expected_revision": application["revision"]})
    assert response.status_code == 202, response.text
    runtime = FixtureRuntime()
    workspace.store.claim("fixture")
    asyncio.run(WorkflowService(workspace, runtime).execute(response.json()["run_id"]))
    assert not runtime.calls
    return workspace.store.run(response.json()["run_id"])


def test_local_pdf_can_be_downloaded_without_ai_or_resolving_content_review(api, profile, local_pdf_compile):
    client, app = api
    workspace, application, approval = prepare_for_api(client, app, profile)
    run = execute_pdf(client, workspace, application)
    assert run["status"] == "succeeded", run
    assert run["result"]["verified"] is False and run["cost_usd"] == 0
    assert len(run["artifacts"]) == 1
    artifact = run["artifacts"][0]
    assert artifact["name"] == "cover_letter-draft-unverified.pdf"
    download = client.get(f"/api/v1/artifacts/{artifact['id']}")
    assert download.status_code == 200 and download.headers["content-type"] == "application/pdf"
    assert "UNVERIFIED DRAFT" in PdfReader(workspace.path(artifact["relative_path"])).pages[0].extract_text()
    current = ApplicationService(workspace).get(application["id"])
    letter = current["documents"]["cover_letter"]
    assert letter["draft_pdf_artifact_id"] == artifact["id"] and not letter["draft_pdf_stale"]
    assert current["revision"] == application["revision"] and current["status"] == "preparing"
    assert current["documents"]["cv"]["content"] == application["documents"]["cv"]["content"]
    assert letter["content"] == application["documents"]["cover_letter"]["content"]
    assert letter["stale"] and not current["published"] and not ApplicationService(workspace).tracker()[1]
    assert workspace.store.approval(approval["id"])["status"] == "pending"
    assert current["latest_run_id"] == application["latest_run_id"]


@pytest.mark.parametrize("change", ["document", "profile"])
def test_draft_pdf_becomes_stale_after_source_changes(api, profile, local_pdf_compile, change):
    client, app = api
    workspace, application, _ = prepare_for_api(client, app, profile)
    assert execute_pdf(client, workspace, application)["status"] == "succeeded"
    if change == "document":
        document = application["documents"]["cover_letter"]
        content = DocumentContent.model_validate(document["content"])
        content.blocks[1].text = "Updated and saved wording."
        ApplicationService(workspace).save_content("edit", application["id"], "cover_letter", content, document["revision"])
    else:
        profiles = ProfileService(workspace)
        current = profiles.read()
        updated = current.data.model_copy(update={"phone": "+45 99999999"})
        profiles.apply("phone-change", profiles.propose(updated, current.revision))
    view = ApplicationService(workspace).get(application["id"])["documents"]["cover_letter"]
    assert view["draft_pdf_artifact_id"] and view["draft_pdf_stale"]


@pytest.mark.parametrize("failure", ["compiler", "content", "concurrent_edit"])
def test_failed_draft_pdf_does_not_expose_a_download_or_change_drafts(
    api, profile, local_pdf_compile, monkeypatch, failure,
):
    client, app = api
    workspace, application, approval = prepare_for_api(client, app, profile)
    original = DocumentService.compile
    if failure == "content":
        monkeypatch.setattr(DocumentService, "content_check", lambda *args: {
            "status": "fail", "message": "The saved achievement is missing.",
        })
    else:
        def compile_document(*args, **kwargs):
            if failure == "compiler":
                raise AppError("tool_failed", "Synthetic compilation failure.", 422)
            pdf = original(*args, **kwargs)
            content = DocumentContent.model_validate(application["documents"]["cover_letter"]["content"])
            content.blocks[1].text = "Keep this concurrent manual edit."
            ApplicationService(workspace).save_content(
                "concurrent", application["id"], "cover_letter", content,
                application["documents"]["cover_letter"]["revision"],
            )
            return pdf
        monkeypatch.setattr(DocumentService, "compile", compile_document)
    run = execute_pdf(client, workspace, application)
    assert run["status"] == "failed" and not run["artifacts"], run
    current = ApplicationService(workspace).get(application["id"])
    assert not current["documents"]["cover_letter"].get("draft_pdf_artifact_id")
    assert current["documents"]["cv"] == application["documents"]["cv"]
    assert workspace.store.approval(approval["id"])["status"] == "pending"


def test_pdf_endpoint_checks_revision_and_is_idempotent(api, profile, fixture_documents):
    client, app = api
    workspace, application, _ = prepare_for_api(client, app, profile)
    path = f"/api/v1/applications/{application['id']}/documents/cv/pdf-runs"
    first = client.post(path, json={"expected_revision": application["revision"]})
    repeated = client.post(path, json={"expected_revision": application["revision"]})
    assert first.status_code == repeated.status_code == 202 and first.json() == repeated.json()
    client.headers["Idempotency-Key"] = str(uuid4())
    assert client.post(path, json={"expected_revision": "outdated"}).status_code == 409
    assert client.post(path, json={"expected_revision": application["revision"]}).status_code == 409
    assert client.post(path.replace("/cv/", "/invalid/"), json={"expected_revision": application["revision"]}).status_code == 422
    workspace.store.finish(first.json()["run_id"], status="cancelled")


def test_ai_edits_can_supersede_pending_content_review_without_applying_them(api, profile, fixture_documents):
    client, app = api
    workspace, application, approval = prepare_for_api(client, app, profile)
    result = client.post(f"/api/v1/applications/{application['id']}/document-runs", json={
        "expected_revision": application["revision"], "repair": True,
    })
    assert result.status_code == 202, result.text
    assert workspace.store.approval(approval["id"])["status"] == "rejected"
    class EditRuntime(FixtureRuntime):
        async def generate(self, run_id, stage, data, *args, **kwargs):
            result = await super().generate(run_id, stage, data, *args, **kwargs)
            if stage == "rewrite":
                result.cover_letter.blocks[1].text = SUPPORTED_WORDING
            return result

    runtime = EditRuntime()
    completed = finish_preparation(workspace, runtime, {"preparation_run_id": result.json()["run_id"]})
    assert completed["status"] == "awaiting_user", completed
    proposed = completed["approvals"][0]
    assert proposed["kind"] == "document_rewrite" and proposed["choices"] == ["approve", "reject"]
    assert proposed["proposal"]["after"]["cover_letter"]["blocks"][1]["text"] == SUPPORTED_WORDING
    rewrite = next(call for call in runtime.calls if call["stage"] == "rewrite")
    assert rewrite["data"]["actual_verification_findings"][0]["suggestion"]
    assert ApplicationService(workspace).get(application["id"])["documents"] == application["documents"]
    assert not completed["artifacts"]
    assert not any(call["stage"].startswith("compile") for call in runtime.calls)
    # A second AI request cannot bypass an unresolved rewrite decision.
    client.headers["Idempotency-Key"] = str(uuid4())
    blocked = client.post(f"/api/v1/applications/{application['id']}/document-runs", json={
        "expected_revision": application["revision"], "repair": True,
    })
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "pending_document_decision"
    WorkflowService(workspace).decide(proposed["id"], Decision(decision="reject", expected_revision=proposed["revision"]))


def test_accepted_ai_edits_still_require_full_verification_before_publication(api, profile, fixture_documents):
    client, app = api
    workspace, application, _ = prepare_for_api(client, app, profile)
    queued = client.post(f"/api/v1/applications/{application['id']}/document-runs", json={
        "expected_revision": application["revision"], "repair": True,
    })
    completed = finish_preparation(workspace, FixtureRuntime(), {"preparation_run_id": queued.json()["run_id"]})
    approval = completed["approvals"][0]
    accepted = WorkflowService(workspace).decide(
        approval["id"], Decision(decision="approve", expected_revision=approval["revision"]),
    )
    application = ApplicationService(workspace).get(application["id"])
    assert application["status"] == "preparing" and not application["published"]
    runtime = FixtureRuntime()
    checked = finish_preparation(workspace, runtime, accepted)
    assert checked["status"] == "succeeded", checked
    assert any(call["stage"] == "independent_review_1" for call in runtime.calls)
    assert any(call["stage"] == "visual_cover_letter_1" for call in runtime.calls)
    assert ApplicationService(workspace).get(application["id"])["status"] == "drafted"


def test_targeted_rewrite_is_allowed_while_content_review_is_pending(api, profile, fixture_documents):
    client, app = api
    _, application, _ = prepare_for_api(client, app, profile)
    result = client.post(f"/api/v1/applications/{application['id']}/document-runs", json={
        "expected_revision": application["revision"], "instruction": "Restore the supported application body.",
    })
    assert result.status_code == 202, result.text
