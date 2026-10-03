"""Registration phase budgets with real synthetic owner/export/roster validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_light_action_m0_canonical_cli import _cli, _synthetic_workspace
from test_public_m0_policy_port import capabilities

from spireagent.json_boundary import BoundaryError
from spireagent.workbench import local_model_export as export_module
from spireagent.workbench import local_model_registration as registration_module
from spireagent.workbench.developer import ProjectConfig
from spireagent.workbench.local_model_export import LocalModelExport
from spireagent.workbench.local_model_registration import LocalModelRegistration
from spireagent.workbench.local_models import LocalModelService


@pytest.fixture
def public_registration(tmp_path: Path, monkeypatch):
    import test_decision_store
    from platform_bundle3_fixture import bundle3

    monkeypatch.setattr(test_decision_store, "bundle3",
                        lambda path: bundle3(path, public_bindings=True))
    project, store_dir, dataset_id, owner = _synthetic_workspace(tmp_path)
    operation = "d" * 32
    common = ("--store", str(store_dir))
    prepared = _cli(
        monkeypatch, *common, "prepare-light-action-m0", "--project-config", str(project),
        "--dataset", dataset_id, "--operation", operation, "--backbone", "s",
        "--input-profile", "public_lite", "--train-limit", "8", "--dev-limit", "4",
    )
    trained = _cli(
        monkeypatch, *common, "train-light-action-m0", "--project-config", str(project),
        "--inputs", prepared["training_input_id"], "--operation", operation,
        "--recipe", "stage1a.dsimple.light-action.m0.s.v1", "--steps", "1",
    )
    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.store import ManifestArtifactStore

    store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
    model_id = store.get_manifest(trained["result_id"]).parent("model")
    config = ProjectConfig.load(project)
    exported = LocalModelExport(config, config_path=project)
    exported.start(model_id)
    assert exported.thread is not None
    exported.thread.join(timeout=30)
    assert exported.status()["operation"]["status"] == "completed"
    models = LocalModelService(config)
    service = LocalModelRegistration(config, exported, models)
    monkeypatch.setattr(registration_module, "validate_runtime_install", lambda *_args: {})
    monkeypatch.setattr(service, "_capabilities", lambda *_args, **_kwargs: capabilities())
    previous = service.register(model_id)
    assert previous["status"] == "registered" and previous["loaded"] is False
    return service, exported, model_id, models, owner, operation, previous


@pytest.mark.parametrize(("local_seconds", "online_seconds", "failure", "expected"), [
    (62.841, 1.0, None, None),
    (300.0, 0.0, None, "registration_timeout"),
    (62.841, 0.0, "revoked_use", "public_m0_lineage_invalid"),
    (62.841, 0.0, "receipt_mismatch", "verified_export_required"),
    (62.841, 45.0, None, "registration_timeout"),
])
def test_public_registration_separates_verification_and_online_budgets(
        public_registration, monkeypatch, local_seconds, online_seconds, failure, expected):
    service, exported, model_id, models, owner, operation, previous = public_registration
    roster = models.private_root / registration_module.REGISTRY
    old_roster = roster.read_bytes()
    journal = exported.config.state_dir / export_module.OPERATION_FILE
    old_journal = journal.read_bytes()
    package = exported.config.state_dir / export_module.EXPORT_ROOT / model_id
    old_package = {path.name: path.read_bytes() for path in package.iterdir()}
    old_selections = set((models.private_root / registration_module.REGISTRATIONS).iterdir())
    if failure == "revoked_use":
        with owner.transaction() as db:
            db.execute("DELETE FROM curation_source_uses WHERE reference=?", (operation,))
            db.execute("DELETE FROM curation_uses WHERE reference=?", (operation,))
    elif failure == "receipt_mismatch":
        value = json.loads(old_journal)
        value["verified_receipt"]["package_sha256"] = "e" * 64
        journal.write_text(json.dumps(value), encoding="utf-8")
        old_journal = journal.read_bytes()

    clock = [0.0]
    events = []
    monkeypatch.setattr(registration_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(export_module, "monotonic", lambda: clock[0])
    verify = exported.verified_public_m0_for_registration

    def timed_verification(identity, *, deadline):
        events.append("verify")
        clock[0] += local_seconds
        return verify(identity, deadline=deadline)

    def timed_capabilities(_sdk, *, deadline, input_profile):
        events.append("capabilities")
        assert input_profile == registration_module.PUBLIC_M0_PROFILE
        assert deadline == local_seconds + registration_module.REGISTRATION_SECONDS
        clock[0] += online_seconds
        value = capabilities()
        value["host"]["version"] = "synthetic-new-connector"
        return value

    monkeypatch.setattr(exported, "verified_public_m0_for_registration", timed_verification)
    monkeypatch.setattr(service, "_capabilities", timed_capabilities)
    if expected is None:
        result = service.register(model_id)
        assert result["status"] == "registered" and result["loaded"] is False
        assert result["selection_id"] != previous["selection_id"]
        entries = json.loads(roster.read_bytes())["policies"]
        assert entries[:-1] == json.loads(old_roster)["policies"]
        assert entries[-1]["id"] == result["selection_id"]
        assert events == ["verify", "capabilities"]
        assert clock[0] == local_seconds + online_seconds
    else:
        with pytest.raises(BoundaryError, match=expected):
            service.register(model_id)
        assert roster.read_bytes() == old_roster
        assert set((models.private_root / registration_module.REGISTRATIONS).iterdir()) == (
            old_selections)
        assert events == (["verify", "capabilities"] if online_seconds else ["verify"])
    assert journal.read_bytes() == old_journal
    assert {path.name: path.read_bytes() for path in package.iterdir()} == old_package
