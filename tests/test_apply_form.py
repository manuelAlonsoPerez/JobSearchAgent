"""Tests for Phase 3 Easy Apply filling (A6 smoke)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobsearchagent.apply.answers import (
    _same_cv_name,
    resolve_cv_path,
    resolve_field,
)
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
        },
        "work_constraints": {
            "based_in": "Oslo, Norway",
            "work_authorization_stated_on_cv": False,
            "available_within_days": 30,
            "will_consider": {"countries": ["Spain"]},
        },
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

    def test_work_auth_and_sponsorship_use_stated_constraints(self) -> None:
        sponsor = resolve_field(
            FormField(
                id="sponsor",
                label=(
                    "Will you now or in the future require sponsorship "
                    "for employment visa status?"
                ),
                type="radio",
                options=["Yes", "No"],
                required=True,
            ),
            profile_id="servicenow-developer",
            profile_config=PROFILE_CONFIG,
            job_location="United Kingdom",
        )
        uk = resolve_field(
            FormField(
                id="uk",
                label="Are you legally authorized to work in United Kingdom?",
                type="radio",
                options=["Yes", "No"],
                required=True,
            ),
            profile_id="servicenow-developer",
            profile_config=PROFILE_CONFIG,
        )
        norway = resolve_field(
            FormField(
                id="no",
                label="Are you legally authorized to work in Norway?",
                type="radio",
                options=["Yes", "No"],
                required=True,
            ),
            profile_id="servicenow-developer",
            profile_config=PROFILE_CONFIG,
        )

        self.assertEqual(sponsor.value, "Yes")
        self.assertEqual(uk.value, "No")
        self.assertEqual(norway.value, "Yes")

    def test_start_within_days_uses_stated_availability(self) -> None:
        start_30 = resolve_field(
            FormField(
                id="start",
                label="Can you start in 30 days or less?",
                type="radio",
                options=[],
                required=True,
            ),
            profile_id="servicenow-developer",
            profile_config=PROFILE_CONFIG,
        )
        start_two_weeks = resolve_field(
            FormField(
                id="weeks",
                label="Are you available to start within 2 weeks?",
                type="radio",
                options=["Yes", "No"],
                required=True,
            ),
            profile_id="servicenow-developer",
            profile_config=PROFILE_CONFIG,
        )
        notice = resolve_field(
            FormField(
                id="notice",
                label="What is your notice period?",
                type="text",
            ),
            profile_id="servicenow-developer",
            profile_config=PROFILE_CONFIG,
        )

        self.assertEqual(start_30.value, "Yes")
        self.assertEqual(start_30.source, "profile")
        self.assertEqual(start_two_weeks.value, "No")
        self.assertEqual(notice.value, "30 days")

    def test_resolve_cv_path_uses_docs_for_the_selected_profile(self) -> None:
        from jobsearchagent.config import SRC_DIR, load_work_profiles

        profiles = load_work_profiles()["work_profiles"]
        path = resolve_cv_path({}, profiles["servicenow-developer"])

        self.assertIsNotNone(path)
        assert path is not None
        self.assertEqual(path.parent, (SRC_DIR / "docs").resolve())
        self.assertEqual(path.name, "Manuel Alonso - Developer ServiceNow.pdf")

    def test_resolve_cv_path_matches_word_order_variants(self) -> None:
        from jobsearchagent.config import SRC_DIR

        self.assertTrue(
            _same_cv_name(
                "Manuel Alonso - ServiceNow Developer.pdf",
                "Manuel Alonso - Developer ServiceNow.pdf",
            )
        )
        self.assertFalse(
            _same_cv_name(
                "Manuel Alonso - ServiceNow Developer.pdf",
                "Manuel Alonso - Full stack developer.pdf",
            )
        )
        path = resolve_cv_path(
            {},
            {"source_cvs": ["Manuel Alonso - ServiceNow Developer.pdf"]},
        )
        self.assertIsNotNone(path)
        assert path is not None
        self.assertEqual(path.name, "Manuel Alonso - Developer ServiceNow.pdf")
        self.assertEqual(path.parent, (SRC_DIR / "docs").resolve())

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

    def test_saved_linkedin_resume_option_resolves_to_local_cv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cv = Path(directory) / "Manuel Alonso - Developer ServiceNow.pdf"
            cv.write_bytes(b"%PDF-1.4 test")
            answer = resolve_field(
                FormField(
                    id="doc",
                    label="PDF Manuel Alonso - ServiceNow Developer.pdf 10/12/2026",
                    type="radio",
                    options=[],
                ),
                profile_id="servicenow-developer",
                profile_config=PROFILE_CONFIG,
                cv_path=cv,
            )
        self.assertEqual(answer.source, "profile")
        self.assertEqual(answer.value, str(cv))


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

    def test_opens_easy_apply_instead_of_page_chrome(self) -> None:
        html = """
        <main>
          <h1>0 notifications</h1>
          <label for="global-search">Search</label>
          <input id="global-search" name="search" type="text">
          <label for="lang">Select language</label>
          <select id="lang" name="language">
            <option>English (English)</option>
            <option>Español (Spanish)</option>
          </select>
          <button type="button" id="page-next">Next</button>
          <button class="jobs-apply-button" id="easy">Easy Apply</button>
          <div id="modal" hidden>
            <h2>Apply to Empiric</h2>
            <label for="email">Email address</label>
            <input id="email" name="email" type="email" required>
            <button type="button" id="review">Review your application</button>
          </div>
        </main>
        <script>
          window.pageNextClicks = 0;
          window.submitClicks = 0;
          const modal = document.getElementById("modal");
          document.getElementById("page-next").onclick = () => {
            window.pageNextClicks += 1;
          };
          document.getElementById("easy").onclick = () => {
            modal.hidden = false;
            document.getElementById("review").onclick = () => {
              modal.innerHTML = `
                <h2>Apply to Empiric</h2>
                <h3>Review your application</h3>
                <button type="button" id="submit">Submit application</button>
              `;
              document.getElementById("submit").onclick = () => {
                window.submitClicks += 1;
              };
            };
          };
        </script>
        """

        logs: list[str] = []
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
                output_fn=logs.append,
            )

            labels = [field.label for field in form.fields]
            self.assertTrue(any("Opening the Easy Apply modal" in line for line in logs))
            self.assertFalse(any(label == "Search" for label in labels))
            self.assertFalse(any("Select language" in label for label in labels))
            self.assertEqual(page.evaluate("window.pageNextClicks"), 0)
            self.assertEqual(page.evaluate("window.submitClicks"), 0)
            email = next(
                answer.value for answer in form.answers if answer.field_id == "email"
            )
            self.assertEqual(email, "nexdevconsulting@outlook.com")

    def test_clicks_upload_resume_on_linkedin_document_picker(self) -> None:
        html = """
        <main>
          <button class="jobs-apply-button" id="easy">Easy Apply</button>
          <div id="modal" hidden>
            <h2>Apply to Empiric</h2>
            <div id="step"></div>
          </div>
        </main>
        <script>
          window.submitClicks = 0;
          window.uploadedName = "";
          const modal = document.getElementById("modal");
          const step = document.getElementById("step");
          const resume = `
            <div class="artdeco-modal__content" style="height:160px;overflow:auto">
              <div style="height:420px">
                <label>
                  <input type="radio" name="urn:li:fsd_resume" value="old-1">
                  PDF Manuel Alonso - Fullstack.pdf 6/1/2026
                </label>
                <label>
                  <input type="radio" name="urn:li:fsd_resume" value="old-2">
                  PDF Manuel Alonso - ServiceNow Developer.pdf 10/12/2026
                </label>
              </div>
              <button type="button" id="upload">Upload resume</button>
              <input id="resume-file" name="file" type="file" accept=".pdf"
                     style="position:absolute;width:1px;height:1px;opacity:0">
            </div>
            <button type="button" id="review">Review your application</button>
          `;
          const review = `
            <h3>Review your application</h3>
            <button type="button" id="submit">Submit application</button>
          `;
          document.getElementById("easy").onclick = () => {
            modal.hidden = false;
            step.innerHTML = resume;
            document.getElementById("resume-file").onchange = (event) => {
              window.uploadedName = event.target.files?.[0]?.name || "";
            };
            document.getElementById("upload").onclick = () => {
              document.getElementById("resume-file").click();
            };
            document.getElementById("review").onclick = () => {
              step.innerHTML = review;
              document.getElementById("submit").onclick = () => {
                window.submitClicks += 1;
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
            cv = Path(temporary) / "Manuel Alonso - Developer ServiceNow.pdf"
            cv.write_bytes(b"%PDF-1.4 test")
            page = context.pages[0] if context.pages else context.new_page()
            page.set_content(html)
            state: JobState = {
                "url": "https://www.linkedin.com/jobs/view/123/",
                "title": "ServiceNow Developer",
                "profile_id": "servicenow-developer",
            }

            build_and_answer_form(
                page,
                state,
                cv_path=cv,
                profile_config=PROFILE_CONFIG,
                output_fn=lambda _: None,
            )

            uploaded = page.evaluate("window.uploadedName")
            self.assertEqual(uploaded, cv.name)
            self.assertEqual(page.evaluate("window.submitClicks"), 0)
            self.assertTrue(
                page.get_by_role("button", name="Submit application").is_visible()
            )

    def test_fills_two_yes_no_radio_groups_in_one_step(self) -> None:
        html = """
        <main>
          <button class="jobs-apply-button">Easy Apply</button>
          <div id="modal" hidden>
            <h2>Apply to Inara</h2>
            <label for="years">How many years of work experience do you have with ServiceNow?</label>
            <input id="years" name="years" type="text">
            <div class="fb-dash-form-element">
              <fieldset>
                <span class="fb-dash-form-element__label">
                  <span>Will you now or in the future require sponsorship for employment visa status?</span>
                  <span>*</span>
                </span>
                <div data-test-text-selectable-option>
                  <input type="radio" name="urn:li:fsd_formElement:(1,2,MULTIPLE_CHOICE)"
                         id="s-yes" value="Yes" class="visually-hidden"
                         style="position:absolute;width:1px;height:1px;opacity:0">
                  <label for="s-yes">Yes</label>
                </div>
                <div data-test-text-selectable-option>
                  <input type="radio" name="urn:li:fsd_formElement:(1,2,MULTIPLE_CHOICE)"
                         id="s-no" value="No" class="visually-hidden"
                         style="position:absolute;width:1px;height:1px;opacity:0">
                  <label for="s-no">No</label>
                </div>
              </fieldset>
            </div>
            <div class="fb-dash-form-element">
              <fieldset>
                <span class="fb-dash-form-element__label">
                  <span>Are you legally authorized to work in United Kingdom?</span>
                  <span>*</span>
                </span>
                <div data-test-text-selectable-option>
                  <input type="radio" name="urn:li:fsd_formElement:(3,4,MULTIPLE_CHOICE)"
                         id="u-yes" value="Yes" class="visually-hidden"
                         style="position:absolute;width:1px;height:1px;opacity:0">
                  <label for="u-yes">Yes</label>
                </div>
                <div data-test-text-selectable-option>
                  <input type="radio" name="urn:li:fsd_formElement:(3,4,MULTIPLE_CHOICE)"
                         id="u-no" value="No" class="visually-hidden"
                         style="position:absolute;width:1px;height:1px;opacity:0">
                  <label for="u-no">No</label>
                </div>
              </fieldset>
            </div>
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
                "location": "United Kingdom",
                "profile_id": "servicenow-developer",
            }

            form = build_and_answer_form(
                page,
                state,
                profile_config=PROFILE_CONFIG,
                output_fn=lambda _: None,
            )

            self.assertTrue(page.locator("#s-yes").is_checked())
            self.assertTrue(page.locator("#u-no").is_checked())
            self.assertFalse(page.locator("#u-yes").is_checked())
            labeled = {
                field.label: next(
                    (
                        answer.value
                        for answer in form.answers
                        if answer.field_id == field.id
                    ),
                    None,
                )
                for field in form.fields
            }
            years = next(
                value for label, value in labeled.items() if "ServiceNow" in label
            )
            sponsor = next(
                value for label, value in labeled.items() if "sponsorship" in label
            )
            uk = next(
                value for label, value in labeled.items() if "United Kingdom" in label
            )
            self.assertEqual(years, "3")
            self.assertEqual(sponsor, "Yes")
            self.assertEqual(uk, "No")

    def test_reads_react_radio_group_ids_as_real_questions(self) -> None:
        html = """
        <main>
          <button class="jobs-apply-button">Easy Apply</button>
          <div id="modal" hidden>
            <h2>Apply to Inara</h2>
            <label for="years">How many years of work experience do you have with ServiceNow?</label>
            <input id="years" name="years" type="text" required>
            <div>
              <label id="_r_35_">Will you now or in the future require sponsorship for employment visa status?</label>
              <div role="radiogroup" aria-labelledby="_r_35_" id="radio-group-_r_36_">
                <input type="radio" name="radio-group-_r_36_" id="s-yes">
                <span>Yes</span>
                <input type="radio" name="radio-group-_r_36_" id="s-no">
                <span>No</span>
              </div>
            </div>
            <div>
              <p>Are you legally authorized to work in United Kingdom?</p>
              <div id="radio-group-_r_3b_">
                <input type="radio" name="radio-group-_r_3b_" id="u-yes">
                <span>Yes</span>
                <input type="radio" name="radio-group-_r_3b_" id="u-no">
                <span>No</span>
              </div>
            </div>
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
                "location": "United Kingdom",
                "profile_id": "servicenow-developer",
            }

            form = build_and_answer_form(
                page,
                state,
                profile_config=PROFILE_CONFIG,
                output_fn=lambda _: None,
            )

            labels = [field.label for field in form.fields]
            self.assertTrue(any("sponsorship" in label for label in labels))
            self.assertTrue(any("United Kingdom" in label for label in labels))
            self.assertFalse(any(label.startswith("radio-group-") for label in labels))
            self.assertTrue(page.locator("#s-yes").is_checked())
            self.assertTrue(page.locator("#u-no").is_checked())

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


class SmokeApplyInputTests(unittest.TestCase):
    DECISION = {
        "url": "https://www.linkedin.com/jobs/view/4481207433/",
        "title": "ServiceNow Engineer | Inara",
        "company": "Unknown company",
        "location": "Unknown location",
        "profile_id": "servicenow-developer",
        "outcome": "accept",
        "fit_score": 1.84,
        "required_artifacts": ["cv", "cover_letter"],
        "apply_status": "dry_run_ready",
        "review_reason": None,
        "details": {
            "profile": {
                "choice": "servicenow-developer",
                "source": "title_and_skills",
            }
        },
    }

    def test_builds_state_from_jev_decision_object(self) -> None:
        from jobsearchagent.apply.smoke_apply import resolve_job

        with patch("jobsearchagent.apply.smoke_apply.get_job", return_value=None):
            url, state = resolve_job(self.DECISION)

        self.assertEqual(url, "https://www.linkedin.com/jobs/view/4481207433/")
        self.assertEqual(state["title"], "ServiceNow Engineer | Inara")
        self.assertEqual(state["profile_id"], "servicenow-developer")
        self.assertEqual(state["outcome"], "accept")
        self.assertEqual(state["source_job_id"], "4481207433")

    def test_reads_jev_json_file(self) -> None:
        from jobsearchagent.apply.smoke_apply import resolve_job

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decision.json"
            path.write_text(json.dumps(self.DECISION), encoding="utf-8")
            with patch("jobsearchagent.apply.smoke_apply.get_job", return_value=None):
                url, state = resolve_job(str(path))

        self.assertEqual(url, "https://www.linkedin.com/jobs/view/4481207433/")
        self.assertEqual(state["profile_id"], "servicenow-developer")

    def test_defaults_to_apply_decision_json(self) -> None:
        from jobsearchagent.apply.smoke_apply import (
            DEFAULT_DECISION_PATH,
            _load_identifier,
        )

        self.assertEqual(DEFAULT_DECISION_PATH.name, "decision.json")
        self.assertEqual(DEFAULT_DECISION_PATH.parent.name, "apply")
        payload = _load_identifier(None, None)
        self.assertEqual(payload["profile_id"], "servicenow-developer")
        self.assertIn("linkedin.com/jobs/view/", str(payload["url"]))

    def test_rejects_json_without_url(self) -> None:
        from jobsearchagent.apply.smoke_apply import parse_decision

        with self.assertRaises(ValueError):
            parse_decision(json.dumps({"outcome": "accept"}))


class _FailIfCalled:
    def choice(self, *args: object, **kwargs: object) -> tuple[str, float]:
        raise AssertionError("JEV must not be asked an ungrounded question")

    def close(self) -> None:
        return None


def _fail_chat(messages: object) -> str:
    raise AssertionError("GEN must not be asked an ungrounded question")


if __name__ == "__main__":
    unittest.main()
