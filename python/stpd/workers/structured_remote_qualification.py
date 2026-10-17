"""Bounded synthetic numerical diagnostic for an exact installed worker image.

No provider calls, source admission, real training cohort or game semantics.
Root's finite provider App invokes this CLI in its locked worker interpreter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import torch

from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.source import source_identity

from ..canonical import semantic_hash
from ..fullrun.structured_inputs import StructuredCandidate, StructuredFrame, StructuredNode
from ..fullrun.structured_sequences import StructuredDataset, StructuredRun, StructuredStep
from ..models.structured_batch import BatchStructuredTrainingEngine
from ..models.structured_device import (
    cpu_model_from_checkpoint,
    structured_device_runtime,
    structured_device_session,
)
from ..models.structured_engine import execution_identity
from ..structured_code_scope import ROOT, TRAINING_SCOPE
from ..structured_remote_contracts import (
    StructuredDevicePolicy,
    freeze_remote_execution_identity,
    remote_code_closure_sha256,
)
from ..structured_workload_contracts import StructuredBatchPolicy, StructuredTrainingConfig
from .checkpoint_codec import decode_checkpoint


def synthetic_dataset() -> StructuredDataset:
    runs = []
    for lane in range(3):
        steps = []
        for position in range(9 - lane):
            frame = StructuredFrame(
                (
                    StructuredNode("player", "object"),
                    StructuredNode("hp", "int", numeric=(1.0, (12 - position) / 20, 0.1)),
                    StructuredNode("card", "object"),
                    StructuredNode("description", "text", "Deal 6 damage."),
                ),
                ((1, 0, 0), (3, 2, 0)),
                (
                    StructuredCandidate(
                        "play", "native_input", "play", "Play Strike", (("card", 2),)
                    ),
                    StructuredCandidate("end", "native_input", "end_turn", "End turn", ()),
                ),
                semantic_hash([lane, position]),
                semantic_hash(["play", "end"]),
                (),
                ((0, 1), (2, 3)),
            )
            steps.append(
                StructuredStep(
                    position,
                    frame,
                    None if position == 1 else "play" if (position + lane) % 2 else "end",
                    position in {0, 6},
                    position != 2,
                    "0" * 64,
                    f"synthetic-{lane}-{position}",
                    False,
                    "synthetic_reset" if position in {0, 6} else None,
                )
            )
        runs.append(
            StructuredRun(
                f"synthetic-run-{lane}",
                f"synthetic-group-{lane}",
                "train",
                FrozenObject(),
                tuple(steps),
            )
        )
    return StructuredDataset(
        "synthetic", FrozenObject(), tuple(runs), semantic_hash([asdict(run) for run in runs]), b""
    )


def make_engine(
    data: StructuredDataset, config: StructuredTrainingConfig, device: StructuredDevicePolicy
) -> BatchStructuredTrainingEngine:
    base = execution_identity(data, config, code_scope=TRAINING_SCOPE)
    identity = freeze_remote_execution_identity(
        base,
        data,
        runtime=FrozenObject.of(structured_device_runtime(device)),
        device_policy=device,
        execution_binding_id="0" * 64,
        code_closure_sha256=remote_code_closure_sha256(TRAINING_SCOPE),
        batch_policy=StructuredBatchPolicy(2),
    )
    return BatchStructuredTrainingEngine(
        data, config, code_scope=TRAINING_SCOPE, identity=identity, policy=device
    )


def tree_equal(left: Any, right: Any) -> bool:
    if isinstance(left, torch.Tensor):
        return isinstance(right, torch.Tensor) and torch.equal(left.cpu(), right.cpu())
    if isinstance(left, dict):
        return (
            isinstance(right, dict)
            and set(left) == set(right)
            and all(tree_equal(left[key], right[key]) for key in left)
        )
    if isinstance(left, (list, tuple)):
        return (
            type(left) is type(right)
            and len(left) == len(right)
            and all(tree_equal(a, b) for a, b in zip(left, right, strict=True))
        )
    return bool(left == right)


def profile(device: str, *, updates: int = 2) -> dict[str, Any]:
    if type(updates) is not int or not 2 <= updates <= 997:
        raise BoundaryError("structured_qualification", "bounded_diagnostic_updates_required")
    data = synthetic_dataset()
    config = StructuredTrainingConfig(epochs=100, max_updates=updates)
    producer = source_identity(ROOT)
    started = time.monotonic()
    with structured_device_session(StructuredDevicePolicy(), cpu_threads=2):
        reference = make_engine(data, config, StructuredDevicePolicy())
        reference.advance_chunk()
        reference.advance_chunk()
        reference_weights = {
            name: tensor.detach().cpu().clone()
            for name, tensor in reference.model.state_dict().items()
        }
        reference_losses = list(reference.chunk_losses)
    cpu_reference_seconds = time.monotonic() - started
    selected = StructuredDevicePolicy(device)
    with structured_device_session(selected, cpu_threads=2):
        setup_started = time.monotonic()
        engine = make_engine(data, config, selected)
        setup_seconds = time.monotonic() - setup_started
        observed = structured_device_runtime(selected)
        numerical_started = time.monotonic()
        engine.advance_chunk()
        raw = engine.checkpoint()
        resumed = make_engine(data, config, selected)
        resumed.restore(raw)
        if not tree_equal(decode_checkpoint(raw), decode_checkpoint(resumed.checkpoint())):
            raise BoundaryError("structured_qualification", "checkpoint_resume_mismatch")
        engine.advance_chunk()
        resumed.advance_chunk()
        if (
            not tree_equal(engine.model.state_dict(), resumed.model.state_dict())
            or not tree_equal(engine.optimizer.state_dict(), resumed.optimizer.state_dict())
            or not tree_equal(engine.memory, resumed.memory)
        ):
            raise BoundaryError("structured_qualification", "resumed_continuation_mismatch")
        weight_difference = max(
            float((tensor.detach().cpu() - reference_weights[name]).abs().max())
            for name, tensor in engine.model.state_dict().items()
        )
        for name, tensor in engine.model.state_dict().items():
            torch.testing.assert_close(
                tensor.detach().cpu(), reference_weights[name], rtol=2e-4, atol=2e-5
            )
        loss_difference = max(
            abs(a - b) for a, b in zip(engine.chunk_losses, reference_losses, strict=True)
        )
        if loss_difference > 2e-5:
            raise BoundaryError("structured_qualification", "cpu_device_joint_loss_mismatch")
        while not engine.training_complete:
            engine.advance_chunk()
        final = engine.checkpoint()
        numeric_resume_seconds = time.monotonic() - numerical_started
        export_started = time.monotonic()
        from ..policy.structured_export import export_structured_package, load_structured_package

        cpu_model = cpu_model_from_checkpoint(final, engine.identity)
        with tempfile.TemporaryDirectory(prefix="structured-diagnostic-export-") as directory:
            destination = Path(directory) / "model"
            exported = export_structured_package(
                cpu_model,
                destination,
                source_revision=producer.source_revision,
                data_sha256=data.source_sha256,
                source_kind="synthetic",
                teacher_sha256=semantic_hash({}),
                training={"diagnostic_only": True, "actual_updates": engine.updates},
            )
            loaded_metadata, loaded_model = load_structured_package(destination)
            if loaded_metadata != exported or not tree_equal(
                cpu_model.state_dict(), loaded_model.state_dict()
            ):
                raise BoundaryError("structured_qualification", "cpu_export_load_mismatch")
            exported_manifest_sha256 = hashlib.sha256(
                (destination / "model.json").read_bytes()
            ).hexdigest()
        export_seconds = time.monotonic() - export_started
        seconds = time.monotonic() - started
        return {
            "schema": "stpd/structured-worker-numerical-diagnostic-v1",
            "evidence": "synthetic_numeric_device_resume_only",
            "producer": producer.to_dict(),
            "runtime": observed,
            "code_closure_sha256": remote_code_closure_sha256(TRAINING_SCOPE),
            "source_sha256": data.source_sha256,
            "batch_policy": StructuredBatchPolicy(2).to_dict(),
            "actual_updates": engine.updates,
            "diagnostic_total_optimizer_executions": engine.updates + 3,
            "label_uses": engine.labels,
            "boundaries": engine.boundary,
            "observations": engine.observations,
            "cpu_reference_updates": 2,
            "max_weight_abs_difference": weight_difference,
            "max_loss_abs_difference": loss_difference,
            "comparison_tolerance": {"weight_rtol": 2e-4, "weight_atol": 2e-5, "loss_atol": 2e-5},
            "same_device_checkpoint_exact": True,
            "same_device_continuation_exact": True,
            "initial_checkpoint_sha256": hashlib.sha256(raw).hexdigest(),
            "final_checkpoint_sha256": hashlib.sha256(final).hexdigest(),
            "cpu_export_load_exact": True,
            "exported_manifest_sha256": exported_manifest_sha256,
            "exported_weights_sha256": exported["weights"]["sha256"],
            "seconds": seconds,
            "stage_seconds": {
                "cpu_reference": cpu_reference_seconds,
                "selected_device_setup": setup_seconds,
                "numeric_checkpoint_resume": numeric_resume_seconds,
                "cpu_export_load": export_seconds,
            },
            "main_updates_per_diagnostic_second": engine.updates / seconds,
            "claims": {
                "vectorized_speedup": False,
                "human": False,
                "scientific_training": False,
                "all_scene_quality": False,
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), required=True)
    parser.add_argument("--updates", type=int, default=2)
    args = parser.parse_args()
    print(json.dumps(profile(args.device, updates=args.updates), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
