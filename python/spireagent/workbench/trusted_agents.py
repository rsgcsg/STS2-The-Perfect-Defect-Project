"""Code-owned Agent composition; model bytes never provide an executable selection."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from spireagent.json_boundary import BoundaryError

TRUSTED_AGENT_ADAPTERS = frozenset({
    "s1-v1", "token-v1", "stpd-m2-decision-adapter", "stpd-s0-structured-adapter",
    "stpd-native-structured-m2-agent",
})
AGENT_PROFILES = (
    ("s1-v1", "s1-v1", "text-menu-v1", True),
    ("text-menu-v1", "token-v1", "text-menu-v1", True),
    ("text-menu-m2-v1", "stpd-m2-decision-adapter", "text-menu-v1", True),
    ("text-menu-m2-v2", "stpd-m2-decision-adapter", "text-menu-v2", True),
    ("stpd-s0-structured-adapter", "stpd-s0-structured-adapter", "text-menu-v2", True),
    ("native-logical-v1", "stpd-native-structured-m2-agent", "native-logical-v1", True),
)


def agent_capabilities() -> list[dict[str, Any]]:
    return [{"profile_id": profile, "adapter_id": adapter, "input_profile": input_profile,
             "installation_available": available,
             "reason": None if available else "structured_installation_adapter_required",
             "gameplay_authority": False, "automatic_retry": False}
            for profile, adapter, input_profile, available in AGENT_PROFILES]


def trusted_agent_support(adapter_id: str) -> ModuleType:
    # Explicit domain modules remain the installation/package authority.
    if adapter_id == "stpd-native-structured-m2-agent":
        from spireagent.workbench import native_agent_support

        return native_agent_support
    if adapter_id == "s1-v1":
        from stpd.policy import installation

        return installation
    if adapter_id == "token-v1":
        from stpd import token_policy_installation

        return token_policy_installation
    if adapter_id == "stpd-m2-decision-adapter":
        from stpd import memory_policy_installation

        return memory_policy_installation
    if adapter_id == "stpd-s0-structured-adapter":
        from stpd import structured_policy_installation

        return structured_policy_installation
    raise BoundaryError("local_model", "unsupported_trusted_adapter")
