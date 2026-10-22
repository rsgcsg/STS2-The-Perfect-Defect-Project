"""One baseline worker on existing Store/Reporter/authority/control contracts.

This is a trusted learning engine, not a provider, scheduler or retry controller.
The application owns Curation, cost/attempt budgets and writer reconciliation.
G1/G2 have zero optimizer updates. fit_steps records actual bounded work separately.
"""

from __future__ import annotations

import io
import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.store import ArtifactStore
from stpd.workers.checkpoint_codec import decode_checkpoint
from stpd.workers.reporting import RunReporter
from stpd.workers.structured_control import (
    AttemptAuthority,
    ExecutionControl,
    StructuredExecutionResult,
)

from .baseline_export import export_model, input_spec, load_model
from .baseline_spec import CHECKPOINT_SCHEMA, MODEL_SCHEMA
from .baseline_workload import BaselineWorkload, populations_from_snapshot, verify_baseline_run
from .baselines import BaselineModel, BaselineTrainer
from .data import digest


@dataclass(frozen=True)
class BaselineCheckpoint:
    manifest: Manifest
    raw: bytes
    state: dict[str, Any]


def verify_baseline_checkpoint(
    store: ArtifactStore, checkpoint_id: str, workload: BaselineWorkload
) -> BaselineCheckpoint:
    checkpoint = store.get_manifest(checkpoint_id)
    info = checkpoint.parameters.value()
    if (
        checkpoint.kind != "checkpoint"
        or checkpoint.producer != workload.run.producer
        or info.get("schema") != CHECKPOINT_SCHEMA
        or {p.role for p in checkpoint.parents} != {"run", "training_input"}
        or checkpoint.parent("run") != workload.run.artifact_id
        or checkpoint.parent("training_input") != workload.training.artifact_id
        or {p.role for p in checkpoint.payloads} != {"checkpoint"}
    ):
        raise BoundaryError("p1_baseline", "checkpoint_run_binding_mismatch")
    raw = b"".join(store.read_payload(checkpoint.payload("checkpoint")))
    state = decode_checkpoint(raw)
    identity = state.get("identity", {})
    model = BaselineModel.from_state(state["model"])
    if (
        state.get("schema") != CHECKPOINT_SCHEMA
        or identity.get("snapshot_id") != workload.snapshot.artifact_id
        or identity.get("config") != workload.config.to_dict()
        or identity.get("categories_sha256")
        != workload.training.parameters.value()["features_sha256"]
        or input_spec(model) != workload.training.parameters.value()["input_spec"]
        or type(state.get("step")) is not int
        or not 0 <= state["step"] <= workload.training.parameters.value()["planned_steps"]
        or state["step"] != info.get("optimizer_updates")
        or state.get("fit_steps") != info.get("fit_steps")
        or state.get("phase") != info.get("phase")
        or state.get("phase") not in {"training", "trained", "completed"}
    ):
        raise BoundaryError("p1_baseline", "checkpoint_content_binding_mismatch")
    return BaselineCheckpoint(checkpoint, raw, state)


def materialize_baseline_model(
    store: ArtifactStore, model_id: str, destination: Path
) -> dict[str, Any]:
    model = store.get_manifest(model_id)
    info = model.parameters.value()
    if model.kind != "model" or info.get("schema") != MODEL_SCHEMA:
        raise BoundaryError("p1_baseline", "baseline_model_required")
    destination.mkdir(parents=True, exist_ok=False)
    for role, name in (("weights", "model.tensor-tree"), ("package_manifest", "model.json")):
        with (destination / name).open("wb") as target:
            for chunk in store.read_payload(model.payload(role)):
                target.write(chunk)
    loaded, package = load_model(destination / "model.tensor-tree", info["package_sha256"])
    descriptor = json.loads((destination / "model.json").read_bytes())
    if (
        descriptor["package_sha256"] != info["package_sha256"]
        or package["input_spec"] != info["input_spec"]
        or descriptor["input_spec"] != info["input_spec"]
        or descriptor["algorithm"] != loaded.algorithm
        or descriptor["checkpoint_sha256"] != package["checkpoint_sha256"]
        or descriptor["training_identity"] != package["training_identity"]
    ):
        raise BoundaryError("p1_baseline", "model_package_binding_mismatch")
    return descriptor


