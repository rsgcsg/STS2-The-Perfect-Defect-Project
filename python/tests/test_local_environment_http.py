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
    starts: list[tuple[str, str | None]] = []
    stops: list[str] = []
    saved: list[tuple[str, str | None]] = []
    compared: list[tuple[str, list[str]]] = []
    resumes: list[bool] = []
    closes: list[bool] = []
    recoveries: list[bool] = []
    monkeypatch.setattr(
        app.local_environment,
        "start",
        lambda identity, *, scene_artifact_id=None:
            starts.append((identity, scene_artifact_id)) or {"status": "starting"},
    )
    monkeypatch.setattr(
        app.local_environment,
        "stop",
        lambda identity: stops.append(identity) or {"status": "stopped_outcome_unknown"},
    )
    monkeypatch.setattr(
        app.local_environment, "resume",
        lambda **_expected: resumes.append(True) or {"status": "resuming"},
    )
    monkeypatch.setattr(
        app.local_environment, "close_environment",
        lambda **_expected: closes.append(True) or {"status": "closed"},
    )
    monkeypatch.setattr(
        app.local_environment, "recover_control",
        lambda **_expected: recoveries.append(True) or {
            "status": "released", "outcome": "still_unknown"
        },
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
    monkeypatch.setattr(app.local_environment, "scenes", lambda: {"items": []})
    monkeypatch.setattr(app.local_environment, "scene", lambda identity: {"artifact_id": identity})
    monkeypatch.setattr(app.local_environment, "comparisons", lambda: {"items": []})
    monkeypatch.setattr(app.local_environment, "comparison",
                        lambda identity: {"artifact_id": identity})
    monkeypatch.setattr(app.local_environment, "save_scene",
                        lambda name, seed=SCENARIO["seed"]:
                            saved.append((name, seed)) or {"name": name, "seed": seed})
    monkeypatch.setattr(app.local_environment, "compare",
                        lambda scene, reports: compared.append((scene, reports)) or
                        {"scene_artifact_id": scene, "report_artifact_ids": reports})

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
        with client.open(root + "/api/local-environment/scenes") as response:
            assert json.load(response)["items"] == []
        with client.open(root + "/api/local-environment/scenes/" + "d" * 64) as response:
            assert json.load(response)["artifact_id"] == "d" * 64
        with client.open(root + "/api/local-environment/comparisons") as response:
            assert json.load(response)["items"] == []
        with client.open(root + "/api/local-environment/comparisons/" + "e" * 64) as response:
            assert json.load(response)["artifact_id"] == "e" * 64
        assert saved == [] and compared == []
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
        with pytest.raises(HTTPError) as seed_attempt:
            post("/api/local-environment/scenes/save", {
                "name": "A0", "seed": "SEED0001", "extra": "not accepted",
            })
        assert seed_attempt.value.code == 400
        with pytest.raises(HTTPError) as scene_seed_attempt:
            post("/api/local-environment/start", {
                "scenario_id": SCENARIO["id"], "scene_artifact_id": "d" * 64,
                "seed": "other-seed",
            })
        assert scene_seed_attempt.value.code == 400
        assert starts == []
        with post("/api/local-environment/start", {"scenario_id": SCENARIO["id"]}) as response:
            assert json.load(response)["status"] == "starting"
        with post("/api/local-environment/start", {
            "scenario_id": SCENARIO["id"], "scene_artifact_id": "d" * 64,
        }) as response:
            assert json.load(response)["status"] == "starting"
        assert starts == [(SCENARIO["id"], None), (SCENARIO["id"], "d" * 64)]
        binding = {"session_id": "a" * 32, "service_instance_id": "service-1",
                   "runtime_instance_id": "runtime-1", "game_continuity_id": "episode-1"}
        with post("/api/local-environment/resume", binding) as response:
            assert json.load(response)["status"] == "resuming"
        with post("/api/local-environment/close", {key: value for key, value in binding.items()
                if key != "session_id"}) as response:
            assert json.load(response)["status"] == "closed"
        with post("/api/local-environment/recover-control", binding) as response:
            assert json.load(response)["outcome"] == "still_unknown"
        assert resumes == [True] and closes == [True] and recoveries == [True]
        with post("/api/local-environment/scenes/save", {"name": "Start A"}) as response:
            assert json.load(response)["name"] == "Start A"
        with post("/api/local-environment/scenes/save", {
            "name": "Seeded A", "seed": "M2H0ST20260929B",
        }) as response:
            assert json.load(response)["seed"] == "M2H0ST20260929B"
        with post("/api/local-environment/compare", {
            "scene_artifact_id": "d" * 64,
            "report_artifact_ids": ["a" * 64, "b" * 64],
        }) as response:
            assert json.load(response)["report_artifact_ids"] == ["a" * 64, "b" * 64]
        assert saved == [("Start A", SCENARIO["seed"]),
                         ("Seeded A", "M2H0ST20260929B")]
        assert compared == [("d" * 64, ["a" * 64, "b" * 64])]
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
