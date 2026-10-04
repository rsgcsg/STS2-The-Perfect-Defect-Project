from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench import developer_server
from tools import install_developer_kit as install


def test_fixed_launcher_quotes_paths_with_spaces_and_uses_isolated_python(tmp_path):
    release = tmp_path / "Approved Kits" / ("a" * 64)
    python_root = release / "source/python"
    binary = python_root / ".venv/bin/python"
    tool = python_root / "tools/install_developer_kit.py"
    binary.parent.mkdir(parents=True)
    tool.parent.mkdir(parents=True)
    binary.touch()
    tool.touch()

    script = install._launcher_script(release)

    assert install._launcher_files(tmp_path)[1].name == "open"
    assert script.startswith("#!/bin/sh\n")
    assert f"cd '{python_root}'" in script
    assert f"'{binary}' -I '{tool}' launch" in script
    assert "PYTHONHOME PYTHONPATH" in script
    assert "--config" not in script and "sh -c" not in script


def test_fixed_launcher_rejects_unsupported_platforms(tmp_path):
    with pytest.raises(BoundaryError, match="launcher_platform_unsupported"):
        install._launcher_directory(platform="win32", home=tmp_path)
    with pytest.raises(BoundaryError, match="launcher_platform_unsupported"):
        install._install_open_launcher(
            tmp_path / ("a" * 64), tmp_path / "profile.json",
            {"workbench_launcher_schema": install.LAUNCHER_SCHEMA}, platform="win32",
        )


def test_launcher_binding_is_idempotent_for_same_profile_and_rejects_another(
    tmp_path, monkeypatch
):
    release = tmp_path / ("b" * 64)
    source_python = release / "source/python"
    (source_python / ".venv/bin").mkdir(parents=True)
    (source_python / ".venv/bin/python").touch()
    (source_python / "tools").mkdir()
    (source_python / "tools/install_developer_kit.py").touch()
    config = tmp_path / "private account" / "project.json"
    config.parent.mkdir()
    config.write_text("{}")
    home = tmp_path / "Home Folder"
    app_support = home / "Library/Application Support/spireagent/workbench"
    monkeypatch.setattr(install, "_launcher_directory", lambda **_: app_support)
    identity = {
        "working_tree_clean": True,
        "source_revision": "c" * 40,
        "workbench_sha256": "d" * 64,
        "uv_lock_sha256": "e" * 64,
    }
    from types import SimpleNamespace

    from spireagent.workbench import developer

    monkeypatch.setattr(developer, "tool_identity", lambda: identity)
    monkeypatch.setattr(developer.ProjectConfig, "load",
                        lambda *_a, **_k: SimpleNamespace(combination={"approved": True}))
    monkeypatch.setattr(install, "_workbench_identity_for_source",
                        lambda _: {**identity, "working_tree_clean": True})
    prepared = {"workbench_launcher_schema": install.LAUNCHER_SCHEMA,
                "source_revision": "c" * 40, "uv_lock_sha256": "e" * 64,
                "developer_combination": {"approved": True}}

    install._install_open_launcher(release, config, prepared, platform="darwin")
    binding_file = home / "Library/Application Support/spireagent/workbench/launcher.json"
    first = binding_file.read_bytes()
    original_config = config.read_bytes()
    install._install_open_launcher(release, config, prepared, platform="darwin")
    assert binding_file.read_bytes() == first
    assert config.read_bytes() == original_config

    binding_file.write_text("[]", encoding="utf-8")
    with pytest.raises(BoundaryError, match="launcher_binding_invalid"):
        install._install_open_launcher(release, config, prepared, platform="darwin")
    assert binding_file.read_bytes() == b"[]"
    binding_file.write_bytes(first)

    other = tmp_path / "other/project.json"
    other.parent.mkdir()
    other.write_text("{}")
    with pytest.raises(BoundaryError, match="launcher_config_binding_mismatch"):
        install._install_open_launcher(release, other, prepared, platform="darwin")



