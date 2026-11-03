"""T2: HTML Canva clones rendered to PDF with Playwright."""

from __future__ import annotations

import base64
from html import escape
from pathlib import Path

from ..utils.pdf import render_html_pdf
from .fields import CvFields
from .source import TemplateId

Locale = str

_LABELS = {
    "en": {
        "html_lang": "en",
        "contact": "Contact",
        "skills": "Skills",
        "languages": "Languages",
        "education": "Education",
        "profile": "Profile",
        "experience": "Work Experience",
    },
    "es": {
        "html_lang": "es",
        "contact": "Contacto",
        "skills": "Competencias",
        "languages": "Idiomas",
        "education": "Formación",
        "profile": "Perfil",
        "experience": "Experiencia laboral",
    },
}

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_PORTRAIT = TEMPLATES_DIR / "portrait.png"
_CSS = TEMPLATES_DIR / "cv.css"

_ICON_LINKEDIN = (
    '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">'
    '<circle cx="12" cy="12" r="12" fill="#5e7c7d"/>'
    '<path fill="#fff" d="M7.3 9.4h2.3v7.7H7.3zM8.4 6.2a1.35 1.35 0 1 1 0 '
    "2.7 1.35 1.35 0 0 1 0-2.7zM12 9.4h2.2v1.05h.03c.3-.55 1.05-1.13 "
    "2.16-1.13 2.31 0 2.74 1.52 2.74 3.5v4.28h-2.3v-3.8c0-.9-.02-2.07-1.26-2.07"
    '-1.26 0-1.45.98-1.45 2v3.87H12z"/></svg>'
)
_ICON_PHONE = (
    '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">'
    '<path fill="#5e7c7d" d="M6.6 3.8h2.3l1.1 3-1.6 1.1a12.6 12.6 0 0 0 5.7 '
    '5.7l1.1-1.6 3 1.1v2.3c0 .7-.6 1.3-1.3 1.3C10.3 16.7 3.8 10.2 3.8 5.1c0'
    '-.7.6-1.3 1.3-1.3z"/></svg>'
)
_ICON_EMAIL = (
    '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">'
    '<path fill="none" stroke="#5e7c7d" stroke-width="1.8" '
    'd="M4 7.2h16v10H4z"/><path fill="none" stroke="#5e7c7d" stroke-width="1.8" '
    'd="M4.4 7.6 12 13l7.6-5.4"/></svg>'
)
_ICON_PIN = (
    '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">'
    '<path fill="#5e7c7d" d="M12 2.8c3.1 0 5.6 2.4 5.6 5.5 0 4.1-5.6 12-5.6 '
    '12S6.4 12.4 6.4 8.3C6.4 5.2 8.9 2.8 12 2.8zm0 7.4a1.9 1.9 0 1 0 0-3.8 '
    '1.9 1.9 0 0 0 0 3.8z"/></svg>'
)


def render_cv_html(
    template_id: TemplateId,
    fields: CvFields,
    *,
    locale: Locale = "en",
) -> str:
    """Fill the Canva-style HTML template with structured fields only."""

    labels = _LABELS.get(locale) or _LABELS["en"]
    css = _CSS.read_text(encoding="utf-8")
    photo = _portrait_data_uri()
    headline = escape(fields.headline.upper())
    summary = "".join(
        f"<p>{escape(paragraph)}</p>"
        for paragraph in fields.summary.split("\n\n")
        if paragraph.strip()
    )
    return f"""<!doctype html>
<html lang="{escape(labels["html_lang"])}">
<head>
  <meta charset="utf-8">
  <title>{escape(fields.name)} — {escape(fields.headline)}</title>
  <style>{css}</style>
</head>
<body class="template-{escape(template_id)}">
  <div class="hero">
    <div class="hero-left"></div>
    <div class="hero-right"><h1>{escape(fields.name.upper())}</h1></div>
    <div class="photo"><img src="{photo}" alt=""></div>
    <div class="title-bar">{headline}</div>
  </div>
  <div class="page">
    <aside class="sidebar">
      {_contact_html(fields, labels["contact"])}
      {_list_section(labels["skills"], fields.skills)}
      {_list_section(labels["languages"], fields.languages)}
      {_education_html(fields, labels["education"])}
    </aside>
    <main class="main">
      <h2>{escape(labels["profile"])}</h2>
      <div class="summary">{summary}</div>
      <h2>{escape(labels["experience"])}</h2>
      <div class="timeline">
        {''.join(_job_html(job) for job in fields.experience)}
      </div>
    </main>
  </div>
</body>
</html>
"""


def render_cv_pdf(
    template_id: TemplateId,
    fields: CvFields,
    output_path: str | Path,
    *,
    locale: Locale = "en",
) -> Path:
    """Render structured CV fields to an A4 PDF."""

    return render_html_pdf(
        render_cv_html(template_id, fields, locale=locale), output_path
    )


def _portrait_data_uri() -> str:
    payload = base64.b64encode(_PORTRAIT.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{payload}"


def _contact_html(fields: CvFields, heading: str = "Contact") -> str:
    contact = fields.contact
    linkedin = escape(contact.linkedin_label or "Linkedin")
    if contact.linkedin_url:
        linkedin = (
            f'<a href="{escape(contact.linkedin_url, quote=True)}">{linkedin}</a>'
        )
    location = escape(contact.street)
    if contact.city:
        location = f"{location}<br>{escape(contact.city)}" if location else escape(contact.city)
    rows = [
        (_ICON_LINKEDIN, linkedin),
        (_ICON_PHONE, escape(contact.phone)),
        (_ICON_EMAIL, escape(contact.email)),
        (_ICON_PIN, location),
    ]
    body = "".join(
        f'<div class="contact-row">{icon}<div>{text}</div></div>'
        for icon, text in rows
        if text
    )
    return f'<h2 class="side-heading">{escape(heading)}</h2>{body}'


def _list_section(title: str, items: list[str]) -> str:
    if not items:
        return ""
    bullets = "".join(f"<li>{escape(item)}</li>" for item in items)
    return f'<h2 class="side-heading">{escape(title)}</h2><ul>{bullets}</ul>'


def _education_html(fields: CvFields, heading: str = "Education") -> str:
    if not fields.education:
        return ""
    blocks: list[str] = []
    for item in fields.education:
        details = "".join(f"<li>{escape(detail)}</li>" for detail in item.details)
        blocks.append(
            "<div class='edu'>"
            f"<div class='edu-years'>{escape(item.years)}</div>"
            f"<div class='edu-school'>{escape(item.school)}</div>"
            f"<ul>{details}</ul>"
            "</div>"
        )
    return (
        f'<div class="education"><h2 class="side-heading">{escape(heading)}</h2>'
        + "".join(blocks)
        + "</div>"
    )


def _job_html(job: object) -> str:
    organization = escape(getattr(job, "organization", ""))
    period = escape(getattr(job, "period", ""))
    title = escape(getattr(job, "title", ""))
    bullets = "".join(
        f"<li>{escape(str(bullet))}</li>" for bullet in getattr(job, "bullets", [])
    )
    return (
        '<article class="job">'
        '<div class="job-head">'
        f'<div class="job-org">{organization}</div>'
        f'<div class="job-period">{period}</div>'
        "</div>"
        f'<div class="job-title">{title}</div>'
        f"<ul>{bullets}</ul>"
        "</article>"
    )
