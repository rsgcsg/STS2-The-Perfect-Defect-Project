"""Pure rehashed cross-arm/producer/budget negatives; no numerical parameters or fit."""

from __future__ import annotations

import io
import json
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from metadata_import_guard import no_torch_imports as no_torch_imports
from test_artifact_store_v1 import PRODUCER
from test_reward_m0_pair import pair_originals as pair_originals
from test_reward_m0_recipe import publish_pair

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.replaceable_file import write_replaceable_json
from spireagent.workbench.local_training import LocalTrainingService
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.trusted_recipes import describe_recipe
from stpd.fullrun.reward_m0_source import verify_reward_m0_source
from stpd.reward_m0_spec import (
    CHECKPOINT_SCHEMA,
    INPUT_SPECS,
    MODEL_SCHEMA,
    RECIPE,
    RESULT_SCHEMA,
    RewardM0Config,
)
from stpd.workers.reward_m0_run import (
    _data_identity,
    _paired_plan,
    prepare_reward_m0_run,
    verify_reward_m0_completed,
)


@pytest.fixture
def metadata_pair(pair_originals, monkeypatch):
    store, _, _, _, _ = pair_originals
    source_id, config = publish_pair(pair_originals, monkeypatch)
    source = verify_reward_m0_source(store, source_id)
    runs = [
        prepare_reward_m0_run(store, source, replace(config, arm=arm), PRODUCER, uuid.uuid4().hex)
        for arm in ("A", "B")
    ]
    return store, source, config, runs


def completion_metadata(store, source, config, run, *, data_identity_arm=None):
    config = replace(config, arm=run.parameters.value()["arm"])
    training = store.get_manifest(run.parent("training_input"))
    backbone = {"state_codec": training.parameters.value()["state_codec"]}
    plan = _paired_plan(config, len(source.value["rows"]))
    chosen = config if data_identity_arm is None else replace(config, arm=data_identity_arm)
    identity = _data_identity(training.artifact_id, chosen, plan, backbone)
    attempt = "a" * 32
    # Opaque metadata-only bytes ensure a negative must fail BEFORE the Torch codec.
    payload = store.put_payload(
        "checkpoint", io.BytesIO(b"metadata-not-numerical"), "application/octet-stream"
    )
    cp = Manifest(
        "checkpoint",
        PRODUCER,
        (Parent("run", run.artifact_id), Parent("training_input", training.artifact_id)),
        (payload,),
        FrozenObject.of(
            {
                "schema": CHECKPOINT_SCHEMA,
                "attempt": attempt,
                "arm": config.arm,
                "source_id": source.manifest.artifact_id,
                "input_spec": INPUT_SPECS[config.arm],
                "data_identity": identity,
                "paired_plan": plan,
                "optimizer_updates": config.steps,
                "initial_weights_sha256": "b" * 64,
            }
        ),
    )
    store.publish(cp)
    weights = store.put_payload(
        "weights", io.BytesIO(b"metadata-not-trained"), "application/octet-stream"
    )
    model = Manifest(
        "model",
        PRODUCER,
        (
            Parent("run", run.artifact_id),
            Parent("training_input", training.artifact_id),
            Parent("source", source.manifest.artifact_id),
            Parent("checkpoint", cp.artifact_id),
        ),
        (weights,),
        FrozenObject.of(
            {
                "schema": MODEL_SCHEMA,
                "arm": config.arm,
                "config": config.to_dict(),
                "source_id": source.manifest.artifact_id,
                "input_spec": INPUT_SPECS[config.arm],
                "data_identity": identity,
                "backbone": backbone,
                "paired_plan": plan,
                "paired_feature_id": training.parent("paired_features"),
                "weights_sha256": weights.sha256,
                "initial_weights_sha256": "b" * 64,
            }
        ),
    )
    store.publish(model)
    metrics_value = {
        "schema": RESULT_SCHEMA,
        "arm": config.arm,
        "source_id": source.manifest.artifact_id,
        "input_id": training.artifact_id,
        "input_spec": INPUT_SPECS[config.arm],
        "data_identity": identity,
        "attempt": attempt,
        "evaluation_scope": "TRAIN_internal_diagnostic_no_independent_heldout",
        "eligible_N": len(source.value["rows"]),
        "rows": [{"occurrence_id": row["occurrence_id"]} for row in source.value["rows"]],
    }
    metrics = store.put_payload(
        "metrics", io.BytesIO(json_bytes(metrics_value)), "application/json"
    )
    result = Manifest(
        "run_result",
        PRODUCER,
        (
            Parent("run", run.artifact_id),
            Parent("training_input", training.artifact_id),
            Parent("model", model.artifact_id),
            Parent("checkpoint", cp.artifact_id),
        ),
        (metrics,),
        FrozenObject.of(
            {
                "schema": "stpd/run-result-v1",
                "domain_schema": RESULT_SCHEMA,
                "state": "completed",
                "arm": config.arm,
                "source_id": source.manifest.artifact_id,
                "input_spec": INPUT_SPECS[config.arm],
                "data_identity": identity,
                "attempt": attempt,
                "optimizer_updates": config.steps,
            }
        ),
    )
    store.publish(result)
    return cp, model, result