def verify_baseline_result(
    store: ArtifactStore, result_id: str, workload: BaselineWorkload
) -> tuple[Manifest, Manifest, BaselineCheckpoint]:
    result = store.get_manifest(result_id)
    info = result.parameters.value()
    if (
        result.kind != "run_result"
        or result.producer != workload.run.producer
        or info.get("schema") != "stpd/run-result-v1"
        or info.get("family") != MODEL_SCHEMA
        or info.get("state") != "completed"
        or result.parent("run") != workload.run.artifact_id
        or result.parent("training_input") != workload.training.artifact_id
    ):
        raise BoundaryError("p1_baseline", "result_run_binding_mismatch")
    checkpoint = verify_baseline_checkpoint(store, result.parent("last_checkpoint"), workload)
    model = store.get_manifest(result.parent("model"))
    metadata = model.parameters.value()
    if (
        checkpoint.state["phase"] != "completed"
        or model.kind != "model"
        or model.producer != workload.run.producer
        or metadata.get("schema") != MODEL_SCHEMA
        or {p.role for p in model.parents} != {"run", "training_input", "checkpoint"}
        or model.parent("run") != workload.run.artifact_id
        or model.parent("training_input") != workload.training.artifact_id
        or model.parent("checkpoint") != checkpoint.manifest.artifact_id
        or info.get("optimizer_updates") != checkpoint.state["step"]
        or metadata.get("input_spec") != workload.training.parameters.value()["input_spec"]
    ):
        raise BoundaryError("p1_baseline", "model_result_binding_mismatch")
    with tempfile.TemporaryDirectory(prefix="p1-baseline-verify-") as temporary:
        path = Path(temporary) / "model"
        materialize_baseline_model(store, model.artifact_id, path)
        loaded, payload = load_model(path / "model.tensor-tree", metadata["package_sha256"])
        state_model = BaselineModel.from_state(checkpoint.state["best_model"])
        if (
            payload["checkpoint_sha256"] != digest(checkpoint.raw)
            or payload["training_identity"] != checkpoint.state["identity"]
            or loaded.categories != state_model.categories
            or loaded.counts != state_model.counts
            or not torch.equal(loaded.weights, state_model.weights)
        ):
            raise BoundaryError("p1_baseline", "exported_model_checkpoint_mismatch")
    evaluation = store.get_manifest(result.parent("evaluation"))
    if (
        evaluation.kind != "offline_evaluation"
        or evaluation.producer != workload.run.producer
        or evaluation.parent("model") != model.artifact_id
        or evaluation.parameters.value().get("test") != checkpoint.state["test_result"]
        or evaluation.parameters.value().get("dev") != checkpoint.state["dev_result"]
    ):
        raise BoundaryError("p1_baseline", "evaluation_result_binding_mismatch")
    return result, model, checkpoint


