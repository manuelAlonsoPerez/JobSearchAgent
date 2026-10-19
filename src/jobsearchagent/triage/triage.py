"""Production JEV triage for one normalized LinkedIn job."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, cast

from ..config import get_settings, load_work_profiles
from ..jev import Jev
from ..models import JobState, Outcome


def _profile_selection_option(profile: Mapping[str, Any]) -> str:
    skills = profile.get("skills", [])
    skill_names = [
        str(skill.get("name"))
        for skill in skills
        if isinstance(skill, Mapping) and skill.get("name")
    ]
    return json.dumps(
        {
            "title": profile.get("title"),
            "summary": profile.get("summary"),
            "years_relevant": profile.get("years_relevant"),
            "skills": skill_names,
            "matching_titles": profile.get("matching_titles", []),
        },
        ensure_ascii=False,
    )


def triage(
    state: JobState,
    *,
    jev: Jev | None = None,
    locations: list[str] | None = None,
    profile_config: Mapping[str, Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Select one profile and return normalized outcome and fit updates."""

    output = progress or (lambda _: None)
    selected_locations = locations or get_settings().job_locations
    if not selected_locations:
        raise ValueError("At least one configured location is required")

    config = profile_config or load_work_profiles()
    profiles = config.get("work_profiles", {})
    if not isinstance(profiles, Mapping) or not profiles:
        raise ValueError("No work profiles are configured")

    profile_options = {
        str(profile_id): _profile_selection_option(profile)
        for profile_id, profile in profiles.items()
        if isinstance(profile, Mapping)
    }
    client = jev or Jev()
    owns_client = jev is None
    try:
        output("JEV 1/2: selecting exactly one work profile...")
        profile_answers = client.ask(
            state={
                "job": {
                    "title": state.get("title", ""),
                    "location": state.get("location", ""),
                    "description": state.get("raw_description", "")[:4_000],
                },
                "profiles": profile_options,
            },
            questions={
                "profile": {
                    "type": "choice",
                    "instructions": (
                        "Select exactly one candidate profile that best matches "
                        "this job. Never combine skills from different profiles."
                    ),
                    "criteria": profile_options,
                },
            },
        )
        profile_answer = profile_answers.get("profile")
        if not isinstance(profile_answer, Mapping):
            raise ValueError("JEV returned no typed profile decision")
        profile_id = profile_answer.get(
            "choice",
            profile_answer.get("answer"),
        )
        profile = profiles.get(profile_id)
        if not isinstance(profile_id, str) or not isinstance(profile, Mapping):
            raise ValueError(f"JEV selected an unknown profile: {profile_id!r}")

        concrete_locations = [
            location
            for location in selected_locations
            if location.casefold() != "remote"
        ]
        configured_locations = ", ".join(selected_locations)
        concrete_scope = (
            ", ".join(concrete_locations)
            if concrete_locations
            else "the configured eligible locations"
        )
        remote_allowed = any(
            location.casefold() == "remote" for location in selected_locations
        )

        output(f"JEV 2/2: evaluating {profile_id} fit and outcome...")
        answers = client.ask(
            state={
                "job": {
                    "title": state.get("title", ""),
                    "company": state.get("company", ""),
                    "location": state.get("location", ""),
                    "description": state.get("raw_description", "")[:10_000],
                    "easy_apply": bool(state.get("easy_apply")),
                },
                "candidate_shared": config.get("shared", {}),
                "location_policy": {
                    "authoritative": True,
                    "configured_locations": selected_locations,
                    "unrestricted_remote_allowed": remote_allowed,
                    "eligible": (
                        "The job matches a configured location, explicitly permits "
                        f"working from {concrete_scope}, has a wider scope that "
                        f"includes {concrete_scope}, or is genuinely worldwide/"
                        "unrestricted remote when Remote is configured."
                    ),
                    "ineligible": (
                        "The job requires residence, work authorization, or remote "
                        "work in a country or region that excludes every configured "
                        "concrete location."
                    ),
                    "unknown": (
                        "The job says remote but does not establish whether someone "
                        "in a configured concrete location may perform it."
                    ),
                    "precedence": (
                        "Use this runtime policy instead of older location "
                        "preferences inside candidate_shared."
                    ),
                },
                "match_rules": config.get("match_rules", {}),
                "selected_profile_id": profile_id,
                "selected_profile": profile,
            },
            questions={
                "location_eligibility": {
                    "type": "choice",
                    "instructions": (
                        "Classify location eligibility using location_policy. "
                        "Remote alone is not enough when its geographic scope is "
                        "restricted or unstated."
                    ),
                    "criteria": {
                        "eligible": (
                            f"Matches {configured_locations}, includes a configured "
                            "concrete location, or is genuinely worldwide/unrestricted "
                            "remote when Remote is configured."
                        ),
                        "ineligible": (
                            "A confirmed geographic, residence, or work restriction "
                            "excludes every configured concrete location."
                        ),
                        "unknown": (
                            "Remote scope or eligibility for configured concrete "
                            "locations is not stated clearly enough."
                        ),
                    },
                },
                "outcome": {
                    "type": "choice",
                    "instructions": (
                        "Decide the candidate's non-location outcome using only "
                        "selected_profile and match_rules. Ignore geographic "
                        "eligibility because location_eligibility handles it."
                    ),
                    "criteria": {
                        "accept": (
                            "The core stack and explicit minimum relevant experience "
                            "match, and no confirmed hard blocker exists. Missing "
                            "secondary skills are allowed."
                        ),
                        "reject": (
                            "Explicit job and profile evidence confirms a hard "
                            "blocker: core-stack mismatch, unmet minimum experience, "
                            "primary weak-fit focus, or mandatory constraint. Never "
                            "reject for secondary-skill gaps alone."
                        ),
                        "needs_confirmation": (
                            "A possible hard blocker depends on an unknown language, "
                            "residence, authorization, schedule, experience "
                            "classification, or other fact."
                        ),
                    },
                },
                "fit": {
                    "type": "score",
                    "instructions": (
                        "Weight core-stack and minimum-experience evidence most "
                        "heavily. Reduce fit for missing secondary skills without "
                        "treating those gaps as rejection."
                    ),
                    "criteria": [
                        "Poor: confirmed core mismatches dominate",
                        "Partial: core is plausible but meaningful gaps remain",
                        "Strong: core requirements are well supported",
                    ],
                },
            },
        )
    finally:
        if owns_client:
            client.close()

    location_answer = answers.get("location_eligibility", {})
    skill_answer = answers.get("outcome", {})
    fit_answer = answers.get("fit", {})
    location_choice = (
        location_answer.get("choice")
        if isinstance(location_answer, Mapping)
        else None
    )
    skill_choice = (
        skill_answer.get("choice") if isinstance(skill_answer, Mapping) else None
    )
    if skill_choice not in {"accept", "reject", "needs_confirmation"}:
        raise ValueError(f"JEV returned an invalid outcome: {skill_choice!r}")

    if skill_choice == "reject" or location_choice == "ineligible":
        outcome: Outcome = "reject"
    elif location_choice == "unknown" or skill_choice == "needs_confirmation":
        outcome = "needs_confirmation"
    else:
        outcome = "accept"

    fit_score = fit_answer.get("score") if isinstance(fit_answer, Mapping) else None
    if not isinstance(fit_score, (int, float)):
        raise ValueError("JEV returned no numeric fit score")

    review_reason: str | None = None
    if outcome == "needs_confirmation":
        review_reason = (
            "Location eligibility is unclear."
            if location_choice == "unknown"
            else "A possible hard blocker requires confirmation."
        )

    return {
        "profile_id": profile_id,
        "outcome": cast(Outcome, outcome),
        "fit_score": float(fit_score),
        "review_reason": review_reason,
        "triage_details": {
            "profile": dict(profile_answer),
            "location_eligibility": location_answer,
            "non_location_outcome": skill_answer,
            "fit": fit_answer,
        },
    }
