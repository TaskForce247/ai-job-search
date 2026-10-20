from __future__ import annotations

import io
import json
import os
import sqlite3
import stat
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .errors import AppError
from .storage import SCHEMA, digest, encoded, now
from .workspace import Workspace

MAGIC = b"AIJS-BACKUP-1\n"
MAX_ARCHIVE = 200 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024


def allowed_backup_path(name: str) -> bool:
    parts = PurePosixPath(name).parts
    if (not parts or ".." in parts or name.startswith("/") or "\\" in name or ":" in name or "\x00" in name
            or PurePosixPath(name).as_posix() != name
            or any(part.rstrip(" .") != part or PureWindowsPath(part).is_reserved() for part in parts)):
        return False
    if name in ("CLAUDE.md", "AGENTS.md", "job_search_tracker.csv", ".app/state.sqlite3"):
        return True
    if name.startswith(".claude/") and name.endswith(".md"):
        return True
    if name.startswith(".agents/skills/") and name.endswith("/SKILL.md"):
        return True
    if name.startswith(("documents/", "cv/", "cover_letters/", "company_research/", "reports/")):
        return name.endswith((".md", ".txt", ".json", ".csv", ".tex", ".pdf", ".html"))
    if name == "job_scraper/seen_jobs.json":
        return True
    if name.startswith((".app/preparations/", ".app/runs/")):
        return not any("runtime" in part or part == "config" for part in parts) and name.endswith(
            (".json", ".md", ".txt", ".pdf", ".tex", ".png"),
        )
    return False


