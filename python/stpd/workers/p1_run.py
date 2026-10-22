"""N-only M-std worker on existing Store/Reporter/authority/control seams.

The same function executes in the local private child and ComputeRequest
dispatch. It publishes immutable candidates, never chooses a cloud lease or
retries a request. Source/data admission belongs to the caller.
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

from ..p1.data import digest
from ..p1.export import export_model, load_model
from ..p1.formal_training import MStdTrainer
from ..p1.spec import CHECKPOINT_FRACTIONS, CHECKPOINT_SCHEMA, MODEL_SCHEMA, TARGET_SPEC
from ..p1.workload import P1Workload, populations_from_reader, verify_p1_run
from .checkpoint_codec import decode_checkpoint
from .reporting import RunReporter
from .structured_control import AttemptAuthority, ExecutionControl, StructuredExecutionResult

PERIODIC_CHECKPOINT_SECONDS = 1800


@dataclass(frozen=True)
class P1Checkpoint:
    manifest: Manifest
    raw: bytes
    state: dict[str, Any]


def verify_p1_checkpoint(
    store: ArtifactStore, checkpoint_id: str, workload: P1Workload
) -> P1Checkpoint:
    checkpoint = store.get_manifest(checkpoint_id)
    info = checkpoint.parameters.value()
    if (
        checkpoint.kind != "checkpoint"
        or checkpoint.producer != workload.run.producer
        or info.get("schema") != CHECKPOINT_SCHEMA
        or checkpoint.parent("run") != workload.run.artifact_id
        or checkpoint.parent("training_input") != workload.training.artifact_id
        or {p.role for p in checkpoint.payloads} != {"checkpoint"}
    ):
        raise BoundaryError("p1_worker", "checkpoint_run_binding_mismatch")
    raw = b"".join(store.read_payload(checkpoint.payload("checkpoint")))
    state = decode_checkpoint(raw)
    identity = state.get("identity", {})
    training = workload.training.parameters.value()
    if (
        state.get("schema") != CHECKPOINT_SCHEMA
        or identity.get("snapshot_id") != workload.snapshot.artifact_id
        or identity.get("config") != workload.config.to_dict()
        or identity.get("input_spec") != training["input_spec"]
        or identity.get("vocabulary_sha256") != training["vocabulary_sha256"]
        or identity.get("target_spec") != TARGET_SPEC
        or type(state.get("step")) is not int
        or not 0 <= state["step"] <= training["planned_steps"]
        or state.get("step") != info.get("optimizer_updates")
        or state.get("phase") != info.get("phase")
        or state.get("phase") not in {"training", "trained", "completed"}
        or not isinstance(state.get("model"), dict)
    ):
        raise BoundaryError("p1_worker", "checkpoint_content_binding_mismatch")
    prior = info.get("prior_checkpoints")
    fractions = info.get("fractions")
    if (
        not isinstance(prior, dict)
        or any(k not in {str(f) for f in CHECKPOINT_FRACTIONS} for k in prior)
        or not isinstance(fractions, list)
        or any(type(f) is not int or f not in CHECKPOINT_FRACTIONS for f in fractions)
    ):
        raise BoundaryError("p1_worker", "checkpoint_schedule_binding_mismatch")
    required = {"run", "training_input", *("checkpoint_" + k for k in prior)}
    if info.get("prior_best_checkpoint") is not None:
        required.add("prior_best")
        if checkpoint.parent("prior_best") != info["prior_best_checkpoint"]:
            raise BoundaryError("p1_worker", "checkpoint_best_parent_mismatch")
    if {p.role for p in checkpoint.parents} != required:
        raise BoundaryError("p1_worker", "checkpoint_parent_inventory_mismatch")
    for fraction, previous_id in prior.items():
        if checkpoint.parent("checkpoint_" + fraction) != previous_id:
            raise BoundaryError("p1_worker", "checkpoint_schedule_binding_mismatch")
        previous = store.get_manifest(previous_id)
        if previous.kind != "checkpoint" or previous.parent("run") != workload.run.artifact_id:
            raise BoundaryError("p1_worker", "foreign_scheduled_checkpoint")
    return P1Checkpoint(checkpoint, raw, state)


def materialize_p1_model(store: ArtifactStore, model_id: str, destination: Path) -> dict[str, Any]:
    """One owned export path for CLI/app consumers of the trained Model artifact."""
    model = store.get_manifest(model_id)
    info = model.parameters.value()
    if model.kind != "model" or info.get("schema") != MODEL_SCHEMA:
        raise BoundaryError("p1_worker", "P1_model_required")
    destination.mkdir(parents=True, exist_ok=False)
    for role, name in (("weights", "model.tensor-tree"), ("package_manifest", "model.json")):
        with (destination / name).open("wb") as target:
            for chunk in store.read_payload(model.payload(role)):
                target.write(chunk)
    loaded, package = load_model(destination / "model.tensor-tree", info["package_sha256"])
    descriptor: dict[str, Any] = json.loads((destination / "model.json").read_bytes())
    if (
        descriptor["package_sha256"] != info["package_sha256"]
        or package["input_spec"] != info["input_spec"]
        or descriptor["input_spec"] != info["input_spec"]
        or sum(p.numel() for p in loaded.parameters()) != descriptor["parameters"]
    ):
        raise BoundaryError("p1_worker", "model_package_binding_mismatch")
    return descriptor


def verify_p1_result(
    store: ArtifactStore, result_id: str, workload: P1Workload
) -> tuple[Manifest, Manifest, P1Checkpoint]:
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
        raise BoundaryError("p1_worker", "result_run_binding_mismatch")
    last = verify_p1_checkpoint(store, result.parent("last_checkpoint"), workload)
    state = last.state
    if state["phase"] != "completed" or state["best_model"] is None:
        raise BoundaryError("p1_worker", "completed_checkpoint_required")
    model = store.get_manifest(result.parent("model"))
    metadata = model.parameters.value()
    if (
        model.kind != "model"
        or model.producer != workload.run.producer
        or metadata.get("schema") != MODEL_SCHEMA
        or {p.role for p in model.parents} != {"run", "training_input", "checkpoint"}
        or model.parent("run") != workload.run.artifact_id
        or model.parent("training_input") != workload.training.artifact_id
        or model.parent("checkpoint") != last.manifest.artifact_id
        or metadata.get("input_spec") != workload.training.parameters.value()["input_spec"]
        or metadata.get("selected_step") != state["best_step"]
        or info.get("optimizer_updates") != state["step"]
    ):
        raise BoundaryError("p1_worker", "model_result_binding_mismatch")
    best = verify_p1_checkpoint(store, result.parent("best_checkpoint"), workload)
    if (
        best.state["step"] != state["best_step"]
        or best.state["model"].keys() != state["best_model"].keys()
        or any(
            not torch.equal(value, state["best_model"][key])
            for key, value in best.state["model"].items()
        )
    ):
        raise BoundaryError("p1_worker", "dev_best_checkpoint_mismatch")
    with tempfile.TemporaryDirectory(prefix="p1-model-verify-") as temporary:
        destination = Path(temporary) / "package"
        materialize_p1_model(store, model.artifact_id, destination)
        loaded, package = load_model(destination / "model.tensor-tree", metadata["package_sha256"])
        if (
            package["checkpoint_sha256"] != digest(last.raw)
            or package["training_identity"] != state["identity"]
            or loaded.state_dict().keys() != state["best_model"].keys()
            or any(
                not torch.equal(value, state["best_model"][key])
                for key, value in loaded.state_dict().items()
            )
        ):
            raise BoundaryError("p1_worker", "exported_dev_best_weights_mismatch")
    evaluation = store.get_manifest(result.parent("evaluation"))
    if (
        evaluation.kind != "offline_evaluation"
        or evaluation.producer != workload.run.producer
        or evaluation.parent("model") != model.artifact_id
        or evaluation.parameters.value().get("schema") != "stpd/p1-N-evaluation-1"
        or evaluation.parameters.value().get("test") != state["test_result"]
        or evaluation.parameters.value().get("best_dev_N_loss") != state["best_loss"]
    ):
        raise BoundaryError("p1_worker", "evaluation_result_binding_mismatch")
    verified = {last.manifest.artifact_id, best.manifest.artifact_id}
    for fraction, checkpoint_id in info["checkpoint_ids"].items():
        if result.parent("checkpoint_" + fraction) != checkpoint_id:
            raise BoundaryError("p1_worker", "result_schedule_binding_mismatch")
        if checkpoint_id not in verified:
            verify_p1_checkpoint(store, checkpoint_id, workload)
            verified.add(checkpoint_id)
    return result, model, last


def execute_p1_run(
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
    started = time.perf_counter()
    workload = verify_p1_run(store, run_id, runtime)
    verified_seconds = time.perf_counter() - started
    operation_id = workload.run.parameters.value()["operation_id"]
    if mode not in {"start", "resume", "reconcile"} or (mode == "resume") != (resume is not None):
        raise BoundaryError("p1_worker", "explicit_execution_mode_required")
    if not isinstance(attempt_id, str) or not attempt_id:
        raise BoundaryError("p1_worker", "attempt_identity_required")
    if stop_after is not None and (type(stop_after) is not int or stop_after < 1):
        raise BoundaryError("p1_worker", "invalid_update_pause_budget")

    def guard() -> None:
        if authority is not None:
            authority.assert_current(run_id, operation_id, attempt_id)

    guard()
    existing = reporter.completed(run_id)
    if existing is not None:
        result, model, checkpoint = verify_p1_result(store, existing.artifact_id, workload)
        return StructuredExecutionResult(
            "completed",
            run_id,
            attempt_id,
            checkpoint.manifest.artifact_id,
            result.artifact_id,
            model.artifact_id,
            result.parameters.value()["event_step"],
            checkpoint.state["step"],
            time.perf_counter() - started,
        )
    if mode == "reconcile":
        raise BoundaryError("p1_worker", "no_selected_result_reconcile_only")
    preparation_started = time.perf_counter()
    populations = populations_from_reader(workload.reader)
    population_seconds = time.perf_counter() - preparation_started
    construction_started = time.perf_counter()
    trainer = MStdTrainer(
        populations,
        workload.vocabulary,
        workload.snapshot.artifact_id,
        workload.config,
    )
    construction_seconds = time.perf_counter() - construction_started
    checkpoints: dict[str, str] = {}
    best_checkpoint: str | None = None
    checkpoint_id: str | None = None
    if resume is not None:
        if authority is not None:
            authority.authorize_resume(run_id, attempt_id, resume)
        saved = verify_p1_checkpoint(store, resume, workload)
        trainer.restore(saved.raw)
        metadata = saved.manifest.parameters.value()
        checkpoints = dict(metadata["prior_checkpoints"])
        checkpoints.update({str(f): resume for f in metadata["fractions"]})
        best_checkpoint = resume if metadata["became_best"] else metadata["prior_best_checkpoint"]
        checkpoint_id = resume
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

    last_saved_at = time.monotonic()

    def save(reason: str, fractions: list[int] | None = None) -> str:
        nonlocal checkpoint_id, best_checkpoint, last_saved_at
        guard()
        checkpoint_started = time.perf_counter()
        raw = trainer.checkpoint()
        encoded_seconds = time.perf_counter() - checkpoint_started
        payload = store.put_payload(
            "checkpoint", io.BytesIO(raw), "application/vnd.stpd.tensor-tree"
        )
        parents = [Parent("run", run_id), Parent("training_input", workload.training.artifact_id)]
        parents.extend(Parent("checkpoint_" + k, v) for k, v in sorted(checkpoints.items()))
        if best_checkpoint is not None:
            parents.append(Parent("prior_best", best_checkpoint))
        became_best = trainer.best_step is not None and trainer.best_step == trainer.step
        manifest = Manifest(
            "checkpoint",
            runtime,
            tuple(parents),
            (payload,),
            FrozenObject.of(
                {
                    "schema": CHECKPOINT_SCHEMA,
                    "attempt": attempt_id,
                    "optimizer_updates": trainer.step,
                    "phase": trainer.phase,
                    "reason": reason,
                    "fractions": fractions or [],
                    "prior_checkpoints": dict(checkpoints),
                    "prior_best_checkpoint": best_checkpoint,
                    "became_best": became_best,
                }
            ),
        )
        store.publish(manifest)
        checkpoint_id = manifest.artifact_id
        checkpoints.update({str(f): checkpoint_id for f in fractions or []})
        if became_best:
            best_checkpoint = checkpoint_id
        event(
            "checkpoint",
            checkpoint_id=checkpoint_id,
            optimizer_updates=trainer.step,
            reason=reason,
            checkpoint_fractions=fractions or [],
            timing={
                "encode_seconds": encoded_seconds,
                "publish_seconds": time.perf_counter() - checkpoint_started - encoded_seconds,
                "checkpoint_seconds": time.perf_counter() - checkpoint_started,
                "checkpoint_bytes": len(raw),
            },
        )
        # Only a completed immutable publication and its original event reset
        # the interval; elapsed time never manufactures a successful save.
        last_saved_at = time.monotonic()
        return checkpoint_id

    initial_step = trainer.step
    event(
        "started",
        mode=mode,
        optimizer_updates=trainer.step,
        planned_steps=trainer.planned_steps,
        device=workload.config.device,
        preparation={
            "verify_run_seconds": verified_seconds,
            "population_seconds": population_seconds,
            "trainer_construction_seconds": construction_seconds,
            "partition_N": {key: len(value.frames) for key, value in populations.items()},
        },
    )
    while trainer.phase == "training":
        guard()
        action = "continue" if control is None else control.requested_action()
        if action not in {"continue", "pause", "cancel"}:
            raise BoundaryError("p1_worker", "invalid_control_action")
        if action != "continue" or (
            stop_after is not None and trainer.step - initial_step >= stop_after
        ):
            checkpoint_id = save("cancelled" if action == "cancel" else "paused")
            event(
                "cancelled" if action == "cancel" else "paused",
                checkpoint_id=checkpoint_id,
                optimizer_updates=trainer.step,
            )
            return StructuredExecutionResult(
                "cancelled" if action == "cancel" else "paused",
                run_id,
                attempt_id,
                checkpoint_id,
                None,
                None,
                event_step,
                trainer.step,
                time.perf_counter() - started,
            )
        progress = trainer.advance()
        event("progress", optimizer_updates=trainer.step, **progress)
        if progress["checkpoint_fractions"] or progress["best_improved"]:
            save("scheduled_or_dev_best", progress["checkpoint_fractions"])
        elif time.monotonic() - last_saved_at >= PERIODIC_CHECKPOINT_SECONDS:
            save("periodic")
    guard()
    action = "continue" if control is None else control.requested_action()
    if action != "continue":
        if action not in {"pause", "cancel"}:
            raise BoundaryError("p1_worker", "invalid_control_action")
        checkpoint_id = save("cancelled" if action == "cancel" else "paused_before_test")
        event(
            "cancelled" if action == "cancel" else "paused",
            checkpoint_id=checkpoint_id,
            optimizer_updates=trainer.step,
        )
        return StructuredExecutionResult(
            "cancelled" if action == "cancel" else "paused",
            run_id,
            attempt_id,
            checkpoint_id,
            None,
            None,
            event_step,
            trainer.step,
            time.perf_counter() - started,
        )
    # Training is complete before test is read. Its labels never affect selection.
    finish_started = time.perf_counter()
    trainer.finish()
    test_seconds = time.perf_counter() - finish_started
    checkpoint_id = save("last")
    if best_checkpoint is None:
        raise BoundaryError("p1_worker", "dev_selected_checkpoint_required")
    last_manifest = store.get_manifest(checkpoint_id)
    export_started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="p1-model-export-") as temporary:
        checkpoint_path = Path(temporary) / "last.tensor-tree"
        with checkpoint_path.open("wb") as handle:
            for chunk in store.read_payload(last_manifest.payload("checkpoint")):
                handle.write(chunk)
        package = Path(temporary) / "model"
        descriptor = export_model(
            trainer.selected_model(), checkpoint_path, package, trainer.identity
        )
        written = []
        for role, name, media in (
            ("weights", "model.tensor-tree", "application/vnd.stpd.tensor-tree"),
            ("package_manifest", "model.json", "application/json"),
        ):
            with (package / name).open("rb") as source_file:
                written.append(store.put_payload(role, source_file, media))
        payloads = tuple(written)
    guard()
    model = Manifest(
        "model",
        runtime,
        (
            Parent("run", run_id),
            Parent("training_input", workload.training.artifact_id),
            Parent("checkpoint", checkpoint_id),
        ),
        payloads,
        FrozenObject.of(
            {
                "schema": MODEL_SCHEMA,
                "package_sha256": descriptor["package_sha256"],
                "input_spec": descriptor["input_spec"],
                "selected_step": trainer.best_step,
                "training_device": workload.config.device,
                "selected_state": "best_model",
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
                "schema": "stpd/p1-N-evaluation-1",
                "best_dev_N_loss": trainer.best_loss,
                "test": trainer.test_result,
                "model_selection": "dev_only",
                "scientific_verdict": "not_claimed",
            }
        ),
    )
    store.publish(evaluation)
    event(
        "completed",
        optimizer_updates=trainer.step,
        model_id=model.artifact_id,
        checkpoint_id=checkpoint_id,
        stop_reason=trainer.stop_reason,
        timing={
            "test_seconds": test_seconds,
            "export_publish_seconds": time.perf_counter() - export_started,
            "worker_seconds": time.perf_counter() - started,
        },
    )
    result = Manifest(
        "run_result",
        runtime,
        (
            Parent("run", run_id),
            Parent("training_input", workload.training.artifact_id),
            Parent("model", model.artifact_id),
            Parent("last_checkpoint", checkpoint_id),
            Parent("best_checkpoint", best_checkpoint),
            Parent("evaluation", evaluation.artifact_id),
            *(Parent("checkpoint_" + k, v) for k, v in sorted(checkpoints.items())),
        ),
        parameters=FrozenObject.of(
            {
                "schema": "stpd/run-result-v1",
                "family": MODEL_SCHEMA,
                "state": "completed",
                "optimizer_updates": trainer.step,
                "event_step": event_step,
                "checkpoint_ids": dict(checkpoints),
                "selected_step": trainer.best_step,
                "selected_epoch": trainer.best_epoch,
                "stop_reason": trainer.stop_reason,
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
