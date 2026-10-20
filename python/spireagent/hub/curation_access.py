"""Apply research reservations at managed byte/compute boundaries, including old exports."""

from __future__ import annotations

from spireagent.artifact_contracts import Manifest
from spireagent.hub.curation import CurationLedger
from spireagent.hub.database import Operations
from spireagent.json_boundary import BoundaryError
from spireagent.storage.store import ArtifactStore


def guarded_runs(
    operations: Operations,
    store: ArtifactStore,
    manifest: Manifest,
    *,
    training: bool = False,
    nodes: tuple[Manifest, ...] = (),
    sources: set[str] | None = None,
) -> set[str]:
    ledger = CurationLedger(operations)
    pending = [manifest]
    seen = set()
    runs: set[str] = set()
    has_gold = ledger.has_gold()
    memo = {item.artifact_id: item for item in nodes}

    def parent(identity: str) -> Manifest:
        if identity not in memo:
            memo[identity] = store.get_manifest(identity)
        return memo[identity]

    while pending:
        item = pending.pop()
        if item.artifact_id in seen:
            continue
        seen.add(item.artifact_id)
        if len(seen) > 512:
            raise BoundaryError("curation", "lineage_limit")
        info = item.parameters.value()
        if info.get("purpose") == "gold" or (training and info.get("purpose") == "test"):
            raise BoundaryError(
                "curation", "gold_reserved_data" if not training else "held_out_data_cannot_train"
            )
        selection = ledger.dataset(item.artifact_id)
        if selection is not None:
            purpose, selected_runs = selection
            if purpose == "gold" or (training and purpose == "test"):
                raise BoundaryError(
                    "curation", "held_out_data_cannot_train" if training else "gold_reserved_data"
                )
            runs.update(selected_runs)
            # Only the compact current receipt omits unselected source context.
            # Legacy reports can contain complete source accounting.
            if info.get("schema") == "stpd/curated-decision-dataset-v1":
                continue
        if info.get("schema") == "stpd/received-bundle-v1":
            if sources is not None:
                sources.add(item.artifact_id)
            source_runs = ledger.source_runs(item.artifact_id)
            if source_runs is None:
                if has_gold:
                    raise BoundaryError("curation", "source_isolation_index_pending")
            else:
                runs.update(source_runs)
            continue
        pending.extend(parent(p.artifact_id) for p in item.parents)
    with operations.transaction() as db:
        related = ledger._groups(db, runs)
        if any(purpose == "gold" for purpose, _ in ledger._claims(db, related).values()):
            raise BoundaryError("curation", "gold_reserved_data")
    return runs


def record_use(
    operations: Operations,
    store: ArtifactStore,
    manifest: Manifest,
    kind: str,
    *,
    nodes: tuple[Manifest, ...] = (),
) -> None:
    from stpd.p1.spec import RUN_SCHEMA as P1_RUN_SCHEMA

    if kind == "training" and manifest.parameters.value().get("schema") == P1_RUN_SCHEMA:
        from spireagent.p1_curation import record_p1_training_use
        from stpd.p1.workload import verify_p1_run

        workload = verify_p1_run(store, manifest.artifact_id, manifest.producer)
        record_p1_training_use(
            CurationLedger(operations), store, workload.snapshot.artifact_id,
            workload.run.parameters.value()["operation_id"],
        )
        return
    sources: set[str] = set()
    runs = guarded_runs(
        operations, store, manifest, training=kind == "training", nodes=nodes, sources=sources
    )
    ledger = CurationLedger(operations)
    ledger.use(runs, kind, manifest.artifact_id)
    for source in sources:
        ledger.use_source(source, kind, manifest.artifact_id)
