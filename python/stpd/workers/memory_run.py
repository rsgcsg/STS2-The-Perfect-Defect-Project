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
from typing import TYPE_CHECKING

from spireagent.artifact_contracts import Manifest, Parent, Payload, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, json_bytes
from spireagent.storage.store import ArtifactStore

from ..fullrun.memory_token_inputs import MAX_TOKENIZER_BYTES
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

if TYPE_CHECKING:
    from stpd.fullrun.memory_sequence_bridge import (
        MemoryEpisodeProjectionConfig,
        MemoryEventMapping,
    )
    from stpd.fullrun.observed_input_sequence import ObservedInputView
    from stpd.models.dsimple_memory import ExperimentalDSimpleM2

INPUT_SCHEMA = "stpd/experimental-m2-training-input-v1"
INPUT_SCHEMA_V2 = "stpd/experimental-m2-training-input-v2"
SOURCE_MAP_SCHEMA = "stpd/experimental-m2-source-event-map-v1"
RUN_SCHEMA = "stpd/experimental-m2-run-v1"
MODEL_SCHEMA = "stpd/experimental-m2-model-v1"
MAX_INPUT_BYTES = 256 * 1024 * 1024
MAX_CHECKPOINT_BYTES = 512 * 1024 * 1024
MAX_SOURCE_MAP_BYTES = 64 * 1024 * 1024


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
    producer: Producer, tokenizer_bytes: bytes, *,
    source_mapping: tuple[MemoryEventMapping, ...] | None = None,
    projection_config: MemoryEpisodeProjectionConfig | None = None,
) -> Manifest:
    """Freeze caller-admitted M2 tensors and exact tokenizer into a train-only run.

    The caller owns purpose/ledger admission. A verified source parent is lineage,
    not permission to train. ``source_mapping`` is the optional event-level audit
    emitted by the observed-input bridge; when present it is immutable run input.
    """
    if type(tokenizer_bytes) is not bytes or not 0 < len(tokenizer_bytes) <= MAX_TOKENIZER_BYTES:
        raise BoundaryError("memory_run", "tokenizer_size_or_type")
    if hashlib.sha256(tokenizer_bytes).hexdigest() != source.tokenizer_sha256:
        raise BoundaryError("memory_run", "tokenizer_digest_mismatch")
    # A parent is lineage only. Its kind or identifier never grants admission.
    store.get_manifest(source.source_id)
    engine = MemoryRankingEngine(source, config)
    raw = _input_bytes(engine.snapshot_input().episodes)
    if len(raw) > MAX_INPUT_BYTES:
        raise BoundaryError("memory_run", "input_size_limit")
    mapping_raw = None
    mapping_parameters: dict[str, object] = {}
    if source_mapping is not None:
        from stpd.fullrun.memory_sequence_bridge import MemoryEpisodeProjectionConfig

        if not isinstance(projection_config, MemoryEpisodeProjectionConfig):
            raise BoundaryError("memory_run", "projection_config_required")
        mapping_raw = _source_map_bytes(source_mapping)
        _validate_source_mapping(source.source_id, engine.snapshot_input().episodes,
                                 source_mapping)
        if len(mapping_raw) > MAX_SOURCE_MAP_BYTES:
            raise BoundaryError("memory_run", "source_map_size_limit")
        mapping_parameters = {
            "source_map_schema": SOURCE_MAP_SCHEMA,
            "source_event_count": len(source_mapping),
            "projection_config": asdict(projection_config),
        }
    elif projection_config is not None:
        raise BoundaryError("memory_run", "projection_config_without_source_map")
    episode_payload = store.put_payload("episodes", io.BytesIO(raw),
                                        "application/vnd.stpd.tensor-tree")
    tokenizer_payload = store.put_payload("tokenizer", io.BytesIO(tokenizer_bytes))
    mapping_payload = None
    schema = INPUT_SCHEMA
    if mapping_raw is not None:
        mapping_payload = store.put_payload("source_map", io.BytesIO(mapping_raw),
                                            "application/json")
        schema = INPUT_SCHEMA_V2
    payloads = (episode_payload, tokenizer_payload) + (
        (mapping_payload,) if mapping_payload is not None else ()
    )
    training_input = Manifest(
        "training_input", producer, (Parent("source", source.source_id),),
        payloads,
        FrozenObject.of({"schema": schema, "input_digest": engine.input_digest,
                         "tokenizer_sha256": source.tokenizer_sha256,
                         "episode_count": config.episode_count,
                         **mapping_parameters,
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


def prepare_observed_memory_run(
    store: ArtifactStore, source_id: str, config: MemoryConfig, producer: Producer,
    tokenizer_bytes: bytes, *, max_settling_events: int = 0,
) -> Manifest:
    """Project a typed observed source and freeze train-only M2 input plus its map.

    The caller must establish source purpose, claim, and training-use exposure in
    the owning ledger before calling this research API. This function verifies the
    source and projection but cannot grant or verify application-level permission.
    It creates artifacts only; execution remains the separate ``run-memory`` CLI.
    """
    if type(tokenizer_bytes) is not bytes or not 0 < len(tokenizer_bytes) <= MAX_TOKENIZER_BYTES:
        raise BoundaryError("memory_run", "tokenizer_size_or_type")
    try:
        from tokenizers import Tokenizer
        tokenizer = Tokenizer.from_str(tokenizer_bytes.decode("utf-8"))
    except Exception as error:
        raise BoundaryError("memory_run", "tokenizer_parse_failed") from error
    if tokenizer.truncation is not None or tokenizer.padding is not None:
        raise BoundaryError("memory_run", "tokenizer_padding_or_truncation_forbidden")
    if tokenizer.get_vocab_size() != config.vocab_size:
        raise BoundaryError("memory_run", "tokenizer_vocabulary_mismatch")

    from stpd.fullrun.memory_sequence_bridge import project_memory_episodes
    from stpd.fullrun.observed_input_sequence import load_observed_input_view

    view = load_observed_input_view(store, source_id)
    model = _projection_model(config)
    from stpd.fullrun.memory_sequence_bridge import MemoryEpisodeProjectionConfig

    projection_config = MemoryEpisodeProjectionConfig(
        "stpd/memory-episode-projection-config-v1", max_settling_events,
    )
    projection = project_memory_episodes(
        view, tokenizer, model,
        max_observations=config.max_episode_observations,
        max_input_tokens=config.max_episode_input_tokens,
        max_settling_events=projection_config.max_settling_events,
    )
    if len(projection.episodes) != config.episode_count:
        raise BoundaryError("memory_run", "projected_episode_count_mismatch")
    source = MemoryTrainingInput(
        source_id, hashlib.sha256(tokenizer_bytes).hexdigest(), projection.episodes,
    )
    return prepare_memory_run(
        store, source, config, producer, tokenizer_bytes,
        source_mapping=projection.event_mapping, projection_config=projection_config,
    )


def _source_map_bytes(mapping: tuple[MemoryEventMapping, ...]) -> bytes:
    from stpd.fullrun.memory_sequence_bridge import MemoryEventMapping

    if not isinstance(mapping, tuple) or not mapping or any(
        not isinstance(item, MemoryEventMapping) for item in mapping
    ):
        raise BoundaryError("memory_run", "source_map_format_mismatch")
    return json_bytes({"schema": SOURCE_MAP_SCHEMA,
                       "events": [item.__dict__ for item in mapping]})


def _validate_source_mapping(source_id: str, episodes: tuple[MemorySequenceEpisode, ...],
                             mapping: tuple[MemoryEventMapping, ...], *,
                             view: ObservedInputView | None = None) -> None:
    from stpd.fullrun.memory_sequence_bridge import MemoryEventMapping

    if not isinstance(mapping, tuple) or not mapping or any(
        not isinstance(item, MemoryEventMapping) for item in mapping
    ):
        raise BoundaryError("memory_run", "source_map_format_mismatch")
    ids: set[str] = set()
    step_bindings: dict[tuple[str, int], str] = {}
    for item in mapping:
        if (item.source_id != source_id or not item.stream_id or not item.event_id
                or type(item.source_sequence) is not int or item.source_sequence < 1
                or item.event_id in ids):
            raise BoundaryError("memory_run", "source_map_event_identity_mismatch")
        ids.add(item.event_id)
        if item.disposition == "step":
            if (not isinstance(item.episode_id, str) or type(item.position) is not int
                    or item.position < 0 or item.reason is not None):
                raise BoundaryError("memory_run", "source_map_step_binding_mismatch")
            key = (item.episode_id, item.position)
            if key in step_bindings:
                raise BoundaryError("memory_run", "source_map_step_binding_mismatch")
            step_bindings[key] = item.event_id
        elif item.disposition in {"settling", "excluded"}:
            if (item.position is not None or not isinstance(item.reason, str)
                    or not item.reason):
                raise BoundaryError("memory_run", "source_map_exclusion_mismatch")
            if item.disposition == "settling" and not isinstance(item.episode_id, str):
                raise BoundaryError("memory_run", "source_map_exclusion_mismatch")
            if item.disposition == "excluded" and item.episode_id is not None:
                raise BoundaryError("memory_run", "source_map_exclusion_mismatch")
        else:
            raise BoundaryError("memory_run", "source_map_disposition_unknown")
    expected = {
        (episode.episode_id, position)
        for episode in episodes for position, _ in enumerate(episode.steps)
    }
    if set(step_bindings) != expected:
        raise BoundaryError("memory_run", "source_map_episode_coverage_mismatch")
    if view is not None:
        inputs = getattr(view, "inputs", None)
        if not isinstance(inputs, tuple):
            raise BoundaryError("memory_run", "source_map_source_verification_failed")
        expected_events = {
            item.event_id: (item.stream_id, item.source_sequence, item.reset_reason)
            for item in inputs
        }
        actual_events = {
            item.event_id: (item.stream_id, item.source_sequence, item.reset_reason)
            for item in mapping
        }
        if actual_events != expected_events:
            raise BoundaryError("memory_run", "source_map_observed_event_coverage_mismatch")


def _projection_model(config: MemoryConfig) -> ExperimentalDSimpleM2:
    import torch

    from stpd.models.dsimple_memory import ExperimentalDSimpleM2
    from stpd.models.token_core import ScratchTokenCore

    with torch.random.fork_rng(devices=[]):
        return ExperimentalDSimpleM2(
            ScratchTokenCore(config.shape()), slots=config.slots,
            reset_each_step=config.reset_each_step, gated=config.gated,
        )


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
    input_schema = input_info.get("schema")
    expected_payload_roles = (["episodes", "tokenizer"] if input_schema == INPUT_SCHEMA
                              else ["episodes", "source_map", "tokenizer"]
                              if input_schema == INPUT_SCHEMA_V2 else None)
    if (training_input.kind != "training_input" or training_input.producer != runtime
            or expected_payload_roles is None
            or (input_schema == INPUT_SCHEMA_V2 and set(input_info) != {
                "schema", "input_digest", "tokenizer_sha256", "episode_count",
                "source_map_schema", "source_event_count", "projection_config",
                "qualification",
            })
            or input_info.get("episode_count") != config.episode_count
            or sorted(p.role for p in training_input.parents) != ["source"]
            or sorted(p.role for p in training_input.payloads) != expected_payload_roles):
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
    if input_schema == INPUT_SCHEMA_V2:
        if (input_info.get("source_map_schema") != SOURCE_MAP_SCHEMA
                or type(input_info.get("source_event_count")) is not int):
            raise BoundaryError("memory_run", "source_map_manifest_mismatch")
        raw_mapping = _payload_bytes(store, training_input.payload("source_map"),
                                     MAX_SOURCE_MAP_BYTES)
        value = decode_json(raw_mapping)
        if (not isinstance(value, dict) or set(value) != {"schema", "events"}
                or value.get("schema") != SOURCE_MAP_SCHEMA
                or not isinstance(value.get("events"), list)
                or len(value["events"]) != input_info["source_event_count"]
                or raw_mapping != json_bytes(value)):
            raise BoundaryError("memory_run", "source_map_payload_mismatch")
        from stpd.fullrun.memory_sequence_bridge import (
            MemoryEpisodeProjectionConfig,
            MemoryEventMapping,
        )

        projection_config_value = input_info.get("projection_config")
        if (not isinstance(projection_config_value, dict)
                or set(projection_config_value) != {"schema", "max_settling_events"}):
            raise BoundaryError("memory_run", "projection_config_mismatch")
        try:
            projection_config = MemoryEpisodeProjectionConfig(**projection_config_value)
        except (TypeError, ValueError) as error:
            raise BoundaryError("memory_run", "projection_config_mismatch") from error
        event_fields = {
            "source_id", "stream_id", "event_id", "source_sequence", "disposition",
            "episode_id", "position", "reason", "reset_reason",
        }
        if any(not isinstance(item, dict) or set(item) != event_fields
               for item in value["events"]):
            raise BoundaryError("memory_run", "source_map_payload_mismatch")
        try:
            mapping = tuple(MemoryEventMapping(**item) for item in value["events"])
        except (TypeError, ValueError) as error:
            raise BoundaryError("memory_run", "source_map_payload_mismatch") from error
        from stpd.fullrun.observed_input_sequence import load_observed_input_view
        view = load_observed_input_view(store, training_input.parent("source"))
        _validate_source_mapping(training_input.parent("source"),
                                 source.episodes, mapping, view=view)
        _verify_observed_projection(
            training_input.parent("source"), tokenizer, config, source, mapping, view,
            projection_config,
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


def _verify_observed_projection(source_id: str, tokenizer_bytes: bytes,
                                config: MemoryConfig, saved: MemoryTrainingInput,
                                mapping: tuple[MemoryEventMapping, ...], view: ObservedInputView,
                                projection_config: MemoryEpisodeProjectionConfig) -> None:
    """Rebuild typed bridge output so v2 mappings cannot be decorative metadata."""
    from tokenizers import Tokenizer

    from stpd.fullrun.memory_sequence_bridge import project_memory_episodes

    try:
        tokenizer = Tokenizer.from_str(tokenizer_bytes.decode("utf-8"))
    except Exception as error:
        raise BoundaryError("memory_run", "tokenizer_parse_failed") from error
    if (tokenizer.truncation is not None or tokenizer.padding is not None
            or tokenizer.get_vocab_size() != config.vocab_size):
        raise BoundaryError("memory_run", "tokenizer_config_mismatch")
    model = _projection_model(config)
    projection = project_memory_episodes(
        view, tokenizer, model,
        max_observations=config.max_episode_observations,
        max_input_tokens=config.max_episode_input_tokens,
        # Settling events appear only when explicitly admitted at prepare time.
        max_settling_events=projection_config.max_settling_events,
    )
    if (projection.event_mapping != mapping
            or len(projection.episodes) != config.episode_count):
        raise BoundaryError("memory_run", "source_map_projection_mismatch")
    projected_source = MemoryTrainingInput(
        source_id, saved.tokenizer_sha256, projection.episodes,
    )
    projected_engine = MemoryRankingEngine(projected_source, config)
    saved_engine = MemoryRankingEngine(saved, config)
    if projected_engine.input_digest != saved_engine.input_digest:
        raise BoundaryError("memory_run", "source_episode_projection_mismatch")


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
