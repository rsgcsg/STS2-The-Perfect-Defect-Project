from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError
from tools import install_developer_kit as install


def archive(tmp_path: Path, *, extra: str | None = None) -> tuple[Path, str]:
    files = {name: b"synthetic" for name in install.STAGING}
    for name in ("platform-bom.json", "developer-combination.json", "mod/STS2_PLATFORM.json"):
        files[name] = b"synthetic"
    if extra:
        files[extra] = b"unsafe"
    manifest = {
        "schema": "spireagent/developer-kit-v1",
        "stpd_source_revision": "a" * 40,
        "uv_lock_sha256": "b" * 64,
        "collection_tool_release_id": "c" * 64,
        "mod_sha256": install.sha(files["mod/STS2_PLATFORM.dll"]),
        "mod_manifest_sha256": install.sha(files["mod/STS2_PLATFORM.json"]),
        "platform_bom_sha256": install.sha(files["platform-bom.json"]),
        "developer_combination_sha256": install.sha(files["developer-combination.json"]),
        "files": {name: install.sha(raw) for name, raw in files.items()},
    }
    files["combination.json"] = json.dumps(manifest).encode()
    target = tmp_path / "kit.zip"
    with zipfile.ZipFile(target, "w") as z:
        for name, raw in files.items():
            # ZipInfo normalizes the host separator on Windows. Preserve the
            # literal archive name so the unsafe-path fixture is OS-independent.
            entry = zipfile.ZipInfo(name)
            entry.filename = name
            entry.orig_filename = name
            z.writestr(entry, raw)
    return target, install.sha(target.read_bytes())


def test_verified_inventory_needs_independent_archive_hash(tmp_path):
    path, expected = archive(tmp_path)
    manifest, files = install.verified_archive(path, expected)
    assert manifest["stpd_source_revision"] == "a" * 40
    assert set(install.STAGING).issubset(files)
    with pytest.raises(BoundaryError, match="checksum"):
        install.verified_archive(path, "f" * 64)
    with zipfile.ZipFile(path, "a") as z:
        z.writestr("unlisted", b"not in manifest")
    with pytest.raises(BoundaryError, match="inventory"):
        install.verified_archive(path, install.sha(path.read_bytes()))


def test_text_runtime_manifest_must_bind_both_profile_and_archive(tmp_path):
    path, _ = archive(tmp_path)
    with zipfile.ZipFile(path, "r") as z:
        files = {name: z.read(name) for name in z.namelist()}
    profile = json.dumps({"schema": "stpd/local-text-runtime-v1", "runtime_package": {
        "package": "@rsgcsg/sts2-policy-runtime",
        "version": "0.1.0",
        "source_revision": "a" * 40,
        "component_tree_revision": "b" * 40,
        "release_asset_sha256": install.sha(b"archive"),
        "package_content_sha256": "c" * 64,
        "dependency_layout": "bundled_source_candidate",
        "bundled_connector_pin": {},
    }}).encode()
    files[install.TEXT_RUNTIME_PROFILE] = profile
    files[install.TEXT_RUNTIME_ARCHIVE] = b"archive"
    manifest = json.loads(files["combination.json"])
    manifest["files"].update({name: install.sha(files[name]) for name in (
        install.TEXT_RUNTIME_PROFILE, install.TEXT_RUNTIME_ARCHIVE)})
    manifest["text_runtime"] = {"profile_sha256": install.sha(profile),
                                 "archive_sha256": install.sha(b"archive")}
    def check():
        path.unlink()
        files["combination.json"] = json.dumps(manifest).encode()
        with zipfile.ZipFile(path, "w") as z:
            for name, raw in files.items():
                z.writestr(name, raw)
        return install.verified_archive(path, install.sha(path.read_bytes()))
    assert check()[0]["text_runtime"]["profile_sha256"] == install.sha(profile)
    del manifest["text_runtime"]
    with pytest.raises(BoundaryError, match="inventory_incomplete"):
        check()
    manifest["text_runtime"] = {"profile_sha256": "0" * 64,
                                "archive_sha256": install.sha(b"archive")}
    with pytest.raises(BoundaryError, match="inventory_mismatch"):
        check()
    manifest["text_runtime"]["profile_sha256"] = install.sha(profile)
    files[install.TEXT_RUNTIME_ARCHIVE] = b"changed"
    manifest["files"][install.TEXT_RUNTIME_ARCHIVE] = install.sha(b"changed")
    manifest["text_runtime"]["archive_sha256"] = install.sha(b"changed")
    with pytest.raises(BoundaryError, match="archive_checksum_mismatch"):
        check()


@pytest.mark.parametrize("extra", ["../outside", "/absolute", "C:/drive", "a\\b", "a/../b"])
def test_unsafe_archive_names_fail_before_any_extraction(tmp_path, extra):
    path, expected = archive(tmp_path, extra=extra)
    with pytest.raises(BoundaryError, match="unsafe"):
        install.verified_archive(path, expected)
    assert set(p.name for p in tmp_path.iterdir()) == {"kit.zip"}


