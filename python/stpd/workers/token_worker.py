"""Token execution capability using the existing store, reporter and evaluation owners."""
from __future__ import annotations

import io
import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes, text
from spireagent.storage.store import ArtifactStore, ManifestArtifactStore

from ..fullrun.dataset_policy import token_dev_qualification
from ..fullrun.evaluation import action_only_prior, evaluate_samples
from ..fullrun.light_action_inputs import (
    CANONICAL_SCHEMA as CANONICAL_LIGHT_ACTION_INPUT_SCHEMA,
)
from ..fullrun.light_action_inputs import (
    PUBLIC_SCHEMA as PUBLIC_LIGHT_ACTION_INPUT_SCHEMA,
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
    TokenTargetRuntime,
    config_payload,
    decode_config,
    require_current_token_runtime,
)
from .worker import WorkerResult

RUN_SCHEMA = "stpd/stage1a-run-v1"
MODEL_SCHEMA = "stpd/stage1a-model-v1"
LIGHT_ACTION_M0_MODEL_SCHEMA = "stpd/stage1a-light-action-m0-model-v1"
CANONICAL_LIGHT_ACTION_M0_MODEL_SCHEMA = "stpd/stage1a-light-action-m0-canonical-model-v1"
PUBLIC_LIGHT_ACTION_M0_MODEL_SCHEMA = "stpd/stage1a-light-action-m0-public-model-v1"


def _load_inputs(
    store: ArtifactStore, identity: str,
) -> LoadedTokenInputs | LoadedLightActionInputs:
    info = store.get_manifest(identity).parameters.value()
    if info.get("schema") in {
        LIGHT_ACTION_INPUT_SCHEMA, CANONICAL_LIGHT_ACTION_INPUT_SCHEMA,
        PUBLIC_LIGHT_ACTION_INPUT_SCHEMA,
    }:
        return load_light_action_inputs(store, identity)
    return load_token_inputs(store, identity)


def _model_schema(config: Stage1aConfig, input_schema: str | None = None) -> str:
    if isinstance(config, LightActionM0Config):
        return (CANONICAL_LIGHT_ACTION_M0_MODEL_SCHEMA
                if input_schema == CANONICAL_LIGHT_ACTION_INPUT_SCHEMA
                else PUBLIC_LIGHT_ACTION_M0_MODEL_SCHEMA
                if input_schema == PUBLIC_LIGHT_ACTION_INPUT_SCHEMA
                else LIGHT_ACTION_M0_MODEL_SCHEMA)
    return MODEL_SCHEMA


def _checkpoint_metadata_for(
    config: Stage1aConfig,
    input_manifest: Manifest,
    step: int,
    data_identity: str,
    backbone: dict[str, object],
    adapter_tensor_names: set[str] | None,
) -> dict[str, object]:
    info: dict[str, object] = {
        "schema": (LIGHT_ACTION_M0_CHECKPOINT_SCHEMA if isinstance(config, LightActionM0Config)
                   else CHECKPOINT_SCHEMA),
        "step": step, "data_identity": data_identity,
    }
    if isinstance(config, LightActionM0Config):
        source = input_manifest.parameters.value()
        info.update({
            "recipe": config.recipe,
            "graph": recipe_for(config.recipe).graph,
            "state_codec": source["state_codec"],
            "action_codec": source["action_codec"],
            "backbone": backbone,
            "adapter_config": backbone.get("adapter_config"),
            "adapter_tensor_names": sorted(adapter_tensor_names or set()),
            "input_schema": source["schema"], "input_format": source["format"],
            "source_view_schema": source["source_schema"],
            "source_renderer": source["source_renderer"],
        })
        if "training_binding" in source:
            info["training_binding"] = source["training_binding"]
    return info


def _checkpoint_metadata(engine: TokenRankingEngine) -> dict[str, object]:
    return _checkpoint_metadata_for(
        engine.config, engine.inputs.manifest, engine.step, engine.data_identity,
        engine.backbone, engine.adapter_tensor_names,
    )


