from __future__ import annotations

from uuid import uuid4

import pytest
from conftest import assessment

from ai_job_search.errors import AppError
from ai_job_search.models import Gate
from ai_job_search.policy import Policy
from ai_job_search.profile import MASTER_PATH, ProfileService, label, section
from ai_job_search.workspace import PROFILE_PATH


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_empty_identity_fields_do_not_capture_the_next_line(newline):
    text = newline.join([
        "- **Name:** Synthetic Candidate",
        "- **GitHub:** ",
        "- **Status:** Employed",
        "- **Headline:**",
        "",
        "### Languages",
        "| English | C2 |",
    ])
    assert label(text, "GitHub") == ""
    assert label(text, "Headline") == ""
    assert label(text, "Status") == "Employed"
    assert label(text, "Name") == "Synthetic Candidate"


def test_empty_github_does_not_corrupt_the_compiled_cv_header(confirmed):
    profile = ProfileService(confirmed).read().data
    assert profile.github == ""
    from ai_job_search.documents import render_cv
    from ai_job_search.models import DocumentContent
    source = render_cv(profile, DocumentContent(), confirmed.resources)
    assert "GitHub:" not in source and "**Status:**" not in source


def test_crlf_sections_and_unknown_content_survive_confirmation(workspace, profile):
    original = workspace.text(PROFILE_PATH).replace("\r\n", "\n").replace("\n", "\r\n")
    original += "\r\n## Additional licenses\r\n- Special synthetic license.\r\n"
    workspace.path(PROFILE_PATH).write_bytes(original.encode("utf-8"))
    root = workspace.text("CLAUDE.md") + "\n## Unknown app-independent policy\nKeep this exact text.\n"
    workspace.path("CLAUDE.md").write_bytes(root.encode("utf-8"))
    cv = workspace.text(MASTER_PATH).replace("\\end{document}", "\\section{Volunteering}\nReal synthetic volunteer work.\n\\end{document}")
    workspace.path(MASTER_PATH).write_bytes(cv.encode("utf-8"))
    service = ProfileService(workspace)
    service.apply(str(uuid4()), service.propose(profile, service.read().revision))
    view = service.read()
    assert view.data.name == profile.name
    assert view.data.email == profile.email
    assert view.data.experiences[0].start == "2020"
    assert view.data.education[0].institution == "Example University"
    assert "Special synthetic license." in workspace.text(PROFILE_PATH)
    assert "Keep this exact text." in workspace.text("CLAUDE.md")
    assert "Real synthetic volunteer work." in workspace.text(MASTER_PATH)
    assert workspace.text(PROFILE_PATH).count("## Identity") == 1
    assert workspace.text("CLAUDE.md").count("## Candidate Profile") == 1
    assert any(profile.email in item.text for item in view.evidence)
    assert profile.name in workspace.text("CLAUDE.md")
    assert profile.name.split()[0] in workspace.text(MASTER_PATH)


def test_profile_revision_blocks_a_stale_form(confirmed, profile):
    service = ProfileService(confirmed)
    revision = service.read().revision
    confirmed.path(PROFILE_PATH).write_text(confirmed.text(PROFILE_PATH) + "\nExternal fact.\n", encoding="utf-8")
    with pytest.raises(AppError) as caught:
        service.propose(profile, revision)
    assert caught.value.code == "revision_conflict"


def test_evidence_ids_are_stable_and_unknown_sections_remain_sources(confirmed):
    service = ProfileService(confirmed)
    first = service.read()
    second = service.read()
    assert first.evidence == second.evidence
    assert first.data.experiences[0].id == second.data.experiences[0].id
    assert len({evidence.id for evidence in first.evidence}) == len(first.evidence)


