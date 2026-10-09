"""Collect-only native Source3 application/CLI composition.

One live Application keeps the existing source/model admission fence. The fixed
Node child owns Host/SDK execution; an explicit pure STPD teacher owns strategy.
The pipe journal is operational evidence, never an alternate training data source.
"""

from __future__ import annotations

import base64
import hashlib
import json
import queue
import shutil
import signal
import subprocess
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
from stpd.fullrun.native_structured_inputs import native_catalog_digest
from stpd.policy.native_public_teacher import TEACHER_ID, TEACHER_VERSION, NativePublicTeacher

if TYPE_CHECKING:
    from spireagent.workbench.developer_server import Application

PIPE_SCHEMA = "spireagent/native-source3-collector-pipe-v1"
REPORT_SCHEMA = "spireagent/native-source3-collection-v1"
MARKER_FILE = "native-source3-collection-operation.json"
MAX_PIPE_BYTES = 96 * 1024 * 1024
MAX_CONTROL_BYTES = 16 * 1024
# Exact Connector result.delivery domain; checked against its authoritative SDK grammar.
NATIVE_TERMINAL_DELIVERIES = frozenset(
    {
        "not_started",
        "rejected_before_input",
        "delivered",
        "partially_delivered",
        "unknown",
    }
)
BASIS_FIELDS = {
    "capture_id",
    "snapshot_id",
    "runtime_instance_id",
    "stream_generation",
    "capture_sha256",
    "byte_count",
    "catalog_digest",
    "total_count",
}


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
            (self.target_choices, 1, 300),
            (self.max_submissions, 1, 300),
            (self.deadline_ms, 1, 2_700_000),
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
        }


