from __future__ import annotations

import copy
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


def prepared_status_fixture(tmp_path: Path, monkeypatch, *, include_text_runtime: bool = False):
    directory = tmp_path / ("f" * 64)
    kit, source = directory / "kit", directory / "source"
    files = {name: ("synthetic:" + name).encode() for name in install.STAGING}
    files["combination.json"] = b"{}"
    runtime_pair = install.KIT_RUNTIME_PAIRS["text-menu-v1"]
    if include_text_runtime:
        files[runtime_pair[0]] = b"synthetic profile"
        files[runtime_pair[1]] = b"synthetic archive"
    manifest = {
        "stpd_source_revision": "a" * 40,
        "collection_tool_release_id": "c" * 64,
        "files": {name: install.sha(raw) for name, raw in files.items()
                  if name != "combination.json"},
    }
    if include_text_runtime:
        manifest[runtime_pair[4]] = {
            "profile_sha256": install.sha(files[runtime_pair[0]]),
            "archive_sha256": install.sha(files[runtime_pair[1]]),
        }
    kit.mkdir(parents=True)
    (kit / "combination.json").write_bytes(files["combination.json"])
    for name, raw in files.items():
        if name == "combination.json":
            continue
        destination = kit / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    for name, relative in install.STAGING.items():
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(files[name])
    if include_text_runtime:
        for name, relative in ((runtime_pair[0], runtime_pair[2]),
                               (runtime_pair[1], runtime_pair[3])):
            destination = source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(files[name])

    monkeypatch.setattr(install, "verified_archive", lambda *_: (manifest, files))
    monkeypatch.setattr(
        install, "run",
        lambda args, cwd, **kwargs: "a" * 40 if args == ["git", "rev-parse", "HEAD"] else "",
    )
    monkeypatch.setattr(install, "CollectionTool", lambda *_args, **_kwargs: None)
    if include_text_runtime:
        monkeypatch.setattr(install, "text_runtime_pin", lambda *_args, **_kwargs: {})
    return directory, source, kit, files


def package_tuple_fixture():
    host = {
        "package": "@rsgcsg/sts2-host-runtime", "version": "1.1.0-rc.7",
        "source_revision": "a" * 40, "component_tree_revision": "b" * 40,
        "release_asset_sha256": "c" * 64, "package_content_sha256": "d" * 64,
    }
    sdk = {
        "package": "@rsgcsg/sts2-connector-client", "version": "1.1.0-rc.1",
        "source_revision": "e" * 40, "component_tree_revision": "f" * 40,
        "release_asset_sha256": "1" * 64, "package_content_sha256": "2" * 64,
    }
    combination = {"schema": "stpd/developer-combination-v1",
                   "node_packages": [host, sdk]}
    bom = {
        "components": {"host_runtime": {"version": "1.1.0-rc.22"},
                       "connector": {"version": "1.3.0-rc.8"},
                       "player_environment_protocol": "1.0.0"},
        "public_packages": {
            "host_runtime": {
                "version": "1.1.0-rc.7", "source_revision": "a" * 40,
                "sha256": "c" * 64, "package_content_digest_sha256": "d" * 64,
                "asset": "rsgcsg-sts2-host-runtime-1.1.0-rc.7.tgz",
                "release": "host-runtime/v1.1.0-rc.7",
                "source_relation": "published_package_precedes_current_source",
            },
            "typescript_sdk": {
                "asset": "rsgcsg-sts2-connector-client-1.1.0-rc.1.tgz",
                "sha256": "1" * 64,
            },
        },
        "exact_runtime_candidate": {"connector": {"protocol": "1.0.0"}},
        "current_v2_candidate": {"connector": {"protocol": "1.0.0"}},
    }
    connector_release = {
        "release": {"version": "1.3.0-rc.8"},
        "player_environment": {"protocol": "1.0.0"},
    }
    return combination, bom, connector_release


