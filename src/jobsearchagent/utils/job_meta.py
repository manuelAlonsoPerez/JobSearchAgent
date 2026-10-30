"""Recover company, location, and employment type from LinkedIn listing text."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .text import clean_job_text

UNKNOWN_COMPANY = "Unknown company"
UNKNOWN_LOCATION = "Unknown location"

_LINKEDIN_SUFFIX = re.compile(r"\s*\|\s*LinkedIn(?:\s+Jobs)?\s*$", re.IGNORECASE)
_AT_COMPANY_LOCATION = re.compile(
    r"^(?P<title>.+?)\s+at\s+(?P<company>.+?)\s+[—–-]\s+(?P<location>.+)$",
    re.IGNORECASE,
)
_AT_COMPANY = re.compile(
    r"^(?P<title>.+?)\s+at\s+(?P<company>.+)$",
    re.IGNORECASE,
)
_PIPE_COMPANY = re.compile(r"^(?P<title>.+?)\s*\|\s*(?P<company>.+)$")
_METADATA_NOISE = re.compile(
    r"applicants?|ago|promoted|responses?|see who|alumni|connections?"
    r"|easy apply|solicitud|repost",
    re.IGNORECASE,
)
_HEADER_LOCATION = re.compile(
    r"[—–-]\s*(?P<place>[^.\n]{2,60}?)\s*[—–-]\s*(?P<mode>Remote|Hybrid|On-?site)\b",
    re.IGNORECASE,
)
_REMOTE_PHRASE = re.compile(
    r"\b(?:100%\s*)?(?:fully\s+)?remote(?:\s+working)?\b",
    re.IGNORECASE,
)
_CONTRACT = re.compile(
    r"\b(?:contract(?:or|ing)?|freelance|b2b|fixed[- ]term|"
    r"outside\s+ir35|c2c)\b",
    re.IGNORECASE,
)
_FULL_TIME = re.compile(r"\b(?:full[- ]time|permanent|indefinite)\b", re.IGNORECASE)
_PART_TIME = re.compile(r"\bpart[- ]time\b", re.IGNORECASE)
_TEMPORARY = re.compile(r"\b(?:temporary|temp\s+role|interim)\b", re.IGNORECASE)
_INTERNSHIP = re.compile(r"\b(?:intern(?:ship)?|praktikant)\b", re.IGNORECASE)
_JSONLD_TYPES = {
    "CONTRACTOR": "contract",
    "CONTRACT": "contract",
    "FULL_TIME": "full_time",
    "FULLTIME": "full_time",
    "PART_TIME": "part_time",
    "PARTTIME": "part_time",
    "TEMPORARY": "temporary",
    "INTERN": "internship",
    "INTERNSHIP": "internship",
}


def missing_company(value: str | None) -> bool:
    text = (value or "").strip()
    return not text or text.casefold() in {UNKNOWN_COMPANY.casefold(), "unknown"}


def missing_location(value: str | None) -> bool:
    text = (value or "").strip()
    return not text or text.casefold() in {UNKNOWN_LOCATION.casefold(), "unknown"}


def parse_linkedin_document_title(document_title: str) -> dict[str, str]:
    """Split LinkedIn tab titles such as ``Role at Acme — Poland | LinkedIn``."""

    text = _LINKEDIN_SUFFIX.sub("", (document_title or "").strip()).strip()
    if not text:
        return {}
    match = _AT_COMPANY_LOCATION.match(text)
    if match:
        return {
            "title": match.group("title").strip(" *"),
            "company": match.group("company").strip(" *"),
            "location": match.group("location").strip(" *"),
        }
    match = _AT_COMPANY.match(text)
    if match:
        return {
            "title": match.group("title").strip(" *"),
            "company": match.group("company").strip(" *"),
        }
    match = _PIPE_COMPANY.match(text)
    if match and match.group("company").casefold() != "linkedin":
        return {
            "title": match.group("title").strip(" *"),
            "company": match.group("company").strip(" *"),
        }
    return {"title": text}


def location_from_top_card(text: str, company: str = "") -> str:
    """Take the place from ``Company · Poland · 6 days ago · 35 applicants``."""

    parts = [part.strip() for part in re.split(r"[·•|\n]", text or "") if part.strip()]
    company_fold = company.strip().casefold()
    for part in parts:
        if company_fold and part.casefold() == company_fold:
            continue
        if _METADATA_NOISE.search(part):
            continue
        if re.fullmatch(r"[\d,.]+", part):
            continue
        if len(part) > 80:
            continue
        return part
    return ""


def location_from_description(description: str) -> str:
    """Read a region/remote line from the opening of the job text."""

    head = (description or "")[:800]
    match = _HEADER_LOCATION.search(head)
    if match:
        place = match.group("place").strip(" *")
        mode = match.group("mode").replace("-", " ").title()
        if place and not _METADATA_NOISE.search(place):
            return f"{place} — {mode}"
    if _REMOTE_PHRASE.search(head):
        return "Remote"
    return ""


def infer_employment_type(*hints: str | None) -> str:
    """Classify contract vs full-time from LinkedIn criteria and the body."""

    blob = "\n".join(hint for hint in hints if hint)
    mapped = _employment_from_codes(blob)
    if mapped:
        return mapped
    if _CONTRACT.search(blob):
        return "contract"
    if _INTERNSHIP.search(blob):
        return "internship"
    if _PART_TIME.search(blob):
        return "part_time"
    if _TEMPORARY.search(blob):
        return "temporary"
    if _FULL_TIME.search(blob):
        return "full_time"
    return "unknown"


def enrich_job_metadata(state: Mapping[str, Any]) -> dict[str, str]:
    """Fill missing company/location/title and detect employment type."""

    title = str(state.get("title") or "")
    company = str(state.get("company") or "")
    location = str(state.get("location") or "")
    description = str(
        state.get("raw_description") or state.get("description") or ""
    )
    document_title = str(state.get("document_title") or "")
    json_ld = state.get("json_ld") if isinstance(state.get("json_ld"), Mapping) else {}
    parsed = parse_linkedin_document_title(document_title or title)

    if _looks_like_document_title(title) and parsed.get("title"):
        title = parsed["title"]
    elif not title.strip() and parsed.get("title"):
        title = parsed["title"]

    if missing_company(company):
        company = (
            str(state.get("company_from_page") or "").strip()
            or str(json_ld.get("company") or "").strip()
            or parsed.get("company", "")
        )
    if not missing_location(location) and _looks_like_metadata_blob(location):
        location = location_from_top_card(location, company) or location
    if missing_location(location):
        location = (
            location_from_top_card(str(state.get("location_from_page") or ""), company)
            or str(json_ld.get("location") or "").strip()
            or parsed.get("location", "")
            or location_from_description(description)
        )

    employment_type = infer_employment_type(
        str(state.get("employment_type") or ""),
        str(state.get("employment_hint") or ""),
        str(json_ld.get("employmentType") or ""),
        description,
        title,
        document_title,
    )
    return {
        "title": clean_job_text(title) or title.strip(),
        "company": clean_job_text(company) or UNKNOWN_COMPANY,
        "location": clean_job_text(location) or UNKNOWN_LOCATION,
        "employment_type": employment_type,
    }


def _looks_like_document_title(title: str) -> bool:
    stripped = _LINKEDIN_SUFFIX.sub("", title or "")
    return bool(_AT_COMPANY_LOCATION.match(stripped) or _AT_COMPANY.match(stripped))


def _looks_like_metadata_blob(location: str) -> bool:
    return bool(re.search(r"[·•]", location) or _METADATA_NOISE.search(location))


def _employment_from_codes(blob: str) -> str:
    for raw in re.findall(r"[A-Z][A-Z_]+", blob):
        mapped = _JSONLD_TYPES.get(raw)
        if mapped:
            return mapped
    folded = blob.casefold()
    if "employment type" in folded and "contract" in folded:
        return "contract"
    return ""
