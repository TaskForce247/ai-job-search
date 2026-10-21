from __future__ import annotations

import csv
import io
import json
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from .errors import AppError
from .models import ApplicationInput, DocumentContent, OutcomeInput, SubmissionInput
from .storage import digest, encoded, now
from .workspace import TRACKER_PATH, Workspace

STANDARD_COLUMNS = [
    "date", "company", "sector", "role", "role_type", "channel", "status", "contact_person",
    "fit_rating", "notes", "cv_file", "cover_letter_file", "source", "deadline",
]
IDENTITY_COLUMNS = ["application_id", "archive_dir"]
FINAL = {"hired", "rejected", "no_response", "offer_declined", "withdrawn"}
ALIASES = {"no response": "no_response", "offer declined": "offer_declined"}


def prefix(company: str, role: str) -> str:
    text = (company + "_" + role).lower().replace(" ", "_")
    text = "".join(c for c in text if c.isalnum() or c == "_")
    result = re.sub("_+", "_", text).strip("_")
    if not result:
        raise AppError("archive_identity_missing", "Use an employer or role containing at least one letter or digit.")
    return result


def archive_for(company: str, role: str, application_id: str) -> str:
    return str(Path("documents", "applications", prefix(company, role)[:96] + "__" + UUID(application_id).hex))


def safe_notes(value: str) -> str:
    if any(c in value for c in ',\"\r\n'):
        raise AppError("unsafe_tracker_note", "Use a single-line note without commas or double quotes (use semicolons instead).")
    return value.strip()


