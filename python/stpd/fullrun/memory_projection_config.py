"""Immutable M2 projection profile metadata; reading it needs no model backend."""

from __future__ import annotations

from dataclasses import dataclass

from .text_menu_inputs import INPUT_PROFILE, V2_INPUT_PROFILE
from .text_menu_inputs import V2_VERSION as TEXT_MENU_V2_VERSION
from .text_menu_inputs import VERSION as TEXT_MENU_VERSION
from .token_format import FORMAT

RENDERER_IDENTITY = {"id": "stpd/m2-canonical-current-page-v1",
                     "text_menu_version": TEXT_MENU_VERSION,
                     "wrapper": FORMAT}
V2_RENDERER_IDENTITY = {"id": "stpd/m2-canonical-current-page-v2",
                        "text_menu_version": TEXT_MENU_V2_VERSION,
                        "wrapper": FORMAT}


@dataclass(frozen=True)
class MemoryEpisodeProjectionConfig:
    """Versioned projection choices not already bound by MemoryConfig."""

    schema: str
    max_settling_events: int

    def __post_init__(self) -> None:
        if (self.schema != "stpd/memory-episode-projection-config-v1"
                or type(self.max_settling_events) is not int
                or self.max_settling_events < 0):
            raise ValueError("invalid memory episode projection config")


@dataclass(frozen=True)
class MemoryEpisodeProjectionConfigV2:
    """Immutable v2 profile and exact renderer binding for observed-input replay."""

    schema: str
    max_settling_events: int
    input_profile: str
    renderer_id: str
    renderer_text_menu_version: str
    renderer_wrapper: str

    def __post_init__(self) -> None:
        if (self.schema != "stpd/memory-episode-projection-config-v2"
                or type(self.max_settling_events) is not int
                or self.max_settling_events != 0
                or self.input_profile != V2_INPUT_PROFILE
                or (self.renderer_id, self.renderer_text_menu_version,
                    self.renderer_wrapper) != (
                        V2_RENDERER_IDENTITY["id"],
                        V2_RENDERER_IDENTITY["text_menu_version"],
                        V2_RENDERER_IDENTITY["wrapper"],
                    )):
            raise ValueError("invalid memory episode projection config")


def v2_episode_projection_config() -> MemoryEpisodeProjectionConfigV2:
    """Construct the sole admitted v2 projection contract."""
    return MemoryEpisodeProjectionConfigV2(
        "stpd/memory-episode-projection-config-v2", 0, V2_INPUT_PROFILE,
        V2_RENDERER_IDENTITY["id"], V2_RENDERER_IDENTITY["text_menu_version"],
        V2_RENDERER_IDENTITY["wrapper"],
    )


def parse_episode_projection_config(
    value: object,
) -> MemoryEpisodeProjectionConfig | MemoryEpisodeProjectionConfigV2:
    """Decode only the two immutable observed-input projection contracts."""
    if not isinstance(value, dict):
        raise ValueError("invalid memory episode projection config")
    if (value.get("schema") == "stpd/memory-episode-projection-config-v1"
            and set(value) == {"schema", "max_settling_events"}):
        return MemoryEpisodeProjectionConfig(**value)
    if (value.get("schema") == "stpd/memory-episode-projection-config-v2"
            and set(value) == {"schema", "max_settling_events", "input_profile",
                               "renderer_id", "renderer_text_menu_version",
                               "renderer_wrapper"}):
        return MemoryEpisodeProjectionConfigV2(**value)
    raise ValueError("invalid memory episode projection config")


def projection_input_profile(
    config: MemoryEpisodeProjectionConfig | MemoryEpisodeProjectionConfigV2,
) -> str:
    """Get the profile only after exact non-virtual config validation."""
    if type(config) is MemoryEpisodeProjectionConfig:
        MemoryEpisodeProjectionConfig.__post_init__(config)
        return INPUT_PROFILE
    if type(config) is MemoryEpisodeProjectionConfigV2:
        MemoryEpisodeProjectionConfigV2.__post_init__(config)
        return V2_INPUT_PROFILE
    raise ValueError("invalid memory episode projection config")
