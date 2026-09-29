from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest
from sts2_platform_evidence.collection_tool import CollectionTool, digest

from spireagent import source as control
from spireagent.json_boundary import BoundaryError
from tools.package_developer_kit import PinnedFile, package, sha256

pytest_plugins = ("test_runtime_install",)


def synthetic_text_pin(archive: bytes) -> dict:
    return {
        "package": "@rsgcsg/sts2-policy-runtime",
        "version": "0.1.0",
        "source_revision": "a" * 40,
        "component_tree_revision": "b" * 40,
        "release_asset_sha256": sha256(archive),
        "package_content_sha256": "c" * 64,
        "dependency_layout": "bundled_source_candidate",
        "bundled_connector_pin": {},
    }


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    (root / "uv.lock").write_bytes(b"synthetic locked dependencies\n")
    config = root / "configs/developer/combination-v1.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "schema": "stpd/developer-combination-v1",
                "platform_repository": "https://github.com/rsgcsg/STS2-The-Pefect-Defect-Project.git",
                "platform_source_revision": "a" * 40,
                "evidence_source_revision": "b" * 40,
                "policy_mode": "existing-adapter-only",
                "node_packages": [{"package": "@rsgcsg/sts2-connector-client"}],
            }
        )
    )
    (root / "python").mkdir()
    (root / "python/uv.lock").write_bytes((root / "uv.lock").read_bytes())
    nested_config = root / "python/configs/developer/combination-v1.json"
    nested_config.parent.mkdir(parents=True)
    nested_config.write_bytes(config.read_bytes())
    native_manifest = root / "apps/game-mod/mod_manifest.json"
    native_manifest.parent.mkdir(parents=True)
    native_manifest.write_bytes(b"synthetic public mod_manifest")
    # Match the repository's Git checkout policy for exact source bytes.
    (root / ".gitattributes").write_bytes(b"* text=auto eol=lf\n")
    (root / ".gitignore").write_text("**/bin/\npython/.local/\n")
    for command in (
        ["init", "-q"],
        ["config", "user.name", "Synthetic Test"],
        ["config", "user.email", "test@example.invalid"],
        ["add", "."],
        ["commit", "-qm", "synthetic packaging source"],
    ):
        subprocess.run(["git", *command], cwd=root, check=True, capture_output=True)
    # Exercise the real clean-source checks with an isolated installed-checkout location.
    monkeypatch.setattr(control, "__file__", str(root / "spireagent/source.py"))
    explicit = {}
    for name in ("mod_dll", "mod_manifest", "platform_bom"):
        path = tmp_path / name
        path.write_bytes(f"synthetic public {name}".encode())
        explicit[name] = PinnedFile(path, sha256(path.read_bytes()))
    tool = tmp_path / "tool"
    tool.mkdir()
    for name in ("sts2-human-annotator.dll", "sts2-human-annotator.deps.json",
                 "sts2-human-annotator.runtimeconfig.json", "STS2HumanAnnotator.Core.dll",
                 "platform-bom.json"):
        (tool / name).write_bytes(f"synthetic tool {name}".encode())
    setup = "setup/apps/game-mod/collection-setup.mjs"
    provenance = "game-mod/build-provenance.json"
    (tool / setup).parent.mkdir(parents=True)
    (tool / setup).write_text("// synthetic setup fixture; never executed")
    (tool / provenance).parent.mkdir()
    (tool / provenance).write_text(
        json.dumps(
            {
                "schema": "sts2.platform/game-mod-build-provenance-1",
                "artifact": {"sha256": explicit["mod_dll"].sha256},
            }
        )
    )
    identity = {
        "worktree": "clean",
        "source_revision": "c" * 40,
        "workspace_revision": "d" * 40,
        "entrypoint": "sts2-human-annotator.dll",
        "supported_recording_schema": "sts2.human-annotator/recording-manifest-2",
        "collection_setup_entrypoint": setup,
        "collection_setup_provenance": provenance,
        "files": [
            {
                "path": path.relative_to(tool).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path.read_bytes()),
            }
            for path in sorted(tool.rglob("*"), key=lambda p: p.relative_to(tool).as_posix())
            if path.is_file()
        ],
    }
    release_id = digest(identity)
    (tool / "collection-tool.json").write_text(
        json.dumps(
            {
                "schema": "sts2.evidence/collection-tool-1",
                "release_id": release_id,
                "identity": identity,
            },
            indent=2,
        )
    )
    return {
        **explicit,
        "root": root,
        "collection_tool": tool,
        "tool_release_id": release_id,
        "output": tmp_path / "first.zip",
    }


