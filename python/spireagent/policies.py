"""Explicit trusted adapter composition; downloaded manifests cannot select arbitrary code."""

from types import ModuleType

from spireagent.json_boundary import BoundaryError

SUPPORTED_ADAPTERS = frozenset({"s1-v1", "token-v1", "stpd-m2-decision-adapter"})


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
    raise BoundaryError("local_model", "unsupported_trusted_adapter")