def execute_baseline_run(
    store: ArtifactStore,
    reporter: RunReporter,
    run_id: str,
    runtime: Producer,
    *,
    attempt_id: str,
    resume: str | None = None,
    stop_after: int | None = None,
    authority: AttemptAuthority | None = None,
    control: ExecutionControl | None = None,
    mode: str = "start",
) -> StructuredExecutionResult:
    workload = verify_baseline_run(store, run_id, runtime)
    operation_id = workload.run.parameters.value()["operation_id"]
    if mode not in {"start", "resume", "reconcile"} or (mode == "resume") != (resume is not None):
        raise BoundaryError("p1_baseline", "explicit_execution_mode_required")
    if not isinstance(attempt_id, str) or not attempt_id:
        raise BoundaryError("p1_baseline", "attempt_identity_required")
    if stop_after is not None and (type(stop_after) is not int or stop_after < 1):
        raise BoundaryError("p1_baseline", "invalid_fit_pause_budget")
    started = time.perf_counter()

    def guard() -> None:
        if authority is not None:
            authority.assert_current(run_id, operation_id, attempt_id)

    guard()
    existing = reporter.completed(run_id)
    if existing is not None:
        result, model, completed_checkpoint = verify_baseline_result(
            store, existing.artifact_id, workload
        )
        return StructuredExecutionResult(
            "completed",
            run_id,
            attempt_id,
            completed_checkpoint.manifest.artifact_id,
            result.artifact_id,
            model.artifact_id,
            result.parameters.value()["event_step"],
            completed_checkpoint.state["step"],
            time.perf_counter() - started,
        )
    if mode == "reconcile":
        raise BoundaryError("p1_baseline", "no_selected_result_reconcile_only")
    trainer = BaselineTrainer(
        populations_from_snapshot(store, workload.snapshot.artifact_id),
        workload.snapshot.artifact_id,
        workload.config,
    )
    if trainer.model.categories != workload.categories:
        raise BoundaryError("p1_baseline", "train_fitted_feature_inventory_drift")
    if resume is not None:
        if authority is not None:
            authority.authorize_resume(run_id, attempt_id, resume)
        trainer.restore(verify_baseline_checkpoint(store, resume, workload).raw)
    event_step = max((e.parameters.value()["step"] for e in reporter.events(run_id)), default=0)

    def event(kind: str, **details: Any) -> None:
        nonlocal event_step
        guard()
        event_step += 1
        reporter.emit(
            Manifest(
                "run_event",
                runtime,
                (Parent("run", run_id),),
                parameters=FrozenObject.of(
                    {
                        "schema": "stpd/run-event-v1",
                        "kind": kind,
                        "attempt": attempt_id,
                        "step": event_step,
                        "details": details,
                    }
                ),
            )
        )

    def save(reason: str) -> str:
        guard()
        raw = trainer.checkpoint()
        payload = store.put_payload(
            "checkpoint", io.BytesIO(raw), "application/vnd.stpd.tensor-tree"
        )
        checkpoint = Manifest(
            "checkpoint",
            runtime,
            (Parent("run", run_id), Parent("training_input", workload.training.artifact_id)),
            (payload,),
            FrozenObject.of(
                {
                    "schema": CHECKPOINT_SCHEMA,
                    "attempt": attempt_id,
                    "optimizer_updates": trainer.step,
                    "fit_steps": trainer.fit_steps,
                    "phase": trainer.phase,
                    "reason": reason,
                }
            ),
        )
        store.publish(checkpoint)
        event(
            "checkpoint",
            checkpoint_id=checkpoint.artifact_id,
            optimizer_updates=trainer.step,
            fit_steps=trainer.fit_steps,
            reason=reason,
        )
        return checkpoint.artifact_id

    initial_fit_steps = trainer.fit_steps
    event(
        "started",
        mode=mode,
        optimizer_updates=trainer.step,
        planned_steps=workload.training.parameters.value()["planned_steps"],
        algorithm=workload.config.algorithm,
        device="cpu",
    )
    while trainer.phase != "completed":
        guard()
        action = "continue" if control is None else control.requested_action()
        if action not in {"continue", "pause", "cancel"}:
            raise BoundaryError("p1_baseline", "invalid_control_action")
        if action != "continue" or (
            stop_after is not None
            and trainer.phase == "training"
            and trainer.fit_steps - initial_fit_steps >= stop_after
        ):
            state = "cancelled" if action == "cancel" else "paused"
            checkpoint_id = save(state)
            event(state, checkpoint_id=checkpoint_id, optimizer_updates=trainer.step)
            return StructuredExecutionResult(
                state,
                run_id,
                attempt_id,
                checkpoint_id,
                None,
                None,
                event_step,
                trainer.step,
                time.perf_counter() - started,
            )
        if trainer.phase == "training":
            event("progress", **trainer.advance())
        else:
            trainer.finish()  # G3 selects on DEV only; TEST is read once after selection.
    checkpoint_id = save("last")
    checkpoint = store.get_manifest(checkpoint_id)
    with tempfile.TemporaryDirectory(prefix="p1-baseline-export-") as temporary:
        path = Path(temporary)
        checkpoint_path = path / "last.tensor-tree"
        checkpoint_path.write_bytes(b"".join(store.read_payload(checkpoint.payload("checkpoint"))))
        descriptor = export_model(
            trainer.selected_model(), checkpoint_path, path / "model", trainer.identity
        )
        payloads = []
        for role, name, media in (
            ("weights", "model.tensor-tree", "application/vnd.stpd.tensor-tree"),
            ("package_manifest", "model.json", "application/json"),
        ):
            with (path / "model" / name).open("rb") as source:
                payloads.append(store.put_payload(role, source, media))
    guard()
    model = Manifest(
        "model",
        runtime,
        (
            Parent("run", run_id),
            Parent("training_input", workload.training.artifact_id),
            Parent("checkpoint", checkpoint_id),
        ),
        tuple(payloads),
        FrozenObject.of(
            {
                "schema": MODEL_SCHEMA,
                "package_sha256": descriptor["package_sha256"],
                "input_spec": descriptor["input_spec"],
                "algorithm": workload.config.algorithm,
                "selected_step": trainer.best_step,
                "training_device": "cpu",
                "selected_state": "dev_best"
                if workload.config.algorithm == "linear"
                else "last_fixed_recipe",
                "research_admission": "not_evaluated",
            }
        ),
    )
    store.publish(model)
    evaluation = Manifest(
        "offline_evaluation",
        runtime,
        (Parent("model", model.artifact_id),),
        parameters=FrozenObject.of(
            {
                "schema": "stpd/p1-baseline-N-evaluation-1",
                "dev": trainer.dev_result,
                "test": trainer.test_result,
                "model_selection": "dev_only"
                if workload.config.algorithm == "linear"
                else "none_fixed_recipe",
                "scientific_verdict": "not_claimed",
            }
        ),
    )
    store.publish(evaluation)
    event(
        "completed",
        model_id=model.artifact_id,
        checkpoint_id=checkpoint_id,
        optimizer_updates=trainer.step,
        fit_steps=trainer.fit_steps,
        stop_reason="fixed_recipe_complete",
    )
    result = Manifest(
        "run_result",
        runtime,
        (
            Parent("run", run_id),
            Parent("training_input", workload.training.artifact_id),
            Parent("model", model.artifact_id),
            Parent("last_checkpoint", checkpoint_id),
            Parent("evaluation", evaluation.artifact_id),
        ),
        parameters=FrozenObject.of(
            {
                "schema": "stpd/run-result-v1",
                "family": MODEL_SCHEMA,
                "state": "completed",
                "optimizer_updates": trainer.step,
                "fit_steps": trainer.fit_steps,
                "event_step": event_step,
                "checkpoint_ids": {},
                "selected_step": trainer.best_step,
                "stop_reason": "fixed_recipe_complete",
                "scientific_verdict": "not_claimed",
            }
        ),
    )
    guard()
    result_id = reporter.complete(result)
    return StructuredExecutionResult(
        "completed",
        run_id,
        attempt_id,
        checkpoint_id,
        result_id,
        model.artifact_id,
        event_step,
        trainer.step,
        time.perf_counter() - started,
    )
