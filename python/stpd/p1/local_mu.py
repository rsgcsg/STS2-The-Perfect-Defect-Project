"""Actor-owned immutable CPU mu and loopback scoring, never a World executor."""
from __future__ import annotations

import copy
import hashlib
import os
import random
import socket
import tempfile
import threading
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

import torch

from spireagent.artifact_contracts import Parent
from spireagent.rl_contracts import MuScore
from spireagent.rl_socket_transport import NodeMuCache
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
        self.node_cache: NodeMuCache | None = None

    def _load(self, identity: dict[str, Any]) -> Any:
        # Download only this grant, never walk all intermediate publications. The
        # temporary package is owned here; no existing Store asset is removed.
        if self.node_cache is not None:
            with self.node_cache.materialize(identity) as (package, head, raw):
                model, _ = load_model(package / "model.tensor-tree", identity["model_sha256"])
        else:
            with tempfile.TemporaryDirectory(prefix="mu-", dir=self.cache_root) as temporary:
                package = self.exchange.materialize_model(identity, Path(temporary))
                model, _ = load_model(package / "model.tensor-tree", identity["model_sha256"])
            head = self.exchange.store.get_manifest(identity["heads_artifact_id"])
            raw = b"".join(self.exchange.store.read_payload(head.payload("checkpoint")))
        payload = head.payload("checkpoint")
        if (head.kind != "checkpoint" or payload.sha256 != identity["heads_sha256"]
                or head.parameters.value().get("schema") != "stpd/rl-behavior-heads-1"
                or head.parents != (Parent("policy", identity["model_artifact_id"]),)):
            raise ValueError("behavior_heads_binding_changed")
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

    def adopt(self, identity: dict[str, Any], *, retain: bool = False) -> None:
        key = identity["snapshot_sha256"]
        with self.capacity:
            if self.closed:
                raise ValueError("actor_mu_owner_closed")
            if key in self.identities and self.identities[key] != identity:
                raise ValueError("same_mu_identity_changed")
            self._retire(keep={key})
            if key not in self.models:
                while (key not in self.models and len(self.models) >= self.max_snapshots
                       and not self.closed):
                    # The previous observer can still be committing a tail.
                    # Pause begin/await, rather than failing a live World or
                    # evicting a referenced mu to make room for the new grant.
                    self.capacity.wait()
                if self.closed:
                    raise ValueError("actor_mu_owner_closed")
                if key not in self.models:
                    started = time.perf_counter()
                    model = self._load(identity)
                    self.models[key] = model
                    self.identities[key] = copy.deepcopy(identity)
                    self.compilers[key] = FrameCompiler(model.vocabulary)
                    self.load_seconds.append(time.perf_counter() - started)
                elif self.identities[key] != identity:
                    raise ValueError("same_mu_identity_changed")
            if key != self.latest:
                # Invalidate at adoption, after any old-tail capacity wait.
                self.text_cache.retire(set())
            self.latest = key
            if retain:
                self.references[key] = self.references.get(key, 0) + 1

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

    def adopt(self, identity: dict[str, Any], *, retain: bool = False) -> None:
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
            if retain:
                self.references[key] = self.references.get(key, 0) + 1

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
        self.episode_owners: dict[tuple[str, str], dict[str, str]] = {}
        self.controls = threading.RLock()
        self.remote_channels = threading.local()
        self.control_timing = {"begin_wait_seconds": 0., "begin_calls": 0,
                               "mu_adopt_seconds": 0.}
        self.timing_samples: dict[str, deque[float]] = {}
        self.timing_counts: dict[str, int] = {}
        self.generated = {"generated_R": 0, "generated_D": 0, "generated_segments": 0}
        self.node_cache: NodeMuCache | None = None
        self.event_journal = None
        if attachment.get("dataplane"):
            from spireagent.rl_local_wal import LocalControlWAL
            from spireagent.rl_socket_transport import NodeMuCache, ensure_fd_capacity

            if not isinstance(scorer, LocalMuScorer):
                raise ValueError("binary_dataplane_requires_actor_local_mu")
            self.fd_limits = ensure_fd_capacity(1)
            node_root = Path(tempfile.gettempdir()) / f"stpd-node-mu-{os.getuid()}" / (
                attachment["run"]) / attachment["training_generation"]
            self.node_cache = NodeMuCache(attachment, node_root / "snapshots",
                interval=attachment.get("mu_pull_interval_seconds", 15),
                max_bytes=attachment.get("mu_cache_bytes", 512 * 1024**2))
            scorer.node_cache = self.node_cache
            journal_name = hashlib.sha256(str(scorer.cache_root).encode()).hexdigest()
            self.event_journal = LocalControlWAL(
                node_root / "journals" / (journal_name + ".sqlite"),
                run=attachment["run"], generation=attachment["training_generation"])
            exchange = scorer.exchange
            exchange.live_generation = attachment["training_generation"]
            exchange.model_provider = self.node_cache
            exchange.event_sink = self._publish_event
            exchange.stop_sink = lambda reason, generation: self._remote_request("stop",
                run=attachment["run"],
                training_generation=generation or attachment["training_generation"],
                reason=reason, epoch=1)
            exchange.generation_source = lambda generation: generation != self._remote_request(
                "status")["training_generation"]
        # Each connection gets its own control channel: a blocked begin must not
        # prevent the observer's commit/status/episode_end from reaching learner.
        super().__init__(attachment["vocabulary"], {"snapshot_sha256": "unadopted"},
            control=None, address=("127.0.0.1", 0), segment_steps=attachment["segment_steps"],
            compiler_identity=attachment["compiler_identity"])

    def attachment(self) -> dict[str, Any]:
        return {**super().attachment(),
                **{key: self.remote_attachment[key] for key in ("run", "training_generation")
                   if key in self.remote_attachment}, "role": "actor_local_mu",
                **({"dataplane": self.remote_attachment["dataplane"],
                    "learner_endpoint": self.remote_attachment} if self.node_cache else {})}

    @contextmanager
    def _remote_channel(self) -> Iterator[SegmentConnection]:
        remote = getattr(self.remote_channels, "connection", None)
        if remote is None:
            remote = SegmentConnection(self.remote_attachment)
            self.remote_channels.connection = remote
        try:
            yield remote
        except BaseException:
            remote.close()
            self.remote_channels.connection = None
            raise

    def _remote_request(self, operation: str, **fields: Any) -> dict[str, Any]:
        with self._remote_channel() as remote:
            return remote.request(operation, **fields)

    def _publish_event(self, event: dict[str, Any]) -> None:
        assert self.event_journal is not None
        self.event_journal.append("event:" + event["record_sha256"], event, {}, kind="event")
        self._remote_request("lifecycle_event", event=event)

    def observe_timing(self, name: str, seconds: float) -> None:
        with self.controls:
            self.control_timing[name] = self.control_timing.get(name, 0.) + seconds
            self.timing_samples.setdefault(name, deque(maxlen=256)).append(seconds)
            self.timing_counts[name] = self.timing_counts.get(name, 0) + 1

    def timing_report(self) -> dict[str, Any]:
        with self.controls:
            samples = {}
            for name, values in self.timing_samples.items():
                ordered = sorted(values)
                samples[name] = {"n": self.timing_counts[name], "sampled": len(ordered),
                    "p50": ordered[len(ordered) // 2],
                    "p95": ordered[min(len(ordered) - 1, int(len(ordered) * .95))]}
            return {**self.control_timing, **self.generated, "latency_seconds": samples,
                "sample_scope": "last 256 observations per metric; producer sums one segment",
                "overlap": "control, producer and native intervals may overlap; do not sum",
                "unattributed_wall_seconds": None}

    def _actor(self, connection: Any) -> None:
        try:
            super()._actor(connection)
        finally:
            remote = getattr(self.remote_channels, "connection", None)
            if remote is not None:
                remote.close()

    def _adopt_grant(self, grant: dict[str, Any], actor: str, episode: str,
                     owner: dict[str, str] | None) -> None:
        lease_key = (grant["mu"].get("training_generation", ""), grant["lease"])
        owner_key = actor, episode
        with self.controls:
            if owner is None or self.episode_owners.get(owner_key) is not owner:
                return
            if lease_key in self.leases:
                return
        # Adopt and acquire the lease's pin in one model critical section. Do
        # not hold controls across capacity waits: old commits must progress.
        self.scorer.adopt(grant["mu"], retain=True)
        with self.controls:
            if (self.episode_owners.get(owner_key) is owner
                    and lease_key not in self.leases):
                self.leases[lease_key] = (grant["mu"]["snapshot_sha256"], episode)
            else:
                self.scorer.release_mu(grant["mu"]["snapshot_sha256"])

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
        pending_owner = None
        if operation == "begin":
            with self.controls:
                owner_key = fields["actor"], fields["episode"]
                if owner_key not in self.episode_owners:
                    # Direct clients bind before sending, never from a late reply.
                    self.episode_owners[owner_key] = {
                        key: fields.get(key, self.remote_attachment[key])
                        for key in ("run", "training_generation")}
                pending_owner = self.episode_owners.get(owner_key)
        client_original = None
        if self.event_journal is not None:
            from spireagent.rl_exchange import control_input, input_key

            if operation in {"episode_open", "begin", "commit", "episode_end"}:
                client_original = control_input({**fields, "operation": operation})
            elif operation == "receipt":
                client_original = control_input(fields["input"])
        def record_open(reply: dict[str, Any], current: dict[str, Any]) -> None:
            if (reply.get("admitted") and
                    reply["training_generation"] == current.get("training_generation")):
                with self.controls:
                    owner_key = fields["actor"], fields["episode"]
                    owner = {key: reply[key] for key in ("run", "training_generation")}
                    if self.episode_owners.get(owner_key) != owner:
                        self.episode_owners[owner_key] = owner

        adoption = (self.node_cache.latest() if operation == "begin" and self.node_cache
                    else nullcontext(None))
        result: dict[str, Any]
        started = time.perf_counter()
        with adoption as cached_mu, self._remote_channel() as remote:
            if cached_mu is not None:
                fields["node_cached_mu_snapshot"] = cached_mu["snapshot_sha256"]
                fields["node_id"] = socket.gethostname()
            if client_original is not None and operation != "receipt":
                assert self.event_journal is not None
                remote_original = control_input({**fields, "operation": operation})
                key = "intent:" + input_key(client_original)
                intent = self.event_journal.receipt(key, client_original)
                if intent["found"]:
                    remote_original = intent["response"]["remote_input"]
                    fields = {key: item for key, item in remote_original.items()
                              if key != "operation"}
                else:
                    self.event_journal.append(key, client_original,
                        {"remote_input": remote_original}, kind="control_intent")
                result = remote.request(operation, **fields)
            elif client_original is not None:
                assert self.event_journal is not None
                local = self.event_journal.receipt("control:" + input_key(client_original),
                                                   client_original)
                if local["found"]:
                    result = {**local, "durability_scope": "actor_witness"}
                else:
                    intent = self.event_journal.receipt("intent:" + input_key(client_original),
                                                       client_original)
                    result = (remote.request("receipt", input=intent["response"]["remote_input"])
                              if intent["found"] else local)
            else:
                result = remote.request(operation, **fields)
            if operation == "episode_open":
                record_open(result, remote.attachment)
            if operation in {"begin", "episode_open"} and result.get("reason") == (
                    "generation_changed"):
                # Re-admit the same live World under the new learning generation,
                # not a new seed/episode execution. Original absent control only.
                owner = {"actor": fields["actor"], "episode": fields["episode"],
                         "run": remote.attachment["run"],
                         "training_generation": remote.attachment["training_generation"]}
                admission = remote.request("episode_open", **owner)
                with self.controls:
                    owner_key = fields["actor"], fields["episode"]
                    if (operation == "begin" and
                            self.episode_owners.get(owner_key) is not pending_owner):
                        pending_owner = None
                    else:
                        record_open(admission, remote.attachment)
                        pending_owner = self.episode_owners.get(owner_key)
                if operation == "begin":
                    result = (remote.request("begin", **owner, previous_lease=None)
                              if admission.get("admitted") and pending_owner is not None
                              else {"stopped": True})
                else:
                    result = admission
            # Keep the node cache's shared pin until the full model and heads
            # have been loaded; release the global refresh lock before begin.
            if operation == "begin" and "mu" in result:
                adopt_started = time.perf_counter()
                self._adopt_grant(result, fields["actor"], fields["episode"], pending_owner)
                self.control_timing["mu_adopt_seconds"] += time.perf_counter() - adopt_started
        if operation == "begin":
            self.observe_timing("begin_wait_seconds", time.perf_counter() - started)
            self.control_timing["begin_calls"] += 1
        if self.event_journal is not None:
            from spireagent.rl_exchange import control_input, input_key

            original = client_original
            response: dict[str, Any] | None = result
            if operation == "receipt":
                original = client_original if result.get("found") else None
                response = result.get("response")
            if original is not None:
                assert response is not None
                self.event_journal.append("control:" + input_key(original), original, response)
        # A receipt is a historical fact, not a new lease. Remote ACK loss is
        # reconciled inside request(begin), before the normal adoption above.
        if operation == "status":
            result = {**result, "actor_timing": {**self.timing_report(),
                "mu_inference_seconds": getattr(self.scorer, "score_seconds", 0.)},
                **({'node_mu_cache': self.node_cache.status(), 'actor_fd_limits': self.fd_limits}
                   if self.node_cache is not None else {})}
        with self.controls:
            if operation == "commit" and ("segment_id" in result
                                            or result.get("reason") == "generation_changed"):
                lease_key = (fields["mu"].get("training_generation", ""), fields["segment_id"])
                held = self.leases.pop(lease_key, None)
                if held is not None:
                    self.scorer.release_mu(held[0])
            elif operation == "episode_end":
                self.release_closed_episode(fields["actor"], fields["episode"],
                                            fields.get("training_generation", ""))
        return result

    def release_closed_episode(self, actor: str, episode: str, generation: str | None) -> None:
        """Release local readers only; this records no remote delivery outcome."""
        with self.controls:
            owner_key = actor, episode
            current = self.episode_owners.get(owner_key)
            current_close = current is not None and (
                generation is None or current["training_generation"] == generation)
            if current_close:
                self.episode_owners.pop(owner_key)
            for lease, (key, lease_episode) in list(self.leases.items()):
                if lease_episode == episode and (
                        generation is None or current_close or lease[0] == generation):
                    self.scorer.release_mu(key)
                    del self.leases[lease]

    def release_discarded_lease(self, generation: str, lease: str) -> None:
        # This comes from the producer after it has observed explicit restore
        # revocation. A later grant alone is not proof the observer has read old rows.
        with self.controls:
            held = self.leases.pop((generation, lease), None)
            if held is not None:
                self.scorer.release_mu(held[0])

    def close(self) -> dict[str, Any]:
        self.scorer.close()
        result = super().close()
        if self.event_journal is not None:
            self.event_journal.close()
        return result


class ActorControlConnection(SegmentConnection):
    """The producer's control channel and direct same-process numerical helper."""

    def __init__(self, pool: ActorInferencePool) -> None:
        super().__init__(pool.attachment())
        self.scorer = pool.scorer
        self.pool = pool

    def episode_owner(self, actor: str, episode: str) -> dict[str, str]:
        with self.pool.controls:
            owner = self.pool.episode_owners.get((actor, episode))
            if owner is None:
                from spireagent.json_boundary import BoundaryError

                raise BoundaryError("rl_actor", "episode_admission_owner_missing")
            return dict(owner)

    def release_discarded_lease(self, generation: str, lease: str) -> None:
        self.pool.release_discarded_lease(generation, lease)

    def release_closed_episode(self, actor: str, episode: str) -> None:
        self.pool.release_closed_episode(actor, episode, None)
