"""Resolve Easy Apply field values from one work profile.

Cover letters and tailored CVs are out of scope for this pass: resume fields
upload the profile's existing source CV, and cover-letter fields are skipped.
CEFR, residence, English-score, and any question not grounded in the profile
are deferred rather than guessed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..config import SRC_DIR
from ..models import FieldAnswer, FormField
from ..triage.easy_apply import field_looks_like_cover_letter, field_looks_like_cv
from ..utils.profile import load_profile, profile_facts

_DEFER_PATTERNS = re.compile(
    r"cefr|language level|english score|toefl|ielts"
    r"|residenc|citizenship"
    r"|salary|compensation|expected pay"
    r"|gender|race|ethnicity|disability|veteran|sexual orientation"
    r"|date of birth|birthdate|ssn|social security|national id",
    re.IGNORECASE,
)
_SPONSORSHIP = re.compile(
    r"sponsor|visa status|employment visa",
    re.IGNORECASE,
)
_WORK_AUTH = re.compile(
    r"legally authorized|authori[sz]ed to work|right to work",
    re.IGNORECASE,
)
_AVAILABILITY = re.compile(
    r"\bstart\b|available (to start|immediately)|notice period|how soon",
    re.IGNORECASE,
)
_START_DURATION = re.compile(
    r"(\d+)\s*(day|days|week|weeks|month|months)",
    re.IGNORECASE,
)
_DURATION_DAYS = {
    "day": 1,
    "days": 1,
    "week": 7,
    "weeks": 7,
    "month": 30,
    "months": 30,
}
_COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "norway": ("norway", "norge", "norwegian"),
    "spain": ("spain", "españa", "espanola", "spanish"),
    "united kingdom": (
        "united kingdom",
        "uk",
        "britain",
        "great britain",
        "england",
        "scotland",
        "wales",
    ),
    "germany": ("germany", "deutschland", "german"),
}
_IGNORE_PATTERNS = re.compile(
    r"^(search|select language|keyword)\b|_r_|typeahead",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\bemail\b|e-mail|correo", re.IGNORECASE)
_PHONE = re.compile(
    r"\b(phone|mobile|tel[eé]fono|telefon)\b",
    re.IGNORECASE,
)
_PHONE_COUNTRY = re.compile(
    r"country code|c[oó]digo de pa[ií]s|phone country",
    re.IGNORECASE,
)
_FIRST_NAME = re.compile(r"first name|given name|nombre", re.IGNORECASE)
_LAST_NAME = re.compile(r"last name|family name|surname|apellido", re.IGNORECASE)
_FULL_NAME = re.compile(r"^(full )?name$|nombre completo", re.IGNORECASE)
_CITY = re.compile(r"\bcity\b|ciudad|by", re.IGNORECASE)
_COUNTRY = re.compile(r"\bcountry\b|pa[ií]s|land", re.IGNORECASE)
_LOCATION = re.compile(r"location|ubicaci[oó]n|city.*country", re.IGNORECASE)
_YEARS = re.compile(
    r"how many years|years of (experience|exp)|a[nñ]os de experiencia",
    re.IGNORECASE,
)
_YES_NO = re.compile(r"^(yes|no|si|sí|true|false)$", re.IGNORECASE)
_GENERIC_STOP = {
    "a",
    "an",
    "the",
    "your",
    "you",
    "are",
    "is",
    "do",
    "did",
    "have",
    "has",
    "how",
    "many",
    "please",
    "select",
    "enter",
    "this",
    "that",
    "with",
    "for",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
}


def resolve_cv_path(
    state: Mapping[str, Any],
    profile: Mapping[str, Any],
    *,
    override: str | Path | None = None,
) -> Path | None:
    """Return an existing CV path: explicit override, state artifact, then source CVs."""

    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return path.resolve()
    artifacts = state.get("artifacts") or {}
    cv = artifacts.get("cv") if isinstance(artifacts, Mapping) else None
    stored = getattr(cv, "path", None) if cv is not None else None
    if stored:
        path = Path(str(stored)).expanduser()
        if path.is_file():
            return path.resolve()
    for name in profile.get("source_cvs") or []:
        path = _find_source_cv(str(name))
        if path is not None:
            return path
    return None


_CV_TOKEN_STOP = {"cv", "resume", "curriculum", "vitae", "pdf"}


def _cv_directories() -> tuple[Path, ...]:
    return (SRC_DIR / "docs", SRC_DIR / "assets")


def _cv_tokens(name: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", Path(name).stem.casefold())
    return frozenset(word for word in words if word not in _CV_TOKEN_STOP)


def _same_cv_name(wanted: str, candidate: str) -> bool:
    """True when two CV filenames share the same words, in any order."""

    if Path(wanted).suffix.casefold() != Path(candidate).suffix.casefold():
        return False
    left = _cv_tokens(wanted)
    right = _cv_tokens(candidate)
    return bool(left) and left == right


def _find_source_cv(name: str) -> Path | None:
    """Find a profile CV under ``src/docs`` (then ``src/assets``)."""

    wanted = Path(name).name.casefold()
    for directory in _cv_directories():
        exact = directory / name
        if exact.is_file():
            return exact.resolve()
        if not directory.is_dir():
            continue
        for candidate in directory.iterdir():
            if not candidate.is_file():
                continue
            if candidate.name.casefold() == wanted or _same_cv_name(name, candidate.name):
                return candidate.resolve()
    return None


def resolve_cover_letter(state: Mapping[str, Any]) -> str | Path | None:
    """Return a cover-letter path or text from ``state.artifacts``, if present."""

    artifacts = state.get("artifacts") or {}
    letter = artifacts.get("cover_letter") if isinstance(artifacts, Mapping) else None
    if letter is None:
        return None
    stored = getattr(letter, "path", None)
    if stored:
        path = Path(str(stored)).expanduser()
        if path.is_file():
            return path.resolve()
    content = getattr(letter, "content", None)
    if content:
        return str(content)
    return None


def resolve_field(
    field: FormField,
    *,
    profile_id: str,
    profile_config: Mapping[str, Any] | None = None,
    cv_path: Path | None = None,
    cover_letter: str | Path | None = None,
    facts: list[str] | None = None,
    context: Mapping[str, Any] | None = None,
    job_location: str = "",
) -> FieldAnswer:
    """Return a profile-backed, deferred, or empty answer for one field."""

    label = field.label or field.id
    if _IGNORE_PATTERNS.search(label):
        return FieldAnswer(field_id=field.id, value=None, source="deferred")

    loaded = context if context is not None else load_profile(profile_id, profile_config)
    profile = loaded.get("profile", {})
    shared = loaded.get("shared", {})
    candidate = shared.get("candidate", {}) if isinstance(shared, Mapping) else {}
    fact_list = facts if facts is not None else profile_facts(profile_id, profile_config)

    if field.type == "file" or field_looks_like_cv(label):
        if field_looks_like_cover_letter(label):
            if cover_letter is not None:
                return FieldAnswer(
                    field_id=field.id,
                    value=str(cover_letter),
                    source="profile",
                )
            return FieldAnswer(
                field_id=field.id,
                value=None,
                source="deferred",
            )
        if cv_path is not None:
            return FieldAnswer(
                field_id=field.id,
                value=str(cv_path),
                source="profile",
            )
        return FieldAnswer(field_id=field.id, value=None, source="deferred")

    if field_looks_like_cover_letter(label):
        if cover_letter is not None:
            return FieldAnswer(
                field_id=field.id,
                value=str(cover_letter),
                source="profile",
            )
        return FieldAnswer(field_id=field.id, value=None, source="deferred")

    if _DEFER_PATTERNS.search(label):
        return FieldAnswer(field_id=field.id, value=None, source="deferred")

    constraint = _work_constraint_value(field, shared, candidate, job_location)
    if constraint is None:
        constraint = _availability_value(field, shared)
    if constraint is not None:
        if field.options:
            matched = _best_option(str(constraint), field.options)
            if matched is None:
                return FieldAnswer(field_id=field.id, value=None, source="deferred")
            constraint = matched
        return FieldAnswer(field_id=field.id, value=constraint, source="profile")

    direct = _direct_profile_value(field, profile, candidate, shared)
    if direct is not None:
        if field.options:
            matched = _best_option(str(direct), field.options)
            if matched is None:
                return FieldAnswer(field_id=field.id, value=None, source="deferred")
            return FieldAnswer(
                field_id=field.id,
                value=matched,
                source="profile",
            )
        return FieldAnswer(field_id=field.id, value=direct, source="profile")

    years = _years_value(field, profile)
    if years is not None:
        return FieldAnswer(field_id=field.id, value=years, source="profile")

    yes_no = _yes_no_from_profile(field, profile, fact_list)
    if yes_no is not None:
        return FieldAnswer(field_id=field.id, value=yes_no, source="profile")

    if field.required and not _has_related_facts(label, fact_list):
        return FieldAnswer(field_id=field.id, value=None, source="deferred")

    return FieldAnswer(field_id=field.id, value=None, source="deferred")


def _work_constraint_value(
    field: FormField,
    shared: Mapping[str, Any],
    candidate: Mapping[str, Any],
    job_location: str,
) -> str | None:
    """Answer sponsorship / work-auth Yes-No from stated work constraints."""

    label = field.label or ""
    if not (_SPONSORSHIP.search(label) or _WORK_AUTH.search(label)):
        return None
    constraints = shared.get("work_constraints")
    if not isinstance(constraints, Mapping):
        constraints = {}
    based_in = str(
        constraints.get("based_in") or candidate.get("based_in") or ""
    )
    will = constraints.get("will_consider")
    considered = []
    if isinstance(will, Mapping):
        considered = [str(item) for item in will.get("countries") or []]
    allowed = [based_in, *considered]
    mentioned = _mentioned_country(f"{label} {job_location}")
    authorized_there = bool(
        mentioned and any(_same_country(mentioned, place) for place in allowed)
    )

    if _WORK_AUTH.search(label):
        return "Yes" if authorized_there else "No"

    # Sponsorship: not needed only when already authorized in that country.
    if mentioned:
        return "No" if authorized_there else "Yes"
    if constraints.get("work_authorization_stated_on_cv") is False:
        return "Yes"
    return None


def _availability_value(field: FormField, shared: Mapping[str, Any]) -> str | None:
    """Answer start-date / notice questions from stated availability."""

    label = field.label or ""
    if not _AVAILABILITY.search(label):
        return None
    constraints = shared.get("work_constraints")
    if not isinstance(constraints, Mapping):
        return None
    raw = constraints.get("available_within_days")
    if raw is None:
        return None
    try:
        available_days = int(raw)
    except (TypeError, ValueError):
        return None

    asked = _asked_start_days(label)
    if asked is not None:
        return "Yes" if available_days <= asked else "No"
    if re.search(r"immediately|asap|\bnow\b", label, re.IGNORECASE):
        return "Yes" if available_days <= 7 else "No"
    if field.type in {"text", "textarea", "number", "select"}:
        if available_days <= 0:
            return "Immediately"
        return f"{available_days} days"
    return None


def _asked_start_days(label: str) -> int | None:
    match = _START_DURATION.search(label)
    if match is None:
        return None
    return int(match.group(1)) * _DURATION_DAYS[match.group(2).casefold()]


def _mentioned_country(text: str) -> str | None:
    folded = text.casefold()
    for canonical, aliases in _COUNTRY_ALIASES.items():
        if any(re.search(rf"\b{re.escape(alias)}\b", folded) for alias in aliases):
            return canonical
    return None


def _same_country(left: str, right: str) -> bool:
    first = _mentioned_country(left)
    second = _mentioned_country(right)
    return first is not None and first == second


def _direct_profile_value(
    field: FormField,
    profile: Mapping[str, Any],
    candidate: Mapping[str, Any],
    shared: Mapping[str, Any],
) -> str | None:
    label = field.label or ""
    email = str(profile.get("email") or "").strip()
    phone = str(candidate.get("phone") or "").strip()
    name = str(candidate.get("name") or "").strip()
    based_in = str(candidate.get("based_in") or "")
    city, country = _split_based_in(based_in)
    first, last = _split_name(name)

    if _EMAIL.search(label) and email:
        return email
    if _PHONE_COUNTRY.search(label):
        return _phone_country(phone, country)
    if _PHONE.search(label) and phone:
        if _looks_like_country_code_field(field):
            return _phone_country(phone, country)
        return _national_phone(phone)
    if _FIRST_NAME.search(label) and first:
        return first
    if _LAST_NAME.search(label) and last:
        return last
    if _FULL_NAME.search(label.strip()) and name:
        return name
    if _CITY.search(label) and city and not _COUNTRY.search(label):
        return city
    if _COUNTRY.search(label) and country and not _PHONE.search(label):
        return country
    if _LOCATION.search(label) and based_in:
        return based_in
    return None


def _years_value(field: FormField, profile: Mapping[str, Any]) -> str | None:
    if not _YEARS.search(field.label or ""):
        return None
    label = (field.label or "").casefold()
    for skill in profile.get("skills") or []:
        if not isinstance(skill, Mapping):
            continue
        names = [str(skill.get("name") or "")]
        names.extend(str(alias) for alias in skill.get("aliases") or [])
        if any(name and name.casefold() in label for name in names):
            years = skill.get("years")
            if years is not None:
                return str(years)
    years = profile.get("years_relevant")
    if years is not None and "service" not in label:
        return str(years)
    core = profile.get("years_in_core_skill")
    if isinstance(core, Mapping) and core.get("years") is not None:
        skill = str(core.get("skill") or "")
        if skill and skill.casefold() in label:
            return str(core["years"])
    return None


def _yes_no_from_profile(
    field: FormField,
    profile: Mapping[str, Any],
    facts: list[str],
) -> str | bool | None:
    options = [str(option) for option in field.options]
    if field.type not in {"radio", "select", "checkbox"} and not options:
        return None
    normalized = [option.casefold() for option in options]
    if options and not all(_YES_NO.match(option) for option in options):
        if field.type != "checkbox":
            return None

    label = (field.label or "").casefold()
    claimed = {
        str(skill.get("name")).casefold()
        for skill in profile.get("skills") or []
        if isinstance(skill, Mapping) and skill.get("name")
    }
    claimed.update(
        str(item).casefold() for item in profile.get("certifications") or []
    )
    not_claimed = {
        str(item).casefold() for item in profile.get("not_claimed") or []
    }

    tokens = _significant_tokens(label)
    if any(token in not_claimed for token in tokens):
        return _as_no(field, options, normalized)
    if any(token in claimed for token in tokens):
        return _as_yes(field, options, normalized)
    if field.type == "checkbox" and re.search(
        r"certify|agree|terms|privacy",
        label,
    ):
        return True
    return None


def _as_yes(field: FormField, options: list[str], normalized: list[str]) -> str | bool:
    if field.type == "checkbox" and not options:
        return True
    for option, folded in zip(options, normalized):
        if folded in {"yes", "si", "sí", "true"}:
            return option
    return options[0] if options else True


def _as_no(field: FormField, options: list[str], normalized: list[str]) -> str | bool:
    if field.type == "checkbox" and not options:
        return False
    for option, folded in zip(options, normalized):
        if folded in {"no", "false"}:
            return option
    return options[-1] if options else False


def must_defer(field: FormField) -> bool:
    """Whether the field is a hard unknown (never send to JEV/GEN)."""

    label = field.label or field.id
    if _IGNORE_PATTERNS.search(label) or _DEFER_PATTERNS.search(label):
        return True
    return field_looks_like_cover_letter(label)


def has_related_facts(label: str, facts: list[str]) -> bool:
    """Whether any profile fact mentions a distinctive word from the label."""

    tokens = _significant_tokens(label)
    if not tokens:
        return False
    blob = "\n".join(facts).casefold()
    return any(token in blob for token in tokens if len(token) >= 4)


def _has_related_facts(label: str, facts: list[str]) -> bool:
    return has_related_facts(label, facts)
    tokens = _significant_tokens(label)
    if not tokens:
        return False
    blob = "\n".join(facts).casefold()
    return any(token in blob for token in tokens if len(token) >= 4)


def _significant_tokens(label: str) -> list[str]:
    words = re.findall(r"[a-z0-9+#.]{2,}", label.casefold())
    return [word for word in words if word not in _GENERIC_STOP]


def _split_name(name: str) -> tuple[str, str]:
    parts = [part for part in name.split() if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def _split_based_in(based_in: str) -> tuple[str, str]:
    parts = [part.strip() for part in based_in.split(",") if part.strip()]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], parts[-1]


def _national_phone(phone: str) -> str:
    stripped = phone.strip()
    if stripped.startswith("+"):
        rest = re.sub(r"^\+\d{1,3}\s*", "", stripped)
        return rest or stripped
    return stripped


def _phone_country(phone: str, country: str) -> str:
    match = re.match(r"\+(\d{1,3})", phone.strip())
    if match:
        return f"+{match.group(1)}"
    return country or phone


def _looks_like_country_code_field(field: FormField) -> bool:
    if not field.options:
        return False
    return any("+" in option or "norway" in option.casefold() for option in field.options)


def _best_option(value: str, options: list[str]) -> str | None:
    folded = value.casefold()
    for option in options:
        if option.casefold() == folded:
            return option
    for option in options:
        if folded in option.casefold() or option.casefold() in folded:
            return option
    digits = re.sub(r"\D", "", value)
    if digits:
        for option in options:
            if digits in re.sub(r"\D", "", option):
                return option
    return None
