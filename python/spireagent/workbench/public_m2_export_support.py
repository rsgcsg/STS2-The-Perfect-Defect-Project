"""Public M2 package admission through the existing model-export application."""

from __future__ import annotations

from typing import Any

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, digest, object_fields

OPERATION_SCHEMA = "stpd/local-model-export-operation-v4"
MODEL_SCHEMA = "stpd/public-m2-model-v1"
PROFILE = "public-snapshot-m2-v1"
LINEAGE_KEYS = {
    "run_id", "input_id", "checkpoint_id", "checkpoint_sha256", "view_id",
    "allocation_id", "dataset_id", "training_operation_id", "stage_id",
    "evaluation_id", "completion_id",
}


def checked_operation(value: dict[str, Any]) -> None:
    """A completed receipt remains a byte proof, never current-use authority."""
    if (value.get("schema") != OPERATION_SCHEMA or value.get("model_type") != "public_m2"
            or value.get("profile") != PROFILE):
        raise BoundaryError("local_model_export", "public_m2_operation_invalid")
    lineage = object_fields(value.get("lineage"), LINEAGE_KEYS, "public_m2.export.lineage")
    for key, identity in lineage.items():
        if key == "completion_id" and identity is None:
            continue
        digest(identity, "public_m2.export." + key,
               length=32 if key == "training_operation_id" else 64)
    receipt = value.get("verified_receipt")
    if receipt is None:
        if value.get("status") == "completed":
            raise BoundaryError("local_model_export", "verified_export_receipt_required")
        return
    receipt = object_fields(receipt, {
        "schema", "model_id", *LINEAGE_KEYS, "package_sha256", "payload_sha256",
        "payload_sizes", "payload_bytes",
    }, "public_m2.export.receipt")
    if (value.get("status") != "completed"
            or receipt["schema"] != "stpd/public-m2-export-verification-v1"
            or receipt["model_id"] != value.get("model_id")
            or any(receipt[key] != lineage[key] for key in LINEAGE_KEYS)
            or receipt["payload_bytes"] != value.get("payload_bytes")):
        raise BoundaryError("local_model_export", "public_m2_operation_invalid")
    digest(receipt["package_sha256"], "public_m2.export.package")
    hashes = object_fields(receipt["payload_sha256"], {"weights", "state_tokenizer"},
                           "public_m2.export.hashes")
    sizes = object_fields(receipt["payload_sizes"], {"weights", "state_tokenizer"},
                          "public_m2.export.sizes")
    for key in hashes:
        digest(hashes[key], "public_m2.export." + key)
        if type(sizes[key]) is not int or sizes[key] <= 0:
            raise BoundaryError("local_model_export", "public_m2_operation_invalid")
    if type(receipt["payload_bytes"]) is not int or receipt["payload_bytes"] != sum(sizes.values()):
        raise BoundaryError("local_model_export", "public_m2_operation_invalid")


def require_lineage(store: Any, owner: Any, model: Manifest, *, stage_id: str) -> dict[str, Any]:
    """Recheck exact original stage parents and today's existing curation owner.

    The existing owner may persist its compatibility-bound projection proof,
    without adding training uses/reservations or interpreting another data source.
    Original partial stages keep their epoch/run_complete values.
    """
    from stpd.policy.public_m2_export import model_lineage

    lineage = model_lineage(store, model, stage_id=stage_id)
    owner.verified_training_receipt(store, (lineage["dataset_id"],),
                                    lineage["training_operation_id"])
    return lineage
