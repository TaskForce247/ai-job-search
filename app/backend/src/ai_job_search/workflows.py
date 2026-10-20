from __future__ import annotations

import asyncio
import contextlib
import io
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel

from .applications import ApplicationService
from .backups import BackupService
from .documents import DocumentService
from .errors import AppError
from .jobs import JobService
from .models import (
    CompanyResearch,
    Decision,
    DraftPair,
    Evaluation,
    ExtractedPosting,
    ExtractedProfile,
    GroundingReview,
    PostingInput,
    Profile,
    Review,
    SearchInput,
    VisualReview,
)
from .network import PostingHTML, WebClient
from .policy import Policy, load_helper
from .portals import PortalService
from .profile import ProfileService
from .runtime import AgentRuntime
from .storage import digest, encoded
from .workspace import JOBS_PATH, Workspace

POLICY_KIND = {
    "profile_change": "setup", "extract_profile": "setup", "posting_import": "rank",
    "workspace_import": "setup", "search": "scrape", "rank": "rank", "evaluate": "apply",
    "documents": "apply", "connection_test": "setup", "reset": "setup", "restore": "setup",
    "dependency_repair": "setup",
}


class WorkflowService:
    def __init__(self, workspace: Workspace, runtime: AgentRuntime | None = None):
        self.workspace = workspace
        self.store = workspace.store
        self.profiles = ProfileService(workspace)
        self.jobs = JobService(workspace)
        self.applications = ApplicationService(workspace)
        self.policy = Policy(workspace)
        self.runtime = runtime or AgentRuntime(self.store)
        self.web = WebClient(workspace.resources)

    def enqueue(self, run_id: str, kind: str, inputs: dict, *, pinned_policy: dict | None = None) -> str:
        bundle = pinned_policy or self.policy.bundle(POLICY_KIND[kind])
        relative = str(Path(".app", "runs", run_id, "policy.json"))
        with self.workspace.lock():
            existing = self.workspace.read(relative)
            if existing is not None and json.loads(existing)["hash"] != bundle["hash"]:
                raise AppError("idempotency_conflict", "The policy changed for this operation key. Start a new operation.", 409)
            if existing is None:
                self.workspace._durable_write(self.workspace.path(relative), encoded(bundle).encode("utf-8"))
            return self.store.enqueue(run_id, kind, inputs, bundle["hash"])

    def bundle(self, run_id: str) -> dict:
        content = self.workspace.text(str(Path(".app", "runs", run_id, "policy.json")))
        try:
            bundle = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AppError("policy_snapshot_missing", "The saved policy snapshot is unavailable. Start a new operation.", 409) from exc
        if digest(bundle["documents"]) != bundle["hash"] or bundle["hash"] != self.store.run(run_id)["policy_hash"]:
            raise AppError("policy_snapshot_changed", "The saved workflow policy changed. Start a new operation.", 409)
        return bundle

    async def ai(self, run_id: str, stage: str, data: dict, schema: type[BaseModel], *,
                 images: list[bytes] | None = None, company_search: str | None = None):
        run = self.store.run(run_id)
        bundle = self.bundle(run_id)
        inputs = {"data": data, "images": [digest(image) for image in images or []], "policy": bundle["hash"]}
        previous_id = run["input"].get("_retry_of")
        if previous_id:
            with self.store.connect() as connection:
                cached = connection.execute(
                    "SELECT output_json FROM steps WHERE run_id=? AND stage=? AND input_hash=? "
                    "AND status='succeeded' ORDER BY completed_at DESC LIMIT 1",
                    (previous_id, stage, digest(inputs)),
                ).fetchone()
            if cached and cached["output_json"]:
                result = schema.model_validate_json(cached["output_json"])
                self.store.event(run_id, "checkpoint_reused", {"stage": stage}, stage)
                return result
        step_id = self.store.stage(run_id, stage, stage.replace("_", " ").capitalize(), inputs)
        stage_path = Path(".app", "runs", run_id, "stages", digest(stage)[:16] + ".input.json")
        self.workspace._durable_write(self.workspace.path(str(stage_path)), encoded(inputs).encode("utf-8"))
        settings, _ = self.store.settings()
        result = await self.runtime.generate(
            run_id, stage, data, bundle, schema, settings,
            self.workspace.path(str(Path(".app", "runs", run_id, "runtime-" + digest(stage)[:16]))),
            images=images, company_search=company_search,
        )
        self.store.finish_step(step_id, result.model_dump(mode="json"))
        return result

    async def execute(self, run_id: str) -> None:
        run = self.store.run(run_id)
        inputs = run["input"]
        try:
            handlers = {
                "profile_change": self.profile_change, "extract_profile": self.extract_profile,
                "posting_import": self.posting_import, "workspace_import": self.workspace_import,
                "search": self.search, "rank": self.rank, "evaluate": self.evaluate,
                "documents": self.documents, "connection_test": self.connection_test,
                "reset": self.reset, "restore": self.restore, "dependency_repair": self.dependency_repair,
            }
            payload = {key: value for key, value in inputs.items() if not key.startswith("_")}
            result = await handlers[run["kind"]](run_id, payload)
            if self.store.run(run_id)["status"] == "running":
                self.store.finish(run_id, result)
        except AppError as exc:
            self.store.finish(run_id, exc.detail, status="cancelled" if exc.code == "cancelled" else "failed",
                              error=exc.message, code=exc.code)
        except asyncio.CancelledError:
            self.store.finish(run_id, status="interrupted", error="Work stopped. Review saved progress before retrying.",
                              code="worker_interrupted")
            raise
        except Exception as exc:
            self.store.event(run_id, "internal_error", {"category": type(exc).__name__})
            self.store.finish(run_id, status="failed",
                              error="An unexpected local error stopped this operation. Saved files were retained; inspect Activity and retry.",
                              code="internal_error")

    async def profile_change(self, run_id: str, inputs: dict) -> None:
        self.store.stage(run_id, "preview", "Preparing a reviewable profile change.")
        proposal = self.profiles.propose(Profile.model_validate(inputs["profile"]), inputs["expected_revision"])
        self.store.create_approval(run_id, "profile_change", "Confirm your profile changes", proposal,
                                   proposal["expected"], self.bundle(run_id)["hash"], ["approve", "reject"])

    async def extract_profile(self, run_id: str, inputs: dict) -> None:
        source = self.workspace.read(inputs["source"])
        if source is None or digest(source) != inputs["source_hash"]:
            raise AppError("import_changed", "The imported document changed. Upload it again.", 409)
        if inputs["source"].lower().endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(source))
            if len(reader.pages) > 30:
                raise AppError("import_too_many_pages", "Import a CV with 30 pages or fewer.")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            if len(text.strip()) < 80:
                raise AppError("scanned_pdf", "This PDF has no usable text layer. Export a text PDF or enter your profile manually.")
        else:
            try:
                text = source.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise AppError("invalid_encoding", "Upload UTF-8 text or a text-based PDF.") from exc
        current = self.profiles.read()
        result = await self.ai(run_id, "extract_profile", {
            "document": text[:200_000], "current_profile": current.data.model_dump(mode="json"),
            "existing_evidence": [item.model_dump(mode="json") for item in current.evidence],
            "task": "Propose an additive/conflict-aware extraction under /setup. Do not infer unsupported facts. Preserve existing facts unless the source explicitly corrects them.",
        }, ExtractedProfile)
        proposal = self.profiles.propose(result.profile, current.revision, allow_unmapped=True)
        proposal["uncertainties"] = result.uncertainties
        self.store.create_approval(run_id, "profile_change", "Review facts extracted from your document", proposal,
                                   proposal["expected"], self.bundle(run_id)["hash"], ["approve", "reject"])

    async def posting_import(self, run_id: str, inputs: dict) -> dict:
        posting = PostingInput.model_validate(inputs)
        record = posting.model_dump(mode="json")
        text = posting.text if posting.text.strip() else ""
        metadata = {}
        needs_confirmation = False
        if not text:
            if not posting.url:
                raise AppError("posting_required", "Paste the full posting or enter its public URL.")
            self.store.stage(run_id, "fetch_posting", "Retrieving the actual posting under its access policy.")
            page = await asyncio.to_thread(self.web.fetch, posting.url)
            parser = PostingHTML()
            parser.feed(page.text)
            metadata = parser.posting() or {}
            text = metadata.get("description") or parser.text
            record["url"] = page.url
        if len(text) < 200:
            raise AppError("posting_incomplete", "The posting has too little content to evaluate. Paste the full description, requirements, and eligibility details.")
        if not posting.title or not posting.company:
            if metadata.get("title") and metadata.get("company"):
                record["title"] = posting.title or metadata["title"]
                record["company"] = posting.company or metadata["company"]
            else:
                result = await self.ai(run_id, "extract_posting", {"posting": text}, ExtractedPosting)
                record["title"] = posting.title or result.title
                record["company"] = posting.company or result.company
                metadata = result.model_dump(mode="json")
                needs_confirmation = True
        if not record["title"].strip() or not record["company"].strip():
            raise AppError("posting_metadata_missing", "Supply the employer and job title explicitly.")
        record["location"] = posting.location or metadata.get("location") or ""
        record["description"] = text
        record.pop("text", None)
        record["deadline"] = posting.deadline.isoformat() if posting.deadline else metadata.get("deadline")
        record.update(portal="manual", source="pasted" if posting.text else "web")
        if needs_confirmation:
            proposal = {"record": record, "expected": self.workspace.snapshot([JOBS_PATH])}
            self.store.create_approval(run_id, "posting_import", "Confirm the employer and role extracted from this posting",
                                       proposal, proposal["expected"], self.bundle(run_id)["hash"], ["approve", "reject"])
            return {"confirmation_required": True}
        keys = self.jobs.ingest(run_id + ":posting", [record])
        return {"job_id": keys[0], "message": "Posting saved. Evaluate fit before preparing documents."}

    async def workspace_import(self, run_id: str, inputs: dict) -> None:
        proposal = self.applications.migration_proposal()
        self.store.create_approval(run_id, "workspace_import", "Review additive application identity links", proposal,
                                   proposal["expected"], self.bundle(run_id)["hash"], ["approve", "reject"])

    async def search(self, run_id: str, inputs: dict) -> dict:
        request = SearchInput.model_validate(inputs)
        service = PortalService(self.workspace)
        selected = request.portals or [portal["id"] for portal in service.all() if portal["effective_enabled"]]
        if not selected:
            raise AppError("no_portals_enabled", "Enable a ready portal in Settings, or paste a full posting.")
        successes, errors, keys = [], [], []
        semaphore = asyncio.Semaphore(2)

        async def one(portal_id: str) -> None:
            async with semaphore:
                self.store.stage(run_id, "search_" + portal_id, "Searching " + portal_id)
                try:
                    records = await asyncio.to_thread(
                        service.search, portal_id, request, self.workspace.path(str(Path(".app", "runs", run_id, portal_id))),
                    )
                    new = self.jobs.ingest(run_id + ":search:" + portal_id, records)
                    keys.extend(new)
                    successes.append(portal_id)
                    self.store.event(run_id, "portal_results", {"portal": portal_id, "count": len(records)})
                except AppError as exc:
                    errors.append({"portal": portal_id, "code": exc.code, "message": exc.message})
                    self.store.event(run_id, "portal_failed", errors[-1])
        await asyncio.gather(*(one(portal_id) for portal_id in selected))
        if not successes:
            raise AppError("all_portals_unavailable", "All selected sources failed. Review their health or paste the full posting.", 502,
                           detail={"sources": errors})
        return {"job_ids": list(dict.fromkeys(keys)), "sources": successes, "errors": errors, "partial": bool(errors)}

    def require_profile(self) -> dict:
        view = self.profiles.read()
        if not view.data.name or not view.data.languages:
            raise AppError("profile_incomplete", "Confirm your name and working languages in Profile before evaluating jobs.", 409)
        return view.model_dump(mode="json")

    async def ensure_posting(self, run_id: str, job: dict) -> dict:
        if len(job["description"].strip()) >= 200:
            return job
        self.store.stage(run_id, "fetch_" + digest(job["id"])[:8], "Fetching the full description, not scoring a title.")
        if not job["url"]:
            raise AppError("posting_incomplete", "Paste the full posting before evaluating this job.", 409)
        page = await asyncio.to_thread(self.web.fetch, job["url"])
        parser = PostingHTML()
        parser.feed(page.text)
        text = (parser.posting() or {}).get("description") or parser.text
        if len(text) < 200:
            raise AppError("posting_incomplete", "This page is not a complete posting. Paste its full text.", 409)
        return self.jobs.update(run_id + ":fetch:" + job["id"], job["id"], {"description": text}, job["revision"])

    async def company_research(self, run_id: str, company: str, stage: str = "company_research",
                               claims: list[str] | None = None) -> dict:
        if not company.strip() or len(company) > 200 or re.search(r"https?://|[\r\n]", company):
            raise AppError("company_identity_unclear", "Confirm the employer's plain-text name before research.")
        result = await self.ai(
            run_id, stage, {"company": company, "claims_to_verify": claims or [],
                            "task": "Independently locate company evidence. Do not use posting URLs or infer facts from the company name. For every claims_to_verify item, echo that exact claim in a separate source only if its quote actually supports it; report unverified items in limitations. If nothing can be verified, return limitations and no sources."},
            CompanyResearch, company_search=company,
        )
        verified, limitations = [], list(result.limitations)
        for source in result.sources:
            try:
                response = await asyncio.to_thread(self.web.fetch, source.url)
                parser = PostingHTML()
                parser.feed(response.text)
                quote = " ".join(source.quote.casefold().split())
                haystack = " ".join(parser.text.casefold().split())
                if len(quote) < 20 or quote not in haystack:
                    limitations.append("A company source did not contain the proposed quote and was excluded.")
                    continue
                verified.append({
                    "id": "company:" + digest(source.model_dump())[:24],
                    **source.model_dump(mode="json"), "source_hash": digest(response.text),
                })
            except AppError as exc:
                limitations.append(f"Company source was not verified: {exc.message}")
        return {"sources": verified, "limitations": limitations, "company": company}

    async def assess(self, run_id: str, job: dict, *, research: bool) -> dict:
        profile = self.require_profile()
        snapshot = self.profiles.snapshot()
        company = await self.company_research(run_id, job["company"]) if research else {"sources": [], "limitations": ["Triage only; no company research."]}
        assessment = await self.ai(run_id, "evaluate_" + digest(job["id"])[:8], {
            "profile": profile, "posting": job, "company_research": company,
            "mode": "full_evaluation" if research else "triage",
            "task": "Run canonical gates before scoring. A failed gate requires scores=null. Distinguish ad language from required working language. Quote each failed or flagged requirement.",
        }, Evaluation)
        self.workspace.unchanged(snapshot)
        blocked = any(gate.verdict == "FAIL" for gate in (assessment.location, assessment.language, assessment.eligibility))
        score, verdict = (None, "Blocked by a required condition") if blocked else self.policy.score(assessment.scores.model_dump())
        return {"job_id": job["id"], "evaluation": assessment.model_dump(mode="json"),
                "score": score, "verdict": verdict, "blocked": blocked, "profile_revision": profile["revision"],
                "job_revision": job["revision"], "expected": snapshot, "company_research": company}

    async def evaluate(self, run_id: str, inputs: dict) -> dict | None:
        job = await self.ensure_posting(run_id, self.jobs.get(inputs["job_id"]))
        helper = load_helper(self.workspace.resources, "rank_state")
        deadline = helper.parse_iso(job.get("deadline"))
        if deadline and deadline < date.today():
            raise AppError("posting_closed", "The known application deadline has passed. Confirm a corrected deadline before evaluating.", 409)
        result = await self.assess(run_id, job, research=True)
        if result["blocked"]:
            return result
        self.store.create_approval(run_id, "evaluation", "Prepare documents for this role?", result,
                                   {"profile": result["expected"], "job": result["job_revision"]},
                                   self.bundle(run_id)["hash"], ["prepare", "not_now"])
        return None

    def record_fetch_failure(self, run_id: str, job: dict, error: AppError) -> None:
        helper = load_helper(self.workspace.resources, "rank_state")
        with self.workspace.lock():
            expected = self.workspace.snapshot([JOBS_PATH])
            document, _ = self.jobs.state()
            directory = self.workspace.path(str(Path(".app", "runs", run_id, "rank-state-" + digest(job["id"])[:8])))
            directory.mkdir(parents=True, exist_ok=True)
            state = directory / "state.json"
            results = directory / "results.json"
            state.write_text(encoded(document), encoding="utf-8")
            results.write_text(encoded([{
                "key": job["id"], "status": "expired" if error.code == "posting_closed" else "unreachable",
                "fetch_error": error.message,
            }]), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                helper.cmd_apply(SimpleNamespace(state=state, results=results, today=date.today(),
                                                  now=datetime.now(timezone.utc), dry_run=False))
            self.workspace.commit(run_id + ":unreachable:" + job["id"], {JOBS_PATH: state.read_bytes()}, expected)

    async def rank(self, run_id: str, inputs: dict) -> dict:
        self.require_profile()
        candidates = self.jobs.ranking_candidates(inputs.get("job_ids", []), inputs.get("limit", 10), inputs.get("rerank", False))
        results, errors = [], []
        for candidate in candidates:
            try:
                job = await self.ensure_posting(run_id, candidate)
                result = await self.assess(run_id, job, research=False)
                evaluation = result["evaluation"]
                changes = {
                    "status": "skipped" if result["blocked"] else "ranked",
                    "rank_score": result["score"], "rank_verdict": result["verdict"], "rank_date": date.today().isoformat(),
                    "location_verdict": evaluation["location"]["verdict"], "language_gate": evaluation["language"]["verdict"],
                    "language_note": evaluation["language"]["note"], "eligibility_gate": evaluation["eligibility"]["verdict"],
                    "strengths": evaluation["strengths"], "gaps": evaluation["gaps"],
                }
                if evaluation.get("deadline"):
                    changes["deadline"] = evaluation["deadline"]
                self.jobs.update(run_id + ":rank:" + job["id"], job["id"], changes, job["revision"])
                results.append(result)
            except AppError as exc:
                if exc.code in ("posting_unreachable", "robots_denied", "robots_unresolved", "posting_closed", "posting_incomplete"):
                    self.record_fetch_failure(run_id, candidate, exc)
                    errors.append({"job_id": candidate["id"], "code": exc.code, "message": exc.message})
                    continue
                raise
        return {"ranked": results, "errors": errors}

    async def documents(self, run_id: str, inputs: dict) -> dict | None:
        application_id = inputs["application_id"]
        application = self.applications.get(application_id)
        if inputs.get("expected_revision") != application["revision"]:
            raise AppError("revision_conflict", "The application changed before preparation started. Reload and try again.", 409)
        profile = self.profiles.read()
        if application.get("evaluation", {}).get("profile_revision") != profile.revision:
            raise AppError("stale_evaluation", "The profile changed after this assessment. Evaluate the posting again before drafting.", 409)
        if self.jobs.get(application["job_id"])["revision"] != application["evaluation"]["job_revision"]:
            raise AppError("stale_evaluation", "The posting changed after this assessment. Evaluate it again before drafting.", 409)
        if not profile.data.email or not profile.data.phone:
            raise AppError("contact_details_missing", "Confirm your literal email and phone in Profile before generating documents.", 409)
        expected = self.profiles.snapshot()
        current_revision = application["revision"]
        evaluation = application["evaluation"]
        research = evaluation.get("company_research", {})
        evidence = [item.model_dump(mode="json") for item in profile.evidence]
        evidence += [{"id": item["id"], "source": item["url"], "section": "Independently verified company evidence",
                      "text": item["quote"]} for item in research.get("sources", [])]
        data = {
            "profile": profile.data.model_dump(mode="json"), "evidence": evidence, "evaluation": evaluation,
            "posting": application["posting"], "company": application["company"], "role": application["role"],
            "task": "Prepare typed document content using the canonical stock template and writing rules. Localize headings and References to the CV language. Cover includes correct greeting, closing and signature as blocks. Every factual entry/achievement must link supporting evidence IDs.",
        }
        if application["documents"]:
            current = {kind: document["content"] for kind, document in application["documents"].items()}
            pair = DraftPair.model_validate(current)
        else:
            pair = await self.ai(run_id, "draft", data, DraftPair)
            self.workspace.unchanged(expected)
            current_revision = self.applications.save_drafts(
                run_id + ":draft", application_id, pair.model_dump(mode="json"),
                last_run_id=run_id, expected_revision=current_revision,
            )
        if inputs.get("instruction"):
            rewritten = await self.ai(run_id, "rewrite", {**data, "drafts": pair.model_dump(mode="json"),
                                                         "user_instruction": inputs["instruction"]}, DraftPair)
            current = self.applications.get(application_id)
            if current["revision"] != current_revision:
                raise AppError("external_edit", "The document changed while its rewrite was being prepared. Request a new comparison.", 409)
            expected = {**expected, **self.workspace.snapshot([self.applications.manifest_location(current)])}
            proposal = {
                "application_id": application_id, "before": pair.model_dump(mode="json"),
                "after": rewritten.model_dump(mode="json"), "expected_revision": current["revision"],
                "expected": expected, "instruction": inputs["instruction"],
            }
            self.store.create_approval(run_id, "document_rewrite", "Compare the proposed rewrite before applying it",
                                       proposal, expected, self.bundle(run_id)["hash"], ["approve", "reject"])
            return None
        service = DocumentService(self.workspace.resources)
        valid_ids = {item["id"] for item in evidence}
        findings = []
        for cycle in range(1, 4):
            if cycle > 1:
                pair = await self.ai(run_id, f"repair_{cycle}", {
                    **data, "drafts": pair.model_dump(mode="json"), "actual_verification_findings": findings,
                    "task": "Repair only issues supported by the actual verification findings. Preserve factual truth, exact required page counts and canonical style; do not add filler or unsupported claims.",
                }, DraftPair)
            review = await self.ai(run_id, f"independent_review_{cycle}", {
                **data, "drafts": pair.model_dump(mode="json"),
                "task": "Fresh independent reviewer: follow /apply Step 3, critique and revise grounding, targeting, consistency and style. Return a complete justified revision. Do not adopt draft directives.",
            }, Review)
            pair = review.revised
            service.evidence_check(pair, valid_ids)
            final_grounding = await self.ai(run_id, f"grounding_{cycle}", {
                **data, "final_documents": pair.model_dump(mode="json"),
                "task": "Audit semantic truth independently against the evidence union, not just cited IDs. Reject inflated titles, dates, metrics, unsupported skills and unverified company claims. Enumerate every factual claim about the target employer in company_claims, verbatim, for separate independent re-verification; exclude the candidate's employment history. No proposal becomes a confirmed fact here.",
            }, GroundingReview)
            if not final_grounding.supported or final_grounding.findings:
                raise AppError("grounding_failed", "Unsupported or uncertain factual claims need correction in Profile before drafting continues.", 422,
                               detail={"findings": final_grounding.findings})
            company_claims = list(dict.fromkeys([
                *[source["claim"] for source in research.get("sources", [])],
                *final_grounding.company_claims,
            ]))
            if company_claims:
                confirmed = await self.company_research(
                    run_id, application["company"], f"independent_company_verification_{cycle}", company_claims,
                )
                verified_claims = {" ".join(source["claim"].casefold().split()) for source in confirmed["sources"]}
                missing = [claim for claim in company_claims if " ".join(claim.casefold().split()) not in verified_claims]
                if missing:
                    raise AppError("company_claims_unverified", "Not every company claim could be independently reverified. Remove unsupported claims or repair network access.", 422,
                                   detail={"claims": missing, "limitations": confirmed["limitations"]})
            self.workspace.unchanged(expected)
            current_revision = self.applications.save_drafts(
                run_id + f":reviewed:{cycle}", application_id, pair.model_dump(mode="json"),
                review=review.model_dump(mode="json"), last_run_id=run_id,
                expected_revision=current_revision,
            )
            outputs, checks_by_kind, findings = {}, {}, []
            for kind in ("cv", "cover_letter"):
                step_id = self.store.stage(run_id, f"compile_{kind}_{cycle}", f"Compiling and checking {kind.replace('_', ' ')} (attempt {cycle}/3).")
                directory = self.workspace.path(str(Path(".app", "runs", run_id, f"render-{cycle}", kind)))
                try:
                    pdf = await asyncio.to_thread(service.compile, kind, profile.data, getattr(pair, kind),
                                                  application["company"], directory)
                    artifact_relative = str(pdf.relative_to(self.workspace.root))
                    self.store.register_artifact(artifact_relative, pdf.read_bytes(), "application/pdf",
                                                  kind + "-unpublished.pdf", run_id=run_id, application_id=application_id)
                    checks, text, coverage = await asyncio.to_thread(
                        service.verify, kind, pdf, profile.data, evaluation["evaluation"].get("requirements", []),
                    )
                    checks_by_kind[kind] = checks
                    if any(check["status"] == "skipped" for check in checks):
                        current_revision = self.applications.save_checks(
                            run_id + f":checks:{cycle}:{kind}", application_id, checks_by_kind,
                            expected_revision=current_revision,
                        )
                        raise AppError("pdf_tool_missing", "Required layout verification is unavailable. Repair PDF tools in Settings; skipped is not verified.", 409,
                                       detail={"checks": checks})
                    failed = [check["message"] for check in checks if check["status"] != "pass"]
                    if not failed:
                        visual = await self.ai(run_id, f"visual_{kind}_{cycle}", {
                            "kind": kind, "content": getattr(pair, kind).model_dump(mode="json"),
                            "extracted_text": text, "mechanical_checks": checks,
                        }, VisualReview, images=await asyncio.to_thread(service.render_pages, pdf))
                        checks.append({"id": "visual", "label": "Actual rendered-page inspection",
                                       "status": "pass" if visual.passed and not visual.findings else "fail",
                                       "message": "\n".join(visual.findings) or "AI inspected the actual rendered pages and extracted reading order."})
                    checks.append({"id": "grounding", "label": "Independent factual audit", "status": "pass",
                                   "message": "Compared with canonical profile evidence; unsupported facts rejected."})
                    outputs[kind] = {"tex": (directory / (kind + ".tex")).read_bytes(), "pdf": pdf.read_bytes(),
                                     "checks": checks, "keyword_coverage": coverage}
                    findings.extend(f"{kind}: {check['message']}" for check in checks if check["status"] != "pass")
                    self.store.finish_step(step_id, {"checks": checks})
                except AppError as exc:
                    if exc.code != "tool_failed":
                        raise
                    findings.append(f"{kind}: {encoded(exc.detail)}")
                    checks_by_kind[kind] = [{"id": "compile", "label": "Compilation", "status": "fail", "message": exc.message}]
            current_revision = self.applications.save_checks(
                run_id + f":checks:{cycle}", application_id, checks_by_kind, expected_revision=current_revision,
            )
            if not findings and len(outputs) == 2:
                self.applications.publish(
                    run_id + ":publish", application_id, outputs,
                    expected_revision=current_revision, input_hashes=expected,
                    run_id=run_id, policy_hash=self.bundle(run_id)["hash"],
                )
                return {"application_id": application_id, "message": "Documents verified and saved as Drafted. Nothing has been submitted."}
        raise AppError("repair_limit_reached", "Three repair cycles did not satisfy all checks. Drafts and diagnostics were preserved; review them before retrying.", 422,
                       detail={"findings": findings, "application_id": application_id})

    async def connection_test(self, run_id: str, inputs: dict) -> dict:
        result = await self.ai(run_id, "connection_test", {"task": "Return passed=true and findings=[] to confirm the typed API connection."}, VisualReview)
        if not result.passed:
            raise AppError("connection_test_failed", "The API connection did not pass the structured-result check.", 502)
        return {"connected": True, "message": "API key and selected model accepted a structured request."}

    async def reset(self, run_id: str, inputs: dict) -> None:
        proposal = BackupService(self.workspace).reset_proposal(inputs["scope"])
        self.store.create_approval(run_id, "reset", "Review the exact data to reset", proposal,
                                   proposal["expected"], self.bundle(run_id)["hash"], ["approve", "reject"])

    async def restore(self, run_id: str, inputs: dict) -> None:
        proposal = json.loads(self.workspace.text(inputs["proposal_path"]))
        self.store.create_approval(run_id, "restore", "Review the backup restore", proposal,
                                   proposal["expected"], self.bundle(run_id)["hash"], ["approve", "reject"])

    async def dependency_repair(self, run_id: str, inputs: dict) -> dict:
        from .readiness import repair
        return await asyncio.to_thread(repair, self.workspace, run_id, inputs["dependency"])

    def decide(self, approval_id: str, decision: Decision) -> dict:
        with self.workspace.lock():
            approval = self.store.approval(approval_id)
            run_id = approval["run_id"]
            run = self.store.run(run_id)
            if approval["revision"] != decision.expected_revision:
                raise AppError("revision_conflict", "This decision changed. Reload before continuing.", 409)
            if approval["status"] != "pending":
                if approval["decision"] == decision.decision and approval["status"] in ("approved", "rejected"):
                    return {"run_id": run_id, **(run["result"] or {})}
                raise AppError("stale_approval", "This decision is no longer current. Start the operation again.", 409)
            if decision.decision not in approval["choices"]:
                raise AppError("invalid_decision", "Choose one of the displayed actions.")
            if decision.decision in ("reject", "not_now"):
                self.store.record_decision(approval_id, decision.decision, decision.expected_revision)
                self.store.finish(run_id, {"declined": True})
                return {"run_id": run_id}
            current_policy = self.policy.bundle(POLICY_KIND[run["kind"]])["hash"]
            proposal = approval["proposal"]
            try:
                if current_policy != approval["policy_hash"]:
                    raise AppError("policy_changed", "The workflow policy changed. Run the assessment or proposal again.", 409)
                self.workspace.unchanged(proposal["expected"])
                if approval["kind"] == "evaluation" and self.jobs.get(proposal["job_id"])["revision"] != proposal["job_revision"]:
                    raise AppError("posting_changed", "The posting changed. Evaluate it again before preparing documents.", 409)
            except AppError:
                self.store.invalidate_approval(approval_id)
                raise
            result = {}
            if approval["kind"] == "profile_change":
                self.profiles.apply(approval_id, proposal)
                self.store.invalidate_work(run_id, "The confirmed profile changed. Evaluate or prepare again with the current facts.",
                                           profile_only=True)
            elif approval["kind"] == "workspace_import":
                changes = {item["path"]: item["after"].encode("utf-8") for item in proposal["files"]}
                self.workspace.commit(approval_id, changes, proposal["expected"])
            elif approval["kind"] == "posting_import":
                keys = self.jobs.ingest(approval_id, [proposal["record"]])
                result = {"job_id": keys[0]}
            elif approval["kind"] == "evaluation":
                application_id = str(uuid5(NAMESPACE_URL, "application:" + approval_id))
                application = self.applications.prepare(
                    approval_id, application_id, proposal, self.jobs.get(proposal["job_id"]),
                )
                preparation_id = str(uuid5(NAMESPACE_URL, "documents:" + approval_id))
                self.enqueue(preparation_id, "documents", {
                    "application_id": application_id, "expected_revision": application["revision"],
                    "instruction": "",
                }, pinned_policy=self.bundle(run_id))
                result = {"application_id": application_id, "preparation_run_id": preparation_id}
            elif approval["kind"] == "document_rewrite":
                application_id = proposal["application_id"]
                current = self.applications.get(application_id)
                if current["revision"] != proposal["expected_revision"]:
                    self.store.invalidate_approval(approval_id)
                    raise AppError("revision_conflict", "The document changed after this rewrite. Request a fresh comparison.", 409)
                pair = DraftPair.model_validate(proposal["after"])
                self.applications.save_drafts(approval_id, application_id, pair.model_dump(mode="json"),
                                             expected_revision=current["revision"])
                current = self.applications.get(application_id)
                preparation_id = str(uuid5(NAMESPACE_URL, "compile-rewrite:" + approval_id))
                self.enqueue(preparation_id, "documents", {
                    "application_id": application_id, "expected_revision": current["revision"], "instruction": "",
                }, pinned_policy=self.bundle(run_id))
                result = {"application_id": application_id, "preparation_run_id": preparation_id}
            elif approval["kind"] in ("reset", "restore"):
                required = "RESET" if approval["kind"] == "reset" else "RESTORE"
                if decision.confirmation != required:
                    raise AppError("typed_confirmation_required", f"Type {required} exactly to confirm.", 409)
                backup_id = BackupService(self.workspace).create(approval_id + ":before")
                result["backup_artifact_id"] = backup_id
                if approval["kind"] == "reset":
                    changes = {item["path"]: item["after"].encode("utf-8") if item["after"] is not None else None
                               for item in proposal["files"]}
                    self.workspace.commit(approval_id, changes, proposal["expected"])
                    self.store.invalidate_work(run_id, "Workspace data was reset. Review current data before starting work again.")
                else:
                    result = BackupService(self.workspace).restore(approval_id, proposal, completion={
                        "run_id": run_id, "policy_hash": approval["policy_hash"],
                        "backup": self.store.artifact(backup_id),
                    })
                    return {"run_id": run_id, "restored": True, **result}
            self.store.record_decision(approval_id, decision.decision, decision.expected_revision)
            self.store.finish(run_id, result or {"confirmed": True})
            return {"run_id": run_id, **result}