def test_source_fixture_lock_bytes_survive_git_checkout(inputs, tmp_path):
    source = inputs["root"]
    committed = subprocess.check_output(["git", "show", "HEAD:python/uv.lock"], cwd=source)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", "--no-checkout", str(source), str(clone)],
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "core.autocrlf", "true"], cwd=clone,
                   check=True, capture_output=True)
    subprocess.run(["git", "checkout", "--detach", "HEAD"], cwd=clone,
                   check=True, capture_output=True)
    assert (clone / "python/uv.lock").read_bytes() == committed


def test_deterministic_public_inventory_and_real_owner_verification(inputs, tmp_path):
    # Unrelated local credentials must never be discovered/copied by the packager.
    (tmp_path / "operator.env").write_text("TOKEN=must-not-be-packaged")
    receipt = package(**inputs)
    second = tmp_path / "second.zip"
    for path in inputs["collection_tool"].iterdir():
        os.utime(path, (1_700_000_000, 1_700_000_000))
    assert package(**{**inputs, "output": second}) == receipt
    raw = inputs["output"].read_bytes()
    assert raw == second.read_bytes()
    assert sha256(raw) == receipt["sha256"]
    assert b"must-not-be-packaged" not in raw
    with zipfile.ZipFile(inputs["output"]) as archive:
        expected = {
            "README.md",
            "mod/STS2_PLATFORM.dll",
            "mod/STS2_PLATFORM.json",
            "platform-bom.json",
            "developer-combination.json",
            "combination.json",
            "collection-tool/collection-tool.json",
            "collection-tool/platform-bom.json",
            "collection-tool/sts2-human-annotator.dll",
            "collection-tool/sts2-human-annotator.deps.json",
            "collection-tool/sts2-human-annotator.runtimeconfig.json",
            "collection-tool/STS2HumanAnnotator.Core.dll",
            "collection-tool/setup/apps/game-mod/collection-setup.mjs",
            "collection-tool/game-mod/build-provenance.json",
        }
        assert set(archive.namelist()) == expected
        manifest = json.loads(archive.read("combination.json"))
        assert set(manifest["files"]) == expected - {"combination.json"}
        for name, expected_hash in manifest["files"].items():
            assert sha256(archive.read(name)) == expected_hash
        assert manifest["collection_tool_source_revision"] == "c" * 40
        assert manifest["collection_tool_workspace_revision"] == "d" * 40
        assert not {"status", "human_approval_source", "hub_image", "stpd_ci_run"} & set(manifest)
        assert (
            archive.read("collection-tool/collection-tool.json")
            == (inputs["collection_tool"] / "collection-tool.json").read_bytes()
        )
        extracted = tmp_path / "extracted"
        # Only our generated archive, after exact path inventory verification.
        archive.extractall(extracted)
    CollectionTool(extracted / "collection-tool", inputs["tool_release_id"])