def key_for(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


class BackupService:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def paths(self, *, include_runs: bool = False) -> list[str]:
        result = []
        folders = [".claude", ".agents", "documents", "cv", "cover_letters", "company_research",
                   "reports", "job_scraper", str(Path(".app", "preparations"))]
        if include_runs:
            folders.append(str(Path(".app", "runs")))
        for folder in folders:
            parent = self.workspace.path(folder)
            if not parent.exists():
                continue
            for path in parent.rglob("*"):
                if path.is_symlink() or path.is_junction():
                    raise AppError("unsafe_backup_path", "Remove links from career-data folders before backup or restore.", 409)
                relative = path.relative_to(self.workspace.root).as_posix()
                if path.is_file() and allowed_backup_path(relative):
                    result.append(relative)
        for name in ("CLAUDE.md", "AGENTS.md", "job_search_tracker.csv"):
            if self.workspace.path(name).is_file():
                result.append(name)
        return sorted(result)

    def create(self, operation_id: str, passphrase: str = "") -> str:
        if passphrase and len(passphrase) < 12:
            raise AppError("weak_backup_passphrase", "Use at least 12 characters to encrypt the backup.")
        with self.workspace.lock():
            directory = self.workspace.path(str(Path(".app", "backups")))
            directory.mkdir(parents=True, exist_ok=True)
            database = directory / (digest(operation_id) + ".sqlite3")
            self.workspace.store.backup(database)
            files: dict[str, bytes] = {".app/state.sqlite3": database.read_bytes()}
            total = len(files[".app/state.sqlite3"])
            for relative in self.paths(include_runs=True):
                content = self.workspace.read(relative)
                if content is None:
                    raise AppError("backup_source_changed", "A career-data file disappeared during backup. Retry.", 409)
                size = len(content)
                if size > 50 * 1024 * 1024 or total + size > MAX_EXPANDED or len(files) >= 10_000:
                    raise AppError("backup_too_large", "The workspace exceeds the supported backup limits. Archive large files externally.")
                files[relative] = content
                total += size
            manifest = {"format": 1, "created_at": now(), "files": {name: digest(data) for name, data in files.items()}}
            with io.BytesIO() as buffer:
                with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for name, data in files.items():
                        archive.writestr(name, data)
                    archive.writestr("manifest.json", encoded(manifest))
                output = buffer.getvalue()
            extension, media_type = ".zip", "application/zip"
            if passphrase:
                salt, nonce = os.urandom(16), os.urandom(12)
                output = MAGIC + salt + nonce + AESGCM(key_for(passphrase, salt)).encrypt(nonce, output, MAGIC)
                extension, media_type = ".aijsbackup", "application/octet-stream"
            relative = str(Path(".app", "backups", digest(operation_id) + extension))
            self.workspace._durable_write(self.workspace.path(relative), output)
            database.unlink(missing_ok=True)
            return self.workspace.store.register_artifact(
                relative, output, media_type, "AIJobSearch-" + now()[:10] + extension,
            )

    def inspect(self, data: bytes, passphrase: str = "") -> dict:
        if len(data) > MAX_ARCHIVE:
            raise AppError("backup_too_large", "Backups must be smaller than 200 MiB.")
        if data.startswith(MAGIC):
            if not passphrase or len(data) < len(MAGIC) + 44:
                raise AppError("backup_passphrase_required", "Enter the passphrase used to encrypt this backup.")
            start = len(MAGIC)
            salt, nonce = data[start:start + 16], data[start + 16:start + 28]
            try:
                data = AESGCM(key_for(passphrase, salt)).decrypt(nonce, data[start + 28:], MAGIC)
            except InvalidTag as exc:
                raise AppError("backup_decryption_failed", "The passphrase is incorrect or the encrypted backup is damaged.") from exc
        files: dict[str, bytes] = {}
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 10_001 or sum(item.file_size for item in entries) > MAX_EXPANDED:
                    raise AppError("backup_too_large", "The expanded backup exceeds the safe restore limit.")
                names = [item.filename for item in entries]
                if len(names) != len({name.casefold() for name in names}):
                    raise AppError("invalid_backup", "The backup contains duplicate or case-colliding file names.")
                for item in entries:
                    if item.filename == "manifest.json":
                        continue
                    if not allowed_backup_path(item.filename) or stat.S_ISLNK(item.external_attr >> 16):
                        raise AppError("unsafe_backup_path", "The backup contains an unsafe or unsupported file.")
                    if item.file_size > 50 * 1024 * 1024:
                        raise AppError("backup_too_large", "A backup file exceeds 50 MiB.")
                    files[item.filename] = archive.read(item)
                manifest = json.loads(archive.read("manifest.json"))
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
            raise AppError("invalid_backup", "This is not a valid AI Job Search backup.") from exc
        if (not isinstance(manifest, dict) or manifest.get("format") != 1
                or not isinstance(manifest.get("created_at"), str)
                or manifest.get("files") != {name: digest(content) for name, content in files.items()}):
            raise AppError("backup_integrity_failed", "The backup manifest or file checksums do not match.")
        if ".app/state.sqlite3" not in files:
            raise AppError("invalid_backup", "This backup has no operational database.")
        folded_files = {name.casefold() for name in files}
        for name in files:
            if any(parent.as_posix().casefold() in folded_files for parent in PurePosixPath(name).parents):
                raise AppError("invalid_backup", "A backup file is also used as a directory.")
        restore_id = str(uuid4())
        directory = self.workspace.path(str(Path(".app", "restores", restore_id)))
        directory.mkdir(parents=True)
        staged = {}
        for index, (name, content) in enumerate(files.items()):
            path = directory / str(index)
            self.workspace._durable_write(path, content)
            staged[name] = str(path.relative_to(self.workspace.root))
        database = self.workspace.path(staged[".app/state.sqlite3"])
        try:
            connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
            expected_database = sqlite3.connect(":memory:")
            try:
                connection.execute("PRAGMA trusted_schema=OFF")
                expected_database.executescript(SCHEMA)
                query = ("SELECT type,name,tbl_name,sql FROM sqlite_master "
                         "WHERE name NOT GLOB 'sqlite_autoindex_*' AND name<>'sqlite_sequence' ORDER BY name")
                actual_schema = connection.execute(query).fetchall()
                expected_schema = expected_database.execute(query).fetchall()
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if version != 1 or integrity != "ok" or actual_schema != expected_schema:
                    raise AppError("unsupported_backup", "The backup database is damaged or needs a different app version.")
                if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise AppError("invalid_backup", "The backup database contains broken record links.")
            finally:
                connection.close()
                expected_database.close()
        except sqlite3.DatabaseError as exc:
            raise AppError("invalid_backup", "The backup's database is invalid.") from exc
        existing = self.paths()
        removed = sorted(set(existing) - files.keys())
        targets = (files.keys() | set(removed)) - {".app/state.sqlite3"}
        expected = self.workspace.snapshot([str(Path(*PurePosixPath(name).parts)) for name in sorted(targets)])
        return {"restore_id": restore_id, "staged": staged, "hashes": manifest["files"],
                "expected": expected, "existing": existing, "removed": removed, "files": [
                    {"path": name, "action": "replace" if self.workspace.path(str(Path(*PurePosixPath(name).parts))).exists() else "create"}
                    for name in files
                ] + [{"path": name, "action": "delete"} for name in removed],
                "backup_date": manifest["created_at"], "confirmation": "RESTORE"}

    def restore(self, operation_id: str, proposal: dict, *, completion: dict | None = None) -> dict:
        with self.workspace.lock():
            if self.paths() != proposal["existing"]:
                raise AppError("external_edit", "Career-data files were added or removed after restore review. Import the backup again.", 409)
            changes = {str(Path(*PurePosixPath(name).parts)): None for name in proposal["removed"]}
            for name, staged in proposal["staged"].items():
                content = self.workspace.read(staged)
                if content is None or digest(content) != proposal["hashes"][name]:
                    raise AppError("restore_staging_changed", "The reviewed restore files changed. Import the backup again.", 409)
                if name != ".app/state.sqlite3":
                    changes[str(Path(*PurePosixPath(name).parts))] = content
            self.workspace.unchanged(proposal["expected"])
            marker = {
                "operation_id": operation_id, "journal_id": digest(operation_id),
                "database": proposal["staged"][".app/state.sqlite3"],
                "database_hash": proposal["hashes"][".app/state.sqlite3"],
                "sequence_floor": self.workspace.store.last_sequence(),
                "completion": completion or {},
            }
            marker_path = self.workspace.path(str(Path(".app", "restore-pending.json")))
            self.workspace._durable_write(marker_path, encoded(marker).encode("utf-8"))
            self.workspace.commit(operation_id, changes, proposal["expected"])
            return self.workspace.complete_restore() or {}

    def reset_proposal(self, scope: str) -> dict:
        files: dict[str, str | None] = {}
        if scope in ("profile", "all"):
            from .profile import FACT_PATHS
            for relative in FACT_PATHS:
                files[relative] = (self.workspace.resources / relative).read_text(encoding="utf-8")
        if scope in ("documents", "all"):
            for folder in (str(Path("documents", "applications")), str(Path(".app", "preparations"))):
                directory = self.workspace.path(folder)
                if directory.exists():
                    for path in directory.rglob("*"):
                        if path.is_file() and path.name != ".gitkeep":
                            files[str(path.relative_to(self.workspace.root))] = None
            files["job_search_tracker.csv"] = None
        if scope == "all":
            files[str(Path("job_scraper", "seen_jobs.json"))] = '{"seen": {}}\n'
        return {
            "scope": scope, "files": [{"path": path, "action": "reset" if content is not None else "delete",
                                     "after": content} for path, content in files.items()],
            "expected": self.workspace.snapshot(list(files)), "confirmation": "RESET",
            "note": "A backup is created before reset. Original uploads and unrelated files are retained.",
        }
