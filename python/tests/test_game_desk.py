"""Application orchestration; native execution is exercised by Runtime's HTTP fixture.

These cases use fake Recorder/model owners to expose independent Stop, source
handoff, uncertain writes and exact annotation targeting. They do not prove a
loaded game, physical Human origin, or model quality.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.workbench.game_desk import GameDeskApplication
from spireagent.workbench.native_tasks import P1_PROFILE


class Models:
    def __init__(self, root: Path) -> None:
        self.lock = threading.RLock()
        self.directory = root / "models"
        self.directory.mkdir()
        self.state = {"loaded": False, "status": "stopped", "operation": {"status": "completed"}}
        self.client = None
        self.thread = None
        self.registry = lambda: {"policies": []}
        self._save = Mock()
        self.commands = []
        self.runtime = {
            "mode": "human",
            "controller": "released",
            "pending_request": None,
            "tainted": False,
            "environment": {"runtime_instance_id": "game-1"},
        }

    def command(self, action, **kwargs):
        from spireagent.workbench.local_models import LocalModelService

        if kwargs.get("expected_context") is not None:
            LocalModelService.read_control_context(kwargs["expected_context"])
        with self.lock:
            if kwargs.get("intent_guard") is not None:
                kwargs["intent_guard"]()
        self.commands.append(action)
        if action == "stop":
            self.client = None
            self.state.update(status="stopped", loaded=False)
        elif action == "human":
            self.runtime.update(mode="human", controller="released")
        else:
            self.runtime["mode"] = action
        self.state["runtime"] = None if action == "stop" else self.runtime
        self.state["operation"] = {"id": str(len(self.commands)), "status": "completed"}
        return self.state


def app_fixture(tmp_path):
    models = Models(tmp_path)
    owner = {
        "runtime_instance_id": "game-1",
        "recording_session_id": None,
        "recording_lifecycle": "ready",
        "closeout_status": "idle",
        "source": None,
        "capture_profile_id": None,
    }
    feedback = {
        "runtime_instance_id": "game-1",
        "session_id": None,
        "lifecycle": "ready",
        "closeout": "idle",
        "count": 0,
        "profile": None,
        "recording_directory": None,
        "armed": False,
        "health": {"append": "healthy", "disk": "healthy", "error": None},
    }
    calls = []
    app = SimpleNamespace(
        models=models,
        config=SimpleNamespace(state_dir=tmp_path, platform_url="http://127.0.0.1:15526"),
    )
    models.native_tasks = SimpleNamespace(recording_feedback=lambda _: dict(feedback))
    app.native_recording_status = lambda: {"status": dict(owner)}

    def control(body):
        calls.append(dict(body))
        if body["kind"] == "start_new_session":
            session = "session-" + str(len(calls))
            directory = tmp_path / "originals" / session
            directory.mkdir(parents=True)
            (directory / "recording-manifest.json").write_bytes(
                json_bytes(
                    {
                        "session_id": session,
                        "capture_profile_id": P1_PROFILE,
                        "p1_native_origin": body["source_kind"],
                    }
                )
            )
            (directory / "p1-native-events.jsonl").write_bytes(b"")
            owner.update(
                recording_session_id=session,
                recording_lifecycle="recording",
                closeout_status="idle",
                capture_profile_id=P1_PROFILE,
            )
            feedback.update(
                session_id=session,
                lifecycle="recording",
                closeout="idle",
                profile=P1_PROFILE,
                recording_directory=str(directory),
                armed=True,
            )
        else:
            owner.update(recording_lifecycle="closed", closeout_status="closed")
            feedback.update(lifecycle="closed", closeout="closed", armed=False)
        return {"status": dict(owner), "last_result": {"accepted": True}}

    app.control_native_recording = control
    desk = GameDeskApplication(app)
    return desk, app, owner, feedback, calls


def native_row(session, sequence=1, decision="human-1"):
    return {
        "kind": "decision_accepted",
        "sequence": sequence,
        "session_id": session,
        "decision_id": decision,
        "recorded_at": "2026-11-01T01:00:00Z",
        "run_id": "native-run",
        "native_action_type": "PlayCardAction",
        "bound_action": {"label": "打击 → 敌人A", "bound_action_id": "play-1"},
        "native_witness": {"origin": "native_ui"},
        "basis": {"snapshot_id": "snapshot-1"},
        "missing_reason": None,
        "native_receipt": {"kind": "accepted"},
    }


def test_recording_buttons_map_to_same_owner_and_model_stop_does_not_stop_recording(tmp_path):
    desk, app, owner, feedback, calls = app_fixture(tmp_path)
    app.models.state["operation"] = None
    result = desk.command("record_start", {})
    assert result["recording"]["saving"] is True
    assert calls[0]["source_kind"] == "declared_human"
    app.models.client = SimpleNamespace(
        request=lambda _: {"status": app.models.runtime}, startup={"run_id": "model-1"}
    )
    app.models.state.update(loaded=True, status="loaded", selection_id="model-1")
    assert desk.command("model_stop", {})["recovery_confirmed"] is True
    assert app.models.commands == ["human"]
    assert owner["recording_lifecycle"] == "recording"
    assert len(calls) == 1


def test_stop_recording_leaves_running_model_and_uses_durable_close(tmp_path, monkeypatch):
    desk, app, owner, feedback, calls = app_fixture(tmp_path)
    desk.record_start()
    app.models.client = SimpleNamespace(
        request=lambda _: {"status": app.models.runtime}, startup={"run_id": "model-1"}
    )
    app.models.runtime["mode"] = "auto"
    app.models.state.update(loaded=True, status="loaded", selection_id="model-1")
    published = Mock(return_value={"artifact_id": "f" * 64})
    monkeypatch.setattr(desk, "publish_index", published)
    result = desk.record_stop()
    assert result["recovery_confirmed"] is True
    assert calls[-1]["kind"] == "close"
    assert owner["closeout_status"] == "closed"
    assert app.models.runtime["mode"] == "auto"
    assert app.models.commands == []
    assert result["recording"]["saving"] is False
    published.assert_called_once()


@pytest.mark.parametrize("lifecycle", ["ready", "closed"])
def test_stop_disarms_original_continuous_recorder_even_between_games(tmp_path, lifecycle):
    desk, app, owner, feedback, calls = app_fixture(tmp_path)
    owner.update(recording_lifecycle=lifecycle, closeout_status="closed")
    feedback.update(lifecycle=lifecycle, armed=True)
    desk.publish_index = Mock()
    assert desk.record_stop()["recovery_confirmed"] is True
    assert [call["kind"] for call in calls] == ["close"]
    assert feedback["armed"] is False


@pytest.mark.parametrize("lifecycle,saving", [("ready", False), ("recording", True)])
def test_inherited_armed_recorder_is_visible_without_guessing_human_origin(
    tmp_path, lifecycle, saving
):
    desk, app, owner, feedback, calls = app_fixture(tmp_path)
    feedback.update(armed=True, lifecycle=lifecycle)
    desk.poll()
    result = desk.view()["recording"]
    assert result["requested"] is True and result["saving"] is saving
    assert "来源尚未核实" in result["message"]
    assert desk.preference is None and desk.steps() == []
    assert calls == []  # Viewing inherited standby must not declare a new Human source.


@pytest.mark.parametrize("action", ["auto", "one_step", "human_choice"])
def test_model_and_rank_strip_response_schema_before_real_control_context_parser(tmp_path, action):
    desk, app, owner, feedback, calls = app_fixture(tmp_path)
    context = {
        "schema": "spireagent/runtime-control-context-1",
        "runtime_run_id": "run-1",
        "runtime_instance_id": "a" * 32,
        "recovery_epoch": 0,
    }
    app.models.control_context = lambda: context
    desk._ensure_loaded = Mock()
    desk._attach_runtime = Mock()
    desk.poll = Mock()
    if action == "human_choice":
        app.models.human_choices = lambda: {
            "context": context,
            "catalog": [],
            "catalog_key": "b" * 64,
            "ready": True,
            "reason": None,
            "observation": {},
            "model_choice_id": None,
        }
        desk.choices()
        desk.choice_context["ids"] = ["choice-1"]
        desk.rank(desk.choice_context["catalog_key"], ["choice-1"])
    else:
        desk.models_list = lambda: [{"id": "model-1", "label": "Model"}]
        desk.model_control("model-1", action)
    assert app.models.commands == [action]


def test_stop_ack_prevents_old_model_control_from_admitting_after_slow_load(tmp_path):
    desk, app, owner, feedback, calls = app_fixture(tmp_path)
    entered, release = threading.Event(), threading.Event()
    errors = []
    desk.models_list = lambda: [{"id": "model-1", "label": "Model"}]
    desk._attach_runtime = Mock()
    desk.poll = Mock()
    app.models.control_context = lambda: {
        "runtime_run_id": "run-1",
        "runtime_instance_id": "a" * 32,
        "recovery_epoch": 0,
    }

    def slow_load(*_):
        entered.set()
        assert release.wait(5)

    desk._ensure_loaded = slow_load

    def run():
        try:
            desk.model_control("model-1", "auto")
        except BoundaryError as error:
            errors.append(error.code)

    worker = threading.Thread(target=run)
    worker.start()
    assert entered.wait(5)
    assert desk.model_stop()["recovery_confirmed"] is True
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert errors == ["native_model_intent_superseded"]
    assert app.models.commands == []


def test_unknown_recording_start_is_not_retried_by_observer_or_restart(tmp_path):
    desk, app, owner, feedback, calls = app_fixture(tmp_path)
    failed = Mock(side_effect=BoundaryError("recording", "native_recording_command_unknown"))
    app.control_native_recording = failed
    with pytest.raises(BoundaryError, match="native_recording_command_unknown"):
        desk.record_start()
    for _ in range(3):
        desk.poll()
    restarted = GameDeskApplication(app)
    restarted.poll()
    assert failed.call_count == 1
    assert restarted.view()["recording"]["saving"] is False
    assert "没在保存" in restarted.view()["recording"]["message"]


def test_mistake_refreshes_and_targets_previous_saved_human_instead_of_latest_model(tmp_path):
    desk, app, owner, feedback, calls = app_fixture(tmp_path)
    desk.record_start()
    session = feedback["session_id"]
    path = Path(feedback["recording_directory"]) / "p1-native-events.jsonl"
    path.write_bytes(json_bytes(native_row(session)))
    desk.poll()
    # A second mouse action arrives before the UI's next poll.
    with path.open("ab") as out:
        out.write(json_bytes(native_row(session, 2, "human-2")))
    desk.sources["runtime"]["model-run"] = {"runtime_instance_id": "game-1"}
    desk._append(
        "runtime_event",
        run_id="model-run",
        recording=False,
        original={
            "kind": "global_choice",
            "sequence": 9,
            "recorded_at": "2026-11-01T01:00:01Z",
            "payload": {"acquisition_id": "acq", "choice_id": "model-choice"},
        },
    )
    result = desk.command("mistake", {"step_id": None})
    steps = desk.steps()
    marked = next(step for step in steps if step["annotations"])
    assert marked["source"]["decision_id"] == "human-2"
    assert marked["actor"] == "human"
    assert len([step for step in steps if step["actor"] == "model"]) == 1
    assert "已标在第" in result["message"]
    before = len(steps)
    desk.poll()
    assert len(desk.steps()) == before  # Re-reading the original is idempotent.


def test_unknown_source_does_not_become_human_and_no_step_can_be_undone(tmp_path):
    desk, app, owner, feedback, calls = app_fixture(tmp_path)
    desk.record_start()
    directory = Path(feedback["recording_directory"])
    manifest = json.loads((directory / "recording-manifest.json").read_bytes())
    manifest["p1_native_origin"] = "unknown"
    (directory / "recording-manifest.json").write_bytes(json_bytes(manifest))
    # New application reads the unchanged original declaration.
    fresh = GameDeskApplication(app)
    (directory / "p1-native-events.jsonl").write_bytes(
        json_bytes(native_row(feedback["session_id"]))
    )
    fresh.poll()
    assert fresh.steps()[0]["actor"] == "unknown"
    with pytest.raises(BoundaryError, match="no_recorded_human_action"):
        fresh.command("mistake", {"step_id": None})
    fresh.command("mark", {"note": "候选标签缺少目标", "step_id": None})
    assert fresh.steps()[0]["action"]["bound_action_id"] == "play-1"
    assert fresh.steps()[0]["annotations"][0]["note"] == "候选标签缺少目标"


@pytest.mark.parametrize("cached", [False, True])
def test_tainted_runtime_stop_seals_original_and_does_not_replay(tmp_path, cached):
    desk, app, owner, feedback, calls = app_fixture(tmp_path)
    app.models.client = SimpleNamespace(
        request=lambda _: {"status": app.models.runtime}, startup={"run_id": "unknown-run"}
    )
    app.models.state.update(loaded=True, status="loaded")
    app.models.runtime["tainted"] = True
    if cached:
        desk.runtime = app.models.runtime
    assert desk.model_stop()["recovery_confirmed"] is True
    assert app.models.commands == (["stop"] if cached else ["human", "stop"])
    assert app.models.client is None


def test_partial_native_line_is_not_counted_and_later_complete_line_is_read_once(tmp_path):
    desk, app, owner, feedback, calls = app_fixture(tmp_path)
    desk.record_start()
    raw = json_bytes(native_row(feedback["session_id"]))
    path = Path(feedback["recording_directory"]) / "p1-native-events.jsonl"
    path.write_bytes(raw[:-1])
    desk.poll()
    assert desk.steps() == []
    with path.open("ab") as out:
        out.write(b"\n")
    desk.poll()
    assert len(desk.steps()) == 1
    desk.poll()
    assert len(desk.steps()) == 1


@pytest.mark.parametrize("status", ["pending", "unknown"])
def test_game_exit_observer_never_resends_an_unconfirmed_stop(tmp_path, status):
    desk, app, owner, feedback, calls = app_fixture(tmp_path)
    app.models.client = SimpleNamespace(startup={"run_id": "owned-run"})
    app.models.state["operation"] = {"action": "stop", "status": status, "id": "original"}
    desk.sources["runtime"]["owned-run"] = {}
    with pytest.raises(BoundaryError, match="model_stop_pending_check_original"):
        desk.finalize_after_game_exit()
    assert app.models.commands == []


@pytest.mark.parametrize("changed", [False, True])
def test_manual_program_registration_is_refreshed_after_mod_upgrade(tmp_path, changed):
    import hashlib

    from spireagent.encoding import canonical_json
    from spireagent.workbench.local_models import GLOBAL_ADAPTER

    desk, app, owner, feedback, calls = app_fixture(tmp_path)
    producer = {"id": "connector", "implementation": {"artifact_sha256": "a" * 64}}
    game = {"version": "fixture"}
    manifest = {
        "requirements": {
            "environment": {
                "producer_sha256": hashlib.sha256(canonical_json(producer).encode()).hexdigest(),
                "game_sha256": hashlib.sha256(canonical_json(game).encode()).hexdigest(),
            }
        }
    }
    path = tmp_path / "old-manifest.json"
    path.write_bytes(json_bytes(manifest))
    if changed:
        producer["implementation"]["artifact_sha256"] = "b" * 64
    app.models.selection = lambda _: {"adapter": GLOBAL_ADAPTER}
    app.models.entry_path = lambda *_: path
    app.models.native_tasks.connector_capabilities = lambda _: {
        "host": {**producer, "runtime_instance_id": "current-game"},
        "game": game,
    }
    register = Mock(return_value={"status": "registered", "selection_id": "new-program"})
    app.local_model_registration = SimpleNamespace(register_global_program=register)
    assert desk._current_model_selection("old-program") == (
        "new-program" if changed else "old-program"
    )
    assert register.call_count == int(changed)
    assert json.loads(path.read_bytes()) == manifest  # Historical binding is unchanged.


def test_complete_catalog_presentation_retains_ids_and_uses_public_hand_target_and_cost():
    from spireagent.workbench.game_desk import present_choices

    catalog = [
        {"choice_id": "end", "kind": "combat.end_turn", "label": "End turn"},
        {
            "choice_id": "second",
            "kind": "combat.play_card",
            "subject": "card-2",
            "target": "enemy",
            "label": "raw-2",
        },
        {
            "choice_id": "first",
            "kind": "combat.play_card",
            "subject": "card-1",
            "target": None,
            "label": "raw-1",
        },
    ]
    observation = {
        "combat": {
            "content": {
                "player": {
                    "hand": [
                        {"entity_id": "card-1", "name": "防御", "cost": "1"},
                        {"entity_id": "card-2", "name": "打击", "cost": "1"},
                    ]
                },
                "enemies": [{"entity_id": "enemy", "name": "敌人A"}],
            }
        }
    }
    result = present_choices(catalog, observation, "second")
    assert [row["choice_id"] for row in result] == ["first", "second", "end"]
    assert (
        result[1]["cost"] == "1" and "打击" in result[1]["label"] and "敌人A" in result[1]["label"]
    )
    assert result[1]["model_selected"] is True
    assert {row["choice_id"] for row in result} == {row["choice_id"] for row in catalog}


def test_recording_index_and_separate_labels_roundtrip_through_the_real_store(
    tmp_path, monkeypatch
):
    from spireagent import source
    from spireagent.artifact_contracts import Producer
    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.registry import SQLiteRegistry
    from spireagent.storage.store import ManifestArtifactStore
    from spireagent.workbench import local_recording_import

    desk, app, owner, feedback, calls = app_fixture(tmp_path)
    desk.record_start()
    path = Path(feedback["recording_directory"]) / "p1-native-events.jsonl"
    path.write_bytes(json_bytes(native_row(feedback["session_id"])))
    desk.poll()
    desk.mark("需要核目标")
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    registry = SQLiteRegistry(tmp_path / "registry.sqlite")
    monkeypatch.setattr(
        local_recording_import, "_selected_store", lambda *a, **k: (store, registry)
    )
    # Only source provenance is a fixture; Store/manifest/payload readers are real.
    monkeypatch.setattr(
        source,
        "source_identity",
        lambda _: Producer("rsgcsg/STS2-The-Perfect-Defect-Project", "a" * 40, "b" * 64),
    )
    result = desk.record_stop()
    assert result["recovery_confirmed"] is True
    identity = desk._get("latest_artifact_id", None)
    read = desk.read_saved(identity)
    assert read["schema"] == "spireagent/game-desk-recording-1"
    step = read["steps"][0]
    assert step["actor"] == "human" and step["input_method"] == "mouse"
    assert step["ranking_binding"] == "original_native_input"
    assert step["annotations"][0]["note"] == "需要核目标"
    assert read["score_eligible"] is False
    assert (
        json.loads(
            b"".join(store.read_payload(store.get_manifest(identity).payload("ranking-labels")))
        )["schema"]
        == "spireagent/human-ranking-labels-1"
    )
