"""Thin, decision-only policy adapter boundary.

Exports load on first access so importing a specialized submodule does not load
unrelated model or training implementations.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .adapter import (
        DEFAULT_CONFIG,
        DEFAULT_MANIFEST,
        PolicyAdapter,
        PolicyAdapterError,
        adapter_code_sha256,
        serve_ndjson,
    )
    from .s1 import ResidentS1Model, S1PolicyError

__all__ = [
    "DEFAULT_MANIFEST",
    "DEFAULT_CONFIG",
    "PolicyAdapter",
    "PolicyAdapterError",
    "adapter_code_sha256",
    "ResidentS1Model",
    "S1PolicyError",
    "serve_ndjson",
]

_EXPORT_MODULES = {
    "DEFAULT_CONFIG": "adapter",
    "DEFAULT_MANIFEST": "adapter",
    "PolicyAdapter": "adapter",
    "PolicyAdapterError": "adapter",
    "adapter_code_sha256": "adapter",
    "serve_ndjson": "adapter",
    "ResidentS1Model": "s1",
    "S1PolicyError": "s1",
}


def __getattr__(name: str) -> Any:
    module = _EXPORT_MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module("." + module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
