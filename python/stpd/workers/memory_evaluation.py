"""Retrospective, dev-only evaluation of an exported observed-input M2 model.

The caller admits dev use through the existing curation owner. This module verifies
immutable model/source lineage and scores ordered observations without training.
"""

from __future__ import annotations

import hashlib
import io
import math
from dataclasses import asdict, fields
from typing import Any

import torch
from tokenizers import Tokenizer

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, digest, json_bytes
from spireagent.storage.store import ArtifactStore
from stpd.fullrun.evaluation import candidate_metrics, summarize_rows
from stpd.fullrun.memory_sequence_bridge import (
    MemoryEpisodeProjectionConfig,
    project_memory_episodes,
)
from stpd.fullrun.memory_token_inputs import (
    MAX_TOKENIZER_BYTES,
    RENDERER_IDENTITY,
    project_memory_snapshot,
)
from stpd.fullrun.memory_training_prepare import fit_observed_memory_tokenizer
from stpd.fullrun.observed_input_sequence import ObservedInputView, load_observed_input_view
from stpd.workers.memory_ranking import CHECKPOINT_SCHEMA, MemoryConfig, load_memory_export
from stpd.workers.memory_run import (
    INPUT_SCHEMA_V2,
    MAX_CHECKPOINT_BYTES,
    MAX_INPUT_BYTES,
    MAX_SOURCE_MAP_BYTES,
    MODEL_SCHEMA,
    RUN_SCHEMA,
    SOURCE_MAP_SCHEMA,
    _input_bytes,
    _payload_bytes,
)
from stpd.workers.report_schemas import (
    MEMORY_EVALUATION_PROTOCOL as PROTOCOL,
)
from stpd.workers.report_schemas import (
    MEMORY_EVALUATION_SCHEMA as EVALUATION_SCHEMA,
)

INPUT_SCHEMA = "stpd/experimental-m2-evaluation-input-v1"
MAX_METRICS_BYTES = 64 * 1024 * 1024


def _ancestors(store: ArtifactStore, source_id: str) -> set[str]:
    pending = [source_id]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        if len(seen) >= 512:
            raise BoundaryError("memory_evaluation", "source_lineage_limit")
        manifest = store.get_manifest(current)
        if manifest.parameters.value().get("purpose") in {"gold", "test"}:
            raise BoundaryError("memory_evaluation", "sealed_source_forbidden")
        seen.add(current)
        pending.extend(parent.artifact_id for parent in manifest.parents)
    return seen


def _stream_scope(view: ObservedInputView) -> set[tuple[str, str]]:
    """Session and stream identity survive model episode reset boundaries."""
    scope: set[tuple[str, str]] = set()
    for item in view.inputs:
        stream = item.stream_id
        scope.add(("stream", stream))
        if item.source_kind == "human_input_stream":
            # human:<session>:<timeline>:<run>; a new timeline is still one session.
            parts = stream.split(":")
            if len(parts) != 4 or not all(parts):
                raise BoundaryError("memory_evaluation", "invalid_human_stream_identity")
            scope.add(("session", parts[1]))
        elif item.source_kind == "agent_decision_inputs":
            # agent:<content>:<run>; content identifies the native archive.
            parts = stream.split(":")
            if len(parts) != 3 or not all(parts):
                raise BoundaryError("memory_evaluation", "invalid_agent_stream_identity")
            scope.add(("native_stream", parts[1]))
        else:
            raise BoundaryError("memory_evaluation", "unsupported_observed_stream")
    return scope


def _rendered_inputs(view: ObservedInputView) -> set[bytes]:
    rendered = set()
    for item in view.inputs:
        if (item.observation_mask and item.snapshot is not None
                and item.snapshot.get("status") == "interactive"):
            public = project_memory_snapshot(item.snapshot)
            rendered.add(json_bytes([public.state_text, public.action_texts]))
    return rendered


