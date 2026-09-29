"""Experimental M2 decision-only port-2 adapter over the complete text menu."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any, TextIO

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, decode_json, json_bytes, object_fields

from ..fullrun.memory_token_inputs import project_memory_profile_snapshot
from ..memory_policy_installation import input_profile_for_config, validate
from .memory_export import validate_memory_package
from .memory_scorer import MAX_SNAPSHOT_BYTES, OnlineM2Scorer

PORT_SCHEMA = "sts2.policy-runtime/policy-port-2"
MAX_PORT_LINE_BYTES = 20 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[2]


class MemoryPolicyAdapter:
    def __init__(self, config_path: Path, manifest_path: Path,
                 binding_root: Path | None = None) -> None:
        self.config, self.manifest = validate(
            ROOT, config_path, manifest_path, binding_root=binding_root)
        self.input_profile = input_profile_for_config(self.config)
        package, weights, tokenizer, settings = validate_memory_package(
            Path(self.config["export_path"]), input_profile=self.input_profile,
            expected_manifest_sha256=self.config["export_manifest_sha256"])
        if package["ids"]["model"] != self.config["model_id"]:
            raise BoundaryError("m2_policy", "model_identity_mismatch")
        self.scorer = OnlineM2Scorer.from_export(
            weights, settings, tokenizer, input_profile=self.input_profile)
        self.closed = False

    def decide(self, value: object) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.closed:
            raise BoundaryError("m2_policy", "adapter_closed")
        request = object_fields(value, {
            "run_id", "manifest", "bundle", "candidate_digest", "candidate_count",
            "continuity_token",
        }, "m2_policy.request")
        if (not isinstance(request["run_id"], str) or not request["run_id"]
                or not isinstance(request["continuity_token"], str)
                or not request["continuity_token"]
                or request["manifest"] != self.manifest):
            raise BoundaryError("m2_policy", "request_identity_mismatch")
        bundle = object_fields(request["bundle"], {"observation", "reads"},
                               "m2_policy.bundle")
        if bundle["reads"] != []:
            raise BoundaryError("m2_policy", "unexpected_reads")
        snapshot = bundle["observation"]
        if not isinstance(snapshot, dict):
            raise BoundaryError("m2_policy", "snapshot_object_required")
        snapshot_bytes = json_bytes(snapshot)
        if len(snapshot_bytes) > MAX_SNAPSHOT_BYTES:
            raise BoundaryError("m2_policy", "snapshot_size_limit")
        # Shared projection owns complete catalog identity. This preflight is
        # before the scorer's single observation write, not a legality filter.
        public = project_memory_profile_snapshot(snapshot, self.input_profile)
        actions = snapshot["menu_actions"]["actions"]
        if (type(request["candidate_count"]) is not int
                or request["candidate_count"] != len(public.action_ids)
                or request["candidate_digest"] != public.candidate_digest):
            raise BoundaryError("m2_policy", "candidate_binding_mismatch")
        support = self.manifest["support"]
        if (snapshot["interaction"]["kind"] not in support["interaction_kinds"]
                or any(action["verb"] not in support["action_verbs"] for action in actions)):
            raise BoundaryError("m2_policy", "unsupported_whole_decision")
        result = self.scorer.observe_and_score(
            continuity_token=request["continuity_token"],
            snapshot_bytes=snapshot_bytes,
            expected_candidate_digest=request["candidate_digest"],
            expected_candidate_count=request["candidate_count"],
        )
        if (result.action_ids != public.action_ids
                or result.candidate_digest != public.candidate_digest
                or len(result.scores) != len(public.action_ids)
                or not result.scores or any(not math.isfinite(v) for v in result.scores)):
            raise BoundaryError("m2_policy", "score_binding_mismatch")
        values = list(result.scores)
        return ({"candidate_digest": public.candidate_digest, "scores": values,
                 "selected_index": max(range(len(values)), key=values.__getitem__)},
                {"continuity_token": request["continuity_token"],
                 "snapshot_id": snapshot["snapshot_id"], "sequence": snapshot["sequence"]})

    def close(self) -> None:
        self.closed = True


def serve(adapter: MemoryPolicyAdapter, source: TextIO, destination: TextIO) -> int:
    def emit(value: dict) -> None:
        destination.write(canonical_json(value) + "\n")
        destination.flush()

    emit({"schema": PORT_SCHEMA, "message_type": "ready",
          "adapter": adapter.manifest["adapter"]})
    try:
        while True:
            line = source.readline(MAX_PORT_LINE_BYTES + 1)
            if not line:
                break
            if not line.strip():
                continue
            request_id = "unknown"
            try:
                if len(line.encode("utf-8")) > MAX_PORT_LINE_BYTES or not line.endswith("\n"):
                    raise BoundaryError("m2_policy", "request_size_limit")
                request = object_fields(decode_json(line.encode("utf-8")), {
                    "schema", "message_type", "request_id", "input"}, "m2_policy.port")
                if isinstance(request["request_id"], str) and request["request_id"]:
                    request_id = request["request_id"]
                else:
                    raise BoundaryError("m2_policy", "request_id_required")
                if request["schema"] != PORT_SCHEMA or request["message_type"] != "decide":
                    raise BoundaryError("m2_policy", "unsupported_request")
                output, completion = adapter.decide(request["input"])
                emit({"schema": PORT_SCHEMA, "message_type": "decision",
                      "request_id": request_id, "output": output, "completion": completion})
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                emit({"schema": PORT_SCHEMA, "message_type": "error",
                      "request_id": request_id,
                      "error": {"code": "policy_error", "message": str(error)}})
                if len(line) > MAX_PORT_LINE_BYTES and not line.endswith("\n"):
                    # Stream framing is lost; discard no further content as a
                    # new request. Runtime must end this adapter instance.
                    break
    finally:
        adapter.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--binding-root", type=Path)
    args = parser.parse_args()
    import torch

    torch.set_num_threads(2)
    return serve(MemoryPolicyAdapter(args.config.resolve(), args.manifest.resolve(),
                                     args.binding_root),
                 sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
