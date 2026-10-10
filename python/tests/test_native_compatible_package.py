"""Synthetic historical metadata and fresh tensors; no original weights or fit."""

from __future__ import annotations

import copy
import hashlib
import io
from dataclasses import replace
from pathlib import Path

import pytest
import torch

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from stpd.canonical import semantic_hash
from stpd.models.structured_m2 import StructuredM2
from stpd.native_code_scope import (
    COMPATIBILITY_PROFILE,
    COMPATIBILITY_REPOSITORY,
    ORIGINAL_EXPORT_REVISION,
    ORIGINAL_LOCK_SHA256,
    TARGET_LOCK_SHA256,
    compatibility_original_identity,
)
from stpd.native_graph_spec import NativeGraphControl
from stpd.native_sampled_carry_spec import FEATURE_PROJECTION, STATE_FORMAT, sampled_agent_spec
from stpd.native_training_source_spec import (
    PACKAGE_SCHEMA,
    SOURCE3_PROFILE,
    source_qualification,
)
from stpd.ordered_source_spec import (
    SAMPLED_PROJECTION_SPEC,
    SAMPLED_TARGET_SPEC,
    SAMPLED_VIEW,
    validation_identity,
    view_input_spec,
)
from stpd.policy import native_structured_export as owner
from stpd.policy.native_operational_outcome import owned_current_known_stale_policy
from stpd.structured_code_scope import exporter_runtime, inference_runtime


@pytest.fixture(autouse=True)
def bounded_cpu():
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


@pytest.fixture
def original(tmp_path, monkeypatch):
    """Hand-built fixture asserts only synthetic own-byte package mechanics."""
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    origin = Producer(COMPATIBILITY_REPOSITORY, ORIGINAL_EXPORT_REVISION, ORIGINAL_LOCK_SHA256)
    # Portable source CI may have a shallow checkout. Use the committed exact
    # Git-extracted proof at that read boundary; every production proof checker
    # still runs. The separate proof test covers missing Git fail-closed, and
    # actual original/target qualification reads the real original commit.
    proof = decode_json(
        (
            Path(__file__).parent / "fixtures/native_compatibility/evidence_rc27_basis.json"
        ).read_bytes()
    )

    def recorded_proof(actual):
        assert actual == origin
        return copy.deepcopy(proof)

    monkeypatch.setattr(owner, "native_compatibility_proof", recorded_proof)
    parents = []
    for role, kind in (
        ("run", "run"),
        ("checkpoint", "checkpoint"),
        ("training_input", "training_input"),
    ):
        artifact = Manifest(kind, origin, parameters=FrozenObject.of({"fixture": role}))
        parents.append(Parent(role, store.publish(artifact)))
    ids = {parent.role: parent.artifact_id for parent in parents}
    data = "d" * 64
    source = {
        "source_profile": SOURCE3_PROFILE,
        "kind": "agent_protocol",
        "cohort": "agent_protocol",
        "data_sha256": data,
        "source_artifact_id": "e" * 64,
        "training_input_id": ids["training_input"],
        "verification_identity": validation_identity(
            data,
            "agent_protocol",
            [{"raw_id": "1" * 64, "admission_id": "2" * 64}],
            projection_spec=SAMPLED_PROJECTION_SPEC,
            target_spec=SAMPLED_TARGET_SPEC,
        ),
    }
    control = NativeGraphControl()
    model = StructuredM2(seed=0, model_control=control)
    raw = owner.encode_native_weights(model)
    identity = compatibility_original_identity()
    train_producer = Producer(COMPATIBILITY_REPOSITORY, "f" * 40, ORIGINAL_LOCK_SHA256)
    platform = exporter_runtime()
    provenance = {
        "training_producer": train_producer.to_dict(),
        "export_producer": origin.to_dict(),
        "run_id": ids["run"],
        "training_input_id": ids["training_input"],
        "checkpoint_id": ids["checkpoint"],
        "export_runtime": platform,
    }
    body = {
        "schema": PACKAGE_SCHEMA,
        "graph": owner.native_graph(control),
        "projection": FEATURE_PROJECTION,
        "input_spec": view_input_spec(SAMPLED_VIEW),
        "agent_spec": sampled_agent_spec(
            control, execution_policy=owned_current_known_stale_policy()
        ),
        "state_format_version": STATE_FORMAT,
        "seed": 0,
        "code_identity": identity,
        "adapter_code_sha256": semantic_hash(identity),
        "weights": {
            "path": owner.WEIGHTS_NAME,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        },
        "producer": origin.to_dict(),
        "source": source,
        "training": {"fixture": "no_fit"},
        "runtime": inference_runtime(str(torch.__version__)),
        "export_runtime": platform,
        "qualification": source_qualification(source),
        "provenance": provenance,
    }
    package = {**body, "model_id": semantic_hash(body)}
    # Fixture metadata is checked through the same closed original parser;
    # strict public loading below must reject this old recorded basis.
    owner._checked_native_manifest(json_bytes(package), recorded_basis=identity)
    metadata = store.put_payload(
        "package_manifest", io.BytesIO(json_bytes(package)), "application/json"
    )
    weights = store.put_payload("weights", io.BytesIO(raw), "application/vnd.stpd.tensor-tree")
    artifact = Manifest(
        "model",
        origin,
        tuple(parents),
        (metadata, weights),
        FrozenObject.of(owner._native_model_parameters_checked(package, "a" * 32)),
    )
    store.publish(artifact)
    return store, artifact, package, raw


