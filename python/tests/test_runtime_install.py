from __future__ import annotations

import hashlib
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.package_identity import PackageIdentityError, directory_sha256
from spireagent.workbench import runtime_install


def package(root, name, *, runtime=False):
    root.mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({"name": name, "version": "0.1.0"}))
    if runtime:
        (root / "dist").mkdir()
        (root / "dist/cli.js").write_text("export {};")
        (root / "bin").mkdir()
        (root / "bin/policy-runtime.mjs").write_text("export {};")
    return {
        "package": name,
        "version": "0.1.0",
        "source_revision": "a" * 40,
        "component_tree_revision": "b" * 40,
        "release_asset_sha256": "c" * 64,
        "package_content_sha256": directory_sha256(root),
    }


@pytest.fixture
def isolated_port(monkeypatch):
    # Exercise a real bind without depending on the operator's live Runtime.
    class Probe:
        def __enter__(self):
            self.socket = socket.socket()
            return self

        def bind(self, address):
            assert address == ("127.0.0.1", 15527)
            self.socket.bind(("127.0.0.1", 0))

        def __exit__(self, *args):
            self.socket.close()

    monkeypatch.setattr(runtime_install, "socket", SimpleNamespace(socket=Probe))


@pytest.fixture
def release(tmp_path, monkeypatch, isolated_port):
    source = tmp_path / "source"
    pin = package(
        source / runtime_install.RUNTIME_PACKAGE, runtime_install.RUNTIME_PACKAGE, runtime=True
    )
    connector = package(
        source / runtime_install.CONNECTOR_PACKAGE, runtime_install.CONNECTOR_PACKAGE
    )
    zod = source / "zod"
    package(zod, "zod")
    pin["dependency_content_sha256"] = {"zod": directory_sha256(zod)}
    archive = tmp_path / "runtime.tgz"
    archive.write_bytes(b"trusted synthetic release")
    pin["release_asset_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(runtime_install.shutil, "which", lambda _: "/trusted/npm")
    return pin, connector, archive, source


def test_install_verifies_checksum_before_running_npm(tmp_path, monkeypatch, release):
    pin, connector, archive, _ = release
    archive.write_bytes(b"changed")
    calls = []
    monkeypatch.setattr(runtime_install.subprocess, "run", lambda *a, **k: calls.append(a))
    target = tmp_path / "state/runtime"
    target.mkdir(parents=True)
    (target / "previous").write_text("keep")
    with pytest.raises(BoundaryError, match="runtime_archive_checksum_mismatch"):
        runtime_install.install_runtime(target.parent, pin, connector, archive=archive)
    assert calls == []
    assert (target / "previous").read_text() == "keep"
    assert not list(target.parent.glob(".runtime-install-*"))


def test_install_uses_fixed_command_private_env_and_verifies_promoted_content(
    tmp_path, monkeypatch, release
):
    pin, connector, archive, source = release
    calls = []
    monkeypatch.setenv("STPD_HUB_DEVICE_TOKEN", "must-not-reach-npm")
    monkeypatch.setenv("NPM_TOKEN", "must-not-reach-npm")
    monkeypatch.setenv("NODE_OPTIONS", "--require=untrusted.js")

    def npm(command, **kwargs):
        calls.append((command, kwargs))
        runtime_install.shutil.copytree(source, kwargs["cwd"] / "node_modules")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runtime_install.subprocess, "run", npm)
    directory = tmp_path / "state"
    result = runtime_install.install_runtime(directory, pin, connector, archive=archive)
    assert result["status"] == "runtime_installed"
    assert result["loaded"] is False and result["activated"] is False
    command, kwargs = calls[0]
    assert command == [
        "npm",
        "install",
        "--ignore-scripts",
        "--omit=dev",
        "--no-audit",
        "--no-fund",
    ]
    assert not {"STPD_HUB_DEVICE_TOKEN", "NPM_TOKEN", "NODE_OPTIONS"} & kwargs["env"].keys()
    assert kwargs["timeout"] == 300
    runtime_install.validate_runtime_install(directory / "runtime/node_modules", pin, connector)
    (
        directory / "runtime/node_modules" / runtime_install.CONNECTOR_PACKAGE / "package.json"
    ).write_text("{}")
    with pytest.raises(PackageIdentityError):
        runtime_install.validate_runtime_install(directory / "runtime/node_modules", pin, connector)


def test_failed_npm_preserves_previous_runtime(tmp_path, monkeypatch, release):
    pin, connector, archive, _ = release
    monkeypatch.setattr(
        runtime_install.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1)
    )
    target = tmp_path / "state/runtime"
    target.mkdir(parents=True)
    (target / "previous").write_text("keep")
    with pytest.raises(BoundaryError, match="pinned_runtime_npm_install_failed"):
        runtime_install.install_runtime(target.parent, pin, connector, archive=archive)
    assert (target / "previous").read_text() == "keep"