def test_rehashed_cross_arm_chain_cannot_relabel_B_as_A(metadata_pair):
    store, source, config, (arm_a, arm_b) = metadata_pair
    cp_b, model_b, result_b = completion_metadata(store, source, config, arm_b)
    input_a = arm_a.parent("training_input")
    cfg_a = replace(config, arm="A")
    cp = replace(
        cp_b,
        parents=(Parent("run", arm_a.artifact_id), Parent("training_input", input_a)),
        parameters=FrozenObject.of(
            {**cp_b.parameters.value(), "arm": "A", "input_spec": INPUT_SPECS["A"]}
        ),
    )
    store.publish(cp)
    model = replace(
        model_b,
        parents=(
            Parent("run", arm_a.artifact_id),
            Parent("training_input", input_a),
            Parent("source", source.manifest.artifact_id),
            Parent("checkpoint", cp.artifact_id),
        ),
        parameters=FrozenObject.of(
            {
                **model_b.parameters.value(),
                "arm": "A",
                "config": cfg_a.to_dict(),
                "input_spec": INPUT_SPECS["A"],
            }
        ),
    )
    store.publish(model)
    metrics = dict(json.loads(b"".join(store.read_payload(result_b.payload("metrics")))))
    metrics.update(arm="A", input_id=input_a, input_spec=INPUT_SPECS["A"])
    metric_payload = store.put_payload(
        "metrics", io.BytesIO(json_bytes(metrics)), "application/json"
    )
    result = replace(
        result_b,
        parents=(
            Parent("run", arm_a.artifact_id),
            Parent("training_input", input_a),
            Parent("checkpoint", cp.artifact_id),
            Parent("model", model.artifact_id),
        ),
        payloads=(metric_payload,),
        parameters=FrozenObject.of(
            {**result_b.parameters.value(), "arm": "A", "input_spec": INPUT_SPECS["A"]}
        ),
    )
    store.publish(result)
    with pytest.raises(BoundaryError, match="completed_model_lineage_mismatch"):
        verify_reward_m0_completed(
            store, SimpleNamespace(completed=lambda _run: result), arm_a.artifact_id, "f" * 32
        )


