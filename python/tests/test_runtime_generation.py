"""Stopped-owner generation switches preserve exact private profile bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench import local_models
from spireagent.workbench import runtime_generation as owner
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.developer_server import instance_lock


@pytest.fixture
def generation(tmp_path: Path, monkeypatch):
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    service = local_models.LocalModelService(config)
    service.private_root.mkdir(parents=True)
    active = service.private_root / "text-menu-m2-runtime-v1.json"
    old = (b'{ "schema": "stpd/local-text-m2-runtime-v1", '
           b'"runtime_package": {"package":"@rsgcsg/sts2-policy-runtime",'
           b'"dependency_layout":"bundled_source_candidate"}}\n')
    active.write_bytes(old)
    legacy_marker = service.directory / "text-menu-m2-v1/runtime/node_modules/ready"
    legacy_marker.parent.mkdir(parents=True)
    legacy_marker.touch()
    archive = tmp_path / "runtime.tgz"
    archive.write_bytes(b"synthetic archive")
    pin = {"package": "@rsgcsg/sts2-policy-runtime", "version": "0.1.0-rc.14",
           "source_revision": "a" * 40, "component_tree_revision": "b" * 40,
           "release_asset_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
           "package_content_sha256": "c" * 64,
           "dependency_layout": "bundled_source_candidate", "bundled_connector_pin": {}}
    new_profile = tmp_path / "profile.json"
    new_profile.write_text(json.dumps({"schema": "stpd/local-text-m2-runtime-v1",
                                       "runtime_package": pin}))
    monkeypatch.setattr(owner, "running", lambda _: None)
    monkeypatch.setattr(local_models, "_check_runtime_port", lambda _: None)
    monkeypatch.setattr(local_models.LocalModelService, "_connector_pin", lambda _: {})
    monkeypatch.setattr(local_models.LocalModelService, "registry", lambda _: {
        "policies": [{"id": "old-model", "runtime_profile": "text-menu-m2-v1"}]})
    verified = []

    def verify(directory, _pin, _connector, _profile):
        if directory.parent.name == "generations":
            # A staged install is complete only when the installer left its marker.
            if not (directory / "runtime/node_modules/ready").is_file():
                raise BoundaryError("local_model", "runtime_generation_install_invalid")
        elif not (directory / "runtime/node_modules/ready").is_file():
            raise BoundaryError("local_model", "runtime_generation_install_invalid")
        verified.append(directory)
        return {"version": "checked"}

    def install(directory, *_args, **_kwargs):
        marker = directory / "runtime/node_modules/ready"
        marker.parent.mkdir(parents=True)
        marker.touch()
        return {"status": "runtime_installed"}

    monkeypatch.setattr(owner, "_verify", verify)
    monkeypatch.setattr(owner, "install_runtime", install)
    return config, active, old, new_profile, archive, verified


def _upgrade(args):
    config, active, old, profile, archive, _ = args
    return owner.upgrade(config, "text-menu-m2-v1",
                         expected_active_sha256=hashlib.sha256(old).hexdigest(),
                         new_profile_file=profile,
                         expected_new_profile_sha256=hashlib.sha256(profile.read_bytes()).hexdigest(),
                         archive=archive)


def test_upgrade_and_rollback_preserve_old_bytes_and_bound_shared_models(generation):
    config, active, old, _, _, _ = generation
    upgraded = _upgrade(generation)
    assert upgraded["activated"] is True
    assert upgraded["affected_selections"] == ["old-model"]
    assert active.read_bytes() != old
    wrapper = json.loads(active.read_bytes())
    assert wrapper["schema"] == owner.SCHEMA
    archive = active.parent / "text-menu-m2-v1/profiles" / hashlib.sha256(old).hexdigest()
    assert archive.with_suffix(".json").read_bytes() == old
    directory, pin = local_models.LocalModelService(config).text_runtime_profile(
        "text-menu-m2-v1")
    assert directory.name == wrapper["generation"]
    assert pin == wrapper["profile"]["runtime_package"]
    rolled = owner.rollback(config, "text-menu-m2-v1",
                            expected_active_sha256=hashlib.sha256(active.read_bytes()).hexdigest(),
                            archived_profile_sha256=hashlib.sha256(old).hexdigest())
    assert rolled["activated"] is True
    assert active.read_bytes() == old


def test_old_active_cas_and_bad_archive_leave_profile_unchanged(generation):
    config, active, old, profile, archive, _ = generation
    with pytest.raises(BoundaryError, match="runtime_active_profile_changed"):
        owner.upgrade(config, "text-menu-m2-v1", expected_active_sha256="0" * 64,
                      new_profile_file=profile,
                      expected_new_profile_sha256=hashlib.sha256(profile.read_bytes()).hexdigest(),
                      archive=archive)
    archive.write_bytes(b"changed")
    with pytest.raises(BoundaryError, match="text_runtime_archive_checksum_mismatch"):
        _upgrade(generation)
    assert active.read_bytes() == old


def test_running_owner_and_busy_port_block_before_install(generation, monkeypatch):
    config, active, old, _, _, _ = generation
    monkeypatch.setattr(owner, "running", lambda _: {"port": 1234})
    with pytest.raises(BoundaryError, match="close_workbench_before_runtime_generation_change"):
        _upgrade(generation)
    monkeypatch.setattr(owner, "running", lambda _: None)
    monkeypatch.setattr(local_models, "_check_runtime_port", lambda _: (_ for _ in ()).throw(
        BoundaryError("local_model", "runtime_port_already_in_use")))
    with pytest.raises(BoundaryError, match="runtime_port_already_in_use"):
        _upgrade(generation)
    assert active.read_bytes() == old


def test_install_failure_and_pre_switch_interrupt_leave_old_active(generation, monkeypatch):
    _, active, old, _, _, _ = generation
    monkeypatch.setattr(owner, "install_runtime", lambda *_a, **_k: (_ for _ in ()).throw(
        BoundaryError("local_model", "pinned_runtime_install_verification_failed")))
    with pytest.raises(BoundaryError, match="pinned_runtime_install_verification_failed"):
        _upgrade(generation)
    assert active.read_bytes() == old
    def install(directory, *_args, **_kwargs):
        marker = directory / "runtime/node_modules/ready"
        marker.parent.mkdir(parents=True)
        marker.touch()
        return {"status": "runtime_installed"}

    monkeypatch.setattr(owner, "install_runtime", install)
    monkeypatch.setattr(owner, "_publish_profile", lambda *_: (_ for _ in ()).throw(
        OSError("interrupted before switch")))
    with pytest.raises(OSError, match="interrupted before switch"):
        _upgrade(generation)
    assert active.read_bytes() == old


def test_existing_generation_tamper_rejects_without_repair(generation):
    config, active, _, profile, archive, _ = generation
    _upgrade(generation)
    wrapper = json.loads(active.read_bytes())
    directory = active.parent / "text-menu-m2-v1/generations" / wrapper["generation"]
    (directory / "runtime/node_modules/ready").unlink()
    with pytest.raises(BoundaryError, match="runtime_generation_install_invalid"):
        owner.upgrade(config, "text-menu-m2-v1",
                      expected_active_sha256=hashlib.sha256(active.read_bytes()).hexdigest(),
                      new_profile_file=profile,
                      expected_new_profile_sha256=hashlib.sha256(profile.read_bytes()).hexdigest(),
                      archive=archive)
    from spireagent.workbench.local_model_cli import model_command

    with pytest.raises(BoundaryError, match="runtime_generation_install_invalid"):
        model_command(config, "install-runtime", runtime_archive=archive,
                      runtime_profile="text-menu-m2-v1")


def test_replace_then_directory_fsync_failure_reports_readback(generation, monkeypatch):
    _, active, _, _, _, _ = generation
    actual = owner._sync_directory

    def fail_after_replace(path):
        if path == active.parent:
            raise OSError("directory fsync failed")
        actual(path)

    monkeypatch.setattr(owner, "_sync_directory", fail_after_replace)
    result = _upgrade(generation)
    assert result["switch_state"] == "new_after_io_error"
    assert result["status"] == "BLOCKED"
    assert result["activated"] is True
    assert result["readback_profile_sha256"] == hashlib.sha256(active.read_bytes()).hexdigest()


def test_rollback_can_recover_when_current_generation_install_is_damaged(generation):
    config, active, old, _, _, _ = generation
    _upgrade(generation)
    wrapper = json.loads(active.read_bytes())
    directory = active.parent / "text-menu-m2-v1/generations" / wrapper["generation"]
    (directory / "runtime/node_modules/ready").unlink()
    result = owner.rollback(config, "text-menu-m2-v1",
                            expected_active_sha256=hashlib.sha256(active.read_bytes()).hexdigest(),
                            archived_profile_sha256=hashlib.sha256(old).hexdigest())
    assert result["activated"] is True
    assert active.read_bytes() == old
    assert directory.is_dir()  # damaged generation remains for inspection


def test_generation_wrapper_rejects_tampered_digest_and_unknown_path(generation):
    config, active, _, _, _, _ = generation
    _upgrade(generation)
    profile = json.loads(active.read_bytes())
    profile["generation"] = "f" * 64
    active.write_text(json.dumps(profile))
    with pytest.raises(BoundaryError, match="runtime_generation_profile_invalid"):
        local_models.LocalModelService(config).text_runtime_profile("text-menu-m2-v1")
    profile["generation"] = _generation_digest(profile["profile"])
    profile["path"] = "/tmp/other"
    active.write_text(json.dumps(profile))
    with pytest.raises(BoundaryError, match="runtime_generation_profile_invalid"):
        local_models.LocalModelService(config).text_runtime_profile("text-menu-m2-v1")


def _generation_digest(profile):
    return owner._profile_hash(profile)


def test_instance_lock_blocks_upgrade_without_touching_active(generation):
    config, active, old, _, _, _ = generation
    with (instance_lock(config.state_dir / "instance.lock"),
          pytest.raises(BoundaryError, match="already_running")):
        _upgrade(generation)
    assert active.read_bytes() == old


def test_cli_sync_uncertainty_is_nonzero_with_readback(generation, monkeypatch, capsys):
    from spireagent.workbench import developer_cli

    config, active, _, profile, archive, _ = generation
    monkeypatch.setattr(developer_cli.ProjectConfig, "load", lambda *_a, **_k: config)
    actual = owner._sync_directory

    def fail_after_replace(path):
        if path == active.parent:
            raise OSError("private diagnostic only")
        actual(path)

    monkeypatch.setattr(owner, "_sync_directory", fail_after_replace)
    args = ["model", "--action", "upgrade-runtime-generation",
            "--runtime-profile", "text-menu-m2-v1", "--runtime-archive", str(archive),
            "--expected-active-sha256", hashlib.sha256(active.read_bytes()).hexdigest(),
            "--new-runtime-profile", str(profile),
            "--expected-new-profile-sha256", hashlib.sha256(profile.read_bytes()).hexdigest()]
    assert developer_cli.main(args) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "BLOCKED"
    assert printed["activated"] is True
    assert printed["readback_profile_sha256"] == hashlib.sha256(active.read_bytes()).hexdigest()


@pytest.mark.parametrize("observed", ["old", "unknown"])
def test_switch_io_failure_does_not_invent_activation(generation, monkeypatch, observed):
    _, active, old, _, _, _ = generation

    def interrupted(_path, _raw):
        if observed == "unknown":
            active.write_bytes(b"damaged unknown active bytes")
        raise OSError("write or sync failed")

    monkeypatch.setattr(owner, "_atomic_raw", interrupted)
    result = _upgrade(generation)
    assert result["status"] == "BLOCKED"
    assert result["activated"] == (False if observed == "old" else "unknown")
    assert result["switch_state"] == observed + "_after_io_error"
    assert result["readback_profile_sha256"] == hashlib.sha256(active.read_bytes()).hexdigest()
    if observed == "old":
        assert active.read_bytes() == old


def test_rollback_rejects_changed_archive_without_switch(generation):
    config, active, old, _, _, _ = generation
    _upgrade(generation)
    current = active.read_bytes()
    archived = (active.parent / "text-menu-m2-v1/profiles" /
                (hashlib.sha256(old).hexdigest() + ".json"))
    archived.write_bytes(b"tampered")
    with pytest.raises(BoundaryError, match="runtime_archived_profile_changed"):
        owner.rollback(config, "text-menu-m2-v1",
                       expected_active_sha256=hashlib.sha256(current).hexdigest(),
                       archived_profile_sha256=hashlib.sha256(old).hexdigest())
    assert active.read_bytes() == current
