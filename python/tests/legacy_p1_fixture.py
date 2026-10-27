"""Explicit pre-upgrade snapshots for historical compatibility tests only.

Production imports neither this fixture nor a legacy creation bypass. The migration
normally captures these identities once from the pre-existing Curation claims.
"""

from spireagent import p1_curation
from spireagent.workbench.p1_data_preparation import freeze_p1_data as _freeze
from spireagent.workbench.p1_training_data import reserve_p1_snapshot as _reserve


def reserve_p1_snapshot(owner, store, snapshot_id):
    ledger = getattr(owner, "ledger", owner)
    with ledger.operations.transaction() as db:
        db.execute("INSERT OR IGNORE INTO curation_p1_legacy_snapshots VALUES(?)", (snapshot_id,))
    if hasattr(owner, "ledger"):
        return _reserve(owner, store, snapshot_id)
    return p1_curation.reserve_p1_snapshot(ledger, store, snapshot_id)


def freeze_p1_data(owner, store, producer, **kwargs):
    if (
        type(kwargs.get("reserve")) is not bool
        or not kwargs.get("reserve")
        or kwargs.get("experiment") is not None
    ):
        return _freeze(owner, store, producer, **kwargs)
    preview = _freeze(owner, store, producer, **{**kwargs, "reserve": False})
    reserve_p1_snapshot(owner, store, preview["snapshot_id"])
    return _freeze(owner, store, producer, **kwargs)
