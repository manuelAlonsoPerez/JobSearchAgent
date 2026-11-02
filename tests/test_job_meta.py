"""Company, location, and contract recovery from LinkedIn listing text."""

from __future__ import annotations

import unittest

from jobsearchagent.triage.triage import triage
from jobsearchagent.utils.job_meta import (
    UNKNOWN_COMPANY,
    UNKNOWN_LOCATION,
    enrich_job_metadata,
    infer_employment_type,
    location_from_description,
    location_from_top_card,
    parse_linkedin_document_title,
)
from tests.test_linkedin_agent import PROFILE_CONFIG, StubJev


class ParseTitleTests(unittest.TestCase):
    def test_red_global_datasphere_listing(self) -> None:
        parsed = parse_linkedin_document_title(
            "Datasphere Developer at RED Global — Poland | LinkedIn Jobs"
        )

        self.assertEqual(parsed["title"], "Datasphere Developer")
        self.assertEqual(parsed["company"], "RED Global")
        self.assertEqual(parsed["location"], "Poland")

    def test_en_dash_and_plain_linkedin_suffix(self) -> None:
        parsed = parse_linkedin_document_title(
            "Frontend Developer at Example – Remote | LinkedIn"
        )
        self.assertEqual(parsed["company"], "Example")
        self.assertEqual(parsed["location"], "Remote")


class LocationAndContractTests(unittest.TestCase):
    def test_top_card_blob_keeps_the_place(self) -> None:
        self.assertEqual(
            location_from_top_card(
                "RED Global · Poland · 6 days ago · 35 applicants",
                "RED Global",
            ),
            "Poland",
        )

    def test_description_header_region_and_remote(self) -> None:
        self.assertEqual(
            location_from_description(
                "***Datasphere Developer –Eastern Europe/Türkiye – Remote ***\n"
                "For our global client RED is looking for 2x Datasphere developers"
            ),
            "Eastern Europe/Türkiye — Remote",
        )

    def test_contract_from_body_and_json_ld(self) -> None:
        self.assertEqual(
            infer_employment_type(
                "This is a new contract opportunity until Feb 2027"
            ),
            "contract",
        )
        self.assertEqual(infer_employment_type("CONTRACTOR"), "contract")
        self.assertEqual(infer_employment_type("Employment type\nFull-time"), "full_time")


class EnrichTests(unittest.TestCase):
    def test_recovers_unknown_fields_from_the_example_listing(self) -> None:
        filled = enrich_job_metadata(
            {
                "title": "Datasphere Developer at RED Global — Poland | LinkedIn Jobs",
                "company": UNKNOWN_COMPANY,
                "location": UNKNOWN_LOCATION,
                "raw_description": (
                    "***Datasphere Developer –Eastern Europe/Türkiye – Remote ***\n"
                    "For our global client RED is looking for 2x Datasphere developers "
                    "for a new contract opportunity. This is to start ASAP until the "
                    "end of Feb 2027, and will be 100% remote working."
                ),
                "document_title": (
                    "Datasphere Developer at RED Global — Poland | LinkedIn Jobs"
                ),
            }
        )

        self.assertEqual(filled["title"], "Datasphere Developer")
        self.assertEqual(filled["company"], "RED Global")
        self.assertEqual(filled["location"], "Poland")
        self.assertEqual(filled["employment_type"], "contract")

    def test_prefers_json_ld_when_css_hooks_are_missing(self) -> None:
        filled = enrich_job_metadata(
            {
                "title": "Datasphere Developer",
                "company": "",
                "location": "",
                "raw_description": "SAP Datasphere data modeling.",
                "json_ld": {
                    "company": "RED Global",
                    "location": "Poland",
                    "employmentType": "CONTRACTOR",
                },
            }
        )
        self.assertEqual(filled["company"], "RED Global")
        self.assertEqual(filled["location"], "Poland")
        self.assertEqual(filled["employment_type"], "contract")


class TriageEnrichmentTests(unittest.TestCase):
    def test_triage_writes_recovered_company_location_and_contract(self) -> None:
        update = triage(
            {
                "title": "Datasphere Developer at RED Global — Poland | LinkedIn Jobs",
                "company": UNKNOWN_COMPANY,
                "location": UNKNOWN_LOCATION,
                "raw_description": (
                    "React and TypeScript are not required. "
                    "New contract opportunity, 100% remote working in Poland."
                ),
                "easy_apply": True,
            },
            jev=StubJev(),
            locations=["Norway", "Remote"],
            profile_config=PROFILE_CONFIG,
        )

        self.assertEqual(update["company"], "RED Global")
        self.assertEqual(update["location"], "Poland")
        self.assertEqual(update["employment_type"], "contract")
        self.assertEqual(update["title"], "Datasphere Developer")


if __name__ == "__main__":
    unittest.main()
