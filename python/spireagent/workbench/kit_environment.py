"""Validated Python dependency profile for immutable developer kits."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

PROFILE_FIELD = "python_environment_profile"
PROFILE_CLOUD = "cloud"
PROFILE_CLOUD_LOCAL_MODELS = "cloud-local-models"
PROFILES = frozenset({PROFILE_CLOUD, PROFILE_CLOUD_LOCAL_MODELS})


def has_bundled_text_runtime(files: Mapping[str, bytes], pairs: Mapping[str, tuple]) -> bool:
    """Return whether any approved fixed text runtime pair is present in the inventory."""
    return any(profile_name in files or archive_name in files
               for profile_name, archive_name, *_ in pairs.values())


def resolve_python_environment_profile(
    manifest: Mapping[str, Any],
    files: Mapping[str, bytes],
    pairs: Mapping[str, tuple],
) -> str:
    """Validate explicit profile or infer the historical profile from bundled pairs."""
    bundled = has_bundled_text_runtime(files, pairs)
    if PROFILE_FIELD not in manifest:
        return PROFILE_CLOUD_LOCAL_MODELS if bundled else PROFILE_CLOUD
    profile = manifest[PROFILE_FIELD]
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ValueError("python_environment_profile_invalid")
    if profile == PROFILE_CLOUD and bundled:
        raise ValueError("python_environment_profile_contradicts_bundled_text_runtime")
    return profile


PROFILE_EXTRAS = MappingProxyType({
    PROFILE_CLOUD: ("--extra", "cloud"),
    PROFILE_CLOUD_LOCAL_MODELS: ("--extra", "cloud", "--extra", "local-models"),
})


def extras_for_python_environment_profile(profile: str) -> list[str]:
    """Map a validated profile to the fixed locked uv extras; never accept arbitrary extras."""
    try:
        return list(PROFILE_EXTRAS[profile])
    except (KeyError, TypeError) as error:
        raise ValueError("python_environment_profile_invalid") from error
