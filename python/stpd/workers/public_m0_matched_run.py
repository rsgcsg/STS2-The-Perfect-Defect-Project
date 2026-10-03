"""Typed A01 run over the A02 public input and matched window schedule.

All manifests remain engineering-only. The reference is a verified M2 run,
not a promotion of either run to runtime or research qualification.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, fields
from typing import Any

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError
from spireagent.storage.store import ArtifactStore

from ..fullrun.public_m2_sequences import PublicM2Input
from ..models.token_core import ScratchShape
from .public_m0_matched_engine import (
    CHECKPOINT_SCHEMA as ENGINE_CHECKPOINT_SCHEMA,
)
from .public_m0_matched_engine import (
    EXPORT_SCHEMA as ENGINE_EXPORT_SCHEMA,
)
from .public_m0_matched_engine import (
    PublicM0MatchedConfig,
    PublicM0MatchedEngine,
    _shape,
)
from .public_m2_run import (
    _execute_run,
    _load_run,
    _prepare_run,
    _RunFlavor,
)
from .reporting import RunReporter
from .worker import WorkerResult

INPUT_SCHEMA = "stpd/public-m0-matched-worker-input-v1"
EXPERIMENT_SCHEMA = "stpd/public-m0-matched-experiment-v1"
RUN_SCHEMA = "stpd/public-m0-matched-run-v1"
CHECKPOINT_SCHEMA = "stpd/public-m0-matched-worker-checkpoint-v1"
MODEL_SCHEMA = "stpd/public-m0-matched-model-v1"
EVALUATION_SCHEMA = "stpd/public-m0-matched-dev-evaluation-v1"
STAGE_SCHEMA = "stpd/public-m0-matched-epoch-stage-v1"
MODEL_KIND = "public_m0_matched_a01"


def _config(value: object) -> PublicM0MatchedConfig:
    if not isinstance(value, dict) or set(value) != {f.name for f in fields(PublicM0MatchedConfig)}:
        raise BoundaryError("public_m0_matched_run", "config_format_mismatch")
    shape = value["shape_override"]
    if shape is not None:
        if not isinstance(shape, dict) or set(shape) != {f.name for f in fields(ScratchShape)}:
            raise BoundaryError("public_m0_matched_run", "shape_format_mismatch")
        shape = ScratchShape(**shape)
    return PublicM0MatchedConfig(**{**value, "shape_override": shape})


def _verify_match(
    store: ArtifactStore, matched_m2_run_id: str, producer: Producer,
    source: PublicM2Input, config: PublicM0MatchedConfig,
) -> None:
    config.validate()
    m2_run, _, m2_input, m2_config, m2_engine = _load_run(
        store, matched_m2_run_id, producer,
    )
    shape = config.shape_override or ScratchShape(
        config.vocab_size, 384, 2, 6, 1536, 0.1, config.max_state_tokens,
    )
    if (
        m2_run.artifact_id != matched_m2_run_id
        or m2_input.identity != source.identity
        or config.source_digest != source.identity
        or m2_config.source_digest != source.identity
        or config.state_tokenizer_sha256 != hashlib.sha256(source.state_tokenizer).hexdigest()
        or m2_config.state_tokenizer_sha256 != config.state_tokenizer_sha256
        or m2_config.shape != shape
        or m2_config.max_action_bytes != config.max_action_bytes
        or m2_config.max_actions_per_step != config.max_actions_per_step
        or m2_config.max_chain_steps != config.max_chain_steps
        or m2_config.max_total_steps != config.max_total_steps
        or m2_config.max_total_input_tokens != config.max_total_input_tokens
        or m2_config.seed != config.seed
        or m2_config.learning_rate != config.learning_rate
        or m2_config.weight_decay != config.weight_decay
        or m2_config.gradient_clip != config.gradient_clip
        or m2_config.epochs != config.epochs or config.epochs != 5
        or m2_config.window_steps != config.window_steps
        or m2_config.max_window_tokens != config.max_window_tokens
        or config.train_windows_per_epoch != sum(
            (len(chain.steps) + config.window_steps - 1) // config.window_steps
            for chain in source.chains if chain.split == "train"
        )
        or config.train_windows_per_epoch != sum(
            (len(chain.steps) + m2_config.window_steps - 1) // m2_config.window_steps
            for chain in m2_engine.train_chains
        )
    ):
        raise BoundaryError("public_m0_matched_run", "matched_m2_run_mismatch")


def _export_extra(engine: PublicM0MatchedEngine) -> dict[str, Any]:
    config = engine.config
    return {
        "core_shape": asdict(_shape(config)),
        "state_tokenizer_sha256": config.state_tokenizer_sha256,
        "vocab_size": config.vocab_size,
        "max_state_tokens": config.max_state_tokens,
        "max_action_bytes": config.max_action_bytes,
        "train_windows_per_epoch": config.train_windows_per_epoch,
        "shape_profile": ("standard_384" if config.shape_override is None
                          else "synthetic_override"),
    }


_M0_FLAVOR = _RunFlavor(
    INPUT_SCHEMA, EXPERIMENT_SCHEMA, RUN_SCHEMA, CHECKPOINT_SCHEMA, MODEL_SCHEMA,
    EVALUATION_SCHEMA, STAGE_SCHEMA, ENGINE_CHECKPOINT_SCHEMA, ENGINE_EXPORT_SCHEMA,
    "scratch_public_m0_matched_a01_five_epoch", MODEL_KIND, _config,
    PublicM0MatchedEngine, lambda engine: engine.source.identity,
    "source_digest", "source_digest", frozenset({
        "core_shape", "state_tokenizer_sha256", "vocab_size", "max_state_tokens",
        "max_action_bytes", "train_windows_per_epoch", "shape_profile",
    }),
    _export_extra, _verify_match,
)


def prepare_public_m0_matched_run(
    store: ArtifactStore, training_input: PublicM2Input, config: PublicM0MatchedConfig,
    producer: Producer, *, source_view_id: str, allocation_id: str,
    matched_m2_run_id: str, operation_id: str,
) -> Manifest:
    """Prepare formal A01 only against a real, fully verified A02 run manifest."""
    if not isinstance(config, PublicM0MatchedConfig) or config.shape_override is not None:
        raise BoundaryError("public_m0_matched_run", "standard_shape_required")
    return _prepare_run(
        store, training_input, config, producer, source_view_id=source_view_id,
        allocation_id=allocation_id, operation_id=operation_id,
        flavor=_M0_FLAVOR, reference_run_id=matched_m2_run_id,
    )


def execute_public_m0_matched_run(
    store: ArtifactStore, reporter: RunReporter, run_id: str, runtime: Producer,
    *, resume: str | None = None, stop_after_windows: int | None = None,
) -> WorkerResult:
    config = store.get_manifest(run_id).parameters.value().get("config")
    if not isinstance(config, dict) or config.get("shape_override", object()) is not None:
        raise BoundaryError("public_m0_matched_run", "standard_shape_required")
    return _execute_run(
        store, reporter, run_id, runtime, resume=resume,
        stop_after_windows=stop_after_windows, flavor=_M0_FLAVOR,
    )
