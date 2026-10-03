"""Synthetic A01 receipts matched to one real typed A02 run manifest."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
import torch
from test_public_m2_run import _fixture, _prepare

from spireagent.json_boundary import BoundaryError
from stpd.models.token_core import ScratchShape
from stpd.workers.public_m0_matched_engine import (
    PublicM0MatchedConfig,
    PublicM0MatchedEngine,
    load_public_m0_matched_weights,
)
from stpd.workers.public_m0_matched_run import (
    _M0_FLAVOR,
    MODEL_KIND,
    RUN_SCHEMA,
    execute_public_m0_matched_run,
    prepare_public_m0_matched_run,
)
from stpd.workers.public_m2_run import (
    _execute_run,
    _load_run,
    _prepare_run,
    _restore,
    prepare_public_m2_run,
)


@pytest.fixture(autouse=True)
def one_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def _config(value, m2):
    return PublicM0MatchedConfig(
        source_digest=value.identity,
        state_tokenizer_sha256=hashlib.sha256(value.state_tokenizer).hexdigest(),
        vocab_size=m2.shape.vocab_size, max_state_tokens=value.max_state_tokens,
        max_action_bytes=value.max_action_bytes, train_windows_per_epoch=2,
        max_chain_steps=m2.max_chain_steps, max_total_steps=m2.max_total_steps,
        max_total_input_tokens=m2.max_total_input_tokens,
        max_actions_per_step=m2.max_actions_per_step,
        seed=m2.seed, learning_rate=m2.learning_rate,
        weight_decay=m2.weight_decay, gradient_clip=m2.gradient_clip,
        epochs=m2.epochs, window_steps=m2.window_steps,
        max_window_tokens=m2.max_window_tokens, shape_override=m2.shape,
    )


def _prepare_fixture(bundle, matched_id, config=None):
    store, _, producer, view, allocation, value, m2 = bundle
    return _prepare_run(
        store, value, _config(value, m2) if config is None else config, producer,
        source_view_id=view.artifact_id, allocation_id=allocation.artifact_id,
        operation_id="d" * 32, flavor=_M0_FLAVOR, reference_run_id=matched_id,
    )


def test_a01_five_epoch_pause_resume_stages_and_source_free_load(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, value, m2 = bundle
    matched = _prepare(bundle)
    run = _prepare_fixture(bundle, matched.artifact_id)
    assert run.parameters.value()["schema"] == RUN_SCHEMA
    assert run.parameters.value()["model_kind"] == MODEL_KIND
    assert run.parent("matched_m2_run") == matched.artifact_id
    paused = _execute_run(
        store, reporter, run.artifact_id, producer,
        stop_after_windows=1, flavor=_M0_FLAVOR,
    )
    assert paused.state == "paused"
    finished = _execute_run(
        store, reporter, run.artifact_id, producer,
        resume=paused.checkpoint_id, flavor=_M0_FLAVOR,
    )
    assert finished.state == "completed"
    result = store.get_manifest(finished.result_id)
    assert result.parameters.value()["model_kind"] == MODEL_KIND
    stages = [store.get_manifest(identity) for identity in store.manifest_ids()
              if store.get_manifest(identity).parameters.value().get("schema")
              == _M0_FLAVOR.stage_schema]
    assert sorted(item.parameters.value()["epoch"] for item in stages) == [1, 3, 5]
    for stage in stages:
        epoch = stage.parameters.value()["epoch"]
        model = store.get_manifest(stage.parent("model"))
        evaluation = store.get_manifest(stage.parent("offline_evaluation"))
        assert model.parameters.value()["model_kind"] == MODEL_KIND
        assert evaluation.parameters.value()["label_count"] == 2
        raw = b"".join(store.read_payload(model.payload("weights")))
        loaded = load_public_m0_matched_weights(
            raw, value.state_tokenizer, _config(value, m2),
            source_digest=value.identity, completed_epochs=epoch,
        )
        assert not loaded.training
        assert model.parameters.value()["run_complete"] is (epoch == 5)
    again = _execute_run(store, reporter, run.artifact_id, producer, flavor=_M0_FLAVOR)
    assert again.result_id == finished.result_id


def test_formal_prepare_rejects_synthetic_shape_and_mismatched_reference(tmp_path):
    bundle = _fixture(tmp_path)
    store, _, producer, view, allocation, value, m2 = bundle
    matched = _prepare(bundle)
    tiny = _config(value, m2)
    before = store.manifest_ids()
    with pytest.raises(BoundaryError, match="standard_shape_required"):
        prepare_public_m0_matched_run(
            store, value, tiny, producer, source_view_id=view.artifact_id,
            allocation_id=allocation.artifact_id,
            matched_m2_run_id=matched.artifact_id, operation_id="d" * 32,
        )
    with pytest.raises(BoundaryError, match="matched_m2_run_mismatch"):
        _prepare_fixture(bundle, matched.artifact_id, replace(tiny, seed=tiny.seed + 1))
    assert store.manifest_ids() == before
    run = _prepare_fixture(bundle, matched.artifact_id)
    with pytest.raises(BoundaryError, match="standard_shape_required"):
        execute_public_m0_matched_run(store, bundle[1], run.artifact_id, producer)


def test_formal_a01_prepare_requires_matching_standard_a02_run(tmp_path):
    bundle = _fixture(tmp_path)
    store, _, producer, view, allocation, value, m2 = bundle
    standard_m2 = replace(
        m2, shape=ScratchShape(258, 384, 2, 6, 1536, 0.1, value.max_state_tokens),
    )
    matched = prepare_public_m2_run(
        store, value, standard_m2, producer,
        source_view_id=view.artifact_id, allocation_id=allocation.artifact_id,
        operation_id="e" * 32,
    )
    standard_m0 = replace(_config(value, standard_m2), shape_override=None)
    a01 = prepare_public_m0_matched_run(
        store, value, standard_m0, producer,
        source_view_id=view.artifact_id, allocation_id=allocation.artifact_id,
        matched_m2_run_id=matched.artifact_id, operation_id="f" * 32,
    )
    assert a01.parent("matched_m2_run") == matched.artifact_id
    assert a01.parameters.value()["config"]["shape_override"] is None


def test_cross_model_checkpoint_rejected_before_mutation(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, value, m2 = bundle
    matched = _prepare(bundle)
    a01 = _prepare_fixture(bundle, matched.artifact_id)
    m2_pause = _execute_run(
        store, reporter, matched.artifact_id, producer, stop_after_windows=1,
    )
    a01_run, a01_training, _, _, a01_engine = _load_run(
        store, a01.artifact_id, producer, flavor=_M0_FLAVOR,
    )
    assert isinstance(a01_engine, PublicM0MatchedEngine)
    with pytest.raises(BoundaryError, match="checkpoint_lineage_mismatch"):
        _restore(
            store, m2_pause.checkpoint_id, a01_run, a01_training, a01_engine,
            flavor=_M0_FLAVOR,
        )
    assert a01_engine.optimizer_updates == 0
    a01_pause = _execute_run(
        store, reporter, a01.artifact_id, producer,
        stop_after_windows=1, flavor=_M0_FLAVOR,
    )
    m2_run, m2_training, _, _, m2_engine = _load_run(
        store, matched.artifact_id, producer,
    )
    with pytest.raises(BoundaryError, match="checkpoint_lineage_mismatch"):
        _restore(
            store, a01_pause.checkpoint_id, m2_run, m2_training, m2_engine,
        )
    assert m2_engine.optimizer_updates == 0
