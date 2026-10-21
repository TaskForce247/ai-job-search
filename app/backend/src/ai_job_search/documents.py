from __future__ import annotations

import io
import re
import shutil
from pathlib import Path
from typing import NotRequired, TypedDict

from .errors import AppError
from .models import DocumentBlock, DocumentContent, DocumentIssue, DraftPair, Profile
from .policy import load_helper
from .processes import command
from .runtime import SETTINGS_ACTION, _failure
from .storage import digest
from .tex import (
    QUIET_TEX_ENVIRONMENT,
    first_error,
    log_tail,
    missing_from_log,
    missing_tex_files,
    package_for,
)
from .tex import clear_cache as clear_tex_cache

COMPILE_FIRST_TIMEOUT = 300
COMPILE_TIMEOUT = 120

ESCAPES = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&",
    "#": r"\#", "_": r"\_", "%": r"\%", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


def tex(value: str) -> str:
    return "".join(ESCAPES.get(character, character) for character in value if character >= " " or character == "\n")


def tex_text(value: str) -> str:
    return "\\par\n".join(
        "\\newline{} ".join(tex(line) for line in paragraph.split("\n"))
        for paragraph in re.split(r"\n[ \t]*\n", value.replace("\r\n", "\n").strip())
    )


def block_body(block: DocumentBlock, *, include_heading: bool = True, include_text: bool = True) -> str:
    parts = []
    if block.heading and include_heading:
        parts.append("\\textbf{" + tex_text(block.heading) + "}")
    for value in (
        " | ".join(value for value in (block.organization, block.location) if value),
        " - ".join(value for value in (block.start, block.end) if value),
        block.text if include_text else "",
    ):
        if value:
            parts.append(tex_text(value))
    return "\\newline{} ".join(parts)


def tex_bullets(block: DocumentBlock) -> str:
    if not block.bullets:
        return ""
    return (
        "\\begin{itemize}\n"
        + "\n".join("\\item {" + tex_text(bullet.text) + "}" for bullet in block.bullets)
        + "\n\\end{itemize}\n"
    )


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
            body = block_body(block, include_heading=False, include_text=bool(block.heading and block.text != block.heading))
            if body:
                result += body + "\\par\n"
            result += tex_bullets(block)
        elif block.kind == "entry":
            details = tex_text(block.text) + tex_bullets(block)
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
            result += "\\item {" + block_body(block) + tex_bullets(block) + "}\n"
        else:
            result += "{\\small " + block_body(block) + "\\par}\n" + tex_bullets(block) + "\\vspace{4pt}\n"
    if bullets:
        result += "\\end{itemize}\n"
    return result + "\\end{document}\n"


def render_cover(profile: Profile, content: DocumentContent, company: str) -> str:
    result = (
        "\\documentclass[a4paper]{cover}\n\\usepackage{fancyhdr}\n"
        "\\setlength{\\parindent}{0pt}\n"
        "\\pagestyle{fancy}\\fancyhf{}\\renewcommand{\\headrulewidth}{0pt}\n"
        "\\begin{document}\\thispagestyle{empty}\n"
        f"\\namesection{{}}{{\\Huge{{{tex(profile.name)}}}}}{{{tex(profile.email)} | {tex(profile.phone)}}}\n"
        f"\\companyname{{{tex(company)}}}\n\\currentdate{{\\today}}\n"
    )
    bullets = False
    list_style = (
        "{\\raggedright\\fontspec[Path = OpenFonts/fonts/raleway/]{Raleway-Medium}"
        "\\fontsize{11pt}{13pt}\\selectfont"
    )
    for block in content.blocks:
        if block.kind != "bullet" and bullets:
            result += "\\end{itemize}\\par}\n"
            bullets = False
        if block.kind == "bullet":
            if not bullets:
                result += list_style + "\\begin{itemize}\n"
                bullets = True
            result += "\\item {" + block_body(block) + tex_bullets(block) + "}\n"
        elif block.kind == "heading":
            body = "\\textbf{" + tex_text(block.heading or block.text) + "}"
            details = block_body(block, include_heading=False, include_text=bool(block.heading and block.text != block.heading))
            if details:
                body += "\\newline{} " + details
            result += "\\lettercontent{" + body + "}\n"
        else:
            body = block_body(block)
            if body:
                result += "\\lettercontent{" + body + "}\n"
        if block.kind != "bullet" and block.bullets:
            result += list_style + tex_bullets(block) + "\\par}\n"
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


class EvidenceFinding(TypedDict):
    document: str
    code: str
    message: str
    block_id: NotRequired[str]
    block_index: NotRequired[int]
    claim_id: NotRequired[str]


