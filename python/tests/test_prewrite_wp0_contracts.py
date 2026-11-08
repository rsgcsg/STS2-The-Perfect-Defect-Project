"""P-REWRITE WP0 contract freeze: managed-native-compact-1 and stpd/rl-inference-3.

Structure is checked with each JSON Schema; the rules a schema cannot express are
checked with small reference functions here, against synthetic fixtures. WP1/WP2
(native writer, Host consumer) and WP4/WP5/WP7 (service, assembly, episodes) must
keep these fixtures passing; their own implementations are tested in their PRs.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

PYTHON = Path(__file__).parents[1]
NATIVE = PYTHON.parent / "contracts" / "managed-native"
INFERENCE = PYTHON / "tests" / "fixtures" / "rl_inference_3"
REF = re.compile(r"^native-[1-9][0-9]*$")


def _validator(path: Path) -> Draft202012Validator:
    schema = json.loads(path.read_bytes())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _lines(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (NATIVE / "fixtures" / name).read_text().splitlines()]


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# managed-native-compact-1 reference consumer ---------------------------------


class Divergence(Exception):
    """compact_state_divergence: the game is closed, originals kept, build output stopped."""


def _root(digests: dict[str, str]) -> str:
    return _sha("".join(f"{key}\t{digests[key]}\n" for key in sorted(digests)))


def _placeholders(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        if set(value) == {"$obj"}:
            found.add(value["$obj"])
            return
        for item in value.values():
            _placeholders(item, found)
    elif isinstance(value, list):
        for item in value:
            _placeholders(item, found)


def _inline(value: Any, table: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$obj"}:
            return _inline(table[value["$obj"]], table)
        return {key: _inline(item, table) for key, item in value.items()}
    if isinstance(value, list):
        return [_inline(item, table) for item in value]
    return value


def reconstruct(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Host side: rebuild each full state from compact frames, failing closed."""
    validator = _validator(NATIVE / "managed-native-compact-1.schema.json")
    table: dict[str, Any] = {}
    digests: dict[str, str] = {}
    held: int | None = None
    instance: str | None = None
    states = []
    for frame in frames:
        validator.validate(frame)
        if frame["frame"] == "full":
            table, digests, instance = {}, {}, frame["episode_instance"]
        elif held is None or frame["episode_instance"] != instance or frame["base_seq"] != held:
            raise Divergence("base_mismatch")
        elif frame["seq"] != held + 1:
            raise Divergence("seq_gap")
        for key, body in frame["objects"].items():
            if frame["object_digests"].get(key) != _sha(_dumps(body)):
                raise Divergence("object_digest_mismatch")
            if body.get("native_ref", key.split("#")[0]) != key.split("#")[0]:
                raise Divergence("object_key_ref_mismatch")
        if set(frame["objects"]) != set(frame["object_digests"]):
            raise Divergence("digest_set_mismatch")
        if frame["frame"] == "delta":
            for key in frame["removed"]:
                table.pop(key, None)
                digests.pop(key, None)
            table.update(frame["objects"])
            digests.update(frame["object_digests"])
        elif frame["frame"] == "reconcile":
            carried = {key: value for key, value in digests.items() if key not in frame["removed"]}
            for key, value in frame["object_digests"].items():
                if key not in frame["upserted"] and carried.get(key) != value:
                    raise Divergence("reconcile_mismatch")
            if set(carried) - set(frame["object_digests"]):
                raise Divergence("reconcile_mismatch")
            table, digests = dict(frame["objects"]), dict(frame["object_digests"])
        else:
            table, digests = dict(frame["objects"]), dict(frame["object_digests"])
        referenced: set[str] = set()
        _placeholders(frame["skeleton"], referenced)
        for body in table.values():
            _placeholders(body, referenced)
        if referenced - set(table):
            raise Divergence("unresolved_placeholder")
        if set(table) - referenced:
            raise Divergence("orphan_object")
        if _root(digests) != frame["objects_root_sha256"]:
            raise Divergence("root_mismatch")
        held = frame["seq"]
        states.append(_inline(frame["skeleton"], table))
    return states


def rename(states: list[dict[str, Any]]) -> list[str]:
    """Bijective, step-persistent relabelling of native refs by first appearance."""
    names: dict[str, str] = {}

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, str) and REF.match(value):
            return names.setdefault(value, f"ref-{len(names) + 1}")
        return value

    return [_dumps(walk(state)) for state in states]


