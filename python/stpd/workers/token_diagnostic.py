"""Private, owner-admitted engineering diagnostics for paused public M0 checkpoints."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, digest, json_bytes
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.local_curation import LocalCurationOwner
from stpd.fullrun.evaluation import action_only_prior, evaluate_samples, summarize_rows
from stpd.fullrun.light_action_inputs import load_checkpoint_diagnostic_inputs
from stpd.workers.token_ranking import (
    LightActionM0Config,
    TokenRankingEngine,
    TokenTargetRuntime,
    recipe_for,
)
from stpd.workers.token_worker import preflight_token_run_contract

from ..canonical import semantic_hash

SCHEMA = "stpd/private-checkpoint-diagnostic-v1"
MAX_DIAGNOSTIC_BYTES = 64 * 1024**2


@dataclass(frozen=True)
class CheckpointDiagnosticResult:
    manifest: Manifest
    summary: dict[str, Any]


def _checkpoint_paused_currently(
    store: ManifestArtifactStore, run_id: str, checkpoint_id: str, step: int,
) -> None:
    reporter = ObjectStoreRunReporter(store, store.blobs)
    if reporter.completed(run_id) is not None:
        raise BoundaryError("token_diagnostic", "completed_run_not_diagnostic_target")
    events = reporter.events(run_id)
    paused_attempts = {
        value.get("attempt")
        for event in events
        if (value := event.parameters.value()).get("kind") == "paused"
        and value.get("details", {}).get("checkpoint_id") == checkpoint_id
        and value.get("step") == step
    }
    if not paused_attempts:
        raise BoundaryError("token_diagnostic", "paused_checkpoint_event_required")
    if any(
        value.get("kind") == "resumed"
        and type(value.get("step")) is int and value["step"] >= step
        for event in events
        if (value := event.parameters.value())
    ):
        raise BoundaryError("token_diagnostic", "checkpoint_no_longer_paused")
    latest_checkpoints = [
        value.get("details", {}).get("checkpoint_id")
        for event in events
        if (value := event.parameters.value()).get("kind") == "checkpoint"
        and type(value.get("step")) is int and value["step"] >= step
    ]
    if latest_checkpoints and latest_checkpoints[-1] != checkpoint_id:
        raise BoundaryError("token_diagnostic", "checkpoint_no_longer_latest")


def _input_fingerprint(sample: Any) -> str:
    """Hash full ordered state and candidate semantics without retaining text in reports."""
    return semantic_hash([sample.state_text, list(sample.action_texts), list(sample.action_keys)])


def _identity_overlap(
    training_samples: tuple[Any, ...], evaluation_samples: tuple[Any, ...],
) -> dict[str, int]:
    training = [sample for sample in training_samples if sample.split == "train"]
    evaluation = [sample for sample in evaluation_samples if sample.split == "dev"]
    return {
        "transition_ids": len(
            {sample.transition_id for sample in training}
            & {sample.transition_id for sample in evaluation}
        ),
        "run_ids": len(
            {sample.run_id for sample in training}
            & {sample.run_id for sample in evaluation}
        ),
        "ordered_rendered_inputs": len(
            {_input_fingerprint(sample) for sample in training}
            & {_input_fingerprint(sample) for sample in evaluation}
        ),
    }


def _mean_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    return summarize_rows(rows, seed=0, bootstrap=0, native_run_independence=False)


def diagnose_checkpoint(
    store: ManifestArtifactStore,
    owner: LocalCurationOwner,
    checkpoint_id: str,
    evaluation_input_id: str,
    training_operation_id: str,
    evaluation_producer: Producer,
) -> CheckpointDiagnosticResult:
    """Score one current paused scratch M0 checkpoint on one admitted dev input.

    The only durable side effects are the owner's existing evaluation-use rows and one
    local ``analysis`` artifact. No model, offline-evaluation, run-result, or run event is
    created, and the scoring engine has an inference-only CPU mode.
    """
    checkpoint_id = digest(checkpoint_id, "token_diagnostic.checkpoint_id")
    evaluation_input_id = digest(evaluation_input_id, "token_diagnostic.evaluation_input_id")
    checkpoint = store.get_manifest(checkpoint_id)
    if checkpoint.kind != "checkpoint":
        raise BoundaryError("token_diagnostic", "checkpoint_required")
    run_id = checkpoint.parent("run")
    run_manifest = store.get_manifest(run_id)
    training_input_id = checkpoint.parent("training_input")
    run_manifest, config, training_manifest, admitted_m0 = preflight_token_run_contract(
        store, run_id, run_manifest.producer, resume=checkpoint_id,
    )
    if (not admitted_m0 or not isinstance(config, LightActionM0Config)
            or config.public_profile != "public_compact"
            or recipe_for(config.recipe).backbone != "s"):
        raise BoundaryError("token_diagnostic", "public_compact_scratch_m0_required")
    checkpoint_info = checkpoint.parameters.value()
    step = checkpoint_info.get("step")
    if type(step) is not int or not 0 < step < config.steps:
        raise BoundaryError("token_diagnostic", "paused_intermediate_checkpoint_required")
    if training_manifest.artifact_id != training_input_id:
        raise BoundaryError("token_diagnostic", "checkpoint_training_input_mismatch")
    _checkpoint_paused_currently(store, run_id, checkpoint_id, step)

    evaluation_operation_id = hashlib.sha256(
        f"{SCHEMA}:{checkpoint_id}:{evaluation_input_id}".encode("ascii")
    ).hexdigest()[:32]
    # Admit the current training-purpose inputs before reading/projecting dev rows;
    # this preflight deliberately records no evaluation use.
    admission = owner.check_checkpoint_allocation_dev(
        store, checkpoint_id, evaluation_input_id, training_operation_id,
        evaluation_operation_id,
    )
    if (admission.get("checkpoint_id") != checkpoint_id
            or admission.get("run_id") != run_id
            or admission.get("training_input_id") != training_input_id
            or admission.get("evaluation_input_id") != evaluation_input_id
            or admission.get("physical_game_independence") != "unresolved"
            or admission.get("clean_held_out_claim") is not False):
        raise BoundaryError("token_diagnostic", "owner_reservation_identity_mismatch")

    bound = load_checkpoint_diagnostic_inputs(
        store, training_input_id, evaluation_input_id,
    )
    training_samples = bound.training.samples
    evaluation_samples = bound.evaluation_samples
    dev_samples = tuple(sample for sample in evaluation_samples if sample.split == "dev")
    if (not dev_samples or len({sample.transition_id for sample in dev_samples}) != len(dev_samples)
            or any(sample.split not in {"train", "dev"} for sample in evaluation_samples)):
        raise BoundaryError("token_diagnostic", "complete_unique_dev_required")
    exact_overlap = _identity_overlap(training_samples, evaluation_samples)
    ledger_overlap = admission.get("identity_overlap")
    if not isinstance(ledger_overlap, dict):
        raise BoundaryError("token_diagnostic", "owner_identity_overlap_missing")
    same_view = bound.evaluation_view.artifact_id == training_manifest.parent("model_view")
    if not same_view and (
        any(exact_overlap.values())
        or any(ledger_overlap.get(key, 0) for key in ("transition_ids", "run_ids", "run_groups"))
    ):
        raise BoundaryError("token_diagnostic", "fixed_dev_training_identity_overlap")

    from stpd.workers.checkpoint_codec import MAX_BYTES as MAX_CHECKPOINT_BYTES

    checkpoint_payload = checkpoint.payload("checkpoint")
    if checkpoint_payload.size > MAX_CHECKPOINT_BYTES:
        raise BoundaryError("token_diagnostic", "checkpoint_size_limit")
    checkpoint_bytes = b"".join(store.read_payload(checkpoint_payload))
    target_runtime = TokenTargetRuntime.from_run_info(run_manifest.parameters.value())
    engine = TokenRankingEngine(
        bound.training, config, score_device="cpu",
    )
    restored_step = engine.restore_for_diagnostic(checkpoint_bytes, target_runtime)
    if restored_step != step:
        raise BoundaryError("token_diagnostic", "checkpoint_step_mismatch")
    # Recheck current purpose/use claims only after codec, overlap, tensors, optimizer,
    # and outer/inner step validation have all succeeded.
    admission = owner.reserve_checkpoint_allocation_dev(
        store, checkpoint_id, evaluation_input_id, training_operation_id,
        evaluation_operation_id,
    )
    if (admission.get("checkpoint_id") != checkpoint_id
            or admission.get("run_id") != run_id
            or admission.get("training_input_id") != training_input_id
            or admission.get("evaluation_input_id") != evaluation_input_id
            or admission.get("physical_game_independence") != "unresolved"
            or admission.get("clean_held_out_claim") is not False):
        raise BoundaryError("token_diagnostic", "owner_reservation_identity_mismatch")

    def scores(index: int) -> tuple[float, ...]:
        return engine.scores_for_row(bound.evaluation_rows[index])

    rows, summary = evaluate_samples(
        evaluation_samples, scores, seed=config.seed, native_run_independence=False,
    )
    prior = action_only_prior(evaluation_samples)
    baselines: dict[str, dict[str, Any]] = {}
    for name, scorer in (
        ("uniform_legal", lambda index: (0.0,) * len(evaluation_samples[index].action_keys)),
        ("action_only", lambda index: prior(evaluation_samples[index])),
    ):
        _, baselines[name] = evaluate_samples(
            evaluation_samples, scorer, seed=config.seed, native_run_independence=False,
        )
    multiple_rows = [row for row in rows if row["candidate_count"] > 1]
    summary["candidate_count_gt1"] = _mean_metrics(multiple_rows)

    dev_commitment = semantic_hash([
        sample.to_dict() for sample in sorted(dev_samples, key=lambda sample: sample.transition_id)
    ])
    metric_value = {
        "schema": SCHEMA,
        "rows": rows,
        "summary": summary,
        "baselines": baselines,
        "candidate_coverage": {
            "dev_decisions": len(dev_samples),
            "candidate_count_gt1": len(multiple_rows),
            "candidate_count_le1": len(rows) - len(multiple_rows),
        },
        "identity_overlap": {
            "sample_inputs": exact_overlap,
            "allocation_and_ledger": ledger_overlap,
        },
        "comparison_mode": (
            "training_input_dev" if same_view else "fixed_input_regression"
        ),
        "qualification": {
            "evaluation_scope": admission["evaluation_scope"],
            "historical_external_exposure": admission["historical_external_exposure"],
            "physical_game_independence": "unresolved",
            "native_run_independence": False,
            "clean_held_out_claim": False,
            "qualification": "engineering_only",
        },
        "producers": {
            "training": run_manifest.producer.to_dict(),
            "evaluation": evaluation_producer.to_dict(),
        },
        "codec": {
            "state_tokenizer_sha256": bound.training.manifest.payload("state_tokenizer").sha256,
            "action_codec_sha256": bound.training.manifest.payload("action_codec").sha256,
            "source_renderer": bound.evaluation_view.parameters.value()["serializer"],
            "codec_refit_on_evaluation_view": False,
        },
        "checkpoint_step": step,
        "target_steps": config.steps,
        "evaluation_seed": config.seed,
        "dev_input_commitment_sha256": dev_commitment,
        "dev_input_count": len(dev_samples),
    }
    metrics_raw = json_bytes(metric_value)
    if len(metrics_raw) > MAX_DIAGNOSTIC_BYTES:
        raise BoundaryError("token_diagnostic", "diagnostic_size_limit")
    evaluation_view_id = bound.evaluation_view.artifact_id
    analysis = Manifest(
        "analysis", evaluation_producer,
        (Parent("checkpoint", checkpoint_id), Parent("run", run_id),
         Parent("training_input", training_input_id),
         Parent("evaluation_input", evaluation_input_id),
         Parent("model_view", evaluation_view_id)),
        (store.put_payload("diagnostic", io.BytesIO(metrics_raw), "application/json"),),
        FrozenObject.of({
            "schema": SCHEMA,
            "diagnostic_type": "private_checkpoint_diagnostic",
            "privacy_scope": "local_artifact_store",
            "partition": "dev",
            "qualification": "engineering_only",
            "checkpoint_id": checkpoint_id,
            "checkpoint_step": step,
            "run_id": run_id,
            "training_input_id": training_input_id,
            "evaluation_input_id": evaluation_input_id,
            "evaluation_view_id": evaluation_view_id,
            "comparison_mode": metric_value["comparison_mode"],
            "dev_input_commitment_sha256": dev_commitment,
            "dev_input_count": len(dev_samples),
            "training_producer": run_manifest.producer.to_dict(),
            "evaluation_producer": evaluation_producer.to_dict(),
            "training_operation_id": admission["training_operation_id"],
            "evaluation_training_operation_id": admission[
                "evaluation_training_operation_id"],
            "evaluation_operation_id": evaluation_operation_id,
            "evaluation_scope": admission["evaluation_scope"],
            "historical_external_exposure": admission["historical_external_exposure"],
            "physical_game_independence": "unresolved",
            "native_run_independence": False,
            "clean_held_out_claim": False,
        }),
    )
    store.publish(analysis)
    return CheckpointDiagnosticResult(analysis, {
        "analysis_id": analysis.artifact_id,
        "checkpoint_id": checkpoint_id,
        "checkpoint_step": step,
        "evaluation_input_id": evaluation_input_id,
        "evaluation_view_id": evaluation_view_id,
        "comparison_mode": metric_value["comparison_mode"],
        "dev_input_count": len(dev_samples),
        "dev_input_commitment_sha256": dev_commitment,
        "candidate_count_gt1": len(multiple_rows),
        "metrics": summary["overall"],
        "baselines": {name: value["overall"] for name, value in baselines.items()},
        "native_run_independence": False,
        "clean_held_out_claim": False,
    })
