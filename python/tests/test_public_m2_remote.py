"""Synthetic provider-neutral M2 bytes transport; no Modal or real data."""

from __future__ import annotations

import hashlib
import io
from dataclasses import replace
from unittest.mock import patch

import pytest
import torch
from test_public_m2_run import _fixture, _prepare

import stpd.workers.public_m2_remote as remote_module
import stpd.workers.public_m2_run as run_module
from spireagent.artifact_contracts import Manifest, Parent, Payload
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.blobs import StoreError
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


def _tamper_manifest(raw: bytes, kind: str, change):
    """Rebind descendant IDs so inventory checks see an otherwise coherent delta."""
    header, blobs = _unpack(raw, _RESULT_MAGIC, MAX_RESULT_BYTES)
    originals = {
        entry["id"]: Manifest.from_bytes(entry["raw"].encode(), entry["id"])
        for entry in header["manifests"]
    }
    target_id = next(key for key, item in originals.items() if item.kind == kind)
    rewritten = {target_id: change(originals[target_id], blobs)}
    mapping = {target_id: rewritten[target_id].artifact_id}
    while True:
        changed = False
        for identity, item in originals.items():
            if identity == target_id:
                continue
            parents = tuple(Parent(parent.role, mapping.get(parent.artifact_id,
                                                            parent.artifact_id))
                            for parent in item.parents)
            parameters = item.parameters.value()
            if item.kind == "run_event":
                details = parameters["details"]
                parameters["details"] = {
                    key: mapping.get(value, value) for key, value in details.items()
                }
            candidate = replace(item, parents=parents,
                                parameters=FrozenObject.of(parameters))
            if candidate.artifact_id != identity:
                rewritten[identity] = candidate
                if mapping.get(identity) != candidate.artifact_id:
                    mapping[identity] = candidate.artifact_id
                    changed = True
        if not changed:
            break
    items = {mapping.get(key, key): rewritten.get(key, item)
             for key, item in originals.items()}
    header["manifests"] = [
        {"id": key, "raw": item.to_bytes().decode()}
        for key, item in sorted(items.items())
    ]
    header["event_ids"] = [mapping.get(key, key) for key in header["event_ids"]]
    result_id = header["worker_result"]["result_id"]
    if result_id is not None:
        header["worker_result"]["result_id"] = mapping.get(result_id, result_id)
    return _pack(_RESULT_MAGIC, {key: value for key, value in header.items()
                                 if key != "blobs"}, blobs, MAX_RESULT_BYTES)


def _extra_payload(item: Manifest, blobs: dict[str, bytes]) -> Manifest:
    sha = next(iter(blobs))
    return replace(item, payloads=(*item.payloads,
                                   Payload("unexpected", sha, len(blobs[sha]))))


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
    with patch.object(run_module, "_stage", wraps=run_module._stage) as stage_writer:
        for index in range(8):
            request = build_public_m2_remote_request(
                store, reporter, run.artifact_id, producer,
                attempt_id=f"{index + 1:032x}", expected_runtime=engine.runtime,
                resume=prior, max_windows=1,
            )
            request_header, _ = _unpack(request, remote_module._REQUEST_MAGIC,
                                        remote_module.MAX_REQUEST_BYTES)
            if request_header["stage_reuse"] is not None:
                assert request_header["stage_reuse"]["epoch"] == 1
            sha = hashlib.sha256(request).hexdigest()
            result = execute_public_m2_remote_request(request, request_sha256=sha)
            result_header, _ = _unpack(result, _RESULT_MAGIC, MAX_RESULT_BYTES)
            if index == 0:
                first = next(item for item in result_header["manifests"]
                             if item["id"] == result_header["event_ids"][0])
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
            if request_header["stage_reuse"] is not None:
                break
        assert stage_writer.call_count == 1
    assert prior is not None
    assert store.get_manifest(prior).parameters.value()["completed_epochs"] == 1
    stages = [store.get_manifest(key) for key in store.manifest_ids()
              if store.get_manifest(key).parameters.value().get("schema")
              == "stpd/public-m2-epoch-stage-v1"]
    assert len(stages) == 1 and stages[0].parameters.value()["epoch"] == 1


