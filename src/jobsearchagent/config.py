"""Environment and YAML configuration for JobSearchAgent."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote_plus

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError


PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
PROJECT_DIR = SRC_DIR.parent
CONFIG_DIR = SRC_DIR / "config"
ENV_PATH = PROJECT_DIR / ".env"


class Settings(BaseModel):
    """Runtime settings loaded from environment variables."""

    ollama_base_url: str = "http://localhost:11434"
    jev_model: str = "nimble"
    jev_timeout_seconds: float = Field(default=500.0, gt=0)
    writer_model: str = "qwen2.5:7b"
    writer_timeout_seconds: float = Field(default=900.0, gt=0)
    writer_num_ctx: int = Field(default=8192, ge=2048)
    apply_model: str = "qwen2.5:7b"
    db_path: Path = Path("~/.jobsearchagent/jobsearch.db")
    chrome_profile_dir: Path = Path("~/.jobsearchagent/chrome-profile")
    tailored_cv_dir: Path = Path("/Users/macnolo/Desktop/Trabajo")
    linkedin_search_page_limit: int = Field(default=2, ge=1)
    linkedin_daily_apply_limit: int = Field(default=10, ge=1)
    linkedin_action_delay_ms: int = Field(default=1200, ge=0)
    min_fit: float = 1.0
    quality_threshold: float = 1.0
    max_artifact_attempts: int = Field(default=3, ge=1)
    max_browser_attempts: int = Field(default=3, ge=1)
    job_locations: list[str] = Field(min_length=1)
    dry_run: bool = True


def get_settings() -> Settings:
    """Load project ``.env`` and return validated runtime settings.

    Existing process environment variables take precedence over values in
    ``.env``. Paths are expanded so downstream code never receives ``~``.
    """

    load_dotenv(ENV_PATH, override=False)
    settings = Settings(
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        jev_model=os.getenv("JEV_MODEL", "nimble"),
        jev_timeout_seconds=os.getenv("JEV_TIMEOUT_SECONDS", "500"),
        writer_model=os.getenv("WRITER_MODEL", "qwen2.5:7b"),
        writer_timeout_seconds=os.getenv("WRITER_TIMEOUT_SECONDS", "900"),
        writer_num_ctx=os.getenv("WRITER_NUM_CTX", "8192"),
        apply_model=os.getenv("APPLY_MODEL", "qwen2.5:7b"),
        db_path=Path(os.getenv("DB_PATH", "~/.jobsearchagent/jobsearch.db")).expanduser(),
        chrome_profile_dir=Path(
            os.getenv("CHROME_PROFILE_DIR", "~/.jobsearchagent/chrome-profile")
        ).expanduser(),
        tailored_cv_dir=Path(
            os.getenv("TAILORED_CV_DIR", "/Users/macnolo/Desktop/Trabajo")
        ).expanduser(),
        linkedin_search_page_limit=os.getenv("LINKEDIN_SEARCH_PAGE_LIMIT", "2"),
        linkedin_daily_apply_limit=os.getenv("LINKEDIN_DAILY_APPLY_LIMIT", "10"),
        linkedin_action_delay_ms=os.getenv("LINKEDIN_ACTION_DELAY_MS", "1200"),
        min_fit=os.getenv("MIN_FIT", "1.0"),
        quality_threshold=os.getenv("QUALITY_THRESHOLD", "1.0"),
        max_artifact_attempts=os.getenv("MAX_ARTIFACT_ATTEMPTS", "3"),
        max_browser_attempts=os.getenv("MAX_BROWSER_ATTEMPTS", "3"),
        job_locations=_parse_job_locations(_required_env("JOB_LOCATIONS")),
        dry_run=os.getenv("DRY_RUN", "true"),
    )
    return settings


def _parse_job_locations(raw: str) -> list[str]:
    """Parse ``JOB_LOCATIONS`` as a JSON array or comma-separated string."""

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw.split(",")

    if isinstance(parsed, str):
        parsed = [parsed]
    if not isinstance(parsed, list):
        raise ValueError(
            "JOB_LOCATIONS must be a JSON array or comma-separated list"
        )

    locations = [str(location).strip() for location in parsed if str(location).strip()]
    if not locations:
        raise ValueError("JOB_LOCATIONS must contain at least one location")
    return locations


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(
            f"{name} is required. Copy .env.example to .env and configure it."
        )
    return value


def _load_yaml(filename: str) -> dict[str, Any]:
    path = CONFIG_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Configuration root must be a mapping: {path}")
    return loaded


def load_sites() -> dict[str, Any]:
    """Load the site registry."""

    return _load_yaml("sites.yaml")


_ADAPTER_SLUG = re.compile(r"^[a-z][a-z0-9_]*$")
_REQUIRED_SITE_FIELDS = (
    "id",
    "name",
    "url",
    "type",
    "adapter",
    "auth",
    "supports_query",
    "supports_location",
    "posted_within",
    "work_type",
    "profile",
)


class Site(BaseModel):
    """One row from ``sites.yaml`` that discovery can walk."""

    id: str
    name: str
    url: str
    type: Literal["search", "static"]
    enabled: bool
    adapter: str
    auth: Literal["none", "login"]
    supports_query: bool
    supports_location: bool
    posted_within: Literal["native", "post_filter", "unsupported"]
    work_type: Literal["native", "url", "post_filter", "unsupported"]
    profile: Literal["shared", "dedicated"]
    query_style: Literal["phrase", "token"] = "phrase"


def parse_sites(registry: Mapping[str, Any]) -> list[Site]:
    """Validate every site row. Disabled rows stay in the list."""

    raw = registry.get("sites")
    if not isinstance(raw, list):
        raise ValueError("sites.yaml 'sites' must be a list")

    sites: list[Site] = []
    seen: set[str] = set()
    for index, row in enumerate(raw):
        if not isinstance(row, Mapping):
            raise ValueError(f"Site at index {index} must be a mapping")
        missing = [field for field in _REQUIRED_SITE_FIELDS if field not in row]
        if missing:
            label = row.get("id") or f"index {index}"
            raise ValueError(f"Site {label} is missing {', '.join(missing)}")
        try:
            site = Site.model_validate(dict(row))
        except ValidationError as exc:
            label = row.get("id") or f"index {index}"
            raise ValueError(f"Invalid site {label}: {exc}") from exc
        if not site.id.strip() or not site.name.strip() or not site.url.strip():
            raise ValueError(f"Site at index {index} has an empty id, name, or url")
        if site.id in seen:
            raise ValueError(f"Duplicate site id: {site.id}")
        seen.add(site.id)
        if _ADAPTER_SLUG.fullmatch(site.adapter) is None:
            raise ValueError(
                f"Site {site.id} has an invalid adapter slug: {site.adapter!r}"
            )
        if site.type == "static" and site.supports_query:
            raise ValueError(f"Static site {site.id} cannot support queries")
        if site.id == "linkedin" and site.name != "LinkedIn Jobs":
            raise ValueError("The linkedin site name must stay 'LinkedIn Jobs'")
        sites.append(site)
    return sites


def blocked_site_names() -> set[str]:
    """Return blocked site ids and display names, compared case-insensitively."""

    raw = load_sites().get("blocked") or {}
    if not isinstance(raw, Mapping):
        return set()
    names = raw.get("sites") or []
    if not isinstance(names, list):
        return set()
    return {str(name).strip().casefold() for name in names if str(name).strip()}


def site_is_blocked(site: Site) -> bool:
    """True when the row's id or display name is in ``blocked.sites``."""

    names = blocked_site_names()
    return site.id.casefold() in names or site.name.casefold() in names


