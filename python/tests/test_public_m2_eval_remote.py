"""Synthetic provider-neutral evaluation request/result wire checks."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from types import SimpleNamespace

import pytest
import torch
from test_public_m2_run import _chain

import stpd.cloud_jobs.public_m2_eval_worker as worker
import stpd.workers.public_m2_evaluation as evaluation
from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.canonical import semantic_hash
from stpd.cloud_jobs.public_m2_modal import PublicM2ModalResources
from stpd.fullrun.public_m2_sequences import PublicM2Input
from stpd.models.token_core import ScratchShape
from stpd.workers import public_m2_eval_remote as remote
from stpd.workers.public_m2_engine import (
    PublicM2Engine,
    PublicM2EngineChain,
    PublicM2EngineConfig,
)
from stpd.workers.public_m2_evaluation import (
    PublicM2EvalChainResult,
    PublicM2EvalExpectedBindings,
    PublicM2EvalShardResult,
    public_m2_engine_input_digest,
)


@pytest.fixture(autouse=True)
def one_torch_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def _source(name: str, dev_rows: tuple[int, ...]) -> PublicM2Input:
    tokenizer = __import__("tokenizers").Tokenizer(
        __import__("tokenizers").models.WordLevel(
            {f"token{i}": i for i in range(258)}, unk_token="token0",
        ),
    ).to_str().encode()
    train = _chain(name + "-train", "train", 1)
    dev = tuple(_chain(name + f"-dev-{index}", "dev", count)
                for index, count in enumerate(dev_rows))
    return PublicM2Input("a" * 64, (train.chain_id,),
                         tuple(row.transition_id for row in train.evidence),
                         tokenizer, (train, *dev), 16, 8)


def _request(*, max_compute_seconds: int = 650):
    pilot = _source("pilot", (13,))
    full = _source("full", (15, 3))
    config = PublicM2EngineConfig(
        source_digest=full.identity,
        state_tokenizer_sha256=hashlib.sha256(full.state_tokenizer).hexdigest(),
        shape=ScratchShape(258, 12, 1, 2, 24, 0.0, 16),
        max_action_bytes=8, max_actions_per_step=2, max_chain_steps=20,
        max_total_steps=32, max_total_input_tokens=512, epochs=5, device="cpu",
    )
    train_chains = tuple(PublicM2EngineChain(chain.chain_id, chain.steps)
                         for chain in full.chains if chain.split == "train")
    dev_chains = tuple(PublicM2EngineChain(chain.chain_id, chain.steps)
                       for chain in full.chains if chain.split == "dev")
    engine = PublicM2Engine(train_chains, dev_chains, config)
    engine.advance_window()
    weights = engine.export_weights()
    engine_digest = public_m2_engine_input_digest(train_chains, dev_chains)
    evaluation_producer = {"repository": "eval", "source_revision": "3" * 40,
                           "uv_lock_sha256": "4" * 64}
    return remote.build_public_m2_eval_request(
        evaluation_operation_id="1" * 32, attempt_id="2" * 32,
        evaluation_producer=evaluation_producer,
        training_producer={"repository": "train", "source_revision": "5" * 40,
                           "uv_lock_sha256": "6" * 64},
        resources={"gpu": "A10", "deadline_seconds": 800},
        model_id="7" * 64, stage_id="8" * 64, checkpoint_id="9" * 64,
        training_config=asdict(config), training_source_digest=full.identity,
        engine_input_digest=engine_digest,
        pilot_input=pilot.payload_bytes(), full_parent_input=full.payload_bytes(),
        tokenizer=full.state_tokenizer, weights=weights,
        pilot_source_id="pilot-source", full_parent_source_id="full-source",
        runtime_requirement=remote.InferenceRuntimeRequirement(
            "A10", "float32", "torch-test", "python-test",
            hashlib.sha256(json_bytes(evaluation_producer)).hexdigest(),
            engine.runtime["implementation_sha256"],
        ),
        chain_range=(0, 2), max_compute_seconds=max_compute_seconds,
    )


def test_eval_request_binary_blobs_deduplicate_and_bind_exact_sources():
    raw = _request()
    value = remote.validate_public_m2_eval_request(raw)
    header, blobs = remote._request_parts(raw)
    assert header["schema"] == remote.REQUEST_SCHEMA
    assert set(header["blob_roles"]) == set(remote._BLOB_ROLES)
    assert len(blobs) == 4
    assert value["validated_selection_identities"] == header["selection_identities"]
    assert len(raw) < sum(len(blob) for blob in blobs.values()) + 64_000


def test_eval_request_rejects_selection_foreign_training_digest_and_bad_blob():
    raw = _request()
    header, blobs = remote._request_parts(raw)
    altered = dict(header)
    altered["engine_input_digest"] = "d" * 64
    foreign = remote._wire._pack(remote._REQUEST_MAGIC,
                                 {key: val for key, val in altered.items()
                                  if key != "blobs" and not key.startswith("_")},
                                 blobs, remote.MAX_REQUEST_BYTES)
    with pytest.raises(BoundaryError, match="selection_identity_mismatch"):
        remote.validate_public_m2_eval_request(foreign)
    corrupt = bytearray(raw)
    corrupt[-1] ^= 1
    with pytest.raises(BoundaryError):
        remote.validate_public_m2_eval_request(bytes(corrupt))


def _runtime():
    return {
        "torch": "torch-test", "python": "python-test", "platform": "test",
        "default_dtype": "torch.float32", "cpu_threads": 1, "device_type": "cuda",
        "gpu_name": "NVIDIA A10", "gpu_compute_capability": [8, 6],
        "gpu_total_memory_bytes": 20_000_000_000, "cuda_version": "test-cuda",
        "cudnn_version": 9000, "tf32_matmul": False, "tf32_cudnn": False,
        "float32_matmul_precision": "highest", "deterministic_algorithms": False,
        "deterministic_warn_only": False, "cudnn_benchmark": False,
        "cudnn_deterministic": False,
    }


class _Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


def _install_fake_session(monkeypatch, clock: _Clock, *, load_seconds=70, pilot_seconds=100,
                          chain_seconds=40):
    calls = {"pilot": 0, "full_ordinals": []}
    runtime = _runtime()

    def factory(weights, config, *, training_input_digest, inference_device, completed_epochs):
        from stpd.workers.checkpoint_codec import decode_checkpoint

        exported = decode_checkpoint(weights)
        bindings = PublicM2EvalExpectedBindings(
            training_input_digest, hashlib.sha256(weights).hexdigest(),
            exported["weights_digest"], completed_epochs, config.epochs,
            exported["run_complete"], exported["implementation_sha256"],
            str(semantic_hash(asdict(config))), inference_device,
        )
        clock.value += load_seconds

        def run(source, selection, *, shard=None):
            is_pilot = tuple(chain.chain_id for chain in source.chains if chain.split == "dev") == (
                _source("pilot", (13,)).chains[1].chain_id,
            )
            if is_pilot:
                calls["pilot"] += 1
                clock.value += pilot_seconds
            else:
                calls["full_ordinals"].extend(range(shard.start_ordinal, shard.stop_ordinal))
                clock.value += chain_seconds
            chains = []
            for ref in selection.chains[shard.start_ordinal:shard.stop_ordinal]:
                correct = 4 if is_pilot else 1
                loss = 1.854619842 * ref.row_count if is_pilot else 2.0 * ref.row_count
                chains.append(PublicM2EvalChainResult(
                    ref.ordinal, ref.chain_id, ref.ordered_evidence_sha256,
                    ref.row_count, loss, correct,
                ))
            return PublicM2EvalShardResult(
                "stpd/public-m2-dev-eval-shard-v1", selection.identity,
                source.identity, training_input_digest,
                hashlib.sha256(weights).hexdigest(), exported["weights_digest"],
                completed_epochs, config.epochs, exported["run_complete"],
                exported["implementation_sha256"], str(semantic_hash(asdict(config))),
                runtime, inference_device, shard,
                tuple(item.chain_id for item in chains), tuple(chains),
                {"model_load": 0.0, "numeric_evaluation": 1.0, "total": 1.0},
                100, 200,
            )

        return SimpleNamespace(bindings=bindings, runtime=runtime, evaluate=run)

    monkeypatch.setattr(evaluation, "open_public_m2_eval_session", factory)
    monkeypatch.setattr(remote, "monotonic", clock)
    return calls


def test_chain_budget_returns_complete_chain_partial_and_strict_decode(monkeypatch):
    request = _request(max_compute_seconds=200)
    clock = _Clock()
    calls = _install_fake_session(monkeypatch, clock)
    response = remote.execute_public_m2_eval_request(
        request, request_sha256=hashlib.sha256(request).hexdigest(),
    )
    result, _ = remote._wire._unpack(response, remote._RESULT_MAGIC, remote.MAX_RESULT_BYTES)
    full = result["full_parent"]
    assert calls == {"pilot": 1, "full_ordinals": [0]}
    assert full["coverage_range"] == [0, 1]
    assert full["next_chain_ordinal"] == 1
    assert full["range_complete"] is False and full["complete"] is False
    assert full["summary"] is None
    parsed = remote.decode_public_m2_eval_result(
        response, request, expected_runtime=_runtime(),
    )
    assert parsed["full_parent"]["coverage_range"] == [0, 1]


def test_chain_budget_zero_progress_and_complete_result(monkeypatch):
    zero_request = _request(max_compute_seconds=150)
    zero_clock = _Clock()
    zero_calls = _install_fake_session(monkeypatch, zero_clock)
    zero_response = remote.execute_public_m2_eval_request(
        zero_request, request_sha256=hashlib.sha256(zero_request).hexdigest(),
    )
    zero, _ = remote._wire._unpack(
        zero_response, remote._RESULT_MAGIC, remote.MAX_RESULT_BYTES,
    )
    assert zero_calls == {"pilot": 1, "full_ordinals": []}
    assert zero["full_parent"]["coverage_range"] == [0, 0]
    assert zero["full_parent"]["next_chain_ordinal"] == 0
    assert zero["full_parent"]["chains"] == [] and zero["full_parent"]["summary"] is None
    remote.decode_public_m2_eval_result(zero_response, zero_request,
                                        expected_runtime=_runtime())

    complete_request = _request(max_compute_seconds=650)
    complete_clock = _Clock()
    complete_calls = _install_fake_session(monkeypatch, complete_clock)
    complete_response = remote.execute_public_m2_eval_request(
        complete_request, request_sha256=hashlib.sha256(complete_request).hexdigest(),
    )
    complete, _ = remote._wire._unpack(
        complete_response, remote._RESULT_MAGIC, remote.MAX_RESULT_BYTES,
    )
    assert complete_calls == {"pilot": 1, "full_ordinals": [0, 1]}
    assert complete["full_parent"]["coverage_range"] == [0, 2]
    assert complete["full_parent"]["range_complete"] is True
    assert complete["full_parent"]["complete"] is True
    assert complete["full_parent"]["next_chain_ordinal"] is None
    remote.decode_public_m2_eval_result(complete_response, complete_request,
                                        expected_runtime=_runtime())


def test_cpu_accept_rejects_forged_chain_metrics_and_model_binding(monkeypatch):
    request = _request(max_compute_seconds=650)
    _install_fake_session(monkeypatch, _Clock())
    response = remote.execute_public_m2_eval_request(
        request, request_sha256=hashlib.sha256(request).hexdigest(),
    )
    header, blobs = remote._wire._unpack(
        response, remote._RESULT_MAGIC, remote.MAX_RESULT_BYTES,
    )
    chain = header["full_parent"]["shard_results"][0]["chains"][0]
    chain["correct_count"] = chain["label_count"] + 1
    shown = header["full_parent"]["chains"][0]
    shown["correct_count"] = chain["correct_count"]
    full = header["full_parent"]
    full["chain_commit_sha256"] = hashlib.sha256(json_bytes({
        "request_sha256": header["request_sha256"],
        "selection_identity": full["selection_identity"],
        "start_ordinal": 0, "stop_ordinal": 2, "chains": full["chains"],
    })).hexdigest()
    tampered_metrics = remote._wire._pack(
        remote._RESULT_MAGIC, {key: value for key, value in header.items() if key != "blobs"},
        blobs, remote.MAX_RESULT_BYTES,
    )
    with pytest.raises(BoundaryError):
        remote.decode_public_m2_eval_result(
            tampered_metrics, request, expected_runtime=_runtime(),
        )
    header, blobs = remote._wire._unpack(
        response, remote._RESULT_MAGIC, remote.MAX_RESULT_BYTES,
    )
    header["full_parent"]["bindings"]["weights_digest"] = "f" * 64
    tampered_binding = remote._wire._pack(
        remote._RESULT_MAGIC, {key: value for key, value in header.items() if key != "blobs"},
        blobs, remote.MAX_RESULT_BYTES,
    )
    with pytest.raises(BoundaryError, match="result_model_binding_mismatch"):
        remote.decode_public_m2_eval_result(
            tampered_binding, request, expected_runtime=_runtime(),
        )


def test_worker_rejects_fake_source_producer_before_decode(monkeypatch):
    producer = Producer("eval", "a" * 40, "b" * 64)
    other = Producer("eval", "c" * 40, "b" * 64)
    resources = PublicM2ModalResources("A10", 2, 4096, 4, 8192, 800, 120)
    monkeypatch.setattr(worker, "_runtime_source_identity", lambda: other)
    with pytest.raises(BoundaryError, match="image_source_identity_mismatch"):
        worker._execute_once(
            b"opaque", request_sha256=hashlib.sha256(b"opaque").hexdigest(),
            attempt_id="2" * 32, producer=producer, resources=resources,
        )


def test_eval_wire_bounds_request_and_result_frames():
    with pytest.raises(BoundaryError):
        remote._wire._unpack(b"x" * (remote.MAX_REQUEST_BYTES + 1),
                             remote._REQUEST_MAGIC, remote.MAX_REQUEST_BYTES)
    large_blob = b"x" * (remote.MAX_RESULT_BYTES + 1)
    oversized = remote._wire._pack(remote._RESULT_MAGIC, {"schema": remote.RESULT_SCHEMA},
                                   {hashlib.sha256(large_blob).hexdigest(): large_blob},
                                   remote.MAX_RESULT_BYTES + 1_000)
    with pytest.raises(BoundaryError):
        remote._wire._unpack(oversized, remote._RESULT_MAGIC, remote.MAX_RESULT_BYTES)
