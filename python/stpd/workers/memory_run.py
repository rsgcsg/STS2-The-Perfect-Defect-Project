"""Experimental train-only M2 lifecycle over immutable artifacts and RunReporter.

The caller admits the source and fixes episode order, labels and tokenizer bytes.
This worker persists those facts; it does not infer their research eligibility.
"""

from __future__ import annotations

import hashlib
import io
import time
import uuid
from dataclasses import asdict, fields

from spireagent.artifact_contracts import Manifest, Parent, Payload, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.store import ArtifactStore

from ..models.dsimple_sequence_training import MemorySequenceEpisode, MemorySequenceStep
from .checkpoint_codec import decode_checkpoint, encode_checkpoint
from .memory_ranking import (
    CHECKPOINT_SCHEMA,
    MemoryConfig,
    MemoryRankingEngine,
    MemoryTrainingInput,
    load_memory_export,
)
from .reporting import RunReporter
from .worker import WorkerResult

INPUT_SCHEMA = "stpd/experimental-m2-training-input-v1"
RUN_SCHEMA = "stpd/experimental-m2-run-v1"
MODEL_SCHEMA = "stpd/experimental-m2-model-v1"
MAX_INPUT_BYTES = 256 * 1024 * 1024
MAX_TOKENIZER_BYTES = 16 * 1024 * 1024
MAX_CHECKPOINT_BYTES = 512 * 1024 * 1024


def _input_bytes(episodes: tuple[MemorySequenceEpisode, ...]) -> bytes:
    return encode_checkpoint({
        "schema": INPUT_SCHEMA,
        "episodes": tuple({
            "episode_id": episode.episode_id,
            "steps": tuple({
                "episode_id": step.episode_id, "position": step.position,
                "page": step.page, "action_keys": step.action_keys,
                "actions": step.actions, "label_key": step.label_key,
                "reset_before": step.reset_before,
                "previous_actual_action": step.previous_actual_action,
                "public_feedback": step.public_feedback,
            } for step in episode.steps),
        } for episode in episodes),
    })


def _read_input(raw: bytes, source_id: str, tokenizer_sha256: str) -> MemoryTrainingInput:
    value = decode_checkpoint(raw)
    if set(value) != {"schema", "episodes"} or value["schema"] != INPUT_SCHEMA \
            or not isinstance(value["episodes"], tuple):
        raise BoundaryError("memory_run", "input_format_mismatch")
    episodes = []
    fields = {"episode_id", "position", "page", "action_keys", "actions", "label_key",
              "reset_before", "previous_actual_action", "public_feedback"}
    for item in value["episodes"]:
        if not isinstance(item, dict) or set(item) != {"episode_id", "steps"} \
                or not isinstance(item["steps"], tuple):
            raise BoundaryError("memory_run", "input_episode_format")
        steps = []
        for step in item["steps"]:
            if not isinstance(step, dict) or set(step) != fields:
                raise BoundaryError("memory_run", "input_step_format")
            steps.append(MemorySequenceStep(**step))
        episodes.append(MemorySequenceEpisode(item["episode_id"], tuple(steps)))
    return MemoryTrainingInput(source_id, tokenizer_sha256, tuple(episodes))


def _payload_bytes(store: ArtifactStore, payload: Payload, maximum: int) -> bytes:
    if payload.size > maximum:
        raise BoundaryError("memory_run", "payload_size_limit")
    return b"".join(store.read_payload(payload))