def target_producer():
    return Producer(COMPATIBILITY_REPOSITORY, "b" * 40, TARGET_LOCK_SHA256)


def package_for(store, artifact):
    return decode_json(b"".join(store.read_payload(artifact.payload("package_manifest"))))


def rehash(package):
    package["model_id"] = semantic_hash({k: v for k, v in package.items() if k != "model_id"})
    return json_bytes(package)


def test_new_identity_same_tensor_bytes_and_original_provenance_and_parent(original, tmp_path):
    store, old, original_package, raw = original
    before = old.to_bytes()
    callbacks = []
    new = owner.publish_native_compatible_model(
        store,
        old.artifact_id,
        producer=target_producer(),
        profile=COMPATIBILITY_PROFILE,
        before_publish=lambda value: callbacks.append((value.artifact_id, store.manifest_ids())),
    )
    assert callbacks and new.artifact_id not in callbacks[0][1]
    assert old.to_bytes() == before == store.get_manifest(old.artifact_id).to_bytes()
    package = package_for(store, new)
    assert new.artifact_id != old.artifact_id
    assert package["model_id"] != original_package["model_id"]
    assert new.parent("original_model") == old.artifact_id
    assert new.payload("weights") == old.payload("weights")
    for field in (
        "graph",
        "projection",
        "input_spec",
        "agent_spec",
        "state_format_version",
        "source",
        "training",
        "qualification",
        "provenance",
        "weights",
        "runtime",
    ):
        assert package[field] == original_package[field]
    assert package["producer"] == target_producer().to_dict()
    assert package["provenance"]["training_producer"]["uv_lock_sha256"] == ORIGINAL_LOCK_SHA256
    receipt = owner.export_native_model(store, new.artifact_id, tmp_path / "derived")
    assert receipt["artifact_id"] == new.artifact_id
    checked, loaded = owner.load_native_package(tmp_path / "derived")
    assert checked == package
    assert owner.encode_native_weights(loaded) == raw
    owner.require_native_model_package(new, checked)


def test_strict_original_package_keeps_rejecting_current_environment(original, tmp_path):
    _, _, package, raw = original
    target = tmp_path / "old"
    target.mkdir()
    (target / owner.MANIFEST_NAME).write_bytes(json_bytes(package))
    (target / owner.WEIGHTS_NAME).write_bytes(raw)
    with pytest.raises(BoundaryError, match="package_identity_mismatch"):
        owner.load_native_package(target)


@pytest.mark.parametrize(
    "field",
    ["input_spec", "agent_spec", "state_format_version", "training", "qualification", "weights"],
)
def test_rehashed_semantic_changes_are_not_an_evidence_only_migration(original, field):
    store, old, _, _ = original
    derived = owner.publish_native_compatible_model(
        store, old.artifact_id, producer=target_producer(), profile=COMPATIBILITY_PROFILE
    )
    package = copy.deepcopy(package_for(store, derived))
    if isinstance(package[field], dict):
        package[field]["unreviewed_change"] = True
    else:
        package[field] += "-changed"
    with pytest.raises(BoundaryError):
        owner._native_manifest(rehash(package))


def test_wrong_original_parent_and_changed_nested_original_payload_are_rejected(original):
    store, old, _, _ = original
    new = owner.publish_native_compatible_model(
        store, old.artifact_id, producer=target_producer(), profile=COMPATIBILITY_PROFILE
    )
    package = package_for(store, new)
    forged = replace(
        new,
        parents=tuple(
            Parent(p.role, "c" * 64) if p.role == "original_model" else p for p in new.parents
        ),
    )
    with pytest.raises(BoundaryError, match="model_package_or_parent_binding"):
        owner.require_native_model_package(forged, package)
    changed = copy.deepcopy(package)
    changed["compatibility"]["original_package"]["training"]["changed"] = True
    with pytest.raises(BoundaryError):
        owner._native_manifest(rehash(changed))


def test_unsupported_profile_recursive_parent_wrong_runtime_or_weight_bytes_fail_closed(
    original, tmp_path
):
    store, old, _, _ = original
    with pytest.raises(BoundaryError, match="unsupported_profile"):
        owner.publish_native_compatible_model(
            store, old.artifact_id, producer=target_producer(), profile="unknown"
        )
    new = owner.publish_native_compatible_model(
        store, old.artifact_id, producer=target_producer(), profile=COMPATIBILITY_PROFILE
    )
    with pytest.raises(BoundaryError, match="original_sampled_trained_model_required"):
        owner.publish_native_compatible_model(
            store, new.artifact_id, producer=target_producer(), profile=COMPATIBILITY_PROFILE
        )
    package = package_for(store, new)
    changed = copy.deepcopy(package)
    changed["compatibility"]["original_package"]["runtime"]["fixture"] = "wrong"
    with pytest.raises(BoundaryError):
        owner._native_manifest(rehash(changed))
    target = tmp_path / "weights"
    owner.export_native_model(store, new.artifact_id, target)
    path = target / owner.WEIGHTS_NAME
    path.write_bytes(path.read_bytes()[:-1] + b"x")
    with pytest.raises(BoundaryError, match="weights_digest_mismatch"):
        owner.load_native_package(target)