def enabled_sites() -> list[Site]:
    """Return enabled registry rows in file order, skipping blocked sites."""

    return [
        site
        for site in parse_sites(load_sites())
        if site.enabled and not site_is_blocked(site)
    ]


def site_by_id(site_id: str) -> Site:
    """Return one registry row, including a disabled or blocked site."""

    needle = site_id.strip()
    if not needle:
        raise ValueError("A site id is required")
    for site in parse_sites(load_sites()):
        if site.id == needle:
            return site
    raise ValueError(f"Unknown site id: {needle}")


def refuse_linkedin_automation() -> None:
    """Stop before any code opens linkedin.com while LinkedIn is blocked.

    The row can stay in the file. Automation starts again only when the id is
    removed from ``blocked.sites`` and ``enabled`` is true.
    """

    site = site_by_id("linkedin")
    if site.enabled and not site_is_blocked(site):
        return
    raise RuntimeError(
        "LinkedIn is blocked in src/config/sites.yaml, so this command "
        "will not open linkedin.com. To allow it later, remove linkedin "
        "from blocked.sites and set the linkedin row to enabled: true."
    )


def load_searches() -> dict[str, Any]:
    """Load the v1 search configuration.

    ``searches.yaml`` is the working file; ``searches.example.yaml`` is the
    fallback when that file is missing.
    """

    if (CONFIG_DIR / "searches.yaml").is_file():
        return _load_yaml("searches.yaml")
    return _load_yaml("searches.example.yaml")


def search_queries(*, max_tier: int | None = None) -> list[str]:
    """Return LinkedIn queries from searches.yaml, most specific first.

    ``max_tier`` keeps queries at that tier or lower (1 = exact). Duplicate
    query strings are dropped, preserving the first occurrence.
    """

    if max_tier is not None and max_tier < 1:
        raise ValueError("max_tier must be at least 1")

    selected: list[str] = []
    seen: set[str] = set()
    ranked: list[tuple[int, int, str]] = []
    raw = load_searches().get("queries") or []
    if not isinstance(raw, list):
        raise ValueError("searches.yaml 'queries' must be a list")

    for index, row in enumerate(raw):
        if isinstance(row, str):
            text, tier = row.strip(), 1
        elif isinstance(row, Mapping):
            text = str(row.get("query") or "").strip()
            try:
                tier = int(row.get("tier") or 1)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid query tier at index {index}") from exc
        else:
            continue
        if not text:
            continue
        if max_tier is not None and tier > max_tier:
            continue
        ranked.append((tier, index, text))

    for _tier, _index, text in ranked:
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        selected.append(text)

    if not selected:
        raise ValueError(
            "searches.yaml has no queries"
            + (f" at tier {max_tier} or below" if max_tier is not None else "")
        )
    return selected


def load_work_profiles() -> dict[str, Any]:
    """Load candidate profiles and matching rules."""

    return _load_yaml("work_profiles.yaml")


def linkedin_search_url(
    query: str,
    location: str,
    easy_apply: bool = True,
) -> str:
    """Build the configured LinkedIn search URL.

    The registry remains the source of truth. This function only fills its
    placeholders and optionally adds LinkedIn's Easy Apply filter.
    """

    linkedin = site_by_id("linkedin")
    template = linkedin.url
    if not template:
        raise ValueError("'LinkedIn Jobs' must define a non-empty URL")

    url = template.format(
        query_encoded=quote_plus(query.strip()),
        location_encoded=quote_plus(location.strip()),
    )
    if easy_apply and "f_AL=" not in url:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}f_AL=true"
    return url
