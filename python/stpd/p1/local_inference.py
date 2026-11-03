"""Ephemeral batched scoring owned by one local evaluation command.

Actors keep the existing consume/ACK/next driver and their independent state.
Only their public observations reach this in-process queue; the model's existing
forward_batch owns scoring. There is no daemon, registry or gameplay controller.
"""

from __future__ import annotations

import copy
import json
import os
import queue
import secrets
import socket
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Connection, Listener
from pathlib import Path
from typing import Any, cast

import torch

from .data import digest, json_bytes
from .export import adapter_identity, bound_projection, load_model
from .frame_compiler import FrameCompiler
from .input import project, validate_input_spec
from .tensor_input import TensorFrame

AUTH_ENV = "STPD_P1_LOCAL_INFERENCE_AUTH"
SCHEMA = "stpd/p1-local-inference-1"
# The port carries the same complete observations as the existing Agent port.
MAX_BYTES = 64 * 1024 * 1024


@dataclass
class _Request:
    model: str
    observation: dict[str, Any]
    answer: Future
    projection: dict[str, Any]


@dataclass
class _CompiledRequest:
    model: str
    frame: TensorFrame
    answer: Future
    floor: int
    queued_at: float


class LocalInferencePool:
    """One command owns the listener, queue, models and explicit close."""

    def __init__(self, packages: dict[str, Path], *, max_batch: int, window_ms: float,
                 models: dict[str, Any] | None = None,
                 address: tuple[str, int] = ("127.0.0.1", 0),
                 authkey: bytes | None = None) -> None:
        if (
            type(max_batch) is not int
            or not 1 <= max_batch <= (64 if models is not None else 16)
            or type(window_ms) not in {int, float}
            or not 0 <= window_ms <= 20
        ):
            raise ValueError("invalid local inference batch bounds")
        if models is None:
            torch.set_num_threads(2)
        self.models = (models if models is not None else
                       {sha: load_model(path, sha)[0] for sha, path in packages.items()})
        self.max_batch, self.window = max_batch, window_ms / 1000
        self.pool_id = secrets.token_hex(16)
        if authkey is not None and len(authkey) != 32:
            raise ValueError("inference_capability_must_be_32_bytes")
        self.auth = secrets.token_bytes(32) if authkey is None else authkey
        self.adapter = adapter_identity()
        self.listener = Listener(address, authkey=self.auth)
        self.address = cast(tuple[str, int], self.listener.address)
        self.queue: queue.Queue[_Request | None] = queue.Queue()
        self.stopping = threading.Event()
        self.connections: list[threading.Thread] = []
        self.stats: dict[str, Any] = {
            "requests": 0,
            "batches": 0,
            "batch_sizes": [],
            "score_seconds": 0.0,
            "errors": 0,
        }
        self.worker = threading.Thread(
            target=self._score, name="p1-local-inference-score", daemon=True
        )
        self.acceptor = threading.Thread(
            target=self._accept, name="p1-local-inference-actors", daemon=True
        )
        self.worker.start()
        self.acceptor.start()

    def attachment(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "pool_id": self.pool_id,
            "address": list(self.address),
            "adapter": self.adapter,
            "max_batch": self.max_batch,
            "window_ms": self.window * 1000,
        }

    def environment(self) -> dict[str, str]:
        # The capability remains environment-only and is never in a report/attachment.
        return {AUTH_ENV: self.auth.hex()}

    def _accept(self) -> None:
        while not self.stopping.is_set():
            try:
                connection = self.listener.accept()
            except (AuthenticationError, EOFError, ConnectionError):
                continue
            except OSError:
                return
            if self.stopping.is_set():
                connection.close()
                return
            thread = threading.Thread(target=self._actor, args=(connection,), daemon=True)
            self.connections[:] = [item for item in self.connections if item.is_alive()]
            self.connections.append(thread)
            thread.start()

    def _actor(self, connection: Connection) -> None:
        try:
            while not self.stopping.is_set():
                if not connection.poll(0.2):
                    continue
                value = json.loads(connection.recv_bytes(MAX_BYTES))
                if (
                    not isinstance(value, dict)
                    or value.get("schema") != SCHEMA
                    or value.get("pool_id") != self.pool_id
                    or value.get("adapter") != self.adapter
                    or value.get("package_sha256") not in self.models
                    or type(value.get("request_id")) is not int
                ):
                    raise ValueError("local inference binding differs")
                projection = bound_projection(
                    self.models[value["package_sha256"]].vocabulary, value.get("input_projection"))
                answer: Future = Future()
                self.queue.put(_Request(value["package_sha256"], value["observation"], answer,
                                        projection))
                try:
                    response = {
                        "request_id": value["request_id"],
                        "scores": answer.result(timeout=60),
                    }
                except Exception as error:
                    response = {"request_id": value["request_id"], "error": type(error).__name__}
                connection.send_bytes(json_bytes(response))
        except (OSError, EOFError, ValueError, TypeError, KeyError):
            # A failed actor owns that connection. No request is retried or rerouted.
            return
        finally:
            connection.close()

    def _score(self) -> None:
        while True:
            first = self.queue.get()
            if first is None:
                return
            batch = [first]
            deadline = time.monotonic() + self.window
            while len(batch) < self.max_batch:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = self.queue.get(timeout=remaining)
                except queue.Empty:
                    break
                if item is None:
                    self.queue.put(None)
                    break
                batch.append(item)
            for package in dict.fromkeys(item.model for item in batch):
                jobs = [item for item in batch if item.model == package]
                started = time.monotonic()
                valid = []
                for item in jobs:
                    try:
                        valid.append((item, project(item.observation,
                                                    input_projection=item.projection)))
                    except Exception as error:
                        item.answer.set_exception(error)
                        self.stats["errors"] += 1
                try:
                    active = [
                        (index, frame) for index, (_, frame) in enumerate(valid) if frame.choices
                    ]
                    scores: list[list[float]] = [[] for _ in valid]
                    if active:
                        with torch.inference_mode():
                            outputs = self.models[package].forward_batch(
                                [frame for _, frame in active]
                            )
                        for (index, _), output in zip(active, outputs, strict=True):
                            scores[index] = output.tolist()
                    for (item, _), score in zip(valid, scores, strict=True):
                        item.answer.set_result(score)
                    if valid:
                        self.stats["requests"] += len(valid)
                        self.stats["batches"] += 1
                        self.stats["batch_sizes"].append(len(valid))
                except Exception as error:
                    for item, _ in valid:
                        item.answer.set_exception(error)
                        self.stats["errors"] += 1
                self.stats["score_seconds"] += time.monotonic() - started

    def close(self) -> dict[str, Any]:
        if not self.stopping.is_set():
            self.stopping.set()
            # Wake accept without waiting for an authentication reply. An already
            # failed acceptor cannot answer a self-authenticated Client handshake.
            if self.acceptor.is_alive():
                with socket.create_connection(self.address, timeout=2):
                    pass
            self.acceptor.join(timeout=5)
            self.listener.close()
            for thread in self.connections:
                thread.join(timeout=60)
            self.queue.put(None)
            self.worker.join(timeout=60)
        if (
            self.acceptor.is_alive()
            or self.worker.is_alive()
            or any(t.is_alive() for t in self.connections)
        ):
            raise RuntimeError("local inference owner did not close")
        return {"schema": SCHEMA, "closed": True, "pool_id": self.pool_id, **self.stats}


