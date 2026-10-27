"""Representation boundaries: sets, full public alphabet, append-only transfer."""
from __future__ import annotations

import copy

import pytest
import torch
from test_p1_t0 import observation

from stpd.p1.export import export_model, load_model
from stpd.p1.input import INPUT_SPEC, SORTED_INPUT_SPEC, V2_INPUT_SPEC, project
from stpd.p1.model import MStd
from stpd.p1.vocabulary import (
    extend_parameters,
    parameters_from_checkpoint,
    public_vocabulary,
    require_extension,
)


def public_state():
    value = observation()
    value["scene"]["content"].pop("entities")
    cards = [{"entity_id": "e1", "definition_id": "STRIKE_IRONCLAD", "cost": 1},
             {"entity_id": "e2", "definition_id": "BASH", "cost": 2}]
    value["deck"] = {"content": {"cards": cards}}
    value["scene"]["native_kind"] = "combat_turn"
    value["scene"]["content"]["surface"] = {"playable_cards": [
        {"entity_id": "e1", "target_entity_ids": ["e2", "e1"]}]}
    return value


@pytest.mark.parametrize("projection", [SORTED_INPUT_SPEC, V2_INPUT_SPEC])
def test_shuffle_and_aliases_preserve_scores(projection):
    torch.set_num_threads(2)
    state = public_state()
    model = MStd(public_vocabulary()).eval()
    frame = project(state, input_projection=projection)
    changed = copy.deepcopy(state)
    changed["deck"]["content"]["cards"].reverse()
    changed["catalog"].reverse()
    changed["scene"]["content"]["surface"]["playable_cards"][0]["target_entity_ids"].reverse()
    def aliases(value):
        if isinstance(value, dict):
            return {k: aliases(v) for k, v in value.items()}
        if isinstance(value, list):
            return [aliases(v) for v in value]
        return ({"e1": "totally-new-x", "e2": "totally-new-y"}.get(value, value)
                if isinstance(value, str) else value)
    other = project(aliases(changed), input_projection=projection)
    with torch.inference_mode():
        left, right = model(frame), model(other).flip(0)
    torch.testing.assert_close(left, right, rtol=3e-4, atol=2e-5)
    assert len(frame.entities) == len(project(state).entities)


def test_ordered_hand_keeps_positions_and_v1_is_unchanged():
    state = public_state()
    state["combat"] = {"content": {"player": {"hand": state["deck"]["content"]["cards"]}}}
    frame = project(state, input_projection=V2_INPUT_SPEC)
    hand = [e.position for e in frame.entities if e.kind == "$.combat.content.player.hand[]"]
    assert sorted(hand) == [0, 1]
    original = project(state, input_projection=INPUT_SPEC)
    assert [e.position for e in original.entities if e.kind == "$.deck.content.cards[]"] == [0, 1]
    assert {e.position for e in frame.entities if e.kind == "$.deck.content.cards[]"} == {0}


def test_public_ids_and_text_are_fixed_for_other_characters():
    vocabulary = public_vocabulary()
    model = MStd(vocabulary)
    for term in ("BASH", "STRIKE_IRONCLAD", "STRIKE_SILENT", "STRIKE_REGENT", "STRIKE_NECROBINDER"):
        assert model.index("ids", term) > 0
    for text in ("新的公开说明：获得 12 格挡。", "Gain 17 Block. 🃏", ""):
        assert 1 not in model.tokenize(text)[0]
    assert public_vocabulary() == vocabulary


