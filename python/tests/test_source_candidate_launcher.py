"""Isolated engineering source candidate under the existing native access owner."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import ProjectConfig, atomic_json, combination
from spireagent.workbench.native_workbench_access import NativeBootstrap
from tools import install_developer_kit as install


def candidate(tmp_path, monkeypatch):
    home = tmp_path / "candidate-home"
    config = tmp_path / "project.json"
    atomic_json(config, ProjectConfig(tmp_path / "state", "", "", None, combination()).to_dict())
    identity = {"source_revision": "a" * 40, "workbench_sha256": "b" * 64,
                "uv_lock_sha256": "c" * 64}
    binding = {"schema": install.SOURCE_CANDIDATE_LAUNCHER_SCHEMA,
               "source_directory": str(tmp_path / "source"),
               "python_executable": "/trusted/python", "candidate_home": str(home),
               "config_path": str(config), **identity,
               "game_mod_sha256": "d" * 64, "build_provenance_sha256": "e" * 64,
               "collection_tool_directory": str(tmp_path / "tool"),
               "collection_tool_release_id": "f" * 64}
    monkeypatch.setattr(install, "_source_candidate_binding", lambda *args: binding)
    return home, config, binding


def test_prepare_source_candidate_keeps_release_claim_and_daily_launcher_separate(
    tmp_path, monkeypatch,
):
    home, config, binding = candidate(tmp_path, monkeypatch)
    result = install.prepare_source_candidate_access(
        tmp_path / "source", config, Path("/trusted/python"), tmp_path / "tool", home=home)
    root = install._launcher_directory(home=home)
    bootstrap = NativeBootstrap.read(root, config)
    assert result["status"] == "source_candidate_prepared"
    assert result["release_kit_verified"] is False
    assert bootstrap.game_mod_sha256 == "d" * 64
    assert json.loads((root / "launcher.json").read_bytes()) == binding
    assert "launch-source-candidate" in (root / "open").read_text()
    assert "-I" in (root / "open").read_text()
    assert str(home) in (root / "open").read_text()
    with pytest.raises(BoundaryError, match="source_candidate_target_not_empty"):
        install.prepare_source_candidate_access(
            tmp_path / "source", config, Path("/trusted/python"), tmp_path / "tool", home=home)
    assert NativeBootstrap.read(root, config) == bootstrap
    from spireagent.workbench import developer_server
    monkeypatch.setattr(developer_server, "running", lambda _: None)
    with pytest.raises(BoundaryError, match="source_candidate_changed"):
        install.rollback_source_candidate_access(home, "0" * 64)
    assert (root / "native-access.json").exists()
    assert install.rollback_source_candidate_access(home, result["launcher_sha256"])[
        "config_and_data_retained"] is True
    assert config.exists() and not (root / "launcher.json").exists()


def test_candidate_cannot_overwrite_current_home_launcher(tmp_path, monkeypatch):
    _, config, _ = candidate(tmp_path, monkeypatch)
    with pytest.raises(BoundaryError, match="source_candidate_isolated_home_required"):
        install.prepare_source_candidate_access(
            tmp_path / "source", config, Path("/trusted/python"), tmp_path / "tool",
            home=Path.home())


def test_candidate_binding_rejects_dirty_source_before_native_publication(tmp_path, monkeypatch):
    home, config, _ = candidate(tmp_path, monkeypatch)
    # Exercise actual candidate validation; no game or interpreter is launched.
    monkeypatch.undo()
    home.mkdir()
    source, tool = tmp_path / "source", tmp_path / "tool"
    source.mkdir()
    tool.mkdir()
    python = tmp_path / "python"
    python.touch()
    monkeypatch.setattr(install, "_workbench_identity_for_source", lambda _: {
        "working_tree_clean": False})
    with pytest.raises(BoundaryError, match="source_candidate_dirty"):
        install._source_candidate_binding(source, config, python, tool, home)
    assert not (home / "Library").exists()


def test_candidate_binding_matches_source_mod_tool_and_installed_environment(tmp_path, monkeypatch):
    from types import SimpleNamespace

    home, config, _ = candidate(tmp_path, monkeypatch)
    monkeypatch.undo()
    home.mkdir()
    source, tool = tmp_path / "source", tmp_path / "tool"
    source.mkdir()
    tool.mkdir()
    python = tmp_path / "python"
    python.touch()
    identity = {"working_tree_clean": True, "source_revision": "a" * 40,
                "workbench_sha256": "b" * 64, "uv_lock_sha256": "c" * 64,
                "python": "3.11.15"}
    monkeypatch.setattr(install, "_workbench_identity_for_source", lambda _: identity)
    provenance = {"schema": "sts2.platform/game-mod-build-provenance-1",
                  "artifact": {"sha256": install.sha(b"candidate dll")},
                  "source": {"components": {"annotator": {"component_tree_revision": "t"}}}}
    output = source / "apps/game-mod/bin/Release/net9.0"
    output.mkdir(parents=True)
    (output / "STS2_PLATFORM.dll").write_bytes(b"candidate dll")
    atomic_json(output / "build-provenance.json", provenance)
    (tool / "game-mod").mkdir()
    atomic_json(tool / "game-mod/build-provenance.json", provenance)
    tool_manifest = {"release_id": "d" * 64, "identity": {"component_tree_revision": "t"}}
    atomic_json(tool / "collection-tool.json", tool_manifest)
    monkeypatch.setattr(install, "CollectionTool",
                        lambda *_: SimpleNamespace(manifest=tool_manifest))
    monkeypatch.setattr(install, "run", lambda command, cwd: "")
    monkeypatch.setattr(install, "_run_launcher_probe", lambda *_, **__: json.dumps({
        "root": str(source / "python"), "identity": identity, "evidence": {"status": "PASS"}}))
    binding = install._source_candidate_binding(source, config, python, tool, home)
    assert binding["game_mod_sha256"] == install.sha(b"candidate dll")
    assert binding["collection_tool_release_id"] == "d" * 64
    # Source candidate input must bind the actual candidate DLL, not just claim a hash.
    (output / "STS2_PLATFORM.dll").write_bytes(b"other dll")
    with pytest.raises(BoundaryError, match="source_candidate_mod_mismatch"):
        install._source_candidate_binding(source, config, python, tool, home)
