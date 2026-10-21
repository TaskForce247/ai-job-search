from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path
from uuid import uuid4

from . import __version__
from .config import data_home
from .errors import AppError
from .processes import command
from .runtime import Credentials, _failure
from .tex import TEX_PACKAGES, clear_cache, missing_tex_files, package_for
from .workspace import Workspace

BUN_VERSION = "1.3.14"
BUN_ARCHIVE = "bun-windows-x64-baseline.zip"
BUN_SHA256 = "538f9c846355d9e847b2671bc00c47da4229a0befb24df3282b739770f3b475f"
WINDOWS_PACKAGES = {"tex": ("MiKTeX.MiKTeX", "25.12"), "pdf": ("oschwartz10612.Poppler", "25.07.0-0")}
__all__ = ["TEX_PACKAGES", "find_tool", "readiness", "repair"]


def tool_directories(resources: Path) -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    appdata = Path(os.environ.get("APPDATA", local))
    return [
        resources / "runtimes" / "bin",
        data_home() / "runtimes" / "bun",
        data_home() / "runtimes" / "poppler" / "Library" / "bin",
        local / "Microsoft" / "WinGet" / "Links",
        local / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64",
        appdata / "MiKTeX" / "miktex" / "bin" / "x64",
    ]


def find_tool(name: str, resources: Path) -> str | None:
    for directory in tool_directories(resources):
        candidate = directory / (name + ".exe" if os.name == "nt" else name)
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def readiness(workspace: Workspace) -> dict:
    import claude_agent_sdk

    from .portals import PortalService
    settings, _ = workspace.store.settings()
    runtime_path = Path(claude_agent_sdk.__file__).parent / "_bundled"
    sdk_ready = runtime_path.is_dir() and any(path.is_file() and path.name.startswith("claude") for path in runtime_path.iterdir())
    tex_ready = all(find_tool(name, workspace.resources) for name in ("lualatex", "xelatex"))
    missing_tex = missing_tex_files(workspace.resources) if tex_ready else None
    pdf_ready = all(find_tool(name, workspace.resources) for name in ("pdftotext", "pdfinfo"))
    winget_ready = os.name == "nt" and bool(shutil.which("winget"))
    tex_repair_ready = os.name == "nt" and tex_ready and bool(tex_manager(workspace.resources))
    portals_ready = any(PortalService(workspace).executable(portal) for portal in ("freehire-search", "linkedin-search"))
    connected = Credentials.connected()
    with workspace.store.connect() as connection:
        active = connection.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','running','awaiting_user')").fetchone()[0]
    dependencies = [
        {"id": "runtime", "label": "Local application runtime", "status": "ready", "description": "Backend and offline workspace access are available.", "can_install": False},
        {"id": "ai", "label": "Claude API connection", "status": "ready" if connected and sdk_ready else "setup_required",
         "description": "API key stored in your OS credential store; API billing is separate from a Claude subscription.",
         "repair_url": "https://console.anthropic.com/", "can_install": False},
        {"id": "portals", "label": "Job source runtime", "status": "ready" if portals_ready else "setup_required",
         "description": "Reviewed portal CLIs only. Pasted postings work without Bun.",
         "repair_url": "https://bun.sh/", "can_install": os.name == "nt"},
        {"id": "tex", "label": "Document tools (LuaLaTeX / XeLaTeX)", "status": "ready" if tex_ready and not missing_tex else "setup_required",
         "description": tex_description(tex_ready, missing_tex),
         "repair_url": {"win32": "https://miktex.org/download", "darwin": "https://tug.org/mactex/"}.get(
             sys.platform, "https://tug.org/texlive/"),
         "can_install": winget_ready or tex_repair_ready},
        {"id": "pdf", "label": "PDF layout tools (Poppler)", "status": "ready" if pdf_ready else "setup_required",
         "description": "Poppler pdftotext with -bbox and pdfinfo. Actual extraction is validated for each PDF; finding a program alone is not a layout pass.",
         "repair_url": "https://github.com/oschwartz10612/poppler-windows/releases" if os.name == "nt" else "https://poppler.freedesktop.org/",
         "can_install": winget_ready},
    ]
    return {"dependencies": dependencies, "ai_connected": connected, "privacy_consent": settings.privacy_consent,
            "workspace_path": str(workspace.root), "active_runs": active, "version": __version__}


