"""Explicit synthetic native training source, directly replaying native units.

No legacy snapshot, Human origin, executed successor, or native recording
qualification is synthesized. The shared engine consumes the resulting frames.
"""

from __future__ import annotations

import hashlib

from spireagent.json_boundary import (
    BoundaryError,
    FrozenObject,
    decode_json,
    json_bytes,
    object_fields,
)

from ..structured_profiles import NATIVE_SOURCE_SCHEMA
from .native_structured_inputs import INPUT_SPEC
from .native_structured_sequences import MAX_SOURCE_BYTES, SOURCE_SCHEMA, parse_native_sequence
from .structured_sequences import MAX_RUNS, StructuredDataset, StructuredRun, StructuredStep, _text


def parse_native_training_dataset(raw: bytes) -> StructuredDataset:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SOURCE_BYTES:
        raise BoundaryError("native_training_source", "source_byte_limit")
    source = object_fields(
        decode_json(raw),
        {"schema", "source_kind", "input_spec", "teacher", "runs"},
        "native_training_source",
    )
    if (
        source["schema"] != NATIVE_SOURCE_SCHEMA
        or source["source_kind"] != "synthetic"
        or source["input_spec"] != INPUT_SPEC
        or not isinstance(source["runs"], list)
        or not 0 < len(source["runs"]) <= MAX_RUNS
    ):
        raise BoundaryError("native_training_source", "source_identity_or_scope")
    teacher = object_fields(
        source["teacher"], {"id", "version", "parameters"}, "native_training_source.teacher"
    )
    _text(teacher["id"], "teacher_identity")
    _text(teacher["version"], "teacher_identity")
    if not isinstance(teacher["parameters"], dict):
        raise BoundaryError("native_training_source", "teacher_parameters")
    runs = []
    seen: set[str] = set()
    groups: dict[str, str] = {}
    for value in source["runs"]:
        run = object_fields(
            value,
            {"run_id", "source_group", "split", "identity", "steps"},
            "native_training_source.run",
        )
        name, group = _text(run["run_id"], "run_id"), _text(run["source_group"], "source_group")
        split = run["split"]
        if (
            name in seen
            or split not in {"train", "dev", "test"}
            or group in groups
            and groups[group] != split
            or not isinstance(run["identity"], dict)
            or not run["identity"]
        ):
            raise BoundaryError("native_training_source", "run_or_split_identity")
        sequence = parse_native_sequence(
            json_bytes(
                {
                    "schema": SOURCE_SCHEMA,
                    "source_kind": "synthetic",
                    "input_spec": INPUT_SPEC,
                    "steps": run["steps"],
                }
            )
        )
        steps = []
        for position, step in enumerate(sequence.steps):
            original = run["steps"][position]
            captured = json_bytes(
                {"observation": original["observation"], "catalog": original["catalog"]}
            )
            steps.append(
                StructuredStep(
                    position,
                    step.frame,
                    step.chosen_action_id,
                    step.reset_before,
                    step.advance,
                    hashlib.sha256(captured).hexdigest(),
                    original["observation"]["snapshot_id"],
                    False,
                    "native_continuity_segment" if step.reset_before else None,
                )
            )
        seen.add(name)
        groups[group] = split
        runs.append(
            StructuredRun(name, group, split, FrozenObject.of(run["identity"]), tuple(steps))
        )
    return StructuredDataset(
        "synthetic",
        FrozenObject.of(teacher),
        tuple(runs),
        hashlib.sha256(raw).hexdigest(),
        raw,
        FrozenObject.of(INPUT_SPEC),
    )