def prepare_memory_run(
    store: ArtifactStore, source: MemoryTrainingInput, config: MemoryConfig,
    producer: Producer, tokenizer_bytes: bytes,
) -> Manifest:
    """Freeze one caller-admitted source and its exact tokenizer into a run."""
    if type(tokenizer_bytes) is not bytes or not 0 < len(tokenizer_bytes) <= MAX_TOKENIZER_BYTES:
        raise BoundaryError("memory_run", "tokenizer_size_or_type")
    if hashlib.sha256(tokenizer_bytes).hexdigest() != source.tokenizer_sha256:
        raise BoundaryError("memory_run", "tokenizer_digest_mismatch")
    # A parent is lineage only. Its kind or identifier never grants admission.
    store.get_manifest(source.source_id)
    engine = MemoryRankingEngine(source, config)
    raw = _input_bytes(engine._episodes)
    if len(raw) > MAX_INPUT_BYTES:
        raise BoundaryError("memory_run", "input_size_limit")
    episode_payload = store.put_payload("episodes", io.BytesIO(raw),
                                        "application/vnd.stpd.tensor-tree")
    tokenizer_payload = store.put_payload("tokenizer", io.BytesIO(tokenizer_bytes))
    training_input = Manifest(
        "training_input", producer, (Parent("source", source.source_id),),
        (episode_payload, tokenizer_payload),
        FrozenObject.of({"schema": INPUT_SCHEMA, "input_digest": engine.input_digest,
                         "tokenizer_sha256": source.tokenizer_sha256,
                         "episode_count": config.episode_count,
                         "qualification": "engineering_only"}),
    )
    store.publish(training_input)
    experiment = Manifest(
        "experiment", producer, (Parent("training_input", training_input.artifact_id),),
        parameters=FrozenObject.of({"schema": "stpd/experiment-v1",
                                    "purpose": "experimental_train_only",
                                    "config": asdict(config)}),
    )
    store.publish(experiment)
    run = Manifest(
        "run", producer, (Parent("training_input", training_input.artifact_id),
                          Parent("experiment", experiment.artifact_id)),
        parameters=FrozenObject.of({"schema": RUN_SCHEMA, "config": asdict(config),
                                    "input_digest": engine.input_digest,
                                    "runtime": engine.runtime,
                                    "partition": "train"}),
    )
    store.publish(run)
    return run


def _load_run(store: ArtifactStore, run_id: str, runtime: Producer,
              ) -> tuple[Manifest, Manifest, MemoryConfig, MemoryRankingEngine]:
    run = store.get_manifest(run_id)
    info = run.parameters.value()
    if (run.kind != "run" or run.producer != runtime or info.get("schema") != RUN_SCHEMA
            or info.get("partition") != "train"
            or sorted(p.role for p in run.parents) != ["experiment", "training_input"]):
        raise BoundaryError("memory_run", "run_identity_mismatch")
    config_info = info.get("config")
    if (not isinstance(config_info, dict)
            or set(config_info) != {field.name for field in fields(MemoryConfig)}):
        raise BoundaryError("memory_run", "config_format_mismatch")
    config = MemoryConfig(**config_info)
    training_input = store.get_manifest(run.parent("training_input"))
    input_info = training_input.parameters.value()
    if (training_input.kind != "training_input" or training_input.producer != runtime
            or input_info.get("schema") != INPUT_SCHEMA
            or input_info.get("episode_count") != config.episode_count
            or sorted(p.role for p in training_input.parents) != ["source"]
            or sorted(p.role for p in training_input.payloads) != ["episodes", "tokenizer"]):
        raise BoundaryError("memory_run", "input_manifest_mismatch")
    store.get_manifest(training_input.parent("source"))
    tokenizer = _payload_bytes(store, training_input.payload("tokenizer"), MAX_TOKENIZER_BYTES)
    if (not tokenizer or hashlib.sha256(tokenizer).hexdigest()
            != input_info.get("tokenizer_sha256")):
        raise BoundaryError("memory_run", "tokenizer_digest_mismatch")
    source = _read_input(
        _payload_bytes(store, training_input.payload("episodes"), MAX_INPUT_BYTES),
        training_input.parent("source"), input_info["tokenizer_sha256"],
    )
    engine = MemoryRankingEngine(source, config)
    if (engine.input_digest != input_info.get("input_digest")
            or engine.input_digest != info.get("input_digest")
            or engine.runtime != info.get("runtime")):
        raise BoundaryError("memory_run", "input_or_runtime_mismatch")
    experiment = store.get_manifest(run.parent("experiment"))
    if (experiment.kind != "experiment" or experiment.producer != runtime
            or sorted(p.role for p in experiment.parents) != ["training_input"]
            or experiment.parent("training_input") != training_input.artifact_id
            or experiment.parameters.value() != {"schema": "stpd/experiment-v1",
                                                 "purpose": "experimental_train_only",
                                                 "config": asdict(config)}):
        raise BoundaryError("memory_run", "experiment_mismatch")
    return run, training_input, config, engine


