"""Original Application admission at the owning CLI request boundary."""

from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench import local_model_cli


@pytest.mark.parametrize(
    "client,action,arguments",
    [
        ("native", "prepare-owned", {"intent_id": "a" * 32, "seed": "ABC123"}),
        ("native", "native-status", {}),
        ("native", "native-finish", {"environment_ref": "b" * 32}),
        ("native", "run-agent", {"environment_ref": "b" * 32, "selection": "agent"}),
        ("model", "status", {}),
        ("model", "auto", {}),
        ("model", "stop", {}),
    ],
)
def test_expected_instance_rejects_replacement_before_transport(
    tmp_path, monkeypatch, client, action, arguments,
):
    config = SimpleNamespace(state_dir=tmp_path)
    replacement = {"instance_id": "replacement", "port": 12345, "control_token": "new-token"}
    resolved = []
    monkeypatch.setattr(
        local_model_cli, "running", lambda value: resolved.append(value) or replacement
    )
    monkeypatch.setattr(local_model_cli, "build_opener", lambda *_: pytest.fail("no HTTP offer"))
    command = (local_model_cli.native_episode_command if client == "native"
               else local_model_cli.model_command)
    with pytest.raises(BoundaryError, match="application_instance_mismatch"):
        command(config, action, expected_instance_id="original", **arguments)
    assert resolved == [config]


@pytest.mark.parametrize("client", ["native", "model"])
@pytest.mark.parametrize("expected_instance_id", [None, "original"])
def test_one_resolution_retains_original_request_address_and_token(
    tmp_path, monkeypatch, client, expected_instance_id,
):
    config = SimpleNamespace(state_dir=tmp_path)
    original = {"instance_id": "original", "port": 12345, "control_token": "original-token"}
    replacement = {"instance_id": "replacement", "port": 54321, "control_token": "new-token"}
    current = original
    resolved, offered = [], []

    def running(value):
        resolved.append(value)
        return current

    class Transport:
        def open(self, request, *, timeout):
            nonlocal current
            current = replacement  # The original resource disappears after the first offer.
            offered.append((request.full_url, request.get_header("Authorization"),
                            json.loads(request.data), timeout))
            return BytesIO(b'{"schema":"stpd/local-models-v1"}')

    monkeypatch.setattr(local_model_cli, "running", running)
    monkeypatch.setattr(local_model_cli, "build_opener", lambda *_: Transport())
    if client == "native":
        local_model_cli.native_episode_command(
            config, "run-agent", selection="agent", environment_ref="b" * 32,
            expected_instance_id=expected_instance_id,
        )
        assert [item[0] for item in offered] == [
            "http://127.0.0.1:12345/api/local-models/request",
            "http://127.0.0.1:12345/api/local-models/run-agent",
        ]
        assert offered[0][2] == {
            "selection_id": "agent", "operation": "prepare-and-takeover",
            "run_profile": "short", "native_episode_ref": "b" * 32,
        }
        assert offered[1][2] == {"schema": "stpd/local-models-v1"}
    else:
        local_model_cli.model_command(config, "auto", expected_instance_id=expected_instance_id)
        assert [item[0] for item in offered] == ["http://127.0.0.1:12345/api/local-models/command"]
        assert offered[0][2] == {"action": "auto"}
    assert resolved == [config]
    assert all(item[1] == "Bearer original-token" for item in offered)


@pytest.mark.parametrize(
    "action,arguments",
    [
        ("install-runtime", {"runtime_archive": "candidate.tgz"}),
        ("initialize-runtime-generation", {}),
        ("upgrade-runtime-generation", {}),
        ("rollback-runtime-generation", {}),
    ],
)
def test_offline_install_and_generation_reject_instance_guard_before_owner(
    tmp_path, monkeypatch, action, arguments,
):
    monkeypatch.setattr(local_model_cli, "running", lambda *_: pytest.fail("no instance lookup"))
    monkeypatch.setattr(local_model_cli, "instance_lock", lambda *_: pytest.fail("no install lock"))
    with pytest.raises(BoundaryError, match="expected_instance_requires_running_command"):
        local_model_cli.model_command(
            SimpleNamespace(state_dir=tmp_path), action,
            expected_instance_id="original", **arguments,
        )
