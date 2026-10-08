"""Preflight the optional local model backend before starting an owned operation."""

from importlib.util import find_spec

from spireagent.json_boundary import BoundaryError


def local_models_available() -> bool:
    return all(find_spec(name) is not None for name in
               ("torch", "tokenizers", "safetensors"))


def require_local_models(owner: str) -> None:
    if not local_models_available():
        raise BoundaryError(owner, "local_models_extra_required")


def recipe_dependencies_available(recipe_id: str) -> bool:
    if recipe_id == "structured-m2-cpu-v2":
        return find_spec("torch") is not None
    if recipe_id == "structured-m2-cpu-v3":
        return all(find_spec(name) is not None for name in ("torch", "safetensors"))
    return local_models_available()


def require_recipe_dependencies(recipe_id: str, owner: str) -> None:
    if not recipe_dependencies_available(recipe_id):
        raise BoundaryError(owner, "local_models_extra_required")
