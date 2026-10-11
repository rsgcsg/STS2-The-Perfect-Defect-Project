"""Existing export-journal ordering and explicit unknown reconciliation, no NN."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
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
    OPERATIONAL_COMPATIBILITY_PROFILE,
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
        "model",
        target,
        parameters=FrozenObject.of(
            {"schema": COMPATIBLE_MODEL_SCHEMA, "compatibility_profile": COMPATIBILITY_PROFILE}
        ),
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
    # This unit fixture covers journal ordering only. The managed-workspace
    # regression exercises the real marker-bound publication selector.
    monkeypatch.setattr(selected, "_native_publication_store", lambda reader, original: store)
    monkeypatch.setattr(owner, "require_native_models", lambda *args: None)
    monkeypatch.setattr(owner, "source_identity", lambda root: target)
    module = ModuleType("stpd.policy.native_structured_export")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return selected, original, derived, records, module


def settled(service):
    assert service.thread is not None
    service.thread.join(timeout=5)
    assert not service.thread.is_alive()
    return service.status()["operation"]


def install_publisher(
    service,
    original,
    derived,
    records,
    module,
    *,
    fail=None,
    selected_profile=COMPATIBILITY_PROFILE,
):
    def publish(store, original_id, *, producer, profile, before_publish):
        assert original_id == original.artifact_id
        assert profile == selected_profile and producer == derived.producer
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


def test_explicit_operational_profile_reaches_existing_owner_and_exact_completed_receipt(
    service, monkeypatch
):
    selected, original, derived, records, module = service
    derived = replace(
        derived,
        parameters=FrozenObject.of(
            {
                "schema": COMPATIBLE_MODEL_SCHEMA,
                "compatibility_profile": OPERATIONAL_COMPATIBILITY_PROFILE,
            }
        ),
    )
    install_publisher(
        selected,
        original,
        derived,
        records,
        module,
        selected_profile=OPERATIONAL_COMPATIBILITY_PROFILE,
    )
    monkeypatch.setattr(owner, "_native_package", lambda *args, **kwargs: 20)
    selected.start(original.artifact_id, compatibility_profile=OPERATIONAL_COMPATIBILITY_PROFILE)
    result = settled(selected)
    assert result["status"] == "completed"
    assert result["compatibility_profile"] == OPERATIONAL_COMPATIBILITY_PROFILE
    assert selected._read()["native_compatibility"]["profile"] == OPERATIONAL_COMPATIBILITY_PROFILE


def test_pending_profile_cannot_be_changed_to_another_supported_intent(service, monkeypatch):
    selected, original, derived, records, module = service
    derived = replace(
        derived,
        parameters=FrozenObject.of(
            {
                "schema": COMPATIBLE_MODEL_SCHEMA,
                "compatibility_profile": OPERATIONAL_COMPATIBILITY_PROFILE,
            }
        ),
    )
    install_publisher(
        selected,
        original,
        derived,
        records,
        module,
        fail="intent_only",
        selected_profile=OPERATIONAL_COMPATIBILITY_PROFILE,
    )
    monkeypatch.setattr(owner, "_native_package", lambda *args, **kwargs: 20)
    selected.start(original.artifact_id, compatibility_profile=OPERATIONAL_COMPATIBILITY_PROFILE)
    assert settled(selected)["status"] == "interrupted"
    before = selected._path().read_bytes()
    with pytest.raises(BoundaryError, match="compatibility_intent_mismatch"):
        selected.start(original.artifact_id, compatibility_profile=COMPATIBILITY_PROFILE)
    assert selected._path().read_bytes() == before


def test_selected_operational_profile_cannot_publish_borrowed_legacy_derivative(
    service, monkeypatch
):
    selected, original, _, _, _ = service
    install_publisher(*service, selected_profile=OPERATIONAL_COMPATIBILITY_PROFILE)
    monkeypatch.setattr(owner, "_native_package", lambda *args, **kwargs: 20)
    selected.start(original.artifact_id, compatibility_profile=OPERATIONAL_COMPATIBILITY_PROFILE)
    result = settled(selected)
    assert result["status"] == "failed" and result["error_code"] == "compatibility_profile_mismatch"
    assert "derived_model_id" not in result


def test_selected_legacy_profile_cannot_publish_borrowed_operational_derivative(
    service, monkeypatch
):
    selected, original, derived, records, module = service
    derived = replace(
        derived,
        parameters=FrozenObject.of(
            {
                "schema": COMPATIBLE_MODEL_SCHEMA,
                "compatibility_profile": OPERATIONAL_COMPATIBILITY_PROFILE,
            }
        ),
    )
    install_publisher(selected, original, derived, records, module)
    monkeypatch.setattr(owner, "_native_package", lambda *args, **kwargs: 20)
    selected.start(original.artifact_id, compatibility_profile=COMPATIBILITY_PROFILE)
    result = settled(selected)
    assert result["status"] == "failed" and result["error_code"] == "compatibility_profile_mismatch"
    assert "derived_model_id" not in result


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


def test_dirty_source_is_rejected_before_any_migration_intent_or_publication(service, monkeypatch):
    selected, original, derived, records, module = service

    def dirty(root):
        raise BoundaryError("source", "clean_checkout_required")

    monkeypatch.setattr(owner, "source_identity", dirty)
    module.publish_native_compatible_model = lambda *args, **kwargs: pytest.fail("published")
    with pytest.raises(BoundaryError, match="clean_checkout_required"):
        selected.start(original.artifact_id, compatibility_profile=COMPATIBILITY_PROFILE)
    assert not selected._path().exists()
    assert derived.artifact_id not in records


@pytest.mark.parametrize("drift", ["head", "dirty"])
def test_threaded_source_drift_is_rejected_before_derived_publication_intent(
    service, monkeypatch, drift
):
    selected, original, derived, records, _ = service
    calls = []

    def changed_source(root):
        calls.append(root)
        if len(calls) == 1:
            return derived.producer
        if drift == "dirty":
            raise BoundaryError("source", "clean_checkout_required")
        return Producer(COMPATIBILITY_REPOSITORY, "c" * 40, TARGET_LOCK_SHA256)

    monkeypatch.setattr(owner, "source_identity", changed_source)
    install_publisher(*service)
    selected.start(original.artifact_id, compatibility_profile=COMPATIBILITY_PROFILE)
    result = settled(selected)
    assert len(calls) == 2
    assert result["status"] == "failed"
    assert result["error_code"] == (
        "clean_checkout_required" if drift == "dirty" else "compatibility_source_changed"
    )
    assert result["model_id"] == result["original_model_id"] == original.artifact_id
    assert "derived_model_id" not in result
    assert derived.artifact_id not in records