class DocumentService:
    def __init__(self, resources: Path):
        self.resources = resources
        self.pdf_helper = load_helper(resources, "verify_pdf")
        self.layout_helper = load_helper(resources, "verify_layout")

    @staticmethod
    def completeness_findings(pair: DraftPair, previous: DraftPair | None = None) -> list[DocumentIssue]:
        findings = []
        for kind in ("cv", "cover_letter"):
            content = getattr(pair, kind)

            def body_text(document: DocumentContent) -> str:
                return " ".join(
                    " ".join([block.text, *(bullet.text for bullet in block.bullets)]).strip()
                    for block in document.blocks if block.kind != "heading"
                ).strip()

            body = body_text(content)
            substantive = any(
                block.kind in (("entry", "bullet") if kind == "cv" else ("paragraph", "bullet"))
                and len(" ".join([block.text, *(bullet.text for bullet in block.bullets)]).strip()) >= 40
                for block in content.blocks
            )
            if not substantive:
                findings.append(DocumentIssue(
                    document=kind,
                    message=f"The {kind} is incomplete: substantive candidate content is missing. "
                            "Headings, greetings, references and sign-offs alone are not an application.",
                    suggestion="Restore the supported experience and role-specific body from the full draft.",
                ))
            elif previous and len(body) < len(body_text(getattr(previous, kind))) / 2:
                # Large deletions require human review even when the remaining claims are true.
                findings.append(DocumentIssue(
                    document=kind,
                    message=f"The proposed {kind} revision removes more than half of the draft's body text. "
                            "The last substantive draft has been retained for review.",
                    suggestion="Correct unsupported wording without deleting the supported application content.",
                ))
        return findings

    @staticmethod
    def evidence_findings(pair: DraftPair, valid_ids: set[str]) -> list[EvidenceFinding]:
        findings: list[EvidenceFinding] = []
        for kind, content in (("cv", pair.cv), ("cover_letter", pair.cover_letter)):
            if not content.blocks:
                findings.append({"document": kind, "code": "empty_document",
                                 "message": f"The {kind} draft has no content."})
            for index, block in enumerate(content.blocks):
                location = {"document": kind, "block_id": block.id, "block_index": index}
                if any(evidence not in valid_ids for evidence in block.evidence_ids):
                    findings.append({**location, "code": "invalid_evidence",
                                     "message": "Replace references outside the approved evidence snapshot with genuine supporting evidence, or remove unsupported claims."})
                if block.kind in ("entry", "bullet") and not block.evidence_ids:
                    findings.append({**location, "code": "missing_evidence",
                                     "message": f"This {block.kind} needs supporting evidence IDs from the approved snapshot."})
                for bullet in block.bullets:
                    if not bullet.evidence_ids:
                        findings.append({**location, "claim_id": bullet.id, "code": "missing_evidence",
                                         "message": "This achievement needs supporting evidence IDs from the approved snapshot."})
                    elif any(evidence not in valid_ids for evidence in bullet.evidence_ids):
                        findings.append({**location, "claim_id": bullet.id, "code": "invalid_evidence",
                                         "message": "This achievement references evidence outside the approved snapshot; cite genuine support or remove the unsupported claim."})
        return findings

    @staticmethod
    def evidence_check(pair: DraftPair, valid_ids: set[str]) -> None:
        findings = DocumentService.evidence_findings(pair, valid_ids)
        if findings:
            raise AppError(findings[0]["code"], findings[0]["message"], 422, detail={"findings": findings})

    def compile(self, kind: str, profile: Profile, content: DocumentContent, company: str,
                directory: Path, *, draft: bool = False) -> Path:
        engine_name = "lualatex" if kind == "cv" else "xelatex"
        from .readiness import find_tool
        engine = find_tool(engine_name, self.resources)
        if not engine:
            raise AppError("document_tool_missing", f"{engine_name} is not installed. Use Settings > Document tools to install or repair MiKTeX.", 409)
        directory.mkdir(parents=True, exist_ok=True)
        source = render_cv(profile, content, self.resources) if kind == "cv" else render_cover(profile, content, company)
        if draft:
            source = source.replace("\\begin{document}", (
                "\\AddToHook{shipout/foreground}{\\put(0,-14){\\makebox[\\paperwidth]{"
                "\\normalfont\\scriptsize UNVERIFIED DRAFT - factual and layout checks not completed}}}\n"
                "\\begin{document}"
            ), 1)
        filename = kind + ".tex"
        (directory / filename).write_text(source, encoding="utf-8")
        if kind == "cover_letter":
            shutil.copyfile(self.resources / "cover_letters" / "cover.cls", directory / "cover.cls")
            font_target = directory / "OpenFonts"
            if not font_target.exists():
                shutil.copytree(self.resources / "cover_letters" / "OpenFonts", font_target)
        environment = {
            "openin_any": "p", "openout_any": "p", "shell_escape": "0", **QUIET_TEX_ENVIRONMENT,
        }
        arguments = [engine, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error",
                     "-file-line-error", filename]
        if "miktex" in str(Path(engine)).lower():
            arguments.insert(1, "--disable-installer")
        label = "CV" if kind == "cv" else "cover letter"
        missing = missing_tex_files(self.resources)
        if missing:
            raise self._missing_packages(engine_name, label, missing)
        # The first pass may build MiKTeX's file-name database, formats, and font caches on a fresh install.
        for timeout in (COMPILE_FIRST_TIMEOUT, COMPILE_TIMEOUT):
            try:
                command(arguments, directory, timeout=timeout, environment=environment)
            except AppError as exc:
                raise self._compile_error(exc, engine_name, label, kind, directory) from exc
        pdf = directory / (kind + ".pdf")
        if not pdf.is_file():
            raise AppError("pdf_missing", "The document compiler did not produce a PDF.", 422)
        return pdf

    @staticmethod
    def _missing_packages(engine: str, label: str, files: list[str], tail: str = "") -> AppError:
        packages = list(dict.fromkeys(package_for(name) for name in files))
        names = ", ".join(packages)
        message = (f"{engine} cannot compile the {label} because required TeX package(s) are missing: {names}. "
                   "Open Settings > Document tools, choose Repair, then retry.")
        return AppError("tex_missing_package", message, 409, detail=_failure(
            "tex_missing_package", "A TeX package is missing",
            f"{engine} needs {', '.join(files)} for the {label}, provided by the TeX package(s) {names}, "
            "which are not installed. Packages are never installed silently during compilation.",
            "Open Settings > Document tools and choose Repair to install the missing packages, "
            "or install them with your TeX distribution's package manager. Then reopen the draft and resume checks.",
            action=SETTINGS_ACTION, tool=engine, missing_files=files, packages=packages, log_tail=tail,
        ))

    def _compile_error(self, exc: AppError, engine: str, label: str, kind: str, directory: Path) -> AppError:
        if exc.code not in ("tool_failed", "process_timeout"):
            return exc
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        output = str(detail.get("output", ""))
        tail = log_tail(directory, kind)
        missing_file = missing_from_log(tail + "\n" + output)
        if missing_file:
            clear_tex_cache()
            return self._missing_packages(engine, label, [missing_file], tail[-2000:])
        if exc.code == "process_timeout":
            seconds = detail.get("timeout_seconds")
            return AppError("tex_timeout", f"{engine} did not finish compiling the {label} within {seconds} seconds and was stopped.",
                            504, detail=_failure(
                                "tex_timeout", "Document compilation timed out",
                                f"{engine} was still running after {seconds} seconds while compiling the {label}. "
                                "This usually means the TeX installation is incomplete or is waiting for an "
                                "install prompt, or a first-run cache build is very slow.",
                                "Open Settings > Document tools and choose Repair, which installs missing packages and "
                                "prepares TeX caches. Then reopen the draft and resume checks.",
                                action=SETTINGS_ACTION, tool=engine, timeout_seconds=seconds,
                                last_error=first_error(tail) or first_error(output),
                                log_tail=(tail or output)[-2000:],
                            ))
        reason = first_error(tail) or first_error(output)
        return AppError("tool_failed", f"{engine} could not compile the {label}" + (f": {reason}" if reason else "."), 422,
                        detail={**detail, "last_error": reason})

    def content_check(self, content: DocumentContent, text: str) -> dict:
        def normalized(value: str) -> str:
            value = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", value)
            return re.sub(r"\s+", "", self.pdf_helper.normalize_text(value).casefold().replace("\u00ad", "").replace("-", ""))

        extracted = normalized(text)
        missing = []
        for index, block in enumerate(content.blocks, 1):
            values = [block.heading, block.organization, block.location, block.start, block.end,
                      block.text, *(bullet.text for bullet in block.bullets)]
            if any(normalized(value) not in extracted for value in values if value.strip()):
                missing.append(str(index))
        return {
            "id": "content", "label": "Saved draft content in PDF", "status": "fail" if missing else "pass",
            "message": "Text is missing from the PDF for content block(s): " + ", ".join(missing) + "." if missing
                       else "All saved headings, fields, body text and achievement bullets are present in the PDF text layer.",
        }

    def verify(self, kind: str, pdf: Path, profile: Profile, requirements: list[dict],
               content: DocumentContent | None = None) -> tuple[list[dict], str, list[dict]]:
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
        if content is not None:
            checks.append(self.content_check(content, text))
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
