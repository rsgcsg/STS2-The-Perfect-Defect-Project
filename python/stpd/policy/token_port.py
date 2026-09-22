"""Stage 1a scoring over the existing Platform decision-only NDJSON port.

Runtime owns game compatibility, controller acquisition, timeouts and execution.
This process only returns scores for the unchanged complete public catalog.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, TextIO

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, object_fields

from ..token_policy_installation import validate
from .token_decision import TokenDecisionScorer

PORT_SCHEMA = "sts2.policy-runtime/policy-port-1"
ROOT = Path(__file__).resolve().parents[2]


class TokenPolicyAdapter:
    def __init__(self, config_path: Path, manifest_path: Path) -> None:
        self.config, self.manifest = validate(ROOT, config_path, manifest_path)
        snapshot = self.config["qwen_snapshot"]
        self.scorer = TokenDecisionScorer(
            Path(self.config["export_path"]),
            snapshot=Path(snapshot) if snapshot is not None else None,
        )
        representation = self.scorer.artifact.parameters.value()["serializer"]
        if (
            self.scorer.serializer is not None
            or self.manifest.get("representation")
            != {
                "id": representation["profile"],
                "version": representation["version"],
                "input_schema": "sts2.player-environment/snapshot-1",
            }
            or self.scorer.artifact.artifact_id != self.config["model_id"]
        ):
            raise BoundaryError("token_policy", "public_model_required")
        self.closed = False

    def decide(self, value: object) -> dict[str, Any]:
        if self.closed:
            raise BoundaryError("token_policy", "adapter_closed")
        request = object_fields(
            value,
            {"run_id", "manifest", "bundle", "candidate_digest", "candidate_count"},
            "token_policy.request",
        )
        if (
            not isinstance(request["run_id"], str)
            or not request["run_id"]
            or request["manifest"] != self.manifest
        ):
            raise BoundaryError("token_policy", "request_identity_mismatch")
        bundle = object_fields(request["bundle"], {"observation", "reads"}, "token_policy.bundle")
        if bundle["reads"] != []:
            raise BoundaryError("token_policy", "unexpected_reads")
        snapshot = bundle["observation"]
        actions = snapshot["bound_actions"]["actions"]
        keys = [a["bound_action_id"] for a in actions]
        candidate_digest = hashlib.sha256(canonical_json(keys).encode()).hexdigest()
        if (
            type(request["candidate_count"]) is not int
            or request["candidate_count"] != len(keys)
            or request["candidate_digest"] != candidate_digest
        ):
            raise BoundaryError("token_policy", "candidate_binding_mismatch")
        support = self.manifest["support"]
        if snapshot["interaction"]["kind"] not in support["interaction_kinds"] or any(
            a["verb"] not in support["action_verbs"] for a in actions
        ):
            raise BoundaryError("token_policy", "unsupported_whole_decision")
        scores = self.scorer.score_snapshot(snapshot)
        if list(scores) != keys or not scores or any(not math.isfinite(v) for v in scores.values()):
            raise BoundaryError("token_policy", "score_binding_mismatch")
        values = list(scores.values())
        return {
            "candidate_digest": candidate_digest,
            "scores": values,
            "selected_index": max(range(len(values)), key=values.__getitem__),
        }

    def close(self) -> None:
        self.closed = True


def serve(adapter: TokenPolicyAdapter, source: TextIO, destination: TextIO) -> int:
    def emit(value: dict) -> None:
        destination.write(canonical_json(value) + "\n")
        destination.flush()

    emit({"schema": PORT_SCHEMA, "message_type": "ready", "adapter": adapter.manifest["adapter"]})
    try:
        for line in source:
            if not line.strip():
                continue
            request_id = "unknown"
            try:
                request = object_fields(
                    json.loads(line),
                    {"schema", "message_type", "request_id", "input"},
                    "policy_port",
                )
                if isinstance(request["request_id"], str) and request["request_id"]:
                    request_id = request["request_id"]
                else:
                    raise BoundaryError("token_policy", "request_id_required")
                if request["schema"] != PORT_SCHEMA or request["message_type"] != "decide":
                    raise BoundaryError("token_policy", "unsupported_request")
                output = adapter.decide(request["input"])
                emit(
                    {
                        "schema": PORT_SCHEMA,
                        "message_type": "decision",
                        "request_id": request_id,
                        "output": output,
                    }
                )
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                emit(
                    {
                        "schema": PORT_SCHEMA,
                        "message_type": "error",
                        "request_id": request_id,
                        "error": {"code": "policy_error", "message": str(error)},
                    }
                )
    finally:
        adapter.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    # Match the engineering training thread budget; stdout is protocol-only.
    import torch

    torch.set_num_threads(2)
    return serve(
        TokenPolicyAdapter(args.config.resolve(), args.manifest.resolve()), sys.stdin, sys.stdout
    )


if __name__ == "__main__":
    raise SystemExit(main())