def _verify_completed(store: ArtifactStore, result: Manifest, run: Manifest,
                      training_input: Manifest, config: MemoryConfig,
                      engine: MemoryRankingEngine) -> None:
    if (result.kind != "run_result" or result.producer != run.producer
            or result.parameters.value().get("schema") != "stpd/run-result-v1"
            or result.parameters.value().get("state") != "completed"
            or result.parameters.value().get("partition") != "train"
            or result.parameters.value().get("episodes") != config.episode_count
            or sorted(p.role for p in result.parents)
            != ["checkpoint", "model", "run", "training_input"]
            or result.parent("run") != run.artifact_id
            or result.parent("training_input") != training_input.artifact_id):
        raise BoundaryError("memory_run", "completed_identity_mismatch")
    model = store.get_manifest(result.parent("model"))
    checkpoint = store.get_manifest(result.parent("checkpoint"))
    if (model.kind != "model" or model.producer != run.producer
            or model.parameters.value() != {"schema": MODEL_SCHEMA,
                                            "config": asdict(config),
                                            "episodes": config.episode_count,
                                            "partition": "train",
                                            "qualification": "engineering_only"}
            or sorted(p.role for p in model.parents) != ["checkpoint", "run", "training_input"]
            or model.parent("run") != run.artifact_id
            or model.parent("training_input") != training_input.artifact_id
            or model.parent("checkpoint") != checkpoint.artifact_id
            or sorted(p.role for p in model.payloads) != ["tokenizer", "weights"]
            or model.payload("tokenizer") != training_input.payload("tokenizer")):
        raise BoundaryError("memory_run", "completed_model_mismatch")
    _restore_checkpoint(store, checkpoint, run, training_input, engine)
    if engine.next_episode != config.episode_count \
            or result.parent("checkpoint") != checkpoint.artifact_id:
        raise BoundaryError("memory_run", "completed_checkpoint_mismatch")
    raw = _payload_bytes(store, model.payload("weights"), MAX_CHECKPOINT_BYTES)
    load_memory_export(raw, config, engine.tokenizer_sha256)
    if raw != engine.export():
        raise BoundaryError("memory_run", "completed_weights_mismatch")


def _restore_checkpoint(store: ArtifactStore, saved: Manifest, run: Manifest,
                        training_input: Manifest, engine: MemoryRankingEngine) -> None:
    info = saved.parameters.value()
    if (saved.kind != "checkpoint" or saved.producer != run.producer
            or info.get("schema") != CHECKPOINT_SCHEMA
            or info.get("input_digest") != engine.input_digest
            or sorted(p.role for p in saved.parents) != ["run", "training_input"]
            or saved.parent("run") != run.artifact_id
            or saved.parent("training_input") != training_input.artifact_id
            or [p.role for p in saved.payloads] != ["checkpoint"]):
        raise BoundaryError("memory_run", "resume_identity_mismatch")
    engine.restore(_payload_bytes(store, saved.payload("checkpoint"), MAX_CHECKPOINT_BYTES))
    if info.get("next_episode") != engine.next_episode:
        raise BoundaryError("memory_run", "resume_episode_mismatch")


