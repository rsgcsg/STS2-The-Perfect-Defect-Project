"""Original partial-model metadata and strict package lineage; no fitting/game."""

from __future__ import annotations

import hashlib
import io
from dataclasses import replace
from pathlib import Path

import pytest
from test_artifact_store_v1 import PRODUCER, store

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.workbench.public_m2_export_support import require_lineage
from stpd.policy.public_m2_export import model_details, model_lineage

FIXTURE = Path(__file__).parent / "fixtures/public_m2_17eb_model_metadata.json"
IMPLEMENTATION_FILES = (
    "stpd/workers/public_m2_engine.py",
    "stpd/workers/checkpoint_codec.py",
    "stpd/models/public_m2_window.py",
    "stpd/models/light_action_m2.py",
    "stpd/models/light_action_m2_training_data.py",
    "stpd/models/light_action_encoder.py",
    "stpd/models/losses.py",
    "stpd/models/token_core.py",
    "stpd/light_action_codec.py",
)


def original_model():
    return Manifest.from_bytes(FIXTURE.read_bytes())


def test_original_epoch_one_model_and_numerical_scope_remain_exact():
    model = original_model()
    before = model.to_bytes()
    config, info = model_details(model)
    assert model.artifact_id == "17ebf91f16579b63e4924e7b1eaf95cf8195753937c7c6cb06347223f7e51216"
    assert info["epoch"] == 1 and info["run_complete"] is False
    assert config.device == "cuda:0" and config.epochs == 5
    assert config.slots == 8 and config.shape.width == 384 and config.max_actions_per_step == 31
    assert config.prior_action_profile == "stpd/public-m2-no-prior-action-v1"
    assert config.feedback_profile == "stpd/public-m2-no-feedback-v1"
    checksum = hashlib.sha256()
    root = Path(__file__).resolve().parents[1]
    for name in IMPLEMENTATION_FILES:
        checksum.update(name.encode())
        checksum.update((root / name).read_bytes())
    assert (
        checksum.hexdigest()
        == info["implementation_sha256"]
        == ("8e27fba36e7c45bc3d7ad6a47a3570f7a9481180aed16db186c229f12b13d0f3")
    )
    assert model.to_bytes() == before


@pytest.mark.parametrize(
    "change",
    [
        lambda info: info.update(run_complete=True),
        lambda info: info.update(epoch=2),
        lambda info: info["config"].update(epochs=1),
        lambda info: info["config"].update(prior_action_profile="infer_last_action"),
        lambda info: info["config"].update(feedback_profile="native_result"),
        lambda info: info["config"].update(device="automatic"),
        lambda info: info["config"].update(extra=True),
    ],
)
def test_original_model_rejects_false_completion_or_changed_input_contract(change):
    model = original_model()
    info = model.parameters.value()
    change(info)
    with pytest.raises(BoundaryError):
        model_details(replace(model, parameters=FrozenObject.of(info)))


