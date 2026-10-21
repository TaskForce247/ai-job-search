"""TeX package requirements, detection, and diagnostics for the stock CV and cover-letter templates."""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from .config import framework_root

# Parsed from tex-requirements.txt at the repository root (the single source of truth shared with
# tools/install_tex_packages.py). Keys are a file and its acceptable alternatives; values are package names.
REQUIREMENTS_PATH = framework_root() / "tex-requirements.txt"


def load_requirements(path: Path = REQUIREMENTS_PATH) -> dict[tuple[str, ...], str]:
    requirements: dict[tuple[str, ...], str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        package, files = line.split(None, 1)
        requirements[tuple(name.strip() for name in files.split("|") if name.strip())] = package
    return requirements


REQUIRED_TEX_FILES = load_requirements()
TEX_PACKAGES = tuple(dict.fromkeys(REQUIRED_TEX_FILES.values()))
PACKAGE_BY_FILE = {name: package for key, package in REQUIRED_TEX_FILES.items() for name in key}
# Environment that keeps MiKTeX from installing packages or opening dialogs behind the app's back.
QUIET_TEX_ENVIRONMENT = {"MIKTEX_ENABLE_INSTALLER": "0", "MIKTEX_AUTOINSTALL": "0"}
_CACHE_SECONDS = 60
_cache: dict[str, tuple[float, list[str]]] = {}


def package_for(filename: str) -> str:
    return PACKAGE_BY_FILE.get(filename, Path(filename).stem)


def missing_tex_files(resources: Path, *, refresh: bool = False) -> list[str] | None:
    """Return the required template files TeX cannot find, or None when the check cannot run."""
    from .readiness import find_tool
    from .runtime import minimal_environment

    kpsewhich = find_tool("kpsewhich", resources)
    if not kpsewhich:
        return None
    cached = _cache.get(kpsewhich)
    if cached and not refresh and time.monotonic() - cached[0] < _CACHE_SECONDS:
        return list(cached[1])
    names = [name for key in REQUIRED_TEX_FILES for name in key]
    environment = {**minimal_environment(), **QUIET_TEX_ENVIRONMENT}
    # Without this flag MiKTeX's kpsewhich silently installs, or waits on an invisible prompt for, missing files.
    options = ["--miktex-disable-installer"] if "miktex" in kpsewhich.casefold() else []
    try:
        completed = subprocess.run(
            [kpsewhich, *options, *names], capture_output=True, text=True, errors="replace", timeout=60, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = {Path(line.strip()).name.casefold() for line in completed.stdout.splitlines() if line.strip()}
    missing = [key[0] for key in REQUIRED_TEX_FILES if not any(name.casefold() in found for name in key)]
    _cache[kpsewhich] = (time.monotonic(), missing)
    return list(missing)


def clear_cache() -> None:
    _cache.clear()


_MISSING_PATTERNS = (
    re.compile(r"File [`'\"]?([^`'\"\s]+\.(?:sty|cls|def|fd|cfg|clo|tex|lua|otf|ttf|tfm|enc|map))['\"]? not found"),
    re.compile(r"I can't find file [`'\"]?([^`'\"\s]+)"),
    re.compile(r"(?:module|file) '([^']+\.lua)' not found"),
)


def missing_from_log(text: str) -> str | None:
    for pattern in _MISSING_PATTERNS:
        if match := pattern.search(text):
            return match.group(1)
    return None


def log_tail(directory: Path, stem: str, limit: int = 4000) -> str:
    log = directory / (stem + ".log")
    try:
        return log.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def first_error(text: str) -> str:
    """The first LaTeX error line (lines starting with '!' or 'file:line: ...') for a readable summary."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("!") or re.match(r"^[^:\s]+\.\w+:\d+:", stripped):
            return stripped[:300]
    return ""
