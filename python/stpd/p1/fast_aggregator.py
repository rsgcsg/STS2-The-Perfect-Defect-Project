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
  TCP_NODELAY per service;
* with several services (`services`, one card's K independent services), binds
  each game to one of them by `service_for_game` (a digest of the game id, so
  fixed for the game's life and never chosen per row); every message of a game
  goes to its service, in order, and each service has its own open packet;
* routes each `scored` / `refused` result back to the connection and row id
  that sent it.

It decides no legality, holds no World and never resends: the environment owns
the inference timeout (`inference.timeout_ms`, default 2 s), closes a timed-out
game as not_started and discards a late reply; the aggregator counts replies
whose connection is gone. A row that cannot be encoded or whose bindings differ
is answered `refused` (ROW_DECODE_FAILED / IDENTITY_MISMATCH) without reaching
the service. A lost service connection is fatal: every environment link is
closed (their pending rows resolve as disconnected = not_started), nothing is
reconnected or retried. With several services the loss of any one is fatal in
the same way; its games are never moved to another service.
"""
from __future__ import annotations

import contextlib
import hashlib
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

from .data import digest, json_bytes
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


def service_for_game(game_instance_id: str, services: int) -> int:
    """The service (0..services-1) a game is bound to, for all of its messages.

    A pure function of the game id, so every aggregator, a restart and a reader
    of the records agree; it is never chosen per row or by load.
    """
    if services == 1:
        return 0
    head = hashlib.sha256(game_instance_id.encode()).digest()[:8]
    return int.from_bytes(head, "big") % services


def agent_input_spec(identity: dict[str, Any]) -> dict[str, str]:
    """The model manifest's ``input.input_spec`` ({id, version, sha256}) for a compiler identity.

    The fast-env kernel sends its agent manifest's input_spec in ``hello`` and in every
    decision line; the model manifest binds ``stpd.p1.export.input_spec(vocabulary)``, so the
    same digest is recomputed here from the projection and the vocabulary digest (never the
    projection dict itself, which is not what the environment carries).
    """
    projection = identity["input_spec"]
    return {"id": projection["id"], "version": projection["version"],
            "sha256": digest(json_bytes({"projection": projection,
                                         "vocabulary_sha256": identity["vocabulary_sha256"]}))}


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
        ("input_spec", source.get("input_spec") == agent_input_spec(encoder.identity)),
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
    forward: threading.Thread | None = None  # hands this link's read messages on, in order

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
class _Upstream:
    """One service connection with its own open packet, ids and pending rows."""

    index: int
    connection: Any
    max_rows: int
    service_instance: str
    lock: threading.Condition = field(default_factory=threading.Condition)
    send: threading.Lock = field(default_factory=threading.Lock)
    request_id: int = 0
    packet_id: int = 0
    rows: list[tuple[_Decision, dict[str, Any], bytes]] = field(default_factory=list)
    games_in_packet: set[str] = field(default_factory=set)
    opened: float = 0.0
    pending: dict[int, _Decision] = field(default_factory=dict)
    sent_rows: int = 0


@dataclass
class _Decision:
    env: _Env
    row_id: int
    game_instance_id: str
    catalog_sha256: str
    future: Future[Any]


class NodeAggregator:
    def __init__(self, vocabulary: dict[str, Any], *,
                 service_address: tuple[str, int] | None = None,
                 authkey: bytes | None = None, socket_path: str, aggregator_id: str,
                 python_env_sha256: str, encoder_workers: int,
                 encoder_cpus: list[int] | None = None,
                 window_ms: float = 1.5, cache_bytes: int | None = None,
                 verify_every: int = 0,
                 services: list[tuple[tuple[str, int], bytes]] | None = None) -> None:
        # One service (service_address + authkey, the default) or a card's K
        # services as [(address, authkey), ...]; never both, never empty.
        if services is None:
            if service_address is None or authkey is None:
                raise ValueError("fast_aggregator_service_required")
            services = [(service_address, authkey)]
        elif service_address is not None or authkey is not None or not services:
            raise ValueError("fast_aggregator_services_or_service_address")
        options: dict[str, Any] = {"workers": encoder_workers, "cpus": encoder_cpus,
                                   "verify_every": verify_every,
                                   "handler": "stpd.p1.fast_aggregator:encode_link_decision"}
        if cache_bytes is not None:
            options["cache_bytes"] = cache_bytes
        self.pool = EncoderPool(vocabulary, **options)
        identity = self.pool.identity
        self.compiler_identity = {key: identity[key] for key in
                                  ("input_spec", "vocabulary_sha256", "compiler_sha256")}
        self.agent_input_spec = agent_input_spec(identity)
        self.window = window_ms / 1000
        self.aggregator_id = aggregator_id
        self.stats: dict[str, Any] = {"rows": 0, "packets": 0, "replies": 0,
                                      "replies_without_env": 0, "refused_here": {},
                                      "refused_service": {}, "env_connections": 0}
        self._stats_lock = threading.Lock()
        self._envs: list[_Env] = []
        self._stopping = False
        self._accept_lock = threading.Lock()  # close() vs a link being accepted
        self.failure: str | None = None

        self._upstreams: list[_Upstream] = []
        try:
            for index, (address, key) in enumerate(services):
                self._upstreams.append(self._attach(index, address, key, python_env_sha256,
                                                    identity))
        except BaseException:
            for upstream in self._upstreams:
                with contextlib.suppress(OSError):
                    upstream.connection.close()
            self.pool.close()
            raise
        first = self._upstreams[0]
        self.service = first.connection
        self.service_instance = first.service_instance
        self.service_instances = [upstream.service_instance for upstream in self._upstreams]
        self.max_rows = min(upstream.max_rows for upstream in self._upstreams)

        with contextlib.suppress(FileNotFoundError):
            os.unlink(socket_path)
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(socket_path)
        self.listener.listen(256)
        self.socket_path = socket_path
        targets: list[tuple[Any, tuple[Any, ...], str]] = [
            (self._accept, (), "fast-aggregator-accept")]
        for upstream in self._upstreams:
            suffix = f"-{upstream.index}" if upstream.index else ""
            targets += [(self._flusher, (upstream,), "fast-aggregator-flush" + suffix),
                        (self._replies, (upstream,), "fast-aggregator-replies" + suffix)]
        self.threads = [threading.Thread(target=target, args=args, daemon=True, name=name)
                        for target, args, name in targets]
        for thread in self.threads:
            thread.start()

    # -- service side --------------------------------------------------------
    def _attach(self, index: int, address: tuple[str, int], key: bytes,
                python_env_sha256: str, identity: dict[str, Any]) -> _Upstream:
        connection = Client(address, authkey=key)
        tcp_nodelay(connection)
        upstream = _Upstream(index, connection, 0, "")
        self._send_control(upstream, {
            "kind": "attach", "aggregator_id": self.aggregator_id, "chain": "fast_chain",
            "python_env_sha256": python_env_sha256, "compiler_identity": self.compiler_identity,
            "encoder": identity["encoder"], "row_format": identity["row_format"]})
        attached, _ = unpack_message(connection.recv_bytes(V3_MAX_MESSAGE))
        if attached.get("kind") != "attached":
            raise BoundaryError("p1_fast_aggregator", "attach_refused")
        upstream.max_rows = min(int(attached["max_rows_per_packet"]), 4096)
        upstream.service_instance = attached["service_instance"]
        return upstream

    def _bound(self, game_instance_id: Any) -> _Upstream:
        """The upstream of a game's service (the only one with several services)."""
        if len(self._upstreams) == 1:
            return self._upstreams[0]
        if not isinstance(game_instance_id, str):
            raise BoundaryError("p1_fast_aggregator", "game_instance_id_required")
        return self._upstreams[service_for_game(game_instance_id, len(self._upstreams))]

    def _count(self, key: str, amount: int = 1) -> None:
        with self._stats_lock:
            self.stats[key] += amount

    def _send_control(self, upstream: _Upstream, message: dict[str, Any]) -> None:
        with upstream.send:
            envelope = {"schema": V3_SCHEMA, **message}
            if message["kind"] != "step_outcome":
                upstream.request_id += 1
                envelope["request_id"] = upstream.request_id
            upstream.connection.send_bytes(pack_message(envelope))

    def _flush_locked(self, upstream: _Upstream) -> None:
        """Send the open packet; request ids are assigned here, in send order."""
        if not upstream.rows:
            return
        rows, upstream.rows = upstream.rows, []
        upstream.games_in_packet = set()
        binary = bytearray()
        wire = []
        with upstream.send:
            upstream.packet_id += 1
            for decision, meta, row in rows:
                upstream.request_id += 1
                upstream.pending[upstream.request_id] = decision
                decision.env.sent[decision.row_id] = upstream.request_id
                wire.append({"request_id": upstream.request_id,
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
                upstream.connection.send_bytes(pack_message(
                    {"schema": V3_SCHEMA, "kind": "score_request",
                     "packet_id": upstream.packet_id, "rows": wire}, binary))
            except (OSError, ValueError) as error:
                self._fail(self._where(upstream, f"service_send:{type(error).__name__}"))
                return
        upstream.sent_rows += len(rows)
        with self._stats_lock:
            self.stats["packets"] += 1
            self.stats["rows"] += len(rows)

    def _where(self, upstream: _Upstream, reason: str) -> str:
        return reason if len(self._upstreams) == 1 else f"{reason}@service_{upstream.index}"

    def _add_row(self, decision: _Decision, meta: dict[str, Any], row: bytes) -> None:
        upstream = self._bound(decision.game_instance_id)
        with upstream.lock:
            if not upstream.rows:
                upstream.opened = time.perf_counter()
                upstream.lock.notify()
            upstream.rows.append((decision, meta, row))
            upstream.games_in_packet.add(decision.game_instance_id)
            if len(upstream.rows) >= upstream.max_rows:
                self._flush_locked(upstream)

    def _control(self, message: dict[str, Any]) -> None:
        upstream = self._bound(message.get("game_instance_id"))
        with upstream.lock:
            # A control naming a game whose row is still in the open packet goes
            # after that row (Stop / timeout races); otherwise it goes now.
            if message.get("game_instance_id") in upstream.games_in_packet:
                self._flush_locked(upstream)
            try:
                self._send_control(upstream, message)
            except (OSError, ValueError) as error:
                self._fail(self._where(upstream, f"service_send:{type(error).__name__}"))

    def _flusher(self, upstream: _Upstream) -> None:
        with upstream.lock:
            while not self._stopping:
                if not upstream.rows:
                    upstream.lock.wait()
                    continue
                remaining = upstream.opened + self.window - time.perf_counter()
                if remaining > 0:
                    upstream.lock.wait(remaining)
                    continue
                self._flush_locked(upstream)

    def _replies(self, upstream: _Upstream) -> None:
        while True:
            try:
                envelope, _ = unpack_message(upstream.connection.recv_bytes(V3_MAX_MESSAGE))
            except (OSError, EOFError, ValueError) as error:
                if not self._stopping:
                    self._fail(self._where(upstream,
                                           f"service_receive:{type(error).__name__}"))
                return
            if envelope.get("kind") != "score_response":
                self._fail(self._where(upstream, "service_unexpected_message"))
                return
            for result in envelope.get("results") or []:
                with upstream.lock:
                    decision = upstream.pending.pop(result.get("request_id"), None)
                if decision is None:
                    self._fail(self._where(upstream, "service_reply_unknown_request"))
                    return
                self._count("replies")
                if result.get("status") == "scored":
                    reply = {key: result[key] for key in (
                        "mu_snapshot_sha256", "segment_id", "row_in_segment", "selected_index",
                        "behavior_q", "selected_q", "applied")}
                    reply.update(type="scored")
                else:
                    code = str(result.get("error_code"))
                    with self._stats_lock:
                        self.stats["refused_service"][code] = (
                            self.stats["refused_service"].get(code, 0) + 1)
                    reply = {"type": "refused", "error_code": code}
                reply.update(row_id=decision.row_id, game_instance_id=decision.game_instance_id,
                             catalog_sha256=decision.catalog_sha256)
                if not decision.env.write(reply):
                    self._count("replies_without_env")

    # -- environment side ----------------------------------------------------
    def _accept(self) -> None:
        while not self._stopping:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            with self._accept_lock:
                if self._stopping:  # close() began: this link is never served
                    with contextlib.suppress(OSError):
                        connection.close()
                    return
                env = _Env(connection, f"env-{len(self._envs)}")
                env.forward = threading.Thread(target=self._forward_env, args=(env,),
                                               daemon=True, name="fast-aggregator-env-forward")
                self._envs.append(env)
                self.stats["env_connections"] += 1
                threading.Thread(target=self._read_env, args=(env,), daemon=True,
                                 name="fast-aggregator-env-read").start()
                env.forward.start()

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
        with self._stats_lock:
            refused = self.stats["refused_here"]
            refused[code] = refused.get(code, 0) + 1
        decision.env.write({"type": "refused", "row_id": decision.row_id,
                            "game_instance_id": decision.game_instance_id,
                            "catalog_sha256": decision.catalog_sha256, "error_code": code})

    def _link_message(self, env: _Env, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "hello":
            if message.get("input_spec") != self.agent_input_spec:
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
            body["row_request_id"] = self._row_request_id(
                env, body.pop("row_id", None), self._bound(body.get("game_instance_id")))
            if body["row_request_id"] is None:
                return  # the row never reached the service (refused here): nothing to resolve
        elif kind == "episode_close":
            self.pool.release(body.get("game_instance_id"))
            body = {key: body[key] for key in ("game_instance_id", "reason", "last_step_index")}
        elif kind != "episode_open":
            raise BoundaryError("p1_fast_aggregator", "unexpected_link_message")
        self._control({"kind": kind, **body})

    def _row_request_id(self, env: _Env, row_id: Any, upstream: _Upstream) -> int | None:
        with upstream.lock:
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

    def close(self, timeout: float = 30.0) -> dict[str, Any]:
        """Stop taking link input, deliver what was already read, then close upstreams.

        A link's forwarder may still be handing on messages it read before close
        (a step_outcome, an episode_close): the upstream connections close only
        after every forwarder ended, so such a message is sent, not met by a
        closed connection (CI: `service_send:OSError` on a clean close). A
        forwarder that does not end within `timeout` is a failure, reported.
        """
        with self._accept_lock:
            self._stopping = True  # no link is added after this
        with contextlib.suppress(OSError):
            self.listener.close()
        for env in list(self._envs):
            with contextlib.suppress(OSError):
                env.connection.shutdown(socket.SHUT_RDWR)
        deadline = time.monotonic() + timeout
        for env in list(self._envs):
            if env.forward is not None:
                env.forward.join(max(0.0, deadline - time.monotonic()))
                if env.forward.is_alive():
                    self._fail(f"env_forward_did_not_end:{env.name}")
        for upstream in self._upstreams:
            with upstream.lock:
                self._flush_locked(upstream)
                upstream.lock.notify_all()
        for upstream in self._upstreams:
            with upstream.lock, upstream.send, contextlib.suppress(OSError):
                upstream.connection.close()
        self.pool.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.socket_path)
        report = {"aggregator_id": self.aggregator_id, "failure": self.failure, **self.stats,
                  "pending": sum(len(upstream.pending) for upstream in self._upstreams)}
        if len(self._upstreams) > 1:
            report["service_rows"] = [upstream.sent_rows for upstream in self._upstreams]
        return report
