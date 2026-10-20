from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest
from conftest import RESOURCES

specification = importlib.util.spec_from_file_location(
    "app_packaging_resources", RESOURCES / "app" / "packaging" / "bundle_resources.py",
)
packaging_resources = importlib.util.module_from_spec(specification)
specification.loader.exec_module(packaging_resources)


def test_reviewed_resource_manifest_excludes_personal_data():
    sources = packaging_resources.verified_files()
    assert len(sources) > 30
    assert all(path.is_relative_to(RESOURCES) for path in sources)
    relatives = [str(path.relative_to(RESOURCES)) for path in sources]
    assert not any(".app" in Path(path).parts or "node_modules" in Path(path).parts for path in relatives)
    assert "job_search_tracker.csv" not in relatives
    assert "cover_letters\\OpenFonts\\OFL.txt" in relatives


def test_modified_candidate_template_stops_packaging(tmp_path):
    source = tmp_path / "CLAUDE.md"
    source.write_bytes(b"private candidate data")
    with pytest.raises(ValueError, match="personalized workspace"):
        packaging_resources.verify_file(tmp_path, "CLAUDE.md", hashlib.sha256(b"reviewed template").hexdigest())


@pytest.mark.parametrize("relative", ["..\\outside.txt", "C:\\outside.txt", "CLAUDE.md:stream", "folder\\..\\CLAUDE.md"])
def test_packaging_manifest_cannot_escape_its_resources(tmp_path, relative):
    with pytest.raises(ValueError, match="Invalid resource path"):
        packaging_resources.verify_file(tmp_path, relative, "not-a-hash")
