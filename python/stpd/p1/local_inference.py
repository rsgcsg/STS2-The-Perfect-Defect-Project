"""Ephemeral batched scoring owned by one local evaluation command.

Actors keep the existing consume/ACK/next driver and their independent state.
Only their public observations reach this in-process queue; the model's existing
forward_batch owns scoring. There is no daemon, registry or gameplay controller.
"""

from __future__ import annotations

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

from .data import json_bytes
from .export import adapter_identity, load_model
from .input import INPUT_SPEC, project

AUTH_ENV = "STPD_P1_LOCAL_INFERENCE_AUTH"
SCHEMA = "stpd/p1-local-inference-1"
# The port carries the same complete observations as the existing Agent port.
MAX_BYTES = 64 * 1024 * 1024


@dataclass
class _Request:
    model: str
    observation: dict[str, Any]
    answer: Future


class LocalInferencePool:
    """One command owns the listener, queue, models and explicit close."""

    def __init__(self, packages: dict[str, Path], *, max_batch: int, window_ms: float) -> None:
        if (
            type(max_batch) is not int
            or not 1 <= max_batch <= 16
            or type(window_ms) not in {int, float}
            or not 0 <= window_ms <= 20
        ):
            raise ValueError("invalid local inference batch bounds")
        torch.set_num_threads(2)
        self.models = {sha: load_model(path, sha)[0] for sha, path in packages.items()}
        self.max_batch, self.window = max_batch, window_ms / 1000
        self.pool_id = secrets.token_hex(16)
        self.auth = secrets.token_bytes(32)
        self.adapter = adapter_identity()
        self.listener = Listener(("127.0.0.1", 0), authkey=self.auth)
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
                    or value.get("input_projection") != INPUT_SPEC
                    or type(value.get("request_id")) is not int
                ):
                    raise ValueError("local inference binding differs")
                answer: Future = Future()
                self.queue.put(_Request(value["package_sha256"], value["observation"], answer))
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
                        valid.append((item, project(item.observation, input_projection=INPUT_SPEC)))
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
            or projection != INPUT_SPEC
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
