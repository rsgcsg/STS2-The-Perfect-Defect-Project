"""Production collector: genuine Application admission with injected process/Recorder owners."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import sys
from collections import deque

import pytest
from test_native_public_teacher import action, observation

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.developer_server import Application
from spireagent.workbench.instance_lock import instance_lock
from spireagent.workbench.native_source3_collection import (
    PIPE_SCHEMA,
    Cancellation,
    CollectionRequest,
    OwnedPipeChild,
    collect_source3,
    decode_current,
)


@pytest.fixture
def collection(tmp_path, monkeypatch):
    config = ProjectConfig(tmp_path / "state", "", "http://127.0.0.1:15526", None, combination())
    request = CollectionRequest(
        tmp_path / "game",
        tmp_path / "host",
        tmp_path / "output",
        "SYNTHETIC",
        target_choices=1,
        max_submissions=1,
    )
    seen = {"commands": [], "apps": [], "children": [], "messages": [], "stops": [], "behavior": {}}
    current = {
        "runtime_instance_id": "runtime",
        "recording_session_id": None,
        "recording_lifecycle": "ready",
        "source": None,
        "capture_profile_id": "none",
        "closeout_status": "ready",
        "health": {"append_health": "healthy", "disk_health": "healthy", "error": None},
    }
    cancel = Cancellation()

    def app_factory(cfg, path):
        app = Application(cfg, config_path=path)
        seen["apps"].append(app)
        monkeypatch.setattr(
            app.models,
            "status",
            lambda: {
                "loaded": seen["behavior"].get("model_loaded", False),
                "status": "idle",
                "operation": None,
            },
        )
        monkeypatch.setattr(
            app.models.native_tasks, "recording_status", lambda _: copy.deepcopy(current)
        )

        def command(endpoint, before, kind, *, source_declaration, command_id):
            seen["commands"].append(kind)
            if seen["behavior"].get("unknown_start") and kind == "start_new_session":
                raise BoundaryError("recording", "native_recording_command_unknown")
            if seen["behavior"].get("unknown_close") and kind == "close":
                raise BoundaryError("recording", "native_recording_command_unknown")
            if kind == "start_new_session":
                current.update(
                    recording_session_id="session",
                    recording_lifecycle="recording",
                    closeout_status="recording",
                    capture_profile_id="native-logical-source-v3",
                    source={
                        "segment_id": "segment",
                        "epoch_id": "epoch",
                        "declaration": source_declaration,
                        "accounting_complete": True,
                        "gaps": 0,
                        "error": None,
                        "inputs": 0,
                    },
                )
            elif kind == "close":
                current.update(recording_lifecycle="closed", closeout_status="closed")
            return {
                "accepted": True,
                "pending": False,
                "command_id": command_id,
                "status": copy.deepcopy(current),
            }

        monkeypatch.setattr(app.models.native_tasks, "recording_command", command)
        return app

    class Child:
        def __init__(self, path, cancellation):
            self.cancel, self.queue, self.mid = cancellation, deque(), 0
            self.source_closed = False
            seen["children"].append(self)

        def message(self, kind, **payload):
            self.mid += 1
            self.queue.append(
                {
                    "schema": PIPE_SCHEMA,
                    "type": kind,
                    "operation_id": self.operation,
                    "message_id": self.mid,
                    **payload,
                }
            )

        def final(self, reason="target_choices_reached"):
            value = {
                "schema": PIPE_SCHEMA,
                "type": "closed",
                "operation_id": self.operation,
                "reason": reason,
                "counts": {
                    "submissions": self.choices,
                    "known_delivered_choices": self.choices,
                    "result_queries": 0,
                },
                "source_closed": self.source_closed,
                "control_release": {"confirmed": True},
                "host_exit": {"code": 0, "signal": None, "forced": False},
                "host_started": True,
                "cleanup_errors": [],
                "advisory": {"history_claimed": False, "eager_scope": []},
                "live_byte_reservations": 0,
                "partial_source_prefix": True,
                "learned_evaluation": False,
            }
            if seen["behavior"].get("bad_final"):
                value["counts"]["submissions"] = 999
            self.queue.append(value)

        def send(self, value):
            seen["messages"].append(copy.deepcopy(value))
            kind = value["type"]
            if kind == "init":
                self.operation, self.choices = value["operation_id"], 0
                self.message(
                    "ready",
                    runtime_instance_id="runtime",
                    endpoint=config.platform_url,
                    host_identity={"host": {"runtime_instance_id": "runtime"}},
                    bootstrap_control_release={
                        "schema": "sts2.host-runtime/reference-controller-handoff-1",
                        "runtime_instance_id": "runtime",
                        "controller": None,
                        "basis": "fresh_control_observation_after_close",
                    },
                )
            elif kind == "source_ready":
                if seen["behavior"].get("source_ready_failure"):
                    raise BoundaryError("pipe", "source_ready_transport_unknown")
                if seen["behavior"].get("source_gap"):
                    current["source"]["gaps"] = 1
                if seen["behavior"].get("source_paused"):
                    current["recording_lifecycle"] = "paused"
                if seen["behavior"].get("storage_failed"):
                    current["health"]["append_health"] = "failed"
                if seen["behavior"].get("double_signal"):
                    self.cancel.stop("external_signal")
                    self.cancel.stop("external_signal")
                    return
                self.actions = [action("browse", "open_run_deck")]
                self.view = observation(self.actions)
                raw = json.dumps(self.view).encode()
                self.basis = {
                    "capture_id": "capture",
                    "snapshot_id": "snapshot",
                    "runtime_instance_id": "runtime",
                    "stream_generation": "generation",
                    "capture_sha256": hashlib.sha256(raw).hexdigest(),
                    "byte_count": len(raw),
                    "catalog_digest": self.view["catalog"]["digest"],
                    "total_count": 1,
                }
                self.message(
                    "current",
                    ordinal=1,
                    basis=self.basis,
                    observation_base64=base64.b64encode(raw).decode(),
                    catalog=self.actions,
                )
            elif kind == "choice":
                assert value["action_id"] == "browse"
                self.choices += 1
                self.message(
                    "result",
                    ordinal=1,
                    request_id="original-request",
                    lookup_status="terminal",
                    result={
                        "request_id": "original-request",
                        "snapshot_id": "snapshot",
                        "action": self.actions[0],
                        "delivery": "delivered",
                        "execution": "unknown",
                        "effect": "unknown",
                    },
                    result_queries=0,
                )
            elif kind == "continue":
                self.message(
                    "quiesced",
                    reason="target_choices_reached",
                    counts={"submissions": self.choices},
                    pending_request_id=None,
                )
            elif kind == "source_closed":
                self.source_closed = value["known_closed"]
                self.final("external_signal" if seen["stops"] else "target_choices_reached")

        def receive(self):
            return self.queue.popleft() if self.queue else None

        def stop(self, reason):
            seen["stops"].append(reason)
            if len(seen["stops"]) == 1:
                if current["recording_session_id"] is None:
                    self.final("original_source_start_unconfirmed")
                else:
                    self.message(
                        "quiesced",
                        reason=reason,
                        counts={"submissions": self.choices},
                        pending_request_id=None,
                    )

        def finish(self):
            if seen["behavior"].get("source_ready_failure"):
                assert seen["commands"][-1] == "close"
                assert self.source_closed
            return {"pid": 12345, "exit_code": 0, "signal": None, "forced_by_parent": False}

    def prepared(cfg, req):
        req.validate()
        return {
            "child_path": str(tmp_path / "fixed-child.mjs"),
            "endpoint": cfg.platform_url,
            "options": req.options(cfg.platform_url),
        }

    def run():
        return collect_source3(
            config,
            tmp_path / "config.json",
            request,
            app_factory=app_factory,
            child_factory=Child,
            preflight=prepared,
            cancel=cancel,
        )

    return config, request, seen, cancel, run


def test_genuine_application_shared_start_close_and_independent_N(collection):
    _, request, seen, _, run = collection
    report = run()
    assert report["status"] == "completed"
    assert seen["commands"] == ["start_new_session", "close"]
    assert report["actual_choices"] == report["submissions"] == 1
    assert report["eligible_unique_N"] is None and report["admission"] == "not_run"
    assert report["partial_source_prefix"] and not report["learned_evaluation"]
    assert (
        report["source_start"]["status"]["source"]["declaration"]["source_kind"] == "agent_protocol"
    )
    assert isinstance(seen["apps"][0], Application)
    original = json.loads((request.output / "original-result-0001.json").read_text())
    assert original["result"]["effect"] == "unknown"
    assert "torch" not in sys.modules
    assert report["source_final_status"]["source"]["inputs"] == 0


@pytest.mark.parametrize(
    "failure",
    [
        "unknown_start",
        "unknown_close",
        "source_ready_failure",
        "source_gap",
        "double_signal",
        "source_paused",
        "storage_failed",
    ],
)
def test_original_unknown_and_cancellation_never_repeat_start_close(collection, failure):
    _, _, seen, cancel, run = collection
    seen["behavior"][failure] = True
    report = run()
    assert seen["commands"].count("start_new_session") == 1
    assert seen["commands"].count("close") <= 1
    assert report["actual_choices"] == (1 if failure == "unknown_close" else 0)
    if failure == "unknown_start":
        assert seen["commands"] == ["start_new_session"]
        assert report["status"] == "unknown"
    elif failure == "source_gap":
        assert report["error_code"] == "original_source_accounting_failed"
        assert report["source_closed"]
    elif failure == "source_ready_failure":
        assert report["source_closed"] and seen["commands"][-1] == "close"
    elif failure == "double_signal":
        assert cancel.signals == 2 and report["source_closed"]
    elif failure == "unknown_close":
        assert not report["source_closed"] and report["status"] == "unknown"


def test_instance_lock_blocks_before_application_or_child_construction(collection):
    config, _, seen, _, run = collection
    with (
        instance_lock(config.state_dir / "instance.lock"),
        pytest.raises(BoundaryError, match="already_running"),
    ):
        run()
    assert seen["apps"] == seen["children"] == []


def test_early_cancellation_no_child_or_start(collection):
    _, _, seen, cancel, run = collection
    cancel.stop("external_signal")
    cancel.stop("external_signal")
    with pytest.raises(BoundaryError, match="external_signal"):
        run()
    assert seen["apps"] == seen["children"] == seen["commands"] == []


def test_final_counts_cannot_create_fake_choices(collection):
    _, _, seen, _, run = collection
    seen["behavior"]["bad_final"] = True
    report = run()
    assert report["status"] == "unknown"
    assert report["error_code"] == "child_counts_disagree"
    assert report["actual_choices"] == 1


def test_pipe_reader_does_not_block_on_full_queue_at_eof(tmp_path):
    script = tmp_path / "pure-child.mjs"
    script.write_text(
        'for (let i = 0; i < 8; i++) process.stdout.write(JSON.stringify({i}) + "\\n");'
    )
    child = OwnedPipeChild(script, Cancellation())
    receipt = child.finish()
    assert receipt["exit_code"] == 0
    assert receipt["reader_terminal"] and receipt["diagnostics_terminal"]


def test_original_current_bytes_digest_and_basis_validation():
    actions = [action("original", "open_run_deck")]
    view = observation(actions)
    raw = json.dumps(view).encode()
    basis = {
        "capture_id": "capture",
        "snapshot_id": "snapshot",
        "runtime_instance_id": "runtime",
        "stream_generation": "generation",
        "capture_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_count": len(raw),
        "catalog_digest": view["catalog"]["digest"],
        "total_count": 1,
    }
    message = {
        "basis": basis,
        "observation_base64": base64.b64encode(raw).decode(),
        "catalog": actions,
    }
    assert decode_current(message, "runtime", 1024 * 1024) == (view, actions)
    basis["capture_sha256"] = "0" * 64
    with pytest.raises(BoundaryError, match="complete_current_integrity_failed"):
        decode_current(message, "runtime", 1024 * 1024)


@pytest.mark.parametrize("plan_only", [False, True])
def test_project_cli_routes_explicit_collect_request_without_gui_or_server(
    collection, monkeypatch, capsys, plan_only
):
    from spireagent.workbench import developer_cli
    from spireagent.workbench import native_source3_collection as module

    config, request, _, _, _ = collection
    monkeypatch.setattr(ProjectConfig, "load", lambda *args, **kwargs: config)
    calls = []

    def plan(cfg, req):
        calls.append(("plan", cfg, req))
        return {"status": "plan"}

    def collect(cfg, path, req):
        calls.append(("collect", cfg, req))
        return {"status": "partial", "eligible_unique_N": None}

    monkeypatch.setattr(module, "metadata_preflight", plan)
    monkeypatch.setattr(module, "collect_source3", collect)
    arguments = [
        "collect-source3",
        "--game-directory",
        str(request.installation),
        "--host-local-root",
        str(request.host_local_root),
        "--output",
        str(request.output),
        "--seed",
        request.seed,
        "--target-choices",
        "1",
        "--max-submissions",
        "2",
    ]
    if plan_only:
        arguments.append("--plan-only")
    assert developer_cli.main(arguments) == 0
    assert calls[0][0] == ("plan" if plan_only else "collect")
    assert calls[0][1] is config
    assert calls[0][2].target_choices == 1 and calls[0][2].max_submissions == 2
    assert json.loads(capsys.readouterr().out)["status"] == ("plan" if plan_only else "partial")


def test_predecessor_unknown_after_preflight_still_blocks_before_child(collection):
    from spireagent.workbench.developer import atomic_json
    from spireagent.workbench.native_source3_collection import MARKER_FILE, REPORT_SCHEMA

    config, _, seen, _, run = collection
    atomic_json(config.state_dir / MARKER_FILE, {"schema": REPORT_SCHEMA, "status": "unknown"})
    # The injected read-only preflight intentionally has no marker check; the
    # production lifetime's inside-lock check must independently reject it.
    with pytest.raises(BoundaryError, match="original_collection_outcome_unresolved"):
        run()
    assert seen["children"] == seen["apps"] == []


def test_read_only_preflight_freezes_teacher_and_child_without_application_or_launch(
    collection, monkeypatch
):
    from spireagent.workbench import native_source3_collection as module

    config, request, seen, _, _ = collection
    monkeypatch.setattr(module, "tool_identity", lambda: {"working_tree_clean": False})
    with pytest.raises(BoundaryError, match="clean_collection_source_required"):
        module.metadata_preflight(config, request)
    monkeypatch.setattr(module, "tool_identity", lambda: {"working_tree_clean": True})
    report = module.metadata_preflight(config, request)
    assert report["status"] == "plan"
    assert len(report["teacher"]["sha256"]) == len(report["child_sha256"]) == 64
    assert report["acquisition"]["eager_event_fields"] == []
    assert report["partial_source_prefix"] and report["eligible_unique_N"] is None
    assert not request.output.exists()
    assert seen["children"] == seen["apps"] == []


def test_nonquiescent_model_failure_finishes_marker_without_start_or_child(collection):
    _, _, seen, _, run = collection
    seen["behavior"]["model_loaded"] = True
    report = run()
    assert report["status"] == "failed"
    assert report["error_code"] == "model_owner_not_quiescent"
    assert seen["commands"] == seen["children"] == []


def test_initial_request_write_failure_marks_failed_before_any_child(collection, monkeypatch):
    from spireagent.workbench import native_source3_collection as module

    _, request, seen, _, run = collection
    original_write = module.atomic_json

    def write(path, value):
        if path.name == "request.json":
            raise OSError("synthetic output failure")
        original_write(path, value)

    monkeypatch.setattr(module, "atomic_json", write)
    report = run()
    assert report["status"] == "failed"
    assert seen["commands"] == seen["children"] == seen["apps"] == []
    assert json.loads((request.output / "report.json").read_text())["status"] == "failed"
