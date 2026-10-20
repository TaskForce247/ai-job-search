"""Test-only real FastAPI server. Never imported by the production application.

HTTP, sessions, storage, approvals and workflows are real. Only credentials,
AI transport and document compilation/checks are deterministic fixtures.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import io
import json
import os
import shutil
import time
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import uvicorn
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from ai_job_search.api import create_app
from ai_job_search.config import Config
from ai_job_search.documents import DocumentService, render_cover, render_cv
from ai_job_search.network import WebClient
from ai_job_search.policy import REFERENCES
from ai_job_search.portals import REVIEWED
from ai_job_search.runtime import AgentRuntime, Credentials
from ai_job_search.server import available_socket
from ai_job_search.workflows import WorkflowService

FRONTEND = Path(__file__).resolve().parents[1]
REPOSITORY = FRONTEND.parents[1]
FIXTURE_KEY = "sk-ant-integration-fixture-not-a-real-key"
DISCLAIMER = "Integration-fixture checks only; no real LaTeX compilation or live Claude verification."


def write_json(path: Path, value: object) -> None:
    staged = path.with_suffix(".pending")
    staged.write_text(json.dumps(value, indent=2), encoding="utf-8")
    staged.replace(path)


def resource_bundle(directory: Path) -> Path:
    """Copy specifications/tools, never the repository's candidate-owned files."""
    resources = directory / "resources"

    def write(relative: Path, text: str) -> None:
        target = resources / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def copy(relative: Path) -> None:
        target = resources / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPOSITORY / relative, target)

    methodology = Path(".claude", "skills", "job-application-assistant")
    for name in REFERENCES:
        if name.startswith(("02-", "03-")):
            heading = "Personal preferences" if name.startswith("02-") else "Personal writing preferences"
            write(methodology / name, f"# Synthetic integration preferences\n\n## {heading}\n\n")
        else:
            copy(methodology / name)
    write(methodology / "01-candidate-profile.md", "# Candidate Profile\n\n## Identity\n\n")
    for name in ("setup", "rank", "apply", "outcome"):
        copy(Path(".claude", "commands", name + ".md"))
    copy(Path(".claude", "skills", "job-scraper", "SKILL.md"))
    copy(Path("documents", "README.md"))
    for portal in REVIEWED:
        copy(Path(".agents", "skills", portal, "SKILL.md"))
    for name in ("job_key", "rank_state", "verify_pdf", "verify_layout", "robots_check"):
        copy(Path("tools", name + ".py"))
    write(Path("CLAUDE.md"), "# Synthetic integration workspace\n\n## Candidate Profile\n\n")
    write(Path("AGENTS.md"), "# Synthetic integration workspace\n\nRead CLAUDE.md.\n")
    write(Path("cv", "main_example.tex"), (
        "\\documentclass[11pt,a4paper,sans]{moderncv}\n\\moderncvstyle{banking}\n"
        "% personal data\n\\name{[YOUR_NAME]}{}\n"
        "\\begin{document}\n\\makecvtitle\n\\end{document}\n"
    ))
    write(Path("cover_letters", "cover_example.tex"), "% Synthetic integration fixture.\n")
    copy(Path("cover_letters", "cover.cls"))
    shutil.copytree(REPOSITORY / "cover_letters" / "OpenFonts", resources / "cover_letters" / "OpenFonts")
    shutil.copytree(FRONTEND / "dist", resources / "app" / "frontend" / "dist")
    return resources


def fixture_runtime_class():
    specification = importlib.util.spec_from_file_location(
        "frontend_backend_integration_fixtures", REPOSITORY / "app" / "backend" / "tests" / "conftest.py",
    )
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)

    class IntegrationRuntime(module.FixtureRuntime):
        async def generate(self, run_id, stage, data, policy, schema, limits, directory, **kwargs):
            assert limits.privacy_consent, "The UI must record explicit AI consent."
            assert Credentials.connected(), "The UI must configure the synthetic API connection."
            return await super().generate(run_id, stage, data, policy, schema, limits, directory, **kwargs)

    return IntegrationRuntime


class FixtureChecks(list):
    def append(self, check):
        # The workflow appends visual/grounding labels; distinguish injected checks in the actual UI.
        super().append({
            **check, "label": "Integration-fixture " + check["label"].lower(),
            "message": DISCLAIMER,
        })


def compile_fixture(self, kind, profile, content, company, directory):
    directory.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    lines = [
        "INTEGRATION FIXTURE - NOT A REAL LATEX OR LIVE AI CHECK",
        profile.name, profile.email, profile.phone, company,
    ]
    for block in content.blocks:
        lines.extend(value for value in (block.heading, block.text, block.organization) if value)
        lines.extend(bullet.text for bullet in block.bullets)
    for number in range(2 if kind == "cv" else 1):
        page = writer.add_blank_page(595, 842)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        commands = ["BT /F1 11 Tf 40 790 Td 17 TL"]
        for line in [f"Integration fixture {kind} - page {number + 1}", *lines]:
            escaped = line.encode("ascii", errors="replace").decode().replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({escaped[:92]}) Tj T*")
        commands.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = directory / (kind + ".pdf")
    with io.BytesIO() as buffer:
        writer.write(buffer)
        output.write_bytes(buffer.getvalue())
    source = render_cv(profile, content, self.resources) if kind == "cv" else render_cover(profile, content, company)
    (directory / (kind + ".tex")).write_text("% " + DISCLAIMER + "\n" + source, encoding="utf-8")
    return output


