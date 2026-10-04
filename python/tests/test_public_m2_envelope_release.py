"""Opt-in CPU transport-capacity smoke using synthetic, one-step M2 chains.

Run with STPD_M2_ENVELOPE_RELEASE_SMOKE=1. This tests byte framing at the
standard model shape; it is not a data-admission or CUDA performance result.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from pathlib import Path

import pytest
import torch
from test_public_m2_run import _chain, _fixture
from tokenizers import Tokenizer, models

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.cloud_jobs import public_m2_modal
from stpd.cloud_jobs.public_m2_modal import (
    MAX_PUBLIC_M2_RESULT_BYTES,
    PublicM2ModalBinding,
    PublicM2ModalResources,
    _decode_modal_response,
    encode_modal_response,
)
from stpd.models.token_core import ScratchShape
from stpd.workers.public_m2_remote import (
    _REQUEST_MAGIC,
    _RESULT_MAGIC,
    MAX_REQUEST_BYTES,
    MAX_RESULT_BYTES,
    _pack,
    _unpack,
    accept_public_m2_remote_result,
    build_public_m2_remote_request,
    execute_public_m2_remote_request,
)
from stpd.workers.public_m2_run import _load_run, prepare_public_m2_run


def test_result_caps_and_corrupt_frame_rejection(monkeypatch):
    assert MAX_RESULT_BYTES == MAX_PUBLIC_M2_RESULT_BYTES == 192 * 1024 * 1024
    assert public_m2_modal.MAX_PUBLIC_M2_TRANSPORT_BYTES > MAX_RESULT_BYTES
    payload = b"synthetic-result-payload"
    blob_sha = hashlib.sha256(payload).hexdigest()
    framed = _pack(_RESULT_MAGIC, {"schema": "synthetic-cap-probe"},
                   {blob_sha: payload}, 1024)
    # Exact maximum and one byte of headroom pass; one byte short rejects.
    assert len(_pack(_RESULT_MAGIC, {"schema": "synthetic-cap-probe"},
                     {blob_sha: payload}, len(framed))) == len(framed)
    assert len(_pack(_RESULT_MAGIC, {"schema": "synthetic-cap-probe"},
                     {blob_sha: payload}, len(framed) + 1)) == len(framed)
    assert _unpack(framed, _RESULT_MAGIC, len(framed))[1] == {blob_sha: payload}
    with pytest.raises(BoundaryError, match="frame_size_limit"):
        _pack(_RESULT_MAGIC, {"schema": "synthetic-cap-probe"},
              {blob_sha: payload}, len(framed) - 1)
    with pytest.raises(BoundaryError, match="invalid_frame"):
        _unpack(framed, _RESULT_MAGIC, len(framed) - 1)
    with pytest.raises(BoundaryError, match="blob_integrity_mismatch"):
        _unpack(framed[:-1] + b"X", _RESULT_MAGIC, 1024)
    binding = PublicM2ModalBinding(
        _fixture_producer(), "a" * 64, "im-synthetic", "d" * 64,
        PublicM2ModalResources("L4", 2.0, 8192, 4.0, 12288, 900, 120),
    )
    monkeypatch.setattr(public_m2_modal, "MAX_PUBLIC_M2_RESULT_BYTES", len(payload))
    modal_wire = encode_modal_response(payload, binding)
    assert _decode_modal_response(modal_wire, binding) == payload
    monkeypatch.setattr(public_m2_modal, "MAX_PUBLIC_M2_RESULT_BYTES", len(payload) + 1)
    assert _decode_modal_response(modal_wire, binding) == payload
    monkeypatch.setattr(public_m2_modal, "MAX_PUBLIC_M2_RESULT_BYTES", len(payload) - 1)
    with pytest.raises(BoundaryError, match="result_size_limit"):
        encode_modal_response(payload, binding)
    with pytest.raises(BoundaryError, match="result_size_limit"):
        _decode_modal_response(modal_wire, binding)
    monkeypatch.setattr(public_m2_modal, "MAX_PUBLIC_M2_RESULT_BYTES", len(payload))
    with pytest.raises(BoundaryError, match="result_target_binding_mismatch"):
        _decode_modal_response(modal_wire[:-1] + b"X", binding)


def _fixture_producer():
    from spireagent.artifact_contracts import Producer

    return Producer("synthetic/test", "a" * 40, "b" * 64)


@pytest.mark.skipif(
    os.environ.get("STPD_M2_ENVELOPE_RELEASE_SMOKE") != "1",
    reason="explicit formal-shape synthetic transport smoke only",
)
def test_formal_shape_epoch_readouts_fit_bounded_wire(tmp_path):
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        _formal_shape_envelope_smoke(tmp_path)
    finally:
        torch.set_num_threads(previous_threads)


def _formal_shape_envelope_smoke(tmp_path):
    store, reporter, producer, view, allocation, original, config = _fixture(tmp_path)
    train, dev = _chain("train", "train", 1), _chain("dev", "dev", 1)
    tokenizer = Tokenizer(models.WordLevel(
        {f"token{i}": i for i in range(7765)}, unk_token="token0",
    )).to_str().encode()
    value = replace(
        original, codec_fit_chain_ids=(train.chain_id,),
        codec_fit_transition_ids=tuple(row.transition_id for row in train.evidence),
        state_tokenizer=tokenizer, chains=(train, dev),
        max_state_tokens=8192, max_action_bytes=8192,
    )
    config = replace(
        config, source_digest=value.identity,
        state_tokenizer_sha256=hashlib.sha256(tokenizer).hexdigest(),
        shape=ScratchShape(7765, 384, 2, 6, 1536, 0.1, 8192),
        max_action_bytes=8192, window_steps=4, max_window_tokens=98_304,
    )
    run = prepare_public_m2_run(
        store, value, config, producer, source_view_id=view.artifact_id,
        allocation_id=allocation.artifact_id, operation_id="c" * 32,
    )
    _, _, _, _, engine = _load_run(store, run.artifact_id, producer)
    assert MAX_RESULT_BYTES == MAX_PUBLIC_M2_RESULT_BYTES == 192 * 1024 * 1024
    prior = None
    observed_stages = []
    attempts = []
    capacity_frame_size = None
    for attempt in range(1, 6):
        request = build_public_m2_remote_request(
            store, reporter, run.artifact_id, producer,
            attempt_id=f"{attempt:032x}", expected_runtime=engine.runtime,
            resume=prior, max_windows=1,
        )
        if attempt == 2:
            capacity_frame_size = _check_max_input_recovery_frame(request, store, run)
        request_sha = hashlib.sha256(request).hexdigest()
        result = execute_public_m2_remote_request(request, request_sha256=request_sha)
        header, blobs = _unpack(result, _RESULT_MAGIC, MAX_RESULT_BYTES)
        assert 128 * 1024 * 1024 < len(result) < MAX_RESULT_BYTES
        # Boundary resume republishes stage 1/3 readouts, so the exact sequence
        # is [1, 1, 3, 3, 5]. Each attempt has only its own stage in the delta;
        # prior weights are not accumulated.
        assert len(blobs) == 2
        manifests = [Manifest.from_bytes(item["raw"].encode(), item["id"])
                     for item in header["manifests"]]
        stages = [item for item in manifests if item.kind == "analysis"]
        assert len(stages) == 1
        stage_epoch = stages[0].parameters.value()["epoch"]
        assert stage_epoch in {1, 3, 5}
        observed_stages.append(stage_epoch)
        assert len([item for item in manifests if item.kind == "checkpoint"]) == 1
        assert len([item for item in manifests if item.kind == "model"]) == 1
        print("synthetic_envelope", attempt, stage_epoch, len(request),
              len(result), sorted(map(len, blobs.values())))
        attempts.append({
            "attempt": attempt, "readout_epoch": stage_epoch,
            "request_bytes": len(request), "result_bytes": len(result),
            "result_blob_bytes": sorted(map(len, blobs.values())),
        })
        if attempt == 1:
            binding = PublicM2ModalBinding(
                producer, request_sha, "im-synthetic", "d" * 64,
                PublicM2ModalResources("L4", 2.0, 8192, 4.0, 12288, 900, 120),
            )
            modal_wire = encode_modal_response(result, binding)
            assert _decode_modal_response(modal_wire, binding) == result
            assert len(modal_wire) <= MAX_PUBLIC_M2_RESULT_BYTES + 16 * 1024 + 64
            del modal_wire
            with pytest.raises(BoundaryError, match="frame_size_limit"):
                _pack(_RESULT_MAGIC,
                      {key: val for key, val in header.items() if key != "blobs"},
                      blobs, 128 * 1024 * 1024)
        outcome = accept_public_m2_remote_result(
            store, reporter, request, result, request_sha256=request_sha,
            expected_runtime=engine.runtime,
        )
        assert outcome.state == ("completed" if attempt == 5 else "paused")
        prior = outcome.checkpoint_id
    assert observed_stages == [1, 1, 3, 3, 5]
    if receipt_path := os.environ.get("STPD_M2_ENVELOPE_RECEIPT_PATH"):
        assert capacity_frame_size is not None
        Path(receipt_path).write_bytes(json_bytes({
            "schema": "stpd/synthetic-public-m2-envelope-smoke-v1",
            "scope": "synthetic_cpu_transport_capacity_only",
            "shape": {"vocab_size": 7765, "width": 384, "layers": 2,
                      "heads": 6, "feedforward": 1536, "dropout": 0.1,
                      "max_tokens": 8192},
            "window_steps": 4, "train_steps": 1, "dev_steps": 1,
            "request_cap_bytes": MAX_REQUEST_BYTES,
            "result_cap_bytes": MAX_RESULT_BYTES,
            "max_input_filler_bytes": 144_644_396,
            "tokenizer_filler_bytes": 315 * 1024,
            "capacity_only_recovery_frame_bytes": capacity_frame_size,
            "capacity_only_short_history": True,
            "attempts": attempts,
        }))


def _check_max_input_recovery_frame(request: bytes, store, run):
    """Capacity-only substitution; the fixture is not a valid training input.

    Keep the actual typed request manifests/history and trained checkpoint,
    replace only the two input blob bodies with synthetic sized fillers.
    """
    header, blobs = _unpack(request, _REQUEST_MAGIC, MAX_REQUEST_BYTES)
    training = store.get_manifest(run.parent("training_input"))
    blobs.pop(training.payload("training_input").sha256)
    blobs.pop(training.payload("state_tokenizer").sha256)
    input_filler = b"x" * 144_644_396
    tokenizer_filler = b"y" * (315 * 1024)
    blobs[hashlib.sha256(input_filler).hexdigest()] = input_filler
    blobs[hashlib.sha256(tokenizer_filler).hexdigest()] = tokenizer_filler
    header_without_inventory = {key: value for key, value in header.items()
                                if key != "blobs"}
    framed = _pack(_REQUEST_MAGIC, header_without_inventory, blobs, MAX_REQUEST_BYTES)
    assert len(framed) < MAX_REQUEST_BYTES
    # This is the short-chain header. The 3k event-history bound is separate.
    assert MAX_REQUEST_BYTES - len(framed) > 15 * 1024 * 1024 - 512 * 1024
    print("synthetic_max_input_recovery", len(framed),
          MAX_REQUEST_BYTES - len(framed))
    with pytest.raises(BoundaryError, match="frame_size_limit"):
        _pack(_REQUEST_MAGIC, header_without_inventory, blobs, len(framed) - 1)
    return len(framed)
