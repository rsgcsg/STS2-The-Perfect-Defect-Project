"""Caller-admitted S0 sampled observation sequences, with verified advance flags.

Capsule hashes alone are provenance supplied by the caller; optional original
capsule JSON makes the exact sealed-byte to snapshot join independently checkable.
Neither an Agent choice nor a receipt is evidence of Human origin or execution.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from spireagent.json_boundary import (
    BoundaryError,
    FrozenObject,
    decode_json,
    digest,
    json_bytes,
    object_fields,
)

from .structured_inputs import MAX_SNAPSHOT_BYTES, StructuredFrame, project_structured_snapshot

SOURCE_SCHEMA = "stpd/structured-sequence-source-v1"
S0_INPUT_SPEC = "s0-admitted-policy-offers-v1"
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_RUNS = 128
MAX_STEPS_PER_RUN = 4096


@dataclass(frozen=True)
class StructuredStep:
    position: int
    frame: StructuredFrame
    chosen_action_id: str | None
    reset_before: bool
    advance: bool
    capsule_sha256: str
    capture_id: str
    capsule_verified: bool
    reset_reason: str | None


@dataclass(frozen=True)
class StructuredRun:
    run_id: str
    source_group: str
    split: str
    identity: FrozenObject
    steps: tuple[StructuredStep, ...]


@dataclass(frozen=True)
class StructuredDataset:
    source_kind: str
    teacher: FrozenObject
    runs: tuple[StructuredRun, ...]
    source_sha256: str
    source_bytes: bytes

    @property
    def independent_test_eligible(self) -> bool:
        return (
            self.source_kind == "agent"
            and all(
                any(run.split == split for run in self.runs) for split in ("train", "dev", "test")
            )
            and len({run.source_group for run in self.runs}) >= 3
        )

    @property
    def capsules_verified(self) -> bool:
        return all(step.capsule_verified for run in self.runs for step in run.steps)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 4096:
        raise BoundaryError("structured_source", name)
    return value


def parse_structured_dataset(raw: bytes) -> StructuredDataset:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SOURCE_BYTES:
        raise BoundaryError("structured_source", "source_bytes_limit")
    source = object_fields(
        decode_json(raw), {"schema", "source_kind", "teacher", "runs"}, "structured_source"
    )
    if (
        source["schema"] != SOURCE_SCHEMA
        or source["source_kind"] not in {"agent", "synthetic"}
        or not isinstance(source["runs"], list)
        or not 0 < len(source["runs"]) <= MAX_RUNS
    ):
        raise BoundaryError("structured_source", "source_schema_or_kind")
    teacher = object_fields(
        source["teacher"], {"id", "version", "parameters"}, "structured_source.teacher"
    )
    _text(teacher["id"], "teacher_identity")
    _text(teacher["version"], "teacher_identity")
    if not isinstance(teacher["parameters"], dict):
        raise BoundaryError("structured_source", "teacher_parameters")
    seen_runs: set[str] = set()
    split_groups: dict[str, str] = {}
    runs = []
    for value in source["runs"]:
        item = object_fields(
            value, {"run_id", "source_group", "split", "identity", "steps"}, "structured_source.run"
        )
        run_id = _text(item["run_id"], "run_id")
        group = _text(item["source_group"], "source_group")
        split = item["split"]
        if (
            run_id in seen_runs
            or split not in {"train", "dev", "test"}
            or group in split_groups
            and split_groups[group] != split
        ):
            raise BoundaryError("structured_source", "run_or_split_leakage")
        if (
            not isinstance(item["identity"], dict)
            or not item["identity"]
            or not isinstance(item["steps"], list)
            or not 0 < len(item["steps"]) <= MAX_STEPS_PER_RUN
        ):
            raise BoundaryError("structured_source", "run_identity_or_steps")
        if source["source_kind"] == "agent" and item["identity"].get("input_spec") != S0_INPUT_SPEC:
            raise BoundaryError("structured_source", "s0_model_offer_input_spec_required")
        seen_runs.add(run_id)
        split_groups[group] = split
        steps = []
        last_state: str | None = None
        last_session: object = None
        last_sequence = -1
        last_snapshot_id: str | None = None
        last_snapshot_digest: str | None = None
        seen_captures: dict[str, str] = {}
        for position, value in enumerate(item["steps"]):
            required = {
                "position",
                "snapshot",
                "capsule_sha256",
                "capture_id",
                "chosen_action_id",
                "reset_before",
                "advance",
            }
            if (
                not isinstance(value, dict)
                or not required <= set(value)
                or set(value) - required - {"capsule_json", "reset_reason"}
            ):
                raise BoundaryError("structured_source", "step_fields")
            if (
                type(value["position"]) is not int
                or value["position"] != position
                or type(value["reset_before"]) is not bool
                or type(value["advance"]) is not bool
                or position == 0
                and not value["reset_before"]
            ):
                raise BoundaryError("structured_source", "step_order_or_reset")
            capture_id = _text(value["capture_id"], "capture_id")
            capsule_sha = digest(value["capsule_sha256"], "structured_source.capsule_sha256")
            snapshot = value["snapshot"]
            frame = project_structured_snapshot(snapshot)
            if source["source_kind"] == "agent" and (
                snapshot["status"] != "interactive" or not frame.candidates
            ):
                raise BoundaryError("structured_source", "unoffered_observation_not_s0_model_input")
            if capture_id in seen_captures and seen_captures[capture_id] != capsule_sha:
                raise BoundaryError("structured_source", "capture_identity_reused")
            seen_captures[capture_id] = capsule_sha
            verified = False
            if "capsule_json" in value:
                capsule = value["capsule_json"]
                if not isinstance(capsule, str):
                    raise BoundaryError("structured_source", "capsule_json_required")
                capsule_bytes = capsule.encode("utf-8")
                if (
                    len(capsule_bytes) > MAX_SNAPSHOT_BYTES
                    or hashlib.sha256(capsule_bytes).hexdigest() != capsule_sha
                    or decode_json(capsule_bytes) != snapshot
                ):
                    raise BoundaryError("structured_source", "capsule_snapshot_join")
                verified = True
            if value["reset_before"]:
                last_state, last_session = None, None
                last_sequence, last_snapshot_id, last_snapshot_digest = -1, None, None
            reset_reason = value.get("reset_reason", "run_start" if position == 0 else None)
            if value["reset_before"] and not reset_reason:
                raise BoundaryError("structured_source", "reset_reason_required")
            if not value["reset_before"] and reset_reason is not None:
                raise BoundaryError("structured_source", "unexpected_reset_reason")
            if reset_reason is not None:
                _text(reset_reason, "reset_reason")
            expected_advance = frame.state_digest != last_state
            if value["advance"] != expected_advance:
                raise BoundaryError("structured_source", "advance_rule_mismatch")
            session, sequence = snapshot["session"], snapshot["sequence"]
            if (
                type(sequence) is not int
                or sequence < 0
                or not isinstance(session, dict)
                or set(session) != {"runtime_instance_id", "environment_fingerprint"}
                or any(not isinstance(x, str) or not x for x in session.values())
            ):
                raise BoundaryError("structured_source", "snapshot_identity")
            if last_session is not None and session != last_session:
                raise BoundaryError("structured_source", "session_change_requires_reset")
            snapshot_id = _text(snapshot["snapshot_id"], "snapshot_id")
            identity_digest = hashlib.sha256(
                json_bytes({key: item for key, item in snapshot.items() if key != "observed_at"})
            ).hexdigest()
            if (
                sequence < last_sequence
                or snapshot_id != last_snapshot_id
                and sequence == last_sequence
                or snapshot_id == last_snapshot_id
                and last_snapshot_digest != identity_digest
            ):
                raise BoundaryError("structured_source", "snapshot_order_or_identity")
            chosen = value["chosen_action_id"]
            if chosen is not None and (
                not isinstance(chosen, str) or chosen not in frame.action_ids
            ):
                raise BoundaryError("structured_source", "label_not_in_complete_catalog")
            steps.append(
                StructuredStep(
                    position,
                    frame,
                    chosen,
                    value["reset_before"],
                    value["advance"],
                    capsule_sha,
                    capture_id,
                    verified,
                    reset_reason,
                )
            )
            last_state, last_session = frame.state_digest, session
            last_sequence, last_snapshot_id, last_snapshot_digest = (
                sequence,
                snapshot_id,
                identity_digest,
            )
        runs.append(
            StructuredRun(run_id, group, split, FrozenObject.of(item["identity"]), tuple(steps))
        )
    return StructuredDataset(
        source["source_kind"],
        FrozenObject.of(teacher),
        tuple(runs),
        hashlib.sha256(raw).hexdigest(),
        raw,
    )
