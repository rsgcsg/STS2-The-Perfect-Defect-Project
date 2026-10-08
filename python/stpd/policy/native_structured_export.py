"""Closed native InputSpec package over the shared safe structured-weight verifier."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import torch

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes, object_fields

from ..canonical import semantic_hash
from ..fullrun.native_structured_inputs import INPUT_SPEC, PROJECTION
from ..models.structured_m2 import StructuredM2
from ..models.structured_weights import encode_structured_weights, load_structured_weights
from ..native_code_scope import native_code_identity, native_code_sha256
from ..structured_code_scope import ROOT, exporter_runtime, inference_runtime
from .structured_export import GRAPH, MAX_MANIFEST_BYTES, MAX_WEIGHTS_BYTES, _regular_bytes

PACKAGE_SCHEMA = "stpd/native-structured-m2-package-v1"
WEIGHT_SCHEMA = "stpd/native-structured-m2-weights-v1"
STATE_FORMAT = "stpd/native-structured-m2-state-v1"
AGENT_SPEC = {
    "id": "stpd-native-full-reference-m2-agent",
    "version": "1.0.0",
    "acquisition": "all_required_full_reference_publications",
    "choice": "complete_catalog_greedy",
    "empty_catalog": "await_observation",
    "timing_learned": False,
    "memory_slots": 1,
    "memory_width": 96,
}
MANIFEST_NAME = "model.json"
WEIGHTS_NAME = "weights.tensor-tree"


def encode_native_weights(model: StructuredM2) -> bytes:
    return encode_structured_weights(
        model, schema=WEIGHT_SCHEMA, graph=GRAPH, projection=PROJECTION
    )


def export_native_package(
    model: StructuredM2,
    destination: Path,
    *,
    producer: Producer,
    data_sha256: str,
    training: dict[str, Any],
) -> dict[str, Any]:
    """New native artifact; old S0 weights/packages never silently acquire this InputSpec."""
    digest(data_sha256, "native_package.data_sha256")
    if not isinstance(producer, Producer) or not isinstance(training, dict) or model.seed != 0:
        raise BoundaryError("native_package", "producer_or_fixed_recipe")
    raw = encode_native_weights(model)
    if len(raw) > MAX_WEIGHTS_BYTES:
        raise BoundaryError("native_package", "weights_size_limit")
    identity = native_code_identity(ROOT)
    if producer.uv_lock_sha256 != identity["dependency_lock_sha256"]:
        raise BoundaryError("native_package", "producer_lock_mismatch")
    body = {
        "schema": PACKAGE_SCHEMA,
        "graph": GRAPH,
        "projection": PROJECTION,
        "input_spec": INPUT_SPEC,
        "agent_spec": AGENT_SPEC,
        "state_format_version": STATE_FORMAT,
        "seed": 0,
        "code_identity": identity,
        "adapter_code_sha256": native_code_sha256(ROOT),
        "weights": {
            "path": WEIGHTS_NAME,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        },
        "producer": producer.to_dict(),
        "source": {"kind": "synthetic", "data_sha256": data_sha256},
        "training": training,
        "runtime": inference_runtime(str(torch.__version__)),
        "export_runtime": exporter_runtime(),
        "qualification": "synthetic_engineering_only",
    }
    manifest = {**body, "model_id": semantic_hash(body)}
    encoded = json_bytes(manifest)
    if len(encoded) > MAX_MANIFEST_BYTES or destination.exists():
        raise BoundaryError("native_package", "destination_or_manifest_size")
    destination.mkdir(parents=True)
    (destination / WEIGHTS_NAME).write_bytes(raw)
    (destination / MANIFEST_NAME).write_bytes(encoded)
    load_native_package(destination)
    return manifest


def load_native_package(
    directory: Path, *, expected_manifest_sha256: str | None = None
) -> tuple[
    dict[str, Any],
    StructuredM2,
]:
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or {p.name for p in directory.iterdir()} != {MANIFEST_NAME, WEIGHTS_NAME}
    ):
        raise BoundaryError("native_package", "closed_package_inventory")
    encoded = _regular_bytes(directory / MANIFEST_NAME, MAX_MANIFEST_BYTES)
    if (
        expected_manifest_sha256 is not None
        and hashlib.sha256(encoded).hexdigest() != expected_manifest_sha256
    ):
        raise BoundaryError("native_package", "manifest_digest_mismatch")
    fields = {
        "schema",
        "graph",
        "projection",
        "input_spec",
        "agent_spec",
        "state_format_version",
        "seed",
        "code_identity",
        "adapter_code_sha256",
        "weights",
        "producer",
        "source",
        "training",
        "runtime",
        "export_runtime",
        "qualification",
        "model_id",
    }
    value = object_fields(decode_json(encoded), fields, "native_package")
    if (
        encoded != json_bytes(value)
        or value["schema"] != PACKAGE_SCHEMA
        or value["graph"] != GRAPH
        or value["projection"] != PROJECTION
        or value["input_spec"] != INPUT_SPEC
        or value["agent_spec"] != AGENT_SPEC
        or value["state_format_version"] != STATE_FORMAT
        or type(value["seed"]) is not int
        or value["seed"] != 0
        or value["code_identity"] != native_code_identity(ROOT)
        or value["adapter_code_sha256"] != native_code_sha256(ROOT)
        or value["runtime"] != inference_runtime(str(torch.__version__))
        or value["qualification"] != "synthetic_engineering_only"
        or value["model_id"] != semantic_hash({k: v for k, v in value.items() if k != "model_id"})
    ):
        raise BoundaryError("native_package", "package_identity_mismatch")
    origin = Producer.decode(value["producer"])
    if origin.uv_lock_sha256 != value["code_identity"]["dependency_lock_sha256"]:
        raise BoundaryError("native_package", "producer_lock_mismatch")
    source = object_fields(value["source"], {"kind", "data_sha256"}, "native_package.source")
    digest(source["data_sha256"], "native_package.source_sha256")
    if source["kind"] != "synthetic" or not isinstance(value["training"], dict):
        raise BoundaryError("native_package", "source_scope_mismatch")
    platform = object_fields(
        value["export_runtime"], {"python", "system", "machine"}, "native_package.export_runtime"
    )
    if any(not isinstance(part, str) or not part for part in platform.values()):
        raise BoundaryError("native_package", "invalid_export_provenance")
    weights = object_fields(value["weights"], {"path", "sha256", "bytes"}, "native_package.weights")
    raw = _regular_bytes(directory / WEIGHTS_NAME, MAX_WEIGHTS_BYTES)
    if (
        weights["path"] != WEIGHTS_NAME
        or type(weights["bytes"]) is not int
        or weights["bytes"] != len(raw)
        or weights["sha256"] != hashlib.sha256(raw).hexdigest()
    ):
        raise BoundaryError("native_package", "weights_digest_mismatch")
    model = load_structured_weights(
        raw, schema=WEIGHT_SCHEMA, graph=GRAPH, projection=PROJECTION, seed=value["seed"]
    )
    return value, model
