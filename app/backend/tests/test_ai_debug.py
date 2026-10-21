from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from ai_job_search.ai_debug import redact, trace_detail, trace_index
from ai_job_search.errors import AppError
from ai_job_search.models import GroundingReview, Review, Settings, VisualReview
from ai_job_search.policy import Policy
from ai_job_search.runtime import AgentRuntime
from ai_job_search.storage import digest, encoded


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["success", "invalid", "provider_error"])
@pytest.mark.parametrize("with_images", [False, True])
async def test_records_actual_prompts_public_messages_and_rejected_results(workspace, mode, with_images):
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )
    from claude_agent_sdk.types import ServerToolResultBlock, ServerToolUseBlock

    secret = "sk-ant-synthetic-secret"
    run_id = str(uuid4())
    policy = Policy(workspace).bundle("apply")
    workspace.store.enqueue(run_id, "documents", {}, policy["hash"])
    stage = "visual_1"
    step_id = workspace.store.stage(run_id, stage, "Checking")
    recorded = {}
    output = {"visual_review": {"passed": True, "findings": []}} if mode == "success" else {"visual_review": {}}

    async def query(*, prompt, options):
        text = prompt if isinstance(prompt, str) else (await anext(prompt))["message"]["content"][0]["text"]
        recorded.update(prompt=text, options=options)
        yield AssistantMessage(content=[
            TextBlock(text=f"Public response {secret}"),
            ThinkingBlock(thinking="private reasoning must not be stored", signature="private-signature"),
            ToolUseBlock(id="search-1", name="WebSearch", input={"query": "Example products"}),
        ], model="test")
        yield UserMessage(content=[ToolResultBlock(tool_use_id="search-1", content="Public search result")])
        yield AssistantMessage(content=[
            ServerToolUseBlock(id="server-search", name="web_search", input={"query": "Example company"}),
            ServerToolResultBlock(tool_use_id="server-search", content={"results": ["Public server search result"]}),
        ], model="test")
        yield UserMessage(content="Public tool status")
        if mode == "provider_error":
            raise RuntimeError(f"Provider error {secret}")
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                            num_turns=1, session_id="test", total_cost_usd=0.01, structured_output=output)

    class Credential:
        def get(self):
            return secret

    runtime = AgentRuntime(workspace.store, Credential())
    with patch("claude_agent_sdk.query", query):
        try:
            result = await runtime.generate(
                run_id, stage, {"draft": "Full candidate body text"},
                policy, VisualReview, Settings(privacy_consent=True),
                workspace.root / ".app" / "runtime-test", trace_id=step_id,
                images=[b"synthetic image"] if with_images else None,
            )
        except AppError as exc:
            assert mode != "success"
            assert exc.code == ("ai_unavailable" if mode == "provider_error" else "ai_invalid_output")
        else:
            assert mode == "success" and result.passed
    detail = trace_detail(workspace, run_id, step_id)
    assert detail["source"] == "captured"
    assert detail["request"]["user_prompt"] == recorded["prompt"]
    assert detail["request"]["system_prompt"] == Path(recorded["options"].system_prompt["path"]).read_text("utf-8")
    assert detail["request"]["output_schema"] == recorded["options"].output_format["schema"]
    assert detail["request"]["images"] == ([{"sha256": digest(b"synthetic image"), "bytes": 15}] if with_images else [])
    assert any(item["type"] == "assistant" for item in detail["responses"])
    assert any(item["type"] == "user" for item in detail["responses"])
    assert "Public server search result" in encoded(detail["responses"])
    assert "Public tool status" in encoded(detail["responses"])
    if mode != "provider_error":
        assert detail["responses"][-1]["structured_output"] == output
    text = encoded(detail)
    assert secret not in text and "[redacted]" in text
    assert "private reasoning must not be stored" not in text and "private-signature" not in text
    assert trace_index(workspace, run_id)[0]["id"] == step_id


def test_legacy_snapshots_are_labelled_and_scoped_to_the_run(api):
    client, app = api
    workspace = app.state.workspace
    run_id, other_run = str(uuid4()), str(uuid4())
    for run in (run_id, other_run):
        workspace.store.enqueue(run, "documents", {}, "test-policy")
    stage = "independent_review_1"
    step_id = workspace.store.stage(run_id, stage, "Reviewing")
    workspace.store.finish_step(step_id, {"revised": {"cv": "saved output"}})
    stage_path = Path(".app", "runs", run_id, "stages", digest(stage)[:16] + ".input.json")
    workspace._durable_write(workspace.path(str(stage_path)), encoded({"data": {"drafts": "full saved text"}}).encode())
    listing = client.get(f"/api/v1/runs/{run_id}/ai-traces")
    assert listing.status_code == 200
    assert listing.json()[0]["source"] == "legacy_snapshot"
    detail = client.get(f"/api/v1/runs/{run_id}/ai-traces/{step_id}").json()
    assert "not a complete wire exchange" in detail["note"]
    assert detail["request"]["stage_input_snapshot"]["data"]["drafts"] == "full saved text"
    assert detail["request"]["system_prompt"] is None
    assert detail["responses"][0]["type"] == "saved_validated_output"
    assert client.get(f"/api/v1/runs/{other_run}/ai-traces/{step_id}").status_code == 404
    assert client.get(f"/api/v1/runs/{uuid4()}/ai-traces").status_code == 404


def test_redaction_does_not_truncate_full_drafts():
    draft = "Candidate body. " * 10000
    result = redact({"draft": draft, "authorization": "Bearer private", "nested": [{"api_key": "private"}]})
    assert result["draft"] == draft
    assert result["authorization"] == result["nested"][0]["api_key"] == "[redacted]"


def test_both_company_claim_schemas_exclude_role_and_candidate_fit():
    for model in (Review, GroundingReview):
        description = model.model_json_schema()["properties"]["company_claims"]["description"]
        assert "application role title" in description and "fit judgments" in description
        assert "Check posting summaries against the supplied posting" in description