def test_explicit_launcher_rebind_checks_exact_prior_bytes_and_preserves_profiles(
    tmp_path, monkeypatch
):
    release = tmp_path / ("b" * 64)
    python_root = release / "source/python"
    (python_root / ".venv/bin").mkdir(parents=True)
    (python_root / ".venv/bin/python").touch()
    (python_root / "tools").mkdir()
    (python_root / "tools/install_developer_kit.py").touch()
    profiles = [tmp_path / name / "project.json" for name in ("research", "game")]
    for profile in profiles:
        profile.parent.mkdir()
        profile.write_text('{"preserve":"original"}')
    root = tmp_path / "launcher"
    monkeypatch.setattr(install, "_launcher_directory", lambda **_: root)
    identity = {"working_tree_clean": True, "source_revision": "c" * 40,
                "uv_lock_sha256": "e" * 64, "workbench_sha256": "d" * 64}
    from types import SimpleNamespace

    from spireagent.workbench import developer
    monkeypatch.setattr(developer, "tool_identity", lambda: identity)
    monkeypatch.setattr(developer.ProjectConfig, "load",
                        lambda *_a, **_k: SimpleNamespace(combination={"approved": True}))
    monkeypatch.setattr(install, "_workbench_identity_for_source",
                        lambda _: {**identity, "working_tree_clean": True})
    prepared = {"workbench_launcher_schema": install.LAUNCHER_SCHEMA,
                "source_revision": "c" * 40, "uv_lock_sha256": "e" * 64,
                "developer_combination": {"approved": True}}
    install._install_open_launcher(release, profiles[0], prepared, platform="darwin")
    binding_file, script_file = install._launcher_files(root)
    prior, script = binding_file.read_bytes(), script_file.read_bytes()
    with pytest.raises(BoundaryError, match="launcher_config_binding_mismatch"):
        install._install_open_launcher(release, profiles[1], prepared, platform="darwin")
    with pytest.raises(BoundaryError, match="launcher_pair_changed"):
        install._install_open_launcher(release, profiles[1], prepared, platform="darwin",
                                       expected_binding_sha256="a" * 64,
                                       expected_open_sha256="a" * 64)
    assert binding_file.read_bytes() == prior and script_file.read_bytes() == script
    replacement = tmp_path / ("f" * 64)
    target_python = replacement / "source/python"
    (target_python / ".venv/bin").mkdir(parents=True)
    (target_python / ".venv/bin/python").touch()
    (target_python / "tools").mkdir()
    (target_python / "tools/install_developer_kit.py").touch()
    original_writer = install._write_executable
    def fail_after_replace(path, contents):
        original_writer(path, contents)
        raise OSError("injected fsync failure after replace")
    with monkeypatch.context() as failing:
        failing.setattr(install, "_write_executable", fail_after_replace)
        with pytest.raises(OSError, match="injected fsync failure"):
            install._install_open_launcher(replacement, profiles[1], prepared, platform="darwin",
                                           expected_binding_sha256=install.sha(prior),
                                           expected_open_sha256=install.sha(script))
    assert binding_file.read_bytes() == prior and script_file.read_bytes() == script
    install._install_open_launcher(release, profiles[1], prepared, platform="darwin",
                                   expected_binding_sha256=install.sha(prior),
                                   expected_open_sha256=install.sha(script))
    assert json.loads(binding_file.read_bytes())["config_path"] == str(profiles[1])
    for profile in profiles:
        assert profile.read_text() == '{"preserve":"original"}'
    with pytest.raises(BoundaryError, match="launcher_pair_changed"):
        install._install_open_launcher(release, profiles[0], prepared, platform="darwin",
                                       expected_binding_sha256=install.sha(prior),
                                       expected_open_sha256=install.sha(script))
    # An explicit rollback uses the newly observed binding, through the same owner.
    install._install_open_launcher(release, profiles[0], prepared, platform="darwin",
                                   expected_binding_sha256=install.sha(binding_file.read_bytes()),
                                   expected_open_sha256=install.sha(script_file.read_bytes()))
    assert binding_file.read_bytes() == prior
    binding_file.unlink()
    with pytest.raises(BoundaryError, match="launcher_pair_incomplete"):
        install._install_open_launcher(release, profiles[1], prepared, platform="darwin",
                                       expected_binding_sha256=install.sha(prior),
                                       expected_open_sha256=install.sha(script))
    assert not binding_file.exists() and script_file.read_bytes() == script


