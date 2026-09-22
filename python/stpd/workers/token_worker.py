"""Token execution capability using the existing store, reporter and evaluation owners."""
from __future__ import annotations

import io
import time
import uuid
from dataclasses import asdict
from pathlib import Path

import torch

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes, text
from spireagent.storage.store import ArtifactStore

from ..fullrun.evaluation import action_only_prior, evaluate_samples
from ..fullrun.token_inputs import FORMAT, LoadedTokenInputs, load_token_inputs
from ..models.stage1a import recipe_for
from .reporting import RunReporter
from .token_ranking import CHECKPOINT_SCHEMA, TokenConfig, TokenRankingEngine
from .worker import WorkerResult

RUN_SCHEMA = "stpd/stage1a-run-v1"
MODEL_SCHEMA = "stpd/stage1a-model-v1"
EVALUATION_SCHEMA = "stpd/stage1a-ranking-evaluation-v1"


def prepare_token_run(store: ArtifactStore, inputs: LoadedTokenInputs, config: TokenConfig,
                      producer: Producer, *, replicate: str = "stage1a") -> Manifest:
    text(replicate, "run.replicate", maximum=128)
    if inputs.manifest.parameters.value()["backbone"] != recipe_for(config.recipe).backbone:
        raise BoundaryError("token_run", "input_backbone_mismatch")
    experiment = Manifest(
        "experiment", producer, (Parent("training_input", inputs.manifest.artifact_id),),
        parameters=FrozenObject.of({"schema": "stpd/experiment-v1", "purpose": "engineering",
                                    "config": asdict(config)}),
    )
    store.publish(experiment)
    run = Manifest(
        "run", producer, (Parent("training_input", inputs.manifest.artifact_id),
                          Parent("experiment", experiment.artifact_id)),
        parameters=FrozenObject.of({"schema": RUN_SCHEMA, "config": asdict(config),
                                    "replicate": replicate,
                                    "cpu_threads": torch.get_num_threads(),
                                    "torch_version": str(torch.__version__)}),
    )
    store.publish(run)
    return run


def _verify_completed(store: ArtifactStore, result: Manifest, run: Manifest) -> None:
    """Do not silently replace a damaged completion with a fresh optimization attempt."""
    if (result.kind != "run_result" or result.producer != run.producer
            or result.parameters.value().get("state") != "completed"
            or result.parameters.value().get("steps") != run.parameters.value()["config"]["steps"]
            or sorted(p.role for p in result.parents)
            != ["model", "offline_evaluation", "run", "training_input"]
            or result.parent("run") != run.artifact_id
            or result.parent("training_input") != run.parent("training_input")):
        raise BoundaryError("token_run", "completed_identity_mismatch")
    pending = [result.artifact_id]
    seen: set[str] = set()
    while pending:
        identity = pending.pop()
        if identity in seen:
            continue
        if len(seen) >= 512:
            raise BoundaryError("token_run", "lineage_limit")
        seen.add(identity)
        item = store.get_manifest(identity)
        for payload in item.payloads:
            for _ in store.read_payload(payload):
                pass
        pending.extend(p.artifact_id for p in item.parents)
    model = store.get_manifest(result.parent("model"))
    report = store.get_manifest(result.parent("offline_evaluation"))
    if (model.kind != "model" or model.producer != run.producer
            or model.parameters.value().get("schema") != MODEL_SCHEMA
            or model.parent("run") != run.artifact_id
            or model.parent("training_input") != run.parent("training_input")
            or model.parameters.value().get("steps") != run.parameters.value()["config"]["steps"]
            or model.parameters.value().get("config") != run.parameters.value()["config"]
            or report.kind != "offline_evaluation" or report.producer != run.producer
            or report.parameters.value().get("schema") != EVALUATION_SCHEMA
            or report.parent("model") != model.artifact_id):
        raise BoundaryError("token_run", "completed_model_or_report_mismatch")


