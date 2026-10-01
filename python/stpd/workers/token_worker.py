"""Token execution capability using the existing store, reporter and evaluation owners."""
from __future__ import annotations

import io
import time
import uuid
from pathlib import Path

import torch

from spireagent.artifact_contracts import Manifest, Parent, Payload, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes, text
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


def _load_inputs(
    store: ArtifactStore, identity: str,
) -> LoadedTokenInputs | LoadedLightActionInputs:
    info = store.get_manifest(identity).parameters.value()
    if info.get("schema") in {LIGHT_ACTION_INPUT_SCHEMA, CANONICAL_LIGHT_ACTION_INPUT_SCHEMA}:
        return load_light_action_inputs(store, identity)
    return load_token_inputs(store, identity)


def _model_schema(config: Stage1aConfig, input_schema: str | None = None) -> str:
    if isinstance(config, LightActionM0Config):
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
    return info


def prepare_token_run(
    store: ArtifactStore, inputs: LoadedTokenInputs | LoadedLightActionInputs,
    config: Stage1aConfig, producer: Producer, *, replicate: str = "stage1a",
) -> Manifest:
    text(replicate, "run.replicate", maximum=128)
    input_info = inputs.manifest.parameters.value()
    if isinstance(config, LightActionM0Config):
        expected_family = ("train-only-byte-bpe" if recipe_for(config.recipe).backbone == "s"
                           else "pinned-qwen3")
        expected_input_schemas = {LIGHT_ACTION_INPUT_SCHEMA, CANONICAL_LIGHT_ACTION_INPUT_SCHEMA}
        canonical = input_info.get("schema") == CANONICAL_LIGHT_ACTION_INPUT_SCHEMA
        if (not isinstance(inputs, LoadedLightActionInputs)
                or input_info.get("schema") not in expected_input_schemas
                or input_info.get("graph") != recipe_for(config.recipe).graph
                or not isinstance(input_info.get("state_codec"), dict)
                or input_info["state_codec"].get("family") != expected_family
                or input_info.get("max_state_tokens") != config.max_state_tokens
                or input_info.get("max_action_bytes") != config.max_action_bytes):
            raise BoundaryError("token_run", "light_action_dual_input_or_codec_mismatch")
        if canonical and not isinstance(input_info.get("training_binding"), dict):
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
    run = Manifest(
        "run", producer, (Parent("training_input", inputs.manifest.artifact_id),
                          Parent("experiment", experiment.artifact_id)),
        parameters=FrozenObject.of({"schema": RUN_SCHEMA, "config": encoded_config,
                                    "replicate": replicate,
                                    "cpu_threads": torch.get_num_threads(),
                                    "torch_version": str(torch.__version__),
                                    **({"training_binding": training_binding}
                                       if training_binding is not None else {})}),
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
            or model.parameters.value().get("config") != config_payload(config)
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
    config = decode_config(info["config"])
    if stop_after is not None and (type(stop_after) is not int or stop_after < 1):
        raise BoundaryError("token_run", "invalid_pause_budget")
    inputs = _load_inputs(store, run.parent("training_input"))
    input_info = inputs.manifest.parameters.value()
    if (input_info.get("schema") == CANONICAL_LIGHT_ACTION_INPUT_SCHEMA
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
        checkpoint_info = _checkpoint_metadata(engine)
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
        if resume is not None:
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
        event("evaluating", model_id=model.artifact_id)
        # Human text-input rows are session-scoped; distinct recorded run IDs do
        # not establish independent native runs across those sessions.
        native_run_independence = view.parameters.value().get("schema") not in {
            "stpd/human-text-input-bc-view-v1", "stpd/human-text-input-bc-view-v2",
        }
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
