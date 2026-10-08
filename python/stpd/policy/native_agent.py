"""Trusted native full-reference AgentSession child; Runtime owns all action authority."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import sys
from pathlib import Path
from typing import Any, TextIO

import torch

from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes, object_fields

from ..fullrun.native_structured_inputs import INPUT_SPEC, PROFILE, PROJECTION_VERSION, SCOPE
from ..models.native_structured_scorer import NativeStructuredScorer
from ..native_code_scope import native_code_sha256
from ..structured_code_scope import ROOT
from ..workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from .native_structured_export import (
    AGENT_SPEC,
    MANIFEST_NAME,
    STATE_FORMAT,
    encode_native_weights,
    load_native_package,
)

SESSION_SCHEMA = "sts2.policy-runtime/agent-session-1"
MANIFEST_SCHEMA = "sts2.policy-runtime/agent-manifest-1"
PROTOCOL = "sts2.policy-runtime/agent-session-ndjson-1"
ADAPTER_ID = "stpd-native-structured-m2-agent"
ADAPTER_VERSION = "1.0.0"
MAX_STATE_BYTES = 16 * 1024 * 1024
MAX_MESSAGE_BYTES = 32 * 1024 * 1024
LIMIT_MAXIMA = {
    "max_message_bytes": MAX_MESSAGE_BYTES,
    "max_acquisitions": 256,
    "max_retained_acquisition_bytes": 256 * 1024 * 1024,
    "max_pending_queries": 8,
    "max_queries_per_turn": 64,
    "max_query_bytes_per_turn": 128 * 1024 * 1024,
    "max_capture_bytes": 8 * 1024 * 1024,
    "max_catalog_actions": 16384,
    "max_cancelled_ids": 256,
    "agent_timeout_ms": 30000,
}

META_FIELDS = {
    "agent_artifact_id",
    "agent_artifact_sha256",
    "adapter_code_sha256",
    "model_bindings",
    "input_spec",
    "profile",
    "state_format_version",
    "stream_generation",
    "continuity_token",
    "consumption_id",
    "state_version",
    "prefix",
    "last_acknowledged_basis",
}
BASIS_FIELDS = {
    "acquisition_id",
    "capture_sha256",
    "snapshot_id",
    "owner_occurrence",
    "revision",
    "included",
    "publication_index",
}


def adapter_identity() -> dict[str, str]:
    return {
        "id": ADAPTER_ID,
        "version": ADAPTER_VERSION,
        "protocol": PROTOCOL,
        "code_sha256": native_code_sha256(ROOT),
    }


def _bounded_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 1024 * 1024:
        raise BoundaryError("native_agent", "manifest_path_or_size")
    value = decode_json(path.read_bytes())
    if not isinstance(value, dict):
        raise BoundaryError("native_agent", "manifest_object_required")
    return value


class NativeStructuredAgent:
    def __init__(self, package: Path, manifest_path: Path) -> None:
        manifest = object_fields(
            _bounded_object(manifest_path),
            {
                "schema",
                "manifest_id",
                "agent",
                "adapter",
                "artifact",
                "input",
                "requirements",
                "support",
                "limits",
                "claims",
            },
            "native_agent.manifest",
        )
        artifact = object_fields(
            manifest["artifact"], {"id", "path", "sha256"}, "native_agent.artifact"
        )
        target = Path(artifact["path"])
        if not target.is_absolute():
            target = manifest_path.parent / target
        if target.resolve() != (package / MANIFEST_NAME).resolve():
            raise BoundaryError("native_agent", "trusted_package_path_binding")
        metadata, model = load_native_package(package, expected_manifest_sha256=artifact["sha256"])
        input_spec = object_fields(
            manifest["input"],
            {
                "profile",
                "input_spec",
                "projection",
                "state_format_version",
                "state_recovery",
                "history_mode",
                "consumption_mode",
                "gap_policy",
                "attachment",
            },
            "native_agent.input",
        )
        attachment = object_fields(
            input_spec["attachment"],
            {"eager_scope", "required_seams", "delivery_mode"},
            "native_agent.attachment",
        )
        seams = attachment["required_seams"]
        if not isinstance(seams, list) or not 1 <= len(seams) <= 256:
            raise BoundaryError("native_agent", "required_complete_seams")
        seen_seams = set()
        for raw_seam in seams:
            seam = object_fields(
                raw_seam, {"source_seam", "version", "coverage"}, "native_agent.seam"
            )
            if (
                not isinstance(seam["source_seam"], str)
                or not seam["source_seam"]
                or seam["source_seam"] in seen_seams
                or not isinstance(seam["version"], str)
                or not seam["version"]
                or seam["coverage"] != "complete_at_seam"
            ):
                raise BoundaryError("native_agent", "required_complete_seams")
            seen_seams.add(seam["source_seam"])
        claims = {
            "catalog_filtered",
            "creates_action_authority",
            "creates_native_operands",
            "human_origin",
            "causal_successor",
        }
        if object_fields(manifest["claims"], claims, "native_agent.claims") != dict.fromkeys(
            claims, False
        ):
            raise BoundaryError("native_agent", "unsupported_claims")
        support = object_fields(
            manifest["support"],
            {"game_versions", "game_commits", "interaction_kinds", "action_verbs"},
            "native_agent.support",
        )
        for values in support.values():
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(v, str) or not v for v in values)
                or len(set(values)) != len(values)
            ):
                raise BoundaryError("native_agent", "support_required")
        if (
            manifest["schema"] != MANIFEST_SCHEMA
            or manifest["adapter"] != adapter_identity()
            or artifact["id"] != metadata["model_id"]
            or not isinstance(input_spec, dict)
            or input_spec.get("profile") != PROFILE
            or input_spec.get("input_spec") != INPUT_SPEC
            or input_spec.get("projection")
            != {"id": INPUT_SPEC["id"], "version": PROJECTION_VERSION}
            or input_spec.get("state_format_version") != STATE_FORMAT
            or input_spec.get("history_mode") != "full_reference"
            or input_spec.get("consumption_mode") != "once_per_occurrence"
            or input_spec.get("gap_policy") != "handoff"
            or input_spec.get("attachment", {}).get("eager_scope") != list(SCOPE)
            or input_spec.get("attachment", {}).get("delivery_mode") != "full_reference"
            or manifest["agent"].get("id") != AGENT_SPEC["id"]
            or manifest["agent"].get("version") != AGENT_SPEC["version"]
        ):
            raise BoundaryError("native_agent", "agent_input_package_identity")
        agent = object_fields(
            manifest["agent"],
            {"id", "version", "provider", "architecture"},
            "native_agent.identity",
        )
        if agent != {
            "id": AGENT_SPEC["id"],
            "version": AGENT_SPEC["version"],
            "provider": "stpd",
            "architecture": "structured-native-m2-k1d96",
        }:
            raise BoundaryError("native_agent", "unsupported_agent_identity")
        limits = object_fields(manifest["limits"], set(LIMIT_MAXIMA), "native_agent.limits")
        if any(
            type(limits[key]) is not int or not 1 <= limits[key] <= maximum
            for key, maximum in LIMIT_MAXIMA.items()
        ):
            raise BoundaryError("native_agent", "unsupported_resource_limits")
        requirements = object_fields(
            manifest["requirements"],
            {"connector_protocol_version", "environment", "required_methods"},
            "native_agent.requirements",
        )
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
            "native_agent.environment",
        )
        if requirements["connector_protocol_version"] != "1.0.0" or environment[
            "host_kind"
        ] not in {"live_ui", "headless", "replay", "test"}:
            raise BoundaryError("native_agent", "unsupported_native_requirements")
        methods = requirements["required_methods"]
        if (
            not isinstance(methods, list)
            or len(set(methods)) != len(methods)
            or not {"attach", "events", "read", "catalog", "submit", "await"} <= set(methods)
        ):
            raise BoundaryError("native_agent", "required_native_methods")
        recovery = input_spec["state_recovery"]
        bindings = [
            {"model_id": metadata["model_id"], "weights_sha256": metadata["weights"]["sha256"]}
        ]
        if (
            recovery
            != {"mode": "opaque", "max_state_bytes": MAX_STATE_BYTES, "model_bindings": bindings}
            or type(manifest["limits"].get("max_message_bytes")) is not int
            or not 1 <= manifest["limits"]["max_message_bytes"] <= MAX_MESSAGE_BYTES
        ):
            raise BoundaryError("native_agent", "opaque_state_contract_required")
        self.metadata, self.manifest = metadata, manifest
        self.scorer = NativeStructuredScorer(
            model, metadata["model_id"], metadata["weights"]["sha256"]
        )
        self.closed = False

    def verify_weights(self) -> None:
        if (
            hashlib.sha256(encode_native_weights(self.scorer.model)).hexdigest()
            != self.scorer.weights_sha256
        ):
            raise BoundaryError("native_agent", "actual_weights_changed")

    def consume(self, value: dict[str, Any]) -> dict[str, Any]:
        self.verify_weights()
        observation = value["observation"]
        kind = "none" if observation["interaction"] is None else observation["interaction"]["kind"]
        if kind not in self.manifest["support"]["interaction_kinds"] or any(
            a["verb"] not in self.manifest["support"]["action_verbs"] for a in value["catalog"]
        ):
            raise BoundaryError("native_agent", "unsupported_whole_native_input")
        return self.scorer.propose_consume(value)

    def next(self, value: dict[str, Any]) -> dict[str, Any]:
        value = object_fields(
            value,
            {
                "continuity_token",
                "consumption_id",
                "state_version",
                "basis_acquisition_id",
                "received_cursor",
            },
            "native_agent.next",
        )
        scorer = self.scorer
        if (
            scorer.pending is not None
            or scorer.frame is None
            or value["continuity_token"] != scorer.continuity
            or value["consumption_id"] != scorer.consumption_id
            or type(value["state_version"]) is not int
            or value["state_version"] != scorer.state_version
            or value["basis_acquisition_id"] != scorer.acquisition_id
        ):
            raise BoundaryError("native_agent", "acknowledged_next_basis_required")
        self.verify_weights()
        values = scorer.scores()
        directive: dict[str, Any]
        if values:
            selected = max(range(len(values)), key=values.__getitem__)
            directive = {
                "type": "act",
                "basis_acquisition_id": scorer.acquisition_id,
                "selection": {"kind": "handle", "action_id": scorer.frame.action_ids[selected]},
                "scores": {"catalog_digest": scorer.frame.candidate_digest, "values": list(values)},
            }
        elif isinstance(value["received_cursor"], str) and value["received_cursor"]:
            directive = {
                "type": "await",
                "after_cursor": value["received_cursor"],
                "condition": "observation",
                "timeout_ms": 30000,
            }
        else:
            directive = {"type": "abstain", "reason": "known_source_cursor_required"}
        return {
            "continuity_token": scorer.continuity,
            "consumption_id": scorer.consumption_id,
            "state_version": scorer.state_version,
            "directive": directive,
        }

    def check_metadata(
        self, expected: dict[str, Any], *, restoring: bool = False
    ) -> dict[str, Any]:
        expected = object_fields(expected, META_FIELDS, "native_agent.state_metadata")
        manifest = self.manifest
        if (
            expected["agent_artifact_id"] != manifest["artifact"]["id"]
            or expected["agent_artifact_sha256"] != manifest["artifact"]["sha256"]
            or expected["adapter_code_sha256"] != manifest["adapter"]["code_sha256"]
            or expected["model_bindings"] != self.scorer.model_bindings
            or expected["input_spec"] != INPUT_SPEC
            or expected["profile"] != PROFILE
            or expected["state_format_version"] != STATE_FORMAT
        ):
            raise BoundaryError("native_agent", "state_package_input_spec_binding")
        basis = object_fields(
            expected["last_acknowledged_basis"], BASIS_FIELDS, "native_agent.state_basis"
        )
        digest(basis["capture_sha256"], "native_agent.capture_sha256")
        if not restoring:
            scorer = self.scorer
            if (
                scorer.pending is not None
                or scorer.unit is None
                or scorer.input is None
                or scorer.prefix is None
                or expected["stream_generation"] != scorer.unit.occurrence[0]
                or expected["continuity_token"] != scorer.continuity
                or expected["consumption_id"] != scorer.consumption_id
                or expected["state_version"] != scorer.state_version
                or expected["prefix"] != scorer.prefix
                or basis["acquisition_id"] != scorer.acquisition_id
                or basis["snapshot_id"] != scorer.unit.occurrence[1]
                or basis["owner_occurrence"] != scorer.input["observation"]["owner_occurrence"]
                or basis["revision"] != scorer.unit.revision
                or basis["included"] != list(SCOPE)
                or basis["publication_index"] != scorer.prefix["consumed_publication_index"]
            ):
                raise BoundaryError("native_agent", "durable_acknowledged_prefix_required")
        return copy.deepcopy(expected)

    def export_state(self, expected: dict[str, Any]) -> dict[str, Any]:
        self.verify_weights()
        metadata = self.check_metadata(expected)
        raw = encode_checkpoint(
            {"schema": STATE_FORMAT, "metadata": metadata, "state": self.scorer.state()}
        )
        if not 0 < len(raw) <= MAX_STATE_BYTES:
            raise BoundaryError("native_agent", "state_size_limit")
        return {
            "metadata": metadata,
            "payload": {
                "encoding": "base64",
                "byte_count": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "data_base64": base64.b64encode(raw).decode("ascii"),
            },
        }

    def restore_state(self, expected: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        if self.scorer.unit is not None or self.scorer.pending is not None:
            raise BoundaryError("native_agent", "fresh_state_restore_required")
        self.verify_weights()
        expected = self.check_metadata(expected, restoring=True)
        state = object_fields(state, {"metadata", "payload"}, "native_agent.opaque_state")
        payload = object_fields(
            state["payload"],
            {"encoding", "byte_count", "sha256", "data_base64"},
            "native_agent.opaque_payload",
        )
        count, text = payload["byte_count"], payload["data_base64"]
        if (
            state["metadata"] != expected
            or payload["encoding"] != "base64"
            or type(count) is not int
            or not 0 < count <= MAX_STATE_BYTES
            or not isinstance(text, str)
            or len(text) != ((count + 2) // 3) * 4
        ):
            raise BoundaryError("native_agent", "opaque_state_metadata_or_size")
        try:
            raw = base64.b64decode(text, validate=True)
        except (ValueError, UnicodeEncodeError) as error:
            raise BoundaryError("native_agent", "invalid_base64") from error
        if (
            len(raw) != count
            or base64.b64encode(raw).decode("ascii") != text
            or hashlib.sha256(raw).hexdigest() != payload["sha256"]
        ):
            raise BoundaryError("native_agent", "opaque_state_integrity")
        value = object_fields(
            decode_checkpoint(raw), {"schema", "metadata", "state"}, "native_agent.state"
        )
        if value["schema"] != STATE_FORMAT or value["metadata"] != expected:
            raise BoundaryError("native_agent", "state_format_binding")
        candidate = NativeStructuredScorer(
            self.scorer.model, self.scorer.model_id, self.scorer.weights_sha256
        )
        candidate.restore(value["state"])
        previous = self.scorer
        self.scorer = candidate
        try:
            self.check_metadata(expected)
        except Exception:
            self.scorer = previous
            raise
        return {"metadata": expected}


def bind_native_agent(
    package: Path,
    manifest_path: Path,
    *,
    manifest_id: str,
    requirements: dict[str, Any],
    support: dict[str, Any],
    required_seams: list[dict[str, str]],
) -> dict[str, Any]:
    metadata, model = load_native_package(package)
    del model
    if manifest_path.exists() or manifest_path.is_symlink():
        raise BoundaryError("native_agent", "manifest_destination_exists")
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "manifest_id": manifest_id,
        "agent": {
            "id": AGENT_SPEC["id"],
            "version": AGENT_SPEC["version"],
            "provider": "stpd",
            "architecture": "structured-native-m2-k1d96",
        },
        "adapter": adapter_identity(),
        "artifact": {
            "id": metadata["model_id"],
            "path": str((package / MANIFEST_NAME).resolve()),
            "sha256": hashlib.sha256((package / MANIFEST_NAME).read_bytes()).hexdigest(),
        },
        "input": {
            "profile": PROFILE,
            "input_spec": INPUT_SPEC,
            "projection": {"id": INPUT_SPEC["id"], "version": PROJECTION_VERSION},
            "state_format_version": STATE_FORMAT,
            "history_mode": "full_reference",
            "consumption_mode": "once_per_occurrence",
            "gap_policy": "handoff",
            "state_recovery": {
                "mode": "opaque",
                "max_state_bytes": MAX_STATE_BYTES,
                "model_bindings": [
                    {
                        "model_id": metadata["model_id"],
                        "weights_sha256": metadata["weights"]["sha256"],
                    }
                ],
            },
            "attachment": {
                "eager_scope": list(SCOPE),
                "required_seams": required_seams,
                "delivery_mode": "full_reference",
            },
        },
        "requirements": requirements,
        "support": support,
        "limits": LIMIT_MAXIMA,
        "claims": {
            "catalog_filtered": False,
            "creates_action_authority": False,
            "creates_native_operands": False,
            "human_origin": False,
            "causal_successor": False,
        },
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        manifest_path.write_bytes(json_bytes(manifest))
        NativeStructuredAgent(package, manifest_path)
    except Exception:
        manifest_path.unlink(missing_ok=True)
        raise
    return manifest


def serve(agent: NativeStructuredAgent, source: TextIO, destination: TextIO) -> int:
    def emit(value: dict[str, Any]) -> None:
        destination.write(json_bytes(value).decode("utf-8"))
        destination.flush()

    emit({"schema": SESSION_SCHEMA, "message_type": "ready", "adapter": agent.manifest["adapter"]})
    context: tuple[str, int] | None = None
    pending_request: str | None = None
    seen: set[str] = set()
    while True:
        line = source.readline(agent.manifest["limits"]["max_message_bytes"] + 1)
        if not line:
            break
        if (
            not line.endswith("\n")
            or len(line.encode("utf-8")) > agent.manifest["limits"]["max_message_bytes"]
        ):
            raise BoundaryError("native_agent", "message_size_or_framing")
        message = decode_json(line)
        kind = message.get("message_type")
        content = "completion" if kind == "consume_ack" else "input"
        message = object_fields(
            message,
            {"schema", "message_type", "session_id", "recovery_epoch", "request_id", content},
            "native_agent.message",
        )
        request = message["request_id"]
        common = {key: message[key] for key in ("session_id", "recovery_epoch", "request_id")}
        current = (common["session_id"], common["recovery_epoch"])
        if (
            len(line.encode("utf-8")) > agent.manifest["limits"]["max_message_bytes"]
            or not line.endswith("\n")
            or message["schema"] != SESSION_SCHEMA
            or not isinstance(request, str)
            or not request
            or not isinstance(current[0], str)
            or not current[0]
            or type(current[1]) is not int
            or current[1] < 0
            or context is not None
            and current != context
        ):
            raise BoundaryError("native_agent", "message_session_or_size")
        context = current
        if kind == "consume_ack":
            if request != pending_request:
                raise BoundaryError("native_agent", "consume_ack_request_binding")
            agent.scorer.acknowledge(message["completion"])
            pending_request = None
            continue
        if request in seen or len(seen) >= 65536:
            raise BoundaryError("native_agent", "request_reuse_or_capacity")
        seen.add(request)
        if pending_request is not None:
            raise BoundaryError("native_agent", "consume_ack_before_next_required")
        if kind == "consume":
            result, output_kind, field = agent.consume(message["input"]), "consumed", "completion"
            pending_request = request
        elif kind == "next":
            result, output_kind, field = agent.next(message["input"]), "directive", "output"
        elif kind == "export_state":
            value = object_fields(message["input"], {"expected_metadata"}, "native_agent.export")
            result, output_kind, field = (
                agent.export_state(value["expected_metadata"]),
                "state_exported",
                "output",
            )
        elif kind == "restore_state":
            value = object_fields(
                message["input"], {"expected_metadata", "state"}, "native_agent.restore"
            )
            result, output_kind, field = (
                agent.restore_state(value["expected_metadata"], value["state"]),
                "state_restored",
                "output",
            )
        else:
            raise BoundaryError("native_agent", "unsupported_message_type")
        emit({"schema": SESSION_SCHEMA, "message_type": output_kind, **common, field: result})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(2)
    return serve(NativeStructuredAgent(args.package, args.manifest), sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
