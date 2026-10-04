"""Synthetic provider-neutral evaluation request/result wire checks."""

from __future__ import annotations

import hashlib

import pytest
from test_public_m2_run import _chain

from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.fullrun.public_m2_sequences import PublicM2Input
from stpd.workers import public_m2_eval_remote as remote


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


def _request():
    pilot = _source("pilot", (13,))
    full = _source("full", (15, 3))
    config_digest = "c" * 64
    config = {
        "source_digest": full.identity,
        "state_tokenizer_sha256": hashlib.sha256(full.state_tokenizer).hexdigest(),
    }
    # The request framing layer treats training config as a strict declared pin;
    # execution tests construct a real full config and model separately.
    evaluation_producer = {"repository": "eval", "source_revision": "3" * 40,
                           "uv_lock_sha256": "4" * 64}
    return remote.build_public_m2_eval_request(
        evaluation_operation_id="1" * 32, attempt_id="2" * 32,
        evaluation_producer=evaluation_producer,
        training_producer={"repository": "train", "source_revision": "5" * 40,
                           "uv_lock_sha256": "6" * 64},
        resources={"gpu": "A10", "deadline_seconds": 800},
        model_id="7" * 64, stage_id="8" * 64, checkpoint_id="9" * 64,
        training_config=config, training_source_digest=full.identity,
        engine_input_digest=config_digest,
        pilot_input=pilot.payload_bytes(), full_parent_input=full.payload_bytes(),
        tokenizer=full.state_tokenizer, weights=b"synthetic-frozen-weights",
        pilot_source_id="pilot-source", full_parent_source_id="full-source",
        runtime_requirement=remote.InferenceRuntimeRequirement(
            "A10", "float32", "torch-test", "python-test",
            hashlib.sha256(json_bytes(evaluation_producer)).hexdigest(), "b" * 64,
        ),
        chain_range=(0, 2),
    )


def test_eval_request_binary_blobs_deduplicate_and_bind_exact_sources():
    raw = _request()
    value = remote.validate_public_m2_eval_request(raw)
    header, blobs = remote._request_parts(raw)
    assert header["schema"] == remote.REQUEST_SCHEMA
    assert set(header["blob_roles"]) == set(remote._BLOB_ROLES)
    assert len(blobs) == 4
    assert value["validated_selection_identities"] == header["selection_identities"]
    assert len(raw) < 1_000_000


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


def test_eval_result_decoder_requires_request_identity_and_chain_commit():
    request = _request()
    header, _ = remote._request_parts(request)
    fake = {
        "schema": remote.RESULT_SCHEMA,
        "request_sha256": hashlib.sha256(request).hexdigest(),
        "evaluation_operation_id": header["evaluation_operation_id"],
        "attempt_id": header["attempt_id"],
        "evaluation_producer": header["evaluation_producer"],
        "training_producer": header["training_producer"],
        "model_id": header["model_id"], "stage_id": header["stage_id"],
        "checkpoint_id": header["checkpoint_id"],
        "pilot": {"label_count": 13, "correct_count": 4, "loss_mean": 1.854619842},
        "full_parent": {
            "source_digest": header["evaluation_source_digests"]["full_parent"],
            "selection_identity": header["selection_identities"]["full_parent"],
            "chain_range": header["chain_range"], "complete": False,
            "next_chain_ordinal": 2, "chains": [], "chain_commit_sha256": "0" * 64,
        },
    }
    framed = remote._wire._pack(remote._RESULT_MAGIC, fake, {}, remote.MAX_RESULT_BYTES)
    with pytest.raises(BoundaryError, match="result_chain_range_mismatch"):
        remote.decode_public_m2_eval_result(framed, request)


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
