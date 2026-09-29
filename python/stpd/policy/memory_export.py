"""Portable, train-only M2 package from a replay-verified research run.

Package integrity travels with the bytes. Source purpose and permission remain
with the caller's admission owner and are not implied by this package.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, decode_json, json_bytes, object_fields
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ArtifactStore

from ..fullrun.memory_token_inputs import RENDERER_IDENTITY
from ..fullrun.text_menu_inputs import IDENTITY as TEXT_MENU_IDENTITY
from ..fullrun.text_menu_inputs import SNAPSHOT_SCHEMA
from ..workers.memory_ranking import MemoryConfig
from ..workers.memory_run import (
    INPUT_SCHEMA_V2,
    MAX_CHECKPOINT_BYTES,
    _load_run,
    _payload_bytes,
    _verify_completed,
)

PACKAGE_SCHEMA = "stpd/experimental-m2-portable-policy-v1"
RENDERER = {**RENDERER_IDENTITY, "text_menu": TEXT_MENU_IDENTITY,
            "input_schema": SNAPSHOT_SCHEMA}
MAX_MANIFEST_BYTES = 64 * 1024
MAX_WEIGHTS_BYTES = MAX_CHECKPOINT_BYTES
WEIGHTS_NAME = "weights.tensor-tree"
TOKENIZER_NAME = "tokenizer.json"
MANIFEST_NAME = "model.json"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _regular_bytes(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise BoundaryError("m2_package", "missing_or_oversize_file")
    return path.read_bytes()


def export_memory_package(store: ArtifactStore, reporter: ObjectStoreRunReporter,
                          run_id: str, destination: Path) -> dict[str, Any]:
    """Export only a completed observed-input v2 run, with no source payloads.

    Local purpose/use admission must happen in the calling application first.
    Generic research runs need no Workbench operation identifier.
    """
    run_manifest = store.get_manifest(run_id)
    run, training_input, config, engine = _load_run(store, run_id, run_manifest.producer)
    input_info = training_input.parameters.value()
    if input_info.get("schema") != INPUT_SCHEMA_V2:
        raise BoundaryError("m2_package", "verified_observed_input_required")
    if any(step.previous_actual_action is not None or step.public_feedback is not None
           for episode in engine.snapshot_input().episodes for step in episode.steps):
        raise BoundaryError("m2_package", "unsupported_optional_history_channel")
    result = reporter.completed(run_id)
    if result is None:
        raise BoundaryError("m2_package", "completed_result_required")
    _verify_completed(store, result, run, training_input, config, engine)
    model = store.get_manifest(result.parent("model"))
    weights = _payload_bytes(store, model.payload("weights"), MAX_WEIGHTS_BYTES)
    tokenizer = _payload_bytes(store, model.payload("tokenizer"), 16 * 1024 * 1024)
    if destination.exists():
        raise BoundaryError("m2_package", "destination_exists")
    manifest = {
        "schema": PACKAGE_SCHEMA,
        "ids": {"source": training_input.parent("source"),
                "training_input": training_input.artifact_id, "run": run.artifact_id,
                "result": result.artifact_id, "checkpoint": result.parent("checkpoint"),
                "model": model.artifact_id},
        "input_digest": input_info["input_digest"],
        "source_map_sha256": training_input.payload("source_map").sha256,
        "source_event_count": input_info["source_event_count"],
        "projection_config": input_info["projection_config"],
        "renderer": RENDERER,
        "config": asdict(config),
        "weights": {"file": WEIGHTS_NAME, "sha256": _sha(weights), "size": len(weights)},
        "tokenizer": {"file": TOKENIZER_NAME, "sha256": _sha(tokenizer),
                      "size": len(tokenizer)},
        "partition": "train", "qualification": "engineering_only",
        "evaluation_status": "not_run", "source_admission": "caller_owned",
    }
    raw_manifest = json_bytes(manifest)
    if len(raw_manifest) > MAX_MANIFEST_BYTES:
        raise BoundaryError("m2_package", "manifest_size_limit")
    destination.mkdir(parents=True)
    try:
        (destination / WEIGHTS_NAME).write_bytes(weights)
        (destination / TOKENIZER_NAME).write_bytes(tokenizer)
        (destination / MANIFEST_NAME).write_bytes(raw_manifest)
        validate_memory_package(destination)
    except Exception:
        for name in (WEIGHTS_NAME, TOKENIZER_NAME, MANIFEST_NAME):
            (destination / name).unlink(missing_ok=True)
        destination.rmdir()
        raise
    return manifest


def validate_memory_package(directory: Path) -> tuple[dict[str, Any], bytes, bytes, MemoryConfig]:
    """Validate detached portable bytes; no local store or ledger is needed."""
    raw = _regular_bytes(directory / MANIFEST_NAME, MAX_MANIFEST_BYTES)
    value = object_fields(decode_json(raw), {
        "schema", "ids", "input_digest", "source_map_sha256", "source_event_count",
        "projection_config",
        "renderer", "config", "weights", "tokenizer", "partition", "qualification",
        "evaluation_status", "source_admission",
    }, "m2_package.manifest")
    if (raw != json_bytes(value) or value["schema"] != PACKAGE_SCHEMA
            or value["renderer"] != RENDERER or value["partition"] != "train"
            or value["qualification"] != "engineering_only"
            or value["evaluation_status"] != "not_run"
            or value["source_admission"] != "caller_owned"):
        raise BoundaryError("m2_package", "unsupported_package_identity")
    ids = object_fields(value["ids"], {"source", "training_input", "run", "result",
                                      "checkpoint", "model"}, "m2_package.ids")
    if any(not isinstance(item, str) or len(item) != 64
           or any(c not in "0123456789abcdef" for c in item) for item in ids.values()):
        raise BoundaryError("m2_package", "artifact_identity_invalid")
    for key in ("input_digest", "source_map_sha256"):
        item = value[key]
        if not isinstance(item, str) or len(item) != 64 or any(
            c not in "0123456789abcdef" for c in item
        ):
            raise BoundaryError("m2_package", "artifact_identity_invalid")
    if (type(value["source_event_count"]) is not int
            or value["source_event_count"] < 1):
        raise BoundaryError("m2_package", "source_event_count_invalid")
    projection = object_fields(value["projection_config"], {
        "schema", "max_settling_events"}, "m2_package.projection")
    if (projection["schema"] != "stpd/memory-episode-projection-config-v1"
            or type(projection["max_settling_events"]) is not int
            or projection["max_settling_events"] < 0):
        raise BoundaryError("m2_package", "projection_config_invalid")
    config_value = value["config"]
    if not isinstance(config_value, dict) or set(config_value) != {
        field.name for field in fields(MemoryConfig)
    }:
        raise BoundaryError("m2_package", "config_invalid")
    config = MemoryConfig(**config_value)
    payloads = []
    for role, name, maximum in (("weights", WEIGHTS_NAME, MAX_WEIGHTS_BYTES),
                                ("tokenizer", TOKENIZER_NAME, 16 * 1024 * 1024)):
        pin = object_fields(value[role], {"file", "sha256", "size"}, "m2_package.pin")
        if pin["file"] != name or type(pin["size"]) is not int or pin["size"] <= 0:
            raise BoundaryError("m2_package", "payload_pin_invalid")
        content = _regular_bytes(directory / name, maximum)
        if pin["size"] != len(content) or pin["sha256"] != _sha(content):
            raise BoundaryError("m2_package", "payload_digest_mismatch")
        payloads.append(content)
    return value, payloads[0], payloads[1], config


def verify_memory_package(store: ArtifactStore, reporter: ObjectStoreRunReporter,
                          model_id: str, directory: Path) -> dict[str, Any]:
    """Reconcile detached package with the completed immutable store lineage."""
    package, _, _, saved_config = validate_memory_package(directory)
    if package["ids"]["model"] != model_id:
        raise BoundaryError("m2_package", "model_identity_mismatch")
    run_id = package["ids"]["run"]
    candidate = store.get_manifest(run_id)
    run, training_input, config, engine = _load_run(store, run_id, candidate.producer)
    if any(step.previous_actual_action is not None or step.public_feedback is not None
           for episode in engine.snapshot_input().episodes for step in episode.steps):
        raise BoundaryError("m2_package", "unsupported_optional_history_channel")
    result = reporter.completed(run_id)
    if result is None:
        raise BoundaryError("m2_package", "completed_result_required")
    _verify_completed(store, result, run, training_input, config, engine)
    model = store.get_manifest(result.parent("model"))
    info = training_input.parameters.value()
    expected_ids = {"source": training_input.parent("source"),
                    "training_input": training_input.artifact_id, "run": run_id,
                    "result": result.artifact_id, "checkpoint": result.parent("checkpoint"),
                    "model": model.artifact_id}
    if (info.get("schema") != INPUT_SCHEMA_V2 or package["ids"] != expected_ids
            or saved_config != config or package["input_digest"] != info["input_digest"]
            or package["projection_config"] != info["projection_config"]
            or package["source_map_sha256"] != training_input.payload("source_map").sha256
            or package["source_event_count"] != info["source_event_count"]
            or package["weights"]["sha256"] != model.payload("weights").sha256
            or package["tokenizer"]["sha256"] != model.payload("tokenizer").sha256):
        raise BoundaryError("m2_package", "store_package_identity_mismatch")
    return package