def test_launcher_rebind_requires_both_compare_and_swap_digests(tmp_path, monkeypatch):
    release = tmp_path / ("b" * 64)
    python_root = release / "source/python"
    (python_root / ".venv/bin").mkdir(parents=True)
    (python_root / ".venv/bin/python").touch()
    (python_root / "tools").mkdir()
    (python_root / "tools/install_developer_kit.py").touch()
    config = tmp_path / "profile.json"
    config.write_text("{}")
    root = tmp_path / "launcher"
    monkeypatch.setattr(install, "_launcher_directory", lambda **_: root)
    from spireagent.workbench import developer
    monkeypatch.setattr(developer, "tool_identity", lambda: {
        "working_tree_clean": True, "source_revision": "c" * 40,
        "uv_lock_sha256": "e" * 64, "workbench_sha256": "d" * 64,
    })
    monkeypatch.setattr(developer.ProjectConfig, "load", lambda *_a, **_k: object())
    prepared = {"workbench_launcher_schema": install.LAUNCHER_SCHEMA,
                "source_revision": "c" * 40, "uv_lock_sha256": "e" * 64}
    with pytest.raises(BoundaryError, match="launcher_replacement_arguments_invalid"):
        install._install_open_launcher(
            release, config, prepared, platform="darwin", expected_binding_sha256="a" * 64,
        )


def test_backup_launcher_snapshots_both_exact_files_as_nonlaunchable_history(
        tmp_path, monkeypatch):
    release = tmp_path / ("c" * 64)
    root = tmp_path / "global-launcher"
    root.mkdir()
    root.chmod(0o700)
    monkeypatch.setattr(install, "_launcher_directory", lambda **_: root)
    source_identity = {"source_revision": "a" * 40, "uv_lock_sha256": "b" * 64,
                       "workbench_sha256": "c" * 64}
    monkeypatch.setattr(install, "status", lambda _: {
        "source_revision": "a" * 40, "uv_lock_sha256": "b" * 64,
    })
    monkeypatch.setattr(install, "_workbench_identity_for_source",
                        lambda _: {**source_identity, "working_tree_clean": True})
    config = tmp_path / "private-profile.json"
    config.write_text("{}")
    binding = {
        "schema": install.LAUNCHER_SCHEMA,
        "release_directory": str(release.resolve()), "kit_sha256": release.name,
        "source_revision": source_identity["source_revision"],
        "uv_lock_sha256": source_identity["uv_lock_sha256"],
        "workbench_sha256": source_identity["workbench_sha256"],
        "config_path": str(config),
    }
    binding_raw = json.dumps(binding, separators=(",", ":")).encode()
    open_raw = b"#!/bin/sh\nlegacy bytes\n"
    (root / "launcher.json").write_bytes(binding_raw)
    (root / "launcher.json").chmod(0o600)
    (root / "open").write_bytes(open_raw)
    (root / "open").chmod(0o700)
    output = tmp_path / "history-snapshot"
    result = install.backup_launcher(
        release, output, install.sha(binding_raw), install.sha(open_raw),
    )
    manifest_raw = (output / "snapshot.json").read_bytes()
    manifest = json.loads(manifest_raw)
    assert result["launchable"] is False
    assert result["snapshot_manifest_sha256"] == install.sha(manifest_raw)
    assert manifest["restore_eligibility"] == "not_granted"
    assert (output / "launcher.json").read_bytes() == binding_raw
    assert (output / "open").read_bytes() == open_raw
    # Windows stat/chmod expose read-only flags rather than POSIX permission bits.
    # Keep byte/identity/restore-eligibility checks above on every platform.
    if install.os.name != "nt":
        assert (output / "launcher.json").stat().st_mode & 0o777 == 0o600
        assert (output / "open").stat().st_mode & 0o777 == 0o700
    with pytest.raises(BoundaryError, match="launcher_snapshot_path_invalid"):
        install.backup_launcher(
            release, output, install.sha(binding_raw), install.sha(open_raw),
        )


def test_launcher_backup_rejects_partial_and_changed_pairs(tmp_path, monkeypatch):
    root = tmp_path / "launcher"
    root.mkdir()
    root.chmod(0o700)
    monkeypatch.setattr(install, "_launcher_directory", lambda **_: root)
    (root / "launcher.json").write_bytes(b"{}")
    with pytest.raises(BoundaryError, match="launcher_pair_incomplete"):
        install._launcher_pair(root)


