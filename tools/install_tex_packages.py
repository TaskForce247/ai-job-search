#!/usr/bin/env python3
"""Check, install, and smoke-test the TeX packages the stock CV and cover-letter templates need.

Reads tex-requirements.txt (repository root) - the same list the desktop app's readiness
check and Repair button use - so a new machine can be prepared with one command:

    python tools/install_tex_packages.py            # install whatever is missing, then smoke-compile
    python tools/install_tex_packages.py --check    # only report missing packages (exit 1 if any)
    python tools/install_tex_packages.py --no-smoke # install without the test compile

Supports MiKTeX (Windows; user-mode installs, no admin needed) and TeX Live/TinyTeX/MacTeX (tlmgr).
Compiles and lookups always run with MiKTeX's on-the-fly installer disabled, so nothing hangs on
an invisible "install package?" prompt.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "tex-requirements.txt"
QUIET = {"MIKTEX_ENABLE_INSTALLER": "0", "MIKTEX_AUTOINSTALL": "0"}
COVER_SMOKE = r"""\documentclass[]{cover}
\begin{document}
\namesection{Test}{Candidate}{test@example.com}
\companyname{Example Company}
\companyaddress{123 Hiring Street\\Example City}
\currentdate{\today}
\lettercontent{Dear Hiring Manager,}
\lettercontent{This smoke test verifies that xelatex can load cover.cls and the bundled fonts.}
\closing{Sincerely,}
\signature{Test Candidate}
\end{document}
"""


def load_requirements() -> dict[tuple[str, ...], str]:
    requirements: dict[tuple[str, ...], str] = {}
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            package, files = line.split(None, 1)
            requirements[tuple(name for name in files.split("|") if name)] = package
    return requirements


def find_tool(name: str) -> str | None:
    if found := shutil.which(name):
        return found
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        for directory in (local / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64",
                          Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "MiKTeX" / "miktex" / "bin" / "x64"):
            candidate = directory / (name + ".exe")
            if candidate.is_file():
                return str(candidate)
    return None


def run(arguments: list[str], *, timeout: int = 1800, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, capture_output=True, text=True, errors="replace", timeout=timeout,
                          cwd=cwd, env={**os.environ, **QUIET})


def missing_files(requirements: dict[tuple[str, ...], str]) -> list[tuple[str, ...]]:
    kpsewhich = find_tool("kpsewhich")
    if not kpsewhich:
        sys.exit("kpsewhich not found. Install MiKTeX (Windows) or TeX Live/MacTeX first; see SETUP.md.")
    options = ["--miktex-disable-installer"] if "miktex" in kpsewhich.casefold() else []
    names = [name for key in requirements for name in key]
    found = {Path(line.strip()).name.casefold() for line in run([kpsewhich, *options, *names], timeout=120).stdout.splitlines()
             if line.strip()}
    return [key for key in requirements if not any(name.casefold() in found for name in key)]


def tail(result: subprocess.CompletedProcess) -> str:
    lines = [line for line in (result.stdout + result.stderr).splitlines()
             if line.strip() and "checked for MiKTeX updates" not in line]
    return "\n    ".join(lines[-4:])


def install(packages: list[str]) -> list[str]:
    """Install packages with the detected package manager; returns packages that failed."""
    failed: list[str] = []
    if miktex := find_tool("miktex"):
        print("Updating the MiKTeX package database ...")
        result = run([miktex, "packages", "update-package-database"], timeout=600)
        if result.returncode:
            print("  warning: " + tail(result))
        print("Installing (MiKTeX user mode): " + " ".join(packages))
        if run([miktex, "packages", "require", *packages]).returncode:
            for package in packages:  # isolate the package(s) that fail
                result = run([miktex, "packages", "require", package], timeout=600)
                if result.returncode:
                    failed.append(package)
                    print(f"  {package}: " + tail(result))
        print("Refreshing the file-name database and formats ...")
        for step in (["fndb", "refresh"], ["formats", "build", "lualatex"], ["formats", "build", "xelatex"]):
            result = run([miktex, *step], timeout=900)
            if result.returncode:
                print(f"  warning ({' '.join(step)}): " + tail(result))
    elif mpm := find_tool("mpm"):
        run([mpm, "--update-db"], timeout=600)
        for package in packages:
            result = run([mpm, "--install=" + package], timeout=600)
            if result.returncode and "already installed" not in result.stdout + result.stderr:
                failed.append(package)
                print(f"  {package}: " + tail(result))
        if initexmf := find_tool("initexmf"):
            run([initexmf, "--update-fndb"], timeout=900)
    elif tlmgr := find_tool("tlmgr"):
        print("Installing with tlmgr: " + " ".join(packages))
        result = run([tlmgr, "install", *packages])
        if result.returncode:
            print("  tlmgr failed (system-wide TeX Live may need sudo: sudo tlmgr install ...):\n    " + tail(result))
            failed.extend(packages)
    else:
        sys.exit("No TeX package manager (miktex, mpm, or tlmgr) found. Install the listed packages manually.")
    return failed


def smoke() -> bool:
    ok = True
    with tempfile.TemporaryDirectory(prefix="tex-smoke-") as temp:
        directory = Path(temp)
        shutil.copy(ROOT / "cv" / "main_example.tex", directory)
        shutil.copy(ROOT / "cover_letters" / "cover.cls", directory)
        shutil.copytree(ROOT / "cover_letters" / "OpenFonts", directory / "OpenFonts")
        (directory / "cover_smoke.tex").write_text(COVER_SMOKE, encoding="utf-8")
        for engine, source in (("lualatex", "main_example.tex"), ("xelatex", "cover_smoke.tex")):
            executable = find_tool(engine)
            if not executable:
                print(f"FAIL {engine}: not found")
                ok = False
                continue
            options = ["--disable-installer"] if "miktex" in executable.casefold() else []
            # First runs may build font caches and formats; allow plenty of time.
            try:
                result = run([executable, *options, "-interaction=nonstopmode", "-halt-on-error", source],
                             timeout=600, cwd=directory)
            except subprocess.TimeoutExpired:
                print(f"FAIL {engine}: timed out after 600 seconds")
                ok = False
                continue
            pdf = directory / (Path(source).stem + ".pdf")
            if result.returncode or not pdf.is_file():
                errors = [line for line in result.stdout.splitlines() if line.startswith("!")]
                print(f"FAIL {engine} {source}: " + ("; ".join(errors[:3]) or tail(result)))
                ok = False
            else:
                print(f"ok   {engine} {source}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="only report missing packages")
    parser.add_argument("--no-smoke", action="store_true", help="skip the test compile")
    args = parser.parse_args()
    requirements = load_requirements()
    missing = missing_files(requirements)
    packages = list(dict.fromkeys(requirements[key] for key in missing))
    if not packages:
        print(f"All {len(set(requirements.values()))} template packages are installed.")
    elif args.check:
        print("Missing TeX packages: " + " ".join(packages))
        return 1
    else:
        failed = install(packages)
        still = list(dict.fromkeys(requirements[key] for key in missing_files(requirements)))
        if still:
            print("Still missing after install: " + " ".join(still)
                  + (f" (install errors: {' '.join(failed)})" if failed else ""))
            return 1
        print("Installed: " + " ".join(packages))
    if args.check or args.no_smoke:
        return 0
    return 0 if smoke() else 1


if __name__ == "__main__":
    raise SystemExit(main())