class LocalInferenceClient:
    def __init__(
        self, attachment: dict[str, Any], package: str, projection: dict[str, Any]
    ) -> None:
        if (
            attachment.get("schema") != SCHEMA
            or attachment.get("adapter") != adapter_identity()
            or validate_input_spec(projection) != projection
        ):
            raise ValueError("local inference attachment differs")
        self.attachment, self.package, self.projection = attachment, package, projection
        self.request_id = 0
        self.connection = Client(
            tuple(attachment["address"]), authkey=bytes.fromhex(os.environ[AUTH_ENV])
        )

    def __call__(self, observation: dict[str, Any]) -> list[float]:
        self.request_id += 1
        self.connection.send_bytes(
            json_bytes(
                {
                    "schema": SCHEMA,
                    "pool_id": self.attachment["pool_id"],
                    "adapter": self.attachment["adapter"],
                    "request_id": self.request_id,
                    "package_sha256": self.package,
                    "input_projection": self.projection,
                    "observation": observation,
                }
            )
        )
        if not self.connection.poll(60):
            raise TimeoutError("local inference reply unavailable; request not retried")
        response = json.loads(self.connection.recv_bytes(MAX_BYTES))
        if response.get("request_id") != self.request_id or "error" in response:
            raise ValueError("local inference score failed")
        return cast(list[float], response["scores"])

    def close(self) -> None:
        self.connection.close()


