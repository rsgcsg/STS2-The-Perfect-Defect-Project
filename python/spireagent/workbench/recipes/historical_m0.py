"""One historical M0 recipe over existing token computation and v3 child lifecycle."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, decode_json
from spireagent.source import source_identity
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.developer import ROOT
from spireagent.workbench.historical_m0_data import (
    record_historical_use,
    require_historical_source,
    require_historical_use,
)
from spireagent.workbench.recipe_contracts import TrainingRequest
from stpd.fullrun.historical_public_m0 import warm_start_metadata
from stpd.historical_m0_spec import DIAGNOSTIC_SCOPE, RUN_SCHEMA, HistoricalM0Config

from .structured import StructuredRecipeAdapter


@dataclass(frozen=True)
class _RunGroup:
    run_id: str


@dataclass(frozen=True)
class HistoricalPreflight:
    runs: tuple[_RunGroup, ...]


class HistoricalM0RecipeAdapter(StructuredRecipeAdapter):
    def preflight(self, store: Any, owner: Any, source_id: str) -> HistoricalPreflight:
        _, summary = require_historical_source(owner, store, source_id)
        return HistoricalPreflight(
            tuple(_RunGroup(run) for run in summary["datasets"][0]["qualified_run_ids"])
        )

    def preflight_request(
        self, store: Any, owner: Any, request: TrainingRequest
    ) -> HistoricalPreflight:
        relation, summary = require_historical_source(owner, store, request.source_id)
        warm_start_metadata(store, relation, HistoricalM0Config.decode(request.config))
        return HistoricalPreflight(
            tuple(_RunGroup(run) for run in summary["datasets"][0]["qualified_run_ids"])
        )

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
            record_historical_use(owner, store, source_id, identity)
        require_historical_use(owner, store, source_id, identity)

    def validate_terminal(
        self, store: Any, operation: dict[str, Any], terminal: dict[str, Any]
    ) -> None:
        if terminal["state"] != "completed":
            return
        result = verify_historical_completed(store, operation["run_id"], operation["attempt_id"])
        for name in (
            "run_id",
            "attempt_id",
            "checkpoint_id",
            "result_id",
            "model_id",
            "event_step",
            "optimizer_updates",
        ):
            if terminal[name] != getattr(result, name):
                raise BoundaryError("historical_m0_recipe", "completed_terminal_artifact_mismatch")


def verify_historical_run(store: Any, run_id: str) -> tuple[Any, HistoricalM0Config]:
    from stpd.workers.token_ranking import LightActionM0Config, config_payload

    run = store.get_manifest(run_id)
    info = run.parameters.value()
    selected = HistoricalM0Config.decode(info.get("historical_config"))
    inputs = store.get_manifest(run.parent("training_input"))
    source = inputs.parameters.value()
    if (
        run.kind != "run"
        or info.get("schema") != RUN_SCHEMA
        or info.get("config") != config_payload(LightActionM0Config(**selected.numerical_fields()))
        or info.get("initialization") != source.get("initialization")
        or info.get("source_id") != inputs.parent("source")
        or run.parent("initial_model") != selected.warm_start_model_id
        or source.get("initialization", {}).get("model_id") != selected.warm_start_model_id
        or source.get("training_binding", {}).get("training_operation_id")
        != info.get("operation_id")
        or inputs.producer != run.producer
    ):
        raise BoundaryError("historical_m0_recipe", "run_input_initialization_binding_mismatch")
    return run, selected


def verify_historical_completed(store: Any, run_id: str, attempt_id: str) -> Any:
    """Validate the actual new checkpoint/weights/result, without old archive reads."""
    import torch

    from stpd.fullrun.historical_public_m0 import load_historical_inputs
    from stpd.workers.token_ranking import LightActionM0Config, TokenRankingEngine
    from stpd.workers.token_worker import HistoricalTokenResult, _verify_completed

    run, config = verify_historical_run(store, run_id)
    reporter = ObjectStoreRunReporter(store, store.blobs)
    result = reporter.completed(run_id)
    if result is None:
        raise BoundaryError("historical_m0_recipe", "completed_original_result_required")
    _verify_completed(store, result, run)
    model = store.get_manifest(result.parent("model"))
    checkpoint = store.get_manifest(model.parent("checkpoint"))
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(run.parameters.value()["cpu_threads"])
    try:
        inputs = load_historical_inputs(store, run.parent("training_input"))
        engine = TokenRankingEngine(inputs, LightActionM0Config(**config.numerical_fields()))
        engine.restore(b"".join(store.read_payload(checkpoint.payload("checkpoint"))))
        if (
            engine.step != config.steps
            or checkpoint.parameters.value().get("step") != engine.step
            or hashlib.sha256(engine.model_bytes()).hexdigest() != model.payload("weights").sha256
        ):
            raise BoundaryError("historical_m0_recipe", "completed_checkpoint_weight_mismatch")
    finally:
        torch.set_num_threads(previous_threads)
    event_step = max(
        (event.parameters.value()["step"] for event in reporter.events(run_id)), default=0
    )
    return HistoricalTokenResult(
        "completed",
        run_id,
        attempt_id,
        checkpoint.artifact_id,
        result.artifact_id,
        model.artifact_id,
        event_step,
        config.steps,
        0.0,
    )


def historical_completed_lineage(
    store: Any, owner: Any, model: Any, *, verify_payloads: bool = False
) -> dict[str, Any]:
    """One completion/admission relation reused by app export and registration."""
    from stpd.policy.token_decision import check_light_action_m0_model
    from stpd.workers.token_worker import _verify_completed

    _, info = check_light_action_m0_model(model)
    run, _ = verify_historical_run(store, model.parent("run"))
    result = ObjectStoreRunReporter(store, store.blobs).completed(run.artifact_id)
    if result is None or result.parent("model") != model.artifact_id:
        raise BoundaryError("historical_m0_recipe", "completed_original_result_required")
    _verify_completed(store, result, run, verify_payloads=verify_payloads)
    source_id = info["historical_relation"]["source_id"]
    require_historical_source(owner, store, source_id)
    require_historical_use(owner, store, source_id, run.parameters.value()["operation_id"])
    if verify_payloads:
        verify_historical_completed(
            store,
            run.artifact_id,
            store.get_manifest(model.parent("checkpoint")).parameters.value()["attempt"],
        )
    return {
        "run_id": run.artifact_id,
        "result_id": result.artifact_id,
        "checkpoint_id": model.parent("checkpoint"),
        "input_id": model.parent("training_input"),
        **info["training_binding"],
        "historical_relation": info["historical_relation"],
        "initialization": info["initialization"],
    }


def verify_historical_resume_checkpoint(
    store: Any, operation: dict[str, Any], checkpoint_id: str
) -> None:
    run, _ = verify_historical_run(store, operation["run_id"])
    if source_identity(ROOT) != run.producer:
        raise BoundaryError("historical_m0_recipe", "unscoped_resume_current_producer_mismatch")
    checkpoint = store.get_manifest(checkpoint_id)
    info = checkpoint.parameters.value()
    from stpd.workers.token_ranking import LIGHT_ACTION_M0_CHECKPOINT_SCHEMA

    if (
        checkpoint.kind != "checkpoint"
        or checkpoint.producer != run.producer
        or checkpoint.parent("run") != run.artifact_id
        or checkpoint.parent("training_input") != operation["input_id"]
        or info.get("schema") != LIGHT_ACTION_M0_CHECKPOINT_SCHEMA
        or info.get("initialization") != run.parameters.value()["initialization"]
    ):
        raise BoundaryError("historical_m0_recipe", "checkpoint_run_initialization_mismatch")
    writers = {item["attempt_id"] for item in operation["attempts"] if item["writer_terminal"]}
    if operation["writer_terminal"]:
        writers.add(operation["attempt_id"])
    if info.get("attempt") not in writers:
        raise BoundaryError("historical_m0_recipe", "checkpoint_writer_terminal_required")
    if not any(
        event.producer == checkpoint.producer
        and event.parameters.value().get("kind") == "checkpoint"
        and event.parameters.value().get("attempt") == info["attempt"]
        and event.parameters.value().get("details", {}).get("checkpoint_id") == checkpoint_id
        for event in ObjectStoreRunReporter(store, store.blobs).events(run.artifact_id)
    ):
        raise BoundaryError("historical_m0_recipe", "checkpoint_event_required")


def run_historical_child(
    args: Any, channel: Any, store: Any, fence: Any, producer: Producer
) -> None:
    import torch

    from spireagent.workbench.local_curation import OWNER_NAME, LocalCurationOwner
    from spireagent.workbench.recipes.structured_child import (
        ChildReporter,
        FencedSlots,
        FencedStore,
    )
    from stpd.fullrun.historical_public_m0 import (
        load_historical_inputs,
        publish_historical_inputs,
        verify_relation,
    )
    from stpd.workers.token_ranking import LightActionM0Config
    from stpd.workers.token_worker import execute_tokens, prepare_token_run

    operation = fence.operation()
    request = TrainingRequest.from_dict(operation["request"])
    config = HistoricalM0Config.decode(request.config)
    marker = decode_json((args.store / OWNER_NAME).read_bytes())
    identity = operation["_owner"]
    owner = LocalCurationOwner(
        Path(marker["ledger_path"]),
        args.store,
        identity[0],
        identity[1],
        identity[2],
        legacy_guard=marker["schema"] == "stpd/local-curation-inplace-owner-v1",
        owner_schema=marker["schema"],
    )
    relation, _ = require_historical_source(owner, store, request.source_id)
    require_historical_use(owner, store, request.source_id, args.operation_id)
    warm_start_metadata(store, relation, config)
    # The selected-only owner proof is already current; read frozen selected
    # bytes here, never decode the original whole-corpus payload again.
    relation = verify_relation(store, request.source_id)
    fenced = FencedStore(store, fence)
    torch.set_num_threads(2)
    if operation["mode"] == "start":
        training = publish_historical_inputs(
            fenced, request.source_id, relation, config, args.operation_id, producer
        )
        inputs = load_historical_inputs(store, training.artifact_id, verified_relation=relation)
        run = prepare_token_run(
            fenced,
            inputs,
            LightActionM0Config(**config.numerical_fields()),
            producer,
            replicate="historical-" + args.operation_id,
            historical_config=config.to_dict(),
            operation_id=args.operation_id,
        )
        fence.bind_run(run)
        channel.emit("prepared", run_id=run.artifact_id, input_id=training.artifact_id)
        fence.wait_for_parent_run(run.artifact_id, training.artifact_id)
    else:
        run, frozen = verify_historical_run(store, operation["run_id"])
        if producer != run.producer or config != frozen:
            raise BoundaryError(
                "historical_m0_recipe", "resume_current_producer_or_config_mismatch"
            )
    reporter = ChildReporter(
        ObjectStoreRunReporter(fenced, FencedSlots(store.blobs, fence)), fence, channel
    )
    if operation["mode"] == "reconcile":
        result = verify_historical_completed(store, run.artifact_id, args.attempt_id)
    else:

        def dev_admitter(model: Any, view: Any) -> dict[str, Any]:
            summary = require_historical_use(owner, store, request.source_id, args.operation_id)
            bound = summary["datasets"][0]
            for source in bound["sources"]:
                owner.ledger.use_source(source["artifact_id"], "evaluation", args.operation_id)
            owner.ledger.use(
                {sample.run_id for sample in relation.samples if sample.split == "dev"},
                "evaluation",
                args.operation_id,
            )
            return dict(DIAGNOSTIC_SCOPE)

        result = execute_tokens(
            fenced,
            reporter,
            run.artifact_id,
            run.producer,
            resume=operation.get("resume_checkpoint_id") if operation["mode"] == "resume" else None,
            dev_admitter=dev_admitter,
            operation_id=args.operation_id,
            attempt_id=args.attempt_id,
            authority=fence,
            control=fence,
            checkpoint_every_boundaries=config.checkpoint_every_boundaries,
        )
    channel.emit("terminal", **asdict(result))
