"""Part M1: typed enabled-site loader."""

from __future__ import annotations

import unittest

from jobsearchagent.config import (
    enabled_sites,
    parse_sites,
    refuse_linkedin_automation,
    site_by_id,
)


class EnabledSiteTests(unittest.TestCase):
    def test_real_registry_enables_the_first_development_set(self) -> None:
        sites = enabled_sites()

        self.assertEqual(
            [site.id for site in sites],
            ["himalayas", "eurotechjobs", "eurotechjobs-remote"],
        )
        linkedin = site_by_id("linkedin")
        self.assertEqual(linkedin.name, "LinkedIn Jobs")
        self.assertFalse(linkedin.enabled)
        self.assertEqual(linkedin.adapter, "linkedin")
        self.assertEqual(site_by_id("himalayas").query_style, "phrase")
        self.assertEqual(site_by_id("eurotechjobs").query_style, "token")
        self.assertFalse(site_by_id("himalayas").supports_query)
        remoteok = site_by_id("remoteok")
        self.assertFalse(remoteok.enabled)
        self.assertEqual(remoteok.adapter, "remoteok")
        for site_id in ("wwr-fullstack", "wwr-frontend", "wwr-backend"):
            self.assertFalse(site_by_id(site_id).enabled)
            self.assertEqual(site_by_id(site_id).adapter, "wwr")

    def test_linkedin_automation_is_refused_while_blocked(self) -> None:
        with self.assertRaises(RuntimeError) as caught:
            refuse_linkedin_automation()
        self.assertIn("will not open linkedin.com", str(caught.exception))

    def test_disabled_spain_board_is_absent_from_the_walk(self) -> None:
        self.assertNotIn("infojobs", [site.id for site in enabled_sites()])
        infojobs = site_by_id("infojobs")
        self.assertFalse(infojobs.enabled)
        self.assertEqual(infojobs.adapter, "infojobs")

    def test_missing_adapter_raises(self) -> None:
        with self.assertRaises(ValueError) as caught:
            parse_sites(
                {
                    "sites": [
                        {
                            "id": "example",
                            "name": "Example",
                            "url": "https://example.com/jobs",
                            "type": "static",
                            "enabled": True,
                            "auth": "none",
                            "supports_query": False,
                            "supports_location": False,
                            "posted_within": "post_filter",
                            "work_type": "url",
                            "profile": "shared",
                        }
                    ]
                }
            )
        self.assertIn("adapter", str(caught.exception))

    def test_static_site_cannot_support_queries(self) -> None:
        with self.assertRaises(ValueError) as caught:
            parse_sites({"sites": [_row(type="static", supports_query=True)]})
        self.assertIn("cannot support queries", str(caught.exception))


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "example",
        "name": "Example",
        "url": "https://example.com/jobs",
        "type": "static",
        "enabled": True,
        "adapter": "example",
        "auth": "none",
        "supports_query": False,
        "supports_location": False,
        "posted_within": "post_filter",
        "work_type": "url",
        "profile": "shared",
    }
    row.update(overrides)
    return row
