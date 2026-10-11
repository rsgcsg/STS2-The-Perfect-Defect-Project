"""Historical M0 composition through the configured Curation owner, no new ledger."""

from __future__ import annotations

import hashlib
from importlib.metadata import version
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes, object_fields
from spireagent.package_identity import file_sha256
from stpd.canonical import semantic_hash
from stpd.fullrun.historical_public_m0 import (
    VerifiedHistoricalRelation,
    compile_relation,
    originals,
    read_relation,
    relation_manifest,
    relation_reference,
    verify_selected_human_encoding,
)
from stpd.historical_m0_spec import HISTORICAL_PUBLIC_PROOF_KIND, HISTORICAL_PUBLIC_RECEIPT_SCHEMA

_PROOF_FILES = (
    "stpd/fullrun/historical_public_m0.py",
    "stpd/fullrun/light_action_row_storage.py",
    "stpd/fullrun/public_bc.py",
    "stpd/fullrun/public_inputs.py",
    "stpd/fullrun/public_compaction.py",
    "stpd/fullrun/platform_bundle3.py",
    "stpd/fullrun/contracts.py",
    "stpd/fullrun/token_format.py",
    "stpd/light_action_codec.py",
    "stpd/historical_m0_spec.py",
    "stpd/canonical.py",
    "spireagent/workbench/historical_m0_data.py",
    "spireagent/json_boundary.py",
    "spireagent/artifact_contracts.py",
    "spireagent/storage/archives.py",
    "spireagent/storage/store.py",
    "spireagent/storage/local.py",
    "uv.lock",
)


def historical_public_compatibility() -> str:
    from stpd.fullrun.decision_cache import implementation_identity

    root = Path(__file__).resolve().parents[2]
    return semantic_hash(
        {
            "proof_kind": HISTORICAL_PUBLIC_PROOF_KIND,
            "original_verifier": implementation_identity(),
            "tokenizers": version("tokenizers"),
            "files": [{"path": name, "sha256": file_sha256(root / name)} for name in _PROOF_FILES],
        }
    )


def _public_receipt_context(owner: Any, store: Any, value: dict[str, Any]) -> dict[str, Any]:
    binding = originals(value["originals"])
    # Canonical admission and permissions are current, never cached by this proof.
    require_original_admission(owner, store, binding)
    return {
        "schema": HISTORICAL_PUBLIC_RECEIPT_SCHEMA,
        "proof_kind": HISTORICAL_PUBLIC_PROOF_KIND,
        "owner": list(owner.identity),
        "originals": binding,
        "relation_sha256": semantic_hash(value),
        "selection_sha256": semantic_hash(value["members"]),
        "parent_payloads": value["parent_payloads"],
        "mapping_sha256": value["mapping_sha256"],
        "selected_rows_sha256": value["selected_rows_sha256"],
        "selected_samples_sha256": value["selected_samples_sha256"],
        "compatibility_sha256": historical_public_compatibility(),
        "scope": "selected_only_H_choice_and_frozen_encoding_no_full_view_replay",
    }


def prepare_historical_public_receipt(
    owner: Any, store: Any, binding: object, selection: object
) -> tuple[VerifiedHistoricalRelation, str]:
    """Explicit preparation, the only path that may read original selected archives.

    Reuses this configured owner's existing receipt table. Canonical-v2 receipt
    semantics and compatibility remain unchanged; this is a distinct closed context.
    """
    summary = require_original_admission(owner, store, binding)
    relation = compile_relation(store, binding, selection)
    require_original_roster(owner, relation, summary)
    sources = tuple(
        store.get_manifest(row["artifact_id"]) for row in summary["datasets"][0]["sources"]
    )
    qualified = verify_selected_human_encoding(store, relation, sources)
    context = _public_receipt_context(owner, store, qualified.value)
    raw = json_bytes(context)
    identity = hashlib.sha256(raw).hexdigest()
    key = semantic_hash(context)
    with owner.transaction() as db:
        # The prior required canonical receipt already owns/created this table.
        db.execute(
            "INSERT OR IGNORE INTO local_training_projection_receipts VALUES(?,?,?)",
            (key, identity, raw.decode("utf-8")),
        )
        stored = db.execute(
            "SELECT identity FROM local_training_projection_receipts WHERE context=?", (key,)
        ).fetchone()
        if stored != (identity,):
            raise BoundaryError("historical_m0_data", "historical_public_receipt_conflict")
    require_historical_public_receipt(owner, store, qualified.value, identity)
    return qualified, identity


def require_historical_public_receipt(
    owner: Any, store: Any, value: dict[str, Any], receipt_id: str
) -> None:
    """Consume exact selected proof/current authority; never prepares on a miss."""
    digest(receipt_id, "historical_m0.selected_H_receipt")
    expected = _public_receipt_context(owner, store, value)
    key = semantic_hash(expected)
    with owner.transaction() as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='local_training_projection_receipts'"
        ).fetchone()
        row = (
            db.execute(
                "SELECT identity,receipt FROM local_training_projection_receipts WHERE context=?",
                (key,),
            ).fetchone()
            if exists
            else None
        )
    if (
        row is None
        or row[0] != receipt_id
        or hashlib.sha256(row[1].encode("utf-8")).hexdigest() != receipt_id
    ):
        raise BoundaryError(
            "historical_m0_data", "historical_public_input_explicit_prepare_required"
        )
    actual = object_fields(decode_json(row[1]), set(expected), "historical_m0.selected_H_receipt")
    if actual != expected:
        raise BoundaryError("historical_m0_data", "historical_public_receipt_context_mismatch")


