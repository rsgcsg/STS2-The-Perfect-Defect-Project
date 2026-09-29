"""Fixed Workbench text Runtime preparation; all installs are synthetic."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.package_identity import PackageIdentityError
from spireagent.workbench import local_models
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.kit_runtime import (
    M2_ARCHIVE_DESTINATION,
    M2_RUNTIME_DESTINATION,
    TEXT_ARCHIVE_DESTINATION,
    TEXT_RUNTIME_DESTINATION,
)
from spireagent.workbench.local_models import LocalModelService


@pytest.fixture
def service(tmp_path: Path) -> LocalModelService:
    return LocalModelService(ProjectConfig(tmp_path / "state", "", "", None, combination()))


def finished(service: LocalModelService) -> dict:
    assert service.thread is not None
    service.thread.join(timeout=3)
    assert not service.thread.is_alive()
    return service.status()


def pair(service: LocalModelService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
         profile_id: str) -> tuple[Path, bytes, bytes]:
    source = tmp_path / "releases" / ("a" * 64) / "source"
    root = source / "python"
    root.mkdir(parents=True)
    service.root = root
    memory = profile_id == "text-menu-m2-v1"
    profile_name = M2_RUNTIME_DESTINATION if memory else TEXT_RUNTIME_DESTINATION
    archive_name = M2_ARCHIVE_DESTINATION if memory else TEXT_ARCHIVE_DESTINATION
    archive = b"synthetic exact archive"
    pin = {
        "package": "@rsgcsg/sts2-policy-runtime", "version": "0.1.0",
        "source_revision": "b" * 40, "component_tree_revision": "c" * 40,
        "release_asset_sha256": hashlib.sha256(archive).hexdigest(),
        "package_content_sha256": "d" * 64,
        "dependency_layout": "bundled_source_candidate", "bundled_connector_pin": {},
    }
    profile = json.dumps({
        "schema": "stpd/local-text-m2-runtime-v1" if memory else "stpd/local-text-runtime-v1",
        "runtime_package": pin,
    }).encode()
    for name, raw in ((profile_name, profile), (archive_name, archive)):
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    key = "m2_runtime" if memory else "text_runtime"
    identity = {
        "profile_sha256": hashlib.sha256(profile).hexdigest(),
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
    }

    def checked(args, **kwargs):
        assert args[3:] == ["status", "--directory", str(source.parent)]
        assert kwargs["cwd"] == root
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "status": "prepared", "directory": str(source.parent),
            key: "bundled_installation_not_checked", key + "_identity": identity,
        }).encode())

    monkeypatch.setattr(local_models.subprocess, "run", checked)
    return source / profile_name, profile, archive


@pytest.mark.parametrize("profile_id", ["text-menu-v1", "text-menu-m2-v1"])
def test_fixed_pair_prepares_private_pin_without_loading(
        service, tmp_path, monkeypatch, profile_id):
    staged, raw, archive = pair(service, tmp_path, monkeypatch, profile_id)
    calls = []
    monkeypatch.setattr(local_models, "install_runtime",
                        lambda directory, pin, connector, *, archive: calls.append(
                            (directory, pin, archive.read_bytes())) or {"verified": True})
    monkeypatch.setattr(local_models, "validate_runtime_install",
                        lambda *_: pytest.fail("not installed yet"))
    assert service.prepare_text_runtime(profile_id)["operation"]["status"] == "pending"
    state = finished(service)
    assert state["operation"]["status"] == "completed"
    assert state["loaded"] is False
    assert state["last_text_runtime_preparation"] == {
        "runtime_profile": profile_id, "status": "ready", "reused": False,
    }
    assert (service.private_root / staged.name).read_bytes() == raw
    assert calls == [(service.directory / profile_id,
                      json.loads(raw)["runtime_package"], archive)]


def test_existing_exact_install_reused_without_selected_kit(service, monkeypatch):
    raw = {"schema": "stpd/local-text-runtime-v1", "runtime_package": {
        "package": "@rsgcsg/sts2-policy-runtime",
        "dependency_layout": "bundled_source_candidate",
    }}
    service.private_root.mkdir(parents=True)
    (service.private_root / "text-menu-runtime-v1.json").write_text(json.dumps(raw))
    monkeypatch.setattr(local_models, "validate_runtime_install", lambda *_: {"verified": True})
    monkeypatch.setattr(service, "_selected_kit_text_runtime",
                        lambda *_: pytest.fail("existing exact install needs no kit"))
    monkeypatch.setattr(local_models, "install_runtime",
                        lambda *_a, **_k: pytest.fail("existing exact install unchanged"))
    service.prepare_text_runtime("text-menu-v1")
    assert finished(service)["last_text_runtime_preparation"]["reused"] is True


def test_development_checkout_and_untrusted_local_files_cannot_supply_kit(
        service, tmp_path, monkeypatch):
    service.root = tmp_path / "checkout/python"
    source = service.root / ".local"
    source.mkdir(parents=True)
    (source / "text-menu-runtime-v1.json").write_text("{}")
    (source / "text-menu-runtime-v1.tgz").write_bytes(b"untrusted")
    monkeypatch.setattr(local_models, "install_runtime", lambda *_a, **_k: pytest.fail("install"))
    service.prepare_text_runtime("text-menu-v1")
    assert finished(service)["error_code"] == "trusted_text_runtime_kit_unavailable"
    assert not (service.private_root / "text-menu-runtime-v1.json").exists()


def test_verifier_hash_mismatch_blocks_before_private_pin_or_install(
        service, tmp_path, monkeypatch):
    staged, _raw, _archive = pair(service, tmp_path, monkeypatch, "text-menu-v1")
    staged.write_bytes(b"changed after verifier")
    monkeypatch.setattr(local_models, "install_runtime", lambda *_a, **_k: pytest.fail("install"))
    service.prepare_text_runtime("text-menu-v1")
    assert finished(service)["error_code"] == "trusted_text_runtime_kit_changed"
    assert not (service.private_root / staged.name).exists()


def test_interrupted_install_keeps_complete_pin_and_explicit_retry_recovers(
        service, tmp_path, monkeypatch):
    staged, raw, archive = pair(service, tmp_path, monkeypatch, "text-menu-v1")
    calls = 0

    def install(_directory, _pin, _connector, *, archive):
        nonlocal calls
        calls += 1
        assert archive.read_bytes() == b"synthetic exact archive"
        if calls == 1:
            raise OSError("synthetic install failure")
        return {"verified": True}

    monkeypatch.setattr(local_models, "install_runtime", install)
    def missing_install(*_args):
        raise PackageIdentityError("synthetic absent closure")

    monkeypatch.setattr(local_models, "validate_runtime_install", missing_install)
    service.prepare_text_runtime("text-menu-v1")
    assert finished(service)["error_code"] == "local_operation_failed"
    assert (service.private_root / staged.name).read_bytes() == raw
    assert calls == 1
    service.prepare_text_runtime("text-menu-v1")
    state = finished(service)
    assert state["operation"]["status"] == "completed"
    assert state["last_text_runtime_preparation"]["reused"] is False
    assert calls == 2
    assert archive == b"synthetic exact archive"


def test_missing_kit_asset_and_collision_fail_closed(service, tmp_path, monkeypatch):
    staged, raw, _archive = pair(service, tmp_path, monkeypatch, "text-menu-m2-v1")
    original = local_models.subprocess.run

    def missing(args, **kwargs):
        result = original(args, **kwargs)
        value = json.loads(result.stdout)
        value.update(m2_runtime="not_bundled", m2_runtime_identity=None)
        return SimpleNamespace(returncode=0, stdout=json.dumps(value).encode())

    monkeypatch.setattr(local_models.subprocess, "run", missing)
    service.prepare_text_runtime("text-menu-m2-v1")
    assert finished(service)["error_code"] == "trusted_text_runtime_asset_not_bundled"
    assert not (service.private_root / staged.name).exists()
    monkeypatch.setattr(local_models.subprocess, "run", original)
    service.private_root.mkdir(parents=True, exist_ok=True)
    (service.private_root / staged.name).write_bytes(raw.replace(b"0.1.0", b"0.2.0"))
    def missing_install(*_args):
        raise PackageIdentityError("synthetic absent closure")

    monkeypatch.setattr(local_models, "validate_runtime_install", missing_install)
    service.prepare_text_runtime("text-menu-m2-v1")
    assert finished(service)["error_code"] == "private_profile_collision"
    assert (service.private_root / staged.name).read_bytes() != raw


def test_loaded_recovery_and_unknown_profile_do_not_prepare(service, monkeypatch):
    monkeypatch.setattr(service, "_selected_kit_text_runtime", lambda *_: pytest.fail("kit"))
    with pytest.raises(BoundaryError, match="unsupported_runtime_profile"):
        service.prepare_text_runtime("arbitrary")
    service.state["loaded"] = True
    with pytest.raises(BoundaryError, match="stop_runtime_before_install"):
        service.prepare_text_runtime("text-menu-v1")
    service.state["loaded"] = False
    service.thread = SimpleNamespace(is_alive=lambda: True)
    with pytest.raises(BoundaryError, match="operation_in_progress"):
        service.prepare_text_runtime("text-menu-v1")
    service.thread = None
    service.state.update(loaded=False, status="recovery_required")
    with pytest.raises(BoundaryError, match="previous_operation_requires_recovery"):
        service.prepare_text_runtime("text-menu-v1")
