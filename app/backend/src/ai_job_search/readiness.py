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
from .runtime import Credentials
from .workspace import Workspace

BUN_VERSION = "1.3.14"
BUN_ARCHIVE = "bun-windows-x64-baseline.zip"
BUN_SHA256 = "538f9c846355d9e847b2671bc00c47da4229a0befb24df3282b739770f3b475f"
WINDOWS_PACKAGES = {"tex": ("MiKTeX.MiKTeX", "25.12"), "pdf": ("oschwartz10612.Poppler", "25.07.0-0")}
TEX_PACKAGES = (
    "moderncv", "fontawesome5", "fontawesome6", "academicons", "import", "luatexbase", "luaotfload",
    "pgf", "titlesec", "textpos", "xltxtra", "xunicode", "cite", "realscripts", "needspace",
    "fontspec", "geometry", "hyperref", "xcolor", "fancyhdr",
)


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
    pdf_ready = all(find_tool(name, workspace.resources) for name in ("pdftotext", "pdfinfo"))
    winget_ready = os.name == "nt" and bool(shutil.which("winget"))
    tex_repair_ready = os.name == "nt" and tex_ready and bool(find_tool("mpm", workspace.resources))
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
        {"id": "tex", "label": "Document tools (LuaLaTeX / XeLaTeX)", "status": "ready" if tex_ready else "setup_required",
         "description": "LuaLaTeX and XeLaTeX are required. Missing packages are reported; compilation never opens package-install dialogs.",
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
        manager = find_tool("mpm", workspace.resources)
        if not manager:
            raise AppError("tex_package_manager_missing", "MiKTeX's package manager was not found. Use its repair link and reopen the app.", 409)
        workspace.store.event(run_id, "install", {"message": "Installing the documented stock-template packages in MiKTeX user mode."})
        command([manager, "--update-db"], directory, timeout=180)
        command([manager, *("--install=" + name for name in TEX_PACKAGES)], directory, timeout=1200)
    if not all(find_tool(name, workspace.resources) for name in names):
        raise AppError("dependency_not_detected", "Installation finished but the tools are not available yet. Reopen the app, or use the repair link.", 409)
    return {"installed": dependency, "message": "Tool executables detected; stock TeX packages installed when applicable. Actual compilation and PDF extraction are checked for each document."}
