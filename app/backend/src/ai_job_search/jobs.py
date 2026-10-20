from __future__ import annotations

import json
from datetime import date
from urllib.parse import urlsplit

from .errors import AppError
from .policy import load_helper
from .storage import digest, encoded, now
from .workspace import JOBS_PATH, Workspace


class JobService:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.key_helper = load_helper(workspace.resources, "job_key")

    def state(self) -> tuple[dict, dict]:
        text = self.workspace.text(JOBS_PATH)
        if not text:
            return {"seen": {}}, {}
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AppError("invalid_job_state", "The stored job list is invalid JSON. Restore or repair it first.", 409) from exc
        seen = document.get("seen", document) if isinstance(document, dict) else None
        if not isinstance(seen, dict) or any(not isinstance(value, dict) for value in seen.values()):
            raise AppError("invalid_job_state", "The stored job list has an unsupported format.", 409)
        return document, seen

    @staticmethod
    def summary(key: str, record: dict) -> dict:
        location = record.get("location_text", record.get("location", "")) or ""
        return {
            "id": key, "key": key, "title": record.get("title", record.get("role", "")),
            "company": record.get("company", ""), "location": "" if location in ("PASS", "FAIL", "FLAG") else location,
            "first_seen": record.get("first_seen"), "status": record.get("status", "new"),
            "saved": bool(record.get("saved", False)),
        }

    @staticmethod
    def view(key: str, record: dict) -> dict:
        item = {**record, **JobService.summary(key, record)}
        location = record.get("location_text", record.get("location", "")) or ""
        if location in ("PASS", "FAIL", "FLAG"):
            item.setdefault("location_verdict", location)
        item.update({
            "description": record.get("description", record.get("text", "")),
            "url": record.get("url", ""), "portal": record.get("portal", "manual"),
            "source": record.get("source", "cli"), "posted_date": record.get("posted_date", record.get("date")),
            "deadline": record.get("deadline"), "fit": record.get("rank_verdict", record.get("fit")),
            "strengths": record.get("strengths", []), "gaps": record.get("gaps", []),
            "revision": digest(record),
            "source_host": urlsplit(record.get("url") or "").hostname,
            "warnings": list(record.get("warnings") or []),
        })
        if record.get("expired_reason") == "unreachable":
            item["warnings"].append("Could not verify this posting; check manually. Closure is not confirmed.")
        return item

    def all(self) -> list[dict]:
        _, seen = self.state()
        return [self.view(key, record) for key, record in seen.items()]

    def get(self, key: str) -> dict:
        _, seen = self.state()
        if key not in seen:
            raise AppError("not_found", "Job not found.", 404)
        return self.view(key, seen[key])

    def ingest(self, operation_id: str, records: list[dict]) -> list[str]:
        with self.workspace.lock():
            expected = self.workspace.snapshot([JOBS_PATH])
            document, seen = self.state()
            if "seen" not in document:
                seen = document
            else:
                seen = document["seen"]
            keys = []
            for record in records:
                title, company = record.get("title", ""), record.get("company", "")
                if not isinstance(title, str) or not isinstance(company, str) or not title.strip() or not company.strip():
                    raise AppError("posting_metadata_missing", "Confirm a job title and employer before saving.")
                key = self.key_helper.make_key(company, title, record.get("url", ""))
                existing_key = next((
                    stored_key for stored_key, stored in seen.items()
                    if (record.get("url") and stored.get("url") == record["url"])
                    or (str(stored.get("company", "")).casefold() == company.casefold()
                        and str(stored.get("title", stored.get("role", ""))).casefold() == title.casefold())
                ), None)
                key = existing_key or key
                previous = seen.get(key, {})
                merged = {**previous, **record}
                if not record.get("description") and previous.get("description"):
                    merged["description"] = previous["description"]
                if not record.get("deadline") and previous.get("deadline"):
                    merged["deadline"] = previous["deadline"]
                if previous.get("first_seen"):
                    merged["first_seen"] = previous["first_seen"]
                changed = bool(previous) and any(
                    merged.get(field) != previous.get(field) for field in ("title", "company", "description", "deadline")
                )
                if changed:
                    for field in ("rank_score", "rank_verdict", "rank_date", "language_gate", "language_note",
                                  "eligibility_gate", "location_verdict", "expired_reason"):
                        merged.pop(field, None)
                    merged["status"] = "new"
                merged.setdefault("first_seen", now())
                merged["last_seen"] = now()
                merged.setdefault("status", "new")
                seen[key] = merged
                keys.append(key)
            self.workspace.commit(operation_id, {JOBS_PATH: (encoded(document) + "\n").encode("utf-8")}, expected)
            return keys

    def update(self, operation_id: str, key: str, changes: dict, expected_revision: str) -> dict:
        with self.workspace.lock():
            expected = self.workspace.snapshot([JOBS_PATH])
            document, seen = self.state()
            if key not in seen:
                raise AppError("not_found", "Job not found.", 404)
            if digest(seen[key]) != expected_revision:
                raise AppError("revision_conflict", "This job changed. Reload before saving.", 409)
            seen[key].update(changes)
            self.workspace.commit(operation_id, {JOBS_PATH: (encoded(document) + "\n").encode("utf-8")}, expected)
            return self.view(key, seen[key])

    def ranking_candidates(self, ids: list[str], limit: int, rerank: bool) -> list[dict]:
        from .applications import ApplicationService
        helper = load_helper(self.workspace.resources, "rank_state")
        pairs = {(helper.norm(a["company"]), helper.norm(a["role"]))
                 for a in ApplicationService(self.workspace).overview() if a.get("published")}
        result = []
        for job in self.all():
            if ids and job["id"] not in ids:
                continue
            if (helper.norm(job["company"]), helper.norm(job["title"])) in pairs:
                continue
            if not rerank and (job["status"] in ("ranked", "expired", "skipped") or job.get("ranked_at")):
                continue
            deadline = helper.parse_iso(job.get("deadline"))
            if deadline and deadline < date.today():
                continue
            result.append(job)
        return result[:limit]
