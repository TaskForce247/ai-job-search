from __future__ import annotations

import asyncio
import io
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from ai_job_search.api import create_app
from ai_job_search.config import Config
from ai_job_search.documents import DocumentService
from ai_job_search.models import (
    Claim,
    CompanyResearch,
    DocumentBlock,
    DocumentContent,
    DraftPair,
    Education,
    Evaluation,
    Experience,
    ExtractedPosting,
    ExtractedProfile,
    Gate,
    GroundingReview,
    Language,
    Profile,
    Review,
    Scores,
    VisualReview,
)
from ai_job_search.profile import ProfileService
from ai_job_search.workflows import WorkflowService
from ai_job_search.workspace import Workspace

RESOURCES = Path(__file__).resolve().parents[3]


@pytest.fixture
def workspace(tmp_path):
    workspace = Workspace(tmp_path / "Career workspace \u00e9", RESOURCES, create=True)
    try:
        yield workspace
    finally:
        workspace.close()


@pytest.fixture
def profile():
    return Profile(
        name="Synthetic Candidate", email="candidate@example.invalid", phone="+45 12345678",
        location="Example City", headline="Software developer", summary="Builds reliable Python services.",
        languages=[Language(name="English", level="C2")], skills=["Python", "SQL", "TypeScript"],
        goals=["Build useful, reliable tools."], deal_breakers=["No relocation."],
        experiences=[Experience(
            title="Software Developer", company="Example Systems", location="Example City",
            start="2020", end="2025", bullets=["Built Python services for an internal reporting team."],
        )],
        education=[Education(degree="BSc Computing", institution="Example University", start="2016", end="2019")],
    )


@pytest.fixture
def confirmed(workspace, profile):
    service = ProfileService(workspace)
    proposal = service.propose(profile, service.read().revision)
    service.apply(str(uuid4()), proposal)
    return workspace


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr("ai_job_search.runtime.Credentials.connected", lambda: False)
    app = create_app(Config(workspace=tmp_path / "API workspace", resources=RESOURCES, start_worker=False))
    with TestClient(app, base_url="http://127.0.0.1:8765", headers={"Origin": "http://127.0.0.1:8765"}) as client:
        response = client.post("/api/v1/session/bootstrap", json={"token": app.state.sessions.mint()})
        assert response.status_code == 200
        client.headers.update({"X-CSRF-Token": response.json()["csrf_token"], "Idempotency-Key": str(uuid4())})
        yield client, app


def assessment(*, failed: bool = False, flagged: bool = False) -> Evaluation:
    return Evaluation(
        scores=None if failed else Scores(technical=82.5, experience=80, behavioral=75, career=90),
        location=Gate(verdict="PASS"), eligibility=Gate(verdict="PASS"),
        language=Gate(
            verdict="FAIL" if failed else "FLAG" if flagged else "PASS",
            note='"Fluent Polish required"; Polish is not declared.' if failed else
            '"Native English required"; candidate declares C2, not native.' if flagged else "",
        ),
        strengths=[] if failed else ["Relevant Python service experience."],
        gaps=[] if failed else ["No unsupported additional skills were inferred."],
        requirements=[{"term": "Python", "priority": "required", "evidence_ids": [], "gap": False}],
        recommendation="Do not draft." if failed else "Apply with the stated caveats.",
    )


class FixtureRuntime:
    """Deterministic, injected test transport; production never imports this class."""

    def __init__(self, *, failed_gate=False, flagged_gate=False, unsupported=False):
        self.calls = []
        self.failed_gate = failed_gate
        self.flagged_gate = flagged_gate
        self.unsupported = unsupported

    async def generate(self, run_id, stage, data, policy, schema, limits, directory, **kwargs):
        self.calls.append({"run_id": run_id, "stage": stage, "schema": schema, "directory": directory, "data": data})
        if schema is CompanyResearch:
            return CompanyResearch(sources=[], limitations=["Offline test fixture; no live company research."])
        if schema is Evaluation:
            return assessment(failed=self.failed_gate, flagged=self.flagged_gate)
        if schema is DraftPair:
            if "drafts" in data:
                return DraftPair.model_validate(data["drafts"])
            evidence = [item["id"] for item in data["evidence"] if not item["id"].startswith("company:")][:8]
            blocks = [
                DocumentBlock(kind="heading", text="Professional Experience"),
                DocumentBlock(kind="entry", heading="Software Developer", organization="Example Systems",
                              start="2020", end="2025", evidence_ids=evidence[:1],
                              bullets=[Claim(text="Built Python services for an internal reporting team.", evidence_ids=evidence)]),
            ]
            return DraftPair(
                cv=DocumentContent(blocks=blocks),
                cover_letter=DocumentContent(blocks=[
                    DocumentBlock(text="Dear Hiring Manager,"),
                    DocumentBlock(text="I build Python services and would like to discuss the role.", evidence_ids=evidence),
                    DocumentBlock(text="Kind regards,\nSynthetic Candidate"),
                ]),
            )
        if schema is Review:
            return Review(findings=[], sources=[], revised=DraftPair.model_validate(data["drafts"]))
        if schema is GroundingReview:
            return GroundingReview(supported=not self.unsupported,
                                   findings=["Unsupported metric."] if self.unsupported else [])
        if schema is VisualReview:
            return VisualReview(passed=True, findings=[])
        if schema is ExtractedPosting:
            return ExtractedPosting(title="Software Developer", company="Example Hiring", description=data["posting"][:20_000])
        if schema is ExtractedProfile:
            return ExtractedProfile(profile=Profile.model_validate(data["current_profile"]), uncertainties=[])
        raise AssertionError(f"Unexpected fixture schema {schema}")


def run_operation(workspace: Workspace, runtime: FixtureRuntime, kind: str, inputs: dict) -> dict:
    service = WorkflowService(workspace, runtime)
    run_id = str(uuid4())
    service.enqueue(run_id, kind, inputs)
    workspace.store.claim("fixture")
    asyncio.run(service.execute(run_id))
    return workspace.store.run(run_id)


@pytest.fixture
def fixture_documents(monkeypatch):
    def compile_document(self, kind, profile, content, company, directory):
        directory.mkdir(parents=True, exist_ok=True)
        pdf = directory / (kind + ".pdf")
        writer = PdfWriter()
        for _ in range(2 if kind == "cv" else 1):
            writer.add_blank_page(595, 842)
        with io.BytesIO() as buffer:
            writer.write(buffer)
            pdf.write_bytes(buffer.getvalue())
        (directory / (kind + ".tex")).write_text("% Synthetic injected compilation fixture\n", encoding="utf-8")
        return pdf

    def verify(self, kind, pdf, profile, requirements):
        checks = [{"id": "fixture_mechanical", "label": "Injected mechanical fixture", "status": "pass",
                   "message": "Deterministic integration test, not a real template compilation."}]
        return checks, f"{profile.name} {profile.email} {profile.phone} Python", []

    monkeypatch.setattr(DocumentService, "compile", compile_document)
    monkeypatch.setattr(DocumentService, "verify", verify)
    monkeypatch.setattr(DocumentService, "render_pages", staticmethod(lambda path: [b"fixture-image"]))