def preflight_fixture(tmp_path: Path, monkeypatch):
    combination, bom, connector_release = package_tuple_fixture()
    directory = tmp_path / ("a" * 64)
    source, kit = directory / "source", directory / "kit"
    (source / "python/configs/developer").mkdir(parents=True)
    (source / "components/connector").mkdir(parents=True)
    (source / "components/host-runtime").mkdir(parents=True)
    (source / "python").mkdir(exist_ok=True)
    kit.mkdir(parents=True)
    combination_bytes = json.dumps(combination).encode()
    bom_bytes = json.dumps(bom).encode()
    (kit / "developer-combination.json").write_bytes(combination_bytes)
    (source / "python/configs/developer/combination-v1.json").write_bytes(combination_bytes)
    (kit / "platform-bom.json").write_bytes(bom_bytes)
    (source / "platform-bom.json").write_bytes(bom_bytes)
    (source / "components/connector/release-manifest.json").write_text(
        json.dumps(connector_release))
    (source / "components/host-runtime/package.json").write_text(
        json.dumps({"engines": {"node": ">=20"}}))
    (source / "python/pyproject.toml").write_text('requires-python = ">=3.11,<3.12"\n')
    runtimeconfig = (source / install.STAGING[
        "collection-tool/sts2-human-annotator.runtimeconfig.json"])
    runtimeconfig.parent.mkdir(parents=True)
    runtimeconfig.write_text(json.dumps({"runtimeOptions": {"framework": {
        "name": "Microsoft.NETCore.App", "version": "9.0.0"}}}))

    game = tmp_path / "private-game-root"
    data = game / "data_sts2_macos_arm64"
    data.mkdir(parents=True)
    game_bytes = {"sts2.dll": b"synthetic game assembly",
                  "GodotSharp.dll": b"synthetic engine dependency",
                  "0Harmony.dll": b"synthetic mod dependency"}
    for name, raw in game_bytes.items():
        (data / name).write_bytes(raw)
    release = {"version": "v0.111.0", "commit": "synthetic-commit"}
    (game / "release_info.json").write_text(json.dumps(release))
    provenance = {
        "platform": "darwin", "architecture": "arm64",
        "game": {"sts2": {"sha256": install.sha(game_bytes["sts2.dll"])},
                 "godotsharp_sha256": install.sha(game_bytes["GodotSharp.dll"]),
                 "harmony_sha256": install.sha(game_bytes["0Harmony.dll"]),
                 "release": release},
    }
    build_provenance = kit / "collection-tool/game-mod/build-provenance.json"
    build_provenance.parent.mkdir(parents=True)
    build_provenance.write_text(json.dumps(provenance))
    doctor = {"status": "ok", "game_running": False, "platform": "darwin",
              "architecture": "arm64", "installation": {
                  "game_dir": str(game), "data_dir": str(data),
                  "release_info": str(game / "release_info.json")}}
    monkeypatch.setattr(install, "status", lambda _: {"source_revision": "3" * 40})
    archive_files = {
        "developer-combination.json": combination_bytes,
        "platform-bom.json": bom_bytes,
        "collection-tool/game-mod/build-provenance.json": build_provenance.read_bytes(),
    }
    monkeypatch.setattr(install, "verified_archive", lambda *_: (
        {"files": {"developer-combination.json": install.sha(combination_bytes)}},
        archive_files,
    ))

    def run(args, cwd, **kwargs):
        if args[-1] == "doctor":
            return json.dumps(doctor)
        if args == ["node", "--version"]:
            return "v22.12.0\n"
        if args == ["dotnet", "--list-runtimes"]:
            return "Microsoft.NETCore.App 9.0.1 [/private/dotnet]\n"
        raise AssertionError(args)

    monkeypatch.setattr(install, "run", run)
    return directory, game, source, kit, doctor, game_bytes


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


