"""Freeze short-segment mu targets once; rebuild inputs without refitting them.

Only immutable tensor input metadata is reusable across theta updates. Stored
acting distributions are preserved; forced-state and boundary values are filled
from the same named frozen mu before any dataset SGD.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any

import torch

from stpd.p1.execution import microbatches
from stpd.p1.export import input_spec, runtime_projection
from stpd.p1.input import project
from stpd.p1.model import MStd
from stpd.p1.tensor_input import TensorFrame, compile_frame

from .heads import OUTCOME_CLASSES, categorical_lambda_targets
from .loss import SegmentSample


def _frame(value: dict[str, Any], model: MStd) -> TensorFrame:
    projection = runtime_projection(model.vocabulary)
    if value["input_spec"] != input_spec(model.vocabulary, projection):
        raise ValueError("segment_model_input_spec_changed")
    with torch.device("cpu"):
        return compile_frame(project(value["observation"], input_projection=projection), model)


def freeze_targets(
    segments: list[dict[str, Any]], models: Mapping[str, MStd], *, gpu_lock: Any = None
) -> dict[str, Any]:
    current: list[torch.Tensor] = []
    targets: list[torch.Tensor] = []
    raw_advantages: list[torch.Tensor] = []
    multi = []
    members = []
    lock = gpu_lock if gpu_lock is not None else nullcontext()
    # Group all missing value-only work by the exact behavior snapshot. Never
    # substitute the current learner or stitch lambda across mu boundaries.
    deferred: dict[str, list[tuple[int, int, TensorFrame, int]]] = {}
    probabilities: list[list[torch.Tensor | None]] = []
    boundaries: list[torch.Tensor | None] = [None] * len(segments)
    for index, segment in enumerate(segments):
        key = segment["mu"]["snapshot_sha256"]
        model = models[key]
        p = []
        for local, step in enumerate(segment["steps"]):
            value = step["behavior"]["outcome_probabilities"]
            p.append(torch.tensor(value, dtype=torch.float32) if value is not None else None)
            if value is None:
                deferred.setdefault(key, []).append(
                    (index, local, _frame(step["input"], model), step["behavior"]["floor"])
                )
            multi.append(len(step["input"]["catalog"]) > 1)
        probabilities.append(p)
        if segment["terminal"] is None:
            final = segment["steps"][-1]
            public_floor = (
                (final["successor"]["observation"].get("run") or {}).get("content") or {}
            ).get("floor")
            boundary_floor = max(
                final["behavior"]["floor"],
                public_floor if type(public_floor) is int else final["behavior"]["floor"],
            )
            deferred.setdefault(key, []).append(
                (index, -1, _frame(final["successor"], model), boundary_floor)
            )
        members.append({"segment_id": segment["segment_id"], "rows": len(p)})
    for key, work in deferred.items():
        model = models[key]
        if model.training:
            raise ValueError("behavior_snapshot_must_be_immutable_eval")
        frames = [item[2] for item in work]
        for indices in microbatches(frames, list(range(len(work))), model):
            with lock, torch.inference_mode():
                _, logits, _ = model.forward_rl4([frames[i] for i in indices],
                    [work[i][3] for i in indices], policy=False, check_finite=True)
                values = logits.float().softmax(-1).cpu()
            for position, value in zip(indices, values, strict=True):
                index, local, _, _ = work[position]
                if local < 0:
                    boundaries[index] = value
                else:
                    probabilities[index][local] = value
    for index, segment in enumerate(segments):
        if any(value is None for value in probabilities[index]):
            raise ValueError("deferred_segment_values_missing")
        p = torch.stack(probabilities[index])
        if p.shape != (len(segment["steps"]), OUTCOME_CLASSES):
            raise ValueError("acting_outcome_distribution_shape_changed")
        target, advantage = categorical_lambda_targets(
            p,
            boundaries[index],
            terminal_class=segment["terminal"]["class"] if segment["terminal"] else None,
        )
        current.append(p)
        targets.append(target)
        raw_advantages.append(advantage)
    advantage = torch.cat(raw_advantages)
    policy_mask = torch.tensor(multi, dtype=torch.bool)
    selected = advantage[policy_mask]
    mean = selected.mean() if len(selected) else torch.tensor(0.0)
    scale = selected.std(unbiased=False).clamp_min(1e-8) if len(selected) else torch.tensor(1.0)
    normalized = torch.zeros_like(advantage)
    normalized[policy_mask] = (selected - mean) / scale
    return {
        "schema": "stpd/frozen-segment-targets-1",
        "members": members,
        "targets": torch.cat(targets),
        "probabilities": torch.cat(current),
        "advantages": normalized,
        "raw_advantages": advantage,
        "normalization": {
            "multi_rows": int(policy_mask.sum()),
            "mean": float(mean),
            "scale": float(scale),
        },
    }


def samples_from_frozen(
    segments: list[dict[str, Any]], models: Mapping[str, MStd], frozen: dict[str, Any]
) -> list[SegmentSample]:
    members = [
        {"segment_id": value["segment_id"], "rows": len(value["steps"])} for value in segments
    ]
    if (
        frozen.get("schema") != "stpd/frozen-segment-targets-1"
        or frozen["members"] != members
        or len(frozen["targets"]) != sum(value["rows"] for value in members)
    ):
        raise ValueError("frozen_dataset_membership_changed")
    rows = []
    for segment in segments:
        model = models[segment["mu"]["snapshot_sha256"]]
        for step in segment["steps"]:
            frame = _frame(step["input"], model)
            action_id = step["directive"]["directive"]["selection"]["action_id"]
            action = frame.choices.index(action_id)
            # Model choices preserve the same IDs; bind probabilities by the
            # complete original catalog rather than assuming a projection order.
            catalog = [item["choice_id"] for item in step["input"]["catalog"]]
            original = step["sampling"]["probabilities"]
            probabilities = [original[catalog.index(choice)] for choice in frame.choices]
            index = len(rows)
            rows.append(
                SegmentSample(
                    frame,
                    step["behavior"]["floor"],
                    f"{segment['run_id']}:{step['step_index']}",
                    action,
                    probabilities[action],
                    probabilities,
                    float(frozen["advantages"][index]),
                    frozen["targets"][index],
                )
            )
    return rows
