from __future__ import annotations

import copy
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.workbench.mixed_runs import (
    BLOCKED,
    DEFAULT_SETTINGS,
    MixedRunApplication,
    environment_index,
    preview,
    settings,
)


def pending():
    return {
        "id": "review-1",
        "epoch": 2,
        "detail": {
            "acquisition_id": "a1",
            "choice_id": "c1",
            "observation": {
                "catalog": [
                    {"choice_id": "c1", "subject": "node", "label": "go"},
                    {"choice_id": "c2", "label": "skip"},
                ],
                "scene": {
                    "native_kind": "map_navigation",
                    "content": {
                        "surface": {"next_options": [{"entity_id": "node", "point_type": "boss"}]}
                    },
                },
            },
            "directive": {"scores": {"values": [2, 1]}},
        },
    }


def test_preview_uses_complete_original_scores_and_public_boss_node():
    result = preview(pending())
    assert result["before_boss"] is True
    assert sum(result["model_probabilities"]) == pytest.approx(1)
    assert result["confidence"] == pytest.approx(0.7310585786)
    assert result["probability_meaning"].endswith("not_win_probability")
    invalid = pending()
    invalid["detail"]["directive"]["scores"]["values"] = [2]
    assert preview(invalid)["confidence"] is None


@pytest.mark.parametrize(
    "edit",
    [
        {"delay_ms": -1},
        {"delay_ms": True},
        {"single_step": 1},
        {"confidence_below": float("nan")},
        {"confidence_below": 1.1},
    ],
)
def test_settings_reject_invalid_boundaries(edit):
    with pytest.raises(BoundaryError):
        settings(DEFAULT_SETTINGS | edit)