def test_status_rejects_symlinked_combination_and_staged_native_file(tmp_path, monkeypatch):
    directory, source, kit, files = prepared_status_fixture(tmp_path, monkeypatch)
    external_combination = tmp_path / "external-combination.json"
    external_combination.write_bytes(files["combination.json"])
    (kit / "combination.json").unlink()
    (kit / "combination.json").symlink_to(external_combination)
    with pytest.raises(BoundaryError, match="prepared_manifest_unsafe"):
        install.status(directory)

    directory, source, kit, files = prepared_status_fixture(tmp_path / "staging", monkeypatch)
    relative = install.STAGING["mod/STS2_PLATFORM.dll"]
    staged = source / relative
    external_native = tmp_path / "external-native.dll"
    external_native.write_bytes(files["mod/STS2_PLATFORM.dll"])
    staged.unlink()
    staged.symlink_to(external_native)
    with pytest.raises(BoundaryError, match="staged_native_unsafe"):
        install.status(directory)

    directory, source, _, _ = prepared_status_fixture(tmp_path / "staging-parent", monkeypatch)
    release_directory = source / "apps/game-mod/bin/Release"
    external_release = tmp_path / "external-release"
    release_directory.rename(external_release)
    release_directory.symlink_to(external_release, target_is_directory=True)
    with pytest.raises(BoundaryError, match="staged_native_unsafe"):
        install.status(directory)

    directory, source, _, runtime_files = prepared_status_fixture(
        tmp_path / "text-runtime", monkeypatch, include_text_runtime=True)
    runtime_pair = install.KIT_RUNTIME_PAIRS["text-menu-v1"]
    staged_profile = source / runtime_pair[2]
    outside_profile = tmp_path / "external-profile.json"
    outside_profile.write_bytes(runtime_files[runtime_pair[0]])
    staged_profile.unlink()
    staged_profile.symlink_to(outside_profile)
    with pytest.raises(BoundaryError, match="staged_text_runtime_unsafe"):
        install.status(directory)


def test_preflight_accepts_published_package_ahead_of_current_source_when_bom_hashes_match():
    combination, bom, connector = package_tuple_fixture()
    result = install.validate_package_tuple(combination, bom, connector)
    assert result["host"]["bom_anchored"]["version"] == "1.1.0-rc.7"
    assert bom["components"]["host_runtime"]["version"] == "1.1.0-rc.22"
    assert result["player_environment_protocol"] == "1.0.0"
    sdk = result["connector_client"]
    assert sdk["bom_anchored"]["release_asset_sha256"] == "1" * 64
    assert sdk["combination_claims_unverified"]["provenance"] == (
        "self_asserted_in_archive_bound_combination")

    combination["node_packages"][1]["source_revision"] = "9" * 40
    combination["node_packages"][1]["package_content_sha256"] = "8" * 64
    result = install.validate_package_tuple(combination, bom, connector)
    claims = result["connector_client"]["combination_claims_unverified"]
    assert claims["source_revision"] == "9" * 40
    assert claims["package_content_sha256"] == "8" * 64
    assert claims["provenance"] == "self_asserted_in_archive_bound_combination"


def test_preflight_rejects_package_hash_protocol_and_duplicate_identity():
    combination, bom, connector = package_tuple_fixture()
    combination["node_packages"][0]["release_asset_sha256"] = "9" * 64
    with pytest.raises(BoundaryError, match="published_package_identity_mismatch"):
        install.validate_package_tuple(combination, bom, connector)

    combination, bom, connector = package_tuple_fixture()
    bom["current_v2_candidate"]["connector"]["protocol"] = "2.0.0"
    with pytest.raises(BoundaryError, match="protocol_incompatible"):
        install.validate_package_tuple(combination, bom, connector)

    combination, bom, connector = package_tuple_fixture()
    combination["node_packages"].append(copy.deepcopy(combination["node_packages"][0]))
    with pytest.raises(BoundaryError, match="package_identity_ambiguous"):
        install.validate_package_tuple(combination, bom, connector)


