"""Unit tests for Phase 0 clients and pure utilities."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import httpx

from jobsearchagent.jev import Jev
from jobsearchagent.llm import chat
from jobsearchagent.utils.pdf import build_document_html
from jobsearchagent.utils.profile import (
    load_profile,
    profile_facts,
    summarise_profile,
)
from jobsearchagent.utils.text import (
    canonicalize_url,
    clean_job_text,
    extract_linkedin_job_id,
    job_dedup_key,
)


class JevTests(unittest.TestCase):
    def test_typed_decision_wrappers(self) -> None:
        seen_requests: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            seen_requests.append(payload)
            question = payload["questions"]["q"]
            if question["type"] == "choice":
                answer = {
                    "choice": "accept",
                    "confidence": 0.91,
                    "probabilities": {"accept": 0.91, "reject": 0.09},
                }
            elif question["type"] == "noul":
                answer = {"type": "noul", "noul": 0.8}
            else:
                answer = {"score": 1.75}
            return httpx.Response(200, json={"answers": {"q": answer}})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        jev = Jev(
            base_url="http://ollama.test",
            model="nimble-test",
            client=client,
        )

        choice, confidence = jev.choice(
            "job",
            "Choose",
            {"accept": "Apply", "reject": "Do not apply"},
        )
        self.assertEqual((choice, confidence), ("accept", 0.91))
        self.assertEqual(jev.noul("job", "Is remote?"), 0.8)
        self.assertEqual(
            jev.score("job", "Rate fit", ["Weak", "Partial", "Strong"]),
            1.75,
        )
        self.assertTrue(
            all(request["model"] == "nimble-test" for request in seen_requests)
        )
        client.close()


class LlmTests(unittest.TestCase):
    def test_chat_supports_json_format_and_apply_model(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"message": {"role": "assistant", "content": '{"ok":true}'}},
            )

        with (
            patch.dict(os.environ, {"APPLY_MODEL": "apply-test"}),
            httpx.Client(transport=httpx.MockTransport(handler)) as client,
        ):
            result = chat(
                [{"role": "user", "content": "Return JSON"}],
                model="apply",
                format="json",
                client=client,
            )

        self.assertEqual(result, '{"ok":true}')
        self.assertEqual(captured["model"], "apply-test")
        self.assertEqual(captured["format"], "json")
        self.assertFalse(captured["stream"])


class TextUtilityTests(unittest.TestCase):
    def test_linkedin_urls_share_a_dedup_key(self) -> None:
        first = "https://www.linkedin.com/jobs/view/example-role-12345/?trk=feed"
        second = "https://www.linkedin.com/jobs/search/?currentJobId=12345"

        self.assertEqual(extract_linkedin_job_id(first), "12345")
        self.assertEqual(
            canonicalize_url(second),
            "https://www.linkedin.com/jobs/view/12345/",
        )
        self.assertEqual(
            job_dedup_key("LinkedIn Jobs", first),
            job_dedup_key("LinkedIn Jobs", second),
        )

    def test_job_text_is_cleaned_without_losing_paragraphs(self) -> None:
        self.assertEqual(
            clean_job_text(" First   line \n second\n\n  Next   paragraph "),
            "First line second\n\nNext paragraph",
        )


class ProfileUtilityTests(unittest.TestCase):
    CONFIG = {
        "match_rules": {"outcomes": ["accept", "reject"]},
        "shared": {"candidate": {"name": "Manuel"}},
        "work_profiles": {
            "frontend": {
                "title": "Frontend Developer",
                "skills": [{"name": "React", "years": 5}],
                "not_claimed": ["Angular"],
            },
            "other": {"title": "Other"},
        },
    }

    def test_only_selected_profile_is_loaded_and_flattened(self) -> None:
        context = load_profile("frontend", self.CONFIG)
        summary = summarise_profile("frontend", self.CONFIG)
        facts = profile_facts("frontend", self.CONFIG)

        self.assertEqual(context["profile"]["title"], "Frontend Developer")
        self.assertNotIn("other", summary)
        self.assertIn("profile.skills[0].name=React", facts)
        self.assertIn("profile.not_claimed[0]=Angular", facts)


class PdfUtilityTests(unittest.TestCase):
    def test_document_html_escapes_untrusted_text(self) -> None:
        html = build_document_html(
            "React & TypeScript\n\n<script>alert(1)</script>",
            "CV <Manuel>",
        )

        self.assertIn("CV &lt;Manuel&gt;", html)
        self.assertIn("React &amp; TypeScript", html)
        self.assertNotIn("<script>", html)


if __name__ == "__main__":
    unittest.main()
