"""Synthetic provider-neutral M2 bytes transport; no Modal or real data."""

from __future__ import annotations

import hashlib
import io
from unittest.mock import patch

import pytest
import torch
from test_public_m2_run import _fixture, _prepare

import stpd.workers.public_m2_remote as remote_module
from spireagent.artifact_contracts import Manifest, Parent, Payload
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from stpd.workers.public_m2_remote import (
    _RESULT_MAGIC,
    MAX_RESULT_BYTES,
    _pack,
    _Projection,
    _unpack,
    accept_public_m2_remote_result,
    build_public_m2_remote_request,
    execute_public_m2_remote_request,
)
from stpd.workers.public_m2_run import _load_run, prepare_public_m2_run


@pytest.fixture(autouse=True)
def one_thread():
    prior = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(prior)


def test_first_remote_window_returns_checkpoint_delta(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, _, _ = bundle
    run = _prepare(bundle)
    _, _, _, _, engine = _load_run(store, run.artifact_id, producer)
    request = build_public_m2_remote_request(
        store, reporter, run.artifact_id, producer, attempt_id="a" * 32,
        expected_runtime=engine.runtime, resume=None, max_windows=1,
    )
    result = execute_public_m2_remote_request(
        request, request_sha256=hashlib.sha256(request).hexdigest(),
    )
    header, blobs = _unpack(result, _RESULT_MAGIC, MAX_RESULT_BYTES)
    assert header["runtime"] == engine.runtime
    assert header["worker_result"]["state"] == "paused"
    assert header["worker_result"]["checkpoint_id"]
    assert len(header["event_ids"]) >= 4
    assert blobs
    assert reporter.events(run.artifact_id) == ()
    with pytest.raises(BoundaryError, match="request_digest_mismatch"):
        execute_public_m2_remote_request(request, request_sha256="0" * 64)
    with patch.object(remote_module, "_runtime_evidence", return_value={
        **engine.runtime, "platform": "synthetic-wrong-platform",
    }), pytest.raises(BoundaryError, match="runtime_mismatch"):
        execute_public_m2_remote_request(
            request, request_sha256=hashlib.sha256(request).hexdigest(),
        )


def test_local_accept_then_resume_to_first_stage(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, _, _ = bundle
    run = _prepare(bundle)
    _, _, _, _, engine = _load_run(store, run.artifact_id, producer)
    prior = None
    for index in range(3):
        request = build_public_m2_remote_request(
            store, reporter, run.artifact_id, producer,
            attempt_id=f"{index + 1:032x}", expected_runtime=engine.runtime,
            resume=prior, max_windows=1,
        )
        sha = hashlib.sha256(request).hexdigest()
        result = execute_public_m2_remote_request(request, request_sha256=sha)
        if index == 0:
            header, _ = _unpack(result, _RESULT_MAGIC, MAX_RESULT_BYTES)
            first = next(item for item in header["manifests"]
                         if item["id"] == header["event_ids"][0])
            reporter.emit(Manifest.from_bytes(first["raw"].encode(), first["id"]))
        outcome = accept_public_m2_remote_result(
            store, reporter, request, result, request_sha256=sha,
            expected_runtime=engine.runtime,
        )
        assert outcome.state == "paused"
        assert accept_public_m2_remote_result(
            store, reporter, request, result, request_sha256=sha,
            expected_runtime=engine.runtime,
        ) == outcome
        prior = outcome.checkpoint_id
    assert prior is not None
    assert store.get_manifest(prior).parameters.value()["completed_epochs"] == 1
    stages = [store.get_manifest(key) for key in store.manifest_ids()
              if store.get_manifest(key).parameters.value().get("schema")
              == "stpd/public-m2-epoch-stage-v1"]
    assert len(stages) == 1 and stages[0].parameters.value()["epoch"] == 1


def test_remote_rejects_cross_request_missing_bytes_and_size_before_local_write(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, _, _ = bundle
    run = _prepare(bundle)
    _, _, _, _, engine = _load_run(store, run.artifact_id, producer)
    request = build_public_m2_remote_request(
        store, reporter, run.artifact_id, producer, attempt_id="a" * 32,
        expected_runtime=engine.runtime, resume=None, max_windows=1,
    )
    sha = hashlib.sha256(request).hexdigest()
    result = execute_public_m2_remote_request(request, request_sha256=sha)
    header, blobs = _unpack(result, _RESULT_MAGIC, MAX_RESULT_BYTES)
    before = store.manifest_ids()
    wrong = _pack(
        _RESULT_MAGIC, {key: value for key, value in header.items() if key != "blobs"}
        | {"request_sha256": "0" * 64}, blobs, MAX_RESULT_BYTES,
    )
    with pytest.raises(BoundaryError, match="result_binding_mismatch"):
        accept_public_m2_remote_result(
            store, reporter, request, wrong, request_sha256=sha,
            expected_runtime=engine.runtime,
        )
    _, _, _, view, allocation, value, config = bundle
    other = prepare_public_m2_run(
        store, value, config, producer, source_view_id=view.artifact_id,
        allocation_id=allocation.artifact_id, operation_id="e" * 32,
    )
    other_request = build_public_m2_remote_request(
        store, reporter, other.artifact_id, producer, attempt_id="b" * 32,
        expected_runtime=engine.runtime, resume=None, max_windows=1,
    )
    with pytest.raises(BoundaryError, match="result_binding_mismatch"):
        accept_public_m2_remote_result(
            store, reporter, other_request, result,
            request_sha256=hashlib.sha256(other_request).hexdigest(),
            expected_runtime=engine.runtime,
        )
    missing = _pack(
        _RESULT_MAGIC, {key: value for key, value in header.items() if key != "blobs"},
        {}, MAX_RESULT_BYTES,
    )
    with pytest.raises(BoundaryError):
        accept_public_m2_remote_result(
            store, reporter, request, missing, request_sha256=sha,
            expected_runtime=engine.runtime,
        )
    with patch.object(remote_module, "MAX_RESULT_BYTES", 128), pytest.raises(
        BoundaryError, match="invalid_frame",
    ):
        accept_public_m2_remote_result(
            store, reporter, request, result, request_sha256=sha,
            expected_runtime=engine.runtime,
        )
    with patch.object(remote_module, "MAX_REQUEST_BYTES", 128), pytest.raises(
        BoundaryError, match="payload_size_limit|frame_size_limit",
    ):
        build_public_m2_remote_request(
            store, reporter, run.artifact_id, producer, attempt_id="c" * 32,
            expected_runtime=engine.runtime, resume=None, max_windows=1,
        )
    assert set(store.manifest_ids()) - set(before) == {
        other.parent("training_input"), other.parent("experiment"), other.artifact_id,
    } - set(before)
    reporter.emit(Manifest(
        "run_event", producer, (Parent("run", run.artifact_id),),
        parameters=FrozenObject.of({"schema": "stpd/run-event-v1", "kind": "other"}),
    ))
    with pytest.raises(BoundaryError, match="unexpected_local_history"):
        accept_public_m2_remote_result(
            store, reporter, request, result, request_sha256=sha,
            expected_runtime=engine.runtime,
        )


def test_source_projection_never_reads_declared_raw_payload(tmp_path):
    base = ManifestArtifactStore(LocalBlobStore(tmp_path))
    payload = base.put_payload("raw", io.BytesIO(b"synthetic-only"))
    source = Manifest("dataset", _fixture(tmp_path / "other")[2], payloads=(payload,))
    projected = _Projection(base, {source.artifact_id: source})
    assert projected.get_manifest(source.artifact_id) == source
    with pytest.raises(BoundaryError, match="source_payload_forbidden"):
        b"".join(projected.read_payload(Payload(
            "raw", payload.sha256, payload.size, payload.media_type,
        )))


def test_terminal_completion_is_selected_only_in_local_reporter(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, _, _ = bundle
    run = _prepare(bundle)
    _, _, _, _, engine = _load_run(store, run.artifact_id, producer)
    prior = None
    for attempt in range(10):
        request = build_public_m2_remote_request(
            store, reporter, run.artifact_id, producer,
            attempt_id=f"{attempt + 1:032x}", expected_runtime=engine.runtime,
            resume=prior, max_windows=1,
        )
        sha = hashlib.sha256(request).hexdigest()
        result = execute_public_m2_remote_request(request, request_sha256=sha)
        assert reporter.completed(run.artifact_id) is None
        outcome = accept_public_m2_remote_result(
            store, reporter, request, result, request_sha256=sha,
            expected_runtime=engine.runtime,
        )
        prior = outcome.checkpoint_id
    assert outcome.state == "completed"
    assert reporter.completed(run.artifact_id) is None
    recovery = build_public_m2_remote_request(
        store, reporter, run.artifact_id, producer, attempt_id="b" * 32,
        expected_runtime=engine.runtime, resume=prior, max_windows=0,
    )
    recovery_sha = hashlib.sha256(recovery).hexdigest()
    recovery_result = execute_public_m2_remote_request(
        recovery, request_sha256=recovery_sha,
    )
    outcome = accept_public_m2_remote_result(
        store, reporter, recovery, recovery_result,
        request_sha256=recovery_sha, expected_runtime=engine.runtime,
        select_completion=True,
    )
    assert reporter.completed(run.artifact_id).artifact_id == outcome.result_id