def test_append_vocabulary_old_package_and_continued_update(tmp_path):
    torch.set_num_threads(2)
    old = public_vocabulary()
    from stpd.p1.formal_training import MStdTrainer, Population
    from stpd.p1.spec import MStdConfig

    frame = project(public_state(), input_projection=V2_INPUT_SPEC)
    train = Population((frame,), (0,), ("train-point",))
    dev = Population((frame,), (0,), ("dev-point",))
    trainer = MStdTrainer({"train": train, "dev": dev, "test": Population((), (), ())},
                         old, "a" * 64, MStdConfig(max_epochs=2))
    trainer.advance()
    checkpoint = tmp_path / "checkpoint"
    checkpoint.write_bytes(trainer.checkpoint())
    model = parameters_from_checkpoint(checkpoint.read_bytes(), old).eval()
    descriptor = export_model(model, checkpoint, tmp_path / "package", trainer.identity)
    loaded, _ = load_model(tmp_path / "package/model.tensor-tree", descriptor["package_sha256"])
    new = copy.deepcopy(old)
    for table, term in (("ids", "NEW_PUBLIC_CARD"), ("fields", "new_public_field"),
                        ("kinds", "$.new_public_body"), ("actions", "new.public.action")):
        new[table].append(term)
    new["vocabulary_version"] += 1
    expanded = extend_parameters(loaded, new)
    for name, tensor in loaded.state_dict().items():
        grown = expanded.state_dict()[name]
        expected = tensor.clone()
        if name == "ids.weight":
            expected[len(old["ids"]):len(new["ids"])].zero_()
        assert torch.equal(expected, grown[:tensor.shape[0]]
                           if grown.shape != tensor.shape else grown)
    assert torch.count_nonzero(expanded.fields.weight[-1]) == 0
    assert torch.count_nonzero(expanded.ids.weight[len(old["ids"]):len(new["ids"])]) == 0
    torch.testing.assert_close(loaded(frame), expanded(frame), rtol=0, atol=0)
    optimizer = torch.optim.AdamW(expanded.parameters(), lr=3e-4)
    expanded.train()
    loss = -expanded(frame).log_softmax(0)[0]
    loss.backward()
    optimizer.step()
    assert torch.isfinite(loss)
    assert any(not torch.equal(v, expanded.state_dict()[k][:v.shape[0]]
                              if expanded.state_dict()[k].shape != v.shape
                              else expanded.state_dict()[k])
               for k, v in loaded.state_dict().items())
    swapped = copy.deepcopy(new)
    swapped["fields"][1:3] = reversed(swapped["fields"][1:3])
    with pytest.raises(ValueError, match="preserve every old row"):
        require_extension(old, swapped)


def test_v2_real_worker_pause_export_and_private_agent_binding(tmp_path):
    from test_artifact_store_v1 import PRODUCER
    from test_p1_workload import small_run

    from stpd.cloud_jobs.contracts import ComputeRequest
    from stpd.cloud_jobs.execution import dispatch, validate_receipt
    from stpd.p1.agent import installation
    from stpd.p1.checkpoint_export import publish_checkpoint_model
    from stpd.p1.dataset import SnapshotReader
    from stpd.p1.export import register
    from stpd.p1.spec import MStdConfig
    from stpd.p1.workload import prepare_p1_run, verify_p1_run
    from stpd.workers.p1_run import materialize_p1_model

    store, snapshot, _ = small_run(tmp_path)
    reader = SnapshotReader(store, snapshot.artifact_id)
    weights = {"schema": "stpd/p1-game-loss-weights-1", "rule_id": "b" * 64,
               "definition": {"rule": "a10-progress-v1", "maximum_multiplier": 2},
               "weights_by_root": {p["root_run_id"]: 1.5 for p in reader.selection["points"]
                                   if p["split"] == "train"}}
    run = prepare_p1_run(store, snapshot.artifact_id, PRODUCER, MStdConfig(max_epochs=2),
                         "b" * 32, input_projection=V2_INPUT_SPEC, loss_weighting=weights)
    workload = verify_p1_run(store, run.artifact_id, PRODUCER)
    assert workload.vocabulary == public_vocabulary()
    assert workload.training.parameters.value()["loss_weighting"] == weights
    request = ComputeRequest("training", run.artifact_id, PRODUCER, "v2-smoke", stop_after=1)
    receipt = dispatch(store, request, PRODUCER)
    validate_receipt(store, request, receipt)
    assert receipt.state == "paused" and receipt.checkpoint_id
    exported = publish_checkpoint_model(store, receipt.checkpoint_id, PRODUCER, selection="current")
    descriptor = materialize_p1_model(store, exported["model_id"], tmp_path / "package")
    private = tmp_path / "registration"
    register(tmp_path / "package", private, {
        "schema": "sts2.policy-runtime/agent-manifest-1",
        "input": {"profile": "p1-global-r1", "global": {"revision": "r1.5"}},
    })
    config, _, model, _ = installation(private / "config.json", private / "manifest.json", private)
    assert config["input_projection"] == V2_INPUT_SPEC
    assert descriptor["input_spec"]["id"] == V2_INPUT_SPEC["id"]
    row = next(reader.iter_points())[1]
    with torch.inference_mode():
        scores = model(project(row, input_projection=V2_INPUT_SPEC))
    assert bool(torch.isfinite(scores).all())


