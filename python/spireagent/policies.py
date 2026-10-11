"""Explicit trusted adapter composition; downloaded manifests cannot select arbitrary code."""

from types import ModuleType

from spireagent.workbench.trusted_agents import TRUSTED_AGENT_ADAPTERS

SUPPORTED_ADAPTERS = TRUSTED_AGENT_ADAPTERS


def policy_support(adapter: str) -> ModuleType:
    from spireagent.workbench.trusted_agents import trusted_agent_support

    return trusted_agent_support(adapter)