def prepare_token_run(
    store: ArtifactStore, inputs: LoadedTokenInputs | LoadedLightActionInputs,
    config: Stage1aConfig, producer: Producer, *, replicate: str = "stage1a",
    target_runtime: TokenTargetRuntime | None = None,
) -> Manifest:
    """Publish a run; explicit target metadata is recorded, not provider-authenticated.

    Callers must source a non-default target from their already-bound image/provider.
    Omitting it preserves the historical local runtime declaration.
    """
    if target_runtime is None:
        target_runtime = TokenTargetRuntime.current()
    elif not isinstance(target_runtime, TokenTargetRuntime):
        raise BoundaryError("token_run", "invalid_target_runtime")
    text(replicate, "run.replicate", maximum=128)
    input_info = inputs.manifest.parameters.value()
    if isinstance(config, LightActionM0Config):
        expected_family = ("train-only-byte-bpe" if recipe_for(config.recipe).backbone == "s"
                           else "pinned-qwen3")
        expected_input_schemas = {LIGHT_ACTION_INPUT_SCHEMA,
                                  CANONICAL_LIGHT_ACTION_INPUT_SCHEMA,
                                  PUBLIC_LIGHT_ACTION_INPUT_SCHEMA}
        canonical = input_info.get("schema") == CANONICAL_LIGHT_ACTION_INPUT_SCHEMA
        public = input_info.get("schema") == PUBLIC_LIGHT_ACTION_INPUT_SCHEMA
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
        if public and (not isinstance(input_info.get("training_binding"), dict)
                       or config.public_profile not in {"public_lite", "public_compact"}
                       or input_info.get("source_renderer", {}).get("profile")
                       != config.public_profile):
            raise BoundaryError("token_run", "public_training_binding_or_profile_required")
        if not public and config.public_profile is not None:
            raise BoundaryError("token_run", "public_profile_source_mismatch")
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
                                    "cpu_threads": target_runtime.cpu_threads,
                                    "torch_version": target_runtime.torch_version,
                                    **({"training_binding": training_binding}
                                       if training_binding is not None else {})}),
    )
    store.publish(run)
    return run


