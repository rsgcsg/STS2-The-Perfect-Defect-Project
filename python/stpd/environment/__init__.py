"""Player Environment projection and stable-transition collection."""

from typing import TYPE_CHECKING, Any

from .collector import CollectedTransition, CollectionError, StableTransitionCollector
from .identity import MANAGED_DRIVER_PROTOCOL, environment_identity_from_managed_ready
from .projector import ProjectedDecision, ResearchProjectorV0

if TYPE_CHECKING:
    from .runtime_collection import (
        RuntimeCollection as RuntimeCollection,
    )
    from .runtime_collection import (
        collect_managed_runtime as collect_managed_runtime,
    )
    from .runtime_collection import (
        token_profile_records as token_profile_records,
    )


def __getattr__(name: str) -> Any:
    # Decision-only projection must not import Host execution and training tools.
    # Preserve the public collection exports for callers that explicitly use them.
    if name not in {"RuntimeCollection", "collect_managed_runtime", "token_profile_records"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import runtime_collection

    value = getattr(runtime_collection, name)
    globals()[name] = value
    return value


__all__ = [
    "CollectionError",
    "CollectedTransition",
    "MANAGED_DRIVER_PROTOCOL",
    "ProjectedDecision",
    "ResearchProjectorV0",
    "RuntimeCollection",
    "StableTransitionCollector",
    "environment_identity_from_managed_ready",
    "collect_managed_runtime",
    "token_profile_records",
]
