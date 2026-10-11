"""Synthetic public World for the real Application/Runtime process seam.

Envelopes derive from existing Connector conformance fixtures. This server owns
only synthetic test facts; it never discovers a game or forwards a request.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from stpd.fullrun.native_structured_inputs import native_catalog_digest


def future():
    return (datetime.now(UTC) + timedelta(minutes=5)).isoformat()


class NativeEpisodeWorld:
    def __init__(self, repository: Path, caps: dict):
        self.caps = copy.deepcopy(caps)
        self.native = json.loads(
            (
                repository / "components/connector/contracts/fixtures/native-logical-v1.json"
            ).read_bytes()
        )["wire_samples"]
        self.requests = []
        self.sent_snapshots = []
        self.submitted = 0
        self.held = False
        self.public_sequence = 1
        self.actions = [
            {
                "action_id": "open-deck",
                "kind": "native_input",
                "verb": "open_run_deck",
                "label": "Run deck",
                "subject_referent_id": None,
                "arguments": [],
                "effect_domain": "native",
            }
        ]
        self.observation = copy.deepcopy(self.native["observation"])
        self.observation["session"]["runtime_instance_id"] = self.instance
        self.observation.update(status="interactive", snapshot_id="native-initial", revision=1)
        self.observation["interaction"].update(kind="native_map", content_schema="synthetic-public")
        self.observation["interaction"]["content"]["surface"] = {"kind": "native_map"}
        self.observation["referents"] = []
        self.observation["catalog"].update(
            snapshot_id="native-initial", total_count=1, digest=native_catalog_digest(self.actions)
        )
        self.capture = copy.deepcopy(self.native["capture"])
        self.capture["session"]["runtime_instance_id"] = self.instance
        self.capture.update(snapshot_id="native-initial", expires_at=future())
        self.bytes = json.dumps(self.observation, separators=(",", ":")).encode()
        self.capture.update(
            byte_count=len(self.bytes), sha256=hashlib.sha256(self.bytes).hexdigest()
        )
        self.subscription = copy.deepcopy(self.native["attach"]["subscription"])
        profile = json.loads(
            (
                repository / "components/connector/contracts/"
                "native-logical-publication-profile-v1.json"
            ).read_bytes()
        )
        self.seams = profile["required_seams"]
        self.subscription["coverage"] = self.seams
        self.subscription.update(starting_cursor="cursor-0", expires_at=future())
        world = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def dispatch(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                body = json.loads(raw) if raw else {}
                world.requests.append({"path": self.path, "body": body})
                try:
                    value = world.route(self.path, body)
                    if self.path.endswith("/snapshot"):
                        world.sent_snapshots.append(copy.deepcopy(value))
                    wire = json.dumps(value).encode()
                    self.send_response(200)
                except (KeyError, ValueError) as error:
                    wire = json.dumps({"error": str(error)}).encode()
                    self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(wire)))
                self.end_headers()
                self.wfile.write(wire)

            do_GET = dispatch
            do_POST = dispatch

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"

    @property
    def instance(self):
        return self.caps["host"]["runtime_instance_id"]

    def task_status(self):
        return {
            "schema": "sts2.platform/task-status-2",
            "runtime_instance_id": self.instance,
            "recording_session_id": None,
            "recording_lifecycle": "ready",
            "closeout_status": "not_started",
            "capture_profile_id": None,
            "source_segment_id": None,
            "source_declaration": None,
            "can_preserve_for_native_agent": False,
            "non_claims": [
                "not_machine_proof_of_human_origin",
                "not_native_coverage_qualified",
                "not_causal_transition_proof",
                "not_research_admission",
                "not_g2_v1_approved",
            ],
        }

    def public_snapshot(self):
        return {
            "protocol_version": "1.0.0",
            "schema": "sts2.player-environment/snapshot-1",
            "snapshot_id": f"public-{self.public_sequence}",
            "sequence": self.public_sequence,
            "observed_at": "2026-10-11T00:00:00.000Z",
            "status": "interactive",
            "persistent": None,
            "interaction": {
                "interaction_id": "combat",
                "kind": "combat_turn",
                "stage": "ready",
                "content_schema": "sts2.player-environment/surface/combat_turn-1",
                "content": {
                    "surface": {"kind": "combat_turn", "can_end_turn": True},
                    "context": {
                        "kind": "combat",
                        "round": self.public_sequence,
                        "is_play_phase": True,
                        "turn_owner": "player",
                    },
                },
                "capabilities": [],
            },
            "referents": [],
            "bound_actions": {
                "schema": "sts2.player-environment/bound-actions-1",
                "status": "complete",
                "materialized_count": 2,
                "total_count": 2,
                "limit": 64,
                "ordering_semantics": "connector_order",
                "actions": [
                    {
                        "bound_action_id": f"candidate-{self.public_sequence}-{i}",
                        "verb": "end_turn",
                        "interaction_id": "combat",
                        "subject_referent_id": None,
                        "arguments": [],
                        "label": "End turn",
                    }
                    for i in range(2)
                ],
            },
            "reads": [],
            "completeness": {
                "status": "complete",
                "visible_information": "synthetic",
                "interaction_discovery": "synthetic",
                "missing": [],
                "hidden_by_policy": [],
            },
            "session": {
                "runtime_instance_id": self.instance,
                "environment_fingerprint": "environment-fixture",
            },
            "information_policy": {
                "id": "fair-player",
                "scope": "synthetic",
                "includes_hidden_information": False,
                "unknown_field_behavior": "reject",
            },
        }

    def control(self):
        return {
            "protocol_version": "1.0.0",
            "schema": "sts2.player-environment/control-1",
            "runtime_instance_id": self.instance,
            "client": None,
            "controller": None,
        }

    def route(self, path, body):
        if path == "/v2/tasks/status":
            return self.task_status()
        if path == "/v2/tasks/prepare-model":
            return {
                "schema": "sts2.platform/task-model-result-2",
                "runtime_instance_id": self.instance,
                "command_id": body["command_id"],
                "model_context": body["model_context"],
                "ready_for_model": True,
                "recording_disposition": "no_active_recording",
                "status": self.task_status(),
            }
        if path.endswith("/clients/register"):
            return {
                **self.control(),
                "client": {
                    "client_session_id": "client-fixture",
                    "client_instance_id": body["client_instance_id"],
                },
            }
        if "/controller" in path:
            operation = path.rsplit("/", 1)[-1]
            if operation == "controller":
                return {
                    "protocol_version": "1.0.0",
                    "schema": "sts2.player-environment/control-1",
                    "runtime_instance_id": self.instance,
                    "clients": [],
                    **(
                        {
                            "controller": {
                                "controller_lease_id": "lease-fixture",
                                "controller_generation": 1,
                                "client_session_id": "client-fixture",
                                "expires_at": future(),
                            }
                        }
                        if self.held
                        else {}
                    ),
                }
            self.held = operation in {"acquire", "renew"}
            return {
                **self.control(),
                **(
                    {
                        "status": "controller_acquired" if self.held else "controller_released",
                        "detail": "synthetic",
                        "controller": {
                            "controller_lease_id": "lease-fixture",
                            "controller_generation": 1,
                            "client_session_id": "client-fixture",
                            "expires_at": future(),
                        }
                        if self.held
                        else None,
                    }
                    if operation != "controller"
                    else {}
                ),
            }
        if path.endswith("/capabilities"):
            if "native-logical" not in path:
                return self.caps
            value = copy.deepcopy(self.native["capabilities"])
            value.update(host=self.caps["host"], game=self.caps["game"])
            value["session"]["runtime_instance_id"] = self.instance
            value["capture_coverage"] = self.seams
            value["supported_methods"] = [
                "capabilities",
                "current",
                "read",
                "catalog",
                "resolve",
                "attach",
                "events",
                "await",
                "cancel_wait",
                "detach",
                "renew",
                "retain",
                "release",
                "submit",
                "result",
            ]
            return value
        if path.endswith("/snapshot"):
            return self.public_snapshot()
        operation = path.rsplit("/", 1)[-1]
        if operation == "attach":
            self.subscription.update(
                eager_scope=body["eager_scope"], delivery_mode=body["delivery_mode"]
            )
            return {
                "schema": self.native["attach"]["schema"],
                "status": "attached",
                "subscription": self.subscription,
            }
        if operation == "events":
            event = copy.deepcopy(self.native["event_batch"]["events"][0]["event"])
            event.update(
                cursor="cursor-1",
                publication_index="1",
                source_index="1",
                kind="observation",
                source_phase="initial_observation",
                payload_reference=self.capture,
                capture_ref=self.capture["capture_id"],
            )
            return {
                "schema": self.native["event_batch"]["schema"],
                "events": [{"event": event, "availability": "available"}]
                if body["after_cursor"] == "cursor-0"
                else [],
                "next_cursor": "cursor-1",
                "high_watermark": "cursor-1",
                "retained_start_cursor": "cursor-0",
                "gap": None,
            }
        if operation == "current":
            return {
                "schema": self.native["current"]["schema"],
                "input_profile": "native-logical-v1",
                "status": "captured",
                "capture": self.capture,
                "context": {
                    **self.native["observation_context"],
                    "capture_ref": self.capture["capture_id"],
                    "observation_ref": self.capture["snapshot_id"],
                },
                "retention": None,
                "reason": None,
            }
        if operation == "read":
            offset = 0 if body["cursor"] == self.capture["read_cursor"] else int(body["cursor"][5:])
            block = self.bytes[offset : offset + body["max_bytes"]]
            end = offset + len(block)
            return {
                "schema": self.native["read"]["schema"],
                "capture_id": self.capture["capture_id"],
                "sha256": self.capture["sha256"],
                "offset": offset,
                "total_bytes": len(self.bytes),
                "data_base64": base64.b64encode(block).decode(),
                "next_cursor": None if end == len(self.bytes) else f"read-{end}",
                "complete": end == len(self.bytes),
            }
        if operation == "catalog":
            return {
                "schema": self.native["catalog_page"]["schema"],
                "status": "complete",
                "catalog_ref": self.observation["catalog"]["catalog_ref"],
                "snapshot_id": self.capture["snapshot_id"],
                "stream_generation": self.capture["stream_generation"],
                "total_count": 1,
                "digest": self.observation["catalog"]["digest"],
                "filtered_count": 1,
                "filtered_digest": self.observation["catalog"]["digest"],
                "actions": self.actions,
                "next_cursor": None,
                "minimum_required_bytes": None,
            }
        if operation in {"renew", "detach", "cancel_wait"}:
            return {
                **self.native[operation],
                **(
                    {
                        "subscription": self.subscription,
                        "next_cursor": body["after_cursor"],
                        "high_watermark": "cursor-1",
                        "retained_start_cursor": "cursor-0",
                    }
                    if operation == "renew"
                    else {
                        "subscription_id": body["subscription_id"],
                        **({"wait_id": body["wait_id"]} if operation == "cancel_wait" else {}),
                    }
                ),
            }
        if operation == "await":
            return {
                "schema": self.native["await"]["schema"],
                "status": "timeout",
                "event": None,
                "gap": None,
                "reason": None,
            }
        if operation == "actions":
            self.submitted += 1
            if body.get("input_profile") == "native-logical-v1":
                return {
                    **self.native["result"],
                    "request_id": body["request_id"],
                    "snapshot_id": self.capture["snapshot_id"],
                    "action": self.actions[0],
                    "delivery": "delivered",
                    "execution": "native_accepted",
                    "effect": "pending",
                    "cancel": "not_requested",
                    "reason": None,
                    "stages": [],
                }
            self.public_sequence += 1
            return {
                "protocol_version": "1.0.0",
                "schema": "sts2.player-environment/receipt-1",
                "request_id": body["request_id"],
                "delivery": "delivered",
                "action": {
                    "bound_action_id": body["bound_action_id"],
                    "verb": "end_turn",
                    "subject_referent_id": None,
                    "arguments": [],
                },
                "reason_code": None,
                "detail": None,
                "retry": {"allowed": False, "reason": "never"},
                "successor": self.public_snapshot(),
                "attribution": None,
            }
        if operation == "retain":
            return {
                "schema": "sts2.player-environment/native-logical-retain-1",
                "input_profile": "native-logical-v1",
                "status": "retained",
                "retention": {
                    "retention_handle_id": "owned-pin",
                    "capture": self.capture,
                    "read_cursor": "read-0",
                    "expires_at": self.capture["expires_at"],
                },
                "reason": None,
            }
        if operation == "release":
            return {
                "schema": "sts2.player-environment/native-logical-release-1",
                "input_profile": "native-logical-v1",
                "status": "released",
                "retention_handle_id": body["retention_handle_id"],
                "released": True,
                "reason": None,
            }
        raise ValueError(f"Unimplemented synthetic World route {path}")

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