def test_preflight_is_redacted_read_only_and_marks_unpinned_dependencies_unqualified(
        tmp_path, monkeypatch):
    directory, game, source, kit, _, game_bytes = preflight_fixture(tmp_path, monkeypatch)
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for path in (source, kit, game) for path in path.rglob("*") if path.is_file()}
    report = install.preflight(directory, game)
    after = {str(path.relative_to(tmp_path)): path.read_bytes()
             for path in (source, kit, game) for path in path.rglob("*") if path.is_file()}
    rendered = json.dumps(report)
    assert report["admission"] == "unqualified"
    assert report["game_running"] is False
    assert report["game"]["identity"] == "matched"
    assert report["game"]["identity_scope"] == "on_disk_files"
    assert report["game"]["loaded_bytes_identity"] == "not_observed"
    assert report["target"] == {
        "platform": "darwin", "architecture": "arm64",
        "identity_source": "verified_build_provenance_and_read_only_doctor",
    }
    assert all(dep["release_pin"] == "unknown" for dep in report["dependencies"])
    assert report["effects"] == {"installed": False, "started": False, "loaded": False}
    assert "no_lock_against_concurrent_local_filesystem_replacement" in report["non_claims"]
    assert "game_running_is_a_point_in_time_doctor_observation" in report["non_claims"]
    assert "running_process_loaded_bytes_are_not_observed" in report["non_claims"]
    assert before == after
    assert str(tmp_path) not in rendered
    assert all(raw.decode(errors="ignore") not in rendered for raw in game_bytes.values())


def test_preflight_hashes_on_disk_files_while_game_runs_without_loaded_identity_claim(
        tmp_path, monkeypatch):
    directory, game, source, kit, doctor, _ = preflight_fixture(tmp_path, monkeypatch)
    doctor["game_running"] = True
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for root in (source, kit, game) for path in root.rglob("*") if path.is_file()}

    report = install.preflight(directory, game)

    after = {str(path.relative_to(tmp_path)): path.read_bytes()
             for root in (source, kit, game) for path in root.rglob("*") if path.is_file()}
    assert report["status"] == "preflight_complete"
    assert report["game_running"] is True
    assert report["game"]["identity"] == "matched"
    assert report["game"]["identity_scope"] == "on_disk_files"
    assert report["game"]["loaded_bytes_identity"] == "not_observed"
    assert report["effects"] == {"installed": False, "started": False, "loaded": False}
    assert "no_lock_against_concurrent_game_state_change" in report["non_claims"]
    assert "running_process_loaded_bytes_are_not_observed" in report["non_claims"]
    assert before == after

    doctor["game_running"] = None
    with pytest.raises(BoundaryError, match="game_state_unavailable"):
        install.preflight(directory, game)


def test_preflight_canonicalizes_game_alias_and_symlinked_ancestor(tmp_path, monkeypatch):
    directory, game, _, _, _, _ = preflight_fixture(tmp_path, monkeypatch)
    parent_alias = tmp_path / "parent-alias"
    parent_alias.symlink_to(tmp_path, target_is_directory=True)
    report = install.preflight(directory, parent_alias / game.name)
    assert report["game"]["identity"] == "matched"

    game_alias = tmp_path / "game-alias"
    game_alias.symlink_to(game, target_is_directory=True)
    report = install.preflight(directory, game_alias)
    assert report["game"]["identity"] == "matched"


def test_preflight_reports_missing_file_platform_protocol_and_ambiguous_game(
        tmp_path, monkeypatch):
    directory, game, _, kit, doctor, _ = preflight_fixture(tmp_path / "missing", monkeypatch)
    (kit / "collection-tool/game-mod/build-provenance.json").unlink()
    combination_raw = (kit / "developer-combination.json").read_bytes()
    bom_raw = (kit / "platform-bom.json").read_bytes()
    monkeypatch.setattr(install, "verified_archive", lambda *_: (
        {"files": {"developer-combination.json": install.sha(combination_raw)}},
        {"developer-combination.json": combination_raw, "platform-bom.json": bom_raw},
    ))
    with pytest.raises(BoundaryError, match="package_identity_missing"):
        install.preflight(directory, game)

    directory, game, _, _, doctor, _ = preflight_fixture(tmp_path / "platform", monkeypatch)
    doctor["architecture"] = "x86_64"
    with pytest.raises(BoundaryError, match="target_platform_mismatch"):
        install.preflight(directory, game)

    directory, game, _, _, doctor, _ = preflight_fixture(tmp_path / "ambiguous", monkeypatch)
    doctor["installation"]["game_dir"] = str(tmp_path / "other-game")
    with pytest.raises(BoundaryError, match="game_identity_ambiguous"):
        install.preflight(directory, game)