def test_prepare_target_restore_revalidates_release_config_and_both_current_hashes(
        tmp_path, monkeypatch):
    from types import SimpleNamespace

    from spireagent.workbench import developer

    release = tmp_path / ("e" * 64)
    python_root = release / "source/python"
    python = python_root / ".venv/bin/python"
    tool = python_root / "tools/install_developer_kit.py"
    python.parent.mkdir(parents=True)
    tool.parent.mkdir(parents=True)
    python.touch()
    tool.touch()
    config = tmp_path / "private-config.json"
    config.write_text("{}")
    root = tmp_path / "global-launcher"
    root.mkdir()
    root.chmod(0o700)
    monkeypatch.setattr(install, "_launcher_directory", lambda **_: root)
    combination = {"release": "approved"}
    prepared = {
        "workbench_launcher_schema": install.LAUNCHER_SCHEMA,
        "source_revision": "a" * 40, "uv_lock_sha256": "b" * 64,
        "developer_combination": combination,
    }
    monkeypatch.setattr(install, "status", lambda _: dict(prepared))
    monkeypatch.setattr(install, "_workbench_identity_for_source", lambda _: {
        "source_revision": "a" * 40, "uv_lock_sha256": "b" * 64,
        "workbench_sha256": "c" * 64, "working_tree_clean": True,
    })
    monkeypatch.setattr(developer.ProjectConfig, "load", lambda *_a, **_k: SimpleNamespace(
        combination=combination))
    probes = []
    from spireagent.workbench.developer_server import instance_lock

    def probe(directory, binding):
        with (pytest.raises(BoundaryError, match="already_running"),
              instance_lock(directory / "initialize.lock")):
            pytest.fail("probe did not hold the selected release initialization lock")
        probes.append((directory, binding["config_path"]))

    monkeypatch.setattr(install, "_probe_launcher_target", probe)

    target_snapshot = tmp_path / "target-snapshot"
    target = install.prepare_launcher_target(release, config, target_snapshot)
    # Exercise the actual file publication owner after preparation. Equal parsed
    # JSON is insufficient: reviewed snapshot hashes must match installed bytes.
    install._install_open_launcher(release, config, prepared, platform="darwin")
    installed_binding, installed_open = install._launcher_files(root)
    assert installed_binding.read_bytes() == (target_snapshot / "launcher.json").read_bytes()
    assert installed_open.read_bytes() == (target_snapshot / "open").read_bytes()
    target_manifest = json.loads((target_snapshot / "snapshot.json").read_bytes())
    for name, path in (("launcher.json", installed_binding), ("open", installed_open)):
        assert install.sha(path.read_bytes()) == target_manifest["files"][name]["sha256"]
        if install.os.name != "nt":
            assert path.stat().st_mode & 0o777 == target_manifest["files"][name]["mode"]
    with (instance_lock(release / "initialize.lock"),
          pytest.raises(BoundaryError, match="already_running")):
        install.prepare_launcher_target(release, config, tmp_path / "concurrent-target")
    assert not (tmp_path / "concurrent-target").exists()
    current_binding, current_open = install._launcher_files(root)
    current_binding.write_bytes(b"reviewed current binding")
    current_open.write_bytes(b"reviewed current open")
    current_binding.chmod(0o600)
    current_open.chmod(0o700)
    binding_sha = install.sha(current_binding.read_bytes())
    open_sha = install.sha(current_open.read_bytes())

    restored = install.restore_launcher(
        target_snapshot, target["snapshot_manifest_sha256"], binding_sha, open_sha,
    )
    assert restored["status"] == "launcher_restored"
    assert json.loads(current_binding.read_bytes())["release_directory"] == str(release)
    assert current_open.read_bytes() == install._launcher_script(release).encode()
    assert probes == [(release, str(config)), (release, str(config))]
    with pytest.raises(BoundaryError, match="launcher_pair_changed"):
        install.restore_launcher(
            target_snapshot, target["snapshot_manifest_sha256"], binding_sha, open_sha,
        )