def test_reader_normalization_cannot_hide_an_unsafe_name(tmp_path, monkeypatch):
    path, expected = archive(tmp_path, extra="a\\b")
    original = zipfile.ZipInfo

    class NormalizingReader(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # Reproduce Windows ZipInfo reader normalization on every test OS.
            self.filename = self.filename.replace("\\", "/")

    monkeypatch.setattr(zipfile, "ZipInfo", NormalizingReader)
    with pytest.raises(BoundaryError, match="unsafe"):
        install.verified_archive(path, expected)
    assert set(p.name for p in tmp_path.iterdir()) == {"kit.zip"}


def test_wrong_native_game_and_running_game_never_deploy(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "status", lambda _: {"status": "prepared"})
    data = tmp_path / "game"
    data.mkdir()
    (data / "sts2.dll").write_bytes(b"changed game")
    doctor = {
        "status": "ok",
        "game_running": True,
        "platform": "darwin",
        "architecture": "arm64",
        "installation": {"data_dir": str(data)},
        "build_provenance": {
            "platform": "darwin",
            "architecture": "arm64",
            "game": {
                "sts2": {"sha256": "a" * 64},
                "godotsharp_sha256": "b" * 64,
                "harmony_sha256": "c" * 64,
            },
        },
    }
    calls = []

    def run(args, cwd, **kwargs):
        calls.append(args)
        assert args[-1] == "doctor"
        return json.dumps(doctor)

    monkeypatch.setattr(install, "run", run)
    with pytest.raises(BoundaryError, match="closed"):
        install.deploy(tmp_path, data)
    doctor["game_running"] = False
    with pytest.raises(BoundaryError, match="native_game"):
        install.deploy(tmp_path, data)
    assert len(calls) == 2


def test_registration_uses_selected_owner_and_never_replaces_existing_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "status", lambda _: {"tool_release_id": "a" * 64})
    calls = []

    def run(args, cwd):
        calls.append((args, cwd))
        return '{"status":"registered"}'

    monkeypatch.setattr(install, "run", run)
    install.register(tmp_path, tmp_path / "profile.json")
    args, cwd = calls[0]
    assert cwd == tmp_path / "source"
    assert "--replace-tool" not in args and "collection-tool" in args
    assert "--locked" in args and "build" not in args


def test_initialize_refuses_a_running_profile_before_changing_dependencies(tmp_path, monkeypatch):
    from test_project_console import config

    from spireagent.workbench.developer_server import instance_lock

    selected = config(tmp_path)
    profile = tmp_path / "project.json"
    profile.write_text(json.dumps(selected.to_dict()))
    directory = tmp_path / "release"
    directory.mkdir()
    calls = []
    monkeypatch.setattr(install, "status", lambda _: {"status": "prepared",
                                                     "text_runtime": "not_bundled"})
    monkeypatch.setattr(install, "run", lambda args, cwd: calls.append(args) or "")
    with instance_lock(selected.state_dir / "instance.lock"), pytest.raises(BoundaryError):
        install.initialize(directory, profile)
    assert calls == []
    assert install.initialize(directory, profile)["environment"] == "initialized"
    assert calls[0] == ["npm", "ci"]
    assert calls[1] == ["npm", "ci", "--prefix", "python"]
    assert "--locked" in calls[2]
    assert selected.to_dict() == json.loads(profile.read_bytes())


def test_initialize_text_runtime_releases_instance_lock_before_owner_cli(tmp_path, monkeypatch):
    from test_project_console import config

    from spireagent.workbench.developer_server import instance_lock

    selected = config(tmp_path)
    profile = tmp_path / "project.json"
    profile.write_text(json.dumps(selected.to_dict()))
    directory = tmp_path / "release"
    directory.mkdir()
    monkeypatch.setattr(install, "status", lambda _: {
        "status": "prepared", "text_runtime": "bundled_installation_not_checked"})
    commands = []
    def run(args, cwd):
        commands.append(args)
        if "model" in args:
            # Real instance_lock call catches holding the same OS lock in the parent.
            with instance_lock(selected.state_dir / "instance.lock"):
                pass
            assert "--runtime-profile" in args and "text-menu-v1" in args
            return '{"status":"runtime_installed"}'
        return ""
    monkeypatch.setattr(install, "run", run)
    result = install.initialize(directory, profile)
    assert result["environment"] == "initialized"
    assert result["text_runtime"] == "installed_verified_by_runtime_owner"
    assert [args[0] for args in commands] == ["npm", "npm", "uv", "uv"]