def test_optional_runtime_requires_external_pins_and_fixed_inventory(inputs, tmp_path,
                                                                     monkeypatch):
    from tools import install_developer_kit as install

    archive = tmp_path / "runtime.tgz"
    archive.write_bytes(b"synthetic archive")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({
        "schema": "stpd/local-text-runtime-v1",
        "runtime_package": synthetic_text_pin(archive.read_bytes()),
    }))
    with pytest.raises(BoundaryError, match="profile_and_archive_required"):
        package(**{**inputs, "text_runtime_profile": PinnedFile(
            profile, sha256(profile.read_bytes()))})
    assert not inputs["output"].exists()
    calls = []
    monkeypatch.setattr("tools.package_developer_kit.install_runtime",
                        lambda directory, pin, connector, archive: calls.append(
                            (directory, pin, connector, archive.read_bytes())))
    args = {**inputs,
            "text_runtime_profile": PinnedFile(profile, sha256(profile.read_bytes())),
            "text_runtime_archive": PinnedFile(archive, sha256(archive.read_bytes()))}
    package(**args)
    assert len(calls) == 1 and calls[0][3] == archive.read_bytes()
    manifest, files = install.verified_archive(
        inputs["output"], sha256(inputs["output"].read_bytes()))
    assert manifest["text_runtime"]["profile_sha256"] == sha256(profile.read_bytes())
    assert manifest["text_runtime"]["archive_sha256"] == sha256(archive.read_bytes())
    assert files[install.TEXT_RUNTIME_PROFILE] == profile.read_bytes()
    assert files[install.TEXT_RUNTIME_ARCHIVE] == archive.read_bytes()
    assert "operator.env" not in files


def test_optional_m2_runtime_is_separate_from_text_and_requires_exact_pair(
        inputs, tmp_path, monkeypatch):
    from tools import install_developer_kit as install

    archive = tmp_path / "m2-runtime.tgz"
    archive.write_bytes(b"independent synthetic M2 archive")
    profile = tmp_path / "m2-profile.json"
    profile.write_text(json.dumps({
        "schema": "stpd/local-text-m2-runtime-v1",
        "runtime_package": synthetic_text_pin(archive.read_bytes()),
    }))
    provided = PinnedFile(profile, sha256(profile.read_bytes()))
    with pytest.raises(BoundaryError, match="m2_runtime_profile_and_archive_required"):
        package(**{**inputs, "m2_runtime_profile": provided})
    wrong = synthetic_text_pin(archive.read_bytes())
    wrong["release_asset_sha256"] = "0" * 64
    profile.write_text(json.dumps({"schema": "stpd/local-text-m2-runtime-v1",
                                   "runtime_package": wrong}))
    with pytest.raises(BoundaryError, match="text_runtime_archive_checksum_mismatch"):
        package(**{**inputs, "m2_runtime_profile": PinnedFile(
            profile, sha256(profile.read_bytes())),
            "m2_runtime_archive": PinnedFile(archive, sha256(archive.read_bytes()))})
    profile.write_text(json.dumps({"schema": "stpd/local-text-m2-runtime-v1",
                                   "runtime_package": synthetic_text_pin(archive.read_bytes())}))
    provided = PinnedFile(profile, sha256(profile.read_bytes()))
    calls = []
    monkeypatch.setattr("tools.package_developer_kit.install_runtime",
                        lambda directory, pin, connector, archive: calls.append(
                            (directory, pin, archive.read_bytes())))
    package(**{**inputs, "m2_runtime_profile": provided,
               "m2_runtime_archive": PinnedFile(archive, sha256(archive.read_bytes()))})
    assert len(calls) == 1 and calls[0][0].name == "text-menu-m2-v1"
    manifest, files = install.verified_archive(
        inputs["output"], sha256(inputs["output"].read_bytes()))
    assert manifest["m2_runtime"]["archive_sha256"] == sha256(archive.read_bytes())
    assert manifest.get("text_runtime") is None
    assert files[install.M2_RUNTIME_ARCHIVE] == archive.read_bytes()
    del manifest["m2_runtime"]
    with pytest.raises(BoundaryError, match="text_runtime_inventory_incomplete"):
        install.text_runtime_files(manifest, files, memory=True)