def test_launcher_target_probe_rejects_empty_interpreter(tmp_path):
    release = tmp_path / ("a" * 64)
    interpreter = release / "source/python/.venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    interpreter.chmod(0o700)
    with pytest.raises(BoundaryError, match="launcher_environment_unverified"):
        install._probe_launcher_target(release, {})


def test_launcher_probe_runner_bounds_output_and_timeout(tmp_path):
    import os

    with pytest.raises(ValueError, match="output bound"):
        install._run_launcher_probe(
            [sys.executable, "-I", "-c", "print('x' * 100000)"], cwd=tmp_path,
            env=dict(os.environ),
        )
    with pytest.raises(subprocess.TimeoutExpired):
        install._run_launcher_probe(
            [sys.executable, "-I", "-c", "import time; time.sleep(10)"], cwd=tmp_path,
            env=dict(os.environ), timeout=0.1,
        )


def test_launcher_target_probe_rejects_foreign_evidence_and_cleans_environment(
        tmp_path, monkeypatch):
    release = tmp_path / ("a" * 64)
    python_root = release / "source/python"
    python_root.mkdir(parents=True)
    prefix = python_root / ".venv"
    binding = {"source_revision": "a" * 40, "uv_lock_sha256": "b" * 64,
               "workbench_sha256": "c" * 64}
    report = {
        "identity": {**binding, "working_tree_clean": True, "python": "3.11.11"},
        "prefix": str(prefix), "python_version": [3, 11, 11],
        "source_revision": binding["source_revision"],
        "uv_lock_sha256": binding["uv_lock_sha256"],
        "evidence": {"status": "PASS"},
        "installed_root": str(prefix / "lib/python3.11/site-packages"),
        "import_file": str(tmp_path / "foreign/sts2_platform_evidence/__init__.py"),
    }
    captured = []

    def run(args, **kwargs):
        captured.append((args, kwargs))
        return json.dumps(report)

    monkeypatch.setattr(install, "_run_launcher_probe", run)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "foreign"))
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(tmp_path / "other-venv"))
    with pytest.raises(BoundaryError, match="launcher_environment_unverified"):
        install._probe_launcher_target(release, binding)
    args, options = captured[0]
    assert args[:3] == [str(prefix / "bin/python"), "-I", "-c"]
    assert "PYTHONPATH" not in options["env"]
    assert "UV_PROJECT_ENVIRONMENT" not in options["env"]


@pytest.mark.parametrize("malformed", ["float_mode", "hardlink", "symlink"])
def test_launcher_snapshot_reader_rejects_typed_mode_and_unsafe_members(tmp_path, malformed):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir(mode=0o700)
    binding, script = b"binding", b"script"
    (snapshot / "launcher.json").write_bytes(binding)
    (snapshot / "launcher.json").chmod(0o600)
    (snapshot / "open").write_bytes(script)
    (snapshot / "open").chmod(0o700)
    manifest = {
        "schema": install.LAUNCHER_SNAPSHOT_SCHEMA, "launchable": True,
        "restore_eligibility": "validated_prepared_target",
        "release_directory": str(tmp_path / ("a" * 64)), "kit_sha256": "a" * 64,
        "source_revision": "b" * 40, "workbench_sha256": "c" * 64,
        "uv_lock_sha256": "d" * 64, "config_path": str(tmp_path / "project.json"),
        "files": {"launcher.json": {"sha256": install.sha(binding), "mode": 0o600},
                  "open": {"sha256": install.sha(script), "mode": 0o700}},
    }
    if malformed == "float_mode":
        manifest["files"]["launcher.json"]["mode"] = float(0o600)
    elif malformed == "hardlink":
        (tmp_path / "outside").hardlink_to(snapshot / "open")
    else:
        (snapshot / "open").rename(tmp_path / "outside")
        try:
            (snapshot / "open").symlink_to(tmp_path / "outside")
        except OSError:
            pytest.skip("symlink creation is unavailable")
    raw = json.dumps(manifest).encode()
    (snapshot / "snapshot.json").write_bytes(raw)
    (snapshot / "snapshot.json").chmod(0o600)
    with pytest.raises(BoundaryError):
        install._read_launcher_snapshot(snapshot, install.sha(raw))


