"""Preflight the optional local model backend before starting an owned operation."""

from importlib.util import find_spec

from spireagent.json_boundary import BoundaryError


def require_local_models(owner: str) -> None:
    if any(find_spec(name) is None for name in ("torch", "tokenizers", "safetensors")):
        raise BoundaryError(owner, "local_models_extra_required")
