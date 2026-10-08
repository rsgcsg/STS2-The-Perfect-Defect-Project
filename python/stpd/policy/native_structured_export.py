"""Closed native InputSpec package over the shared safe structured-weight verifier."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import torch

from spireagent.artifact_contracts import Manifest, Payload, Producer
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes, object_fields

from ..canonical import semantic_hash
from ..fullrun.native_structured_inputs import INPUT_SPEC, PROJECTION
from ..models.structured_m2 import StructuredM2
from ..models.structured_weights import encode_structured_weights, load_structured_weights
from ..native_code_scope import MODEL_SCHEMA as MODEL_SCHEMA
from ..native_code_scope import is_native_model_schema as is_native_model_schema
from ..native_code_scope import native_code_identity, native_code_sha256
from ..structured_code_scope import ROOT, exporter_runtime, inference_runtime
from .structured_export import GRAPH, MAX_MANIFEST_BYTES, MAX_WEIGHTS_BYTES, _regular_bytes

if TYPE_CHECKING:
    from spireagent.storage.store import ArtifactStore

PACKAGE_SCHEMA = "stpd/native-structured-m2-package-v1"
TRAINED_PACKAGE_SCHEMA = "stpd/native-structured-m2-package-v2"
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
    return _export_package(
        model,
        destination,
        producer=producer,
        data_sha256=data_sha256,
        training=training,
        source=None,
        provenance=None,
    )


def _trained_source(source: object, data_sha256: str) -> dict[str, Any]:
    value = object_fields(
        source,
        {"kind", "data_sha256", "source_artifact_id", "training_input_id", "verification_identity"},
        "native_package.source",
    )
    digest(value["source_artifact_id"], "native_package.source_artifact_id")
    digest(value["training_input_id"], "native_package.training_input_id")
    if value["kind"] != "synthetic" or value["data_sha256"] != data_sha256:
        raise BoundaryError("native_package", "source_scope_mismatch")
    if value["verification_identity"] != {
        "schema": "stpd/native-synthetic-source-validation-v1",
        "source_sha256": data_sha256,
        "input_spec_sha256": INPUT_SPEC["sha256"],
        "validation": "synthetic_conformance_only",
    }:
        raise BoundaryError("native_package", "source_verification_identity_mismatch")
    return value


def _trained_provenance(
    value: object, source: dict[str, Any], producer: Producer
) -> dict[str, Any]:
    result = object_fields(
        value,
        {
            "training_producer",
            "export_producer",
            "run_id",
            "training_input_id",
            "checkpoint_id",
            "export_runtime",
        },
        "native_package.provenance",
    )
    training_producer = Producer.decode(result["training_producer"])
    if (
        Producer.decode(result["export_producer"]) != producer
        or training_producer.uv_lock_sha256 != producer.uv_lock_sha256
        or result["training_input_id"] != source["training_input_id"]
    ):
        raise BoundaryError("native_package", "trained_provenance_mismatch")
    for field in ("run_id", "training_input_id", "checkpoint_id"):
        digest(result[field], "native_package.provenance." + field)
    platform = object_fields(
        result["export_runtime"],
        {"python", "system", "machine"},
        "native_package.provenance.export_runtime",
    )
    if any(not isinstance(part, str) or not part for part in platform.values()):
        raise BoundaryError("native_package", "invalid_export_provenance")
    return result


def export_native_trained_package(
    model: StructuredM2,
    destination: Path,
    *,
    producer: Producer,
    data_sha256: str,
    training: dict[str, Any],
    source: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """The trained v2 shape binds original source and immutable numerical lineage."""
    return _export_package(
        model,
        destination,
        producer=producer,
        data_sha256=data_sha256,
        training=training,
        source=source,
        provenance=provenance,
    )


def _export_package(
    model: StructuredM2,
    destination: Path,
    *,
    producer: Producer,
    data_sha256: str,
    training: dict[str, Any],
    source: dict[str, Any] | None,
    provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    digest(data_sha256, "native_package.data_sha256")
    if not isinstance(producer, Producer) or not isinstance(training, dict) or model.seed != 0:
        raise BoundaryError("native_package", "producer_or_fixed_recipe")
    raw = encode_native_weights(model)
    if len(raw) > MAX_WEIGHTS_BYTES:
        raise BoundaryError("native_package", "weights_size_limit")
    identity = native_code_identity(ROOT)
    if producer.uv_lock_sha256 != identity["dependency_lock_sha256"]:
        raise BoundaryError("native_package", "producer_lock_mismatch")
    trained = source is not None
    if trained:
        source = _trained_source(source, data_sha256)
        provenance = _trained_provenance(provenance, source, producer)
    elif provenance is not None:
        raise BoundaryError("native_package", "standalone_provenance_forbidden")
    body = {
        "schema": TRAINED_PACKAGE_SCHEMA if trained else PACKAGE_SCHEMA,
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
        "source": source if trained else {"kind": "synthetic", "data_sha256": data_sha256},
        "training": training,
        "runtime": inference_runtime(str(torch.__version__)),
        "export_runtime": exporter_runtime(),
        "qualification": "synthetic_engineering_only",
        **({"provenance": provenance} if trained else {}),
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


def _native_manifest(encoded: bytes) -> dict[str, Any]:
    if not 0 < len(encoded) <= MAX_MANIFEST_BYTES:
        raise BoundaryError("native_package", "manifest_size_limit")
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
    decoded = decode_json(encoded)
    trained = isinstance(decoded, dict) and decoded.get("schema") == TRAINED_PACKAGE_SCHEMA
    value = object_fields(
        decoded, fields | ({"provenance"} if trained else set()), "native_package"
    )
    if (
        encoded != json_bytes(value)
        or value["schema"] != (TRAINED_PACKAGE_SCHEMA if trained else PACKAGE_SCHEMA)
        or json_bytes(value["graph"]) != json_bytes(GRAPH)
        or json_bytes(value["projection"]) != json_bytes(PROJECTION)
        or json_bytes(value["input_spec"]) != json_bytes(INPUT_SPEC)
        or json_bytes(value["agent_spec"]) != json_bytes(AGENT_SPEC)
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
    source = object_fields(
        value["source"],
        {"kind", "data_sha256"}
        | (
            {"source_artifact_id", "training_input_id", "verification_identity"}
            if trained
            else set()
        ),
        "native_package.source",
    )
    digest(source["data_sha256"], "native_package.source_sha256")
    if trained:
        source = _trained_source(source, source["data_sha256"])
    if source["kind"] != "synthetic" or not isinstance(value["training"], dict):
        raise BoundaryError("native_package", "source_scope_mismatch")
    platform = object_fields(
        value["export_runtime"], {"python", "system", "machine"}, "native_package.export_runtime"
    )
    if any(not isinstance(part, str) or not part for part in platform.values()):
        raise BoundaryError("native_package", "invalid_export_provenance")
    if trained:
        provenance = _trained_provenance(value["provenance"], source, origin)
        if provenance["export_runtime"] != value["export_runtime"]:
            raise BoundaryError("native_package", "export_platform_provenance_mismatch")
    weights = object_fields(value["weights"], {"path", "sha256", "bytes"}, "native_package.weights")
    digest(weights["sha256"], "native_package.weights_sha256")
    if (
        weights["path"] != WEIGHTS_NAME
        or type(weights["bytes"]) is not int
        or not 0 < weights["bytes"] <= MAX_WEIGHTS_BYTES
    ):
        raise BoundaryError("native_package", "weights_descriptor_mismatch")
    return value


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
    value = _native_manifest(encoded)
    weights = value["weights"]
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


def native_model_parameters(package: dict[str, Any], attempt: str) -> dict[str, Any]:
    digest(attempt, "native_model.attempt", length=32)
    if package.get("schema") != TRAINED_PACKAGE_SCHEMA:
        raise BoundaryError("native_model", "trained_package_required")
    package = _native_manifest(json_bytes(package))
    return {
        "schema": MODEL_SCHEMA,
        "model_id": package["model_id"],
        "graph_id": package["graph"]["id"],
        "input_spec": package["input_spec"],
        "agent_spec": package["agent_spec"],
        "state_format_version": package["state_format_version"],
        "code_identity": package["code_identity"],
        "source": package["source"],
        "qualification": package["qualification"],
        "attempt": attempt,
        "provenance": package["provenance"],
    }


def require_native_model_package(model: Manifest, package: dict[str, Any]) -> None:
    """Validate an authorized model's own bytes and lineage IDs, without opening parents."""
    info = model.parameters.value()
    if (
        model.kind != "model"
        or not is_native_model_schema(info.get("schema"))
        or {parent.role for parent in model.parents} != {"run", "checkpoint", "training_input"}
        or {payload.role for payload in model.payloads} != {"package_manifest", "weights"}
        or package.get("schema") != TRAINED_PACKAGE_SCHEMA
    ):
        raise BoundaryError("native_model", "model_inventory_or_schema")
    attempt = digest(info.get("attempt"), "native_model.attempt", length=32)
    expected = native_model_parameters(package, attempt)
    if (
        info != expected
        or model.producer.to_dict() != package["provenance"]["export_producer"]
        or any(
            model.parent(role) != package["provenance"][role + "_id"]
            for role in ("run", "checkpoint", "training_input")
        )
    ):
        raise BoundaryError("native_model", "model_package_or_parent_binding")
    metadata, weights = model.payload("package_manifest"), model.payload("weights")
    if (
        metadata.media_type != "application/json"
        or metadata.size != len(json_bytes(package))
        or metadata.sha256 != hashlib.sha256(json_bytes(package)).hexdigest()
        or weights.media_type != "application/vnd.stpd.tensor-tree"
        or weights.sha256 != package["weights"]["sha256"]
        or weights.size != package["weights"]["bytes"]
    ):
        raise BoundaryError("native_model", "model_payload_binding")


