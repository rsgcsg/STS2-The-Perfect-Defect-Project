"""Actual Source3 producer-fixture, typed verifier, Store and worker composition.

The committed C# production-path golden is synthetic. These CPU2 tests prove
consumer source/test behavior, never real gameplay, Human origin or model quality.
"""

from __future__ import annotations

import io
import json
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import sts2_platform_evidence as evidence_owner
from test_native_agent_application import native_capabilities, settled
from test_native_structured_model import ack, offer
from test_native_structured_training import origin
from test_native_structured_training import two_threads as two_threads
from test_native_task_completion import next_input, terminal_snapshot
from test_protocol_source import setup_store
from test_structured_resume import Authority, PauseControl, equal_tree, finish

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, json_bytes
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_model_export import LocalModelExport
from spireagent.workbench.local_model_registration import LocalModelRegistration
from spireagent.workbench.local_models import LocalModelService
from spireagent.workbench.local_training import LocalTrainingService
from spireagent.workbench.native_agent_support import validate
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.recipes import structured as adapter_module
from stpd.fullrun import ordered_source as sources
from stpd.models.structured_engine import StructuredTrainingEngine
from stpd.models.structured_training import StructuredTrainingConfig
from stpd.ordered_source_spec import (
    DEFAULT_RECIPE,
    DEFAULT_VIEW,
    EVALUATION_REPORT_SCHEMA,
    MODEL_SCHEMA,
    PACKAGE_SCHEMA,
    PRETRAIN_VIEW,
    SCOPE,
    recipe_control,
)
from stpd.policy.native_agent import NativeStructuredAgent
from stpd.policy.native_structured_export import export_native_model, load_native_package
from stpd.policy.native_task import ready_summary_task_spec
from stpd.structured_code_scope import ROOT
from stpd.workers.structured_control import StructuredWorkloadRequest
from stpd.workers.structured_evaluation import (
    StructuredEvaluationRequest,
    prepare_structured_evaluation,
    run_structured_evaluation,
)
from stpd.workers.structured_execution import (
    execute_structured_workload,
    prepare_structured_workload,
)

GOLDEN = ROOT.parent / "components/evidence/tests/fixtures/source_session_v3/bundle"
CONTENT_ID = "03b032ff677b3e0ff0218750c492bc133d63b024227da8f823d161467e577563"
ORIGINAL = Producer("fixture://original-Source3-production-path", "e" * 40, "a" * 64)


def source(store, *, split="train", cohort="agent_protocol", view=DEFAULT_VIEW):
    original = evidence_owner.verify_source_session_bundle_v3(GOLDEN)
    assert isinstance(original.value, evidence_owner.SourceSessionBundleV3)
    assert original.passed and original.value.content_id == CONTENT_ID
    assert original.descriptor.version == 3
    assert not original.value.human_origin_verified
    raw = sources.publish_ordered_source_raw(store, GOLDEN, ORIGINAL)
    ref = sources.publish_ordered_source_admission(
        store, raw.artifact_id, origin(), cohort=cohort, view=view
    )
    partition = sources.publish_ordered_source_partition(store, (ref,), split, origin())
    return raw, ref, partition


def test_actual_original_archive_admission_and_sparse_N_are_replayed(tmp_path):
    store, _owner = setup_store(tmp_path)
    raw, ref, partition = source(store)
    report = decode_json(store.bytes(store.get_manifest(ref.admission_id).payload("report")))
    assert raw.producer == ORIGINAL
    assert raw.parameters.value()["source_kinds"] == ["agent_protocol", "declared_human"]
    assert report["N_coverage"] == {
        "cohort": "agent_protocol",
        "eligible": 1,
        "denominator": 1,
        "fraction": 1.0,
    }
    assert report["counts"]["original_publications"] == 6
    assert report["counts"]["original_inputs"] == 1
    assert report["counts"]["admitted_frames"] == 6
    assert report["counts"]["excluded_frames"] == 1
    assert report["counts"]["whole_game_recorded_capture_runs"] == 0
    assert not partition.dataset.capsules_verified
    assert (
        sources.verify_ordered_source_partition(store, partition.manifest.artifact_id) == partition
    )
    labelled = [
        step
        for run in partition.dataset.runs
        for step in run.steps
        if step.chosen_action_id is not None
    ]
    assert len(labelled) == 1 and not labelled[0].advance
    _, _, human = source(store, cohort="declared_human")
    assert not any(step.chosen_action_id for run in human.dataset.runs for step in run.steps)
    assert human.runs == partition.runs and human.source_groups == partition.source_groups