def test_prepare_target_rejects_prior_config_combination_drift(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from spireagent.workbench import developer

    release = tmp_path / ("f" * 64)
    python_root = release / "source/python"
    (python_root / ".venv/bin").mkdir(parents=True)
    (python_root / ".venv/bin/python").touch()
    (python_root / "tools").mkdir()
    (python_root / "tools/install_developer_kit.py").touch()
    config = tmp_path / "private-config.json"
    config.write_text("{}")
    prepared = {
        "workbench_launcher_schema": install.LAUNCHER_SCHEMA,
        "source_revision": "a" * 40, "uv_lock_sha256": "b" * 64,
        "developer_combination": {"evidence_source_revision": "new"},
    }
    monkeypatch.setattr(install, "status", lambda _: dict(prepared))
    monkeypatch.setattr(developer.ProjectConfig, "load", lambda *_a, **_k: SimpleNamespace(
        combination={"evidence_source_revision": "legacy"}))
    snapshot = tmp_path / "must-not-exist"
    with pytest.raises(BoundaryError, match="launcher_config_combination_mismatch"):
        install.prepare_launcher_target(release, config, snapshot)
    assert not snapshot.exists()


def test_pair_rollback_attempts_both_files_and_reports_recovery_required(tmp_path, monkeypatch):
    root = tmp_path / "launcher"
    root.mkdir()
    root.chmod(0o700)
    binding_path, open_path = install._launcher_files(root)
    binding_path.write_bytes(b"old binding")
    open_path.write_bytes(b"old open")
    original_writer = install._write_launcher_file
    attempts = []

    def fail_target_open_and_binding_rollback(path, contents, mode):
        attempts.append((path.name, contents))
        if path.name == "open" and contents == b"target open":
            original_writer(path, contents, mode)
            raise OSError("target open fsync failed")
        if path.name == "launcher.json" and contents == b"old binding":
            raise OSError("binding rollback failed")
        return original_writer(path, contents, mode)

    monkeypatch.setattr(install, "_write_launcher_file", fail_target_open_and_binding_rollback)
    with pytest.raises(BoundaryError, match="launcher_recovery_required"):
        install._write_launcher_pair(root, b"target binding", b"target open", 0o600, 0o700)
    assert ("launcher.json", b"old binding") in attempts
    assert ("open", b"old open") in attempts
    assert binding_path.read_bytes() == b"target binding"
    assert open_path.read_bytes() == b"old open"


def test_installer_cli_rejects_one_sided_compare_and_swap_and_wrong_defer_command(
        monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "install_developer_kit.py", "install-launcher", "--config", "/tmp/profile.json",
        "--expected-launcher-binding-sha256", "a" * 64,
    ])
    assert install.main() == 1
    assert json.loads(capsys.readouterr().out)["code"] == "launcher_replacement_arguments_invalid"
    monkeypatch.setattr(sys, "argv", [
        "install_developer_kit.py", "install-launcher", "--config", "/tmp/profile.json",
        "--expected-open-sha256", "a" * 64,
    ])
    assert install.main() == 1
    assert json.loads(capsys.readouterr().out)["code"] == "launcher_replacement_arguments_invalid"
    monkeypatch.setattr(sys, "argv", ["install_developer_kit.py", "launch", "--defer-launcher"])
    assert install.main() == 1
    assert json.loads(capsys.readouterr().out)["code"] == "defer_launcher_arguments_invalid"

