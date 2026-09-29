"""M2 registration uses the existing Workbench roster and a separate pinned runtime."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import torch
from test_local_memory_model_export import _fixture, _settle
from test_local_model_registration import _caps

from spireagent.json_boundary import BoundaryError
from spireagent.workbench import local_model_registration as registration_module
from spireagent.workbench.developer import ROOT, atomic_json
from spireagent.workbench.local_model_export import OPERATION_FILE, LocalModelExport
from spireagent.workbench.local_model_registration import LocalModelRegistration
from spireagent.workbench.local_models import LocalModelService
from stpd.memory_policy_installation import PROTOCOL, validate


def test_m2_registration_requires_own_install_context_and_preserves_token_roster(
        tmp_path: Path, monkeypatch) -> None:
    config, owner, _, _, sources, _, _, model_id = _fixture(tmp_path, monkeypatch)
    exported = LocalModelExport(config)
    exported.start(model_id)
    assert _settle(exported)["status"] == "completed"
    models = LocalModelService(config)
    root = tmp_path / "python-root"
    for name in ("configs/developer/local-policies-v1.json", "stpd/policy/memory_port.py",
                 "uv.lock"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (root / "spireagent").mkdir()
    models.private_root.mkdir(parents=True, exist_ok=True)
    models.root = root
    service = LocalModelRegistration(config, exported, models)
    monkeypatch.setattr(service, "_capabilities", lambda _sdk, **_kwargs: _caps())
    monkeypatch.setattr(service, "_context_available", lambda _sdk, **_kwargs: None)
    monkeypatch.setattr(service, "_m2_runtime_manifest_compatible",
                        lambda _modules, _manifest, **_kwargs: None)
    monkeypatch.setattr(registration_module, "validate_runtime_install",
                        lambda *_: {"version": "synthetic"})
    with pytest.raises(BoundaryError, match="text_runtime_profile_required"):
        service.register(model_id)
    assert not (models.private_root / "token-policies-v1.json").exists()
    atomic_json(models.private_root / "text-menu-m2-runtime-v1.json", {
        "schema": "stpd/local-text-m2-runtime-v1",
        "runtime_package": {"package": "@rsgcsg/sts2-policy-runtime",
                            "dependency_layout": "bundled_source_candidate"},
    })
    old = {"id": "old-token", "label": "old", "adapter": "token-v1",
           "runtime_profile": "text-menu-v1", "manifest": "old-manifest.json",
           "config": "old-config.json"}
    atomic_json(models.private_root / "token-policies-v1.json", {
        "schema": "stpd/local-token-policies-v1", "policies": [old],
    })
    previous_threads = torch.get_num_threads()
    try:
        torch.set_num_threads(3)  # Web request need not inherit the run's two threads.
        result = service.register(model_id)
        assert torch.get_num_threads() == 3
    finally:
        torch.set_num_threads(previous_threads)
    assert result["status"] == "registered"
    assert result["runtime_profile"] == "text-menu-m2-v1"
    entry = models.selection(result["selection_id"])
    assert entry["adapter"] == "stpd-m2-decision-adapter"
    _, manifest = validate(root, models.private_root / entry["config"],
                           models.private_root / entry["manifest"],
                           binding_root=models.private_root)
    assert manifest["adapter"]["protocol"] == PROTOCOL
    assert models.registry()["policies"][-2] == old
    assert service.register(model_id) == result
    monkeypatch.setattr(models, "_runtime_package", lambda _identity: {"version": "synthetic"})
    monkeypatch.setattr(models, "_public_manifest_contract", lambda *_: None)
    readiness = models.readiness(result["selection_id"])
    assert readiness["status"] == "ready_to_load"
    assert readiness["checks"]["policy_identity"] == {"status": "pass"}
    assert models._run_profile(result["selection_id"], "extended") is True
    roster = (models.private_root / "token-policies-v1.json").read_bytes()
    journal_path = config.state_dir / OPERATION_FILE
    journal = json.loads(journal_path.read_bytes())
    old = dict(journal)
    old.pop("verified_receipt")
    atomic_json(journal_path, old)
    with pytest.raises(BoundaryError, match="verified_export_receipt_required"):
        service.register(model_id)
    atomic_json(journal_path, journal)
    wrong_result = json.loads(journal_path.read_bytes())
    wrong_result["verified_receipt"]["result_id"] = "0" * 64
    atomic_json(journal_path, wrong_result)
    with pytest.raises(BoundaryError, match="export_identity_mismatch"):
        service.register(model_id)
    atomic_json(journal_path, journal)
    wrong_hash = json.loads(journal_path.read_bytes())
    wrong_hash["verified_receipt"]["weights_sha256"] = "0" * 64
    atomic_json(journal_path, wrong_hash)
    with pytest.raises(BoundaryError, match="export_identity_mismatch"):
        service.register(model_id)
    atomic_json(journal_path, journal)
    tokenizer_path = config.state_dir / "model-exports" / model_id / "tokenizer.json"
    tokenizer = tokenizer_path.read_bytes()
    tokenizer_path.write_bytes(tokenizer + b" ")
    with pytest.raises(BoundaryError, match="payload_digest_mismatch"):
        service.register(model_id)
    tokenizer_path.write_bytes(tokenizer)
    with owner.transaction() as db:
        db.execute("DELETE FROM curation_source_uses WHERE source=?", (sources[0],))
    with pytest.raises(BoundaryError, match="training_source_use_missing"):
        service.register(model_id)
    assert (models.private_root / "token-policies-v1.json").read_bytes() == roster


def test_m2_context_missing_blocks_before_roster_write(tmp_path: Path, monkeypatch) -> None:
    config, _, _, _, _, _, _, model_id = _fixture(tmp_path, monkeypatch)
    exported = LocalModelExport(config)
    exported.start(model_id)
    assert _settle(exported)["status"] == "completed"
    models = LocalModelService(config)
    root = tmp_path / "python-root"
    for name in ("configs/developer/local-policies-v1.json", "stpd/policy/memory_port.py",
                 "uv.lock"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (root / "spireagent").mkdir()
    models.private_root.mkdir(parents=True, exist_ok=True)
    atomic_json(models.private_root / "text-menu-m2-runtime-v1.json", {
        "schema": "stpd/local-text-m2-runtime-v1",
        "runtime_package": {"package": "@rsgcsg/sts2-policy-runtime",
                            "dependency_layout": "bundled_source_candidate"},
    })
    models.root = root
    service = LocalModelRegistration(config, exported, models)
    monkeypatch.setattr(service, "_capabilities", lambda _sdk, **_kwargs: _caps())
    monkeypatch.setattr(registration_module, "validate_runtime_install",
                        lambda *_: {"version": "synthetic"})
    monkeypatch.setattr(service, "_context_available", lambda _sdk, **_kwargs:
                        (_ for _ in ()).throw(BoundaryError(
                            "local_model_registration", "observation_context_unavailable")))
    with pytest.raises(BoundaryError, match="observation_context_unavailable"):
        service.register(model_id)
    assert not (models.private_root / "token-policies-v1.json").exists()