@pytest.mark.parametrize("which", ["admission", "partition"])
def test_rehashed_report_or_projection_cannot_replace_original_join(tmp_path, which):
    store, _owner = setup_store(tmp_path)
    _raw, ref, partition = source(store)
    if which == "admission":
        manifest = store.get_manifest(ref.admission_id)
        value = decode_json(store.bytes(manifest.payload("report")))
        value["counts"]["eligible_unique_N"] = 100
        payload = store.put_payload("report", io.BytesIO(json_bytes(value)), "application/json")
        forged = Manifest(
            "analysis", manifest.producer, manifest.parents, (payload,), manifest.parameters
        )
        store.publish(forged)
        with pytest.raises(BoundaryError, match="source3_admission_report_binding"):
            sources.publish_ordered_source_partition(
                store,
                (sources.OrderedSourceRef(ref.raw_id, forged.artifact_id),),
                "train",
                origin(),
            )
    else:
        manifest = partition.manifest
        value = decode_json(partition.dataset.source_bytes)
        for run in value["runs"]:
            for step in run["steps"]:
                step["chosen_action_id"] = None
        payload = store.put_payload("source", io.BytesIO(json_bytes(value)), "application/json")
        info = manifest.parameters.value() | {"source_sha256": payload.sha256}
        forged = Manifest(
            "dataset", manifest.producer, manifest.parents, (payload,), FrozenObject.of(info)
        )
        store.publish(forged)
        with pytest.raises(BoundaryError, match="projected_original_join_mismatch"):
            sources.verify_ordered_source_partition(store, forged.artifact_id)


def test_view_cohort_duplicates_and_original_producer_are_closed(tmp_path):
    store, _owner = setup_store(tmp_path)
    raw, ref, _partition = source(store)
    human = sources.publish_ordered_source_admission(store, raw.artifact_id, origin())
    pretrain = sources.publish_ordered_source_admission(
        store, raw.artifact_id, origin(), cohort="agent_protocol", view=PRETRAIN_VIEW
    )
    for other in (ref, human, pretrain):
        with pytest.raises(BoundaryError, match="duplicate_raw_source"):
            sources.publish_ordered_source_partition(store, (ref, other), "train", origin())
    duplicate = sources.publish_ordered_source_raw(
        store, GOLDEN, replace(ORIGINAL, repository="fixture://copied-original")
    )
    duplicate_ref = sources.publish_ordered_source_admission(
        store, duplicate.artifact_id, origin(), cohort="agent_protocol"
    )
    with pytest.raises(BoundaryError, match="duplicate_original_bundle_content"):
        sources.publish_ordered_source_partition(store, (ref, duplicate_ref), "train", origin())
    before = store.manifest_ids()
    with pytest.raises(BoundaryError, match="original_recorder_producer_mismatch"):
        sources.publish_ordered_source_raw(
            store, GOLDEN, replace(ORIGINAL, source_revision="f" * 40)
        )
    assert store.manifest_ids() == before


def test_raw_masked_context_and_reexpression_remain_one_use_and_split_family(tmp_path):
    store, owner = setup_store(tmp_path)
    raw, _ref, train = source(store)
    owner.reserve_verified_ordered_source(store, train.manifest.artifact_id)
    receipt = owner.record_verified_ordered_training_use(
        store, train.manifest.artifact_id, "1" * 32
    )
    assert receipt["qualified_run_ids"] == sorted(train.runs)
    for cohort, view in (("agent_protocol", PRETRAIN_VIEW), ("declared_human", DEFAULT_VIEW)):
        _, _, held_out = source(store, split="test", cohort=cohort, view=view)
        with pytest.raises(BoundaryError, match="protocol_split_purpose_overlap"):
            owner.reserve_verified_ordered_source(store, held_out.manifest.artifact_id)
    with owner.transaction() as db:
        assert db.execute(
            "SELECT count(*) FROM curation_source_decisions WHERE source=?", (raw.artifact_id,)
        ).fetchone() == (7,)
        assert db.execute("SELECT count(*) FROM curation_occurrences").fetchone() == (0,)
        db.execute("DELETE FROM curation_source_decisions WHERE source=?", (raw.artifact_id,))
    with pytest.raises(BoundaryError, match="source_index_incomplete"):
        owner.require_verified_ordered_training_use(store, train.manifest.artifact_id, "1" * 32)


