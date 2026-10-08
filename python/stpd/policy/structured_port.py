"""Trusted observation-only S0 adapter for the existing Runtime NDJSON port 2.

Each process begins with zero W. The caller must start a new continuity segment
after a process restart; this adapter never restores or claims implicit memory.
Runtime owns controller/legality/unknown/Stop. This module returns complete scores.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
from pathlib import Path
from threading import Lock
from typing import Any, TextIO

import torch

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes, object_fields

from ..fullrun.structured_inputs import (
    INPUT_ID,
    MAX_REQUEST_BYTES,
    PROJECTION_VERSION,
    StructuredFrame,
    project_structured_snapshot,
)
from ..fullrun.text_menu_inputs import V2_SNAPSHOT_SCHEMA
from ..models.structured_m2 import GRAPH_ID, StructuredM2
from ..structured_code_scope import (
    INFERENCE_SCOPE,
    SCOPED_ADAPTER_VERSION,
    SCOPED_PACKAGE_SCHEMA,
    code_identity,
    code_sha256,
)
from .structured_export import MANIFEST_NAME, ROOT, code_digest, load_structured_package

PORT_SCHEMA = "sts2.policy-runtime/policy-port-2"
ADAPTER_ID = "stpd-s0-structured-adapter"
ADAPTER_VERSION = "1.0.0"
MAX_RETIRED_SEGMENTS = 1024


class StructuredOnlineScorer:
    """Commit memory only after successful bound, finite whole-catalog scoring."""

    def __init__(self, model: StructuredM2) -> None:
        self.model = model.eval()
        self.model.validate_parameters()
        self.memory = model.initial_memory()
        self.state_digest: str | None = None
        self.continuity: str | None = None
        self.run_id: str | None = None
        self.session: object = None
        self.sequence = -1
        self.snapshot_id: str | None = None
        self.snapshot_digest: str | None = None
        self.retired: set[str] = set()
        self.advances = 0
        self.lock = Lock()

    def observe(
        self,
        snapshot: dict[str, Any],
        *,
        run_id: str,
        continuity_token: str,
        expected_candidate_digest: str,
        expected_candidate_count: int,
    ) -> tuple[StructuredFrame, tuple[float, ...], bool]:
        if not self.lock.acquire(blocking=False):
            raise BoundaryError("structured_online", "concurrent_observation")
        try:
            return self._observe(
                snapshot,
                run_id=run_id,
                continuity_token=continuity_token,
                expected_candidate_digest=expected_candidate_digest,
                expected_candidate_count=expected_candidate_count,
            )
        finally:
            self.lock.release()

    def _observe(
        self,
        snapshot: dict[str, Any],
        *,
        run_id: str,
        continuity_token: str,
        expected_candidate_digest: str,
        expected_candidate_count: int,
    ) -> tuple[StructuredFrame, tuple[float, ...], bool]:
        frame = project_structured_snapshot(snapshot)
        if (
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(continuity_token, str)
            or not continuity_token
            or type(expected_candidate_count) is not int
            or expected_candidate_count != len(frame.candidates)
            or expected_candidate_digest != frame.candidate_digest
        ):
            raise BoundaryError("structured_online", "candidate_or_continuity_binding")
        if continuity_token in self.retired:
            raise BoundaryError("structured_online", "retired_continuity")
        changed = continuity_token != self.continuity
        if changed and self.continuity is not None and len(self.retired) >= MAX_RETIRED_SEGMENTS:
            raise BoundaryError("structured_online", "segment_budget")
        session, sequence = snapshot["session"], snapshot["sequence"]
        if (
            not isinstance(session, dict)
            or set(session) != {"runtime_instance_id", "environment_fingerprint"}
            or any(not isinstance(item, str) or not item for item in session.values())
            or type(sequence) is not int
            or sequence < 0
            or not isinstance(snapshot["snapshot_id"], str)
            or not snapshot["snapshot_id"]
            or not isinstance(snapshot["observed_at"], str)
            or not snapshot["observed_at"]
        ):
            raise BoundaryError("structured_online", "snapshot_identity")
        snapshot_id = snapshot["snapshot_id"]
        session_key = (session["runtime_instance_id"], session["environment_fingerprint"])
        transport_digest = hashlib.sha256(
            json_bytes({key: value for key, value in snapshot.items() if key != "observed_at"})
        ).hexdigest()
        if not changed and (run_id != self.run_id or session_key != self.session):
            raise BoundaryError("structured_online", "session_or_run_requires_new_segment")
        if not changed and (
            sequence < self.sequence
            or snapshot_id != self.snapshot_id
            and sequence == self.sequence
            or snapshot_id == self.snapshot_id
            and (sequence != self.sequence or transport_digest != self.snapshot_digest)
        ):
            raise BoundaryError("structured_online", "snapshot_order_or_identity")
        old = self.model.initial_memory() if changed else self.memory
        advance = changed or frame.state_digest != self.state_digest
        with torch.inference_mode():
            # E and bindings are always rebuilt, even for equal state and changed C/IDs.
            entities = self.model.encode(frame)
            proposed = self.model.advance(entities, old) if advance else old
            scores = (
                tuple(
                    float(value) for value in self.model.score(frame, entities, proposed).tolist()
                )
                if frame.candidates
                else ()
            )
        if len(scores) != len(frame.candidates) or any(
            not math.isfinite(value) for value in scores
        ):
            raise BoundaryError("structured_online", "finite_complete_scores_required")
        # No error before this point can alter W or its accepted observation basis.
        if changed and self.continuity is not None:
            self.retired.add(self.continuity)
        self.memory = proposed.clone()
        self.state_digest = frame.state_digest
        self.continuity, self.run_id, self.session = continuity_token, run_id, session_key
        self.sequence, self.snapshot_id, self.snapshot_digest = (
            sequence,
            snapshot_id,
            transport_digest,
        )
        self.advances += advance
        return frame, scores, advance


class StructuredPolicyAdapter:
    def __init__(self, package: Path, manifest_path: Path) -> None:
        raw = manifest_path.read_bytes()
        if len(raw) > 1024 * 1024:
            raise BoundaryError("structured_port", "manifest_size")
        self.manifest = object_fields(
            decode_json(raw),
            {
                "schema",
                "manifest_id",
                "policy",
                "adapter",
                "artifact",
                "representation",
                "requirements",
                "support",
                "adapter_config",
                "claims",
            },
            "structured_port.manifest",
        )
        manifest = self.manifest
        policy = object_fields(
            manifest["policy"],
            {"id", "version", "provider", "architecture"},
            "structured_port.policy",
        )
        if (
            not isinstance(manifest["manifest_id"], str)
            or not manifest["manifest_id"]
            or any(not isinstance(value, str) or not value for value in policy.values())
            or not isinstance(manifest["adapter_config"], dict)
        ):
            raise BoundaryError("structured_port", "manifest_identity")
        adapter_identity = object_fields(manifest["adapter"],
            {"id", "version", "protocol", "code_sha256"}, "structured_port.adapter")
        scoped = adapter_identity["version"] == SCOPED_ADAPTER_VERSION
        if scoped:
            config_identity = manifest["adapter_config"].get("stage1a", {})
            if (not isinstance(config_identity, dict)
                    or config_identity.get("code_digest_scope") != INFERENCE_SCOPE
                    or config_identity.get("code_identity") !=
                    code_identity(INFERENCE_SCOPE, ROOT)):
                raise BoundaryError("structured_port", "manifest_identity")
        if (
            manifest["schema"] != "sts2.policy-runtime/policy-manifest-1"
            or manifest["adapter"]
            != {
                "id": ADAPTER_ID,
                "version": SCOPED_ADAPTER_VERSION if scoped else ADAPTER_VERSION,
                "protocol": "sts2.policy-runtime/decision-only-ndjson-2",
                "code_sha256": (code_sha256(INFERENCE_SCOPE, ROOT) if scoped
                                else code_digest(ROOT)),
            }
            or manifest["representation"]
            != {"id": INPUT_ID, "version": PROJECTION_VERSION, "input_schema": V2_SNAPSHOT_SCHEMA}
            or manifest["policy"].get("architecture") != GRAPH_ID
        ):
            raise BoundaryError("structured_port", "manifest_identity")
        artifact = object_fields(
            manifest["artifact"], {"id", "path", "sha256"}, "structured_port.artifact"
        )
        artifact_path = Path(artifact["path"])
        if not artifact_path.is_absolute():
            artifact_path = manifest_path.parent / artifact_path
        if artifact_path.resolve() != (package / MANIFEST_NAME).resolve():
            raise BoundaryError("structured_port", "artifact_path_binding")
        metadata, model = load_structured_package(
            package, expected_manifest_sha256=artifact["sha256"]
        )
        if scoped != (metadata["schema"] == SCOPED_PACKAGE_SCHEMA):
            raise BoundaryError("structured_port", "package_scope_binding")
        if artifact["id"] != metadata["model_id"]:
            raise BoundaryError("structured_port", "model_id_binding")
        requirements = object_fields(
            manifest["requirements"],
            {
                "connector_protocol_version",
                "environment",
                "reads",
                "whole_decision_admission",
                "candidate_order_digest",
                "score_count_matches_candidate_count",
                "selected_index",
                "successor_required",
            },
            "structured_port.requirements",
        )
        if (
            requirements.get("reads") != []
            or requirements.get("whole_decision_admission") is not True
            or requirements.get("candidate_order_digest") != "sha256-json-menu-action-id-order"
            or requirements.get("score_count_matches_candidate_count") is not True
            or requirements.get("selected_index") is not True
            or requirements.get("successor_required") is not True
        ):
            raise BoundaryError("structured_port", "requirements_identity")
        if (
            not isinstance(requirements["connector_protocol_version"], str)
            or not requirements["connector_protocol_version"]
        ):
            raise BoundaryError("structured_port", "protocol_identity")
        environment = object_fields(
            requirements["environment"],
            {
                "host_kind",
                "connector_version",
                "connector_source_revision",
                "connector_artifact_sha256",
                "connector_module_version_id",
                "modset_status",
                "modset_fingerprint",
                "loaded_mod_ids",
            },
            "structured_port.environment",
        )
        if environment["host_kind"] not in {"live_ui", "headless", "replay", "test"}:
            raise BoundaryError("structured_port", "environment_kind")
        for key in (
            "connector_version",
            "connector_source_revision",
            "connector_module_version_id",
            "modset_status",
        ):
            if not isinstance(environment[key], str) or not environment[key]:
                raise BoundaryError("structured_port", "environment_identity")
        for key in ("connector_artifact_sha256", "modset_fingerprint"):
            digest(environment[key], "structured_port.environment_digest")
        mods = environment["loaded_mod_ids"]
        if (
            not isinstance(mods, list)
            or any(not isinstance(value, str) or not value for value in mods)
            or len(set(mods)) != len(mods)
        ):
            raise BoundaryError("structured_port", "modset_identity")
        support = object_fields(
            manifest["support"],
            {"game_versions", "game_commits", "interaction_kinds", "action_verbs"},
            "structured_port.support",
        )
        if any(
            not isinstance(values, list)
            or not values
            or len(set(values)) != len(values)
            or any(not isinstance(value, str) or not value for value in values)
            for values in support.values()
        ):
            raise BoundaryError("structured_port", "support_scope")
        claims = object_fields(
            manifest["claims"],
            {
                "full_run",
                "selector",
                "catalog_filtered",
                "creates_action_authority",
                "creates_native_operands",
            },
            "structured_port.claims",
        )
        if type(claims["full_run"]) is not bool or type(claims["selector"]) is not bool:
            raise BoundaryError("structured_port", "scope_claims")
        if any(
            claims.get(key) is not False
            for key in ("catalog_filtered", "creates_action_authority", "creates_native_operands")
        ):
            raise BoundaryError("structured_port", "authority_claims")
        self.scorer = StructuredOnlineScorer(model)
        self.closed = False

    def decide(self, value: object) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.closed:
            raise BoundaryError("structured_port", "adapter_closed")
        request = object_fields(
            value,
            {
                "run_id",
                "manifest",
                "bundle",
                "candidate_digest",
                "candidate_count",
                "continuity_token",
            },
            "structured_port.request",
        )
        if type(request["candidate_count"]) is not int or request["candidate_count"] < 1:
            raise BoundaryError("structured_port", "decision_catalog_required")
        if request["manifest"] != self.manifest:
            raise BoundaryError("structured_port", "request_manifest_binding")
        bundle = object_fields(
            request["bundle"], {"observation", "reads"}, "structured_port.bundle"
        )
        if bundle["reads"] != []:
            raise BoundaryError("structured_port", "unexpected_reads")
        snapshot = bundle["observation"]
        if not isinstance(snapshot, dict):
            raise BoundaryError("structured_port", "snapshot_required")
        support = self.manifest["support"]
        if snapshot["interaction"]["kind"] not in support["interaction_kinds"] or any(
            action["verb"] not in support["action_verbs"]
            for action in snapshot["menu_actions"]["actions"]
        ):
            raise BoundaryError("structured_port", "unsupported_whole_decision")
        frame, scores, _advance = self.scorer.observe(
            snapshot,
            run_id=request["run_id"],
            continuity_token=request["continuity_token"],
            expected_candidate_digest=request["candidate_digest"],
            expected_candidate_count=request["candidate_count"],
        )
        return (
            {
                "candidate_digest": frame.candidate_digest,
                "scores": list(scores),
                "selected_index": max(range(len(scores)), key=scores.__getitem__),
            },
            {
                "continuity_token": request["continuity_token"],
                "snapshot_id": snapshot["snapshot_id"],
                "sequence": snapshot["sequence"],
            },
        )

    def close(self) -> None:
        self.closed = True


def serve(adapter: StructuredPolicyAdapter, source: TextIO, destination: TextIO) -> int:
    def emit(value: dict[str, Any]) -> None:
        destination.write(canonical_json(value) + "\n")
        destination.flush()

    emit({"schema": PORT_SCHEMA, "message_type": "ready", "adapter": adapter.manifest["adapter"]})
    try:
        while True:
            line = source.readline(MAX_REQUEST_BYTES + 1)
            if not line:
                break
            if not line.strip():
                continue
            request_id = "unknown"
            try:
                if len(line.encode("utf-8")) > MAX_REQUEST_BYTES or not line.endswith("\n"):
                    raise BoundaryError("structured_port", "request_size_or_framing")
                request = object_fields(
                    decode_json(line),
                    {"schema", "message_type", "request_id", "input"},
                    "structured_port.message",
                )
                if not isinstance(request["request_id"], str) or not request["request_id"]:
                    raise BoundaryError("structured_port", "request_id")
                request_id = request["request_id"]
                if request["schema"] != PORT_SCHEMA or request["message_type"] != "decide":
                    raise BoundaryError("structured_port", "message_type")
                output, completion = adapter.decide(request["input"])
                emit(
                    {
                        "schema": PORT_SCHEMA,
                        "message_type": "decision",
                        "request_id": request_id,
                        "output": output,
                        "completion": completion,
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
                if not line.endswith("\n"):
                    break
    finally:
        adapter.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(2)
    return serve(
        StructuredPolicyAdapter(args.package.resolve(), args.manifest.resolve()),
        sys.stdin,
        sys.stdout,
    )


if __name__ == "__main__":
    raise SystemExit(main())
