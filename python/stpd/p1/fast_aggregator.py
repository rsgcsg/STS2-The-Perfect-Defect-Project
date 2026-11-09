"""Node aggregator: environment decision links -> one rl-inference-3 connection (WP4).

fast_chain only. One aggregator runs per environment node. Environment processes
connect to its Unix socket and speak `sts2.policy-runtime/fast-env-link-1`
(NDJSON, one message per line). The aggregator

* sends every `decision` line, unparsed, to the node encoder pool; a worker
  parses it once, encodes `input.observation` to a `stpd/rows-v2-row-1` row and
  checks the line's own bindings (catalog digest, catalog length, combat,
  floor, input_spec) against what it encoded;
* forwards `episode_open` / `step_outcome` / `episode_close` unchanged in
  meaning (rl-inference-3 field names), in each connection's order;
* packs rows from every connection into `score_request` packets, flushed after
  `window_ms` or at `max_rows_per_packet`, on one TCP connection with
  TCP_NODELAY;
* routes each `scored` / `refused` result back to the connection and row id
  that sent it.

It decides no legality, holds no World and never resends: the environment owns
the inference timeout (`inference.timeout_ms`, default 2 s), closes a timed-out
game as not_started and discards a late reply; the aggregator counts replies
whose connection is gone. A row that cannot be encoded or whose bindings differ
is answered `refused` (ROW_DECODE_FAILED / IDENTITY_MISMATCH) without reaching
the service. A lost service connection is fatal: every environment link is
closed (their pending rows resolve as disconnected = not_started), nothing is
reconnected or retried.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import sys
import threading
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from multiprocessing.connection import Client
from typing import Any

from spireagent.json_boundary import BoundaryError

from .inference_service import V3_MAX_MESSAGE, V3_SCHEMA, pack_message, unpack_message
from .local_inference import tcp_nodelay
from .rows_encoder_pool import EncoderPool

LINK_SCHEMA = "sts2.policy-runtime/fast-env-link-1"
_DECISION_PREFIX = f'{{"schema":"{LINK_SCHEMA}","type":"decision","row_id":'.encode()
# The environment writes these fields first, in this order (WP2 kernel.ts); the
# reader takes routing from them without parsing the observation.
_DECISION_HEAD = re.compile(
    rb'^\{"schema":"[^"]+","type":"decision","row_id":(\d+),"game_instance_id":"([A-Za-z0-9._:-]{1,128})",'
    rb'"step_index":(\d+),"consumption_id":"[^"]*","catalog_len":\d+,"catalog_sha256":"([0-9a-f]{64})"')
_BINDING_FIELDS = ("catalog_len", "in_combat", "floor")


def encode_link_decision(encoder: Any, payload: memoryview) -> tuple[dict[str, Any], bytes]:
    """Encoder-pool handler: one decision line -> (meta, row); parsed once, here."""
    from .rows_encoder import parse_public_json

    message = parse_public_json(bytes(payload))
    if not isinstance(message, dict) or message.get("type") != "decision":
        raise BoundaryError("p1_fast_aggregator", "decision_line_required")
    source = message.get("input")
    if not isinstance(source, dict) or not isinstance(source.get("observation"), dict):
        raise BoundaryError("p1_fast_aggregator", "decision_input_required")
    encoded = encoder.encode_parsed(source["observation"])
    meta = {"row_sha256": encoded.row_sha256, "observation_sha256": encoded.observation_sha256,
            "catalog_sha256": encoded.catalog_sha256, "catalog_len": encoded.catalog_len,
            "in_combat": encoded.in_combat, "floor": message.get("floor"),
            "step_index": message.get("step_index"),
            "consumption_id": message.get("consumption_id"), "binding": None}
    # The line's own claims must equal what was encoded (no repair, no fallback).
    differs = [name for name, ok in (
        ("input_spec", source.get("input_spec") == encoder.identity["input_spec"]),
        ("catalog", source.get("catalog") == source["observation"]["catalog"]),
        ("catalog_sha256", message.get("catalog_sha256") == encoded.catalog_sha256),
        ("catalog_len", message.get("catalog_len") == encoded.catalog_len
                        and encoded.catalog_len >= 1),
        ("in_combat", message.get("in_combat") is encoded.in_combat),
        ("floor", type(message.get("floor")) is int and (
            encoded.floor is None or message.get("floor") == encoded.floor)),
        ("step_index", type(message.get("step_index")) is int),
        ("consumption_id", isinstance(message.get("consumption_id"), str)),
    ) if not ok]
    if differs:
        meta["binding"] = ",".join(differs)
        return meta, b""
    return meta, encoded.row


@dataclass
class _Env:
    connection: socket.socket
    name: str
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    order: deque[Any] = field(default_factory=deque)
    ready: threading.Condition = field(default_factory=threading.Condition)
    closed: bool = False
    hello: dict[str, Any] | None = None
    sent: dict[int, int] = field(default_factory=dict)  # row_id -> request_id, until its outcome

    def write(self, message: dict[str, Any]) -> bool:
        line = json.dumps({"schema": LINK_SCHEMA, **message}, separators=(",", ":"),
                          allow_nan=False).encode() + b"\n"
        with self.send_lock:
            if self.closed:
                return False
            try:
                self.connection.sendall(line)
            except OSError:
                self.closed = True
                return False
        return True


@dataclass
class _Decision:
    env: _Env
    row_id: int
    game_instance_id: str
    catalog_sha256: str
    future: Future[Any]


class NodeAggregator:
    def __init__(self, vocabulary: dict[str, Any], *, service_address: tuple[str, int],
                 authkey: bytes, socket_path: str, aggregator_id: str,
                 python_env_sha256: str, encoder_workers: int,
                 encoder_cpus: list[int] | None = None,
                 window_ms: float = 1.5, cache_bytes: int | None = None,
                 verify_every: int = 0) -> None:
        options: dict[str, Any] = {"workers": encoder_workers, "cpus": encoder_cpus,
                                   "verify_every": verify_every,
                                   "handler": "stpd.p1.fast_aggregator:encode_link_decision"}
        if cache_bytes is not None:
            options["cache_bytes"] = cache_bytes
        self.pool = EncoderPool(vocabulary, **options)
        identity = self.pool.identity
        self.compiler_identity = {key: identity[key] for key in
                                  ("input_spec", "vocabulary_sha256", "compiler_sha256")}
        self.window = window_ms / 1000
        self.aggregator_id = aggregator_id
        self.stats: dict[str, Any] = {"rows": 0, "packets": 0, "replies": 0,
                                      "replies_without_env": 0, "refused_here": {},
                                      "refused_service": {}, "env_connections": 0}
        self._lock = threading.Condition()
        self._request_id = 0
        self._packet_id = 0
        self._rows: list[tuple[_Decision, dict[str, Any], bytes]] = []
        self._games_in_packet: set[str] = set()
        self._opened = 0.0
        self._pending: dict[int, _Decision] = {}
        self._envs: list[_Env] = []
        self._stopping = False
        self.failure: str | None = None

        self.service = Client(service_address, authkey=authkey)
        tcp_nodelay(self.service)
        self._service_send = threading.Lock()
        self._send_control({"kind": "attach", "aggregator_id": aggregator_id,
                            "chain": "fast_chain", "python_env_sha256": python_env_sha256,
                            "compiler_identity": self.compiler_identity,
                            "encoder": identity["encoder"], "row_format": identity["row_format"]})
        attached, _ = unpack_message(self.service.recv_bytes(V3_MAX_MESSAGE))
        if attached.get("kind") != "attached":
            raise BoundaryError("p1_fast_aggregator", "attach_refused")
        self.max_rows = min(int(attached["max_rows_per_packet"]), 4096)
        self.service_instance = attached["service_instance"]

        with contextlib.suppress(FileNotFoundError):
            os.unlink(socket_path)
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(socket_path)
        self.listener.listen(256)
        self.socket_path = socket_path
        targets = ((self._accept, "fast-aggregator-accept"),
                   (self._flusher, "fast-aggregator-flush"),
                   (self._replies, "fast-aggregator-replies"))
        self.threads = [threading.Thread(target=target, daemon=True, name=name)
                        for target, name in targets]
        for thread in self.threads:
            thread.start()

    # -- service side --------------------------------------------------------
    def _send_control(self, message: dict[str, Any]) -> None:
        with self._service_send:
            envelope = {"schema": V3_SCHEMA, **message}
            if message["kind"] != "step_outcome":
                self._request_id += 1
                envelope["request_id"] = self._request_id
            self.service.send_bytes(pack_message(envelope))

    def _flush_locked(self) -> None:
        """Send the open packet; request ids are assigned here, in send order."""
        if not self._rows:
            return
        rows, self._rows = self._rows, []
        self._games_in_packet = set()
        binary = bytearray()
        wire = []
        with self._service_send:
            self._packet_id += 1
            for decision, meta, row in rows:
                self._request_id += 1
                self._pending[self._request_id] = decision
                decision.env.sent[decision.row_id] = self._request_id
                wire.append({"request_id": self._request_id,
                             "game_instance_id": decision.game_instance_id,
                             "step_index": meta["step_index"],
                             "consumption_id": meta["consumption_id"],
                             "catalog_len": meta["catalog_len"],
                             "catalog_sha256": meta["catalog_sha256"],
                             "observation_sha256": meta["observation_sha256"],
                             "in_combat": meta["in_combat"], "floor": meta["floor"],
                             "payload": {"offset": len(binary), "length": len(row),
                                         "row_sha256": meta["row_sha256"]}})
                binary += row
            try:
                self.service.send_bytes(pack_message(
                    {"schema": V3_SCHEMA, "kind": "score_request",
                     "packet_id": self._packet_id, "rows": wire}, binary))
            except (OSError, ValueError) as error:
                self._fail(f"service_send:{type(error).__name__}")
                return
        self.stats["packets"] += 1
        self.stats["rows"] += len(rows)

    def _add_row(self, decision: _Decision, meta: dict[str, Any], row: bytes) -> None:
        with self._lock:
            if not self._rows:
                self._opened = time.perf_counter()
                self._lock.notify()
            self._rows.append((decision, meta, row))
            self._games_in_packet.add(decision.game_instance_id)
            if len(self._rows) >= self.max_rows:
                self._flush_locked()

    def _control(self, message: dict[str, Any]) -> None:
        with self._lock:
            # A control naming a game whose row is still in the open packet goes
            # after that row (Stop / timeout races); otherwise it goes now.
            if message.get("game_instance_id") in self._games_in_packet:
                self._flush_locked()
            try:
                self._send_control(message)
            except (OSError, ValueError) as error:
                self._fail(f"service_send:{type(error).__name__}")

    def _flusher(self) -> None:
        with self._lock:
            while not self._stopping:
                if not self._rows:
                    self._lock.wait()
                    continue
                remaining = self._opened + self.window - time.perf_counter()
                if remaining > 0:
                    self._lock.wait(remaining)
                    continue
                self._flush_locked()

    def _replies(self) -> None:
        while True:
            try:
                envelope, _ = unpack_message(self.service.recv_bytes(V3_MAX_MESSAGE))
            except (OSError, EOFError, ValueError) as error:
                if not self._stopping:
                    self._fail(f"service_receive:{type(error).__name__}")
                return
            if envelope.get("kind") != "score_response":
                self._fail("service_unexpected_message")
                return
            for result in envelope.get("results") or []:
                with self._lock:
                    decision = self._pending.pop(result.get("request_id"), None)
                if decision is None:
                    self._fail("service_reply_unknown_request")
                    return
                self.stats["replies"] += 1
                if result.get("status") == "scored":
                    reply = {key: result[key] for key in (
                        "mu_snapshot_sha256", "segment_id", "row_in_segment", "selected_index",
                        "behavior_q", "selected_q", "applied")}
                    reply.update(type="scored")
                else:
                    code = str(result.get("error_code"))
                    self.stats["refused_service"][code] = (
                        self.stats["refused_service"].get(code, 0) + 1)
                    reply = {"type": "refused", "error_code": code}
                reply.update(row_id=decision.row_id, game_instance_id=decision.game_instance_id,
                             catalog_sha256=decision.catalog_sha256)
                if not decision.env.write(reply):
                    self.stats["replies_without_env"] += 1

    # -- environment side ----------------------------------------------------
    def _accept(self) -> None:
        while not self._stopping:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            env = _Env(connection, f"env-{len(self._envs)}")
            self._envs.append(env)
            self.stats["env_connections"] += 1
            threading.Thread(target=self._read_env, args=(env,), daemon=True,
                             name="fast-aggregator-env-read").start()
            threading.Thread(target=self._forward_env, args=(env,), daemon=True,
                             name="fast-aggregator-env-forward").start()

    def _read_env(self, env: _Env) -> None:
        reader = env.connection.makefile("rb", buffering=1 << 20)
        try:
            for line in reader:
                line = line.rstrip(b"\n")
                if not line:
                    continue
                if line.startswith(_DECISION_PREFIX):
                    head = _DECISION_HEAD.match(line)
                    if head is None:
                        raise BoundaryError("p1_fast_aggregator", "decision_head")
                    row_id, game, _, catalog = head.groups()
                    try:
                        future = self.pool.submit(game.decode(), line)
                    except BoundaryError as error:
                        if error.code == "encoder_worker_died":
                            self._fail("encoder_pool_broken")  # #431 review S5(a)
                        raise
                    item: Any = _Decision(env, int(row_id), game.decode(), catalog.decode(), future)
                else:
                    item = json.loads(line)
                    if not isinstance(item, dict) or item.get("schema") != LINK_SCHEMA:
                        raise BoundaryError("p1_fast_aggregator", "link_schema")
                with env.ready:
                    env.order.append(item)
                    env.ready.notify()
        except (OSError, ValueError) as error:
            print(json.dumps({"fast_aggregator_env_closed": env.name,
                              "error": type(error).__name__,
                              "code": getattr(error, "code", None)}), file=sys.stderr, flush=True)
        finally:
            with env.ready:
                env.order.append(None)
                env.ready.notify()

    def _forward_env(self, env: _Env) -> None:
        """Hand this connection's messages on in arrival order."""
        while True:
            with env.ready:
                while not env.order:
                    env.ready.wait()
                item = env.order.popleft()
            if item is None:
                break
            try:
                if isinstance(item, _Decision):
                    self._decision(item)
                else:
                    self._link_message(env, item)
            except Exception as error:
                # Never a silently stalled link (#431 review S5(b)): log it and
                # shut this env connection; its reader then ends the queue.
                print(json.dumps({"fast_aggregator_env_failed": env.name,
                                  "error": type(error).__name__,
                                  "code": getattr(error, "code", None)}),
                      file=sys.stderr, flush=True)
                with contextlib.suppress(OSError):
                    env.connection.shutdown(socket.SHUT_RDWR)
                break
        with env.send_lock:
            env.closed = True
        with contextlib.suppress(OSError):
            env.connection.close()

    def _decision(self, decision: _Decision) -> None:
        try:
            meta, row = decision.future.result()
        except ValueError as error:  # BoundaryError: the line could not be encoded
            if getattr(error, "code", None) == "encoder_worker_died":
                # The pool is gone, not this row (#431 review S5(a)): fail loudly.
                self._fail("encoder_pool_broken")
                return
            self._refuse_here(decision, "ROW_DECODE_FAILED")
            return
        if meta["binding"] is not None:
            print(json.dumps({"fast_aggregator_binding_differs": meta["binding"],
                              "game_instance_id": decision.game_instance_id}),
                  file=sys.stderr, flush=True)
            self._refuse_here(decision, "IDENTITY_MISMATCH")
            return
        self._add_row(decision, meta, row)

    def _refuse_here(self, decision: _Decision, code: str) -> None:
        refused = self.stats["refused_here"]
        refused[code] = refused.get(code, 0) + 1
        decision.env.write({"type": "refused", "row_id": decision.row_id,
                            "game_instance_id": decision.game_instance_id,
                            "catalog_sha256": decision.catalog_sha256, "error_code": code})

    def _link_message(self, env: _Env, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "hello":
            if message.get("input_spec") != self.compiler_identity["input_spec"]:
                print(json.dumps({"fast_aggregator_hello_input_spec_differs": env.name}),
                      file=sys.stderr, flush=True)
                with contextlib.suppress(OSError):
                    env.connection.shutdown(socket.SHUT_RDWR)
                return
            env.hello = message
            return
        if env.hello is None:
            raise BoundaryError("p1_fast_aggregator", "hello_required")
        body = {key: value for key, value in message.items() if key not in {"schema", "type"}}
        if kind == "step_outcome":
            body["row_request_id"] = self._row_request_id(env, body.pop("row_id", None))
            if body["row_request_id"] is None:
                return  # the row never reached the service (refused here): nothing to resolve
        elif kind == "episode_close":
            self.pool.release(body.get("game_instance_id"))
            body = {key: body[key] for key in ("game_instance_id", "reason", "last_step_index")}
        elif kind != "episode_open":
            raise BoundaryError("p1_fast_aggregator", "unexpected_link_message")
        self._control({"kind": kind, **body})

    def _row_request_id(self, env: _Env, row_id: Any) -> int | None:
        with self._lock:
            return env.sent.pop(row_id, None) if type(row_id) is int else None

    # -- lifecycle -----------------------------------------------------------
    def _fail(self, reason: str) -> None:
        if self.failure is None:
            self.failure = reason
            print(json.dumps({"fast_aggregator_failed": reason}), file=sys.stderr, flush=True)
        self._stopping = True
        for env in list(self._envs):
            with contextlib.suppress(OSError):
                env.connection.shutdown(socket.SHUT_RDWR)

    def close(self) -> dict[str, Any]:
        self._stopping = True
        with self._lock:
            self._flush_locked()
            self._lock.notify_all()
        with contextlib.suppress(OSError):
            self.listener.close()
        for env in list(self._envs):
            with contextlib.suppress(OSError):
                env.connection.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            self.service.close()
        self.pool.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.socket_path)
        return {"aggregator_id": self.aggregator_id, "failure": self.failure, **self.stats,
                "pending": len(self._pending)}
