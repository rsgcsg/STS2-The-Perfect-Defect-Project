"""Deterministic model views and verified frozen-Qwen feature artifacts for Full-Run data."""

from __future__ import annotations

import io
import tempfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, json_bytes, unsigned
from spireagent.storage.store import ArtifactStore

from ..canonical import semantic_hash, to_json_value
from .data import AdmittedDataset, load_dataset
from .representation import FullRunSerializer

if TYPE_CHECKING:
    from ..contracts import QwenBackend

VIEW_SCHEMA = "stpd/fullrun-model-view-v1"
FEATURE_SCHEMA = "stpd/fullrun-feature-set-v1"
DECISION_FEATURE_SCHEMA = "stpd/decision-feature-set-v1"


@dataclass(frozen=True)
class ModelSample:
    transition_id: str
    run_id: str
    split: str
    surface: str
    family: str
    state_text: str
    action_texts: tuple[str, ...]
    action_keys: tuple[str, ...]
    chosen_index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "transition_id": self.transition_id,
            "run_id": self.run_id,
            "split": self.split,
            "surface": self.surface,
            "family": self.family,
            "state_text": self.state_text,
            "action_texts": list(self.action_texts),
            "action_keys": list(self.action_keys),
            "chosen_index": self.chosen_index,
        }


def model_samples(
    dataset: AdmittedDataset, serializer: FullRunSerializer
) -> tuple[ModelSample, ...]:
    splits = dataset.splits.value()
    samples = []
    for record in dataset.records:
        state, actions = serializer.serialize(record)
        keys = tuple(action.key for action in record.actions)
        if record.chosen_key not in keys:
            raise BoundaryError("model_view", "chosen_action_not_in_catalog")
        samples.append(
            ModelSample(
                record.transition_id,
                record.run_id,
                "test" if splits[record.run_id] == "gold_test" else splits[record.run_id],
                record.surface,
                record.family,
                state,
                actions,
                keys,
                keys.index(record.chosen_key),
            )
        )
    return tuple(samples)


def publish_model_view(
    store: ArtifactStore, dataset_id: str, serializer: FullRunSerializer, producer: Producer
) -> Manifest:
    dataset_manifest, dataset = load_dataset(store, dataset_id)
    samples = model_samples(dataset, serializer)
    with tempfile.TemporaryFile("w+b") as handle:
        for sample in samples:
            handle.write(json_bytes(sample.to_dict()))
        handle.seek(0)
        payload = store.put_payload("samples", handle, "application/x-ndjson")
    manifest = Manifest(
        "model_view",
        producer,
        parents=(Parent("dataset", dataset_id),),
        payloads=(payload,),
        parameters=FrozenObject.of(
            {
                "schema": VIEW_SCHEMA,
                "serializer": serializer.identity,
                "scope": dataset.scope,
                "samples": len(samples),
                "dataset_logical_id": dataset_manifest.parameters.value()["logical_id"],
            }
        ),
    )
    store.publish(manifest)
    return manifest


def load_model_view(store: ArtifactStore, view_id: str) -> tuple[Manifest, tuple[ModelSample, ...]]:
    from .view_session import load_view

    return load_view(store, view_id, _load_model_view)


def _load_model_view(
    store: ArtifactStore, view_id: str
) -> tuple[Manifest, tuple[ModelSample, ...]]:
    manifest = store.get_manifest(view_id)
    parameters = manifest.parameters.value()
    from .decision_training import VIEW_SCHEMA as DECISION_VIEW_SCHEMA
    from .decision_training import load_decision_view
    from .public_bc import LEGACY_VIEW_SCHEMA, load_public_bc_view
    from .public_bc import VIEW_SCHEMA as PUBLIC_BC_SCHEMA

    if parameters.get("schema") in {PUBLIC_BC_SCHEMA, LEGACY_VIEW_SCHEMA}:
        return load_public_bc_view(store, manifest)
    if parameters.get("schema") == DECISION_VIEW_SCHEMA:
        return load_decision_view(store, manifest)
    if manifest.kind != "model_view" or parameters.get("schema") != VIEW_SCHEMA:
        raise BoundaryError("model_view", "unsupported_contract")
    if len(manifest.parents) != 1 or {p.role for p in manifest.payloads} != {"samples"}:
        raise BoundaryError("model_view", "unexpected_inventory")
    dataset_manifest, dataset = load_dataset(store, manifest.parent("dataset"))
    identity = parameters.get("serializer", {})
    if not isinstance(identity, dict):
        raise BoundaryError("model_view", "invalid_serializer")
    serializer = FullRunSerializer(identity.get("profile", ""))
    if identity != serializer.identity:
        raise BoundaryError("model_view", "serializer_identity_mismatch")
    expected = model_samples(dataset, serializer)
    with tempfile.TemporaryFile("w+b") as handle:
        for chunk in store.read_payload(manifest.payload("samples")):
            handle.write(chunk)
        handle.seek(0)
        for sample in expected:
            if handle.readline() != json_bytes(sample.to_dict()):
                raise BoundaryError("model_view", "dataset_projection_or_label_mismatch")
        if handle.read(1):
            raise BoundaryError("model_view", "extra_sample")
    if (
        parameters.get("samples") != len(expected)
        or parameters.get("scope") != dataset.scope
        or parameters.get("dataset_logical_id") != dataset_manifest.parameters.value()["logical_id"]
    ):
        raise BoundaryError("model_view", "dataset_identity_mismatch")
    return manifest, expected