def _model_lineage(
    store: ArtifactStore, model_id: str,
) -> tuple[Manifest, Manifest, MemoryConfig, str, bytes]:
    model = store.get_manifest(model_id)
    info = model.parameters.value()
    if (model.kind != "model" or info.get("schema") != MODEL_SCHEMA
            or info.get("partition") != "train"
            or info.get("qualification") != "engineering_only"
            or sorted(p.role for p in model.parents) != ["checkpoint", "run", "training_input"]
            or sorted(p.role for p in model.payloads) != ["tokenizer", "weights"]):
        raise BoundaryError("memory_evaluation", "model_identity_mismatch")
    raw_config = info.get("config")
    if (not isinstance(raw_config, dict)
            or set(raw_config) != {f.name for f in fields(MemoryConfig)}):
        raise BoundaryError("memory_evaluation", "model_config_mismatch")
    config = MemoryConfig(**raw_config)
    run = store.get_manifest(model.parent("run"))
    training = store.get_manifest(model.parent("training_input"))
    checkpoint = store.get_manifest(model.parent("checkpoint"))
    if (info.get("episodes") != config.episode_count
            or run.kind != "run" or run.parameters.value().get("schema") != RUN_SCHEMA
            or run.parameters.value().get("config") != asdict(config)
            or run.parameters.value().get("partition") != "train"
            or run.parent("training_input") != training.artifact_id
            or training.kind != "training_input"
            or training.parameters.value().get("schema") != INPUT_SCHEMA_V2
            or training.parameters.value().get("episode_count") != config.episode_count
            or model.payload("tokenizer") != training.payload("tokenizer")
            or checkpoint.kind != "checkpoint"
            or checkpoint.parameters.value().get("schema") != CHECKPOINT_SCHEMA
            or checkpoint.parameters.value().get("next_episode") != config.episode_count
            or checkpoint.parent("run") != run.artifact_id
            or checkpoint.parent("training_input") != training.artifact_id
            or model.producer != run.producer or model.producer != training.producer):
        raise BoundaryError("memory_evaluation", "model_lineage_mismatch")
    tokenizer_bytes = _payload_bytes(store, model.payload("tokenizer"), MAX_TOKENIZER_BYTES)
    tokenizer_sha = hashlib.sha256(tokenizer_bytes).hexdigest()
    if tokenizer_sha != training.parameters.value().get("tokenizer_sha256"):
        raise BoundaryError("memory_evaluation", "tokenizer_digest_mismatch")
    return model, training, config, training.parent("source"), tokenizer_bytes


def _verify_train_projection(
    store: ArtifactStore, training: Manifest, source: ObservedInputView,
    tokenizer: Tokenizer, tokenizer_bytes: bytes, network: Any, config: MemoryConfig,
) -> None:
    """Bind the model's V2 train bytes to its verified observed source."""
    parameters = training.parameters.value()
    projection_info = parameters.get("projection_config")
    if (not isinstance(projection_info, dict)
            or set(projection_info) != {"schema", "max_settling_events"}
            or parameters.get("source_map_schema") != SOURCE_MAP_SCHEMA):
        raise BoundaryError("memory_evaluation", "train_projection_identity_mismatch")
    try:
        projection_config = MemoryEpisodeProjectionConfig(**projection_info)
    except (TypeError, ValueError) as error:
        raise BoundaryError("memory_evaluation", "train_projection_identity_mismatch") from error
    if source.stream_scope != "partial_human_input_stream":
        raise BoundaryError("memory_evaluation", "human_train_source_required")
    fitted_bytes, fitted_episodes = fit_observed_memory_tokenizer(
        source, max_settling_events=projection_config.max_settling_events,
    )
    if fitted_bytes != tokenizer_bytes or fitted_episodes != config.episode_count:
        raise BoundaryError("memory_evaluation", "train_tokenizer_fit_mismatch")
    projected = project_memory_episodes(
        source, tokenizer, network,
        max_observations=config.max_episode_observations,
        max_input_tokens=config.max_episode_input_tokens,
        max_settling_events=projection_config.max_settling_events,
    )
    mapping = decode_json(_payload_bytes(store, training.payload("source_map"),
                                         MAX_SOURCE_MAP_BYTES))
    if (not isinstance(mapping, dict)
            or mapping.get("schema") != SOURCE_MAP_SCHEMA
            or mapping.get("events") != [asdict(item) for item in projected.event_mapping]
            or parameters.get("source_event_count") != len(projected.event_mapping)
            or len(projected.episodes) != config.episode_count
            or _payload_bytes(store, training.payload("episodes"), MAX_INPUT_BYTES)
            != _input_bytes(projected.episodes)):
        raise BoundaryError("memory_evaluation", "train_projection_identity_mismatch")


