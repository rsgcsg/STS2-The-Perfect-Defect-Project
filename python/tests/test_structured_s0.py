"""Synthetic source/model/sequence/export/Runtime-port checks, no real qualification."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import torch

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from stpd.fullrun.structured_inputs import (
    INPUT_ID,
    PROJECTION_VERSION,
    StructuredNode,
    project_structured_snapshot,
)
from stpd.fullrun.structured_sequences import SOURCE_SCHEMA, parse_structured_dataset
from stpd.fullrun.text_menu_inputs import V2_SNAPSHOT_SCHEMA, validate_text_menu_v2_snapshot
from stpd.models.structured_m2 import GRAPH_ID, ByteFieldEncoder, StructuredM2
from stpd.models.structured_training import StructuredTrainingConfig, train_structured_model
from stpd.policy.structured_export import export_structured_package, load_structured_package
from stpd.policy.structured_port import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    PORT_SCHEMA,
    StructuredOnlineScorer,
    StructuredPolicyAdapter,
    serve,
)
from stpd.workers.checkpoint_codec import decode_checkpoint
from stpd.workers.structured_run import run_structured_job

FIXTURES = Path(__file__).parent / "fixtures/text_menu_v2"
PRODUCER = Producer("https://example.test/source", "a" * 40, "b" * 64)


@pytest.fixture(autouse=True)
def cpu_threads():
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


def fixture(name="targeted-root"):
    return json.loads((FIXTURES / (name + ".json")).read_text())


def sample(number=20, *, hp=12, labels=("Play Strike", "End turn")):
    value = fixture()
    value["snapshot_id"] = f"snapshot-{number}"
    value["sequence"] = number
    value["persistent"] = {"content": {"hp": hp, "energy": 3, "gold": 0}}
    value["referents"][0]["properties"] = {"cost": 1, "description": "Deal 6 damage."}
    value["referents"][1]["properties"] = {"hp": 28, "block": 0}
    value["menu_actions"]["actions"] = [
        {
            "action_id": "play-id",
            "kind": "native_input",
            "verb": "play",
            "label": labels[0],
            "subject_referent_id": "card-C",
            "arguments": [{"role": "target", "referent_id": "enemy-E"}],
            "effect_domain": "native_input",
        },
        {
            "action_id": "end-id",
            "kind": "native_input",
            "verb": "end_turn",
            "label": labels[1],
            "subject_referent_id": None,
            "arguments": [],
            "effect_domain": "native_input",
        },
    ]
    value["menu_actions"].update(total_count=2, materialized_count=2)
    return value


def rename(value, mapping):
    if isinstance(value, dict):
        return {key: rename(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [rename(item, mapping) for item in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def source(snapshots=None, *, split="train", group="group-1", source_kind="synthetic"):
    snapshots = snapshots or [sample()]
    rows = []
    previous = None
    for position, snapshot in enumerate(snapshots):
        frame = project_structured_snapshot(snapshot)
        capsule = json_bytes(snapshot).decode("utf-8")
        rows.append(
            {
                "position": position,
                "snapshot": snapshot,
                "capsule_json": capsule,
                "capsule_sha256": hashlib.sha256(capsule.encode()).hexdigest(),
                "capture_id": f"capture-{position}",
                "chosen_action_id": frame.action_ids[0] if frame.action_ids else None,
                "reset_before": position == 0,
                "advance": previous != frame.state_digest,
            }
        )
        previous = frame.state_digest
    return {
        "schema": SOURCE_SCHEMA,
        "source_kind": source_kind,
        "teacher": {"id": "fixture-teacher", "version": "1", "parameters": {}},
        "runs": [
            {
                "run_id": group + "-run",
                "source_group": group,
                "split": split,
                "identity": {"runtime": "synthetic", "input_spec": "s0-admitted-policy-offers-v1"},
                "steps": rows,
            }
        ],
    }


def score(model, value, memory=None):
    frame = project_structured_snapshot(value)
    entities = model.encode(frame)
    memory = model.advance(entities, model.initial_memory() if memory is None else memory)
    return frame, model.score(frame, entities, memory), memory


def test_shared_validation_does_not_render(monkeypatch):
    def forbidden(_value):
        raise AssertionError("structured validation must not render JSON text")

    monkeypatch.setattr("stpd.fullrun.text_menu_inputs._render", forbidden)
    validate_text_menu_v2_snapshot(sample())
    assert project_structured_snapshot(sample()).candidates


def test_opaque_rename_entity_and_candidate_permutation_are_equivariant():
    original = sample()
    changed = rename(
        original,
        {"card-C": "new-card", "enemy-E": "new-enemy", "play-id": "new-play", "end-id": "new-end"},
    )
    changed["referents"].reverse()
    changed["menu_actions"]["actions"].reverse()
    changed["snapshot_id"] = "totally-new"
    changed["observed_at"] = "changed"
    changed["menu"].update(revision=100, native_snapshot_id="opaque-new")
    model = StructuredM2()
    first, logits, memory = score(model, original)
    second, reordered, new_memory = score(model, changed)
    assert first.state_digest == second.state_digest
    assert first.candidate_digest != second.candidate_digest
    assert torch.allclose(logits, reordered.flip(0), atol=2e-6, rtol=2e-6)
    assert torch.allclose(memory, new_memory, atol=2e-6, rtol=2e-6)
    assert first.ref_rows != second.ref_rows
    assert all("card-C" not in node.text + node.field for node in first.nodes)


def test_candidates_labels_never_write_memory_or_digest():
    first = sample()
    second = sample(labels=("A completely different decision", "Another label"))
    second["menu_actions"]["actions"][0]["arguments"] = []
    model = StructuredM2()
    frame, _, memory = score(model, first)
    altered, _, changed = score(model, second)
    assert frame.state_digest == altered.state_digest
    assert torch.equal(memory, changed)


def test_same_name_instances_keep_numeric_attributes_and_binding():
    value = sample()
    value["referents"][1].update(role="playable_card", label="Strike")
    value["referents"][1]["properties"] = {"cost": 3, "description": "Deal 6 damage."}
    frame = project_structured_snapshot(value)
    model = StructuredM2()
    entities = model.encode(frame)
    refs = dict(frame.ref_rows)
    assert not torch.allclose(entities[refs["card-C"]], entities[refs["enemy-E"]])


def ordered_identical_cards(*, collection="hand", aliases=False):
    current = sample()
    first = copy.deepcopy(current["referents"][0])
    second = copy.deepcopy(first)
    second["referent_id"] = "card-B"
    current["referents"] = [first, second]
    identifiers = ("entity-C", "entity-B") if aliases else ("card-C", "card-B")
    for ref, identifier in zip(current["referents"], identifiers, strict=True):
        if aliases:
            ref["properties"]["entity_id"] = identifier
    current["interaction"]["content"][collection] = [
        {"entity_id": identifier, "title": "Strike", "cost": 1} for identifier in identifiers
    ]
    current["menu_actions"]["actions"] = [
        {
            "action_id": "choose-" + ref["referent_id"],
            "kind": "system_selection",
            "verb": "select_card",
            "label": "Select Strike",
            "subject_referent_id": ref["referent_id"],
            "arguments": [],
            "effect_domain": "text_menu",
        }
        for ref in current["referents"]
    ]
    current["menu_actions"].update(total_count=2, materialized_count=2)
    return current


@pytest.mark.parametrize("seed", [0, 1, 7])
@pytest.mark.parametrize("collection", ["hand", "orbs", "potions"])
@pytest.mark.parametrize("aliases", [False, True])
def test_public_order_reaches_candidate_anchors_without_extra_neural_layers(
    seed, collection, aliases
):
    first = ordered_identical_cards(collection=collection, aliases=aliases)
    second = copy.deepcopy(first)
    second["interaction"]["content"][collection].reverse()
    model = StructuredM2(seed=seed)
    before, after = project_structured_snapshot(first), project_structured_snapshot(second)
    assert (
        before.state_digest == after.state_digest
    )  # Equal public instances, different binding layout.
    encoded_before, encoded_after = model.encode(before), model.encode(after)
    rows_before, rows_after = dict(before.ref_rows), dict(after.ref_rows)
    left_before, right_before = (
        encoded_before[rows_before["card-C"]],
        encoded_before[rows_before["card-B"]],
    )
    left_after, right_after = (
        encoded_after[rows_after["card-C"]],
        encoded_after[rows_after["card-B"]],
    )
    assert not torch.allclose(left_before, right_before)
    assert torch.allclose(left_before, right_after, atol=2e-6, rtol=2e-6)
    assert torch.allclose(right_before, left_after, atol=2e-6, rtol=2e-6)
    memory = model.initial_memory()
    scores_before = model.score(before, encoded_before, memory)
    scores_after = model.score(after, encoded_after, memory)
    assert not torch.allclose(scores_before[0], scores_before[1])
    assert torch.allclose(scores_before, scores_after.flip(0), atol=2e-6, rtol=2e-6)


def test_ordered_position_binding_survives_id_rename_and_referent_bag_permutation():
    first = ordered_identical_cards(aliases=True)
    second = rename(
        first,
        {
            "card-C": "other-C",
            "card-B": "other-B",
            "entity-C": "other-entity-C",
            "entity-B": "other-entity-B",
        },
    )
    second["referents"].reverse()
    model = StructuredM2()
    before, scores_before, memory_before = score(model, first)
    after, scores_after, memory_after = score(model, second)
    assert before.state_digest == after.state_digest
    assert torch.allclose(scores_before, scores_after, atol=2e-6, rtol=2e-6)
    assert torch.allclose(memory_before, memory_after, atol=2e-6, rtol=2e-6)


def test_unordered_run_deck_and_referent_bag_never_create_position_features():
    first = ordered_identical_cards(collection="cards")
    first["interaction"]["content"].update(kind="run_deck", ordering_semantics="unordered_multiset")
    second = copy.deepcopy(first)
    second["interaction"]["content"]["cards"].reverse()
    second["referents"].reverse()
    before, after = project_structured_snapshot(first), project_structured_snapshot(second)
    assert not any(node.field.endswith("$public_order") for node in before.nodes)
    assert before.state_digest == after.state_digest
    model = StructuredM2()
    _, scores_before, memory_before = score(model, first)
    _, scores_after, memory_after = score(model, second)
    assert torch.allclose(scores_before, scores_after, atol=2e-6, rtol=2e-6)
    assert torch.allclose(memory_before, memory_after, atol=2e-6, rtol=2e-6)


@pytest.mark.parametrize("value", [None, False, 0, 0.0, "unknown"])
def test_scalar_types_distinguish_null_zero_false_and_unknown(value):
    current = sample()
    current["persistent"]["content"]["hp"] = value
    others = []
    for other in (None, False, 0, 0.0, "unknown"):
        alternate = sample()
        alternate["persistent"]["content"]["hp"] = other
        others.append(project_structured_snapshot(alternate).state_digest)
    assert len(set(others)) == 5
    assert project_structured_snapshot(current).state_digest in others


def test_known_order_and_bag_policy():
    current = sample()
    content = current["interaction"]["content"]
    content["list"] = [{"x": 1}, {"x": 2}]
    before = project_structured_snapshot(current)
    content["list"].reverse()
    assert before.state_digest == project_structured_snapshot(current).state_digest
    content["orbs"] = [{"charge": 1}, {"charge": 2}]
    before = project_structured_snapshot(current)
    content["orbs"].reverse()
    assert before.state_digest != project_structured_snapshot(current).state_digest


def test_selected_focus_public_state_is_observable():
    current = sample()
    before = project_structured_snapshot(current)
    current["referents"][0]["state"]["focused"] = True
    assert before.state_digest != project_structured_snapshot(current).state_digest
    selected = fixture("targeted-select")["successor"]
    selected_frame = project_structured_snapshot(selected)
    assert any(node.field.endswith("selected_referent_ref") for node in selected_frame.nodes)


def test_public_relations_resolve_without_opaque_embedding():
    current = sample()
    current["interaction"]["content"]["map"] = {
        "nodes": [
            {"entity_id": "node-a", "kind": "fight", "next_entity_id": "node-b"},
            {"entity_id": "node-b", "kind": "shop", "next_entity_id": "node-a"},
        ]
    }
    original = project_structured_snapshot(current)
    changed = rename(current, {"node-a": "other-a", "node-b": "other-b"})
    changed["interaction"]["content"]["map"]["nodes"].reverse()
    altered = project_structured_snapshot(changed)
    assert original.state_digest == altered.state_digest
    assert any(relation == 2 for _, _, relation in original.edges)
    assert not any("node-a" in node.text for node in original.nodes)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["information_policy"].update(includes_hidden_information=True),
        lambda value: value["menu_actions"].update(status="partial"),
        lambda value: value["menu_actions"]["actions"][0].update(subject_referent_id="missing"),
        lambda value: value["interaction"]["content"].update(hidden_rng=1),
        lambda value: value["referents"][0]["properties"].update(description="x" * 4097),
    ],
)
def test_invalid_or_overbudget_source_fails_whole_decision(mutation):
    value = sample()
    mutation(value)
    with pytest.raises(ValueError):
        project_structured_snapshot(value)


def test_nonfinite_graph_and_weight_inputs_fail():
    frame = project_structured_snapshot(sample())
    model = StructuredM2()
    bad = replace(frame, nodes=(StructuredNode("bad", "real", numeric=(1.0, float("nan"), 0.0)),))
    with pytest.raises(BoundaryError, match="node_type_or_numeric"):
        model.encode(bad)
    with torch.no_grad():
        model.write_query[0, 0] = float("inf")
    with pytest.raises(BoundaryError, match="finite_parameters"):
        model.encode(frame)


def test_byte_encoder_padding_mask_and_unicode():
    model = ByteFieldEncoder()
    singleton = model(("中文",))[0]
    padded = model(("中文", "much longer neighbouring text", ""))[0]
    assert torch.allclose(singleton, padded, atol=1e-6, rtol=1e-6)


def observe(scorer, value, *, token="segment-a", run="run-a", digest=None):
    frame = project_structured_snapshot(value)
    return scorer.observe(
        value,
        run_id=run,
        continuity_token=token,
        expected_candidate_digest=digest or frame.candidate_digest,
        expected_candidate_count=len(frame.candidates),
    )


def test_online_same_digest_rebinds_fresh_e_and_c_without_advance():
    scorer = StructuredOnlineScorer(StructuredM2())
    original = sample()
    _, scores, advance = observe(scorer, original)
    memory = scorer.memory.clone()
    changed = rename(
        sample(21),
        {
            "card-C": "other-card",
            "enemy-E": "other-enemy",
            "play-id": "another-play",
            "end-id": "another-end",
        },
    )
    changed["referents"].reverse()
    changed["menu_actions"]["actions"].reverse()
    frame, second, repeated = observe(scorer, changed)
    assert advance and not repeated and scorer.advances == 1
    assert frame.action_ids == ("another-end", "another-play")
    assert torch.equal(memory, scorer.memory)
    assert second == pytest.approx(tuple(reversed(scores)), abs=2e-6)
    assert not observe(scorer, copy.deepcopy(changed))[2]


def test_online_revisit_reset_retired_and_transactional_failure():
    scorer = StructuredOnlineScorer(StructuredM2())
    observe(scorer, sample())
    observe(scorer, sample(21, hp=11))
    assert observe(scorer, sample(22))[2]
    assert scorer.advances == 3
    before = scorer.memory.clone()
    with pytest.raises(BoundaryError, match="candidate_or_continuity_binding"):
        observe(scorer, sample(23, hp=5), digest="0" * 64)
    assert torch.equal(before, scorer.memory)
    observe(scorer, sample(20), token="segment-b")
    with pytest.raises(BoundaryError, match="retired_continuity"):
        observe(scorer, sample(30), token="segment-a")
    with pytest.raises(BoundaryError, match="snapshot_order"):
        observe(scorer, sample(19), token="segment-b")


def test_online_new_session_and_process_restart_do_not_recover_memory():
    scorer = StructuredOnlineScorer(StructuredM2())
    observe(scorer, sample())
    changed = sample(21)
    changed["session"]["runtime_instance_id"] = "different"
    with pytest.raises(BoundaryError, match="new_segment"):
        observe(scorer, changed)
    observe(scorer, changed, token="segment-b")
    restarted = StructuredOnlineScorer(scorer.model)
    assert restarted.state_digest is None and not restarted.memory.any()


def test_sequence_preserves_score_only_n_and_unlabelled_prefix():
    value = source([sample(20), sample(21), sample(22, hp=9)])
    value["runs"][0]["steps"][0]["chosen_action_id"] = None
    dataset = parse_structured_dataset(json_bytes(value))
    assert [step.advance for step in dataset.runs[0].steps] == [True, False, True]
    result = train_structured_model(dataset)
    assert result.metrics["optimizer_updates"] == 1
    assert result.metrics["label_uses"] == 2
    assert result.metrics["partitions"]["train"]["score_only_labels"] == 1
    assert result.metrics["capsule_bytes_verified"] is True
    assert result.metrics["evaluation_scope"] == "learning_smoke"


def test_tbptt_four_actual_advances_includes_score_only_rows():
    snapshots = [
        sample(20),
        sample(21),
        sample(22, hp=11),
        sample(23, hp=10),
        sample(24, hp=9),
        sample(25, hp=9),
        sample(26, hp=8),
    ]
    dataset = parse_structured_dataset(json_bytes(source(snapshots)))
    result = train_structured_model(dataset)
    assert result.metrics["optimizer_updates"] == 2
    assert result.metrics["label_uses"] == 7
    assert result.metrics["partitions"]["train"]["score_only_labels"] == 2


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda value: value["runs"][0]["steps"][0].update(advance=False), "advance_rule"),
        (
            lambda value: value["runs"][0]["steps"][0].update(chosen_action_id="missing"),
            "label_not",
        ),
        (
            lambda value: value["runs"][0]["steps"][0].update(capsule_sha256="0" * 64),
            "capsule_snapshot",
        ),
        (
            lambda value: value["runs"][0]["steps"][0].update(previous_actual_action="play"),
            "step_fields",
        ),
        (lambda value: value.update(source_kind="human"), "source_schema"),
    ],
)
def test_source_flags_and_provenance_fail_closed(mutation, reason):
    value = source()
    mutation(value)
    with pytest.raises(BoundaryError, match=reason):
        parse_structured_dataset(json_bytes(value))


def test_splits_group_whole_runs_and_do_not_claim_synthetic_generalization():
    value = source()
    extra = source(split="test")["runs"][0]
    extra["run_id"] = "another-run"
    value["runs"].append(extra)
    with pytest.raises(BoundaryError, match="split_leakage"):
        parse_structured_dataset(json_bytes(value))
    value = source(source_kind="agent")
    value["runs"] += source(split="dev", group="group-2")["runs"]
    value["runs"] += source(split="test", group="group-3")["runs"]
    assert parse_structured_dataset(json_bytes(value)).independent_test_eligible
    value["source_kind"] = "synthetic"
    assert not parse_structured_dataset(json_bytes(value)).independent_test_eligible


@pytest.fixture
def exported(tmp_path):
    model = StructuredM2()
    directory = tmp_path / "agent"
    metadata = export_structured_package(
        model,
        directory,
        source_revision="a" * 40,
        data_sha256="b" * 64,
        source_kind="synthetic",
        teacher_sha256="c" * 64,
        training={"fixture": True},
    )
    return directory, metadata, model


def test_export_offline_and_online_scores_memory_match_exactly(exported):
    directory, metadata, model = exported
    loaded, restored = load_structured_package(directory)
    assert loaded == metadata
    original = StructuredOnlineScorer(model)
    resident = StructuredOnlineScorer(restored)
    for value in [sample(20), sample(21), sample(22, hp=5), sample(23)]:
        first = observe(original, value)
        second = observe(resident, value)
        assert first[1:] == second[1:]
        assert torch.equal(original.memory, resident.memory)


def test_export_rejects_drift_and_old_history_profile(exported):
    directory, metadata, _model = exported
    weights = directory / "weights.tensor-tree"
    original = weights.read_bytes()
    weights.write_bytes(original + b"drift")
    with pytest.raises(BoundaryError, match="weights_digest"):
        load_structured_package(directory)
    weights.write_bytes(original)
    changed = copy.deepcopy(metadata)
    changed["projection"]["I"] = True
    (directory / "model.json").write_bytes(json_bytes(changed))
    with pytest.raises(BoundaryError, match="unsupported_package"):
        load_structured_package(directory)


def manifest_for(directory, metadata):
    return {
        "schema": "sts2.policy-runtime/policy-manifest-1",
        "manifest_id": "synthetic-s0",
        "policy": {"id": "s0", "version": "1", "provider": "stpd", "architecture": GRAPH_ID},
        "adapter": {
            "id": ADAPTER_ID,
            "version": ADAPTER_VERSION,
            "protocol": "sts2.policy-runtime/decision-only-ndjson-2",
            "code_sha256": metadata["adapter_code_sha256"],
        },
        "artifact": {
            "id": metadata["model_id"],
            "path": str(directory / "model.json"),
            "sha256": hashlib.sha256((directory / "model.json").read_bytes()).hexdigest(),
        },
        "representation": {
            "id": INPUT_ID,
            "version": PROJECTION_VERSION,
            "input_schema": V2_SNAPSHOT_SCHEMA,
        },
        "requirements": {
            "connector_protocol_version": "1.0.0",
            "environment": {
                "host_kind": "test",
                "connector_version": "fixture",
                "connector_source_revision": "a" * 40,
                "connector_artifact_sha256": "b" * 64,
                "connector_module_version_id": "fixture",
                "modset_status": "exact_platform_modset",
                "modset_fingerprint": "c" * 64,
                "loaded_mod_ids": ["STS2_PLATFORM"],
            },
            "reads": [],
            "whole_decision_admission": True,
            "candidate_order_digest": "sha256-json-menu-action-id-order",
            "score_count_matches_candidate_count": True,
            "selected_index": True,
            "successor_required": True,
        },
        "support": {
            "game_versions": ["fixture"],
            "game_commits": ["fixture"],
            "interaction_kinds": ["combat_turn"],
            "action_verbs": ["play", "end_turn"],
        },
        "adapter_config": {},
        "claims": {
            "full_run": False,
            "selector": False,
            "catalog_filtered": False,
            "creates_action_authority": False,
            "creates_native_operands": False,
        },
    }


def test_ndjson_port_returns_existing_runtime_contract(exported, tmp_path):
    directory, metadata, _model = exported
    manifest = manifest_for(directory, metadata)
    path = tmp_path / "policy.json"
    path.write_bytes(json_bytes(manifest))
    adapter = StructuredPolicyAdapter(directory, path)
    snapshot = sample()
    frame = project_structured_snapshot(snapshot)
    request = {
        "schema": PORT_SCHEMA,
        "message_type": "decide",
        "request_id": "request-1",
        "input": {
            "run_id": "run-1",
            "manifest": manifest,
            "bundle": {"observation": snapshot, "reads": []},
            "candidate_count": len(frame.candidates),
            "candidate_digest": frame.candidate_digest,
            "continuity_token": "segment-1",
        },
    }
    destination = io.StringIO()
    serve(adapter, io.StringIO(json_bytes(request).decode()), destination)
    ready, result = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert ready["adapter"] == manifest["adapter"] and ready["schema"] == PORT_SCHEMA
    assert result["message_type"] == "decision"
    assert set(result["output"]) == {"candidate_digest", "scores", "selected_index"}
    assert len(result["output"]["scores"]) == 2
    assert result["completion"] == {
        "continuity_token": "segment-1",
        "snapshot_id": "snapshot-20",
        "sequence": 20,
    }
    assert adapter.closed
    # Launch the actual trusted module as Runtime does; no weights can supply executable code.
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "stpd.policy.structured_port",
            "--package",
            str(directory),
            "--manifest",
            str(path),
        ],
        input=json_bytes(request),
        capture_output=True,
        check=True,
        timeout=30,
    )
    child_lines = [json.loads(line) for line in child.stdout.splitlines()]
    assert child_lines == [ready, result]


def test_port_rejects_history_extra_fields_before_any_memory_write(exported, tmp_path):
    directory, metadata, _model = exported
    manifest = manifest_for(directory, metadata)
    path = tmp_path / "policy.json"
    path.write_bytes(json_bytes(manifest))
    adapter = StructuredPolicyAdapter(directory, path)
    request = {
        "run_id": "r",
        "manifest": manifest,
        "bundle": {"observation": sample(), "reads": []},
        "candidate_count": 2,
        "candidate_digest": "a" * 64,
        "continuity_token": "t",
        "previous_interaction": {"receipt": "delivered"},
    }
    with pytest.raises(BoundaryError, match="missing_or_unknown_fields"):
        adapter.decide(request)
    assert adapter.scorer.state_digest is None


def test_worker_uses_existing_artifacts_reporter_and_safe_optimizer_checkpoint(tmp_path):
    dataset = parse_structured_dataset(json_bytes(source()))
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    reporter = ObjectStoreRunReporter(store, store.blobs)
    summary = run_structured_job(
        dataset,
        StructuredTrainingConfig(),
        tmp_path / "output",
        PRODUCER,
        store=store,
        reporter=reporter,
    )
    artifacts = summary["artifacts"]
    assert reporter.completed(artifacts["run"]).artifact_id == artifacts["result"]
    assert {store.get_manifest(identifier).kind for identifier in store.manifest_ids()} >= {
        "dataset",
        "training_input",
        "experiment",
        "run",
        "run_event",
        "checkpoint",
        "model",
        "run_result",
    }
    checkpoint = decode_checkpoint((tmp_path / "output/checkpoint.tensor-tree").read_bytes())
    assert checkpoint["optimizer"]["state"] and checkpoint["model"]
    assert summary["optimizer_updates"] == 1
    package, _ = load_structured_package(Path(summary["package"]))
    assert package["source"]["data_sha256"] == dataset.source_sha256
    with pytest.raises(BoundaryError, match="existing_attempt"):
        run_structured_job(
            dataset,
            StructuredTrainingConfig(),
            tmp_path / "retry",
            PRODUCER,
            store=store,
            reporter=reporter,
        )


def test_training_never_mutates_dev_test_source_or_calls_optimizer_for_unlabelled_chunk():
    value = source(
        [sample(20), sample(21, hp=11), sample(22, hp=10), sample(23, hp=9), sample(24, hp=8)]
    )
    for step in value["runs"][0]["steps"][:4]:
        step["chosen_action_id"] = None
    value["runs"] += source(split="dev", group="g2")["runs"]
    value["runs"] += source(split="test", group="g3")["runs"]
    dataset = parse_structured_dataset(json_bytes(value))
    raw = dataset.source_bytes
    result = train_structured_model(dataset)
    assert result.metrics["unlabelled_chunks"] == 1
    assert result.metrics["optimizer_updates"] == 1
    assert result.metrics["label_uses"] == 1
    assert result.metrics["partitions"]["dev"]["labels"] == 1
    assert dataset.source_bytes == raw


def test_all_published_s0_scenes_accept_generic_structure():
    for name in ("targeted-root", "card-only-root", "targeted-select", "card-only-select"):
        value = fixture(name)
        if "successor" in value:
            value = value["successor"]
        frame = project_structured_snapshot(value)
        model = StructuredM2()
        entities = model.encode(frame)
        assert len(
            model.score(frame, entities, model.advance(entities, model.initial_memory()))
        ) == len(frame.candidates)


def test_real_shaped_no_choice_observation_advances_without_score_or_label():
    terminal = fixture("managed-game-over")
    frame = project_structured_snapshot(terminal)
    assert not frame.candidates
    scorer = StructuredOnlineScorer(StructuredM2())
    assert observe(scorer, terminal)[1:] == ((), True)
    with pytest.raises(BoundaryError, match="decision_catalog"):
        scorer.model.score(frame, scorer.model.encode(frame), scorer.memory)
    dataset = parse_structured_dataset(json_bytes(source([terminal])))
    assert dataset.runs[0].steps[0].advance and not dataset.runs[0].steps[0].frame.candidates


def test_program_receipt_action_history_fields_never_update_model_memory():
    first = sample()
    second = sample(21)
    second["interaction"]["content"].update(
        receipt={"delivery": "delivered"},
        control={"state": "held"},
        previous_actual_action="last picked card",
        public_feedback="delivered",
        last_action_text="own action trace",
        reason_code="ok",
        history=["old request"],
    )
    model = StructuredM2()
    before, _, memory = score(model, first)
    after, _, new_memory = score(model, second)
    assert before.state_digest == after.state_digest
    assert torch.equal(memory, new_memory)


@pytest.mark.parametrize("metadata_key", ["receipt", "control", "history"])
@pytest.mark.parametrize(
    "declaration_key", ["entity_id", "referent_id", "slot_entity_id", "interaction_id"]
)
@pytest.mark.parametrize("identifier", ["receipt-only-id", "card-C", "Deal 6 damage."])
def test_excluded_subtree_declarations_cannot_create_or_override_entity_bindings(
    metadata_key, declaration_key, identifier
):
    first = sample()
    second = sample()
    # Covers new anchors, a collision with a visible referent and a collision
    # with legitimate public text that must not become a registry reference.
    excluded = {
        "nested": [
            {
                declaration_key: identifier,
                "delivery": "delivered",
                "extra_public_like_fields": "ignored" * 100,
            }
        ]
    }
    second["interaction"]["content"][metadata_key] = excluded
    model = StructuredM2()
    before = project_structured_snapshot(first)
    after = project_structured_snapshot(second)
    assert before == after
    encoded_before, encoded_after = model.encode(before), model.encode(after)
    assert torch.equal(encoded_before, encoded_after)
    assert torch.equal(
        model.advance(encoded_before, model.initial_memory()),
        model.advance(encoded_after, model.initial_memory()),
    )


@pytest.mark.parametrize("location", ["persistent", "page", "properties", "state"])
def test_excluded_declarations_are_filtered_at_every_authorized_scope_path(location):
    first = sample()
    second = sample()
    destination = {
        "persistent": second["persistent"]["content"],
        "page": second["interaction"]["content"],
        "properties": second["referents"][0]["properties"],
        "state": second["referents"][0]["state"],
    }[location]
    destination["receipt"] = {
        "entity_id": "card-C",
        "hp": 999,
        "extra_public_like_fields": "ignored" * 100,
    }
    before, after = project_structured_snapshot(first), project_structured_snapshot(second)
    assert before == after
    model = StructuredM2()
    assert torch.equal(model.encode(before), model.encode(after))


def test_authorized_public_declarations_still_bind_relations_and_candidates():
    current = sample()
    current["interaction"]["content"]["map"] = {
        "nodes": [{"entity_id": "node-public", "kind": "shop"}],
        "current_entity_id": "node-public",
    }
    frame = project_structured_snapshot(current)
    bindings = dict(frame.ref_rows)
    assert {"node-public", "card-C", "enemy-E"} <= set(bindings)
    assert frame.candidates[0].roles == (
        ("subject", bindings["card-C"]),
        ("argument:target", bindings["enemy-E"]),
    )
    assert any(
        target == bindings["node-public"] and relation == 2
        for _source, target, relation in frame.edges
    )


@pytest.mark.parametrize("key", ["entity_id", "referent_id", "slot_entity_id", "interaction_id"])
def test_declaration_values_are_scalar_bindings_not_nested_discovery_paths(key):
    current = sample()
    current["interaction"]["content"][key] = {"entity_id": "nested-id"}
    with pytest.raises(BoundaryError, match="invalid_public_declaration"):
        project_structured_snapshot(current)


def test_referent_wrapper_extras_are_not_model_features():
    first = sample()
    second = sample()
    second["referents"][0]["properties_schema"] = "a-different-source-schema-metadata"
    second["referents"][0]["extra_transport_metadata"] = "opaque-internal-context"
    before = project_structured_snapshot(first)
    after = project_structured_snapshot(second)
    assert before.nodes == after.nodes and before.state_digest == after.state_digest


def test_inconsistent_complete_flag_with_missing_required_scope_is_rejected():
    current = sample()
    current["completeness"]["missing"] = ["required-current-player"]
    with pytest.raises(BoundaryError, match="required_public_scope_missing"):
        project_structured_snapshot(current)


def test_agent_source_requires_actual_offer_input_spec():
    value = source(source_kind="agent")
    value["runs"][0]["identity"].pop("input_spec")
    with pytest.raises(BoundaryError, match="input_spec_required"):
        parse_structured_dataset(json_bytes(value))
    value = source([fixture("managed-game-over")], source_kind="agent")
    with pytest.raises(BoundaryError, match="unoffered_observation"):
        parse_structured_dataset(json_bytes(value))


def test_actual_training_cli_emits_package_and_checkpoint(tmp_path):
    input_file = tmp_path / "input.json"
    input_file.write_bytes(json_bytes(source()))
    output = tmp_path / "output"
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "stpd.workers.structured_run",
            "--input",
            str(input_file),
            "--output",
            str(output),
            "--source-revision",
            "a" * 40,
            "--epochs",
            "1",
            "--max-updates",
            "1",
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    summary = json.loads(child.stdout)
    assert summary["optimizer_updates"] == 1
    assert Path(summary["package"]).is_dir() and Path(summary["checkpoint"]).is_file()
    assert summary["evaluation_scope"] == "learning_smoke"


@pytest.mark.parametrize(
    "scene",
    ["shop", "map", "event", "card_selector", "card_reward", "treasure", "rest_site", "game_over"],
)
def test_generic_tree_represents_scene_content_without_scene_strategies(scene):
    current = sample()
    current["interaction"].update(
        kind=scene,
        stage="ready",
        prompt="Current public page",
        content_schema=f"sts2.player-environment/surface/{scene}-1",
    )
    current["interaction"]["content"] = {
        "surface": {
            "kind": scene,
            "body": "Visible text",
            "prices": [10, 25],
            "selected": [False, True],
            "public_warning": None,
        },
        "context": {"kind": scene},
    }
    current["menu_actions"]["actions"][0].update(verb="choose", arguments=[])
    frame = project_structured_snapshot(current)
    model = StructuredM2()
    entities = model.encode(frame)
    assert model.score(frame, entities, model.advance(entities, model.initial_memory())).shape == (
        2,
    )