def test_score_matches_canonical_helper_and_rejects_personalized_drift(workspace):
    score, band = Policy(workspace).score({"technical": 82.5, "experience": 80, "behavioral": 75, "career": 90})
    assert score == 83
    assert band == "Strong Fit"
    relative = ".claude\\skills\\job-application-assistant\\04-job-evaluation.md"
    workspace.path(relative).write_text(workspace.text(relative).replace("Technical Skills: 30%", "Technical Skills: 35%"), encoding="utf-8")
    with pytest.raises(AppError) as caught:
        Policy(workspace).score({"technical": 82.5, "experience": 80, "behavioral": 75, "career": 90})
    assert caught.value.code == "unsupported_rubric"


def test_failed_gate_is_not_scored_and_flag_is_not_failure():
    assert assessment(failed=True).scores is None
    assert assessment(flagged=True).language.verdict == "FLAG"
    invalid = assessment().model_dump()
    invalid["language"] = Gate(verdict="FAIL", note="Required undeclared language.").model_dump()
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        type(assessment()).model_validate(invalid)


def test_unrelated_profile_edit_preserves_nested_notes_and_custom_master_preamble(confirmed):
    source = confirmed.text(PROFILE_PATH).replace("- Python", "### Languages and tools\n\n- Python")
    source = source.replace("## Technical Skills", "## Technical Skills\n\nAn unmapped skills note.")
    confirmed.path(PROFILE_PATH).write_bytes(source.encode("utf-8"))
    master = confirmed.text(MASTER_PATH).replace("\\begin{document}", "\\newcommand{\\customnote}{Keep me}\n\\begin{document}")
    master = master.replace("\\section{Professional Experience}", "\\section{Professional Experience}\nUnmapped scope note.")
    confirmed.path(MASTER_PATH).write_bytes(master.encode("utf-8"))
    service = ProfileService(confirmed)
    current = service.read()
    changed = current.data.model_copy(deep=True)
    changed.email = "updated@example.invalid"
    service.apply("email-only", service.propose(changed, current.revision))
    assert section(confirmed.text(PROFILE_PATH), "Technical Skills") == section(source, "Technical Skills")
    assert "Unmapped scope note." in confirmed.text(MASTER_PATH)
    assert "\\newcommand{\\customnote}{Keep me}" in confirmed.text(MASTER_PATH)
    assert "\\email{updated@example.invalid}" in confirmed.text(MASTER_PATH)


def test_editing_unmapped_section_requires_explicit_import_review(confirmed):
    confirmed.path(PROFILE_PATH).write_text(
        confirmed.text(PROFILE_PATH).replace("## Technical Skills", "## Technical Skills\n\nUnmapped factual note."),
        encoding="utf-8",
    )
    service = ProfileService(confirmed)
    current = service.read()
    changed = current.data.model_copy(deep=True)
    changed.skills.append("New confirmed skill")
    with pytest.raises(AppError) as caught:
        service.propose(changed, current.revision)
    assert caught.value.code == "unmapped_profile"
    assert "Unmapped factual note." in confirmed.text(PROFILE_PATH)


def test_multiple_constraints_do_not_grow_on_repeated_saves(confirmed):
    service = ProfileService(confirmed)
    current = service.read()
    changed = current.data.model_copy(deep=True)
    changed.deal_breakers = ["No relocation", "No weekly travel"]
    service.apply("constraints", service.propose(changed, current.revision))
    for index in range(2):
        current = service.read()
        assert current.data.deal_breakers == ["No relocation", "No weekly travel"]
        service.apply(f"save-{index}", service.propose(current.data, current.revision))


def test_unfilled_stock_sections_are_not_candidate_evidence_or_master_facts(workspace, profile):
    service = ProfileService(workspace)
    assert not service.read().evidence
    service.apply("initial-profile", service.propose(profile, service.read().revision))
    master = workspace.text(MASTER_PATH)
    assert "[Certification" not in master and "[AUTHOR" not in master
    assert "[https://linkedin.com/in/your-profile]" not in master
    assert "[Achievement" not in master