def test_v2_only_declared_alias_paths_become_references():
    state = public_state()
    state["scene"]["content"]["surface"]["room_entity_id"] = "room-alias"
    state["scene"]["content"]["extension"] = {"literal_id": "room-alias"}
    frame = project(state, input_projection=V2_INPUT_SPEC)
    extension = next(e for e in frame.entities if e.kind == "$.scene.content.extension")
    assert extension.identities == [("literal_id", "room-alias")]
    assert extension.refs == []
    assert all(value != "room-alias" for e in frame.entities if e is not extension
               for _, value in e.identities + e.text)


def test_v2_request_is_preparation_only_and_does_not_select_a_cohort():
    from stpd.p1.input_cli import request

    first = request(max_seconds=18000, max_epochs=15, stop_after=None)
    second = request(max_seconds=900, max_epochs=15, stop_after=8)
    assert first["input_projection"] == second["input_projection"] == V2_INPUT_SPEC
    assert first["config"]["batch_size"] == 64
    assert first["config"]["dtype"] == "float32"
    assert first["stop_after"] is None and second["stop_after"] == 8
    assert first["intent_id"] != second["intent_id"]
    assert "source_id" not in first and "loss_weighting" not in first
    with pytest.raises(ValueError, match="300 second"):
        request(max_seconds=300, max_epochs=15, stop_after=8)


