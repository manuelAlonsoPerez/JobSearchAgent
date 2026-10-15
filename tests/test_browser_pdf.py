"""Local-browser tests for Phase 0 Playwright helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobsearchagent.browser import a11y_text, page_to_form, session, upload
from jobsearchagent.utils.pdf import render_pdf


class BrowserTests(unittest.TestCase):
    def test_session_serializes_form_and_uploads_file(self) -> None:
        html = """
        <main>
          <div role="dialog">
            <h2>Additional questions</h2>
            <label for="email">Email address</label>
            <input id="email" type="email" required>
            <label for="years">Years of experience</label>
            <input id="years" type="number">
            <fieldset>
              <legend>Authorized to work?</legend>
              <label><input type="radio" name="authorized" value="yes">Yes</label>
              <label><input type="radio" name="authorized" value="no">No</label>
            </fieldset>
            <label><input id="remote" type="checkbox">Open to remote</label>
            <label for="note">Note</label>
            <textarea id="note"></textarea>
            <label for="resume">Resume</label>
            <input id="resume" type="file">
          </div>
        </main>
        """

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {"LINKEDIN_ACTION_DELAY_MS": "0"}),
            session(profile_dir=Path(temporary) / "profile", headless=True) as context,
        ):
            page = context.pages[0] if context.pages else context.new_page()
            page.set_content(html)

            form = page_to_form(page, "https://www.linkedin.com/jobs/view/123/")
            field_types = {field.id: field.type for field in form.fields}

            self.assertEqual(form.step_label, "Additional questions")
            self.assertEqual(field_types["email"], "email")
            self.assertEqual(field_types["years"], "number")
            self.assertEqual(field_types["authorized"], "radio")
            self.assertEqual(field_types["remote"], "checkbox")
            self.assertEqual(field_types["note"], "textarea")
            self.assertEqual(field_types["resume"], "file")
            self.assertIn("Additional questions", a11y_text(page))

            sample = Path(temporary) / "resume.pdf"
            sample.write_bytes(b"%PDF-1.4 test")
            upload(page, "#resume", sample)
            uploaded_name = page.locator("#resume").evaluate(
                "element => element.files[0].name"
            )
            self.assertEqual(uploaded_name, "resume.pdf")


class PdfTests(unittest.TestCase):
    def test_render_pdf_creates_nonempty_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "cv.pdf"
            result = render_pdf("Manuel Alonso\n\nFrontend Developer", destination, "CV")

            self.assertEqual(result, destination.resolve())
            self.assertTrue(destination.read_bytes().startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