def _verify_completed(
    store: ArtifactStore, result: Manifest, run: Manifest, *, verify_payloads: bool = True,
) -> None:
    """Do not silently replace a damaged completion with a fresh optimization attempt."""
    if (result.kind != "run_result" or result.producer != run.producer
            or result.parameters.value().get("state") != "completed"
            or result.parameters.value().get("steps") != run.parameters.value()["config"]["steps"]
            or sorted(p.role for p in result.parents)
            != ["model", "offline_evaluation", "run", "training_input"]
            or result.parent("run") != run.artifact_id
            or result.parent("training_input") != run.parent("training_input")):
        raise BoundaryError("token_run", "completed_identity_mismatch")
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
        if source_info.get("schema") in {
            CANONICAL_LIGHT_ACTION_INPUT_SCHEMA, PUBLIC_LIGHT_ACTION_INPUT_SCHEMA,
        }:
            expected_eval = {
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
            if verify_payloads:
                metrics = json.loads(b"".join(store.read_payload(payload)))
                summaries = [metrics.get("summary", {}).get("bootstrap")]
                summaries.extend(item.get("bootstrap") for item in
                                 metrics.get("baselines", {}).values())
                if any(not isinstance(item, dict) or item.get("status") != "unknown"
                       or item.get("unit") != "session_scoped_run_group"
                       for item in summaries):
                    raise BoundaryError("token_run", "canonical_dev_independence_claim")
                if metrics.get("admission") != expected_eval:
                    raise BoundaryError("token_run", "canonical_dev_qualification_mismatch")
    # Validate every manifest-known completion edge before consuming payloads.
    if not verify_payloads:
        return
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


def _m0_completion_pair(
    store: ManifestArtifactStore, model: Manifest,
) -> tuple[Manifest, Manifest]:
    from spireagent.storage.run_reporter import ObjectStoreRunReporter

    if (model.kind != "model" or model.parameters.value().get("schema") not in {
            CANONICAL_LIGHT_ACTION_M0_MODEL_SCHEMA, PUBLIC_LIGHT_ACTION_M0_MODEL_SCHEMA,
    }):
        raise BoundaryError("light_action_m0", "canonical_model_required")
    run = store.get_manifest(model.parent("run"))
    completed = ObjectStoreRunReporter(store, store.blobs).completed(run.artifact_id)
    if completed is None or completed.parent("model") != model.artifact_id:
        raise BoundaryError("light_action_m0", "completed_model_required")
    return completed, run


def preflight_m0_model_completion(store: ManifestArtifactStore, model: Manifest) -> Manifest:
    """Validate completed model lineage from manifests only, without reading payloads."""
    completed, run = _m0_completion_pair(store, model)
    _verify_completed(store, completed, run, verify_payloads=False)
    return completed


def verify_m0_model_completion(store: ManifestArtifactStore, model: Manifest) -> Manifest:
    """Validate the exact completed M0 model and then verify its referenced bytes."""
    completed, run = _m0_completion_pair(store, model)
    _verify_completed(store, completed, run)
    return completed


def preflight_token_run_contract(
    store: ArtifactStore, run_id: str, runtime: Producer, *, resume: str | None = None,
) -> tuple[Manifest, Stage1aConfig, Manifest, bool]:
    """Check run contract and lineage without comparing against the executing runtime."""
    run = store.get_manifest(run_id)
    info = run.parameters.value()
    if (run.kind != "run" or run.producer != runtime or info.get("schema") != RUN_SCHEMA
            or sorted(p.role for p in run.parents) != ["experiment", "training_input"]):
        raise BoundaryError("token_run", "source_or_contract_mismatch")
    try:
        TokenTargetRuntime.from_run_info(info)
    except BoundaryError as error:
        raise BoundaryError("token_run", "source_or_contract_mismatch") from error
    config = decode_config(info["config"])
    input_manifest = store.get_manifest(run.parent("training_input"))
    input_info = input_manifest.parameters.value()
    admitted_m0 = input_info.get("schema") in {
        CANONICAL_LIGHT_ACTION_INPUT_SCHEMA, PUBLIC_LIGHT_ACTION_INPUT_SCHEMA,
    }
    if admitted_m0:
        from stpd.fullrun.light_action_inputs import (
            canonical_training_binding,
            public_training_binding,
        )

        binding = (public_training_binding(store, input_manifest.artifact_id)
                   if input_info.get("schema") == PUBLIC_LIGHT_ACTION_INPUT_SCHEMA
                   else canonical_training_binding(store, input_manifest.artifact_id))
        if info.get("training_binding") != binding:
            raise BoundaryError("token_run", "training_binding_mismatch")
    experiment = store.get_manifest(run.parent("experiment"))
    if (experiment.kind != "experiment" or experiment.producer != runtime
            or experiment.parameters.value().get("schema") != "stpd/experiment-v1"
            or experiment.parent("training_input") != input_manifest.artifact_id
            or experiment.parameters.value().get("config") != config_payload(config)
            or experiment.parameters.value().get("training_binding")
            != info.get("training_binding")):
        raise BoundaryError("token_run", "experiment_mismatch")
    if resume is not None:
        resume_manifest = store.get_manifest(resume)
        resume_info = resume_manifest.parameters.value()
        expected_schema = (LIGHT_ACTION_M0_CHECKPOINT_SCHEMA
                           if isinstance(config, LightActionM0Config)
                           else CHECKPOINT_SCHEMA)
        expected_checkpoint_identity = {}
        if isinstance(config, LightActionM0Config):
            expected_checkpoint_identity = {
                "recipe": config.recipe,
                "graph": recipe_for(config.recipe).graph,
                "state_codec": input_info.get("state_codec"),
                "action_codec": input_info.get("action_codec"),
                "input_schema": input_info.get("schema"),
                "input_format": input_info.get("format"),
                "source_view_schema": input_info.get("source_schema"),
                "source_renderer": input_info.get("source_renderer"),
                "training_binding": input_info.get("training_binding"),
            }
        if (resume_manifest.kind != "checkpoint" or resume_manifest.producer != runtime
                or resume_info.get("schema") != expected_schema
                or sorted(p.role for p in resume_manifest.parents)
                != ["run", "training_input"]
                or resume_manifest.parent("run") != run_id
                or resume_manifest.parent("training_input") != input_manifest.artifact_id
                or any(resume_info.get(key) != value
                       for key, value in expected_checkpoint_identity.items())
                or [payload.role for payload in resume_manifest.payloads] != ["checkpoint"]
                or resume_manifest.payload("checkpoint").size > 512 * 1024**2
                or type(resume_info.get("step")) is not int
                or not 0 <= resume_info["step"] <= config.steps):
            raise BoundaryError("token_run", "resume_identity_mismatch")
    return run, config, input_manifest, admitted_m0


def preflight_token_run(
    store: ArtifactStore, run_id: str, runtime: Producer, *, resume: str | None = None,
) -> tuple[Manifest, Stage1aConfig, Manifest, bool]:
    """Preflight lineage and require the current executor to match the declared runtime."""
    result = preflight_token_run_contract(store, run_id, runtime, resume=resume)
    run = result[0]
    target_runtime = TokenTargetRuntime.from_run_info(run.parameters.value())
    require_current_token_runtime(
        target_runtime, "token_run", "source_or_contract_mismatch",
    )
    return result


def _finalize_token_run(
    store: ArtifactStore,
    reporter: RunReporter,
    run: Manifest,
    inputs: LoadedTokenInputs | LoadedLightActionInputs,
    config: Stage1aConfig,
    runtime: Producer,
    checkpoint_id: str,
    started: float,
    step: int,
    scores: Callable[[int], tuple[float, ...]],
    model_bytes: bytes,
    backbone: dict[str, object],
    adapter_tensor_names: set[str] | None,
    event: Callable[..., None],
    dev_admitter: Callable[[Manifest, Manifest], dict] | None,
    *,
    evaluation_runtime: dict[str, object] | None = None,
) -> WorkerResult:
    """Publish the existing M0 model/dev/completion chain for validated final weights."""
    run_id = run.artifact_id
    input_info = inputs.manifest.parameters.value()
    weights = store.put_payload(
        "weights", io.BytesIO(model_bytes), "application/vnd.safetensors",
    )
    view = store.get_manifest(inputs.manifest.parent("model_view"))
    parents = (Parent("run", run_id), Parent("checkpoint", checkpoint_id),
               Parent("training_input", inputs.manifest.artifact_id),
               Parent("model_view", view.artifact_id))
    if isinstance(config, LightActionM0Config):
        if not isinstance(inputs, LoadedLightActionInputs):
            raise BoundaryError("token_run", "light_action_inputs_required")
        model_payloads = (weights, inputs.manifest.payload("state_tokenizer"),
                          inputs.manifest.payload("action_codec"))
        model_info: dict[str, object] = {
            "schema": _model_schema(config, input_info["schema"]),
            "config": config_payload(config), "recipe": config.recipe,
            "graph": recipe_for(config.recipe).graph, "backbone": backbone,
            "state_codec": input_info["state_codec"],
            "action_codec": input_info["action_codec"],
            "adapter_config": backbone.get("adapter_config"),
            "adapter_tensor_names": sorted(adapter_tensor_names or set()),
            "state_tokenizer_sha256": input_info["state_codec"]["sha256"],
            "action_codec_sha256": input_info["action_codec"]["sha256"],
            "weights_sha256": weights.sha256,
            "source_view_schema": view.parameters.value().get("schema"),
            "source_renderer": input_info["source_renderer"],
            "input_schema": input_info["schema"], "input_format": input_info["format"],
            "steps": step, "qualification": "engineering_only", "dtype": "float32",
            "runtime_integration": "not_included_in_light_action_m0_slice",
        }
        if "training_binding" in input_info:
            model_info["training_binding"] = input_info["training_binding"]
        model_payloads = tuple(model_payloads)
    else:
        if not isinstance(inputs, LoadedTokenInputs):
            raise BoundaryError("token_run", "token_inputs_required")
        model_payloads = (weights, inputs.manifest.payload("tokenizer"))
        model_info = {
            "schema": MODEL_SCHEMA, "config": config_payload(config),
            "graph": recipe_for(config.recipe).graph, "backbone": backbone,
            "vocab_size": inputs.manifest.parameters.value()["vocab_size"],
            "serializer": view.parameters.value()["serializer"],
            "input_format": FORMAT, "steps": step,
            "qualification": "engineering_only", "dtype": "float32",
        }
    model = Manifest("model", runtime, parents, model_payloads,
                     FrozenObject.of(model_info))
    store.publish(model)
    admitted = input_info.get("schema") in {
        CANONICAL_LIGHT_ACTION_INPUT_SCHEMA, PUBLIC_LIGHT_ACTION_INPUT_SCHEMA,
    }
    dev_admission = None
    if admitted:
        if dev_admitter is None:
            raise BoundaryError("token_run", "canonical_dev_admission_required")
        dev_admission = dev_admitter(model, view)
        if (not isinstance(dev_admission, dict)
                or dev_admission.get("evaluation_scope")
                != "within_training_purpose_allocation"
                or dev_admission.get("physical_game_independence") != "unresolved"
                or dev_admission.get("clean_held_out_claim") is not False):
            raise BoundaryError("token_run", "canonical_dev_admission_invalid")
    event("evaluating", model_id=model.artifact_id)
    qualification = token_dev_qualification(store, view, admission=dev_admission)
    native_run_independence = qualification["native_run_independence"]
    rows, summary = evaluate_samples(
        inputs.samples, scores, seed=config.seed,
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
    evaluation_info: dict[str, object] = {
        "schema": EVALUATION_SCHEMA, "partition": "dev",
        "qualification": "engineering_only",
        "native_run_independence": native_run_independence,
    }
    if evaluation_runtime is not None:
        evaluation_info["scoring_runtime"] = evaluation_runtime
    restrictions = {key: qualification[key] for key in (
        "evaluation_scope", "historical_external_exposure",
        "physical_game_independence", "clean_held_out_claim",
    )}
    metric_value["admission"] = restrictions
    metric_value["qualification_evidence"] = qualification["evidence"]
    evaluation_info.update(restrictions)
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
                                    "steps": step,
                                    "attempt_seconds": time.perf_counter() - started}),
    )
    result_id = reporter.complete(result)
    event("completed", result_id=result_id)
    return WorkerResult("completed", run_id, checkpoint_id, result_id)


