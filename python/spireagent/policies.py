"""Explicit trusted adapter composition; downloaded manifests cannot select arbitrary code."""

from types import ModuleType

SUPPORTED_ADAPTERS = frozenset(
    {"s1-v1", "token-v1", "stpd-m2-decision-adapter", "stpd-s0-structured-adapter",
     "stpd-native-structured-m2-agent"}
)


def policy_support(adapter: str) -> ModuleType:
    from spireagent.workbench.trusted_agents import trusted_agent_support

    return trusted_agent_support(adapter)
