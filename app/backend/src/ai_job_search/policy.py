from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from .errors import AppError
from .storage import digest
from .workspace import Workspace

METHODOLOGY = Path(".claude", "skills", "job-application-assistant")
COMMANDS = {
    "setup": ["setup"],
    "scrape": [],
    "rank": ["rank"],
    "apply": ["apply"],
    "outcome": ["outcome"],
}
REFERENCES = [
    "02-behavioral-profile.md", "03-writing-style.md", "04-job-evaluation.md",
    "05-cv-templates.md", "06-cover-letter-templates.md", "09-web-research.md",
    "10-local-app-transport.md",
]


def load_helper(resources: Path, name: str):
    specification = importlib.util.spec_from_file_location(f"ai_job_search_helper_{name}", resources / "tools" / f"{name}.py")
    if not specification or not specification.loader:
        raise AppError("missing_framework_tool", f"The bundled {name} helper is missing.", 503)
    import sys
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class Policy:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def bundle(self, kind: str) -> dict:
        with self.workspace.lock():
            return self._bundle_snapshot(kind)

    def _bundle_snapshot(self, kind: str) -> dict:
        paths = [str(Path(".claude", "commands", name + ".md")) for name in COMMANDS.get(kind, ["apply"])]
        if kind == "scrape":
            paths.append(str(Path(".claude", "skills", "job-scraper", "SKILL.md")))
        paths += [str(METHODOLOGY / name) for name in REFERENCES]
        paths += [str(Path("documents", "README.md"))]
        expected = self.workspace.snapshot(paths)
        documents = {}
        versions = {}
        for path in paths:
            content = self.workspace.text(path)
            if not content:
                raise AppError("missing_policy", f"The canonical framework file {path} is missing.", 409)
            documents[path] = content
            match = re.search(r"^framework_version:\s*(.+)$", content, re.M)
            if match:
                versions[path] = match.group(1).strip()
        self.workspace.unchanged(expected)
        return {"hash": digest(documents), "documents": documents, "versions": versions}

    def score(self, scores: dict[str, float]) -> tuple[int, str]:
        text = self.workspace.text(str(METHODOLOGY / "04-job-evaluation.md"))
        helper = load_helper(self.workspace.resources, "rank_state")
        weighting = text.split("## Weighting", 1)[-1].split("## ", 1)[0]
        labels = {"technical": "Technical Skills", "experience": "Experience Match",
                  "behavioral": "Behavioral Fit", "career": "Career Alignment"}
        for name, label in labels.items():
            match = re.search(rf"{re.escape(label)}:\s*(\d+(?:\.\d+)?)%", weighting)
            if not match or abs(float(match.group(1)) / 100 - helper.WEIGHTS[name]) > 0.00001:
                raise AppError("unsupported_rubric", "The personalized score weights need an adapter update. No score was calculated.", 409)
        thresholds = text.split("## Thresholds", 1)[-1].split("## ", 1)[0]
        for minimum, label in helper.BANDS[:-1]:
            if not re.search(rf"\*\*{re.escape(label)}\*\*\s*\({minimum}(?:\+|-)", thresholds):
                raise AppError("unsupported_rubric", "The personalized score thresholds need an adapter update.", 409)
        score = helper.overall_score(scores)
        verdict = next(label for minimum, label in helper.BANDS if score >= minimum)
        return score, verdict