def evaluate_memory(
    store: ArtifactStore, model_id: str, dev_source_id: str, producer: Producer, *,
    max_settling_events: int = 0, seed: int = 0, operation_id: str | None = None,
    semantic_overlap: bool | None = None,
) -> Manifest:
    """Publish exact dev projection and metrics; source admission precedes this call."""
    if operation_id is not None:
        digest(operation_id, "memory_evaluation.operation_id", length=32)
    if type(semantic_overlap) not in {type(None), bool}:
        raise BoundaryError("memory_evaluation", "semantic_overlap_diagnostic_invalid")
    model_manifest, training, config, train_source_id, tokenizer_bytes = _model_lineage(
        store, model_id)
    train_ancestors = _ancestors(store, train_source_id)
    dev_ancestors = _ancestors(store, dev_source_id)
    if train_ancestors & dev_ancestors:
        raise BoundaryError("memory_evaluation", "train_dev_source_or_evidence_overlap")
    train_view = load_observed_input_view(store, train_source_id)
    dev_view = load_observed_input_view(store, dev_source_id)
    if not train_view.inputs or not dev_view.inputs:
        raise BoundaryError("memory_evaluation", "empty_observed_source")
    if _stream_scope(train_view) & _stream_scope(dev_view):
        raise BoundaryError("memory_evaluation", "train_dev_session_or_stream_overlap")
    rendered_overlap_count = len(_rendered_inputs(train_view) & _rendered_inputs(dev_view))
    try:
        tokenizer = Tokenizer.from_str(tokenizer_bytes.decode("utf-8"))
    except Exception as error:
        raise BoundaryError("memory_evaluation", "tokenizer_parse_failed") from error
    if (tokenizer.padding is not None or tokenizer.truncation is not None
            or tokenizer.get_vocab_size() != config.vocab_size):
        raise BoundaryError("memory_evaluation", "tokenizer_config_mismatch")
    weights = _payload_bytes(store, model_manifest.payload("weights"), MAX_CHECKPOINT_BYTES)
    network = load_memory_export(weights, config, hashlib.sha256(tokenizer_bytes).hexdigest())
    _verify_train_projection(
        store, training, train_view, tokenizer, tokenizer_bytes, network, config)
    projection = project_memory_episodes(
        dev_view, tokenizer, network,
        max_observations=config.max_episode_observations,
        max_input_tokens=config.max_episode_input_tokens,
        max_settling_events=max_settling_events,
    )
    if projection.diagnostics:
        raise BoundaryError("memory_evaluation", projection.diagnostics[0].reason)
    if not projection.episodes:
        raise BoundaryError("memory_evaluation", "no_dev_episodes")
    rows: list[dict[str, Any]] = []
    with torch.inference_mode():
        for episode, source in zip(projection.episodes, projection.sources, strict=True):
            memory = network.initial_memory()
            for step in episode.steps:
                memory = network.advance(
                    step.page, memory, reset_before=step.reset_before,
                    previous_actual_action=step.previous_actual_action,
                    feedback=step.public_feedback,
                )
                if step.label_key is None:
                    continue
                scores_tensor = network.score(memory, step.actions)
                scores = scores_tensor.detach().cpu().tolist()
                if (not isinstance(scores, list) or len(scores) != len(step.action_keys)
                        or len(set(step.action_keys)) != len(step.action_keys)
                        or any(type(value) is not float or not math.isfinite(value)
                               for value in scores)):
                    raise BoundaryError("memory_evaluation", "candidate_alignment_or_nonfinite")
                label = step.action_keys.index(step.label_key)
                rows.append({
                    "transition_id": source.step_event_ids[step.position],
                    "run_id": source.stream_id, "surface": "text_menu",
                    "family": "observed_input", "split": "dev",
                    "candidate_count": len(step.action_keys),
                    **candidate_metrics(scores, (label,)),
                })
    if not rows:
        raise BoundaryError("memory_evaluation", "no_labeled_dev_decisions")
    summary = summarize_rows(rows, seed=seed, native_run_independence=False)
    event_mapping = [asdict(item) for item in projection.event_mapping]
    input_raw = json_bytes({
        "schema": INPUT_SCHEMA, "model_id": model_id, "source_id": dev_source_id,
        "operation_id": operation_id,
        "protocol": PROTOCOL, "semantic_overlap": semantic_overlap,
        "tokenizer_sha256": hashlib.sha256(tokenizer_bytes).hexdigest(),
        "projection": {"renderer": RENDERER_IDENTITY,
                       "max_settling_events": max_settling_events},
        "train_dev_rendered_overlap_count": rendered_overlap_count,
        "events": event_mapping, "episodes": [
            {"episode_id": episode.episode_id, "stream_id": source.stream_id,
             "step_event_ids": source.step_event_ids,
             "candidate_keys": [step.action_keys for step in episode.steps],
             "label_keys": [step.label_key for step in episode.steps]}
            for episode, source in zip(projection.episodes, projection.sources, strict=True)
        ],
    })
    if len(input_raw) > MAX_METRICS_BYTES:
        raise BoundaryError("memory_evaluation", "input_size_limit")
    evaluation_input = Manifest(
        "analysis", producer,
        (Parent("model", model_id), Parent("source", dev_source_id)),
        (store.put_payload("projection", io.BytesIO(input_raw), "application/json"),),
        FrozenObject.of({"schema": INPUT_SCHEMA, "partition": "dev",
                         "operation_id": operation_id,
                         "protocol": PROTOCOL, "semantic_overlap": semantic_overlap,
                         "episode_count": len(projection.episodes),
                         "labeled_decisions": len(rows),
                         "train_dev_rendered_overlap_count": rendered_overlap_count,
                         "qualification": "engineering_only"}),
    )
    store.publish(evaluation_input)
    metrics_raw = json_bytes({"rows": rows, "summary": summary})
    if len(metrics_raw) > MAX_METRICS_BYTES:
        raise BoundaryError("memory_evaluation", "metrics_size_limit")
    evaluation = Manifest(
        "offline_evaluation", producer,
        (Parent("evaluation_input", evaluation_input.artifact_id),
         Parent("model", model_id), Parent("source", dev_source_id)),
        (store.put_payload("metrics", io.BytesIO(metrics_raw), "application/json"),),
        FrozenObject.of({"schema": EVALUATION_SCHEMA, "partition": "dev",
                         "operation_id": operation_id,
                         "protocol": PROTOCOL, "semantic_overlap": semantic_overlap,
                         "strict_deduplicated_benchmark": False,
                         "rows": len(rows), "qualification": "engineering_only",
                         "train_dev_rendered_overlap_count": rendered_overlap_count,
                         "model_selection_exposure": "unknown",
                         "scientific_verdict": "not_claimed",
                         "native_run_independence": False}),
    )
    store.publish(evaluation)
    return evaluation
