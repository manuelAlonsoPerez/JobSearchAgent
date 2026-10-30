"""Production JEV triage for one normalized LinkedIn job."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any, cast

from ..config import get_settings, load_work_profiles
from ..jev import Jev
from ..models import JobState, Outcome
from ..utils.job_meta import enrich_job_metadata

# The selected decision model reprocesses the whole prompt per question with no
# KV-cache reuse, so prompt size drives latency. Requirements sit near the top
# of a description.
_EVALUATION_DESCRIPTION_LIMIT = 4_000
_PROFILE_SCAN_LIMIT = 1_500
_GENERIC_TITLE_TOKENS = {
    "consultant",
    "consultor",
    "developer",
    "desarrollador",
    "engineer",
    "ingeniero",
    "lead",
    "programador",
    "specialist",
    "utvikler",
}


def _profile_evaluation_context(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the evaluation prompt grounded without sending full CV histories."""

    compact_skills: list[dict[str, Any]] = []
    for skill in profile.get("skills", []):
        if not isinstance(skill, Mapping):
            continue
        compact_skills.append(
            {
                key: skill[key]
                for key in ("name", "aliases", "level", "years", "last_used")
                if key in skill
            }
        )

    return {
        key: profile[key]
        for key in (
            "title",
            "summary",
            "years_relevant",
            "years_in_core_skill",
            "not_claimed",
            "certifications",
            "matching_titles",
            "weak_fit",
        )
        if key in profile
    } | {"skills": compact_skills}


def _job_payload(state: JobState, *, char_limit: int) -> dict[str, Any]:
    return {
        "title": state.get("title", ""),
        "company": state.get("company", ""),
        "location": state.get("location", ""),
        "employment_type": state.get("employment_type", ""),
        "description": (state.get("raw_description", "") or "")[:char_limit],
    }


def _location_policy(selected_locations: list[str]) -> dict[str, Any]:
    concrete_locations = [
        location
        for location in selected_locations
        if location.casefold() != "remote"
    ]
    concrete_scope = (
        ", ".join(concrete_locations)
        if concrete_locations
        else "the configured eligible locations"
    )
    remote_allowed = any(
        location.casefold() == "remote" for location in selected_locations
    )
    return {
        "authoritative": True,
        "configured_locations": selected_locations,
        "unrestricted_remote_allowed": remote_allowed,
        "eligible": (
            "Matches a configured location, permits working from "
            f"{concrete_scope}, includes {concrete_scope}, or is genuinely "
            "worldwide/unrestricted remote when Remote is configured."
        ),
        "ineligible": (
            "Residence, authorization, or remote-work restrictions exclude "
            "every configured concrete location."
        ),
        "unknown": (
            "Remote is stated but eligibility for a configured concrete "
            "location is not established."
        ),
    }


def _compact_shared(shared: Mapping[str, Any]) -> dict[str, Any]:
    """Drop phone numbers and long evidence text that do not change decisions."""

    compact: dict[str, Any] = {}
    candidate = shared.get("candidate")
    if isinstance(candidate, Mapping):
        compact["candidate"] = {
            key: candidate[key]
            for key in ("name", "based_in")
            if key in candidate
        }

    languages = shared.get("languages")
    if isinstance(languages, list):
        compact["languages"] = [
            {
                key: language[key]
                for key in (
                    "name",
                    "cefr",
                    "spoken",
                    "cv_wording",
                    "exam",
                    "exam_passed",
                    "score",
                    "requirement_met",
                )
                if key in language
            }
            for language in languages
            if isinstance(language, Mapping)
        ]

    constraints = shared.get("work_constraints")
    if isinstance(constraints, Mapping):
        will = constraints.get("will_consider")
        compact_constraints = {
            key: constraints[key]
            for key in (
                "based_in",
                "spain_residence_stated",
                "work_authorization_stated_on_cv",
            )
            if key in constraints
        }
        if isinstance(will, Mapping):
            compact_will = {
                key: will[key]
                for key in (
                    "countries",
                    "arrangements",
                    "remote_from_norway",
                    "spain_locations",
                )
                if key in will
            }
            if compact_will:
                compact_constraints["will_consider"] = compact_will
        compact["work_constraints"] = compact_constraints

    education = shared.get("education")
    if isinstance(education, list):
        compact["education"] = [
            {
                key: item[key]
                for key in ("school", "credential", "years", "completed", "notes")
                if key in item
            }
            for item in education
            if isinstance(item, Mapping)
        ]
    return compact


def _compact_match_rules(match_rules: Mapping[str, Any]) -> dict[str, Any]:
    """Keep policy definitions; question criteria already encode the steps."""

    compact = {
        key: match_rules[key]
        for key in ("outcomes", "definitions")
        if key in match_rules
    }
    return compact or dict(match_rules)


def _contains_phrase(haystack: str, phrase: str) -> bool:
    phrase = phrase.strip()
    if not phrase:
        return False
    if re.search(r"[\s\-]", phrase):
        return phrase in haystack
    return re.search(rf"\b{re.escape(phrase)}\b", haystack) is not None


def _distinctive_phrases(profile: Mapping[str, Any]) -> list[str]:
    phrases: list[str] = []
    for raw in (
        profile.get("title"),
        *(profile.get("matching_titles") or []),
    ):
        text = str(raw or "").casefold()
        if text:
            phrases.append(text)
            tokens = [
                token
                for token in re.split(r"[\s\-/]+", text)
                if token and token not in _GENERIC_TITLE_TOKENS
            ]
            if tokens:
                phrases.append(" ".join(tokens))
                phrases.extend(tokens)
    for skill in profile.get("skills") or []:
        if not isinstance(skill, Mapping):
            continue
        name = str(skill.get("name") or "").casefold()
        if name:
            phrases.append(name)
        for alias in skill.get("aliases") or []:
            alias_text = str(alias or "").casefold()
            if alias_text:
                phrases.append(alias_text)
    return phrases


