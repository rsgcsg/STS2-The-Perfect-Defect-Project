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


def reserve_p1_snapshot(owner: Any, store: Any, snapshot_id: str, *,
                        experiment: dict[str, Any] | None = None) -> dict[str, Any]:
    return p1_curation.reserve_p1_snapshot(_ledger(owner, store), store, snapshot_id,
                                         experiment=experiment)


def require_p1_snapshot(
    owner: Any, store: Any, snapshot_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return p1_curation.require_p1_snapshot(_ledger(owner, store), store, snapshot_id)


def record_p1_training_use(owner: Any, store: Any, snapshot_id: str, operation_id: str, *,
                           loss_weighting: dict[str, Any] | None = None,
                           initial_checkpoint: str | None = None) -> None:
    p1_curation.record_p1_training_use(_ledger(owner, store), store, snapshot_id, operation_id,
                                     loss_weighting=loss_weighting,
                                     initial_checkpoint=initial_checkpoint)


def require_p1_training_use(owner: Any, store: Any, snapshot_id: str, operation_id: str) -> None:
    p1_curation.require_p1_training_use(_ledger(owner, store), store, snapshot_id, operation_id)



def p1_read_payloads(
    store: Any, snapshot_id: str, *, run_id: str | None = None,
    checkpoint_id: str | None = None, initial_checkpoint: bool = False,
) -> tuple[Any, ...]:
    from stpd.p1.workload import read_payloads

    return read_payloads(store, snapshot_id, run_id=run_id, checkpoint_id=checkpoint_id,
                         initial_checkpoint=initial_checkpoint)