def test_v2_kit_pair_is_inventoried_staged_and_selected_without_caller_path(
        inputs, tmp_path, monkeypatch):
    from spireagent.workbench.developer import ProjectConfig, combination
    from spireagent.workbench.local_models import LocalModelService
    from tools import install_developer_kit as install

    archive = tmp_path / "v2.tgz"
    archive.write_bytes(b"synthetic v2 candidate")
    profile = tmp_path / "v2.json"
    profile.write_text(json.dumps({
        "schema": "stpd/local-text-m2-runtime-v2",
        "runtime_package": synthetic_text_pin(archive.read_bytes()),
    }))
    supplied = {"m2_v2_runtime_profile": PinnedFile(profile, sha256(profile.read_bytes())),
                "m2_v2_runtime_archive": PinnedFile(archive, sha256(archive.read_bytes()))}
    with pytest.raises(BoundaryError, match="m2_v2_runtime_profile_and_archive_required"):
        package(**{**inputs, "m2_v2_runtime_profile": supplied["m2_v2_runtime_profile"]})
    wrong = json.loads(profile.read_text())
    wrong["schema"] = "stpd/local-text-m2-runtime-v1"
    profile.write_text(json.dumps(wrong))
    with pytest.raises(BoundaryError, match="text_runtime_profile_invalid"):
        package(**{**inputs, **supplied, "m2_v2_runtime_profile": PinnedFile(
            profile, sha256(profile.read_bytes()))})
    wrong["schema"] = "stpd/local-text-m2-runtime-v2"
    wrong["runtime_package"]["release_asset_sha256"] = "0" * 64
    profile.write_text(json.dumps(wrong))
    with pytest.raises(BoundaryError, match="text_runtime_archive_checksum_mismatch"):
        package(**{**inputs, **supplied, "m2_v2_runtime_profile": PinnedFile(
            profile, sha256(profile.read_bytes()))})
    wrong["runtime_package"] = synthetic_text_pin(archive.read_bytes())
    profile.write_text(json.dumps(wrong))
    supplied["m2_v2_runtime_profile"] = PinnedFile(profile, sha256(profile.read_bytes()))
    checked = []
    monkeypatch.setattr("tools.package_developer_kit.install_runtime",
                        lambda *a, **k: checked.append((a, k)))
    package(**{**inputs, **supplied})
    assert len(checked) == 1
    assert checked[0][0][0].name == "text-menu-m2-v2"
    assert checked[0][1]["required_profile"] == "text-menu-m2-v2"
    monkeypatch.setattr(install, "REPOSITORY", str(inputs["root"]))
    expected = sha256(inputs["output"].read_bytes())
    target = tmp_path / "releases" / expected
    receipt = install.prepare(inputs["output"], expected, target.parent)
    assert receipt["m2_v2_runtime"] == "bundled_installation_not_checked"
    assert receipt["m2_v2_runtime_identity"] == {
        "profile_sha256": sha256(profile.read_bytes()),
        "archive_sha256": sha256(archive.read_bytes()),
    }
    service = LocalModelService(ProjectConfig(tmp_path / "state", "", "", None, combination()))
    service.root = target / "source/python"
    raw, staged_archive, pin = service._selected_kit_text_runtime("text-menu-m2-v2")
    assert raw == profile.read_bytes()
    assert staged_archive.read_bytes() == archive.read_bytes()
    assert pin == synthetic_text_pin(archive.read_bytes())
    staged_archive.write_bytes(b"replaced")
    with pytest.raises(BoundaryError, match="trusted_text_runtime_kit_invalid"):
        service._selected_kit_text_runtime("text-menu-m2-v2")
    staged_archive.unlink()
    staged_archive.symlink_to(archive)
    with pytest.raises(BoundaryError, match="trusted_text_runtime_kit_invalid"):
        service._selected_kit_text_runtime("text-menu-m2-v2")


