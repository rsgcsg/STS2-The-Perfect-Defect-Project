"""Freeze the comparison recipe on existing P1 snapshot and Run artifacts."""

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

from .baseline_export import INPUT_PROJECTION, input_spec
from .baseline_features import fit_categories
from .baseline_spec import RUN_SCHEMA, TRAINING_SCHEMA, BaselineConfig
from .baselines import BaselineModel, BaselinePopulation
from .data import digest, json_bytes
from .dataset import iter_snapshot_points, load_snapshot
from .input import INPUT_SPEC
from .spec import TARGET_SPEC

ENTRYPOINT = "stpd.p1.baseline_worker:execute_baseline_run"


def populations_from_snapshot(
    store: ArtifactStore, snapshot_id: str
) -> dict[str, BaselinePopulation]:
    _, selection = load_snapshot(store, snapshot_id)
    counts = selection["partition_N"]
    if (
        selection["input_projection"] != INPUT_SPEC
        or set(counts) - {"train", "dev", "test"}
        or not counts.get("train")
        or not counts.get("dev")
    ):
        raise BoundaryError("p1_baseline", "current_input_and_nonempty_train_dev_required")
    rows: dict[str, list[tuple[str, dict[str, Any], int]]] = {
        s: [] for s in ("train", "dev", "test")
    }
    for reference, observation in iter_snapshot_points(store, snapshot_id):
        target = [c["choice_id"] for c in observation["catalog"]].index(reference["chosen"])
        rows[reference["split"]].append((reference["point_id"], observation, target))
    return {
        split: BaselinePopulation(
            tuple(r[1] for r in sorted(samples)),
            tuple(r[2] for r in sorted(samples)),
            tuple(r[0] for r in sorted(samples)),
        )
        for split, samples in rows.items()
    }


def prepare_baseline_run(
    store: ArtifactStore,
    snapshot_id: str,
    producer: Producer,
    config: BaselineConfig,
    operation_id: str,
) -> Manifest:
    """Caller records the same normal application/Curation use before this fit."""
    checked_digest(operation_id, "p1_baseline.operation_id", length=32)
    training_sources(store, snapshot_id)
    snapshot, _ = load_snapshot(store, snapshot_id)
    if snapshot.parameters.value().get("purpose") != "engineering":
        raise BoundaryError("p1_baseline", "engineering_snapshot_required_no_auto_admission")
    populations = populations_from_snapshot(store, snapshot_id)
    categories = (
        fit_categories(list(populations["train"].observations))
        if config.algorithm == "linear"
        else {}
    )
    model = BaselineModel(config.algorithm, categories)
    raw = json_bytes(categories)
    payload = store.put_payload("features", io.BytesIO(raw), "application/json")
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
                "input_spec": input_spec(model),
                "input_projection": INPUT_PROJECTION,
                "target_spec": TARGET_SPEC,
                "algorithm": config.algorithm,
                "partition_N": {s: len(p.point_ids) for s, p in populations.items()},
                "planned_steps": config.steps(len(populations["train"].point_ids)),
                "features_sha256": digest(raw),
            }
        ),
    )
    store.publish(training)
    experiment = Manifest(
        "experiment",
        producer,
        (Parent("training_input", training.artifact_id),),
        parameters=FrozenObject.of(
            {"schema": "stpd/p1-baseline-experiment-1", "purpose": "engineering"}
        ),
    )
    store.publish(experiment)
    run = Manifest(
        "run",
        producer,
        (
            Parent("experiment", experiment.artifact_id),
            Parent("training_input", training.artifact_id),
        ),
        parameters=FrozenObject.of(
            {"schema": RUN_SCHEMA, "operation_id": operation_id, "event_index": "run-events-v1"}
        ),
    )
    store.publish(run)
    return run


@dataclass(frozen=True)
class BaselineWorkload:
    run: Manifest
    training: Manifest
    snapshot: Manifest
    config: BaselineConfig
    categories: dict[str, list[str]]


def verify_baseline_run(store: ArtifactStore, run_id: str, producer: Producer) -> BaselineWorkload:
    training_sources(store, run_id)
    run = store.get_manifest(run_id)
    if (
        run.kind != "run"
        or run.producer != producer
        or run.parameters.value().get("schema") != RUN_SCHEMA
        or {p.role for p in run.parents} != {"experiment", "training_input"}
    ):
        raise BoundaryError("p1_baseline", "run_source_or_schema_mismatch")
    checked_digest(
        run.parameters.value().get("operation_id"), "p1_baseline.operation_id", length=32
    )
    training = store.get_manifest(run.parent("training_input"))
    info = training.parameters.value()
    if (
        training.kind != "training_input"
        or training.producer != producer
        or info.get("schema") != TRAINING_SCHEMA
        or info.get("entrypoint") != ENTRYPOINT
        or {p.role for p in training.parents} != {"source"}
        or {p.role for p in training.payloads} != {"features"}
        or info.get("input_projection") != INPUT_PROJECTION
        or info.get("target_spec") != TARGET_SPEC
        or info.get("purpose") != "engineering"
    ):
        raise BoundaryError("p1_baseline", "training_input_binding_mismatch")
    config = BaselineConfig.decode(info["config"])
    snapshot, selection = load_snapshot(store, training.parent("source"))
    raw = b"".join(store.read_payload(training.payload("features")))
    categories = json.loads(raw)
    model = BaselineModel(config.algorithm, categories)
    if (
        snapshot.parameters.value().get("purpose") != "engineering"
        or selection["input_projection"] != INPUT_SPEC
        or digest(raw) != info.get("features_sha256")
        or training.payload("features").sha256 != info.get("features_sha256")
        or info.get("input_spec") != input_spec(model)
        or info.get("algorithm") != config.algorithm
        or info.get("snapshot_selection_sha256") != snapshot.payload("selection").sha256
        or info.get("partition_N")
        != {s: selection["partition_N"].get(s, 0) for s in ("train", "dev", "test")}
        or info.get("planned_steps") != config.steps(info["partition_N"]["train"])
    ):
        raise BoundaryError("p1_baseline", "frozen_input_identity_mismatch")
    experiment = store.get_manifest(run.parent("experiment"))
    if (
        experiment.kind != "experiment"
        or experiment.producer != producer
        or experiment.parameters.value().get("schema") != "stpd/p1-baseline-experiment-1"
        or experiment.parent("training_input") != training.artifact_id
    ):
        raise BoundaryError("p1_baseline", "experiment_input_mismatch")
    return BaselineWorkload(run, training, snapshot, config, categories)
