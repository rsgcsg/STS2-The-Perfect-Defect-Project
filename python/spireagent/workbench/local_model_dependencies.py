"""Preflight the optional local model backend before starting an owned operation."""

from importlib.util import find_spec

from spireagent.json_boundary import BoundaryError


def local_models_available() -> bool:
    return all(find_spec(name) is not None for name in ("torch", "tokenizers", "safetensors"))


def require_local_models(owner: str) -> None:
    if not local_models_available():
        raise BoundaryError(owner, "local_models_extra_required")


def native_models_available() -> bool:
    return all(find_spec(name) is not None for name in ("torch", "safetensors"))


def require_native_models(owner: str) -> None:
    if not native_models_available():
        raise BoundaryError(owner, "native_models_extra_required")


def recipe_dependencies_available(recipe_id: str) -> bool:
    from stpd.native_training_source_spec import RECIPE as NATIVE_SAMPLED_RECIPE
    from stpd.ordered_source_spec import RECIPES

    if recipe_id == "structured-m2-cpu-v2":
        return find_spec("torch") is not None
    if recipe_id in {"structured-m2-cpu-v3", NATIVE_SAMPLED_RECIPE} or recipe_id in RECIPES:
        return all(find_spec(name) is not None for name in ("torch", "safetensors"))
    return local_models_available()


def require_recipe_dependencies(recipe_id: str, owner: str) -> None:
    if not recipe_dependencies_available(recipe_id):
        raise BoundaryError(owner, "local_models_extra_required")
