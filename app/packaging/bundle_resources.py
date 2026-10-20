from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import re
import sys
import tomllib
from pathlib import Path, PureWindowsPath

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("framework-resources.json")
PORTALS = ("freehire-search", "linkedin-search", "jobindex-search", "jobbank-search", "jobdanmark-search", "jobnet-search")
SOURCE_PORTALS = ("freehire-search", "linkedin-search")
FIXED = (
    "LICENSE", "CLAUDE.md", "AGENTS.md", "documents\\README.md", "cv\\main_example.tex",
    "cover_letters\\cover_example.tex", "cover_letters\\cover.cls",
    "tools\\job_key.py", "tools\\rank_state.py", "tools\\robots_check.py",
    "tools\\verify_pdf.py", "tools\\verify_layout.py",
)
PATTERNS = (
    ".claude\\commands\\*.md", ".claude\\skills\\job-application-assistant\\*.md",
    ".claude\\skills\\job-scraper\\SKILL.md", "cover_letters\\OpenFonts\\**\\*",
)


def resource_paths(root: Path) -> list[str]:
    paths = set(FIXED)
    for pattern in PATTERNS:
        paths.update(str(path.relative_to(root)) for path in root.glob(pattern) if path.is_file())
    for portal in PORTALS:
        paths.add(str(Path(".agents", "skills", portal, "SKILL.md")))
    for portal in SOURCE_PORTALS:
        directory = Path(".agents", "skills", portal, "cli")
        paths.add(str(directory / "package.json"))
        paths.update(str(path.relative_to(root)) for path in (root / directory / "src").rglob("*.ts"))
    return sorted(paths)


def verify_file(root: Path, relative: str, checksum: str) -> Path:
    path = PureWindowsPath(relative)
    if path.is_absolute() or path.drive or ".." in path.parts or ":" in relative or str(path) != relative:
        raise ValueError(f"Invalid resource path: {relative}")
    source = root / relative
    for item in (source, *source.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError(f"Linked resource is not allowed: {relative}")
        if item == root:
            break
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != checksum:
        raise ValueError(f"Resource differs from the reviewed template: {relative}. Do not package a personalized workspace.")
    return source


def verified_files(root: Path = ROOT, manifest: Path = MANIFEST) -> list[Path]:
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if document.get("version") != 1 or not isinstance(document.get("resources"), dict):
        raise ValueError("Unsupported framework resource manifest.")
    resources = document["resources"]
    if set(resources) != set(resource_paths(root)):
        raise ValueError("Framework resource inventory changed. Review the explicit allowlist and manifest before building.")
    return [verify_file(root, relative, checksum) for relative, checksum in resources.items()]


def record_reviewed_templates() -> None:
    resources = {}
    for relative in resource_paths(ROOT):
        source = ROOT / relative
        resources[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
    MANIFEST.write_text(json.dumps({"version": 1, "resources": resources}, indent=2) + "\n", encoding="utf-8")


def runtime_distributions(root: Path) -> list[metadata.Distribution]:
    project = tomllib.loads((root / "app" / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    pending = [Requirement(value) for value in project["project"]["dependencies"]]
    distributions = {}
    while pending:
        requirement = pending.pop()
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        key = re.sub(r"[-_.]+", "-", requirement.name).lower()
        if key in distributions:
            continue
        distribution = metadata.distribution(requirement.name)
        distributions[key] = distribution
        pending.extend(Requirement(value) for value in distribution.requires or [])
    distributions["pyinstaller"] = metadata.distribution("pyinstaller")
    return [distributions[key] for key in sorted(distributions)]


def prepare_notices(output: Path, root: Path = ROOT) -> list[tuple[str, str]]:
    output.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    index = [
        "Third-party dependency notices for AI Job Search.",
        "Notices describe installed production dependencies; not every optional module is bundled.",
        "The SDK native runtime is unmodified. Provider service terms and API billing apply separately.",
        "Bun, MiKTeX and Poppler are not bundled; guided setup downloads them separately with consent.",
        "",
    ]

    def save(relative: Path, content: bytes) -> None:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        files.append(target)

    for distribution in runtime_distributions(root):
        name = distribution.metadata["Name"]
        directory = Path("python", name + "-" + distribution.version)
        notices = [
            file for file in distribution.files or []
            if any(word in str(file).casefold() for word in ("license", "notice", "copying", "copyright"))
        ]
        if not notices:
            raise ValueError(f"The installed {name} distribution is missing its license. Review redistribution before building.")
        for file in notices:
            source = Path(distribution.locate_file(file))
            if source.is_file():
                save(directory / Path(str(file)), source.read_bytes())
        index.append(f"{name} {distribution.version}: {directory}")

    lock = json.loads((root / "app" / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    for location, package in sorted(lock["packages"].items()):
        if not location or package.get("dev") or "node_modules/" not in location:
            continue
        # PDF.js's optional Node/Skia backend is not part of the browser bundle.
        if location.startswith("node_modules/@napi-rs/canvas"):
            continue
        folder = root / "app" / "frontend" / Path(*location.split("/"))
        if not folder.is_dir() and package.get("optional"):
            continue
        notices = [
            path for path in folder.iterdir() if path.is_file()
            and any(word in path.name.casefold() for word in ("license", "notice", "copying", "copyright"))
        ]
        if not notices:
            raise ValueError(f"The installed {location} package is missing its license.")
        name = location.removeprefix("node_modules/").replace("/", "_")
        directory = Path("frontend", name + "-" + package["version"])
        for source in notices:
            save(directory / source.name, source.read_bytes())
        index.append(f"{name} {package['version']}: {directory}")

    python = Path(sys.base_prefix)
    runtime_license = (python / "LICENSE.txt").read_bytes()
    # Windows Python includes Tcl and Tk notices in its combined license.
    if runtime_license.count(b"Scriptics Corporation") < 2:
        raise ValueError("The Python distribution's combined Tcl/Tk notices are missing. Review them before redistribution.")
    save(Path("python-runtime", "LICENSE.txt"), runtime_license)
    save(Path("fonts-OFL.txt"), (root / "cover_letters" / "OpenFonts" / "OFL.txt").read_bytes())
    save(Path("project-LICENSE.txt"), (root / "LICENSE").read_bytes())
    save(Path("index.txt"), ("\n".join(index) + "\n").encode("utf-8"))
    return [(str(path), str(Path("licenses") / path.relative_to(output).parent)) for path in files]


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the packaging allowlist; never copy a candidate workspace.")
    parser.add_argument("--record-reviewed-templates", action="store_true",
                        help="Maintainer-only: replace hashes after reviewing pristine, non-personal resource changes.")
    args = parser.parse_args()
    if args.record_reviewed_templates:
        record_reviewed_templates()
    print(f"Verified {len(verified_files())} reviewed framework resources.")


if __name__ == "__main__":
    main()
