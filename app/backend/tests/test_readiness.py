from __future__ import annotations

import os
from uuid import uuid4

import pytest

from ai_job_search.errors import AppError
from ai_job_search.readiness import TEX_PACKAGES, readiness, repair

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Guided dependency installation supports Windows.")


def test_existing_miktex_can_repair_packages_without_winget(workspace, monkeypatch):
    tools = {name: f"C:\\Synthetic Tools\\{name}.exe" for name in ("lualatex", "xelatex", "mpm")}
    monkeypatch.setattr("ai_job_search.readiness.find_tool", lambda name, resources: tools.get(name))
    monkeypatch.setattr("ai_job_search.readiness.shutil.which", lambda name: None)
    monkeypatch.setattr("ai_job_search.readiness.Credentials.connected", lambda: False)
    calls = []
    monkeypatch.setattr("ai_job_search.readiness.command", lambda arguments, directory, **kwargs: calls.append(arguments))
    status = {dependency["id"]: dependency for dependency in readiness(workspace)["dependencies"]}
    assert status["tex"]["can_install"] is True
    assert status["pdf"]["can_install"] is False
    run_id = str(uuid4())
    workspace.store.enqueue(run_id, "dependency", {}, "test-policy")
    assert repair(workspace, run_id, "tex")["installed"] == "tex"
    assert calls == [
        [tools["mpm"], "--update-db"],
        [tools["mpm"], *("--install=" + package for package in TEX_PACKAGES)],
    ]


def test_missing_compilers_without_winget_need_explicit_guided_setup(workspace, monkeypatch):
    monkeypatch.setattr("ai_job_search.readiness.find_tool", lambda name, resources: None)
    monkeypatch.setattr("ai_job_search.readiness.shutil.which", lambda name: None)
    monkeypatch.setattr("ai_job_search.readiness.Credentials.connected", lambda: False)
    assert not next(item for item in readiness(workspace)["dependencies"] if item["id"] == "tex")["can_install"]
    with pytest.raises(AppError) as caught:
        repair(workspace, str(uuid4()), "tex")
    assert caught.value.code == "guided_install_required"
