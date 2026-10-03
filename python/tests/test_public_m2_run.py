"""Synthetic typed public M2 run receipts; no real-data admission or model claim."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, replace
from unittest.mock import patch

import pytest
import torch
from tokenizers import Tokenizer, models

import stpd.workers.public_m2_run as run_module
from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from stpd.canonical import semantic_hash
from stpd.fullrun.decision_training import ALLOCATION_SCHEMA
from stpd.fullrun.light_action_inputs import PUBLIC_COMPACT_VIEW_SCHEMA
from stpd.fullrun.public_inputs import COMPACT_IDENTITY
from stpd.fullrun.public_m2_input_storage import (
    public_m2_source_binding_digest,
    read_public_m2_input,
)
from stpd.fullrun.public_m2_sequences import (
    EDGE_PROFILE,
    PublicM2Chain,
    PublicM2EvidenceRow,
    PublicM2Input,
)
from stpd.models.light_action_m2_training_data import LightActionM2TrainingStep
from stpd.models.token_core import ScratchShape
from stpd.workers.public_m2_engine import (
    PublicM2Engine,
    PublicM2EngineConfig,
    load_public_m2_weights,
)
from stpd.workers.public_m2_run import (
    CHECKPOINT_SCHEMA,
    EVALUATION_SCHEMA,
    MODEL_SCHEMA,
    STAGE_SCHEMA,
    execute_public_m2_run,
    prepare_public_m2_run,
)


@pytest.fixture(autouse=True)
def one_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def _chain(name: str, split: str, count: int) -> PublicM2Chain:
    evidence = tuple(PublicM2EvidenceRow(
        semantic_hash([name, position]), semantic_hash(["archive", name]),
        "synthetic-session", name, position + 1,
        semantic_hash(["frame", name, position]),
        semantic_hash(["frame", name, position + 1]),
        semantic_hash(["commit", name, position]),
        semantic_hash(["proof", name, position]),
        semantic_hash(["segment", name]),
        semantic_hash(["synthetic-public-sample", name, position]),
    ) for position in range(count))
    steps = tuple(LightActionM2TrainingStep(
        position, (1, 2), ("b", "a"),
        ((256, 65, 257), (256, 66, 257)), "a", None, position == 0,
    ) for position in range(count))
    chain_id = semantic_hash({
        "profile": EDGE_PROFILE, "split": split,
        "evidence": [asdict(row) for row in evidence],
    })
    return PublicM2Chain(chain_id, split, evidence, steps)


def _fixture(tmp_path):
    blobs = LocalBlobStore(tmp_path)
    store = ManifestArtifactStore(blobs)
    reporter = ObjectStoreRunReporter(store, blobs)
    producer = Producer("synthetic/test", "a" * 40, "b" * 64)
    dataset = Manifest("dataset", producer)
    store.publish(dataset)
    allocation = Manifest(
        "protocol", producer, (Parent("dataset", dataset.artifact_id),),
        parameters=FrozenObject.of({"schema": ALLOCATION_SCHEMA}),
    )
    store.publish(allocation)
    view = Manifest(
        "model_view", producer,
        (Parent("allocation", allocation.artifact_id), Parent("dataset", dataset.artifact_id)),
        parameters=FrozenObject.of({
            "schema": PUBLIC_COMPACT_VIEW_SCHEMA, "serializer": COMPACT_IDENTITY,
        }),
    )
    store.publish(view)
    tokenizer = Tokenizer(models.WordLevel({f"token{i}": i for i in range(258)},
                                           unk_token="token0")).to_str().encode()
    train, dev = _chain("train", "train", 9), _chain("dev", "dev", 2)
    value = PublicM2Input(
        public_m2_source_binding_digest(view.artifact_id, allocation.artifact_id),
        (train.chain_id,), tuple(row.transition_id for row in train.evidence),
        tokenizer, (train, dev), 16, 8,
    )
    config = PublicM2EngineConfig(
        source_digest=value.identity,
        state_tokenizer_sha256=hashlib.sha256(tokenizer).hexdigest(),
        shape=ScratchShape(258, 12, 1, 2, 24, 0.0, 16),
        max_action_bytes=8, max_actions_per_step=2, max_chain_steps=16,
        max_total_steps=32, max_total_input_tokens=512,
        learning_rate=0.0003, weight_decay=0.0, gradient_clip=1.0,
        seed=1701, epochs=5,
    )
    return store, reporter, producer, view, allocation, value, config


def _prepare(bundle):
    store, _, producer, view, allocation, value, config = bundle
    return prepare_public_m2_run(
        store, value, config, producer,
        source_view_id=view.artifact_id, allocation_id=allocation.artifact_id,
        operation_id="c" * 32,
    )


def test_d6_prepared_manifest_and_initial_event_wire_golden(tmp_path):
    """Freeze exact M2 wire identities before adding another model run flavor."""
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, _, _ = bundle
    run = _prepare(bundle)
    expected = {
        "training_input": (
            "89d6eff54a6479bd7b8d38d2d5f581369e89b4f5181579f56b66fd90575d92ff",
            "5684d19bbb122d33ba8326663287f558df4a90ea7eb950a24ce8a96b74b3542a",
        ),
        "experiment": (
            "2a0acf87a4e7dfed3b885406d568183f23b817e4df4f08bc7b16a04173274ea2",
            "e4c5275c1a67fc8dcb5456f29df4145b7efee6faa1c158faa0342bba57f6e944",
        ),
        "run": (
            "cb8868134ff51344cd561339fac1532c191706baae5720dc15c6bc36189339ef",
            "37c79e29a472907a7c4b2b8f264ce0f6c48958566e609872d8ee71e18c8a04ff",
        ),
    }
    actual = {
        item.kind: (item.artifact_id, hashlib.sha256(item.to_bytes()).hexdigest())
        for identity in store.manifest_ids()
        if (item := store.get_manifest(identity)).kind in expected
    }
    assert actual == expected
    with patch.object(run_module.uuid, "uuid4", return_value=uuid.UUID(
        "01234567-89ab-cdef-0123-456789abcdef"
    )):
        execute_public_m2_run(
            store, reporter, run.artifact_id, producer, stop_after_windows=1,
        )
    expected_events = {
        "loading": (
            "ddc3abee2026a07b6ad4373c33931ff29077a7d728aa31000218a25b6d207ff9",
            "0f0f4080de6fa2e89ce17854b1a3f010c4003bbf8a3524c03984e315542f2a90",
        ),
        "started": (
            "97a126578475deb78804236ec16fb94b5d162083714de06789574ec94dfab03a",
            "4d0b1e54e37b1eefbd3410551c6821e36e44e397b11811a55b5c0ca0860596b0",
        ),
    }
    events = {
        item.parameters.value()["kind"]:
            (item.artifact_id, hashlib.sha256(item.to_bytes()).hexdigest())
        for item in reporter.events(run.artifact_id)
        if item.parameters.value()["kind"] in expected_events
    }
    assert events == expected_events


def test_canonical_wire_round_trip_and_rejects_edge_fit_and_action_corruption(tmp_path):
    bundle = _fixture(tmp_path)
    value = bundle[-2]
    assert read_public_m2_input(value.payload_bytes(), value.state_tokenizer) == value
    raw = value.content()
    raw["chains"][0]["evidence"][1]["pre_frame_sha256"] = "f" * 64
    raw["identity"] = semantic_hash({key: item for key, item in raw.items() if key != "identity"})
    with pytest.raises(BoundaryError):
        read_public_m2_input(json_bytes(raw), value.state_tokenizer)
    raw = value.content()
    raw["codec_fit_transition_ids"] = raw["codec_fit_transition_ids"][:-1]
    raw["identity"] = semantic_hash({key: item for key, item in raw.items() if key != "identity"})
    with pytest.raises(BoundaryError, match="codec_fit_membership"):
        read_public_m2_input(json_bytes(raw), value.state_tokenizer)
    raw = value.content()
    raw["chains"][0]["steps"][0]["byte_actions"] = [[256, 999, 257], [256, 66, 257]]
    raw["identity"] = semantic_hash({key: item for key, item in raw.items() if key != "identity"})
    with pytest.raises(BoundaryError):
        read_public_m2_input(json_bytes(raw), value.state_tokenizer)


def test_prepare_rejects_wrong_source_or_config_without_publishing_run(tmp_path):
    bundle = _fixture(tmp_path)
    store, _, producer, view, allocation, value, config = bundle
    before = store.manifest_ids()
    with pytest.raises(BoundaryError, match="source_binding_mismatch"):
        prepare_public_m2_run(
            store, replace(value, source_binding_digest="d" * 64), config, producer,
            source_view_id=view.artifact_id, allocation_id=allocation.artifact_id,
            operation_id="c" * 32,
        )
    with pytest.raises(BoundaryError, match="input_config_mismatch"):
        prepare_public_m2_run(
            store, value, replace(config, source_digest="d" * 64), producer,
            source_view_id=view.artifact_id, allocation_id=allocation.artifact_id,
            operation_id="c" * 32,
        )
    assert store.manifest_ids() == before


def test_five_epoch_run_pause_resume_and_stage_contract(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, value, config = bundle
    run = _prepare(bundle)
    first = execute_public_m2_run(
        store, reporter, run.artifact_id, producer, stop_after_windows=1
    )
    assert first.state == "paused" and first.run_id == run.artifact_id
    assert reporter.completed(run.artifact_id) is None
    checkpoint = store.get_manifest(first.checkpoint_id)
    assert checkpoint.parameters.value()["schema"] == CHECKPOINT_SCHEMA
    assert checkpoint.parameters.value()["window_cursor"] == 8
    with pytest.raises(BoundaryError, match="explicit_resume"):
        execute_public_m2_run(store, reporter, run.artifact_id, producer)
    finished = execute_public_m2_run(
        store, reporter, run.artifact_id, producer, resume=first.checkpoint_id
    )
    assert finished.state == "completed" and finished.run_id == run.artifact_id
    assert reporter.completed(run.artifact_id).artifact_id == finished.result_id
    stages = [item for identity in store.manifest_ids()
              if (item := store.get_manifest(identity)).kind == "analysis"
              and item.parameters.value().get("schema") == STAGE_SCHEMA]
    assert sorted(item.parameters.value()["epoch"] for item in stages) == [1, 3, 5]
    assert [
        item.parameters.value()["run_complete"]
        for item in sorted(stages, key=lambda item: item.parameters.value()["epoch"])
    ] == [False, False, True]
    for stage in stages:
        model = store.get_manifest(stage.parent("model"))
        evaluation = store.get_manifest(stage.parent("offline_evaluation"))
        assert model.parameters.value()["schema"] == MODEL_SCHEMA
        assert evaluation.parameters.value()["schema"] == EVALUATION_SCHEMA
        assert evaluation.parameters.value()["label_count"] == 2
        assert model.payload("state_tokenizer").sha256 == hashlib.sha256(
            value.state_tokenizer
        ).hexdigest()
        epoch = stage.parameters.value()["epoch"]
        loaded = load_public_m2_weights(
            b"".join(store.read_payload(model.payload("weights"))), config,
            input_digest=run.parameters.value()["engine_input_digest"],
            completed_epochs=epoch, inference_device="cpu",
        )
        assert not loaded.training
    repeated = execute_public_m2_run(store, reporter, run.artifact_id, producer)
    assert repeated.result_id == finished.result_id


def test_explicit_window_pause_continuation_matches_uninterrupted_checkpoint(tmp_path):
    first_bundle = _fixture(tmp_path / "first")
    second_bundle = _fixture(tmp_path / "second")
    first_run = _prepare(first_bundle)
    second_run = _prepare(second_bundle)
    first = execute_public_m2_run(
        first_bundle[0], first_bundle[1], first_run.artifact_id, first_bundle[2],
    )
    paused = execute_public_m2_run(
        second_bundle[0], second_bundle[1], second_run.artifact_id, second_bundle[2],
        stop_after_windows=1,
    )
    resumed = execute_public_m2_run(
        second_bundle[0], second_bundle[1], second_run.artifact_id, second_bundle[2],
        resume=paused.checkpoint_id,
    )
    assert first.run_id == resumed.run_id
    assert first.checkpoint_id == resumed.checkpoint_id
    assert first.result_id == resumed.result_id


def test_epoch_checkpoint_resume_is_idempotent_and_old_checkpoint_is_rejected(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, _, _ = bundle
    run = _prepare(bundle)
    first = execute_public_m2_run(
        store, reporter, run.artifact_id, producer, stop_after_windows=1
    )
    second = execute_public_m2_run(
        store, reporter, run.artifact_id, producer,
        resume=first.checkpoint_id, stop_after_windows=1,
    )
    assert store.get_manifest(second.checkpoint_id).parameters.value()["completed_epochs"] == 1
    with pytest.raises(BoundaryError, match="latest_reported_checkpoint"):
        execute_public_m2_run(
            store, reporter, run.artifact_id, producer, resume=first.checkpoint_id
        )
    assert reporter.completed(run.artifact_id) is None
    finished = execute_public_m2_run(
        store, reporter, run.artifact_id, producer, resume=second.checkpoint_id
    )
    assert finished.state == "completed"
    stages = [item for identity in store.manifest_ids()
              if (item := store.get_manifest(identity)).kind == "analysis"
              and item.parameters.value().get("schema") == STAGE_SCHEMA]
    assert sorted(item.parameters.value()["epoch"] for item in stages) == [1, 3, 5]


def test_source_view_requires_public_compact_and_exact_allocation_parent(tmp_path):
    bundle = _fixture(tmp_path)
    store, _, producer, view, allocation, value, config = bundle
    wrong = Manifest(
        "model_view", producer, view.parents,
        parameters=FrozenObject.of({
            "schema": PUBLIC_COMPACT_VIEW_SCHEMA,
            "serializer": {"profile": "public_lite"},
        }),
    )
    store.publish(wrong)
    rebound = replace(
        value, source_binding_digest=public_m2_source_binding_digest(
            wrong.artifact_id, allocation.artifact_id
        ),
    )
    with pytest.raises(BoundaryError, match="source_lineage_mismatch"):
        prepare_public_m2_run(
            store, rebound, replace(config, source_digest=rebound.identity), producer,
            source_view_id=wrong.artifact_id, allocation_id=allocation.artifact_id,
            operation_id="c" * 32,
        )


def test_prepared_operation_is_stable_and_cannot_be_reused_for_new_config(tmp_path):
    bundle = _fixture(tmp_path)
    first = _prepare(bundle)
    assert _prepare(bundle).artifact_id == first.artifact_id
    store, _, producer, view, allocation, value, config = bundle
    with pytest.raises(BoundaryError, match="operation_id_collision"):
        prepare_public_m2_run(
            store, value, replace(config, learning_rate=0.0002), producer,
            source_view_id=view.artifact_id, allocation_id=allocation.artifact_id,
            operation_id="c" * 32,
        )


def test_runtime_producer_pin_fails_before_an_attempt_event(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, _, _ = bundle
    run = _prepare(bundle)
    changed = replace(producer, source_revision="d" * 40)
    with pytest.raises(BoundaryError, match="run_identity_mismatch"):
        execute_public_m2_run(store, reporter, run.artifact_id, changed)
    assert reporter.events(run.artifact_id) == ()


@pytest.mark.parametrize("pause_windows,epoch", [(3, 1), (7, 3)])
def test_resume_inside_epoch_two_or_four_does_not_republish_prior_stage(
    tmp_path, pause_windows, epoch,
):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, _, _ = bundle
    run = _prepare(bundle)
    paused = execute_public_m2_run(
        store, reporter, run.artifact_id, producer,
        stop_after_windows=pause_windows,
    )
    checkpoint = store.get_manifest(paused.checkpoint_id).parameters.value()
    assert checkpoint["completed_epochs"] == epoch
    assert checkpoint["window_cursor"] == 8
    result = execute_public_m2_run(
        store, reporter, run.artifact_id, producer, resume=paused.checkpoint_id,
    )
    assert result.state == "completed"


def test_completed_read_never_reevaluates_dev_or_loads_inference_model(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, _, _ = bundle
    run = _prepare(bundle)
    original = PublicM2Engine.evaluate_dev
    calls = 0

    def once(self):
        nonlocal calls
        calls += 1
        if calls > 3:
            raise AssertionError("completed verification recomputed dev")
        return original(self)

    with patch.object(PublicM2Engine, "evaluate_dev", once):
        completed = execute_public_m2_run(store, reporter, run.artifact_id, producer)
        repeated = execute_public_m2_run(store, reporter, run.artifact_id, producer)
    assert completed.result_id == repeated.result_id
    assert calls == 3  # only epoch 1/3/5 readouts


def test_terminal_verification_precedes_singleton_completion(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, _, _ = bundle
    run = _prepare(bundle)
    with (
        patch.object(run_module, "_verify_completed",
                     side_effect=BoundaryError("fixture", "verification_failed")),
        pytest.raises(BoundaryError),
    ):
        execute_public_m2_run(store, reporter, run.artifact_id, producer)
    assert reporter.completed(run.artifact_id) is None