def verify_fixture(self, kind, pdf, profile, requirements):
    reader = PdfReader(pdf)
    assert len(reader.pages) == (2 if kind == "cv" else 1)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert all(value in text for value in (profile.name, profile.email, profile.phone))
    return FixtureChecks([{
        "id": "integration_fixture_pdf", "label": "Integration-fixture PDF page count and text",
        "status": "pass", "message": DISCLAIMER + " Real PDF bytes and embedded synthetic contact text.",
    }]), text, []


def forbidden(*args, **kwargs):
    raise AssertionError("This integration fixture cannot contact live providers or external websites.")


async def forbidden_ai(*args, **kwargs):
    forbidden()


async def serve(directory: Path, port: int) -> None:
    resources = resource_bundle(directory)
    memory = {"key": None}

    def set_key(value):
        assert value == FIXTURE_KEY, "Only the synthetic integration key is accepted."
        memory["key"] = value

    with ExitStack() as patches:
        patches.enter_context(patch.object(Credentials, "get", staticmethod(lambda: memory["key"])))
        patches.enter_context(patch.object(Credentials, "set", staticmethod(set_key)))
        patches.enter_context(patch.object(AgentRuntime, "generate", forbidden_ai))
        patches.enter_context(patch.object(WebClient, "fetch", forbidden))
        patches.enter_context(patch.object(DocumentService, "compile", compile_fixture))
        patches.enter_context(patch.object(DocumentService, "verify", verify_fixture))
        patches.enter_context(patch.object(DocumentService, "render_pages", staticmethod(lambda path: [
            base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="),
        ])))
        listener = patches.enter_context(available_socket(port))
        port = listener.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        app = create_app(Config(
            workspace=directory / "Synthetic career workspace é", resources=resources,
            origin=origin, start_worker=False,
        ))
        workspace = app.state.workspace
        runtime = fixture_runtime_class()()
        service = WorkflowService(workspace, runtime)
        claimed = []
        original_lifespan = app.router.lifespan_context
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                              timeout_graceful_shutdown=3))

        def report():
            with workspace.store.connect() as connection:
                events = [dict(row) for row in connection.execute("SELECT run_id,type,stage FROM events ORDER BY sequence")]
                rows = [dict(row) for row in connection.execute("SELECT id,kind,status,attempt,error,error_code FROM runs ORDER BY created_at")]
                approvals = [dict(row) for row in connection.execute("SELECT run_id,kind,status FROM approvals")]
            write_json(directory / "fixture-report.json", {
                "disclaimer": DISCLAIMER, "claims": claimed, "runs": rows, "events": events, "approvals": approvals,
                "ai_calls": [{"run_id": item["run_id"], "stage": item["stage"], "schema": item["schema"].__name__}
                             for item in runtime.calls],
            })

        async def consume():
            while True:
                run = await asyncio.to_thread(workspace.store.claim, "frontend-integration-fixture")
                if run:
                    claimed.append({"id": run["id"], "owner": run["owner"], "attempt": run["attempt"]})
                    await service.execute(run["id"])
                    report()
                else:
                    await asyncio.sleep(0.1)

        async def supervise():
            deadline = time.monotonic() + 150
            while not (directory / "shutdown").exists() and time.monotonic() < deadline:
                await asyncio.to_thread(workspace.store.heartbeat, "frontend-integration-fixture", os.getpid())
                await asyncio.sleep(0.25)
            app.state.stopping = True
            server.should_exit = True

        @asynccontextmanager
        async def lifespan(application):
            async with original_lifespan(application):
                consumer = asyncio.create_task(consume())
                supervisor = asyncio.create_task(supervise())
                try:
                    yield
                finally:
                    consumer.cancel()
                    supervisor.cancel()
                    await asyncio.gather(consumer, supervisor, return_exceptions=True)
                    report()

        app.router.lifespan_context = lifespan
        write_json(directory / "launch.json", {
            "url": origin + "/#token=" + app.state.sessions.mint(), "origin": origin,
            "workspace_path": str(workspace.root), "data_directory": str(directory), "disclaimer": DISCLAIMER,
        })
        await server.serve(sockets=[listener])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8876)
    args = parser.parse_args()
    directory = args.data_dir.resolve()
    if not directory.name.startswith("frontend-real-api-") or directory.exists():
        parser.error("Use a new, uniquely named frontend-real-api- directory for synthetic integration data.")
    if args.port != 0 and not 1024 <= args.port <= 65535:
        parser.error("Use port zero or an unprivileged loopback port.")
    if not (FRONTEND / "dist" / "index.html").is_file():
        parser.error("Build the frontend before starting the integration fixture.")
    directory.mkdir(exist_ok=False)
    asyncio.run(serve(directory, args.port))


if __name__ == "__main__":
    main()