def test_text_and_m2_kit_profiles_coexist_and_staged_m2_drift_blocks_status(
        inputs, tmp_path, monkeypatch):
    from tools import install_developer_kit as install

    supplied = {}
    for name, schema in (("text", "stpd/local-text-runtime-v1"),
                         ("m2", "stpd/local-text-m2-runtime-v1")):
        archive = tmp_path / f"{name}.tgz"
        archive.write_bytes(name.encode() + b" synthetic runtime")
        profile = tmp_path / f"{name}.json"
        profile.write_text(json.dumps({
            "schema": schema,
            "runtime_package": synthetic_text_pin(archive.read_bytes()),
        }))
        supplied[f"{name}_runtime_profile"] = PinnedFile(profile, sha256(profile.read_bytes()))
        supplied[f"{name}_runtime_archive"] = PinnedFile(archive, sha256(archive.read_bytes()))
    monkeypatch.setattr("tools.package_developer_kit.install_runtime", lambda *a, **k: None)
    package(**{**inputs, **supplied})
    monkeypatch.setattr(install, "REPOSITORY", str(inputs["root"]))
    releases = tmp_path / "releases"
    expected = sha256(inputs["output"].read_bytes())
    receipt = install.prepare(inputs["output"], expected, releases)
    target = releases / expected
    assert receipt["text_runtime"] == "bundled_installation_not_checked"
    assert receipt["m2_runtime"] == "bundled_installation_not_checked"
    assert (target / "source" / install.TEXT_RUNTIME_DESTINATION).read_bytes() == (
        supplied["text_runtime_profile"].path.read_bytes())
    m2_staged = target / "source" / install.M2_RUNTIME_DESTINATION
    assert m2_staged.read_bytes() == supplied["m2_runtime_profile"].path.read_bytes()
    m2_staged.write_bytes(b"changed")
    with pytest.raises(BoundaryError, match="staged_text_runtime_changed"):
        install.status(target)


def test_workbench_consumes_real_kit_verifier_receipt_and_rejects_staged_drift(
        inputs, tmp_path, monkeypatch):
    from spireagent.workbench.developer import ProjectConfig, combination
    from spireagent.workbench.local_models import LocalModelService
    from tools import install_developer_kit as install

    archive = tmp_path / "runtime.tgz"
    archive.write_bytes(b"synthetic exact runtime")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"schema": "stpd/local-text-runtime-v1",
                                   "runtime_package": synthetic_text_pin(archive.read_bytes())}))
    monkeypatch.setattr("tools.package_developer_kit.install_runtime", lambda *a, **k: None)
    package(**{**inputs,
               "text_runtime_profile": PinnedFile(profile, sha256(profile.read_bytes())),
               "text_runtime_archive": PinnedFile(archive, sha256(archive.read_bytes()))})
    monkeypatch.setattr(install, "REPOSITORY", str(inputs["root"]))
    target = tmp_path / "releases" / sha256(inputs["output"].read_bytes())
    receipt = install.prepare(inputs["output"], target.name, target.parent)
    assert receipt["text_runtime_identity"] == {
        "profile_sha256": sha256(profile.read_bytes()),
        "archive_sha256": sha256(archive.read_bytes()),
    }
    assert receipt["m2_runtime_identity"] is None
    service = LocalModelService(ProjectConfig(tmp_path / "state", "", "", None, combination()))
    service.root = target / "source/python"
    actual, staged_archive, pin = service._selected_kit_text_runtime("text-menu-v1")
    assert actual == profile.read_bytes()
    assert staged_archive.read_bytes() == archive.read_bytes()
    assert pin == synthetic_text_pin(archive.read_bytes())
    with pytest.raises(BoundaryError, match="trusted_text_runtime_asset_not_bundled"):
        service._selected_kit_text_runtime("text-menu-m2-v1")
    with pytest.raises(BoundaryError, match="trusted_text_runtime_asset_not_bundled"):
        service._selected_kit_text_runtime("text-menu-m2-v2")
    staged = target / "source" / install.TEXT_RUNTIME_DESTINATION
    staged.write_bytes(b"changed")
    with pytest.raises(BoundaryError, match="trusted_text_runtime_kit_invalid"):
        service._selected_kit_text_runtime("text-menu-v1")
    staged.write_bytes(profile.read_bytes())
    kit_manifest = target / "kit/combination.json"
    original_manifest = kit_manifest.read_bytes()
    kit_manifest.write_bytes(b"changed inventory")
    with pytest.raises(BoundaryError, match="trusted_text_runtime_kit_invalid"):
        service._selected_kit_text_runtime("text-menu-v1")
    kit_manifest.write_bytes(original_manifest)
    lock = target / "source/python/uv.lock"
    lock.write_bytes(b"changed tracked source")
    with pytest.raises(BoundaryError, match="trusted_text_runtime_kit_invalid"):
        service._selected_kit_text_runtime("text-menu-v1")