def stage_graph(tmp_path):
    archive = store(tmp_path / "store")
    info = original_model().parameters.value()

    def publish(kind, parameters, parents=(), payloads=()):
        item = Manifest(
            kind,
            PRODUCER,
            tuple(Parent(role, item.artifact_id) for role, item in parents),
            tuple(payloads),
            FrozenObject.of(parameters),
        )
        archive.publish(item)
        return archive.get_manifest(item.artifact_id)

    dataset = publish("dataset", {"schema": "test/public-dataset"})
    allocation = publish(
        "protocol", {"schema": "stpd/decision-allocation-v1"}, [("dataset", dataset)]
    )
    view = publish(
        "model_view",
        {
            "schema": "stpd/public-observation-bc-view-v2",
            "serializer": {
                "version": "stpd-public-snapshot-compact-v2",
                "profile": "public_compact",
                "status": "provisional",
            },
        },
        [("allocation", allocation), ("dataset", dataset)],
    )
    tokenizer = archive.put_payload(
        "state_tokenizer", io.BytesIO(b"metadata-only codec"), "application/json"
    )
    weights = archive.put_payload(
        "weights", io.BytesIO(b"metadata-only weights"), "application/vnd.stpd.tensor-tree"
    )
    info["config"]["state_tokenizer_sha256"] = tokenizer.sha256
    training = publish(
        "training_input",
        {"schema": "stpd/public-m2-worker-input-v1", "input_identity": info["input_identity"]},
        [("allocation", allocation), ("source_view", view)],
        [tokenizer],
    )
    common = {
        key: info[key]
        for key in (
            "operation_id",
            "config",
            "input_identity",
            "engine_input_digest",
            "implementation_sha256",
        )
    }
    run = publish(
        "run",
        {
            **common,
            "schema": "stpd/public-m2-run-v1",
            "source_view_id": view.artifact_id,
            "allocation_id": allocation.artifact_id,
        },
        [("training_input", training)],
    )
    checkpoint = publish(
        "checkpoint",
        {
            **common,
            "schema": "stpd/public-m2-worker-checkpoint-v1",
            "completed_epochs": 1,
            "chain_index": 0,
            "window_cursor": 0,
        },
        [("run", run), ("training_input", training)],
        [archive.put_payload("checkpoint", io.BytesIO(b"metadata-only checkpoint"))],
    )
    model = publish(
        "model",
        info,
        [("run", run), ("training_input", training), ("checkpoint", checkpoint)],
        [tokenizer, weights],
    )
    report = publish(
        "offline_evaluation",
        {
            "schema": "stpd/public-m2-dev-evaluation-v1",
            "epoch": 1,
            "partition": "dev",
            "input_identity": info["input_identity"],
            "loss_mean": 1.0,
            "top1_accuracy": 0.5,
            "label_count": 2,
            "correct_count": 1,
            "qualification": "engineering_only",
        },
        [("model", model), ("checkpoint", checkpoint), ("run", run), ("training_input", training)],
    )
    stage = publish(
        "analysis",
        {
            "schema": "stpd/public-m2-epoch-stage-v1",
            "epoch": 1,
            "run_complete": False,
            "operation_id": info["operation_id"],
            "input_identity": info["input_identity"],
        },
        [
            ("model", model),
            ("checkpoint", checkpoint),
            ("run", run),
            ("training_input", training),
            ("offline_evaluation", report),
        ],
    )
    return archive, model, stage


def test_partial_stage_requires_exact_lineage_and_current_use_without_new_reservation(tmp_path):
    archive, model, stage = stage_graph(tmp_path)
    calls = []

    class Owner:
        def verified_training_receipt(self, *args):
            calls.append(args)

    value = require_lineage(archive, Owner(), model, stage_id=stage.artifact_id)
    assert value["completion_id"] is None
    assert value["checkpoint_id"] == model.parent("checkpoint")
    assert calls == [(archive, (value["dataset_id"],), value["training_operation_id"])]


def test_crossed_partial_stage_is_rejected_before_current_use(tmp_path):
    archive, model, stage = stage_graph(tmp_path)
    wrong = replace(stage, parameters=FrozenObject.of({**stage.parameters.value(), "epoch": 3}))
    archive.publish(wrong)
    with pytest.raises(BoundaryError, match="epoch_stage_required"):
        model_lineage(archive, model, stage_id=wrong.artifact_id)


def test_malformed_evaluation_cannot_qualify_matching_stage(tmp_path):
    archive, model, stage = stage_graph(tmp_path)
    evaluation = archive.get_manifest(stage.parent("offline_evaluation"))
    malformed = replace(
        evaluation,
        parameters=FrozenObject.of({**evaluation.parameters.value(), "top1_accuracy": 1.0}),
    )
    archive.publish(malformed)
    wrong = replace(
        stage,
        parents=tuple(
            Parent(p.role, malformed.artifact_id) if p.role == "offline_evaluation" else p
            for p in stage.parents
        ),
    )
    archive.publish(wrong)
    with pytest.raises(BoundaryError, match="epoch_evaluation_required"):
        model_lineage(archive, model, stage_id=wrong.artifact_id)
