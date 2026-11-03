"""Safe HTML generation, PDF text extraction, and PDF rendering."""

from __future__ import annotations

import re
from html import escape
from pathlib import Path


def build_document_html(text: str, title: str = "Application document") -> str:
    """Convert plain text to a small, print-ready HTML document."""

    escaped_title = escape(title.strip() or "Application document")
    paragraphs = [
        f"<p>{escape(paragraph.strip()).replace(chr(10), '<br>')}</p>"
        for paragraph in text.replace("\r\n", "\n").split("\n\n")
        if paragraph.strip()
    ]
    body = "\n".join(paragraphs)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escaped_title}</title>
  <style>
    @page {{ size: A4; margin: 16mm; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: #1f2937;
      font-family: Arial, Helvetica, sans-serif;
      font-size: 10.5pt;
      line-height: 1.45;
    }}
    h1 {{ margin: 0 0 12pt; font-size: 18pt; color: #111827; }}
    p {{ margin: 0 0 8pt; white-space: normal; }}
  </style>
</head>
<body>
  <h1>{escaped_title}</h1>
  {body}
</body>
</html>
"""


def collapse_letter_spaced_text(text: str) -> str:
    """Join Canva-style letter-spaced PDF text ('M A N U E L') into words."""

    collapsed: list[str] = []
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = _collapse_letter_spaced_line(raw_line)
        if line:
            collapsed.append(line)
        elif collapsed and collapsed[-1] != "":
            collapsed.append("")
    return "\n".join(collapsed).strip()


def _collapse_letter_spaced_line(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    parts = stripped.split(" ")
    nonempty = [part for part in parts if part]
    singles = sum(1 for part in nonempty if len(part) == 1)
    if nonempty and singles / len(nonempty) >= 0.55:
        words = re.split(r" {2,}", stripped)
        return " ".join("".join(word.split(" ")) for word in words)
    return stripped


def extract_pdf_text(path: str | Path) -> str:
    """Extract plain text from a local PDF (pypdf), collapsing letter-spacing."""

    from pypdf import PdfReader

    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"PDF not found: {source}")
    reader = PdfReader(str(source))
    pages = [
        collapse_letter_spaced_text(page.extract_text() or "")
        for page in reader.pages
    ]
    return "\n\n".join(page for page in pages if page.strip())


def render_html_pdf(html: str, output_path: str | Path) -> Path:
    """Render an HTML document to an A4 PDF using Playwright Chromium."""

    from playwright.sync_api import sync_playwright

    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            page.pdf(
                path=str(destination),
                format="A4",
                print_background=True,
                margin={
                    "top": "0",
                    "right": "0",
                    "bottom": "0",
                    "left": "0",
                },
            )
        finally:
            browser.close()
    return destination


def render_pdf(
    text: str,
    output_path: str | Path,
    title: str = "Application document",
) -> Path:
    """Render artifact text to an A4 PDF using Playwright Chromium."""

    return render_html_pdf(build_document_html(text, title), output_path)
