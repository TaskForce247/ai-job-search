from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Generic, Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

Text = Annotated[str, Field(max_length=20_000)]
Short = Annotated[str, Field(max_length=500)]
Revision = Annotated[str, Field(min_length=1, max_length=64)]
RunStatus = Literal["queued", "running", "awaiting_user", "succeeded", "failed", "cancelled", "interrupted"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Language(Model):
    name: Short
    level: Short


class Experience(Model):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: Short = ""
    company: Short = ""
    location: Short = ""
    start: Short = ""
    end: Short = ""
    bullets: list[Text] = Field(default_factory=list, max_length=40)


class Education(Model):
    id: str = Field(default_factory=lambda: str(uuid4()))
    degree: Short = ""
    institution: Short = ""
    start: Short = ""
    end: Short = ""
    details: Text = ""


class Project(Model):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: Short = ""
    description: Text = ""
    bullets: list[Text] = Field(default_factory=list, max_length=30)


class Profile(Model):
    name: Short = ""
    email: Short = ""
    phone: Short = ""
    location: Short = ""
    linkedin: Short = ""
    github: Short = ""
    headline: Short = ""
    summary: Text = ""
    employment_status: Short = ""
    cv_language: Short = "English"
    languages: list[Language] = Field(default_factory=list, max_length=40)
    skills: list[Short] = Field(default_factory=list, max_length=200)
    goals: list[Text] = Field(default_factory=list, max_length=40)
    deal_breakers: list[Text] = Field(default_factory=list, max_length=40)
    experiences: list[Experience] = Field(default_factory=list, max_length=60)
    education: list[Education] = Field(default_factory=list, max_length=30)
    projects: list[Project] = Field(default_factory=list, max_length=40)
    behavioral: Text = ""
    writing_style: Text = ""

    @field_validator("email")
    @classmethod
    def email_is_literal(cls, value: str) -> str:
        if value and ("@" not in value or any(c.isspace() for c in value)):
            raise ValueError("Enter a valid email address.")
        return value


class Evidence(Model):
    id: str
    source: str
    section: str
    text: str


class ProfileView(Model):
    data: Profile
    revision: str
    evidence: list[Evidence]
    warnings: list[str] = Field(default_factory=list)


class ProfileChange(Model):
    profile: Profile
    expected_revision: Revision


class Settings(Model):
    model: Short = "claude-sonnet-4-6"
    privacy_consent: bool = False
    max_run_cost_usd: float = Field(default=3.0, ge=0.05, le=100)
    max_turns: int = Field(default=8, ge=1, le=30)
    stage_timeout_seconds: int = Field(default=180, ge=30, le=600)
    search_location: Short = ""
    search_country: str = Field(default="", max_length=2)
    search_query: Short = ""
    onboarding_complete: bool = False


class SettingsChange(Model):
    settings: Settings
    expected_revision: Revision


class AIConnection(Model):
    api_key: str = Field(min_length=10, max_length=500, exclude=True)


class PostingInput(Model):
    title: Short = ""
    company: Short = ""
    location: Short = ""
    url: str = Field(default="", max_length=2048)
    text: str = Field(default="", max_length=120_000)
    deadline: date | None = None

    @field_validator("url")
    @classmethod
    def url_is_http(cls, value: str) -> str:
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("Use an HTTP or HTTPS posting URL.")
        return value


class SearchInput(Model):
    query: Short
    location: Short = ""
    country: str = Field(default="", max_length=2)
    portals: list[str] = Field(default_factory=list, max_length=6)
    limit: int = Field(default=20, ge=1, le=50)


class RankingInput(Model):
    job_ids: list[str] = Field(default_factory=list, max_length=50)
    limit: int = Field(default=10, ge=1, le=50)
    rerank: bool = False


class EvaluationInput(Model):
    job_id: str


class Scores(Model):
    technical: float = Field(ge=0, le=100)
    experience: float = Field(ge=0, le=100)
    behavioral: float = Field(ge=0, le=100)
    career: float = Field(ge=0, le=100)


class Gate(Model):
    verdict: Literal["PASS", "FAIL", "FLAG"]
    note: Text = ""


class Requirement(Model):
    term: Short
    priority: Literal["required", "preferred"] = "required"
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    gap: bool = False


class Evaluation(Model):
    scores: Scores | None = None
    location: Gate
    language: Gate
    eligibility: Gate
    strengths: list[Text] = Field(default_factory=list, max_length=10)
    gaps: list[Text] = Field(default_factory=list, max_length=15)
    requirements: list[Requirement] = Field(default_factory=list, max_length=80)
    recommendation: Text
    posting_language: Short = "English"
    deadline: date | None = None

    @model_validator(mode="after")
    def gates_before_scores(self) -> Evaluation:
        failed = any(gate.verdict == "FAIL" for gate in (self.location, self.language, self.eligibility))
        if failed and self.scores is not None:
            raise ValueError("A failed gate must not be scored.")
        if not failed and self.scores is None:
            raise ValueError("A passing or flagged evaluation requires dimension scores.")
        if any(gate.verdict in ("FAIL", "FLAG") and not gate.note.strip()
               for gate in (self.location, self.language, self.eligibility)):
            raise ValueError("Failed or flagged gates need a quoted requirement and explanation.")
        return self


class Claim(Model):
    id: str = Field(default_factory=lambda: str(uuid4()))
    text: Text
    evidence_ids: list[str] = Field(
        default_factory=list, max_length=30,
        description="Supporting IDs from the supplied evidence snapshot. Required for every achievement; preserve valid references during revision.",
    )


class DocumentBlock(Model):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: Literal["heading", "paragraph", "bullet", "entry"] = "paragraph"
    text: Text = ""
    heading: Short = ""
    organization: Short = ""
    location: Short = ""
    start: Short = ""
    end: Short = ""
    evidence_ids: list[str] = Field(
        default_factory=list, max_length=30,
        description="Supporting IDs from the supplied evidence snapshot. Required for entry and bullet blocks. Preserve references when revising supported claims; never invent IDs.",
    )
    bullets: list[Claim] = Field(default_factory=list, max_length=30)


class DocumentContent(Model):
    language: Short = "English"
    blocks: list[DocumentBlock] = Field(default_factory=list, max_length=100)


class DraftPair(Model):
    cv: DocumentContent
    cover_letter: DocumentContent


class ReviewFinding(Model):
    kind: Literal["grounding", "targeting", "style", "company", "consistency"]
    message: Text
    block_id: str = ""


class ResearchSource(Model):
    url: str = Field(max_length=2048)
    claim: Text
    quote: Text


class Review(Model):
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=80)
    sources: list[ResearchSource] = Field(default_factory=list, max_length=8)
    company_claims: list[Text] = Field(
        default_factory=list, max_length=8,
        description="Every factual claim about the target employer in the revised documents, verbatim. Exclude the candidate's employment history.",
    )
    assessment_warnings: list[Text] = Field(default_factory=list, max_length=30)
    revised: DraftPair


