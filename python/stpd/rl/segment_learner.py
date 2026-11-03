"""Short-segment replacement for the existing RL learner's live training edge.

The application supplies the same Store model publisher and the new trajectory
reader. This owner freezes datasets/targets, shares one GPU between theta SGD
and immutable mu inference, and checkpoints through the existing typed codec.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import torch

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.rl_exchange import Exchange
from spireagent.rl_tensorboard import TensorBoardMetrics
from spireagent.source import source_identity
from spireagent.storage.blobs import StoreError
from stpd.p1.export import (
    action_weights,
    adapter_identity,
    export_model,
    load_model,
    runtime_projection,
)
from stpd.p1.input import project
from stpd.p1.local_inference import SegmentInferencePool
from stpd.p1.model import MStd
from stpd.p1.tensor_input import compile_frame
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from stpd.workers.training_io import CheckpointPublisher, FrozenCheckpoint

from .checkpoint import metadata_tensor
from .critic import held_out
from .learner import ROOT, LearnerConfig, _device
from .loss import AuxiliarySample, SegmentSample, segment_update
from .performance import PerformanceConfig
from .reward import REWARD_SPEC
from .segment_targets import freeze_targets, samples_from_frozen
from .segments import AuxiliaryPool, SegmentQueue, minibatch_members

CHECKPOINT_SCHEMA = "stpd/rl-segment-checkpoint-1"


@dataclass(frozen=True)
class SegmentLearnerIO:
    read_segment: Callable[..., dict[str, Any]]
    publish_model: Callable[..., Any]


class SegmentLearner:
    def __init__(self, config: LearnerConfig, work: Path, io: SegmentLearnerIO) -> None:
        if config.segments is None or config.anchor is not None or config.critic is not None:
            raise BoundaryError("rl_learner", "explicit_segment_config_without_legacy_objectives")
        if config.loss.mode != "ppo" or config.loss.value_coef != 0.2:
            raise BoundaryError("rl_learner", "explicit_RL4_PPO_loss_required")
        self.config, self.settings, self.work, self.io = config, config.segments, work, io
        work.mkdir(parents=True, exist_ok=True)
        self.producer = source_identity(ROOT)
        self.exchange = Exchange(config.peer, config.run)
        if self.exchange.stop_request() is not None:
            raise BoundaryError("rl_learner", "cannot_resume_a_permanently_stopped_run")
        self.device = _device(config.device)
        torch.set_num_threads(config.threads)
        torch.manual_seed(config.seed)
        self.rng = random.Random(config.seed)
        self.queue = SegmentQueue(self.settings)
        self.condition = self.queue.condition
        self.gpu_lock = threading.RLock()
        self.terminal = AuxiliaryPool(self.settings.terminal_games, self.settings.terminal_uses)
        self.hp = AuxiliaryPool(self.settings.hp_battles, self.settings.hp_uses)
        self.lease_owners: dict[str, dict[str, Any]] = {}
        self.actor_episodes: dict[str, str] = {}
        self.released_closed_reservations: list[str] = []
        self.newlease_stopped = False
        self.active: dict[str, Any] | None = None
        self.active_rows: list[SegmentSample] | None = None
        self.active_auxiliary: tuple[list[AuxiliarySample], list[AuxiliarySample]] | None = None
        self.row_metadata: list[dict[str, Any]] = []
        self.updates = self.datasets = self.version = self.checkpoint_sequence = 0
        self.published_updates = 0
        self.first_use = self.first_use_multi = 0
        self.performance = config.performance or PerformanceConfig()
        self.model = self._start_model()
        if not hasattr(self.model, "rl4_heads") or self.model.value_heads:
            raise BoundaryError("rl_learner", "warmed_RL4_heads_required")
        self.model.to(self.device).eval()
        for parameter in self.model.parameters():
            parameter.requires_grad_(True)
        self.optimizer = torch.optim.AdamW(
            [
                {
                    "params": [
                        p
                        for name, p in self.model.named_parameters()
                        if not name.startswith("rl4_heads.")
                    ],
                    "lr": config.lr,
                },
                {"params": list(self.model.rl4_heads.parameters()), "lr": self.settings.head_lr},
            ],
            weight_decay=config.weight_decay,
        )
        self.ema = {name: value.detach().clone() for name, value in self.model.state_dict().items()}
        self.publisher = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rl-model-publication"
        )
        self.pending_publication: Future | None = None
        self.telemetry: deque[Future] = deque()
        self.metrics = (
            TensorBoardMetrics(
                self.exchange,
                work / "tb",
                value_description="RL4 categorical TD with observed terminal/HP auxiliaries",
            )
            if config.tensorboard_steps
            else None
        )
        self.pool: SegmentInferencePool
        self.restored_mu: list[dict[str, Any]] = []
        self.checkpoints = CheckpointPublisher(self.exchange.store, self.producer, background=True)
        restored_rng = self._restore(config.resume_checkpoint) if config.resume_checkpoint else None
        if config.resume_checkpoint and not config.republish_on_resume:
            identity = max(self.restored_mu, key=lambda value: value["version"])
            frozen = self._load_mu(identity)
            initial = {
                "identity": identity,
                "model": frozen.eval(),
                "updates": self.published_updates,
            }
        else:
            if config.resume_checkpoint:
                self.version += 1
            initial = self._publish_model(self._capture_model(), self.version, self.updates)
        self._start_pool(initial)
        if restored_rng is not None:
            # Materializing immutable mu models initializes temporary modules.
            # Restore the saved RNG only after those constructors have finished.
            torch.set_rng_state(restored_rng[0])
            if self.device.type == "cuda":
                torch.cuda.set_rng_state_all(restored_rng[1])

    def _start_model(self) -> MStd:
        if self.config.start_package:
            package = Path(self.config.start_package)
        elif self.config.start_artifact_id and self.config.start_model_sha256:
            package = self.exchange.materialize_model(
                {
                    "model_artifact_id": self.config.start_artifact_id,
                    "model_sha256": self.config.start_model_sha256,
                },
                self.work / "models",
            )
        else:
            raise BoundaryError("rl_learner", "pinned_start_model_required")
        descriptor = json.loads((package / "model.json").read_bytes())
        sha = self.config.start_model_sha256 or descriptor["package_sha256"]
        model, _ = load_model(package / "model.tensor-tree", sha)
        self.start_sha256 = sha
        if not self.settings.warmup_artifact_id:
            raise BoundaryError("rl_learner", "explicit_warmup_checkpoint_required")
        warmup = self.exchange.store.get_manifest(self.settings.warmup_artifact_id)
        raw = b"".join(self.exchange.store.read_payload(warmup.payload("checkpoint")))
        fitted = decode_checkpoint(raw)
        if warmup.kind != "checkpoint" or fitted.get("schema") != "stpd/rl4-head-warmup-1":
            raise ValueError("normal_RL4_warmup_checkpoint_required")
        source_id = warmup.parent("model")
        source_package = self.exchange.materialize_model(
            {"model_artifact_id": source_id, "model_sha256": fitted["model_package_sha256"]},
            self.work / "models",
        )
        source_model, _ = load_model(
            source_package / "model.tensor-tree", fitted["model_package_sha256"]
        )
        policy = action_weights(source_model.state_dict())
        if policy.keys() != model.state_dict().keys() or any(
            not torch.equal(policy[name], value) for name, value in model.state_dict().items()
        ):
            raise ValueError("warmup_start_policy_weights_changed")
        if {
            key: value for key, value in source_model.vocabulary.items() if key != "input_spec"
        } != {key: value for key, value in model.vocabulary.items() if key != "input_spec"}:
            raise ValueError("warmup_start_vocabulary_changed")
        model.enable_rl4_heads(0)
        model.rl4_heads.load_state_dict(fitted["heads"], strict=True)
        return model

    def _load_mu(self, identity: dict[str, Any]) -> MStd:
        package = self.exchange.materialize_model(identity, self.work / "models")
        model, _ = load_model(package / "model.tensor-tree", identity["model_sha256"])
        head = self.exchange.store.get_manifest(identity["heads_artifact_id"])
        payload = head.payload("checkpoint")
        if (
            head.kind != "checkpoint"
            or payload.sha256 != identity["heads_sha256"]
            or head.parameters.value().get("schema") != "stpd/rl-behavior-heads-1"
            or head.parents != (Parent("policy", identity["model_artifact_id"]),)
        ):
            raise ValueError("behavior_heads_binding_changed")
        state = decode_checkpoint(b"".join(self.exchange.store.read_payload(payload)))
        model.enable_rl4_heads(0)
        model.rl4_heads.load_state_dict(state["heads"], strict=True)
        return model.eval()

    def _capture_model(self) -> MStd:
        with self.gpu_lock:
            return copy.deepcopy(self.model).cpu().eval()

    def _publish_model(self, model: MStd, version: int, updates: int) -> dict[str, Any]:
        self._configure_mu(model)
        destination = self.work / "versions" / f"{version:06d}"
        checkpoint = destination.with_suffix(".tensor-tree")
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_bytes(encode_checkpoint({"weights": model.state_dict()}))
        export_model(
            model,
            checkpoint,
            destination,
            {
                "run": self.config.run,
                "version": version,
                "updates": updates,
                "reward": REWARD_SPEC,
                "segments": asdict(self.settings),
                "producer": self.producer.to_dict(),
                "start_package_sha256": self.start_sha256,
            },
        )
        manifest = self.io.publish_model(self.exchange.store, destination, self.producer)
        weights = manifest.payload("weights").sha256
        raw_heads = encode_checkpoint({"heads": model.rl4_heads.state_dict()})
        head_payload = self.exchange.store.put_payload(
            "checkpoint", io.BytesIO(raw_heads), "application/vnd.stpd.tensor-tree"
        )
        head = Manifest(
            "checkpoint",
            self.producer,
            parents=(Parent("policy", manifest.artifact_id),),
            payloads=(head_payload,),
            parameters=FrozenObject.of({"schema": "stpd/rl-behavior-heads-1"}),
        )
        self.exchange.store.publish(head)
        numerical = {
            "precision": "fp32",
            "tf32": False,
            "projection": runtime_projection(model.vocabulary),
            "execution": asdict(model.execution),
            "adapter": adapter_identity(),
            "deduplicate_text": model.deduplicate_text,
        }
        identity = {
            "version": version,
            "updates": updates,
            "model_artifact_id": manifest.artifact_id,
            "model_sha256": weights,
            "heads_artifact_id": head.artifact_id,
            "heads_sha256": head_payload.sha256,
            **numerical,
            "snapshot_sha256": hashlib.sha256(
                json_bytes(
                    {"weights": weights, "heads": head_payload.sha256, "numerical": numerical}
                )
            ).hexdigest(),
        }
        self.exchange.publish_model(
            {
                "run": self.config.run,
                **identity,
                "parent_version": version - 1 if version else None,
                "published_at": time.time(),
                "trajectory_encoding": "short-segment-1",
                "training": {"updates": updates},
            }
        )
        return {"identity": identity, "model": model, "updates": updates}

    def _start_pool(self, publication: dict[str, Any]) -> None:
        self.published_updates = publication["updates"]
        model = publication["model"].to(self.device).eval()
        self._configure_mu(model)
        self.pool = SegmentInferencePool(
            model,
            publication["identity"],
            control=self.control,
            gpu_lock=self.gpu_lock,
            max_batch=self.settings.inference_batch,
            window_ms=self.settings.inference_window_ms,
            segment_steps=self.settings.steps,
        )
        for identity in self.restored_mu:
            if identity["snapshot_sha256"] in self.pool.snapshots:
                continue
            frozen = self._load_mu(identity)
            frozen.to(self.device).eval()
            self._configure_mu(frozen)
            self.pool.publish(frozen, identity)
        # Loading old protected snapshots must not make an old one latest.
        self.pool.latest = publication["identity"]["snapshot_sha256"]
        (self.work / "inference.json").write_bytes(json_bytes(self.pool.attachment()))

    def _configure_mu(self, model: MStd) -> None:
        model.deduplicate_text = self.performance.deduplicate_text
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        model.execution = replace(
            model.execution,
            implementation="experimental-packed-v1",
            microbatch_size=self.settings.inference_batch,
            scene_attention_area=self.performance.scene_attention_area,
            text_attention_area=self.performance.text_attention_area,
        )
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False

    def _collect_publication(self, *, wait: bool) -> None:
        if self.pending_publication is None or not wait and not self.pending_publication.done():
            return
        publication = self.pending_publication.result()
        model = publication["model"].to(self.device).eval()
        self._configure_mu(model)
        with self.condition:
            self.pool.publish(model, publication["identity"])
            self.published_updates = publication["updates"]
            self.pending_publication = None
            self._retire_mu()

    def _schedule_publication(self) -> None:
        self._collect_publication(wait=True)
        model = self._capture_model()
        version = self.version + 1
        publication = self.publisher.submit(
            self._publish_model, model, version, self.updates
        )
        self.pending_publication, self.version = publication, version

    def _retire_mu(self) -> None:
        protected = {value["mu"]["snapshot_sha256"] for value in self.lease_owners.values()}
        protected.update(value["mu"]["snapshot_sha256"] for value in self.queue.pending)
        if self.active:
            protected.update(value["mu"]["snapshot_sha256"] for value in self.active["segments"])
        self.pool.retire(protected)

    def control(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = request["operation"]
        if operation == "episode_open":
            with self.condition:
                if self.newlease_stopped:
                    return {"admitted": False}
                actor, episode = request["actor"], request["episode"]
                self.actor_episodes[f"{actor}:{episode}"] = episode
                return {"admitted": True}
        if operation == "status":
            return {
                "accepting_new_segments": not self.newlease_stopped,
                "server_time": time.time(),
                "pool_id": self.pool.pool_id,
            }
        if operation == "begin":
            actor, episode = request["actor"], request["episode"]
            with self.condition:
                episode_key = f"{actor}:{episode}"
                while not self.newlease_stopped and episode_key in self.actor_episodes:
                    sequence = self.queue.cursors.get(actor, -1) + 1
                    lease = f"{actor}/{sequence}"
                    # The per-game share belongs to dataset selection, not to
                    # admission. Only the unified queue capacity pauses producers.
                    if self.queue.reserve(lease):
                        key = self.pool.latest
                        mu = self.pool.snapshots[key]
                        self.lease_owners[lease] = {"actor": actor, "episode": episode, "mu": mu}
                        return {"mu": mu, "lease": lease, "server_time": time.time()}
                    self.condition.wait(0.1)
                return {"stopped": True}
        if operation == "commit":
            return self._admit(request)
        if operation == "episode_end":
            actor, episode = request["actor"], request["episode"]
            with self.condition:
                owned = [
                    lease
                    for lease, owner in self.lease_owners.items()
                    if owner["actor"] == actor and owner["episode"] == episode
                ]
            for lease in owned:
                sequence = int(lease.rpartition("/")[2])
                key = f"{self.exchange.prefix}shards/{actor}/{sequence:08d}.json"
                try:
                    record = self.exchange.segment_record(key)
                except StoreError as error:
                    if error.code != "object_not_found":
                        raise
                else:
                    # Read only this owner's exact records, not the full run's
                    # growing history. This reconciles admission, never World.
                    self._admit(record)
            with self.condition:
                if request.get("terminal") is not None:
                    self.terminal.add(request["run_id"], request["outcome_states"])
                for battle in request["hp_battles"]:
                    self.hp.add(battle["battle_id"], battle["states"])
                for lease, owner in list(self.lease_owners.items()):
                    if owner["actor"] == actor and owner["episode"] == episode:
                        self.queue.release(lease)
                        self.lease_owners.pop(lease)
                self.actor_episodes.pop(f"{actor}:{episode}", None)
                self.condition.notify_all()
            return {"run_id": request["run_id"]}
        raise ValueError("unsupported_segment_control")

    def _admit(self, request: dict[str, Any]) -> dict[str, Any]:
        segment = self.io.read_segment(
            self.exchange.store, request["segment_artifact_id"], scratch=self.work
        )
        if any(segment[key] != request[key] for key in ("segment_id", "run_id", "rows", "mu")):
            raise ValueError("segment_record_changed")
        from stpd.evaluation.suites import seed_membership

        seed = segment["identity"]["seed"]
        if seed in self.settings.excluded_seeds or seed_membership(seed) or held_out(seed):
            raise ValueError("segment_seed_is_held_out")
        battles = []
        sources = {request["segment_artifact_id"]: segment}
        for battle in segment.get("hp_battles", []):
            states = []
            for state in battle["states"]:
                reference = dict(state)
                if "current_row" in reference:
                    reference.update(
                        segment_artifact_id=request["segment_artifact_id"],
                        row=reference.pop("current_row"),
                    )
                artifact = reference["segment_artifact_id"]
                if artifact not in sources:
                    sources[artifact] = self.io.read_segment(
                        self.exchange.store, artifact, scratch=self.work
                    )
                source = sources[artifact]
                step = source["steps"][reference["row"]]
                if (
                    source["run_id"] != segment["run_id"]
                    or reference["key"] != f"{segment['run_id']}:{step['step_index']}"
                    or not battle["start_step"] <= step["step_index"] < battle["end_step"]
                    or reference["hp"] != battle["hp"]
                ):
                    raise ValueError("battle_hp_state_binding_changed")
                states.append(reference)
            battles.append((battle["battle_id"], states))
        with self.condition:
            owner = self.lease_owners.get(segment["segment_id"])
            if (
                owner is None
                or owner["mu"] != segment["mu"]
                or owner["episode"] != segment["identity"]["intent_id"]
            ):
                raise ValueError("segment_reserved_behavior_changed")
            self.queue.commit(
                segment["segment_id"],
                {
                    **{
                        key: request[key]
                        for key in ("segment_id", "run_id", "rows", "mu", "segment_artifact_id")
                    },
                    "episode": owner["episode"],
                },
            )
            for battle_id, states in battles:
                self.hp.add(battle_id, states)
            self.lease_owners.pop(segment["segment_id"])
            self.condition.notify_all()
        return {"segment_id": segment["segment_id"]}

    def _load(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            self.io.read_segment(
                self.exchange.store, value["segment_artifact_id"], scratch=self.work
            )
            for value in records
        ]

    def _auxiliary(self, references: list[dict[str, Any]]) -> list[AuxiliarySample]:
        cache = {}
        result = []
        for reference in references:
            artifact = reference["segment_artifact_id"]
            if artifact not in cache:
                cache[artifact] = self.io.read_segment(
                    self.exchange.store, artifact, scratch=self.work
                )
            segment = cache[artifact]
            step = segment["steps"][reference["row"]]
            # Input/vocabulary remains fixed while the shared trunk learns.
            frame = compile_frame(
                project(
                    step["input"]["observation"],
                    input_projection=runtime_projection(self.model.vocabulary),
                ),
                self.model,
            )
            result.append(
                AuxiliarySample(
                    frame,
                    step["behavior"]["floor"],
                    reference["key"],
                    outcome=reference.get("outcome"),
                    hp=reference.get("hp"),
                )
            )
        return result

    def _prepare(self) -> bool:
        with self.condition:
            if self.active is None:
                records = self.queue.dataset()
                if records is None:
                    return False
                self.active = {
                    "segments": records,
                    "frozen": None,
                    "epoch": 0,
                    "minibatch": 0,
                    "members": None,
                    "used": [],
                    "start_updates": self.updates,
                    "terminal": [],
                    "hp": [],
                }
                self.condition.notify_all()
            active = self.active
        segments = self._load(active["segments"])
        if active["frozen"] is None:
            active["frozen"] = freeze_targets(segments, self.pool.models, gpu_lock=self.gpu_lock)
            order = list(range(sum(value["rows"] for value in active["segments"])))
            self.rng.shuffle(order)
            active["members"] = [
                [order[index] for index in group]
                for group in minibatch_members(len(order), self.settings.minibatches)
            ]
            with self.condition:
                active["terminal"] = self.terminal.sample(
                    self.settings.auxiliary_rows, self.rng, epochs=self.settings.epochs
                )
                active["hp"] = self.hp.sample(
                    self.settings.auxiliary_rows, self.rng, epochs=self.settings.epochs
                )
        self.active_rows = samples_from_frozen(segments, self.pool.models, active["frozen"])
        self.row_metadata = [
            {
                "generated_at": step.get("generated_at"),
                "floor": step["behavior"]["floor"],
                "mu_version": segment["mu"]["version"],
                "mu_updates": segment["mu"]["updates"],
            }
            for segment in segments
            for step in segment["steps"]
        ]
        self.active_auxiliary = self._auxiliary(active["terminal"]), self._auxiliary(active["hp"])
        return True

    def _step(self) -> None:
        active = self.active
        assert active is not None and self.active_rows is not None
        assert self.active_auxiliary is not None
        index, epoch = active["minibatch"], active["epoch"]
        terminal_parts = (
            minibatch_members(len(active["terminal"]), self.settings.minibatches)
            if len(active["terminal"]) >= self.settings.minibatches
            else [list(range(len(active["terminal"])))] + [[]] * (self.settings.minibatches - 1)
        )
        hp_parts = (
            minibatch_members(len(active["hp"]), self.settings.minibatches)
            if len(active["hp"]) >= self.settings.minibatches
            else [list(range(len(active["hp"])))] + [[]] * (self.settings.minibatches - 1)
        )
        terminal = [self.active_auxiliary[0][i] for i in terminal_parts[index]]
        hp = [self.active_auxiliary[1][i] for i in hp_parts[index]]
        result = segment_update(
            self.model,
            self.optimizer,
            [self.active_rows[i] for i in active["members"][index]],
            terminal,
            hp,
            config=self.config.loss,
            performance=self.performance,
            target_kl=self.settings.target_kl,
            gpu_lock=self.gpu_lock,
            diagnostics=self.updates % self.settings.diagnostics_every == 0,
        )
        if result["stepped"]:
            used = set(active["used"])
            newly_used = [i for i in active["members"][index] if i not in used]
            self.first_use += len(newly_used)
            self.first_use_multi += sum(
                len(self.active_rows[i].frame.choices) > 1 for i in newly_used
            )
            first = [self.row_metadata[i] for i in newly_used]
            ages = []
            now = time.time()
            for row in first:
                if row["generated_at"] is not None:
                    ages.append(now - datetime.fromisoformat(row["generated_at"]).timestamp())
            ages.sort()
            result.update(
                first_use_age_seconds={
                    "n": len(ages),
                    "median": ages[len(ages) // 2] if ages else None,
                    "p95": ages[min(len(ages) - 1, int(len(ages) * 0.95))] if ages else None,
                },
                first_use_floors=[row["floor"] for row in first],
                first_use_version_lag=[self.version - row["mu_version"] for row in first],
                first_use_pre_update_SGD_lag=[self.updates - row["mu_updates"] for row in first],
            )
            active["used"].extend(newly_used)
            with self.condition:
                self.terminal.used(
                    [active["terminal"][i] for i in terminal_parts[index]],
                    self.datasets,
                    epochs=self.settings.epochs,
                )
                self.hp.used(
                    [active["hp"][i] for i in hp_parts[index]],
                    self.datasets,
                    epochs=self.settings.epochs,
                )
            decay = self.settings.ema_decay
            for name, value in self.model.state_dict().items():
                current = value.detach()
                if current.is_floating_point():
                    self.ema[name].mul_(decay).add_(current, alpha=1 - decay)
                else:
                    self.ema[name].copy_(current)
            self.updates += 1
        result.update(
            update=self.updates,
            dataset=self.datasets,
            epoch=epoch,
            minibatch=index,
            first_use=self.first_use,
            first_use_multi=self.first_use_multi,
        )
        # Complete the in-memory learning transition before fallible logs,
        # telemetry, or publication. A final CP must never pair new Adam/theta
        # with the old minibatch cursor, nor leave an exhausted dataset active.
        active["minibatch"] += 1
        if active["minibatch"] == self.settings.minibatches:
            active["epoch"] += 1
            active["minibatch"] = 0
        finished = result["kl_stopped"] or active["epoch"] == self.settings.epochs
        if finished:
            self.datasets += 1
            with self.condition:
                self.active = self.active_rows = self.active_auxiliary = None
                self._retire_mu()
        with (self.work / "updates.jsonl").open("ab") as stream:
            stream.write(json_bytes(result))
        if self.updates % max(1, self.config.tensorboard_steps) == 0:
            if self.metrics is not None:
                self.metrics.scalars(
                    {
                        f"train/{key}": result.get(key)
                        for key in (
                            "td_loss",
                            "policy_loss",
                            "entropy",
                            "approx_kl_mu_theta",
                            "acting_kl_theta_mu",
                            "clip_fraction",
                            "terminal_loss",
                            "hp_loss",
                            "gradient_norm",
                            "first_use",
                            "first_use_multi",
                        )
                    },
                    self.updates,
                )
                self.metrics.histograms(
                    {
                        "decisions/first_use_floor": result.get("first_use_floors", []),
                        "decisions/first_use_version_lag": result.get("first_use_version_lag", []),
                        "decisions/first_use_pre_update_SGD_lag": result.get(
                            "first_use_pre_update_SGD_lag", []),
                    },
                    self.updates,
                )
            while self.telemetry and self.telemetry[0].done():
                self.telemetry.popleft().result()
            if len(self.telemetry) >= 8:
                self.telemetry.popleft().result()
            self.telemetry.append(
                self.publisher.submit(
                    self.exchange.publish_event,
                    "progress",
                    **result,
                    version=self.version,
                    queued_rows=self.queue.rows,
                    reserved_rows=sum(self.queue.leases.values()),
                    trajectory_encoding="short-segment-1",
                )
            )
        if finished and self.updates != active["start_updates"]:
            self._schedule_publication()

    def _state(self) -> tuple[bytes, tuple[Parent, ...]]:
        with self.condition, self.gpu_lock:
            metadata: dict[str, Any] = {
                "schema": CHECKPOINT_SCHEMA,
                "config": asdict(self.config),
                "producer": self.producer.to_dict(),
                "queue": self.queue.state(),
                "lease_owners": self.lease_owners,
                "actor_episodes": self.actor_episodes,
                "terminal": self.terminal.state(),
                "hp": self.hp.state(),
                "active": (
                    {key: value for key, value in self.active.items() if key != "frozen"}
                    if self.active
                    else None
                ),
                "mu": list(self.pool.snapshots.values()),
                "updates": self.updates,
                "published_updates": self.published_updates,
                "datasets": self.datasets,
                "version": self.version,
                "first_use": self.first_use,
                "first_use_multi": self.first_use_multi,
                "checkpoint_sequence": self.checkpoint_sequence,
            }
            # Active auxiliary references must resume their original pool counters.
            if metadata["active"] is not None:
                metadata["active"] = copy.deepcopy(metadata["active"])
            state = {
                "schema": CHECKPOINT_SCHEMA,
                "metadata": metadata_tensor(metadata),
                "weights": self.model.state_dict(),
                "optimizer": self.optimizer.state_dict(),
                "ema": self.ema,
                "random": self.rng.getstate(),
                "torch_rng": torch.get_rng_state(),
                "cuda_rng": torch.cuda.get_rng_state_all() if self.device.type == "cuda" else [],
                "frozen": self.active["frozen"] if self.active else None,
            }
            artifacts = {value["heads_artifact_id"] for value in metadata["mu"]}
            artifacts.add(self.settings.warmup_artifact_id)
            if self.config.start_artifact_id:
                artifacts.add(self.config.start_artifact_id)
            artifacts.update(value["segment_artifact_id"] for value in metadata["queue"]["pending"])
            for pool in (metadata["terminal"], metadata["hp"]):
                artifacts.update(
                    row["segment_artifact_id"] for _, group in pool["groups"] for row in group
                )
            if self.active:
                artifacts.update(value["segment_artifact_id"] for value in self.active["segments"])
                artifacts.update(
                    row["segment_artifact_id"]
                    for key in ("terminal", "hp")
                    for row in self.active[key]
                )
            parents = tuple(
                Parent(f"input_{i}", artifact) for i, artifact in enumerate(sorted(artifacts))
            )
        # Metadata was frozen above and theta/Adam/EMA have one writer (this
        # learner thread). Encoding does not need to block control or inference.
        return encode_checkpoint(state), parents

    def _collect_checkpoint(self, *, wait: bool = False) -> None:
        taken = self.checkpoints.take(wait=wait)
        if taken:
            info = taken[0].parameters.value()
            self.exchange.publish_checkpoint(
                info["version"], taken[0].artifact_id, sequence=info["sequence"]
            )

    def checkpoint(self, *, final: bool = False) -> None:
        self._collect_publication(wait=True)
        if self.metrics is not None:
            self.metrics.publish()
        self._collect_checkpoint(wait=final)
        if self.checkpoints.pending:
            return
        self.checkpoint_sequence += 1
        raw, refs = self._state()
        self.checkpoints.submit(
            FrozenCheckpoint(
                raw,
                refs,
                FrozenObject.of(
                    {
                        "schema": CHECKPOINT_SCHEMA,
                        "run": self.config.run,
                        "sequence": self.checkpoint_sequence,
                        "version": self.version,
                    }
                ),
                0.0,
            )
        )
        if final:
            self._collect_checkpoint(wait=True)

    def _restore(self, artifact_id: str) -> tuple[torch.Tensor, list[torch.Tensor]]:
        import tempfile

        manifest = self.exchange.store.get_manifest(artifact_id)
        if (
            manifest.kind != "checkpoint"
            or manifest.parameters.value().get("schema") != CHECKPOINT_SCHEMA
        ):
            raise ValueError("segment_checkpoint_required")
        with tempfile.TemporaryDirectory(prefix="segment-restore-", dir=self.work) as directory:
            path = self.exchange.store.materialize(
                manifest.payload("checkpoint"), Path(directory) / "cp"
            )
            state = decode_checkpoint(path.read_bytes())
        metadata = json.loads(state["metadata"].cpu().numpy().tobytes())
        expected = asdict(self.config)
        previous = metadata["config"]
        for key in (
            "resume_checkpoint",
            "resume_source",
            "republish_on_resume",
            "wall_seconds",
            "device",
            "threads",
            "max_versions",
            "peer",
            "start_package",
        ):
            expected.pop(key, None)
            previous.pop(key, None)
        # Diagnostics only read extra gradients; cadence is an operational cost.
        # It does not change targets, membership, successful updates or optimizer math.
        expected["segments"].pop("diagnostics_every", None)
        previous["segments"].pop("diagnostics_every", None)
        # Microbatch is gradient accumulation within the same frozen minibatch;
        # membership, global denominators and successful SGD accounting stay fixed.
        expected["performance"].pop("microbatch_size", None)
        previous["performance"].pop("microbatch_size", None)
        if (
            metadata["schema"] != CHECKPOINT_SCHEMA
            or previous != expected
            or metadata["producer"] != (self.config.resume_source or self.producer.to_dict())
        ):
            raise ValueError("segment_resume_config_or_source_changed")
        self.model.load_state_dict(state["weights"])
        self.optimizer.load_state_dict(state["optimizer"])
        self.ema = {key: value.to(self.device) for key, value in state["ema"].items()}
        self.queue.restore(metadata["queue"])
        self.lease_owners = metadata["lease_owners"]
        self.actor_episodes = metadata["actor_episodes"]
        # The CP episode registry is authoritative: an ended episode cannot own
        # an unused capacity reservation. Older timed-out begin calls could
        # wake after episode_end and leave precisely these orphan reservations.
        for lease, owner in list(self.lease_owners.items()):
            if f"{owner['actor']}:{owner['episode']}" not in self.actor_episodes:
                self.queue.release(lease)
                self.lease_owners.pop(lease)
                self.released_closed_reservations.append(lease)
        self.terminal.restore(metadata["terminal"])
        self.hp.restore(metadata["hp"])
        self.active = metadata["active"]
        if self.active:
            self.active["frozen"] = state["frozen"]
            for name, pool in (("terminal", self.terminal), ("hp", self.hp)):
                by_key = {row["key"]: row for rows in pool.groups.values() for row in rows}
                self.active[name] = [by_key.get(row["key"], row) for row in self.active[name]]
        for name in (
            "updates",
            "published_updates",
            "datasets",
            "version",
            "first_use",
            "first_use_multi",
            "checkpoint_sequence",
        ):
            setattr(self, name, metadata[name])
        self.rng.setstate(state["random"])
        self.restored_mu = metadata["mu"]
        return state["torch_rng"], state["cuda_rng"]

    def run(self, *, initialize_only: bool = False) -> dict[str, Any]:
        began = time.monotonic()
        deadline = began + self.config.wall_seconds
        checkpoint_at = began + self.settings.checkpoint_seconds
        stop_checked = began
        try:
            while (
                not initialize_only
                and time.monotonic() < deadline
                and not (self.work / "STOP").exists()
                and self.version < self.config.max_versions
            ):
                if time.monotonic() >= stop_checked:
                    stop_checked = time.monotonic() + self.config.remote_stop_seconds
                    if self.exchange.stop_request() is not None:
                        break
                self._collect_publication(wait=False)
                self._collect_checkpoint()
                if time.monotonic() >= checkpoint_at:
                    self.checkpoint()
                    checkpoint_at = time.monotonic() + self.settings.checkpoint_seconds
                if self.active_rows is None and not self._prepare():
                    with self.condition:
                        self.condition.wait(0.1)
                    continue
                self._step()
        finally:
            with self.condition:
                self.newlease_stopped = True
                self.condition.notify_all()
            # Existing pinned segments can still infer/commit; new leases stop.
            # This drain is within the card's separately reserved closeout budget.
            drain_deadline = time.monotonic() + 600
            while (self.queue.leases or self.actor_episodes) and time.monotonic() < drain_deadline:
                self._collect_publication(wait=False)
                with self.condition:
                    self.condition.wait(0.2)
            self._collect_publication(wait=True)
            if self.updates > self.published_updates:
                # A budget Stop can fall between successful SGD steps of a frozen
                # dataset. Publish that exact end theta, retaining its active
                # dataset/targets/cursor in CP so evaluation and resume agree.
                self._schedule_publication()
            self.checkpoint(final=True)
            inference = self.pool.close()
            self.publisher.shutdown(wait=True)
            for future in self.telemetry:
                future.result()
            self.checkpoints.__exit__(None)
        summary = {
            "updates": self.updates,
            "published_updates": self.published_updates,
            "versions": self.version,
            "datasets": self.datasets,
            "first_use": self.first_use,
            "first_use_multi": self.first_use_multi,
            "elapsed_seconds": time.monotonic() - began,
            "unresolved_inflight": len(self.queue.leases),
            "queued_rows": self.queue.rows,
            "unresolved_episodes": len(self.actor_episodes),
            "released_closed_reservations": self.released_closed_reservations,
            "inference": inference,
            "trajectory_encoding": "short-segment-1",
            "counts": {"first_use": self.first_use, "first_use_multi": self.first_use_multi},
        }
        (self.work / "learner-summary.json").write_bytes(json_bytes(summary))
        return summary
