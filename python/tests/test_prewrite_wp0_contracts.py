"""P-REWRITE WP0 contract freeze: managed-native-compact-1 and stpd/rl-inference-4.

Structure is checked with each JSON Schema; the rules a schema cannot express are
checked with small reference functions here, against synthetic fixtures. WP1/WP2
(native writer, Host consumer) and WP4/WP5/WP7 (service, assembly, episodes) must
keep these fixtures passing; their own implementations are tested in their PRs.
"""

from __future__ import annotations

import copy
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
INFERENCE = PYTHON / "tests" / "fixtures" / "rl_inference_4"
REF = re.compile(r"^native-[1-9][0-9]*$")


def _validator(path: Path) -> Draft202012Validator:
    schema = json.loads(path.read_bytes())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _raw(name: str) -> list[str]:
    return (NATIVE / "fixtures" / name).read_bytes().decode("utf-8").splitlines()


def _lines(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in _raw(name)]


def _dumps(value: Any, ascii_only: bool = False) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=ascii_only)


def _sha(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


# managed-native-compact-1 reference consumer ---------------------------------


class Divergence(Exception):
    """compact_state_divergence: the game is closed, originals kept, build output stopped."""


_WS = re.compile(r"[ \t\n\r]*")
_DECODER = json.JSONDecoder()


def _members(line: str, i: int) -> list[tuple[str, int, int]]:
    """(key, value start, value end) of the JSON object starting at line[i]."""
    out = []
    i = _WS.match(line, i + 1).end()  # type: ignore[union-attr]
    while line[i] != "}":
        key, i = _DECODER.raw_decode(line, i)
        i = _WS.match(line, i).end()  # type: ignore[union-attr]
        start = _WS.match(line, i + 1).end()  # type: ignore[union-attr]
        _, end = _DECODER.raw_decode(line, start)
        out.append((key, start, end))
        i = _WS.match(line, end).end()  # type: ignore[union-attr]
        if line[i] == ",":
            i = _WS.match(line, i + 1).end()  # type: ignore[union-attr]
    return out


def body_bytes(line: str) -> dict[str, bytes]:
    """The exact bytes of each table body as the writer emitted them in this line."""
    for key, start, _ in _members(line, _WS.match(line, 0).end()):  # type: ignore[union-attr]
        if key == "objects":
            return {name: line[s:e].encode() for name, s, e in _members(line, start)}
    raise Divergence("schema_invalid")


def _root(digests: dict[str, str]) -> str:
    return _sha("".join(f"{key}\t{digests[key]}\n" for key in sorted(digests)))


def _placeholders(value: Any, found: list[str]) -> None:
    if isinstance(value, dict):
        if set(value) == {"$obj"}:
            found.append(value["$obj"])
            return
        for item in value.values():
            _placeholders(item, found)
    elif isinstance(value, list):
        for item in value:
            _placeholders(item, found)


PILES = frozenset({"draw_pile", "discard_pile", "exhaust_pile"})


def _check_piles(value: Any, in_pile: bool = False) -> None:
    """Fair piles carry no instance: no table placeholder inside any pile container."""
    if isinstance(value, dict):
        if set(value) == {"$obj"} and in_pile:
            raise Divergence("pile_placeholder")
        in_pile = in_pile or "ordering_semantics" in value
        for key, item in value.items():
            _check_piles(item, in_pile or key in PILES)
    elif isinstance(value, list):
        for item in value:
            _check_piles(item, in_pile)


def _reachable(skeleton: Any, table: dict[str, Any]) -> set[str]:
    """Keys reachable from the skeleton; a placeholder cycle diverges."""
    seen: set[str] = set()

    def visit(key: str, path: tuple[str, ...]) -> None:
        if key in path:
            raise Divergence("placeholder_cycle")
        if key not in table:
            raise Divergence("unresolved_placeholder")
        found: list[str] = []
        _placeholders(table[key], found)
        seen.add(key)
        for child in found:
            visit(child, path + (key,))

    roots: list[str] = []
    _placeholders(skeleton, roots)
    for key in roots:
        visit(key, ())
    return seen


def _inline(value: Any, table: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$obj"}:
            return _inline(table[value["$obj"]], table)
        return {key: _inline(item, table) for key, item in value.items()}
    if isinstance(value, list):
        return [_inline(item, table) for item in value]
    return value


def _valid_start(start: Any) -> bool:
    if not isinstance(start, dict):
        return False
    if start.get("kind") == "new_run":
        return len(start) == 1
    return (start.get("kind") == "from_save" and len(start) == 2
            and isinstance(start.get("save_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", start["save_sha256"]) is not None)


def reconstruct(lines: list[str], *, sampled: bool = True,
                reconcile_interval: int = 64) -> list[dict[str, Any]]:
    """Host side: rebuild each full state from raw compact lines, failing closed.

    Hot path (every frame): sequence and base, key/native_ref binding, no placeholder
    inside a pile, reachability
    and cycles, reconcile point due, and the root over the carried digests (bodies are
    not re-hashed). Sampled path (about 1% of steps, every R7 game, and every frame in
    this test): schema and each body's digest over its exact emitted bytes.
    """
    validator = _validator(NATIVE / "managed-native-compact-1.schema.json")
    table: dict[str, Any] = {}
    digests: dict[str, str] = {}
    held: int | None = None
    anchor = 0
    instance: str | None = None
    states = []
    for line in lines:
        frame = json.loads(line)
        if sampled:
            if not validator.is_valid(frame):
                raise Divergence("schema_invalid")
            for key, raw in body_bytes(line).items():
                if frame["object_digests"].get(key) != _sha(raw):
                    raise Divergence("object_digest_mismatch")
        if frame["frame"] == "full":
            if frame["seq"] != 0:
                raise Divergence("full_seq_nonzero")
            if not _valid_start(frame.get("start")):
                raise Divergence("start_invalid")
            table, digests, instance, anchor = {}, {}, frame["episode_instance"], frame["seq"]
        elif held is None or frame["episode_instance"] != instance or frame["base_seq"] != held:
            raise Divergence("base_mismatch")
        elif frame["seq"] != held + 1:
            raise Divergence("seq_gap")
        elif frame["frame"] == "delta" and frame["seq"] - anchor >= reconcile_interval:
            raise Divergence("reconcile_overdue")
        for key, body in frame["objects"].items():
            if not isinstance(body, dict) or body.get("native_ref") != key.split("#")[0]:
                raise Divergence("object_key_ref_mismatch")
            if "ordering_semantics" in body:  # a pile container is never tabled, under any key
                raise Divergence("pile_tabled")
        if set(frame["objects"]) != set(frame["object_digests"]):
            raise Divergence("digest_set_mismatch")
        if frame["frame"] == "delta":
            for key in frame["removed"]:  # writer bookkeeping must match the Host's (rules 5, 9)
                if key not in digests:
                    raise Divergence("removed_not_held")
                if key in frame["objects"]:
                    raise Divergence("removed_and_upserted")
            for key, value in frame["object_digests"].items():  # rule 5 is an iff
                if digests.get(key) == value:
                    raise Divergence("noop_upsert")
            for key in frame["removed"]:  # the Host forgets the key; it must come back as an upsert
                table.pop(key, None)
                digests.pop(key, None)
            table.update(frame["objects"])
            digests.update(frame["object_digests"])
        elif frame["frame"] == "reconcile":
            for key in frame["removed"]:
                if key not in digests or key in frame["objects"]:
                    raise Divergence("reconcile_mismatch")
            for key in frame["upserted"]:
                unchanged = digests.get(key) == frame["object_digests"].get(key)
                if key not in frame["objects"] or unchanged:
                    raise Divergence("reconcile_mismatch")
            carried = {key: value for key, value in digests.items()
                       if key not in frame["removed"]}
            for key, value in frame["object_digests"].items():
                if key not in frame["upserted"] and carried.get(key) != value:
                    raise Divergence("reconcile_mismatch")
            if set(carried) - set(frame["object_digests"]):
                raise Divergence("reconcile_mismatch")
            table, digests = dict(frame["objects"]), dict(frame["object_digests"])
            anchor = frame["seq"]
        else:
            table, digests = dict(frame["objects"]), dict(frame["object_digests"])
        _check_piles(frame["skeleton"])
        for body in table.values():
            _check_piles(body)
        if set(table) - _reachable(frame["skeleton"], table):
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


def _sealed_full(frame: dict[str, Any]) -> str:
    """Re-seal a mutated full frame (digests and root over this writer's own bytes)."""
    frame["object_digests"] = {key: _sha(_dumps(body)) for key, body in frame["objects"].items()}
    frame["objects_root_sha256"] = _root(frame["object_digests"])
    return _dumps(frame)


def test_native_compact_schema_and_reconstruction_equal_legacy() -> None:
    lines = _raw("compact-episode.jsonl")
    assert [json.loads(line)["frame"] for line in lines] == ["full", "delta", "delta", "reconcile"]
    legacy = _lines("legacy-episode.jsonl")
    assert rename(reconstruct(lines)) == rename(legacy)
    # Ref numbers differ between the two writers; equality holds only after renaming.
    assert reconstruct(lines) != legacy
    # Piles stay inline exactly as FairPileProjection writes them: no native ref anywhere.
    assert "native-" not in _dumps([state.get("draw_pile") for state in reconstruct(lines)])
    fact = json.loads(lines[3])["facts"]["combat_end"][0]
    assert fact["result"] == "victory" and fact["hp_start"] - fact["hp_end"] == 4


def test_native_compact_digest_is_over_emitted_bytes() -> None:
    plain, escaped = _raw("compact-episode.jsonl"), _raw("compact-episode-escaped.jsonl")
    assert plain != escaped and reconstruct(escaped) == reconstruct(plain)
    # Escaped bytes with digests of the unescaped bytes: a re-serialising consumer would accept.
    mixed = []
    for plain_line, escaped_line in zip(plain, escaped, strict=True):
        frame, source = json.loads(escaped_line), json.loads(plain_line)
        frame["object_digests"] = source["object_digests"]
        frame["objects_root_sha256"] = source["objects_root_sha256"]
        mixed.append(_dumps(frame, ascii_only=True))
    with pytest.raises(Divergence, match="object_digest_mismatch"):
        reconstruct(mixed)
    assert rename(reconstruct(mixed, sampled=False)) == rename(reconstruct(plain))  # hot path only


def test_native_compact_reappearing_object_is_resent() -> None:
    frames = _lines("compact-episode.jsonl")
    card = "native-12#hand"
    assert card in frames[1]["removed"] and card in frames[2]["objects"]
    with pytest.raises(Divergence, match="unresolved_placeholder"):
        reconstruct(_raw("invalid-reappear-not-resent.jsonl"))


@pytest.mark.parametrize(("name", "reason"), [
    ("invalid-missed-upsert.jsonl", "root_mismatch"),
    ("invalid-stale-bookkeeping.jsonl", "reconcile_mismatch"),
    ("invalid-wrong-base.jsonl", "base_mismatch"),
    ("invalid-orphan.jsonl", "orphan_object"),
    ("invalid-removed-not-held.jsonl", "removed_not_held"),
    ("invalid-removed-and-upserted.jsonl", "removed_and_upserted"),
    ("invalid-noop-upsert.jsonl", "noop_upsert"),
    ("invalid-reconcile-removed-not-held.jsonl", "reconcile_mismatch"),
    ("invalid-reconcile-upserted-unchanged.jsonl", "reconcile_mismatch"),
    ("invalid-full-seq-nonzero.jsonl", "full_seq_nonzero"),
])
def test_native_compact_divergence_fails_closed(name: str, reason: str) -> None:
    with pytest.raises(Divergence, match=reason):
        reconstruct(_raw(name))
    with pytest.raises(Divergence, match=reason):
        reconstruct(_raw(name), sampled=False)


@pytest.mark.parametrize(("name", "hot", "sampled"), [
    ("invalid-pile-card-in-table.jsonl", "object_key_ref_mismatch", "schema_invalid"),
    ("invalid-pile-key-with-ref.jsonl", "pile_placeholder", "schema_invalid"),
    ("invalid-pile-card-other-view.jsonl", "pile_placeholder", "pile_placeholder"),
    ("invalid-discard-view.jsonl", "pile_placeholder", "schema_invalid"),
    ("invalid-pile-container-tabled.jsonl", "pile_tabled", "pile_tabled"),
])
def test_native_compact_pile_cards_never_tabled(name: str, hot: str, sampled: str) -> None:
    with pytest.raises(Divergence, match=hot):
        reconstruct(_raw(name), sampled=False)
    with pytest.raises(Divergence, match=sampled):
        reconstruct(_raw(name))


def test_native_compact_start_is_checked_on_both_paths() -> None:
    # The hot path checks the start itself; the sampled path's schema already refuses it.
    with pytest.raises(Divergence, match="start_invalid"):
        reconstruct(_raw("invalid-start-without-save-digest.jsonl"), sampled=False)
    with pytest.raises(Divergence, match="schema_invalid"):
        reconstruct(_raw("invalid-start-without-save-digest.jsonl"))


def test_native_compact_reachability_cycles_and_reconcile_due() -> None:
    first = json.loads(_raw("compact-episode.jsonl")[0])
    island = copy.deepcopy(first)
    for ref, other in (("native-90", "native-91"), ("native-91", "native-90")):
        island["objects"][f"{ref}#enemy"] = {"native_ref": ref, "next": {"$obj": f"{other}#enemy"}}
    with pytest.raises(Divergence, match="orphan_object"):
        reconstruct([_sealed_full(island)])
    cycle = copy.deepcopy(first)
    relic = next(key for key in cycle["objects"] if key.endswith("#relic"))
    player = next(key for key in cycle["objects"] if key.endswith("#player"))
    cycle["objects"][relic]["owner"] = {"$obj": player}
    with pytest.raises(Divergence, match="placeholder_cycle"):
        reconstruct([_sealed_full(cycle)])
    with pytest.raises(Divergence, match="reconcile_overdue"):
        reconstruct(_raw("compact-episode.jsonl"), reconcile_interval=2)
    assert len(reconstruct(_raw("compact-episode.jsonl"), reconcile_interval=3)) == 4


def test_native_compact_rename_must_be_a_bijection() -> None:
    collapsed = reconstruct(_raw("invalid-not-bijective.jsonl"))
    assert rename(collapsed) != rename(_lines("legacy-episode.jsonl"))


def test_native_compact_default_output_needs_explicit_configure() -> None:
    validator = _validator(NATIVE / "managed-native-compact-1.schema.json")
    validator.validate({"cmd": "configure_output", "format": "managed-native-compact-1",
                        "reconcile_interval": 64})
    legacy = _lines("legacy-episode.jsonl")[0]
    assert not validator.is_valid(legacy)  # Today's full output is not a compact frame.


# stpd/rl-inference-4 ---------------------------------------------------------


def _inference() -> Draft202012Validator:
    return _validator(PYTHON / "schemas" / "rl-inference-4.schema.json")


def test_inference_valid_and_invalid_messages() -> None:
    validator = _inference()
    for name, message in json.loads((INFERENCE / "valid.json").read_bytes()).items():
        assert validator.is_valid(message), name
    for name, message in json.loads((INFERENCE / "invalid.json").read_bytes()).items():
        assert not validator.is_valid(message), name


_NOT_STARTED_REASONS = ("inference_timeout", "response_binding_mismatch", "refused",
                        "stop_before_send", "native_not_delivered")
_UNKNOWN_REASONS = ("delivery_unknown", "native_crash", "compact_state_divergence")


def test_inference_step_outcome_reason_fixes_its_status() -> None:
    validator = _inference()
    base = json.loads((INFERENCE / "invalid.json").read_bytes())["not_started_with_receipt"]
    base = {key: value for key, value in base.items() if key != "receipt_sha256"}
    for reason in _NOT_STARTED_REASONS + _UNKNOWN_REASONS:
        right = "not_started" if reason in _NOT_STARTED_REASONS else "unknown"
        wrong = "unknown" if right == "not_started" else "not_started"
        assert validator.is_valid({**base, "status": right, "reason": reason}), reason
        assert not validator.is_valid({**base, "status": wrong, "reason": reason}), reason
    assert set(_NOT_STARTED_REASONS + _UNKNOWN_REASONS) == set(
        json.loads((PYTHON / "schemas" / "rl-inference-4.schema.json").read_bytes())
        ["$defs"]["step_outcome"]["properties"]["reason"]["enum"])


def check_binding(row: dict[str, Any], result: dict[str, Any], open_instances: set[str],
                  segment_mu: str | None, sampling: dict[str, Any],
                  validator: Draft202012Validator) -> str:
    """Environment side, before a position becomes a BoundAction.

    accept: map selected_index to this game's BoundAction. discard_late: the game
    instance is already closed (restart or earlier timeout); count it, never apply it.
    not_started: no native action is sent and the game is closed; never retried.
    """
    if result.get("game_instance_id") not in open_instances:
        return "discard_late"
    envelope = {"schema": "stpd/rl-inference-4", "kind": "score_response", "packet_id": 1,
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
    decision_class = "combat" if row["in_combat"] else "noncombat"
    expected = sampling["classes"][decision_class]
    if result["applied"] != {"arm_id": sampling["arm_id"], "decision_class": decision_class,
                             "temperature": expected["temperature"],
                             "uniform_mix": expected["uniform_mix"]}:
        return "not_started"
    return "accept"


def test_inference_response_binding_scenarios() -> None:
    validator = _inference()
    scenarios = json.loads((INFERENCE / "binding-scenarios.json").read_bytes())
    assert {item["verdict"] for item in scenarios} == {"accept", "discard_late", "not_started"}
    for item in scenarios:
        verdict = check_binding(item["row"], item["response"], set(item["open_instances"]),
                                item["segment_mu"], item["sampling"], validator)
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
