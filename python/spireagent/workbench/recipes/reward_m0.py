"""Explicit reward-M0 backend on the existing v3 lifecycle/publication channel."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.reward_m0_data import (
    record_reward_m0_use,
    require_reward_m0_source,
    require_reward_m0_use,
)
from stpd.reward_m0_spec import CHECKPOINT_SCHEMA, RewardM0Config

from .structured import StructuredRecipeAdapter


@dataclass(frozen=True)
class _OriginalRunGroup:
    run_id: str


@dataclass(frozen=True)
class RewardM0Preflight:
    runs: tuple[_OriginalRunGroup, ...]
    paired_lengths: dict[str, Any]


class RewardM0RecipeAdapter(StructuredRecipeAdapter):
    """Share only owned v3 lifecycle machinery; domain identities are reward-M0."""

    def preflight(self, store: Any, owner: Any, source_id: str) -> RewardM0Preflight:
        from stpd.fullrun.light_action_inputs import fit_state_bpe
        from stpd.fullrun.reward_m0_inputs import _compile, samples_for

        source = require_reward_m0_source(owner, store, source_id)
        config = RewardM0Config(**{**source.value["paired_config"], "arm": "A"})
        tokenizer = fit_state_bpe(
            samples_for(source, "A") + samples_for(source, "B"), vocab_size=config.state_vocab_size
        )
        _, compiled = _compile(source, tokenizer, config)
        return RewardM0Preflight(
            tuple(_OriginalRunGroup(run) for run in sorted(source.runs)), compiled["length_stats"]
        )

    def preflight_request(
        self, store: Any, owner: Any, request: TrainingRequest
    ) -> RewardM0Preflight:
        source = require_reward_m0_source(owner, store, request.source_id)
        config = RewardM0Config.decode(request.config)
        if (
            config.paired_schedule() != source.value["paired_config"]
            or request.limits != source.value["paired_limits"]
        ):
            raise BoundaryError("reward_m0_recipe", "frozen_pair_config_or_limits_mismatch")
        if config.device == "mps":
            # This is an availability check only; no model or numerical update.
            import torch

            if not torch.backends.mps.is_available():
                raise BoundaryError("reward_m0_recipe", "mps_unavailable_no_fallback")
        return self.preflight(store, owner, request.source_id)

    def record_training_use(
        self,
        owner: Any,
        store: Any,
        operation: dict[str, Any],
        source_id: str,
        identity: str,
        runs: set[str],
    ) -> None:
        self.preflight_request(store, owner, TrainingRequest.from_dict(operation["request"]))
        if operation["mode"] == "start":
            record_reward_m0_use(owner, store, source_id, identity)
        require_reward_m0_use(owner, store, source_id, identity)

    def validate_terminal(
        self, store: Any, operation: dict[str, Any], terminal: dict[str, Any]
    ) -> None:
        if terminal["state"] != "completed":
            return
        from stpd.workers.reward_m0_run import verify_reward_m0_completed

        result = verify_reward_m0_completed(
            store,
            ObjectStoreRunReporter(store, store.blobs),
            operation["run_id"],
            operation["attempt_id"],
        )
        if any(
            terminal[key] != getattr(result, key)
            for key in (
                "run_id",
                "attempt_id",
                "checkpoint_id",
                "result_id",
                "model_id",
                "event_step",
                "optimizer_updates",
            )
        ):
            raise BoundaryError("reward_m0_recipe", "completed_terminal_artifact_mismatch")


def verify_reward_resume_checkpoint(
    store: Any, operation: dict[str, Any], checkpoint_id: str
) -> None:
    from stpd.workers.reward_m0_run import verify_reward_m0_run

    run, config = verify_reward_m0_run(store, operation["run_id"])
    checkpoint = store.get_manifest(checkpoint_id)
    info = checkpoint.parameters.value()
    if (
        checkpoint.kind != "checkpoint"
        or checkpoint.producer != run.producer
        or checkpoint.parent("run") != run.artifact_id
        or checkpoint.parent("training_input") != operation["input_id"]
        or info.get("schema") != CHECKPOINT_SCHEMA
        or info.get("arm") != config.arm
        or type(info.get("optimizer_updates")) is not int
        or not 0 <= info["optimizer_updates"] <= config.steps
    ):
        raise BoundaryError("reward_m0_recipe", "checkpoint_run_arm_identity_mismatch")
    prior = {item["attempt_id"]: item for item in operation["attempts"] if item["writer_terminal"]}
    if operation["writer_terminal"]:
        prior[operation["attempt_id"]] = operation
    if info.get("attempt") not in prior:
        raise BoundaryError("reward_m0_recipe", "checkpoint_writer_terminal_required")
    events = ObjectStoreRunReporter(store, store.blobs).events(run.artifact_id)
    if not any(
        event.producer == checkpoint.producer
        and event.parameters.value().get("kind") == "checkpoint"
        and event.parameters.value().get("attempt") == info["attempt"]
        and event.parameters.value().get("details", {}).get("checkpoint_id") == checkpoint_id
        for event in events
    ):
        raise BoundaryError("reward_m0_recipe", "checkpoint_event_required")


def run_reward_child(args: Any, channel: Any, store: Any, fence: Any, producer: Producer) -> None:
    from spireagent.workbench.recipes.structured_child import (
        ChildReporter,
        FencedSlots,
        FencedStore,
    )
    from stpd.workers.reward_m0_run import (
        execute_reward_m0_run,
        prepare_reward_m0_run,
        verify_reward_m0_run,
    )

    operation = fence.operation()
    request = TrainingRequest.from_dict(operation["request"])
    config = RewardM0Config.decode(request.config)
    # The child consumes the same owner-held receipt/use, never a copied permit.
    from spireagent.workbench.local_curation import LocalCurationOwner

    identity = operation["_owner"]
    # Managed and configured workspaces have different durable ledger paths.
    # Resolve the existing marker, without creating or recovering another owner.
    from spireagent.json_boundary import decode_json
    from spireagent.workbench.local_curation import OWNER_NAME

    marker = decode_json((args.store / OWNER_NAME).read_bytes())
    owner = LocalCurationOwner(
        Path(marker["ledger_path"]),
        args.store,
        identity[0],
        identity[1],
        identity[2],
        legacy_guard=marker["schema"] == "stpd/local-curation-inplace-owner-v1",
        owner_schema=marker["schema"],
    )
    source = require_reward_m0_source(owner, store, request.source_id)
    require_reward_m0_use(owner, store, request.source_id, args.operation_id)
    if (
        config.paired_schedule() != source.value["paired_config"]
        or request.limits != source.value["paired_limits"]
    ):
        raise BoundaryError("reward_m0_recipe", "frozen_pair_config_or_limits_mismatch")
    fenced = FencedStore(store, fence)
    if operation["mode"] == "start":
        run = prepare_reward_m0_run(fenced, source, config, producer, args.operation_id)
        input_id = run.parent("training_input")
        fence.bind_run(run)
        channel.emit("prepared", run_id=run.artifact_id, input_id=input_id)
        fence.wait_for_parent_run(run.artifact_id, input_id)
    else:
        run, existing = verify_reward_m0_run(store, operation["run_id"])
        if config != existing:
            raise BoundaryError("reward_m0_recipe", "resume_configuration_mismatch")
    reporter = ChildReporter(
        ObjectStoreRunReporter(fenced, FencedSlots(store.blobs, fence)), fence, channel
    )
    result = execute_reward_m0_run(
        fenced,
        reporter,
        run.artifact_id,
        args.operation_id,
        args.attempt_id,
        mode=operation["mode"],
        authority=fence,
        control=fence,
        checkpoint_id=operation.get("resume_checkpoint_id")
        if operation["mode"] == "resume"
        else None,
    )
    channel.emit("terminal", **asdict(result))
