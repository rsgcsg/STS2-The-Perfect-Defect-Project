"""Synthetic full-closure transfer and destination readback; no training/compute."""
from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from test_p1_snapshot import explicit_snapshot, projection_fixture, store_at

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.storage.blobs import StoreError
from spireagent.workbench import p1_recording_storage
from spireagent.workbench.p1_recording_storage import open_peer
from spireagent.workbench.remote_training_storage import transfer_p1_input
from stpd.p1.dataset import iter_snapshot_points
from stpd.p1.spec import MStdConfig
from stpd.p1.workload import prepare_p1_run, verify_p1_run


def inputs(tmp_path):
    local = store_at(tmp_path / "source")
    projections = [projection_fixture(local, "transfer-" + str(i))[0] for i in range(3)]
    snapshot = explicit_snapshot(local, projections)
    run = prepare_p1_run(local, snapshot.artifact_id, snapshot.producer,
                         MStdConfig(max_epochs=2), "a" * 32)
    peer_binding = {"kind": "local", "directory": str(tmp_path / "peer")}
    return local, run, peer_binding


def test_full_original_input_vocabulary_closure_and_all_N_read_from_reopened_peer(tmp_path):
    local, run, binding = inputs(tmp_path)
    before = local.manifest_ids()
    facts = transfer_p1_input(local, binding, run.artifact_id, run.producer,
                              scratch=tmp_path / "scratch")
    peer = open_peer(binding)
    assert set(peer.manifest_ids()) == set(before)
    for identity in before:
        original = local.get_manifest(identity)
        assert peer.get_manifest(identity) == original
        for payload in original.payloads:
            assert b"".join(peer.read_payload(payload)) == b"".join(local.read_payload(payload))
    checked = verify_p1_run(peer, run.artifact_id, run.producer)
    assert len(list(iter_snapshot_points(peer, checked.snapshot.artifact_id))) == 3
    assert facts["unique_N"] == 3 and facts["catalog_choices"] == 6
    assert facts["partition_N"] == {"train": 1, "dev": 1, "test": 1}
    assert facts["vocabulary_sha256"] == checked.training.payload("vocabulary").sha256
    assert facts["compute_store"] == binding and facts["provider_submitted"] is False
    assert facts["closure_payload_readback"] == "verified"
    assert facts["budget"]["lineage_manifests"] == len(before)
    assert local.manifest_ids() == before
    # The exact immutable preparation can be explicitly read/copy verified again.
    repeated = transfer_p1_input(local, binding, run.artifact_id, run.producer,
                                 scratch=tmp_path / "scratch")
    assert repeated["run_id"] == facts["run_id"]
    assert repeated["vocabulary_sha256"] == facts["vocabulary_sha256"]


def test_existing_corrupt_remote_original_is_not_healed_or_reported_prepared(tmp_path):
    local, run, binding = inputs(tmp_path)
    transfer_p1_input(local, binding, run.artifact_id, run.producer,
                      scratch=tmp_path / "scratch")
    peer = open_peer(binding)
    original = next(peer.get_manifest(i) for i in peer.manifest_ids()
                    if any(p.role == "archive" for p in peer.get_manifest(i).payloads))
    payload = original.payload("archive")
    original_bytes = b"".join(local.read_payload(payload))
    corrupted = tmp_path / "peer" / "objects" / "sha256" / payload.sha256
    corrupted.write_bytes(b"corrupt")
    with pytest.raises(StoreError, match="payload_chunk_integrity_failure"):
        transfer_p1_input(local, binding, run.artifact_id, run.producer,
                          scratch=tmp_path / "scratch")
    assert corrupted.read_bytes() == b"corrupt"
    assert b"".join(local.read_payload(payload)) == original_bytes


def test_reception_budgets_missing_bytes_without_reading_present_parent_payloads(
    tmp_path, monkeypatch
):
    local, run, binding = inputs(tmp_path)
    transfer_p1_input(local, binding, run.artifact_id, run.producer,
                      scratch=tmp_path / "scratch")
    peer = open_peer(binding)
    payload = peer.put_payload("new-result", io.BytesIO(b"x" * 20))
    result = Manifest("model", run.producer, (Parent("run", run.artifact_id),), (payload,))
    peer.publish(result)
    # The input ancestry is already local. The capacity calculation reads only
    # small indexes and file sizes; final copy_artifact remains byte-verifying.
    monkeypatch.setattr(peer, "read_payload", lambda _: pytest.fail("budget read parent bytes"))
    monkeypatch.setattr(local, "read_payload", lambda _: pytest.fail("budget read local bytes"))
    reserve = 512 * 1024**2
    monkeypatch.setattr(p1_recording_storage.shutil, "disk_usage",
                        lambda _: SimpleNamespace(free=reserve + 40))
    facts = p1_recording_storage.storage_budget(
        peer, result.artifact_id, scratch=tmp_path / "scratch", destination=local.blobs.root,
        destination_store=local,
    )
    assert facts["persistent_budget_bytes"] == 20
    assert facts["scratch_budget_bytes"] == 20
    assert facts["required_available_bytes"] == reserve + 40
    assert facts["destination_estimate"] == (
        "missing_local_chunks_by_stat_then_transfer_verifies_bytes")


def test_reception_missing_chunk_reserves_full_temporary_but_only_missing_persistent(tmp_path):
    from test_artifact_store_v1 import PRODUCER

    from spireagent.storage.store import CHUNK_BYTES

    peer, local = store_at(tmp_path / "peer"), store_at(tmp_path / "local")
    raw = b"a" * CHUNK_BYTES + b"b" * 20
    payload = peer.put_payload("result", io.BytesIO(raw))
    local.put_payload("result", io.BytesIO(raw))
    result = Manifest("model", PRODUCER, payloads=(payload,))
    peer.publish(result)
    index = json.loads(local.blobs.get(f"payload-indexes/v1/{payload.sha256}.json"))
    local.blobs._path("objects/sha256/" + index["chunks"][-1]["sha256"]).unlink()
    facts = p1_recording_storage.storage_budget(
        peer, result.artifact_id, scratch=tmp_path / "scratch", destination=local.blobs.root,
        destination_store=local,
    )
    assert facts["persistent_budget_bytes"] == 20
    assert facts["scratch_budget_bytes"] == len(raw)
