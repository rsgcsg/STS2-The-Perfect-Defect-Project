"""Existing Application instance/browser authority guards the new closed routes."""

from __future__ import annotations

import json
import threading
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest

from spireagent.workbench.developer import ProjectConfig, atomic_json, combination
from spireagent.workbench.developer_server import Application, configuration_id, create_server


def test_common_routes_use_same_browser_configuration_and_registration_owner(tmp_path, monkeypatch):
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    atomic_json(
        config.state_dir / "runtime.json",
        {
            "instance_id": app.instance_id,
            "configuration_id": configuration_id(config),
        },
    )
    calls = []
    monkeypatch.setattr(
        app.local_model_registration,
        "register_teacher",
        lambda **kw: calls.append(("teacher", kw)) or {"registered": True},
    )
    monkeypatch.setattr(
        app.local_model_registration,
        "register_composition",
        lambda identity, **kw: calls.append((identity, kw)) or {"registered": True},
    )
    monkeypatch.setattr(
        app.models, "run_agent", lambda body: calls.append(("run", body)) or {"admitted": True}
    )
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    def post(route, body, *, csrf=None):
        return client.open(
            Request(
                root + route,
                data=json.dumps(body).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Origin": root,
                    "X-CSRF-Token": app.account.csrf if csrf is None else csrf,
                },
            )
        )

    try:
        client.open(root + "/").close()
        teacher = "/api/local-model-registrations/register-teacher"
        composition = "/api/local-model-registrations/register-composition"
        run = "/api/local-models/run-agent"
        for route in (teacher, composition, run):
            with pytest.raises(HTTPError) as denied:
                post(route, {}, csrf="wrong")
            assert denied.value.code == 403
        assert calls == []
        with post(teacher, {"variant": "continuous"}) as response:
            assert json.load(response) == {"registered": True}
        assert calls[-1] == ("teacher", {"variant": "continuous"})
        options = {"model_id": "a" * 64, "task_option": "none", "program_option": "none"}
        with post(composition, options) as response:
            assert json.load(response) == {"registered": True}
        assert calls[-1] == (
            "a" * 64,
            {"environment_kind": "native", "task_option": "none", "program_option": "none"},
        )
        with post(run, {"already_effective": "pure-owner-tested"}) as response:
            assert json.load(response) == {"admitted": True}
        assert calls[-1] == ("run", {"already_effective": "pure-owner-tested"})
        before = len(calls)
        for route, body in (
            (teacher, {"command": "shell"}),
            (composition, {**options, "command": "shell"}),
            (composition, {"model_id": "a" * 64}),
        ):
            with pytest.raises(HTTPError) as invalid:
                post(route, body)
            assert invalid.value.code == 400
        assert len(calls) == before
        atomic_json(
            config.state_dir / "runtime.json",
            {
                "instance_id": "wrong",
                "configuration_id": configuration_id(config),
            },
        )
        for route, body in ((teacher, {}), (composition, options)):
            with pytest.raises(HTTPError) as changed:
                post(route, body)
            assert changed.value.code == 409
            assert json.load(changed.value)["error"] == "running_configuration_mismatch"
        assert len(calls) == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()