def test_stage_reuse_claim_binds_run_checkpoint_and_local_closure(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, _, _ = bundle
    run = _prepare(bundle)
    _, _, _, _, engine = _load_run(store, run.artifact_id, producer)
    prior = None
    request = None
    for index in range(8):
        request = build_public_m2_remote_request(
            store, reporter, run.artifact_id, producer,
            attempt_id=f"{index + 1:032x}", expected_runtime=engine.runtime,
            resume=prior, max_windows=1,
        )
        header, request_blobs = _unpack(request, remote_module._REQUEST_MAGIC,
                                        remote_module.MAX_REQUEST_BYTES)
        if header["stage_reuse"] is not None:
            break
        sha = hashlib.sha256(request).hexdigest()
        result = execute_public_m2_remote_request(request, request_sha256=sha)
        outcome = accept_public_m2_remote_result(
            store, reporter, request, result, request_sha256=sha,
            expected_runtime=engine.runtime,
        )
        prior = outcome.checkpoint_id
    assert prior is not None and request is not None
    claim = header["stage_reuse"]
    assert claim["checkpoint_id"] == prior
    accepted_events = reporter.events(run.artifact_id)

    def without_stage_acceptance(_store, run_id):
        return tuple(event for event in accepted_events
                     if event.parameters.value().get("kind") != "epoch_stage")

    with patch.object(remote_module, "_run_events", side_effect=without_stage_acceptance):
        fallback = build_public_m2_remote_request(
            store, reporter, run.artifact_id, producer, attempt_id="c" * 32,
            expected_runtime=engine.runtime, resume=prior, max_windows=1,
        )
    fallback_header, _ = _unpack(fallback, remote_module._REQUEST_MAGIC,
                                 remote_module.MAX_REQUEST_BYTES)
    assert fallback_header["stage_reuse"] is None
    get_manifest = store.get_manifest
    for missing_id in (claim["stage_id"], claim["model_id"], claim["evaluation_id"]):
        def missing_manifest(identity, target=missing_id):
            if identity == target:
                raise StoreError("object_not_found")
            return get_manifest(identity)

        with patch.object(store, "get_manifest", side_effect=missing_manifest):
            missing = build_public_m2_remote_request(
                store, reporter, run.artifact_id, producer, attempt_id="b" * 32,
                expected_runtime=engine.runtime, resume=prior, max_windows=1,
            )
        missing_header, _ = _unpack(missing, remote_module._REQUEST_MAGIC,
                                    remote_module.MAX_REQUEST_BYTES)
        assert missing_header["stage_reuse"] is None
    for field in ("run_id", "checkpoint_id"):
        tampered_header = dict(header)
        tampered_claim = dict(claim)
        tampered_claim[field] = "0" * 64
        tampered_claim["commitment_sha256"] = hashlib.sha256(json_bytes({
            key: value for key, value in tampered_claim.items()
            if key != "commitment_sha256"
        })).hexdigest()
        tampered_header["stage_reuse"] = tampered_claim
        tampered = _pack(remote_module._REQUEST_MAGIC, tampered_header, request_blobs,
                         remote_module.MAX_REQUEST_BYTES)
        with pytest.raises(BoundaryError, match="stage_reuse_binding_mismatch"):
            execute_public_m2_remote_request(
                tampered, request_sha256=hashlib.sha256(tampered).hexdigest(),
            )
    sha = hashlib.sha256(request).hexdigest()
    result = execute_public_m2_remote_request(request, request_sha256=sha)
    before = store.manifest_ids()
    result_header, result_blobs = _unpack(result, _RESULT_MAGIC, MAX_RESULT_BYTES)
    wrong_binding = _pack(
        _RESULT_MAGIC,
        {key: value for key, value in result_header.items() if key != "blobs"}
        | {"stage_reuse_sha256": "0" * 64},
        result_blobs, MAX_RESULT_BYTES,
    )
    with pytest.raises(BoundaryError, match="result_binding_mismatch"):
        accept_public_m2_remote_result(
            store, reporter, request, wrong_binding, request_sha256=sha,
            expected_runtime=engine.runtime,
        )

    read_payload = store.read_payload

    def corrupt_weight(payload):
        raw = b"".join(read_payload(payload))
        if payload.sha256 == claim["weights"]["sha256"]:
            raw = raw[:-1] + bytes([raw[-1] ^ 1])
        yield raw

    with patch.object(store, "read_payload", side_effect=corrupt_weight), pytest.raises(
        BoundaryError, match="payload_integrity_mismatch",
    ):
        accept_public_m2_remote_result(
            store, reporter, request, result, request_sha256=sha,
            expected_runtime=engine.runtime,
        )
    assert store.manifest_ids() == before

    get_manifest = store.get_manifest
    for manifest_id, error in (
        (claim["stage_id"], "stage_reuse_local_mismatch"),
        (claim["evaluation_id"], "stage_reuse_local_mismatch"),
    ):
        def corrupt_manifest(identity, target=manifest_id):
            item = get_manifest(identity)
            if identity == target:
                parameters = item.parameters.value()
                parameters["input_identity"] = "f" * 64
                return replace(item, parameters=FrozenObject.of(parameters))
            return item

        with patch.object(store, "get_manifest", side_effect=corrupt_manifest), pytest.raises(
            BoundaryError, match=error,
        ):
            accept_public_m2_remote_result(
                store, reporter, request, result, request_sha256=sha,
                expected_runtime=engine.runtime,
            )
        assert store.manifest_ids() == before

    check_result = remote_module._checked_result

    def race_history(*args, **kwargs):
        outcome = check_result(*args, **kwargs)
        concurrent = Manifest(
            "run_event", producer, (Parent("run", run.artifact_id),),
            parameters=FrozenObject.of({
                "schema": "stpd/run-event-v1", "attempt": "e" * 32,
                "kind": "started", "completed_epochs": 1, "chain_index": 0,
                "window_cursor": 0, "label_count": 0, "optimizer_updates": 0,
                "details": {},
            }),
        )
        reporter.emit(concurrent)
        return outcome

    with patch.object(remote_module, "_checked_result", side_effect=race_history), pytest.raises(
        BoundaryError, match="local_history_changed_during_validation",
    ):
        accept_public_m2_remote_result(
            store, reporter, request, result, request_sha256=sha,
            expected_runtime=engine.runtime,
        )


def test_accepted_epoch_three_reuse_finishes_with_new_epoch_five_stage(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, _, _ = bundle
    run = _prepare(bundle)
    _, _, _, _, engine = _load_run(store, run.artifact_id, producer)
    prior = None
    request = None
    for index in range(20):
        request = build_public_m2_remote_request(
            store, reporter, run.artifact_id, producer,
            attempt_id=f"{index + 1:032x}", expected_runtime=engine.runtime,
            resume=prior, max_windows=1,
        )
        header, _ = _unpack(request, remote_module._REQUEST_MAGIC,
                            remote_module.MAX_REQUEST_BYTES)
        if header["stage_reuse"] is not None and header["stage_reuse"]["epoch"] == 3:
            break
        sha = hashlib.sha256(request).hexdigest()
        result = execute_public_m2_remote_request(request, request_sha256=sha)
        outcome = accept_public_m2_remote_result(
            store, reporter, request, result, request_sha256=sha,
            expected_runtime=engine.runtime,
        )
        prior = outcome.checkpoint_id
    assert prior is not None and request is not None
    assert store.get_manifest(prior).parameters.value()["completed_epochs"] == 3
    claim = header["stage_reuse"]
    assert claim["epoch"] == 3 and claim["checkpoint_id"] == prior
    run_manifest, training, _, _, profile = _load_run(
        store, run.artifact_id, producer, preflight_only=True,
    )
    checkpoint_state = remote_module.validate_public_m2_checkpoint(
        b"".join(store.read_payload(store.get_manifest(prior).payload("checkpoint"))),
        profile, expected_runtime=engine.runtime,
    )
    checked_claim = remote_module._accepted_stage_reuse(
        store, reporter, run_manifest, training, profile, prior,
        checkpoint_state, engine.runtime,
    )
    assert checked_claim == claim
    with patch.object(run_module, "_stage", wraps=run_module._stage) as stage_writer:
        outcome = run_module._execute_run(
            store, reporter, run.artifact_id, producer, resume=prior,
            stage_reuse=checked_claim,
        )
    assert outcome.state == "completed"
    assert stage_writer.call_count == 1
    result = reporter.completed(run.artifact_id)
    terminal_stage = store.get_manifest(result.parent("stage"))
    assert terminal_stage.parameters.value()["epoch"] == 5
    assert result.parent("stage") != claim["stage_id"]
    assert result.parent("model") == terminal_stage.parent("model")
    assert result.parent("offline_evaluation") == terminal_stage.parent("offline_evaluation")
    assert result.parent("model") != claim["model_id"]


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


@pytest.mark.parametrize("change", ["payload", "parameter", "parent", "details"])
def test_remote_rejects_extra_event_inventory_before_local_write(tmp_path, change):
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

    def corrupt(item, blobs):
        if change == "payload":
            return _extra_payload(item, blobs)
        if change == "parent":
            return replace(item, parents=(*item.parents,
                                          Parent("training_input",
                                                 run.parent("training_input"))))
        values = item.parameters.value()
        if change == "parameter":
            values["unexpected"] = 1
        else:
            values["details"]["unexpected"] = 1
        return replace(item, parameters=FrozenObject.of(values))

    tampered = _tamper_manifest(result, "run_event", corrupt)
    before = store.manifest_ids()
    with pytest.raises(BoundaryError, match="event_inventory_mismatch"):
        accept_public_m2_remote_result(
            store, reporter, request, tampered, request_sha256=sha,
            expected_runtime=engine.runtime,
        )
    assert store.manifest_ids() == before


def test_remote_rejects_extra_stage_payload_before_local_write(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, _, _ = bundle
    run = _prepare(bundle)
    _, _, _, _, engine = _load_run(store, run.artifact_id, producer)
    prior = None
    old_stage = None
    for attempt in range(10):
        request = build_public_m2_remote_request(
            store, reporter, run.artifact_id, producer,
            attempt_id=f"{attempt + 1:032x}", expected_runtime=engine.runtime,
            resume=prior, max_windows=1,
        )
        sha = hashlib.sha256(request).hexdigest()
        result = execute_public_m2_remote_request(request, request_sha256=sha)
        for kind in ({9: ("run_result",)}.get(attempt, ())):
            tampered = _tamper_manifest(result, kind, _extra_payload)
            before = store.manifest_ids()
            with pytest.raises(BoundaryError):
                accept_public_m2_remote_result(
                    store, reporter, request, tampered, request_sha256=sha,
                    expected_runtime=engine.runtime,
                )
            assert store.manifest_ids() == before
        if attempt == 9:
            assert old_stage is not None
            old_ids = {old_stage.artifact_id, *(parent.artifact_id
                        for parent in old_stage.parents
                        if parent.role in {"checkpoint", "model", "offline_evaluation"})}

            def old_result(item, _blobs, stage=old_stage):
                parents = tuple(
                    Parent(parent.role, stage.artifact_id if parent.role == "stage"
                           else stage.parent(parent.role))
                    if parent.role in {"stage", "model", "offline_evaluation"}
                    else parent for parent in item.parents
                )
                return replace(item, parents=parents)

            crossed = _tamper_manifest(result, "run_result", old_result)
            header, blobs = _unpack(crossed, _RESULT_MAGIC, MAX_RESULT_BYTES)
            entries = {}
            for entry in header["manifests"]:
                item = Manifest.from_bytes(entry["raw"].encode(), entry["id"])
                if item.kind in {"analysis", "model", "offline_evaluation"}:
                    continue
                if (item.kind == "run_event"
                        and item.parameters.value()["kind"] == "epoch_stage"):
                    values = item.parameters.value()
                    values["details"] = {"epoch": 1, "stage_id": old_stage.artifact_id}
                    changed = replace(item, parameters=FrozenObject.of(values))
                    header["event_ids"] = [changed.artifact_id if key == entry["id"]
                                           else key for key in header["event_ids"]]
                    item = changed
                entries[item.artifact_id] = item
            for identity in old_ids:
                item = store.get_manifest(identity)
                entries[identity] = item
                for payload in item.payloads:
                    blobs[payload.sha256] = b"".join(store.read_payload(payload))
            header["manifests"] = [
                {"id": key, "raw": item.to_bytes().decode()}
                for key, item in sorted(entries.items())
            ]
            referenced = {payload.sha256 for item in entries.values()
                          for payload in item.payloads}
            blobs = {key: value for key, value in blobs.items() if key in referenced}
            crossed = _pack(_RESULT_MAGIC,
                            {key: value for key, value in header.items()
                             if key != "blobs"}, blobs, MAX_RESULT_BYTES)
            before = store.manifest_ids()
            with pytest.raises(BoundaryError, match="terminal_result_mismatch"):
                accept_public_m2_remote_result(
                    store, reporter, request, crossed, request_sha256=sha,
                    expected_runtime=engine.runtime,
                )
            assert store.manifest_ids() == before
        outcome = accept_public_m2_remote_result(
            store, reporter, request, result, request_sha256=sha,
            expected_runtime=engine.runtime,
        )
        prior = outcome.checkpoint_id
        if attempt == 2:
            old_stage = next(store.get_manifest(key) for key in store.manifest_ids()
                             if store.get_manifest(key).kind == "analysis")
    assert outcome.state == "completed"


def test_terminal_completion_is_selected_only_in_local_reporter(tmp_path, monkeypatch):
    bundle = _fixture(tmp_path)
    store, reporter, producer, _, _, _, _ = bundle
    run = _prepare(bundle)
    _, _, _, _, engine = _load_run(store, run.artifact_id, producer)
    prior = None
    stage_epochs = []
    original_stage = run_module._stage

    def count_stage(*args, **kwargs):
        stage_epochs.append(args[3].completed_epochs)
        return original_stage(*args, **kwargs)

    monkeypatch.setattr(run_module, "_stage", count_stage)
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
    recovery_header, _ = _unpack(
        recovery, remote_module._REQUEST_MAGIC, remote_module.MAX_REQUEST_BYTES,
    )
    terminal_claim = recovery_header["stage_reuse"]
    assert terminal_claim["epoch"] == 5
    recovery_sha = hashlib.sha256(recovery).hexdigest()
    recovery_result = execute_public_m2_remote_request(
        recovery, request_sha256=recovery_sha,
    )
    outcome = accept_public_m2_remote_result(
        store, reporter, recovery, recovery_result,
        request_sha256=recovery_sha, expected_runtime=engine.runtime,
        select_completion=True,
    )
    terminal_result = store.get_manifest(outcome.result_id)
    assert terminal_result.parent("stage") == terminal_claim["stage_id"]
    assert terminal_result.parent("model") == terminal_claim["model_id"]
    assert terminal_result.parent("offline_evaluation") == terminal_claim["evaluation_id"]
    assert reporter.completed(run.artifact_id).artifact_id == outcome.result_id
    assert stage_epochs == [1, 3, 5]
    reused_epochs = {
        item.parameters.value()["details"]["epoch"]
        for item in reporter.events(run.artifact_id)
        if item.parameters.value()["kind"] == "epoch_stage_reused"
    }
    assert reused_epochs >= {1, 3, 5}
