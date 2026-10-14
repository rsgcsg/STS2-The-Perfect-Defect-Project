"""Closed numerical TRAIN projection and explicit device policy; no Torch import.

The application freezes this declaration in a Run before provider submission.
Projection bytes contain model inputs and supervision, never source archives,
canonical journal locators, store credentials or admission authority.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any

from spireagent.json_boundary import (
    BoundaryError,
    FrozenObject,
    decode_json,
    digest,
    json_bytes,
    object_fields,
)

from .fullrun.structured_inputs import StructuredCandidate, StructuredFrame, StructuredNode
from .fullrun.structured_sequences import (
    MAX_RUNS,
    MAX_SOURCE_BYTES,
    MAX_STEPS_PER_RUN,
    StructuredDataset,
    StructuredRun,
    StructuredStep,
)
from .structured_workload_contracts import StructuredBatchPolicy, StructuredWorkloadRequest

STAGE = "structured_remote"
CAPABILITY = "structured-m2-slice-v1"
REQUEST_SCHEMA = "stpd/structured-remote-request-v1"
PROJECTION_SCHEMA = "stpd/structured-numerical-train-projection-v1"
CHECKPOINT_SCHEMA = "stpd/structured-m2-remote-training-checkpoint-v1"
MAX_REQUEST_BYTES = 64 * 1024


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class StructuredDevicePolicy:
    device: str = "cpu"
    dtype: str = "float32"
    allow_tf32: bool = False
    deterministic_algorithms: bool = True
    cudnn_benchmark: bool = False

    def __post_init__(self) -> None:
        if (
            self.device not in {"cpu", "cuda:0"}
            or self.dtype != "float32"
            or self.allow_tf32 is not False
            or self.deterministic_algorithms is not True
            or self.cudnn_benchmark is not False
        ):
            raise BoundaryError(STAGE, "unsupported_explicit_device_policy")

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "stpd/structured-training-device-policy-v1", **asdict(self)}

    @classmethod
    def from_dict(cls, value: object) -> StructuredDevicePolicy:
        fields = dict(object_fields(value, {"schema", *cls.__dataclass_fields__}, STAGE))
        if fields.pop("schema") != "stpd/structured-training-device-policy-v1":
            raise BoundaryError(STAGE, "unsupported_device_policy_schema")
        return cls(**fields)


def numerical_projection(dataset: StructuredDataset) -> bytes:
    """Only already admitted TRAIN rows; no source_bytes or source identity payloads."""
    if not isinstance(dataset, StructuredDataset):
        raise BoundaryError(STAGE, "typed_preverified_dataset_required")
    runs = tuple(run for run in dataset.runs if run.split == "train")
    if not runs or not any(step.chosen_action_id is not None for run in runs for step in run.steps):
        raise BoundaryError(STAGE, "train_choices_required")
    raw = json_bytes(
        {
            "schema": PROJECTION_SCHEMA,
            "source_sha256": dataset.source_sha256,
            "runs": [
                {
                    "run_id": run.run_id,
                    "source_group": run.source_group,
                    "steps": [
                        {
                            "position": step.position,
                            "frame": asdict(step.frame),
                            "chosen_action_id": step.chosen_action_id,
                            "reset_before": step.reset_before,
                            "advance": step.advance,
                        }
                        for step in run.steps
                    ],
                }
                for run in runs
            ],
        }
    )
    if len(raw) > MAX_SOURCE_BYTES:
        raise BoundaryError(STAGE, "projection_byte_limit")
    return raw


def _tuple_rows(value: object, length: int) -> tuple[tuple[Any, ...], ...]:
    if not isinstance(value, list) or any(
        not isinstance(row, list) or len(row) != length for row in value
    ):
        raise BoundaryError(STAGE, "projection_tuple_shape")
    return tuple(tuple(row) for row in value)


def _frame(value: object) -> StructuredFrame:
    info = object_fields(value, set(StructuredFrame.__dataclass_fields__), STAGE)
    if not isinstance(info["nodes"], list) or not isinstance(info["candidates"], list):
        raise BoundaryError(STAGE, "projection_frame_inventory")
    nodes, candidates = [], []
    for value in info["nodes"]:
        node = dict(object_fields(value, set(StructuredNode.__dataclass_fields__), STAGE))
        if not isinstance(node["numeric"], list) or len(node["numeric"]) != 3:
            raise BoundaryError(STAGE, "projection_numeric_shape")
        node["numeric"] = tuple(node["numeric"])
        nodes.append(StructuredNode(**node))
    for value in info["candidates"]:
        candidate = dict(object_fields(value, set(StructuredCandidate.__dataclass_fields__), STAGE))
        candidate["roles"] = _tuple_rows(candidate["roles"], 2)
        candidates.append(StructuredCandidate(**candidate))
    return StructuredFrame(
        tuple(nodes),
        _tuple_rows(info["edges"], 3),
        tuple(candidates),
        info["state_digest"],
        info["candidate_digest"],
        _tuple_rows(info["ref_rows"], 2),
        _tuple_rows(info["entity_members"], 2),
    )


def parse_numerical_projection(raw: bytes) -> StructuredDataset:
    """Reconstruct typed numerics, without pretending to replay Human/source verification."""
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_SOURCE_BYTES:
        raise BoundaryError(STAGE, "projection_byte_limit")
    body = object_fields(decode_json(raw), {"schema", "source_sha256", "runs"}, STAGE)
    if body["schema"] != PROJECTION_SCHEMA or raw != json_bytes(body):
        raise BoundaryError(STAGE, "projection_schema_or_canonical_bytes")
    digest(body["source_sha256"], STAGE)
    if not isinstance(body["runs"], list) or not 1 <= len(body["runs"]) <= MAX_RUNS:
        raise BoundaryError(STAGE, "projection_run_limit")
    runs = []
    for value in body["runs"]:
        row = object_fields(value, {"run_id", "source_group", "steps"}, STAGE)
        if (
            any(
                not isinstance(row[name], str) or not 1 <= len(row[name]) <= 4096
                for name in ("run_id", "source_group")
            )
            or not isinstance(row["steps"], list)
            or not 1 <= len(row["steps"]) <= MAX_STEPS_PER_RUN
        ):
            raise BoundaryError(STAGE, "projection_run_shape")
        steps = []
        for index, value in enumerate(row["steps"]):
            step = object_fields(
                value, {"position", "frame", "chosen_action_id", "reset_before", "advance"}, STAGE
            )
            frame = _frame(step["frame"])
            if (
                type(step["position"]) is not int
                or step["position"] < 0
                or type(step["reset_before"]) is not bool
                or type(step["advance"]) is not bool
                or index == 0
                and step["reset_before"] is not True
                or step["chosen_action_id"] is not None
                and step["chosen_action_id"] not in frame.action_ids
            ):
                raise BoundaryError(STAGE, "projection_step_binding")
            if steps and step["position"] <= steps[-1].position:
                raise BoundaryError(STAGE, "projection_step_order")
            steps.append(
                StructuredStep(
                    step["position"],
                    frame,
                    step["chosen_action_id"],
                    step["reset_before"],
                    step["advance"],
                    "0" * 64,
                    "remote-projection",
                    False,
                    None,
                )
            )
        runs.append(
            StructuredRun(row["run_id"], row["source_group"], "train", FrozenObject(), tuple(steps))
        )
    if len({run.run_id for run in runs}) != len(runs):
        raise BoundaryError(STAGE, "duplicate_projection_run")
    dataset = StructuredDataset(
        "synthetic", FrozenObject(), tuple(runs), body["source_sha256"], b""
    )
    if numerical_projection(dataset) != raw:
        raise BoundaryError(STAGE, "projection_roundtrip_mismatch")
    return dataset


def build_structured_remote_request(
    workload: StructuredWorkloadRequest,
    *,
    execution_identity: dict[str, Any],
    code_scope: str,
    device_policy: StructuredDevicePolicy,
    numerical_projection_sha256: str,
    grant_expires_at_ms: int,
    event_step: int = 0,
) -> bytes:
    workload.validate()
    if workload.mode not in {"start", "resume"}:
        raise BoundaryError(STAGE, "reconciliation_is_parent_owned")
    digest(numerical_projection_sha256, STAGE)
    if type(grant_expires_at_ms) is not int or grant_expires_at_ms <= 0:
        raise BoundaryError(STAGE, "finite_grant_expiry_required")
    if type(event_step) is not int or event_step < 0:
        raise BoundaryError(STAGE, "event_step_required")
    raw = json_bytes(
        {
            "schema": REQUEST_SCHEMA,
            "workload": workload.to_dict(),
            "execution_identity": execution_identity,
            "code_scope": code_scope,
            "device_policy": device_policy.to_dict(),
            "numerical_projection_sha256": numerical_projection_sha256,
            "grant_expires_at_ms": grant_expires_at_ms,
            "event_step": event_step,
        }
    )
    if len(raw) > MAX_REQUEST_BYTES:
        raise BoundaryError(STAGE, "request_byte_limit")
    return raw


def parse_structured_remote_request(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_REQUEST_BYTES:
        raise BoundaryError(STAGE, "request_byte_limit")
    body = dict(
        object_fields(
            decode_json(raw),
            {
                "schema",
                "workload",
                "execution_identity",
                "code_scope",
                "device_policy",
                "numerical_projection_sha256",
                "grant_expires_at_ms",
                "event_step",
            },
            STAGE,
        )
    )
    if body["schema"] != REQUEST_SCHEMA or raw != json_bytes(body):
        raise BoundaryError(STAGE, "request_schema_or_canonical_bytes")
    workload = StructuredWorkloadRequest.from_dict(body["workload"])
    policy = StructuredDevicePolicy.from_dict(body["device_policy"])
    if (
        build_structured_remote_request(
            workload,
            execution_identity=body["execution_identity"],
            code_scope=body["code_scope"],
            device_policy=policy,
            numerical_projection_sha256=body["numerical_projection_sha256"],
            grant_expires_at_ms=body["grant_expires_at_ms"],
            event_step=body["event_step"],
        )
        != raw
    ):
        raise BoundaryError(STAGE, "request_roundtrip_mismatch")
    return body


def freeze_remote_execution_identity(
    base_identity: dict[str, Any],
    dataset: StructuredDataset,
    *,
    runtime: FrozenObject,
    device_policy: StructuredDevicePolicy,
    execution_binding_id: str,
    code_closure_sha256: str,
    batch_policy: StructuredBatchPolicy | None = None,
) -> dict[str, Any]:
    """Called after the existing source verifier, before immutable Run publication.

    A remote runtime/device is a new Run identity. This is never a CPU-to-CUDA
    resume conversion or permission to rewrite an existing Run.
    """
    digest(execution_binding_id, STAGE)
    digest(code_closure_sha256, STAGE)
    if (
        not isinstance(runtime, FrozenObject)
        or runtime.value().get("device") != device_policy.device
        or runtime.value().get("dtype") != device_policy.dtype
        or base_identity.get("source_sha256") != dataset.source_sha256
    ):
        raise BoundaryError(STAGE, "remote_identity_binding_mismatch")
    return {
        **base_identity,
        "engine": "structured-remote-tbptt-v1",
        "runtime": runtime.value(),
        "training_device_policy": device_policy.to_dict(),
        **({"batch_policy": batch_policy.to_dict()} if batch_policy is not None else {}),
        "numerical_projection_sha256": sha256(numerical_projection(dataset)),
        "execution_binding_id": execution_binding_id,
        "remote_code_closure_sha256": code_closure_sha256,
    }


def remote_code_closure_sha256(code_scope: str) -> str:
    """Extend the owned numerical closure; never claim unchanged CUDA source scope."""
    from .canonical import semantic_hash
    from .structured_code_scope import ROOT
    from .structured_profiles import numerical_code_identity

    paths = (
        "spireagent/training_execution_contracts.py",
        "spireagent/training_transport.py",
        "stpd/structured_remote_contracts.py",
        "stpd/models/structured_device.py",
        "stpd/models/structured_batch.py",
        "stpd/workers/structured_remote_domain.py",
    )
    return semantic_hash(
        {
            "schema": "stpd/structured-remote-code-closure-v1",
            "numerical": numerical_code_identity(code_scope),
            "remote": {path: sha256((ROOT / path).read_bytes()) for path in paths},
        }
    )
