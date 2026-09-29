"""Stage 1a recipe identities, shared by model execution and report readers."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal


@dataclass(frozen=True)
class Recipe:
    recipe_id: str
    graph: str
    family: Literal["b", "dsimple"]
    backbone: Literal["s", "pf"]
    objective: str = "rank_n"
    memory: str = "m0"


_FAMILIES: tuple[Literal["b", "dsimple"], ...] = ("b", "dsimple")
_BACKBONES: tuple[Literal["s", "pf"], ...] = ("s", "pf")

RECIPES = MappingProxyType({**{
    f"stage1a.{family}.{backbone}.v1": Recipe(
        f"stage1a.{family}.{backbone}.v1",
        "b.single-stream.v1" if family == "b" else "dsimple.vector.v1",
        family, backbone,
    )
    for family in _FAMILIES
    for backbone in _BACKBONES
}, **{
    f"stage1a.b.{backbone}.v2": Recipe(
        f"stage1a.b.{backbone}.v2", "b.shared-observation.v2", "b", backbone,
    ) for backbone in _BACKBONES
}})


def recipe_for(recipe_id: str) -> Recipe:
    try:
        return RECIPES[recipe_id]
    except KeyError as exc:
        raise ValueError("unsupported stage1a recipe") from exc
