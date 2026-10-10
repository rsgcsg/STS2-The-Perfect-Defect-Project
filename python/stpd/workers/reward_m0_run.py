"""Reward-M0 typed artifacts using the existing stateless scorer and numerical helpers."""

from __future__ import annotations

import hashlib
import io
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.store import ArtifactStore

from ..canonical import semantic_hash
from ..fullrun.reward_m0_inputs import (
    LoadedRewardM0Inputs,
    load_reward_m0_inputs,
    publish_reward_m0_inputs,
)
from ..fullrun.reward_m0_source import VerifiedRewardM0Source, verify_reward_m0_source
from ..reward_m0_spec import (
    CHECKPOINT_SCHEMA,
    INPUT_SPECS,
    MODEL_SCHEMA,
    RECIPE,
    RESULT_SCHEMA,
    RUN_SCHEMA,
    RewardM0Config,
)
from ..stage1a_recipes import LIGHT_ACTION_M0_GRAPH

CODE_FILES = (
    "stpd/reward_m0_spec.py",
    "stpd/fullrun/reward_m0_source.py",
    "stpd/fullrun/reward_m0_inputs.py",
    "stpd/workers/reward_m0_run.py",
    "stpd/workers/token_ranking.py",
    "stpd/fullrun/light_action_inputs.py",
    "stpd/fullrun/token_format.py",
    "stpd/fullrun/public_inputs.py",
    "stpd/fullrun/public_compaction.py",
    "stpd/fullrun/semantic_projection.py",
    "stpd/fullrun/public_bc.py",
    "stpd/light_action_codec.py",
    "stpd/stage1a_recipes.py",
    "stpd/models/stage1a.py",
    "stpd/models/token_core.py",
    "stpd/models/light_action_encoder.py",
    "stpd/models/losses.py",
    "stpd/models/packed_actions.py",
    "stpd/workers/checkpoint_codec.py",
    "stpd/canonical.py",
    "spireagent/encoding.py",
    "spireagent/json_boundary.py",
    "spireagent/artifact_contracts.py",
)


def code_identity() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    rows = []
    for name in CODE_FILES:
        raw = (root / name).read_bytes()
        rows.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
    return {
        "schema": "stpd/reward-m0-code-identity-v1",
        "files": rows,
        "dependency_lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
    }


def prepare_reward_m0_run(
    store: ArtifactStore,
    source: VerifiedRewardM0Source,
    config: RewardM0Config,
    producer: Producer,
    operation_id: str,
) -> Manifest:
    inputs = publish_reward_m0_inputs(store, source, config, producer)
    run = Manifest(
        "run",
        producer,
        (Parent("training_input", inputs.artifact_id),),
        parameters=FrozenObject.of(
            {
                "schema": RUN_SCHEMA,
                "recipe": RECIPE,
                "operation_id": operation_id,
                "source_id": source.manifest.artifact_id,
                "arm": config.arm,
                "config": config.to_dict(),
                "graph": LIGHT_ACTION_M0_GRAPH,
                "input_spec": INPUT_SPECS[config.arm],
                "paired_limits": source.value["paired_limits"],
                "code_identity": code_identity(),
                "evaluation_scope": "TRAIN_internal_diagnostic_no_independent_heldout",
            }
        ),
    )
    store.publish(run)
    return run


def verify_reward_m0_run(store: ArtifactStore, run_id: str) -> tuple[Manifest, RewardM0Config]:
    run = store.get_manifest(run_id)
    info = run.parameters.value()
    if (
        run.kind != "run"
        or info.get("schema") != RUN_SCHEMA
        or info.get("recipe") != RECIPE
        or info.get("code_identity") != code_identity()
    ):
        raise BoundaryError("reward_m0_run", "run_code_or_contract_mismatch")
    config = RewardM0Config.decode(info["config"])
    inputs = store.get_manifest(run.parent("training_input"))
    if (
        inputs.producer != run.producer
        or inputs.parent("source") != info["source_id"]
        or info["arm"] != config.arm
        or info["input_spec"] != INPUT_SPECS[config.arm]
        or info["graph"] != LIGHT_ACTION_M0_GRAPH
    ):
        raise BoundaryError("reward_m0_run", "run_input_arm_binding_mismatch")
    return run, config


@dataclass(frozen=True)
class RewardM0Result:
    state: str
    run_id: str
    attempt_id: str
    checkpoint_id: str
    result_id: str | None
    model_id: str | None
    event_step: int
    optimizer_updates: int
    attempt_seconds: float