def test_existing_gold_original_epoch_family_stays_reserved(tmp_path):
    store, owner = setup_store(tmp_path)
    original = evidence_owner.verify_source_session_bundle_v3(GOLDEN)
    assert original.passed and isinstance(original.value, evidence_owner.SourceSessionBundleV3)
    reserved = {sources._run_identity(original.value, epoch)[0] for epoch in original.value.epochs}
    owner.ledger.claim("gold-source3-fixture", "gold", reserved)
    _, _, train = source(store)
    assert train.runs == reserved
    with pytest.raises(BoundaryError, match="gold_reserved_data"):
        owner.reserve_verified_ordered_source(store, train.manifest.artifact_id)


@pytest.fixture
def completed(tmp_path):
    store, owner = setup_store(tmp_path)
    _raw, _ref, partition = source(store)
    config = StructuredTrainingConfig(epochs=2, max_updates=2)
    control = recipe_control(DEFAULT_RECIPE)
    producer = origin()
    reporter = ObjectStoreRunReporter(store, store.blobs)
    run = prepare_structured_workload(
        store,
        partition.dataset,
        producer,
        config,
        source_id=partition.manifest.artifact_id,
        operation_id="1" * 32,
        code_scope=SCOPE,
        model_control=control,
    )
    request = StructuredWorkloadRequest(
        run.artifact_id, run.parent("training_input"), "1" * 32, "2" * 32
    )
    paused = execute_structured_workload(
        store,
        reporter,
        request,
        producer,
        authority=Authority(),
        control=PauseControl(),
        attempt_producer=producer,
    )
    assert paused.state == "paused" and paused.optimizer_updates == 1
    result = execute_structured_workload(
        store,
        reporter,
        replace(
            request, attempt_id="3" * 32, mode="resume", resume_checkpoint_id=paused.checkpoint_id
        ),
        producer,
        authority=Authority(),
        attempt_producer=producer,
    )
    assert result.state == "completed" and result.optimizer_updates == 2
    assert result.model_id is not None
    folder = tmp_path / "native-package"
    export_native_model(store, result.model_id, folder)
    metadata, model = load_native_package(folder)
    original = StructuredTrainingEngine(
        partition.dataset, config, code_scope=SCOPE, model_control=control
    )
    finish(original)
    equal_tree(original.model.state_dict(), model.state_dict())
    return store, owner, partition, result, folder, metadata


def test_actual_source3_workload_resume_export_and_fixed_weights_evaluation(completed, tmp_path):
    store, _, train, result, folder, package = completed
    assert store.get_manifest(result.model_id).parameters.value()["schema"] == MODEL_SCHEMA
    assert package["schema"] == PACKAGE_SCHEMA
    assert package["agent_spec"]["version"] == "1.2.0"
    assert package["agent_spec"]["task_spec"] == ready_summary_task_spec()
    assert package["source"]["source_artifact_id"] == train.manifest.artifact_id
    assert package["provenance"]["checkpoint_id"] == result.checkpoint_id
    # Domain replay is descriptive. An application must still perform its ledger
    # overlap check; this same-origin dev copy is not an unseen held-out claim.
    _, _, dev = source(store, split="dev")
    weights_before = (folder / "weights.tensor-tree").read_bytes()
    request = StructuredEvaluationRequest(
        dev.manifest.artifact_id, result.model_id, "4" * 32, "dev"
    )
    prepared = prepare_structured_evaluation(store, request, origin())
    evaluation = run_structured_evaluation(store, prepared.artifact_id, origin())
    report = decode_json(store.bytes(evaluation.payload("report")))
    assert report["schema"] == EVALUATION_REPORT_SCHEMA
    assert report["optimizer_updates"] == 0 and report["known_label_denominator"] == 1
    assert not report["claims"]["independent_generalization"]
    assert sum(row["label_index"] is not None for row in report["rows"]) == 1
    assert (folder / "weights.tensor-tree").read_bytes() == weights_before
    _, _, pretrain = source(store, split="dev", view=PRETRAIN_VIEW)
    with pytest.raises(BoundaryError, match="projection_target_view_mismatch"):
        prepare_structured_evaluation(
            store, replace(request, source_id=pretrain.manifest.artifact_id), origin()
        )


