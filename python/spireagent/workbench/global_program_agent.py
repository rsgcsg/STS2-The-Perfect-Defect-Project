"""Code-owned model-less GLOBAL Agent; only ACK commits script consumption."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ADAPTER = "spireagent-global-public-program"
PROTOCOL = "sts2.policy-runtime/agent-session-ndjson-1"
SCHEMA = "sts2.policy-runtime/agent-session-1"


def adapter_identity():
    return {"id": ADAPTER, "version": "1", "protocol": PROTOCOL,
            "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def descriptor():
    return {"schema": "spireagent/global-public-program-1", "adapter": adapter_identity(),
            "agent_spec": {"id": "global-public-program", "version": "1", "model_bindings": [],
                           "task_spec": {"id": "current-public-semantic-choice", "version": "1"},
                           "selection": "first_complete_semantic_choice"}}


def serve(manifest_path: Path):
    manifest = json.loads(manifest_path.read_bytes())
    if (
        manifest.get("adapter") != adapter_identity()
        or manifest.get("input", {}).get("profile") != "p1-global-r1"
        or manifest.get("input", {}).get("state_recovery", {}).get("model_bindings") != []
    ):
        raise ValueError("global_program_identity_changed")
    unit = consumption = basis = pending = None
    version = 0
    def send(value):
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)
    def reply(parent, kind, key, value):
        send(
            {
                "schema": SCHEMA,
                "message_type": kind,
                "session_id": parent["session_id"],
                "recovery_epoch": parent["recovery_epoch"],
                "request_id": parent["request_id"],
                key: value,
            }
        )

    send({"schema": SCHEMA, "message_type": "ready", "adapter": manifest["adapter"]})
    for line in sys.stdin:
        message = json.loads(line)
        if message["schema"] != SCHEMA:
            raise ValueError("global_program_protocol")
        kind = message["message_type"]
        if kind == "consume":
            if pending is not None:
                raise ValueError("global_program_unacknowledged_consumption")
            value = message["input"]
            if value["previous_consumption_id"] != consumption:
                raise ValueError("global_program_continuity")
            advanced = value["consumption_unit"]["id"] != unit
            next_version = version + int(advanced)
            pending = (value["consumption_unit"]["id"], next_version,
                       "global-consumed-" + str(next_version) if advanced else consumption, value)
            reply(
                message,
                "consumed",
                "completion",
                {
                    "acquisition_id": value["acquisition_id"],
                    "input_spec": manifest["input"]["input_spec"],
                    "continuity_token": value["continuity_token"],
                    "previous_consumption_id": consumption,
                    "consumption_id": pending[2],
                    "state_version": next_version,
                    "advanced": advanced,
                },
            )
        elif kind == "consume_ack":
            if pending is None or message["completion"]["consumption_id"] != pending[2]:
                raise ValueError("global_program_ack_without_pending")
            unit, version, consumption, basis = pending
            pending = None
        elif kind == "next":
            if basis is None or pending is not None:
                raise ValueError("global_program_next_without_ack")
            directive = (
                {"type": "close", "reason": "native_terminal_observed"}
                if basis["observation"]["status"] == "terminal"
                else {
                    "type": "act",
                    "basis_acquisition_id": basis["acquisition_id"],
                    "selection": {"kind": "handle", "action_id": basis["catalog"][0]["choice_id"]},
                    "scores": None,
                }
            )
            reply(
                message,
                "directive",
                "output",
                {
                    "continuity_token": message["input"]["continuity_token"],
                    "consumption_id": consumption,
                    "state_version": version,
                    "directive": directive,
                },
            )
        else:
            raise ValueError("global_program_unknown_message")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    serve(parser.parse_args().manifest)
