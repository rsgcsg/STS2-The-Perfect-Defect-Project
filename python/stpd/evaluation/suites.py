"""Versioned evaluation seed plans and the collection/training exclusion query."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..game_seed import derive_game_seed, require_canonical_game_seed

MANIFEST_ROOT = Path(__file__).resolve().parent / "suites"
FOUR_AGENT_SUITE_ID = "ref-first-four-10-20261023"
CHARACTERS = ("Defect", "Ironclad", "Silent", "Regent", "Necrobinder")
CHECKPOINT_SUITE_ID = "ref-checkpoints-20261024"


def checkpoint_reference_manifest(game_version: str, character: str = "Defect",
                                  ascension: int = 0) -> dict[str, Any]:
    """One frozen fresh paired seed set; model selection is a separate input."""
    four_agent_reference_manifest(game_version)
    if (character not in CHARACTERS or type(ascension) is not int
            or ascension not in {0, 10} or ascension == 10 and character != "Defect"):
        raise ValueError("unsupported checkpoint evaluation scenario")
    count = 10 if character == "Defect" and ascension == 0 else 2
    name = f"{CHECKPOINT_SUITE_ID}-{character}-a{ascension}"
    return {"schema": "stpd/evaluation-suite-v1", "suite_id": name, "version": "1.0.0",
            "game_version": game_version, "character": character, "ascension": ascension,
            "host": "Reference", "purpose": "checkpoint_reference", "episode_count": count,
            "seed_derivation": "derive_game_seed(suite_id, ordinal_1_based)",
            "seeds": [derive_game_seed(name, i) for i in range(1, count + 1)]}


def four_agent_reference_manifest(game_version: str) -> dict[str, Any]:
    """A new ten-seed reservation; bind the actual game version before use."""
    if (not isinstance(game_version, str) or not game_version.startswith("v")
            or not 2 <= len(game_version) <= 80
            or any(not (c.isascii() and (c.isalnum() or c in ".-+")) for c in game_version)):
        raise ValueError("explicit exact game version required")
    return {"schema": "stpd/evaluation-suite-v1", "suite_id": FOUR_AGENT_SUITE_ID,
            "version": "1.0.0", "game_version": game_version, "character": "Defect",
            "ascension": 0, "host": "Reference", "purpose": "paired_reference",
            "episode_count": 10,
            "seed_derivation": "derive_game_seed(suite_id, ordinal_1_based)",
            "seeds": [derive_game_seed(FOUR_AGENT_SUITE_ID, i) for i in range(1, 11)]}


def diagnostic_reference_manifest(game_version: str, group_id: str, count: int) -> dict[str, Any]:
    """Small engineering group, separate from every reserved evaluation suite."""
    # Reuse the existing exact-version grammar, retaining the final suite unchanged.
    four_agent_reference_manifest(game_version)
    if (not isinstance(group_id, str) or len(group_id) != 32
            or any(c not in "0123456789abcdef" for c in group_id)
            or type(count) is not int or not 1 <= count <= 10):
        raise ValueError("diagnostic group identity and 1..10 seeds required")
    suite_id = "ref-diagnostic-" + group_id
    seeds = [derive_game_seed(suite_id, i) for i in range(1, count + 1)]
    if any(seed_membership(seed) for seed in seeds):
        raise ValueError("diagnostic seed overlaps a reserved evaluation seed")
    return {"schema": "stpd/evaluation-suite-v1", "suite_id": suite_id,
            "version": "1.0.0", "group_id": group_id, "game_version": game_version,
            "character": "Defect", "ascension": 0, "host": "Reference",
            "purpose": "diagnostic_reference", "episode_count": count,
            "seed_derivation": "derive_game_seed(suite_id, ordinal_1_based)", "seeds": seeds}


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
    legacy = tuple(name for name in suite_names() if canonical in load_suite(name)["seeds"])
    reserved = tuple(derive_game_seed(FOUR_AGENT_SUITE_ID, i) for i in range(1, 11))
    checkpoint = tuple(m["suite_id"] for m in (
        *(checkpoint_reference_manifest("v0.111.0", character) for character in CHARACTERS),
        checkpoint_reference_manifest("v0.111.0", "Defect", 10),
    ) if canonical in m["seeds"])
    return legacy + ((FOUR_AGENT_SUITE_ID,) if canonical in reserved else ()) + checkpoint


def require_non_evaluation_seed(seed: str) -> str:
    if seed_membership(seed):
        raise ValueError("evaluation seed is reserved; collection/training is forbidden")
    return require_canonical_game_seed(seed)