def _download(url: str, path: Path, max_size: int, workspace: Workspace, run_id: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "AIJobSearch-Dependency-Installer/0.1"})
    # These are fixed, official release URLs, not user-supplied posting URLs.
    with urllib.request.urlopen(request, timeout=30) as response, path.open("wb") as output:
        total = 0
        while chunk := response.read(256 * 1024):
            total += len(chunk)
            if total > max_size:
                raise AppError("dependency_download_too_large", "The dependency download exceeded its expected size.", 502)
            output.write(chunk)
            workspace.store.event(run_id, "download", {"bytes_received": total})


def repair(workspace: Workspace, run_id: str, dependency: str) -> dict:
    directory = workspace.path(str(Path(".app", "runs", run_id, "dependency")))
    directory.mkdir(parents=True, exist_ok=True)
    if dependency == "portals":
        if os.name != "nt":
            raise AppError("platform_setup_required", "This release's automatic Bun installer supports Windows x64. Use your operating system's guided installer.", 409)
        base = f"https://github.com/oven-sh/bun/releases/download/bun-v{BUN_VERSION}/"
        checksum_path, zip_path = directory / "checksums.txt", directory / "bun.zip"
        try:
            _download(base + "SHASUMS256.txt", checksum_path, 100_000, workspace, run_id)
            _download(base + BUN_ARCHIVE, zip_path, 150 * 1024 * 1024, workspace, run_id)
        except OSError as exc:
            raise AppError("dependency_download_failed", "Bun could not be downloaded from its official release. Use the repair link and retry.", 502) from exc
        match = re.search(rf"^([0-9a-fA-F]{{64}})\s+\*?{re.escape(BUN_ARCHIVE)}\s*$", checksum_path.read_text(), re.M)
        if (not match or match.group(1).lower() != BUN_SHA256
                or hashlib.sha256(zip_path.read_bytes()).hexdigest().lower() != BUN_SHA256):
            raise AppError("dependency_integrity_failed", "The Bun download did not match the official checksum. Nothing was installed.", 502)
        with zipfile.ZipFile(zip_path) as archive:
            content = archive.read(BUN_ARCHIVE.removesuffix(".zip") + "/bun.exe")
        target = data_home() / "runtimes" / "bun" / "bun.exe"
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_name("bun-" + uuid4().hex + ".exe")
        staged.write_bytes(content)
        os.replace(staged, target)
        version = command([str(target), "--version"], directory, timeout=10).strip()
        if version != BUN_VERSION:
            target.unlink()
            raise AppError("dependency_version_mismatch", "The installed Bun runtime did not report the expected version.", 502)
        return {"installed": dependency, "version": version}
    if dependency not in WINDOWS_PACKAGES or os.name != "nt":
        raise AppError("guided_install_required", "Use this dependency's official repair link, complete the installer, then refresh readiness.", 409)
    names = ("lualatex", "xelatex") if dependency == "tex" else ("pdftotext", "pdfinfo")
    if not all(find_tool(name, workspace.resources) for name in names):
        winget = shutil.which("winget")
        if not winget:
            raise AppError("guided_install_required", "Use this dependency's official repair link, complete the installer, then refresh readiness.", 409)
        identifier, version = WINDOWS_PACKAGES[dependency]
        workspace.store.event(run_id, "install", {"package": identifier, "version": version, "scope": "user"})
        command([
            winget, "install", "--id", identifier, "--version", version, "--exact", "--scope", "user", "--silent",
            "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity",
        ], directory, timeout=600)
    if dependency == "tex":
        installed, warnings = _repair_tex(workspace, run_id, directory)
    if not all(find_tool(name, workspace.resources) for name in names):
        raise AppError("dependency_not_detected", "Installation finished but the tools are not available yet. Reopen the app, or use the repair link.", 409)
    if dependency == "tex":
        return {"installed": dependency, "packages_installed": installed, "warnings": warnings,
                "message": "LuaLaTeX, XeLaTeX, and every package the stock templates need are installed and detected."}
    return {"installed": dependency, "message": "Tool executables detected. Actual PDF extraction is checked for each document."}


