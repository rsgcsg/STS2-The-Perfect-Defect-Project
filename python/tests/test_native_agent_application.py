"""Native own-byte export/registration boundaries over actual synthetic model artifacts."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_native_structured_training import completed as completed
from test_native_structured_training import two_threads as two_threads

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_model_export import LocalModelExport, _DownloadedStructuredModel
from spireagent.workbench.local_model_registration import (
    LocalModelRegistration,
    _native_requirements,
)
from spireagent.workbench.local_models import NATIVE_ADAPTER, NATIVE_PROFILE, LocalModelService
from spireagent.workbench.native_agent_support import (
    PUBLICATION_PROFILE_SHA256,
    bind_native_export,
    validate,
)

ROOT = Path(__file__).resolve().parents[2]


def native_capabilities() -> dict:
    profile = json.loads(
        (
            ROOT / "components/connector/contracts/native-logical-publication-profile-v1.json"
        ).read_bytes()
    )
    wire = json.loads(
        (ROOT / "components/connector/contracts/fixtures/native-logical-v1.json").read_bytes()
    )
    caps = wire["wire_samples"]["capabilities"]
    caps["host"].update(
        host_kind="test",
        version="synthetic",
        runtime_instance_id="game-synthetic",
        implementation={
            "source_revision": "a" * 40,
            "artifact_sha256": "b" * 64,
            "module_version_id": "mvid-synthetic",
        },
    )
    caps["game"].update(version="game-synthetic", commit="commit-synthetic")
    caps["game"]["modset"].update(status="exact", fingerprint="c" * 64, loaded_mod_ids=["fixture"])
    caps["session"]["runtime_instance_id"] = "game-synthetic"
    caps["capture_coverage"] = copy.deepcopy(profile["required_seams"])
    return {
        "capabilities": caps,
        "publication_profile": profile,
        "publication_profile_sha256": PUBLICATION_PROFILE_SHA256,
    }


def settled(service: LocalModelExport) -> dict:
    assert service.thread is not None
    service.thread.join(timeout=30)
    assert not service.thread.is_alive()
    return service.status()["operation"]


def export_service(tmp_path: Path, completed, monkeypatch):
    store, _, _, _, _, result, _, _, package, _ = completed
    state = tmp_path / "application"
    state.mkdir()
    config = ProjectConfig(state, "", "", None, combination())
    export = LocalModelExport(config)
    monkeypatch.setattr(export, "_workspace", lambda: SimpleNamespace(store=store))
    return config, export, store, result.model_id, package


def test_actual_native_artifact_uses_same_export_journal_and_keeps_package_id_separate(
    tmp_path,
    completed,
    monkeypatch,
):
    _, export, store, artifact_id, package = export_service(tmp_path, completed, monkeypatch)
    before = store.manifest_ids()
    export.start(artifact_id)
    operation = settled(export)
    assert operation["status"] == "completed", operation
    assert operation["model_type"] == "native"
    assert artifact_id != package["model_id"]
    destination = export.verified_for_registration(artifact_id)
    assert set(path.name for path in destination.iterdir()) == {"model.json", "weights.tensor-tree"}
    assert json.loads((destination / "model.json").read_bytes())["model_id"] == package["model_id"]
    previous = (destination / "weights.tensor-tree").read_bytes()
    export.start(artifact_id)
    assert settled(export)["status"] == "completed"
    assert (destination / "weights.tensor-tree").read_bytes() == previous
    assert store.manifest_ids() == before
    with pytest.raises(BoundaryError):
        export.verified_for_registration(package["model_id"])


def test_own_download_payloads_export_without_fetching_private_ancestry(
    tmp_path,
    completed,
    monkeypatch,
):
    config, export, store, artifact_id, _ = export_service(tmp_path, completed, monkeypatch)
    directory = config.state_dir / "downloads" / artifact_id
    directory.mkdir(parents=True)
    model = store.get_manifest(artifact_id)
    (directory / "manifest.json").write_bytes(model.to_bytes())
    (directory / "download.json").write_text(
        json.dumps({"schema": "stpd/result-download-v1", "artifact_id": artifact_id})
    )
    for payload in model.payloads:
        (directory / (payload.sha256 + ".bin")).write_bytes(b"".join(store.read_payload(payload)))
    downloaded = _DownloadedStructuredModel(directory, artifact_id)
    with pytest.raises(BoundaryError, match="private_ancestry_unavailable"):
        downloaded.get_manifest(model.parent("run"))
    monkeypatch.setattr(
        export,
        "_workspace",
        lambda: (_ for _ in ()).throw(AssertionError("private ancestry opened")),
    )
    export.start(artifact_id)
    assert settled(export)["status"] == "completed"
    assert export.verified_for_registration(artifact_id).is_dir()


@pytest.mark.parametrize("change", ["missing", "sampled", "version", "duplicate", "hash"])
def test_fixed_publication_profile_never_shrinks_to_current_advertisement(change):
    value = native_capabilities()
    caps = value["capabilities"]
    if change == "missing":
        caps["capture_coverage"].pop()
    elif change == "sampled":
        caps["capture_coverage"][5]["coverage"] = "sampled"
    elif change == "version":
        caps["capture_coverage"][9]["version"] = "2"
    elif change == "duplicate":
        caps["capture_coverage"].append(copy.deepcopy(caps["capture_coverage"][0]))
    else:
        value["publication_profile_sha256"] = "0" * 64
    with pytest.raises(BoundaryError):
        _native_requirements(value)


def test_registration_same_catalog_separates_artifact_and_package_identity(
    tmp_path,
    completed,
    monkeypatch,
):
    config, export, _, artifact_id, package = export_service(tmp_path, completed, monkeypatch)
    export.start(artifact_id)
    assert settled(export)["status"] == "completed"
    models = LocalModelService(config)
    registration = LocalModelRegistration(config, export, models)
    monkeypatch.setattr(
        registration, "_native_runtime", lambda **_: (Path("/synthetic"), Path("/synthetic/sdk.js"))
    )
    monkeypatch.setattr(registration, "_capabilities", lambda *_, **__: native_capabilities())
    # This unit isolates metadata publication. Actual installed APIs have separate
    # negative gates and the real child/HTTP integration, without this injection.
    monkeypatch.setattr(models, "_public_manifest_contract", lambda *_, **__: None)
    result = registration.register(artifact_id)
    assert result["status"] == "registered", result
    assert registration.register(artifact_id) == result
    assert registration.status(artifact_id) == result
    entry = models.selection(result["selection_id"])
    assert entry["adapter"] == NATIVE_ADAPTER and entry["runtime_profile"] == NATIVE_PROFILE
    config_path, manifest_path = (
        models.entry_path(entry, "config"),
        models.entry_path(entry, "manifest"),
    )
    metadata, manifest = validate(
        models.root, config_path, manifest_path, binding_root=models.private_root
    )
    assert metadata["artifact_id"] == artifact_id
    assert metadata["package_model_id"] == package["model_id"]
    assert manifest["artifact"]["id"] == package["model_id"]
    assert metadata["publication_profile"]["definition_sha256"] == PUBLICATION_PROFILE_SHA256
    assert len(manifest["input"]["attachment"]["required_seams"]) == 13
    assert models.adapter_arguments(entry) == [
        "-m",
        "stpd.policy.native_agent",
        "--package",
        str((config.state_dir / "model-exports" / artifact_id).resolve()),
        "--manifest",
        str(manifest_path),
    ]
    primary, _ = models.runtime_profile()
    selected, _ = models.runtime_profile(entry["id"])
    assert selected == primary
    with pytest.raises(BoundaryError, match="native_managed_environment_not_supported"):
        registration.register(artifact_id, environment_kind="managed")


def test_tampered_native_package_cannot_reconcile_old_completed_journal(
    tmp_path,
    completed,
    monkeypatch,
):
    config, export, _, artifact_id, _ = export_service(tmp_path, completed, monkeypatch)
    export.start(artifact_id)
    assert settled(export)["status"] == "completed"
    (config.state_dir / "model-exports" / artifact_id / "weights.tensor-tree").write_bytes(
        b"tampered"
    )
    with pytest.raises(BoundaryError):
        export.verified_for_registration(artifact_id)
    export.start(artifact_id)
    assert settled(export)["status"] == "failed"


def test_static_binding_refuses_executable_selection_from_config(
    tmp_path,
    completed,
    monkeypatch,
):
    config, export, _, artifact_id, _ = export_service(tmp_path, completed, monkeypatch)
    export.start(artifact_id)
    assert settled(export)["status"] == "completed"
    owner = config.state_dir / "models"
    owner.mkdir()
    config_path, manifest_path = owner / "config.json", owner / "manifest.json"
    requirements, support, seams = _native_requirements(native_capabilities())
    bind_native_export(
        ROOT / "python",
        export.verified_for_registration(artifact_id),
        config_path,
        manifest_path,
        artifact_id=artifact_id,
        manifest_id="native-binding-fixture",
        requirements=requirements,
        support=support,
        required_seams=seams,
        binding_root=owner,
    )
    value = json.loads(config_path.read_bytes())
    value["module"] = "downloaded.code"
    config_path.write_text(json.dumps(value))
    with pytest.raises(BoundaryError):
        validate(ROOT / "python", config_path, manifest_path, binding_root=owner)
