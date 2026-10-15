"""Work-profile selection and grounding helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import yaml

from ..config import load_work_profiles


def load_profile(
    profile_id: str,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return shared facts plus exactly one selected work profile."""

    source = dict(config) if config is not None else load_work_profiles()
    profiles = source.get("work_profiles")
    if not isinstance(profiles, dict):
        raise ValueError("work_profiles.yaml has no 'work_profiles' mapping")
    profile = profiles.get(profile_id)
    if not isinstance(profile, dict):
        available = ", ".join(sorted(str(key) for key in profiles))
        raise KeyError(f"Unknown profile {profile_id!r}; available: {available}")

    shared = source.get("shared", {})
    if not isinstance(shared, dict):
        raise ValueError("work_profiles.yaml 'shared' value must be a mapping")

    return {
        "match_rules": source.get("match_rules", {}),
        "shared": shared,
        "profile": profile,
    }


def summarise_profile(
    profile_id: str,
    config: Mapping[str, Any] | None = None,
) -> str:
    """Serialize one profile and shared facts as compact grounding text."""

    context = load_profile(profile_id, config)
    return yaml.safe_dump(
        context,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()


def summarize_profile(
    profile_id: str,
    config: Mapping[str, Any] | None = None,
) -> str:
    """US spelling alias for :func:`summarise_profile`."""

    return summarise_profile(profile_id, config)


def profile_facts(
    profile_id: str,
    config: Mapping[str, Any] | None = None,
) -> list[str]:
    """Flatten one profile into deterministic, path-qualified facts."""

    facts: list[str] = []
    _flatten(load_profile(profile_id, config), "", facts)
    return facts


def _flatten(value: Any, path: str, output: list[str]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            _flatten(child, child_path, output)
        return

    if isinstance(value, list):
        for index, child in enumerate(value):
            _flatten(child, f"{path}[{index}]", output)
        if not value:
            output.append(f"{path}=[]")
        return

    output.append(f"{path}={value}")
