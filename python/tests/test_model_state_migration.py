"""Checkout-era model metadata remains history after an explicit state move."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench import model_state_migration as migration


@pytest.fixture
def legacy(tmp_path: Path, monkeypatch):
    config = SimpleNamespace(
        state_dir=tmp_path / "state",
        combination={"node_packages": [{"package": "@rsgcsg/sts2-connector-client"}]},
    )
    old_root = tmp_path / "checkout/python"
    old = old_root / ".local"
    old.mkdir(parents=True)
    (old / "text-menu-m2-runtime-v1.json").write_text(json.dumps({
        "schema": "stpd/local-text-m2-runtime-v1",
        "runtime_package": {"package": "@rsgcsg/sts2-policy-runtime",
                            "dependency_layout": "bundled_source_candidate"},
    }))
    (old / "model-registrations/old").mkdir(parents=True)
    (old / "model-registrations/old/config.json").write_text("{}")
    (old / "model-registrations/old/manifest.json").write_text("{}")
    (old / "token-policies-v1.json").write_text(json.dumps({
        "schema": "stpd/local-token-policies-v1", "policies": [{
            "id": "old", "config": ".local/model-registrations/old/config.json",
            "manifest": ".local/model-registrations/old/manifest.json",
        }],
    }))
    monkeypatch.setattr(migration, "running", lambda _: None)
    monkeypatch.setattr(migration, "validate_runtime_install", lambda *_: {})
    return config, old_root


def test_migration_archives_old_selections_and_imports_only_verified_profile(legacy):
    config, old_root = legacy
    first = migration.migrate_legacy_model_state(config, old_root)
    second = migration.migrate_legacy_model_state(config, old_root)
    assert first == second
    assert first["legacy_selections"] == 1
    assert first["legacy_selections_loadable"] is False
    private = config.state_dir / "models"
    assert (private / "text-menu-m2-runtime-v1.json").is_file()
    assert not (private / "token-policies-v1.json").exists()
    archive = private / "legacy-archive" / first["archive_id"]
    assert (archive / "model-registrations/old/manifest.json").read_text() == "{}"
    assert (old_root / ".local/token-policies-v1.json").is_file()


def test_migration_rejects_active_owner_and_profile_collision(legacy, monkeypatch):
    config, old_root = legacy
    monkeypatch.setattr(migration, "running", lambda _: {"port": 123})
    with pytest.raises(BoundaryError, match="close_workbench_before_model_state_migration"):
        migration.migrate_legacy_model_state(config, old_root)
    monkeypatch.setattr(migration, "running", lambda _: None)
    private = config.state_dir / "models"
    private.mkdir(parents=True)
    (private / "text-menu-m2-runtime-v1.json").write_text("{}")
    with pytest.raises(BoundaryError, match="private_profile_collision"):
        migration.migrate_legacy_model_state(config, old_root)
    assert not (private / "legacy-archive").exists()


@pytest.mark.parametrize("bad", ["../../outside.json", ".local/../outside.json"])
def test_migration_rejects_legacy_path_escape(legacy, bad):
    config, old_root = legacy
    roster = old_root / ".local/token-policies-v1.json"
    data = json.loads(roster.read_text())
    data["policies"][0]["config"] = bad
    roster.write_text(json.dumps(data))
    with pytest.raises(BoundaryError):
        migration.migrate_legacy_model_state(config, old_root)


def test_migration_rejects_symlinked_profile(legacy, tmp_path: Path):
    config, old_root = legacy
    profile = old_root / ".local/text-menu-m2-runtime-v1.json"
    other = tmp_path / "outside"
    profile.rename(other)
    profile.symlink_to(other)
    with pytest.raises(BoundaryError, match="legacy_metadata_unsafe"):
        migration.migrate_legacy_model_state(config, old_root)