def _payload_bytes(store: ArtifactStore, payload: Payload, maximum: int) -> bytes:
    if not 0 < payload.size <= maximum:
        raise BoundaryError("native_model", "payload_size_limit")
    raw = bytearray()
    for chunk in store.read_payload(payload):
        if not isinstance(chunk, bytes) or len(raw) + len(chunk) > payload.size:
            raise BoundaryError("native_model", "payload_integrity")
        raw.extend(chunk)
    if len(raw) != payload.size or hashlib.sha256(raw).hexdigest() != payload.sha256:
        raise BoundaryError("native_model", "payload_integrity")
    return bytes(raw)


def export_native_model(store: ArtifactStore, model_id: str, destination: Path) -> dict[str, Any]:
    """Materialize one closed v2 package; the caller retains journal/budget authority."""
    model = store.get_manifest(model_id)
    if model.artifact_id != model_id or not is_native_model_schema(
        model.parameters.value().get("schema")
    ):
        raise BoundaryError("native_model", "exact_model_required")
    if (
        destination.exists()
        or destination.is_symlink()
        or any(parent.is_symlink() for parent in destination.parents)
    ):
        raise BoundaryError("native_model", "unsafe_or_existing_destination")
    raw_manifest = _payload_bytes(store, model.payload("package_manifest"), MAX_MANIFEST_BYTES)
    raw_weights = _payload_bytes(store, model.payload("weights"), MAX_WEIGHTS_BYTES)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="native-model-export-", dir=destination.parent) as tmp:
        checked = Path(tmp) / "package"
        checked.mkdir()
        (checked / MANIFEST_NAME).write_bytes(raw_manifest)
        (checked / WEIGHTS_NAME).write_bytes(raw_weights)
        package, _ = load_native_package(checked)
        require_native_model_package(model, package)
        # Exclusive destination creation never replaces an existing directory.
        destination.mkdir()
        try:
            (destination / MANIFEST_NAME).write_bytes(raw_manifest)
            (destination / WEIGHTS_NAME).write_bytes(raw_weights)
            load_native_package(destination)
        except Exception:
            for name in (MANIFEST_NAME, WEIGHTS_NAME):
                (destination / name).unlink(missing_ok=True)
            destination.rmdir()
            raise
    return {
        "artifact_id": model.artifact_id,
        "package_model_id": package["model_id"],
        "package": str(destination.absolute()),
        "package_manifest_sha256": model.payload("package_manifest").sha256,
        "weights_sha256": model.payload("weights").sha256,
        **{
            field: package[field]
            for field in (
                "input_spec",
                "agent_spec",
                "state_format_version",
                "code_identity",
                "qualification",
                "provenance",
            )
        },
    }
