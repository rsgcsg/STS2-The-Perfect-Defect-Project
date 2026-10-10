"""Existing export-journal ordering and explicit unknown reconciliation, no NN."""

from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.workbench import local_model_export as owner
from spireagent.workbench.developer import ProjectConfig, atomic_json
from stpd.native_code_scope import (
    COMMON_MODEL_SCHEMA,
    COMPATIBILITY_PROFILE,
    COMPATIBILITY_REPOSITORY,
    COMPATIBLE_MODEL_SCHEMA,
    ORIGINAL_EXPORT_REVISION,
    ORIGINAL_LOCK_SHA256,
    TARGET_LOCK_SHA256,
)


@pytest.fixture
def service(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    root = tmp_path / "store"
    root.mkdir()
    origin = Producer(COMPATIBILITY_REPOSITORY, ORIGINAL_EXPORT_REVISION, ORIGINAL_LOCK_SHA256)
    target = Producer(COMPATIBILITY_REPOSITORY, "b" * 40, TARGET_LOCK_SHA256)
    original = Manifest(
        "model", origin, parameters=FrozenObject.of({"schema": COMMON_MODEL_SCHEMA})
    )
    derived = Manifest(
        "model", target, parameters=FrozenObject.of({"schema": COMPATIBLE_MODEL_SCHEMA})
    )
    records = {original.artifact_id: original}
    store = SimpleNamespace(
        blobs=SimpleNamespace(root=root),
        get_manifest=records.__getitem__,
        publish=lambda value: records.setdefault(value.artifact_id, value),
        put_payload=lambda *args: None,
    )
    selected = owner.LocalModelExport(ProjectConfig(state, "", "", None, {}))
    monkeypatch.setattr(selected, "_workspace", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(owner, "require_native_models", lambda *args: None)
    monkeypatch.setattr(
        owner,
        "tool_identity",
        lambda: {"source_revision": target.source_revision, "uv_lock_sha256": TARGET_LOCK_SHA256},
    )
    module = ModuleType("stpd.policy.native_structured_export")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return selected, original, derived, records, module


def settled(service):
    assert service.thread is not None
    service.thread.join(timeout=5)
    assert not service.thread.is_alive()
    return service.status()["operation"]


def install_publisher(service, original, derived, records, module, *, fail=None):
    def publish(store, original_id, *, producer, profile, before_publish):
        assert original_id == original.artifact_id
        assert profile == COMPATIBILITY_PROFILE and producer == derived.producer
        intent = service._read()
        assert intent["status"] == "pending" and intent["model_id"] == original_id
        assert intent["native_compatibility"]["original_model_id"] == original_id
        if fail == "before_intent":
            raise BoundaryError("fixture", "prepublication_failed")
        before_publish(derived)
        intent = json.loads(service._path().read_bytes())
        assert intent["model_id"] == intent["derived_model_id"] == derived.artifact_id
        assert intent["native_compatibility"]["original_model_id"] == original_id
        assert derived.artifact_id not in records
        if fail != "intent_only":
            records[derived.artifact_id] = derived
        if fail is not None:
            raise BoundaryError("fixture", "publication_outcome_unknown")
        return derived

    module.publish_native_compatible_model = publish


def test_same_operation_retains_both_ids_and_original_completed_receipt(service, monkeypatch):
    selected, original, derived, records, module = service
    old_receipt = {
        "schema": owner.SCHEMA,
        "status": "completed",
        "model_type": "native",
        "operation_id": "a" * 32,
        "model_id": original.artifact_id,
        "store_root": str(selected._workspace().store.blobs.root),
        "payload_bytes": 10,
    }
    atomic_json(selected._path(), old_receipt)
    install_publisher(*service)
    exports = []

    def verify(store, model, destination, *, materialize=False):
        exports.append((model.artifact_id, destination.name, materialize))
        assert model == derived
        return 20

    monkeypatch.setattr(owner, "_native_package", verify)
    selected.start(original.artifact_id, compatibility_profile=COMPATIBILITY_PROFILE)
    result = settled(selected)
    assert result["status"] == "completed"
    assert result["model_id"] == result["derived_model_id"] == derived.artifact_id
    assert result["original_model_id"] == original.artifact_id
    assert exports == [(derived.artifact_id, derived.artifact_id, True)]
    assert selected._model_operation(original.artifact_id) == old_receipt
    assert selected._model_operation(derived.artifact_id)["status"] == "completed"


@pytest.mark.parametrize("failure", ["intent_only", "after_publication"])
def test_interrupted_publication_stays_unknown_and_original_is_never_automatically_replayed(
    service, monkeypatch, failure
):
    selected, original, derived, records, module = service
    install_publisher(*service, fail=failure)
    monkeypatch.setattr(owner, "_native_package", lambda *args, **kwargs: 20)
    selected.start(original.artifact_id, compatibility_profile=COMPATIBILITY_PROFILE)
    result = settled(selected)
    assert result["status"] == "interrupted"
    operation_id = result["operation_id"]
    assert result["derived_model_id"] == derived.artifact_id
    assert result["original_model_id"] == original.artifact_id
    with pytest.raises(BoundaryError, match="previous_export_outcome_unknown"):
        selected.start(original.artifact_id, compatibility_profile=COMPATIBILITY_PROFILE)
    if failure == "intent_only":
        with pytest.raises(KeyError):
            selected.start(derived.artifact_id)
        assert selected._read()["status"] == "pending"
    else:
        module.publish_native_compatible_model = lambda *args, **kwargs: pytest.fail("replayed")
        selected.start(derived.artifact_id)
        result = settled(selected)
        assert result["status"] == "completed"
        assert result["original_model_id"] == original.artifact_id
        assert result["operation_id"] == operation_id


def test_prepublication_failure_is_known_and_unknown_profile_does_not_journal(service):
    selected, original, _, _, _ = service
    with pytest.raises(BoundaryError, match="unsupported_native_compatibility_profile"):
        selected.start(original.artifact_id, compatibility_profile="unknown")
    assert not selected._path().exists()
    install_publisher(*service, fail="before_intent")
    selected.start(original.artifact_id, compatibility_profile=COMPATIBILITY_PROFILE)
    result = settled(selected)
    assert result["status"] == "failed" and result["error_code"] == "prepublication_failed"
    assert result["original_model_id"] == original.artifact_id
    assert "derived_model_id" not in result
