"""Closed S-M2-0 inference package over the existing safe tensor-tree codec.

The executable is the reviewed installed Python module, never supplied by a
model artifact. Source and package hashes bind graph/projection/config/weights.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import torch

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes, object_fields

from ..canonical import semantic_hash
from ..fullrun.structured_inputs import INPUT_ID, PROJECTION_VERSION
from ..fullrun.text_menu_inputs import V2_SNAPSHOT_SCHEMA
from ..models.structured_m2 import GRAPH_ID, SLOTS, WIDTH, StructuredM2
from ..models.structured_weights import encode_structured_weights, load_structured_weights
from ..structured_code_scope import (
    INFERENCE_SCOPE,
    LEGACY_SCOPE,
    SCOPED_PACKAGE_SCHEMA,
    code_identity,
    exporter_runtime,
    inference_runtime,
)

PACKAGE_SCHEMA = "stpd/structured-m2-package-v1"
WEIGHT_SCHEMA = "stpd/structured-m2-weights-v1"
MANIFEST_NAME = "model.json"
WEIGHTS_NAME = "weights.tensor-tree"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_WEIGHTS_BYTES = 32 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[2]
PROJECTION = {
    "id": INPUT_ID,
    "version": PROJECTION_VERSION,
    "source_schema": V2_SNAPSHOT_SCHEMA,
    "I": False,
    "F": False,
}
GRAPH = {
    "id": GRAPH_ID,
    "width": WIDTH,
    "slots": SLOTS,
    "byte_vocabulary": 258,
    "byte_embedding": 32,
    "cnn_channels": 64,
    "cnn_kernel": 3,
    "relation_layers": 1,
    "relation_aggregation": "typed_transform_masked_mean",
    "entity_fields": "typed_local_mean",
    "numeric": "known_signed_scale_signed_log1p",
    "array_policy": "explicit_public_order_else_bag-v1",
}


def code_digest(root: Path = ROOT) -> str:
    """Match the existing portable policy source scope without loading old recipes.

    This is conservative source provenance, not a second artifact registry. In
    particular the S0 adapter must not import obsolete Human training machinery
    merely to hash files in the trusted installed source tree.
    """
    paths = sorted(
        [*root.glob("stpd/**/*.py"), *root.glob("spireagent/**/*.py"), root / "uv.lock"],
        key=lambda path: path.relative_to(root).as_posix(),
    )
    if not (root / "stpd/policy/structured_port.py").is_file() or any(
        path.is_symlink() or not path.is_file() for path in paths
    ):
        raise BoundaryError("structured_package", "trusted_source_missing")
    rows = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in paths
    ]
    return semantic_hash(rows)


def _regular_bytes(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise BoundaryError("structured_package", "file_missing_or_size")
    return path.read_bytes()


def export_structured_package(
    model: StructuredM2,
    destination: Path,
    *,
    source_revision: str,
    data_sha256: str,
    source_kind: str,
    teacher_sha256: str,
    training: dict[str, Any],
    code_scope: str = LEGACY_SCOPE,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    digest(source_revision, "structured_package.source_revision", length=40)
    digest(data_sha256, "structured_package.data_sha256")
    digest(teacher_sha256, "structured_package.teacher_sha256")
    if source_kind not in {"agent", "synthetic"} or not isinstance(training, dict):
        raise BoundaryError("structured_package", "source_or_training")
    if code_scope not in {LEGACY_SCOPE, INFERENCE_SCOPE}:
        raise BoundaryError("structured_package", "unsupported_code_scope")
    scoped = code_scope == INFERENCE_SCOPE
    if not scoped and provenance is not None:
        raise BoundaryError("structured_package", "unexpected_scoped_provenance")
    model.validate_parameters()
    if model.model_control is not None:
        raise BoundaryError("structured_package", "native_control_not_legacy")
    if model.seed != 0:
        raise BoundaryError("structured_package", "unsupported_initialization_recipe")
    raw = encode_structured_weights(model, schema=WEIGHT_SCHEMA, graph=GRAPH,
                                     projection=PROJECTION)
    if len(raw) > MAX_WEIGHTS_BYTES:
        raise BoundaryError("structured_package", "weights_size_limit")
    body = {
        "schema": SCOPED_PACKAGE_SCHEMA if scoped else PACKAGE_SCHEMA,
        "graph": GRAPH,
        "projection": PROJECTION,
        "seed": model.seed,
        "adapter_code_sha256": (semantic_hash(code_identity(INFERENCE_SCOPE, ROOT))
                                if scoped else code_digest(ROOT)),
        "weights": {
            "path": WEIGHTS_NAME,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        },
        "source": {
            "source_revision": source_revision,
            "data_sha256": data_sha256,
            "source_kind": source_kind,
            "teacher_sha256": teacher_sha256,
        },
        "training": training,
        "runtime": (inference_runtime(str(torch.__version__)) if scoped else
                    {"device": "cpu", "dtype": "float32", "torch_version": torch.__version__}),
        "qualification": "engineering_only",
    }
    if scoped:
        identity = code_identity(INFERENCE_SCOPE, ROOT)
        body["code_identity"] = identity
        supplied = object_fields(provenance, {"training_producer", "export_producer", "run_id",
                                              "training_input_id", "checkpoint_id"},
                                 "structured_package.export_provenance")
        body["provenance"] = _provenance(
            {**supplied, "export_runtime": exporter_runtime()}, source_revision, identity)
    manifest = {**body, "model_id": semantic_hash(body)}
    encoded = json_bytes(manifest)
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise BoundaryError("structured_package", "manifest_size_limit")
    if destination.exists():
        raise BoundaryError("structured_package", "destination_exists")
    destination.mkdir(parents=True)
    (destination / WEIGHTS_NAME).write_bytes(raw)
    # Manifest-last publication; incomplete output never loads as a package.
    (destination / MANIFEST_NAME).write_bytes(encoded)
    load_structured_package(destination)
    return manifest



def _provenance(value: object, source_revision: str, identity: dict[str, str]) -> dict[str, Any]:
    result = object_fields(value, {"training_producer", "export_producer", "run_id",
                                  "training_input_id", "checkpoint_id", "export_runtime"},
                           "structured_package.provenance")
    platform = object_fields(result["export_runtime"], {"python", "system", "machine"},
                             "structured_package.export_runtime")
    if any(not isinstance(item, str) or not item for item in platform.values()):
        raise BoundaryError("structured_package", "export_provenance_mismatch")
    training = Producer.decode(result["training_producer"])
    exporter = Producer.decode(result["export_producer"])
    for key in ("run_id", "training_input_id", "checkpoint_id"):
        digest(result[key], "structured_package.provenance." + key)
    if (training.source_revision != source_revision
            or exporter.uv_lock_sha256 != identity["dependency_lock_sha256"]):
        raise BoundaryError("structured_package", "producer_provenance_mismatch")
    return result

def load_structured_package(
    directory: Path,
    *,
    expected_manifest_sha256: str | None = None,
) -> tuple[dict[str, Any], StructuredM2]:
    if directory.is_symlink() or not directory.is_dir():
        raise BoundaryError("structured_package", "directory_required")
    if set(path.name for path in directory.iterdir()) != {MANIFEST_NAME, WEIGHTS_NAME}:
        raise BoundaryError("structured_package", "package_inventory")
    encoded = _regular_bytes(directory / MANIFEST_NAME, MAX_MANIFEST_BYTES)
    if expected_manifest_sha256 is not None and hashlib.sha256(encoded).hexdigest() != digest(
        expected_manifest_sha256, "structured_package.expected_manifest_sha256"
    ):
        raise BoundaryError("structured_package", "manifest_digest_mismatch")
    decoded_manifest = decode_json(encoded)
    scoped = (isinstance(decoded_manifest, dict)
              and decoded_manifest.get("schema") == SCOPED_PACKAGE_SCHEMA)
    manifest = object_fields(
        decoded_manifest,
        {
            "schema",
            "graph",
            "projection",
            "seed",
            "adapter_code_sha256",
            "weights",
            "source",
            "training",
            "runtime",
            "qualification",
            "model_id",
        } | ({"code_identity", "provenance"} if scoped else set()),
        "structured_package",
    )
    if (
        encoded != json_bytes(manifest)
        or manifest["schema"] != (SCOPED_PACKAGE_SCHEMA if scoped else PACKAGE_SCHEMA)
        or manifest["graph"] != GRAPH
        or manifest["projection"] != PROJECTION
        or manifest["qualification"] != "engineering_only"
        or type(manifest["seed"]) is not int
        or manifest["seed"] != 0
        or manifest["adapter_code_sha256"] != (
            semantic_hash(code_identity(INFERENCE_SCOPE, ROOT)) if scoped else code_digest(ROOT))
        or scoped and manifest["code_identity"] != code_identity(INFERENCE_SCOPE, ROOT)
    ):
        raise BoundaryError("structured_package", "unsupported_package_identity")
    body = {key: value for key, value in manifest.items() if key != "model_id"}
    if manifest["model_id"] != semantic_hash(body):
        raise BoundaryError("structured_package", "model_id_mismatch")
    source = object_fields(
        manifest["source"],
        {"source_revision", "data_sha256", "source_kind", "teacher_sha256"},
        "structured_package.source",
    )
    digest(source["source_revision"], "structured_package.source_revision", length=40)
    digest(source["data_sha256"], "structured_package.data_sha256")
    digest(source["teacher_sha256"], "structured_package.teacher_sha256")
    if source["source_kind"] not in {"agent", "synthetic"} or not isinstance(
        manifest["training"], dict
    ):
        raise BoundaryError("structured_package", "source_or_training")
    expected_runtime = (inference_runtime(str(torch.__version__)) if scoped else
                        {"device": "cpu", "dtype": "float32", "torch_version": torch.__version__})
    runtime = object_fields(
        manifest["runtime"], set(expected_runtime), "structured_package.runtime")
    if runtime != expected_runtime:
        raise BoundaryError("structured_package", "runtime_identity_mismatch")
    if scoped:
        _provenance(manifest["provenance"], source["source_revision"], manifest["code_identity"])
    weights = object_fields(
        manifest["weights"], {"path", "sha256", "bytes"}, "structured_package.weights"
    )
    raw = _regular_bytes(directory / WEIGHTS_NAME, MAX_WEIGHTS_BYTES)
    if (
        weights["path"] != WEIGHTS_NAME
        or type(weights["bytes"]) is not int
        or weights["bytes"] != len(raw)
        or weights["sha256"] != hashlib.sha256(raw).hexdigest()
    ):
        raise BoundaryError("structured_package", "weights_digest_mismatch")
    model = load_structured_weights(raw, schema=WEIGHT_SCHEMA, graph=GRAPH,
                                    projection=PROJECTION, seed=manifest["seed"])
    return manifest, model