@pytest.mark.parametrize(
    "field",
    [
        "cp_input",
        "cp_arm",
        "cp_source",
        "cp_spec",
        "cp_identity",
        "result_input",
        "result_arm",
        "result_source",
        "result_spec",
        "result_identity",
        "metrics_arm",
        "metrics_identity",
    ],
)
def test_rehashed_completed_binding_cannot_replace_admitted_input(metadata_pair, field):
    store, source, config, (run, other) = metadata_pair
    cp, model, result = completion_metadata(store, source, config, run)
    if field.startswith("cp_"):
        info = cp.parameters.value()
        if field == "cp_input":
            cp = replace(
                cp,
                parents=(
                    Parent("run", run.artifact_id),
                    Parent("training_input", other.parent("training_input")),
                ),
            )
        else:
            key = {
                "cp_arm": "arm",
                "cp_source": "source_id",
                "cp_spec": "input_spec",
                "cp_identity": "data_identity",
            }[field]
            info[key] = (
                "B" if key == "arm" else INPUT_SPECS["B"] if key == "input_spec" else "f" * 64
            )
            cp = replace(cp, parameters=FrozenObject.of(info))
        store.publish(cp)
        model = replace(
            model,
            parents=tuple(
                Parent(p.role, cp.artifact_id) if p.role == "checkpoint" else p
                for p in model.parents
            ),
        )
        store.publish(model)
        result = replace(
            result,
            parents=tuple(
                Parent(p.role, cp.artifact_id)
                if p.role == "checkpoint"
                else Parent(p.role, model.artifact_id)
                if p.role == "model"
                else p
                for p in result.parents
            ),
        )
    elif field.startswith("result_"):
        if field == "result_input":
            result = replace(
                result,
                parents=tuple(
                    Parent(p.role, other.parent("training_input"))
                    if p.role == "training_input"
                    else p
                    for p in result.parents
                ),
            )
        else:
            info = result.parameters.value()
            key = {
                "result_arm": "arm",
                "result_source": "source_id",
                "result_spec": "input_spec",
                "result_identity": "data_identity",
            }[field]
            info[key] = (
                "B" if key == "arm" else INPUT_SPECS["B"] if key == "input_spec" else "f" * 64
            )
            result = replace(result, parameters=FrozenObject.of(info))
    else:
        metrics = json.loads(b"".join(store.read_payload(result.payload("metrics"))))
        metrics["arm" if field == "metrics_arm" else "data_identity"] = (
            "B" if field == "metrics_arm" else "f" * 64
        )
        payload = store.put_payload("metrics", io.BytesIO(json_bytes(metrics)), "application/json")
        result = replace(result, payloads=(payload,))
    store.publish(result)
    with pytest.raises(BoundaryError, match="completed_.*mismatch"):
        verify_reward_m0_completed(
            store, SimpleNamespace(completed=lambda _run: result), run.artifact_id, "f" * 32
        )


def test_frozen_pair_denies_wall_amendment_without_journal_or_artifact_writes(pair_originals):
    _, owner, config, _, _ = pair_originals
    service = LocalTrainingService(config)
    operation_id, attempt = uuid.uuid4().hex, uuid.uuid4().hex
    request = TrainingRequest(
        uuid.uuid4().hex,
        RECIPE,
        "a" * 64,
        RewardM0Config().to_dict(),
        limits={"wall_seconds": 120, "scratch_bytes": 512 * 1024**2},
    )
    value = service._new_operation(request, owner, operation_id, previous={"status": "idle"})
    value.update(
        status="paused",
        writer_terminal=True,
        attempt_id=attempt,
        run_id="b" * 64,
        input_id="c" * 64,
        checkpoint_id="d" * 64,
    )
    path, lock = service._paths(owner)
    lock.touch()
    write_replaceable_json(path, value)
    before = path.read_bytes()
    assert "amend_wall_budget" not in describe_recipe(RECIPE)["supported_actions"]
    assert "amend_wall_budget" not in service.status()["operation"]["supported_actions"]
    with pytest.raises(BoundaryError, match="frozen_pair_budget_amendment_forbidden"):
        service.amend_wall_budget(operation_id, attempt, uuid.uuid4().hex, 600)
    assert path.read_bytes() == before
    value["budget_amendments"] = [{"unexpected": "larger single-arm cap"}]
    with pytest.raises(ValueError, match="frozen_pair_budget_amendment_forbidden"):
        service._validate_v3(value, owner.identity)


def test_unscoped_resume_cannot_claim_another_actual_producer(metadata_pair, monkeypatch):
    from spireagent.workbench.recipes.reward_m0 import verify_reward_resume_checkpoint

    store, _, _, (run, _) = metadata_pair
    monkeypatch.setattr(
        "spireagent.workbench.recipes.reward_m0.source_identity",
        lambda _root: Producer("fixture://different-actual-writer", "f" * 40, "f" * 64),
    )
    with pytest.raises(BoundaryError, match="unscoped_resume_current_producer_mismatch"):
        verify_reward_resume_checkpoint(store, {"run_id": run.artifact_id}, "a" * 64)
