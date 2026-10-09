"""Collect-only native Source3 application/CLI composition.

One live Application keeps the existing source/model admission fence. The fixed
Node child owns Host/SDK execution; an explicit pure STPD teacher owns strategy.
The pipe journal is operational evidence, never an alternate training data source.
"""

from __future__ import annotations

import hashlib
import json
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from spireagent.json_boundary import BoundaryError, decode_json, object_fields
from spireagent.workbench.developer import ROOT, ProjectConfig, atomic_json, tool_identity
from spireagent.workbench.instance_lock import instance_lock
from stpd.policy.native_public_teacher import TEACHER_ID, TEACHER_VERSION
from stpd.policy.native_teacher_agent import descriptor, write_artifact

if TYPE_CHECKING:
    from spireagent.workbench.developer_server import Application

PIPE_SCHEMA = "spireagent/native-source3-collector-pipe-v2"
REPORT_SCHEMA = "spireagent/native-source3-collection-v2"
MARKER_FILE = "native-source3-collection-operation.json"
MAX_PIPE_BYTES = 96 * 1024 * 1024
MAX_CONTROL_BYTES = 16 * 1024


def fail(code: str) -> BoundaryError:
    return BoundaryError("source3_collection", code)


@dataclass(frozen=True)
class CollectionRequest:
    installation: Path
    host_local_root: Path
    output: Path
    seed: str
    target_choices: int = 100
    max_submissions: int = 100
    deadline_ms: int = 900_000
    template_id: str = "defect-a0-s0"
    teacher: str = TEACHER_ID
    experimental_build_acknowledged: bool = False
    experimental_connector_acknowledged: bool = False
    max_input_bytes: int = 64 * 1024**2
    max_diagnostic_bytes: int = 256 * 1024**2
    record_source3: bool = True

    def validate(self) -> None:
        for value in (self.installation, self.host_local_root, self.output):
            if not value.is_absolute() or ".." in value.parts:
                raise fail("absolute_collection_path_required")
            if any(p.is_symlink() for p in (value, *value.parents)):
                raise fail("collection_path_symlink")
        if self.output.exists():
            raise fail("new_collection_output_required")
        if self.installation == self.output or self.installation in self.output.parents:
            raise fail("output_inside_game_forbidden")
        if (
            self.teacher != TEACHER_ID
            or self.template_id != "defect-a0-s0"
            or not isinstance(self.seed, str)
            or not self.seed
            or len(self.seed) > 128
        ):
            raise fail("fixed_collection_request_required")
        for limit_value, lower, upper in (
            (self.target_choices, 1, 100),
            (self.max_submissions, 1, 100),
            (self.deadline_ms, 1, 900_000),
            (self.max_input_bytes, 1024, 64 * 1024**2),
            (self.max_diagnostic_bytes, 1024, 768 * 1024**2),
        ):
            if type(limit_value) is not int or not lower <= limit_value <= upper:
                raise fail("finite_collection_limits_required")
        if self.target_choices > self.max_submissions:
            raise fail("choice_limit_exceeds_submission_budget")
        if (
            type(self.experimental_build_acknowledged) is not bool
            or type(self.experimental_connector_acknowledged) is not bool
            or type(self.record_source3) is not bool
        ):
            raise fail("explicit_host_acknowledgement_required")

    def options(self, endpoint: str) -> dict[str, Any]:
        return {
            "installation": str(self.installation),
            "host_local_root": str(self.host_local_root),
            "output": str(self.output),
            "endpoint": endpoint,
            "seed": self.seed,
            "template_id": self.template_id,
            "target_choices": self.target_choices,
            "max_submissions": self.max_submissions,
            "deadline_ms": self.deadline_ms,
            "max_input_bytes": self.max_input_bytes,
            "max_diagnostic_bytes": self.max_diagnostic_bytes,
            "experimental_build_acknowledged": self.experimental_build_acknowledged,
            "experimental_connector_acknowledged": self.experimental_connector_acknowledged,
            "record_source3": self.record_source3,
            "python_executable": sys.executable,
            "teacher_artifact": None,
            "teacher_descriptor": descriptor(),
        }


def require_resolved_predecessor(marker: Path) -> None:
    if marker.exists():
        prior = decode_json(marker.read_bytes())
        if (
            not isinstance(prior, dict)
            or prior.get("schema") not in {REPORT_SCHEMA, "spireagent/native-source3-collection-v1"}
            or prior.get("status") not in {"completed", "partial", "failed"}
        ):
            raise fail("original_collection_outcome_unresolved")