def tex_manager(resources: Path) -> tuple[str, bool] | None:
    """MiKTeX's package manager: the `miktex` CLI (preferred) or legacy `mpm`."""
    if modern := find_tool("miktex", resources):
        return modern, True
    if legacy := find_tool("mpm", resources):
        return legacy, False
    return None


def tex_description(tools_ready: bool, missing: list[str] | None) -> str:
    if tools_ready and missing:
        packages = ", ".join(dict.fromkeys(package_for(name) for name in missing))
        return f"Missing TeX packages the CV and cover letter need: {packages}. Use Repair to install them."
    return "LuaLaTeX and XeLaTeX plus the stock template packages are required. Compilation never opens package-install dialogs."


def _tail(exc: AppError) -> str:
    output = exc.detail.get("output", "") if isinstance(exc.detail, dict) else ""
    lines = [line for line in str(output).splitlines() if line.strip() and "checked for MiKTeX updates" not in line]
    return "\n".join(lines[-5:]) or exc.message


def _repair_tex(workspace: Workspace, run_id: str, directory: Path) -> tuple[list[str], dict[str, str]]:
    manager = tex_manager(workspace.resources)
    if not manager:
        missing = missing_tex_files(workspace.resources, refresh=True) or []
        packages = ", ".join(dict.fromkeys(package_for(name) for name in missing))
        raise AppError("tex_package_manager_missing",
                       "MiKTeX's package manager was not found. Use its repair link and reopen the app."
                       + (f" Missing packages: {packages}." if packages else ""), 409)
    executable, modern = manager

    def run(arguments: list[str], timeout: int) -> None:
        command([executable, *arguments], directory, timeout=timeout)

    workspace.store.event(run_id, "install", {"message": "Updating the MiKTeX package database."})
    try:
        run(["packages", "update-package-database"] if modern else ["--update-db"], 600)
    except AppError as exc:
        raise AppError("dependency_download_failed",
                       f"MiKTeX could not update its package database. Check the internet connection and retry. {_tail(exc)}",
                       502, detail={"output": _tail(exc)}) from exc
    missing = missing_tex_files(workspace.resources, refresh=True)
    # When kpsewhich is unavailable, require every template package; `require` skips installed ones.
    packages = list(TEX_PACKAGES) if missing is None else list(dict.fromkeys(package_for(name) for name in missing))
    failures: dict[str, str] = {}
    if packages:
        workspace.store.event(run_id, "install", {"message": "Installing missing TeX packages in MiKTeX user mode.",
                                                  "packages": packages})
        try:
            if not modern:
                raise AppError("tool_failed", "Legacy mpm installs one package at a time.", 422)
            run(["packages", "require", *packages], 1800)
        except AppError:
            for package in packages:
                workspace.store.event(run_id, "install", {"package": package})
                try:
                    run(["packages", "require", package] if modern else ["--install=" + package], 600)
                except AppError as exc:
                    if "already installed" not in _tail(exc):
                        failures[package] = _tail(exc)
    workspace.store.event(run_id, "install", {"message": "Refreshing the TeX file-name database and formats."})
    if modern:
        refresh_steps = [["fndb", "refresh"], ["formats", "build", "lualatex"], ["formats", "build", "xelatex"]]
    else:
        refresh_steps = [["--update-fndb"]] if find_tool("initexmf", workspace.resources) else []
        executable = find_tool("initexmf", workspace.resources) or executable
    for step in refresh_steps:
        try:
            run(step, 900)
        except AppError as exc:
            failures[" ".join(step)] = _tail(exc)
    clear_cache()
    still_missing = missing_tex_files(workspace.resources, refresh=True)
    if still_missing:
        names = list(dict.fromkeys(package_for(name) for name in still_missing))
        raise AppError("tex_missing_package",
                       f"These TeX packages are still missing after repair: {', '.join(names)}. "
                       "Install them with the MiKTeX Console (Packages tab) and refresh readiness.", 502,
                       detail=_failure(
                           "tex_missing_package", "Some TeX packages could not be installed",
                           f"After repair, TeX still cannot find {', '.join(still_missing)} "
                           f"(packages: {', '.join(names)}).",
                           "Check the internet connection and retry Repair. If it keeps failing, open MiKTeX Console, "
                           "install the listed packages on the Packages tab, then refresh readiness.",
                           missing_files=still_missing, packages=names, install_errors=failures,
                       ))
    return packages, failures
