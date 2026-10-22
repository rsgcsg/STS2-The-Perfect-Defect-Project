"""One P1 consume/ACK/next program driver for stateless scorers.

The Runtime still owns budgets, Stop, delivery and unknown fencing. A randomized
chooser commits its RNG only on ACK. Re-observing a committed consumption unit
reuses its choice, and repeated next never draws again.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TextIO

from .data import digest, json_bytes

SCHEMA = "sts2.policy-runtime/agent-session-1"


def serve_session(
    manifest: dict[str, Any],
    score: Callable[[dict[str, Any]], list[float]],
    select: Callable[[list[float]], int],
    source: TextIO,
    destination: TextIO,
    *,
    consumption_prefix: str = "mstd-",
    reuse_selection: bool = False,
) -> None:
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
            scores = score(value["observation"])
            if len(scores) != len(value["catalog"]):
                raise ValueError("P1 scores must cover the complete current catalog")
            advanced = value["consumption_unit"]["id"] != unit
            if reuse_selection and not advanced and committed is not None:
                prior, prior_scores, _ = committed
                if value["catalog"] != prior["catalog"] or scores != prior_scores:
                    raise ValueError("P1 same consumption unit changed its choice basis")
            next_version = version + int(advanced)
            next_consumption = (
                consumption_prefix
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
            pending = (
                message["request_id"],
                value,
                scores,
                next_version,
                next_consumption,
                advanced,
            )
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
            _, value, scores, version, consumption, advanced = pending
            if value["observation"]["status"] == "terminal":
                selected = None
            elif not reuse_selection:
                # Preserve the deterministic M-std program's original next-time argmax.
                selected = None
            elif reuse_selection and not advanced and committed is not None:
                selected = committed[2]
            else:
                if not scores:
                    raise ValueError("nonterminal P1 observation has no legal choices")
                selected = select(scores)
                if type(selected) is not int or not 0 <= selected < len(scores):
                    raise ValueError("P1 chooser selected outside full catalog")
            committed = value, scores, selected
            unit = value["consumption_unit"]["id"]
            pending = None
        elif kind == "next":
            if committed is None or pending is not None:
                raise ValueError("P1 Agent next requires acknowledged input")
            value, scores, selected = committed
            directive: dict[str, Any]
            if value["observation"]["status"] == "terminal":
                directive = {"type": "close", "reason": "native_terminal_observed"}
            else:
                if not scores:
                    raise ValueError("nonterminal P1 observation has no legal choices")
                if not reuse_selection:
                    selected = select(scores)
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
