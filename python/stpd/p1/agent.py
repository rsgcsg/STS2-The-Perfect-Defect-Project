"""Learned stateless P1 Agent on the existing consume/ACK/next Runtime port."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

import torch

from .data import digest, json_bytes
from .export import adapter_identity, input_spec, load_model
from .input import project
from .model import MStd

SCHEMA = "sts2.policy-runtime/agent-session-1"


def installation(
    config_path: Path, manifest_path: Path, owner: Path
) -> tuple[dict[str, Any], dict[str, Any], MStd, dict[str, Any]]:
    from spireagent.policy_files import _object_file, bound_path

    config = _object_file(bound_path(owner, config_path, stage="native_agent_support"))
    manifest = _object_file(bound_path(owner, manifest_path, stage="native_agent_support"))
    path = bound_path(owner, Path(config["package_path"]), stage="native_agent_support")
    model, payload = load_model(path, config["package_sha256"])
    expected_artifact = {
        "id": config["package_sha256"],
        "path": str(path),
        "sha256": config["package_sha256"],
    }
    if (
        config["schema"] != "stpd/p1-mstd-installation-1"
        or manifest.get("adapter") != adapter_identity()
        or manifest.get("artifact") != expected_artifact
        or manifest.get("input", {}).get("input_spec") != input_spec(model.vocabulary)
        or manifest["input"].get("profile") != "p1-global-r1"
        or manifest["input"].get("state_recovery")
        != {"mode": "none", "max_state_bytes": 0, "model_bindings": []}
        or config["checkpoint_sha256"] != payload["checkpoint_sha256"]
        or config["weights_sha256"] != digest(encode_weights(payload["weights"]))
        or config["package_model_id"] != config["package_sha256"]
        or config["input_spec"] != manifest["input"]["input_spec"]
    ):
        raise ValueError("P1 model installation binding differs")
    return config, manifest, model, payload


def encode_weights(weights: dict[str, Any]) -> bytes:
    from stpd.workers.checkpoint_codec import encode_checkpoint

    return encode_checkpoint({"weights": weights})


def serve(
    config_path: Path,
    manifest_path: Path,
    owner: Path,
    source: TextIO = sys.stdin,
    destination: TextIO = sys.stdout,
) -> None:
    torch.set_num_threads(2)
    _, manifest, model, _ = installation(config_path, manifest_path, owner)
    unit = consumption = committed = pending = None
    version = 0

    def send(value):
        destination.write(
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
        )
        destination.flush()

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
    for line in source:
        message = json.loads(line)
        if message["schema"] != SCHEMA:
            raise ValueError("P1 Agent schema mismatch")
        kind = message["message_type"]
        if kind == "consume":
            value = message["input"]
            if pending is not None or value["previous_consumption_id"] != consumption:
                raise ValueError("P1 Agent consumption continuity mismatch")
            if (
                value["input_spec"] != manifest["input"]["input_spec"]
                or value["catalog"] != value["observation"]["catalog"]
            ):
                raise ValueError("P1 Agent needs its bound InputSpec and full current catalog")
            frame = project(value["observation"])
            with torch.inference_mode():
                scores = model(frame).tolist() if frame.choices else []
            advanced = value["consumption_unit"]["id"] != unit
            next_version = version + int(advanced)
            next_consumption = (
                "mstd-"
                + digest(
                    json_bytes(
                        {
                            "unit": value["consumption_unit"],
                            "version": next_version,
                            "artifact": manifest["artifact"]["sha256"],
                        }
                    )
                )
                if advanced
                else consumption
            )
            pending = (message["request_id"], value, scores, next_version, next_consumption)
            reply(
                message,
                "consumed",
                "completion",
                {
                    "acquisition_id": value["acquisition_id"],
                    "input_spec": value["input_spec"],
                    "continuity_token": value["continuity_token"],
                    "previous_consumption_id": consumption,
                    "consumption_id": next_consumption,
                    "state_version": next_version,
                    "advanced": advanced,
                },
            )
        elif kind == "consume_ack":
            if (
                pending is None
                or message["request_id"] != pending[0]
                or message["completion"]["consumption_id"] != pending[4]
            ):
                raise ValueError("P1 Agent ACK does not bind pending consumption")
            _, value, scores, version, consumption = pending
            committed = value, scores
            unit = value["consumption_unit"]["id"]
            pending = None
        elif kind == "next":
            if committed is None or pending is not None:
                raise ValueError("P1 Agent next requires acknowledged input")
            value, scores = committed
            directive: dict[str, Any]
            if value["observation"]["status"] == "terminal":
                directive = {"type": "close", "reason": "native_terminal_observed"}
            else:
                if not scores:
                    raise ValueError("nonterminal P1 observation has no legal choices")
                selected = max(range(len(scores)), key=scores.__getitem__)
                directive = {
                    "type": "act",
                    "basis_acquisition_id": value["acquisition_id"],
                    "selection": {
                        "kind": "handle",
                        "action_id": value["catalog"][selected]["choice_id"],
                    },
                    "scores": {
                        "catalog_digest": digest(json_bytes(value["catalog"])),
                        "values": scores,
                    },
                }
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
            raise ValueError("unsupported P1 Agent message")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--binding-root", type=Path, required=True)
    args = parser.parse_args()
    serve(args.config.resolve(), args.manifest.resolve(), args.binding_root.resolve())


if __name__ == "__main__":
    main()
