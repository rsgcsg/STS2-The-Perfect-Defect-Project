"""Synthetic pairing credentials only; no actual installed configuration is changed."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench import native_workbench_access as access_module
from spireagent.workbench.developer import ProjectConfig, atomic_json, combination
from spireagent.workbench.developer_server import Application, configuration_id
from spireagent.workbench.native_workbench_access import (
    BOOTSTRAP_SCHEMA,
    CURRENT_SCHEMA,
    NativeBootstrap,
    NativePair,
    private_bytes,
)

FIXTURE = Path(__file__).parents[2] / "docs/design/fixtures/native_workbench_pair_v1.json"


def paired_app(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    config = ProjectConfig(state, "", "http://127.0.0.1:15526", None, combination())
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    root = tmp_path / "selected-launcher"
    root.mkdir()
    identity = {
        "working_tree_clean": True,
        "source_revision": "4" * 40,
        "workbench_sha256": "5" * 64,
        "uv_lock_sha256": "6" * 64,
    }
    # A synthetic verified-launcher fixture isolates pairing from real kit install.
    app.identity = identity
    launcher = {
        "schema": "spireagent/workbench-launcher-v1",
        "release_directory": str(tmp_path),
        "kit_sha256": "7" * 64,
        "config_path": str(config_path),
        **{key: identity[key] for key in ("source_revision", "workbench_sha256", "uv_lock_sha256")},
    }
    atomic_json(root / "launcher.json", launcher)
    secret = "a" * 64
    bootstrap = {
        "schema": BOOTSTRAP_SCHEMA,
        "enabled": True,
        "config_path": str(config_path),
        "launcher_sha256": hashlib.sha256((root / "launcher.json").read_bytes()).hexdigest(),
        "game_mod_sha256": "8" * 64,
        "secret": secret,
    }
    atomic_json(root / "native-access.json", bootstrap)
    monkeypatch.setattr(access_module, "selected_directory", lambda: root)
    atomic_json(
        state / "runtime.json",
        {"instance_id": app.instance_id, "configuration_id": configuration_id(config)},
    )
    pair = NativePair(
        "fixture-game",
        app.instance_id,
        configuration_id(config),
        "http://127.0.0.1:12345/",
        "b" * 32,
        int(time.time()) + 500,
    )

    class Peer:
        def __init__(self):
            self.calls = []
            self.current = True

        def _request(self, method, route, *, headers):
            self.calls.append((method, route))
            assert headers == pair.headers(secret)
            if not self.current:
                return {"schema": "wrong-current-pair"}
            return {
                "schema": CURRENT_SCHEMA,
                **pair.to_dict(),
                "signature": pair.sign(secret, "native-current-v1"),
            }

    peer = Peer()
    app.native_access.install(pair, peer)
    return app, pair, root, secret, peer


def test_shared_python_csharp_signing_vectors_reject_role_or_control_injection():
    vector = json.loads(FIXTURE.read_text())
    pair = NativePair.from_dict(vector["binding"])
    assert vector["fixture_only"] is True
    for role, expected in vector["roles"].items():
        assert pair.sign(vector["bootstrap_secret"], role) == expected["hmac_sha256"]
    assert len({value["hmac_sha256"] for value in vector["roles"].values()}) == 4
    with pytest.raises(BoundaryError, match="invalid_native_role"):
        pair.sign(vector["bootstrap_secret"], "native-register-v1\ninjected")
    with pytest.raises(BoundaryError):
        NativePair.from_dict({**pair.to_dict(), "runtime_instance_id": "game\nother"})
    with pytest.raises(BoundaryError):
        NativePair.from_dict({**pair.to_dict(), "workbench_url": "https://other.example/"})


def test_private_selected_bootstrap_binds_exact_launcher_and_profile(tmp_path, monkeypatch):
    app, pair, root, secret, peer = paired_app(tmp_path, monkeypatch)
    try:
        assert app.native_access.authenticate(pair.headers(secret)) == pair
        assert peer.calls == [("GET", "/v1/workbench/native-status")]
        assert secret not in repr(app.native_access.bootstrap())
        raw = json.loads((root / "native-access.json").read_text())
        atomic_json(
            root / "native-access.json", {**raw, "config_path": str(tmp_path / "other.json")}
        )
        with pytest.raises(BoundaryError, match="native_configuration_mismatch"):
            app.native_access.authenticate(pair.headers(secret))
        atomic_json(root / "native-access.json", raw)
        atomic_json(root / "launcher.json", {"config_path": str(app.config_path), "changed": True})
        with pytest.raises(BoundaryError, match="native_launcher_mismatch"):
            app.native_access.authenticate(pair.headers(secret))
    finally:
        app.close()


@pytest.mark.parametrize(
    "tamper",
    [
        "Authorization",
        "X-STS2-Game-Instance-ID",
        "X-SpireAgent-Workbench-Instance-ID",
        "X-SpireAgent-Configuration-ID",
        "X-SpireAgent-Pair-ID",
    ],
)
def test_every_native_header_is_exact_and_does_not_dispatch(tmp_path, monkeypatch, tamper):
    app, pair, _root, secret, peer = paired_app(tmp_path, monkeypatch)
    try:
        headers = {**pair.headers(secret), tamper: "wrong"}
        with pytest.raises(BoundaryError, match="native_pair_mismatch"):
            app.native_access.authenticate(headers)
        assert peer.calls == []
    finally:
        app.close()


def test_origin_cookie_revocation_expiry_and_current_game_are_independent(tmp_path, monkeypatch):
    app, pair, root, secret, peer = paired_app(tmp_path, monkeypatch)
    try:
        for name in ("Origin", "Cookie"):
            with pytest.raises(BoundaryError, match="native_loopback_required"):
                app.native_access.authenticate({**pair.headers(secret), name: "browser"})
        peer.current = False
        with pytest.raises(BoundaryError, match="native_game_pair_changed"):
            app.native_access.authenticate(pair.headers(secret))
        peer.current = True
        monkeypatch.setattr(access_module.time, "time", lambda: pair.expires_at + 1)
        with pytest.raises(BoundaryError, match="native_pair_expired"):
            app.native_access.authenticate(pair.headers(secret))
        (root / "native-access.json").unlink()
        with pytest.raises(BoundaryError, match="native_access_not_configured"):
            app.native_access.bootstrap()
    finally:
        app.close()


def test_private_reader_rejects_symlink_ancestors_duplicate_keys_and_size(tmp_path, monkeypatch):
    app, _pair, root, _secret, _peer = paired_app(tmp_path, monkeypatch)
    try:
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        with pytest.raises(BoundaryError, match="private_native_path_required"):
            private_bytes(alias / "native-access.json")
        (root / "native-access.json").write_bytes(b" " * 4097)
        with pytest.raises(BoundaryError, match="private_native_file_required"):
            NativeBootstrap.read(root, app.config_path)
        (root / "native-access.json").write_text('{"schema":"a","schema":"b"}')
        with pytest.raises(ValueError, match="duplicate_json_key"):
            NativeBootstrap.read(root, app.config_path)
        (root / "native-access.json").chmod(0o644)
        with pytest.raises(BoundaryError, match="private_native_file_required"):
            private_bytes(root / "native-access.json")
    finally:
        app.close()


def test_installer_explicit_opt_in_is_selected_private_idempotent_and_revocable(
    tmp_path, monkeypatch
):
    from tools import install_developer_kit as installer

    app, _pair, root, _secret, _peer = paired_app(tmp_path, monkeypatch)
    try:
        selected = json.loads((root / "launcher.json").read_text())
        monkeypatch.setattr(installer, "_launcher_directory", lambda: root)
        monkeypatch.setattr(installer, "_launcher_binding", lambda *_: selected)
        prepared = {"mod_sha256": "8" * 64}
        before = (root / "native-access.json").read_bytes()
        enabled = installer.configure_native_access(
            tmp_path, app.config_path, prepared, enabled=True
        )
        assert enabled["status"] == "native_access_enabled" and enabled["gameplay_started"] is False
        assert (root / "native-access.json").read_bytes() == before
        disabled = installer.configure_native_access(
            tmp_path, app.config_path, prepared, enabled=False
        )
        assert disabled["status"] == "native_access_disabled"
        with pytest.raises(BoundaryError, match="native_access_not_configured"):
            app.native_access.bootstrap()
        alias = tmp_path / "alias-profile"
        alias.symlink_to(app.config_path)
        with pytest.raises(BoundaryError, match="private_native_path_required"):
            installer.configure_native_access(tmp_path, alias, prepared, enabled=True)
        assert "secret" not in enabled and "secret" not in disabled
    finally:
        app.close()


@pytest.mark.parametrize("enabled", [True, False])
def test_launcher_republication_requires_explicit_native_binding_revalidation(
    tmp_path, monkeypatch, enabled,
):
    from tools import install_developer_kit as installer

    app, _pair, root, _secret, _peer = paired_app(tmp_path, monkeypatch)
    try:
        selected = json.loads((root / "launcher.json").read_bytes())
        before_launcher = (root / "launcher.json").read_bytes()
        (root / "open").write_bytes(b"#!/bin/sh\nexit 0\n")
        (root / "open").chmod(0o700)
        monkeypatch.setattr(installer, "_launcher_directory", lambda **_: root)
        monkeypatch.setattr(installer, "_launcher_binding", lambda *_: selected)
        monkeypatch.setattr(installer, "_launcher_script", lambda *_: "#!/bin/sh\nexit 0\n")
        prepared = {"workbench_launcher_schema": installer.LAUNCHER_SCHEMA,
                    "mod_sha256": "8" * 64}
        installer.configure_native_access(tmp_path, app.config_path, prepared, enabled=enabled)
        previous_bootstrap = (root / "native-access.json").read_bytes()
        installer._install_open_launcher(tmp_path, app.config_path, prepared, platform="darwin")
        assert (root / "launcher.json").read_bytes() != before_launcher
        assert json.loads((root / "launcher.json").read_bytes()) == selected
        assert (root / "native-access.json").read_bytes() == previous_bootstrap
        code = "native_launcher_mismatch" if enabled else "native_access_not_configured"
        with pytest.raises(BoundaryError, match=code):
            NativeBootstrap.read(root, app.config_path)
        result = installer.configure_native_access(
            tmp_path, app.config_path, prepared, enabled=enabled,
        )
        assert "secret" not in result
        current = json.loads((root / "native-access.json").read_bytes())
        assert current["enabled"] is enabled
        assert current["launcher_sha256"] == hashlib.sha256(
            (root / "launcher.json").read_bytes()).hexdigest()
        if enabled:
            assert NativeBootstrap.read(root, app.config_path).config_path == str(app.config_path)
        else:
            with pytest.raises(BoundaryError, match="native_access_not_configured"):
                NativeBootstrap.read(root, app.config_path)
    finally:
        app.close()
