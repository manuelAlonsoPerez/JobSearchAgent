"""Part M2: discovery adapter registry."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from jobsearchagent.config import Site, enabled_sites, site_by_id
from jobsearchagent.discovery.linkedin import (
    WORK_TYPE_CODES,
    LinkedInDiscovery,
    SearchFilters as LinkedInSearchFilters,
    posted_window_seconds,
)
from jobsearchagent.discovery.registry import (
    SearchFilters,
    adapter_for,
    discover_with_adapter,
    linkedin_search_filters,
    register_adapter,
    require_adapters,
)
from jobsearchagent.models import JobState


class _StaticAdapter:
    id = "static_example"

    def discover(
        self,
        page: Any,
        site: Site,
        query: str | None,
        location: str | None,
        limit: int,
        filters: SearchFilters,
    ) -> list[JobState]:
        self.seen = {
            "page": page,
            "query": query,
            "location": location,
            "limit": limit,
            "filters": dict(filters),
        }
        return [
            JobState(
                url="https://example.com/jobs/1",
                title="Frontend developer",
                company="Example",
                location="Remote",
                raw_description="Build the product.",
                source_job_id="1",
                apply_url="https://example.com/apply/1",
                easy_apply=True,
            )
        ]


class RegistryTests(unittest.TestCase):
    def test_registry_resolves_linkedin(self) -> None:
        adapter = adapter_for("linkedin", site_id="linkedin")

        self.assertEqual(adapter.id, "linkedin")
        resolved = require_adapters([site_by_id("linkedin")])
        self.assertIs(resolved["linkedin"], adapter)

    def test_unknown_slug_names_the_site(self) -> None:
        with self.assertRaises(RuntimeError) as caught:
            adapter_for("not_a_board", site_id="himalayas")
        self.assertIn("himalayas", str(caught.exception))
        self.assertIn("not_a_board", str(caught.exception))

    def test_enabled_sites_resolve(self) -> None:
        sites = enabled_sites()
        implemented = [
            site for site in sites if site.adapter in {"himalayas", "eurotechjobs"}
        ]
        resolved = require_adapters(implemented)
        self.assertEqual(
            set(resolved),
            {"himalayas", "eurotechjobs", "eurotechjobs-remote"},
        )
        self.assertEqual(resolved["eurotechjobs"].id, "eurotechjobs")
        self.assertIs(resolved["eurotechjobs"], resolved["eurotechjobs-remote"])
        with self.assertRaises(RuntimeError) as caught:
            require_adapters(sites)
        self.assertIn("wwr-fullstack (adapter wwr)", str(caught.exception))

    def test_static_adapter_accepts_a_skipped_query(self) -> None:
        adapter = _StaticAdapter()
        register_adapter(adapter)
        self.addCleanup(self._drop, adapter.id)
        site = Site(
            id="example",
            name="Example Board",
            url="https://example.com/jobs",
            type="static",
            enabled=True,
            adapter=adapter.id,
            auth="none",
            supports_query=False,
            supports_location=False,
            posted_within="post_filter",
            work_type="post_filter",
            profile="shared",
        )

        jobs = discover_with_adapter(
            adapter,
            page=object(),
            site=site,
            query=None,
            location=None,
            limit=3,
            filters=SearchFilters(),
        )

        self.assertIsNone(adapter.seen["query"])
        self.assertIsNone(adapter.seen["location"])
        self.assertEqual(jobs[0]["site"], "Example Board")
        self.assertFalse(jobs[0]["easy_apply"])
        self.assertEqual(jobs[0]["apply_url"], "https://example.com/apply/1")
        self.assertEqual(jobs[0]["raw_description"], "Build the product.")

    def test_empty_filters_keep_the_linkedin_defaults(self) -> None:
        self.assertEqual(linkedin_search_filters({}), LinkedInSearchFilters())

    def test_linkedin_filters_use_the_current_url_builder(self) -> None:
        translated = linkedin_search_filters(
            {
                "month": 1,
                "work_type": "remote",
                "easy_apply": True,
                "geo_id": "91000007",
            }
        )
        direct = LinkedInSearchFilters(
            posted_within_seconds=posted_window_seconds(month=1),
            work_type=WORK_TYPE_CODES["remote"],
            easy_apply=True,
            geo_id="91000007",
        )

        self.assertEqual(
            LinkedInDiscovery.build_search_url("React", "Norway", filters=translated),
            LinkedInDiscovery.build_search_url("React", "Norway", filters=direct),
        )

    def test_any_work_type_clears_the_linkedin_code(self) -> None:
        translated = linkedin_search_filters({"work_type": "any"})
        self.assertIsNone(translated.work_type)

    def test_blocked_linkedin_adapter_does_not_search(self) -> None:
        site = site_by_id("linkedin")
        with patch.object(LinkedInDiscovery, "discover_with_page") as discover:
            with self.assertRaises(RuntimeError) as caught:
                adapter_for("linkedin").discover(
                    object(),
                    site,
                    "React",
                    "Norway",
                    1,
                    SearchFilters(),
                )
        self.assertIn("will not open linkedin.com", str(caught.exception))
        discover.assert_not_called()

    def test_unblocked_linkedin_adapter_delegates(self) -> None:
        site = site_by_id("linkedin")
        found = [
            JobState(
                url="https://www.linkedin.com/jobs/view/1/",
                title="Frontend developer",
                site="LinkedIn Jobs",
                easy_apply=True,
            )
        ]
        with (
            patch("jobsearchagent.discovery.registry.refuse_linkedin_automation"),
            patch.object(
                LinkedInDiscovery,
                "discover_with_page",
                return_value=found,
            ) as discover,
        ):
            jobs = adapter_for("linkedin").discover(
                object(),
                site,
                None,
                None,
                5,
                SearchFilters(easy_apply=False),
            )

        self.assertEqual(jobs[0]["site"], "LinkedIn Jobs")
        self.assertTrue(jobs[0]["easy_apply"])
        self.assertEqual(discover.call_args.args[1], "")
        self.assertEqual(discover.call_args.args[2], "")
        self.assertEqual(discover.call_args.args[3], 5)
        self.assertFalse(discover.call_args.kwargs["filters"].easy_apply)

    def _drop(self, slug: str) -> None:
        from jobsearchagent.discovery import registry

        registry._ADAPTERS.pop(slug, None)
