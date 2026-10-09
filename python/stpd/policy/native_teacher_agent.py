"""Explicit program teacher over the public AgentSession port; no model/weights.

Runtime owns acquisition, controller, delivery, Await, deadlines and evidence.
This adapter stages disclosed script state, committing only an exact ConsumeACK.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import sys
import uuid
from pathlib import Path
from typing import Any, TextIO

from spireagent.json_boundary import BoundaryError, decode_json, json_bytes, object_fields
from stpd.fullrun.native_structured_inputs import PROFILE, SCOPE
from stpd.fullrun.native_structured_sequences import NativeUnit, native_advance, qualify_native
from stpd.policy.native_public_teacher import (
    TEACHER_ID,
    TEACHER_VERSION,
    NativePublicTeacher,
    TeacherChoice,
)
from stpd.policy.native_task import (
    map_timed_ready_summary_task_spec,
    observe_ready_summary,
    public_map_travel_timing_spec,
)

ROOT = Path(__file__).resolve().parents[2]
SESSION_SCHEMA = "sts2.policy-runtime/agent-session-1"
PROTOCOL = "sts2.policy-runtime/agent-session-ndjson-1"
ARTIFACT_SCHEMA = "stpd/native-program-teacher-artifact-v1"
AGENT_ID = "stpd-native-public-program-teacher"
AGENT_VERSION = "1.1.0"
RECHECK_TIMEOUT_MS = 250
MAX_MESSAGE_BYTES = 96 * 1024 * 1024
CODE_FILES = (
    "spireagent/__init__.py",
    "spireagent/encoding.py",
    "spireagent/json_boundary.py",
    "stpd/__init__.py",
    "stpd/contracts.py",
    "stpd/linear_q.py",
    "stpd/representation.py",
    "stpd/canonical.py",
    "stpd/fullrun/__init__.py",
    "stpd/fullrun/contracts.py",
    "stpd/fullrun/representation.py",
    "stpd/fullrun/semantic_projection.py",
    "stpd/policy/__init__.py",
    "stpd/fullrun/native_structured_inputs.py",
    "stpd/fullrun/native_structured_sequences.py",
    "stpd/fullrun/structured_inputs.py",
    "stpd/fullrun/structured_tree.py",
    "stpd/fullrun/text_menu_inputs.py",
    "stpd/policy/native_public_teacher.py",
    "stpd/policy/native_teacher_agent.py",
    "stpd/policy/native_task.py",
)
INPUT_BODY = {
    "schema": "stpd/native-program-teacher-input-spec-v1",
    "id": "stpd-native-program-teacher-current-v1",
    "version": "1.0.0",
    "profile": PROFILE,
    "policy_input": "qualified_complete_current_observation_and_complete_original_C",
    "history_mode": "sampled_current",
    "eager_scope": list(SCOPE),
    "readiness": "unchanged_unit_or_empty_nonterminal_current_no_consume_no_state_advance",
    "terminal": "qualified_public_ready_summary_may_consume_without_N",
    "state": "explicit_script_state_proposed_then_exact_ACK_committed",
    "reset": "fresh_live_session_and_explicit_segment_only",
    "state_recovery": "none",
    "I": False,
    "F": False,
    "learned": False,
}
INPUT_SPEC = {
    "id": INPUT_BODY["id"],
    "version": "1.0.0",
    "sha256": hashlib.sha256(json_bytes(INPUT_BODY)).hexdigest(),
}
AGENT_SPEC = {
    "schema": "stpd/native-program-teacher-agent-spec-v1",
    "id": AGENT_ID,
    "version": AGENT_VERSION,
    "teacher": {"id": TEACHER_ID, "version": TEACHER_VERSION},
    "learned": False,
    "model_bindings": [],
    "scores": None,
    "choice": "explicit_public_teacher_original_complete_C_member",
    "timing": "explicit_Await_public_map_travel_or_expected_public_owner_or_focus_pending",
    "timing_policy": public_map_travel_timing_spec(),
    "task_spec": map_timed_ready_summary_task_spec(),
    "recheck_timeout_ms": RECHECK_TIMEOUT_MS,
    "arrival": "fresh_qualified_public_observation_not_delivery_timer_or_Close_proof",
    "unsupported": "Close_with_original_reason",
    "unknown_delivery": "Runtime_handoff_never_resubmit",
    "state_recovery": "none",
    "max_browse_choices": 12,
}


def code_identity() -> tuple[str, list[dict[str, Any]]]:
    checksum = hashlib.sha256(b"stpd.native-program-teacher.code.v1\0")
    files = []
    for relative in CODE_FILES:
        path = ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise BoundaryError("native_teacher_agent", "code_closure_unavailable")
        raw = path.read_bytes()
        checksum.update(relative.encode() + b"\0" + raw + b"\0")
        files.append(
            {"path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        )
    return checksum.hexdigest(), files


def descriptor() -> dict[str, Any]:
    sha, files = code_identity()
    return {
        "schema": ARTIFACT_SCHEMA,
        "agent_spec": copy.deepcopy(AGENT_SPEC),
        "input_spec_body": copy.deepcopy(INPUT_BODY),
        "input_spec": dict(INPUT_SPEC),
        "adapter": {
            "id": AGENT_ID,
            "version": AGENT_VERSION,
            "protocol": PROTOCOL,
            "code_sha256": sha,
        },
        "code_files": files,
        "runtime_provenance": {
            "dependency_lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
            "python_implementation": sys.implementation.name,
            "python_version": list(sys.version_info[:3]),
            "python_cache_tag": sys.implementation.cache_tag,
            "external_inference_dependencies": [],
        },
    }


def write_artifact(path: Path) -> dict[str, Any]:
    raw = json_bytes(descriptor())
    with path.open("xb") as handle:
        handle.write(raw)
    return {
        "id": AGENT_ID + "-" + hashlib.sha256(raw).hexdigest()[:16],
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


class NativeTeacherAgent:
    def __init__(self, manifest_path: Path) -> None:
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise BoundaryError("native_teacher_agent", "manifest_path_required")
        manifest = decode_json(manifest_path.read_bytes())
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema") != "sts2.policy-runtime/agent-manifest-1"
        ):
            raise BoundaryError("native_teacher_agent", "manifest_required")
        artifact_path = Path(manifest["artifact"]["path"])
        if not artifact_path.is_absolute():
            artifact_path = manifest_path.parent / artifact_path
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise BoundaryError("native_teacher_agent", "artifact_path_required")
        raw = artifact_path.read_bytes()
        expected = descriptor()
        if (
            hashlib.sha256(raw).hexdigest() != manifest["artifact"]["sha256"]
            or decode_json(raw) != expected
            or manifest.get("adapter") != expected["adapter"]
            or manifest.get("input", {}).get("input_spec") != INPUT_SPEC
            or manifest["input"].get("history_mode") != "sampled_current"
            or manifest["input"].get("state_recovery")
            != {"mode": "none", "max_state_bytes": 0, "model_bindings": []}
        ):
            raise BoundaryError("native_teacher_agent", "real_code_artifact_manifest_binding")
        self.manifest = manifest
        self.teacher = NativePublicTeacher()
        self.unit: NativeUnit | None = None
        self.continuity: str | None = None
        self.consumption_id: str | None = None
        self.state_version = 0
        self.acquisition_id: str | None = None
        self.choice: TeacherChoice | None = None
        self.pending: dict[str, Any] | None = None

    def begin_next(self, value: dict[str, Any]) -> None:
        object_fields(
            value,
            {
                "continuity_token",
                "consumption_id",
                "state_version",
                "basis_acquisition_id",
                "received_cursor",
            },
            "native_teacher_agent.next",
        )
        if (
            self.pending is not None
            or not isinstance(value["continuity_token"], str)
            or not value["continuity_token"]
            or value["consumption_id"] != self.consumption_id
            or type(value["state_version"]) is not int
            or value["state_version"] != self.state_version
            or value["basis_acquisition_id"] != self.acquisition_id
            or self.continuity is not None
            and value["continuity_token"] != self.continuity
        ):
            raise BoundaryError("native_teacher_agent", "next_watermark_binding")

    def wait(self, next_input: dict[str, Any]) -> dict[str, Any]:
        cursor = next_input["received_cursor"]
        if not isinstance(cursor, str) or not cursor:
            raise BoundaryError("native_teacher_agent", "known_await_cursor_required")
        return self.output(
            {
                "type": "await",
                "after_cursor": cursor,
                "condition": "any_event",
                "timeout_ms": RECHECK_TIMEOUT_MS,
            },
            next_input["continuity_token"],
        )

    def output(self, directive: dict[str, Any], continuity: str) -> dict[str, Any]:
        return {
            "continuity_token": continuity,
            "consumption_id": self.consumption_id,
            "state_version": self.state_version,
            "directive": directive,
        }

    def propose_current(
        self, result: dict[str, Any], next_input: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        object_fields(result, {"method", "value", "acquisition_id"}, "native_teacher_agent.query")
        value = object_fields(
            result["value"],
            {"capture", "observation", "catalog", "catalog_materialized"},
            "native_teacher_agent.current",
        )
        capture, observation, catalog = value["capture"], value["observation"], value["catalog"]
        if (
            result["method"] != "current"
            or value["catalog_materialized"] is not True
            or not isinstance(result["acquisition_id"], str)
            or not result["acquisition_id"]
            or not isinstance(capture, dict)
            or not isinstance(observation, dict)
            or capture.get("schema") != "sts2.player-environment/native-logical-capture-1"
            or capture.get("input_profile") != PROFILE
            or capture.get("snapshot_id") != observation.get("snapshot_id")
            or capture.get("session") != observation.get("session")
            or capture.get("stream_generation")
            != observation.get("catalog", {}).get("stream_generation")
            or capture.get("scope_id") != observation["catalog"].get("scope_id")
        ):
            raise BoundaryError("native_teacher_agent", "complete_current_binding")
        _, unit = qualify_native(observation, catalog)
        if not native_advance(self.unit, unit) or (
            not catalog and not observe_ready_summary(observation).agent_task_complete
        ):
            return "directive", self.wait(next_input)
        staged = copy.deepcopy(self.teacher)
        choice = staged.decide(observation, catalog)
        report = {
            "acquisition_id": result["acquisition_id"],
            "input_spec": dict(INPUT_SPEC),
            "continuity_token": next_input["continuity_token"],
            "previous_consumption_id": self.consumption_id,
            "consumption_id": "teacher-consume-" + uuid.uuid4().hex,
            "state_version": self.state_version + 1,
            "advanced": True,
        }
        self.pending = {"report": report, "teacher": staged, "unit": unit, "choice": choice}
        return "consumed", report

    def acknowledge(self, ack: dict[str, Any]) -> None:
        if self.pending is None:
            raise BoundaryError("native_teacher_agent", "ACK_without_proposal")
        object_fields(
            ack,
            {"consumption_id", "acquisition_id", "state_version", "advanced", "prefix"},
            "native_teacher_agent.ack",
        )
        report = self.pending["report"]
        prefix = object_fields(
            ack["prefix"],
            {
                "continuity_token",
                "history_mode",
                "consumption_mode",
                "received_cursor",
                "consumed_publication_index",
                "omissions",
            },
            "native_teacher_agent.prefix",
        )
        omissions = object_fields(
            prefix["omissions"],
            {"received_unconsumed_count", "missing_scopes", "gap"},
            "native_teacher_agent.omissions",
        )
        if (
            any(
                ack[key] != report[key] or type(ack[key]) is not type(report[key])
                for key in ("consumption_id", "acquisition_id", "state_version", "advanced")
            )
            or type(ack["state_version"]) is not int
            or type(ack["advanced"]) is not bool
            or ack["advanced"] is not True
            or prefix["continuity_token"] != report["continuity_token"]
            or prefix["history_mode"] != "sampled_current"
            or prefix["consumption_mode"] != "once_per_occurrence"
            or prefix["consumed_publication_index"] is not None
            or not isinstance(prefix["received_cursor"], str)
            or not prefix["received_cursor"]
            or omissions["missing_scopes"] != []
            or omissions["gap"] is not None
            or type(omissions["received_unconsumed_count"]) is not int
            or omissions["received_unconsumed_count"] < 0
        ):
            raise BoundaryError("native_teacher_agent", "exact_ACK_required")
        self.teacher, self.unit, self.choice = (
            self.pending["teacher"],
            self.pending["unit"],
            self.pending["choice"],
        )
        self.continuity, self.consumption_id = report["continuity_token"], report["consumption_id"]
        self.state_version, self.acquisition_id = report["state_version"], report["acquisition_id"]
        self.pending = None

    def directive(self, next_input: dict[str, Any]) -> dict[str, Any]:
        if self.pending is not None or self.choice is None:
            raise BoundaryError("native_teacher_agent", "known_ACK_before_directive")
        if self.choice.directive == "await":
            return self.wait(next_input)
        if self.choice.directive == "close":
            return self.output(
                {"type": "close", "reason": self.choice.reason}, next_input["continuity_token"]
            )
        if self.choice.action_id is None or self.acquisition_id is None:
            raise BoundaryError("native_teacher_agent", "original_member_required")
        return self.output(
            {
                "type": "act",
                "basis_acquisition_id": self.acquisition_id,
                "selection": {"kind": "handle", "action_id": self.choice.action_id},
                "scores": None,
            },
            next_input["continuity_token"],
        )


def serve(agent: NativeTeacherAgent, source: TextIO, sink: TextIO) -> int:
    def emit(value: dict[str, Any]) -> None:
        sink.write(json_bytes(value).decode())
        sink.flush()

    emit({"schema": SESSION_SCHEMA, "message_type": "ready", "adapter": agent.manifest["adapter"]})
    session: tuple[str, int] | None = None
    seen: set[str] = set()
    pending_next: tuple[dict[str, Any], dict[str, Any]] | None = None
    pending_query = pending_consume = None
    maximum = agent.manifest["limits"]["max_message_bytes"]
    while line := source.readline(maximum + 1):
        if not line.endswith("\n") or len(line.encode()) > maximum:
            raise BoundaryError("native_teacher_agent", "bounded_message_framing")
        value = decode_json(line)
        kind = value.get("message_type")
        field = (
            "completion"
            if kind == "consume_ack"
            else "result"
            if kind == "query_result"
            else "input"
        )
        value = object_fields(
            value,
            {"schema", "message_type", "session_id", "recovery_epoch", "request_id", field},
            "native_teacher_agent.message",
        )
        common = {key: value[key] for key in ("session_id", "recovery_epoch", "request_id")}
        context = (common["session_id"], common["recovery_epoch"])
        if (
            value["schema"] != SESSION_SCHEMA
            or not isinstance(common["request_id"], str)
            or not common["request_id"]
            or not isinstance(context[0], str)
            or not context[0]
            or type(context[1]) is not int
            or context[1] < 0
            or session is not None
            and (context[0] != session[0] or context[1] < session[1])
        ):
            raise BoundaryError("native_teacher_agent", "session_binding")
        if kind == "consume_ack":
            if (
                context != session
                or common["request_id"] != pending_consume
                or pending_next is None
            ):
                raise BoundaryError("native_teacher_agent", "ACK_request_binding")
            agent.acknowledge(value["completion"])
            pending_consume = None
            next_common, next_input = pending_next
            emit(
                {
                    "schema": SESSION_SCHEMA,
                    "message_type": "directive",
                    **next_common,
                    "output": agent.directive(next_input),
                }
            )
            pending_next = None
            continue
        if kind == "query_result":
            if context != session or common["request_id"] != pending_query or pending_next is None:
                raise BoundaryError("native_teacher_agent", "query_result_binding")
            pending_query = None
            next_common, next_input = pending_next
            output_kind, output = agent.propose_current(value["result"], next_input)
            if output_kind == "consumed":
                pending_consume = "child-consume-" + uuid.uuid4().hex
                emit(
                    {
                        "schema": SESSION_SCHEMA,
                        "message_type": "consumed",
                        **common,
                        "request_id": pending_consume,
                        "completion": output,
                    }
                )
            else:
                emit(
                    {
                        "schema": SESSION_SCHEMA,
                        "message_type": "directive",
                        **next_common,
                        "output": output,
                    }
                )
                pending_next = None
            continue
        if (
            kind != "next"
            or common["request_id"] in seen
            or len(seen) >= 65536
            or pending_next is not None
        ):
            raise BoundaryError("native_teacher_agent", "new_idle_Next_required")
        seen.add(common["request_id"])
        session = context
        agent.begin_next(value["input"])
        pending_next = (common, copy.deepcopy(value["input"]))
        pending_query = "child-query-" + uuid.uuid4().hex
        emit(
            {
                "schema": SESSION_SCHEMA,
                "message_type": "query",
                **common,
                "request_id": pending_query,
                "input": {
                    "method": "current",
                    "arguments": {"eager_scope": list(SCOPE), "expected_snapshot_id": None},
                },
            }
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    return serve(NativeTeacherAgent(args.manifest), sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
