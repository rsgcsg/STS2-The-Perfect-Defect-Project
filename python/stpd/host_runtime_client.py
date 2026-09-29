"""Compatibility import for the shared, strategy-free Host Runtime package loader."""

from spireagent.host_runtime_client import (
    DEFAULT_HOST_RUNTIME,
    DEFAULT_HOST_RUNTIME_PIN,
    activate_host_runtime_client,
    load_host_runtime_pin,
)

__all__ = [
    "DEFAULT_HOST_RUNTIME",
    "DEFAULT_HOST_RUNTIME_PIN",
    "activate_host_runtime_client",
    "load_host_runtime_pin",
]
