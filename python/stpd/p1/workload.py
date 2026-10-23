"""Freeze/verify P1 M-std work on the existing Store and run artifact kinds."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.json_boundary import digest as checked_digest
from spireagent.storage.store import ArtifactStore
from stpd.fullrun.dataset_policy import training_sources

from .data import digest, json_bytes
from .dataset import SnapshotReader
from .derivation import DERIVATION
from .execution import MStdExecution
from .export import input_spec
from .formal_training import Population
from .input import INPUT_SPEC, fit_vocabulary, project
from .model import ARCHITECTURE
from .spec import (
    DERIVED_RUN_SCHEMA,
    RUN_SCHEMA,
    RUN_SCHEMAS,
    TARGET_SPEC,
    TRAINING_SCHEMA,
    MStdConfig,
)

ENTRYPOINT = "stpd.workers.p1_run:execute_p1_run"


def populations_from_snapshot(store: ArtifactStore, snapshot_id: str) -> dict[str, Population]:
    return populations_from_reader(SnapshotReader(store, snapshot_id))


def populations_from_reader(reader: SnapshotReader) -> dict[str, Population]:
    """Project one already verified immutable read session, without reloading it."""
    selection = reader.selection
    if selection["input_projection"] != INPUT_SPEC:
        raise BoundaryError("p1_workload", "current_InputSpec_required_for_new_training")
    counts = selection["partition_N"]
    if set(counts) - {"train", "dev", "test"} or not counts.get("train") or not counts.get("dev"):
        raise BoundaryError("p1_workload", "explicit_nonempty_train_dev_required")
    rows: dict[str, list[tuple[str, Any, int]]] = {s: [] for s in ("train", "dev", "test")}
    for reference, observation in reader.iter_points():
        frame = project(observation)
        target = [c["choice_id"] for c in frame.choices].index(reference["chosen"])
        rows[reference["split"]].append((reference["point_id"], frame, target))
    return {
        split: Population(
            tuple(row[1] for row in sorted(samples)),
            tuple(row[2] for row in sorted(samples)),
            tuple(row[0] for row in sorted(samples)),
        )
        for split, samples in rows.items()
    }


def prepare_p1_run(
    store: ArtifactStore,
    snapshot_id: str,
    producer: Producer,
    config: MStdConfig,
    operation_id: str,
    *, execution_policy: dict[str, Any] | None = None, initial_checkpoint: str | None = None,
) -> Manifest:
    """Caller reserves the existing application/Hub use before fitting vocabulary.

    This batch implements engineering runs only. Snapshot publication/readability
    is never promoted here into formal research admission.
    """
    checked_digest(operation_id, "p1_workload.operation_id", length=32)
    execution = (MStdExecution() if execution_policy is None
                 else MStdExecution.decode(execution_policy))
    if execution.cpu_threads != config.cpu_threads:
        raise BoundaryError("p1_workload", "formal_recipe_cpu_threads_mismatch")
    original = None
    if initial_checkpoint is not None:
        if execution_policy is None:
            raise BoundaryError("p1_derivation", "explicit_frozen_execution_policy_required")
        from .derivation import require_execution, source_workload

        checked_digest(initial_checkpoint, "p1_workload.initial_checkpoint")
        require_execution(execution)
        original, _ = source_workload(store, initial_checkpoint)
        if original.snapshot.artifact_id != snapshot_id or original.config != config:
            raise BoundaryError("p1_derivation", "same_snapshot_and_config_required")
    training_sources(store, snapshot_id)
    reader = SnapshotReader(store, snapshot_id)
    snapshot = reader.manifest
    if snapshot.parameters.value().get("purpose") != "engineering":
        raise BoundaryError("p1_workload", "engineering_snapshot_required_no_auto_admission")
    populations = populations_from_reader(reader)
    vocabulary = fit_vocabulary(list(populations["train"].frames))
    vocabulary_raw = json_bytes(vocabulary)
    payload = store.put_payload("vocabulary", io.BytesIO(vocabulary_raw), "application/json")
    training = Manifest(
        "training_input",
        producer,
        (Parent("source", snapshot_id),),
        (payload,),
        FrozenObject.of(
            {
                "schema": TRAINING_SCHEMA,
                "entrypoint": ENTRYPOINT,
                "config": config.to_dict(),
                "purpose": "engineering",
                "research_admission": "not_evaluated",
                "snapshot_selection_sha256": snapshot.payload("selection").sha256,
                "input_spec": input_spec(vocabulary),
                "input_projection": INPUT_SPEC,
                "target_spec": TARGET_SPEC,
                "architecture": ARCHITECTURE,
                "partition_N": {s: len(p.frames) for s, p in populations.items()},
                "planned_steps": config.steps(len(populations["train"].frames)),
                "vocabulary_sha256": digest(vocabulary_raw),
            }
        ),
    )
    store.publish(training)
    experiment = Manifest(
        "experiment",
        producer,
        (Parent("training_input", training.artifact_id),),
        parameters=FrozenObject.of(
            {"schema": "stpd/p1-mstd-experiment-1", "purpose": "engineering"}
        ),
    )
    store.publish(experiment)
    run = Manifest(
        "run",
        producer,
        (
            Parent("experiment", experiment.artifact_id),
            Parent("training_input", training.artifact_id),
            *((Parent("original_run", original.run.artifact_id),
               Parent("original_checkpoint", initial_checkpoint))
              if original is not None and initial_checkpoint is not None else ()),
        ),
        parameters=FrozenObject.of(
            {"schema": DERIVED_RUN_SCHEMA if original is not None else RUN_SCHEMA,
             "operation_id": operation_id, "event_index": "run-events-v1",
             **({"execution_policy": execution.to_dict()} if execution_policy is not None else {}),
             **({"derivation": DERIVATION}
                if original is not None else {})}
        ),
    )
    store.publish(run)
    return run


@dataclass(frozen=True)
class P1Workload:
    run: Manifest
    training: Manifest
    snapshot: Manifest
    config: MStdConfig
    vocabulary: dict[str, Any]
    reader: SnapshotReader
    execution: MStdExecution


def verify_p1_run(store: ArtifactStore, run_id: str, producer: Producer) -> P1Workload:
    training_sources(store, run_id)
    run = store.get_manifest(run_id)
    if (
        run.kind != "run"
        or run.producer != producer
        or run.parameters.value().get("schema") not in RUN_SCHEMAS
        or {p.role for p in run.parents} != (
            {"experiment", "training_input", "original_run", "original_checkpoint"}
            if run.parameters.value().get("schema") == DERIVED_RUN_SCHEMA
            else {"experiment", "training_input"})
    ):
        raise BoundaryError("p1_workload", "run_source_or_schema_mismatch")
    info_run = run.parameters.value()
    checked_digest(info_run.get("operation_id"), "p1_workload.operation_id", length=32)
    execution = (MStdExecution.decode(info_run["execution_policy"])
                 if "execution_policy" in info_run else MStdExecution())
    training = store.get_manifest(run.parent("training_input"))
    info = training.parameters.value()
    if (
        training.kind != "training_input"
        or training.producer != producer
        or info.get("schema") != TRAINING_SCHEMA
        or info.get("entrypoint") != ENTRYPOINT
        or {p.role for p in training.parents} != {"source"}
        or {p.role for p in training.payloads} != {"vocabulary"}
        or info.get("input_projection") != INPUT_SPEC
        or info.get("target_spec") != TARGET_SPEC
        or info.get("architecture") != ARCHITECTURE
        or info.get("purpose") != "engineering"
    ):
        raise BoundaryError("p1_workload", "training_input_binding_mismatch")
    config = MStdConfig.decode(info["config"])
    if execution.cpu_threads != config.cpu_threads:
        raise BoundaryError("p1_workload", "formal_recipe_cpu_threads_mismatch")
    reader = SnapshotReader(store, training.parent("source"))
    snapshot, selection = reader.manifest, reader.selection
    if snapshot.parameters.value().get("purpose") != "engineering":
        raise BoundaryError("p1_workload", "engineering_snapshot_required_no_auto_admission")
    vocabulary_raw = b"".join(store.read_payload(training.payload("vocabulary")))
    vocabulary = json.loads(vocabulary_raw)
    if (
        digest(vocabulary_raw) != info.get("vocabulary_sha256")
        or input_spec(vocabulary) != info.get("input_spec")
        or vocabulary.get("input_spec") != INPUT_SPEC
        or training.payload("vocabulary").sha256 != info.get("vocabulary_sha256")
        or info.get("snapshot_selection_sha256") != snapshot.payload("selection").sha256
        or info.get("partition_N")
        != {s: selection["partition_N"].get(s, 0) for s in ("train", "dev", "test")}
        or info.get("planned_steps") != config.steps(info["partition_N"]["train"])
    ):
        raise BoundaryError("p1_workload", "frozen_input_identity_mismatch")
    experiment = store.get_manifest(run.parent("experiment"))
    if (
        experiment.kind != "experiment"
        or experiment.producer != producer
        or experiment.parameters.value().get("schema") != "stpd/p1-mstd-experiment-1"
        or experiment.parent("training_input") != training.artifact_id
    ):
        raise BoundaryError("p1_workload", "experiment_input_mismatch")
    if info_run["schema"] == DERIVED_RUN_SCHEMA:
        from .derivation import DERIVATION, LEGACY_PRODUCER, require_execution

        if "execution_policy" not in info_run:
            raise BoundaryError("p1_derivation", "explicit_frozen_execution_policy_required")
        require_execution(execution)
        original_cp = store.get_manifest(run.parent("original_checkpoint"))
        original_run = store.get_manifest(run.parent("original_run"))
        original_training = store.get_manifest(original_run.parent("training_input"))
        if (info_run.get("derivation") != DERIVATION
                or original_cp.kind != "checkpoint" or original_cp.producer != LEGACY_PRODUCER
                or original_run.kind != "run" or original_run.producer != LEGACY_PRODUCER
                or original_run.parameters.value().get("schema") != RUN_SCHEMA
                or original_cp.parent("run") != original_run.artifact_id
                or original_cp.parent("training_input") != original_training.artifact_id
                or original_training.parameters != training.parameters
                or original_training.payloads != training.payloads
                or original_training.parents != training.parents):
            raise BoundaryError("p1_derivation", "original_training_lineage_mismatch")
    elif "derivation" in info_run:
        raise BoundaryError("p1_derivation", "explicit_derived_run_schema_required")
    return P1Workload(run, training, snapshot, config, vocabulary, reader, execution)