def metadata_preflight(config: ProjectConfig, request: CollectionRequest) -> dict[str, Any]:
    """Read-only source/request check. Does not import or create an Application/SDK."""
    request.validate()
    from spireagent.workbench.native_tasks import NativeTasks

    endpoint = NativeTasks.bound_connector(config.platform_url or "http://127.0.0.1:15526")
    selected = tool_identity()
    if selected.get("working_tree_clean") is not True:
        raise fail("clean_collection_source_required")
    child = ROOT.parent / "tools/native-source3-collector.mjs"
    if not child.is_file() or child.is_symlink():
        raise fail("fixed_collector_child_unavailable")
    require_resolved_predecessor(config.state_dir / MARKER_FILE)
    return {
        "schema": REPORT_SCHEMA,
        "status": "plan",
        "source": selected,
        "endpoint": endpoint,
        "options": request.options(endpoint),
        "teacher": {
            "id": TEACHER_ID,
            "version": TEACHER_VERSION,
            "sha256": hashlib.sha256(
                (ROOT / "stpd/policy/native_public_teacher.py").read_bytes()
            ).hexdigest(),
            "source_kind": "agent_protocol",
            "machine_verifiable": False,
            "learned_evaluation": False,
        },
        "child_path": str(child),
        "child_sha256": hashlib.sha256(child.read_bytes()).hexdigest(),
        "acquisition": {
            "current": "complete",
            "catalog": "complete_original_C",
            "attachment": "scoped",
            "eager_event_fields": [],
            "advisory_event_history_required": False,
            "owner": "public_generic_Agent_Runtime",
        },
        "partial_source_prefix": True,
        "actual_choices": 0,
        "eligible_unique_N": None,
        "admission": "not_run",
        "automatic_training": False,
        "automatic_upload": False,
    }


class Cancellation:
    """Installed before construction/launch. Repeated signals request cleanup, never exit early."""

    def __init__(self) -> None:
        self.requested = threading.Event()
        self.child: Any = None
        self.signals = 0
        self.reason = "external_stop"

    def stop(self, reason: str = "external_stop") -> None:
        self.signals += 1
        self.reason = reason
        self.requested.set()
        if self.child is not None:
            self.child.stop(reason)

    def check(self) -> None:
        if self.requested.is_set():
            raise fail(self.reason)


@contextmanager
def cancellation_signals(cancel: Cancellation) -> Iterator[None]:
    previous: dict[int, Any] = {}
    if threading.current_thread() is threading.main_thread():

        def interrupted(number, _frame):
            cancel.stop("external_signal")

        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, interrupted)
    try:
        yield
    finally:
        for restored_number, handler in previous.items():
            signal.signal(restored_number, handler)