def test_isolated_installer_entry_imports_its_source_packages():
    tool = Path(__file__).resolve().parents[1] / "tools/install_developer_kit.py"
    result = subprocess.run(
        [sys.executable, "-I", str(tool), "--help"],
        cwd=tool.parents[1], capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert all(name in result.stdout for name in (
        "install-launcher", "backup-launcher", "prepare-launcher-target",
        "restore-launcher", "launch", "--expected-open-sha256",
        "--defer-launcher",
    ))


def test_isolated_installer_entry_keeps_locked_evidence_import_origin():
    """The fixed launcher executes this entry before Workbench runs its doctor."""
    tool = Path(__file__).resolve().parents[1] / "tools/install_developer_kit.py"
    script = "\n".join((
        "import importlib.metadata, json, runpy, sys",
        "runpy.run_path(sys.argv[1], run_name='installer_import')",
        "import sts2_platform_evidence, sts2_platform_evidence.delivery_cli",
        "from spireagent.workbench.developer import combination, evidence_identity",
        "distribution = importlib.metadata.distribution('rsgcsg-sts2-platform-evidence')",
        "pin = combination()['evidence_source_revision']",
        "print(json.dumps({'identity': evidence_identity(pin),"
        "                  'import_file': sts2_platform_evidence.__file__,"
        "                  'entry_file': sts2_platform_evidence.delivery_cli.__file__,"
        "                  'installed_root': str(distribution.locate_file(''))}))",
    ))
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(tool)],
        cwd=tool.parents[1], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["identity"]["status"] == "PASS"
    assert observed["identity"]["delivery_entrypoint_verified"] is True
    assert Path(observed["import_file"]).is_relative_to(Path(observed["installed_root"]))
    assert Path(observed["entry_file"]).is_relative_to(Path(observed["installed_root"]))


def test_open_lock_serializes_two_open_requests(tmp_path):
    lock = tmp_path / "state/open.lock"
    events: list[str] = []
    first_entered = threading.Event()
    release_first = threading.Event()

    def first():
        with developer_server.open_lock(lock):
            events.append("first-enter")
            first_entered.set()
            assert release_first.wait(2)
            events.append("first-exit")

    def second():
        assert first_entered.wait(2)
        with developer_server.open_lock(lock):
            events.append("second-enter")

    left = threading.Thread(target=first)
    right = threading.Thread(target=second)
    left.start()
    right.start()
    assert first_entered.wait(2)
    time.sleep(0.1)
    assert events == ["first-enter"]
    release_first.set()
    left.join(2)
    right.join(2)
    assert not left.is_alive() and not right.is_alive()
    assert events == ["first-enter", "first-exit", "second-enter"]


def test_open_project_reuses_only_the_exact_expected_release(tmp_path, monkeypatch):
    from test_project_console import config as make_config

    config = make_config(tmp_path)
    profile = tmp_path / "private/project.json"
    profile.parent.mkdir()
    profile.write_text(json.dumps(config.to_dict()))
    (config.state_dir / "logs").mkdir(parents=True)
    expected = {
        "python": "/private/release/python/.venv/bin/python",
        "source_revision": "a" * 40,
        "uv_lock_sha256": "b" * 64,
        "workbench_sha256": "c" * 64,
        "working_tree_clean": True,
    }
    runtime = {"port": 12345, "instance_id": "instance", "delivery": "local",
               "identity": expected}
    monkeypatch.setattr(developer_server, "running", lambda _: runtime)
    monkeypatch.setattr(developer_server, "doctor",
                        lambda _: pytest.fail("ready runtime must be reused"))
    monkeypatch.setattr(developer_server.subprocess, "Popen",
                        lambda *_a, **_k: pytest.fail("must not spawn"))

    opened = developer_server.open_project(profile, browser=False, expected_identity=expected)
    assert opened["status"] == "running" and opened["instance_id"] == "instance"

    runtime["identity"] = {**expected, "source_revision": "d" * 40}
    with pytest.raises(BoundaryError, match="running_identity_mismatch"):
        developer_server.open_project(profile, browser=False, expected_identity=expected)