SEGMENT_SCHEMA = "stpd/rl-inference-2"


class SegmentInferencePool(LocalInferencePool):
    """The same owned TCP/scorer queue, with immutable RL behavior snapshots.

    Training and scoring share one GPU lock at microbatch boundaries. The control
    callback belongs to the existing learner's capacity/commit owner; this service
    does not choose actions, validate legality, own worlds or write a new ledger.
    """

    def __init__(self, model: Any, identity: dict[str, Any], *, control: Any,
                 gpu_lock: Any, max_batch: int = 40, window_ms: float = 2,
                 address: tuple[str, int] | None = None, segment_steps: int = 64,
                 compiler_identity: dict[str, Any] | None = None) -> None:
        if model.execution.implementation not in {
                "experimental-packed-v1", "experimental-joint-nodes-v1"}:
            raise ValueError("RL_inference_requires_explicit_packed_execution")
        if any(parameter.requires_grad for parameter in model.parameters()):
            raise ValueError("RL_inference_requires_immutable_snapshot")
        self.compiler = FrameCompiler(model.vocabulary)
        self.compiler_identity = copy.deepcopy(
            self.compiler.identity if compiler_identity is None else compiler_identity)
        if any(self.compiler_identity.get(field) != self.compiler.identity[field]
               for field in ("input_spec", "vocabulary_sha256")):
            raise ValueError("RL_compiler_vocabulary_binding_differs")
        self.vocabulary = copy.deepcopy(model.vocabulary)
        key = identity["snapshot_sha256"]
        self.snapshots = {key: identity}
        self.latest = key
        self.control, self.gpu_lock = control, gpu_lock
        if type(segment_steps) is not int or not 1 <= segment_steps <= 64:
            raise ValueError("invalid_RL_segment_steps")
        self.segment_steps = segment_steps
        from .text_cache import TextVectorCache

        self.text_cache = TextVectorCache()
        model.text_cache, model.text_cache_namespace = self.text_cache, key
        self.model_lock = threading.RLock()
        ip = socket.gethostbyname(socket.gethostname())
        inherited = os.environ.get(AUTH_ENV)
        try:
            authkey = bytes.fromhex(inherited) if inherited is not None else None
        except ValueError:
            raise ValueError("invalid_inherited_inference_capability") from None
        super().__init__({}, max_batch=max_batch, window_ms=window_ms,
            models={key: model}, address=address or (ip, 0), authkey=authkey)

    def attachment(self) -> dict[str, Any]:
        return {**super().attachment(), "schema": SEGMENT_SCHEMA,
                "segment_steps": self.segment_steps, "vocabulary": copy.deepcopy(self.vocabulary),
                "compiler_identity": copy.deepcopy(self.compiler_identity)}

    def close(self) -> dict[str, Any]:
        return {**super().close(), "schema": SEGMENT_SCHEMA,
                "text_cache": self.text_cache.report()}

    def publish(self, model: Any, identity: dict[str, Any]) -> None:
        if any(parameter.requires_grad for parameter in model.parameters()):
            raise ValueError("RL_inference_requires_immutable_snapshot")
        if digest(json_bytes(model.vocabulary)) != self.compiler_identity["vocabulary_sha256"]:
            raise ValueError("RL_compiler_vocabulary_binding_differs")
        key = identity["snapshot_sha256"]
        model.text_cache, model.text_cache_namespace = self.text_cache, key
        with self.model_lock:
            if key in self.models:
                raise ValueError("behavior_snapshot_already_published")
            self.models[key] = model
            self.snapshots[key] = identity
            self.latest = key

    def retire(self, protected: set[str]) -> None:
        with self.model_lock, self.gpu_lock:
            for key in list(self.models):
                if key not in protected and key != self.latest:
                    del self.models[key]
                    del self.snapshots[key]
            self.text_cache.retire(protected | {self.latest})

    def _actor(self, connection: Connection) -> None:
        try:
            while not self.stopping.is_set():
                if not connection.poll(.2):
                    continue
                value = json.loads(connection.recv_bytes(MAX_BYTES))
                if (value.get("schema") != SEGMENT_SCHEMA
                        or value.get("pool_id") != self.pool_id
                        or value.get("adapter") != self.adapter
                        or type(value.get("request_id")) is not int):
                    raise ValueError("RL inference binding differs")
                try:
                    if value["operation"] != "score":
                        if value["operation"] not in {"status", "receipt"}:
                            from spireagent.rl_exchange import control_input, input_key

                            if value.get("input_key") != input_key(control_input(value)):
                                raise ValueError("RL input key differs")
                        response = self.control(value)
                    else:
                        key = value["snapshot_sha256"]
                        with self.model_lock:
                            model = self.models[key]
                            bound_projection(model.vocabulary, value["input_projection"])
                        frame = self.compiler.decode(value["compiled_input"],
                            expected_identity=self.compiler_identity)
                        answer: Future = Future()
                        # Only already compiled public metadata enters the queue.
                        cast(queue.Queue[_CompiledRequest | None], self.queue).put(
                            _CompiledRequest(key, frame, answer, value["floor"],
                                             time.perf_counter()))
                        response = answer.result(timeout=120)
                    connection.send_bytes(json_bytes({"request_id": value["request_id"],
                                                       "result": response}))
                except Exception as error:
                    connection.send_bytes(json_bytes({"request_id": value["request_id"],
                        "error": type(error).__name__, "error_code": getattr(error, "code", None)}))
        except (OSError, EOFError, ValueError, TypeError, KeyError):
            return  # No inference or control request is automatically retried.
        finally:
            connection.close()

    def _score(self) -> None:
        from stpd.p1.execution import microbatches

        pending = cast(queue.Queue[_CompiledRequest | None], self.queue)
        while True:
            first = pending.get()
            if first is None:
                return
            batch = [first]
            deadline = time.perf_counter() + self.window
            while len(batch) < self.max_batch:
                remaining = deadline-time.perf_counter()
                if remaining <= 0:
                    break
                try:
                    item = pending.get(timeout=remaining)
                except queue.Empty:
                    break
                if item is None:
                    pending.put(None)
                    break
                batch.append(item)
            for key in dict.fromkeys(item.model for item in batch):
                jobs = [item for item in batch if item.model == key]
                started = time.perf_counter()
                try:
                    with self.model_lock:
                        model, identity = self.models[key], self.snapshots[key]
                    frames = [item.frame for item in jobs]
                    for indices in microbatches(frames, list(range(len(jobs))), model):
                        group = [jobs[i] for i in indices]
                        entered = time.perf_counter()
                        with self.gpu_lock, torch.inference_mode():
                            scores, outcome, _ = model.forward_rl4([frames[i] for i in indices],
                                [item.floor for item in group], check_finite=True)
                            probabilities = outcome.float().softmax(-1).cpu().tolist()
                            output = [value.float().cpu().tolist() for value in scores]
                        elapsed = time.perf_counter()-started
                        for item, score, probability in zip(
                                group, output, probabilities, strict=True):
                            item.answer.set_result({"scores": score,
                                "outcome_probabilities": probability, "mu": identity,
                                "server_seconds": elapsed, "batch_size": len(group),
                                "queue_seconds": entered-item.queued_at})
                        self.stats["requests"] += len(group)
                        self.stats["batches"] += 1
                        histogram = self.stats.setdefault("batch_histogram", {})
                        histogram[str(len(group))] = histogram.get(str(len(group)), 0) + 1
                except Exception as error:
                    for item in jobs:
                        if not item.answer.done():
                            item.answer.set_exception(error)
                    self.stats["errors"] += len(jobs)
                self.stats["score_seconds"] += time.perf_counter()-started