@pytest.mark.parametrize("mutation", ["archive", "profile", "closure"])
def test_optional_runtime_invalid_inputs_never_publish(inputs, tmp_path, monkeypatch,
                                                       mutation):
    archive = tmp_path / "runtime.tgz"
    archive.write_bytes(b"synthetic archive")
    profile = tmp_path / "profile.json"
    pin = synthetic_text_pin(archive.read_bytes())
    if mutation == "archive":
        pin["release_asset_sha256"] = "0" * 64
    if mutation == "profile":
        pin["dependency_layout"] = "flat"
    profile.write_text(json.dumps({"schema": "stpd/local-text-runtime-v1",
                                   "runtime_package": pin}))
    def closure_check(*args, **kwargs):
        raise BoundaryError("local_model", "pinned_runtime_install_verification_failed")
    monkeypatch.setattr("tools.package_developer_kit.install_runtime", closure_check)
    with pytest.raises(BoundaryError):
        package(**{**inputs,
                   "text_runtime_profile": PinnedFile(profile, sha256(profile.read_bytes())),
                   "text_runtime_archive": PinnedFile(archive, sha256(archive.read_bytes()))})
    assert not inputs["output"].exists()


def test_optional_runtime_package_prepare_and_status_preserve_staged_bytes(
    inputs, tmp_path, monkeypatch
):
    from tools import install_developer_kit as install

    archive = tmp_path / "runtime.tgz"
    archive.write_bytes(b"synthetic archive")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"schema": "stpd/local-text-runtime-v1",
                                   "runtime_package": synthetic_text_pin(archive.read_bytes())}))
    # The closure's real extraction and install are exercised in test_runtime_install.
    monkeypatch.setattr("tools.package_developer_kit.install_runtime", lambda *a, **k: None)
    package(**{**inputs,
               "text_runtime_profile": PinnedFile(profile, sha256(profile.read_bytes())),
               "text_runtime_archive": PinnedFile(archive, sha256(archive.read_bytes()))})
    monkeypatch.setattr(install, "REPOSITORY", str(inputs["root"]))
    root = tmp_path / "releases"
    expected = sha256(inputs["output"].read_bytes())
    receipt = install.prepare(inputs["output"], expected, root)
    target = root / expected
    assert receipt["text_runtime"] == "bundled_installation_not_checked"
    staged_profile = target / "source" / install.TEXT_RUNTIME_DESTINATION
    staged_archive = target / "source" / install.TEXT_ARCHIVE_DESTINATION
    assert staged_profile.read_bytes() == profile.read_bytes()
    assert staged_archive.read_bytes() == archive.read_bytes()
    assert install.status(target)["text_runtime"] == "bundled_installation_not_checked"
    with pytest.raises(BoundaryError, match="release_exists"):
        install.prepare(inputs["output"], expected, root)
    staged_archive.write_bytes(b"changed")
    with pytest.raises(BoundaryError, match="staged_text_runtime_changed"):
        install.status(target)


def test_packager_uses_real_npm_closure_validation(inputs, tmp_path, bundled_release):
    from tools.install_developer_kit import run

    _, runtime_root, pin = bundled_release
    output = run(["npm", "pack", "--ignore-scripts", "--pack-destination", str(tmp_path)],
                 runtime_root)
    archive = tmp_path / output.strip().splitlines()[-1]
    pin["release_asset_sha256"] = sha256(archive.read_bytes())
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"schema": "stpd/local-text-runtime-v1",
                                   "runtime_package": pin}))
    package(**{**inputs,
               "text_runtime_profile": PinnedFile(profile, sha256(profile.read_bytes())),
               "text_runtime_archive": PinnedFile(archive, sha256(archive.read_bytes()))})
    assert inputs["output"].exists()


