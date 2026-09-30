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
    from spireagent.workbench import developer

    monkeypatch.setattr(developer, "tool_identity", lambda: identity)
    monkeypatch.setattr(developer.ProjectConfig, "load", lambda *_a, **_k: object())
    prepared = {"workbench_launcher_schema": install.LAUNCHER_SCHEMA,
                "source_revision": "c" * 40, "uv_lock_sha256": "e" * 64}

    install._install_open_launcher(release, config, prepared, platform="darwin")
    binding_file = home / "Library/Application Support/spireagent/workbench/launcher.json"
    first = binding_file.read_bytes()
    original_config = config.read_bytes()
    install._install_open_launcher(release, config, prepared, platform="darwin")
    assert binding_file.read_bytes() == first
    assert config.read_bytes() == original_config

    other = tmp_path / "other/project.json"
    other.parent.mkdir()
    other.write_text("{}")
    with pytest.raises(BoundaryError, match="launcher_config_binding_mismatch"):
        install._install_open_launcher(release, other, prepared, platform="darwin")


def test_isolated_installer_entry_imports_its_source_packages():
    tool = Path(__file__).resolve().parents[1] / "tools/install_developer_kit.py"
    result = subprocess.run(
        [sys.executable, "-I", str(tool), "--help"],
        cwd=tool.parents[1], capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "install-launcher" in result.stdout and "launch" in result.stdout


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