def require_resolved_predecessor(marker: Path) -> None:
    if marker.exists():
        prior = decode_json(marker.read_bytes())
        if (
            not isinstance(prior, dict)
            or prior.get("schema") != REPORT_SCHEMA
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
                    and message.get("type") not in {"current", "result"}
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


def decode_current(message: dict[str, Any], runtime: str, limit: int) -> tuple[dict, list]:
    basis = object_fields(message["basis"], BASIS_FIELDS, "source3_pipe.basis")
    try:
        raw = base64.b64decode(message["observation_base64"], validate=True)
    except (ValueError, TypeError):
        raise fail("current_bytes_invalid") from None
    catalog = message["catalog"]
    if (
        not isinstance(catalog, list)
        or len(raw) > limit
        or len(raw) + len(json.dumps(catalog, ensure_ascii=False).encode()) > limit
        or type(basis["byte_count"]) is not int
        or basis["byte_count"] != len(raw)
        or hashlib.sha256(raw).hexdigest() != basis["capture_sha256"]
        or basis["runtime_instance_id"] != runtime
        or type(basis["total_count"]) is not int
        or basis["total_count"] != len(catalog)
        or native_catalog_digest(catalog) != basis["catalog_digest"]
    ):
        raise fail("complete_current_integrity_failed")
    observation = decode_json(raw)
    if (
        not isinstance(observation, dict)
        or observation.get("snapshot_id") != basis["snapshot_id"]
        or observation.get("session", {}).get("runtime_instance_id") != runtime
        or observation.get("catalog", {}).get("stream_generation") != basis["stream_generation"]
        or observation["catalog"].get("digest") != basis["catalog_digest"]
    ):
        raise fail("complete_current_binding_failed")
    return observation, catalog


FINAL_FIELDS = {
    "schema",
    "type",
    "operation_id",
    "reason",
    "counts",
    "source_closed",
    "control_release",
    "host_exit",
    "host_started",
    "cleanup_errors",
    "advisory",
    "live_byte_reservations",
    "partial_source_prefix",
    "learned_evaluation",
    "pending_request_id",
    "last_submission",
}


def validate_submission(
    value: Any, pending_id: Any, counts: dict[str, Any], request: CollectionRequest
) -> None:
    if value is None:
        if counts["submissions"] != 0 or pending_id is not None:
            raise fail("original_submission_identity_missing")
        return
    item = object_fields(
        value,
        {
            "request_id",
            "ordinal",
            "basis",
            "action_id",
            "sdk_admitted",
            "lookup_status",
            "delivery",
            "result_queries",
            "automatic_retry",
        },
        "source3_pipe.submission",
    )
    object_fields(item["basis"], BASIS_FIELDS, "source3_pipe.submission_basis")
    if (
        not isinstance(item["request_id"], str)
        or not 1 <= len(item["request_id"]) <= 128
        or not isinstance(item["action_id"], str)
        or not item["action_id"]
        or type(item["ordinal"]) is not int
        or not 1 <= item["ordinal"] <= request.max_submissions
        or type(item["sdk_admitted"]) is not bool
        or item["automatic_retry"] is not False
        or type(item["result_queries"]) is not int
        or not 0 <= item["result_queries"] <= 40
        or item["result_queries"] > counts["result_queries"]
    ):
        raise fail("original_submission_identity_invalid")
    admitted = item["sdk_admitted"]
    if (
        item["ordinal"] != counts["submissions"] + (0 if admitted else 1)
        or item["lookup_status"]
        not in ({"unresolved", "pending", "terminal"} if admitted else {"not_started"})
        or item["delivery"] not in (NATIVE_TERMINAL_DELIVERIES if admitted else {None})
        or item["lookup_status"] in {"unresolved", "pending"}
        and item["delivery"] != "unknown"
        or pending_id
        != (item["request_id"] if admitted and item["lookup_status"] != "terminal" else None)
    ):
        raise fail("original_submission_disposition_changed")


def validate_final(
    message: dict[str, Any],
    operation: str,
    request: CollectionRequest,
    actual_choices: int,
    delivered_choices: int,
    result_queries: int,
) -> None:
    object_fields(message, FINAL_FIELDS, "source3_pipe.closed")
    if (
        message["schema"] != PIPE_SCHEMA
        or message["type"] != "closed"
        or message["operation_id"] != operation
        or not isinstance(message["reason"], str)
        or type(message["source_closed"]) is not bool
        or type(message["host_started"]) is not bool
        or message["partial_source_prefix"] is not True
        or message["learned_evaluation"] is not False
        or type(message["live_byte_reservations"]) is not int
        or message["live_byte_reservations"] != 0
        or not isinstance(message["cleanup_errors"], list)
        or any(not isinstance(code, str) for code in message["cleanup_errors"])
        or not isinstance(message["advisory"], dict)
        or message["advisory"].get("history_claimed") is not False
        or message["advisory"].get("eager_scope") != []
    ):
        raise fail("invalid_child_final_receipt")
    counts = object_fields(
        message["counts"],
        {"submissions", "known_delivered_choices", "result_queries"},
        "source3_pipe.counts",
    )
    if (
        any(type(counts[key]) is not int for key in counts)
        or not actual_choices
        <= counts["submissions"]
        <= min(request.max_submissions, actual_choices + 1)
        or counts["known_delivered_choices"] != delivered_choices
        or counts["result_queries"] < result_queries
        or counts["result_queries"] > counts["submissions"] * 40
    ):
        raise fail("child_counts_disagree")
    validate_submission(message["last_submission"], message["pending_request_id"], counts, request)


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
    """One explicit bounded attempt; injected owners exercise this same production composition."""
    cancel = cancel or Cancellation()
    with cancellation_signals(cancel):
        cancel.check()
        prepared = preflight(config, request)
        cancel.check()
        with instance_lock(config.state_dir / "instance.lock"):
            cancel.check()
            # Recheck after acquisition: a predecessor may have become unknown
            # between read-only preflight and obtaining the process-lifetime lock.
            require_resolved_predecessor(config.state_dir / MARKER_FILE)
            operation = uuid.uuid4().hex
            actor = "source3-teacher-" + operation
            request.output.mkdir(parents=True, mode=0o700)
            report: dict[str, Any] = {
                **prepared,
                "status": "pending",
                "operation_id": operation,
                "actor_id": actor,
                "actual_choices": 0,
                "eligible_unique_N": None,
                "N_exclusions": None,
                "admission": "not_run",
                "partial_source_prefix": True,
                "learned_evaluation": False,
                "automatic_restart": False,
                "automatic_resume": False,
                "automatic_retry": False,
                "source_start": None,
                "source_close": None,
                "child": None,
                "child_final": None,
            }
            marker = config.state_dir / MARKER_FILE
            app = child = original = None
            close_sent = False
            terminal = False
            error_code = None
            source_closed = False
            runtime = None
            last_message = 0
            teacher = NativePublicTeacher()
            pending_choice = None
            delivered_choices = result_queries = 0
            deadline = time.monotonic() + request.deadline_ms / 1000

            def close_original_once() -> tuple[bool, dict | None, str]:
                nonlocal close_sent, source_closed
                status = None
                if original is None or close_sent:
                    return (
                        source_closed,
                        report.get("source_final_status"),
                        "not_owned_or_already_requested",
                    )
                try:
                    status = _status(app, original, for_close=True)
                    body = _body(status, "close")
                    try:
                        atomic_json(request.output / "source-close-request.json", body)
                    except Exception:
                        # A diagnostic write is not an offered owner command.
                        # Preserve its failure while still closing the known session.
                        report["source_close_request_write_error"] = "diagnostic_write_failed"
                    close_sent = True  # Set only immediately before the original owner call.
                    closed = app.control_native_recording(body)
                    report["source_close"] = closed
                    status = closed["status"]
                    _status(app, original, for_close=True)
                    end = time.monotonic() + 20
                    while status["recording_lifecycle"] == "closing" and time.monotonic() < end:
                        time.sleep(0.1)
                        status = _status(app, original, for_close=True)
                    source_closed = (
                        closed.get("accepted") is True
                        and closed.get("pending") is False
                        and closed.get("command_id") == body["command_id"]
                        and status.get("source", {}).get("segment_id")
                        == original["source"]["segment_id"]
                        and status["source"].get("declaration") == original["source"]["declaration"]
                        and status["recording_session_id"] == original["recording_session_id"]
                        and status["runtime_instance_id"] == original["runtime_instance_id"]
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
                        "counts",
                        "pending_request_id",
                        "last_submission",
                    },
                    "source3_pipe.quiesced",
                )
                if message["schema"] != PIPE_SCHEMA or message["operation_id"] != operation:
                    raise fail("quiesced_operation_changed")
                counts = object_fields(
                    message["counts"],
                    {"submissions", "known_delivered_choices", "result_queries"},
                    "source3_pipe.counts",
                )
                if (
                    any(type(counts[key]) is not int for key in counts)
                    or not report["actual_choices"]
                    <= counts["submissions"]
                    <= min(request.max_submissions, report["actual_choices"] + 1)
                    or not 0 <= counts["known_delivered_choices"] <= counts["submissions"]
                    or not 0 <= counts["result_queries"] <= counts["submissions"] * 40
                ):
                    raise fail("quiesced_counts_invalid")
                validate_submission(
                    message["last_submission"], message["pending_request_id"], counts, request
                )
                submission = message["last_submission"]
                if (
                    submission is not None
                    and pending_choice is not None
                    and pending_choice["action"] is not None
                    and submission["ordinal"] == pending_choice["ordinal"]
                    and (
                        submission["basis"] != pending_choice["basis"]
                        or submission["action_id"] != pending_choice["action"]["action_id"]
                    )
                ):
                    raise fail("quiesced_original_choice_changed")
                report["pending_request_id"] = message["pending_request_id"]
                report["last_submission"] = submission

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
                atomic_json(request.output / "request.json", report)
                cancel.check()
                app = app_factory(config, config_path)
                model = app.models.status()
                if (
                    model.get("loaded") is not False
                    or model.get("status") not in {"idle", "stopped", "failed"}
                    or model.get("operation") is not None
                    and model["operation"].get("status") not in {"completed", "failed"}
                ):
                    raise fail("model_owner_not_quiescent")
                cancel.check()
                child = child_factory(Path(prepared["child_path"]), cancel)
                cancel.child = child
                cancel.check()
                child.send(
                    {
                        "schema": PIPE_SCHEMA,
                        "type": "init",
                        "operation_id": operation,
                        "options": prepared["options"],
                    }
                )
                while True:
                    if time.monotonic() >= deadline and not cancel.requested.is_set():
                        cancel.stop("deadline")
                    message = child.receive()
                    if message is None:
                        continue
                    if (
                        message.get("schema") != PIPE_SCHEMA
                        or message.get("operation_id") != operation
                        or message.get("type")
                        not in {"ready", "current", "result", "quiesced", "closed"}
                    ):
                        raise fail("invalid_child_operation_message")
                    kind = message["type"]
                    if kind == "closed":
                        validate_final(
                            message,
                            operation,
                            request,
                            report["actual_choices"],
                            delivered_choices,
                            result_queries,
                        )
                        report["child_final"] = message
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
                            },
                            "source3_pipe.ready",
                        )
                        handoff = message["bootstrap_control_release"]
                        if (
                            not isinstance(handoff, dict)
                            or handoff.get("schema")
                            != "sts2.host-runtime/reference-controller-handoff-1"
                            or handoff.get("runtime_instance_id") != message["runtime_instance_id"]
                            or handoff.get("controller") is not None
                            or handoff.get("basis") != "fresh_control_observation_after_close"
                        ):
                            raise fail("bootstrap_control_release_unconfirmed")
                        if original is not None or message["endpoint"] != prepared["endpoint"]:
                            raise fail("child_host_binding_changed")
                        runtime = message["runtime_instance_id"]
                        observed = _status(app)
                        if observed["runtime_instance_id"] != runtime or observed[
                            "recording_lifecycle"
                        ] not in {"ready", "closed"}:
                            raise fail("source_start_context_unavailable")
                        body = _body(observed, "start_new_session", actor)
                        report["source_start_request"] = body
                        atomic_json(request.output / "source-start-request.json", body)
                        cancel.check()
                        started = app.control_native_recording(body)
                        report["source_start"] = started
                        # An accepted exact new session is ours for cleanup even
                        # if a later profile/declaration gate rejects acquisition.
                        owned = started.get("status")
                        if (
                            started.get("accepted") is True
                            and isinstance(owned, dict)
                            and owned.get("runtime_instance_id") == runtime
                            and owned.get("recording_session_id") is not None
                            and owned.get("recording_session_id")
                            != observed["recording_session_id"]
                            and isinstance(owned.get("source"), dict)
                        ):
                            original = owned
                        if (
                            started.get("accepted") is not True
                            or started.get("pending") is not False
                            or started.get("command_id") != body["command_id"]
                            or started["status"]["runtime_instance_id"] != runtime
                            or started["status"]["recording_lifecycle"] != "recording"
                            or started["status"]["recording_session_id"]
                            == observed["recording_session_id"]
                            or started["status"].get("capture_profile_id")
                            != "native-logical-source-v3"
                        ):
                            raise fail("known_source_start_required")
                        original = started["status"]
                        source = original["source"]
                        if (
                            source["declaration"].get("source_kind") != "agent_protocol"
                            or source["declaration"].get("actor_id") != actor
                            or source["declaration"].get("machine_verifiable") is not False
                        ):
                            raise fail("original_agent_protocol_declaration_required")
                        _status(app, original)
                        atomic_json(request.output / "source-start.json", started)
                        cancel.check()
                        child.send(
                            {
                                **common,
                                "type": "source_ready",
                                "source_context": {
                                    "runtime_instance_id": runtime,
                                    "recording_session_id": original["recording_session_id"],
                                    "source_segment_id": source["segment_id"],
                                    "source_epoch_id": source["epoch_id"],
                                    "declaration": source["declaration"],
                                },
                            }
                        )
                    elif kind == "current":
                        cancel.check()
                        if original is None or runtime is None:
                            raise fail("current_before_known_source_start")
                        object_fields(
                            message,
                            {
                                "schema",
                                "type",
                                "operation_id",
                                "message_id",
                                "ordinal",
                                "basis",
                                "observation_base64",
                                "catalog",
                            },
                            "source3_pipe.current",
                        )
                        if (
                            type(message["ordinal"]) is not int
                            or message["ordinal"] != report["actual_choices"] + 1
                            or message["ordinal"] > request.max_submissions
                            or pending_choice is not None
                        ):
                            raise fail("original_choice_order_changed")
                        _status(app, original)
                        observation, catalog = decode_current(
                            message, runtime, request.max_input_bytes
                        )
                        choice = teacher.decide(observation, catalog)
                        pending_choice = {
                            "ordinal": message["ordinal"],
                            "basis": message["basis"],
                            "action": next(
                                (a for a in catalog if a["action_id"] == choice.action_id), None
                            ),
                        }
                        cancel.check()
                        child.send(
                            {
                                **common,
                                "type": "choice",
                                "ordinal": message["ordinal"],
                                "basis": message["basis"],
                                "action_id": choice.action_id,
                                "stop_reason": choice.reason if choice.action_id is None else None,
                                "teacher_state": choice.state,
                            }
                        )
                    elif kind == "result":
                        object_fields(
                            message,
                            {
                                "schema",
                                "type",
                                "operation_id",
                                "message_id",
                                "ordinal",
                                "request_id",
                                "lookup_status",
                                "result",
                                "result_queries",
                            },
                            "source3_pipe.result",
                        )
                        if (
                            pending_choice is None
                            or pending_choice["action"] is None
                            or type(message["ordinal"]) is not int
                            or message["ordinal"] != pending_choice["ordinal"]
                            or message["lookup_status"] not in {"terminal", "pending"}
                            or type(message["result_queries"]) is not int
                            or not 0 <= message["result_queries"] <= 40
                            or not isinstance(message["request_id"], str)
                        ):
                            raise fail("original_result_order_changed")
                        result = message["result"]
                        if isinstance(result, dict) and (
                            result.get("snapshot_id") != pending_choice["basis"]["snapshot_id"]
                            or result.get("action") is not None
                            and result.get("action") != pending_choice["action"]
                        ):
                            raise fail("original_result_basis_changed")
                        if (
                            message["lookup_status"] == "pending"
                            and result is not None
                            or message["lookup_status"] == "terminal"
                            and not isinstance(result, dict)
                        ):
                            raise fail("original_result_status_changed")
                        pending_choice_action = pending_choice["action"]
                        pending_choice = None
                        result_queries += message["result_queries"]
                        report["actual_choices"] += 1
                        atomic_json(
                            request.output / f"original-result-{message['ordinal']:04d}.json",
                            message,
                        )
                        allowed = False
                        reason = "original_result_unresolved"
                        if message["lookup_status"] == "terminal" and isinstance(result, dict):
                            if result.get("request_id") != message["request_id"]:
                                raise fail("original_result_identity_changed")
                            if result.get("delivery") == "delivered":
                                if result.get("action") != pending_choice_action:
                                    raise fail("delivered_original_action_missing")
                                delivered_choices += 1
                                try:
                                    _status(app, original)
                                    allowed = not cancel.requested.is_set()
                                    reason = "known_original_source"
                                except BoundaryError as error:
                                    reason = error.code
                        child.send(
                            {**common, "type": "continue", "continue": allowed, "reason": reason}
                        )
                    elif kind == "quiesced":
                        retain_quiesced(message)
                        source_closed, status, close_outcome = close_original_once()
                        child.send(
                            {
                                **common,
                                "type": "source_closed",
                                "known_closed": source_closed,
                                "source_status": status,
                                "close_outcome": close_outcome,
                            }
                        )
            except BaseException as error:  # noqa: BLE001 - own cleanup also covers signals/startup failure
                error_code = (
                    error.code if isinstance(error, BoundaryError) else "collection_parent_failed"
                )
                if child is not None:
                    child.stop(error_code)
                    # Accepted Start remains ours before SourceReady transmission;
                    # Node quiesces after requesting admission, even without its ACK.
                    close_original_once()
                    # Keep the genuine App alive to service a final quiesced Close if possible.
                    end = time.monotonic() + 120
                    while not terminal and time.monotonic() < end:
                        try:
                            message = child.receive()
                        except Exception:
                            break
                        if message is None:
                            continue
                        if (
                            message.get("type") == "closed"
                            and message.get("operation_id") == operation
                        ):
                            try:
                                validate_final(
                                    message,
                                    operation,
                                    request,
                                    report["actual_choices"],
                                    delivered_choices,
                                    result_queries,
                                )
                            except BoundaryError as final_error:
                                report["child_final_error"] = final_error.code
                                break
                            report["child_final"] = message
                            terminal = True
                            break
                        if (
                            message.get("type") == "quiesced"
                            and message.get("operation_id") == operation
                        ):
                            try:
                                retain_quiesced(message)
                            except BoundaryError as quiesced_error:
                                report["quiesced_error"] = quiesced_error.code
                            source_closed, status, close_outcome = close_original_once()
                            child.send(
                                {
                                    "schema": PIPE_SCHEMA,
                                    "type": "source_closed",
                                    "operation_id": operation,
                                    "message_id": message["message_id"],
                                    "known_closed": source_closed,
                                    "source_status": status,
                                    "close_outcome": close_outcome,
                                }
                            )
            finally:
                # A known accepted Start remains ours even if SourceReady was
                # never delivered or the peer disappeared before quiescing.
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
            final: dict[str, Any] = (
                report["child_final"] if isinstance(report.get("child_final"), dict) else {}
            )
            host = final.get("host_exit") if isinstance(final, dict) else None
            exited = report.get("child")
            clean_exit = (
                isinstance(host, dict)
                and host.get("code") == 0
                and host.get("signal") is None
                and host.get("forced") is False
                and isinstance(exited, dict)
                and exited.get("exit_code") == 0
            )
            release = final.get("control_release") if isinstance(final, dict) else None
            known_cleanup = (
                terminal
                and clean_exit
                and isinstance(release, dict)
                and release.get("confirmed") is True
                and source_closed
                and final.get("source_closed") is True
                and not final.get("cleanup_errors")
                and not report.get("application_close_unconfirmed")
            )
            report.update(
                error_code=error_code,
                teacher_state=teacher.state(),
                source_closed=source_closed,
                close_sent=close_sent,
                cancellation_requests=cancel.signals,
                known_delivered_choices=delivered_choices,
                submissions=final.get("counts", {}).get("submissions"),
                pending_request_id=final.get(
                    "pending_request_id", report.get("pending_request_id")
                ),
                last_submission=final.get("last_submission", report.get("last_submission")),
                status="completed"
                if known_cleanup
                and error_code is None
                and final.get("reason") == "target_choices_reached"
                else "partial"
                if known_cleanup
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