@pytest.mark.parametrize("change", ["old_tool", "mismatched_mod"])
def test_new_workflow_kit_requires_native_setup_and_same_mod(inputs, change):
    tool = inputs["collection_tool"]
    manifest_path = tool / "collection-tool.json"
    value = json.loads(manifest_path.read_bytes())
    if change == "old_tool":
        del value["identity"]["collection_setup_entrypoint"]
    else:
        path = inputs["mod_dll"].path
        path.write_bytes(b"another independently valid native candidate")
        inputs["mod_dll"] = PinnedFile(path, sha256(path.read_bytes()))
    value["release_id"] = digest(value["identity"])
    manifest_path.write_text(json.dumps(value))
    inputs["tool_release_id"] = value["release_id"]
    with pytest.raises(BoundaryError, match="collection_setup_"):
        package(**inputs)
    assert not inputs["output"].exists()


@pytest.mark.parametrize("field", ["mod_dll", "mod_manifest", "platform_bom"])
def test_explicit_public_file_tamper_never_publishes(inputs, field):
    inputs[field].path.write_bytes(b"changed")
    with pytest.raises(BoundaryError, match="pinned_file_changed"):
        package(**inputs)
    assert not inputs["output"].exists()


@pytest.mark.parametrize("change", ["tamper", "extra", "untrusted_manifest", "wrong_pin"])
def test_tool_owner_rejection_and_uninventoried_manifest_fields(inputs, change):
    tool = inputs["collection_tool"]
    if change == "tamper":
        (tool / "sts2-human-annotator.dll").write_bytes(b"changed")
    elif change == "extra":
        (tool / "credentials.env").write_text("TOKEN=private")
    elif change == "untrusted_manifest":
        path = tool / "collection-tool.json"
        value = json.loads(path.read_bytes())
        value["ignored_private_field"] = "must-not-be-packaged"
        path.write_text(json.dumps(value))
    else:
        inputs["tool_release_id"] = "0" * 64
    with pytest.raises(ValueError):
        package(**inputs)
    assert not inputs["output"].exists()


@pytest.mark.parametrize("kind", ["mod", "tool_file", "tool_directory"])
def test_symlinks_are_not_followed(inputs, tmp_path, kind):
    if kind == "mod":
        path = tmp_path / "mod-link"
        target = inputs["mod_dll"].path
    elif kind == "tool_file":
        path = inputs["collection_tool"] / "extra-link"
        target = inputs["mod_dll"].path
    else:
        path = tmp_path / "tool-link"
        target = inputs["collection_tool"]
    try:
        path.symlink_to(target, target_is_directory=kind == "tool_directory")
    except OSError:
        pytest.skip("OS does not permit symlink creation")
    if kind == "mod":
        inputs["mod_dll"] = PinnedFile(path, inputs["mod_dll"].sha256)
    elif kind == "tool_directory":
        inputs["collection_tool"] = path
    with pytest.raises(ValueError):
        package(**inputs)
    assert not inputs["output"].exists()


def test_existing_output_is_immutable_even_at_publication_race(inputs, monkeypatch):
    inputs["output"].write_bytes(b"original release")
    with pytest.raises(BoundaryError, match="output_already_exists"):
        package(**inputs)
    assert inputs["output"].read_bytes() == b"original release"
    inputs["output"].unlink()
    real_link = os.link

    def raced_link(source, destination):
        Path(destination).write_bytes(b"concurrently published release")
        real_link(source, destination)

    monkeypatch.setattr(os, "link", raced_link)
    with pytest.raises(FileExistsError):
        package(**inputs)
    assert inputs["output"].read_bytes() == b"concurrently published release"


def test_dirty_source_and_mid_packaging_changes_never_publish(inputs, monkeypatch):
    lock = inputs["root"] / "uv.lock"
    original = lock.read_bytes()
    lock.write_bytes(b"changed lock")
    with pytest.raises(BoundaryError, match="clean_checkout_required"):
        package(**inputs)
    assert not inputs["output"].exists()
    lock.write_bytes(original)
    real_write = zipfile.ZipFile.writestr

    def changing_write(self, *args, **kwargs):
        result = real_write(self, *args, **kwargs)
        lock.write_bytes(b"source changed during packaging")
        return result

    monkeypatch.setattr(zipfile.ZipFile, "writestr", changing_write)
    with pytest.raises(BoundaryError, match="clean_checkout_required"):
        package(**inputs)
    assert not inputs["output"].exists()