def test_open_project_spawns_isolated_child_for_fixed_release(tmp_path, monkeypatch):
    from test_project_console import config as make_config

    config = make_config(tmp_path)
    profile = tmp_path / "private/project.json"
    profile.parent.mkdir()
    profile.write_text(json.dumps(config.to_dict()))
    (config.state_dir / "logs").mkdir(parents=True)
    expected = {
        "python": "/private/release/python/.venv/bin/python",
        "source_revision": "a" * 40,
        "uv_lock_sha256": "b" * 64,
        "workbench_sha256": "c" * 64,
        "working_tree_clean": True,
    }
    runtime = {"port": 12345, "instance_id": "started", "delivery": "local",
               "identity": expected}
    observed_commands = []
    def current_runtime(_):
        return runtime if observed_commands else None

    monkeypatch.setattr(developer_server, "running", current_runtime)
    monkeypatch.setattr(developer_server, "doctor", lambda _: {"status": "PASS"})
    monkeypatch.setenv("PYTHONPATH", "/untrusted/source")
    monkeypatch.setenv("PYTHONHOME", "/untrusted/python")

    spawned_children = []

    class Child:
        pid = 42

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, *, timeout):
            return 0

        def kill(self):
            self.killed = True

    def spawn(command, **kwargs):
        observed_commands.append(command)
        assert command[:2] == [developer_server.sys.executable, "-I"]
        assert "PYTHONPATH" not in kwargs["env"]
        assert "PYTHONHOME" not in kwargs["env"]
        child = Child()
        spawned_children.append(child)
        return child

    monkeypatch.setattr(developer_server.subprocess, "Popen", spawn)
    opened = developer_server.open_project(profile, browser=False, expected_identity=expected)
    assert opened["instance_id"] == "started"
    assert len(observed_commands) == 1
    observed_commands.clear()
    runtime.update({"pid": 42, "identity": {**expected, "source_revision": "f" * 40}})
    with pytest.raises(BoundaryError, match="running_identity_mismatch"):
        developer_server.open_project(profile, browser=False, expected_identity=expected)
    assert spawned_children[-1].terminated is True


def test_launch_workbench_rejects_binding_tampering_and_opens_only_bound_profile(
    tmp_path, monkeypatch
):
    release = tmp_path / ("f" * 64)
    tool = release / "source/python/tools/install_developer_kit.py"
    tool.parent.mkdir(parents=True)
    tool.touch()
    profile = tmp_path / "private/project.json"
    profile.parent.mkdir()
    profile.write_text("{}")
    root = tmp_path / "Library/Application Support/spireagent/workbench"
    root.mkdir(parents=True)
    binding_file = root / "launcher.json"
    identity = {
        "python": str(release / "source/python/.venv/bin/python"),
        "source_revision": "a" * 40,
        "uv_lock_sha256": "b" * 64,
        "workbench_sha256": "c" * 64,
        "working_tree_clean": True,
    }
    binding = {
        "schema": install.LAUNCHER_SCHEMA,
        "release_directory": str(release),
        "kit_sha256": release.name,
        "source_revision": identity["source_revision"],
        "workbench_sha256": identity["workbench_sha256"],
        "uv_lock_sha256": identity["uv_lock_sha256"],
        "config_path": str(profile),
    }
    binding_file.write_text(json.dumps(binding))
    opened = []
    monkeypatch.setattr(install, "__file__", str(tool))
    monkeypatch.setattr(install, "_launcher_directory", lambda **_: root)
    monkeypatch.setattr(install, "status", lambda _: {
        "workbench_launcher_schema": install.LAUNCHER_SCHEMA,
        "source_revision": identity["source_revision"],
        "uv_lock_sha256": identity["uv_lock_sha256"],
    })
    from spireagent.workbench import developer

    monkeypatch.setattr(developer, "tool_identity", lambda: identity)
    monkeypatch.setattr(developer.ProjectConfig, "load", lambda *_a, **_k: object())
    monkeypatch.setattr(
        developer_server, "open_project",
        lambda path, **kwargs: opened.append((path, kwargs)) or {
            "instance_id": "known", "status": "running",
        },
    )
    monkeypatch.setattr(install.sys, "platform", "darwin")

    result = install.launch_workbench()
    assert result == {"status": "running", "instance_id": "known",
                      "gameplay_started": False}
    assert opened == [(profile, {"browser": False, "expected_identity": identity})]

    opened.clear()
    binding["unexpected"] = "tampered"
    binding_file.write_text(json.dumps(binding))
    with pytest.raises(BoundaryError, match="launcher_binding_invalid"):
        install.launch_workbench()
    assert opened == []


@pytest.mark.parametrize("getter", [None, lambda: True, lambda: -1, lambda: "0"])
def test_launcher_unix_owner_check_requires_valid_os_capability(monkeypatch, getter):
    monkeypatch.setattr(install.os, "geteuid", getter, raising=False)
    with pytest.raises(BoundaryError, match="launcher_ownership_unavailable"):
        install._effective_user_id()


def test_launcher_unix_owner_check_retains_effective_uid(monkeypatch):
    monkeypatch.setattr(install.os, "geteuid", lambda: 42, raising=False)
    assert install._effective_user_id() == 42
