"""Standalone tailored-CV agent (guide parts T1–T5)."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from jobsearchagent.config import SRC_DIR
from jobsearchagent.models import JobState
from jobsearchagent.preparation.fields import (
    ExperienceRole,
    baseline_fields,
    initial_fields,
)
from jobsearchagent.preparation.render import render_cv_html, render_cv_pdf
from jobsearchagent.preparation.rewrite import (
    finish_tailored_cv,
    headline_from_offer,
    restore_source_length,
    rewrite_cv,
)
from jobsearchagent.preparation.source import resolve_source_cv, template_id_from_filename
from jobsearchagent.preparation.tailor_validate import extra_grounded_skills, validate_cv_fields
from jobsearchagent.tailor_cv import main, tailored_filename, tailored_output_path
from jobsearchagent.utils.pdf import collapse_letter_spaced_text, extract_pdf_text


def _job(**overrides: object) -> JobState:
    state: JobState = {
        "url": "https://www.linkedin.com/jobs/view/1234567890/",
        "source_job_id": "1234567890",
        "title": "ServiceNow Developer",
        "company": "Example / Corp",
        "location": "Spain",
        "site": "LinkedIn Jobs",
        "raw_description": (
            "We need a ServiceNow developer with ITSM, Flow Designer, "
            "and Integration Hub experience."
        ),
        "easy_apply": True,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


class SourceCvTests(unittest.TestCase):
    def test_servicenow_profile_resolves_docs_pdf_and_extracts_text(self) -> None:
        source = resolve_source_cv("servicenow-developer")

        self.assertEqual(source.template_id, "servicenow")
        self.assertEqual(source.path.parent, (SRC_DIR / "docs").resolve())
        self.assertEqual(source.path.name, "Manuel Alonso - Developer ServiceNow.pdf")
        self.assertIn("ServiceNow", source.raw_text)
        self.assertIn("MANUEL ALONSO", source.raw_text.upper())
        self.assertGreater(len(source.raw_text), 200)

    def test_fullstack_profile_selects_fullstack_template(self) -> None:
        source = resolve_source_cv("fullstack-developer")
        self.assertEqual(source.template_id, "fullstack")
        self.assertEqual(source.path.name, "Manuel Alonso - Developer Full stack.pdf")

    def test_unknown_profile_lists_available_ids(self) -> None:
        with self.assertRaises(KeyError) as raised:
            resolve_source_cv("not-a-real-profile")
        message = str(raised.exception)
        self.assertIn("servicenow-developer", message)
        self.assertIn("fullstack-developer", message)

    def test_template_id_comes_from_filename(self) -> None:
        self.assertEqual(
            template_id_from_filename("Manuel Alonso - Developer ServiceNow.pdf"),
            "servicenow",
        )
        self.assertEqual(
            template_id_from_filename("Manuel Alonso - Developer Full stack.pdf"),
            "fullstack",
        )

    def test_letter_spaced_pdf_text_collapses(self) -> None:
        self.assertEqual(
            collapse_letter_spaced_text("M A N U E L  A L O N S O"),
            "MANUEL ALONSO",
        )
        extracted = extract_pdf_text(
            SRC_DIR / "docs" / "Manuel Alonso - Developer ServiceNow.pdf"
        )
        self.assertIn("Flow Designer", extracted)


class TemplateTests(unittest.TestCase):
    def test_unmodified_fields_fill_recognisable_layout(self) -> None:
        fields = baseline_fields("servicenow")
        html = render_cv_html("servicenow", fields)

        self.assertIn("MANUEL ALONSO", html)
        self.assertIn("SERVICENOW DEVELOPER", html)
        self.assertIn('class="title-bar"', html)
        self.assertNotIn('class="title-bar title-bar-long"', html)
        long_title = fields.model_copy(deep=True)
        long_title.headline = "Senior Full Stack Engineer (React/Node)"
        long_html = render_cv_html("servicenow", long_title)
        self.assertIn('class="title-bar title-bar-long"', long_html)
        self.assertIn("SENIOR FULL STACK ENGINEER (REACT/NODE)", long_html)
        self.assertIn("Contact", html)
        self.assertIn("Work Experience", html)
        self.assertIn("Sopra Steria Norway", html)
        self.assertIn("Flow Designer", html)
        self.assertNotIn("<script>", html)

    def test_render_pdf_writes_nonempty_file(self) -> None:
        fields = baseline_fields("fullstack")
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "cv.pdf"
            result = render_cv_pdf("fullstack", fields, destination)
            self.assertEqual(result, destination.resolve())
            self.assertTrue(destination.read_bytes().startswith(b"%PDF"))


class ValidateTests(unittest.TestCase):
    def test_planted_kubernetes_lead_fails(self) -> None:
        source = resolve_source_cv("servicenow-developer")
        baseline = baseline_fields("servicenow")
        planted = baseline.model_copy(deep=True)
        planted.skills.append("Kubernetes lead, 8 years")

        errors = validate_cv_fields(planted, source=source, baseline=baseline)
        self.assertTrue(errors)
        self.assertTrue(any("Kubernetes lead" in error for error in errors))

    def test_baseline_fields_pass(self) -> None:
        source = resolve_source_cv("servicenow-developer")
        baseline = baseline_fields("servicenow")
        self.assertEqual(validate_cv_fields(baseline, source=source, baseline=baseline), [])

    def test_reworded_bullet_is_allowed(self) -> None:
        source = resolve_source_cv("fullstack-developer")
        baseline = baseline_fields("fullstack")
        rewritten = baseline.model_copy(deep=True)
        rewritten.summary = (
            "Full-stack developer focused on React frontends and Python APIs, "
            "with RAG and REST work in production."
        )
        rewritten.experience[0].bullets = [
            "Built responsive frontend interfaces with React and Material UI."
        ]
        self.assertEqual(
            validate_cv_fields(rewritten, source=source, baseline=baseline),
            [],
        )

    def test_education_details_accept_a_string(self) -> None:
        from jobsearchagent.preparation.fields import EducationItem

        item = EducationItem(
            years="2014-2017",
            school="UNIVERSITET I OSLO",
            details="Bachelor in Mathematics and Informatics",
        )
        self.assertEqual(item.details, ["Bachelor in Mathematics and Informatics"])

    def test_unknown_employer_fails(self) -> None:
        source = resolve_source_cv("servicenow-developer")
        baseline = baseline_fields("servicenow")
        planted = baseline.model_copy(deep=True)
        planted.experience.insert(
            0,
            ExperienceRole(
                organization="K8s Labs",
                title="Kubernetes lead",
                period="8 years",
                bullets=["Led Kubernetes for 8 years."],
            ),
        )
        errors = validate_cv_fields(planted, source=source, baseline=baseline)
        self.assertTrue(any("employer" in error or "title" in error for error in errors))


class RewriteTests(unittest.TestCase):
    def test_rewrite_uses_writer_json_and_retries_on_invention(self) -> None:
        source = resolve_source_cv("servicenow-developer")
        calls = {"count": 0}

        def chat_fn(messages, model="writer", format=None):
            calls["count"] += 1
            self.assertEqual(model, "writer")
            self.assertIsNotNone(format)
            fields = baseline_fields("servicenow").model_copy(deep=True)
            if calls["count"] == 1:
                fields.skills.append("Kubernetes lead, 8 years")
                return fields.model_dump_json()
            fields.summary = (
                "ServiceNow developer focused on ITSM, Flow Designer, and "
                "Integration Hub, with CSA and CAD."
            )
            return fields.model_dump_json()

        notes: list[str] = []
        result = rewrite_cv(
            source,
            _job(),
            max_attempts=3,
            chat_fn=chat_fn,
            output_fn=notes.append,
        )
        self.assertEqual(calls["count"], 1)
        self.assertTrue(any("ungrounded" in note for note in notes))
        self.assertNotIn("Kubernetes lead", " ".join(result.skills))
        self.assertEqual(result.headline, "ServiceNow Developer")
        self.assertEqual(
            [role.organization for role in result.experience],
            [role.organization for role in baseline_fields("servicenow").experience],
        )

    def test_ollama_timeout_becomes_a_clear_error(self) -> None:
        import httpx
        from jobsearchagent.preparation import rewrite as rewrite_mod

        source = resolve_source_cv("servicenow-developer")

        def chat_fn(messages, **kwargs):
            raise httpx.ReadTimeout("timed out")

        with self.assertRaises(RuntimeError) as raised:
            rewrite_cv(source, _job(), chat_fn=chat_fn)
        self.assertIn("timed out", str(raised.exception).casefold())
        self.assertIn("WRITER_TIMEOUT_SECONDS", str(raised.exception))
        prompt = rewrite_mod._messages(
            source, _job(), baseline_fields("servicenow"), "", [], 1
        )[1]["content"]
        self.assertNotIn("Source CV text:", prompt)

    def test_short_rewrite_restores_summary_and_bullet_count(self) -> None:
        baseline = baseline_fields("fullstack")
        short = baseline.model_copy(deep=True)
        short.summary = "Full-stack developer with React and Python."
        short.experience = [short.experience[0].model_copy(deep=True)]
        short.experience[0].bullets = ["Built a React UI."]
        restored = restore_source_length(short, baseline)
        self.assertGreaterEqual(
            len(re.findall(r"\S+", restored.summary)),
            int(0.85 * len(re.findall(r"\S+", baseline.summary))),
        )
        self.assertGreaterEqual(len(restored.summary.split("\n\n")), 2)
        self.assertEqual(len(restored.experience), len(baseline.experience))
        self.assertEqual(
            len(restored.experience[0].bullets),
            len(baseline.experience[0].bullets),
        )

    def test_finish_uses_offer_title_keeps_job_order_and_adds_skills(self) -> None:
        source = resolve_source_cv("fullstack-developer")
        baseline = initial_fields(source)
        shuffled = baseline.model_copy(deep=True)
        shuffled.experience = list(reversed(shuffled.experience))
        shuffled.skills.append("Invented Quantum Mesh")
        job = _job(
            title="Frontier Engineer",
            raw_description=(
                "React, TypeScript, Python, FastAPI, Node.js, PostgreSQL, "
                "REST APIs, Docker, and Kubernetes."
            ),
        )

        finished = finish_tailored_cv(
            shuffled, source=source, baseline=baseline, job=job
        )

        self.assertEqual(finished.headline, "Frontier Engineer")
        self.assertEqual(
            [role.organization for role in finished.experience],
            [role.organization for role in baseline.experience],
        )
        extras = [
            skill
            for skill in finished.skills
            if skill.casefold() not in {item.casefold() for item in baseline.skills}
        ]
        self.assertTrue(all(skill in finished.skills for skill in baseline.skills))
        self.assertGreater(len(extras), 0)
        self.assertLessEqual(len(extras), 7)
        self.assertNotIn("Invented Quantum Mesh", finished.skills)
        self.assertIn("React", extras)

    def test_headline_from_offer_normalizes_and_falls_back(self) -> None:
        self.assertEqual(
            headline_from_offer({"title": "  Frontier   Engineer  "}, "Fallback"),
            "Frontier Engineer",
        )
        self.assertEqual(headline_from_offer({"title": ""}, "Fallback"), "Fallback")

    def test_extra_grounded_skills_cap_at_seven(self) -> None:
        source = resolve_source_cv("fullstack-developer")
        baseline = initial_fields(source)
        extras = extra_grounded_skills(
            [
                "React",
                "TypeScript",
                "Python",
                "FastAPI",
                "Node.js",
                "PostgreSQL",
                "Docker",
                "Kubernetes",
                "Playwright",
            ],
            source=source,
            baseline=baseline,
            existing=list(baseline.skills),
            limit=7,
        )
        self.assertEqual(len(extras), 7)
        self.assertEqual(extras[0], "React")
        self.assertNotIn("Playwright", extras)


class CliTests(unittest.TestCase):
    def test_filename_includes_job_id_company_and_title(self) -> None:
        name = tailored_filename(_job())
        self.assertEqual(
            name,
            "1234567890 - Example Corp - ServiceNow Developer.pdf",
        )
        path = tailored_output_path(_job(), "/tmp/out")
        self.assertEqual(path.name, name)
        default_path = tailored_output_path(_job())
        self.assertEqual(default_path.parent, Path("/Users/macnolo/Desktop/Trabajo"))

    def test_main_writes_pdf_and_prints_path(self) -> None:
        messages: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            code = main(
                [
                    "--profile",
                    "servicenow-developer",
                    "--url",
                    "https://www.linkedin.com/jobs/view/1234567890/",
                    "--output-dir",
                    temporary,
                ],
                output_fn=messages.append,
                scrape_fn=lambda url: _job(url=url),
                chat_fn=lambda messages, **kwargs: baseline_fields("servicenow").model_dump_json(),
            )
            written = Path(temporary) / tailored_filename(_job())
            self.assertEqual(code, 0)
            self.assertTrue(written.is_file())
            self.assertTrue(written.read_bytes().startswith(b"%PDF"))
            self.assertTrue(any(str(written.resolve()) in item for item in messages))

    def test_unknown_profile_exits_nonzero(self) -> None:
        messages: list[str] = []
        code = main(
            [
                "--profile",
                "not-a-real-profile",
                "--url",
                "https://www.linkedin.com/jobs/view/1/",
            ],
            output_fn=messages.append,
            scrape_fn=lambda url: _job(),
        )
        self.assertEqual(code, 1)
        self.assertTrue(any("available" in item for item in messages))

    def test_initial_fields_overlay_profile_email(self) -> None:
        source = resolve_source_cv("frontend-developer")
        fields = initial_fields(source)
        self.assertEqual(source.template_id, "fullstack")
        self.assertEqual(fields.contact.email, "manuelalonsoper@gmail.com")
        self.assertIn("Frontend", fields.headline)


if __name__ == "__main__":
    unittest.main()