def require_original_admission(owner: Any, store: Any, value: object) -> dict[str, Any]:
    """Consume the existing exact original/canonical proof, not a fresh H replay."""
    binding = originals(value)
    try:
        summary = owner.require_training_receipt(
            store,
            (binding["dataset_id"],),
            binding["admission_operation_id"],
            binding["admission_receipt_id"],
        )
    except BoundaryError as error:
        raise BoundaryError(
            "historical_m0_data", "historical_input_source_verification_required"
        ) from error
    datasets = summary.get("datasets")
    manifest = store.get_manifest(binding["dataset_id"])
    if (
        not isinstance(datasets, list)
        or len(datasets) != 1
        or datasets[0].get("artifact_id") != manifest.artifact_id
        or datasets[0].get("logical_id") != manifest.parameters.value().get("logical_id")
        or summary.get("operation_id") != binding["admission_operation_id"]
    ):
        raise BoundaryError("historical_m0_data", "original_admission_parent_mismatch")
    return dict(summary)


def require_original_roster(
    owner: Any, relation: VerifiedHistoricalRelation, summary: dict[str, Any]
) -> None:
    bound = summary["datasets"][0]
    runs = set(bound["qualified_run_ids"])
    source_by_archive = {row["archive_sha256"]: row["artifact_id"] for row in bound["sources"]}
    if len(source_by_archive) != len(bound["sources"]):
        raise BoundaryError("historical_m0_data", "original_source_alias")
    with owner.transaction() as db:
        for row in relation.value["members"]:
            source = source_by_archive.get(row["source_archive_sha256"])
            if (
                row["run_id"] not in runs
                or source is None
                or db.execute(
                    "SELECT run FROM curation_occurrences WHERE id=?", (row["occurrence"],)
                ).fetchone()
                != (row["run_id"],)
                or db.execute(
                    "SELECT transition_id FROM curation_source_decisions "
                    "WHERE source=? AND occurrence=?",
                    (source, row["occurrence"]),
                ).fetchone()
                != (row["transition_id"],)
            ):
                raise BoundaryError("historical_m0_data", "current_original_roster_mismatch")
            annotation = db.execute(
                "SELECT action FROM curation_annotations WHERE occurrence=? "
                "ORDER BY sequence DESC LIMIT 1",
                (row["occurrence"],),
            ).fetchone()
            if annotation is not None and annotation[0] == "exclude":
                raise BoundaryError("historical_m0_data", "current_original_occurrence_excluded")


def publish_historical_source(
    owner: Any,
    store: Any,
    binding: object,
    selection: object,
    producer: Producer,
    operation_id: str,
) -> str:
    """Explicit preparation command; no source/use mutation until both proofs pass."""
    digest(operation_id, "historical_m0.source_operation", length=32)
    relation, public_receipt = prepare_historical_public_receipt(owner, store, binding, selection)
    summary = require_original_admission(owner, store, binding)
    # Conservative preparation exposure precedes the first derivative publication.
    # It is not an optimizer/update or completion receipt.
    bound = summary["datasets"][0]
    for source in bound["sources"]:
        owner.ledger.use_source(source["artifact_id"], "training", operation_id)
    owner.ledger.use(bound["qualified_run_ids"], "training", operation_id)
    manifest = relation_manifest(store, relation, producer, public_receipt)
    owner.ledger.claim(manifest.artifact_id, "training", bound["qualified_run_ids"])
    owner.ledger.bind(manifest.artifact_id, manifest.artifact_id)
    return str(manifest.artifact_id)


def require_historical_source(
    owner: Any, store: Any, identity: str
) -> tuple[VerifiedHistoricalRelation, dict[str, Any]]:
    manifest, value = read_relation(store, identity)
    summary = require_original_admission(owner, store, value.get("originals"))
    require_historical_public_receipt(
        owner, store, value, manifest.parameters.value()["historical_public_receipt_id"]
    )
    relation = relation_reference(store, identity)
    require_original_roster(owner, relation, summary)
    runs = set(summary["datasets"][0]["qualified_run_ids"])
    if owner.ledger.dataset(identity) != ("training", runs):
        raise BoundaryError("historical_m0_data", "historical_source_not_reserved")
    return relation, summary


def record_historical_use(owner: Any, store: Any, identity: str, operation_id: str) -> None:
    manifest, value = read_relation(store, identity)
    summary = require_original_admission(owner, store, value.get("originals"))
    require_historical_public_receipt(
        owner, store, value, manifest.parameters.value()["historical_public_receipt_id"]
    )
    bound = summary["datasets"][0]
    if owner.ledger.dataset(identity) != ("training", set(bound["qualified_run_ids"])):
        raise BoundaryError("historical_m0_data", "historical_source_not_reserved")
    for source in bound["sources"]:
        owner.ledger.use_source(source["artifact_id"], "training", operation_id)
    owner.ledger.use(bound["qualified_run_ids"], "training", operation_id)
    require_historical_use(owner, store, identity, operation_id)


def require_historical_use(
    owner: Any, store: Any, identity: str, operation_id: str
) -> dict[str, Any]:
    manifest, value = read_relation(store, identity)
    summary = require_original_admission(owner, store, value.get("originals"))
    require_historical_public_receipt(
        owner, store, value, manifest.parameters.value()["historical_public_receipt_id"]
    )
    bound = summary["datasets"][0]
    owner.ledger.require_training_use(
        identity,
        (row["artifact_id"] for row in bound["sources"]),
        bound["qualified_run_ids"],
        operation_id,
    )
    return summary
