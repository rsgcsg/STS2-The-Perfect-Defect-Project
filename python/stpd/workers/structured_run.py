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
from ..fullrun.structured_sequences import (
    MAX_SOURCE_BYTES,
    SOURCE_SCHEMA,
    StructuredDataset,
    parse_structured_dataset,
)
from ..models.structured_m2 import GRAPH_ID
from ..models.structured_training import StructuredTrainingConfig, train_structured_model
from ..native_graph_spec import RUN_SCHEMA as GRAPH_RUN_SCHEMA
from ..native_graph_spec import NativeGraphControl, optional_control
from ..structured_code_scope import LEGACY_SCOPE, ROOT, SCOPED_RUN_SCHEMA, TRAINING_SCOPE
from ..structured_profiles import (
    NATIVE_GRAPH_SCOPE,
    NATIVE_INPUT_SCHEMA,
    NATIVE_RUN_SCHEMA,
    NATIVE_SCOPES,
    NATIVE_SOURCE_SCHEMA,
    parse_dataset,
    profile_projection,
    qualification,
    source_verification,
    validate_profile,
)
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
    operation_id: str | None = None,
    code_scope: str = LEGACY_SCOPE,
    model_control: NativeGraphControl | None = None,
) -> Manifest:
    """Persist a caller-authorized input using existing immutable artifact kinds."""
    config.validate()
    model_control = optional_control(model_control)
    if (model_control is not None) != (code_scope == NATIVE_GRAPH_SCOPE):
        raise BoundaryError("structured_run", "control_scope_mismatch")
    native = validate_profile(dataset, code_scope)
    scoped = code_scope in {TRAINING_SCOPE, *NATIVE_SCOPES}
    if (parse_dataset(dataset.source_bytes, code_scope) != dataset
            or scoped and operation_id is None):
        raise BoundaryError("structured_run", "unsupported_code_scope")
    execution_parameters: dict[str, Any] = {}
    if operation_id is not None:
        from spireagent.json_boundary import digest

        from ..models.structured_engine import execution_identity

        digest(operation_id, "structured_run.operation_id", length=32)
        torch.set_num_threads(config.cpu_threads)
        identity = execution_identity(
            dataset, config, code_scope=code_scope, model_control=model_control
        )
        if (scoped
                and producer.uv_lock_sha256 != identity["code_identity"]["dependency_lock_sha256"]):
            raise BoundaryError("structured_run", "producer_lock_identity_mismatch")
        execution_parameters = {"operation_id": operation_id, "execution_identity": identity}
    source_info = {
        "schema": NATIVE_SOURCE_SCHEMA if native else SOURCE_SCHEMA,
        "source_kind": dataset.source_kind, "source_sha256": dataset.source_sha256,
        "qualification": qualification(dataset),
        **({"input_spec": dataset.input_spec.value(),
            "verification_identity": source_verification(dataset),
            "code_identity": execution_parameters["execution_identity"]["code_identity"]}
           if native and dataset.input_spec else {}),
    }
    if source_id is None:
        payload = store.put_payload("source", io.BytesIO(dataset.source_bytes), "application/json")
        source = Manifest(
            "dataset",
            producer,
            payloads=(payload,),
            parameters=FrozenObject.of(source_info),
        )
        store.publish(source)
    else:
        source = store.get_manifest(source_id)
        payload = source.payload("source")
        if (
            payload.size > MAX_SOURCE_BYTES
            or payload.sha256 != dataset.source_sha256
            or b"".join(store.read_payload(payload)) != dataset.source_bytes
            or native and (source.kind != "dataset" or source.parameters.value() != source_info
                           or {p.role for p in source.payloads} != {"source"}
                           or source.parents or payload.media_type != "application/json")
        ):
            raise BoundaryError("structured_run", "source_payload_binding")
    training_input = Manifest(
        "training_input",
        producer,
        (Parent("source", source.artifact_id),),
        (payload,),
        FrozenObject.of(
            {
                "schema": NATIVE_INPUT_SCHEMA if native else "stpd/structured-m2-training-input-v1",
                "source_sha256": dataset.source_sha256,
                "projection": profile_projection(dataset, code_scope),
                **({"input_spec": dataset.input_spec.value(),
                    "verification_identity": source_verification(dataset),
                    "code_identity": execution_parameters["execution_identity"]["code_identity"]}
                   if native and dataset.input_spec else {}),
                "splits": {
                    split: [run.run_id for run in dataset.runs if run.split == split]
                    for split in ("train", "dev", "test")
                },
                "qualification": qualification(dataset),
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
                "schema": ("stpd/native-structured-experiment-v1"
                           if native else "stpd/experiment-v1"),
                "purpose": ("native_synthetic_teacher_imitation"
                            if native else "s0_agent_teacher_imitation"),
                "graph_id": GRAPH_ID if model_control is None else model_control.id,
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
                "schema": (GRAPH_RUN_SCHEMA
                           if code_scope == NATIVE_GRAPH_SCOPE else
                           NATIVE_RUN_SCHEMA if native else
                           SCOPED_RUN_SCHEMA if code_scope == TRAINING_SCOPE else
                           "stpd/structured-m2-run-v2" if operation_id is not None else RUN_SCHEMA),
                "config": asdict(config),
                "source_sha256": dataset.source_sha256,
                "torch_version": torch.__version__,
                "partition": "train",
                **execution_parameters,
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
    from ..policy.structured_export import export_structured_package

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