def validate_qwen_identity(
    identity: FrozenObject, scope: str, *, decision_view: bool = False
) -> None:
    from ..contracts import QwenIdentity
    from ..qwen.l2 import load_l2_pin

    try:
        value = QwenIdentity(**identity.value())
        value.validate_v0()
        if value.model_id == "stpd/deterministic-fake-qwen":
            if scope != "engineering":
                raise BoundaryError("features", "fake_qwen_is_engineering_only")
            return
        if decision_view:
            from ..qwen.portable_backend import validate_engineering_identity

            validate_engineering_identity(value)
        else:
            value.validate_scientific_v0()
        pin = load_l2_pin()
        if (
            value.model_revision != pin.repo_revision
            or value.tokenizer_revision != pin.repo_revision
            or value.config_sha256 != pin.l1.config_sha256
            or value.tokenizer_sha256 != pin.l1.tokenizer_bundle_sha256
            or (value.control == "pretrained" and value.weights_sha256 != pin.weights_sha256)
        ):
            raise BoundaryError("features", "qwen_pin_mismatch")
    except (TypeError, ValueError) as error:
        if isinstance(error, BoundaryError):
            raise
        raise BoundaryError("features", "invalid_qwen_identity") from error


def joint_key(state: str, action: str, identity: FrozenObject) -> str:
    return semantic_hash(
        {"operation": "pooled-joint-v1", "state": state, "action": action, "qwen": identity.value()}
    )


def feature_index(
    samples: tuple[ModelSample, ...], identity: FrozenObject
) -> tuple[tuple[str, ...], tuple[tuple[int, ...], ...], dict[str, tuple[str, str]]]:
    pairs: dict[str, tuple[str, str]] = {}
    sample_keys = []
    for sample in samples:
        keys = []
        for action in sample.action_texts:
            key = joint_key(sample.state_text, action, identity)
            pair = (sample.state_text, action)
            if pairs.setdefault(key, pair) != pair:
                raise BoundaryError("features", "semantic_hash_collision")
            keys.append(key)
        sample_keys.append(keys)
    ordered = tuple(sorted(pairs))
    positions = {key: index for index, key in enumerate(ordered)}
    rows = tuple(tuple(positions[key] for key in keys) for keys in sample_keys)
    return ordered, rows, pairs


def compile_features(
    store: ArtifactStore,
    view_id: str,
    backend: QwenBackend,
    producer: Producer,
    *,
    batch_size: int = 8,
) -> Manifest:
    import torch

    unsigned(batch_size, "features.batch_size")
    if batch_size == 0:
        raise BoundaryError("features", "empty_batch")
    view, samples = load_model_view(store, view_id)
    if view.parameters.value()["schema"] not in {VIEW_SCHEMA, "stpd/decision-model-view-v1"}:
        raise BoundaryError("features", "unsupported_pooled_view")
    identity_value = to_json_value(backend.identity)
    if not isinstance(identity_value, dict):
        raise BoundaryError("features", "invalid_backend_identity")
    identity = FrozenObject.of(identity_value)
    scope = view.parameters.value()["scope"]
    decision_view = view.parameters.value()["schema"] == "stpd/decision-model-view-v1"
    validate_qwen_identity(identity, scope, decision_view=decision_view)
    keys, rows, pairs = feature_index(samples, identity)
    token_lengths = getattr(backend, "token_lengths", None)
    if callable(token_lengths):
        # Validate every full input before spending any encoder compute. Real pinned
        # backends reject over-limit sequences here; never silently drop/truncate them.
        for key in keys:
            state, action = pairs[key]
            token_lengths([f"{state}\n{action}"])
    arrays: list[NDArray[np.float32]] = []
    for offset in range(0, len(keys), batch_size):
        chunk = [pairs[key] for key in keys[offset : offset + batch_size]]
        with torch.no_grad():
            encoded = backend.encode_joint([pair[0] for pair in chunk], [pair[1] for pair in chunk])
        if (
            not isinstance(encoded, torch.Tensor)
            or encoded.requires_grad
            or encoded.ndim != 2
            or encoded.shape[0] != len(chunk)
            or encoded.shape[1] < 1
            or not bool(torch.isfinite(encoded).all())
        ):
            raise BoundaryError("features", "invalid_frozen_backend_output")
        canonical = encoded.detach().to(device="cpu", dtype=torch.float32)
        if not bool(torch.isfinite(canonical).all()):
            raise BoundaryError("features", "float32_output_overflow")
        arrays.append(canonical.numpy())
    matrix = np.concatenate(arrays, axis=0).astype("<f4", copy=False)
    with tempfile.TemporaryFile("w+b") as handle:
        np.save(handle, matrix, allow_pickle=False)
        handle.seek(0)
        features = store.put_payload("features", handle, "application/x-npy")
    index_payload = store.put_payload(
        "index",
        io.BytesIO(
            json_bytes(
                {
                    "keys": list(keys),
                    "sample_rows": [list(row) for row in rows],
                }
            )
        ),
        "application/json",
    )
    manifest = Manifest(
        "feature_set",
        producer,
        parents=(Parent("model_view", view_id),),
        payloads=(features, index_payload),
        parameters=FrozenObject.of(
            {
                "schema": DECISION_FEATURE_SCHEMA if decision_view else FEATURE_SCHEMA,
                "scope": scope,
                "qwen": identity.value(),
                "rows": len(keys),
                "hidden_size": int(matrix.shape[1]),
                "dtype": "float32",
                "samples": len(samples),
                "serializer": view.parameters.value()["serializer"],
            }
        ),
    )
    store.publish(manifest)
    return manifest


