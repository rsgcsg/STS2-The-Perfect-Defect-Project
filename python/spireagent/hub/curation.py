"""Hub adapter for the shared research ledger, preserving the historical import/API."""

from __future__ import annotations

import sqlite3

from spireagent.hub.database import Operations
from spireagent.research_curation import PURPOSES as PURPOSES
from spireagent.research_curation import CurationLedger as SharedCurationLedger


def _hub_inventory_pending(db: sqlite3.Connection) -> bool:
    """Check every verified Hub upload in the claim's writer transaction."""
    return (
        db.execute(
            "SELECT 1 FROM uploads u WHERE u.status='verified' AND NOT EXISTS "
            "(SELECT 1 FROM curation_sources s WHERE "
            "s.id=json_extract(u.receipt,'$.evidence_id') AND s.complete=1) LIMIT 1"
        ).fetchone()
        is not None
    )


class CurationLedger(SharedCurationLedger):
    def __init__(self, operations: Operations) -> None:
        super().__init__(operations, inventory_pending=_hub_inventory_pending)
