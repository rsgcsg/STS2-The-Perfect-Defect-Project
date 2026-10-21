"""Exact P1 input transfer on the existing immutable Store, without compute."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Producer
from spireagent.storage.store import ManifestArtifactStore, copy_artifact
from spireagent.workbench.p1_recording_storage import open_peer, storage_budget


def transfer_p1_input(
    local: ManifestArtifactStore,
    peer_binding: dict[str, Any],
    run_id: str,
    producer: Producer,
    *,
    scratch: Path,
) -> dict[str, Any]:
    """Copy/read the full parent closure and consume every selected N at the peer.

    The caller owns purpose/use before vocabulary fitting, then creates the Run.
    This helper neither queues work nor submits a provider call. A failed copy
    stays visible; immutable partial objects never imply successful preparation.
    """
    from stpd.p1.dataset import iter_snapshot_points
    from stpd.p1.input import project
    from stpd.p1.workload import verify_p1_run

    budget = storage_budget(local, run_id, scratch=scratch, destination=None)
    peer = open_peer(peer_binding)
    copy_artifact(local, peer, run_id, scratch=scratch)
    # Reopen the configured destination and use its actual payload reader for
    # every byte, including original parents that are not themselves model input.
    readback = open_peer(peer_binding)
    copy_artifact(readback, readback, run_id, scratch=scratch)
    workload = verify_p1_run(readback, run_id, producer)
    partition_N: dict[str, int] = {}
    catalog_choices = 0
    for reference, observation in iter_snapshot_points(readback, workload.snapshot.artifact_id):
        frame = project(observation)
        partition = reference["split"]
        partition_N[partition] = partition_N.get(partition, 0) + 1
        catalog_choices += len(frame.choices)
    info = workload.training.parameters.value()
    return {
        "schema": "spireagent/p1-remote-input-readback-1",
        "run_id": run_id,
        "training_input_id": workload.training.artifact_id,
        "snapshot_id": workload.snapshot.artifact_id,
        "compute_store": dict(peer_binding),
        "input_spec": info["input_spec"],
        "vocabulary_sha256": info["vocabulary_sha256"],
        "partition_N": partition_N,
        "unique_N": sum(partition_N.values()),
        "catalog_choices": catalog_choices,
        "budget": budget,
        "closure_payload_readback": "verified",
        "provider_submitted": False,
        "gpu_qualification": "not_established",
    }
