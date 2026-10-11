"""Token execution capability using the existing store, reporter and evaluation owners."""
from __future__ import annotations

import io
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch

from spireagent.artifact_contracts import Manifest, Parent, Payload, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, digest, json_bytes, text
from spireagent.storage.store import ArtifactStore

from ..fullrun.evaluation import action_only_prior, evaluate_samples
from ..fullrun.light_action_inputs import (
    CANONICAL_SCHEMA as CANONICAL_LIGHT_ACTION_INPUT_SCHEMA,
)
from ..fullrun.light_action_inputs import (
    SCHEMA as LIGHT_ACTION_INPUT_SCHEMA,
)
from ..fullrun.light_action_inputs import (
    LoadedLightActionInputs,
    load_light_action_inputs,
)
from ..fullrun.token_inputs import FORMAT, LoadedTokenInputs, load_token_inputs
from ..historical_m0_spec import (
    DIAGNOSTIC_SCOPE,
    HistoricalM0Config,
)
from ..historical_m0_spec import (
    INPUT_SCHEMA as HISTORICAL_INPUT_SCHEMA,
)
from ..historical_m0_spec import (
    MODEL_SCHEMA as HISTORICAL_MODEL_SCHEMA,
)
from ..historical_m0_spec import (
    RECIPE as HISTORICAL_RECIPE,
)
from ..historical_m0_spec import (
    RUN_SCHEMA as HISTORICAL_RUN_SCHEMA,
)
from ..models.stage1a import recipe_for
from .report_schemas import TOKEN_EVALUATION_SCHEMA as EVALUATION_SCHEMA
from .reporting import RunReporter
from .token_ranking import (
    CHECKPOINT_SCHEMA,
    LIGHT_ACTION_M0_CHECKPOINT_SCHEMA,
    LightActionM0Config,
    Stage1aConfig,
    TokenRankingEngine,
    config_payload,
    decode_config,
)
from .worker import WorkerResult

RUN_SCHEMA = "stpd/stage1a-run-v1"
MODEL_SCHEMA = "stpd/stage1a-model-v1"
LIGHT_ACTION_M0_MODEL_SCHEMA = "stpd/stage1a-light-action-m0-model-v1"
CANONICAL_LIGHT_ACTION_M0_MODEL_SCHEMA = "stpd/stage1a-light-action-m0-canonical-model-v1"


@dataclass(frozen=True)
class HistoricalTokenResult:
    state: str
    run_id: str
    attempt_id: str
    checkpoint_id: str
    result_id: str | None
    model_id: str | None
    event_step: int
    optimizer_updates: int
    attempt_seconds: float


def _load_inputs(
    store: ArtifactStore, identity: str,
) -> LoadedTokenInputs | LoadedLightActionInputs:
    info = store.get_manifest(identity).parameters.value()
    if info.get("schema") == HISTORICAL_INPUT_SCHEMA:
        from ..fullrun.historical_public_m0 import load_historical_inputs

        return load_historical_inputs(store, identity)
    if info.get("schema") in {LIGHT_ACTION_INPUT_SCHEMA, CANONICAL_LIGHT_ACTION_INPUT_SCHEMA}:
        return load_light_action_inputs(store, identity)
    return load_token_inputs(store, identity)


def _model_schema(config: Stage1aConfig, input_schema: str | None = None) -> str:
    if isinstance(config, LightActionM0Config):
        if input_schema == HISTORICAL_INPUT_SCHEMA:
            return HISTORICAL_MODEL_SCHEMA
        return (CANONICAL_LIGHT_ACTION_M0_MODEL_SCHEMA
                if input_schema == CANONICAL_LIGHT_ACTION_INPUT_SCHEMA
                else LIGHT_ACTION_M0_MODEL_SCHEMA)
    return MODEL_SCHEMA


