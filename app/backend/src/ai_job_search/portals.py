from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import yaml

from .errors import AppError
from .models import PortalChange, SearchInput
from .processes import command
from .storage import digest, now
from .workspace import Workspace

REVIEWED = {
    "freehire-search": ("Freehire", "--query"),
    "linkedin-search": ("LinkedIn", "--query"),
    "jobindex-search": ("Jobindex (Denmark)", "--query"),
    "jobbank-search": ("Jobbank (Denmark)", "--key"),
    "jobdanmark-search": ("Jobdanmark (Denmark)", "--text"),
    "jobnet-search": ("Jobnet (Denmark)", "--search-string"),
}


class PortalService:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    @staticmethod
    def relative(portal_id: str) -> str:
        return str(Path(".agents", "skills", portal_id, "SKILL.md"))

    def get(self, portal_id: str) -> dict:
        if portal_id not in REVIEWED:
            raise AppError("unreviewed_portal", "This portal is not a reviewed application adapter.", 409)
        source = self.workspace.resources / self.relative(portal_id)
        metadata = self.workspace.text(self.relative(portal_id)) or source.read_text(encoding="utf-8")
        try:
            frontmatter = metadata.split("---", 2)[1]
            data = yaml.safe_load(frontmatter)
        except (IndexError, yaml.YAMLError) as exc:
            raise AppError("invalid_portal_metadata", f"{portal_id}'s metadata needs repair.", 409) from exc
        if not isinstance(data, dict) or not isinstance(data.get("enabled", False), bool):
            raise AppError("invalid_portal_metadata", f"{portal_id}'s enabled setting must be a boolean.", 409)
        options, _ = self.workspace.store.setting("portal:" + portal_id, {})
        acknowledged = options.get("acknowledged", False)
        installed_enabled = data.get("enabled", False)
        executable = self.executable(portal_id)
        included = bool(executable) or (self.workspace.resources / ".agents" / "skills" / portal_id / "cli" / "src" / "cli.ts").is_file()
        enabled = bool(installed_enabled)
        warning = ""
        if portal_id == "linkedin-search":
            warning = "LinkedIn prohibits automated access in its Terms of Service. Personal, low-volume use only; acknowledge responsibility before enabling."
            enabled = enabled and bool(acknowledged)
        if portal_id == "freehire-search":
            warning = "Tech-focused, community-maintained aggregator; availability and completeness are not guaranteed."
        if not included:
            warning = "This optional source is not included in this distribution. Use an available source or paste the full posting instead."
        health, _ = self.workspace.store.setting("portal-health:" + portal_id, {})
        return {
            "id": portal_id, "label": REVIEWED[portal_id][0], "enabled": installed_enabled,
            "installed_enabled": installed_enabled, "effective_enabled": enabled and bool(executable),
            "supported": included, "warning": warning, "acknowledged": acknowledged,
            "health": health or {"status": "not_checked"}, "revision": digest({"metadata": metadata, "options": options}),
            "runtime_ready": bool(executable),
        }

    def all(self) -> list[dict]:
        result = [self.get(portal_id) for portal_id in REVIEWED]
        skills = self.workspace.root / ".agents" / "skills"
        if skills.exists():
            for folder in skills.iterdir():
                if folder.is_dir() and folder.name not in REVIEWED and (folder / "SKILL.md").exists():
                    result.append({
                        "id": folder.name, "label": folder.name, "enabled": False, "installed_enabled": False,
                        "effective_enabled": False, "supported": False, "warning": "Installed but not reviewed for GUI execution.",
                        "health": {"status": "unsupported"}, "revision": "",
                    })
        return result

    def executable(self, portal_id: str) -> list[str] | None:
        bundled = self.workspace.resources / "runtimes" / "portals" / (portal_id + ".exe")
        if bundled.is_file():
            return [str(bundled)]
        from .readiness import find_tool
        found = find_tool("bun", self.workspace.resources)
        directory = self.workspace.resources / ".agents" / "skills" / portal_id / "cli"
        entry = directory / "src" / "cli.ts"
        manifest = directory / "package.json"
        if manifest.is_file():
            dependencies = json.loads(manifest.read_text(encoding="utf-8")).get("dependencies", {})
            if any(not (directory / "node_modules" / name).exists() for name in dependencies):
                return None
        return [found, "run", "--no-install", str(entry)] if found and entry.is_file() else None

    def configure(self, operation_id: str, portal_id: str, change: PortalChange) -> dict:
        with self.workspace.lock():
            relative = self.relative(portal_id)
            expected = self.workspace.snapshot([relative])
            current = self.get(portal_id)
            if change.enabled and not current["supported"]:
                raise AppError("portal_not_bundled", current["warning"], 409)
            if current["revision"] != change.expected_revision:
                raise AppError("revision_conflict", "This portal setting changed. Reload before saving.", 409)
            if portal_id == "linkedin-search" and change.enabled and not change.acknowledged:
                raise AppError("portal_acknowledgement_required", current["warning"], 409)
            original = self.workspace.text(relative) or (self.workspace.resources / relative).read_text(encoding="utf-8")
            updated = re.sub(r"^enabled:\s*(?:true|false)", f"enabled: {'true' if change.enabled else 'false'}",
                             original, count=1, flags=re.M)
            self.workspace.commit(operation_id, {relative: updated.encode("utf-8")}, expected)
            self.workspace.store.save_setting("portal:" + portal_id, {"acknowledged": change.acknowledged})
        return self.get(portal_id)

    def arguments(self, portal_id: str, request: SearchInput) -> list[str]:
        if portal_id not in REVIEWED:
            raise AppError("unreviewed_portal", "This portal has not been reviewed.", 409)
        if request.query.startswith("-") or request.location.startswith("-"):
            raise AppError("invalid_search", "Search text cannot start with a command-line flag.")
        arguments = ["search", REVIEWED[portal_id][1], request.query, "--limit", str(request.limit), "--format", "json"]
        if portal_id == "linkedin-search":
            if not request.location.strip():
                raise AppError("search_location_required", "LinkedIn requires an explicit search location.")
            arguments += ["--location", request.location, "--jobage", "7"]
        elif portal_id == "freehire-search":
            arguments += ["--jobage", "7", "--description-format", "text"]
            if request.country:
                arguments += ["--country", request.country.upper()]
            if request.location and request.location.casefold() != "remote":
                arguments += ["--city", request.location]
            elif request.location.casefold() == "remote":
                arguments += ["--remote", "remote"]
        else:
            if request.country and request.country.upper() != "DK":
                raise AppError("portal_market_mismatch", "This opt-in portal supports Denmark only.")
            if portal_id == "jobindex-search":
                arguments[2] = " ".join(value for value in (request.query, request.location) if value)
                arguments += ["--jobage", "7", "--sort", "date"]
            elif portal_id == "jobbank-search":
                arguments += ["--since", (date.today() - timedelta(days=7)).isoformat()]
            elif portal_id == "jobnet-search":
                arguments += ["--order", "PublicationDate"]
        return arguments

    def search(self, portal_id: str, request: SearchInput, directory: Path) -> list[dict]:
        current = self.get(portal_id)
        if not current["effective_enabled"]:
            raise AppError("portal_not_ready", f"{current['label']} is disabled, unacknowledged, or missing its runtime.", 409)
        executable = self.executable(portal_id)
        if not executable:
            raise AppError("portal_runtime_missing", "Install the portal runtime from Settings.", 409)
        directory.mkdir(parents=True, exist_ok=True)
        try:
            raw = command(executable + self.arguments(portal_id, request), directory, timeout=90)
            envelope = json.loads(raw)
            records = envelope.get("results") if isinstance(envelope, dict) else None
            if not isinstance(records, list):
                raise AppError("portal_invalid_output", "The portal returned an unsupported result envelope.", 502)
            normalized = []
            for record in records[:request.limit]:
                if not isinstance(record, dict) or not all(isinstance(record.get(k), str) for k in ("title", "company", "url")):
                    raise AppError("portal_invalid_output", "A portal result is missing title, employer, or URL.", 502)
                location = record.get("location") or ""
                if not isinstance(location, str):
                    location = str(location)
                if portal_id not in ("freehire-search", "linkedin-search", "jobindex-search") and request.location:
                    if request.location.casefold() not in location.casefold():
                        continue
                posted = record.get("date", record.get("posted_date"))
                if portal_id in ("jobdanmark-search", "jobnet-search") and isinstance(posted, str):
                    try:
                        if date.fromisoformat(posted[:10]) < date.today() - timedelta(days=7):
                            continue
                    except ValueError:
                        pass
                normalized.append({
                    **record, "portal": portal_id, "source": "cli", "location": location,
                    "description": record.get("description_text") or record.get("description") or "",
                    "posted_date": posted,
                    "warnings": (["Location filtering was applied to this limited result batch, not the portal's full inventory."]
                                 if request.location and portal_id in ("jobbank-search", "jobdanmark-search", "jobnet-search") else []),
                })
            self.workspace.store.save_setting("portal-health:" + portal_id, {"status": "ready", "last_ok": now()})
            return normalized
        except (AppError, json.JSONDecodeError) as exc:
            message = exc.message if isinstance(exc, AppError) else "Invalid JSON result from portal."
            self.workspace.store.save_setting("portal-health:" + portal_id, {
                "status": "unavailable", "last_error": message, "checked_at": now(),
            })
            if isinstance(exc, json.JSONDecodeError):
                raise AppError("portal_invalid_output", message, 502) from exc
            raise