class SegmentConnection:
    """Persistent scoring; lost control replies resolve the same durable input key."""

    def __init__(self, attachment: dict[str, Any]) -> None:
        if (attachment.get("schema") != SEGMENT_SCHEMA
                or attachment.get("adapter") != adapter_identity()):
            raise ValueError("RL service attachment differs")
        self.attachment = attachment
        self.connection = Client(tuple(attachment["address"]),
                                 authkey=bytes.fromhex(os.environ[AUTH_ENV]))
        self.request_id = 0

    def request(self, operation: str, **fields: Any) -> dict[str, Any]:
        from spireagent.rl_exchange import control_input, input_key

        if operation not in {"score", "status", "receipt"}:
            original = control_input({**fields, "operation": operation})
            fields = {**fields, "input_key": input_key(original)}
        else:
            original = None
        try:
            return self._request_once(operation, fields)
        except (OSError, EOFError, TimeoutError):
            if original is None:
                raise  # Scoring/World actions are never automatically replayed.
            self.connection.close()
            self.connection = Client(tuple(self.attachment["address"]),
                                     authkey=bytes.fromhex(os.environ[AUTH_ENV]))
            receipt = self._request_once("receipt", {"input": original})
            if receipt["found"]:
                return cast(dict[str, Any], receipt["response"])
            # The original owner explicitly found no durable acceptance. Same
            # key/input, at most one further delivery; no game action is resent.
            return self._request_once(operation, fields)

    def _request_once(self, operation: str, fields: dict[str, Any]) -> dict[str, Any]:
        self.request_id += 1
        self.connection.send_bytes(json_bytes({**fields, "schema": SEGMENT_SCHEMA,
            "pool_id": self.attachment["pool_id"], "adapter": self.attachment["adapter"],
            "request_id": self.request_id, "operation": operation}))
        # A begin is one owned capacity wait, not a scoring/network deadline.
        # The Agent stays responsive through its existing await directives;
        # learner Stop or episode_end wakes this same request without a retry.
        if not self.connection.poll(None if operation in {"begin", "episode_open"} else 120):
            raise TimeoutError("RL service request unavailable; original request not retried")
        response = json.loads(self.connection.recv_bytes(MAX_BYTES))
        if response.get("request_id") != self.request_id or "error" in response:
            code = response.get('error_code') or response.get('error')
            raise ValueError(f"RL service {operation} failed: {code}")
        return cast(dict[str, Any], response["result"])

    def close(self) -> None:
        self.connection.close()


