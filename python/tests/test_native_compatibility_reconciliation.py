"""Explicit observations/new intents over real managed stores; no inference."""

from __future__ import annotations

import hashlib
import json
import sys
from types import ModuleType

import pytest

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.workbench import local_model_export as owner
from spireagent.workbench.developer import ProjectConfig, atomic_json
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.managed_local_workspace import create_managed_workspace
from stpd.native_code_scope import (
    COMMON_MODEL_SCHEMA,
    COMPATIBILITY_PROFILE,
    COMPATIBLE_MODEL_SCHEMA,
    OPERATIONAL_COMPATIBILITY_PROFILE,
)


@pytest.fixture
def interrupted(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    create_managed_workspace(state)
    config = ProjectConfig(state, "", "", None, {})
    _, writable, _ = LocalDatasetService(config)._selected()
    old_producer = Producer("fixture://migration", "a" * 40, "b" * 64)
    new_producer = Producer("fixture://migration", "c" * 40, "b" * 64)
    original = Manifest(
        "model", old_producer, parameters=FrozenObject.of({"schema": COMMON_MODEL_SCHEMA})
    )
    writable.publish(original)
    derived = Manifest(
        "model",
        old_producer,
        (Parent("original_model", original.artifact_id),),
        parameters=FrozenObject.of(
            {"schema": COMPATIBLE_MODEL_SCHEMA, "compatibility_profile": COMPATIBILITY_PROFILE}
        ),
    )
    journal = {
        "schema": owner.SCHEMA,
        "status": "pending",
        "operation_id": "d" * 32,
        "model_id": derived.artifact_id,
        "derived_model_id": derived.artifact_id,
        "model_type": "native",
        "store_root": str(writable.blobs.root),
        "native_compatibility": {
            "profile": COMPATIBILITY_PROFILE,
            "original_model_id": original.artifact_id,
            "export_producer": old_producer.to_dict(),
        },
    }
    service = owner.LocalModelExport(config)
    atomic_json(service._path(), journal)
    (state / owner.LOCK_FILE).touch()
    monkeypatch.setattr(owner, "source_identity", lambda root: new_producer)
    monkeypatch.setattr(owner, "require_native_models", lambda *args: None)
    return service, writable, original, derived, journal, new_producer


def reconcile(case):
    service, _, original, derived, journal, _ = case
    return service.reconcile_native_compatibility(
        journal["operation_id"],
        original_model_id=original.artifact_id,
        derived_model_id=derived.artifact_id,
    )


def test_absence_is_immutable_observation_and_keeps_original_unknown_journal_bytes(
    interrupted, monkeypatch
):
    service, store, _, derived, journal, _ = interrupted
    raw = service._path().read_bytes()
    manifests = store.manifest_ids()
    receipt = reconcile(interrupted)
    assert receipt["outcome"] == "manifest_absent_at_reconciliation"
    assert receipt["original_journal_utf8"].encode() == raw
    assert receipt["original_journal_sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["derived_manifest_sha256"] is None
    assert service._path().read_bytes() == raw
    assert store.manifest_ids() == manifests and derived.artifact_id not in manifests
    assert service.status()["operation"]["status"] == "interrupted"
    assert (
        service.status()["operation"]["reconciliation"]["receipt_sha256"]
        == receipt["receipt_sha256"]
    )
    # Repeated explicit inspection reuses the original time/bytes, even if the
    # current reconciliation producer advances. It never overwrites history.
    monkeypatch.setattr(
        owner, "source_identity", lambda root: Producer("fixture://migration", "e" * 40, "b" * 64)
    )
    assert reconcile(interrupted) == receipt
    immutable = (
        service.config.state_dir / owner.RECONCILIATION_ROOT / (journal["operation_id"] + ".json")
    )
    assert hashlib.sha256(immutable.read_bytes()).hexdigest() == receipt["receipt_sha256"]


def test_present_manifest_is_verified_without_republication_and_does_not_unlock_new_migration(
    interrupted, monkeypatch
):
    service, store, original, derived, _, _ = interrupted
    store.publish(derived)
    raw = service._path().read_bytes()
    seen = []

    def verify(reader, model, destination, *, materialize=False):
        assert reader.blobs.readonly and model.artifact_id == derived.artifact_id
        seen.append((model.artifact_id, materialize))
        return 10

    monkeypatch.setattr(owner, "_native_package", verify)
    receipt = reconcile(interrupted)
    assert receipt["outcome"] == "manifest_present_verified_at_reconciliation"
    assert seen == [(derived.artifact_id, True)]
    assert receipt["derived_manifest_sha256"] == hashlib.sha256(derived.to_bytes()).hexdigest()
    assert service._path().read_bytes() == raw
    with pytest.raises(BoundaryError, match="reconciliation_receipt_mismatch"):
        service.start(
            original.artifact_id,
            compatibility_profile=COMPATIBILITY_PROFILE,
            prior_native_reconciliation=receipt["receipt_sha256"],
        )


def test_workspace_drift_and_live_export_lock_do_not_produce_a_new_observation(
    interrupted, monkeypatch, tmp_path
):
    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.store import ManifestArtifactStore
    from spireagent.workbench.developer_server import instance_lock

    service, _, original, derived, journal, _ = interrupted
    raw = service._path().read_bytes()
    with instance_lock(service.config.state_dir / owner.LOCK_FILE, create=False), pytest.raises(
        BoundaryError, match="already_running"
    ):
        reconcile(interrupted)
    alternate = ManifestArtifactStore(LocalBlobStore(tmp_path / "alternate"))
    alternate.publish(original)
    reader = ManifestArtifactStore(
        LocalBlobStore(alternate.blobs.root, create=False, readonly=True)
    )
    monkeypatch.setattr(service, "_source", lambda identity: reader)
    with pytest.raises(BoundaryError, match="reconciliation_workspace_mismatch"):
        reconcile(interrupted)
    assert service._path().read_bytes() == raw
    assert not (service.config.state_dir / owner.RECONCILIATION_ROOT).exists()


def test_present_incompatible_loader_keeps_unknown_and_writes_no_receipt(interrupted, monkeypatch):
    service, store, _, derived, _, _ = interrupted
    store.publish(derived)
    raw = service._path().read_bytes()

    def reject(*args, **kwargs):
        raise BoundaryError("native_package", "package_identity_mismatch")

    monkeypatch.setattr(owner, "_native_package", reject)
    with pytest.raises(BoundaryError, match="package_identity_mismatch"):
        reconcile(interrupted)
    assert service._path().read_bytes() == raw
    assert not (service.config.state_dir / owner.RECONCILIATION_ROOT).exists()


@pytest.mark.parametrize(
    "change", ["wrong_hash", "same_producer", "journal_drift", "manifest_appeared", "other_profile"]
)
def test_no_pending_family_or_old_producer_can_bypass_the_bound_receipt(
    interrupted, monkeypatch, change
):
    service, store, original, derived, journal, _ = interrupted
    receipt = reconcile(interrupted)
    requested = receipt["receipt_sha256"]
    profile = COMPATIBILITY_PROFILE
    if change == "wrong_hash":
        requested = "e" * 64
    elif change == "same_producer":
        monkeypatch.setattr(owner, "source_identity", lambda root: derived.producer)
    elif change == "journal_drift":
        journal["operation_id"] = "f" * 32
        atomic_json(service._path(), journal)
    elif change == "manifest_appeared":
        store.publish(derived)
    else:
        profile = None
    raw = service._path().read_bytes()
    with pytest.raises(BoundaryError):
        service.start(
            original.artifact_id,
            compatibility_profile=profile,
            prior_native_reconciliation=requested,
        )
    assert service._path().read_bytes() == raw


def test_new_profile_cannot_borrow_an_old_profile_absence_receipt(interrupted):
    service, _, original, _, _, _ = interrupted
    receipt = reconcile(interrupted)
    before = service._path().read_bytes()
    with pytest.raises(BoundaryError, match="reconciliation_receipt_mismatch"):
        service.start(original.artifact_id, compatibility_profile=OPERATIONAL_COMPATIBILITY_PROFILE,
                      prior_native_reconciliation=receipt["receipt_sha256"])
    assert service._path().read_bytes() == before


@pytest.mark.parametrize(
    "selected_profile", [COMPATIBILITY_PROFILE, OPERATIONAL_COMPATIBILITY_PROFILE]
)
def test_present_reconciliation_cannot_borrow_other_derived_profile(
    interrupted, monkeypatch, selected_profile
):
    from dataclasses import replace

    service, store, original, derived, journal, _ = interrupted
    other = (
        OPERATIONAL_COMPATIBILITY_PROFILE
        if selected_profile == COMPATIBILITY_PROFILE
        else COMPATIBILITY_PROFILE
    )
    foreign = replace(
        derived,
        parameters=FrozenObject.of(
            {"schema": COMPATIBLE_MODEL_SCHEMA, "compatibility_profile": other}
        ),
    )
    store.publish(foreign)
    journal.update(model_id=foreign.artifact_id, derived_model_id=foreign.artifact_id)
    journal["native_compatibility"]["profile"] = selected_profile
    atomic_json(service._path(), journal)
    monkeypatch.setattr(owner, "_native_package", lambda *args, **kwargs: 10)
    before = service._path().read_bytes()
    with pytest.raises(BoundaryError, match="reconciliation_intent_mismatch"):
        service.reconcile_native_compatibility(
            journal["operation_id"],
            original_model_id=original.artifact_id,
            derived_model_id=foreign.artifact_id,
        )
    assert service._path().read_bytes() == before


def test_separate_explicit_new_intent_links_receipt_and_cannot_republish_old_id(
    interrupted, monkeypatch
):
    service, store, original, old_derived, old_journal, producer = interrupted
    raw = service._path().read_bytes()
    receipt = reconcile(interrupted)
    with pytest.raises(BoundaryError, match="previous_export_outcome_unknown"):
        service.start(original.artifact_id, compatibility_profile=COMPATIBILITY_PROFILE)
    new = Manifest(
        "model",
        producer,
        (Parent("original_model", original.artifact_id),),
        parameters=FrozenObject.of(
            {"schema": COMPATIBLE_MODEL_SCHEMA, "compatibility_profile": COMPATIBILITY_PROFILE}
        ),
    )
    module = ModuleType("stpd.policy.native_structured_export")
    published = []

    def publish(writable, identity, *, producer, profile, before_publish):
        assert not writable.blobs.readonly and identity == original.artifact_id
        pending = service._read()
        assert pending["operation_id"] != old_journal["operation_id"]
        assert pending["prior_native_reconciliation"]["receipt_sha256"] == receipt["receipt_sha256"]
        before_publish(new)
        published.append(writable.publish(new))
        return new

    module.publish_native_compatible_model = publish
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(owner, "_native_package", lambda *args, **kwargs: 10)
    service.start(
        original.artifact_id,
        compatibility_profile=COMPATIBILITY_PROFILE,
        prior_native_reconciliation=receipt["receipt_sha256"],
    )
    service.thread.join(timeout=5)
    assert not service.thread.is_alive()
    current = service.status()["operation"]
    assert current["status"] == "completed" and current["derived_model_id"] == new.artifact_id
    assert new.artifact_id != old_derived.artifact_id and published == [new.artifact_id]
    assert old_derived.artifact_id not in store.manifest_ids()
    assert current["prior_native_reconciliation"]["operation_id"] == old_journal["operation_id"]
    assert receipt["original_journal_utf8"].encode() == raw
    assert service._native_reconciliation(old_journal["operation_id"]) == receipt


def test_wrong_reconciliation_ids_and_tampered_receipt_cannot_release_the_slot(interrupted):
    service, _, original, derived, journal, _ = interrupted
    raw = service._path().read_bytes()
    with pytest.raises(BoundaryError, match="reconciliation_intent_mismatch"):
        service.reconcile_native_compatibility(
            journal["operation_id"],
            original_model_id="f" * 64,
            derived_model_id=derived.artifact_id,
        )
    receipt = reconcile(interrupted)
    path = (
        service.config.state_dir / owner.RECONCILIATION_ROOT / (journal["operation_id"] + ".json")
    )
    value = json.loads(path.read_bytes())
    value["original_model_id"] = "f" * 64
    path.write_text(json.dumps(value))
    with pytest.raises(BoundaryError):
        service.start(
            original.artifact_id,
            compatibility_profile=COMPATIBILITY_PROFILE,
            prior_native_reconciliation=receipt["receipt_sha256"],
        )
    assert service._path().read_bytes() == raw