def select_profile(
    state: JobState,
    profiles: Mapping[str, Any],
) -> str:
    """Pick the one profile whose titles and skills best match the job.

    This is deterministic so triage only spends one JEV call on location and skills.
    """

    title = str(state.get("title") or "").casefold()
    haystack = " ".join(
        [
            title,
            (str(state.get("raw_description") or "")[:_PROFILE_SCAN_LIMIT]).casefold(),
        ]
    )
    best_id: str | None = None
    best_score = -1
    fallback_id: str | None = None
    for profile_id, profile in profiles.items():
        if not isinstance(profile, Mapping):
            continue
        if fallback_id is None:
            fallback_id = str(profile_id)
        score = 0
        for phrase in _distinctive_phrases(profile):
            in_title = _contains_phrase(title, phrase)
            in_body = _contains_phrase(haystack, phrase)
            if not in_body:
                continue
            weight = 6 if " " in phrase or "-" in phrase else 3
            if in_title:
                weight += 4
            if len(phrase) >= 8:
                weight += 2
            score = max(score, 0) + weight
        if score > best_score:
            best_score = score
            best_id = str(profile_id)
    if best_id is None or best_score <= 0:
        if fallback_id is None:
            raise ValueError("No work profiles are configured")
        return fallback_id
    return best_id


def triage(
    state: JobState,
    *,
    jev: Jev | None = None,
    locations: list[str] | None = None,
    profile_config: Mapping[str, Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Select one profile locally, then JEV-score only that profile."""

    output = progress or (lambda _: None)
    recovered = enrich_job_metadata(state)
    state = {**state, **recovered}
    selected_locations = locations or get_settings().job_locations
    if not selected_locations:
        raise ValueError("At least one configured location is required")

    config = profile_config or load_work_profiles()
    profiles = config.get("work_profiles", {})
    if not isinstance(profiles, Mapping) or not profiles:
        raise ValueError("No work profiles are configured")

    profile_id = select_profile(state, profiles)
    profile = profiles[profile_id]
    if not isinstance(profile, Mapping):
        raise ValueError(f"Selected an unknown profile: {profile_id!r}")

    configured_locations = ", ".join(selected_locations)
    client = jev or Jev()
    owns_client = jev is None
    try:
        output(
            f"Listing: {state.get('company')} · {state.get('location')} · "
            f"{state.get('employment_type') or 'unknown'}"
        )
        output(
            f"Selected profile '{profile_id}'. "
            "JEV: evaluating location and skills..."
        )
        answers = client.ask(
            state={
                "job": _job_payload(
                    state,
                    char_limit=_EVALUATION_DESCRIPTION_LIMIT,
                ),
                "candidate_shared": _compact_shared(config.get("shared", {})),
                "location_policy": _location_policy(selected_locations),
                "match_rules": _compact_match_rules(config.get("match_rules", {})),
                "selected_profile": _profile_evaluation_context(profile),
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
                        "Decide the candidate's skills outcome using only "
                        "selected_profile and match_rules. Ignore geographic "
                        "eligibility because location_eligibility handles it."
                    ),
                    "criteria": {
                        "accept": (
                            "A good part of the core stack (roughly half or more) "
                            "is covered by the selected profile, OR the uncovered "
                            "core technologies are closely adjacent to its "
                            "demonstrated skills (same language, paradigm, or "
                            "ecosystem) and so are reasonably learnable on the job "
                            "by a candidate with ~8 years across many technologies "
                            "who has repeatedly learned new stacks after starting a "
                            "role. Explicit minimum experience is broadly met and no "
                            "confirmed hard blocker exists. Missing secondary skills "
                            "are allowed."
                        ),
                        "reject": (
                            "Explicit job and profile evidence confirms a hard "
                            "blocker: most of the core stack is absent AND not "
                            "adjacent to the profile's skills, a core technology is "
                            "explicitly not_claimed, relevant experience is clearly "
                            "far below an explicit minimum, the primary focus is a "
                            "weak_fit, or a mandatory constraint is unmet. Never "
                            "reject for secondary-skill gaps, nor for a minority of "
                            "missing core skills that are adjacent or learnable."
                        ),
                        "needs_confirmation": (
                            "A possible hard blocker depends on an unknown language, "
                            "residence, authorization, schedule, experience "
                            "classification, or other fact; or core coverage is "
                            "borderline and hinges on an unverifiable detail."
                        ),
                    },
                },
            },
        )
    finally:
        if owns_client:
            client.close()

    location_answer = answers.get("location_eligibility", {})
    skill_answer = answers.get("outcome", {})
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

    review_reason: str | None = None
    if outcome == "needs_confirmation":
        review_reason = (
            "Location eligibility is unclear."
            if location_choice == "unknown"
            else "A possible hard blocker requires confirmation."
        )

    return {
        "title": recovered["title"],
        "company": recovered["company"],
        "location": recovered["location"],
        "employment_type": recovered["employment_type"],
        "profile_id": profile_id,
        "outcome": cast(Outcome, outcome),
        "review_reason": review_reason,
        "triage_details": {
            "profile": {"choice": profile_id, "source": "title_and_skills"},
            "location_eligibility": location_answer,
            "skills_outcome": skill_answer,
        },
    }
