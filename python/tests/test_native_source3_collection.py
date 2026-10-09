"""Genuine Application admission/Close with explicit public Runtime report projections."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from collections import deque
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from spireagent.json_boundary import BoundaryError
from spireagent.workbench import native_source3_collection as module
from spireagent.workbench.developer import ProjectConfig, atomic_json, combination
from spireagent.workbench.developer_server import Application
from spireagent.workbench.instance_lock import instance_lock


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.config = ProjectConfig(
            self.root / "state", "", "http://127.0.0.1:15526", None, combination()
        )
        self.request = module.CollectionRequest(
            self.root / "game",
            self.root / "host",
            self.root / "output",
            "SYNTHETIC",
            target_choices=1,
            max_submissions=1,
        )
        self.commands, self.messages, self.apps, self.children, self.order = [], [], [], [], []
        self.behavior = {}
        self.cancel = module.Cancellation()
        self.status = {
            "runtime_instance_id": "runtime",
            "recording_session_id": None,
            "recording_lifecycle": "ready",
            "capture_profile_id": "none",
            "closeout_status": "ready",
            "source": None,
            "health": {"append_health": "healthy", "disk_health": "healthy", "error": None},
        }
        self.patches = []

    def tearDown(self):
        for value in reversed(self.patches):
            value.stop()
        self.temp.cleanup()

    def mock(self, owner, name, value):
        handle = patch.object(owner, name, value)
        handle.start()
        self.patches.append(handle)

    def application(self, cfg, path):
        app = Application(cfg, config_path=path)
        self.apps.append(app)
        self.mock(
            app.models,
            "status",
            lambda: {
                "loaded": self.behavior.get("model_loaded", False),
                "status": "idle",
                "operation": None,
            },
        )

        def owner_status(_):
            if not self.request.record_source3:
                raise AssertionError("Source3 opt-out must not query recording capability")
            return copy.deepcopy(self.status)

        def owner_command(endpoint, previous, kind, *, source_declaration, command_id):
            self.commands.append(kind)
            if kind == "close":
                self.order.append("source_close")
            if self.behavior.get(
                "unknown_" + ("start" if kind == "start_new_session" else "close")
            ):
                raise BoundaryError("recording", "native_recording_command_unknown")
            if kind == "start_new_session":
                self.status.update(
                    recording_session_id="source-session",
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
                self.status.update(recording_lifecycle="closed", closeout_status="closed")
            return {
                "accepted": True,
                "pending": False,
                "command_id": command_id,
                "status": copy.deepcopy(self.status),
            }

        self.mock(app.models.native_tasks, "recording_status", owner_status)
        self.mock(app.models.native_tasks, "recording_command", owner_command)
        return app

    def child(self, path, cancel):
        test = self

        class Child:
            def __init__(self):
                self.queue = deque()
                self.mid = 0
                self.operation = None
                self.result = None
                self.submissions = 0
                self.source_closed = False
                self.stopped = False
                self.runtime = {
                    "schema": "sts2.policy-runtime/agent-session-status-1",
                    "run_id": "run-fixture",
                    "lifecycle": "running",
                    "mode": "auto",
                    "controller": "released",
                    "tainted": False,
                    "session": {
                        "profile": "native-logical-v1",
                        "agent_state": "known",
                        "state_version": 0,
                    },
                    "environment": {"runtime_instance_id": "runtime"},
                    "pending_request": None,
                    "last_result": None,
                    "autonomy_budget": {"submissions_used": 0, "policy_calls_used": 0},
                }
                self.evidence = {
                    "schema": "sts2.policy-runtime/agent-session-run-1",
                    "run_id": "run-fixture",
                    "directory": str(test.root / "direct-evidence"),
                    "training_admission": "not_run",
                }

            def message(self, kind, **values):
                self.mid += 1
                self.queue.append(
                    {
                        "schema": module.PIPE_SCHEMA,
                        "type": kind,
                        "operation_id": self.operation,
                        "message_id": self.mid,
                        **values,
                    }
                )

            def quiesce(self, reason):
                test.order.append("runtime_stopped")
                self.runtime.update(lifecycle="stopped", mode="human", controller="released")
                self.message(
                    "quiesced",
                    reason=reason,
                    runtime_status=copy.deepcopy(self.runtime),
                    direct_evidence=self.evidence,
                    counts=self.counts(),
                    record_source3=test.request.record_source3,
                )

            def counts(self):
                return {
                    "result_messages": 1 if self.result else 0,
                    "known_delivered_choices": 1
                    if self.result and self.result["delivery"] == "delivered"
                    else 0,
                }

            def final(self, reason):
                summary = {
                    "schema": "spireagent/native-agent-runtime-summary-v1",
                    "public_status_schema": self.runtime["schema"],
                    "lifecycle": "stopped",
                    "mode": "human",
                    "controller": "released",
                    "tainted": self.runtime["tainted"],
                    "agent_state": "known",
                    "submissions_used": self.submissions,
                    "policy_calls_used": self.runtime["autonomy_budget"]["policy_calls_used"],
                    "state_version": self.runtime["session"]["state_version"],
                    "pending_request": self.runtime["pending_request"],
                    "last_result": self.result,
                }
                wire = {
                    "schema": module.PIPE_SCHEMA,
                    "type": "closed",
                    "operation_id": self.operation,
                    "reason": reason,
                    "counts": self.counts(),
                    "runtime_summary": summary,
                    "direct_evidence": self.evidence,
                    "teacher_exit": {
                        "pid": 12345,
                        "code": None,
                        "signal": "SIGKILL",
                        "actual_exit": True,
                    },
                    "source_closed": self.source_closed,
                    "record_source3": test.request.record_source3,
                    "control_release": {"confirmed": True, "runtime_instance_id": "runtime"},
                    "host_exit": {"code": 0, "signal": None, "forced": False},
                    "host_started": True,
                    "cleanup_errors": [],
                    "failure_details": None,
                    "partial_source_prefix": True,
                    "learned_evaluation": False,
                    "unknown_request_id_projection": (
                        "not_exposed_by_public_status_see_original_immutable_evidence"
                    )
                    if self.runtime["tainted"] and self.result is None
                    else None,
                }
                complete = {
                    **wire,
                    "schema": "spireagent/native-source3-collector-final-full-v2",
                    "runtime_status": self.runtime,
                }
                raw = json.dumps(complete).encode()
                (test.request.output / "collector-final-full.json").write_bytes(raw)
                wire["full_record_ref"] = {
                    "path": "collector-final-full.json",
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
                if test.behavior.get("bad_reference"):
                    wire["full_record_ref"]["sha256"] = "0" * 64
                self.queue.append(wire)

            def send(self, value):
                test.messages.append(copy.deepcopy(value))
                kind = value["type"]
                if kind == "init":
                    self.operation = value["operation_id"]
                    assert value["options"]["teacher_descriptor"]["agent_spec"]["learned"] is False
                    self.message(
                        "ready",
                        runtime_instance_id="runtime",
                        endpoint=test.config.platform_url,
                        host_identity={},
                        record_source3=test.request.record_source3,
                        bootstrap_control_release={
                            "schema": "sts2.host-runtime/reference-controller-handoff-1",
                            "runtime_instance_id": "runtime",
                            "controller": None,
                            "basis": "fresh_control_observation_after_close",
                        },
                    )
                elif kind == "source_ready":
                    if test.behavior.get("source_ready_failure"):
                        raise BoundaryError("pipe", "source_ready_transport_unknown")
                    if test.behavior.get("source_gap"):
                        test.status["source"]["gaps"] = 1
                    self.message("runtime_gate", status=self.runtime, direct_evidence=self.evidence)
                elif kind == "runtime_continue":
                    if not self.result and not self.submissions:
                        self.submissions = 1
                        self.runtime["autonomy_budget"] = {
                            "submissions_used": 1,
                            "policy_calls_used": 1,
                        }
                        self.runtime["session"]["state_version"] = 1
                        if test.behavior.get("unknown_submission"):
                            self.runtime["tainted"] = True
                            self.quiesce("original_runtime_outcome_unresolved")
                            return
                        self.result = {
                            "request_id": "original-request",
                            "snapshot_id": "snapshot",
                            "action_id": "original",
                            "status": "terminal",
                            "delivery": "delivered",
                            "execution": "unknown",
                            "effect": "unknown",
                            "cancel": "not_requested",
                            "reason": None,
                        }
                        self.runtime["last_result"] = self.result
                        self.message(
                            "runtime_tick",
                            tick={"type": "delivered", "status": self.runtime},
                            direct_evidence=self.evidence,
                        )
                    else:
                        self.quiesce("target_choices_reached")
                elif kind == "source_closed":
                    self.source_closed = (
                        value["known_closed"] or value["close_outcome"] == "not_requested"
                    )
                    self.final(
                        "original_runtime_outcome_unresolved"
                        if self.runtime["tainted"]
                        else "runtime_handoff"
                        if self.stopped
                        else "target_choices_reached"
                    )

            def receive(self):
                value = self.queue.popleft() if self.queue else None
                if value is not None and value.get("type") == "quiesced":
                    test.order.append("quiesced_received")
                return value

            def stop(self, reason):
                if not self.stopped:
                    self.stopped = True
                    self.quiesce(reason)

            def finish(self):
                return {"pid": 54321, "exit_code": 0, "signal": None, "forced_by_parent": False}

        child = Child()
        self.children.append(child)
        return child

    def preflight(self, config, request):
        request.validate()
        return {
            "child_path": str(self.root / "fixed-node.mjs"),
            "endpoint": config.platform_url,
            "options": request.options(config.platform_url),
        }

    def run_collection(self):
        return module.collect_source3(
            self.config,
            self.root / "config.json",
            self.request,
            app_factory=self.application,
            child_factory=self.child,
            preflight=self.preflight,
            cancel=self.cancel,
        )

    def test_genuine_App_admission_full_status_and_direct_evidence_are_separate_from_N(self):
        report = self.run_collection()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(self.commands, ["start_new_session", "close"])
        self.assertEqual(
            (report["submissions"], report["actual_choices"], report["known_delivered_choices"]),
            (1, 1, 1),
        )
        self.assertIsNone(report["eligible_unique_N"])
        self.assertEqual(report["admission"], "not_run")
        self.assertEqual(report["teacher_exit"]["signal"], "SIGKILL")
        self.assertEqual(
            report["child_final_full"]["runtime_status"]["last_result"]["effect"], "unknown"
        )
        self.assertFalse(
            any(value["type"] in {"current", "choice", "result"} for value in self.messages)
        )

    def test_source_opt_out_does_not_access_Recorder_and_still_retains_actor_trace(self):
        self.request = replace(self.request, record_source3=False)
        report = self.run_collection()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(self.commands, [])
        self.assertIsNone(report["source_closed"])
        self.assertEqual(report["direct_evidence"]["training_admission"], "not_run")

    def test_unknown_Start_admits_zero_Runtime_ticks_and_never_repeats(self):
        self.behavior["unknown_start"] = True
        report = self.run_collection()
        self.assertEqual(self.commands, ["start_new_session"])
        self.assertEqual(report["submissions"], 0)
        self.assertEqual(report["status"], "unknown")

    def test_unknown_Close_offered_once_and_diagnostic_request_failure_cannot_skip_it(self):
        original = module.atomic_json
        for unknown in [False, True]:
            with self.subTest(unknown=unknown):
                if self.request.output.exists():
                    import shutil

                    shutil.rmtree(self.request.output)
                    (self.config.state_dir / module.MARKER_FILE).unlink()
                self.commands.clear()
                self.behavior["unknown_close"] = unknown
                self.status.update(
                    recording_session_id=None,
                    source=None,
                    recording_lifecycle="ready",
                    closeout_status="ready",
                    capture_profile_id="none",
                )

                def write(path, value):
                    if path.name == "source-close-request.json":
                        raise OSError("synthetic diagnostic failure")
                    return original(path, value)

                with patch.object(module, "atomic_json", write):
                    report = self.run_collection()
                self.assertEqual(self.commands, ["start_new_session", "close"])
                self.assertTrue(report["close_sent"])
                self.assertEqual(
                    report["source_close_request_write_error"], "diagnostic_write_failed"
                )
                self.assertEqual(report["source_closed"], not unknown)
                self.assertEqual(report["status"], "unknown" if unknown else "completed")

    def test_source_accounting_failure_closes_known_original_before_another_tick(self):
        self.behavior["source_gap"] = True
        report = self.run_collection()
        self.assertEqual(report["error_code"], "original_source_accounting_failed")
        self.assertEqual(report["submissions"], 0)
        self.assertEqual(self.commands, ["start_new_session", "close"])
        self.assertLess(self.order.index("runtime_stopped"), self.order.index("quiesced_received"))
        self.assertLess(self.order.index("quiesced_received"), self.order.index("source_close"))

    def test_SourceReady_transport_failure_still_closes_accepted_original(self):
        self.behavior["source_ready_failure"] = True
        report = self.run_collection()
        self.assertEqual(self.commands, ["start_new_session", "close"])
        self.assertTrue(report["source_closed"])
        self.assertEqual(report["submissions"], 0)

    def test_transport_unknown_carries_public_projection_absence_not_fake_pending(self):
        self.behavior["unknown_submission"] = True
        report = self.run_collection()
        self.assertEqual(report["status"], "unknown")
        self.assertEqual((report["submissions"], report["actual_choices"]), (1, 0))
        self.assertIsNone(report["runtime_status"]["pending_request"])
        self.assertEqual(
            report["unknown_request_id_projection"],
            "not_exposed_by_public_status_see_original_immutable_evidence",
        )

    def test_full_private_final_must_match_SHA_and_cannot_select_another_path(self):
        self.behavior["bad_reference"] = True
        report = self.run_collection()
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["full_final_reporting_error"], "full_final_integrity_failed")
        self.assertIsNotNone(report["child_final"])
        self.assertTrue(report["child_final"]["source_closed"])
        self.assertEqual(report["teacher_exit"]["signal"], "SIGKILL")
        self.assertEqual(report["child_final"]["host_exit"]["code"], 0)
        self.assertEqual(self.commands.count("close"), 1)

    def test_instance_lock_or_unresolved_predecessor_blocks_before_App_or_child(self):
        with (
            instance_lock(self.config.state_dir / "instance.lock"),
            self.assertRaisesRegex(BoundaryError, "already_running"),
        ):
            self.run_collection()
        atomic_json(
            self.config.state_dir / module.MARKER_FILE,
            {"schema": module.REPORT_SCHEMA, "status": "unknown"},
        )
        with self.assertRaisesRegex(BoundaryError, "original_collection_outcome_unresolved"):
            self.run_collection()
        self.assertEqual(self.apps, [])

    def test_early_signals_or_nonquiescent_model_cannot_launch_teacher(self):
        self.cancel.stop("external_signal")
        self.cancel.stop("external_signal")
        with self.assertRaisesRegex(BoundaryError, "external_signal"):
            self.run_collection()
        self.assertEqual(self.children, [])
        self.cancel = module.Cancellation()
        self.behavior["model_loaded"] = True
        report = self.run_collection()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(self.children, [])

    def test_pipe_reader_EOF_drains_bounded_queue_without_leaking_process(self):
        script = self.root / "pure-child.mjs"
        script.write_text(
            'for (let i = 0; i < 8; i++) process.stdout.write(JSON.stringify({i}) + "\\n");'
        )
        child = module.OwnedPipeChild(script, module.Cancellation())
        receipt = child.finish()
        self.assertEqual(receipt["exit_code"], 0)
        self.assertTrue(receipt["reader_terminal"] and receipt["diagnostics_terminal"])


if __name__ == "__main__":
    unittest.main()


class CollectorCliTests(unittest.TestCase):
    def test_no_source3_is_explicit_and_default_remains_recording(self):
        from contextlib import redirect_stdout
        from io import StringIO

        from spireagent.workbench import developer_cli

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = ProjectConfig(
                root / "state", "", "http://127.0.0.1:15526", None, combination()
            )
            for omit in [False, True]:
                calls = []

                def collect(cfg, path, request, *, destination=calls):
                    destination.append(request)
                    return {"status": "partial"}

                arguments = [
                    "collect-source3",
                    "--game-directory",
                    str(root / "game"),
                    "--host-local-root",
                    str(root / "host"),
                    "--output",
                    str(root / "output"),
                    "--seed",
                    "SYNTHETIC",
                ]
                if omit:
                    arguments.append("--no-source3")
                with (
                    patch.object(ProjectConfig, "load", return_value=config),
                    patch.object(module, "collect_source3", collect),
                    redirect_stdout(StringIO()),
                ):
                    self.assertEqual(developer_cli.main(arguments), 0)
                self.assertEqual(calls[0].record_source3, not omit)