def execute_memory_run(store: ArtifactStore, reporter: RunReporter, run_id: str,
                       runtime: Producer, *, resume: str | None = None,
                       stop_after: int | None = None) -> WorkerResult:
    """Advance whole episodes only; any retry requires a caller-selected checkpoint."""
    if stop_after is not None and (type(stop_after) is not int or stop_after < 1):
        raise BoundaryError("memory_run", "invalid_pause_budget")
    run, training_input, config, engine = _load_run(store, run_id, runtime)
    previous = reporter.completed(run_id)
    if previous is not None:
        _verify_completed(store, previous, run, training_input, config, engine)
        return WorkerResult("completed", run_id, result_id=previous.artifact_id)
    if resume is None and reporter.events(run_id):
        raise BoundaryError("memory_run", "existing_attempt_requires_explicit_resume")
    attempt, started = uuid.uuid4().hex, time.perf_counter()
    checkpoint_id: str | None = None

    def event(kind: str, **details: object) -> None:
        reporter.emit(Manifest(
            "run_event", runtime, (Parent("run", run_id),),
            parameters=FrozenObject.of({"schema": "stpd/run-event-v1", "attempt": attempt,
                                        "kind": kind, "episode": engine.next_episode,
                                        "details": details}),
        ))

    def checkpoint() -> str:
        raw = engine.checkpoint()
        payload = store.put_payload("checkpoint", io.BytesIO(raw),
                                    "application/vnd.stpd.tensor-tree")
        item = Manifest(
            "checkpoint", runtime,
            (Parent("run", run_id), Parent("training_input", training_input.artifact_id)),
            (payload,), FrozenObject.of({"schema": CHECKPOINT_SCHEMA,
                                        "next_episode": engine.next_episode,
                                        "input_digest": engine.input_digest}),
        )
        store.publish(item)
        event("checkpoint", checkpoint_id=item.artifact_id)
        return item.artifact_id

    try:
        event("loading", resume=resume)
        if resume is not None:
            _restore_checkpoint(store, store.get_manifest(resume), run, training_input, engine)
        initial_episode = engine.next_episode
        event("resumed" if resume else "started")
        while engine.next_episode < config.episode_count:
            step_started = time.perf_counter()
            loss = engine.advance()
            event("episode", loss=loss, seconds=time.perf_counter() - step_started)
            checkpoint_id = checkpoint()
            if (stop_after is not None and engine.next_episode - initial_episode >= stop_after
                    and engine.next_episode < config.episode_count):
                event("paused", checkpoint_id=checkpoint_id)
                return WorkerResult("paused", run_id, checkpoint_id=checkpoint_id)
        if checkpoint_id is None:
            checkpoint_id = checkpoint()
        weights = store.put_payload("weights", io.BytesIO(engine.export()),
                                    "application/vnd.stpd.tensor-tree")
        model = Manifest(
            "model", runtime,
            (Parent("run", run_id), Parent("checkpoint", checkpoint_id),
             Parent("training_input", training_input.artifact_id)),
            (weights, training_input.payload("tokenizer")),
            FrozenObject.of({"schema": MODEL_SCHEMA, "config": asdict(config),
                             "episodes": engine.next_episode, "partition": "train",
                             "qualification": "engineering_only"}),
        )
        store.publish(model)
        result = Manifest(
            "run_result", runtime,
            (Parent("run", run_id), Parent("model", model.artifact_id),
             Parent("training_input", training_input.artifact_id),
             Parent("checkpoint", checkpoint_id)),
            parameters=FrozenObject.of({"schema": "stpd/run-result-v1",
                                        "state": "completed", "partition": "train",
                                        "episodes": engine.next_episode,
                                        "attempt_seconds": time.perf_counter() - started}),
        )
        result_id = reporter.complete(result)
        selected = reporter.completed(run_id)
        if selected is None or selected.artifact_id != result_id:
            raise BoundaryError("memory_run", "completion_race")
        event("completed", result_id=result_id)
        return WorkerResult("completed", run_id, checkpoint_id, result_id)
    except Exception as error:
        event("failed", error_type=type(error).__name__, last_checkpoint=checkpoint_id)
        raise