class ApplicationService:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def tracker(self) -> tuple[list[str], list[dict[str, str]]]:
        text = self.workspace.text(TRACKER_PATH)
        if not text:
            return STANDARD_COLUMNS + IDENTITY_COLUMNS, []
        reader = csv.DictReader(io.StringIO(text, newline=""))
        fields = reader.fieldnames or []
        if not {"company", "role", "status", "date"}.issubset(fields) or len(fields) != len(set(fields)):
            raise AppError("unsupported_tracker", "The tracker is missing required columns or repeats a header.", 409)
        rows = []
        for row in reader:
            if None in row:
                raise AppError("invalid_tracker", "A tracker row has extra cells. Repair the CSV before editing.", 409)
            rows.append({key: value or "" for key, value in row.items()})
        return fields, rows

    @staticmethod
    def tracker_bytes(fields: list[str], rows: list[dict]) -> bytes:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue().encode("utf-8")

    @staticmethod
    def row_id(row: dict, index: int) -> str:
        return row.get("application_id") or str(uuid5(NAMESPACE_URL, f"legacy-application:{index}:{encoded(row)}"))

    def _manifest_path(self, application_id: str, archive: str | None = None) -> str:
        UUID(application_id)
        return str(Path(archive, "application.json")) if archive else str(Path(".app", "preparations", application_id + ".json"))

    def validate_archive(self, relative: str) -> None:
        path = self.workspace.path(relative)
        parent = self.workspace.path(str(Path("documents", "applications")))
        if path == parent or not path.is_relative_to(parent):
            raise AppError("unsafe_archive_link", "Application archives must stay inside documents/applications.", 409)

    def _manifest(self, relative: str, text: str | None = None) -> dict:
        if text is None:
            text = self.workspace.text(relative)
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise AppError("invalid_application", "An application's metadata file is damaged.", 409) from exc
        if not isinstance(data, dict):
            raise AppError("invalid_application", "An application's metadata format is unsupported.", 409)
        return data

    def all(self) -> list[dict]:
        with self.workspace.lock():
            _, rows = self.tracker()
            result = [self._view(row, index) for index, row in enumerate(rows)]
            ids = {item["id"] for item in result}
            directory = self.workspace.path(str(Path(".app", "preparations")))
            if directory.exists():
                for path in directory.glob("*.json"):
                    application_id = path.stem
                    if application_id not in ids:
                        result.append(self._preparation_view(application_id))
            return result

    def _row_locator(self, row: dict, index: int) -> tuple[str, str]:
        application_id = self.row_id(row, index)
        explicit = row.get("archive_dir") or ""
        archive = explicit or str(Path("documents", "applications", prefix(row["company"], row["role"])))
        part = Path(archive)
        parent = Path("documents", "applications")
        if part == parent or not part.is_relative_to(parent):
            raise AppError("unsafe_archive_link", "Application archives must stay inside documents/applications.", 409)
        return application_id, archive

    def overview(self) -> list[dict]:
        with self.workspace.lock():
            from .watcher import ApplicationWatcher
            cached_revision = self.workspace.overview_revision
            self.workspace.overview_revision = None
            if self.workspace.application_watcher is None:
                self.workspace.application_watcher = ApplicationWatcher(self.workspace.root)
            watcher = self.workspace.application_watcher
            changed = watcher.changed()
            fields, rows = self.tracker()
            tracker_revision = digest({"fields": fields, "rows": rows})
            if not changed and tracker_revision == cached_revision:
                self.workspace.overview_revision = tracker_revision
                return [dict(view) for view in self.workspace.overview_records]
            records = []
            ids = set()
            for index, row in enumerate(rows):
                application_id, archive = self._row_locator(row, index)
                manifest = self._manifest_path(application_id, archive)
                outcome = str(Path(archive, "outcome.md"))
                records.append((application_id, row, index, manifest, outcome))
                ids.add(application_id)
            directory = self.workspace.path(str(Path(".app", "preparations")))
            if directory.exists():
                for path in directory.glob("*.json"):
                    if path.stem not in ids:
                        relative = self._manifest_path(path.stem)
                        records.append((path.stem, None, None, relative, None))

            updated, candidates, paths = {}, [], []
            for application_id, row, index, manifest, outcome in records:
                key = manifest + ":" + str(index)
                row_revision = digest(row)
                cached = self.workspace.application_overviews.get(key)
                affected = watcher.affects(manifest) or (outcome is not None and watcher.affects(outcome))
                if (cached_revision is not None and watcher.native and cached
                        and cached[0][0] == row_revision and not affected):
                    updated[key] = cached
                else:
                    candidates.append((application_id, row, index, manifest, outcome, key, row_revision, affected))
                    paths.extend([manifest, outcome] if outcome is not None else [manifest])
            metadata = self.workspace.stat_paths(paths)

            def fingerprint(relative):
                info = metadata[relative] if relative else None
                return (info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino) if info else None

            def snapshot_text(relative):
                info = metadata[relative]
                if info is None:
                    return ""
                if not stat.S_ISREG(info.st_mode):
                    raise AppError("not_a_file", "A workspace record points to a directory.", 409)
                try:
                    content = (self.workspace.root / relative).read_bytes()
                except (FileNotFoundError, NotADirectoryError) as exc:
                    raise AppError("external_edit", "Application files changed while being read. Reload before continuing.", 409) from exc
                return self.workspace.decode_text(relative, content)

            def read_texts(relatives):
                relatives = list(dict.fromkeys(relatives))
                if len(relatives) > 1:
                    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="application-index") as pool:
                        return dict(zip(relatives, pool.map(snapshot_text, relatives)))
                return {relative: snapshot_text(relative) for relative in relatives}

            pending = []
            for application_id, row, index, manifest, outcome, key, row_revision, affected in candidates:
                signature = (row_revision, fingerprint(manifest), fingerprint(outcome))
                cached = self.workspace.application_overviews.get(key)
                if cached_revision is not None and cached and cached[0] == signature and not affected:
                    updated[key] = cached
                else:
                    pending.append((application_id, row, index, manifest, outcome, key, signature))
            texts = read_texts([manifest for _, _, _, manifest, _, _, _ in pending])
            documents = {relative: self._manifest(relative, text) for relative, text in texts.items()}
            outcomes = read_texts([
                outcome for _, row, _, manifest, outcome, _, _ in pending
                if row is not None and not documents[manifest].get("timeline")
            ])
            for application_id, row, index, manifest, outcome, key, signature in pending:
                document = documents[manifest]
                if row is not None:
                    view = self._view(row, index, documents=False, manifest=document, outcome_text=outcomes.get(outcome, ""))
                else:
                    view = self._preparation_view(application_id, documents=False, manifest=document)
                updated[key] = (signature, view)
            result = [dict(updated[manifest + ":" + str(index)][1]) for _, _, index, manifest, _ in records]
            self.workspace.application_overviews = updated
            self.workspace.overview_records = result
            self.workspace.overview_revision = tracker_revision
            return [dict(view) for view in result]

    def _view(self, row: dict, index: int, *, documents: bool = True,
              manifest: dict | None = None, outcome_text: str | None = None) -> dict:
        application_id, archive = self._row_locator(row, index)
        if manifest is None:
            self.validate_archive(archive)
            manifest = self._manifest(self._manifest_path(application_id, archive))
        status = ALIASES.get(row["status"].strip().lower(), row["status"].strip().lower())
        result = {
            **manifest, "id": application_id, "company": row["company"], "role": row["role"],
            "status": status, "source": row.get("source", ""), "deadline": row.get("deadline") or None,
            "applied_on": row["date"] if status != "drafted" else None, "created_at": row["date"],
            "fit_rating": int(row["fit_rating"]) if str(row.get("fit_rating", "")).isdigit() else None,
            "archive_dir": archive, "published": True, "legacy": not bool(row.get("application_id")),
            "revision": digest({"row": row, "manifest": manifest}),
            "documents": self._document_views(manifest) if documents else {},
            "timeline": manifest.get("timeline") or self._legacy_timeline(archive, outcome_text),
        }
        return result

    def _legacy_timeline(self, archive: str, text: str | None = None) -> list[dict]:
        if text is None:
            text = self.workspace.text(str(Path(archive, "outcome.md")))
        result = []
        for match in re.finditer(r"^- \[x\] (.+)$", text, re.M | re.I):
            result.append({"date": "", "status": "interview", "stage": match.group(1), "notes": ""})
        return result

    def _preparation_view(self, application_id: str, *, documents: bool = True, manifest: dict | None = None) -> dict:
        if manifest is None:
            manifest = self._manifest(self._manifest_path(application_id))
        if not manifest:
            raise AppError("not_found", "Application not found.", 404)
        return {
            **manifest, "id": application_id, "status": "preparing", "published": False, "legacy": False,
            "revision": digest(manifest), "documents": self._document_views(manifest) if documents else {},
            "timeline": manifest.get("timeline", []), "applied_on": None,
        }

    def _document_views(self, manifest: dict) -> dict:
        result = {}
        profile_hashes = manifest.get("profile_hashes", {})
        inputs_changed = bool(profile_hashes and self.workspace.snapshot(list(profile_hashes)) != profile_hashes)
        if manifest.get("policy_hash"):
            from .policy import Policy
            inputs_changed |= Policy(self.workspace).bundle("apply")["hash"] != manifest["policy_hash"]
        for kind, data in manifest.get("documents", {}).items():
            content = DocumentContent.model_validate(data["content"])
            revision = digest(content.model_dump(mode="json"))
            artifacts_changed = any(
                self.workspace.hash(data[extension + "_path"]) != self.workspace.store.artifact(data[artifact])["hash"]
                for extension, artifact in (("pdf", "pdf_artifact_id"), ("tex", "source_artifact_id"))
                if data.get(artifact) and data.get(extension + "_path")
            )
            result[kind] = {
                **data, "content": content.model_dump(mode="json"), "revision": revision,
                "stale": data.get("compiled_revision") != revision or inputs_changed or artifacts_changed,
                "checks": data.get("checks", []),
            }
        return result

    def get(self, application_id: str) -> dict:
        with self.workspace.lock():
            try:
                UUID(application_id)
            except ValueError as exc:
                raise AppError("not_found", "Application not found.", 404) from exc
            _, rows = self.tracker()
            for index, row in enumerate(rows):
                if self.row_id(row, index) == application_id:
                    application = self._view(row, index)
                    break
            else:
                application = self._preparation_view(application_id)
            with self.workspace.store.connect() as connection:
                latest = connection.execute(
                    "SELECT id,result_json FROM runs WHERE kind='documents' AND json_extract(input_json,'$.application_id')=? "
                    "ORDER BY created_at DESC,id DESC LIMIT 1", (application_id,),
                ).fetchone()
                usage = connection.execute(
                    "SELECT COUNT(*) AS attempts,COALESCE(SUM(cost_usd),0) AS cost FROM runs "
                    "WHERE kind='documents' AND json_extract(input_json,'$.application_id')=?", (application_id,),
                ).fetchone()
                documents_hash = digest({kind: document["content"] for kind, document in application["documents"].items()})
                checkpoint = connection.execute(
                    "SELECT 1 FROM steps JOIN runs ON runs.id=steps.run_id WHERE runs.kind='documents' "
                    "AND json_extract(runs.input_json,'$.application_id')=? AND steps.stage='document_review' "
                    "AND steps.status='succeeded' AND json_extract(steps.output_json,'$.documents_hash')=? LIMIT 1",
                    (application_id, documents_hash),
                ).fetchone()
                for kind, document in application["documents"].items():
                    draft = connection.execute(
                        "SELECT result_json FROM runs WHERE kind='document_pdf' AND status='succeeded' "
                        "AND json_extract(input_json,'$.application_id')=? AND json_extract(input_json,'$.kind')=? "
                        "ORDER BY created_at DESC,id DESC LIMIT 1", (application_id, kind),
                    ).fetchone()
                    if draft:
                        result = json.loads(draft["result_json"])
                        artifact = self.workspace.store.artifact(result["artifact_id"])
                        hashes = result["profile_hashes"]
                        document.update(
                            draft_pdf_artifact_id=artifact["id"],
                            draft_pdf_stale=(
                                result["document_revision"] != document["revision"]
                                or self.workspace.snapshot(list(hashes)) != hashes
                                or self.workspace.hash(artifact["relative_path"]) != artifact["hash"]
                            ),
                        )
            settings, _ = self.workspace.store.settings()
            latest_result = json.loads(latest["result_json"] or "null") if latest else None
            issues = latest_result.get("issues", []) if isinstance(latest_result, dict) else []
            if not issues and isinstance(latest_result, dict):
                issues = [{"kind": "grounding", "message": finding} for finding in latest_result.get("findings", [])
                          if isinstance(finding, str)]
            return {
                **application, "latest_run_id": latest["id"] if latest else None,
                "preparation_cost_usd": usage["cost"], "preparation_attempts": usage["attempts"],
                "review_checkpoint_available": checkpoint is not None, "next_run_limit_usd": settings.max_run_cost_usd,
                "content_issues": issues,
            }

    def manifest_location(self, application: dict) -> str:
        if application.get("legacy"):
            raise AppError("migration_required", "Import this existing workspace's identity links before editing legacy applications.", 409)
        return self._manifest_path(application["id"], application["archive_dir"] if application["published"] else None)

    def _edit_snapshot(self, application_id: str) -> tuple[dict, str, dict, dict[str, str | None]]:
        expected = self.workspace.snapshot([TRACKER_PATH])
        application = self.get(application_id)
        relative = self.manifest_location(application)
        expected[relative] = self.workspace.hash(relative)
        manifest = self._manifest(relative)
        if application["published"]:
            _, rows = self.tracker()
            row = next((row for row in rows if row.get("application_id") == application_id), None)
            revision = digest({"row": row, "manifest": manifest})
        else:
            revision = digest(manifest)
        if application["revision"] != revision:
            raise AppError("external_edit", "Application files changed while being read. Reload before editing.", 409)
        expected.update(self.workspace.snapshot([
            str(Path(application["archive_dir"], name))
            for name in ("outcome.md", "job_posting.md", "cv_draft.tex", "cover_letter.tex")
        ]))
        self.workspace.unchanged(expected)
        return application, relative, manifest, expected

    def prepare(self, operation_id: str, application_id: str, evaluation: dict, job: dict) -> dict:
        relative = self._manifest_path(application_id)
        with self.workspace.lock():
            expected = self.workspace.snapshot([relative])
            if self.workspace.read(relative) is not None:
                return self.get(application_id)
            metadata = {
                "id": application_id, "company": job["company"], "role": job["title"],
                "source": job["url"], "job_id": job["id"], "posting": job["description"],
                "deadline": job.get("deadline"), "evaluation": evaluation, "fit_rating": evaluation.get("score"),
                "archive_dir": archive_for(job["company"], job["title"], application_id),
                "created_at": now(), "documents": {}, "timeline": [], "submitted_revisions": [],
            }
            self.workspace.commit(operation_id, {relative: encoded(metadata).encode("utf-8")},
                                  expected)
        return self.get(application_id)

    def create_external(self, operation_id: str, application_id: str, data: ApplicationInput) -> dict:
        if not data.company.strip() or not data.role.strip() or data.applied_on is None:
            raise AppError("application_details_missing", "Enter the employer, role, and actual submission date.")
        if data.applied_on > date.today():
            raise AppError("future_submission", "A submission date cannot be in the future.")
        archive = archive_for(data.company, data.role, application_id)
        relative = self._manifest_path(application_id, archive)
        with self.workspace.lock():
            expected = self.workspace.snapshot([
                TRACKER_PATH, relative, str(Path(archive, "outcome.md")), str(Path(archive, "job_posting.md")),
            ])
            fields, rows = self.tracker()
            if any(row.get("application_id") == application_id for row in rows):
                return self.get(application_id)
            if any(expected[path] is not None for path in expected if path != TRACKER_PATH):
                raise AppError("archive_exists", "This archive already contains files. Import its identity before recording a new application.", 409)
            for column in STANDARD_COLUMNS + IDENTITY_COLUMNS:
                if column not in fields:
                    fields.append(column)
            row = dict.fromkeys(fields, "")
            row.update(date=data.applied_on.isoformat(), company=data.company, role=data.role,
                       status=data.status, source=data.source, application_id=application_id, archive_dir=archive)
            rows.append(row)
            timeline = [{"date": data.applied_on.isoformat(), "status": data.status,
                         "stage": "Recorded external application", "notes": ""}]
            manifest = {"id": application_id, "documents": {}, "timeline": timeline,
                        "submitted_revisions": [], "created_at": now()}
            outcome = self.outcome_text(data.company, data.role, data.status, timeline)
            changes = {
                TRACKER_PATH: self.tracker_bytes(fields, rows), relative: encoded(manifest).encode("utf-8"),
                str(Path(archive, "outcome.md")): outcome.encode("utf-8"),
                str(Path(archive, "job_posting.md")): b"Posting not supplied. No posting has been reconstructed.\n",
            }
            self.workspace.commit(operation_id, changes, expected)
        return self.get(application_id)

    def save_content(self, operation_id: str, application_id: str, kind: str,
                     content: DocumentContent, expected_revision: str) -> dict:
        if kind not in ("cv", "cover_letter"):
            raise AppError("not_found", "Document not found.", 404)
        with self.workspace.lock():
            application, relative, manifest, expected = self._edit_snapshot(application_id)
            previous = application["documents"].get(kind)
            if not previous or previous["revision"] != expected_revision:
                raise AppError("revision_conflict", "The document changed. Reload before saving.", 409)
            document = manifest["documents"][kind]
            document["content"] = content.model_dump(mode="json")
            document["checks"] = [{"id": "revision", "label": "Current revision", "status": "pending",
                                   "message": "Edited text needs review, compilation, and verification."}]
            self.workspace.commit(operation_id, {relative: encoded(manifest).encode("utf-8")},
                                  expected)
        return self.get(application_id)["documents"][kind]

    def save_drafts(self, operation_id: str, application_id: str, pair: dict, *,
                    review: dict | None = None, last_run_id: str | None = None,
                    expected_revision: str | None = None) -> str:
        with self.workspace.lock():
            application, relative, manifest, expected = self._edit_snapshot(application_id)
            if expected_revision and application["revision"] != expected_revision:
                raise AppError("external_edit", "The application changed while documents were being prepared.", 409)
            for kind in ("cv", "cover_letter"):
                content = DocumentContent.model_validate(pair[kind])
                previous = manifest.setdefault("documents", {}).get(kind, {})
                manifest["documents"][kind] = {**previous, "content": content.model_dump(mode="json")}
            if review is not None:
                manifest["review"] = review
            if last_run_id:
                manifest["last_run_id"] = last_run_id
            self.workspace.commit(operation_id, {relative: encoded(manifest).encode("utf-8")},
                                  expected)
            return self.get(application_id)["revision"]

    def save_checks(self, operation_id: str, application_id: str, checks: dict[str, list[dict]], *,
                    expected_revision: str) -> str:
        with self.workspace.lock():
            application, relative, manifest, expected = self._edit_snapshot(application_id)
            if application["revision"] != expected_revision:
                raise AppError("external_edit", "The documents changed during verification. Reload before retrying.", 409)
            for kind, values in checks.items():
                if kind in manifest.get("documents", {}):
                    manifest["documents"][kind]["checks"] = values
            self.workspace.commit(operation_id, {relative: encoded(manifest).encode("utf-8")},
                                  expected)
            return self.get(application_id)["revision"]

    def publish(self, operation_id: str, application_id: str, outputs: dict, *,
                expected_revision: str, input_hashes: dict[str, str | None], run_id: str, policy_hash: str) -> None:
        with self.workspace.lock():
            self.workspace.unchanged(input_hashes)
            application, location, manifest, expected = self._edit_snapshot(application_id)
            if application["revision"] != expected_revision:
                raise AppError("external_edit", "Application files changed during verification. Review and retry.", 409)
            from .policy import Policy
            if Policy(self.workspace).bundle("apply")["hash"] != policy_hash:
                raise AppError("policy_changed", "The document policy changed during verification. Review and prepare again.", 409)
            archive = application["archive_dir"]
            revision = digest({
                "content": {kind: document["content"] for kind, document in manifest["documents"].items()},
                "profile": input_hashes, "policy": policy_hash,
                "artifacts": {kind: {"tex": digest(files["tex"]), "pdf": digest(files["pdf"])}
                              for kind, files in outputs.items()},
            })
            changes: dict[str, bytes | None] = {}
            for kind, files in outputs.items():
                document = manifest["documents"][kind]
                if any(check["status"] != "pass" for check in files["checks"]):
                    raise AppError("document_not_verified", "Required document checks have not all passed.", 409)
                folder = Path(archive, "drafts", revision)
                for extension, media_type in (("tex", "application/x-tex"), ("pdf", "application/pdf")):
                    relative = str(folder / f"{kind}.{extension}")
                    content = files[extension]
                    existing = self.workspace.read(relative)
                    if existing is not None and existing != content:
                        raise AppError("artifact_changed", "An immutable draft artifact was modified externally. Preserve it and retry as a new revision.", 409)
                    expected[relative] = digest(existing) if existing is not None else None
                    changes[relative] = content
                    artifact = self.workspace.store.register_artifact(
                        relative, content, media_type, f"{application['company']}_{kind}.{extension}",
                        run_id=run_id, application_id=application_id, revision=revision,
                    )
                    document["pdf_artifact_id" if extension == "pdf" else "source_artifact_id"] = artifact
                    document[extension + "_path"] = relative
                document["compiled_revision"] = digest(document["content"])
                document["checks"] = files["checks"]
                document["keyword_coverage"] = files.get("keyword_coverage", [])
            manifest["policy_hash"] = policy_hash
            manifest["profile_hashes"] = input_hashes
            manifest["last_run_id"] = run_id
            fields, rows = self.tracker()
            for column in STANDARD_COLUMNS + IDENTITY_COLUMNS:
                if column not in fields:
                    fields.append(column)
            row = next((r for r in rows if r.get("application_id") == application_id), None)
            if row is None:
                row = dict.fromkeys(fields, "")
                row.update(
                    date=date.today().isoformat(), company=application["company"], role=application["role"],
                    status="drafted", fit_rating=str(application.get("fit_rating") or ""),
                    source=application.get("source", ""), deadline=application.get("deadline") or "",
                    application_id=application_id, archive_dir=archive,
                )
                rows.append(row)
            if row["status"] == "drafted":
                row["cv_file"] = manifest["documents"]["cv"]["tex_path"]
                row["cover_letter_file"] = manifest["documents"]["cover_letter"]["tex_path"]
                changes[str(Path(archive, "cv_draft.tex"))] = outputs["cv"]["tex"]
                changes[str(Path(archive, "cover_letter.tex"))] = outputs["cover_letter"]["tex"]
            new_location = self._manifest_path(application_id, archive)
            if new_location != location:
                expected[new_location] = None
            changes[new_location] = encoded(manifest).encode("utf-8")
            if location != new_location:
                changes[location] = None
            posting_path = str(Path(archive, "job_posting.md"))
            if self.workspace.read(posting_path) is None:
                changes[posting_path] = manifest.get("posting", "").encode("utf-8")
            changes[TRACKER_PATH] = self.tracker_bytes(fields, rows)
            self.workspace.commit(operation_id, changes, {**expected, **input_hashes})

    def submit(self, operation_id: str, application_id: str, data: SubmissionInput) -> dict:
        return self.outcome(operation_id, application_id, OutcomeInput(
            expected_revision=data.expected_revision, status="applied", occurred_on=data.submitted_on,
            submitted_on=data.submitted_on, notes=data.notes, stage="Submitted",
        ), require_verified=True)

    def outcome(self, operation_id: str, application_id: str, data: OutcomeInput, *,
                require_verified: bool = False) -> dict:
        note = safe_notes(data.notes)
        stage = safe_notes(data.stage)
        if data.occurred_on > date.today():
            raise AppError("future_outcome", "Use the date the change actually happened, not a future date.")
        with self.workspace.lock():
            application, location, manifest, expected = self._edit_snapshot(application_id)
            if application["revision"] != data.expected_revision:
                raise AppError("revision_conflict", "This application changed. Reload before recording the update.", 409)
            if not application["published"]:
                raise AppError("not_ready", "Complete document preparation before recording submission.", 409)
            fields, rows = self.tracker()
            row = next(r for r in rows if r.get("application_id") == application_id)
            previous_status = ALIASES.get(row["status"], row["status"])
            if previous_status in FINAL:
                raise AppError("closed_application", "This application is closed. Create a separate application to reapply.", 409)
            transitioning = previous_status == "drafted"
            if transitioning and data.submitted_on is None:
                raise AppError("submission_date_required", "Supply the actual submission date, not the draft creation date.")
            if data.submitted_on and (data.submitted_on > data.occurred_on or data.submitted_on > date.today()):
                raise AppError("invalid_submission_date", "Submission must be on or before the outcome date.")
            changes: dict[str, bytes | None] = {}
            if require_verified or transitioning:
                expected.update(manifest.get("profile_hashes", {}))
                documents = application["documents"]
                if set(documents) != {"cv", "cover_letter"} or any(
                    document["stale"] or not document["checks"]
                    or any(check["status"] != "pass" for check in document["checks"])
                    for document in documents.values()
                ):
                    raise AppError("documents_not_current", "Review and compile the current documents before recording submission.", 409)
                submission_id = digest(operation_id)[:32]
                submission_path = Path(application["archive_dir"], "submissions", submission_id)
                submission = {"id": submission_id, "submitted_on": (data.submitted_on or data.occurred_on).isoformat(),
                              "documents": {}, "recorded_at": now()}
                for kind, document in manifest["documents"].items():
                    frozen = {}
                    for extension in ("tex", "pdf"):
                        content = self.workspace.read(document[extension + "_path"])
                        if content is None:
                            raise AppError("missing_document", "A verified document is missing. Compile it again.", 409)
                        relative = str(submission_path / f"{kind}.{extension}")
                        if self.workspace.read(relative) is not None:
                            raise AppError("immutable_submission", "This submitted revision already exists.", 409)
                        changes[relative] = content
                        expected[relative] = None
                        frozen[extension + "_path"] = relative
                        frozen[extension + "_hash"] = digest(content)
                    frozen["content"] = document["content"]
                    submission["documents"][kind] = frozen
                receipt_path = str(submission_path / "submission.json")
                changes[receipt_path] = encoded(submission).encode("utf-8")
                expected[receipt_path] = None
                manifest.setdefault("submitted_revisions", []).append(submission)
                row["cv_file"] = submission["documents"]["cv"]["tex_path"]
                row["cover_letter_file"] = submission["documents"]["cover_letter"]["tex_path"]
                changes[str(Path(application["archive_dir"], "cv_draft.tex"))] = changes[row["cv_file"]]
                changes[str(Path(application["archive_dir"], "cover_letter.tex"))] = changes[row["cover_letter_file"]]
            if transitioning:
                row["date"] = data.submitted_on.isoformat()
            row["status"] = data.status
            dated_note = f"{data.occurred_on.isoformat()} {stage or data.status}"
            if note:
                dated_note += " - " + note
            row["notes"] = (row.get("notes", "") + "; " + dated_note).strip("; ")
            manifest.setdefault("timeline", []).append({
                "date": data.occurred_on.isoformat(), "status": data.status, "stage": stage, "notes": note,
            })
            changes[location] = encoded(manifest).encode("utf-8")
            outcome_path = str(Path(application["archive_dir"], "outcome.md"))
            changes[outcome_path] = self.outcome_text(
                application["company"], application["role"], data.status, manifest["timeline"],
                previous=self.workspace.text(outcome_path),
            ).encode("utf-8")
            changes[TRACKER_PATH] = self.tracker_bytes(fields, rows)
            self.workspace.commit(operation_id, changes, expected)
        return self.get(application_id)

    @staticmethod
    def outcome_text(company: str, role: str, status: str, timeline: list[dict], previous: str = "") -> str:
        from .profile import replace_section
        archive_status = "interview_only" if status == "withdrawn" else (status if status in FINAL else "in_progress")
        text = previous or f"# Outcome: {company} - {role}\n\n**Status:** in_progress\n\n## Interview stages reached\n\n## Notes\n"
        text = re.sub(r"^\*\*Status:\*\*.*$", f"**Status:** {archive_status}", text, count=1, flags=re.M)
        if status in FINAL:
            line = "**Date resolved:** " + timeline[-1]["date"]
            if re.search(r"^\*\*Date resolved:\*\*", text, re.M):
                text = re.sub(r"^\*\*Date resolved:\*\*.*$", line, text, count=1, flags=re.M)
            else:
                text = text.replace("\n\n## Interview", "\n\n" + line + "\n\n## Interview", 1)
        for entry in timeline:
            marker = f"- [{entry['date']}] {entry['stage'] or entry['status']}"
            if entry.get("notes"):
                marker += " - " + entry["notes"]
            if marker not in text:
                text = text.rstrip() + "\n" + marker + "\n"
        reached = [entry for entry in timeline if entry["status"] in ("interview", "offer", "hired", "offer_declined")]
        if reached:
            from .profile import section
            body = section(text, "Interview stages reached")
            for entry in reached:
                line = f"- [x] {entry['stage'] or entry['status']} ({entry['date']})"
                if line not in body:
                    body = body.rstrip() + "\n" + line
            text = replace_section(text, "Interview stages reached", body)
        return text

    def migration_proposal(self) -> dict:
        expected = self.workspace.snapshot([TRACKER_PATH])
        fields, rows = self.tracker()
        original = self.workspace.text(TRACKER_PATH)
        for column in ("deadline", *IDENTITY_COLUMNS):
            if column not in fields:
                fields.append(column)
        archives: set[str] = set()
        for index, row in enumerate(rows):
            row.setdefault("application_id", self.row_id(row, index))
            if not row["application_id"]:
                row["application_id"] = self.row_id(row, index)
            if not row.get("archive_dir"):
                legacy = str(Path("documents", "applications", prefix(row["company"], row["role"])))
                if self.workspace.path(legacy).exists():
                    row["archive_dir"] = legacy
                else:
                    row["archive_dir"] = archive_for(row["company"], row["role"], row["application_id"])
            if row["archive_dir"] in archives:
                raise AppError("ambiguous_legacy_archive", "Two applications share a legacy archive. Select distinct archives before importing.", 409)
            archives.add(row["archive_dir"])
            self.validate_archive(row["archive_dir"])
        updated = self.tracker_bytes(fields, rows).decode("utf-8")
        self.workspace.unchanged(expected)
        return {"files": [{"path": TRACKER_PATH, "before": original, "after": updated}],
                "expected": expected, "application_count": len(rows)}
