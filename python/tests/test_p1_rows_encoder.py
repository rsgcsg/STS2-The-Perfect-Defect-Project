"""The cached rows encoder equals the reference compiler byte for byte."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from test_p1_frame_compiler import assert_frame_equal
from test_p1_input_r18 import claws, trial
from test_p1_input_r19 import sphere
from test_p1_input_v2 import public_state

from spireagent.json_boundary import BoundaryError
from stpd.p1 import rows_encoder
from stpd.p1.frame_compiler import FrameCompiler, observation_hashes
from stpd.p1.input import V2_INPUT_SPEC, V2_R18_INPUT_SPEC, V2_R19_INPUT_SPEC, project
from stpd.p1.rows_encoder import RowsEncoder
from stpd.p1.rows_encoder_wire import decode_row, encode_row, row_sha256
from stpd.p1.vocabulary import public_vocabulary

FIXTURES = [(public_state, V2_INPUT_SPEC), (trial, V2_R18_INPUT_SPEC),
            (claws, V2_R18_INPUT_SPEC), (sphere, V2_R19_INPUT_SPEC)]
# The cache scopes in `_Encoder._scope` and the visit correspondence were derived
# from these reference sources. A change to any of them is a compiler identity
# change: re-derive the scopes, re-run the corpus equivalence, then update here.
REFERENCE_SOURCES = {
    "input.py": "a532d8ec53cf9966537366a5bf899d72e14213e5948a5e68173d47bc02ef26db",
    "input_identity.py": "d72eb70d82c69f571bed445277af98f910a8766fd12126790819de42c9bef885",
    "input_order.py": "c2ba34cf658fa9bcf27c0f128fbbcc42858baf5188323af007987e2199b1f895",
    "tensor_input.py": "8799195165c453e3b80d0799120a031e3ea4c83a22a84066e80cea0105bafe7e",
}


def _reference_row(compiler, observation):
    return encode_row(compiler.compile(observation))


def _check(encoder, compiler, observation):
    raw = json.dumps(observation).encode()
    encoded = encoder.encode(raw)
    assert encoded.row == _reference_row(compiler, observation)
    assert encoded.row_sha256 == row_sha256(encoded.row)
    hashes = observation_hashes(observation)  # same names, same json_bytes definition
    assert (encoded.observation_sha256, encoded.catalog_sha256) == (
        hashes["observation_sha256"], hashes["catalog_sha256"])
    assert encoded.catalog_len == len(observation["catalog"])
    assert_frame_equal(compiler.compile(observation),
                       decode_row(encoded.row, validator=compiler,
                                  expected_sha256=encoded.row_sha256))
    return encoded


def _rich(base):
    """public_state plus every structure the assembly special-cases."""
    observation = copy.deepcopy(base)
    card = {"entity_id": "e1", "definition_id": "STRIKE_IRONCLAD", "cost": 1,
            "keywords": [{"name": "Exhaust", "description": "Remove 2 cards."}]}
    observation["extra_reads"] = [
        {"kind": "shop_catalog", "content": {
            "cards": [{"entity_id": "s1", "slot_entity_id": "slot1", "price": 50,
                       "card": {"entity_id": "c50", "definition_id": "BASH", "cost": 2}}],
            "card_removal": {"entity_id": "rm", "slot_entity_id": "slot2"}}},
        {"kind": "surface_card", "content": {**card, "display_owner_entity_id": "p1"}},
        {"kind": "surface_card", "content": {**card, "entity_id": "e2",
                                             "display_owner_entity_id": "ghost"}},
    ]
    observation["piles"] = {"content": {"zones": [
        {"ordering": "unordered_multiset", "name": "draw",
         "cards": [{"entity_id": "d1", "cost": 1, "tags": [{"k": 1}]},
                   {"entity_id": "d2", "cost": None, "tags": [{"k": 2}]}]},
        {"ordering": "native_visible_order", "name": "discard",
         "cards": [{"entity_id": "d3", "cost": 0, "tags": [{"k": 3}]},
                   {"entity_id": "d4", "cost": 0, "tags": [{"k": 3}]}]}]}}
    observation["player"] = {"content": {"entity_id": "p1", "hp": 70, "relics": [
        {"entity_id": "r1", "name": "Anchor", "counter": None,
         "card_previews": [{"entity_id": "e1", "definition_id": "STRIKE_IRONCLAD"}]}]}}
    observation["commitments"] = {"declined_reward_groups": ["g-unknown", "g-unknown"]}
    observation["map"] = {"content": {"nodes": [
        {"entity_id": f"m{i}", "kind": "monster", "edges": [{"to": i + 1}]}
        for i in range(4)]}}
    observation["deck"]["content"]["cards"].append(
        {"entity_id": "e1", "definition_id": "STRIKE_IRONCLAD", "cost": 1})
    observation["catalog"].append({"choice_id": "c11", "kind": "event.leave",
                                   "subject": "nowhere", "target": "e1", "label": "Go 3"})
    observation["status_list"] = [1, 2.5, True, None, "x", [3, [4]]]
    return observation


def _variants(observation):
    """Same subtrees at other rows, indices, positions and alias contexts."""
    out = [observation]
    shifted = copy.deepcopy(observation)
    shifted["extra_reads"].insert(1, copy.deepcopy(shifted["extra_reads"][2]))
    out.append(shifted)
    reordered = copy.deepcopy(observation)
    for zone in reordered["piles"]["content"]["zones"]:
        zone["cards"].reverse()
    reordered["map"]["content"]["nodes"].reverse()
    reordered["extra_reads"] = reordered["extra_reads"][1:]  # no shop read first
    out.append(reordered)
    dropped = copy.deepcopy(observation)
    del dropped["player"]  # its aliases become reference-only nodes
    zones = dropped["piles"]["content"]["zones"]
    zones[0]["ordering"], zones[1]["ordering"] = "native_visible_order", "unordered_multiset"
    out.append(dropped)
    keyed = copy.deepcopy(observation)
    keyed["map"]["content"]["nodes"][0] = dict(reversed(list(
        keyed["map"]["content"]["nodes"][0].items())))  # insertion order differs
    out.append(keyed)
    return out


@pytest.mark.parametrize("source,spec", FIXTURES)
def test_fixture_rows_equal_reference_cold_and_warm(source, spec):
    vocabulary = public_vocabulary(spec)
    compiler, encoder = FrameCompiler(vocabulary), RowsEncoder(vocabulary)
    first = _check(encoder, compiler, source())
    misses = encoder.cache_stats["misses"]
    second = _check(encoder, compiler, source())
    assert second == first and encoder.cache_stats["misses"] == misses


def test_replayed_units_equal_reference_in_every_context():
    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    compiler, warm = FrameCompiler(vocabulary), RowsEncoder(vocabulary)
    variants = _variants(_rich(public_state()))
    for observation in variants + variants[::-1]:
        _check(warm, compiler, observation)
        _check(RowsEncoder(vocabulary), compiler, observation)
    assert warm.cache_stats["hits"] > 0
    # The fixture reaches the special cases: reference-only nodes, kept and
    # zeroed positions in both pile orderings, shop-first read sets.
    for observation in variants:
        frame = project(observation, input_projection=V2_INPUT_SPEC)
        assert sum(e.kind == "public_reference_only" for e in frame.entities) >= 3
        assert any(e.kind == "$.piles.content.zones[].cards[]" and e.position
                   for e in frame.entities)


def test_frame_path_keeps_reference_list_numbers_and_errors():
    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    compiler, encoder = FrameCompiler(vocabulary), RowsEncoder(vocabulary)
    observation = _rich(public_state())
    observation["status_list"].append(float("nan"))  # list numerics are not checked
    assert encode_row(encoder.frame(observation)) == _reference_row(compiler, observation)
    with pytest.raises(BoundaryError, match="invalid_public_json"):
        encoder.encode(json.dumps(observation).encode())
    observation["status_list"].pop()
    observation["player"]["content"]["hp"] = float("inf")
    for raw in (json.dumps(observation).encode(), b'{"catalog": [], "hp": 1e999}'):
        with pytest.raises(BoundaryError, match="invalid_public_json"):
            encoder.encode(raw)
    with pytest.raises(BoundaryError, match="invalid_public_json"):
        compiler.encode(observation)
    with pytest.raises(ValueError, match="non-finite public numeric field"):
        compiler.compile(observation)
    with pytest.raises(ValueError, match="non-finite public numeric field"):
        encoder.frame(observation)
    wrong = public_state()
    wrong["revision"] = "r1.9"
    with pytest.raises(ValueError, match="differs from bound InputSpec"):
        encoder.frame(wrong)


def test_meta_reports_public_floor_and_combat_class():
    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    encoder = RowsEncoder(vocabulary)
    observation = public_state()
    observation["run"] = {"content": {"floor": 7}}
    observation["combat"] = {"availability": "available", "content": {}}
    encoded = encoder.encode(json.dumps(observation).encode())
    assert (encoded.floor, encoded.in_combat) == (7, True)
    observation["run"]["content"]["floor"] = 7.0
    observation["combat"]["availability"] = "unavailable"
    encoded = encoder.encode(json.dumps(observation).encode())
    assert (encoded.floor, encoded.in_combat) == (None, False)


def test_verify_every_raises_on_reference_mismatch(monkeypatch):
    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    encoder = RowsEncoder(vocabulary, verify_every=1)
    encoder.frame(public_state())
    other = FrameCompiler(vocabulary).compile(trial() | {"revision": "r1.5"})
    monkeypatch.setattr(encoder._reference, "compile", lambda observation: other)
    with pytest.raises(BoundaryError, match="rows_encoder_reference_mismatch"):
        encoder.frame(public_state())


def test_small_cache_evicts_and_stays_exact():
    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    compiler, encoder = FrameCompiler(vocabulary), RowsEncoder(vocabulary, cache_bytes=4096)
    for observation in _variants(_rich(public_state())) * 2:
        _check(encoder, compiler, observation)
    stats = encoder.cache_stats
    assert stats["bytes"] <= 4096 and stats["misses"] > stats["units"]


def test_encoder_identity_binds_codec_and_reference_compiler():
    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    encoder = RowsEncoder(vocabulary)
    identity = encoder.identity
    assert identity["row_format"] == "stpd/rows-v2-row-1"
    assert identity["encoder"] == {"implementation": "python",  # rl-inference-3 enum
                                   "sha256": rows_encoder.encoder_digest()}
    assert identity["compiler_sha256"] == FrameCompiler(vocabulary).identity["compiler_sha256"]
    assert len(identity["encoder"]["sha256"]) == 64


def test_reference_sources_are_the_ones_the_scopes_were_derived_from():
    root = Path(rows_encoder.__file__).parent
    actual = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
              for name in REFERENCE_SOURCES}
    assert actual == REFERENCE_SOURCES


@pytest.mark.skipif("STPD_ROWS_ENCODER_CORPUS" not in os.environ,
                    reason="private observation corpus (JSON lines) not supplied")
def test_private_corpus_rows_equal_reference():
    lines = Path(os.environ["STPD_ROWS_ENCODER_CORPUS"]).read_bytes().splitlines()
    vocabulary = public_vocabulary(V2_R19_INPUT_SPEC)
    compiler, encoder = FrameCompiler(vocabulary), RowsEncoder(vocabulary)
    for raw in lines:
        observation = json.loads(raw)
        encoded = encoder.encode(raw)
        assert encoded.row == _reference_row(compiler, observation)
        assert (encoded.observation_sha256, encoded.catalog_sha256) == tuple(
            observation_hashes(observation).values())


def test_catalog_binding_errors_match_frame_compiler_encode():
    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    encoder = RowsEncoder(vocabulary)
    for broken, code in (({"catalog": None}, "public_observation_and_catalog_required"),
                         ({"catalog": [{"kind": "x"}]}, "invalid_public_catalog")):
        observation = public_state() | broken
        with pytest.raises(BoundaryError, match=code):
            FrameCompiler(vocabulary).encode(observation)
        with pytest.raises(BoundaryError, match=code):
            encoder.encode(json.dumps(observation).encode())
    with pytest.raises(BoundaryError, match="public_observation_and_catalog_required"):
        encoder.encode(b"[1]")
