"""Tiny synthetic numerical learning through the existing fenced workload/store."""

from __future__ import annotations

import copy
import hashlib
import subprocess
import sys
from dataclasses import replace

import pytest
import torch
from test_native_structured_model import (
    ack,
    offer,
    snapshot,
)
from test_native_structured_model import agent_files as agent_files
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
from stpd.models.native_structured_scorer import NativeStructuredScorer
from stpd.models.structured_engine import StructuredTrainingEngine
from stpd.models.structured_m2 import StructuredM2
from stpd.models.structured_training import StructuredTrainingConfig
from stpd.native_code_scope import (
    MODEL_SCHEMA,
    PATHS,
    REQUIRED_METHODS,
    TRAINING_PATHS_NATIVE,
    is_native_model_schema,
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


MALFORMED_PUBLIC_FIELDS = [
    ("interaction_id", lambda o: o["interaction"].update(interaction_id=False)),
    ("kind", lambda o: o["interaction"].update(kind=123)),
    ("stage", lambda o: o["interaction"].update(stage=False)),
    ("prompt", lambda o: o["interaction"].update(prompt=123)),
    ("content_scalar", lambda o: o["interaction"].update(content="not-a-content-object")),
    ("content_context_missing", lambda o: o["interaction"].update(content={"surface": {}})),
    ("content_surface_null", lambda o: o["interaction"]["content"].update(surface=None)),
    ("content_context_null", lambda o: o["interaction"]["content"].update(context=None)),
    ("content_schema", lambda o: o["interaction"].update(content_schema=123)),
    ("capabilities_container", lambda o: o["interaction"].update(capabilities="pick")),
    ("capabilities_null_member", lambda o: o["interaction"].update(capabilities=[None])),
    ("persistent_schema", lambda o: o["persistent"].update(content_schema=123)),
    ("persistent_content_null", lambda o: o["persistent"].update(content=None)),
    ("referent_schema", lambda o: o["referents"][0].update(properties_schema=123)),
    ("referent_kind", lambda o: o["referents"][0].update(kind=False)),
    ("referent_role", lambda o: o["referents"][0].update(role=123)),
    ("referent_label", lambda o: o["referents"][0].update(label=True)),
    ("referent_visible", lambda o: o["referents"][0]["state"].update(visible=1)),
    ("referent_enabled", lambda o: o["referents"][0]["state"].update(enabled=0)),
    ("referent_selected", lambda o: o["referents"][0]["state"].update(selected=0)),
    ("referent_focused", lambda o: o["referents"][0]["state"].update(focused=0)),
    ("referent_basis", lambda o: o["referents"][0]["state"].update(observation_basis=True)),
    ("catalog_ref", lambda o: o["catalog"].update(catalog_ref=123)),
    ("catalog_order", lambda o: o["catalog"].update(ordering_semantics=123)),
    ("catalog_access_container", lambda o: o["catalog"].update(access_methods="catalog")),
    ("catalog_access_member", lambda o: o["catalog"].update(access_methods=[123])),
    ("catalog_scope", lambda o: o["catalog"].update(scope_id=False)),
    ("observed_at", lambda o: o.update(observed_at=123)),
    ("revision_long_overflow", lambda o: o.update(revision=1 << 63)),
]


@pytest.mark.parametrize(
    "name,mutate", MALFORMED_PUBLIC_FIELDS, ids=[case[0] for case in MALFORMED_PUBLIC_FIELDS]
)
def test_declared_dto_domains_reject_before_dataset_qualification_or_memory(name, mutate):
    value = copy.deepcopy(source())
    value["runs"][0]["steps"] = value["runs"][0]["steps"][:1]
    step = value["runs"][0]["steps"][0]
    mutate(step["observation"])
    with pytest.raises(BoundaryError):
        parse_native_training_dataset(json_bytes(value))
    model = NativeStructuredScorer(
        model=StructuredM2(seed=0), model_id="a" * 64, weights_sha256="b" * 64
    )
    before = model.memory.clone()
    with pytest.raises(BoundaryError):
        model.propose_consume(offer(step["observation"], step["catalog"]))
    assert model.pending is None and torch.equal(model.memory, before)


@pytest.mark.parametrize(
    "field,value",
    [
        ("verb", 123),
        ("subject_role", False),
        ("arguments", "role"),
        ("availability_basis", 123),
    ],
)
def test_capability_scalar_and_container_domains_are_not_legality_rules(field, value):
    observation, actions = snapshot()
    capability = {
        "verb": "pick",
        "subject_role": None,
        "arguments": [],
        "availability_basis": "public",
    }
    capability[field] = value
    observation["interaction"]["capabilities"] = [capability]
    with pytest.raises(BoundaryError):
        project_native_structured(observation, actions)


@pytest.mark.parametrize(
    "argument", [None, {"role": 123, "required": True}, {"role": "card", "required": 1}]
)
def test_capability_arguments_require_their_declared_record_and_boolean(argument):
    observation, actions = snapshot()
    observation["interaction"]["capabilities"] = [
        {
            "verb": "pick",
            "subject_role": None,
            "arguments": [argument],
            "availability_basis": "public",
        }
    ]
    with pytest.raises(BoundaryError):
        project_native_structured(observation, actions)


def test_dto_nullable_fields_generic_json_nodes_and_unknown_valid_words_are_preserved():
    observation, actions = snapshot()
    observation["interaction"].update(
        kind="future-public-kind",
        stage="future-public-stage",
        prompt=None,
        content={"surface": [None, True, 7], "context": False},
    )
    observation["interaction"]["capabilities"] = [
        {
            "verb": "future-public-verb",
            "subject_role": None,
            "arguments": [{"role": "future-role", "required": False}],
            "availability_basis": "future-public-basis",
        }
    ]
    observation["referents"][0].update(properties_schema=None, properties=None, label=None)
    observation["persistent"]["content"] = [None, True, 7, "public"]
    frame = project_native_structured(observation, actions)
    assert frame.action_ids == tuple(action["action_id"] for action in actions)
    observation["persistent"] = None
    project_native_structured(observation, actions)


def test_observed_public_view_replays_once_without_readiness_or_terminal_inference(agent_files):
    folder, path, _, _ = agent_files
    agent = NativeStructuredAgent(folder, path)
    one, c1 = snapshot(empty=True)
    two, c2 = snapshot(2)
    one["status"] = two["status"] = "observed"
    value = source()
    value["runs"][0]["steps"] = [
        {
            "observation": observation,
            "catalog": catalog,
            "continuity_token": "segment",
            "reset_before": position == 0,
            "chosen_action_id": catalog[0]["action_id"] if catalog else None,
        }
        for position, (observation, catalog) in enumerate(((one, c1), (one, c1), (two, c2)))
    ]
    dataset = parse_native_training_dataset(json_bytes(value))
    assert [step.advance for step in dataset.runs[0].steps] == [True, False, True]
    memory = agent.scorer.model.initial_memory()
    for position, step in enumerate(dataset.runs[0].steps):
        original = value["runs"][0]["steps"][position]
        with torch.inference_mode():
            entities = agent.scorer.model.encode(step.frame)
            if step.advance:
                memory = agent.scorer.model.advance(entities, memory)
        report = agent.consume(
            offer(
                original["observation"],
                original["catalog"],
                f"observed-acquisition-{position}",
                agent.scorer.consumption_id,
            )
        )
        agent.scorer.acknowledge(ack(report, str(position + 1)))
        assert torch.equal(memory, agent.scorer.memory)
        output = agent.next(
            {
                "continuity_token": "segment",
                "consumption_id": report["consumption_id"],
                "state_version": report["state_version"],
                "basis_acquisition_id": f"observed-acquisition-{position}",
                "received_cursor": "observed-cursor",
            }
        )
        assert output["directive"]["type"] == ("act" if original["catalog"] else "await")
        assert original["observation"]["status"] == "observed"
    assert agent.scorer.state_version == 2


def test_actual_stdio_observed_empty_catalog_returns_await(agent_files, monkeypatch):
    import test_native_structured_model as fixture_module

    def observed_snapshot(*args, **kwargs):
        observation, actions = snapshot(*args, **kwargs)
        observation["status"] = "observed"
        return observation, actions

    monkeypatch.setattr(fixture_module, "snapshot", observed_snapshot)
    stdio_loop(agent_files)


def test_native_schema_gate_is_lightweight_exact_and_exporter_reexports_it():
    from stpd.policy.native_structured_export import is_native_model_schema as exported

    assert exported is is_native_model_schema and exported(MODEL_SCHEMA)
    assert not any(exported(value) for value in (None, [], {}, "stpd/structured-m2-model-v3"))
    body = f"""
import importlib.abc, sys
sys.path.insert(0, {str(ROOT)!r})
class NoNumericalBackend(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = {{'torch', 'numpy', 'safetensors', 'tokenizers', 'transformers'}}
        if fullname.split('.')[0] in blocked:
            raise AssertionError('lightweight gate imported numerical backend: '+fullname)
sys.meta_path.insert(0, NoNumericalBackend())
from stpd.native_code_scope import MODEL_SCHEMA, REQUIRED_METHODS, is_native_model_schema
assert MODEL_SCHEMA == 'stpd/native-structured-m2-model-v1'
assert is_native_model_schema(MODEL_SCHEMA)
assert not is_native_model_schema('stpd/structured-m2-model-v1')
assert len(REQUIRED_METHODS) == 15 and {{'retain', 'renew', 'release'}} <= set(REQUIRED_METHODS)
assert not any(name.startswith('stpd.qwen') for name in sys.modules)
"""
    child = subprocess.run([sys.executable, "-I", "-c", body], capture_output=True, text=True)
    assert child.returncode == 0, child.stderr


@pytest.mark.parametrize("method", ["retain", "renew", "release"])
def test_native_binder_requires_declared_retention_lifecycle_methods(agent_files, tmp_path, method):
    folder, _, _, template = agent_files
    requirements = copy.deepcopy(template["requirements"])
    requirements["required_methods"].remove(method)
    path = tmp_path / ("without-" + method + ".json")
    with pytest.raises(BoundaryError, match="required_native_methods"):
        bind_native_agent(
            folder,
            path,
            manifest_id="incomplete-methods",
            requirements=requirements,
            support=template["support"],
            required_seams=template["input"]["attachment"]["required_seams"],
        )
    assert not path.exists()
    assert len(REQUIRED_METHODS) == len(set(REQUIRED_METHODS)) == 15


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