def test_preflight_refuses_game_symlink_path_traversal_and_missing_dotnet_proof(
        tmp_path, monkeypatch):
    directory, game, _, _, doctor, _ = preflight_fixture(tmp_path, monkeypatch)
    data = game / "data_sts2_macos_arm64"
    outside = tmp_path / "outside.dll"
    outside.write_bytes(b"synthetic game assembly")
    (data / "sts2.dll").unlink()
    (data / "sts2.dll").symlink_to(outside)
    with pytest.raises(BoundaryError, match="native_game_file_unsafe"):
        install.preflight(directory, game)

    with pytest.raises(BoundaryError, match="game_identity_ambiguous"):
        install._game_file(game, str(data / ".." / "outside.dll"), "native_game_file")
    with pytest.raises(BoundaryError, match="package_identity_unsafe"):
        install._read_tree_file(directory / "kit", "../platform-bom.json", "package_identity")

    directory, game, _, _, _, _ = preflight_fixture(tmp_path / "dotnet", monkeypatch)
    def no_dotnet(args, cwd, **kwargs):
        if args[-1] == "doctor":
            return json.dumps({"status": "ok", "game_running": False, "platform": "darwin",
                               "architecture": "arm64", "installation": {
                                   "game_dir": str(game),
                                   "data_dir": str(game / "data_sts2_macos_arm64"),
                                   "release_info": str(game / "release_info.json")}})
        if args == ["node", "--version"]:
            return "v22.12.0"
        if args == ["dotnet", "--list-runtimes"]:
            return "Microsoft.AspNetCore.App 9.0.1 [/private/dotnet]"
        raise AssertionError(args)
    monkeypatch.setattr(install, "run", no_dotnet)
    with pytest.raises(BoundaryError, match="runtime_dependency_missing_dotnet"):
        install.preflight(directory, game)


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


def test_v2_partial_or_mismatched_pair_is_rejected(tmp_path):
    path, _ = archive(tmp_path)
    with zipfile.ZipFile(path) as z:
        files = {name: z.read(name) for name in z.namelist()}
    profile_name, archive_name, _, _, key, schema = install.KIT_RUNTIME_PAIRS[
        "text-menu-m2-v2"]
    payload = b"v2 archive"
    profile = json.dumps({"schema": schema, "runtime_package": {
        "package": "@rsgcsg/sts2-policy-runtime", "version": "0.1.0",
        "source_revision": "a" * 40, "component_tree_revision": "b" * 40,
        "release_asset_sha256": install.sha(payload),
        "package_content_sha256": "c" * 64,
        "dependency_layout": "bundled_source_candidate", "bundled_connector_pin": {},
    }}).encode()
    manifest = json.loads(files["combination.json"])
    files[profile_name], files[archive_name] = profile, payload
    manifest["files"].update({profile_name: install.sha(profile),
                              archive_name: install.sha(payload)})
    with pytest.raises(BoundaryError, match="inventory_incomplete"):
        install.text_runtime_files(manifest, files, required_profile="text-menu-m2-v2")
    manifest[key] = {"profile_sha256": "0" * 64,
                     "archive_sha256": install.sha(payload)}
    with pytest.raises(BoundaryError, match="inventory_mismatch"):
        install.text_runtime_files(manifest, files, required_profile="text-menu-m2-v2")
    manifest[key]["profile_sha256"] = install.sha(profile)
    assert install.text_runtime_files(manifest, files, required_profile="text-menu-m2-v2")


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
