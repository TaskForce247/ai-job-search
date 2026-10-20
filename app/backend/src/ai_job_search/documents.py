from __future__ import annotations

import io
import re
import shutil
from pathlib import Path

from .errors import AppError
from .models import DocumentContent, DraftPair, Profile
from .policy import load_helper
from .processes import command
from .storage import digest

ESCAPES = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&",
    "#": r"\#", "_": r"\_", "%": r"\%", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


def tex(value: str) -> str:
    return "".join(ESCAPES.get(character, character) for character in value if character >= " " or character == "\n")


def cv_preamble(profile: Profile, resources: Path) -> str:
    example = (resources / "cv" / "main_example.tex").read_text(encoding="utf-8")
    preamble = example.split("% personal data", 1)[0].replace("[YOUR_NAME]", tex(profile.name))
    parts = profile.name.rsplit(" ", 1)
    first, last = (parts[0], parts[1]) if len(parts) == 2 else (profile.name, "")
    preamble += "\\usepackage{needspace}\n"
    preamble += f"\\name{{{tex(first)}}}{{{tex(last)}}}\n"
    if profile.location:
        preamble += f"\\address{{{tex(profile.location)}}}{{}}{{}}\n"
    if profile.phone:
        preamble += f"\\phone[mobile]{{{tex(profile.phone)}}}\n"
    if profile.email:
        preamble += f"\\email{{{tex(profile.email)}}}\n"
    links = []
    for title, value in (("LinkedIn", profile.linkedin), ("GitHub", profile.github)):
        if value:
            links.append(tex(title + ": " + value))
    if links:
        preamble += "\\extrainfo{" + " | ".join(links) + "}\n"
    return preamble + "\\begin{document}\n\\makecvtitle\n"


def render_cv(profile: Profile, content: DocumentContent, resources: Path) -> str:
    result = cv_preamble(profile, resources)
    bullets = False
    for block in content.blocks:
        if block.kind != "bullet" and bullets:
            result += "\\end{itemize}\n"
            bullets = False
        if block.kind == "heading":
            result += f"\\needspace{{4\\baselineskip}}\\section{{{tex(block.heading or block.text)}}}\n"
        elif block.kind == "entry":
            details = tex(block.text)
            if block.bullets:
                details += "\\begin{itemize}\n" + "\n".join("\\item " + tex(b.text) for b in block.bullets) + "\n\\end{itemize}"
            dates = " - ".join(value for value in (block.start, block.end) if value)
            result += (
                "\\needspace{5\\baselineskip}\n"
                f"\\cventry{{{tex(dates)}}}{{{tex(block.heading)}}}{{{tex(block.organization)}}}"
                f"{{{tex(block.location)}}}{{}}{{{details}}}\n"
            )
        elif block.kind == "bullet":
            if not bullets:
                result += "\\begin{itemize}\n"
                bullets = True
            result += "\\item " + tex(block.text) + "\n"
        else:
            result += "\\small{" + tex(block.text) + "}\\par\\vspace{4pt}\n"
    if bullets:
        result += "\\end{itemize}\n"
    return result + "\\end{document}\n"


def render_cover(profile: Profile, content: DocumentContent, company: str) -> str:
    result = (
        "\\documentclass[]{cover}\n\\usepackage{fancyhdr}\n"
        "\\pagestyle{fancy}\\fancyhf{}\\renewcommand{\\headrulewidth}{0pt}\n"
        "\\begin{document}\\thispagestyle{empty}\n"
        f"\\namesection{{}}{{\\Huge{{{tex(profile.name)}}}}}{{{tex(profile.email)} | {tex(profile.phone)}}}\n"
        f"\\companyname{{{tex(company)}}}\n\\currentdate{{\\today}}\n"
    )
    bullets = False
    for block in content.blocks:
        if block.kind != "bullet" and bullets:
            result += "\\end{itemize}\\par}\n"
            bullets = False
        if block.kind == "bullet":
            if not bullets:
                result += (
                    "{\\raggedright\\fontspec[Path = OpenFonts/fonts/raleway/]{Raleway-Medium}"
                    "\\fontsize{11pt}{13pt}\\selectfont\\begin{itemize}\n"
                )
                bullets = True
            result += "\\item " + tex(block.text) + "\n"
        elif block.kind == "heading":
            result += "\\lettercontent{\\textbf{" + tex(block.heading or block.text) + "}}\n"
        else:
            result += "\\lettercontent{" + tex(block.text) + "}\n"
    if bullets:
        result += "\\end{itemize}\\par}\n"
    return result + "\\end{document}\n"