class SegmentInferenceClient(SegmentConnection):
    """One Agent's permanent connection and explicit per-consumption mu record."""

    def __init__(self, attachment: dict[str, Any], projection: dict[str, Any],
                 trace: Path) -> None:
        if (attachment.get("schema") != SEGMENT_SCHEMA
                or attachment.get("adapter") != adapter_identity()
                or validate_input_spec(projection) != projection):
            raise ValueError("RL inference attachment differs")
        self.compiler = FrameCompiler(attachment["vocabulary"])
        if (self.compiler.identity != attachment["compiler_identity"]
                or self.compiler.identity["input_spec"] != projection):
            raise ValueError("RL_producer_compiler_identity_differs")
        super().__init__(attachment)
        self.projection = projection
        self.trace = os.open(trace, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        self.unit: str | None = None
        self.cached: dict[str, Any] | None = None
        self.mu: dict[str, Any] | None = None
        self.lease: str | None = None
        self.in_segment = 0
        self.floor = 0
        self.stop_requested = False
        self.clock_offset: dict[str, float] | None = None
        self.begin: Future | None = None

    def consume(self, value: dict[str, Any]) -> list[float] | None:
        observation, catalog = value["observation"], value["catalog"]
        if not catalog:
            return []
        unit = value["consumption_unit"]["id"]
        observation_hash = digest(json_bytes(observation))
        advanced = self.unit != unit
        if advanced and (self.mu is None or self.in_segment >= self.attachment["segment_steps"]):
            if self.begin is None:
                answer: Future = Future()
                self.begin = answer

                def wait_for_capacity() -> None:
                    try:
                        began = time.time()
                        adopted = self.request("begin", actor=self.attachment["actor"],
                            episode=self.attachment["episode"], previous_lease=self.lease)
                        answer.set_result((adopted, began, time.time()))
                    except Exception as error:
                        answer.set_exception(error)

                threading.Thread(target=wait_for_capacity,
                    name="RL-segment-capacity-wait", daemon=True).start()
            if not self.begin.done():
                return None  # No scores, behavior record, or policy draw yet.
            adopted, began, received = self.begin.result()
            self.begin = None
            if adopted.get("stopped"):
                self.stop_requested = True
                return [0.0] * len(catalog)  # ACK input, then normal Close; no act.
            if type(adopted.get("server_time")) in (int, float):
                self.clock_offset = {"lower": adopted["server_time"]-received,
                                     "upper": adopted["server_time"]-began}
            self.mu, self.lease = adopted["mu"], adopted["lease"]
            self.in_segment = 0
        assert self.mu is not None  # First nonempty input adopts a behavior lease above.
        if self.unit == unit:
            if self.cached is None or self.cached["observation_sha256"] != observation_hash:
                raise ValueError("same consumption changed its RL basis")
            record = {**self.cached, "acquisition_id": value["acquisition_id"],
                "input_sha256": digest(json_bytes(value)), "lease": self.lease,
                "network_request": False, "request_seconds": 0.0,
                "compile_seconds": 0.0,
                "score_source": "reused_consumption"}
        else:
            public_floor = ((observation.get("run") or {}).get("content") or {}).get("floor")
            if type(public_floor) is int:
                self.floor = max(self.floor, public_floor)
            compile_began = time.perf_counter()
            compiled = self.compiler.encode(observation)
            compile_seconds = time.perf_counter()-compile_began
            compiled_wire_bytes = len(json_bytes(compiled))
            began = time.perf_counter()
            if len(catalog) == 1:
                result: dict[str, Any] = {"scores": [0.0], "outcome_probabilities": None,
                    "batch_size": 0, "queue_seconds": 0, "server_seconds": 0}
            else:
                result = self.request("score", snapshot_sha256=self.mu["snapshot_sha256"],
                    compiled_input=compiled, floor=self.floor, input_projection=self.projection)
                if result["mu"] != self.mu:
                    raise ValueError("actual behavior snapshot changed within segment")
            record = {"schema": "stpd/rl-behavior-decision-1", "mu": self.mu,
                "lease": self.lease, "acquisition_id": value["acquisition_id"],
                "input_sha256": digest(json_bytes(value)), "observation_sha256": observation_hash,
                "catalog_sha256": digest(json_bytes(catalog)), "consumption_unit": unit,
                "compiled_input": compiled, "compile_seconds": compile_seconds,
                "compiled_wire_bytes": compiled_wire_bytes,
                "scores": result["scores"],
                "outcome_probabilities": result["outcome_probabilities"],
                "floor": self.floor, "request_seconds": time.perf_counter()-began,
                "batch_size": result["batch_size"], "queue_seconds": result["queue_seconds"],
                "server_seconds": result["server_seconds"],
                "network_request": len(catalog) > 1,
                "clock_offset": self.clock_offset,
                "score_source": "deterministic_single_catalog" if len(catalog) == 1 else "model"}
            self.unit, self.cached = unit, record
        self.in_segment += int(advanced)
        # Append, then the trajectory producer fsyncs this journal together with
        # original Runtime bytes once per segment before atomic admission.
        raw = json_bytes(record) + b"\n"
        if os.write(self.trace, raw) != len(raw):
            raise OSError("partial behavior journal write")
        return cast(list[float], record["scores"])

    def close(self) -> None:
        self.connection.close()
        os.close(self.trace)
