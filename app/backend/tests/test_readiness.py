from __future__ import annotations

import os
from uuid import uuid4

import pytest

from ai_job_search.errors import AppError
from ai_job_search.readiness import readiness, repair

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Guided dependency installation supports Windows.")


def _setup(monkeypatch, tools: dict, missing: list[str]):
    monkeypatch.setattr("ai_job_search.readiness.find_tool", lambda name, resources: tools.get(name))
    monkeypatch.setattr("ai_job_search.readiness.shutil.which", lambda name: None)
    monkeypatch.setattr("ai_job_search.readiness.Credentials.connected", lambda: False)
    monkeypatch.setattr("ai_job_search.readiness.missing_tex_files", lambda resources, refresh=False: list(missing))


def test_missing_template_packages_make_tex_not_ready(workspace, monkeypatch):
    tools = {name: f"C:\\Synthetic Tools\\{name}.exe" for name in ("lualatex", "xelatex", "miktex")}
    _setup(monkeypatch, tools, ["needspace.sty", "t3enc.def"])
    tex = next(item for item in readiness(workspace)["dependencies"] if item["id"] == "tex")
    assert tex["status"] == "setup_required" and tex["can_install"] is True
    assert "needspace" in tex["description"] and "tipa" in tex["description"]


def test_existing_miktex_repair_installs_only_missing_packages_and_verifies(workspace, monkeypatch):
    tools = {name: f"C:\\Synthetic Tools\\{name}.exe" for name in ("lualatex", "xelatex", "miktex")}
    missing = ["needspace.sty", "t3enc.def"]
    _setup(monkeypatch, tools, missing)
    calls = []

    def fake_command(arguments, directory, **kwargs):
        calls.append(arguments[1:])
        if arguments[1:3] == ["packages", "require"]:
            missing.clear()
        return ""

    monkeypatch.setattr("ai_job_search.readiness.command", fake_command)
    run_id = str(uuid4())
    workspace.store.enqueue(run_id, "dependency", {}, "test-policy")
    result = repair(workspace, run_id, "tex")
    assert result["installed"] == "tex" and result["packages_installed"] == ["needspace", "tipa"]
    assert calls == [
        ["packages", "update-package-database"], ["packages", "require", "needspace", "tipa"],
        ["fndb", "refresh"], ["formats", "build", "lualatex"], ["formats", "build", "xelatex"],
    ]


def test_repair_retries_per_package_and_fails_when_packages_stay_missing(workspace, monkeypatch):
    tools = {name: f"C:\\Synthetic Tools\\{name}.exe" for name in ("lualatex", "xelatex", "miktex")}
    missing = ["needspace.sty", "t3enc.def"]
    _setup(monkeypatch, tools, missing)

    def fake_command(arguments, directory, **kwargs):
        if arguments[1:3] == ["packages", "require"]:
            if len(arguments) > 4 or arguments[3] == "tipa":
                raise AppError("tool_failed", "failed", 422, detail={"output": "Sorry, but: network unreachable"})
            missing.remove("needspace.sty")
        return ""

    monkeypatch.setattr("ai_job_search.readiness.command", fake_command)
    run_id = str(uuid4())
    workspace.store.enqueue(run_id, "dependency", {}, "test-policy")
    with pytest.raises(AppError) as caught:
        repair(workspace, run_id, "tex")
    assert caught.value.code == "tex_missing_package" and "tipa" in caught.value.message
    failure = caught.value.detail["ai_failure"]
    assert failure["packages"] == ["tipa"] and "network unreachable" in failure["install_errors"]["tipa"]


def test_legacy_mpm_installs_each_missing_package(workspace, monkeypatch):
    tools = {name: f"C:\\Synthetic Tools\\{name}.exe" for name in ("lualatex", "xelatex", "mpm", "initexmf")}
    missing = ["cite.sty"]
    _setup(monkeypatch, tools, missing)
    calls = []

    def fake_command(arguments, directory, **kwargs):
        calls.append(arguments)
        if arguments[1].startswith("--install="):
            missing.clear()
        return ""

    monkeypatch.setattr("ai_job_search.readiness.command", fake_command)
    run_id = str(uuid4())
    workspace.store.enqueue(run_id, "dependency", {}, "test-policy")
    repair(workspace, run_id, "tex")
    assert calls == [[tools["mpm"], "--update-db"], [tools["mpm"], "--install=cite"],
                     [tools["initexmf"], "--update-fndb"]]


def test_missing_compilers_without_winget_need_explicit_guided_setup(workspace, monkeypatch):
    monkeypatch.setattr("ai_job_search.readiness.find_tool", lambda name, resources: None)
    monkeypatch.setattr("ai_job_search.readiness.shutil.which", lambda name: None)
    monkeypatch.setattr("ai_job_search.readiness.Credentials.connected", lambda: False)
    assert not next(item for item in readiness(workspace)["dependencies"] if item["id"] == "tex")["can_install"]
    with pytest.raises(AppError) as caught:
        repair(workspace, str(uuid4()), "tex")
    assert caught.value.code == "guided_install_required"
