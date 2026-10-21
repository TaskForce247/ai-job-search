from __future__ import annotations

import json
import re
from pathlib import Path

from .errors import AppError
from .storage import digest
from .workspace import Workspace


def redact(value: object, secret: str = "") -> object:
    if isinstance(value, str):
        text = value.replace(secret, "[redacted]") if secret else value
        return re.sub(r"sk-ant-[A-Za-z0-9_\-]+", "[redacted]", text)
    if isinstance(value, dict):
        return {
            key: "[redacted]" if str(key).casefold() in {
                "api_key", "anthropic_api_key", "authorization", "access_token", "refresh_token",
            } else redact(item, secret)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, secret) for item in value]
    return value


def trace_index(workspace: Workspace, run_id: str) -> list[dict]:
    workspace.store.run(run_id)
    with workspace.store.connect() as connection:
        traces = [dict(row) for row in connection.execute(
            "SELECT id,stage,created_at FROM ai_traces WHERE run_id=? ORDER BY created_at,id", (run_id,),
        )]
        steps = [dict(row) for row in connection.execute(
            "SELECT id,stage,status,started_at FROM steps WHERE run_id=? ORDER BY started_at,id",
            (run_id,),
        )]
    by_id = {step["id"]: step for step in steps}
    result = [{
        **trace, "status": by_id.get(trace["id"], {}).get("status", "recorded"), "source": "captured",
    } for trace in traces]
    captured = {trace["id"] for trace in traces}
    for step in steps:
        path = str(Path(".app", "runs", run_id, "stages", digest(step["stage"])[:16] + ".input.json"))
        if step["id"] not in captured and workspace.read(path) is not None:
            result.append({
                "id": step["id"], "stage": step["stage"], "created_at": step["started_at"],
                "status": step["status"], "source": "legacy_snapshot",
            })
    return sorted(result, key=lambda item: (item["created_at"], item["id"]))


def trace_detail(workspace: Workspace, run_id: str, trace_id: str) -> dict:
    item = next((item for item in trace_index(workspace, run_id) if item["id"] == trace_id), None)
    if item is None:
        raise AppError("not_found", "AI exchange not found for this operation.", 404)
    with workspace.store.connect() as connection:
        trace = connection.execute(
            "SELECT request_json,response_json FROM ai_traces WHERE id=? AND run_id=?", (trace_id, run_id),
        ).fetchone()
        step = connection.execute("SELECT output_json FROM steps WHERE id=? AND run_id=?", (trace_id, run_id)).fetchone()
    if trace:
        return {**item, "request": redact(json.loads(trace["request_json"])),
                "responses": redact(json.loads(trace["response_json"])),
                "note": "Application-supplied prompts and public SDK responses. SDK-added instructions and private reasoning are not recorded. Image attachments are represented by hashes, not pixels."}
    stage_hash = digest(item["stage"])[:16]
    root = Path(".app", "runs", run_id)
    inputs = json.loads(workspace.text(str(root / "stages" / (stage_hash + ".input.json"))))
    system = workspace.read(str(root / ("runtime-" + stage_hash) / "system-prompt.txt"))
    return {
        **item, "request": redact({
            "system_prompt": system.decode("utf-8") if system else None, "stage_input_snapshot": inputs,
        }), "responses": redact([{"type": "saved_validated_output", "output": json.loads(step["output_json"])}]
                               if step and step["output_json"] else []),
        "note": "Legacy snapshot, not a complete wire exchange. Raw replies, tool messages and the output schema were not recorded. A repeated stage may share its latest input snapshot. Reused checkpoints did not send a new request.",
    }