def test_native_compact_schema_and_reconstruction_equal_legacy() -> None:
    frames = _lines("compact-episode.jsonl")
    assert [frame["frame"] for frame in frames] == ["full", "delta", "reconcile"]
    assert rename(reconstruct(frames)) == rename(_lines("legacy-episode.jsonl"))
    # Ref numbers differ between the two writers; equality holds only after renaming.
    assert reconstruct(frames) != _lines("legacy-episode.jsonl")
    fact = frames[2]["facts"]["combat_end"][0]
    assert fact["result"] == "victory" and fact["hp_start"] - fact["hp_end"] == 4


@pytest.mark.parametrize(("name", "reason"), [
    ("invalid-missed-upsert.jsonl", "root_mismatch"),
    ("invalid-stale-bookkeeping.jsonl", "reconcile_mismatch"),
    ("invalid-wrong-base.jsonl", "base_mismatch"),
    ("invalid-orphan.jsonl", "orphan_object"),
])
def test_native_compact_divergence_fails_closed(name: str, reason: str) -> None:
    with pytest.raises(Divergence, match=reason):
        reconstruct(_lines(name))


def test_native_compact_rename_must_be_a_bijection() -> None:
    collapsed = reconstruct(_lines("invalid-not-bijective.jsonl"))
    assert rename(collapsed) != rename(_lines("legacy-episode.jsonl"))


def test_native_compact_default_output_needs_explicit_configure() -> None:
    validator = _validator(NATIVE / "managed-native-compact-1.schema.json")
    validator.validate({"cmd": "configure_output", "format": "managed-native-compact-1",
                        "reconcile_interval": 64})
    legacy = _lines("legacy-episode.jsonl")[0]
    assert not validator.is_valid(legacy)  # Today's full output is not a compact frame.


# stpd/rl-inference-3 ---------------------------------------------------------


def _inference() -> Draft202012Validator:
    return _validator(PYTHON / "schemas" / "rl-inference-3.schema.json")


def test_inference_valid_and_invalid_messages() -> None:
    validator = _inference()
    for name, message in json.loads((INFERENCE / "valid.json").read_bytes()).items():
        assert validator.is_valid(message), name
    for name, message in json.loads((INFERENCE / "invalid.json").read_bytes()).items():
        assert not validator.is_valid(message), name


def check_binding(row: dict[str, Any], result: dict[str, Any], open_instances: set[str],
                  segment_mu: str | None, validator: Draft202012Validator) -> str:
    """Environment side, before a position becomes a BoundAction.

    accept: map selected_index to this game's BoundAction. discard_late: the game
    instance is already closed (restart or earlier timeout); count it, never apply it.
    not_started: no native action is sent and the game is closed; never retried.
    """
    if result.get("game_instance_id") not in open_instances:
        return "discard_late"
    envelope = {"schema": "stpd/rl-inference-3", "kind": "score_response", "packet_id": 1,
                "results": [result]}
    if not validator.is_valid(envelope) or result["status"] != "scored":
        return "not_started"
    if (result["request_id"], result["game_instance_id"], result["catalog_sha256"]) != (
            row["request_id"], row["game_instance_id"], row["catalog_sha256"]):
        return "not_started"
    q = result["behavior_q"]
    if (len(q) != row["catalog_len"] or not 0 <= result["selected_index"] < len(q)
            or not math.isclose(sum(q), 1.0, abs_tol=1e-6)
            or q[result["selected_index"]] != result["selected_q"]):
        return "not_started"
    if segment_mu is not None and result["mu_snapshot_sha256"] != segment_mu:
        return "not_started"
    return "accept"


def test_inference_response_binding_scenarios() -> None:
    validator = _inference()
    scenarios = json.loads((INFERENCE / "binding-scenarios.json").read_bytes())
    assert {item["verdict"] for item in scenarios} == {"accept", "discard_late", "not_started"}
    for item in scenarios:
        verdict = check_binding(item["row"], item["response"], set(item["open_instances"]),
                                item["segment_mu"], validator)
        assert verdict == item["verdict"], item["name"]


def test_inference_request_rows_carry_identity_triple_and_combat_class() -> None:
    valid = json.loads((INFERENCE / "valid.json").read_bytes())
    rows = valid["score_request"]["rows"]
    results = {item["request_id"]: item for item in valid["score_response"]["results"]}
    for row in rows:
        result = results[row["request_id"]]
        assert (result["game_instance_id"], result["catalog_sha256"]) == (
            row["game_instance_id"], row["catalog_sha256"])
        if result["status"] == "scored":
            expected = "combat" if row["in_combat"] else "noncombat"
            assert result["applied"]["decision_class"] == expected
