"""One explicit full-state derivation from the frozen f7 M-std implementation.

This is recipe code, not a resume identity exception. Original artifacts are read
only. New numerical execution may change subsequent FP32 trajectories. Existing
DEV selection and data exposure are inherited, never reclassified as independent.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.store import ArtifactStore
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint

from .data import digest
from .execution import MStdExecution
from .spec import CHECKPOINT_SCHEMA, DERIVED_CHECKPOINT_SCHEMA

LEGACY_PRODUCER = Producer(
    "rsgcsg/STS2-The-Perfect-Defect-Project",
    "f7c77d0df11f591a8c5e285ce5b0586418428981",
    "1e1067532fbea42ccc18501c1703bf0e6ea0e86540bcb0356689b773fbb75bd8",
)
# SHA256 of exact git-show bytes at LEGACY_PRODUCER, not current source files.
LEGACY_CODE = {
    "formal_training.py": "8b866382d27913cf82fe5c60dbb9d7d05ed7437ffdd0f7cb18c6ccd0eec1214f",
    "spec.py": "298f04c721079688a97cc337eff8e0bf251add5095102b7e3cb562c532b8659d",
    "input.py": "90ef0c352330b1db73b789d0bd53549a5b11b65aa737dbcf57ef63897c43b9a9",
    "model.py": "4c722871defb7a0044c5cad473f7aace657e3b7d49cdf15aa55c598a74089a4f",
    "numerics.py": "8214e9916f8fad542360c5089656ad499c1969afd660a9693f29c81ac11b8ddc",
}
LEGACY_CODEC = "03343cbd9607b59c57c55d37eeb2596785e55546ba61ed89846fbb221fd3dcdd"
MAX_CHECKPOINTS = 128
DERIVATION = {
    "adapter": "f7-full-state-v1",
    "state_transfer": "all_nonidentity_fields_exact",
    "trajectory": "target_execution_frozen_future_bitwise_equivalence_not_asserted",
    "exposure": "inherits_original_training_not_independent_replicate",
    "inherited_DEV": "original_reference_execution_metrics_and_selection_not_recomputed",
    "new_DEV_TEST": "target_execution_on_future_normal_recipe_evaluations",
    "resource_policy": "new_attempt_admission_existing_cumulative_limits_not_reset_or_copied",
}


def state_sha256(state: dict[str, Any]) -> str:
    """Canonical tensor-tree encoding includes every field except execution identity."""
    return digest(encode_checkpoint({k: v for k, v in state.items() if k != "identity"}))


def require_execution(execution: MStdExecution) -> None:
    if execution not in {
        MStdExecution(),
        MStdExecution(implementation="experimental-packed-v1", microbatch_size=1),
    }:
        raise BoundaryError(
            "p1_derivation", "reference_or_packed_singleton_cpu2_execution_required"
        )


def source_workload(store: ArtifactStore, checkpoint_id: str) -> Any:
    from stpd.workers.p1_run import verify_p1_checkpoint

    from .workload import verify_p1_run

    checkpoint = store.get_manifest(checkpoint_id)
    if checkpoint.producer != LEGACY_PRODUCER:
        raise BoundaryError("p1_derivation", "unsupported_original_source_or_lock")
    workload = verify_p1_run(store, checkpoint.parent("run"), LEGACY_PRODUCER)
    checked = verify_p1_checkpoint(store, checkpoint_id, workload)
    identity = checked.state["identity"]
    if (
        identity.get("code") != LEGACY_CODE
        or identity.get("codec_sha256") != LEGACY_CODEC
        or "execution" in identity
    ):
        raise BoundaryError("p1_derivation", "unsupported_original_code_closure")
    if (
        checked.state["phase"] != "training"
        or checked.state.get("test_result") is not None
        or checked.state.get("stop_reason") is not None
        or checked.state["step"] < 1
    ):
        raise BoundaryError("p1_derivation", "unfinished_training_unopened_TEST_required")
    return workload, checked


def runtime_compatibility(source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """CPU same-runtime fixture/local path; CUDA only the observed f7/L40S stack."""
    before, after = dict(source), dict(target)
    old_python, new_python = before.pop("python", None), after.pop("python", None)
    if before != after:
        raise BoundaryError("p1_derivation", "unsupported_runtime_change")
    if before.get("device") == "cpu":
        if old_python != new_python:
            raise BoundaryError("p1_derivation", "unsupported_python_change")
    elif (
        old_python != "3.11.15"
        or new_python not in {"3.11.15", "3.11.17"}
        or before.get("torch") != "2.13.0+cu130"
        or before.get("cuda") != "13.0"
        or before.get("cuda_runtime", {}).get("name") != "NVIDIA L40S"
    ):
        raise BoundaryError("p1_derivation", "unsupported_frozen_CUDA_runtime")
    return {"source": source, "target": target, "python_patch_change": old_python != new_python}


def transfer_state(state: dict[str, Any], trainer: Any) -> tuple[bytes, str]:
    """Actual target trainer supplies identity; strict restore validates tensor bindings."""
    target = trainer.identity
    original = state.get("identity", {})
    if (
        original.get("code") != LEGACY_CODE
        or original.get("codec_sha256") != LEGACY_CODEC
        or "execution" in original
    ):
        raise BoundaryError("p1_derivation", "unsupported_original_code_closure")
    ignored = {"code", "runtime", "execution"}
    if {k: v for k, v in original.items() if k not in ignored} != {
        k: v for k, v in target.items() if k not in ignored
    }:
        raise BoundaryError("p1_derivation", "immutable_training_identity_mismatch")
    runtime_compatibility(original["runtime"], target["runtime"])
    template = decode_checkpoint(trainer.checkpoint())
    if set(state) != set(template) or state.get("schema") != CHECKPOINT_SCHEMA:
        raise BoundaryError("p1_derivation", "full_state_inventory_mismatch")
    size = len(trainer.populations["train"].frames)
    sampler = state["sampler"]
    if (
        set(sampler) != {"generator", "order", "cursor"}
        or sorted(sampler["order"]) != list(range(size))
        or any(type(v) is not int for v in sampler["order"])
        or type(sampler["cursor"]) is not int
        or not 0 <= sampler["cursor"] < size
        or type(state["epoch"]) is not int
        or not 0 <= state["epoch"] < trainer.config.max_epochs
        or type(state["step"]) is not int
        or not 0 < state["step"] < trainer.planned_steps
        or state["phase"] != "training"
        or state["test_result"] is not None
        or state["stop_reason"] is not None
    ):
        raise BoundaryError("p1_derivation", "invalid_original_training_progress")
    updates_per_epoch = math.ceil(size / trainer.config.batch_size)
    if (
        state["step"]
        != state["epoch"] * updates_per_epoch
        + math.ceil(sampler["cursor"] / trainer.config.batch_size)
        or len(state["history"]) != state["step"]
        or type(state["bad_epochs"]) is not int
        or not 0 <= state["bad_epochs"] < trainer.config.patience
    ):
        raise BoundaryError("p1_derivation", "invalid_original_training_progress")
    before = state_sha256(state)  # also validates every tensor is finite
    rewritten = dict(state)
    rewritten["identity"] = target
    raw = encode_checkpoint(rewritten)
    trainer.restore(raw)  # the ordinary strict restore, unchanged
    if state_sha256(decode_checkpoint(trainer.checkpoint())) != before:
        raise BoundaryError("p1_derivation", "nonidentity_state_changed_on_strict_restore")
    return raw, before


def derive_checkpoints(
    store: ArtifactStore,
    workload: Any,
    trainer: Any,
    attempt_id: str,
    publish_event: Callable[..., None],
    guard: Callable[[], None],
) -> Any:
    """Topologically remap the finite referenced best/milestone DAG, then restore root."""
    from stpd.workers.p1_run import verify_p1_checkpoint

    original_id = workload.run.parent("original_checkpoint")
    source, root = source_workload(store, original_id)
    mapped: dict[str, str] = {}
    visiting: set[str] = set()
    root_identity = root.state["identity"]
    transfer_state(root.state, trainer)  # validate root/runtime before publishing any derived CP

    def visit(identity: str) -> str:
        if identity in mapped:
            return mapped[identity]
        if identity in visiting or len(mapped) + len(visiting) >= MAX_CHECKPOINTS:
            raise BoundaryError("p1_derivation", "historical_checkpoint_DAG_limit_or_cycle")
        visiting.add(identity)
        # Traverse manifests first. Holding every decoded 70MB checkpoint on a
        # recursive best chain would multiply memory by historical chain length.
        manifest = store.get_manifest(identity)
        info = manifest.parameters.value()
        if (
            manifest.kind != "checkpoint"
            or manifest.producer != source.run.producer
            or info.get("schema") != CHECKPOINT_SCHEMA
            or not isinstance(info.get("prior_checkpoints"), dict)
        ):
            raise BoundaryError("p1_derivation", "historical_checkpoint_binding_mismatch")
        prior = {k: visit(v) for k, v in sorted(info["prior_checkpoints"].items())}
        best = visit(info["prior_best_checkpoint"]) if info.get("prior_best_checkpoint") else None
        checked = verify_p1_checkpoint(store, identity, source)
        if checked.state["identity"] != root_identity or checked.state["step"] > root.state["step"]:
            raise BoundaryError(
                "p1_derivation", "historical_checkpoint_identity_or_progress_mismatch"
            )
        raw, preserved = transfer_state(checked.state, trainer)
        guard()
        payload = store.put_payload(
            "checkpoint", io.BytesIO(raw), "application/vnd.stpd.tensor-tree"
        )
        parents = [
            Parent("run", workload.run.artifact_id),
            Parent("training_input", workload.training.artifact_id),
            Parent("original_checkpoint", identity),
        ]
        parents.extend(Parent("checkpoint_" + k, v) for k, v in sorted(prior.items()))
        if best is not None:
            parents.append(Parent("prior_best", best))
        manifest = Manifest(
            "checkpoint",
            workload.run.producer,
            tuple(parents),
            (payload,),
            FrozenObject.of(
                {
                    **info,
                    "schema": DERIVED_CHECKPOINT_SCHEMA,
                    "attempt": attempt_id,
                    "prior_checkpoints": prior,
                    "prior_best_checkpoint": best,
                    "derivation": DERIVATION,
                    "preserved_state_sha256": preserved,
                    "original_checkpoint": identity,
                    "original_payload_sha256": checked.manifest.payload("checkpoint").sha256,
                    "runtime_compatibility": runtime_compatibility(
                        root_identity["runtime"], trainer.identity["runtime"]
                    ),
                }
            ),
        )
        store.publish(manifest)
        mapped[identity] = manifest.artifact_id
        visiting.remove(identity)
        publish_event(
            "checkpoint",
            checkpoint_id=manifest.artifact_id,
            optimizer_updates=checked.state["step"],
            reason="full_state_derivation",
            original_checkpoint=identity,
            preserved_state_sha256=preserved,
        )
        return manifest.artifact_id

    derived = visit(original_id)
    checked = verify_p1_checkpoint(store, derived, workload)
    trainer.restore(checked.raw)
    if state_sha256(decode_checkpoint(trainer.checkpoint())) != state_sha256(root.state):
        raise BoundaryError("p1_derivation", "derived_root_state_changed")
    return checked