class VisualReview(Model):
    passed: bool
    findings: list[Text] = Field(default_factory=list, max_length=30)


class DocumentIssue(Model):
    kind: Literal["grounding", "company", "evidence", "layout"] = "grounding"
    document: Literal["cv", "cover_letter"] | None = None
    block_id: str = ""
    claim_id: str = ""
    quote: Text = ""
    message: Text
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    suggestion: Text = ""
    needs_confirmation: bool = False


class GroundingReview(Model):
    supported: bool
    findings: list[Text] = Field(
        default_factory=list, max_length=60,
        description="Blocking candidate-claim problems in the final documents only. Positive observations and assessment commentary belong in notes, not findings.",
    )
    issues: list[DocumentIssue] = Field(default_factory=list, max_length=60)
    notes: list[Text] = Field(default_factory=list, max_length=30)
    assessment_warnings: list[Text] = Field(default_factory=list, max_length=30)
    company_claims: list[Text] = Field(default_factory=list, max_length=8)


class CompanyResearch(Model):
    sources: list[ResearchSource] = Field(default_factory=list, max_length=8)
    limitations: list[Text] = Field(default_factory=list, max_length=15)


class DocumentReviewCheckpoint(Model):
    documents_hash: str
    review: Review
    grounding: GroundingReview
    company_research: CompanyResearch
    additional_company_research: CompanyResearch = Field(default_factory=CompanyResearch)

    @property
    def company_sources(self) -> list[ResearchSource]:
        return [*self.company_research.sources, *self.additional_company_research.sources]


class ExtractedProfile(Model):
    profile: Profile
    uncertainties: list[Text] = Field(default_factory=list, max_length=40)


class ExtractedPosting(Model):
    title: Short
    company: Short
    location: Short = ""
    description: Text
    deadline: date | None = None


class DocumentEdit(Model):
    content: DocumentContent
    expected_revision: Revision