class OwnedPipeChild:
    """Only the fixed reviewed repository child; no shell or request executable."""

    def __init__(self, child_path: Path, cancel: Cancellation) -> None:
        node = shutil.which("node")
        if node is None:
            raise fail("node_unavailable")
        cancel.check()
        self.process = subprocess.Popen(
            [node, str(child_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT.parent,
        )
        self.incoming: queue.Queue[Any] = queue.Queue(maxsize=2)
        self.stderr_bytes = 0
        self.reader_done = threading.Event()
        self.write_lock = threading.Lock()
        self.reader = threading.Thread(target=self._read, daemon=True, name="source3-child-pipe")
        self.diagnostics = threading.Thread(
            target=self._stderr, daemon=True, name="source3-child-diagnostics"
        )
        cancel.child = self
        self.reader.start()
        self.diagnostics.start()
        if cancel.requested.is_set():
            self.stop(cancel.reason)

    def _offer(self, value: Any) -> None:
        while not self.reader_done.is_set():
            try:
                self.incoming.put(value, timeout=0.1)
                return
            except queue.Full:
                continue

    def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            while True:
                line = self.process.stdout.readline(MAX_PIPE_BYTES + 1)
                if not line:
                    self._offer(fail("child_stdout_closed"))
                    return
                if len(line) > MAX_PIPE_BYTES or not line.endswith(b"\n"):
                    raise fail("child_pipe_capacity_or_truncation")
                message = decode_json(line)
                if (
                    isinstance(message, dict)
                    and message.get("type") not in {"runtime_gate", "runtime_tick", "quiesced"}
                    and len(line) > MAX_CONTROL_BYTES
                ):
                    raise fail("child_control_capacity")
                self._offer(message)
        except Exception:
            self._offer(fail("child_pipe_invalid"))

    def _stderr(self) -> None:
        assert self.process.stderr is not None
        while bytes_ := self.process.stderr.read(4096):
            self.stderr_bytes += len(bytes_)
            if self.stderr_bytes > 64 * 1024:
                self.stop("child_diagnostic_capacity")

    def send(self, value: dict[str, Any]) -> None:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode() + b"\n"
        if len(raw) > MAX_CONTROL_BYTES:
            raise fail("parent_control_capacity")
        with self.write_lock:
            if self.process.stdin is None or self.process.stdin.closed:
                raise fail("child_stdin_closed")
            self.process.stdin.write(raw)
            self.process.stdin.flush()

    def receive(self, timeout: float = 0.25) -> dict[str, Any] | None:
        try:
            value = self.incoming.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(value, BaseException):
            raise value
        if not isinstance(value, dict):
            raise fail("child_pipe_object_required")
        return value

    def stop(self, _reason: str) -> None:
        # The Node handler was installed before init/launch. Never SIGKILL past owned Host cleanup.
        if self.process.poll() is None:
            with suppress(ProcessLookupError):
                self.process.send_signal(signal.SIGTERM)

    def finish(self) -> dict[str, Any]:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        # Drain the bounded pipe while awaiting exact exit: a blocked reader must
        # never leave Node waiting to flush its final owned-cleanup receipt.
        end = time.monotonic() + 150
        while self.process.poll() is None and time.monotonic() < end:
            with suppress(queue.Empty):
                self.incoming.get(timeout=0.1)
        if self.process.poll() is None:
            self.stop("child_exit_timeout")
            raise fail("child_exit_unconfirmed")
        code = self.process.wait()
        self.reader_done.set()
        self.reader.join(timeout=1)
        self.diagnostics.join(timeout=1)
        if not self.reader.is_alive() and self.process.stdout is not None:
            self.process.stdout.close()
        if not self.diagnostics.is_alive() and self.process.stderr is not None:
            self.process.stderr.close()
        return {
            "pid": self.process.pid,
            "exit_code": code,
            "signal": -code if code < 0 else None,
            "forced_by_parent": False,
            "stderr_bytes": self.stderr_bytes,
            "reader_terminal": not self.reader.is_alive(),
            "diagnostics_terminal": not self.diagnostics.is_alive(),
        }


def _application(config: ProjectConfig, path: Path) -> Application:
    from spireagent.workbench.developer_server import Application

    return Application(config, config_path=path)


def _status(
    app: Application, original: dict[str, Any] | None = None, *, for_close: bool = False
) -> dict[str, Any]:
    view = app.native_recording_status()
    status = view["status"]
    if not isinstance(status, dict):
        raise fail("source_status_object_required")
    if (
        view.get("command_pending") is True
        or not for_close
        and view.get("recovery_required") is True
    ):
        raise fail("application_source_outcome_unresolved")
    if original is not None:
        source = status.get("source")
        if (
            status["runtime_instance_id"] != original["runtime_instance_id"]
            or status["recording_session_id"] != original["recording_session_id"]
            or not isinstance(source, dict)
            or source.get("segment_id") != original["source"]["segment_id"]
            or source.get("declaration") != original["source"]["declaration"]
        ):
            raise fail("original_source_context_changed")
        if not for_close and (
            status.get("recording_lifecycle") != "recording"
            or status.get("capture_profile_id") != "native-logical-source-v3"
        ):
            raise fail("original_source_not_recording")
        if not for_close and (
            status.get("health")
            != {"append_health": "healthy", "disk_health": "healthy", "error": None}
        ):
            raise fail("original_source_storage_unhealthy")
        if not for_close and (
            source.get("accounting_complete") is not True
            or source.get("gaps") != 0
            or source.get("error") is not None
        ):
            raise fail("original_source_accounting_failed")
    return status


def _body(status: dict[str, Any], kind: str, actor: str | None = None) -> dict[str, Any]:
    return {
        "kind": kind,
        "runtime_instance_id": status["runtime_instance_id"],
        "recording_session_id": status["recording_session_id"],
        "source_segment_id": status["source"]["segment_id"] if status["source"] else None,
        "source_kind": "agent_protocol" if actor is not None else None,
        "actor_id": actor,
        "command_id": str(uuid.uuid4()),
    }


def runtime_status(value: Any, runtime: str | None, request: CollectionRequest) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or value.get("schema") != "sts2.policy-runtime/agent-session-status-1"
        or not isinstance(value.get("autonomy_budget"), dict)
        or not isinstance(value.get("session"), dict)
        or value["session"].get("profile") != "native-logical-v1"
    ):
        raise fail("public_runtime_status_required")
    if runtime is not None and value.get("environment", {}).get("runtime_instance_id") != runtime:
        raise fail("public_runtime_environment_changed")
    budget = value["autonomy_budget"]
    if (
        type(budget.get("submissions_used")) is not int
        or not 0 <= budget["submissions_used"] <= request.max_submissions
        or type(budget.get("policy_calls_used")) is not int
        or not 0 <= budget["policy_calls_used"] <= 1200
    ):
        raise fail("public_runtime_budget_invalid")
    return value


def collect_source3(
    config: ProjectConfig,
    config_path: Path,
    request: CollectionRequest,
    *,
    app_factory: Callable = _application,
    child_factory: Callable = OwnedPipeChild,
    preflight: Callable = metadata_preflight,
    cancel: Cancellation | None = None,
) -> dict[str, Any]:
    """One genuine application lifetime; public Runtime owns the whole Agent execution."""
    cancel = cancel or Cancellation()
    with cancellation_signals(cancel):
        cancel.check()
        prepared = preflight(config, request)
        with instance_lock(config.state_dir / "instance.lock"):
            cancel.check()
            marker = config.state_dir / MARKER_FILE
            require_resolved_predecessor(marker)
            operation = uuid.uuid4().hex
            actor = "source3-teacher-" + operation
            request.output.mkdir(parents=True, mode=0o700)
            report: dict[str, Any] = {
                **prepared,
                "schema": REPORT_SCHEMA,
                "status": "pending",
                "operation_id": operation,
                "actor_id": actor,
                "actual_choices": 0,
                "known_delivered_choices": 0,
                "submissions": 0,
                "eligible_unique_N": None,
                "N_exclusions": None,
                "admission": "not_run",
                "partial_source_prefix": True,
                "learned_evaluation": False,
                "automatic_restart": False,
                "automatic_resume": False,
                "automatic_retry": False,
                "record_source3": request.record_source3,
                "source_start": None,
                "source_close": None,
                "child": None,
                "child_final": None,
                "runtime_status": None,
                "direct_evidence": None,
                "teacher_exit": None,
            }
            app = child = original = None
            runtime: str | None = None
            close_sent = source_closed = False
            terminal = False
            error_code: str | None = None
            last_message = 0
            seen_results: set[str] = set()
            deadline = time.monotonic() + request.deadline_ms / 1000

            def close_original_once() -> tuple[bool, dict | None, str]:
                nonlocal close_sent, source_closed
                if not request.record_source3:
                    return True, None, "not_requested"
                if original is None or close_sent:
                    return (
                        source_closed,
                        report.get("source_final_status"),
                        "not_owned_or_already_requested",
                    )
                status = None
                try:
                    status = _status(app, original, for_close=True)
                    body = _body(status, "close")
                    try:
                        atomic_json(request.output / "source-close-request.json", body)
                    except Exception:
                        report["source_close_request_write_error"] = "diagnostic_write_failed"
                    close_sent = True
                    closed = app.control_native_recording(body)
                    report["source_close"] = closed
                    status = closed["status"]
                    end = time.monotonic() + 20
                    while status["recording_lifecycle"] == "closing" and time.monotonic() < end:
                        time.sleep(0.1)
                        status = _status(app, original, for_close=True)
                    source = status.get("source")
                    source_closed = (
                        closed.get("accepted") is True
                        and closed.get("pending") is False
                        and closed.get("command_id") == body["command_id"]
                        and status["recording_session_id"] == original["recording_session_id"]
                        and status["runtime_instance_id"] == original["runtime_instance_id"]
                        and isinstance(source, dict)
                        and source.get("segment_id") == original["source"]["segment_id"]
                        and source.get("declaration") == original["source"]["declaration"]
                        and status["recording_lifecycle"] == "closed"
                        and status.get("closeout_status") == "closed"
                    )
                    report["source_final_status"] = status
                    try:
                        atomic_json(
                            request.output / "source-close.json",
                            {"result": closed, "status": status},
                        )
                    except Exception:
                        report["source_close_receipt_write_error"] = "diagnostic_write_failed"
                    return (
                        source_closed,
                        status,
                        "known_closed" if source_closed else "pending_or_unconfirmed",
                    )
                except Exception as error:
                    code = error.code if isinstance(error, BoundaryError) else "close_unknown"
                    report["source_close_error" if close_sent else "source_close_prepare_error"] = (
                        code
                    )
                    return False, status, code

            def project_status(value: Any, *, tick: bool = False) -> None:
                status = runtime_status(value, runtime, request)
                report["runtime_status"] = status
                report["submissions"] = status["autonomy_budget"]["submissions_used"]
                result = status.get("last_result")
                if isinstance(result, dict) and result.get("request_id") not in seen_results:
                    if not isinstance(result.get("request_id"), str) or not result["request_id"]:
                        raise fail("original_runtime_result_identity_required")
                    seen_results.add(result["request_id"])
                    report["actual_choices"] += 1
                    if result.get("status") == "terminal" and result.get("delivery") == "delivered":
                        report["known_delivered_choices"] += 1
                    # This is explicitly the public operational projection. Exact native
                    # Result/stages and original acquisition bytes stay in AgentRunEvidence.
                    atomic_json(
                        request.output / f"runtime-result-{report['actual_choices']:04d}.json",
                        result,
                    )
                if tick:
                    policy_calls = status["autonomy_budget"]["policy_calls_used"]
                    atomic_json(request.output / f"runtime-status-{policy_calls:04d}.json", status)

            def retain_quiesced(message: dict[str, Any]) -> None:
                report["quiesced"] = message
                try:
                    atomic_json(request.output / "quiesced.json", message)
                except Exception:
                    report["quiesced_write_error"] = "diagnostic_write_failed"
                object_fields(
                    message,
                    {
                        "schema",
                        "type",
                        "operation_id",
                        "message_id",
                        "reason",
                        "runtime_status",
                        "direct_evidence",
                        "counts",
                        "record_source3",
                    },
                    "source3_pipe.quiesced_v2",
                )
                if message["runtime_status"] is not None:
                    project_status(message["runtime_status"])
                report["direct_evidence"] = message["direct_evidence"]

            def final_message(message: dict[str, Any]) -> None:
                object_fields(
                    message,
                    {
                        "schema",
                        "type",
                        "operation_id",
                        "reason",
                        "counts",
                        "runtime_summary",
                        "direct_evidence",
                        "teacher_exit",
                        "source_closed",
                        "record_source3",
                        "control_release",
                        "host_exit",
                        "host_started",
                        "cleanup_errors",
                        "failure_details",
                        "full_record_ref",
                        "partial_source_prefix",
                        "learned_evaluation",
                        "unknown_request_id_projection",
                    },
                    "source3_pipe.closed_v2",
                )
                if (
                    message["schema"] != PIPE_SCHEMA
                    or message["operation_id"] != operation
                    or message["record_source3"] is not request.record_source3
                    or message["partial_source_prefix"] is not True
                    or message["learned_evaluation"] is not False
                ):
                    raise fail("invalid_child_final_receipt")
                summary = message["runtime_summary"]
                if summary is not None:
                    object_fields(
                        summary,
                        {
                            "schema",
                            "public_status_schema",
                            "lifecycle",
                            "mode",
                            "controller",
                            "tainted",
                            "agent_state",
                            "submissions_used",
                            "policy_calls_used",
                            "state_version",
                            "pending_request",
                            "last_result",
                        },
                        "source3_pipe.runtime_summary",
                    )
                    if (
                        summary["schema"] != "spireagent/native-agent-runtime-summary-v1"
                        or summary["public_status_schema"]
                        != "sts2.policy-runtime/agent-session-status-1"
                        or type(summary["submissions_used"]) is not int
                        or not 0 <= summary["submissions_used"] <= request.max_submissions
                        or type(summary["policy_calls_used"]) is not int
                        or not 0 <= summary["policy_calls_used"] <= 1200
                    ):
                        raise fail("runtime_summary_invalid")
                    report["submissions"] = summary["submissions_used"]
                    result = summary["last_result"]
                    if result is not None and result["request_id"] not in seen_results:
                        seen_results.add(result["request_id"])
                        report["actual_choices"] += 1
                        if result["status"] == "terminal" and result["delivery"] == "delivered":
                            report["known_delivered_choices"] += 1
                if message["counts"] != {
                    "result_messages": report["actual_choices"],
                    "known_delivered_choices": report["known_delivered_choices"],
                }:
                    raise fail("runtime_result_counts_disagree")
                report["runtime_summary"] = summary
                report["child_final"] = message
                report["direct_evidence"], report["teacher_exit"] = (
                    message["direct_evidence"],
                    message["teacher_exit"],
                )
                report["unknown_request_id_projection"] = message["unknown_request_id_projection"]
                try:
                    reference = message["full_record_ref"]
                    if reference is not None:
                        object_fields(
                            reference, {"path", "bytes", "sha256"}, "source3_pipe.final_ref"
                        )
                        path = request.output / "collector-final-full.json"
                        if (
                            reference["path"] != path.name
                            or path.is_symlink()
                            or type(reference["bytes"]) is not int
                            or not 1 <= reference["bytes"] <= request.max_diagnostic_bytes
                        ):
                            raise fail("owned_full_final_reference_required")
                        if path.stat().st_size != reference["bytes"]:
                            raise fail("full_final_size_failed")
                        with path.open("rb") as handle:
                            raw = handle.read(reference["bytes"] + 1)
                        if (
                            len(raw) != reference["bytes"]
                            or hashlib.sha256(raw).hexdigest() != reference["sha256"]
                        ):
                            raise fail("full_final_integrity_failed")
                        full = decode_json(raw)
                        if (
                            not isinstance(full, dict)
                            or full.get("schema")
                            != "spireagent/native-source3-collector-final-full-v2"
                            or full.get("operation_id") != operation
                            or any(
                                full.get(key) != message[key]
                                for key in (
                                    "reason",
                                    "counts",
                                    "teacher_exit",
                                    "source_closed",
                                    "record_source3",
                                    "partial_source_prefix",
                                    "learned_evaluation",
                                    "unknown_request_id_projection",
                                )
                            )
                        ):
                            raise fail("full_final_wire_facts_disagree")
                        if full["runtime_status"] is not None:
                            status = runtime_status(full["runtime_status"], runtime, request)
                            if summary is None or any(
                                summary[key] != status[status_key]
                                for key, status_key in (
                                    ("lifecycle", "lifecycle"),
                                    ("mode", "mode"),
                                    ("controller", "controller"),
                                    ("tainted", "tainted"),
                                )
                            ):
                                raise fail("full_final_summary_disagrees")
                            if (
                                summary["submissions_used"]
                                != status["autonomy_budget"]["submissions_used"]
                                or summary["policy_calls_used"]
                                != status["autonomy_budget"]["policy_calls_used"]
                                or summary["state_version"] != status["session"]["state_version"]
                                or summary["agent_state"] != status["session"]["agent_state"]
                            ):
                                raise fail("full_final_summary_disagrees")
                            project_status(status)
                        report["child_final_full"] = full
                except Exception as hydrate_error:
                    report["full_final_reporting_error"] = (
                        hydrate_error.code
                        if isinstance(hydrate_error, BoundaryError)
                        else "full_final_unreadable"
                    )

            try:
                atomic_json(
                    marker,
                    {
                        "schema": REPORT_SCHEMA,
                        "status": "pending",
                        "operation_id": operation,
                        "output": str(request.output),
                    },
                )
                artifact = write_artifact(request.output / "teacher-code-artifact.json")
                options = {
                    **prepared["options"],
                    "teacher_artifact": artifact,
                    "teacher_descriptor": descriptor(),
                    "record_source3": request.record_source3,
                    "python_executable": sys.executable,
                }
                atomic_json(request.output / "request.json", report)
                cancel.check()
                app = app_factory(config, config_path)
                model = app.models.status()
                if (
                    model.get("loaded") is not False
                    or model.get("status") not in {"idle", "stopped", "failed"}
                    or isinstance(model.get("operation"), dict)
                    and model["operation"].get("status") not in {"completed", "failed"}
                ):
                    raise fail("model_owner_not_quiescent")
                child = child_factory(Path(prepared["child_path"]), cancel)
                cancel.child = child
                cancel.check()
                child.send(
                    {
                        "schema": PIPE_SCHEMA,
                        "type": "init",
                        "operation_id": operation,
                        "options": options,
                    }
                )
                while True:
                    if time.monotonic() >= deadline and not cancel.requested.is_set():
                        cancel.stop("deadline")
                    # A silent child cannot keep cancellation in the main receive
                    # loop forever; the exception path owns the bounded drain.
                    cancel.check()
                    message = child.receive()
                    if message is None:
                        continue
                    if (
                        message.get("schema") != PIPE_SCHEMA
                        or message.get("operation_id") != operation
                    ):
                        raise fail("invalid_child_operation_message")
                    kind = message.get("type")
                    if kind == "closed":
                        final_message(message)
                        terminal = True
                        break
                    current_id = message.get("message_id")
                    if type(current_id) is not int or current_id != last_message + 1:
                        raise fail("child_message_order_changed")
                    last_message = current_id
                    common = {
                        "schema": PIPE_SCHEMA,
                        "operation_id": operation,
                        "message_id": current_id,
                    }
                    if kind == "ready":
                        cancel.check()
                        object_fields(
                            message,
                            {
                                "schema",
                                "type",
                                "operation_id",
                                "message_id",
                                "runtime_instance_id",
                                "endpoint",
                                "host_identity",
                                "bootstrap_control_release",
                                "record_source3",
                            },
                            "source3_pipe.ready_v2",
                        )
                        handoff = message["bootstrap_control_release"]
                        runtime = message["runtime_instance_id"]
                        if (
                            message["endpoint"] != prepared["endpoint"]
                            or message["record_source3"] is not request.record_source3
                            or not isinstance(handoff, dict)
                            or handoff.get("schema")
                            != "sts2.host-runtime/reference-controller-handoff-1"
                            or handoff.get("runtime_instance_id") != runtime
                            or handoff.get("controller") is not None
                            or handoff.get("basis") != "fresh_control_observation_after_close"
                        ):
                            raise fail("bootstrap_control_release_unconfirmed")
                        context = None
                        if request.record_source3:
                            observed = _status(app)
                            if observed["runtime_instance_id"] != runtime or observed[
                                "recording_lifecycle"
                            ] not in {"ready", "closed"}:
                                raise fail("source_start_context_unavailable")
                            body = _body(observed, "start_new_session", actor)
                            atomic_json(request.output / "source-start-request.json", body)
                            started = app.control_native_recording(body)
                            report["source_start"] = started
                            owned = started.get("status")
                            if (
                                started.get("accepted") is True
                                and isinstance(owned, dict)
                                and owned.get("runtime_instance_id") == runtime
                                and owned.get("recording_session_id")
                                not in {None, observed["recording_session_id"]}
                                and isinstance(owned.get("source"), dict)
                            ):
                                original = owned
                            if (
                                original is None
                                or started.get("pending") is not False
                                or started.get("command_id") != body["command_id"]
                                or original["recording_lifecycle"] != "recording"
                                or original.get("capture_profile_id") != "native-logical-source-v3"
                                or original["source"]["declaration"].get("source_kind")
                                != "agent_protocol"
                                or original["source"]["declaration"].get("actor_id") != actor
                                or original["source"]["declaration"].get("machine_verifiable")
                                is not False
                            ):
                                raise fail("known_source_start_required")
                            _status(app, original)
                            context = {
                                "runtime_instance_id": runtime,
                                "recording_session_id": original["recording_session_id"],
                                "source_segment_id": original["source"]["segment_id"],
                                "source_epoch_id": original["source"]["epoch_id"],
                                "declaration": original["source"]["declaration"],
                            }
                            atomic_json(request.output / "source-start.json", started)
                        cancel.check()
                        child.send(
                            {
                                **common,
                                "type": "source_ready",
                                "source_context": context,
                                "source_recording": "known_recording"
                                if request.record_source3
                                else "not_requested",
                            }
                        )
                    elif kind in {"runtime_gate", "runtime_tick"}:
                        cancel.check()
                        if kind == "runtime_gate":
                            object_fields(
                                message,
                                {
                                    "schema",
                                    "type",
                                    "operation_id",
                                    "message_id",
                                    "status",
                                    "direct_evidence",
                                },
                                "source3_pipe.runtime_gate",
                            )
                            project_status(message["status"])
                        else:
                            object_fields(
                                message,
                                {
                                    "schema",
                                    "type",
                                    "operation_id",
                                    "message_id",
                                    "tick",
                                    "direct_evidence",
                                },
                                "source3_pipe.runtime_tick",
                            )
                            project_status(message["tick"]["status"], tick=True)
                        report["direct_evidence"] = message["direct_evidence"]
                        allowed, why = not cancel.requested.is_set(), "known_application_source"
                        if request.record_source3:
                            _status(app, original)
                        child.send(
                            {
                                **common,
                                "type": "runtime_continue",
                                "continue": allowed,
                                "reason": why,
                            }
                        )
                    elif kind == "quiesced":
                        report["runtime_quiescence"] = "observed_exact_Node_quiesced"
                        retain_quiesced(message)
                        known, status, outcome = close_original_once()
                        child.send(
                            {
                                **common,
                                "type": "source_closed",
                                "known_closed": known,
                                "source_status": status,
                                "close_outcome": outcome,
                            }
                        )
                    else:
                        raise fail("unsupported_child_message")
            except BaseException as error:
                error_code = (
                    error.code if isinstance(error, BoundaryError) else "collection_parent_failed"
                )
                if child is not None:
                    child.stop(error_code)
                    report["runtime_quiescence"] = "unconfirmed"
                    end = time.monotonic() + 120
                    while not terminal and time.monotonic() < end:
                        try:
                            message = child.receive()
                            if message is None:
                                continue
                            if (
                                message.get("schema") != PIPE_SCHEMA
                                or message.get("operation_id") != operation
                            ):
                                continue
                            if message.get("type") == "closed":
                                final_message(message)
                                terminal = True
                                break
                            if message.get("type") == "quiesced":
                                report["runtime_quiescence"] = "observed_exact_Node_quiesced"
                                try:
                                    retain_quiesced(message)
                                except Exception:
                                    report["quiesced_invalid"] = True
                                known, status, outcome = close_original_once()
                                child.send(
                                    {
                                        "schema": PIPE_SCHEMA,
                                        "type": "source_closed",
                                        "operation_id": operation,
                                        "message_id": message["message_id"],
                                        "known_closed": known,
                                        "source_status": status,
                                        "close_outcome": outcome,
                                    }
                                )
                        except Exception:
                            break
            finally:
                if (
                    original is not None
                    and not close_sent
                    and report.get("runtime_quiescence") != "observed_exact_Node_quiesced"
                ):
                    report["source_close_fallback"] = (
                        "bounded_cleanup_without_confirmed_Runtime_quiescence"
                    )
                close_original_once()
                if child is not None:
                    try:
                        report["child"] = child.finish()
                    except Exception:
                        report["child_exit_unconfirmed"] = True
                if app is not None:
                    try:
                        app.close()
                    except Exception:
                        report["application_close_unconfirmed"] = True
            final = report.get("child_final") or {}
            host, exited = final.get("host_exit"), report.get("child")
            released = final.get("control_release")
            public = report.get("runtime_status") or {}
            actual_teacher = report.get("teacher_exit")
            known_cleanup = (
                terminal
                and isinstance(host, dict)
                and host.get("code") == 0
                and host.get("signal") is None
                and host.get("forced") is False
                and isinstance(exited, dict)
                and exited.get("exit_code") == 0
                and isinstance(released, dict)
                and released.get("confirmed") is True
                and (source_closed if request.record_source3 else True)
                and final.get("source_closed") is True
                and not final.get("cleanup_errors")
                and not report.get("application_close_unconfirmed")
                and not report.get("full_final_reporting_error")
                and (
                    actual_teacher is None
                    if report["direct_evidence"] is None
                    else isinstance(actual_teacher, dict)
                    and actual_teacher.get("actual_exit") is True
                )
            )
            summary = report.get("runtime_summary") or {}
            unknown = (
                public.get("tainted") is True
                or public.get("pending_request") is not None
                or public.get("session", {}).get("agent_state") == "uncertain"
                or summary.get("tainted") is True
                or summary.get("pending_request") is not None
                or summary.get("agent_state") == "uncertain"
            )
            report.update(
                error_code=error_code,
                source_closed=source_closed if request.record_source3 else None,
                close_sent=close_sent,
                cancellation_requests=cancel.signals,
                status="completed"
                if known_cleanup
                and not unknown
                and error_code is None
                and final.get("reason") == "target_choices_reached"
                else "partial"
                if known_cleanup and not unknown
                else "failed"
                if child is None and report["source_start"] is None
                else "unknown",
                eligible_unique_N=None,
                N_exclusions=None,
                admission="not_run",
            )
            atomic_json(request.output / "report.json", report)
            atomic_json(
                marker,
                {
                    "schema": REPORT_SCHEMA,
                    "status": report["status"],
                    "operation_id": operation,
                    "output": str(request.output),
                    "report_sha256": hashlib.sha256(
                        (request.output / "report.json").read_bytes()
                    ).hexdigest(),
                },
            )
            return report
