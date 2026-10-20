from __future__ import annotations

import json
import os
import shutil
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path, PurePath
from typing import Iterator
from uuid import uuid4

from .errors import AppError
from .storage import Store, digest, encoded, now

PROFILE_PATH = str(Path(".claude", "skills", "job-application-assistant", "01-candidate-profile.md"))
TRACKER_PATH = "job_search_tracker.csv"
JOBS_PATH = str(Path("job_scraper", "seen_jobs.json"))


class Workspace:
    def __init__(self, root: Path, resources: Path, *, create: bool = False):
        self.root = root.absolute()
        self.resources = resources.resolve()
        self._mutex = threading.RLock()
        self._local = threading.local()
        self.application_overviews: dict[str, tuple[object, dict]] = {}
        self.application_watcher = None
        self.overview_revision: str | None = None
        self.overview_records: list[dict] = []
        self._validate_root()
        if not root.exists():
            if not create:
                raise AppError("workspace_missing", "The selected workspace no longer exists.", 404)
            root.mkdir(parents=True)
        if create and not (root / PROFILE_PATH).exists():
            self._seed()
        if not (root / PROFILE_PATH).is_file():
            raise AppError("unsupported_workspace", "Choose an AI Job Search workspace, or create an empty one.")
        self.path(".app").mkdir(exist_ok=True)
        self.store = Store(self.path(str(Path(".app", "state.sqlite3"))))
        with self.lock():
            self.recover()
            self.complete_restore()

    def _validate_root(self) -> None:
        value = str(self.root)
        if value.startswith(("\\\\", "//")):
            raise AppError("network_workspace", "Choose a local disk, not a network share.")
        for item in (self.root, *self.root.parents):
            if item.exists() and (item.is_symlink() or item.is_junction()):
                raise AppError("linked_workspace", "Choose a workspace without symbolic links or junctions.")
        if os.name == "nt" and self.root.drive:
            import ctypes
            if ctypes.windll.kernel32.GetDriveTypeW(self.root.anchor) == 4:
                raise AppError("network_workspace", "Choose a local disk, not a mapped network drive.")

    def _seed(self) -> None:
        existing = [p for p in self.root.iterdir() if p.name not in (".app",)]
        if existing:
            raise AppError("directory_not_empty", "Create a workspace in an empty folder. Existing files were not changed.")
        for folder in (".claude",):
            for source in (self.resources / folder).rglob("*.md"):
                relative = source.relative_to(self.resources)
                target = self.path(str(relative))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        for relative in (
            "CLAUDE.md", "AGENTS.md", str(Path("cv", "main_example.tex")),
            str(Path("cover_letters", "cover_example.tex")), str(Path("cover_letters", "cover.cls")),
            str(Path("documents", "README.md")),
        ):
            source = self.resources / relative
            target = self.path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        shutil.copytree(self.resources / "cover_letters" / "OpenFonts", self.path(str(Path("cover_letters", "OpenFonts"))))
        self.path("job_scraper").mkdir(exist_ok=True)
        self.path(JOBS_PATH).write_text('{"seen": {}}\n', encoding="utf-8")

    def path(self, relative: str) -> Path:
        self.stat_paths([relative])
        return self.root / relative

    def close(self) -> None:
        with self._mutex:
            if self.application_watcher is not None:
                self.application_watcher.close()
                self.application_watcher = None
            self.overview_revision = None
            self.application_overviews.clear()
            self.overview_records.clear()

    def stat_paths(self, relatives: list[str]) -> dict[str, os.stat_result | None]:
        if not relatives:
            return {}

        def inspect(path):
            try:
                return os.stat(path, follow_symlinks=False)
            except (FileNotFoundError, NotADirectoryError):
                return None

        def reject_link(info):
            if info and (stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", None) == stat.IO_REPARSE_TAG_MOUNT_POINT):
                raise AppError("unsafe_path", "Linked files and junctions are not allowed in the workspace.")

        root = str(self.root)
        checked = {(): inspect(root)}
        reject_link(checked[()])
        directories = {}
        bulk = len(relatives) > 1
        result = {}
        for relative in relatives:
            part = Path(relative)
            if not relative or part.root or part.drive or any(p in ("..", "") for p in PurePath(relative).parts):
                raise AppError("unsafe_path", "The requested file is outside the workspace.")
            if "\x00" in relative or ":" in relative:
                raise AppError("unsafe_path", "The requested file name is not supported.")
            key, absolute = (), root
            for component in part.parts:
                parent_key, parent = key, absolute
                key += (os.path.normcase(component),)
                absolute = os.path.join(parent, component)
                if key in checked:
                    continue
                info = None
                parent_info = checked[parent_key]
                if parent_info and stat.S_ISDIR(parent_info.st_mode):
                    if bulk:
                        # Windows directory entries carry timestamps without reopening every file.
                        if parent_key not in directories:
                            with os.scandir(parent) as entries:
                                directories[parent_key] = {os.path.normcase(entry.name): entry for entry in entries}
                        entry = directories[parent_key].get(os.path.normcase(component))
                        info = entry.stat(follow_symlinks=False) if entry else None
                    else:
                        info = inspect(absolute)
                reject_link(info)
                checked[key] = info
            result[relative] = checked[key]
        return result

    def read(self, relative: str) -> bytes | None:
        path = self.path(relative)
        if not path.exists():
            return None
        if not path.is_file():
            raise AppError("not_a_file", "A workspace record points to a directory.", 409)
        return path.read_bytes()

    def text(self, relative: str) -> str:
        return self.decode_text(relative, self.read(relative))

    @staticmethod
    def decode_text(relative: str, content: bytes | None) -> str:
        if content is None:
            return ""
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise AppError("invalid_encoding", f"{relative} is not UTF-8. Import a converted copy instead.", 409) from exc

    def hash(self, relative: str) -> str | None:
        data = self.read(relative)
        return digest(data) if data is not None else None

    def snapshot(self, relatives: list[str]) -> dict[str, str | None]:
        return {relative: self.hash(relative) for relative in relatives}

    def unchanged(self, expected: dict[str, str | None]) -> None:
        conflicts = [relative for relative, checksum in expected.items() if self.hash(relative) != checksum]
        if conflicts:
            raise AppError("external_edit", "Workspace files changed. Review the new version and retry.", 409,
                           detail={"files": conflicts})

    @contextmanager
    def lock(self) -> Iterator[None]:
        with self._mutex:
            depth = getattr(self._local, "depth", 0)
            if depth:
                self._local.depth = depth + 1
                try:
                    yield
                finally:
                    self._local.depth -= 1
                return
            lock_path = self.path(str(Path(".app", "workspace.lock")))
            lock_path.parent.mkdir(exist_ok=True)
            handle = lock_path.open("a+b")
            handle.seek(0, os.SEEK_END)
            if not handle.tell():
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 10
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() > deadline:
                        handle.close()
                        raise AppError("workspace_busy", "Another operation is writing this workspace. Retry shortly.", 409) from exc
                    time.sleep(0.05)
            self._local.depth = 1
            try:
                yield
            finally:
                self._local.depth = 0
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()

    @staticmethod
    def _durable_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def commit(self, operation_id: str, changes: dict[str, bytes | None],
               expected: dict[str, str | None]) -> None:
        if any(relative not in expected for relative in changes):
            raise ValueError("Every changed file must have an expected hash.")
        transaction_id = digest(operation_id)
        directory = self.path(str(Path(".app", "transactions", transaction_id)))
        journal_path = directory / "journal.json"
        with self.lock():
            if journal_path.exists():
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
                if journal["state"] == "committed":
                    for entry in journal["files"]:
                        if self.hash(entry["path"]) != entry["after_hash"]:
                            raise AppError("external_edit", "A previously saved operation was edited externally.", 409)
                    return
                if journal["state"] != "rolled_back":
                    raise AppError("recovery_required", "A previous file operation needs recovery.", 409)
                raise AppError("retry_with_new_operation", "This operation was rolled back. Create a fresh retry.", 409)
            self.unchanged(expected)
            directory.mkdir(parents=True, exist_ok=True)
            entries = []
            for index, (relative, content) in enumerate(changes.items()):
                original = self.read(relative)
                if original is not None:
                    self._durable_write(directory / f"{index}.before", original)
                if content is not None:
                    self._durable_write(directory / f"{index}.after", content)
                entries.append({
                    "path": relative, "index": index, "before_hash": digest(original) if original is not None else None,
                    "after_hash": digest(content) if content is not None else None,
                })
            journal = {"id": transaction_id, "state": "prepared", "files": entries, "created_at": now()}
            self._durable_write(journal_path, encoded(journal).encode("utf-8"))
            with self.store.connect(write=True) as connection:
                stamp = now()
                connection.execute("INSERT INTO file_transactions VALUES (?,?,?,?,?)",
                                   (transaction_id, "prepared", str(journal_path.relative_to(self.root)), stamp, stamp))
            try:
                self.unchanged(expected)
                for entry in entries:
                    self._replace_entry(directory, entry, "after")
                journal["state"] = "committed"
                self._durable_write(journal_path, encoded(journal).encode("utf-8"))
                with self.store.connect(write=True) as connection:
                    connection.execute("UPDATE file_transactions SET state='committed',updated_at=? WHERE id=?",
                                       (now(), transaction_id))
            except BaseException:
                self._rollback(directory, journal)
                raise

    def _replace_entry(self, directory: Path, entry: dict, version: str) -> None:
        target = self.path(entry["path"])
        if entry[version + "_hash"] is None:
            target.unlink(missing_ok=True)
        else:
            data = (directory / f"{entry['index']}.{version}").read_bytes()
            if digest(data) != entry[version + "_hash"]:
                raise AppError("damaged_journal", "A recovery file is damaged. Restore a backup before continuing.", 409)
            self._durable_write(target, data)

    def _rollback(self, directory: Path, journal: dict) -> None:
        for entry in reversed(journal["files"]):
            if self.hash(entry["path"]) not in (entry["before_hash"], entry["after_hash"]):
                raise AppError("recovery_conflict", "A file changed during recovery. Preserve it and restore a backup.", 409,
                               detail={"file": entry["path"]})
            self._replace_entry(directory, entry, "before")
        journal["state"] = "rolled_back"
        self._durable_write(directory / "journal.json", encoded(journal).encode("utf-8"))
        with self.store.connect(write=True) as connection:
            connection.execute("UPDATE file_transactions SET state='rolled_back',updated_at=? WHERE id=?",
                               (now(), journal["id"]))

    def recover(self) -> None:
        directory = self.path(str(Path(".app", "transactions")))
        if not directory.exists():
            return
        for path in directory.glob("*"):
            if path.is_symlink() or path.is_junction():
                raise AppError("unsafe_journal", "A recovery directory is a link. Workspace writes are disabled.", 409)
            journal_path = path / "journal.json"
            if not journal_path.exists():
                continue
            try:
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
                if journal["state"] not in ("committed", "rolled_back"):
                    self._rollback(path, journal)
                elif journal["state"] == "committed":
                    with self.store.connect(write=True) as connection:
                        connection.execute("UPDATE file_transactions SET state='committed',updated_at=? WHERE id=?",
                                           (now(), journal["id"]))
            except (KeyError, json.JSONDecodeError) as exc:
                raise AppError("damaged_journal", "A file-recovery journal is invalid. Restore a backup.", 409) from exc

    def complete_restore(self) -> dict | None:
        marker_path = self.path(str(Path(".app", "restore-pending.json")))
        if not marker_path.exists():
            return
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        journal_path = self.path(str(Path(".app", "transactions", marker["journal_id"], "journal.json")))
        if not journal_path.exists() or json.loads(journal_path.read_text(encoding="utf-8"))["state"] != "committed":
            marker_path.unlink()
            return
        source = self.path(marker["database"])
        if digest(source.read_bytes()) != marker["database_hash"]:
            raise AppError("restore_database_changed", "The staged restore database is damaged. Restore is blocked.", 409)
        import sqlite3
        restored = sqlite3.connect(source)
        try:
            with self.store.connect() as current:
                restored.backup(current)
        finally:
            restored.close()
        with self.store.connect(write=True) as current:
            updated = current.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name='events'",
                                      (marker.get("sequence_floor", 0),))
            if not updated.rowcount:
                current.execute("INSERT INTO sqlite_sequence(name,seq) VALUES ('events',?)",
                                (marker.get("sequence_floor", 0),))
        completion = marker.get("completion", {})
        run_id = completion.get("run_id", "")
        self.store.invalidate_work(run_id, "The workspace was restored. Review current data before starting work again.")
        result = {"restored": True}
        if completion.get("backup"):
            backup = completion["backup"]
            content = self.read(backup["relative_path"])
            if content is None or digest(content) != backup["hash"]:
                raise AppError("safety_backup_changed", "The safety backup changed during restore. Preserve the workspace and its recovery files.", 409)
            result["backup_artifact_id"] = self.store.register_artifact(
                backup["relative_path"], content, backup["media_type"], backup["name"],
            )
        if run_id:
            self.store.enqueue(run_id, "restore", {"restored": True}, completion["policy_hash"])
            self.store.finish(run_id, result)
        marker_path.unlink()
        return result
