"""Versioned evaluation seed plans and the collection/training exclusion query."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..game_seed import derive_game_seed, require_canonical_game_seed

MANIFEST_ROOT = Path(__file__).resolve().parent / "suites"
CHARACTERS = ("Defect", "Ironclad", "Silent", "Regent", "Necrobinder")


def load_suite(name: str) -> dict[str, Any]:
    if name not in suite_names():
        raise ValueError("unknown evaluation suite")
    value: dict[str, Any] = json.loads((MANIFEST_ROOT / f"{name}.json").read_text(encoding="utf-8"))
    character = name[4:-4] if name.startswith("gen-") else "Defect"
    count = (
        200
        if name.startswith("gen-")
        else {
            "smoke-20": 20,
            "dev-500": 500,
            "test-1000": 1000,
            "ref-20": 20,
        }[name]
    )
    purpose = (
        "generalization"
        if name.startswith("gen-")
        else {
            "smoke-20": "smoke",
            "dev-500": "development",
            "test-1000": "held_out",
            "ref-20": "paired_reference",
        }[name]
    )
    if (
        value.get("purpose") != purpose
        or value.get("version") != "1.0.0"
        or value.get("game_version") != "v0.111.0"
        or value.get("character") != character
        or value.get("ascension") != 0
        or value.get("episode_count") != count
        or value.get("host") != ("Reference" if name == "ref-20" else "Managed")
    ):
        raise ValueError("evaluation suite manifest configuration changed")
    expected = [derive_game_seed(name, i + 1) for i in range(count)]
    if (
        value["schema"] != "stpd/evaluation-suite-v1"
        or value["suite_id"] != name
        or value["seeds"] != expected
        or len(set(expected)) != len(expected)
    ):
        raise ValueError("evaluation suite manifest identity changed")
    return value


def suite_names() -> tuple[str, ...]:
    return (
        "smoke-20",
        "dev-500",
        "test-1000",
        "ref-20",
        *(f"gen-{character}-200" for character in CHARACTERS),
    )


def seed_membership(seed: str) -> tuple[str, ...]:
    """All reserved evaluation sets, including the sealed test set, without exposure."""
    canonical = require_canonical_game_seed(seed)
    return tuple(name for name in suite_names() if canonical in load_suite(name)["seeds"])


def require_non_evaluation_seed(seed: str) -> str:
    if seed_membership(seed):
        raise ValueError("evaluation seed is reserved; collection/training is forbidden")
    return require_canonical_game_seed(seed)
