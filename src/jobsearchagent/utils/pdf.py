"""Safe HTML generation and PDF rendering for application artifacts."""

from __future__ import annotations

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


def render_pdf(
    text: str,
    output_path: str | Path,
    title: str = "Application document",
) -> Path:
    """Render artifact text to an A4 PDF using Playwright Chromium."""

    from playwright.sync_api import sync_playwright

    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    html = build_document_html(text, title)

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
