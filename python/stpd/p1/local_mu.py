"""Actor-owned immutable CPU mu and loopback scoring, never a World executor."""
from __future__ import annotations

import copy
import random
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import torch

from spireagent.artifact_contracts import Parent
from spireagent.rl_contracts import MuScore
from stpd.workers.checkpoint_codec import decode_checkpoint

from .data import digest, json_bytes
from .execution import MStdExecution
from .export import adapter_identity, bound_projection, load_model
from .frame_compiler import FrameCompiler
from .local_inference import SegmentConnection, SegmentInferencePool
from .text_cache import TextVectorCache


def single_cpu_thread() -> None:
    torch.set_num_threads(1)
    if torch.get_num_interop_threads() != 1:
        torch.set_num_interop_threads(1)


class LocalMuScorer:
    """Full model codec/Store reader; bounded cache and explicitly retained tails.

    A grant pins an immutable snapshot. Only adopt() changes the active snapshot;
    score_compiled() always names a snapshot, including old forced/tail values.
    """

    def __init__(self, exchange: Any, cache: Path, *, cache_bytes: int = 64 * 1024**2,
                 max_snapshots: int = 2) -> None:
        single_cpu_thread()
        # One actor owns this CPU inference process. The fused MHA/encoder
        # shortcut is slower than the ordinary SDPA path for these small,
        # masked single-observation batches (measured on Friday AVX2 and M4).
        # This file is in adapter_identity(), so a new grant binds the choice;
        # existing immutable snapshots are never relabelled or hot-swapped.
        torch.backends.mha.set_fastpath_enabled(False)
        self.exchange, self.cache_root = exchange, cache
        cache.mkdir(parents=True, exist_ok=True)
        self.text_cache = TextVectorCache(limit=cache_bytes)
        self.max_snapshots = max_snapshots
        self.models: dict[str, Any] = {}
        self.identities: dict[str, dict[str, Any]] = {}
        self.compilers: dict[str, FrameCompiler] = {}
        self.references: dict[str, int] = {}
        self.latest: str | None = None
        self.lock = threading.RLock()
        self.capacity = threading.Condition(self.lock)
        self.closed = False
        self.load_seconds: list[float] = []
        self.score_seconds = 0.0
        self.score_sample: list[float] = []
        self.score_sample_rng = random.Random(0)  # telemetry only, never acting RNG
        self.requests = 0
        self.scoring_threads: set[tuple[int, int]] = set()

    def _load(self, identity: dict[str, Any]) -> Any:
        # Download only this grant, never walk all intermediate publications. The
        # temporary package is owned here; no existing Store asset is removed.
        with tempfile.TemporaryDirectory(prefix="mu-", dir=self.cache_root) as temporary:
            package = self.exchange.materialize_model(identity, Path(temporary))
            model, _ = load_model(package / "model.tensor-tree", identity["model_sha256"])
        head = self.exchange.store.get_manifest(identity["heads_artifact_id"])
        payload = head.payload("checkpoint")
        if (head.kind != "checkpoint" or payload.sha256 != identity["heads_sha256"]
                or head.parameters.value().get("schema") != "stpd/rl-behavior-heads-1"
                or head.parents != (Parent("policy", identity["model_artifact_id"]),)):
            raise ValueError("behavior_heads_binding_changed")
        raw = b"".join(self.exchange.store.read_payload(payload))
        if digest(raw) != identity["heads_sha256"]:
            raise ValueError("behavior_heads_checksum_changed")
        state = decode_checkpoint(raw)
        with torch.random.fork_rng(devices=[]):
            model.enable_rl4_heads(0)
        model.rl4_heads.load_state_dict(state["heads"], strict=True)
        numerical = {key: identity[key] for key in (
            "precision", "tf32", "projection", "execution", "adapter", "deduplicate_text")}
        expected = digest(json_bytes({"weights": identity["model_sha256"],
                                      "heads": identity["heads_sha256"],
                                      "numerical": numerical}))
        if identity["snapshot_sha256"] != expected:
            raise ValueError("behavior_snapshot_checksum_changed")
        compiler = FrameCompiler(model.vocabulary)
        if (identity["precision"] != "fp32" or identity["tf32"] is not False
                or identity["adapter"] != adapter_identity()
                or identity.get("vocabulary_sha256", compiler.identity["vocabulary_sha256"])
                    != compiler.identity["vocabulary_sha256"]
                or identity.get("input_spec", compiler.identity["input_spec"])
                    != compiler.identity["input_spec"]):
            raise ValueError("behavior_numerical_or_input_binding_changed")
        bound_projection(model.vocabulary, identity["projection"])
        execution = MStdExecution.decode(identity["execution"])
        if execution.implementation not in {
                "experimental-packed-v1", "experimental-joint-nodes-v1"}:
            raise ValueError("actor_mu_requires_packed_execution")
        model.execution = execution
        model.deduplicate_text = identity["deduplicate_text"]
        model.cpu().eval().requires_grad_(False)
        model.text_cache = self.text_cache
        model.text_cache_namespace = identity["snapshot_sha256"]
        return model

    def adopt(self, identity: dict[str, Any]) -> None:
        key = identity["snapshot_sha256"]
        with self.capacity:
            if key in self.identities and self.identities[key] != identity:
                raise ValueError("same_mu_identity_changed")
            self._retire(keep={key})
            if key not in self.models:
                while len(self.models) >= self.max_snapshots and not self.closed:
                    # The previous observer can still be committing a tail.
                    # Pause begin/await, rather than failing a live World or
                    # evicting a referenced mu to make room for the new grant.
                    self.capacity.wait()
                if self.closed:
                    raise ValueError("actor_mu_owner_closed")
                started = time.perf_counter()
                model = self._load(identity)
                self.models[key] = model
                self.identities[key] = copy.deepcopy(identity)
                self.compilers[key] = FrameCompiler(model.vocabulary)
                self.load_seconds.append(time.perf_counter() - started)
            if key != self.latest:
                # Invalidate at adoption, after any old-tail capacity wait.
                self.text_cache.retire(set())
            self.latest = key

    def _retire(self, *, keep: set[str]) -> None:
        for key in list(self.models):
            if key not in keep and not self.references.get(key, 0):
                del self.models[key], self.identities[key], self.compilers[key]
        self.text_cache.retire(set(self.models))

    def retain_mu(self, snapshot_sha256: str) -> None:
        with self.lock:
            if snapshot_sha256 not in self.models:
                raise ValueError("actor_mu_not_loaded")
            self.references[snapshot_sha256] = self.references.get(snapshot_sha256, 0) + 1

    def release_mu(self, snapshot_sha256: str) -> None:
        with self.lock:
            count = self.references.get(snapshot_sha256, 0)
            if not count:
                raise ValueError("actor_mu_not_retained")
            if count == 1:
                del self.references[snapshot_sha256]
            else:
                self.references[snapshot_sha256] = count - 1
            self._retire(keep={self.latest} if self.latest is not None else set())
            self.capacity.notify_all()

    def close(self) -> None:
        with self.capacity:
            self.closed = True
            self.capacity.notify_all()

    def score_compiled(self, snapshot_sha256: str, frame_wire: dict[str, Any], floor: int,
                       *, policy: bool) -> MuScore:
        with self.lock, torch.inference_mode():
            # ATen/OpenMP settings also apply to the observer/connection thread
            # that actually executes this forward, rather than only __main__.
            if torch.get_num_threads() != 1:
                torch.set_num_threads(1)
            self.scoring_threads.add((torch.get_num_threads(), torch.get_num_interop_threads()))
            model = self.models[snapshot_sha256]
            compiler = self.compilers[snapshot_sha256]
            identity = self.identities[snapshot_sha256]
            frame = compiler.decode(frame_wire,
                expected_identity=identity.get("compiler_identity", frame_wire["compiler"]))
            # decode validates vocabulary/InputSpec as well as the producer's
            # complete compiler identity. No projection or hidden state is added.
            started = time.perf_counter()
            scores, outcome, _ = model.forward_rl4([frame], [floor], check_finite=True)
            probabilities = tuple(outcome.float().softmax(-1)[0].tolist())
            score = tuple(scores[0].float().tolist()) if policy else None
            elapsed = time.perf_counter() - started
            self.requests += 1
            self.score_seconds += elapsed
            if len(self.score_sample) < 4096:
                self.score_sample.append(elapsed)
            else:
                slot = self.score_sample_rng.randrange(self.requests)
                if slot < len(self.score_sample):
                    self.score_sample[slot] = elapsed
            return MuScore(snapshot_sha256, score, probabilities)

    def report(self) -> dict[str, Any]:
        with self.lock:
            sample = sorted(self.score_sample)
            latency = {"population": self.requests, "sampled": len(sample),
                "sampling": "uniform reservoir; all requests when population<=4096",
                "median": sample[len(sample) // 2] if sample else None,
                "p95": sample[min(len(sample) - 1, int(len(sample) * .95))] if sample else None}
            return {"threads": torch.get_num_threads(),
                    "interop_threads": torch.get_num_interop_threads(),
                    "mha_fastpath": torch.backends.mha.get_fastpath_enabled(),
                    "scoring_threads": sorted(self.scoring_threads),
                    "resident_mu": len(self.models), "retained": dict(self.references),
                    "load_seconds": self.load_seconds, "requests": self.requests,
                    "score_seconds": self.score_seconds, "score_latency_seconds": latency,
                    "score_latency_sample_seconds": list(self.score_sample),
                    "text_cache": self.text_cache.report()}


class RemoteMuScorer:
    """Explicit central alternative, sharing the same frozen-mu/tail interface."""

    def __init__(self, attachment: dict[str, Any]) -> None:
        self.attachment = copy.deepcopy(attachment)
        # Scores do not wait for commits or grants. One actor-owned, serialized
        # score channel can therefore serve policy and old-mu tails without
        # opening/authenticating a new TCP connection for every row. Control
        # channels stay separate so capacity waits cannot block a commit.
        self.remote = SegmentConnection(self.attachment)
        self.score_lock = threading.RLock()
        self.closed = False
        self.identities: dict[str, dict[str, Any]] = {}
        self.references: dict[str, int] = {}
        self.lock = threading.RLock()
        self.requests = 0

    def adopt(self, identity: dict[str, Any]) -> None:
        with self.score_lock, self.lock:
            if self.closed:
                raise ValueError("actor_mu_owner_closed")
            key = identity["snapshot_sha256"]
            if key in self.identities and self.identities[key] != identity:
                raise ValueError("same_mu_identity_changed")
            if identity["training_generation"] != self.remote.attachment.get(
                    "training_generation"):
                # A new grant must not score on the closed predecessor's socket.
                # Reconnect via the original rendezvous on the next new request;
                # no outstanding or unknown score is resent.
                self.remote.close()
            self.identities = {k: v for k, v in self.identities.items()
                               if k == key or self.references.get(k, 0)}
            self.identities[key] = copy.deepcopy(identity)

    def retain_mu(self, snapshot_sha256: str) -> None:
        with self.lock:
            if snapshot_sha256 not in self.identities:
                raise ValueError("central_mu_not_adopted")
            self.references[snapshot_sha256] = self.references.get(snapshot_sha256, 0) + 1

    def release_mu(self, snapshot_sha256: str) -> None:
        with self.lock:
            count = self.references[snapshot_sha256]
            if count == 1:
                del self.references[snapshot_sha256]
            else:
                self.references[snapshot_sha256] = count - 1

    def score_compiled(self, snapshot_sha256: str, frame_wire: dict[str, Any], floor: int,
                       *, policy: bool) -> MuScore:
        from spireagent.json_boundary import BoundaryError

        generation = self.identities[snapshot_sha256]["training_generation"]
        with self.score_lock:
            if self.closed:
                raise ValueError("actor_mu_owner_closed")
            remote = self.remote
            try:
                if not policy:
                    # A tail may outlive a learner. Read its generation before
                    # asking for values; never substitute a replacement's mu.
                    status = remote.request("status")
                    if status["training_generation"] != generation:
                        raise BoundaryError("rl_mu", "generation_changed")
                try:
                    result = remote.request("score", snapshot_sha256=snapshot_sha256,
                        compiled_input=frame_wire, floor=floor, policy=policy,
                        input_projection=self.identities[snapshot_sha256]["projection"])
                except (OSError, EOFError, TimeoutError):
                    # A late score response must not be mistaken for this read
                    # or the next independent score. Discard the uncertain
                    # channel before reconnecting for status only.
                    remote.close()
                    status = remote.request("status")
                    if status["training_generation"] != generation:
                        raise BoundaryError("rl_mu", "generation_changed") from None
                    raise
                if result["mu"] != self.identities[snapshot_sha256]:
                    raise ValueError("central_mu_score_identity_changed")
                scored = MuScore(snapshot_sha256,
                    tuple(result["scores"]) if policy else None,
                    tuple(result["outcome_probabilities"]))
            except BaseException:
                # Preserve the old context-manager cleanup on every failure,
                # including malformed or mismatched replies. Never resend it.
                remote.close()
                raise
        with self.lock:
            self.requests += 1
        return scored

    def report(self) -> dict[str, Any]:
        with self.lock:
            return {"backend": "central", "requests": self.requests,
                    "retained": dict(self.references)}

    def close(self) -> None:
        with self.score_lock:
            self.closed = True
            self.remote.close()


class ActorInferencePool(SegmentInferencePool):
    """One actor process serves its Agent over loopback and proxies control only."""

    def __init__(self, remote: SegmentConnection, scorer: LocalMuScorer | RemoteMuScorer) -> None:
        attachment = remote.attachment
        self.remote_attachment = copy.deepcopy(attachment)
        self.scorer = scorer
        self.leases: dict[tuple[str, str], tuple[str, str]] = {}
        self.controls = threading.RLock()
        # Each connection gets its own control channel: a blocked begin must not
        # prevent the observer's commit/status/episode_end from reaching learner.
        super().__init__(attachment["vocabulary"], {"snapshot_sha256": "unadopted"},
            control=None, address=("127.0.0.1", 0), segment_steps=attachment["segment_steps"],
            compiler_identity=attachment["compiler_identity"])

    def attachment(self) -> dict[str, Any]:
        return {**super().attachment(),
                **{key: self.remote_attachment[key] for key in ("run", "training_generation")
                   if key in self.remote_attachment}, "role": "actor_local_mu"}

    def _dispatch(self, value: dict[str, Any]) -> dict[str, Any]:
        operation = value["operation"]
        if operation == "score":
            key = value["snapshot_sha256"]
            started = time.perf_counter()
            scored = self.scorer.score_compiled(key, value["compiled_input"], value["floor"],
                                                policy=value.get("policy", True))
            return {"mu": self.scorer.identities[key], "scores": scored.scores,
                    "outcome_probabilities": scored.outcome_probabilities,
                    "batch_size": 1, "queue_seconds": 0,
                    "server_seconds": time.perf_counter() - started}
        fields = {key: item for key, item in value.items()
                  if key not in {"schema", "pool_id", "adapter", "request_id", "operation"}}
        with SegmentConnection(self.remote_attachment) as remote:
            result = remote.request(operation, **fields)
            if operation in {"begin", "episode_open"} and result.get("reason") == (
                    "generation_changed"):
                # Re-admit the same live World under the new learning generation,
                # not a new seed/episode execution. Original absent control only.
                owner = {"actor": fields["actor"], "episode": fields["episode"],
                         "run": remote.attachment["run"],
                         "training_generation": remote.attachment["training_generation"]}
                admission = remote.request("episode_open", **owner)
                if operation == "begin":
                    result = (remote.request("begin", **owner, previous_lease=None)
                              if admission.get("admitted") else {"stopped": True})
                else:
                    result = admission
        grant: dict[str, Any] = {}
        if operation == "begin":
            grant = result
        elif operation == "receipt" and value["input"]["operation"] == "begin":
            grant = result.get("response") or {}
        if "mu" in grant:
            # No map/control lock during bounded model-capacity waits: the
            # observer's commit/release must remain able to free the old mu.
            self.scorer.adopt(grant["mu"])
        with self.controls:
            if "mu" in grant:
                lease, key = grant["lease"], grant["mu"]["snapshot_sha256"]
                episode = (value["input"]["episode"] if operation == "receipt"
                           else value["episode"])
                lease_key = (grant["mu"].get("training_generation", ""), lease)
                if lease_key not in self.leases:
                    self.scorer.retain_mu(key)
                    self.leases[lease_key] = (key, episode)
            elif operation == "commit" and ("segment_id" in result
                                            or result.get("reason") == "generation_changed"):
                lease_key = (fields["mu"].get("training_generation", ""), fields["segment_id"])
                held = self.leases.pop(lease_key, None)
                if held is not None:
                    self.scorer.release_mu(held[0])
            elif operation == "episode_end":
                for lease, (key, episode) in list(self.leases.items()):
                    if episode == fields.get("episode"):
                        self.scorer.release_mu(key)
                        del self.leases[lease]
        return result

    def release_discarded_lease(self, generation: str, lease: str) -> None:
        # This comes from the producer after it has observed explicit restore
        # revocation. A later grant alone is not proof the observer has read old rows.
        with self.controls:
            held = self.leases.pop((generation, lease), None)
            if held is not None:
                self.scorer.release_mu(held[0])

    def close(self) -> dict[str, Any]:
        self.scorer.close()
        return super().close()


class ActorControlConnection(SegmentConnection):
    """The producer's control channel and direct same-process numerical helper."""

    def __init__(self, pool: ActorInferencePool) -> None:
        super().__init__(pool.attachment())
        self.scorer = pool.scorer
        self.pool = pool

    def release_discarded_lease(self, generation: str, lease: str) -> None:
        self.pool.release_discarded_lease(generation, lease)
