"""Explicit combined input migration, including the normal CPU training paths.

Synthetic public Sphere DTOs test the research consumer. Native visibility,
legality and natural completion belong to the Managed producer integration gate.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import time

import pytest
import torch
from test_artifact_store_v1 import PRODUCER
from test_p1_input_r18 import claws, trial
from test_p1_input_v2 import public_state
from test_p1_snapshot import projection_fixture, store_at

from spireagent.json_boundary import BoundaryError
from stpd.p1.agent import installation
from stpd.p1.export import compatibility_package, input_spec, load_model, register
from stpd.p1.input import (
    V2_INPUT_SPEC,
    V2_R18_INPUT_SPEC,
    V2_R19_INPUT_SPEC,
    project,
    training_projection,
)
from stpd.p1.input_cli import request
from stpd.p1.input_identity import identity_paths
from stpd.p1.tensor_input import compile_frame
from stpd.p1.vocabulary import public_vocabulary

pytest_plugins = ("test_p1_input_r18",)


def sphere():
    state = {**public_state(), "revision": "r1.9"}
    cells = [{"entity_id": f"cell-{x}-{y}", "x": x, "y": y, "cleared": x == 0,
              **({"empty": y != 2} if x == 0 else {})}
             for y in range(11) for x in range(11)]
    tools = [{"entity_id": f"tool-{tool}", "tool": tool, "label": label}
             for tool, label in (("Small", "Small divination"), ("Big", "Big divination"))]
    state["scene"] = {"family": "event", "native_kind": "crystal_sphere", "content": {
        "context": {"kind": "event", "event_id": "CRYSTAL_SPHERE"}, "surface": {
            "kind": "crystal_sphere", "sphere_entity_id": "sphere-owner",
            "event_entity_id": "sphere-event", "room_entity_id": "sphere-room",
            "width": 11, "height": 11, "remaining_divinations": 3, "selected_tool": "Big",
            "cells": cells, "tools": tools, "visible_items": [{
                "entity_id": "item-gold", "kind": "small_gold", "label": "Small gold",
                "x": 0, "y": 2, "width": 2, "height": 1,
                "cleared_cells": [{"x": 0, "y": 2}], "fully_revealed": False,
            }],
        }}}
    state["catalog"] = [{"choice_id": f"c-{tool['tool']}-{cell['x']}-{cell['y']}",
        "kind": "event.choose", "subject": cell["entity_id"], "target": tool["entity_id"],
        "label": f"{tool['label']} at ({cell['x']}, {cell['y']})", "mapped": True}
        for tool in tools for cell in cells]
    return state


@pytest.fixture(scope="module")
def package(packages):
    root, old, _, descriptor, _, _ = packages
    rebound = root / "r19"
    receipt = compatibility_package(old, rebound, revision="r1.9")
    model, payload = load_model(rebound / "model.tensor-tree", receipt["package_sha256"])
    return old, descriptor, rebound, receipt, model, payload


def test_compatibility_keeps_old_api_bytes_weights_tables_and_tokenizer(package, packages,
                                                                     tmp_path):
    old, descriptor, _, receipt, model, payload = package
    before, original = load_model(old / "model.tensor-tree", descriptor["package_sha256"])
    assert payload["vocabulary"] == {
        **original["vocabulary"], "input_spec": V2_R19_INPUT_SPEC}
    for name, value in before.state_dict().items():
        assert torch.equal(value, model.state_dict()[name]), name
    assert receipt["training_admission_changed"] is False
    _, _, r18, _, old_receipt, _ = packages
    other = tmp_path / "old-api"
    assert compatibility_package(old, other) == old_receipt
    assert {p.name: p.read_bytes() for p in r18.iterdir()} == {
        p.name: p.read_bytes() for p in other.iterdir()}
    with pytest.raises(ValueError, match="unsupported compatibility revision"):
        compatibility_package(old, tmp_path / "invalid", revision="r1.7")


def test_old_non_sphere_input_tensors_and_model_scores_are_bitwise_identical(package):
    old, descriptor, _, _, current, _ = package
    before, _ = load_model(old / "model.tensor-tree", descriptor["package_sha256"])
    state = public_state()
    left = project(state, input_projection=V2_INPUT_SPEC)
    right = project({**state, "revision": "r1.9"}, input_projection=V2_R19_INPUT_SPEC)
    assert left == right
    a, b = compile_frame(left, before), compile_frame(right, current)
    for key in a.tensors:
        assert torch.equal(a.tensors[key], b.tensors[key]), key
    with torch.inference_mode():
        assert torch.equal(before(left), current(right))
    for source in (trial(), claws()):
        previous = project(source, input_projection=V2_R18_INPUT_SPEC)
        migrated = project({**source, "revision": "r1.9"}, input_projection=V2_R19_INPUT_SPEC)
        assert previous == migrated
    assert training_projection(V2_R19_INPUT_SPEC) == V2_R19_INPUT_SPEC
    with pytest.raises(ValueError, match="new training requires"):
        training_projection(V2_R18_INPUT_SPEC)


def test_sphere_full_catalog_scores_aliases_and_new_facts_are_consumed(package):
    model = package[4]
    state = sphere()
    frame = project(state, input_projection=V2_R19_INPUT_SPEC)
    assert len(frame.choices) == 242
    assert all(source != -1 and target != -1 for source, target in frame.roles)
    with torch.inference_mode():
        scores = model(frame)
    assert scores.shape == (242,) and torch.isfinite(scores).all()
    root = "scene.content.surface."
    relation = identity_paths(state)
    for field in ("sphere_entity_id", "event_entity_id", "room_entity_id", "cells[0].entity_id",
                  "visible_items[0].entity_id", "tools[0].entity_id"):
        assert relation(root + field) == "declared"
        assert identity_paths({**state, "revision": "r1.8"})(root + field) is None
    aliases = {entity: "renamed-" + entity for entity in (
        "sphere-owner", "sphere-event", "sphere-room", "item-gold", "tool-Small", "tool-Big",
        *(cell["entity_id"] for cell in state["scene"]["content"]["surface"]["cells"]))}

    def rename(value):
        if isinstance(value, dict):
            return {key: rename(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rename(item) for item in value]
        return aliases.get(value, value) if isinstance(value, str) else value

    other = project(rename(state), input_projection=V2_R19_INPUT_SPEC)
    assert frame.entities == other.entities and frame.roles == other.roles
    changed = copy.deepcopy(state)
    surface = changed["scene"]["content"]["surface"]
    surface["visible_items"][0]["label"] = "Curse"
    surface["visible_items"][0]["fully_revealed"] = True
    changed_frame = project(changed, input_projection=V2_R19_INPUT_SPEC)
    assert changed_frame.entities != frame.entities
    with torch.inference_mode():
        assert not torch.equal(scores, model(changed_frame))
    surface["extension_entity_id"] = "literal-owner"
    literal = project(changed, input_projection=V2_R19_INPUT_SPEC)
    assert any(("extension_entity_id", "literal-owner") in entity.identities
               for entity in literal.entities)


def test_registration_cli_and_explicit_training_request(package, tmp_path):
    old, _, rebound, receipt, _, _ = package
    root = tmp_path / "registration"
    manifest = {"schema": "sts2.policy-runtime/agent-manifest-1", "input": {
        "profile": "p1-global-r1", "global": {"revision": "r1.9"}}}
    register(rebound, root, manifest)
    config, bound, model, _ = installation(root / "config.json", root / "manifest.json", root)
    assert config["input_projection"] == V2_R19_INPUT_SPEC
    assert bound["input"]["global"]["revision"] == "r1.9"
    with torch.inference_mode():
        scores = model(project(sphere(), input_projection=config["input_projection"]))
        assert torch.isfinite(scores).all()
    result = subprocess.run([sys.executable, "-m", "stpd.p1.input_cli", "rebind-r19",
        "--package", str(old), "--output", str(tmp_path / "cli")],
        check=True, capture_output=True, text=True)
    output = json.loads(result.stdout)
    assert output["receipt"] == receipt and output["activated"] is False
    assert request(max_seconds=600, max_epochs=2, stop_after=1)["input_projection"] == V2_INPUT_SPEC
    assert request(max_seconds=600, max_epochs=2, stop_after=1,
                   input_projection=V2_R19_INPUT_SPEC)["input_projection"] == V2_R19_INPUT_SPEC


def test_explicit_r19_store_tensor_pack_worker_and_gradients(tmp_path):
    from stpd.cloud_jobs.contracts import ComputeRequest
    from stpd.cloud_jobs.execution import dispatch, validate_receipt
    from stpd.p1.dataset import inspect_projections, publish_snapshot
    from stpd.p1.formal_training import MStdTrainer
    from stpd.p1.spec import MStdConfig
    from stpd.p1.workload import prepare_p1_run, verify_p1_run
    from stpd.workers.p1_run import verify_p1_checkpoint

    store = store_at(tmp_path / "store")
    projections = []
    for index in range(3):
        _, original, value = projection_fixture(store, f"sphere-{index}", revision="r1.9")
        point = value["segments"][0]["points"][0]
        point["observation"] = sphere()
        point["chosen"] = point["observation"]["catalog"][index]["choice_id"]
        projection = projection_fixture(store, f"sphere-selected-{index}", original=original,
                                         value=value, revision="r1.9")[0]
        projections.append(projection.artifact_id)
    with pytest.raises(BoundaryError, match="revision_mismatch"):
        inspect_projections(store, projections)
    inspected = inspect_projections(store, projections, input_projection=V2_R19_INPUT_SPEC)
    splits = {group["group_id"]: split for group, split in zip(
        inspected["groups"], ("train", "dev", "test"), strict=True)}
    snapshot = publish_snapshot(store, projections, PRODUCER, split_salt="explicit-r19",
        split_by_group=splits, input_projection=V2_R19_INPUT_SPEC)
    with pytest.raises((BoundaryError, ValueError), match="InputSpec|revision"):
        prepare_p1_run(store, snapshot.artifact_id, PRODUCER, MStdConfig(max_epochs=2), "d" * 32)
    run = prepare_p1_run(store, snapshot.artifact_id, PRODUCER, MStdConfig(max_epochs=2),
                         "e" * 32, input_projection=V2_R19_INPUT_SPEC)
    workload = verify_p1_run(store, run.artifact_id, PRODUCER, prepare_populations=True)
    assert workload.vocabulary == public_vocabulary(V2_R19_INPUT_SPEC)
    assert workload.training.parameters.value()["tensor_pack_id"]
    initial = MStdTrainer(workload.prepared_populations, workload.vocabulary,
                         snapshot.artifact_id, workload.config, workload.execution)
    request = ComputeRequest("training", run.artifact_id, PRODUCER, "r19-cpu-one", stop_after=1)
    receipt = dispatch(store, request, PRODUCER)
    validate_receipt(store, request, receipt)
    assert receipt.state == "paused" and receipt.checkpoint_id
    saved = verify_p1_checkpoint(store, receipt.checkpoint_id, workload)
    assert saved.state["step"] == 1
    assert any(not torch.equal(value, saved.state["model"][name])
               for name, value in initial.model.state_dict().items())
    assert all(torch.isfinite(value).all() for value in saved.state["model"].values())


def test_normal_data_preparation_explicit_r19_has_no_legacy_training_block(tmp_path):
    from test_p1_data_preparation import workspace

    from spireagent.workbench.p1_data_preparation import prepare_p1_data

    owner, store, _ = workspace(tmp_path, names=())
    ids = [projection_fixture(store, f"r19-prepare-{i}", revision="r1.9")[0].artifact_id
           for i in range(3)]
    result = prepare_p1_data(owner, store, revision="r1.9", projection_ids=ids)
    assert result["revision"] == "r1.9"
    assert not result["conflicts"]
    assert "legacy_revision_inventory_only" not in result["allocation"]["blockers"]
    with pytest.raises(BoundaryError, match="unsupported_revision"):
        prepare_p1_data(owner, store, revision="r1.8", projection_ids=ids)


def test_normal_rl_learner_projects_full_r19_catalog_and_updates(package, tmp_path, monkeypatch):
    from spireagent.workbench.rl_learner import IO
    from stpd.rl import learner as module
    from stpd.rl.learner import Learner, LearnerConfig
    from stpd.rl.performance import PerformanceConfig

    monkeypatch.setattr(module, "source_identity", lambda _: PRODUCER)
    config = LearnerConfig(run="r19-cpu",
        peer={"kind": "local", "directory": str(tmp_path / "peer")},
        start_package=str(package[2]), tensorboard_steps=0, batch_size=1,
        min_episodes_before_training=1, anchor_batch=0,
        performance=PerformanceConfig(packed=True, tensor_cache=True, precompile_inputs=True))
    learner = Learner(config, tmp_path / "learner", IO)
    try:
        assert learner.projection == V2_R19_INPUT_SPEC
        state = sphere()
        episode = {"run_id": "r19-original", "original_content_id": "c" * 64,
            "identity": {"input_spec": input_spec(learner.model.vocabulary), "ascension": 0},
            "observations": [{"observation": state}], "steps": [{"index": 0,
                "observation_index": 0, "action_id": state["catalog"][0]["choice_id"],
                "action_index": 0, "generated_at": time.time() - 2,
                "behavior": {"mode": "temperature_uniform", "selected_probability": 1 / 242,
                    "model_probabilities": [1 / 242] * 242, "probabilities": [1 / 242] * 242}}],
            "outcome": {"status": "finished", "unknown": False,
                "terminal": {"status": "observed", "result": "loss"},
                "max_observed_floor": 12, "boss_clearance": {"count": None}}}
        samples = learner._project(episode)
        assert len(samples[0][0].choices) == 242
        learner._admit([{"key": "r19-shard", "record": {"model_version": 0,
            "finished_at": time.time(), "shard_artifact_id": "d" * 64},
            "episode": episode, "samples": samples}])
        before = {name: value.clone() for name, value in learner.model.state_dict().items()}
        result = learner.train_once()
        assert result["update"] == 1
        assert any(not torch.equal(value, learner.model.state_dict()[name])
                   for name, value in before.items())
    finally:
        learner.exchange.request_stop("bounded_cpu_check_complete")
        learner.run()
