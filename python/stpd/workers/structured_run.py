"""Explicit local S0 training CLI and optional existing Store/Manifest/Reporter persistence.

Caller owns source qualification, purpose, permissions and training-use ledger.
Only frozen train runs update parameters; dev/test are evaluated separately.
No provider requests, downloaded executable, game access or implicit restart.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

import torch

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ArtifactStore, ManifestArtifactStore

from ..canonical import semantic_hash
from ..fullrun.structured_inputs import INPUT_ID, PROJECTION_VERSION
from ..fullrun.structured_sequences import (
    MAX_SOURCE_BYTES,
    SOURCE_SCHEMA,
    StructuredDataset,
    parse_structured_dataset,
)
from ..models.structured_m2 import GRAPH_ID
from ..models.structured_training import StructuredTrainingConfig, train_structured_model
from ..policy.structured_export import ROOT, export_structured_package
from .checkpoint_codec import encode_checkpoint
from .reporting import RunReporter

RUN_SCHEMA = "stpd/structured-m2-run-v1"
CHECKPOINT_SCHEMA = "stpd/structured-m2-training-checkpoint-v1"


def prepare_structured_run(
    store: ArtifactStore,
    dataset: StructuredDataset,
    producer: Producer,
    config: StructuredTrainingConfig,
    *,
    source_id: str | None = None,
) -> Manifest:
    """Persist a caller-authorized input using existing immutable artifact kinds."""
    config.validate()
    if source_id is None:
        payload = store.put_payload("source", io.BytesIO(dataset.source_bytes), "application/json")
        source = Manifest(
            "dataset",
            producer,
            payloads=(payload,),
            parameters=FrozenObject.of(
                {
                    "schema": SOURCE_SCHEMA,
                    "source_kind": dataset.source_kind,
                    "source_sha256": dataset.source_sha256,
                    "qualification": "engineering_only",
                }
            ),
        )
        store.publish(source)
    else:
        source = store.get_manifest(source_id)
        payload = source.payload("source")
        if (
            payload.size > MAX_SOURCE_BYTES
            or payload.sha256 != dataset.source_sha256
            or b"".join(store.read_payload(payload)) != dataset.source_bytes
        ):
            raise BoundaryError("structured_run", "source_payload_binding")
    training_input = Manifest(
        "training_input",
        producer,
        (Parent("source", source.artifact_id),),
        (payload,),
        FrozenObject.of(
            {
                "schema": "stpd/structured-m2-training-input-v1",
                "source_sha256": dataset.source_sha256,
                "projection": {
                    "id": INPUT_ID,
                    "version": PROJECTION_VERSION,
                    "I": False,
                    "F": False,
                },
                "splits": {
                    split: [run.run_id for run in dataset.runs if run.split == split]
                    for split in ("train", "dev", "test")
                },
                "qualification": "engineering_only",
            }
        ),
    )
    store.publish(training_input)
    experiment = Manifest(
        "experiment",
        producer,
        (Parent("training_input", training_input.artifact_id),),
        parameters=FrozenObject.of(
            {
                "schema": "stpd/experiment-v1",
                "purpose": "s0_agent_teacher_imitation",
                "graph_id": GRAPH_ID,
                "config": asdict(config),
            }
        ),
    )
    store.publish(experiment)
    run = Manifest(
        "run",
        producer,
        (
            Parent("training_input", training_input.artifact_id),
            Parent("experiment", experiment.artifact_id),
        ),
        parameters=FrozenObject.of(
            {
                "schema": RUN_SCHEMA,
                "config": asdict(config),
                "source_sha256": dataset.source_sha256,
                "torch_version": torch.__version__,
                "partition": "train",
            }
        ),
    )
    store.publish(run)
    return run


def run_structured_job(
    dataset: StructuredDataset,
    config: StructuredTrainingConfig,
    output: Path,
    producer: Producer,
    *,
    store: ArtifactStore | None = None,
    reporter: RunReporter | None = None,
    source_id: str | None = None,
) -> dict[str, Any]:
    """Train once and publish model/checkpoint/results; never overwrite/retry a run."""
    config.validate()
    if (
        not isinstance(dataset, StructuredDataset)
        or parse_structured_dataset(dataset.source_bytes) != dataset
    ):
        raise BoundaryError("structured_run", "source_projection_mismatch")
    if output.exists():
        raise BoundaryError("structured_run", "output_exists")
    if (store is None) != (reporter is None) or source_id is not None and store is None:
        raise BoundaryError("structured_run", "store_and_reporter_required")
    run = None
    if store is not None:
        run = prepare_structured_run(store, dataset, producer, config, source_id=source_id)
        assert reporter is not None
        if reporter.completed(run.artifact_id) is not None or reporter.events(run.artifact_id):
            raise BoundaryError("structured_run", "existing_attempt_requires_explicit_new_job")
    attempt = uuid.uuid4().hex
    started = time.perf_counter()

    def event(kind: str, **details: object) -> None:
        if run is not None:
            assert reporter is not None
            reporter.emit(
                Manifest(
                    "run_event",
                    producer,
                    (Parent("run", run.artifact_id),),
                    parameters=FrozenObject.of(
                        {
                            "schema": "stpd/run-event-v1",
                            "attempt": attempt,
                            "kind": kind,
                            "details": details,
                        }
                    ),
                )
            )

    try:
        event("started")
        result = train_structured_model(dataset, config)
        report = {
            "schema": "stpd/structured-m2-training-report-v1",
            "producer": producer.to_dict(),
            "source_sha256": dataset.source_sha256,
            "source_kind": dataset.source_kind,
            "teacher_sha256": semantic_hash(dataset.teacher.value()),
            "config": asdict(config),
            "metrics": result.metrics,
            "runtime": {
                "torch_version": torch.__version__,
                "device": "cpu",
                "dtype": "float32",
                "threads": 2,
            },
            "seconds": time.perf_counter() - started,
        }
        checkpoint_bytes = encode_checkpoint(
            {
                "schema": CHECKPOINT_SCHEMA,
                "graph_id": GRAPH_ID,
                "source_sha256": dataset.source_sha256,
                "producer": producer.to_dict(),
                "config": asdict(config),
                "model": dict(result.model.state_dict()),
                "optimizer": result.optimizer_state,
                "metrics": result.metrics,
            }
        )
        output.mkdir(parents=True)
        (output / "training.json").write_bytes(json_bytes(report))
        (output / "checkpoint.tensor-tree").write_bytes(checkpoint_bytes)
        metadata = export_structured_package(
            result.model,
            output / "agent",
            source_revision=producer.source_revision,
            data_sha256=dataset.source_sha256,
            source_kind=dataset.source_kind,
            teacher_sha256=semantic_hash(dataset.teacher.value()),
            training={"config": asdict(config), "metrics": result.metrics},
        )
        summary = {
            "package": str((output / "agent").resolve()),
            "model_id": metadata["model_id"],
            "model_manifest_sha256": hashlib.sha256(
                (output / "agent/model.json").read_bytes()
            ).hexdigest(),
            "code_sha256": metadata["adapter_code_sha256"],
            "report": str((output / "training.json").resolve()),
            "checkpoint": str((output / "checkpoint.tensor-tree").resolve()),
            "source_sha256": dataset.source_sha256,
            "evaluation_scope": result.metrics["evaluation_scope"],
            "optimizer_updates": result.metrics["optimizer_updates"],
        }
        if run is not None:
            assert store is not None and reporter is not None
            checkpoint_payload = store.put_payload(
                "checkpoint", io.BytesIO(checkpoint_bytes), "application/vnd.stpd.tensor-tree"
            )
            checkpoint = Manifest(
                "checkpoint",
                producer,
                (
                    Parent("run", run.artifact_id),
                    Parent("training_input", run.parent("training_input")),
                ),
                (checkpoint_payload,),
                FrozenObject.of(
                    {
                        "schema": CHECKPOINT_SCHEMA,
                        "source_sha256": dataset.source_sha256,
                        "optimizer_updates": result.metrics["optimizer_updates"],
                    }
                ),
            )
            store.publish(checkpoint)
            model_payloads = tuple(
                store.put_payload(
                    role, io.BytesIO((output / "agent" / filename).read_bytes()), media_type
                )
                for role, filename, media_type in (
                    ("package_manifest", "model.json", "application/json"),
                    ("weights", "weights.tensor-tree", "application/vnd.stpd.tensor-tree"),
                )
            )
            model = Manifest(
                "model",
                producer,
                (
                    Parent("run", run.artifact_id),
                    Parent("checkpoint", checkpoint.artifact_id),
                    Parent("training_input", run.parent("training_input")),
                ),
                model_payloads,
                FrozenObject.of(
                    {
                        "schema": "stpd/structured-m2-model-v1",
                        "model_id": metadata["model_id"],
                        "graph_id": GRAPH_ID,
                        "qualification": "engineering_only",
                    }
                ),
            )
            store.publish(model)
            report_payload = store.put_payload(
                "report", io.BytesIO(json_bytes(report)), "application/json"
            )
            completed = Manifest(
                "run_result",
                producer,
                (
                    Parent("run", run.artifact_id),
                    Parent("model", model.artifact_id),
                    Parent("checkpoint", checkpoint.artifact_id),
                    Parent("training_input", run.parent("training_input")),
                ),
                (report_payload,),
                FrozenObject.of(
                    {
                        "schema": "stpd/run-result-v1",
                        "state": "completed",
                        "partition": "train",
                        "source_sha256": dataset.source_sha256,
                        "qualification": "engineering_only",
                    }
                ),
            )
            result_id = reporter.complete(completed)
            selected = reporter.completed(run.artifact_id)
            if selected is None or selected.artifact_id != result_id:
                raise BoundaryError("structured_run", "completion_race")
            summary["artifacts"] = {
                "run": run.artifact_id,
                "model": model.artifact_id,
                "checkpoint": checkpoint.artifact_id,
                "result": result_id,
                "training_input": run.parent("training_input"),
            }
        event("completed", optimizer_updates=result.metrics["optimizer_updates"])
        return summary
    except Exception as error:
        event("failed", error_type=type(error).__name__)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument(
        "--repository", default="https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project"
    )
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--max-updates", type=int, default=1000)
    parser.add_argument("--store", type=Path)
    parser.add_argument("--source-id")
    args = parser.parse_args()
    if (
        args.input.is_symlink()
        or not args.input.is_file()
        or args.input.stat().st_size > MAX_SOURCE_BYTES
    ):
        raise BoundaryError("structured_run", "input_file_or_size")
    dataset = parse_structured_dataset(args.input.read_bytes())
    producer = Producer(
        args.repository,
        args.source_revision,
        hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
    )
    store = ManifestArtifactStore(LocalBlobStore(args.store.resolve())) if args.store else None
    reporter = ObjectStoreRunReporter(store, store.blobs) if store else None
    summary = run_structured_job(
        dataset,
        StructuredTrainingConfig(epochs=args.epochs, max_updates=args.max_updates),
        args.output.resolve(),
        producer,
        store=store,
        reporter=reporter,
        source_id=args.source_id,
    )
    print(json_bytes(summary).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
