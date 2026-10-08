"""Tiny synthetic numerical learning through the existing fenced workload/store."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import replace

import pytest
import torch
from test_native_structured_model import agent_files as agent_files
from test_native_structured_model import (
    offer,
    snapshot,
)
from test_native_structured_model import (
    test_actual_stdio_agent_session_consume_ack_act_and_empty_c_await as stdio_loop,
)
from test_structured_code_scope import (
    test_reviewed_static_potential_imports_and_initializers_are_covered as check_static_closure,
)
from test_structured_resume import Authority, PauseControl, equal_tree, finish

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from stpd.fullrun.native_structured_inputs import (
    INPUT_SPEC,
    native_catalog_digest,
    project_native_structured,
)
from stpd.fullrun.native_training_sequences import parse_native_training_dataset
from stpd.models.structured_engine import StructuredTrainingEngine
from stpd.models.structured_training import StructuredTrainingConfig
from stpd.native_code_scope import (
    PATHS,
    TRAINING_PATHS_NATIVE,
    native_code_identity,
    native_training_code_identity,
)
from stpd.policy.native_agent import NativeStructuredAgent, bind_native_agent
from stpd.policy.native_structured_export import (
    TRAINED_PACKAGE_SCHEMA,
    export_native_model,
    load_native_package,
    require_native_model_package,
)
from stpd.structured_code_scope import LEGACY_SCOPE, ROOT, TRAINING_SCOPE
from stpd.structured_profiles import NATIVE_SCOPE, NATIVE_SOURCE_SCHEMA
from stpd.workers.structured_control import StructuredWorkloadRequest
from stpd.workers.structured_execution import (
    execute_structured_workload,
    prepare_structured_workload,
)


@pytest.fixture(autouse=True)
def two_threads():
    old = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(old)


def source():
    steps = []
    for number in (1, 1, 2, 3, 4, 5, 6):
        observation, actions = snapshot(number, empty=number in {2, 6})
        observation["referents"][1]["label"] = "Defend"
        observation["referents"][1]["properties"] = {"cost": 2, "block": 9}
        if actions:
            actions[1]["label"] = "Pick Defend"
        observation["catalog"]["digest"] = native_catalog_digest(actions)
        steps.append(
            {
                "observation": observation,
                "catalog": actions,
                "continuity_token": "segment",
                "reset_before": not steps,
                "chosen_action_id": actions[1]["action_id"] if actions else None,
            }
        )
    return {
        "schema": NATIVE_SOURCE_SCHEMA,
        "source_kind": "synthetic",
        "input_spec": INPUT_SPEC,
        "teacher": {"id": "synthetic_sparse_choices", "version": "1", "parameters": {}},
        "runs": [
            {
                "run_id": "run-1",
                "source_group": "group-1",
                "split": "train",
                "identity": {"fixture": "native_conformance"},
                "steps": steps,
            }
        ],
    }


def data():
    return parse_native_training_dataset(json_bytes(source()))


@pytest.mark.parametrize("status", ["interactive", "settling", "terminal"])
def test_full_reference_never_fabricates_null_interaction(status):
    observation, actions = snapshot(empty=True)
    observation["status"] = status
    observation["interaction"] = None
    with pytest.raises(BoundaryError, match="native_structured_input.page"):
        project_native_structured(observation, actions)
    observation, actions = snapshot(empty=True)
    observation["persistent"] = None
    project_native_structured(observation, actions)


def test_native_mechanical_wildcard_accepts_valid_unseen_whole_input(agent_files):
    folder, path, _, manifest = agent_files
    manifest["support"].update(interaction_kinds=["*"], action_verbs=["*"])
    path.write_bytes(json_bytes(manifest))
    agent = NativeStructuredAgent(folder, path)
    observation, actions = snapshot()
    observation["interaction"]["kind"] = "valid-public-new-kind"
    actions[0]["verb"] = "valid-public-new-verb"
    observation["catalog"]["digest"] = native_catalog_digest(actions)
    completion = agent.consume(offer(observation, actions))
    assert completion["advanced"]
    assert agent.scorer.pending["frame"].action_ids == tuple(a["action_id"] for a in actions)


@pytest.mark.parametrize(
    "field,values",
    [
        ("interaction_kinds", ["*", "selector"]),
        ("action_verbs", ["*", "pick"]),
        ("game_versions", ["*"]),
        ("game_commits", ["*"]),
    ],
)
def test_native_wildcard_never_expands_mixed_vocabulary_or_game_identity(
    agent_files, field, values
):
    folder, path, _, manifest = agent_files
    manifest["support"][field] = values
    path.write_bytes(json_bytes(manifest))
    with pytest.raises(BoundaryError, match="invalid_support_wildcard"):
        NativeStructuredAgent(folder, path)


def origin():
    return Producer(
        "https://example.test/source",
        "a" * 40,
        hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
    )


def test_native_units_feed_same_engine_with_empty_advance_and_no_fake_wait():
    dataset = data()
    assert dataset.input_spec.value() == INPUT_SPEC
    assert [step.advance for step in dataset.runs[0].steps] == [
        True,
        False,
        True,
        True,
        True,
        True,
        True,
    ]
    assert not dataset.runs[0].steps[2].frame.candidates
    assert dataset.runs[0].steps[2].chosen_action_id is None
    engine = StructuredTrainingEngine(dataset, StructuredTrainingConfig(), code_scope=NATIVE_SCOPE)
    assert engine.plans[0].advances == 4 and engine.plans[0].end == 5
    first = engine.advance_chunk()
    assert first.advances == 4 and first.observations == 5 and first.label_uses == 4
    assert engine.identity["input_spec"] == INPUT_SPEC
    assert engine.identity["projection"]["profile"] == "native-logical-v1"


def test_native_numerical_potential_import_inventory_is_closed():
    check_static_closure(NATIVE_SCOPE, TRAINING_PATHS_NATIVE)


def test_native_inference_identity_does_not_inherit_training_or_application_edits(tmp_path):
    for relative in {*PATHS, *TRAINING_PATHS_NATIVE, "uv.lock"}:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / relative).read_bytes())
    inference, numerical = native_code_identity(tmp_path), native_training_code_identity(tmp_path)
    path = tmp_path / "stpd/fullrun/native_training_sequences.py"
    path.write_bytes(path.read_bytes() + b"\n# source change\n")
    assert native_code_identity(tmp_path) == inference
    assert native_training_code_identity(tmp_path) != numerical
    numerical = native_training_code_identity(tmp_path)
    application = tmp_path / "spireagent/workbench/local_models.py"
    application.parent.mkdir(parents=True)
    application.write_bytes(b"# unrelated application edit\n")
    assert native_code_identity(tmp_path) == inference
    assert native_training_code_identity(tmp_path) == numerical


@pytest.mark.parametrize("boundary", [0, 1, 2])
def test_native_numeric_pause_resume_exact_weights_memory_optimizer_and_budget(boundary):
    dataset, config = data(), StructuredTrainingConfig(epochs=3, max_updates=3)
    original = StructuredTrainingEngine(dataset, config, code_scope=NATIVE_SCOPE)
    for _ in range(boundary):
        original.advance_chunk()
    restored = StructuredTrainingEngine(dataset, config, code_scope=NATIVE_SCOPE)
    restored.restore(original.checkpoint())
    equal_tree(original.memory, restored.memory)
    assert finish(original) == finish(restored)
    equal_tree(original.model.state_dict(), restored.model.state_dict())
    equal_tree(original.optimizer.state_dict(), restored.optimizer.state_dict())
    assert original.updates == restored.updates == 3
    assert (
        original.metrics()["partitions"]["train"]["mean_loss"]
        < original.metrics()["initial_train"]["mean_loss"]
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.update(source_kind="agent"),
        lambda value: value["input_spec"].update(sha256="0" * 64),
        lambda value: value["runs"][0]["steps"][1].update(reset_before=True),
        lambda value: value["runs"][0]["steps"][2].update(chosen_action_id="wait"),
    ],
)
def test_native_source_refuses_relabel_descriptor_reset_and_fake_label(change):
    value = copy.deepcopy(source())
    change(value)
    with pytest.raises(BoundaryError):
        parse_native_training_dataset(json_bytes(value))


@pytest.mark.parametrize("scope", [LEGACY_SCOPE, TRAINING_SCOPE])
def test_native_descriptor_cannot_enter_legacy_workload_before_store_mutation(tmp_path, scope):
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    with pytest.raises(BoundaryError):
        prepare_structured_workload(
            store,
            data(),
            origin(),
            StructuredTrainingConfig(),
            operation_id="1" * 32,
            code_scope=scope,
        )
    assert store.manifest_ids() == ()


@pytest.fixture
def completed(tmp_path):
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    reporter = ObjectStoreRunReporter(store, store.blobs)
    producer, dataset = origin(), data()
    run = prepare_structured_workload(
        store,
        dataset,
        producer,
        StructuredTrainingConfig(epochs=2, max_updates=3),
        operation_id="1" * 32,
        code_scope=NATIVE_SCOPE,
    )
    request = StructuredWorkloadRequest(
        run.artifact_id, run.parent("training_input"), "1" * 32, "2" * 32
    )
    authority = Authority()
    paused = execute_structured_workload(
        store,
        reporter,
        request,
        producer,
        authority=authority,
        control=PauseControl(),
        attempt_producer=producer,
    )
    assert paused.state == "paused" and paused.optimizer_updates == 1
    resumed = replace(
        request, attempt_id="3" * 32, mode="resume", resume_checkpoint_id=paused.checkpoint_id
    )
    result = execute_structured_workload(
        store, reporter, resumed, producer, authority=authority, attempt_producer=producer
    )
    assert result.state == "completed" and result.optimizer_updates == 3
    package_path = tmp_path / "exported"
    exported = export_native_model(store, result.model_id, package_path)
    package, model = load_native_package(package_path)
    return store, reporter, producer, run, resumed, result, package_path, exported, package, model


def test_native_store_model_export_retains_exact_lineage_and_fresh_stdio(
    completed, agent_files, tmp_path
):
    store, reporter, producer, run, request, result, folder, exported, metadata, learned = completed
    manifest = store.get_manifest(result.model_id)
    require_native_model_package(manifest, metadata)
    assert metadata["schema"] == TRAINED_PACKAGE_SCHEMA
    assert metadata["source"]["source_artifact_id"] == store.get_manifest(
        run.parent("training_input")
    ).parent("source")
    assert metadata["provenance"]["checkpoint_id"] == result.checkpoint_id
    assert exported["artifact_id"] != exported["package_model_id"]
    assert exported["qualification"] == "synthetic_engineering_only"
    original = StructuredTrainingEngine(data(), StructuredTrainingConfig(), code_scope=NATIVE_SCOPE)
    assert any(
        not torch.equal(tensor, learned.state_dict()[name])
        for name, tensor in original.model.state_dict().items()
    )
    reconciled = execute_structured_workload(
        store,
        reporter,
        replace(request, mode="reconcile", resume_checkpoint_id=None),
        producer,
        authority=Authority(),
        attempt_producer=producer,
    )
    assert reconciled.model_id == result.model_id and reconciled.optimizer_updates == 3
    _, _, _, template = agent_files
    path = tmp_path / "trained-agent.json"
    bound = bind_native_agent(
        folder,
        path,
        manifest_id="trained-native-fixture",
        requirements=template["requirements"],
        support=template["support"],
        required_seams=template["input"]["attachment"]["required_seams"],
    )
    stdio_loop((folder, path, metadata, bound))


def test_native_model_parent_binding_and_payload_integrity_are_not_package_identity(
    completed, tmp_path
):
    store, _, _, _, _, result, _, _, package, _ = completed
    model = store.get_manifest(result.model_id)
    bad = Manifest(
        model.kind,
        model.producer,
        tuple(
            Parent(parent.role, "0" * 64 if parent.role == "checkpoint" else parent.artifact_id)
            for parent in model.parents
        ),
        model.payloads,
        model.parameters,
    )
    with pytest.raises(BoundaryError, match="parent_binding"):
        require_native_model_package(bad, package)
    modified = model.parameters.value()
    modified["model_id"] = model.artifact_id
    bad = Manifest(
        model.kind, model.producer, model.parents, model.payloads, FrozenObject.of(modified)
    )
    with pytest.raises(BoundaryError, match="parent_binding"):
        require_native_model_package(bad, package)

    class CorruptStore:
        def get_manifest(self, identity):
            return store.get_manifest(identity)

        def read_payload(self, payload):
            yield b"tampered"

    destination = tmp_path / "refused"
    with pytest.raises(BoundaryError, match="payload_integrity"):
        export_native_model(CorruptStore(), model.artifact_id, destination)
    assert not destination.exists()
