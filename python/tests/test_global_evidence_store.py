"""Real CAS/archive/transfer boundaries; only typed replay is a synthetic seam.

The real large sealed GLOBAL run is exercised separately, never a test fixture
that claims native execution or an installed Application.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import sts2_platform_evidence as evidence

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.encoding import semantic_hash
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.blobs import StoreError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore, copy_artifact
from spireagent.workbench import global_evidence_store as global_store


def test_failed_stop_diagnostic_restores_without_becoming_a_typed_recording(tmp_path, monkeypatch):
    store, producer, directory, expected, *_ = _setup(tmp_path, monkeypatch)
    run_path = directory / "manifest.json"
    run = json.loads(run_path.read_bytes())
    run["agent_manifest_sha256"] = "a" * 64
    run_path.write_bytes(json_bytes(run))
    expected.update(run_id=run["run_id"], agent_manifest_sha256=run["agent_manifest_sha256"])
    files = []
    for path in sorted(directory.iterdir()):
        files.append({"path": path.name, "bytes": path.stat().st_size,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    marker = {"schema": "sts2.policy-runtime/unsealed-diagnostic-1", "run_id": run["run_id"],
              "complete": False, "status": "unsealed", "ended_at": "2026-10-24T08:00:00Z",
              "reason": "capture staging incomplete", "files": files,
              "manifest_sha256": semantic_hash({"run_id": run["run_id"], "files": files})}
    (directory / "diagnostic-manifest.json").write_bytes(json_bytes(marker))
    offered = []
    manifest = global_store.publish_global_diagnostic(
        store, directory, producer, expected=expected, scratch=tmp_path / "scratch",
        before_publish=lambda item: offered.append(item.artifact_id),
    )
    peer = ManifestArtifactStore(LocalBlobStore(tmp_path / "peer"))
    copy_artifact(store, peer, manifest.artifact_id)
    shutil.rmtree(directory)
    result = global_store.verify_global_diagnostic(
        peer, manifest.artifact_id, expected=expected, scratch=tmp_path / "scratch",
    )
    assert result["full_readback"] is True and result["typed_recording_qualified"] is False
    assert offered == [manifest.artifact_id] and manifest.kind == "analysis"
    with pytest.raises(BoundaryError, match="global_original_required"):
        global_store.verify_global_artifact(
            peer, manifest.artifact_id, expected=expected, scratch=tmp_path / "scratch",
        )


def _setup(tmp_path, monkeypatch):
    directory = tmp_path / "closed"
    directory.mkdir()
    model = tmp_path / "model"
    model.mkdir()
    weights = b"synthetic weight bytes; not a tensor model"
    weight_sha = hashlib.sha256(weights).hexdigest()
    (model / "model.tensor-tree").write_bytes(weights)
    (model / "model.json").write_bytes(
        json_bytes(
            {
                "schema": "stpd/p1-mstd-model-1",
                "package_sha256": weight_sha,
                "input_spec": {"id": "fixture-input", "version": "1", "sha256": "a" * 64},
            }
        )
    )
    runtime = tmp_path / "package" / "dist"
    runtime.mkdir(parents=True)
    (runtime.parent / "package.json").write_bytes(
        json_bytes(
            {
                "name": "@rsgcsg/sts2-policy-runtime",
                "version": "fixture",
            }
        )
    )
    (runtime / "global-replay.js").write_bytes(b"// synthetic not executed\n")
    code = hashlib.sha256(
        b"global-replay.js\0" + (runtime / "global-replay.js").read_bytes() + b"\0"
    ).hexdigest()
    expected = {
        "global_runtime_directory": str(runtime),
        "runtime_version": "fixture",
        "runtime_code_sha256": code,
    }
    run = {
        "schema": global_store.RUN_SCHEMA,
        "run_id": "run-fixture",
        "agent_artifact_sha256": weight_sha,
        "runtime_code_sha256": code,
        "runtime_version": "fixture",
        "status": "stopped",
        "tainted": False,
    }
    (directory / "manifest.json").write_bytes(json_bytes(run))
    (directory / "agent-manifest.json").write_bytes(
        json_bytes(
            {
                "input": {
                    "profile": "p1-global-r1",
                    "input_spec": {
                        "id": "fixture-input",
                        "version": "1",
                        "sha256": "a" * 64,
                    },
                }
            }
        )
    )
    (directory / "global-original.bin").write_bytes(b"full input, catalog, scores and Receipt\r\n")
    calls = []

    def verify(path, trust):
        # An archived path cannot replace the caller-owned replay installation.
        assert trust == expected
        calls.append(Path(path))
        files = [
            {
                "path": p.name,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "bytes": p.stat().st_size,
            }
            for p in sorted(Path(path).iterdir())
        ]
        value = evidence.AgentSessionRunEvidence(
            Path(path),
            json.loads((Path(path) / "manifest.json").read_bytes()),
            json.loads((Path(path) / "agent-manifest.json").read_bytes()),
            {},
            4,
            semantic_hash({"schema": "sts2.evidence/store-directory-1", "files": files}),
        )
        return SimpleNamespace(passed=True, value=value, findings=[])

    monkeypatch.setattr(evidence, "verify_agent_session_run_evidence", verify)
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    producer = Producer("fixture", "a" * 40, "b" * 64)
    model_artifact = global_store.publish_detached_p1_model(store, model, producer)
    runtime_artifact = global_store.publish_global_runtime(store, producer, expected)
    return store, producer, directory, expected, model_artifact, runtime_artifact, calls


def _publish(setup, scratch):
    store, producer, directory, expected, model, runtime, _ = setup
    return global_store.publish_global_evidence(
        store,
        directory,
        producer,
        expected=expected,
        model_artifact_id=model.artifact_id,
        runtime_artifact_id=runtime.artifact_id,
        scratch=scratch,
    )


def test_original_bytes_survive_by_id_transfer_and_full_typed_readback(tmp_path, monkeypatch):
    setup = _setup(tmp_path, monkeypatch)
    store, _, directory, expected, model, runtime, calls = setup
    manifest = _publish(setup, tmp_path / "scratch")
    assert manifest.artifact_id != manifest.parameters.value()["content_id"]
    assert manifest.parent("model") == model.artifact_id
    assert manifest.parent("runtime") == runtime.artifact_id
    assert "global_runtime_directory" not in manifest.to_bytes().decode()
    peer = ManifestArtifactStore(LocalBlobStore(tmp_path / "peer"))
    copy_artifact(store, peer, manifest.artifact_id)
    original = (directory / "global-original.bin").read_bytes()
    shutil.rmtree(directory)  # Only this test's synthetic original, forcing by-ID restoration.
    with global_store.materialize_global_evidence(
        peer,
        manifest.artifact_id,
        expected=expected,
        scratch=tmp_path / "scratch",
    ) as value:
        assert (value.directory / "global-original.bin").read_bytes() == original
    assert len(calls) == 2
    assert not calls[-1].exists()  # Expanded read working set has bounded lifetime.
    for parent in (model, runtime):
        for payload in parent.payloads:
            assert b"".join(peer.read_payload(payload)) == b"".join(store.read_payload(payload))


def test_wrong_parents_and_resealed_metadata_do_not_change_typed_facts(tmp_path, monkeypatch):
    setup = _setup(tmp_path, monkeypatch)
    store, producer, directory, expected, model, runtime, _ = setup
    wrong = replace(
        runtime,
        parameters=FrozenObject.of(
            {
                **runtime.parameters.value(),
                "runtime_code_sha256": "0" * 64,
            }
        ),
    )
    store.publish(wrong)
    with pytest.raises(BoundaryError, match="original_runtime_parent_mismatch"):
        global_store.publish_global_evidence(
            store,
            directory,
            producer,
            expected=expected,
            model_artifact_id=model.artifact_id,
            runtime_artifact_id=wrong.artifact_id,
            scratch=tmp_path / "scratch",
        )
    # Matching labels cannot make a different compiled payload a Runtime parent.
    fake_runtime = replace(
        runtime,
        payloads=(
            store.put_bytes("runtime/global-replay.js", b"// untrusted replacement"),
            runtime.payload("package_manifest"),
        ),
    )
    store.publish(fake_runtime)
    with pytest.raises(BoundaryError, match="original_runtime_parent_bytes"):
        global_store.publish_global_evidence(
            store,
            directory,
            producer,
            expected=expected,
            model_artifact_id=model.artifact_id,
            runtime_artifact_id=fake_runtime.artifact_id,
            scratch=tmp_path / "scratch",
        )
    bad_descriptor = json.loads(b"".join(store.read_payload(model.payload("package_manifest"))))
    bad_descriptor["input_spec"]["sha256"] = "0" * 64
    fake_model = replace(
        model,
        payloads=(
            model.payload("weights"),
            store.put_bytes("package_manifest", json_bytes(bad_descriptor)),
        ),
    )
    store.publish(fake_model)
    with pytest.raises(BoundaryError, match="original_model_descriptor_mismatch"):
        global_store.publish_global_evidence(
            store,
            directory,
            producer,
            expected=expected,
            model_artifact_id=fake_model.artifact_id,
            runtime_artifact_id=runtime.artifact_id,
            scratch=tmp_path / "scratch",
        )
    artifact = _publish(setup, tmp_path / "scratch")
    altered = replace(
        artifact,
        parameters=FrozenObject.of(
            {
                **artifact.parameters.value(),
                "tainted": True,
            }
        ),
    )
    store.publish(altered)
    with pytest.raises(BoundaryError, match="typed_original_binding_mismatch"):
        global_store.verify_global_artifact(
            store,
            altered.artifact_id,
            expected=expected,
            scratch=tmp_path / "scratch",
        )
    model_without_weights = Manifest("model", producer)
    store.publish(model_without_weights)
    with pytest.raises(BoundaryError):
        global_store.publish_global_evidence(
            store,
            directory,
            producer,
            expected=expected,
            model_artifact_id=model_without_weights.artifact_id,
            runtime_artifact_id=runtime.artifact_id,
            scratch=tmp_path / "scratch",
        )


def test_detached_model_has_unknown_ancestry_and_never_forges_training_parents(
    tmp_path, monkeypatch
):
    setup = _setup(tmp_path, monkeypatch)
    store, producer, _, _, model, _, _ = setup
    assert not model.parents
    assert model.parameters.value()["training_ancestry"] == "unknown_detached_engineering_export"
    (tmp_path / "model" / "model.tensor-tree").write_bytes(b"changed")
    with pytest.raises(BoundaryError, match="original_model_package_hash_mismatch"):
        global_store.publish_detached_p1_model(store, tmp_path / "model", producer)


def test_explicit_current_binding_preserves_package_input_and_rejects_other_projection(
    tmp_path,
    monkeypatch,
):
    from stpd.p1.input import INPUT_SPEC

    setup = _setup(tmp_path, monkeypatch)
    store, producer, directory, expected, model, runtime, _ = setup
    original = model.parameters.value()["input_spec"]
    current = {**original, "version": "2", "sha256": "c" * 64}
    path = directory / "agent-manifest.json"
    agent = json.loads(path.read_bytes())
    agent["input"]["input_spec"] = current
    path.write_bytes(json_bytes(agent))
    expected["model_binding"] = {
        "package_input_spec": original,
        "input_spec": current,
        "input_projection": INPUT_SPEC,
    }
    artifact = _publish(setup, tmp_path / "scratch")
    assert (
        global_store.verify_global_artifact(
            store,
            artifact.artifact_id,
            expected=expected,
            scratch=tmp_path / "scratch",
        )["typed_verification"]
        == "pass"
    )
    for field in ("package_input_spec", "input_projection"):
        previous = expected["model_binding"][field]
        expected["model_binding"][field] = {"arbitrary": "projection"}
        with pytest.raises(BoundaryError, match="trusted_model_binding_mismatch"):
            global_store.publish_global_evidence(
                store,
                directory,
                producer,
                expected=expected,
                model_artifact_id=model.artifact_id,
                runtime_artifact_id=runtime.artifact_id,
                scratch=tmp_path / "scratch",
            )
        expected["model_binding"][field] = previous


def _importer(setup, tmp_path, monkeypatch):
    from spireagent.workbench import local_recording_import as importing
    from spireagent.workbench.developer import ProjectConfig, combination

    store, producer, directory, expected, model, _, _ = setup
    expected['run_id'] = 'run-fixture'
    config = ProjectConfig(tmp_path / 'state', '', '', None, combination())
    config.state_dir.mkdir()
    monkeypatch.setattr(importing, '_selected_store', lambda *a, **k: (store, None))
    monkeypatch.setattr(importing, '_sync', lambda *a: None)
    monkeypatch.setattr(importing, 'source_identity', lambda root: producer)
    context = {'directory': str(directory), 'expected': expected,
               'model_artifact_id': model.artifact_id}
    return importing.LocalRecordingImporter(config, None), context


def test_original_import_owner_transfers_and_reads_global_with_frozen_trust(tmp_path, monkeypatch):
    setup = _setup(tmp_path, monkeypatch)
    importer, context = _importer(setup, tmp_path, monkeypatch)
    peer = {'kind': 'local', 'directory': str(tmp_path / 'peer')}
    started = importer.start_global_evidence(context, peer)
    assert '_global_context' not in started
    assert 'directory' not in started
    importer.thread.join(10)
    assert not importer.thread.is_alive()
    status = importer.status()
    assert status['status'] == 'completed'
    assert status['local_readback']['typed_verification'] == 'pass'
    assert status['peer_readback']['typed_verification'] == 'pass'
    original_id = status['artifact_id']

    def unexpected(*args, **kwargs):
        raise AssertionError('reconcile repeated a publication')

    monkeypatch.setattr(global_store, 'publish_global_evidence', unexpected)
    monkeypatch.setattr(global_store, 'publish_global_runtime', unexpected)
    assert importer.reconcile_global_evidence()['artifact_id'] == original_id


def test_global_typed_rejection_before_writes_remains_known_failure(tmp_path, monkeypatch):
    setup = _setup(tmp_path, monkeypatch)
    importer, context = _importer(setup, tmp_path, monkeypatch)
    before = set(setup[0].manifest_ids())
    monkeypatch.setattr(evidence, 'verify_agent_session_run_evidence',
                        lambda *a: SimpleNamespace(passed=False, value=None,
                            findings=[SimpleNamespace(code='known-invalid-original')]))
    importer.start_global_evidence(context)
    importer.thread.join(10)
    assert importer.status()['status'] == 'failed'
    assert importer.status()['error_code'] == 'typed_global_verification_failed'
    assert set(setup[0].manifest_ids()) == before


def test_global_postpublication_unknown_survives_restart_and_only_reads_original_id(
    tmp_path, monkeypatch,
):
    from spireagent.workbench.local_recording_import import LocalRecordingImporter

    setup = _setup(tmp_path, monkeypatch)
    importer, context = _importer(setup, tmp_path, monkeypatch)
    verify = global_store.verify_global_artifact

    def unreadable(*args, **kwargs):
        raise StoreError('s3_get_failed')

    monkeypatch.setattr(global_store, 'verify_global_artifact', unreadable)
    importer.start_global_evidence(context)
    importer.thread.join(10)
    assert importer.status()['status'] == 'publication_unknown'
    assert importer.status()['failure_phase'] == 'local_readback'
    identity = importer.status()['artifact_id']
    reopened = LocalRecordingImporter(importer.config, None)
    with pytest.raises(BoundaryError, match='original_intent_reconciliation_required'):
        reopened.start_global_evidence(context)
    with pytest.raises(BoundaryError, match='original_intent_reconciliation_required'):
        reopened.start_p1_derivation('a' * 64, 'native-timeline-targets-1')
    monkeypatch.setattr(global_store, 'verify_global_artifact', verify)

    def unexpected(*args, **kwargs):
        raise AssertionError('reconcile repeated a publication')

    monkeypatch.setattr(global_store, 'publish_global_evidence', unexpected)
    monkeypatch.setattr(global_store, 'publish_global_runtime', unexpected)
    assert reopened.reconcile_global_evidence()['status'] == 'completed'
    assert reopened.status()['artifact_id'] == identity


def test_global_manifest_ack_loss_keeps_intended_id_before_publication(tmp_path, monkeypatch):
    from spireagent.workbench.local_recording_import import LocalRecordingImporter

    setup = _setup(tmp_path, monkeypatch)
    importer, context = _importer(setup, tmp_path, monkeypatch)
    store = setup[0]
    publish = store.publish
    published = []

    def publish_with_lost_ack(manifest):
        identity = publish(manifest)
        if manifest.kind == 'evidence':
            published.append(identity)
            raise OSError('deliberate manifest persisted but ACK lost')
        return identity

    monkeypatch.setattr(store, 'publish', publish_with_lost_ack)
    importer.start_global_evidence(context)
    importer.thread.join(10)
    assert len(published) == 1
    assert importer.status()['status'] == 'publication_unknown'
    assert importer.status()['artifact_id'] == published[0]
    reopened = LocalRecordingImporter(importer.config, None)
    assert reopened.reconcile_global_evidence()['status'] == 'completed'
    assert reopened.status()['artifact_id'] == published[0]
    assert len(published) == 1  # No publication retry during reconciliation.


def test_global_peer_delivery_store_error_is_unknown_and_cannot_redispatch(tmp_path, monkeypatch):
    from spireagent.storage import store as storage

    setup = _setup(tmp_path, monkeypatch)
    importer, context = _importer(setup, tmp_path, monkeypatch)
    deliveries = []

    def failed_delivery(*args, **kwargs):
        deliveries.append(args[-1])
        raise StoreError('s3_put_failed')

    monkeypatch.setattr(storage, 'copy_artifact', failed_delivery)
    peer = {'kind': 'local', 'directory': str(tmp_path / 'peer')}
    importer.start_global_evidence(context, peer)
    importer.thread.join(10)
    assert importer.status()['status'] == 'publication_unknown'
    assert importer.status()['failure_phase'] == 'peer_delivery'
    with pytest.raises(BoundaryError, match='original_intent_reconciliation_required'):
        importer.start_global_evidence(context, peer)
    assert len(deliveries) == 1


@pytest.mark.parametrize("concurrency", [2, 8])
def test_parallel_readback_is_bounded_fresh_and_reads_all_parent_payloads(
    tmp_path, monkeypatch, concurrency,
):
    from collections import Counter
    from threading import Barrier, Lock

    setup = _setup(tmp_path, monkeypatch)
    store, _, _, expected, model, runtime, _ = setup
    manifest = _publish(setup, tmp_path / "scratch")
    peer = ManifestArtifactStore(LocalBlobStore(tmp_path / "peer"))
    copy_artifact(store, peer, manifest.artifact_id)
    original = peer.read_payload
    counts = Counter()
    mutex = Lock()
    active = peak = 0
    barrier = Barrier(2)
    concurrent = {model.payload("weights").sha256, runtime.payloads[0].sha256}

    def observed(payload):
        nonlocal active, peak
        with mutex:
            counts[payload.sha256] += 1
            active += 1
            peak = max(peak, active)
        try:
            if payload.sha256 in concurrent:
                barrier.wait(timeout=5)
            yield from original(payload)
        finally:
            with mutex:
                active -= 1

    monkeypatch.setattr(peer, "read_payload", observed)
    for _ in range(2):
        result = global_store.verify_global_artifact(peer, manifest.artifact_id,
            expected=expected, scratch=tmp_path / "scratch", read_concurrency=concurrency)
        assert result["full_readback"] and result["typed_verification"] == "pass"
    assert 2 <= peak <= concurrency
    assert counts == Counter({p.sha256: 2 for m in (manifest, model, runtime) for p in m.payloads})
    assert not list((tmp_path / "scratch").glob("verified-read-*"))


@pytest.mark.parametrize("failure", ["missing", "tampered", "rate_limited", "wrong_parent"])
def test_parallel_readback_rejects_new_remote_failures_and_wrong_parent(
    tmp_path, monkeypatch, failure,
):
    from spireagent.artifact_contracts import Parent

    setup = _setup(tmp_path, monkeypatch)
    store, _, _, expected, model, runtime, _ = setup
    manifest = _publish(setup, tmp_path / "scratch")
    peer = ManifestArtifactStore(LocalBlobStore(tmp_path / "peer"))
    copy_artifact(store, peer, manifest.artifact_id)
    global_store.verify_global_artifact(peer, manifest.artifact_id, expected=expected,
        scratch=tmp_path / "scratch", read_concurrency=8)
    if failure == "wrong_parent":
        wrong = replace(runtime, parameters=FrozenObject.of({
            **runtime.parameters.value(), "runtime_code_sha256": "0" * 64}))
        peer.publish(wrong)
        changed = replace(manifest, parents=(Parent("model", model.artifact_id),
                                             Parent("runtime", wrong.artifact_id)))
        peer.publish(changed)
        identity = changed.artifact_id
        reason = "original_runtime_parent_mismatch"
    else:
        identity = manifest.artifact_id
        payload = runtime.payloads[0]
        index = json.loads(peer.blobs.get(f"payload-indexes/v1/{payload.sha256}.json"))
        key = "objects/sha256/" + index["chunks"][0]["sha256"]
        original = peer.blobs.get

        def failing(name):
            if name == key:
                if failure == "tampered":
                    return b"corrupted remote bytes"
                raise StoreError("s3_throttled" if failure == "rate_limited"
                                 else "object_not_found")
            return original(name)

        monkeypatch.setattr(peer.blobs, "get", failing)
        reason = {"missing": "object_not_found", "tampered": "integrity",
                  "rate_limited": "s3_throttled"}[failure]
    with pytest.raises((BoundaryError, StoreError), match=reason):
        global_store.verify_global_artifact(peer, identity, expected=expected,
            scratch=tmp_path / "scratch", read_concurrency=8)
    assert not list((tmp_path / "scratch").glob("verified-read-*"))


@pytest.mark.parametrize("phase", ["copy_peer", "peer_readback"])
def test_managed_preservation_failure_keeps_original_and_exact_resume_identity(
    tmp_path, monkeypatch, phase,
):
    from spireagent.workbench import managed_evaluation as backend

    setup = _setup(tmp_path, monkeypatch)
    store, producer, directory, expected, model, _, _ = setup
    expected["run_id"] = "run-fixture"
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    executor = backend.ManagedEpisodeExecutor.__new__(backend.ManagedEpisodeExecutor)
    executor.store = store
    executor.peer = ManifestArtifactStore(LocalBlobStore(tmp_path / "peer"))
    executor.producer = producer
    executor.preparation = {"store": str(store.blobs.root)}
    executor.configuration = {"peer": {"kind": "local", "directory": str(tmp_path / "peer")}}
    executor.read_concurrency = 8
    written = {}
    checkpoints = []

    class Artifacts:
        def put(self, name, raw):
            written[name] = json.loads(raw)
            return {"path": name}

    def fail(*args, **kwargs):
        raise StoreError("s3_throttled")

    monkeypatch.setattr(backend, "copy_artifact" if phase == "copy_peer"
                        else "verify_global_artifact", fail)
    row = {}
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(StoreError, match="s3_throttled"):
        executor._preserve(row, Artifacts(), lambda: checkpoints.append(dict(row)),
            directory, expected, {"artifact_id": model.artifact_id}, scratch)
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    assert "stored_evidence_id" not in row and "preservation" not in row
    assert checkpoints[-1]["preservation_artifact_id"] == row["preservation_artifact_id"]
    pending = written["preservation-pending.json"]
    assert pending["expected"] == expected
    assert pending["artifact_id"] == row["preservation_artifact_id"]
    assert phase in row["detail_seconds"]
    # Restore this exact save operation, without a second game or changed original.
    copy_artifact(store, executor.peer, pending["artifact_id"])
    result = global_store.verify_global_artifact(executor.peer, pending["artifact_id"],
        expected=pending["expected"], scratch=scratch, read_concurrency=8)
    assert result["full_readback"] and result["run_id"] == "run-fixture"
