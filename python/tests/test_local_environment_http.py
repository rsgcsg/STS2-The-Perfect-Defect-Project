from __future__ import annotations

import json
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest

from spireagent.workbench.developer import ProjectConfig, atomic_json, combination
from spireagent.workbench.developer_server import Application, configuration_id, create_server
from spireagent.workbench.local_environment import JOURNAL_FILE, SCENARIO


def test_local_environment_http_is_browser_scoped_exact_and_get_only_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    config = ProjectConfig(state, "", "", None, combination())
    config_path = tmp_path / "project.json"
    atomic_json(config_path, config.to_dict())
    app = Application(config, config_path=config_path)
    server = create_server(app)
    atomic_json(
        state / "runtime.json",
        {
            "instance_id": app.instance_id,
            "configuration_id": configuration_id(config),
        },
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    starts: list[str] = []
    stops: list[str] = []
    monkeypatch.setattr(
        app.local_environment,
        "start",
        lambda identity: starts.append(identity) or {"status": "starting"},
    )
    monkeypatch.setattr(
        app.local_environment,
        "stop",
        lambda identity: stops.append(identity) or {"status": "stopped_outcome_unknown"},
    )
    monkeypatch.setattr(
        app.local_environment,
        "event",
        lambda identity: {
            "request_id": "request-1",
            "result": {"status": "applied"},
            "event_artifact_id": identity,
        },
    )

    def post(path: str, body: dict[str, str], *, csrf: bool = True):
        return client.open(
            Request(
                root + path,
                data=json.dumps(body).encode(),
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Origin": root,
                    **({"X-CSRF-Token": app.account.csrf} if csrf else {}),
                },
            )
        )

    try:
        with pytest.raises(HTTPError) as unauthenticated:
            client.open(root + "/api/local-environment")
        assert unauthenticated.value.code == 401
        client.open(root + "/").close()
        with client.open(root + "/api/local-environment") as response:
            value = json.load(response)
        assert value["availability"] == "profile_required"
        assert value["csrf_token"] == app.account.csrf
        with client.open(root + "/api/local-environment/reports") as response:
            assert json.load(response)["items"] == []
        with client.open(root + "/api/local-environment/events/" + "e" * 64) as response:
            assert json.load(response)["result"]["status"] == "applied"
        with pytest.raises(HTTPError) as malformed_event:
            client.open(root + "/api/local-environment/events/../../private")
        assert malformed_event.value.code in {400, 404}
        assert not (state / JOURNAL_FILE).exists()
        with pytest.raises(HTTPError) as denied:
            post("/api/local-environment/start", {"scenario_id": SCENARIO["id"]}, csrf=False)
        assert denied.value.code == 403
        with pytest.raises(HTTPError) as path_attempt:
            post(
                "/api/local-environment/start",
                {
                    "scenario_id": SCENARIO["id"],
                    "candidate_directory": str(tmp_path),
                },
            )
        assert path_attempt.value.code == 400
        assert starts == []
        with post("/api/local-environment/start", {"scenario_id": SCENARIO["id"]}) as response:
            assert json.load(response)["status"] == "starting"
        assert starts == [SCENARIO["id"]]
        atomic_json(
            state / "runtime.json",
            {
                "instance_id": "another",
                "configuration_id": configuration_id(config),
            },
        )
        with pytest.raises(HTTPError) as mismatched:
            post(
                "/api/local-environment/submit",
                {
                    "session_id": "a" * 32,
                    "action_id": "action",
                    "expected_snapshot_id": "snapshot",
                    "expected_game_continuity_id": "episode",
                },
            )
        assert mismatched.value.code == 409
        # This service still owns safe Stop even if a source/config upgrade
        # prevents a new game mutation from this old running instance.
        with post("/api/local-environment/stop", {"session_id": "a" * 32}) as response:
            assert json.load(response)["status"] == "stopped_outcome_unknown"
        assert stops == ["a" * 32]
        assert not (state / JOURNAL_FILE).exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        app.close()