def execute_tokens(store: ArtifactStore, reporter: RunReporter, run_id: str, runtime: Producer,
                   *, snapshot: Path | None = None, resume: str | None = None,
                   stop_after: int | None = None,
                   checkpoint_interval: int = 1,
                   dev_admitter: Callable[[Manifest, Manifest], dict] | None = None,
                   ) -> WorkerResult:
    run, config, input_manifest, admitted_m0 = preflight_token_run(
        store, run_id, runtime, resume=resume,
    )
    info = run.parameters.value()
    if stop_after is not None and (type(stop_after) is not int or stop_after < 1):
        raise BoundaryError("token_run", "invalid_pause_budget")
    if type(checkpoint_interval) is not int or not 1 <= checkpoint_interval <= 100000:
        raise BoundaryError("token_run", "invalid_checkpoint_interval")
    previous = reporter.completed(run_id)
    if previous is not None:
        _verify_completed(store, previous, run)
        return WorkerResult("completed", run_id, result_id=previous.artifact_id)
    if resume is None and reporter.events(run_id):
        raise BoundaryError("token_run", "existing_attempt_requires_explicit_resume")
    inputs = _load_inputs(store, input_manifest.artifact_id)
    input_info = inputs.manifest.parameters.value()
    if (admitted_m0 and info.get("training_binding") != input_info.get("training_binding")):
        raise BoundaryError("token_run", "training_binding_mismatch")
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
            # Preserve the last known-good checkpoint if the first resumed save fails.
            checkpoint_id = resume
        initial_step = engine.step
        event("resumed" if resume else "started", **(
            {"checkpoint_interval": checkpoint_interval} if checkpoint_interval > 1 else {}
        ))
        while engine.step < config.steps:
            step_started = time.perf_counter()
            loss = engine.advance()
            seconds = time.perf_counter() - step_started
            event("step", loss=loss, seconds=seconds)
            print(json_bytes({"run_id": run_id, "step": engine.step, "total_steps": config.steps,
                              "loss": loss, "seconds": seconds}).decode(), flush=True)
            # Retain every update's loss curve. Checkpoint only at the configured global step
            # boundary, while forcing pause and final checkpoints so those outcomes stay usable.
            should_pause = (stop_after is not None
                           and engine.step - initial_step >= stop_after
                           and engine.step < config.steps)
            if (engine.step % checkpoint_interval == 0 or should_pause
                    or engine.step == config.steps):
                checkpoint_id = checkpoint()
            if should_pause:
                assert checkpoint_id is not None
                event("paused", checkpoint_id=checkpoint_id)
                return WorkerResult("paused", run_id, checkpoint_id=checkpoint_id)
        if checkpoint_id is None:
            checkpoint_id = checkpoint()
        return _finalize_token_run(
            store, reporter, run, inputs, config, runtime, checkpoint_id, started,
            engine.step, engine.scores, engine.model_bytes(), engine.backbone,
            engine.adapter_tensor_names, event, dev_admitter,
        )
    except Exception as error:
        event("failed", error_type=type(error).__name__, last_checkpoint=checkpoint_id)
        raise