def _checkpoint_metadata(engine: TokenRankingEngine) -> dict[str, object]:
    info: dict[str, object] = {
        "schema": (LIGHT_ACTION_M0_CHECKPOINT_SCHEMA if engine.is_light_action_m0
                   else CHECKPOINT_SCHEMA),
        "step": engine.step, "data_identity": engine.data_identity,
    }
    if engine.is_light_action_m0:
        source = engine.inputs.manifest.parameters.value()
        info.update({
            "recipe": engine.config.recipe,
            "graph": recipe_for(engine.config.recipe).graph,
            "state_codec": source["state_codec"],
            "action_codec": source["action_codec"],
            "backbone": engine.backbone,
            "adapter_config": engine.backbone.get("adapter_config"),
            "adapter_tensor_names": sorted(engine.adapter_tensor_names or set()),
            "input_schema": source["schema"], "input_format": source["format"],
            "source_view_schema": source["source_schema"],
            "source_renderer": source["source_renderer"],
        })
        if "training_binding" in source:
            info["training_binding"] = source["training_binding"]
        if source["schema"] == HISTORICAL_INPUT_SCHEMA:
            info["initialization"] = source["initialization"]
            info["historical_relation"] = source["historical_relation"]
    return info


def prepare_token_run(
    store: ArtifactStore, inputs: LoadedTokenInputs | LoadedLightActionInputs,
    config: Stage1aConfig, producer: Producer, *, replicate: str = "stage1a",
    historical_config: dict[str, Any] | None = None, operation_id: str | None = None,
) -> Manifest:
    text(replicate, "run.replicate", maximum=128)
    input_info = inputs.manifest.parameters.value()
    historical = input_info.get("schema") == HISTORICAL_INPUT_SCHEMA
    if historical:
        selected = HistoricalM0Config.decode(historical_config)
        if (not isinstance(config, LightActionM0Config)
                or config_payload(config)
                != config_payload(LightActionM0Config(**selected.numerical_fields()))
                or input_info.get("training_binding", {}).get("training_operation_id")
                != operation_id
                or input_info.get("initialization", {}).get("model_id")
                != selected.warm_start_model_id):
            raise BoundaryError("token_run", "historical_run_binding_mismatch")
    elif historical_config is not None or operation_id is not None:
        raise BoundaryError("token_run", "historical_binding_on_legacy_run")
    if isinstance(config, LightActionM0Config):
        expected_family = ("train-only-byte-bpe" if recipe_for(config.recipe).backbone == "s"
                           else "pinned-qwen3")
        expected_input_schemas = {LIGHT_ACTION_INPUT_SCHEMA, CANONICAL_LIGHT_ACTION_INPUT_SCHEMA,
                                 HISTORICAL_INPUT_SCHEMA}
        canonical = input_info.get("schema") == CANONICAL_LIGHT_ACTION_INPUT_SCHEMA
        if (not isinstance(inputs, LoadedLightActionInputs)
                or input_info.get("schema") not in expected_input_schemas
                or input_info.get("graph") != recipe_for(config.recipe).graph
                or not isinstance(input_info.get("state_codec"), dict)
                or input_info["state_codec"].get("family") != expected_family
                or input_info.get("max_state_tokens") != config.max_state_tokens
                or input_info.get("max_action_bytes") != config.max_action_bytes):
            raise BoundaryError("token_run", "light_action_dual_input_or_codec_mismatch")
        if (canonical or historical) and not isinstance(input_info.get("training_binding"), dict):
            raise BoundaryError("token_run", "canonical_training_binding_required")
    elif (not isinstance(inputs, LoadedTokenInputs)
          or input_info.get("backbone") != recipe_for(config.recipe).backbone):
        raise BoundaryError("token_run", "input_backbone_mismatch")
    encoded_config = config_payload(config)
    training_binding = input_info.get("training_binding")
    experiment = Manifest(
        "experiment", producer, (Parent("training_input", inputs.manifest.artifact_id),),
        parameters=FrozenObject.of({"schema": "stpd/experiment-v1", "purpose": "engineering",
                                    "config": encoded_config,
                                    **({"training_binding": training_binding}
                                       if training_binding is not None else {})}),
    )
    store.publish(experiment)
    historical_parameters = ({
        "recipe": HISTORICAL_RECIPE, "operation_id": operation_id,
        "source_id": inputs.manifest.parent("source"), "historical_config": historical_config,
        "initialization": input_info["initialization"], "scope": DIAGNOSTIC_SCOPE,
    } if historical else {})
    run = Manifest(
        "run", producer, (Parent("training_input", inputs.manifest.artifact_id),
                          Parent("experiment", experiment.artifact_id),
                          *((Parent("initial_model", input_info["initialization"]["model_id"]),)
                            if historical else ())),
        parameters=FrozenObject.of({"schema": HISTORICAL_RUN_SCHEMA if historical else RUN_SCHEMA,
                                    "config": encoded_config,
                                    "replicate": replicate,
                                    "cpu_threads": torch.get_num_threads(),
                                    "torch_version": str(torch.__version__),
                                    **historical_parameters,
                                    **({"training_binding": training_binding}
                                       if training_binding is not None else {})}),
    )
    store.publish(run)
    return run