def replace_tex_field(preamble: str, name: str, arguments: int, replacement: str) -> str:
    match = re.search(rf"^[ \t]*\\{re.escape(name)}(?![A-Za-z@])(?:\[[^\]\r\n]*\])?\s*", preamble, re.M)
    if not match:
        return preamble.rstrip() + "\n" + replacement + "\n" if replacement else preamble
    position = match.end()
    for _ in range(arguments):
        while position < len(preamble) and preamble[position].isspace():
            position += 1
        if position >= len(preamble) or preamble[position] != "{":
            raise AppError("unsupported_master_cv", "A master-CV contact command has an unsupported format. Review an import before replacing it.", 409)
        depth = 1
        position += 1
        while position < len(preamble) and depth:
            if preamble[position] == "\\":
                position += 2
                continue
            if preamble[position] == "{":
                depth += 1
            elif preamble[position] == "}":
                depth -= 1
            position += 1
        if depth:
            raise AppError("unsupported_master_cv", "A master-CV contact command has unbalanced braces.", 409)
    return preamble[:match.start()] + replacement + preamble[position:]


def master_cv(profile: Profile, resources: Path, original: str = "", previous: Profile | None = None) -> str:
    from .models import Claim, DocumentBlock
    blocks = [DocumentBlock(text=profile.summary)] if profile.summary else []
    for heading, items in (
        ("Core Competencies", [DocumentBlock(kind="bullet", text=skill) for skill in profile.skills]),
        ("Professional Experience", [
            DocumentBlock(kind="entry", heading=role.title, organization=role.company, location=role.location,
                          start=role.start, end=role.end, bullets=[Claim(text=b) for b in role.bullets])
            for role in profile.experiences
        ]),
        ("Education", [
            DocumentBlock(kind="entry", heading=degree.degree, organization=degree.institution,
                          start=degree.start, end=degree.end, text=degree.details) for degree in profile.education
        ]),
        ("Projects", [DocumentBlock(kind="entry", heading=project.name, text=project.description,
                                   bullets=[Claim(text=b) for b in project.bullets]) for project in profile.projects]),
        ("Languages", [DocumentBlock(kind="bullet", text=f"{language.name}: {language.level}") for language in profile.languages]),
    ):
        if items:
            blocks.append(DocumentBlock(kind="heading", text=heading))
            blocks.extend(items)
    rendered = render_cv(profile, DocumentContent(language=profile.cv_language, blocks=blocks), resources)
    if not original or "\\begin{document}" not in original:
        return rendered
    pattern = r"\\section\{([^{}]+)\}(.*?)(?=\\section\{|\\end\{document\}|\Z)"
    template = (resources / "cv" / "main_example.tex").read_text(encoding="utf-8")
    template_sections = {match.group(1): match.group(0).strip() for match in re.finditer(pattern, template, re.S)}
    new_sections = {match.group(1): match.group(0).strip() for match in re.finditer(pattern, rendered, re.S)}
    owned = {"Core Competencies": "skills", "Professional Experience": "experiences",
             "Education": "education", "Projects": "projects", "Languages": "languages"}
    sections = []
    for match in re.finditer(pattern, original, re.S):
        heading, body = match.group(1), match.group(0).strip()
        untouched_template = " ".join(body.split()) == " ".join(template_sections.get(heading, "").split())
        field = owned.get(heading)
        changed = previous is None or (field and getattr(previous, field) != getattr(profile, field))
        replacement = new_sections.pop(heading, "")
        if not untouched_template and (not field or not changed):
            sections.append(body)
        elif field and replacement:
            sections.append(replacement)
    for heading, body in new_sections.items():
        if previous is None or getattr(previous, owned[heading]) != getattr(profile, owned[heading]):
            sections.append(body)
    preamble, old_body = original.split("\\begin{document}", 1)
    generated_body = rendered.split("\\makecvtitle", 1)[1].split("\\section{", 1)[0]
    old_intro = old_body.split("\\makecvtitle", 1)[-1].split("\\section{", 1)[0]
    template_intro = template.split("\\makecvtitle", 1)[1].split("\\section{", 1)[0]
    intro = generated_body
    if (previous and profile.summary == previous.summary
            and " ".join(old_intro.split()) != " ".join(template_intro.split())):
        intro = old_intro
    parts = profile.name.rsplit(" ", 1)
    first, last = (parts[0], parts[1]) if len(parts) == 2 else (profile.name, "")
    for command_name, field, arguments, value in (
        ("name", "name", 2, f"\\name{{{tex(first)}}}{{{tex(last)}}}"),
        ("address", "location", 3, f"\\address{{{tex(profile.location)}}}{{}}{{}}" if profile.location else ""),
        ("phone", "phone", 1, f"\\phone[mobile]{{{tex(profile.phone)}}}" if profile.phone else ""),
        ("email", "email", 1, f"\\email{{{tex(profile.email)}}}" if profile.email else ""),
    ):
        existing = re.search(rf"^[ \t]*\\{command_name}(?![A-Za-z@])[^\n]*", preamble, re.M)
        if (previous and getattr(previous, field) == getattr(profile, field) and existing
                and not re.search(r"\[(?:YOUR_|Your |your\.|\+XX|First|Last|https?://)", existing.group(0))):
            continue
        preamble = replace_tex_field(preamble, command_name, arguments, value)
    if (previous is None or (profile.linkedin, profile.github) != (previous.linkedin, previous.github)
            or "[https://linkedin.com/in/your-profile]" in preamble):
        links = [tex(title + ": " + value) for title, value in (("LinkedIn", profile.linkedin), ("GitHub", profile.github)) if value]
        preamble = replace_tex_field(preamble, "extrainfo", 1, "\\extrainfo{" + " | ".join(links) + "}" if links else "")
    preamble = preamble.replace("[YOUR_NAME]", tex(profile.name))
    if "\\usepackage{needspace}" not in preamble:
        preamble += "\n\\usepackage{needspace}\n"
    return preamble + "\\begin{document}\n\\makecvtitle\n" + intro + "\n".join(sections) + "\n\\end{document}\n"


