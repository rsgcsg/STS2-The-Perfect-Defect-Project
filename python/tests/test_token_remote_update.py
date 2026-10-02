"""Synthetic, owner-admitted train-only remote M0 update closure."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import replace

import pytest

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from stpd.fullrun.light_action_inputs import load_light_action_inputs
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from stpd.workers.token_ranking import (
    CUDA_STEP_RNG_PROTOCOL,
    LightActionM0Config,
    TokenRankingEngine,
    TokenTargetRuntime,
    config_payload,
    token_training_identity,
    validate_light_action_m0_scratch_checkpoint,
)
from stpd.workers.token_remote_update import (
    TokenRemoteUpdateRequest,
    TokenRemoteUpdateResult,
    execute_token_remote_update,
    prepare_token_remote_update,
    validate_token_remote_update,
)
from stpd.workers.token_worker import (
    execute_tokens,
    preflight_token_run,
    preflight_token_run_contract,
    prepare_token_run,
)


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


def test_cpu_can_prepare_and_structurally_validate_cuda_target_checkpoint(
    tmp_path, monkeypatch,
):
    case = _canonical_run(tmp_path, monkeypatch)
    torch, previous_threads, store, owner, operation, producer, inputs, config, local_run = case
    try:
        assert not torch.cuda.is_available(), "this regression requires a CPU-only host"
        local_runtime = TokenTargetRuntime.current()
        assert local_run.parameters.value()["torch_version"] == local_runtime.torch_version
        assert local_run.parameters.value()["cpu_threads"] == local_runtime.cpu_threads
        preflight_token_run(store, local_run.artifact_id, producer)

        # The Mac host is torch 2.13.0; the identified worker image is
        # torch 2.13.0+cu130. The thread-count difference below is synthetic
        # because the image's configured CPU thread count is not part of its
        # supplied metadata.
        monkeypatch.setattr(torch, "__version__", "2.13.0")
        local_runtime = TokenTargetRuntime.current()
        local_run = prepare_token_run(
            store, inputs, config, producer, replicate="local-cpu-runtime",
        )
        assert local_run.parameters.value()["torch_version"] == "2.13.0"
        preflight_token_run(store, local_run.artifact_id, producer)
        target_runtime = TokenTargetRuntime(
            "2.13.0+cu130", local_runtime.cpu_threads + 1,
        )
        assert target_runtime.torch_version != local_runtime.torch_version
        assert target_runtime.cpu_threads != local_runtime.cpu_threads
        cuda_config = replace(config, device="cuda")
        cuda_run = prepare_token_run(
            store, inputs, cuda_config, producer, target_runtime=target_runtime,
        )
        assert preflight_token_run_contract(
            store, cuda_run.artifact_id, producer,
        )[0].artifact_id == cuda_run.artifact_id
        with pytest.raises(BoundaryError, match="source_or_contract_mismatch"):
            preflight_token_run(store, cuda_run.artifact_id, producer)

        # This is only a synthetic typed-checkpoint fixture: CPU-generated tensors are
        # tagged with the CUDA request identity; it is not evidence of CUDA execution.
        cpu_engine = TokenRankingEngine(inputs, config)
        cpu_engine.advance()
        cpu_engine.advance()
        state = decode_checkpoint(cpu_engine.checkpoint())
        mismatched_resume = copy.deepcopy(state)
        mismatched_resume["torch_version"] = target_runtime.torch_version
        mismatched_resume["cpu_threads"] = target_runtime.cpu_threads
        with pytest.raises(BoundaryError, match="resume_identity_mismatch"):
            TokenRankingEngine(inputs, config).restore(encode_checkpoint(mismatched_resume))
        monkeypatch.setattr(
            torch.cuda, "is_available",
            lambda: pytest.fail("prepare/validate must not probe or initialize CUDA"),
        )
        request = prepare_token_remote_update(
            store, owner, cuda_run.artifact_id, producer, operation, 2,
            attempt_id="f" * 32,
        )
        assert request.config.device == request.target_device == "cuda"
        assert request.target_runtime == target_runtime
        decoded_request = TokenRemoteUpdateRequest.from_bytes(request.to_bytes())
        assert decoded_request.target_runtime == target_runtime
        assert request.backbone_identity.value() == cpu_engine.backbone
        _, expected_identity = token_training_identity(
            inputs, cuda_config, request.backbone_identity.value(),
        )
        state["config"] = config_payload(cuda_config)
        state["data_identity"] = expected_identity
        state["rng_protocol"] = CUDA_STEP_RNG_PROTOCOL
        state["torch_version"] = target_runtime.torch_version
        state["cpu_threads"] = target_runtime.cpu_threads
        checkpoint = encode_checkpoint(state)

        result = TokenRemoteUpdateResult(
            request.request_sha256, request.attempt_id, request.run_id,
            request.input_id, request.producer, request.operation_id,
            request.training_binding, request.target_device, request.target_step,
            request.config, request.resume_checkpoint_id, None,
            request.target_step, hashlib.sha256(checkpoint).hexdigest(), checkpoint,
            request.backbone_identity,
        )
        before = store.manifest_ids()
        assert validate_token_remote_update(
            store, owner, request, result, producer, operation,
        ) == result
        assert store.manifest_ids() == before
        with pytest.raises(BoundaryError, match="producer_runtime_mismatch"):
            execute_token_remote_update(request)

        def reject_state(mutator, reason):
            invalid = copy.deepcopy(state)
            mutator(invalid)
            raw = encode_checkpoint(invalid)
            with pytest.raises(BoundaryError, match=reason):
                validate_light_action_m0_scratch_checkpoint(
                    raw, inputs, cuda_config, request.backbone_identity.value(),
                    request.target_runtime,
                )

        reject_state(lambda value: value.update(config=config_payload(config)),
                     "resume_identity_mismatch")
        reject_state(lambda value: value.update(rng_protocol="seed_plus_completed_steps_v1"),
                     "resume_identity_mismatch")
        reject_state(lambda value: value.update(step=cuda_config.steps + 1),
                     "resume_identity_mismatch")
        reject_state(lambda value: value.update(data_identity="0" * 64),
                     "resume_identity_mismatch")
        reject_state(lambda value: value.update(torch_version=local_runtime.torch_version),
                     "resume_identity_mismatch")
        reject_state(lambda value: value.update(cpu_threads=local_runtime.cpu_threads),
                     "resume_identity_mismatch")

        model_name = next(name for name, tensor in state["model"].items()
                          if tensor.numel() > 1)
        reject_state(
            lambda value: value["model"].__setitem__(
                model_name, value["model"][model_name].reshape(-1)[:1],
            ),
            "invalid_weights",
        )
        reject_state(
            lambda value: value["model"].__setitem__(
                model_name, value["model"][model_name].to(dtype=torch.float64),
            ),
            "invalid_weights",
        )
        optimizer_index = next(iter(state["optimizer"]["state"]))
        reject_state(
            lambda value: value["optimizer"]["state"][optimizer_index].pop("exp_avg_sq"),
            "optimizer_state_mismatch",
        )
        reject_state(
            lambda value: value["optimizer"]["state"].pop(optimizer_index),
            "optimizer_state_inventory",
        )
        reject_state(
            lambda value: value["optimizer"]["param_groups"][0].update(lr=0.5),
            "optimizer_config_mismatch",
        )

        nonfinite = copy.deepcopy(state)
        nonfinite["optimizer"]["state"][optimizer_index]["exp_avg"].fill_(float("nan"))
        with pytest.raises(BoundaryError, match="non_finite"):
            encode_checkpoint(nonfinite)
    finally:
        torch.set_num_threads(previous_threads)


def test_cpu_target_runtime_mismatch_is_structurally_accepted_but_not_executable(
    tmp_path, monkeypatch,
):
    case = _canonical_run(tmp_path, monkeypatch)
    torch, previous_threads, store, owner, operation, producer, inputs, config, _ = case
    try:
        # Match the supplied Mac and worker image Torch identities; only the
        # image thread-count difference is synthetic test data.
        monkeypatch.setattr(torch, "__version__", "2.13.0")
        local_runtime = TokenTargetRuntime.current()
        local_run = prepare_token_run(
            store, inputs, config, producer, replicate="cpu-local-runtime",
        )
        assert local_run.parameters.value()["torch_version"] == "2.13.0"
        preflight_token_run(store, local_run.artifact_id, producer)

        target_runtime = TokenTargetRuntime(
            "2.13.0+cu130", local_runtime.cpu_threads + 1,
        )
        target_run = prepare_token_run(
            store, inputs, config, producer, replicate="cpu-remote-target",
            target_runtime=target_runtime,
        )
        assert preflight_token_run_contract(
            store, target_run.artifact_id, producer,
        )[0].artifact_id == target_run.artifact_id
        with pytest.raises(BoundaryError, match="source_or_contract_mismatch"):
            preflight_token_run(store, target_run.artifact_id, producer)

        request = prepare_token_remote_update(
            store, owner, target_run.artifact_id, producer, operation, 2,
            attempt_id="9" * 32,
        )
        assert request.config.device == request.target_device == "cpu"
        assert request.target_runtime == target_runtime
        engine = TokenRankingEngine(inputs, config)
        engine.advance()
        engine.advance()
        state = decode_checkpoint(engine.checkpoint())
        state["torch_version"] = target_runtime.torch_version
        state["cpu_threads"] = target_runtime.cpu_threads
        checkpoint = encode_checkpoint(state)

        with pytest.raises(BoundaryError, match="resume_identity_mismatch"):
            TokenRankingEngine(inputs, config).restore(checkpoint)
        result = TokenRemoteUpdateResult(
            request.request_sha256, request.attempt_id, request.run_id,
            request.input_id, request.producer, request.operation_id,
            request.training_binding, request.target_device, request.target_step,
            request.config, request.resume_checkpoint_id, None,
            request.target_step, hashlib.sha256(checkpoint).hexdigest(), checkpoint,
            request.backbone_identity,
        )
        assert validate_token_remote_update(
            store, owner, request, result, producer, operation,
        ) == result
        with pytest.raises(BoundaryError, match="producer_runtime_mismatch"):
            execute_token_remote_update(request)
    finally:
        torch.set_num_threads(previous_threads)