def _verify_completed(store: ArtifactStore, result: Manifest, run: Manifest,
                      *, verify_payloads: bool = True) -> None:
    """Do not silently replace a damaged completion with a fresh optimization attempt."""
    if (result.kind != "run_result" or result.producer != run.producer
            or result.parameters.value().get("state") != "completed"
            or result.parameters.value().get("steps") != run.parameters.value()["config"]["steps"]
            or sorted(p.role for p in result.parents)
            != ["model", "offline_evaluation", "run", "training_input"]
            or result.parent("run") != run.artifact_id
            or result.parent("training_input") != run.parent("training_input")):
        raise BoundaryError("token_run", "completed_identity_mismatch")
    historical = run.parameters.value().get("schema") == HISTORICAL_RUN_SCHEMA
    pending = [result.artifact_id] if verify_payloads else []
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
        if historical:
            # Original admission is the configured owner's separate proof. A new
            # subset completion must not hydrate all old evidence/weight ancestry.
            allowed = {result.artifact_id, run.artifact_id, run.parent("training_input"),
                       result.parent("model"), result.parent("offline_evaluation")}
            if item.kind == "model":
                allowed.add(item.parent("checkpoint"))
            pending.extend(p.artifact_id for p in item.parents if p.artifact_id in allowed)
        else:
            pending.extend(p.artifact_id for p in item.parents)
    config = decode_config(run.parameters.value()["config"])
    model = store.get_manifest(result.parent("model"))
    report = store.get_manifest(result.parent("offline_evaluation"))
    training_input = store.get_manifest(run.parent("training_input"))
    if (model.kind != "model" or model.producer != run.producer
            or model.parameters.value().get("schema")
            != _model_schema(config, training_input.parameters.value().get("schema"))
            or model.parent("run") != run.artifact_id
            or model.parent("training_input") != run.parent("training_input")
            or model.parameters.value().get("steps") != run.parameters.value()["config"]["steps"]
            or model.parameters.value().get("config")
            != ({**config_payload(config), "public_profile": "public_compact"}
                if historical else config_payload(config))
            or report.kind != "offline_evaluation" or report.producer != run.producer
            or report.parameters.value().get("schema") != EVALUATION_SCHEMA
            or report.parent("model") != model.artifact_id):
        raise BoundaryError("token_run", "completed_model_or_report_mismatch")
    if isinstance(config, LightActionM0Config):
        source = training_input
        source_info = source.parameters.value()
        model_info = model.parameters.value()
        checkpoint = store.get_manifest(model.parent("checkpoint"))
        checkpoint_info = checkpoint.parameters.value()
        view = store.get_manifest(source.parent("model_view"))
        names = model_info.get("adapter_tensor_names")
        backbone = model_info.get("backbone")
        if (
            model_info.get("recipe") != config.recipe
            or model_info.get("graph") != recipe_for(config.recipe).graph
            or model_info.get("state_codec") != source_info.get("state_codec")
            or model_info.get("action_codec") != source_info.get("action_codec")
            or model_info.get("state_tokenizer_sha256")
            != source_info.get("state_codec", {}).get("sha256")
            or model_info.get("action_codec_sha256")
            != source_info.get("action_codec", {}).get("sha256")
            or model_info.get("source_view_schema") != view.parameters.value().get("schema")
            or model_info.get("input_schema") != source_info.get("schema")
            or model_info.get("input_format") != source_info.get("format")
            or model_info.get("source_renderer") != source_info.get("source_renderer")
            or model_info.get("training_binding") != source_info.get("training_binding")
            or source_info.get("training_binding") != run.parameters.value().get("training_binding")
            or model_info.get("weights_sha256") != model.payload("weights").sha256
            or sorted(payload.role for payload in model.payloads)
            != ["action_codec", "state_tokenizer", "weights"]
            or model.payload("state_tokenizer").sha256
            != source_info.get("state_codec", {}).get("sha256")
            or model.payload("action_codec").sha256
            != source_info.get("action_codec", {}).get("sha256")
            or checkpoint.kind != "checkpoint"
            or checkpoint.parent("run") != run.artifact_id
            or checkpoint.parent("training_input") != source.artifact_id
            or checkpoint_info.get("schema") != LIGHT_ACTION_M0_CHECKPOINT_SCHEMA
            or checkpoint_info.get("step") != config.steps
            or checkpoint_info.get("recipe") != config.recipe
            or checkpoint_info.get("graph") != recipe_for(config.recipe).graph
            or checkpoint_info.get("action_codec") != source_info.get("action_codec")
            or checkpoint_info.get("input_schema") != source_info.get("schema")
            or checkpoint_info.get("input_format") != source_info.get("format")
            or checkpoint_info.get("source_view_schema") != source_info.get("source_schema")
            or checkpoint_info.get("source_renderer") != source_info.get("source_renderer")
            or checkpoint_info.get("training_binding") != source_info.get("training_binding")
            or checkpoint_info.get("backbone") != backbone
            or checkpoint_info.get("adapter_config") != model_info.get("adapter_config")
            or checkpoint_info.get("adapter_tensor_names") != names
            or not isinstance(backbone, dict)
            or not isinstance(names, list)
            or len(names) != len(set(names))
        ):
            raise BoundaryError("token_run", "light_action_model_identity_mismatch")
        if historical and (
            model_info.get("initialization") != source_info.get("initialization")
            or model_info.get("historical_relation") != source_info.get("historical_relation")
            or model_info.get("scope") != DIAGNOSTIC_SCOPE
            or run.parent("initial_model") != source_info["initialization"]["model_id"]
            or run.parameters.value().get("initialization") != source_info["initialization"]
            or checkpoint_info.get("initialization") != source_info["initialization"]
            or checkpoint_info.get("historical_relation") != source_info["historical_relation"]
        ):
            raise BoundaryError("token_run", "historical_model_initialization_or_relation_mismatch")
        if source_info.get("schema") in {
            CANONICAL_LIGHT_ACTION_INPUT_SCHEMA, HISTORICAL_INPUT_SCHEMA,
        }:
            expected_eval = DIAGNOSTIC_SCOPE if historical else {
                "evaluation_scope": "within_training_purpose_allocation",
                "historical_external_exposure": "unknown",
                "physical_game_independence": "unresolved",
                "clean_held_out_claim": False,
            }
            if any(report.parameters.value().get(key) != value
                   for key, value in expected_eval.items()):
                raise BoundaryError("token_run", "canonical_dev_qualification_mismatch")
            payload = report.payload("metrics")
            if payload.size > 64 * 1024 * 1024:
                raise BoundaryError("token_run", "evaluation_size_limit")
            metrics = json.loads(b"".join(store.read_payload(payload)))
            summaries = [metrics.get("summary", {}).get("bootstrap")]
            summaries.extend(item.get("bootstrap") for item in
                             metrics.get("baselines", {}).values())
            if any(not isinstance(item, dict) or item.get("status") != "unknown"
                   or item.get("unit") != "session_scoped_run_group" for item in summaries):
                raise BoundaryError("token_run", "canonical_dev_independence_claim")
            if metrics.get("admission") != expected_eval:
                raise BoundaryError("token_run", "canonical_dev_qualification_mismatch")


