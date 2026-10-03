"""Explicit trusted adapter composition; downloaded manifests cannot select arbitrary code."""

from types import ModuleType

from spireagent.json_boundary import BoundaryError

PUBLIC_M0_ADAPTER = "stpd-public-m0-decision-adapter"
SUPPORTED_ADAPTERS = frozenset({
    "s1-v1", "token-v1", "stpd-m2-decision-adapter", PUBLIC_M0_ADAPTER,
})


def policy_support(adapter: str) -> ModuleType:
    if adapter == "s1-v1":
        from stpd.policy import installation

        return installation
    if adapter == "token-v1":
        from stpd import token_policy_installation

        return token_policy_installation
    if adapter == "stpd-m2-decision-adapter":
        from stpd import memory_policy_installation

        return memory_policy_installation
    if adapter == PUBLIC_M0_ADAPTER:
        from stpd import public_m0_policy_installation

        return public_m0_policy_installation
    raise BoundaryError("local_model", "unsupported_trusted_adapter")