def test_successful_npm_with_wrong_content_is_not_promoted(tmp_path, monkeypatch, release):
    pin, connector, archive, _ = release
    monkeypatch.setattr(
        runtime_install.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0)
    )
    with pytest.raises(BoundaryError, match="pinned_runtime_install_verification_failed"):
        runtime_install.install_runtime(tmp_path / "state", pin, connector, archive=archive)
    assert not (tmp_path / "state/runtime").exists()


def test_install_rejects_symlink_archive(tmp_path, release):
    pin, connector, archive, _ = release
    link = tmp_path / "linked.tgz"
    try:
        link.symlink_to(archive)
    except (NotImplementedError, OSError) as error:
        if os.name == "nt" and (
            isinstance(error, NotImplementedError) or getattr(error, "winerror", None) == 1314
        ):
            pytest.skip("symbolic-link creation privilege is unavailable")
        raise
    with pytest.raises(BoundaryError, match="runtime_archive_missing_or_unsafe"):
        runtime_install.install_runtime(tmp_path / "state", pin, connector, archive=link)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/payload",
        "http://github.com/payload",
        "https://user:password@github.com/payload",
        "https://github.com:8443/payload",
    ],
)
def test_release_redirects_stay_on_https_github_storage(url):
    handler = runtime_install.ReleaseRedirect()
    with pytest.raises(BoundaryError, match="untrusted_runtime_release_redirect"):
        handler.redirect_request(Request("https://github.com/release"), None, 302, "Found", {}, url)


def test_offline_cli_archive_never_becomes_an_http_path(tmp_path, monkeypatch):
    from spireagent.workbench import local_model_cli
    from spireagent.workbench.developer import ProjectConfig, combination

    config = ProjectConfig(tmp_path, "", "", None, combination())
    monkeypatch.setattr(local_model_cli, "running", lambda _: {"port": 1234})
    with pytest.raises(BoundaryError, match="close_workbench_before_offline_runtime_install"):
        local_model_cli.model_command(
            config, "install-runtime", runtime_archive=Path("/private/archive")
        )


@pytest.mark.parametrize("spelling", ["Pefect", "Perfect"])
def test_download_accepts_exact_rename_alias_and_still_checks_bytes(
    tmp_path, monkeypatch, release, spelling
):
    from io import BytesIO

    pin, connector, _, _ = release
    pin["release_url"] = (
        f"https://github.com/rsgcsg/STS2-The-{spelling}-Defect-Project/releases/download/v1/runtime.tgz"
    )
    monkeypatch.setattr(
        runtime_install,
        "build_opener",
        lambda *args: SimpleNamespace(open=lambda *a, **k: BytesIO(b"wrong bytes")),
    )
    with pytest.raises(BoundaryError, match="runtime_archive_checksum_mismatch"):
        runtime_install.install_runtime(tmp_path / "state", pin, connector)


@pytest.mark.parametrize(
    "repository",
    ["other/STS2-The-Perfect-Defect-Project", "rsgcsg/STS2-The-Perfect-Defect-Project-extra"],
)
def test_download_rejects_unrelated_rename_lookalike(tmp_path, monkeypatch, release, repository):
    pin, connector, _, _ = release
    pin["release_url"] = f"https://github.com/{repository}/releases/download/v1/runtime.tgz"

    def unexpected(*args):
        raise AssertionError("must reject before network")

    monkeypatch.setattr(runtime_install, "build_opener", unexpected)
    with pytest.raises(BoundaryError, match="runtime_release_url_not_pinned"):
        runtime_install.install_runtime(tmp_path / "state", pin, connector)


def test_install_rejects_occupied_port_before_touching_release(tmp_path, monkeypatch, release):
    pin, connector, archive, _ = release
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        occupied = listener.getsockname()

        class Probe:
            def __enter__(self):
                self.socket = socket.socket()
                return self

            def bind(self, address):
                assert address == ("127.0.0.1", 15527)
                self.socket.bind(occupied)

            def __exit__(self, *args):
                self.socket.close()

        monkeypatch.setattr(runtime_install, "socket", SimpleNamespace(socket=Probe))
        calls = []
        monkeypatch.setattr(runtime_install.subprocess, "run", lambda *a, **k: calls.append(a))
        with pytest.raises(BoundaryError, match="stop_runtime_before_install"):
            runtime_install.install_runtime(tmp_path / "state", pin, connector, archive=archive)
        assert calls == []
        assert not (tmp_path / "state/runtime").exists()
        assert not list((tmp_path / "state").glob(".runtime-install-*"))