def execute_tokens(store: ArtifactStore, reporter: RunReporter, run_id: str, runtime: Producer,
                   *, snapshot: Path | None = None, resume: str | None = None,
                   stop_after: int | None = None,
                   dev_admitter: Callable[[Manifest, Manifest], dict] | None = None,
                   operation_id: str | None = None, attempt_id: str | None = None,
                   authority: Any = None, control: Any = None,
                   checkpoint_every_boundaries: int = 100,
                   ) -> WorkerResult | HistoricalTokenResult:
    run = store.get_manifest(run_id)
    info = run.parameters.value()
    historical = info.get("schema") == HISTORICAL_RUN_SCHEMA
    if historical:
        selected = HistoricalM0Config.decode(info.get("historical_config"))
        if (authority is None or control is None or attempt_id is None or stop_after is not None
                or operation_id != info.get("operation_id")
                or info.get("recipe") != HISTORICAL_RECIPE
                or selected.checkpoint_every_boundaries != checkpoint_every_boundaries):
            raise BoundaryError("token_run", "historical_attempt_authority_required")
        authority.assert_current(run_id, operation_id, attempt_id)
    elif any(value is not None for value in (operation_id, attempt_id, authority, control)):
        raise BoundaryError("token_run", "historical_authority_on_legacy_run")
    if (run.kind != "run" or run.producer != runtime
            or info.get("schema") != (HISTORICAL_RUN_SCHEMA if historical else RUN_SCHEMA)
            or info.get("torch_version") != str(torch.__version__)
            or info.get("cpu_threads") != torch.get_num_threads()
            or sorted(p.role for p in run.parents)
            != (["experiment", "initial_model", "training_input"] if historical
                else ["experiment", "training_input"])):
        raise BoundaryError("token_run", "source_or_contract_mismatch")
    config = decode_config(info["config"])
    if (historical and config_payload(config)
            != config_payload(LightActionM0Config(**selected.numerical_fields()))):
        raise BoundaryError("token_run", "historical_numerical_configuration_mismatch")
    if stop_after is not None and (type(stop_after) is not int or stop_after < 1):
        raise BoundaryError("token_run", "invalid_pause_budget")
    inputs = _load_inputs(store, run.parent("training_input"))
    input_info = inputs.manifest.parameters.value()
    if (input_info.get("schema") in {CANONICAL_LIGHT_ACTION_INPUT_SCHEMA, HISTORICAL_INPUT_SCHEMA}
            and info.get("training_binding") != input_info.get("training_binding")):
        raise BoundaryError("token_run", "training_binding_mismatch")
    experiment = store.get_manifest(run.parent("experiment"))
    if (experiment.kind != "experiment" or experiment.producer != runtime
            or experiment.parent("training_input") != inputs.manifest.artifact_id
            or experiment.parameters.value().get("config") != config_payload(config)
            or experiment.parameters.value().get("training_binding")
            != info.get("training_binding")):
        raise BoundaryError("token_run", "experiment_mismatch")
    previous = reporter.completed(run_id)
    if previous is not None:
        _verify_completed(store, previous, run)
        if historical:
            raise BoundaryError("token_run", "completed_historical_attempt_requires_reconciliation")
        return WorkerResult("completed", run_id, result_id=previous.artifact_id)
    if resume is None and reporter.events(run_id):
        raise BoundaryError("token_run", "existing_attempt_requires_explicit_resume")
    attempt = digest(attempt_id, "token_run.attempt", length=32) if historical else uuid.uuid4().hex
    started = time.perf_counter()
    event_step = max((event.parameters.value().get("step", 0)
                      for event in reporter.events(run_id)), default=0) if historical else 0
    engine: TokenRankingEngine | None = None
    checkpoint_id = None

    def event(kind: str, **details: object) -> None:
        nonlocal event_step
        if historical:
            authority.assert_current(run_id, operation_id, attempt_id)
            event_step += 1
            details = {"optimizer_updates": engine.step if engine else 0, **details}
        reporter.emit(Manifest(
            "run_event", runtime, (Parent("run", run_id),),
            parameters=FrozenObject.of({"schema": "stpd/run-event-v1", "attempt": attempt,
                                        "kind": kind,
                                        "step": (event_step if historical else
                                                 engine.step if engine else 0),
                                        "details": details}),
        ))

    def checkpoint() -> str:
        assert engine is not None
        checkpoint_info = _checkpoint_metadata(engine)
        if historical:
            authority.assert_current(run_id, operation_id, attempt_id)
            checkpoint_info["attempt"] = attempt_id
        payload = store.put_payload("checkpoint", io.BytesIO(engine.checkpoint()),
                                    "application/vnd.stpd.tensor-tree")
        item = Manifest(
            "checkpoint", runtime,
            (Parent("run", run_id), Parent("training_input", inputs.manifest.artifact_id)),
            (payload,), FrozenObject.of(checkpoint_info),
        )
        store.publish(item)
        event("checkpoint", checkpoint_id=item.artifact_id)
        return item.artifact_id

    try:
        event("loading", resume=resume)
        engine = TokenRankingEngine(inputs, config, snapshot=snapshot)
        if historical and resume is None:
            from ..policy.token_decision import check_light_action_m0_model

            original_model = store.get_manifest(run.parent("initial_model"))
            _, model_info = check_light_action_m0_model(original_model)
            if (model_info["backbone"] != engine.backbone
                    or input_info.get("initialization") != info.get("initialization")
                    or original_model.artifact_id != input_info["initialization"]["model_id"]):
                raise BoundaryError("token_run", "warm_start_original_model_mismatch")
            engine.initialize_model_weights(
                b"".join(store.read_payload(original_model.payload("weights"))),
                input_info["initialization"],
            )
        if resume is not None:
            if historical:
                authority.authorize_resume(run_id, attempt_id, resume)
            saved = store.get_manifest(resume)
            saved_info = saved.parameters.value()
            expected_checkpoint = _checkpoint_metadata(engine)
            exact_checkpoint_identity = (
                all(saved_info.get(key) == value for key, value in expected_checkpoint.items()
                    if key != "step")
                and type(saved_info.get("step")) is int
                and 0 <= saved_info["step"] <= config.steps
            )
            if (saved.kind != "checkpoint" or saved.producer != runtime
                    or saved_info.get("schema") != (LIGHT_ACTION_M0_CHECKPOINT_SCHEMA
                                                     if engine.is_light_action_m0
                                                     else CHECKPOINT_SCHEMA)
                    or saved.parent("run") != run_id
                    or saved.parent("training_input") != inputs.manifest.artifact_id
                    or saved_info.get("data_identity") != engine.data_identity
                    or not exact_checkpoint_identity
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
            if historical:
                authority.assert_current(run_id, operation_id, attempt_id)
                action = control.requested_action()
                if action != "continue":
                    checkpoint_id = checkpoint()
                    state = "cancelled" if action == "cancel" else "paused"
                    event(state, checkpoint_id=checkpoint_id)
                    return HistoricalTokenResult(state, run_id, attempt, checkpoint_id, None, None,
                                                 event_step, engine.step,
                                                 time.perf_counter() - started)
            step_started = time.perf_counter()
            loss = engine.advance()
            seconds = time.perf_counter() - step_started
            event("step", loss=loss, seconds=seconds)
            if not historical:
                print(json_bytes({"run_id": run_id, "step": engine.step,
                                  "total_steps": config.steps,
                              "loss": loss, "seconds": seconds}).decode(), flush=True)
            # Small 1a runs: every completed update is recoverable. Never write mid-update state.
            if (not historical or engine.step % checkpoint_every_boundaries == 0
                    or engine.step == config.steps):
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
        parents = (Parent("run", run_id), Parent("checkpoint", checkpoint_id),
                   Parent("training_input", inputs.manifest.artifact_id),
                   Parent("model_view", view.artifact_id))
        model_payloads: tuple[Payload, ...]
        if engine.is_light_action_m0:
            assert isinstance(inputs, LoadedLightActionInputs)
            input_info = inputs.manifest.parameters.value()
            model_payloads = (weights, inputs.manifest.payload("state_tokenizer"),
                              inputs.manifest.payload("action_codec"))
            model_info = {
                "schema": _model_schema(config, input_info["schema"]),
                "config": config_payload(config), "recipe": config.recipe,
                "graph": recipe_for(config.recipe).graph, "backbone": engine.backbone,
                "state_codec": input_info["state_codec"],
                "action_codec": input_info["action_codec"],
                "adapter_config": engine.backbone.get("adapter_config"),
                "adapter_tensor_names": sorted(engine.adapter_tensor_names or set()),
                "state_tokenizer_sha256": input_info["state_codec"]["sha256"],
                "action_codec_sha256": input_info["action_codec"]["sha256"],
                "weights_sha256": weights.sha256,
                "source_view_schema": view.parameters.value().get("schema"),
                "source_renderer": input_info["source_renderer"],
                "input_schema": input_info["schema"],
                "input_format": input_info["format"], "steps": engine.step,
                "qualification": "engineering_only", "dtype": "float32",
                "runtime_integration": "not_included_in_light_action_m0_slice",
            }
            if "training_binding" in input_info:
                model_info["training_binding"] = input_info["training_binding"]
            if historical:
                model_info["config"] = {
                    **config_payload(config), "public_profile": "public_compact",
                }
                model_info.update(initialization=input_info["initialization"],
                                  historical_relation=input_info["historical_relation"],
                                  scope=DIAGNOSTIC_SCOPE)
        else:
            assert isinstance(inputs, LoadedTokenInputs)
            model_payloads = (weights, inputs.manifest.payload("tokenizer"))
            model_info = {
                "schema": MODEL_SCHEMA, "config": config_payload(config),
                "graph": recipe_for(config.recipe).graph, "backbone": engine.backbone,
                "vocab_size": inputs.manifest.parameters.value()["vocab_size"],
                "serializer": view.parameters.value()["serializer"],
                "input_format": FORMAT, "steps": engine.step,
                "qualification": "engineering_only", "dtype": "float32",
            }
        model = Manifest("model", runtime, parents, model_payloads,
                         FrozenObject.of(model_info))
        store.publish(model)
        # Human text-input rows are session-scoped; distinct recorded run IDs do
        # not establish independent native runs across those sessions.
        canonical = (input_info.get("schema") == CANONICAL_LIGHT_ACTION_INPUT_SCHEMA)
        dev_admission = None
        if canonical or historical:
            if dev_admitter is None:
                raise BoundaryError("token_run", "canonical_dev_admission_required")
            dev_admission = dev_admitter(model, view)
            if (not isinstance(dev_admission, dict)
                    or dev_admission.get("evaluation_scope")
                    != (DIAGNOSTIC_SCOPE["evaluation_scope"] if historical
                        else "within_training_purpose_allocation")
                    or dev_admission.get("physical_game_independence") != "unresolved"
                    or dev_admission.get("clean_held_out_claim") is not False):
                raise BoundaryError("token_run", "canonical_dev_admission_invalid")
        event("evaluating", model_id=model.artifact_id)
        native_run_independence = (
            False if canonical or historical else view.parameters.value().get("schema") not in {
                "stpd/human-text-input-bc-view-v1", "stpd/human-text-input-bc-view-v2",
            }
        )
        rows, summary = evaluate_samples(
            inputs.samples, engine.scores, seed=config.seed,
            native_run_independence=native_run_independence,
        )
        prior = action_only_prior(inputs.samples)
        baselines = {}
        for name, scorer in (
            ("uniform_legal", lambda i: (0.0,) * len(inputs.samples[i].action_keys)),
            ("action_only", lambda i: prior(inputs.samples[i])),
        ):
            _, baselines[name] = evaluate_samples(
                inputs.samples, scorer, seed=config.seed,
                native_run_independence=native_run_independence,
            )
        metric_value = {"rows": rows, "summary": summary, "baselines": baselines}
        evaluation_info: dict[str, Any] = {"schema": EVALUATION_SCHEMA, "partition": "dev",
                           "qualification": "engineering_only"}
        if dev_admission is not None:
            qualification = DIAGNOSTIC_SCOPE if historical else {
                key: dev_admission[key] for key in (
                "evaluation_scope", "historical_external_exposure",
                "physical_game_independence", "clean_held_out_claim",
            )}
            metric_value["admission"] = qualification
            evaluation_info.update(qualification)
        metrics = store.put_payload("metrics", io.BytesIO(json_bytes(metric_value)),
                                    "application/json")
        evaluation = Manifest(
            "offline_evaluation", runtime,
            (Parent("model", model.artifact_id), Parent("model_view", view.artifact_id)),
            (metrics,), FrozenObject.of(evaluation_info),
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
        if historical:
            return HistoricalTokenResult("completed", run_id, attempt, checkpoint_id, result_id,
                                         model.artifact_id, event_step, engine.step,
                                         time.perf_counter() - started)
        return WorkerResult("completed", run_id, checkpoint_id, result_id)
    except Exception as error:
        event("failed", error_type=type(error).__name__, last_checkpoint=checkpoint_id)
        raise
