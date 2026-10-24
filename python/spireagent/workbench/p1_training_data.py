"""Local Store-owner binding for the shared P1 CurationLedger allocation/use rules."""
from __future__ import annotations

from typing import Any

from spireagent import p1_curation
from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore


def _ledger(owner: Any, store: Any) -> Any:
    if not isinstance(store.blobs, LocalBlobStore) or store.blobs.root != owner.store_dir.resolve():
        raise BoundaryError("p1_training_data", "store_owner_mismatch")
    return owner.ledger


def reserve_p1_snapshot(owner: Any, store: Any, snapshot_id: str) -> dict[str, Any]:
    return p1_curation.reserve_p1_snapshot(_ledger(owner, store), store, snapshot_id)


def require_p1_snapshot(
    owner: Any, store: Any, snapshot_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return p1_curation.require_p1_snapshot(_ledger(owner, store), store, snapshot_id)


def record_p1_training_use(owner: Any, store: Any, snapshot_id: str, operation_id: str) -> None:
    p1_curation.record_p1_training_use(_ledger(owner, store), store, snapshot_id, operation_id)


def require_p1_training_use(owner: Any, store: Any, snapshot_id: str, operation_id: str) -> None:
    p1_curation.require_p1_training_use(_ledger(owner, store), store, snapshot_id, operation_id)



def p1_read_payloads(
    store: Any, snapshot_id: str, *, run_id: str | None = None,
    checkpoint_id: str | None = None, initial_checkpoint: bool = False,
) -> tuple[Any, ...]:
    """Plan the existing P1 consumer's named reads using only manifest metadata."""
    from stpd.p1.dataset import projection_payload_role
    from stpd.p1.derivation import source_checkpoint_dag

    payloads = {}
    snapshots, runs, checkpoints = {snapshot_id}, set(), set()
    if run_id is not None:
        runs.add(run_id)
    if checkpoint_id is not None:
        checkpoints.add(checkpoint_id)
    while checkpoints:
        identity = checkpoints.pop()
        checkpoint = store.get_manifest(identity)
        payload = checkpoint.payload("checkpoint")
        payloads[payload.sha256] = payload
        runs.add(checkpoint.parent("run"))
        original = [p.artifact_id for p in checkpoint.parents if p.role == "original_checkpoint"]
        checkpoints.update(original)
        if initial_checkpoint:
            run = store.get_manifest(checkpoint.parent("run"))
            for saved in source_checkpoint_dag(store, identity, run):
                payload = saved.payload("checkpoint")
                payloads[payload.sha256] = payload
    for identity in runs:
        run = store.get_manifest(identity)
        training = store.get_manifest(run.parent("training_input"))
        for payload in training.payloads:
            payloads[payload.sha256] = payload
        snapshots.add(training.parent("source"))
    for identity in snapshots:
        snapshot = store.get_manifest(identity)
        selection = snapshot.payload("selection")
        payloads[selection.sha256] = selection
        for parent in snapshot.parents:
            projection = store.get_manifest(parent.artifact_id)
            payload = projection.payload(projection_payload_role(projection))
            payloads[payload.sha256] = payload
    return tuple(payloads.values())
