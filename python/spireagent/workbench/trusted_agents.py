"""Code-owned Agent composition; model bytes never provide an executable selection."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from spireagent.json_boundary import BoundaryError

TRUSTED_AGENT_ADAPTERS = frozenset(
    {
        "s1-v1",
        "token-v1",
        "stpd-m2-decision-adapter",
        "stpd-s0-structured-adapter",
        "stpd-native-structured-m2-agent",
        "stpd-public-m0-decision-adapter",
        "stpd-public-m2-decision-adapter",
        "stpd-native-public-program-teacher",
        "spireagent-global-public-program",
        "stpd-p1-mstd-agent",
        "stpd-p1-baseline-agent",
    }
)
AGENT_PROFILES = (
    ("p1-global-r1", "stpd-p1-baseline-agent", "p1-global-r1", True),
    ("p1-global-r1", "stpd-p1-mstd-agent", "p1-global-r1", True),
    ("p1-global-r1", "spireagent-global-public-program", "p1-global-r1", True),
    ("s1-v1", "s1-v1", "text-menu-v1", True),
    ("text-menu-v1", "token-v1", "text-menu-v1", True),
    ("text-menu-m2-v1", "stpd-m2-decision-adapter", "text-menu-v1", True),
    ("text-menu-m2-v2", "stpd-m2-decision-adapter", "text-menu-v2", True),
    ("stpd-s0-structured-adapter", "stpd-s0-structured-adapter", "text-menu-v2", True),
    ("public-snapshot-m0-v1", "stpd-public-m0-decision-adapter", "snapshot-1", True),
    ("public-snapshot-m2-v1", "stpd-public-m2-decision-adapter", "snapshot-1", True),
    ("native-logical-v1", "stpd-native-structured-m2-agent", "native-logical-v1", True),
    ("native-logical-v1", "stpd-native-public-program-teacher", "native-logical-v1", True),
)


def agent_capabilities() -> list[dict[str, Any]]:
    return [{"profile_id": profile, "adapter_id": adapter, "input_profile": input_profile,
             "installation_available": available,
             "reason": None if available else "structured_installation_adapter_required",
             "gameplay_authority": False, "automatic_retry": False}
            for profile, adapter, input_profile, available in AGENT_PROFILES]


def trusted_agent_support(adapter_id: str) -> ModuleType:
    if adapter_id == "stpd-p1-baseline-agent":
        from stpd.p1 import baseline_installation

        return baseline_installation
    if adapter_id == "stpd-p1-mstd-agent":
        from stpd.p1 import installation as p1_installation

        return p1_installation
    if adapter_id == "spireagent-global-public-program":
        from spireagent.workbench import global_program_support
        return global_program_support
    if adapter_id == "stpd-public-m2-decision-adapter":
        from stpd import public_m2_policy_installation

        return public_m2_policy_installation
    if adapter_id == "stpd-public-m0-decision-adapter":
        from stpd import public_m0_policy_installation

        return public_m0_policy_installation
    if adapter_id == "stpd-native-public-program-teacher":
        from spireagent.workbench import native_teacher_support

        return native_teacher_support
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
