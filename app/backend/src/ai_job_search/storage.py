from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal
from uuid import uuid4

from .errors import AppError
from .models import Settings


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: bytes | str | object) -> str:
    if not isinstance(value, (str, bytes)):
        value = encoded(value)
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
 stage TEXT NOT NULL DEFAULT 'queued', message TEXT NOT NULL DEFAULT '',
 input_json TEXT NOT NULL, result_json TEXT, policy_hash TEXT NOT NULL,
 owner TEXT, lease_until REAL, attempt INTEGER NOT NULL DEFAULT 0,
 cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT, error_code TEXT,
 cost_usd REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_queue ON runs(status,created_at);
CREATE TABLE IF NOT EXISTS steps (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
 stage TEXT NOT NULL, attempt INTEGER NOT NULL, status TEXT NOT NULL,
 input_hash TEXT NOT NULL, output_json TEXT, started_at TEXT NOT NULL, completed_at TEXT,
 UNIQUE(run_id,stage,attempt)
);
CREATE TABLE IF NOT EXISTS events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
 type TEXT NOT NULL, stage TEXT NOT NULL, payload TEXT NOT NULL, occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id,sequence);
CREATE TABLE IF NOT EXISTS approvals (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), kind TEXT NOT NULL,
 title TEXT NOT NULL, revision TEXT NOT NULL, input_hash TEXT NOT NULL,
 policy_hash TEXT NOT NULL, proposal_json TEXT NOT NULL, choices_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', decision TEXT, decided_at TEXT, created_at TEXT NOT NULL,
 UNIQUE(run_id,kind)
);
CREATE TABLE IF NOT EXISTS artifacts (
 id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id), application_id TEXT,
 revision TEXT NOT NULL, relative_path TEXT NOT NULL, hash TEXT NOT NULL,
 media_type TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(relative_path,hash)
);
CREATE TABLE IF NOT EXISTS file_transactions (
 id TEXT PRIMARY KEY, state TEXT NOT NULL, journal_path TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS record_index (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, locator TEXT NOT NULL,
 source_hash TEXT NOT NULL, projection_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
 key TEXT PRIMARY KEY, value_json TEXT NOT NULL, revision TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency (
 key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, status INTEGER NOT NULL,
 response_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS worker (
 id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, pid INTEGER NOT NULL,
 heartbeat REAL NOT NULL
);
"""

EVENT_TYPES = {
    "queued": "run.queued", "started": "run.started", "stage": "stage.started",
    "succeeded": "run.completed", "failed": "run.failed", "cancelled": "run.cancelled",
    "interrupted": "run.interrupted", "approval_required": "approval.required",
    "download": "stage.progress", "install": "stage.progress", "portal_results": "stage.progress",
    "portal_failed": "warning",
}


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise AppError("newer_database", "This workspace needs a newer application version.", 409)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            connection.execute("PRAGMA user_version=1")
            connection.commit()

    @contextmanager
    def connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def setting(self, key: str, default: object) -> tuple[object, str]:
        with self.connect() as connection:
            row = connection.execute("SELECT value_json,revision FROM settings WHERE key=?", (key,)).fetchone()
        return (json.loads(row["value_json"]), row["revision"]) if row else (default, digest(default))

    def save_setting(self, key: str, value: object, expected: str | None = None) -> str:
        with self.connect(write=True) as connection:
            row = connection.execute("SELECT revision FROM settings WHERE key=?", (key,)).fetchone()
            actual = row["revision"] if row else digest(Settings().model_dump() if key == "preferences" else {})
            if expected is not None and expected != actual:
                raise AppError("revision_conflict", "Settings changed. Reload before saving.", 409)
            revision = digest(value)
            connection.execute(
                "INSERT INTO settings VALUES (?,?,?) ON CONFLICT(key) "
                "DO UPDATE SET value_json=excluded.value_json,revision=excluded.revision",
                (key, encoded(value), revision),
            )
        return revision

    def settings(self) -> tuple[Settings, str]:
        value, revision = self.setting("preferences", Settings().model_dump())
        return Settings.model_validate(value), revision

    def enqueue(self, run_id: str, kind: str, inputs: object, policy_hash: str) -> str:
        with self.connect(write=True) as connection:
            existing = connection.execute("SELECT kind,input_json FROM runs WHERE id=?", (run_id,)).fetchone()
            if existing:
                if existing["kind"] != kind or existing["input_json"] != encoded(inputs):
                    raise AppError("idempotency_conflict", "This operation key was used for different inputs.", 409)
                return run_id
            stamp = now()
            connection.execute(
                "INSERT INTO runs(id,kind,input_json,policy_hash,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (run_id, kind, encoded(inputs), policy_hash, stamp, stamp),
            )
            self._event(connection, run_id, "queued", "queued", {"message": "Waiting to start."})
        return run_id

    @staticmethod
    def _event(connection: sqlite3.Connection, run_id: str, event: str, stage: str, payload: object) -> None:
        connection.execute(
            "INSERT INTO events(run_id,type,stage,payload,occurred_at) VALUES (?,?,?,?,?)",
            (run_id, EVENT_TYPES.get(event, event), stage, encoded(payload), now()),
        )

    def event(self, run_id: str, event: str, payload: object, stage: str | None = None) -> None:
        with self.connect(write=True) as connection:
            run = connection.execute("SELECT stage FROM runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                raise AppError("not_found", "Operation not found.", 404)
            self._event(connection, run_id, event, stage or run["stage"], payload)

    def last_sequence(self) -> int:
        with self.connect() as connection:
            return connection.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]

    def invalidate_work(self, except_run_id: str, reason: str, *, profile_only: bool = False) -> None:
        with self.connect(write=True) as connection:
            query = "SELECT id,stage FROM runs WHERE id<>? AND status IN ('queued','running','awaiting_user')"
            if profile_only:
                query += " AND status<>'running' AND kind IN ('evaluate','documents','profile_change','extract_profile')"
            for row in connection.execute(query, (except_run_id,)).fetchall():
                connection.execute(
                    "UPDATE runs SET status='interrupted',error=?,error_code='workspace_changed',"
                    "owner=NULL,lease_until=NULL,updated_at=? WHERE id=?", (reason, now(), row["id"]),
                )
                connection.execute("UPDATE approvals SET status='invalidated' WHERE run_id=? AND status='pending'", (row["id"],))
                connection.execute("UPDATE steps SET status='interrupted',completed_at=? WHERE run_id=? AND status='running'",
                                   (now(), row["id"]))
                self._event(connection, row["id"], "interrupted", row["stage"], {"message": reason})

    def claim(self, owner: str) -> dict | None:
        with self.connect(write=True) as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE status='queued' AND cancel_requested=0 ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not run:
                return None
            connection.execute(
                "UPDATE runs SET status='running',owner=?,lease_until=?,attempt=attempt+1,updated_at=? WHERE id=?",
                (owner, time.time() + 30, now(), run["id"]),
            )
            self._event(connection, run["id"], "started", "starting", {"message": "Starting operation."})
            return dict(connection.execute("SELECT * FROM runs WHERE id=?", (run["id"],)).fetchone())

    def heartbeat(self, owner: str, pid: int) -> None:
        with self.connect(write=True) as connection:
            stamp = time.time()
            connection.execute(
                "INSERT INTO worker VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "owner=excluded.owner,pid=excluded.pid,heartbeat=excluded.heartbeat",
                (owner, pid, stamp),
            )
            connection.execute("UPDATE runs SET lease_until=? WHERE owner=? AND status='running'",
                               (stamp + 30, owner))

    def recover_runs(self, *, all_running: bool = False) -> None:
        with self.connect(write=True) as connection:
            query = "SELECT id FROM runs WHERE status='running'"
            parameters: tuple = ()
            if not all_running:
                query += " AND lease_until<?"
                parameters = (time.time(),)
            rows = connection.execute(query, parameters).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE runs SET status='interrupted',owner=NULL,lease_until=NULL,updated_at=?,"
                    "error='Work stopped before completion. Review saved progress, then retry.',"
                    "error_code='worker_interrupted' WHERE id=?", (now(), row["id"]),
                )
                connection.execute("UPDATE steps SET status='interrupted',completed_at=? WHERE run_id=? AND status='running'",
                                   (now(), row["id"]))
                self._event(connection, row["id"], "interrupted", "recovery", {"retry_available": True})

    def stage(self, run_id: str, stage: str, message: str, inputs: object = None) -> str:
        step_id = str(uuid4())
        with self.connect(write=True) as connection:
            run = connection.execute("SELECT attempt,cancel_requested FROM runs WHERE id=?", (run_id,)).fetchone()
            if run["cancel_requested"]:
                raise AppError("cancelled", "Operation cancelled.", 409)
            connection.execute("UPDATE runs SET stage=?,message=?,updated_at=? WHERE id=?",
                               (stage, message, now(), run_id))
            previous = connection.execute(
                "SELECT COUNT(*) FROM steps WHERE run_id=? AND stage=?", (run_id, stage),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO steps(id,run_id,stage,attempt,status,input_hash,started_at) VALUES (?,?,?,?,?,?,?)",
                (step_id, run_id, stage, previous + 1, "running", digest(inputs), now()),
            )
            self._event(connection, run_id, "stage", stage, {"message": message})
        return step_id

    def finish_step(self, step_id: str, output: object, *, status: Literal["succeeded", "failed"] = "succeeded") -> None:
        with self.connect(write=True) as connection:
            step = connection.execute("SELECT run_id,stage FROM steps WHERE id=?", (step_id,)).fetchone()
            if not step:
                raise AppError("not_found", "Operation stage not found.", 404)
            connection.execute("UPDATE steps SET status=?,output_json=?,completed_at=? WHERE id=?",
                               (status, encoded(output), now(), step_id))
            self._event(connection, step["run_id"], "stage.completed" if status == "succeeded" else "stage.failed",
                        step["stage"], {"step_id": step_id})

    def finish(self, run_id: str, result: object = None, *, status: str = "succeeded",
               error: str | None = None, code: str | None = None) -> None:
        with self.connect(write=True) as connection:
            connection.execute(
                "UPDATE runs SET status=?,result_json=?,error=?,error_code=?,owner=NULL,lease_until=NULL,"
                "updated_at=? WHERE id=?", (status, encoded(result), error, code, now(), run_id),
            )
            row = connection.execute("SELECT stage FROM runs WHERE id=?", (run_id,)).fetchone()
            if status in ("failed", "cancelled", "interrupted"):
                connection.execute("UPDATE steps SET status=?,completed_at=? WHERE run_id=? AND status='running'",
                                   (status, now(), run_id))
            self._event(connection, run_id, status, row["stage"], {"message": error, "result": result})

    def run(self, run_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise AppError("not_found", "Operation not found.", 404)
            result = dict(row)
            result["input"] = json.loads(result.pop("input_json"))
            result["result"] = json.loads(result.pop("result_json") or "null")
            result["approvals"] = [
                self.approval_dict(item) for item in connection.execute(
                    "SELECT * FROM approvals WHERE run_id=? ORDER BY created_at", (run_id,),
                )
            ]
            result["artifacts"] = [
                dict(item) for item in connection.execute("SELECT * FROM artifacts WHERE run_id=?", (run_id,))
            ]
            return result

    def create_approval(self, run_id: str, kind: str, title: str, proposal: dict,
                        inputs: object, policy_hash: str, choices: list[str]) -> str:
        approval_id = str(uuid4())
        revision = digest({"inputs": inputs, "proposal": proposal, "policy": policy_hash})
        with self.connect(write=True) as connection:
            existing = connection.execute(
                "SELECT id FROM approvals WHERE run_id=? AND kind=?", (run_id, kind),
            ).fetchone()
            if existing:
                return existing["id"]
            connection.execute(
                "INSERT INTO approvals(id,run_id,kind,title,revision,input_hash,policy_hash,proposal_json,"
                "choices_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (approval_id, run_id, kind, title, revision, digest(inputs), policy_hash,
                 encoded(proposal), encoded(choices), now()),
            )
            connection.execute("UPDATE runs SET status='awaiting_user',result_json=?,updated_at=? WHERE id=?",
                               (encoded(proposal), now(), run_id))
            connection.execute("UPDATE steps SET status='succeeded',completed_at=? WHERE run_id=? AND status='running'",
                               (now(), run_id))
            self._event(connection, run_id, "approval_required", kind, {"approval_id": approval_id})
        return approval_id

    @staticmethod
    def approval_dict(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["proposal"] = json.loads(result.pop("proposal_json"))
        result["choices"] = json.loads(result.pop("choices_json"))
        return result

    def approval(self, approval_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not row:
            raise AppError("not_found", "Decision not found.", 404)
        return self.approval_dict(row)

    def invalidate_approval(self, approval_id: str) -> None:
        with self.connect(write=True) as connection:
            connection.execute("UPDATE approvals SET status='invalidated' WHERE id=? AND status='pending'",
                               (approval_id,))

    def record_decision(self, approval_id: str, decision: str, revision: str) -> None:
        with self.connect(write=True) as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if not row or row["revision"] != revision:
                raise AppError("revision_conflict", "This decision changed. Reload it before continuing.", 409)
            if row["status"] in ("approved", "rejected") and row["decision"] == decision:
                return
            if row["status"] != "pending":
                raise AppError("stale_approval", "This decision is no longer current. Run the operation again.", 409)
            if decision not in json.loads(row["choices_json"]):
                raise AppError("invalid_decision", "Choose one of the displayed actions.")
            status = "rejected" if decision in ("reject", "not_now") else "approved"
            connection.execute("UPDATE approvals SET status=?,decision=?,decided_at=? WHERE id=?",
                               (status, decision, now(), approval_id))
            self._event(connection, row["run_id"], "decision", row["kind"], {"decision": decision})

    def register_artifact(self, relative: str, content: bytes, media_type: str, name: str, *,
                          run_id: str | None = None, application_id: str | None = None,
                          revision: str = "") -> str:
        checksum = digest(content)
        with self.connect(write=True) as connection:
            row = connection.execute("SELECT id FROM artifacts WHERE relative_path=? AND hash=?",
                                     (relative, checksum)).fetchone()
            if row:
                return row["id"]
            artifact_id = str(uuid4())
            connection.execute(
                "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?)",
                (artifact_id, run_id, application_id, revision or checksum, relative,
                 checksum, media_type, name, now()),
            )
            if run_id:
                self._event(connection, run_id, "artifact.created", "artifact", {"artifact_id": artifact_id, "name": name})
            return artifact_id

    def artifact(self, artifact_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if not row:
            raise AppError("not_found", "File not found.", 404)
        return dict(row)

    def backup(self, destination: Path) -> None:
        with self.connect() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
