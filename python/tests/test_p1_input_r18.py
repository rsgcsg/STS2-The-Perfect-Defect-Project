"""Explicit r1.8 inference binding: original tables/weights, new public facts.

Synthetic public DTOs isolate the Python consumer; actual native/SDK captures and
v416 acceptance belong to U1's integration gate, not these training fixtures.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest
import torch
from test_p1_input_v2 import public_state

from stpd.p1.agent import installation
from stpd.p1.data import digest, json_bytes
from stpd.p1.export import compatibility_package, export_model, input_spec, load_model, register
from stpd.p1.input import V2_INPUT_SPEC, V2_R18_INPUT_SPEC, project, training_projection
from stpd.p1.input_identity import identity_paths
from stpd.p1.tensor_input import compile_frame
from stpd.p1.vocabulary import public_vocabulary
from stpd.workers.checkpoint_codec import encode_checkpoint


@pytest.fixture(scope="module")
def packages(tmp_path_factory):
    from stpd.p1.formal_training import MStdTrainer, Population
    from stpd.p1.spec import MStdConfig

    torch.set_num_threads(2)
    root = tmp_path_factory.mktemp("r18-package")
    vocabulary = public_vocabulary()
    frame = project(public_state(), input_projection=V2_INPUT_SPEC)
    population = Population((frame,), (0,), ("actual-fixture-update",))
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(184)
        trainer = MStdTrainer(
            {
                "train": population,
                "dev": Population((frame,), (0,), ("fixture-dev",)),
                "test": Population((), (), ()),
            },
            vocabulary,
            "a" * 64,
            MStdConfig(max_epochs=2),
        )
    trainer.advance()
    checkpoint = root / "checkpoint"
    checkpoint.write_bytes(trainer.checkpoint())
    old = root / "old"
    descriptor = export_model(trainer.model.eval(), checkpoint, old, trainer.identity)
    before = {p.name: p.read_bytes() for p in old.iterdir()}
    rebound = root / "r18"
    receipt = compatibility_package(old, rebound)
    return root, old, rebound, descriptor, receipt, before


def model_pair(packages):
    _, old, rebound, descriptor, receipt, _ = packages
    return (
        load_model(old / "model.tensor-tree", descriptor["package_sha256"])[0],
        load_model(rebound / "model.tensor-tree", receipt["package_sha256"])[0],
    )


def test_explicit_package_preserves_all_nonbinding_vocabulary_parameters_and_source(packages):
    _, old, rebound, descriptor, receipt, before = packages
    left, a = load_model(old / "model.tensor-tree", descriptor["package_sha256"])
    right, b = load_model(rebound / "model.tensor-tree", receipt["package_sha256"])
    assert before == {p.name: p.read_bytes() for p in old.iterdir()}
    expected = copy.deepcopy(a["vocabulary"])
    expected["input_spec"] = V2_R18_INPUT_SPEC
    assert b["vocabulary"] == expected
    for table in ("ids", "fields", "kinds", "actions", "tokenizer"):
        assert b["vocabulary"][table] == a["vocabulary"][table]
    assert a.keys() == b.keys()
    for key in a.keys() - {"vocabulary", "input_spec", "weights"}:
        assert a[key] == b[key]
    assert left.state_dict().keys() == right.state_dict().keys()
    for name, tensor in left.state_dict().items():
        assert tensor.dtype == right.state_dict()[name].dtype
        assert torch.equal(tensor, right.state_dict()[name]), name
    assert encode_checkpoint({"weights": a["weights"]}) == encode_checkpoint(
        {"weights": b["weights"]}
    )
    assert receipt["source_descriptor_sha256"] == digest(before["model.json"])
    assert receipt["weights_sha256"] == digest(encode_checkpoint({"weights": a["weights"]}))
    assert receipt["tokenizer_sha256"] == digest(a["vocabulary"]["tokenizer"].encode())
    assert receipt["tables_sha256"] == digest(
        json_bytes({key: a["vocabulary"][key] for key in ("ids", "fields", "kinds", "actions")})
    )
    assert receipt["preserved_vocabulary_sha256"] == digest(
        json_bytes({key: value for key, value in a["vocabulary"].items() if key != "input_spec"})
    )
    assert json.loads((rebound / "model.json").read_bytes())["input_spec"] == input_spec(
        b["vocabulary"]
    )
    with pytest.raises(FileExistsError):
        compatibility_package(old, rebound)
    with pytest.raises(ValueError, match="original V2"):
        compatibility_package(rebound, rebound.parent / "twice")


def test_regular_old_frame_tensors_and_scores_remain_bitwise_equal(packages):
    left, right = model_pair(packages)
    state = public_state()
    old_frame = project(state, input_projection=V2_INPUT_SPEC)
    current = {**state, "revision": "r1.8"}
    new_frame = project(current, input_projection=V2_R18_INPUT_SPEC)
    assert old_frame == new_frame
    a, b = compile_frame(old_frame, left), compile_frame(new_frame, right)
    assert a.entity_count == b.entity_count and a.choices == b.choices
    for key in a.tensors:
        assert torch.equal(a.tensors[key], b.tensors[key]), key
    for key in a.reductions:
        assert torch.equal(a.reductions[key].inverse, b.reductions[key].inverse)
        for (aw, an, ai), (bw, bn, bi) in zip(
            a.reductions[key].buckets, b.reductions[key].buckets, strict=True
        ):
            assert (aw, an) == (bw, bn) and torch.equal(ai, bi)
    with torch.inference_mode():
        assert torch.equal(left(old_frame), right(new_frame))
    for spec, obs in ((V2_INPUT_SPEC, current), (V2_R18_INPUT_SPEC, state)):
        with pytest.raises(ValueError, match="bound InputSpec revision"):
            project(obs, input_projection=spec)
    with pytest.raises(ValueError, match="new training requires"):
        training_projection(V2_R18_INPUT_SPEC)


def trial():
    state = {**public_state(), "revision": "r1.8"}
    state["scene"] = {
        "family": "event",
        "native_kind": "event_option",
        "content": {
            "context": {"kind": "event", "event_id": "TRIAL"},
            "surface": {
                "kind": "event_option",
                "options": [
                    {
                        "entity_id": "option-yes",
                        "title": "Double Down",
                        "description": "Reject the Trial",
                        "is_enabled": True,
                        "is_locked": False,
                    },
                    {
                        "entity_id": "option-other",
                        "title": "Accept",
                        "description": "Accept the Trial",
                        "is_enabled": True,
                        "is_locked": False,
                    },
                ],
                "abandon_confirmation": {
                    "kind": "abandon_run_confirmation",
                    "event_entity_id": "event-owner",
                    "room_entity_id": "room-owner",
                    "parent_option_entity_id": "option-yes",
                    "title": "Abandon this run?",
                    "body": "This ends the current run.",
                },
            },
        },
    }
    state["catalog"] = [
        {
            "choice_id": "confirm",
            "kind": "event.choose",
            "subject": "option-yes",
            "target": None,
            "label": "Confirm abandoning this run",
            "mapped": True,
        },
        {
            "choice_id": "accept",
            "kind": "event.choose",
            "subject": "option-other",
            "target": None,
            "label": "Accept",
            "mapped": True,
        },
    ]
    return state


def claws():
    state = {**public_state(), "revision": "r1.8"}
    original = copy.deepcopy(state["deck"]["content"]["cards"][0])
    after = {
        **original,
        "definition_id": "MAUL",
        "description": "Deal 6 damage.",
        "is_upgraded": True,
    }
    state["scene"] = {
        "family": "selector",
        "native_kind": "deck_transform_selection",
        "content": {
            "surface": {
                "kind": "deck_transform_selection",
                "cards": [original],
                "screen_entity_id": "claws-owner",
                "request_entity_id": "claws-request",
                "stage": "selecting",
                "selected_card_entity_ids": [],
                "selected_count": 0,
                "selector_request": {
                    "entity_id": "claws-request",
                    "owner_entity_id": "claws-owner",
                    "family": "deck_transform",
                    "transformation_preview": "deterministic_native_after",
                    "selected_entity_ids": [],
                },
                "current_controls": [],
                "preview_options": [
                    {
                        "subject_entity_id": original["entity_id"],
                        "before": original,
                        "after": after,
                        "display_context": "native_deterministic_transform_after",
                        "after_availability": "available",
                        "missing": [],
                    }
                ],
            }
        },
    }
    state["catalog"] = [
        {
            "choice_id": "select",
            "kind": "selector.select",
            "subject": original["entity_id"],
            "target": None,
            "label": "Select",
            "mapped": True,
        },
        {
            "choice_id": "finish",
            "kind": "selector.finish",
            "subject": None,
            "target": None,
            "label": "Finish",
            "mapped": True,
        },
    ]
    return state


@pytest.mark.parametrize("variant", ["warning", "after", "selected"])
def test_new_fact_and_selection_changes_reach_tensors_and_original_learned_scores(
    packages, variant
):
    _, model = model_pair(packages)
    state = trial() if variant == "warning" else claws()
    changed = copy.deepcopy(state)
    surface = changed["scene"]["content"]["surface"]
    if variant == "warning":
        surface["abandon_confirmation"]["body"] = (
            "The run will end immediately and cannot continue."
        )
    elif variant == "after":
        surface["preview_options"][0]["after"]["description"] = "Deal 9 damage."
    else:
        entity = surface["cards"][0]["entity_id"]
        surface["selected_count"] = 1
        surface["selected_card_entity_ids"] = surface["selector_request"]["selected_entity_ids"] = [
            entity
        ]
    a, b = [project(row, input_projection=V2_R18_INPUT_SPEC) for row in (state, changed)]
    assert a != b
    ta, tb = compile_frame(a, model), compile_frame(b, model)
    assert any(not torch.equal(ta.tensors[key], tb.tensors[key]) for key in ta.tensors)
    with torch.inference_mode():
        scores = [model(frame) for frame in (a, b)]
    assert all(torch.isfinite(score).all() for score in scores)
    assert not torch.equal(*scores), variant


def test_trial_identity_paths_are_r18_only_and_unknown_extensions_stay_literal(packages):
    _, model = model_pair(packages)
    state = trial()
    root = "$.scene.content.surface."
    relation = identity_paths(state)
    assert relation(root + "abandon_confirmation.event_entity_id") == "declared"
    assert relation(root + "abandon_confirmation.room_entity_id") == "declared"
    assert relation(root + "abandon_confirmation.parent_option_entity_id") == "reference"
    assert (
        identity_paths({**state, "revision": "r1.5"})(root + "abandon_confirmation.event_entity_id")
        is None
    )
    assert relation(root + 'extension["abandon_confirmation.event_entity_id"]') is None
    modal = copy.deepcopy(state)
    modal["scene"]["native_kind"] = "abandon_run_confirmation"
    for field in (
        "modal_entity_id",
        "event_entity_id",
        "room_entity_id",
        "parent_option_entity_id",
        "controls[0].entity_id",
    ):
        assert identity_paths(modal)(root + field) == "declared"
        assert identity_paths({**modal, "revision": "r1.5"})(root + field) is None
    aliases = {
        "event-owner": "renamed-event",
        "room-owner": "renamed-room",
        "option-yes": "renamed-option",
        "option-other": "renamed-other",
    }

    def rename(value):
        if isinstance(value, dict):
            return {key: rename(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rename(item) for item in value]
        return aliases.get(value, value) if isinstance(value, str) else value

    a, b = [project(row, input_projection=V2_R18_INPUT_SPEC) for row in (state, rename(state))]
    # Choice DTOs retain aliases for dispatch; learned entities/roles do not.
    assert a.entities == b.entities and a.roles == b.roles
    with torch.inference_mode():
        assert torch.equal(model(a), model(b))


def test_normal_registration_installation_and_cli_bind_new_revision(packages, tmp_path):
    _, old, rebound, _, receipt, _ = packages
    manifest = {
        "schema": "sts2.policy-runtime/agent-manifest-1",
        "input": {"profile": "p1-global-r1", "global": {"revision": "r1.8"}},
    }
    root = tmp_path / "registered"
    register(rebound, root, manifest)
    config, bound, model, _ = installation(root / "config.json", root / "manifest.json", root)
    assert config["input_projection"] == V2_R18_INPUT_SPEC
    assert bound["input"]["global"]["revision"] == "r1.8"
    with torch.inference_mode():
        assert torch.isfinite(
            model(project(trial(), input_projection=config["input_projection"]))
        ).all()
    with pytest.raises(ValueError, match="current r1.8"):
        register(
            rebound,
            tmp_path / "wrong",
            {**manifest, "input": {"profile": "p1-global-r1", "global": {"revision": "r1.5"}}},
        )
    with pytest.raises(ValueError, match="current r1.5"):
        register(old, tmp_path / "old-wrong", manifest)
    output = tmp_path / "cli"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "stpd.p1.input_cli",
            "rebind-r18",
            "--package",
            str(old),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["activated"] is result["game_started"] is False
    assert result["receipt"] == receipt


def test_r18_packed_inference_consumes_the_same_compiled_tensors(packages):
    from stpd.p1.execution import MStdExecution

    _, model = model_pair(packages)
    model.execution = MStdExecution(implementation="experimental-packed-v1")
    frames = [project(state, input_projection=V2_R18_INPUT_SPEC) for state in (trial(), claws())]
    packed = [compile_frame(frame, model) for frame in frames]
    with torch.inference_mode():
        raw_scores = model.forward_batch(frames)
        tensor_scores = model.forward_batch(packed)
    for raw, tensor in zip(raw_scores, tensor_scores, strict=True):
        assert torch.equal(raw, tensor)
