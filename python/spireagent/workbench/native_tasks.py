"""Exact native recording-to-model handoff; no browser or gameplay authority."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import endpoint
from spireagent.workbench.hub_client import NoRedirect


class NativeTasks:
    address = "http://127.0.0.1:15528"

    def __init__(self) -> None:
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    @staticmethod
    def _recording_identifier(value: object, *, nullable: bool = False) -> bool:
        return (nullable and value is None) or (
            isinstance(value, str) and value not in {".", ".."}
            and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value) is not None
        )

    @classmethod
    def _source_declaration(cls, value: object) -> bool:
        return (
            isinstance(value, dict)
            and set(value) == {"source_kind", "actor_id", "declaration_id", "machine_verifiable"}
            and isinstance(value.get("source_kind"), str)
            and value.get("source_kind") in {"declared_human", "agent_native_ui", "agent_protocol", "unknown"}
            and cls._recording_identifier(value.get("actor_id"))
            and cls._recording_identifier(value.get("declaration_id"))
            and value.get("machine_verifiable") is False
        )

    @classmethod
    def _recording_status_valid(cls, value: object) -> bool:
        fields = {"schema", "runtime_instance_id", "recording_session_id", "recording_lifecycle",
                  "capture_profile_id", "closeout_status", "source", "health", "non_claims"}
        if not isinstance(value, dict) or set(value) != fields:
            return False
        if (value["schema"] != "sts2.platform/recording-status-1"
                or not cls._recording_identifier(value["runtime_instance_id"])
                or not cls._recording_identifier(value["recording_session_id"], nullable=True)
                or not cls._recording_identifier(value["capture_profile_id"], nullable=True)
                or not isinstance(value["recording_lifecycle"], str)
                or value["recording_lifecycle"] not in {"ready", "recording", "paused", "closing", "closed"}
                or not isinstance(value["closeout_status"], str)
                or not re.fullmatch(r"[a-z_]{1,96}", value["closeout_status"])):
            return False
        health = value["health"]
        if (not isinstance(health, dict) or set(health) != {"append_health", "disk_health", "error"}
                or any(not isinstance(health[key], str) or not re.fullmatch(r"[a-z_]{1,96}", health[key])
                       for key in ("append_health", "disk_health"))
                or (health["error"] is not None and (not isinstance(health["error"], str)
                    or not re.fullmatch(r"[a-z0-9_]{1,96}", health["error"])) )):
            return False
        if value["non_claims"] != ["not_machine_proof_of_human_origin", "not_native_coverage_qualified",
                                   "not_causal_transition_proof", "not_research_admission", "not_g2_v1_approved"]:
            return False
        source = value["source"]
        if source is None:
            return value["capture_profile_id"] not in {"native-logical-source-v2", "native-logical-source-v3"}
        source_fields = {"epoch_id", "segment_id", "declaration", "observations", "inputs", "pending_inputs",
                         "epochs", "gaps", "accounting_complete", "error"}
        return (
            isinstance(source, dict) and set(source) == source_fields
            and value["capture_profile_id"] in {"native-logical-source-v2", "native-logical-source-v3"}
            and all(cls._recording_identifier(source[key]) for key in ("epoch_id", "segment_id"))
            and cls._source_declaration(source["declaration"])
            and all(type(source[key]) is int and 0 <= source[key] <= 2**63 - 1
                    for key in ("observations", "inputs", "pending_inputs", "epochs", "gaps"))
            and type(source["accounting_complete"]) is bool
            and (source["error"] is None or (isinstance(source["error"], str)
                 and re.fullmatch(r"[a-z0-9_]{1,96}", source["error"]) is not None))
        )

    def _recording_request(self, route: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        def exact_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        request = Request(self.address + route, data=json.dumps(body).encode() if body is not None else None,
                          headers={"Accept": "application/json", "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=15 if body is not None else 2) as response:
                raw = response.read(16385)
            if len(raw) > 16384:
                raise ValueError
            value = json.loads(raw, object_pairs_hook=exact_object)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except HTTPError as error:
            # Only the owner/transport's closed non-dispatch response permits that claim.
            try:
                raw = error.read(4097)
                value = json.loads(raw, object_pairs_hook=exact_object) if len(raw) <= 4096 else None
                if error.code == 503 and value == {"error": "native_task_not_dispatched"}:
                    raise BoundaryError("recording", "native_recording_not_dispatched") from None
                if (error.code == 409 and isinstance(value, dict) and set(value) == {"error", "detail"}
                        and value["error"] == "task_handoff_rejected"
                        and isinstance(value["detail"], str)
                        and value["detail"] in {"recording_game_instance_changed", "invalid_recording_fields",
                            "invalid_recording_schema", "invalid_recording_identifier", "recording_runtime_required",
                            "invalid_source_command_schema", "invalid_recording_command_id", "invalid_recording_kind",
                            "source_declaration_not_attestation", "invalid_source_declaration",
                            "source3_profile_and_declaration_required", "source_declaration_and_segment_required",
                            "source_command_fields_not_applicable", "invalid_task_request"}):
                    raise BoundaryError("recording", "native_recording_rejected") from None
            except BoundaryError:
                raise
            except (ValueError, TypeError, OSError, RecursionError):
                pass
        except (URLError, OSError, ValueError, TypeError, RecursionError):
            pass
        raise BoundaryError("recording", "native_recording_command_unknown" if body else "native_recording_unavailable") from None

    def recording_status(self, connector_endpoint: object) -> dict[str, Any]:
        expected = self.connector_instance(connector_endpoint)
        value = self._recording_request("/v1/tasks/recording/status")
        if not self._recording_status_valid(value):
            raise BoundaryError("recording", "native_recording_unavailable")
        if value["runtime_instance_id"] != expected or self.connector_instance(connector_endpoint) != expected:
            raise BoundaryError("recording", "native_recording_game_identity_mismatch")
        return value

    def recording_command(self, connector_endpoint: object, observed: dict[str, Any], kind: str, *,
                          source_declaration: dict[str, Any] | None = None,
                          command_id: str | None = None) -> dict[str, Any]:
        if (not self._recording_status_valid(observed) or not isinstance(kind, str)
                or kind not in {"start_new_session", "pause", "resume", "change_source", "close"}
                or (kind in {"start_new_session", "change_source"} and not self._source_declaration(source_declaration))
                or (kind not in {"start_new_session", "change_source"} and source_declaration is not None)
                or (kind == "change_source" and (observed["recording_lifecycle"] != "paused" or observed["source"] is None))):
            raise BoundaryError("recording", "invalid_native_recording_command")
        command_id = command_id if command_id is not None else str(uuid4())
        if not isinstance(command_id, str) or not re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", command_id):
            raise BoundaryError("recording", "invalid_native_recording_command")
        expected = self.connector_instance(connector_endpoint)
        if observed["runtime_instance_id"] != expected:
            raise BoundaryError("recording", "native_recording_game_identity_mismatch")
        body = {"schema": "sts2.platform/recording-request-1", "runtime_instance_id": expected,
                "recording_session_id": observed["recording_session_id"], "command": {
                    "schema": "sts2.ai-platform/recording-command-3", "command_id": command_id, "kind": kind,
                    "capture_profile_id": "native-logical-source-v3" if kind == "start_new_session" else None,
                    "source_declaration": source_declaration,
                    "expected_source_segment_id": observed["source"]["segment_id"] if kind == "change_source" else None}}
        value = self._recording_request("/v1/tasks/recording/command", body)
        if (set(value) != {"schema", "command_id", "accepted", "pending", "code", "status"}
                or value["schema"] != "sts2.platform/recording-result-1" or value["command_id"] != command_id
                or type(value["accepted"]) is not bool or type(value["pending"]) is not bool
                or not isinstance(value["code"], str) or not re.fullmatch(r"[a-z0-9_]{1,96}", value["code"])
                or not self._recording_status_valid(value["status"])):
            raise BoundaryError("recording", "native_recording_command_unknown")
        if value["status"]["runtime_instance_id"] != expected or self.connector_instance(connector_endpoint) != expected:
            raise BoundaryError("recording", "native_recording_command_unknown")
        return value

    def request(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        route = "/v1/tasks/status" if body is None else "/v1/tasks/prepare-model"
        request = Request(
            self.address + route,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=15 if body is not None else 2) as response:
                raw = response.read(16385)
            if len(raw) > 16384:
                raise ValueError
            value = json.loads(raw)
            if (
                not isinstance(value, dict)
                or value.get("schema") != "sts2.platform/task-status-1"
                or not isinstance(value.get("runtime_instance_id"), str)
                or type(value.get("ready_for_model")) is not bool
                or "recording_session_id" not in value
                or value.get("recording_lifecycle")
                not in {"ready", "recording", "paused", "closing", "closed"}
                or (
                    value.get("recording_session_id") is not None
                    and not isinstance(value["recording_session_id"], str)
                )
                or (
                    value.get("ready_for_model")
                    and value.get("recording_lifecycle") not in {"ready", "closed"}
                )
            ):
                raise ValueError
            return value
        except (HTTPError, URLError, OSError, ValueError, TypeError):
            raise BoundaryError(
                "local_model", "native_task_command_unknown" if body else "native_task_unavailable"
            ) from None

    @staticmethod
    def bound_connector(value: object) -> str:
        """Only the endpoint persisted when this Runtime was launched is usable."""
        try:
            address = endpoint(value)
            if not address.startswith(
                ("http://127.0.0.1:", "http://localhost:", "http://[::1]:")
            ):
                raise ValueError
            return address
        except (BoundaryError, ValueError, TypeError):
            raise BoundaryError("local_model", "runtime_connector_binding_required") from None

    def connector_instance(self, connector_endpoint: object) -> str:
        address = self.bound_connector(connector_endpoint)
        request = Request(
            address + "/api/player-environment/capabilities",
            headers={"Accept": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=2) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError
            value = json.loads(raw)
            # Consume just the public identity contract. Connector/Runtime still
            # own capability validation, control and gameplay legality.
            required = {
                "protocol_version": "1.0.0",
                "snapshot_schema": "sts2.player-environment/snapshot-1",
                "action_schema": "sts2.player-environment/action-1",
                "receipt_schema": "sts2.player-environment/receipt-1",
                "control_schema": "sts2.player-environment/control-1",
            }
            if not isinstance(value, dict) or any(
                value.get(key) != expected for key, expected in required.items()
            ):
                raise ValueError
            host = value.get("host")
            identity = host.get("runtime_instance_id") if isinstance(host, dict) else None
            if not isinstance(identity, str) or not identity:
                raise ValueError
            return identity
        except (HTTPError, URLError, OSError, ValueError, TypeError):
            raise BoundaryError("local_model", "connector_identity_unavailable") from None

    @staticmethod
    def runtime_instance(runtime: dict[str, Any]) -> str | None:
        if "environment" not in runtime:
            raise BoundaryError("local_model", "runtime_game_identity_required")
        environment = runtime["environment"]
        if environment is None:
            return None
        identity = environment.get("runtime_instance_id") if isinstance(environment, dict) else None
        if not isinstance(identity, str) or not identity:
            raise BoundaryError("local_model", "runtime_game_identity_required")
        return identity

    @staticmethod
    def confirm_runtime(runtime: dict[str, Any], expected: str) -> None:
        instance = NativeTasks.runtime_instance(runtime)
        if instance is not None and instance != expected:
            raise BoundaryError("local_model", "native_task_game_identity_mismatch")

    def prepare_model(
        self, runtime: dict[str, Any], connector_endpoint: object
    ) -> dict[str, Any]:
        instance = self.runtime_instance(runtime)
        expected = self.connector_instance(connector_endpoint)
        # Human Runtime has no environment before its first tick. Do not run a
        # tick to discover identity: read the bound Connector and compare bridge.
        if instance is not None and instance != expected:
            raise BoundaryError("local_model", "native_task_game_identity_mismatch")
        observed = self.request()
        if observed["runtime_instance_id"] != expected:
            raise BoundaryError("local_model", "native_task_game_identity_mismatch")
        if not observed["ready_for_model"]:
            # One bounded request only. Uncertain Close is never automatically retried.
            observed = self.request(
                {
                    "runtime_instance_id": expected,
                    "recording_session_id": observed["recording_session_id"],
                    "command_id": str(uuid4()),
                }
            )
        if observed["runtime_instance_id"] != expected:
            raise BoundaryError("local_model", "native_task_game_identity_mismatch")
        if not observed["ready_for_model"]:
            raise BoundaryError("local_model", "recording_close_pending_or_failed")
        if self.connector_instance(connector_endpoint) != expected:
            raise BoundaryError("local_model", "native_task_game_identity_mismatch")
        return observed