def _engine(inputs: LoadedRewardM0Inputs, config: RewardM0Config) -> Any:
    # Numerical imports remain below the common application admission/fence.
    import torch

    from .token_ranking import LightActionM0Config, TokenRankingEngine, construct_model

    class RewardM0Engine(TokenRankingEngine):
        """Only typed new-input setup differs; scorer/update/RNG/checkpoint code is inherited."""

        def __init__(self) -> None:
            if inputs.arm != config.arm or inputs.config != config:
                raise BoundaryError("reward_m0_run", "typed_input_config_mismatch")
            numerical = LightActionM0Config(
                **{
                    key: value
                    for key, value in asdict(config).items()
                    if key not in {"arm", "state_vocab_size", "checkpoint_every_boundaries"}
                }
            )
            self.inputs, self.config = inputs, numerical
            self.frozen, self.is_light_action_m0, self.adapter_tensor_names = False, True, None
            codec = inputs.manifest.parameters.value()["state_codec"]
            self.model, self.backbone = construct_model(
                numerical, codec["vocab_size"], state_codec=codec
            )
            self.parameters = [
                parameter for parameter in self.model.parameters() if parameter.requires_grad
            ]
            self.optimizer = torch.optim.AdamW(
                self.parameters, lr=config.learning_rate, weight_decay=config.weight_decay
            )
            train = list(range(len(inputs.samples)))
            self.plan = []
            for epoch in range((config.steps + len(train) - 1) // len(train)):
                order = list(train)
                random.Random(f"stage1a:{config.seed}:{epoch}").shuffle(order)
                self.plan.extend(order)
            self.plan = self.plan[: config.steps]
            self.data_identity = semantic_hash(
                {
                    "schema": "stpd/reward-m0-numerical-data-v1",
                    "input_id": inputs.manifest.artifact_id,
                    "config": config.to_dict(),
                    "plan": self.plan,
                    "backbone": self.backbone,
                }
            )
            self.step = 0

    return RewardM0Engine()


def execute_reward_m0_run(
    store: ArtifactStore,
    reporter: Any,
    run_id: str,
    operation_id: str,
    attempt_id: str,
    *,
    mode: str,
    authority: Any,
    control: Any,
    checkpoint_id: str | None = None,
) -> RewardM0Result:
    import torch

    torch.set_num_threads(2)
    run, config = verify_reward_m0_run(store, run_id)
    if run.parameters.value()["operation_id"] != operation_id:
        raise BoundaryError("reward_m0_run", "operation_binding_mismatch")
    source = verify_reward_m0_source(store, run.parameters.value()["source_id"])
    if source.value["paired_config"] != config.paired_schedule():
        raise BoundaryError("reward_m0_run", "paired_config_mismatch")
    inputs = load_reward_m0_inputs(store, run.parent("training_input"), config)
    engine = _engine(inputs, config)
    initial_sha = hashlib.sha256(engine.model_bytes()).hexdigest()
    started = time.monotonic()
    event_step = 0
    for old in reporter.events(run_id):
        event_step = max(event_step, old.parameters.value().get("step", 0))
    authority.assert_current(run_id, operation_id, attempt_id)

    def event(kind: str, **details: Any) -> None:
        nonlocal event_step
        authority.assert_current(run_id, operation_id, attempt_id)
        event_step += 1
        info = {
            "schema": "stpd/run-event-v1",
            "kind": kind,
            "attempt": attempt_id,
            "step": event_step,
            "details": {"optimizer_updates": engine.step, **details},
        }
        reporter.emit(
            Manifest(
                "run_event",
                run.producer,
                (Parent("run", run_id),),
                parameters=FrozenObject.of(info),
            )
        )

    last_checkpoint = checkpoint_id

    def checkpoint() -> str:
        authority.assert_current(run_id, operation_id, attempt_id)
        payload = store.put_payload(
            "checkpoint", io.BytesIO(engine.checkpoint()), "application/vnd.stpd.tensor-tree"
        )
        saved = Manifest(
            "checkpoint",
            run.producer,
            (Parent("run", run_id), Parent("training_input", inputs.manifest.artifact_id)),
            (payload,),
            FrozenObject.of(
                {
                    "schema": CHECKPOINT_SCHEMA,
                    "attempt": attempt_id,
                    "arm": config.arm,
                    "source_id": source.manifest.artifact_id,
                    "input_spec": INPUT_SPECS[config.arm],
                    "optimizer_updates": engine.step,
                    "data_identity": engine.data_identity,
                    "initial_weights_sha256": initial_sha,
                    "paired_plan": engine.plan,
                }
            ),
        )
        store.publish(saved)
        event("checkpoint", checkpoint_id=saved.artifact_id)
        return saved.artifact_id

    if mode == "resume":
        if checkpoint_id is None:
            raise BoundaryError("reward_m0_run", "resume_checkpoint_required")
        authority.authorize_resume(run_id, attempt_id, checkpoint_id)
        saved = store.get_manifest(checkpoint_id)
        if (
            saved.kind != "checkpoint"
            or saved.producer != run.producer
            or saved.parent("run") != run_id
            or saved.parent("training_input") != inputs.manifest.artifact_id
            or saved.parameters.value().get("schema") != CHECKPOINT_SCHEMA
            or saved.parameters.value().get("arm") != config.arm
            or saved.parameters.value().get("data_identity") != engine.data_identity
        ):
            raise BoundaryError("reward_m0_run", "resume_arm_or_input_mismatch")
        engine.restore(b"".join(store.read_payload(saved.payload("checkpoint"))))
        if engine.step != saved.parameters.value()["optimizer_updates"]:
            raise BoundaryError("reward_m0_run", "resume_update_mismatch")
    elif mode != "start":
        # Reconciliation validates completion; it cannot launch another optimizer.
        return verify_reward_m0_completed(store, reporter, run_id, attempt_id, started)
    event(
        "started" if mode == "start" else "resumed",
        initial_weights_sha256=initial_sha,
        paired_plan=engine.plan,
        numerical_device=config.device,
        cpu_threads=torch.get_num_threads(),
        state_tokenizer_sha256=(inputs.manifest.payload("state_tokenizer").sha256),
    )
    losses = []
    while engine.step < config.steps:
        action = control.requested_action()
        if action != "continue":
            last_checkpoint = checkpoint()
            state = "paused" if action == "pause" else "cancelled"
            event(state, checkpoint_id=last_checkpoint)
            return RewardM0Result(
                state,
                run_id,
                attempt_id,
                last_checkpoint,
                None,
                None,
                event_step,
                engine.step,
                time.monotonic() - started,
            )
        loss = engine.advance()
        losses.append(loss)
        event("update", loss=loss)
        if engine.step % config.checkpoint_every_boundaries == 0:
            last_checkpoint = checkpoint()
    if (
        last_checkpoint is None
        or store.get_manifest(last_checkpoint).parameters.value()["optimizer_updates"]
        != engine.step
    ):
        last_checkpoint = checkpoint()
    authority.assert_current(run_id, operation_id, attempt_id)
    weights = store.put_payload(
        "weights", io.BytesIO(engine.model_bytes()), "application/vnd.safetensors"
    )
    model = Manifest(
        "model",
        run.producer,
        (
            Parent("run", run_id),
            Parent("checkpoint", last_checkpoint),
            Parent("training_input", inputs.manifest.artifact_id),
            Parent("source", source.manifest.artifact_id),
        ),
        (
            weights,
            inputs.manifest.payload("state_tokenizer"),
            inputs.manifest.payload("action_codec"),
        ),
        FrozenObject.of(
            {
                "schema": MODEL_SCHEMA,
                "recipe": RECIPE,
                "graph": LIGHT_ACTION_M0_GRAPH,
                "arm": config.arm,
                "input_spec": INPUT_SPECS[config.arm],
                "config": config.to_dict(),
                "backbone": engine.backbone,
                "weights_sha256": weights.sha256,
                "initial_weights_sha256": initial_sha,
                "optimizer_updates": engine.step,
                "paired_plan": engine.plan,
                "source_id": source.manifest.artifact_id,
                "paired_feature_id": inputs.manifest.parent("paired_features"),
                "qualification": "TRAIN_internal_engineering_only",
                "dtype": "float32",
                "runtime_integration": "separate_online_InputSpec_Read_adapter_required",
            }
        ),
    )
    store.publish(model)
    diagnostics = []
    for index, sample in enumerate(inputs.samples):
        values = engine.scores(index)
        tensor = torch.tensor(values, dtype=torch.float64)
        nll = float(torch.logsumexp(tensor, 0) - tensor[sample.chosen_index])
        winner = max(range(len(values)), key=lambda i: (values[i], -i))
        diagnostics.append(
            {
                "occurrence_id": source.value["rows"][index]["occurrence_id"],
                "surface": sample.surface,
                "NLL": nll,
                "top1_correct": winner == sample.chosen_index,
                "candidate_count": len(values),
            }
        )
    metrics = store.put_payload(
        "metrics",
        io.BytesIO(
            json_bytes(
                {
                    "schema": RESULT_SCHEMA,
                    "evaluation_scope": "TRAIN_internal_diagnostic_no_independent_heldout",
                    "arm": config.arm,
                    "rows": diagnostics,
                    "mean_NLL": sum(row["NLL"] for row in diagnostics) / len(diagnostics),
                    "top1_correct": sum(row["top1_correct"] for row in diagnostics),
                    "eligible_N": len(diagnostics),
                    "update_losses": losses,
                }
            )
        ),
        "application/json",
    )
    result = Manifest(
        "run_result",
        run.producer,
        (
            Parent("run", run_id),
            Parent("model", model.artifact_id),
            Parent("checkpoint", last_checkpoint),
            Parent("training_input", inputs.manifest.artifact_id),
        ),
        (metrics,),
        FrozenObject.of(
            {
                "schema": "stpd/run-result-v1",
                "domain_schema": RESULT_SCHEMA,
                "state": "completed",
                "arm": config.arm,
                "optimizer_updates": engine.step,
                "attempt": attempt_id,
                "initial_weights_sha256": initial_sha,
                "source_id": source.manifest.artifact_id,
                "heldout_or_Gold": False,
            }
        ),
    )
    result_id = reporter.complete(result)
    event("completed", result_id=result_id, model_id=model.artifact_id)
    return RewardM0Result(
        "completed",
        run_id,
        attempt_id,
        last_checkpoint,
        result_id,
        model.artifact_id,
        event_step,
        engine.step,
        time.monotonic() - started,
    )


def verify_reward_m0_completed(
    store: ArtifactStore, reporter: Any, run_id: str, attempt_id: str, started: float | None = None
) -> RewardM0Result:
    run, config = verify_reward_m0_run(store, run_id)
    result = reporter.completed(run_id)
    if result is None:
        raise BoundaryError("reward_m0_run", "completed_result_required_for_reconcile")
    result_id = result.artifact_id
    model = store.get_manifest(result.parent("model"))
    checkpoint = store.get_manifest(result.parent("checkpoint"))
    if (
        result.kind != "run_result"
        or result.producer != run.producer
        or result.parent("run") != run_id
        or result.parameters.value().get("schema") != "stpd/run-result-v1"
        or result.parameters.value().get("domain_schema") != RESULT_SCHEMA
        or result.parameters.value().get("optimizer_updates") != config.steps
        or model.kind != "model"
        or model.producer != run.producer
        or model.parameters.value().get("schema") != MODEL_SCHEMA
        or model.parameters.value().get("arm") != config.arm
        or model.parameters.value().get("config") != config.to_dict()
        or model.parameters.value().get("weights_sha256") != model.payload("weights").sha256
        or model.parent("run") != run_id
        or model.parent("training_input") != run.parent("training_input")
        or model.parent("checkpoint") != checkpoint.artifact_id
        or checkpoint.kind != "checkpoint"
        or checkpoint.producer != run.producer
        or checkpoint.parent("run") != run_id
        or checkpoint.parameters.value().get("schema") != CHECKPOINT_SCHEMA
        or checkpoint.parameters.value().get("optimizer_updates") != config.steps
    ):
        raise BoundaryError("reward_m0_run", "completed_model_lineage_mismatch")
    for payload in (*model.payloads, *result.payloads, *checkpoint.payloads):
        for _ in store.read_payload(payload):
            pass
    # The ordinary Model must contain the exact final checkpoint parameters,
    # not just self-consistent manifest hashes or a matching step counter.
    from safetensors.torch import save

    from .checkpoint_codec import decode_checkpoint

    state = decode_checkpoint(b"".join(store.read_payload(checkpoint.payload("checkpoint"))))
    if (
        state.get("step") != config.steps
        or state.get("data_identity") != checkpoint.parameters.value()["data_identity"]
        or save(state["model"]) != b"".join(store.read_payload(model.payload("weights")))
        or model.parameters.value()["initial_weights_sha256"]
        != (checkpoint.parameters.value()["initial_weights_sha256"])
        or model.parameters.value()["paired_plan"] != checkpoint.parameters.value()["paired_plan"]
    ):
        raise BoundaryError("reward_m0_run", "completed_checkpoint_weights_mismatch")
    event_step = max(
        (event.parameters.value().get("step", 0) for event in reporter.events(run_id)), default=0
    )
    return RewardM0Result(
        "completed",
        run_id,
        attempt_id,
        checkpoint.artifact_id,
        result_id,
        model.artifact_id,
        event_step,
        config.steps,
        time.monotonic() - started if started is not None else 0.0,
    )
