"""Genuine Application admission/Close with explicit public Runtime report projections."""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import deque
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from metadata_import_guard import no_torch_imports as no_torch_imports

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
                if self.behavior.get("reuse_source"):
                    old = self.behavior["reuse_source"]
                    self.status["recording_session_id"] = old[0]
                    self.status["source"].update(segment_id=old[1], epoch_id=old[2])
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
                if test.request.execution_policy is not None:
                    self.runtime.update(
                        agent_manifest_sha256="a" * 64,
                        runtime={"version": "fixture", "code_sha256": "b" * 64},
                        agent={
                            "adapter": {
                                "id": "fixture",
                                "version": "1",
                                "protocol": "fixture",
                                "code_sha256": "c" * 64,
                            }
                        },
                    )
                    self.evidence.update(
                        directory=str(test.request.output / "agent-runs" / self.runtime["run_id"]),
                        manifest_id="fixture",
                        artifact_sha256="d" * 64,
                    )
                if test.behavior.get("fresh"):
                    self.runtime["run_id"] = "fresh-run-fixture"
                    self.runtime["session"].update(
                        consumption_id=None,
                        continuity_token="fresh-continuity",
                        stream_generation="fresh-stream",
                    )
                    self.evidence["run_id"] = "fresh-run-fixture"
                    if test.behavior.get("warm_state"):
                        self.runtime["session"]["state_version"] = 1

            def message(self, kind, **values):
                self.mid += 1
                self.queue.append(
                    {
                        "schema": test.request.pipe_schema,
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
                    "schema": test.request.pipe_schema,
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
                if test.request.execution_policy is not None:
                    wire.update(
                        execution_policy=test.request.policy(),
                        terminal_summary=copy.deepcopy(test.behavior.get("terminal_summary")),
                        counter_proof_error=test.behavior.get("counter_proof_error"),
                    )
                    if wire["terminal_summary"] is None:
                        wire["cleanup_errors"].append("terminal_counter_proof_unavailable")
                complete = {
                    **wire,
                    "schema": "spireagent/native-source3-collector-final-full-v3"
                    if test.request.execution_policy is not None
                    else "spireagent/native-source3-collector-final-full-v2",
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
                    if test.request.execution_policy is not None:
                        artifact = value["options"]["teacher_artifact"]
                        self.evidence.update(
                            artifact_sha256=artifact["sha256"],
                            manifest_id="native-program-teacher-" + artifact["sha256"][:16],
                        )
                        self.runtime["agent"]["adapter"] = value["options"]["teacher_descriptor"][
                            "adapter"
                        ]
                    assert value["options"]["teacher_descriptor"]["agent_spec"]["learned"] is False
                    self.message(
                        "ready",
                        runtime_instance_id="runtime",
                        endpoint=test.config.platform_url,
                        host_identity=copy.deepcopy(test.behavior.get("host_identity", {})),
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
        prepared = {
            "child_path": str(self.root / "fixed-node.mjs"),
            "endpoint": config.platform_url,
            "options": request.options(config.platform_url),
        }
        if request.execution_policy is not None:
            prepared["promotion_gate"] = {
                "installed_terminal_summary_available": True,
                "closed_producer_relation": (
                    {"id": "synthetic_closed_relation"} if request.record_source3 else None
                ),
            }
        return prepared

    def run_collection(self, *, terminal_verifier=module.verify_terminal_summary):
        return module.collect_source3(
            self.config,
            self.root / "config.json",
            self.request,
            app_factory=self.application,
            child_factory=self.child,
            preflight=self.preflight,
            terminal_verifier=terminal_verifier,
            cancel=self.cancel,
        )

    def owned_proof(self):
        self.request = replace(self.request, execution_policy=module.OWNED_EXECUTION_POLICY)
        summary = {
            "schema": "sts2.evidence/agent-session-terminal-summary-1",
            "run_id": "run-fixture",
            "content_id": "f" * 64,
            "original_submission_count": 1,
            "terminal_result_count": 1,
            "known_delivered": 1,
            "known_stale_rejections": 0,
            "consecutive_known_stale_rejections": 0,
            "proof_scope": "recorded_dispatch_and_terminal_results",
            "live_eligibility_proved": False,
        }
        self.behavior["terminal_summary"] = copy.deepcopy(summary)
        self.mock(module, "installed_terminal_summary_available", lambda: True)
        self.mock(
            module, "closed_collection_producer", lambda *args: {"id": "synthetic_closed_relation"}
        )
        return summary

    def test_v3_parent_reverifies_exact_owned_final_evidence_and_keeps_N_separate(self):
        summary, calls = self.owned_proof(), []

        def verifier(directory, expected, policy):
            calls.append((directory, expected, policy))
            self.assertEqual(directory, self.request.output / "agent-runs/run-fixture")
            self.assertEqual(
                expected["agent_artifact_sha256"], self.children[0].evidence["artifact_sha256"]
            )
            self.assertEqual(policy, self.request.policy())
            return copy.deepcopy(summary)

        report = self.run_collection(terminal_verifier=verifier)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["schema"], module.OWNED_REPORT_SCHEMA)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["budget_submissions_used"], 1)
        self.assertEqual(report["terminal_result_count"], 1)
        self.assertEqual(report["known_delivered_terminal_count"], 1)
        self.assertEqual(report["known_stale_rejections"], 0)
        self.assertFalse(report["terminal_summary"]["live_eligibility_proved"])
        self.assertIsNone(report["eligible_unique_N"])
        self.assertEqual(report["admission"], "not_run")
        self.assertTrue(all(value["schema"] == module.OWNED_PIPE_SCHEMA for value in self.messages))

    def test_v3_source_off_diagnostic_retains_exact_actor_and_public_counter_proof(self):
        summary, verified = self.owned_proof(), []
        self.request = replace(self.request, record_source3=False)

        def verifier(directory, expected, policy):
            verified.append((directory, expected, policy))
            return copy.deepcopy(summary)

        with patch.object(module, "closed_collection_producer", side_effect=AssertionError(
            "Source-off cannot obtain authority from a research-admission relation"
        )):
            report = self.run_collection(terminal_verifier=verifier)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(len(verified), 1)
        self.assertEqual(report["terminal_summary"], summary)
        self.assertEqual(report["promotion_gate"]["closed_producer_relation"], None)
        self.assertEqual(report["options"]["teacher_descriptor"], module.descriptor(
            execution_policy=self.request.policy()))
        self.assertEqual(self.commands, [])
        self.assertIsNone(report["source_start"])
        self.assertIsNone(report["eligible_unique_N"])
        self.assertEqual(report["admission"], "not_run")

    def test_v3_preflight_actor_drift_is_rejected_with_and_without_source_before_side_effects(self):
        self.owned_proof()
        for record_source in (True, False):
            self.request = replace(self.request, record_source3=record_source)

            def changed_preflight(config, request):
                prepared = self.preflight(config, request)
                prepared["options"]["teacher_descriptor"]["adapter"]["code_sha256"] = "0" * 64
                return prepared

            with self.subTest(record_source=record_source), self.assertRaisesRegex(
                BoundaryError, "collection_teacher_preflight_changed"
            ):
                module.collect_source3(
                    self.config, self.root / "config.json", self.request,
                    app_factory=self.application, child_factory=self.child,
                    preflight=changed_preflight, cancel=self.cancel,
                )
            self.assertEqual(self.apps, [])
            self.assertEqual(self.children, [])
            self.assertEqual(self.commands, [])
            self.assertFalse(self.request.output.exists())

    def test_v3_parent_rejects_a_child_counter_claim_that_disagrees_with_the_public_owner(self):
        summary = self.owned_proof()
        self.behavior["terminal_summary"].update(known_delivered=0, known_stale_rejections=1)
        report = self.run_collection(terminal_verifier=lambda *args: summary)
        self.assertNotEqual(report["status"], "completed")
        self.assertEqual(report["error_code"], "terminal_counter_proof_disagrees")
        self.assertIsNone(report["terminal_result_count"])
        self.assertEqual(self.commands, ["start_new_session", "close"])

    def test_v3_source_off_counter_mismatch_cannot_become_success_or_admitted_N(self):
        summary = self.owned_proof()
        self.request = replace(self.request, record_source3=False)
        self.behavior["terminal_summary"].update(known_delivered=0, known_stale_rejections=1)
        report = self.run_collection(terminal_verifier=lambda *args: summary)
        self.assertNotEqual(report["status"], "completed")
        self.assertEqual(report["error_code"], "terminal_counter_proof_disagrees")
        self.assertIsNone(report["terminal_result_count"])
        self.assertIsNone(report["eligible_unique_N"])
        self.assertEqual(report["admission"], "not_run")
        self.assertEqual(self.commands, [])

    def test_v3_missing_terminal_proof_retains_actual_cleanup_and_unavailable_counters(self):
        self.owned_proof()
        self.behavior.update(
            terminal_summary=None, counter_proof_error="original_evidence_finalize_failed"
        )
        report = self.run_collection(
            terminal_verifier=lambda *args: self.fail("unavailable proof must not be synthesized")
        )
        self.assertNotEqual(report["status"], "completed")
        self.assertTrue(report["source_closed"])
        self.assertTrue(report["child_final"]["control_release"]["confirmed"])
        self.assertEqual(report["counter_proof_error"], "original_evidence_finalize_failed")
        self.assertIsNone(report["terminal_result_count"])
        self.assertIsNone(report["known_stale_rejections"])
        self.assertEqual(self.commands, ["start_new_session", "close"])

    def test_v3_teacher_descriptor_or_artifact_drift_after_closed_gate_stops_before_App(self):
        summary = self.owned_proof()
        original_descriptor, original_writer = module.descriptor, module.write_artifact
        for record_source, mode in ((True, "descriptor"), (True, "artifact"),
                                    (False, "descriptor"), (False, "artifact")):
            variant = f"{record_source}-{mode}"
            self.request = replace(self.request, output=self.root / variant,
                                   record_source3=record_source)
            self.config = replace(self.config, state_dir=self.root / (variant + "-state"))
            calls = 0
            apps_before, children_before, commands_before = (
                len(self.apps), len(self.children), len(self.commands)
            )

            def changing_descriptor(*, execution_policy=None, mode=mode):
                nonlocal calls
                calls += 1
                value = original_descriptor(execution_policy=execution_policy)
                if mode == "descriptor" and calls == 3:
                    value["runtime_provenance"]["dependency_lock_sha256"] = "0" * 64
                return value

            def changing_writer(path, *, execution_policy=None, mode=mode):
                result = original_writer(path, execution_policy=execution_policy)
                if mode == "artifact":
                    value = module.decode_json(path.read_bytes())
                    value["runtime_provenance"]["dependency_lock_sha256"] = "0" * 64
                    raw = module.json_bytes(value)
                    path.write_bytes(raw)
                    digest = hashlib.sha256(raw).hexdigest()
                    result.update(id=value["agent_spec"]["id"] + "-" + digest[:16], sha256=digest)
                return result

            with (
                self.subTest(mode=mode, record_source=record_source),
                patch.object(module, "descriptor", changing_descriptor),
                patch.object(module, "write_artifact", changing_writer),
            ):
                report = self.run_collection(terminal_verifier=lambda *args: summary)
                self.assertEqual(len(self.apps), apps_before)
                self.assertEqual(len(self.children), children_before)
                self.assertEqual(len(self.commands), commands_before)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(
                    report["error_code"], "closed_collection_producer_artifact_changed"
                )

    def test_v3_unpromoted_installed_verifier_or_closed_producer_blocks_before_App_Host_or_output(
        self,
    ):
        self.request = replace(self.request, execution_policy=module.OWNED_EXECUTION_POLICY)
        for record_source, installed, producer, code in (
            (True, False, {"id": "synthetic"}, "installed_terminal_summary_unavailable"),
            (False, False, None, "installed_terminal_summary_unavailable"),
            (True, True, None, "closed_collection_producer_unavailable"),
        ):
            self.request = replace(self.request, record_source3=record_source)
            with (
                self.subTest(installed=installed, record_source=record_source),
                patch.object(
                    module, "installed_terminal_summary_available", lambda value=installed: value
                ),
                patch.object(
                    module, "closed_collection_producer", lambda *args, value=producer: value
                ),
                self.assertRaisesRegex(BoundaryError, code),
            ):
                self.run_collection()
            self.assertEqual(self.apps, [])
            self.assertEqual(self.children, [])
            self.assertEqual(self.commands, [])
            self.assertFalse(self.request.output.exists())

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

    def test_silent_child_deadline_enters_bounded_cleanup_without_any_next_submission(self):
        clock = {"now": 0.0}
        self.request = replace(self.request, deadline_ms=1000)
        receive_calls = []
        finishing = []
        original_factory = self.child

        def silent_factory(path, cancellation):
            child = original_factory(path, cancellation)
            original_send = child.send
            original_receive = child.receive
            stopping = {"requested": False}

            def send(value):
                if value["type"] == "source_ready":
                    # Known App Start is retained, but the Node child then stops
                    # answering without EOF/quiesced/closed. No Runtime permission.
                    self.messages.append(copy.deepcopy(value))
                    return
                original_send(value)

            def receive():
                receive_calls.append(clock["now"])
                if len(receive_calls) > 4:
                    raise AssertionError("silent receive loop escaped bounded cleanup")
                if child.queue:
                    return original_receive()
                clock["now"] += 121.0 if stopping["requested"] else 2.0
                return None

            def stop(reason):
                stopping["requested"] = True  # Deliberately no quiescence/EOF reply.

            def finish():
                finishing.append(True)
                raise module.fail("child_exit_unconfirmed")

            child.send, child.receive, child.stop, child.finish = send, receive, stop, finish
            return child

        with (
            patch.object(module.time, "monotonic", lambda: clock["now"]),
            patch.object(self, "child", silent_factory),
        ):
            report = self.run_collection()
        self.assertEqual(report["error_code"], "deadline")
        self.assertEqual(len(receive_calls), 3)
        self.assertEqual(finishing, [True])
        self.assertEqual(report["status"], "unknown")
        self.assertTrue(report["child_exit_unconfirmed"])
        self.assertEqual(report["runtime_quiescence"], "unconfirmed")
        self.assertEqual(
            report["source_close_fallback"], "bounded_cleanup_without_confirmed_Runtime_quiescence"
        )
        self.assertEqual(self.commands, ["start_new_session", "close"])
        self.assertFalse(any(value["type"] == "runtime_continue" for value in self.messages))
        self.assertEqual(report["submissions"], 0)

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
    def test_fixed_helper_verifies_source_owner_fixture_with_explicit_dependency(
        self,
    ):
        evidence = module.ROOT.parent / "components/evidence"
        script = """
import json, subprocess, sys
from pathlib import Path
from test_sampled_agent_session_run_evidence import OwnedStaleEvidenceFixture
fixture = OwnedStaleEvidenceFixture()
try:
    fixture.terminal(fixture.submission(), defer=True)
    fixture.readiness()
    fixture.sample()
    fixture.terminal(fixture.submission(), delivery='delivered')
    fixture.stop()
    request = {'directory': str(fixture.directory.resolve()), 'expected': fixture.f.startup,
               'execution_policy': fixture.f.agent['execution_policy']}
    result = subprocess.run([sys.executable, '-m', 'spireagent.workbench.native_source3_collection',
                             '--verify-terminal-summary'], input=json.dumps(request),
                            capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert [summary[k] for k in ('original_submission_count', 'terminal_result_count',
                                'known_delivered', 'known_stale_rejections',
                                'consecutive_known_stale_rejections')] == [2, 2, 1, 1, 0]
    assert summary['live_eligibility_proved'] is False
    print(json.dumps(summary))
finally:
    fixture.close()
"""
        import os

        # Explicit source-only Evidence dependency. The installed production pin
        # is unchanged, and its preflight remains unavailable until promotion.
        environment = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                (str(module.ROOT), str(evidence), str(evidence / "tests"))
            ),
        }
        checked = subprocess.run(
            [sys.executable, "-c", script],
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)["terminal_result_count"], 2)

    def test_model_composer_selects_policy_only_from_the_exact_immutable_sampled_spec(self):
        from spireagent.workbench.native_agent_support import native_package_execution_policy
        from stpd.native_graph_spec import NativeGraphControl
        from stpd.native_sampled_carry_spec import sampled_agent_spec
        from stpd.policy.native_operational_outcome import owned_current_known_stale_policy

        control = NativeGraphControl()
        package = {
            "agent_spec": sampled_agent_spec(control),
            "graph": {"model_control": control.to_dict()},
        }
        self.assertIsNone(native_package_execution_policy(package))
        policy = owned_current_known_stale_policy()
        package["agent_spec"] = sampled_agent_spec(control, execution_policy=policy)
        self.assertEqual(native_package_execution_policy(package), policy)
        for change in (
            lambda value: value["agent_spec"].update(version="1.1.0"),
            lambda value: value["agent_spec"].pop("execution_policy"),
            lambda value: value["agent_spec"].update(I=True),
            lambda value: value["agent_spec"]["execution_policy"].update(
                max_known_stale_rejections=True
            ),
        ):
            changed = copy.deepcopy(package)
            change(changed)
            with self.subTest(change=change), self.assertRaises(BoundaryError):
                native_package_execution_policy(changed)

    def test_selected_package_owned_current_capability_is_checked_before_registration_writes(self):
        from spireagent.workbench import local_model_registration as registration
        from spireagent.workbench.native_agent_support import PUBLICATION_PROFILE_SHA256
        from stpd.native_code_scope import REQUIRED_METHODS
        from stpd.native_graph_spec import NativeGraphControl
        from stpd.native_sampled_carry_spec import sampled_agent_spec

        # The existing requirements projector imports two literal support exports
        # from the numerical Agent module. Inject their actual source values;
        # importing that module would violate this pure test's no-Torch boundary.
        names = {"SUPPORTED_ACTION_VERBS", "SUPPORTED_INTERACTION_KINDS"}
        owner_source = module.ROOT / "stpd/policy/native_agent.py"
        owner_bytes = owner_source.read_bytes()
        static_exports = {}
        for statement in ast.parse(owner_bytes).body:
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name) and target.id in names:
                        self.assertNotIn(target.id, static_exports)
                        static_exports[target.id] = ast.literal_eval(statement.value)
        self.assertEqual(static_exports, {
            "SUPPORTED_ACTION_VERBS": ("*",),
            "SUPPORTED_INTERACTION_KINDS": ("*",),
        })
        self.enterContext(
            patch.dict(sys.modules, {"stpd.policy.native_agent": SimpleNamespace(**static_exports)})
        )
        from stpd.policy.native_operational_outcome import owned_current_known_stale_policy

        control, policy = NativeGraphControl(), owned_current_known_stale_policy()
        contracts = module.ROOT.parent / "components/connector/contracts"
        profile = json.loads(
            (contracts / "native-logical-publication-profile-v1.json").read_bytes()
        )
        wire = json.loads((contracts / "fixtures/native-logical-v1.json").read_bytes())
        caps = wire["wire_samples"]["capabilities"]
        caps["host"].update(
            host_kind="test",
            implementation={
                "source_revision": "a" * 40,
                "artifact_sha256": "b" * 64,
                "module_version_id": "synthetic-mvid",
            },
        )
        caps["game"].update(version="synthetic-game", commit="synthetic-game-commit")
        caps["game"]["modset"].update(
            status="exact", fingerprint="c" * 64, loaded_mod_ids=["synthetic-fixture"]
        )
        caps["supported_methods"] = list(REQUIRED_METHODS)
        caps["capture_coverage"] = copy.deepcopy(profile["required_seams"])
        reply = {
            "capabilities": caps,
            "publication_profile": profile,
            "publication_profile_sha256": PUBLICATION_PROFILE_SHA256,
        }
        legacy = registration._native_requirements(reply)[0]
        self.assertNotIn("current_owned", legacy["required_methods"])
        legacy_without_marker_field = copy.deepcopy(reply)
        del legacy_without_marker_field["capabilities"]["implemented_mechanisms"]
        self.assertEqual(registration._native_requirements(legacy_without_marker_field)[0], legacy)
        with self.assertRaises(BoundaryError):
            registration._native_requirements(reply, execution_policy=policy)
        method_only_reply = copy.deepcopy(reply)
        method_only_reply["capabilities"]["supported_methods"].append("current_owned")
        owned_reply = copy.deepcopy(method_only_reply)
        owned_reply["capabilities"]["implemented_mechanisms"].append(
            "native_current_reader_owned_v1"
        )
        selected = registration._native_requirements(owned_reply, execution_policy=policy)[0]
        self.assertEqual(
            selected["required_methods"], [*legacy["required_methods"], "current_owned"]
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            export = root / "already-verified-export"
            export.mkdir()
            (export / "model.json").write_text(
                json.dumps(
                    {
                        "agent_spec": sampled_agent_spec(control, execution_policy=policy),
                        "graph": {"model_control": control.to_dict()},
                    }
                )
            )
            config = ProjectConfig(
                root / "state", "", "http://127.0.0.1:15526", None, combination()
            )
            # The numerical export owner is an explicit injected prerequisite.
            # This test verifies the actual metadata/capability pre-write route.
            calls = []
            service = registration.LocalModelRegistration(
                config,
                SimpleNamespace(
                    verified_for_registration=lambda identity: (
                        calls.append("verified_export") or export
                    )
                ),
                SimpleNamespace(private_root=root / "private", root=root),
            )
            invalid_replies = [
                ("missing_method", reply),
                ("method_only_missing_marker", method_only_reply),
            ]
            for label, value in (
                ("missing_marker_field", "missing"),
                ("null_markers", None),
                ("markers_not_array", "native_current_reader_owned_v1"),
                ("empty_markers", []),
                ("wrong_marker", ["native_current_reader_owned_v2"]),
                ("nonstring_marker", ["native_current_reader_owned_v1", 17]),
                ("empty_string_marker", ["native_current_reader_owned_v1", ""]),
            ):
                invalid = copy.deepcopy(method_only_reply)
                if label == "missing_marker_field":
                    del invalid["capabilities"]["implemented_mechanisms"]
                else:
                    invalid["capabilities"]["implemented_mechanisms"] = value
                invalid_replies.append((label, invalid))
            for label, invalid in invalid_replies:
                calls.clear()
                private = root / ("private-" + label)
                service.models = SimpleNamespace(private_root=private, root=root)
                before_files = set(root.iterdir())
                with self.subTest(capability=label):
                    with (
                        patch.object(registration, "require_native_models", lambda stage: None),
                        patch.object(
                            service, "_native_runtime", lambda **kwargs: (None, root / "sdk")
                        ),
                        patch.object(
                            service, "_capabilities", lambda *args, value=invalid, **kwargs: value
                        ),
                        patch.object(
                            service,
                            "_matching_native",
                            side_effect=AssertionError(
                                "compatibility must stop before registration"
                            ),
                        ),
                        self.assertRaises(BoundaryError) as rejected,
                    ):
                        service._register_native(
                            "a" * 64, environment_kind="native", deadline=float("inf")
                        )
                    self.assertEqual(rejected.exception.code, "native_capabilities_incompatible")
                    self.assertEqual(calls, ["verified_export"])
                    self.assertFalse(private.exists())
                    self.assertFalse(config.state_dir.exists())
                    self.assertEqual(set(root.iterdir()), before_files)
            from spireagent.workbench import native_agent_support

            wrong_policy = {**policy, "max_known_stale_rejections": 7}
            for existing in (False, True):
                public_checks = []
                old_manifest = export / "already-selected-manifest.json"
                old_manifest.write_text(json.dumps({"execution_policy": wrong_policy}))
                models = SimpleNamespace(
                    private_root=root / "private",
                    root=root,
                    selection=lambda selected, path=old_manifest: {"manifest": str(path)},
                    entry_path=lambda entry, field: Path(entry[field]),
                    _public_manifest_contract=lambda *args, destination=public_checks: (
                        destination.append(args)
                    ),
                )
                service.models = models
                with (
                    self.subTest(existing=existing),
                    patch.object(registration, "require_native_models", lambda stage: None),
                    patch.object(service, "_native_runtime", lambda **kwargs: (None, root / "sdk")),
                    patch.object(service, "_capabilities", lambda *args, **kwargs: owned_reply),
                    patch.object(
                        service,
                        "_matching_native",
                        lambda *args, value=existing, **kwargs: (
                            "existing-selection" if value else None,
                            False,
                        ),
                    ),
                    patch.object(
                        native_agent_support,
                        "bind_native_export",
                        lambda *args, **kwargs: ({}, {"execution_policy": wrong_policy}),
                    ),
                    self.assertRaisesRegex(
                        BoundaryError, "selected_package_execution_policy_changed"
                    ),
                ):
                    service._register_native(
                        "a" * 64, environment_kind="native", deadline=module.time.monotonic() + 60
                    )
                self.assertEqual(public_checks, [])
                self.assertFalse((models.private_root / registration.REGISTRY).exists())
                if (models.private_root / registration.REGISTRATIONS).exists():
                    self.assertEqual(
                        list((models.private_root / registration.REGISTRATIONS).iterdir()), []
                    )

    def test_installed_summary_capability_checks_only_the_public_property_without_evaluation(self):
        class PublicTypedValue:
            @property
            def terminal_summary(self):
                raise AssertionError("capability inspection must not evaluate a fabricated value")

        with patch.dict(
            sys.modules,
            {"sts2_platform_evidence": SimpleNamespace(AgentSessionRunEvidence=PublicTypedValue)},
        ):
            self.assertTrue(module.installed_terminal_summary_available())
        with patch.dict(
            sys.modules,
            {
                "sts2_platform_evidence": SimpleNamespace(
                    AgentSessionRunEvidence=type("LegacyTypedValue", (), {})
                )
            },
        ):
            self.assertFalse(module.installed_terminal_summary_available())

    def test_owned_policy_is_explicit_and_does_not_raise_an_omitted_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            legacy = module.CollectionRequest(root / "game", root / "host", root / "output", "TEST")
            legacy.validate()
            with self.assertRaises(BoundaryError):
                replace(legacy, max_submissions=200).validate()
            owned = replace(legacy, execution_policy=module.OWNED_EXECUTION_POLICY)
            owned.validate()
            self.assertEqual(owned.max_submissions, 100)
            replace(owned, max_submissions=200).validate()
            self.assertEqual(owned.report_schema, module.OWNED_REPORT_SCHEMA)
            self.assertEqual(legacy.report_schema, module.REPORT_SCHEMA)
            for candidate in (
                replace(owned, max_submissions=201),
                replace(owned, target_choices=101),
                replace(legacy, execution_policy="silently_retry"),
            ):
                with self.subTest(candidate=candidate), self.assertRaises(BoundaryError):
                    candidate.validate()

    def test_version_only_proposed_teacher_cannot_pass_the_existing_closed_producer_registry(self):
        candidate = module.descriptor()
        candidate["agent_spec"]["teacher"]["version"] = "1.0.6"
        candidate["adapter"]["version"] = "1.2.0"
        self.assertIsNone(module.closed_collection_producer(candidate, {"proposed": True}))

    def test_terminal_helper_consumes_public_typed_value_and_rejects_policy_or_content_substitution(
        self,
    ):
        summary = {
            "schema": "sts2.evidence/agent-session-terminal-summary-1",
            "run_id": "run-fixture",
            "content_id": "f" * 64,
            "original_submission_count": 4,
            "terminal_result_count": 3,
            "known_delivered": 1,
            "known_stale_rejections": 2,
            "consecutive_known_stale_rejections": 1,
            "proof_scope": "recorded_dispatch_and_terminal_results",
            "live_eligibility_proved": False,
        }
        policy, expected, calls = {"synthetic_declared_policy": True}, {"run_id": "run-fixture"}, []
        value = SimpleNamespace(
            terminal_summary=summary,
            content_id=summary["content_id"],
            agent_manifest={"execution_policy": policy},
        )

        def public_verifier(directory, checked_expected):
            calls.append((directory, checked_expected))
            return SimpleNamespace(passed=True, require_value=lambda: value)

        owner = SimpleNamespace(verify_agent_session_run_evidence=public_verifier)
        with patch.dict(sys.modules, {"sts2_platform_evidence": owner}):
            self.assertEqual(
                module.verify_terminal_summary(Path("/synthetic/owner/run"), expected, policy),
                summary,
            )
            self.assertEqual(calls, [(Path("/synthetic/owner/run"), expected)])
            with self.assertRaises(BoundaryError):
                module.verify_terminal_summary(
                    Path("/synthetic/owner/run"), expected, {"wrong_policy": True}
                )
            value.content_id = "a" * 64
            with self.assertRaises(BoundaryError):
                module.verify_terminal_summary(Path("/synthetic/owner/run"), expected, policy)
        for corrupt in (
            {**summary, "known_stale_rejections": True},
            {**summary, "live_eligibility_proved": True},
            {**summary, "known_delivered": 2},
            {**summary, "deferred_events": 2},
        ):
            with self.subTest(corrupt=corrupt), self.assertRaises(BoundaryError):
                module.terminal_summary_fields(corrupt, expected["run_id"])

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


class FreshEpisodeTests(unittest.TestCase):
    """Existing synthetic owner fixtures, both actual typed verifiers, genuine App fencing."""

    setUp = CollectionTests.setUp
    tearDown = CollectionTests.tearDown
    mock = CollectionTests.mock
    application = CollectionTests.application
    child = CollectionTests.child
    run_collection = CollectionTests.run_collection

    def prepare_prior(self):
        import sts2_platform_evidence as evidence

        owner_tests = Path(module.ROOT).parent / "components/evidence/tests"
        sys.path.insert(0, str(owner_tests))
        try:
            source_helpers = importlib.import_module("test_source_session_bundle_v3")
            direct_helpers = importlib.import_module("test_agent_session_run_evidence")
        finally:
            sys.path.remove(str(owner_tests))
        s = source_helpers.SourceSessionBundleV3Tests()
        s.setUp()
        self.addCleanup(s.tearDown)
        epoch, segment = (
            s.rows("source-attachment-epochs.jsonl")[0],
            s.rows("source-segments.jsonl")[0],
        )
        operation = "a" * 32
        segment["declaration"]["actor_id"] = "source3-teacher-" + operation
        s.write_rows("source-attachment-epochs.jsonl", [epoch])
        s.write_rows("source-segments.jsonl", [segment])
        observations = [
            r for r in s.rows("public-observations.jsonl") if r["epoch_id"] == epoch["epoch_id"]
        ]
        s.write_rows("public-observations.jsonl", observations)
        closed = s.rows("source-boundaries.jsonl")[-1]
        closed.update(
            sequence=1,
            segment_id=segment["segment_id"],
            position=observations[-1]["position"],
            transition=None,
            paused_intervals=[],
        )
        closed["sealed_epochs"] = closed["sealed_epochs"][:1]
        s.write_rows("source-boundaries.jsonl", [closed])
        s.write_rows("canonical-transitions.jsonl", [])
        receipt = s.read("raw/source-close-receipt.json")
        receipt.update(
            final_position=closed["position"],
            sealed_epochs=closed["sealed_epochs"],
            final_drains=receipt["final_drains"][:1],
            epoch_count=1,
            source_kinds=["agent_protocol"],
        )
        s.write("raw/source-close-receipt.json", receipt)
        payloads = set()

        def collect_refs(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "payload_ref":
                        payloads.add(child)
                    else:
                        collect_refs(child)
            elif isinstance(value, list):
                for child in value:
                    collect_refs(child)

        for row in observations + s.rows("native-input-witnesses.jsonl"):
            collect_refs(row)
        for family in ("public-captures", "public-catalogs"):
            for file in (s.bundle / "raw" / family).rglob("*.bin"):
                if file.relative_to(s.bundle / "raw").as_posix() not in payloads:
                    file.unlink()
        manifest = s.read("source-session-bundle-manifest.json")
        manifest.update(observation_count=len(observations), source_kinds=["agent_protocol"])
        s.write("source-session-bundle-manifest.json", manifest)
        coverage = s.read("raw/source-coverage.json")
        coverage["status"].update(
            epoch_id=epoch["epoch_id"],
            segment_id=segment["segment_id"],
            declaration=segment["declaration"],
            observations=len(observations),
            epochs=1,
        )
        s.write("raw/source-coverage.json", coverage)
        identity = s.read("content-identity.json")
        identity["source_kinds"] = ["agent_protocol"]
        s.write("content-identity.json", identity)
        audit = s.read("audit/source-audit.json")
        audit.update(source_kinds=["agent_protocol"], observation_count=len(observations))
        s.write("audit/source-audit.json", audit)
        # Match the stricter Agent environment digest domain in this synthetic Source fixture.
        recording = s.read("raw/recording-manifest.json")
        recording["source_environment"].update(
            environment_fingerprint="a" * 64, modset_fingerprint="b" * 64
        )
        s.write("raw/recording-manifest.json", recording)
        epoch["context"]["environment"].update(
            environment_fingerprint="a" * 64, modset_fingerprint="b" * 64
        )
        s.write_rows("source-attachment-epochs.jsonl", [epoch])
        changed_payloads = {}
        for file in (s.bundle / "raw/public-captures").rglob("*.bin"):
            value = json.loads(file.read_bytes())
            value["session"]["environment_fingerprint"] = "a" * 64
            raw = json.dumps(value, separators=(",", ":")).encode()
            checksum = hashlib.sha256(raw).hexdigest()
            relative = f"public-captures/sha256/{checksum[:2]}/{checksum}.bin"
            target = s.bundle / "raw" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            changed_payloads[file.relative_to(s.bundle / "raw").as_posix()] = (
                relative,
                checksum,
                len(raw),
            )
            file.unlink()

        def update_payload_refs(value):
            if isinstance(value, dict):
                if value.get("payload_ref") in changed_payloads:
                    relative, checksum, count = changed_payloads[value["payload_ref"]]
                    value.update(payload_ref=relative, sha256=checksum, byte_count=count)
                for child in value.values():
                    update_payload_refs(child)
            elif isinstance(value, list):
                for child in value:
                    update_payload_refs(child)

        for name in ("public-observations.jsonl", "native-input-witnesses.jsonl"):
            rows = s.rows(name)
            update_payload_refs(rows)
            s.write_rows(name, rows)
        s.reseal()  # Existing owning integrity helper; no packer or new Source verifier.
        source = evidence.verify_source_session_bundle_v3(s.bundle).require_value()
        f = direct_helpers.NativeAgentSessionEvidenceTests()
        f.setUp()
        self.addCleanup(f.tearDown)
        attached = f.event("native_session_attached")["payload"]["environment"]
        env = source.recording["source_environment"]
        replacements = {
            attached["runtime_instance_id"]: env["runtime_instance_id"],
            attached["environment_fingerprint"]: env["environment_fingerprint"],
            f.result["request_id"]: source.inputs[0]["input_id"],
        }

        def replace_values(value):
            if isinstance(value, dict):
                return {k: replace_values(v) for k, v in value.items()}
            if isinstance(value, list):
                return [replace_values(v) for v in value]
            return replacements.get(value, value) if isinstance(value, str) else value

        f.events = replace_values(f.events)
        attached = f.event("native_session_attached")["payload"]["environment"]
        connector = env["connector"]
        pins = {
            "connector_artifact_sha256": connector["sha256"],
            "connector_module_version_id": connector["module_version_id"],
            "connector_source_revision": connector["source_revision"],
            "connector_version": connector["version"],
            "modset_fingerprint": env["modset_fingerprint"],
        }
        attached.update(pins)
        f.agent["requirements"]["environment"].update(pins)
        f.event("stopped")["payload"]["agent_state"] = "uncertain"
        f.events.insert(
            -1,
            {
                "schema": module.REPORT_SCHEMA,
                "kind": "fail_closed",
                "recorded_at": "2026-10-08T00:00:30.000Z",
                "sequence": 1,
                "payload": {
                    "session_id": "session-fixture",
                    "recovery_epoch": 0,
                    "reason": "query_current_source_capture_incomplete",
                    "agent_state": "uncertain",
                },
            },
        )
        f.events[-2]["schema"] = "sts2.policy-runtime/agent-session-event-1"
        f.write()
        self.prior_output = self.root / "prior"
        direct_path = self.prior_output / "agent-runs" / f.run["run_id"]
        shutil.copytree(f.directory, direct_path, dirs_exist_ok=True)
        direct = evidence.verify_agent_session_run_evidence(direct_path).require_value()
        public = {
            "schema": "sts2.policy-runtime/agent-session-status-1",
            "run_id": direct.run_id,
            "lifecycle": "stopped",
            "mode": "human",
            "controller": "released",
            "tainted": False,
            "pending_request": None,
            "environment": copy.deepcopy(attached),
            "session": {
                "agent_state": "uncertain",
                "continuity_token": "old-continuity",
                "stream_generation": "old-stream",
            },
            "runtime": {
                "version": f.run["runtime_version"],
                "code_sha256": f.run["runtime_code_sha256"],
            },
            "agent": {"adapter": f.agent["adapter"]},
            "agent_manifest_sha256": direct.manifest["agent_manifest_sha256"],
            "autonomy_budget": {"submissions_used": 1},
        }
        teacher = {"pid": 123, "actual_exit": True, "code": None, "signal": "SIGKILL"}
        full = {
            "schema": "spireagent/native-source3-collector-final-full-v2",
            "operation_id": operation,
            "counts": {"result_messages": 1, "known_delivered_choices": 1},
            "runtime_status": public,
            "teacher_exit": teacher,
            "source_closed": True,
            "record_source3": True,
            "control_release": {"observation": {"runtime_instance_id": env["runtime_instance_id"]}},
            "host_exit": {"code": 0, "signal": None, "forced": False},
        }
        atomic_json(self.prior_output / "collector-final-full.json", full)
        raw = (self.prior_output / "collector-final-full.json").read_bytes()
        final = {
            **full,
            "schema": module.PIPE_SCHEMA,
            "type": "closed",
            "control_release": {
                "confirmed": True,
                "runtime_instance_id": env["runtime_instance_id"],
            },
            "cleanup_errors": [],
            "full_record_ref": {
                "path": "collector-final-full.json",
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
        }
        self.prior = {
            "schema": module.REPORT_SCHEMA,
            "status": "unknown",
            "operation_id": operation,
            "actor_id": "source3-teacher-" + operation,
            "runtime_status": public,
            "runtime_summary": {
                "agent_state": "uncertain",
                "tainted": False,
                "pending_request": None,
                "controller": "released",
            },
            "child_final": final,
            "child_final_full": full,
            "submissions": 1,
            "actual_choices": 1,
            "known_delivered_choices": 1,
            "teacher_exit": teacher,
            "record_source3": True,
            "source_closed": True,
            "runtime_quiescence": "observed_exact_Node_quiesced",
            "unknown_request_id_projection": None,
            "child": {
                "exit_code": 0,
                "forced_by_parent": False,
                "reader_terminal": True,
                "diagnostics_terminal": True,
            },
            "source_final_status": {
                "recording_session_id": source.recording["session_id"],
                "recording_lifecycle": "closed",
                "closeout_status": "closed",
                "source": coverage["status"],
            },
            "direct_evidence": {
                "directory": str(direct_path),
                "run_id": direct.run_id,
                "manifest_id": f.run["manifest_id"],
                "artifact_sha256": f.run["agent_artifact_sha256"],
            },
        }
        self.report_path = self.prior_output / "report.json"
        self.marker = self.config.state_dir / module.MARKER_FILE
        self.reseal_report()
        self.request = replace(
            self.request,
            predecessor_report_path=self.report_path,
            predecessor_source3_bundle=s.bundle.resolve(),
            predecessor_source3_content_id=source.content_id,
            predecessor_report_sha256=hashlib.sha256(self.report_path.read_bytes()).hexdigest(),
            predecessor_marker_sha256=hashlib.sha256(self.marker.read_bytes()).hexdigest(),
        )
        self.behavior.update(
            fresh=True,
            host_identity={
                "host": {"runtime_instance_id": "runtime"},
                "profile": {
                    "status": "instantiated",
                    "template_id": "defect-a0-s0",
                    "profile_id": "fresh-profile",
                    "generation_id": "fresh-generation",
                },
            },
        )

    def reseal_report(self):
        atomic_json(self.report_path, self.prior)
        atomic_json(
            self.marker,
            {
                "schema": module.REPORT_SCHEMA,
                "status": "unknown",
                "operation_id": "a" * 32,
                "output": str(self.prior_output),
                "report_sha256": hashlib.sha256(self.report_path.read_bytes()).hexdigest(),
            },
        )
        if self.request.predecessor_report_path is not None:
            self.request = replace(
                self.request,
                predecessor_report_sha256=hashlib.sha256(self.report_path.read_bytes()).hexdigest(),
                predecessor_marker_sha256=hashlib.sha256(self.marker.read_bytes()).hexdigest(),
            )

    def preflight(self, config, request):
        prepared = CollectionTests.preflight(self, config, request)
        prepared["fresh_episode_boundary"] = module.verified_fresh_predecessor(self.marker, request)
        return prepared

    def test_explicit_fresh_episode_preserves_unknown_bytes_and_uses_both_real_verifiers(self):
        self.prepare_prior()
        old_marker, old_report = self.marker.read_bytes(), self.report_path.read_bytes()
        self.assertIsNotNone(module.verified_fresh_predecessor(self.marker, self.request))
        result = self.run_collection()
        self.assertEqual(result["status"], "completed")
        self.assertEqual((self.request.output / "predecessor-marker.json").read_bytes(), old_marker)
        self.assertEqual(self.report_path.read_bytes(), old_report)
        archived = json.loads(old_marker)
        self.assertEqual(archived["status"], "unknown")
        boundary = result["fresh_episode_boundary"]
        sealed = (self.request.output / boundary["receipt"]["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(sealed).hexdigest(), boundary["receipt"]["sha256"])
        self.assertEqual(boundary["prior_input_consumption"], "unresolved")
        self.assertEqual(self.commands, ["start_new_session", "close"])

    def test_default_unknown_and_partial_fresh_arguments_still_block_before_App(self):
        self.prepare_prior()
        with self.assertRaisesRegex(BoundaryError, "original_collection_outcome_unresolved"):
            module.require_resolved_predecessor(self.marker)
        with self.assertRaisesRegex(BoundaryError, "complete_fresh_predecessor_required"):
            replace(self.request, predecessor_marker_sha256=None).validate()
        self.assertEqual(self.apps, [])

    def test_delivery_or_cleanup_uncertainty_never_becomes_fresh_eligibility(self):
        self.prepare_prior()
        original = copy.deepcopy(self.prior)
        for mutation in [
            lambda p: p["runtime_status"].update(tainted=True),
            lambda p: p["runtime_status"].update(pending_request={"request_id": "old"}),
            lambda p: p.update(known_delivered_choices=0),
            lambda p: p["child"].update(forced_by_parent=True),
            lambda p: p.update(source_close_fallback="unconfirmed"),
            lambda p: p.update(full_final_reporting_error="bad_hash"),
        ]:
            with self.subTest(mutation=mutation):
                self.prior = copy.deepcopy(original)
                mutation(self.prior)
                self.reseal_report()
                with self.assertRaises(BoundaryError):
                    module.verified_fresh_predecessor(self.marker, self.request)
        self.assertEqual(self.apps, [])

    def test_hash_CAS_and_replayed_predecessor_cannot_launch_a_new_owner(self):
        self.prepare_prior()
        prepared = self.preflight(self.config, self.request)
        self.marker.write_bytes(self.marker.read_bytes() + b"\n")
        with self.assertRaisesRegex(BoundaryError, "fresh_predecessor_marker_changed"):
            module.collect_source3(
                self.config,
                self.root / "config.json",
                self.request,
                preflight=lambda *_: prepared,
                app_factory=self.application,
                child_factory=self.child,
            )
        self.assertEqual(self.apps, [])
        self.assertFalse(self.request.output.exists())

    def test_foreign_source_content_or_corrupt_direct_evidence_blocks_without_packing(self):
        self.prepare_prior()
        with self.assertRaisesRegex(BoundaryError, "fresh_predecessor_source_identity_mismatch"):
            module.verified_fresh_predecessor(
                self.marker, replace(self.request, predecessor_source3_content_id="0" * 64)
            )
        direct = Path(self.prior["direct_evidence"]["directory"])
        (direct / "events.jsonl").write_bytes(b"corrupt\n")
        with self.assertRaisesRegex(BoundaryError, "fresh_predecessor_direct_verification_failed"):
            module.verified_fresh_predecessor(self.marker, self.request)
        self.assertEqual(self.apps, [])

    def test_reused_host_or_warm_initial_state_stops_before_gameplay_permission(self):
        for warm in [False, True]:
            with self.subTest(warm=warm):
                self.prepare_prior()
                if warm:
                    self.behavior["warm_state"] = True
                else:
                    self.behavior["host_identity"]["profile"]["status"] = "reused"
                result = self.run_collection()
                self.assertEqual(result["status"], "unknown")
                self.assertFalse(any(m["type"] == "runtime_continue" for m in self.messages))
                # A fresh pending/unknown pointer cannot be bypassed using the old authorization.
                with self.assertRaises(BoundaryError):
                    module.verified_fresh_predecessor(self.marker, self.request)
                if self.request.output.exists():
                    shutil.rmtree(self.request.output)
                self.messages.clear()
                self.behavior.clear()

    def test_reused_Source_identity_keeps_original_once_Close_and_no_Runtime_permission(self):
        self.prepare_prior()
        old = self.prior["source_final_status"]
        self.behavior["reuse_source"] = (
            old["recording_session_id"],
            old["source"]["segment_id"],
            old["source"]["epoch_id"],
        )
        result = self.run_collection()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["error_code"], "fresh_source_identity_required")
        self.assertEqual(result["submissions"], 0)
        self.assertEqual(self.commands, ["start_new_session", "close"])
        self.assertFalse(any(m["type"] == "runtime_continue" for m in self.messages))

    def test_explicit_new_Source_optout_does_not_bypass_old_Source_on_closure(self):
        self.prepare_prior()
        self.prior["source_closed"] = False
        self.reseal_report()
        with self.assertRaisesRegex(BoundaryError, "fresh_predecessor_source_closure_required"):
            module.verified_fresh_predecessor(
                self.marker, replace(self.request, record_source3=False)
            )

    def test_history_directory_barrier_is_before_pending_pointer_and_App_child(self):
        self.prepare_prior()
        self.request = replace(self.request, output=self.root / "new" / "nested" / "output")
        calls = []
        real_sync, real_atomic = module._sync_directory, module.atomic_json

        def barrier(path):
            self.assertTrue((self.request.output / "predecessor-marker.json").is_file())
            self.assertTrue((self.request.output / "fresh-episode-boundary.json").is_file())
            calls.append(("directory", path))
            real_sync(path)

        def atomic(path, value):
            if path == self.marker:
                calls.append(("pointer", value["status"]))
            real_atomic(path, value)

        self.mock(module, "_sync_directory", barrier)
        self.mock(module, "atomic_json", atomic)
        self.assertEqual(self.run_collection()["status"], "completed")
        pending = calls.index(("pointer", "pending"))
        self.assertEqual(
            [p for kind, p in calls[:pending] if kind == "directory"],
            [
                self.request.output,
                self.request.output.parent,
                self.request.output.parent.parent,
                self.root,
            ],
        )

    def test_history_directory_sync_failure_retains_exact_old_pointer_without_launch(self):
        self.prepare_prior()
        original = self.marker.read_bytes()

        def fail_barrier(_):
            self.assertTrue((self.request.output / "predecessor-marker.json").is_file())
            self.assertTrue((self.request.output / "fresh-episode-boundary.json").is_file())
            raise OSError("faithful_directory_fsync_failure")

        self.mock(module, "_sync_directory", fail_barrier)
        with self.assertRaisesRegex(BoundaryError, "fresh_history_durability_failed"):
            self.run_collection()
        self.assertEqual(self.marker.read_bytes(), original)
        self.assertEqual(self.apps, [])
        self.assertEqual(self.children, [])

    def prepare_source_off_prior(self):
        self.prepare_prior()
        self.prior.update(
            record_source3=False,
            source_start=None,
            source_close=None,
            source_closed=None,
            close_sent=False,
            source_final_status=None,
            options={"record_source3": False},
            source={"source_revision": "b" * 40},
            child_path="/fixed/collector.mjs",
            child_sha256="c" * 64,
        )
        self.prior["child_final"]["record_source3"] = False
        self.prior["child_final_full"]["record_source3"] = False
        atomic_json(self.prior_output / "collector-final-full.json", self.prior["child_final_full"])
        raw = (self.prior_output / "collector-final-full.json").read_bytes()
        self.prior["child_final"]["full_record_ref"].update(
            bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()
        )
        quiesced = {
            "schema": module.PIPE_SCHEMA,
            "type": "quiesced",
            "operation_id": "a" * 32,
            "record_source3": False,
            "counts": self.prior["child_final"]["counts"],
            "direct_evidence": self.prior["direct_evidence"],
            "runtime_status": self.prior["runtime_status"],
        }
        self.prior["quiesced"] = quiesced
        atomic_json(self.prior_output / "quiesced.json", quiesced)
        original_request = {
            "schema": module.REPORT_SCHEMA,
            "status": "pending",
            "operation_id": "a" * 32,
            "actor_id": self.prior["actor_id"],
            "record_source3": False,
            "options": self.prior["options"],
            "source": self.prior["source"],
            "child_path": self.prior["child_path"],
            "child_sha256": self.prior["child_sha256"],
            "source_start": None,
            "source_close": None,
            "source_closed": None,
        }
        atomic_json(self.prior_output / "request.json", original_request)
        self.request = replace(
            self.request, predecessor_source3_bundle=None, predecessor_source3_content_id=None
        )
        self.reseal_report()

    def test_source_off_prior_uses_real_typed_direct_proof_without_dummy_Source_identity(self):
        self.prepare_source_off_prior()
        from spireagent.workbench import local_recording_import

        self.mock(
            local_recording_import,
            "_verified_source3",
            lambda _: self.fail("Source-off must not verify a bundle"),
        )
        boundary = module.verified_fresh_predecessor(self.marker, self.request)
        self.assertFalse(boundary["prior_record_source3"])
        self.assertEqual(boundary["prior_source3_outcome"], "not_requested")
        for key in (
            "prior_source_session_id",
            "prior_source_segment_id",
            "prior_source_epoch_id",
            "source3_content_id",
            "source3_bundle_path",
            "original_manifest_sha256",
            "original_close_sha256",
        ):
            self.assertNotIn(key, boundary)
        old_marker = self.marker.read_bytes()
        result = self.run_collection()  # New Source-on, with no invented old Source to compare.
        self.assertEqual(result["status"], "completed")
        self.assertEqual((self.request.output / "predecessor-marker.json").read_bytes(), old_marker)
        self.assertEqual(self.commands, ["start_new_session", "close"])

    def test_bundle_pair_is_conditional_on_old_attempt_and_never_a_general_force(self):
        self.prepare_prior()
        without_bundle = replace(
            self.request, predecessor_source3_bundle=None, predecessor_source3_content_id=None
        )
        without_bundle.validate()
        with self.assertRaisesRegex(
            BoundaryError, "fresh_source3_bundle_required_for_recorded_predecessor"
        ):
            module.verified_fresh_predecessor(self.marker, without_bundle)
        with self.assertRaisesRegex(BoundaryError, "fresh_source3_bundle_pair_required"):
            replace(self.request, predecessor_source3_content_id=None).validate()
        bundle = self.request.predecessor_source3_bundle
        content_id = self.request.predecessor_source3_content_id
        self.prepare_source_off_prior()
        with self.assertRaisesRegex(BoundaryError, "fresh_source3_bundle_forbidden_for_optout"):
            module.verified_fresh_predecessor(
                self.marker,
                replace(
                    self.request,
                    predecessor_source3_bundle=bundle,
                    predecessor_source3_content_id=content_id,
                ),
            )
        with self.assertRaisesRegex(BoundaryError, "original_collection_outcome_unresolved"):
            module.require_resolved_predecessor(self.marker)
        self.assertEqual(self.apps, [])

    def test_source_off_original_request_or_quiesced_disagreement_blocks_without_launch(self):
        self.prepare_source_off_prior()
        for filename, change in [
            ("request.json", lambda v: v["options"].update(record_source3=True)),
            ("request.json", lambda v: v.update(source_start={"accepted": True})),
            ("request.json", lambda v: v.update(operation_id="f" * 32)),
            ("quiesced.json", lambda v: v.update(record_source3=True)),
        ]:
            with self.subTest(filename=filename, change=change):
                target = self.prior_output / filename
                original = target.read_bytes()
                value = json.loads(original)
                change(value)
                atomic_json(target, value)
                with self.assertRaisesRegex(
                    BoundaryError, "fresh_predecessor_source_optout_mismatch"
                ):
                    module.verified_fresh_predecessor(self.marker, self.request)
                target.write_bytes(original)
        self.assertEqual(self.apps, [])
        self.assertEqual(self.children, [])

    def test_source_off_latent_recording_file_or_unknown_preference_cannot_erase_obligation(self):
        self.prepare_source_off_prior()
        latent = self.prior_output / "source-start-request.json"
        latent.write_text("{}")
        with self.assertRaisesRegex(BoundaryError, "fresh_predecessor_source_optout_mismatch"):
            module.verified_fresh_predecessor(self.marker, self.request)
        latent.unlink()
        self.prior["record_source3"] = None
        self.reseal_report()
        with self.assertRaisesRegex(BoundaryError, "fresh_predecessor_source_closure_required"):
            module.verified_fresh_predecessor(self.marker, self.request)
        self.assertEqual(self.apps, [])

    def test_legacy_mixed_child_or_full_family_is_rejected(self):
        self.prepare_source_off_prior()
        original = copy.deepcopy(self.prior)
        for part, schema in (
            ("child_final", module.OWNED_PIPE_SCHEMA),
            ("child_final_full", "spireagent/native-source3-collector-final-full-v3"),
        ):
            with self.subTest(part=part):
                self.prior = copy.deepcopy(original)
                self.prior[part]["schema"] = schema
                atomic_json(
                    self.prior_output / "collector-final-full.json", self.prior["child_final_full"]
                )
                raw = (self.prior_output / "collector-final-full.json").read_bytes()
                self.prior["child_final"]["full_record_ref"].update(
                    bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()
                )
                self.reseal_report()
                with self.assertRaisesRegex(BoundaryError, "fresh_predecessor_family_mismatch"):
                    module.verified_fresh_predecessor(self.marker, self.request)


class OwnedFreshEpisodeTests(unittest.TestCase):
    """Synthetic originals verified by the actual installed I27 owner, never source fallback."""

    tearDown = CollectionTests.tearDown

    def setUp(self):
        CollectionTests.setUp(self)
        self.owned_index = 0

    def prepare_owned(self, total=9, stale=(), duplicate=False, delivery="delivered"):
        import sts2_platform_evidence as evidence

        self.assertIn("site-packages", str(Path(evidence.__file__).resolve()))
        owner_tests = module.ROOT.parent / "components/evidence/tests"
        sys.path.insert(0, str(owner_tests))
        try:
            helpers = importlib.import_module("test_sampled_agent_session_run_evidence")
        finally:
            sys.path.remove(str(owner_tests))
        fixture = helpers.OwnedStaleEvidenceFixture()
        self.addCleanup(fixture.close)
        self.fixture = fixture
        for index in range(total):
            if index:
                fixture.sample()
            result = fixture.terminal(
                fixture.submission(),
                delivery="not_started" if index in stale else delivery,
                defer=index in stale,
            )
            if result["delivery"] == "delivered":
                result.update(execution="unknown", effect="unknown", cancel="unknown")
        if duplicate:
            fixture.f.add("native_result", result=copy.deepcopy(result))
        if not fixture.f.run["tainted"]:
            fixture.f.add(
                "agent_sample_next_requested",
                request_id="parent-unfinished",
                input={
                    "continuity_token": "segment-1",
                    "consumption_id": fixture.consumption,
                    "state_version": fixture.version,
                    "basis_acquisition_id": fixture.basis,
                    "received_cursor": fixture.f.events[0]["payload"]["subscription"][
                        "starting_cursor"
                    ],
                    "operational_outcome": None,
                },
            )
        fixture.f.add(
            "fail_closed", reason="query_current_source_capture_incomplete", agent_state="uncertain"
        )
        fixture.f.budget.update(max_submissions=200, submissions_used=total)
        fixture.stop()
        fixture.f.events[-1]["payload"]["agent_state"] = "uncertain"
        fixture.f.write()
        self.owned_index += 1
        self.prior_output = self.root / ("prior-" + str(self.owned_index))
        direct_path = self.prior_output / "agent-runs" / fixture.f.run["run_id"]
        shutil.copytree(fixture.directory, direct_path)
        checked = evidence.verify_agent_session_run_evidence(direct_path)
        self.assertTrue(checked.passed, checked.findings)
        self.direct = checked.require_value()
        summary = self.direct.terminal_summary
        policy = copy.deepcopy(self.direct.agent_manifest["execution_policy"])
        environment = copy.deepcopy(fixture.f.events[0]["payload"]["environment"])
        public = {
            "schema": "sts2.policy-runtime/agent-session-status-1",
            "run_id": self.direct.run_id,
            "lifecycle": "stopped",
            "mode": "human",
            "controller": "released",
            "tainted": fixture.f.run["tainted"],
            "pending_request": None,
            "environment": environment,
            "session": {
                "agent_state": "uncertain",
                "continuity_token": "segment-1",
                "stream_generation": "old-stream",
            },
            "runtime": {
                "version": fixture.f.run["runtime_version"],
                "code_sha256": fixture.f.run["runtime_code_sha256"],
            },
            "agent": {"adapter": fixture.f.agent["adapter"]},
            "agent_manifest_sha256": self.direct.manifest["agent_manifest_sha256"],
            "autonomy_budget": {"submissions_used": total, "max_submissions": 200},
        }
        direct_ref = {
            "directory": str(direct_path),
            "run_id": self.direct.run_id,
            "manifest_id": fixture.f.run["manifest_id"],
            "artifact_sha256": fixture.f.run["agent_artifact_sha256"],
        }
        counts = {"result_messages": total, "known_delivered_choices": summary["known_delivered"]}
        teacher = {"pid": 123, "actual_exit": True, "code": None, "signal": "SIGKILL"}
        full = {
            "schema": "spireagent/native-source3-collector-final-full-v3",
            "operation_id": "a" * 32,
            "counts": counts,
            "runtime_status": public,
            "teacher_exit": teacher,
            "source_closed": True,
            "record_source3": False,
            "control_release": {
                "observation": {"runtime_instance_id": environment["runtime_instance_id"]}
            },
            "host_exit": {
                "code": 0, "signal": None, "forced": False,
                "host_shutdown": {"status": "requested", "http_status": 200},
            },
            "execution_policy": policy,
            "terminal_summary": summary,
            "counter_proof_error": None,
            "direct_evidence": direct_ref,
        }
        final = {
            **copy.deepcopy(full),
            "schema": module.OWNED_PIPE_SCHEMA,
            "type": "closed",
            "host_exit": {"code": 0, "signal": None, "forced": False},
            "control_release": {
                "confirmed": True, "runtime_instance_id": environment["runtime_instance_id"]
            },
            "cleanup_errors": [],
        }
        quiesced = {
            "schema": module.OWNED_PIPE_SCHEMA,
            "type": "quiesced",
            "operation_id": "a" * 32,
            "record_source3": False,
            "counts": counts,
            "direct_evidence": direct_ref,
            "runtime_status": public,
        }
        options = {
            "record_source3": False, "execution_policy": policy, "max_submissions": 200,
            "teacher_descriptor": {"agent_spec": {"execution_policy": policy}},
        }
        self.prior = {
            "schema": module.OWNED_REPORT_SCHEMA, "status": "unknown", "operation_id": "a" * 32,
            "actor_id": "source3-teacher-" + "a" * 32,
            "source": {"source_revision": "b" * 40}, "child_path": "/fixed/collector.mjs",
            "child_sha256": "c" * 64, "options": options, "execution_policy": policy,
            "runtime_status": public,
            "runtime_summary": {
                "agent_state": "uncertain", "tainted": public["tainted"],
                "pending_request": None, "controller": "released",
            },
            "child_final": final, "child_final_full": full, "quiesced": quiesced,
            "submissions": total, "actual_choices": total,
            "known_delivered_choices": summary["known_delivered"],
            "budget_submissions_used": total,
            "terminal_result_count": summary["terminal_result_count"],
            "known_delivered_terminal_count": summary["known_delivered"],
            "known_stale_rejections": summary["known_stale_rejections"],
            "consecutive_known_stale_rejections": summary["consecutive_known_stale_rejections"],
            "terminal_summary": summary, "counter_proof_error": None,
            "teacher_exit": teacher, "record_source3": False,
            "source_start": None, "source_close": None, "source_closed": None,
            "close_sent": False, "source_final_status": None,
            "runtime_quiescence": "observed_exact_Node_quiesced",
            "unknown_request_id_projection": None,
            "child": {"exit_code": 0, "forced_by_parent": False,
                      "reader_terminal": True, "diagnostics_terminal": True},
            "direct_evidence": direct_ref,
        }
        self.report_path = self.prior_output / "report.json"
        self.marker = self.config.state_dir / module.MARKER_FILE
        self.request = replace(self.request, predecessor_report_path=self.report_path)
        self.save_owned()

    def save_owned(self):
        full = self.prior["child_final_full"]
        atomic_json(self.prior_output / "collector-final-full.json", full)
        raw = (self.prior_output / "collector-final-full.json").read_bytes()
        self.prior["child_final"]["full_record_ref"] = {
            "path": "collector-final-full.json", "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()
        }
        atomic_json(self.prior_output / "quiesced.json", self.prior["quiesced"])
        original = {
            **{key: self.prior[key] for key in (
                "schema", "operation_id", "actor_id", "source", "child_path", "child_sha256",
                "options", "execution_policy", "record_source3",
            )},
            "status": "pending", "source_start": None, "source_close": None, "source_closed": None,
        }
        atomic_json(self.prior_output / "request.json", original)
        atomic_json(self.report_path, self.prior)
        report_sha = hashlib.sha256(self.report_path.read_bytes()).hexdigest()
        atomic_json(self.marker, {"schema": self.prior["schema"], "status": "unknown",
                                 "operation_id": "a" * 32, "output": str(self.prior_output),
                                 "report_sha256": report_sha})
        self.request = replace(self.request, predecessor_report_sha256=report_sha,
                               predecessor_marker_sha256=hashlib.sha256(self.marker.read_bytes()).hexdigest())

    def test_owned_native8_shaped_closed_input_gap_keeps_original_unknown_bytes(self):
        self.prepare_owned()
        before = {path: path.read_bytes() for path in (self.report_path, self.marker)}
        boundary = module.verified_fresh_predecessor(self.marker, self.request)
        self.assertFalse(boundary["can_resume_prior"])
        self.assertFalse(boundary["can_retry_prior"])
        self.assertFalse(boundary["prior_record_source3"])
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(self.apps, [])
        self.assertEqual(self.children, [])

    def test_owned_101_originals_99_deliveries_two_stale_and_duplicate_use_owner_counts(self):
        self.prepare_owned(total=101, stale=(0, 50), duplicate=True)
        self.assertEqual(self.direct.terminal_summary["terminal_result_count"], 101)
        self.assertEqual(self.direct.terminal_summary["known_delivered"], 99)
        self.assertIsNotNone(module.verified_fresh_predecessor(self.marker, self.request))

    def test_owned_forged_operational_result_count_cannot_disagree_with_public_terminals(self):
        self.prepare_owned()
        self.prior["actual_choices"] = 8
        for record in (
            self.prior["child_final"], self.prior["child_final_full"], self.prior["quiesced"]
        ):
            record["counts"]["result_messages"] = 8
        self.save_owned()
        with self.assertRaisesRegex(BoundaryError, "fresh_predecessor_owned_terminal_mismatch"):
            module.verified_fresh_predecessor(self.marker, self.request)

    def test_owned_mixed_policy_schema_counter_and_direct_joins_are_rejected(self):
        self.prepare_owned()
        original = copy.deepcopy(self.prior)
        mutations = [
            lambda p: p.update(execution_policy=None),
            lambda p: p["execution_policy"].update(extra=True),
            lambda p: p["child_final"].update(schema=module.PIPE_SCHEMA),
            lambda p: p["child_final_full"].update(
                schema="spireagent/native-source3-collector-final-full-v2"
            ),
            lambda p: p["quiesced"].update(schema=module.PIPE_SCHEMA),
            lambda p: p["quiesced"]["direct_evidence"].update(run_id="foreign"),
            lambda p: p["child_final"]["terminal_summary"].update(known_delivered=8),
            lambda p: p["terminal_summary"].update(content_id="0" * 64),
            lambda p: p.update(counter_proof_error="missing"),
            lambda p: p["runtime_status"].update(pending_request={"request_id": "pending"}),
            lambda p: p["runtime_status"].update(tainted=True),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.prior = copy.deepcopy(original)
                mutation(self.prior)
                self.save_owned()
                with self.assertRaises(BoundaryError):
                    module.verified_fresh_predecessor(self.marker, self.request)
        self.assertEqual(self.apps, [])

    def test_owned_unknown_or_partial_native_delivery_never_admits_fresh_episode(self):
        for delivery in ("unknown", "partially_delivered"):
            with self.subTest(delivery=delivery):
                self.prepare_owned(total=1, delivery=delivery)
                with self.assertRaises(BoundaryError):
                    module.verified_fresh_predecessor(self.marker, self.request)