def test_public_builder_extends_rows_and_keeps_the_tokenizer(tmp_path):
    import hashlib
    import importlib.util
    import json
    from pathlib import Path

    path = Path(__file__).parents[1] / "tools/build_p1_public_vocabulary.py"
    spec = importlib.util.spec_from_file_location("public_builder", path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    localization = tmp_path / "localization"
    files = []
    for language in ("eng", "zhs"):
        (localization / language).mkdir(parents=True)
        for table in builder.TABLES:
            raw = json.dumps({"NEW_PUBLIC_TERM.title": "Public example"}).encode()
            (localization / language / (table + ".json")).write_bytes(raw)
            files.append({"language": language, "table": table + ".json",
                          "sha256": hashlib.sha256(raw).hexdigest()})
    (localization / "manifest.json").write_text(json.dumps({
        "source_pack_sha256": "b" * 64, "files": files}))
    metadata = tmp_path / "types.txt"
    metadata.write_text("Class MegaCrit.Sts2.Core.Models.Cards.NewPublicCard\n")
    shapes = tmp_path / "shapes.json"
    shapes.write_text(json.dumps({"kinds": ["$.new_public_body"], "fields": ["new_public_field"]}))
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    (sdk / "reducer.ts").write_text('const example = "combat.play_card";')
    old = public_vocabulary()
    base = tmp_path / "base.json"
    base.write_text(json.dumps(old))
    new = builder.build(localization, metadata, shapes, sdk, base, "a" * 64)
    require_extension(old, new)
    assert new["tokenizer"] == old["tokenizer"]
    assert new["vocabulary_version"] == old["vocabulary_version"] + 1
    assert new["ids"].index("NEW_PUBLIC_CARD") >= len(old["ids"])
    assert new["fields"].index("new_public_field") >= len(old["fields"])


def test_legacy_potion_popup_native_aliases_are_not_vocabulary_terms():
    state = public_state()
    state["scene"] = {"native_kind": "potion_popup", "family": "other", "content": {
        "surface": {"potion_entity_id": "potion_" + "a" * 48,
                    "definition_id": "BLOCK_POTION", "use_target_entity_ids": ["player_alias"]}}}
    state["player"] = {"content": {"entity_id": "player_alias", "potions": [
        {"entity_id": "potion_" + "a" * 48, "definition_id": "BLOCK_POTION"}]}}
    state["catalog"] = [{"choice_id": "use", "kind": "potion.use",
                         "subject": "potion_" + "a" * 48, "target": "player_alias", "mapped": True}]
    original = copy.deepcopy(state)
    frame = project(state, input_projection=V2_INPUT_SPEC)
    assert state == original
    for entity in frame.entities:
        assert all(value not in {"potion_" + "a" * 48, "player_alias"}
                   for _, value in entity.identities + entity.text)
    popup = next(e for e in frame.entities if e.kind == "$.scene.content.surface")
    assert ("potion_entity_id", "potion_" + "a" * 48) not in popup.identities
    target = next(e for e in frame.entities
                  if e.kind == "$.scene.content.surface.use_target_entity_ids[]")
    assert target.refs and ("value", "<REF>") in target.text


@pytest.mark.parametrize("projection", [SORTED_INPUT_SPEC, V2_INPUT_SPEC])
def test_reordered_public_views_do_not_choose_a_first_body_anchor(projection):
    state = public_state()
    state["scene"]["native_kind"] = "native_combat_pile_selection"
    state["scene"]["content"]["surface"] = {
        "cards": [{"entity_id": "e1", "definition_id": "BASH", "description": "Deal 8 damage."}],
        "selectable_card_entity_ids": ["e1", "e2"]}
    state["extra_reads"] = [
        {"kind": "surface_card", "content": {"entity_id": "e1", "definition_id": "BASH",
         "display_context": "selector_original:combat_pile", "description": "Deal 8 damage."}},
        {"kind": "surface_card", "content": {"entity_id": "e1", "definition_id": "BASH",
         "display_context": "discard", "description": "Deal 12 damage."}},
        {"kind": "surface_card", "content": {"entity_id": "e2", "definition_id": "STRIKE_IRONCLAD",
         "display_context": "discard", "description": "Deal 6 damage."}}]
    changed = copy.deepcopy(state)
    changed["extra_reads"].reverse()
    left, right = [project(value, input_projection=projection) for value in (state, changed)]
    from stpd.p1.input import fit_vocabulary

    vocabulary = (public_vocabulary() if projection == V2_INPUT_SPEC
                  else fit_vocabulary([left, right]))
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(0)
        model = MStd(vocabulary).eval()
    with torch.inference_mode():
        a, b = model(left), model(right)
    torch.testing.assert_close(a, b, rtol=3e-4, atol=2e-5)
    assert len(left.entities) == len(right.entities)


def test_literal_extension_keys_do_not_impersonate_public_paths():
    state = public_state()
    state["scene"]["content"]["surface.room_entity_id"] = "literal-extension-public-fact"
    state["scene"]["content"]["extension"] = {"schema": "literal-public-schema",
                                              "choice_id": "literal-public-choice"}
    state["deck.content.cards"] = [{"name": "First"}, {"name": "Second"}]
    frame = project(state, input_projection=V2_INPUT_SPEC)
    assert any(value == "literal-extension-public-fact" for e in frame.entities
               for _, value in e.identities + e.text)
    assert any(value == "literal-public-schema" for e in frame.entities for _, value in e.text)
    assert any(value == "literal-public-choice" for e in frame.entities
               for _, value in e.identities)
    literal = [e for e in frame.entities if e.kind == '$["deck.content.cards"][]']
    assert [e.position for e in literal] == [0, 1]
