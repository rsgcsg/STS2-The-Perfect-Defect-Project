"""Reward-pair command composition over the existing curation authority.

There is no alternate ledger, receipt cache, permit or inventory here. Every
owned boundary consumes the owner-held exact receipt and today's ledger.
"""

from __future__ import annotations

from typing import Any

from spireagent.json_boundary import BoundaryError
from stpd.fullrun.reward_m0_source import (
    VerifiedRewardM0Source,
    historical_binding,
    verify_reward_m0_source,
)


def require_historical_training(owner: Any, store: Any, historical: object) -> dict[str, Any]:
    binding = historical_binding(historical)
    return dict(
        owner.require_training_receipt(
            store, (binding["dataset_id"],), binding["training_operation_id"], binding["receipt_id"]
        )
    )


def require_selected_roster(
    owner: Any, source: VerifiedRewardM0Source, historical_summary: dict[str, Any]
) -> None:
    previous = historical_summary["datasets"]
    if len(previous) != 1 or previous[0]["artifact_id"] != source.value["dataset_id"]:
        raise BoundaryError("reward_m0_data", "historical_dataset_receipt_mismatch")
    runs = set(previous[0]["qualified_run_ids"])
    original_sources = {row["artifact_id"]: row["archive_sha256"] for row in previous[0]["sources"]}
    with owner.transaction() as db:
        for row in source.value["rows"]:
            if (
                row["run_id"] not in runs
                or original_sources.get(row["source_id"]) != row["source_archive_sha256"]
                or db.execute(
                    "SELECT run FROM curation_occurrences WHERE id=?", (row["occurrence_id"],)
                ).fetchone()
                != (row["run_id"],)
                or db.execute(
                    "SELECT transition_id FROM curation_source_decisions "
                    "WHERE source=? AND occurrence=?",
                    (row["source_id"], row["occurrence_id"]),
                ).fetchone()
                != (row["transition_id"],)
            ):
                raise BoundaryError("reward_m0_data", "current_original_roster_mismatch")
            annotation = db.execute(
                "SELECT action FROM curation_annotations WHERE occurrence=? "
                "ORDER BY sequence DESC LIMIT 1",
                (row["occurrence_id"],),
            ).fetchone()
            if annotation is not None and annotation[0] == "exclude":
                raise BoundaryError("reward_m0_data", "current_original_occurrence_excluded")


def require_reward_m0_source(
    owner: Any, store: Any, source_id: str, *, reserved: bool = True
) -> VerifiedRewardM0Source:
    manifest = store.get_manifest(source_id)
    # Read only the bounded typed artifact to locate its historical receipt.
    # The source owner then repeats exact selected original replay.
    from spireagent.json_boundary import decode_json
    from stpd.reward_m0_spec import MAX_SOURCE_BYTES, SOURCE_SCHEMA

    if (
        manifest.kind != "dataset"
        or manifest.parameters.value().get("schema") != SOURCE_SCHEMA
        or manifest.payload("source").size > MAX_SOURCE_BYTES
    ):
        raise BoundaryError("reward_m0_data", "typed_source_required")
    value = decode_json(b"".join(store.read_payload(manifest.payload("source"))))
    summary = require_historical_training(owner, store, value["historical_binding"])
    source = verify_reward_m0_source(store, source_id)
    require_selected_roster(owner, source, summary)
    if reserved and owner.ledger.dataset(source_id) != ("training", set(source.runs)):
        raise BoundaryError("reward_m0_data", "pair_not_reserved_for_training")
    return source


def reserve_reward_m0_source(owner: Any, store: Any, source_id: str) -> dict[str, Any]:
    source = require_reward_m0_source(owner, store, source_id, reserved=False)
    owner.ledger.claim(source_id, "training", source.runs)
    owner.ledger.bind(source_id, source_id)
    return {
        "source_id": source_id,
        "split_status": "reserved",
        "actual_training_use": False,
        "historical_external_exposure": "unknown",
    }


def record_reward_m0_use(owner: Any, store: Any, source_id: str, operation_id: str) -> None:
    source = require_reward_m0_source(owner, store, source_id)
    for original in sorted(source.source_ids):
        owner.ledger.use_source(original, "training", operation_id)
    owner.ledger.use(source.runs, "training", operation_id)
    require_reward_m0_use(owner, store, source_id, operation_id)


def require_reward_m0_use(owner: Any, store: Any, source_id: str, operation_id: str) -> None:
    source = require_reward_m0_source(owner, store, source_id)
    owner.ledger.require_training_use(source_id, source.source_ids, source.runs, operation_id)