class DocumentService:
    def __init__(self, resources: Path):
        self.resources = resources
        self.pdf_helper = load_helper(resources, "verify_pdf")
        self.layout_helper = load_helper(resources, "verify_layout")

    @staticmethod
    def evidence_check(pair: DraftPair, valid_ids: set[str]) -> None:
        for kind, content in (("cv", pair.cv), ("cover_letter", pair.cover_letter)):
            if not content.blocks:
                raise AppError("empty_document", f"The {kind} draft has no content.", 422)
            for block in content.blocks:
                if any(evidence not in valid_ids for evidence in block.evidence_ids):
                    raise AppError("invalid_evidence", "A draft references evidence outside its approved snapshot.", 422)
                if block.kind == "entry" and not block.evidence_ids:
                    raise AppError("missing_evidence", "A draft entry has no supporting profile evidence.", 422)
                for bullet in block.bullets:
                    if not bullet.evidence_ids or any(evidence not in valid_ids for evidence in bullet.evidence_ids):
                        raise AppError("invalid_evidence", "An achievement has no valid profile evidence.", 422)

    def compile(self, kind: str, profile: Profile, content: DocumentContent, company: str,
                directory: Path) -> Path:
        engine_name = "lualatex" if kind == "cv" else "xelatex"
        from .readiness import find_tool
        engine = find_tool(engine_name, self.resources)
        if not engine:
            raise AppError("document_tool_missing", f"{engine_name} is not installed. Use Settings > Document tools to install or repair MiKTeX.", 409)
        directory.mkdir(parents=True, exist_ok=True)
        source = render_cv(profile, content, self.resources) if kind == "cv" else render_cover(profile, content, company)
        filename = kind + ".tex"
        (directory / filename).write_text(source, encoding="utf-8")
        if kind == "cover_letter":
            shutil.copyfile(self.resources / "cover_letters" / "cover.cls", directory / "cover.cls")
            font_target = directory / "OpenFonts"
            if not font_target.exists():
                shutil.copytree(self.resources / "cover_letters" / "OpenFonts", font_target)
        environment = {
            "openin_any": "p", "openout_any": "p", "shell_escape": "0",
            "MIKTEX_ENABLE_INSTALLER": "0", "MIKTEX_AUTOINSTALL": "0",
        }
        arguments = [engine, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error",
                     "-file-line-error", filename]
        if "miktex" in str(Path(engine)).lower():
            arguments.insert(1, "--disable-installer")
        for _ in range(2):
            command(arguments, directory, timeout=120, environment=environment)
        pdf = directory / (kind + ".pdf")
        if not pdf.is_file():
            raise AppError("pdf_missing", "The document compiler did not produce a PDF.", 422)
        return pdf

    def verify(self, kind: str, pdf: Path, profile: Profile, requirements: list[dict]) -> tuple[list[dict], str, list[dict]]:
        checks = []
        text = ""
        try:
            required = [value for value in (profile.email, profile.phone) if value]
            extractor, text, pages = self.pdf_helper.verify_pdf(
                pdf, expected_pages=2 if kind == "cv" else 1, min_chars=150, required_text=required,
                ascii_dates=kind == "cv",
            )
            checks.append({"id": "text", "label": "Text, contact details, and page count", "status": "pass",
                           "message": f"{extractor}; {pages} pages; literal contact details present."})
        except self.pdf_helper.VerificationError as exc:
            checks.append({"id": "text", "label": "Text, contact details, and page count", "status": "fail",
                           "message": str(exc)})
        if text:
            garbage = bool(re.search(r"\(cid:\d+\)|\ufffd", text))
            checks.append({"id": "text_garbage", "label": "ATS text encoding", "status": "fail" if garbage else "pass",
                           "message": "Unreadable text markers found." if garbage else "No CID or replacement-character markers."})
        try:
            pages = self.layout_helper.parse_pdf(pdf)
            issues = self.layout_helper.report(pdf, pages)
            checks.append({"id": "layout", "label": "Measured layout", "status": "fail" if issues else "pass",
                           "message": "\n".join(issues) if issues else "Poppler: no measured gaps, thin pages, orphan headings, or footer collisions."})
        except RuntimeError as exc:
            checks.append({"id": "layout", "label": "Measured layout", "status": "skipped",
                           "message": str(exc) + " Use Settings > PDF tools to repair; this is not a passing check."})
        normalized = self.pdf_helper.normalize_text(text).casefold()
        coverage = []
        for requirement in requirements:
            term = requirement["term"]
            found = self.pdf_helper.normalize_text(term).casefold() in normalized
            coverage.append({"term": term, "priority": requirement.get("priority", "required"),
                             "status": "present" if found else "honestly_absent",
                             "note": "" if found else "Not added automatically; only truthful supported terms belong in the CV."})
        return checks, text, coverage

    @staticmethod
    def render_pages(pdf: Path) -> list[bytes]:
        import pypdfium2
        document = pypdfium2.PdfDocument(pdf)
        images = []
        try:
            if len(document) > 3:
                raise AppError("unexpected_pages", "The document exceeds the visual verification page limit.", 422)
            for index in range(len(document)):
                page = document[index]
                bitmap = page.render(scale=1.5)
                try:
                    with bitmap.to_pil() as image, io.BytesIO() as buffer:
                        image.save(buffer, format="PNG")
                        images.append(buffer.getvalue())
                finally:
                    bitmap.close()
                    page.close()
        finally:
            document.close()
        return images

    @staticmethod
    def revision(content: DocumentContent) -> str:
        return digest(content.model_dump(mode="json"))