def test_initialize_m2_runtime_uses_real_cli_parser_and_owner_install_boundary(
        tmp_path, monkeypatch, capsys):
    from test_project_console import config

    from spireagent.workbench import developer_cli, local_model_cli, runtime_install
    from spireagent.workbench.local_models import LocalModelService

    selected = config(tmp_path)
    profile = tmp_path / "project.json"
    profile.write_text(json.dumps(selected.to_dict()))
    directory = tmp_path / "release"
    directory.mkdir()
    monkeypatch.setattr(install, "status", lambda _: {
        "status": "prepared", "text_runtime": "not_bundled",
        "m2_runtime": "bundled_installation_not_checked"})
    monkeypatch.setattr(developer_cli.ProjectConfig, "load", lambda *_a, **_k: selected)
    monkeypatch.setattr(local_model_cli, "running", lambda _: None)
    expected = (selected.state_dir / "models/text-menu-m2-v1",
                {"package": runtime_install.RUNTIME_PACKAGE})
    monkeypatch.setattr(LocalModelService, "text_runtime_profile",
                        lambda _self, profile="text-menu-v1": expected if profile ==
                        "text-menu-m2-v1" else (_ for _ in ()).throw(AssertionError(profile)))
    monkeypatch.setattr(LocalModelService, "_connector_pin", lambda _: {})
    installed = []
    monkeypatch.setattr(runtime_install, "install_runtime",
                        lambda *a, **k: installed.append((a, k)) or
                        {"status": "runtime_installed"})
    def run(args, _cwd):
        if "model" in args:
            assert developer_cli.main(args[args.index("model"):]) == 0
            return capsys.readouterr().out
        return ""
    monkeypatch.setattr(install, "run", run)
    receipt = install.initialize(directory, profile)
    assert receipt["m2_runtime"] == "installed_verified_by_runtime_owner"
    assert installed == [((expected[0], expected[1], {}),
                          {"archive": directory / "source" / install.M2_ARCHIVE_DESTINATION})]


def test_initialize_new_profile_runs_real_owner_setup_without_selection(tmp_path, monkeypatch):
    from spireagent.workbench.developer import ProjectConfig
    from spireagent.workbench.developer_server import instance_lock

    directory = tmp_path / "release"
    directory.mkdir()
    profile = tmp_path / "private/project.json"
    source = Path(__file__).resolve().parents[2]
    registry = source / "python/.local/token-policies-v1.json"
    original_registry = registry.read_bytes() if registry.exists() else None
    monkeypatch.setattr(install, "status", lambda _: {
        "status": "prepared", "text_runtime": "bundled_installation_not_checked"})
    calls = []
    def run(args, cwd):
        calls.append((args, cwd))
        assert cwd == directory / "source"
        if "setup" in args:
            # Run the actual CLI subprocess against this test checkout's equivalent source.
            result = subprocess.run(args, cwd=source, capture_output=True, text=True,
                                    check=True)
            return result.stdout
        if "model" in args:
            selected = ProjectConfig.load(profile, require_current_combination=False)
            with instance_lock(selected.state_dir / "instance.lock"):
                pass
            assert selected.state_dir == profile.parent
            assert "--selection" not in args
            assert (registry.read_bytes() if registry.exists() else None) == original_registry
            return '{"status":"runtime_installed"}'
        return ""
    monkeypatch.setattr(install, "run", run)
    assert install.initialize(directory, profile)["environment"] == "initialized"
    loaded = ProjectConfig.load(profile, require_current_combination=False)
    assert loaded.state_dir == profile.parent
    assert "setup" in calls[0][0] and "model" in calls[-1][0]


def test_selected_source_uv_subprocess_ignores_foreign_python_and_uv_targets(
    tmp_path, monkeypatch
):
    source = Path(__file__).resolve().parents[2]
    foreign = tmp_path / "foreign"
    package = foreign / "spireagent"
    workbench = package / "workbench"
    workbench.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (workbench / "__init__.py").write_text("")
    (workbench / "developer.py").write_text(f"ROOT = {str(foreign)!r}\n")
    foreign_environment = tmp_path / "foreign-environment"
    foreign_environment.mkdir()
    sentinel = foreign_environment / "sentinel"
    sentinel.write_text("unchanged")
    monkeypatch.setenv("PYTHONPATH", str(foreign))
    command = [
        "uv", "run", "--project", "python", "--locked", "--extra", "cloud", "python",
        "-c", "import spireagent.workbench.developer as d; print(d.ROOT)",
    ]
    assert install.run(command, source).strip() == str(source / "python")
    monkeypatch.setenv("PYTHONHOME", str(foreign))
    monkeypatch.setenv("VIRTUAL_ENV", str(foreign_environment))
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(foreign_environment))
    monkeypatch.setenv("UV_WORKING_DIR", str(foreign))
    monkeypatch.setenv("UV_PROJECT", str(foreign))
    monkeypatch.setenv("UV_PYTHON", str(foreign / "python"))
    assert install.run(command, source).strip() == str(source / "python")
    assert sentinel.read_text() == "unchanged"
    assert sorted(p.name for p in foreign_environment.iterdir()) == ["sentinel"]