class DocumentRunInput(Model):
    expected_revision: Revision
    instruction: Text = ""
    resume_from: str | None = Field(default=None, max_length=100)


class ApplicationInput(Model):
    evaluation_run_id: str | None = None
    company: Short = ""
    role: Short = ""
    source: str = Field(default="", max_length=2048)
    status: Literal["applied", "interview", "offer", "hired", "rejected", "no_response",
                    "offer_declined", "withdrawn"] = "applied"
    applied_on: date | None = None


class SubmissionInput(Model):
    expected_revision: Revision
    submitted_on: date
    notes: Text = ""


class OutcomeInput(Model):
    expected_revision: Revision
    status: Literal["applied", "interview", "offer", "hired", "rejected", "no_response",
                    "offer_declined", "withdrawn"]
    occurred_on: date
    stage: Short = ""
    notes: Text = ""
    submitted_on: date | None = None


class Decision(Model):
    decision: str = Field(max_length=100)
    expected_revision: Revision
    confirmation: str = Field(default="", max_length=100)


class PortalChange(Model):
    enabled: bool
    acknowledged: bool = False
    expected_revision: Revision


class WorkspaceInput(Model):
    path: str = Field(min_length=1, max_length=4096)
    create: bool = False

    @field_validator("path")
    @classmethod
    def absolute_local_path(cls, value: str) -> str:
        if "\x00" in value or not Path(value).is_absolute():
            raise ValueError("Enter the full absolute path to a local workspace folder.")
        return value


class BackupInput(Model):
    passphrase: str = Field(default="", max_length=500, exclude=True)


class ResetInput(Model):
    scope: Literal["profile", "documents", "all"]


class BootstrapInput(Model):
    token: str = Field(min_length=20, max_length=200)


class JobChange(Model):
    saved: bool
    expected_revision: Revision


class AcceptedInput(Model):
    accepted: bool


class Check(Model):
    id: str
    label: str
    status: Literal["pass", "fail", "skipped", "pending"]
    message: str


class DocumentView(Model):
    content: DocumentContent
    revision: str
    stale: bool
    checks: list[Check]
    pdf_artifact_id: str | None = None
    source_artifact_id: str | None = None
    keyword_coverage: list[dict[str, JsonValue]] = Field(default_factory=list)
    model_config = ConfigDict(extra="allow")


class JobView(Model):
    id: str
    key: str
    title: str
    company: str
    location: str
    description: str
    url: str
    portal: str
    source: str
    status: str
    revision: str
    saved: bool
    first_seen: str | None = None
    posted_date: str | None = None
    deadline: str | None = None
    fit: str | None = None
    rank_score: float | None = None
    rank_verdict: str | None = None
    source_host: str | None = None
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="allow")


class ApplicationView(Model):
    id: str
    company: str
    role: str
    status: str
    source: str = ""
    deadline: str | None = None
    applied_on: str | None = None
    fit_rating: int | None = None
    revision: str
    archive_dir: str
    documents: dict[str, DocumentView]
    timeline: list[dict[str, JsonValue]]
    published: bool
    legacy: bool
    evaluation: dict[str, JsonValue] | None = None
    review: dict[str, JsonValue] | None = None
    last_run_id: str | None = None
    latest_run_id: str | None = None
    preparation_cost_usd: float = 0
    preparation_attempts: int = 0
    review_checkpoint_available: bool = False
    next_run_limit_usd: float | None = None
    content_issues: list[DocumentIssue] = Field(default_factory=list)
    model_config = ConfigDict(extra="allow")


class ApprovalView(Model):
    id: str
    run_id: str
    kind: str
    title: str
    revision: str
    status: str
    choices: list[str]
    proposal: dict[str, JsonValue]
    model_config = ConfigDict(extra="allow")


class RunView(Model):
    id: str
    kind: str
    status: RunStatus
    stage: str
    message: str
    input: dict[str, JsonValue]
    result: JsonValue = None
    error: str | None = None
    error_code: str | None = None
    cost_usd: float
    created_at: str
    updated_at: str
    approvals: list[ApprovalView] = Field(default_factory=list)
    artifacts: list[dict[str, JsonValue]] = Field(default_factory=list)
    model_config = ConfigDict(extra="allow")


Item = TypeVar("Item")


class Page(Model, Generic[Item]):
    items: list[Item]
    next_cursor: str | None
    total: int


class Queued(Model):
    run_id: str
    application_id: str | None = None


class SettingsView(Model):
    settings: Settings
    revision: str
    ai_connected: bool