@dataclass(frozen=True)
class LoadedFeatures:
    manifest: Manifest
    view: Manifest
    samples: tuple[ModelSample, ...]
    matrix: NDArray[np.float32]
    rows: tuple[tuple[int, ...], ...]


def load_features(store: ArtifactStore, feature_id: str) -> LoadedFeatures:
    manifest = store.get_manifest(feature_id)
    parameters = manifest.parameters.value()
    if manifest.kind != "feature_set" or parameters.get("schema") not in {
        FEATURE_SCHEMA,
        DECISION_FEATURE_SCHEMA,
    }:
        raise BoundaryError("features", "unsupported_contract")
    if len(manifest.parents) != 1 or {p.role for p in manifest.payloads} != {"features", "index"}:
        raise BoundaryError("features", "payload_inventory_mismatch")
    view, samples = load_model_view(store, manifest.parent("model_view"))
    identity = FrozenObject.of(parameters.get("qwen", {}))
    scope = view.parameters.value()["scope"]
    decision_view = view.parameters.value()["schema"] == "stpd/decision-model-view-v1"
    if (parameters.get("schema") == DECISION_FEATURE_SCHEMA) != decision_view:
        raise BoundaryError("features", "view_schema_mismatch")
    validate_qwen_identity(identity, scope, decision_view=decision_view)
    keys, expected_rows, _ = feature_index(samples, identity)
    if manifest.payload("index").size > 64 * 1024 * 1024:
        raise BoundaryError("features", "index_size_limit")
    index_raw = b"".join(store.read_payload(manifest.payload("index")))
    expected_index = {"keys": list(keys), "sample_rows": [list(row) for row in expected_rows]}
    if decode_json(index_raw) != expected_index or index_raw != json_bytes(expected_index):
        raise BoundaryError("features", "candidate_alignment_mismatch")
    hidden_size = unsigned(parameters.get("hidden_size"), "features.hidden_size")
    if not 0 < hidden_size <= 65536:
        raise BoundaryError("features", "hidden_size_limit")
    expected_size = len(keys) * hidden_size * 4
    if expected_size > 2 * 1024**3:
        raise BoundaryError("features", "matrix_size_limit")
    payload = manifest.payload("features")
    if not expected_size < payload.size <= expected_size + 16384:
        raise BoundaryError("features", "npy_payload_size_mismatch")
    with tempfile.TemporaryFile("w+b") as handle:
        for chunk in store.read_payload(payload):
            handle.write(chunk)
        handle.seek(0)
        try:
            version = np.lib.format.read_magic(handle)
            if version != (1, 0):
                raise BoundaryError("features", "unsupported_npy_version")
            shape, fortran, dtype = np.lib.format.read_array_header_1_0(handle)
            if shape != (len(keys), hidden_size) or fortran or dtype != np.dtype("<f4"):
                raise BoundaryError("features", "npy_header_mismatch")
            if handle.tell() + expected_size != payload.size:
                raise BoundaryError("features", "npy_payload_size_mismatch")
            handle.seek(0)
            matrix = np.load(handle, allow_pickle=False)
        except (ValueError, EOFError, OSError) as error:
            if isinstance(error, BoundaryError):
                raise
            raise BoundaryError("features", "malformed_npy") from None
    if (
        not isinstance(matrix, np.ndarray)
        or matrix.dtype != np.dtype("<f4")
        or matrix.shape != (len(keys), hidden_size)
        or hidden_size == 0
        or not np.isfinite(matrix).all()
        or parameters.get("rows") != len(keys)
        or parameters.get("samples") != len(samples)
        or parameters.get("scope") != scope
        or parameters.get("dtype") != "float32"
        or parameters.get("serializer") != view.parameters.value()["serializer"]
    ):
        raise BoundaryError("features", "matrix_or_identity_mismatch")
    matrix.flags.writeable = False
    return LoadedFeatures(manifest, view, samples, matrix, expected_rows)