def execute_tokens(store: ArtifactStore, reporter: RunReporter, run_id: str, runtime: Producer,
                   *, snapshot: Path | None = None, resume: str | None = None,
                   stop_after: int | None = None) -> WorkerResult:
    run = store.get_manifest(run_id)
    info = run.parameters.value()
    if (run.kind != "run" or run.producer != runtime or info.get("schema") != RUN_SCHEMA
            or info.get("torch_version") != str(torch.__version__)
            or info.get("cpu_threads") != torch.get_num_threads()
            or sorted(p.role for p in run.parents) != ["experiment", "training_input"]):
        raise BoundaryError("token_run", "source_or_contract_mismatch")
    config = TokenConfig.decode(info["config"])
    if stop_after is not None and (type(stop_after) is not int or stop_after < 1):
        raise BoundaryError("token_run", "invalid_pause_budget")
    inputs = load_token_inputs(store, run.parent("training_input"))
    experiment = store.get_manifest(run.parent("experiment"))
    if (experiment.kind != "experiment" or experiment.producer != runtime
            or experiment.parent("training_input") != inputs.manifest.artifact_id
            or experiment.parameters.value().get("config") != asdict(config)):
        raise BoundaryError("token_run", "experiment_mismatch")
    previous = reporter.completed(run_id)
    if previous is not None:
        _verify_completed(store, previous, run)
        return WorkerResult("completed", run_id, result_id=previous.artifact_id)
    if resume is None and reporter.events(run_id):
        raise BoundaryError("token_run", "existing_attempt_requires_explicit_resume")
    attempt, started = uuid.uuid4().hex, time.perf_counter()
    engine: TokenRankingEngine | None = None
    checkpoint_id = None

    def event(kind: str, **details: object) -> None:
        reporter.emit(Manifest(
            "run_event", runtime, (Parent("run", run_id),),
            parameters=FrozenObject.of({"schema": "stpd/run-event-v1", "attempt": attempt,
                                        "kind": kind, "step": engine.step if engine else 0,
                                        "details": details}),
        ))

    def checkpoint() -> str:
        assert engine is not None
        payload = store.put_payload("checkpoint", io.BytesIO(engine.checkpoint()),
                                    "application/vnd.stpd.tensor-tree")
        item = Manifest(
            "checkpoint", runtime,
            (Parent("run", run_id), Parent("training_input", inputs.manifest.artifact_id)),
            (payload,), FrozenObject.of({"schema": CHECKPOINT_SCHEMA, "step": engine.step,
                                        "data_identity": engine.data_identity}),
        )
        store.publish(item)
        event("checkpoint", checkpoint_id=item.artifact_id)
        return item.artifact_id

    try:
        event("loading", resume=resume)
        engine = TokenRankingEngine(inputs, config, snapshot=snapshot)
        if resume is not None:
            saved = store.get_manifest(resume)
            saved_info = saved.parameters.value()
            if (saved.kind != "checkpoint" or saved.producer != runtime
                    or saved_info.get("schema") != CHECKPOINT_SCHEMA
                    or saved.parent("run") != run_id
                    or saved.parent("training_input") != inputs.manifest.artifact_id
                    or saved_info.get("data_identity") != engine.data_identity
                    or sorted(p.role for p in saved.parents) != ["run", "training_input"]
                    or [p.role for p in saved.payloads] != ["checkpoint"]
                    or saved.payload("checkpoint").size > 512 * 1024**2):
                raise BoundaryError("token_run", "resume_identity_mismatch")
            engine.restore(b"".join(store.read_payload(saved.payload("checkpoint"))))
            if saved_info.get("step") != engine.step:
                raise BoundaryError("token_run", "resume_step_mismatch")
        initial_step = engine.step
        event("resumed" if resume else "started")
        while engine.step < config.steps:
            step_started = time.perf_counter()
            loss = engine.advance()
            seconds = time.perf_counter() - step_started
            event("step", loss=loss, seconds=seconds)
            print(json_bytes({"run_id": run_id, "step": engine.step, "total_steps": config.steps,
                              "loss": loss, "seconds": seconds}).decode(), flush=True)
            # Small 1a runs: every completed update is recoverable. Never write mid-update state.
            checkpoint_id = checkpoint()
            if (stop_after is not None and engine.step - initial_step >= stop_after
                    and engine.step < config.steps):
                event("paused", checkpoint_id=checkpoint_id)
                return WorkerResult("paused", run_id, checkpoint_id=checkpoint_id)
        if checkpoint_id is None:
            checkpoint_id = checkpoint()
        weights = store.put_payload("weights", io.BytesIO(engine.model_bytes()),
                                     "application/vnd.safetensors")
        view = store.get_manifest(inputs.manifest.parent("model_view"))
        model = Manifest(
            "model", runtime, (Parent("run", run_id), Parent("checkpoint", checkpoint_id),
                               Parent("training_input", inputs.manifest.artifact_id),
                               Parent("model_view", view.artifact_id)),
            (weights, inputs.manifest.payload("tokenizer")),
            FrozenObject.of({"schema": MODEL_SCHEMA, "config": asdict(config),
                             "graph": recipe_for(config.recipe).graph, "backbone": engine.backbone,
                             "vocab_size": inputs.manifest.parameters.value()["vocab_size"],
                             "serializer": view.parameters.value()["serializer"],
                             "input_format": FORMAT, "steps": engine.step,
                             "qualification": "engineering_only", "dtype": "float32"}),
        )
        store.publish(model)
        event("evaluating", model_id=model.artifact_id)
        rows, summary = evaluate_samples(inputs.samples, engine.scores, seed=config.seed)
        prior = action_only_prior(inputs.samples)
        baselines = {}
        for name, scorer in (
            ("uniform_legal", lambda i: (0.0,) * len(inputs.samples[i].action_keys)),
            ("action_only", lambda i: prior(inputs.samples[i])),
        ):
            _, baselines[name] = evaluate_samples(inputs.samples, scorer, seed=config.seed)
        metrics = store.put_payload("metrics", io.BytesIO(json_bytes({
            "rows": rows, "summary": summary, "baselines": baselines,
        })), "application/json")
        evaluation = Manifest(
            "offline_evaluation", runtime,
            (Parent("model", model.artifact_id), Parent("model_view", view.artifact_id)),
            (metrics,), FrozenObject.of({"schema": EVALUATION_SCHEMA, "partition": "dev",
                                        "qualification": "engineering_only"}),
        )
        store.publish(evaluation)
        result = Manifest(
            "run_result", runtime,
            (Parent("run", run_id), Parent("model", model.artifact_id),
             Parent("training_input", inputs.manifest.artifact_id),
             Parent("offline_evaluation", evaluation.artifact_id)),
            parameters=FrozenObject.of({"schema": "stpd/run-result-v1", "state": "completed",
                                        "steps": engine.step,
                                        "attempt_seconds": time.perf_counter() - started}),
        )
        result_id = reporter.complete(result)
        event("completed", result_id=result_id)
        return WorkerResult("completed", run_id, checkpoint_id, result_id)
    except Exception as error:
        event("failed", error_type=type(error).__name__, last_checkpoint=checkpoint_id)
        raise
