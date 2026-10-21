from __future__ import annotations

import re
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .errors import AppError
from .models import Education, Evidence, Experience, Language, Profile, ProfileView, Project
from .storage import digest
from .workspace import PROFILE_PATH, Workspace

MASTER_PATH = str(Path("cv", "main_example.tex"))
BEHAVIOR_PATH = str(Path(".claude", "skills", "job-application-assistant", "02-behavioral-profile.md"))
STYLE_PATH = str(Path(".claude", "skills", "job-application-assistant", "03-writing-style.md"))
FACT_PATHS = [PROFILE_PATH, "CLAUDE.md", MASTER_PATH, BEHAVIOR_PATH, STYLE_PATH]


def clean(value: str) -> str:
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S).strip()
    if re.search(r"\[(?:YOUR_|LANGUAGE|LEVEL|DEGREE|YEARS|INSTITUTION|TOPICS|JOB_TITLE|COMPANY|START|END|PROJECT|OTHER_)",
                 value):
        return ""
    return value.replace("\\|", "|").strip()


def inline(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


def normalized_section(value: str) -> str:
    return " ".join(re.sub(r"<!--.*?-->", "", value, flags=re.S).split())


def section(text: str, title: str, level: int = 2) -> str:
    heading = "#" * level
    match = re.search(
        rf"^{heading} {re.escape(title)}\s*\n(.*?)(?=^#{{1,{level}}} |\Z)",
        text, re.M | re.S,
    )
    return match.group(1).strip() if match else ""


def replace_section(text: str, title: str, body: str, level: int = 2) -> str:
    heading = "#" * level
    pattern = rf"^{heading} {re.escape(title)}[ \t]*\r?\n.*?(?=^#{{1,{level}}} |\Z)"
    replacement = f"{heading} {title}\n\n{body.strip()}\n\n"
    if re.search(pattern, text, re.M | re.S):
        return re.sub(pattern, lambda _: replacement, text, count=1, flags=re.M | re.S)
    return text.rstrip() + "\n\n" + replacement


def label(text: str, name: str) -> str:
    match = re.search(rf"^[ \t]*-?[ \t]*\*\*{re.escape(name)}:\*\*[ \t]*([^\r\n]*)\r?$", text, re.M)
    return clean(match.group(1)) if match else ""


def set_label(text: str, name: str, value: str) -> str:
    pattern = rf"^\s*-?\s*\*\*{re.escape(name)}:\*\*[^\n]*$"
    line = f"- **{name}:** {inline(value)}"
    if re.search(pattern, text, re.M):
        return re.sub(pattern, lambda _: line, text, count=1, flags=re.M)
    return text.rstrip() + "\n" + line + "\n"


def table_rows(text: str) -> list[list[str]]:
    result = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        parts = [clean(part) for part in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
        if not parts or not parts[0] or parts[0].lower() in ("language", "degree") or re.fullmatch(r"[-: ]+", parts[0]):
            continue
        result.append(parts)
    return result


def bullet_values(text: str) -> list[str]:
    return [value for match in re.finditer(r"^\s*[-*] (.+)$", text, re.M) if (value := clean(match.group(1)))]


def record_id(source: str, index: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"ai-job-search:{source}:{index}"))


class ProfileService:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def snapshot(self) -> dict[str, str | None]:
        return self.workspace.snapshot(FACT_PATHS)

    def read(self) -> ProfileView:
        with self.workspace.lock():
            return self._read_snapshot()

    def _read_snapshot(self) -> ProfileView:
        snapshot = self.snapshot()
        text = self.workspace.text(PROFILE_PATH)
        identity = section(text, "Identity")
        root_identity = section(section(self.workspace.text("CLAUDE.md"), "Candidate Profile"), "Identity", 3)
        profile = Profile(
            name=label(identity, "Name"), email=label(identity, "Email"),
            phone=label(identity, "Phone"), location=label(identity, "Location"),
            linkedin=label(identity, "LinkedIn"), github=label(identity, "GitHub"),
            employment_status=label(identity, "Status"), headline=label(identity, "Headline"),
            cv_language=label(identity, "CV language") or label(root_identity, "CV language") or "English",
            summary=clean(section(text, "Professional Summary")),
            behavioral=clean(section(self.workspace.text(BEHAVIOR_PATH), "Personal preferences")),
            writing_style=clean(section(self.workspace.text(STYLE_PATH), "Personal writing preferences")),
        )
        profile.languages = [
            Language(name=row[0], level=row[1] if len(row) > 1 else "")
            for row in table_rows(section(identity, "Languages", 3))
        ]
        profile.education = []
        for index, row in enumerate(table_rows(section(text, "Education"))):
            period = re.split(r"\s+(?:-|to)\s+|\s*[–—]\s*", row[1] if len(row) > 1 else "", maxsplit=1)
            profile.education.append(Education(
                id=record_id("education", index), degree=row[0],
                start=period[0], end=period[1] if len(period) > 1 else "",
                institution=row[2] if len(row) > 2 else "", details=row[3] if len(row) > 3 else "",
            ))
        experience = section(text, "Professional Experience")
        for index, match in enumerate(re.finditer(
            r"^### (.*?) - (.*?) \((.*?) - (.*?)\)\s*\n(.*?)(?=^### |\Z)", experience, re.M | re.S,
        )):
            title, company, start, end, body = match.groups()
            if not clean(title):
                continue
            lines = body.strip().splitlines()
            location = clean(lines[0]) if lines and not lines[0].startswith(("-", "<!--")) else ""
            profile.experiences.append(Experience(
                id=record_id("experience", index), title=clean(title), company=clean(company),
                start=clean(start), end=clean(end), location=location, bullets=bullet_values(body),
            ))
        projects = section(text, "Independent Projects")
        for index, match in enumerate(re.finditer(
            r"^- \*\*(.+?)\*\*:\s*(.*?)(?=^- \*\*|\Z)", projects, re.M | re.S,
        )):
            if not clean(match.group(1)):
                continue
            body = match.group(2).strip()
            profile.projects.append(Project(
                id=record_id("project", index), name=clean(match.group(1)),
                description=clean(body.split("\n")[0]), bullets=bullet_values(body),
            ))
        profile.skills = bullet_values(section(text, "Technical Skills"))
        preferences = section(text, "Search Preferences")
        profile.goals = bullet_values(section(preferences, "Goals", 3))
        profile.deal_breakers = bullet_values(section(preferences, "Deal-breakers", 3))
        constraints = label(identity, "Constraints")
        for constraint in (value.strip() for value in constraints.split(";")):
            if constraint and constraint not in profile.deal_breakers:
                profile.deal_breakers.append(constraint)
        evidence = self.evidence()
        warnings = []
        if experience and clean(re.sub(r"<!--.*?-->", "", experience, flags=re.S)) and not profile.experiences:
            warnings.append("Work history uses an unrecognized format. Import the profile before replacing this section.")
        if not profile.name and evidence:
            warnings.append("Some facts exist outside the mapped profile. Import them before completing onboarding.")
        self.workspace.unchanged(snapshot)
        return ProfileView(data=profile, revision=digest(snapshot), evidence=evidence, warnings=warnings)

    def evidence(self) -> list[Evidence]:
        result = []
        for relative in FACT_PATHS[:3]:
            text = self.workspace.text(relative)
            template = (self.workspace.resources / relative).read_text(encoding="utf-8")
            if relative == "CLAUDE.md":
                text = section(text, "Candidate Profile")
                template = section(template, "Candidate Profile")
            elif relative == MASTER_PATH:
                start = re.search(r"\\name\{", text)
                text = text[start.start():] if start else text.split("\\begin{document}", 1)[-1]
                start = re.search(r"\\name\{", template)
                template = template[start.start():] if start else template.split("\\begin{document}", 1)[-1]
            template_chunks = {normalized_section(chunk) for chunk in re.split(r"\n\s*\n", template)}
            heading = "Candidate"
            for chunk in re.split(r"\n\s*\n", text):
                chunk = chunk.strip()
                if chunk.startswith("#"):
                    heading = chunk.splitlines()[0].lstrip("# ").strip()
                if (not chunk or not clean(chunk) or normalized_section(chunk) in template_chunks
                        or chunk.startswith(("---", "<!--", "%%", "% "))):
                    continue
                if re.search(r"\[(?:YOUR_|First|Last|Language |Skill |Achievement |Write |YYYY|Job Title|Company|City)",
                             chunk):
                    continue
                if len(chunk) < 8:
                    continue
                substance = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?", "", chunk)
                if not re.search(r"\w{2}", substance):
                    continue
                result.append(Evidence(
                    id=digest(relative + "\n" + chunk)[:24], source=relative, section=heading, text=chunk[:12_000],
                ))
        return result

    def propose(self, profile: Profile, expected_revision: str, *, allow_unmapped: bool = False) -> dict:
        expected = self.snapshot()
        current = self.read()
        if current.revision != expected_revision:
            raise AppError("revision_conflict", "The profile changed. Reload it before saving.", 409)
        if not allow_unmapped and any(warning.startswith("Work history uses") for warning in current.warnings) and current.data.experiences != profile.experiences:
            raise AppError("unmapped_profile", "Import the existing profile to review unmapped work history first.", 409)
        original = self.workspace.text(PROFILE_PATH)
        before = current.data.model_dump(exclude={
            field: {"__all__": {"id"}} for field in ("experiences", "education", "projects")
        })
        after = profile.model_dump(exclude={
            field: {"__all__": {"id"}} for field in ("experiences", "education", "projects")
        })
        changed = {field for field in after if after[field] != before[field]}
        identity = section(original, "Identity")
        languages = "| Language | Level | Notes |\n|----------|-------|-------|\n"
        language_notes = {row[0].casefold(): row[2] for row in table_rows(section(identity, "Languages", 3)) if len(row) > 2}
        languages += "\n".join(
            f"| {inline(item.name)} | {inline(item.level)} | {inline(language_notes.get(item.name.casefold(), ''))} |"
            for item in profile.languages
        )
        for field, value in (
            ("Name", profile.name), ("Location", profile.location), ("Phone", profile.phone),
            ("Email", profile.email), ("LinkedIn", profile.linkedin), ("GitHub", profile.github),
            ("Status", profile.employment_status), ("Headline", profile.headline), ("CV language", profile.cv_language),
            ("Constraints", "; ".join(profile.deal_breakers)),
        ):
            identity = set_label(identity, field, value)
        if "languages" in changed:
            identity = replace_section(identity, "Languages", languages, 3)
        result = replace_section(original, "Identity", identity)
        representations = {
            "Education": ("education", self.education_markdown),
            "Professional Experience": ("experiences", self.experience_markdown),
            "Independent Projects": ("projects", self.projects_markdown),
            "Technical Skills": ("skills", lambda value: "\n".join("- " + inline(s) for s in value.skills)),
        }
        template = (self.workspace.resources / PROFILE_PATH).read_text(encoding="utf-8")
        for title, (field, render) in representations.items():
            if field not in changed:
                continue
            existing = normalized_section(section(original, title))
            if (not allow_unmapped and existing
                    and existing != normalized_section(section(template, title))
                    and existing != normalized_section(render(current.data))):
                raise AppError("unmapped_profile", f"{title} contains unmapped notes or formatting. Import it for explicit review before replacing that section.", 409)
            result = replace_section(result, title, render(profile))
        if "summary" in changed:
            result = replace_section(result, "Professional Summary", profile.summary)
        if {"goals", "deal_breakers"} & changed:
            existing_preferences = section(original, "Search Preferences")
            existing_preferences = replace_section(
                existing_preferences, "Goals", "\n".join("- " + inline(s) for s in profile.goals), 3,
            )
            existing_preferences = replace_section(
                existing_preferences, "Deal-breakers", "\n".join("- " + inline(s) for s in profile.deal_breakers), 3,
            )
            result = replace_section(result, "Search Preferences", existing_preferences)
        changes = {PROFILE_PATH: result}
        root = self.workspace.text("CLAUDE.md")
        candidate = section(root, "Candidate Profile")
        root_identity = section(candidate, "Identity", 3)
        for name, field, value in (
                ("Name", "name", profile.name), ("Location", "location", profile.location), ("Phone", "phone", profile.phone),
                ("Email", "email", profile.email), ("LinkedIn", "linkedin", profile.linkedin), ("GitHub", "github", profile.github),
                ("Status", "employment_status", profile.employment_status), ("CV language", "cv_language", profile.cv_language),
                ("LinkedIn headline", "headline", profile.headline),
            ):
            existing = label(root_identity, name)
            if field not in changed and existing and existing != str(getattr(current.data, field)):
                continue
            root_identity = set_label(root_identity, name, value)
        if "languages" in changed:
            table = r"^[ \t]*\|\s*Language\s*\|[^\n]*\n(?:[ \t]*\|[^\n]*(?:\n|\Z))+"
            if re.search(table, root_identity, re.M):
                root_identity = re.sub(table, lambda _: languages + "\n", root_identity, count=1, flags=re.M)
            else:
                root_identity = root_identity.rstrip() + "\n\n" + languages
        root_changed = {"Identity": True, "Education": "education" in changed,
                        "Professional Experience": "experiences" in changed, "Technical Skills": "skills" in changed,
                        "Behavioral Profile": "behavioral" in changed, "What Excites You": "goals" in changed,
                        "Deal-breakers": "deal_breakers" in changed}
        for title, body in (
            ("Identity", root_identity), ("Education", self.education_markdown(profile)),
            ("Professional Experience", self.experience_markdown(profile).replace("### ", "#### ")),
            ("Technical Skills", "\n".join("- " + inline(s) for s in profile.skills)),
            ("Behavioral Profile", profile.behavioral), ("What Excites You", "\n".join("- " + inline(s) for s in profile.goals)),
            ("Deal-breakers", "\n".join("- " + inline(s) for s in profile.deal_breakers)),
        ):
            if root_changed[title]:
                candidate = replace_section(candidate, title, body, 3)
        changes["CLAUDE.md"] = replace_section(root, "Candidate Profile", candidate)
        changes[BEHAVIOR_PATH] = replace_section(
            self.workspace.text(BEHAVIOR_PATH), "Personal preferences", profile.behavioral,
        )
        changes[STYLE_PATH] = replace_section(
            self.workspace.text(STYLE_PATH), "Personal writing preferences", profile.writing_style,
        )
        from .documents import master_cv
        changes[MASTER_PATH] = master_cv(profile, self.workspace.resources, self.workspace.text(MASTER_PATH), current.data)
        self.workspace.unchanged(expected)
        return {
            "before": current.data.model_dump(mode="json"), "after": profile.model_dump(mode="json"),
            "files": [
                {"path": path, "before": self.workspace.text(path), "after": content}
                for path, content in changes.items() if content != self.workspace.text(path)
            ],
            "expected": expected,
        }

    def apply(self, operation_id: str, proposal: dict) -> None:
        Profile.model_validate(proposal["after"])
        changes = {item["path"]: item["after"].encode("utf-8") for item in proposal["files"]}
        if any(path not in FACT_PATHS for path in changes):
            raise AppError("invalid_proposal", "This proposal contains unsupported profile paths.", 409)
        self.workspace.commit(operation_id, changes, proposal["expected"])

    @staticmethod
    def education_markdown(profile: Profile) -> str:
        body = "| Degree | Period | Institution | Key Topics |\n|--------|--------|-------------|------------|\n"
        return body + "\n".join(
            f"| {inline(item.degree)} | {inline(item.start)} - {inline(item.end)} | "
            f"{inline(item.institution)} | {inline(item.details)} |" for item in profile.education
        )

    @staticmethod
    def experience_markdown(profile: Profile) -> str:
        return "\n\n".join(
            f"### {inline(item.title)} - {inline(item.company)} ({inline(item.start)} - {inline(item.end)})\n"
            + inline(item.location) + "\n" + "\n".join("- " + inline(b) for b in item.bullets)
            for item in profile.experiences
        )

    @staticmethod
    def projects_markdown(profile: Profile) -> str:
        return "\n\n".join(
            f"- **{inline(item.name)}**: {inline(item.description)}\n"
            + "\n".join("  - " + inline(b) for b in item.bullets) for item in profile.projects
        )
