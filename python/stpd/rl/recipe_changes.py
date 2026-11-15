"""Declared learning-recipe changes at a continuation from a checkpoint (RL5-METHODS C, #573).

A restore normally refuses any recipe difference between the CP and the new config
(`self_contained_resume_source_config_or_generation_changed`). A continuation into a new
run directory may instead name, in `LearnerConfig.resume_recipe_changes`, the CP it
continues from and every changed key with its old (the CP's) and new (this config's)
value:

    {"checkpoint_artifact_id": "<CP id>",
     "changes": {"lr": {"old": 0.000113, "new": 0.00005}, ...}}

The declaration applies only when the learner restores exactly that CP. Restoring any
other CP (the run's own automatic resume) keeps the exact recipe check, so a second
resume inside the new run is locked to the new recipe. Pure and torch-free: the learner
owns the decision; the fast-chain preflight repeats the same rules before launch.
"""

from __future__ import annotations

import copy
from typing import Any

# key (path in the learner config) -> why a declared change is sound at a continuation.
# Every other recipe key stays locked. A continuation starts a fresh training generation:
# the queue, held segments, value-target spill and in-flight batches of the old one are
# discarded (CP-only restore), so no stored row carries the old value.
DECLARABLE: dict[str, str] = {
    "lr": "optimizer group 0 step size; Adam moments do not depend on it and are kept, "
          "the group's lr is set to the new value after the optimizer state is loaded",
    "segments.head_lr": "optimizer group 1 (RL4 heads) step size; same as lr",
    "loss.entropy_coef": "loss weight only; no state",
    "loss.hp_coef": "HP-head loss weight only; no state",
    "loss.hp_advantage_coef": "c_hp, applied when fresh segments are read; the old "
                              "generation's rows are discarded",
    "loss.hp_advantage_scope": "which rows get c_hp, applied like c_hp",
    "segments.target_kl": "per-dataset KL stop threshold; no state",
    "segments.kl_stop_scope": "which rows the KL stop measures; no state",
    "segments.value_target": "value/advantage targets of fresh segments; the old "
                             "generation's held segments and spill are discarded, the new "
                             "intake starts empty (and clears its spill directory)",
    "segments.gae_lambda": "advantage targets of fresh segments (learner and remote "
                           "prepare read it from this run's config); no stored rows",
}
# Recipe keys `_resume_config` leaves out at their default (so older CPs keep their
# digest): a missing key means this value.
RECIPE_DEFAULTS: dict[str, Any] = {
    "segments.value_target": "segment",
    "segments.kl_stop_scope": "all",
}
_MISSING = object()


def lookup(recipe: dict[str, Any], key: str) -> Any:
    """The recipe value at a dotted path (a default for keys left out at default)."""
    node: Any = recipe
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return RECIPE_DEFAULTS.get(key, _MISSING)
        node = node[part]
    return node


def _drop(recipe: dict[str, Any], key: str) -> None:
    *parents, last = key.split(".")
    node: Any = recipe
    for part in parents:
        node = node.get(part) if isinstance(node, dict) else None
    if isinstance(node, dict):
        node.pop(last, None)


def differing_paths(old: Any, new: Any, prefix: str = "") -> list[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        out: list[str] = []
        for key in sorted(set(old) | set(new), key=str):
            name = f"{prefix}{key}"
            if key not in old or key not in new:
                out.append(name)
            else:
                out += differing_paths(old[key], new[key], name + ".")
        return out
    return [] if old == new else [prefix.rstrip(".") or "<root>"]


def _same(a: Any, b: Any) -> bool:
    # 1 == True in Python; a recipe value never legitimately changes bool <-> number.
    return a == b and (type(a) is bool) == (type(b) is bool)


def validate(declaration: Any) -> dict[str, dict[str, Any]]:
    """The declaration's shape and allow-list; returns its changes."""
    if (not isinstance(declaration, dict)
            or set(declaration) != {"checkpoint_artifact_id", "changes"}
            or not isinstance(declaration["checkpoint_artifact_id"], str)
            or not declaration["checkpoint_artifact_id"]
            or not isinstance(declaration["changes"], dict) or not declaration["changes"]):
        raise ValueError("declared_recipe_changes_malformed: need checkpoint_artifact_id and "
                         "a non-empty changes table")
    for key, change in declaration["changes"].items():
        if key not in DECLARABLE:
            raise ValueError(f"declared_recipe_change_not_allowed: {key} (allowed: "
                             f"{', '.join(DECLARABLE)})")
        if not isinstance(change, dict) or set(change) != {"old", "new"}:
            raise ValueError(f"declared_recipe_changes_malformed: {key} needs exactly old, new")
    return declaration["changes"]


def check(declaration: Any, old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Accept `new` (this config's resume recipe) over `old` (the CP's) only as declared.

    Each declared key: its old value is the CP's, its new value this config's, and the two
    differ. Every other recipe key must be equal. Returns {key: {"old", "new"}}; anything
    else raises ValueError naming the key."""
    changes = validate(declaration)
    for key, change in changes.items():
        have_old, have_new = lookup(old, key), lookup(new, key)
        if have_old is _MISSING or have_new is _MISSING:
            raise ValueError(f"declared_recipe_change_key_absent: {key}")
        if not _same(change["old"], have_old):
            raise ValueError(f"declared_recipe_change_old_mismatch: {key} declared old "
                             f"{change['old']!r}, checkpoint has {have_old!r}")
        if not _same(change["new"], have_new):
            raise ValueError(f"declared_recipe_change_new_mismatch: {key} declared new "
                             f"{change['new']!r}, config has {have_new!r}")
        if _same(have_old, have_new):
            raise ValueError(f"declared_recipe_change_not_different: {key} is "
                             f"{have_old!r} in both")
    rest_old, rest_new = copy.deepcopy(old), copy.deepcopy(new)
    for key in changes:
        _drop(rest_old, key)
        _drop(rest_new, key)
    if rest_old != rest_new:
        raise ValueError("undeclared_recipe_change: "
                         + ", ".join(differing_paths(rest_old, rest_new)))
    return {key: {"old": change["old"], "new": change["new"]}
            for key, change in changes.items()}
