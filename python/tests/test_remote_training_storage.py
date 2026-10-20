"""Synthetic full-closure transfer and destination readback; no training/compute."""
from __future__ import annotations

import pytest
from test_p1_snapshot import explicit_snapshot, projection_fixture, store_at

from spireagent.storage.blobs import StoreError
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
    with pytest.raises(StoreError, match="payload_integrity_failure"):
        transfer_p1_input(local, binding, run.artifact_id, run.producer,
                          scratch=tmp_path / "scratch")
    assert corrupted.read_bytes() == b"corrupt"
    assert b"".join(local.read_payload(payload)) == original_bytes