def test_index_keeps_missing_witness_unknown_and_declaration_cannot_change_actor(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    manifest = {
        "session_id": "s",
        "timeline_id": "t",
        "p1_native_origin": "unknown",
        "mixed_run_id": "a" * 32,
    }
    (raw / "recording-manifest.json").write_bytes(json_bytes(manifest))
    rows = [
        {
            "sequence": 1,
            "kind": "decision_accepted",
            "decision_id": "native-1",
            "basis": {"snapshot_id": "s1"},
            "native_witness": {"origin": "native_ui"},
            "missing_reason": None,
        },
        {
            "sequence": 2,
            "kind": "decision_accepted",
            "decision_id": "native-2",
            "source_kind": "declared_human",
            "basis": None,
            "native_witness": None,
            "missing_reason": "unproved",
        },
    ]
    encoded = b"".join(map(json_bytes, rows))
    (raw / "p1-native-events.jsonl").write_bytes(encoded)
    (raw / "run-journal.jsonl").write_bytes(b"")
    cut = {
        "session_id": "s",
        "timeline_id": "t",
        "watermark": {
            "events_byte_count": len(encoded),
            "events_sha256": hashlib.sha256(encoded).hexdigest(),
            "journal_byte_count": 0,
            "journal_sha256": hashlib.sha256(b"").hexdigest(),
        },
    }
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    dispatch = {
        "kind": "global_dispatch",
        "sequence": 10,
        "payload": {
            "request_id": "r1",
            "recovery_epoch": 2,
            "source_ordinal": 3,
            "choice_id": "c1",
            "bound_action": {"bound_action_id": "action"},
            "expected_snapshot_id": "s3",
        },
    }
    receipt = {
        "kind": "global_receipt",
        "sequence": 11,
        "payload": {"request_id": "r1", "receipt": {"delivery": "unknown", "request_id": "r1"}},
    }
    (runtime / "events.jsonl").write_bytes(b"".join(map(json_bytes, [dispatch, receipt])))
    index = environment_index(raw, {"run": runtime}, [{"cut": cut}])
    assert [row["actor"] for row in index["native_operations"]] == ["native_operator", "unknown"]
    assert index["model_operations"][0]["delivery"] == "unknown"
    assert index["model_operations"][0]["actor"] == "model"
    assert index["model_operations"][0]["outcome_unknown"] is True
    assert index["native_operations"][0]["outcome_unknown"] is True
    assert index["score_eligible"] is False and index["training_admission"] == BLOCKED
    assert "source_kind" not in json.dumps(index)
    (raw / "p1-native-events.jsonl").write_bytes(encoded.replace(b"native-1", b"native-X"))
    with pytest.raises(BoundaryError, match="cut_hash_mismatch"):
        environment_index(raw, {"run": runtime}, [{"cut": cut}])


class NativeOwner:
    def __init__(self):
        self.owner = {
            "runtime_instance_id": "game",
            "recording_session_id": None,
            "recording_lifecycle": "ready",
            "health": {"append_health": "healthy", "disk_health": "healthy", "error": None},
        }
        self.calls = []

    def recording_status(self, endpoint):
        return copy.deepcopy(self.owner)

    def recording_command(self, endpoint, observed, kind, **kwargs):
        self.calls.append((kind, kwargs))
        if kind == "close":
            self.owner["recording_lifecycle"] = "closed"
        cut = {
            "schema": "sts2.human-annotator/p1-native-checkpoint-1",
            "session_id": "s",
            "timeline_id": "t",
            "watermark": {"pending_count": 0},
        }
        return {
            "accepted": True,
            "pending": False,
            "status": copy.deepcopy(self.owner),
            "p1_native_checkpoint": cut,
        }


@pytest.fixture
def application(tmp_path):
    native = NativeOwner()
    runtime = {
        "mode": "human",
        "controller": "released",
        "run_id": "run",
        "session": {"profile": "p1-global-r1", "recovery_epoch": 0},
        "environment": {"runtime_instance_id": "game"},
        "lifecycle": "running",
    }

    class Client:
        startup = {"run_id": "run"}

        def request(self, route, *args, **kwargs):
            return {"status": copy.deepcopy(runtime)}

    models = SimpleNamespace(
        lock=threading.RLock(),
        client=Client(),
        state={"loaded": True, "runtime": runtime},
        native_tasks=native,
        _agent_evidence_expected=lambda x: x,
        directory=tmp_path / "models",
        process=None,
        thread=None,
    )
    app = SimpleNamespace(
        models=models,
        config=SimpleNamespace(state_dir=tmp_path, platform_url="http://game"),
        native_recording_status=lambda: {"status": native.recording_status(None)},
    )

    def start(body):
        assert body["mixed_run_id"]
        native.owner.update(recording_lifecycle="recording", recording_session_id="s")
        return {"accepted": True, "status": native.recording_status(None)}

    app.control_native_recording = start
    mixed = MixedRunApplication(app)
    models.command = lambda action: mixed.after_control(action, runtime | {"lifecycle": "stopped"})
    return mixed, models, native, runtime


def test_start_is_explicit_and_does_not_relabel_existing_human_recording(application):
    mixed, models, native, runtime = application
    native.owner["recording_lifecycle"] = "recording"
    with pytest.raises(BoundaryError, match="do_not_relabel"):
        mixed.start()
    assert mixed.status()["status"] == "idle"


def test_start_rejects_runtime_that_already_acted(application):
    mixed, models, native, runtime = application
    runtime["last_observation"] = {"acquisition_id": "prior"}
    with pytest.raises(BoundaryError, match="load_reference_p1_model_in_human_first"):
        mixed.start()


@pytest.mark.parametrize("model_stopped", [False, True])
def test_finish_closes_the_same_recording_even_if_stop_clears_client_before_observer(
    application, model_stopped
):
    mixed, models, native, runtime = application
    assert mixed.start()["session_id"] == "s"
    if model_stopped:
        models.client = None
    mixed.finish()
    assert mixed.status()["status"] == "closing"
    assert [kind for kind, _ in native.calls].count("close") == 1
    assert mixed.status()["model_runs"] == ["run"]


def test_q_scope_never_makes_a_mixed_source_an_ordinary_training_original(tmp_path):
    from spireagent.workbench.p1_recording_store import publish_p1_original

    tool = SimpleNamespace(
        verify_p1_native=lambda _: {
            "manifest": {"content_identity": {}},
            "recording": {"mixed_run_id": "a" * 32},
        }
    )
    with pytest.raises(BoundaryError, match="mixed_run_requires_isolated_intake"):
        publish_p1_original(None, tmp_path, tool, None, scratch=tmp_path)


def test_q_stops_the_original_once_when_game_exited_before_model_observer(application):
    mixed, models, native, runtime = application
    mixed.start()
    offered = []
    models.process = SimpleNamespace(poll=lambda: None)
    models.command = offered.append
    native.recording_status = lambda _: (_ for _ in ()).throw(
        BoundaryError("recording", "game_exited")
    )
    candidate = {"session_id": "s"}
    assert mixed.prepare_for_intake(candidate) is False
    assert mixed.prepare_for_intake(candidate) is False
    assert offered == ["stop"]
    mixed.after_control("stop", runtime | {"lifecycle": "stopped"})
    models.process = None
    assert mixed.prepare_for_intake(candidate) is True
    assert mixed.status()["status"] == "finishing"


@pytest.mark.parametrize("failure_at", [2, 3])
def test_confirmed_stop_survives_game_exit_during_cut_or_close(application, failure_at):
    mixed, models, native, runtime = application
    mixed.start()
    mixed.current["status"] = "finishing"
    original = native.recording_status
    reads = 0

    def racing_owner(endpoint):
        nonlocal reads
        reads += 1
        if reads >= failure_at:
            raise BoundaryError("recording", "game_exited")
        return original(endpoint)

    native.recording_status = racing_owner
    mixed.after_control("stop", runtime | {"lifecycle": "stopped"})
    assert mixed.status()["status"] == "finishing"
    assert [kind for kind, _ in native.calls].count("close") == 0


def test_unknown_approval_is_offered_once_then_human_recovery_without_reposting(application):
    mixed, models, native, runtime = application
    mixed.start()
    mixed.configure(DEFAULT_SETTINGS | {"delay_ms": 0})
    runtime.update(mode="auto")
    mutations = []
    recovery = []

    class LostReply:
        startup = {"run_id": "run"}

        def request(self, route, body=None, **kwargs):
            if route == "/environment":
                return {
                    "schema": "sts2.policy-runtime/environment-1",
                    "run_id": "run",
                    "runtime_instance_id": "game",
                    "recovery_epoch": 2,
                }
            if body is not None:
                mutations.append(body)
                raise BoundaryError("local_model", "runtime_command_unknown")
            return {"review": {"enabled": True, "pending": pending()}}

    models.command = recovery.append
    client = LostReply()
    with pytest.raises(BoundaryError, match="runtime_command_unknown"):
        mixed.observe(client, runtime)
    mixed.observe(client, runtime)
    mixed.observe(client, runtime)
    assert mutations == [{"approve": "review-1"}]
    assert recovery == ["human"]
    assert mixed.current["review_approvals"]["review-1"] == "unknown"


def test_review_transport_accepts_supported_catalog_over_one_mib_without_relaxing_status():
    from spireagent.workbench.local_models import RuntimeClient

    body = json_bytes(
        {
            "schema": "sts2.policy-runtime/http-2",
            "run_id": "run",
            "review": {
                "enabled": True,
                "pending": {
                    "detail": {
                        "catalog": [{"choice_id": str(i), "label": "x" * 1024} for i in range(1100)]
                    }
                },
            },
        }
    )
    assert 1024**2 < len(body) < 8 * 1024**2
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = RuntimeClient(f"http://127.0.0.1:{server.server_port}", {"run_id": "run"})
        assert len(client.request("/review")["review"]["pending"]["detail"]["catalog"]) == 1100
        with pytest.raises(BoundaryError):
            client.request("/status")
        assert calls == ["/review", "/status"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
