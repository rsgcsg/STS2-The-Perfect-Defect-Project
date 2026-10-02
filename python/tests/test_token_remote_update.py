"""Synthetic, owner-admitted train-only remote M0 update closure."""

from __future__ import annotations

from dataclasses import replace

import pytest

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from stpd.fullrun.light_action_inputs import load_light_action_inputs
from stpd.workers.token_ranking import LightActionM0Config, TokenRankingEngine
from stpd.workers.token_remote_update import (
    TokenRemoteUpdateRequest,
    TokenRemoteUpdateResult,
    execute_token_remote_update,
    prepare_token_remote_update,
    validate_token_remote_update,
)
from stpd.workers.token_worker import execute_tokens, prepare_token_run


def _canonical_run(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    pytest.importorskip("tokenizers")
    pytest.importorskip("jwt")
    from test_artifact_store_v1 import PRODUCER
    from test_light_action_m0_canonical_cli import _cli, _synthetic_workspace

    config_path, store_dir, dataset_id, owner = _synthetic_workspace(tmp_path)
    operation_id = "d" * 32
    monkeypatch.setattr("spireagent.research_cli.source_identity", lambda _root: PRODUCER)
    prepared = _cli(
        monkeypatch,
        "--store", str(store_dir), "prepare-light-action-m0",
        "--project-config", str(config_path), "--dataset", dataset_id,
        "--operation", operation_id, "--backbone", "s",
        "--train-limit", "8", "--dev-limit", "4",
    )
    store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
    input_manifest = store.get_manifest(prepared["training_input_id"])
    inputs = load_light_action_inputs(store, input_manifest.artifact_id)
    config = LightActionM0Config(
        recipe="stage1a.dsimple.light-action.m0.s.v1", steps=3,
        max_state_tokens=8192, max_action_bytes=8192,
    )
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(2)
    run = prepare_token_run(store, inputs, config, PRODUCER)
    return torch, previous_threads, store, owner, operation_id, PRODUCER, inputs, config, run


def test_remote_update_request_transports_train_only_and_validates_exact_checkpoint(
    tmp_path, monkeypatch,
):
    case = _canonical_run(tmp_path, monkeypatch)
    torch, previous_threads, store, owner, operation, producer, inputs, config, run = case
    try:
        request = prepare_token_remote_update(
            store, owner, run.artifact_id, producer, operation, 2,
            attempt_id="a" * 32,
        )
        dev_ids = {sample.transition_id for sample in inputs.samples if sample.split == "dev"}
        train_ids = {sample.transition_id for sample in inputs.samples if sample.split == "train"}
        assert {item.sample.transition_id for item in request.train_rows} == train_ids
        assert all(item.sample.split == "train" for item in request.train_rows)
        assert not dev_ids.intersection(item.sample.transition_id for item in request.train_rows)
        assert [item.source_index for item in request.train_rows] == [
            index for index, sample in enumerate(inputs.samples) if sample.split == "train"
        ]

        wire = request.to_bytes()
        assert all(identity.encode("utf-8") not in wire for identity in dev_ids)
        decoded_request = TokenRemoteUpdateRequest.from_bytes(wire)
        assert decoded_request == request

        before = store.manifest_ids()
        result = execute_token_remote_update(decoded_request)
        result = TokenRemoteUpdateResult.from_bytes(result.to_bytes())
        assert store.manifest_ids() == before
        expected = TokenRankingEngine(inputs, config)
        expected.advance()
        expected.advance()
        assert result.checkpoint == expected.checkpoint()
        assert result.checkpoint_step == 2

        assert validate_token_remote_update(
            store, owner, request, result, producer, operation,
        ) == result
        assert store.manifest_ids() == before

        with pytest.raises(BoundaryError, match="training_binding_mismatch"):
            prepare_token_remote_update(
                store, owner, run.artifact_id, producer, "e" * 32, 2,
            )
        with pytest.raises(BoundaryError, match="target_config_mismatch"):
            replace(request, target_device="cuda")

        bad_attempt = replace(result, attempt_id="b" * 32)
        with pytest.raises(BoundaryError, match="result_request_binding_mismatch"):
            validate_token_remote_update(
                store, owner, request, bad_attempt, producer, operation,
            )
        bad_input = replace(result, input_id="0" * 64)
        with pytest.raises(BoundaryError, match="result_request_binding_mismatch"):
            validate_token_remote_update(
                store, owner, request, bad_input, producer, operation,
            )
        other_producer = Producer(producer.repository, "1" * 40, producer.uv_lock_sha256)
        bad_producer = replace(result, producer=other_producer)
        with pytest.raises(BoundaryError, match="result_request_binding_mismatch"):
            validate_token_remote_update(
                store, owner, request, bad_producer, producer, operation,
            )
        bad_config = replace(result, config=replace(config, steps=4))
        with pytest.raises(BoundaryError, match="result_request_binding_mismatch"):
            validate_token_remote_update(
                store, owner, request, bad_config, producer, operation,
            )
        with pytest.raises(BoundaryError, match="result_identity_mismatch"):
            bad_target = replace(result, target_device="cuda")
            validate_token_remote_update(store, owner, request, bad_target, producer, operation)

        with pytest.raises(BoundaryError, match="train_row_count_limit"):
            replace(request, train_rows=request.train_rows[:-1])
    finally:
        torch.set_num_threads(previous_threads)


def test_remote_update_resume_uses_exact_existing_checkpoint_and_rejects_tampering(
    tmp_path, monkeypatch,
):
    case = _canonical_run(tmp_path, monkeypatch)
    torch, previous_threads, store, owner, operation, producer, inputs, config, run = case
    try:
        paused = execute_tokens(
            store, ObjectStoreRunReporter(store, store.blobs), run.artifact_id, producer,
            stop_after=1,
        )
        assert paused.state == "paused" and paused.checkpoint_id
        checkpoint = store.get_manifest(paused.checkpoint_id)
        checkpoint_bytes = b"".join(store.read_payload(checkpoint.payload("checkpoint")))
        request = prepare_token_remote_update(
            store, owner, run.artifact_id, producer, operation, 3,
            resume_checkpoint_id=paused.checkpoint_id, attempt_id="c" * 32,
        )
        assert request.resume_manifest == checkpoint
        assert request.resume_checkpoint == checkpoint_bytes

        resumed = execute_token_remote_update(TokenRemoteUpdateRequest.from_bytes(
            request.to_bytes(),
        ))
        expected = TokenRankingEngine(inputs, config)
        expected.restore(checkpoint_bytes)
        expected.advance()
        expected.advance()
        assert resumed.checkpoint == expected.checkpoint()
        assert validate_token_remote_update(
            store, owner, request, resumed, producer, operation,
        ) == resumed

        with pytest.raises(BoundaryError, match="resume_manifest_identity_mismatch"):
            replace(request, resume_checkpoint=request.resume_checkpoint[:-1] + b"x")

        wrong_run = replace(resumed, run_id="0" * 64)
        with pytest.raises(BoundaryError, match="result_request_binding_mismatch"):
            validate_token_remote_update(
                store, owner, request, wrong_run, producer, operation,
            )
        assert not any(
            store.get_manifest(identity).kind in {"offline_evaluation", "model", "run_result"}
            for identity in store.manifest_ids()
        )
    finally:
        torch.set_num_threads(previous_threads)