def test_actual_source3_application_child_use_export_and_native_registry(tmp_path, monkeypatch):
    store, owner = setup_store(tmp_path)
    _, _, partition = source(store)
    owner.reserve_verified_ordered_source(store, partition.manifest.artifact_id)
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    service = LocalTrainingService(config)
    request = TrainingRequest(
        "1" * 32,
        DEFAULT_RECIPE,
        partition.manifest.artifact_id,
        {"epochs": 1},
        limits={"wall_seconds": 600},
    )
    service.start(request)
    assert service._thread is not None
    service._thread.join(timeout=30)
    completed_operation = service.status()["operation"]
    assert not service._thread.is_alive()
    assert completed_operation["status"] == "completed", completed_operation
    assert completed_operation["validation_state"] == "verified"
    owner.require_verified_ordered_training_use(
        store, partition.manifest.artifact_id, completed_operation["operation_id"]
    )
    export = LocalModelExport(config)
    monkeypatch.setattr(export, "_workspace", lambda: SimpleNamespace(store=store))
    export.start(completed_operation["model_id"])
    assert settled(export)["status"] == "completed"
    folder = export.verified_for_registration(completed_operation["model_id"])
    package, _model = load_native_package(folder)
    models = LocalModelService(config)
    registration = LocalModelRegistration(config, export, models)
    # Synthetic runtime/capability metadata isolate registration from a game.
    # Actual package loading, binding and own-byte validation are exercised.
    monkeypatch.setattr(registration, "_native_runtime", lambda **_: (Path("/test"), Path("/sdk")))
    monkeypatch.setattr(registration, "_capabilities", lambda *_, **__: native_capabilities())
    monkeypatch.setattr(models, "_public_manifest_contract", lambda *_, **__: None)
    registered = registration.register(completed_operation["model_id"])
    assert registered["status"] == "registered" and not registered["loaded"]
    entry = models.selection(registered["selection_id"])
    bound, manifest = validate(
        models.root,
        models.entry_path(entry, "config"),
        models.entry_path(entry, "manifest"),
        binding_root=models.private_root,
    )
    assert bound["package_model_id"] == package["model_id"]
    assert manifest["agent"]["version"] == "1.2.0"
    agent = NativeStructuredAgent(folder, models.entry_path(entry, "manifest"))
    assert agent.metadata["agent_spec"]["task_spec"] == ready_summary_task_spec()
    observation, catalog = terminal_snapshot()
    agent.scorer.acknowledge(ack(agent.consume(offer(observation, catalog))))
    memory_before = agent.scorer.memory.clone()
    monkeypatch.setattr(agent.scorer, "scores", lambda: pytest.fail("ready task must not score"))
    assert agent.next(next_input(agent))["directive"] == {
        "type": "close",
        "reason": "native_ready_summary_task_complete",
    }
    assert agent.scorer.input is not None and agent.scorer.input["catalog"] == catalog
    equal_tree(memory_before, agent.scorer.memory)
    assert models.readiness(entry["id"])["checks"]["policy_identity"] == {"status": "pass"}
    models.close()


def test_actual_source3_private_child_pause_requires_explicit_checkpoint_resume(
    tmp_path, monkeypatch
):
    store, owner = setup_store(tmp_path)
    _, _, partition = source(store)
    owner.reserve_verified_ordered_source(store, partition.manifest.artifact_id)
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    service = LocalTrainingService(config)
    request = TrainingRequest(
        "1" * 32,
        DEFAULT_RECIPE,
        partition.manifest.artifact_id,
        {"epochs": 2},
        limits={"wall_seconds": 600},
    )
    original = adapter_module._private_child
    entered, released = threading.Event(), threading.Event()
    held_once = False

    def delayed(command, *args, **kwargs):
        parent_message = kwargs["on_stdout_line"]

        def message(raw):
            nonlocal held_once
            if json.loads(raw)["kind"] == "prepared" and not held_once:
                held_once = True
                entered.set()
                assert released.wait(3)
            parent_message(raw)

        return original(command, *args, **{**kwargs, "on_stdout_line": message})

    monkeypatch.setattr(adapter_module, "_private_child", delayed)
    started = service.start(request)["operation"]
    assert entered.wait(10)
    service.pause(started["operation_id"], started["attempt_id"])
    released.set()
    assert service._thread is not None
    service._thread.join(timeout=30)
    paused = service.status()["operation"]
    assert not service._thread.is_alive()
    assert paused["status"] == "paused" and paused["checkpoint_id"]
    assert "model_id" not in paused
    resumed = service.resume(
        paused["operation_id"],
        paused["attempt_id"],
        paused["checkpoint_id"],
        "2" * 32,
        request.limits,
    )
    assert resumed["operation"]["attempt_id"] != paused["attempt_id"]
    assert service._thread is not None
    service._thread.join(timeout=30)
    completed = service.status()["operation"]
    assert not service._thread.is_alive()
    assert completed["status"] == "completed", completed
    assert completed["run_id"] == paused["run_id"]
    assert store.get_manifest(completed["model_id"]).parameters.value()["schema"] == MODEL_SCHEMA
    owner.require_verified_ordered_training_use(
        store, partition.manifest.artifact_id, completed["operation_id"]
    )
