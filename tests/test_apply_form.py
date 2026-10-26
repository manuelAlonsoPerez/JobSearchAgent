"""Tests for Phase 3 Easy Apply filling (A6 smoke)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobsearchagent.apply.answers import resolve_cv_path, resolve_field
from jobsearchagent.apply.form import build_and_answer_form
from jobsearchagent.browser import session
from jobsearchagent.models import FormField, JobState


PROFILE_CONFIG = {
    "match_rules": {},
    "shared": {
        "candidate": {
            "name": "Manuel Alonso",
            "based_in": "Oslo, Norway",
            "phone": "+47 451 00 934",
        }
    },
    "work_profiles": {
        "servicenow-developer": {
            "title": "ServiceNow Developer",
            "email": "nexdevconsulting@outlook.com",
            "source_cvs": ["missing.pdf"],
            "years_relevant": 3,
            "skills": [{"name": "ServiceNow", "years": 3}],
            "certifications": ["CSA"],
            "not_claimed": ["Salesforce"],
            "matching_titles": ["ServiceNow Developer"],
        }
    },
}


class AnswerResolutionTests(unittest.TestCase):
    def test_fills_contact_fields_from_the_profile(self) -> None:
        email = resolve_field(
            FormField(id="email", label="Email address", type="email"),
            profile_id="servicenow-developer",
            profile_config=PROFILE_CONFIG,
        )
        phone = resolve_field(
            FormField(id="phone", label="Mobile phone number", type="text"),
            profile_id="servicenow-developer",
            profile_config=PROFILE_CONFIG,
        )
        country = resolve_field(
            FormField(
                id="code",
                label="Phone country code",
                type="select",
                options=["United States (+1)", "Norway (+47)"],
            ),
            profile_id="servicenow-developer",
            profile_config=PROFILE_CONFIG,
        )

        self.assertEqual(email.value, "nexdevconsulting@outlook.com")
        self.assertEqual(email.source, "profile")
        self.assertEqual(phone.value, "451 00 934")
        self.assertEqual(country.value, "Norway (+47)")

    def test_uploads_existing_cv_and_skips_cover_letter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cv = Path(directory) / "cv.pdf"
            cv.write_bytes(b"%PDF-1.4 test")
            resume = resolve_field(
                FormField(id="resume", label="Resume", type="file"),
                profile_id="servicenow-developer",
                profile_config=PROFILE_CONFIG,
                cv_path=cv,
            )
            letter = resolve_field(
                FormField(id="letter", label="Cover letter", type="file"),
                profile_id="servicenow-developer",
                profile_config=PROFILE_CONFIG,
                cv_path=cv,
            )

        self.assertEqual(resume.source, "profile")
        self.assertTrue(str(resume.value).endswith("cv.pdf"))
        self.assertIsNone(letter.value)
        self.assertEqual(letter.source, "deferred")

    def test_unknown_screening_and_cefr_are_never_guessed(self) -> None:
        color = resolve_field(
            FormField(
                id="color",
                label="What is your favorite color?",
                type="text",
                required=True,
            ),
            profile_id="servicenow-developer",
            profile_config=PROFILE_CONFIG,
        )
        cefr = resolve_field(
            FormField(
                id="english",
                label="English CEFR grade",
                type="text",
                required=True,
            ),
            profile_id="servicenow-developer",
            profile_config=PROFILE_CONFIG,
        )
        residence = resolve_field(
            FormField(
                id="live",
                label="Do you have Spanish residence?",
                type="radio",
                options=["Yes", "No"],
                required=True,
            ),
            profile_id="servicenow-developer",
            profile_config=PROFILE_CONFIG,
        )

        self.assertEqual(color.source, "deferred")
        self.assertIsNone(color.value)
        self.assertEqual(cefr.source, "deferred")
        self.assertEqual(residence.source, "deferred")

    def test_years_and_yes_no_use_profile_skills(self) -> None:
        years = resolve_field(
            FormField(
                id="sn-years",
                label="How many years of ServiceNow experience do you have?",
                type="number",
            ),
            profile_id="servicenow-developer",
            profile_config=PROFILE_CONFIG,
        )
        certified = resolve_field(
            FormField(
                id="csa",
                label="Do you have a CSA certification?",
                type="radio",
                options=["Yes", "No"],
            ),
            profile_id="servicenow-developer",
            profile_config=PROFILE_CONFIG,
        )
        salesforce = resolve_field(
            FormField(
                id="sf",
                label="Do you have Salesforce experience?",
                type="radio",
                options=["Yes", "No"],
            ),
            profile_id="servicenow-developer",
            profile_config=PROFILE_CONFIG,
        )

        self.assertEqual(years.value, "3")
        self.assertEqual(certified.value, "Yes")
        self.assertEqual(salesforce.value, "No")

    def test_resolve_cv_path_prefers_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cv = Path(directory) / "override.pdf"
            cv.write_bytes(b"%PDF-1.4 test")
            path = resolve_cv_path(
                {},
                PROFILE_CONFIG["work_profiles"]["servicenow-developer"],
                override=cv,
            )
        self.assertEqual(path, cv.resolve())


class ApplyModalTests(unittest.TestCase):
    def test_fills_contact_and_cv_and_never_clicks_submit(self) -> None:
        html = """
        <main>
          <button class="jobs-apply-button" id="easy">Easy Apply</button>
          <div id="modal" hidden>
            <h2>Apply to Example</h2>
            <div id="step"></div>
          </div>
        </main>
        <script>
          window.submitClicks = 0;
          const step = document.getElementById("step");
          const modal = document.getElementById("modal");
          const contact = `
            <label for="email">Email address</label>
            <input id="email" name="email" type="email" required>
            <label for="phone">Mobile phone number</label>
            <input id="phone" name="phone" type="text" required>
            <button type="button" id="next">Next</button>
          `;
          const resume = `
            <label for="resume">Resume</label>
            <input id="resume" name="resume" type="file">
            <button type="button" id="review">Review your application</button>
          `;
          const review = `
            <h3>Review your application</h3>
            <button type="button" id="submit">Submit application</button>
          `;
          document.getElementById("easy").onclick = () => {
            modal.hidden = false;
            step.innerHTML = contact;
            document.getElementById("next").onclick = () => {
              step.innerHTML = resume;
              document.getElementById("review").onclick = () => {
                step.innerHTML = review;
                document.getElementById("submit").onclick = () => {
                  window.submitClicks += 1;
                };
              };
            };
          };
        </script>
        """

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {"LINKEDIN_ACTION_DELAY_MS": "0"}),
            session(
                profile_dir=Path(temporary) / "profile",
                headless=True,
            ) as context,
        ):
            cv = Path(temporary) / "Manuel.pdf"
            cv.write_bytes(b"%PDF-1.4 test")
            page = context.pages[0] if context.pages else context.new_page()
            page.set_content(html)
            state: JobState = {
                "url": "https://www.linkedin.com/jobs/view/123/",
                "title": "ServiceNow Developer",
                "profile_id": "servicenow-developer",
            }

            form = build_and_answer_form(
                page,
                state,
                cv_path=cv,
                profile_config=PROFILE_CONFIG,
                output_fn=lambda _: None,
            )

            self.assertEqual(page.evaluate("window.submitClicks"), 0)
            self.assertTrue(page.get_by_role("button", name="Submit application").is_visible())
            self.assertEqual(state.get("apply_status"), "dry_run_ready")
            values = {answer.field_id: answer.value for answer in form.answers}
            sources = {answer.field_id: answer.source for answer in form.answers}
            self.assertEqual(values.get("email"), "nexdevconsulting@outlook.com")
            self.assertEqual(sources.get("email"), "profile")
            self.assertEqual(sources.get("resume"), "profile")
            self.assertTrue(str(values.get("resume") or "").endswith("Manuel.pdf"))

    def test_planted_unknown_question_is_deferred(self) -> None:
        html = """
        <main>
          <button class="jobs-apply-button">Easy Apply</button>
          <div id="modal" hidden>
            <h2>Apply to Example</h2>
            <label for="color">What is your favorite color?</label>
            <input id="color" name="color" type="text" required>
            <button type="button">Review your application</button>
          </div>
          <script>
            document.querySelector(".jobs-apply-button").onclick = () => {
              document.getElementById("modal").hidden = false;
            };
          </script>
        </main>
        """

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {"LINKEDIN_ACTION_DELAY_MS": "0"}),
            session(
                profile_dir=Path(temporary) / "profile",
                headless=True,
            ) as context,
        ):
            page = context.pages[0] if context.pages else context.new_page()
            page.set_content(html)
            state: JobState = {
                "url": "https://www.linkedin.com/jobs/view/123/",
                "title": "ServiceNow Developer",
                "profile_id": "servicenow-developer",
            }

            form = build_and_answer_form(
                page,
                state,
                profile_config=PROFILE_CONFIG,
                jev=_FailIfCalled(),
                chat_fn=_fail_chat,
                output_fn=lambda _: None,
            )

            self.assertEqual(page.locator("#color").input_value(), "")
            color = next(answer for answer in form.answers if answer.field_id == "color")
            self.assertEqual(color.source, "deferred")
            self.assertIsNone(color.value)
            self.assertIn("favorite color", state.get("review_reason", "").lower())


class _FailIfCalled:
    def choice(self, *args: object, **kwargs: object) -> tuple[str, float]:
        raise AssertionError("JEV must not be asked an ungrounded question")

    def close(self) -> None:
        return None


def _fail_chat(messages: object) -> str:
    raise AssertionError("GEN must not be asked an ungrounded question")


if __name__ == "__main__":
    unittest.main()
