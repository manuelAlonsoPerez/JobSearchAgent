"""Environment and YAML configuration for JobSearchAgent."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
PROJECT_DIR = SRC_DIR.parent
CONFIG_DIR = SRC_DIR / "config"
ENV_PATH = PROJECT_DIR / ".env"


class Settings(BaseModel):
    """Runtime settings loaded from environment variables."""

    ollama_base_url: str = "http://localhost:11434"
    jev_model: str = "nimble"
    jev_timeout_seconds: float = Field(default=180.0, gt=0)
    writer_model: str = "qwen2.5:7b"
    apply_model: str = "qwen2.5:7b"
    db_path: Path = Path("~/.jobsearchagent/jobsearch.db")
    chrome_profile_dir: Path = Path("~/.jobsearchagent/chrome-profile")
    linkedin_search_page_limit: int = Field(default=2, ge=1)
    linkedin_daily_apply_limit: int = Field(default=10, ge=1)
    linkedin_action_delay_ms: int = Field(default=1200, ge=0)
    min_fit: float = 1.0
    quality_threshold: float = 1.0
    max_artifact_attempts: int = Field(default=3, ge=1)
    max_browser_attempts: int = Field(default=3, ge=1)
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
        jev_timeout_seconds=os.getenv("JEV_TIMEOUT_SECONDS", "180"),
        writer_model=os.getenv("WRITER_MODEL", "qwen2.5:7b"),
        apply_model=os.getenv("APPLY_MODEL", "qwen2.5:7b"),
        db_path=Path(os.getenv("DB_PATH", "~/.jobsearchagent/jobsearch.db")).expanduser(),
        chrome_profile_dir=Path(
            os.getenv("CHROME_PROFILE_DIR", "~/.jobsearchagent/chrome-profile")
        ).expanduser(),
        linkedin_search_page_limit=os.getenv("LINKEDIN_SEARCH_PAGE_LIMIT", "2"),
        linkedin_daily_apply_limit=os.getenv("LINKEDIN_DAILY_APPLY_LIMIT", "10"),
        linkedin_action_delay_ms=os.getenv("LINKEDIN_ACTION_DELAY_MS", "1200"),
        min_fit=os.getenv("MIN_FIT", "1.0"),
        quality_threshold=os.getenv("QUALITY_THRESHOLD", "1.0"),
        max_artifact_attempts=os.getenv("MAX_ARTIFACT_ATTEMPTS", "3"),
        max_browser_attempts=os.getenv("MAX_BROWSER_ATTEMPTS", "3"),
        dry_run=os.getenv("DRY_RUN", "true"),
    )
    return settings


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


def load_searches() -> dict[str, Any]:
    """Load the v1 search configuration."""

    return _load_yaml("searches.example.yaml")


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

    sites = load_sites().get("sites", [])
    linkedin = next(
        (
            site
            for site in sites
            if isinstance(site, dict) and site.get("name") == "LinkedIn Jobs"
        ),
        None,
    )
    if linkedin is None:
        raise ValueError("sites.yaml has no 'LinkedIn Jobs' entry")

    template = linkedin.get("url")
    if not isinstance(template, str) or not template:
        raise ValueError("'LinkedIn Jobs' must define a non-empty URL")

    url = template.format(
        query_encoded=quote_plus(query.strip()),
        location_encoded=quote_plus(location.strip()),
    )
    if easy_apply and "f_AL=" not in url:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}f_AL=true"
    return url
