"""Authenticated byte transport and learner-owned bounded binary sources.

Control, capacity waits and packet streams have independent connections. The
original learner owns admission; this module never invents leases or labels.
"""

from __future__ import annotations

import copy
import errno
import hashlib
import json
import mmap
import os
import re
import secrets
import socket
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from multiprocessing import AuthenticationError
from multiprocessing.connection import (
    Client,
    Listener,
    answer_challenge,
    deliver_challenge,
)
from pathlib import Path
from typing import Any, cast

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import json_bytes
from spireagent.rl_dataplane_ports import (
    MAX_CHUNK_BYTES,
    MAX_PACKET_BYTES,
    SOCKET_SCHEMA,
    TENSOR_SEGMENT_FORMAT,
    BinaryRef,
    EncodedSegment,
)

AUTH_ENV = "STPD_P1_LOCAL_INFERENCE_AUTH"
MAX_CONTROL_BYTES = 16 * 1024 * 1024


class DataplaneError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def fd_health() -> dict[str, Any]:
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    path = Path("/proc/self/fd") if Path("/proc/self/fd").is_dir() else Path("/dev/fd")
    try:
        opened = len(os.listdir(path))
    except OSError:
        opened = None
    return {"nofile_soft": soft, "nofile_hard": hard, "open_fds": opened}


def ensure_fd_capacity(
    lanes: int, *, source_slots: int = 0, connections_per_lane: int = 6, reserve: int = 256
) -> dict[str, Any]:
    """Raise the process soft limit before opening learner/actor resources."""
    import resource

    if min(lanes, connections_per_lane) < 1 or source_slots < 0 or reserve < 0:
        raise ValueError("invalid_fd_capacity_profile")
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    requested = hard if hard != resource.RLIM_INFINITY else max(soft, 65536)
    if requested != soft:
        resource.setrlimit(resource.RLIMIT_NOFILE, (requested, hard))
    actual = fd_health()
    required = lanes * connections_per_lane + source_slots + reserve
    if actual["nofile_soft"] != resource.RLIM_INFINITY and actual["nofile_soft"] < required:
        raise DataplaneError("FD_CAPACITY_EXCEEDED")
    return {
        **actual,
        "required_fds": required,
        "connections_per_lane": connections_per_lane,
        "fd_reserve": reserve,
    }


def binary_ref(value: BinaryRef | dict[str, Any]) -> BinaryRef:
    try:
        ref = value if isinstance(value, BinaryRef) else BinaryRef(**value)
    except (TypeError, KeyError) as error:
        raise DataplaneError("INVALID_PACKET") from error
    if ref.format != TENSOR_SEGMENT_FORMAT:
        raise DataplaneError("SCHEMA_MISMATCH")
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}/[a-z0-9][a-z0-9-]{0,62}/[a-f0-9]{32}", ref.slot)
        or not re.fullmatch(r"[a-f0-9]{64}", ref.sha256)
        or type(ref.size) is not int
        or not 0 < ref.size <= MAX_PACKET_BYTES
    ):
        raise DataplaneError("INVALID_PACKET")
    return ref


def _json(raw: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DataplaneError("INVALID_PACKET")
            result[key] = value
        return result

    value = json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(DataplaneError("INVALID_PACKET")),
    )
    if not isinstance(value, dict):
        raise DataplaneError("INVALID_PACKET")
    return value


class BinarySources:
    """Bounded node-local training spool. Only owned temporary bodies are reclaimed."""

    def __init__(
        self,
        root: Path,
        *,
        run: str,
        generation: str,
        max_bytes: int = 20 * 1024**3,
        max_slots: int = 8192,
    ) -> None:
        if max_bytes < 1 or max_slots < 1:
            raise ValueError("invalid_binary_source_capacity")
        root.mkdir(parents=True, exist_ok=True)
        self.root, self.run, self.generation = root, run, generation
        self.max_bytes, self.max_slots = max_bytes, max_slots
        self.condition = threading.Condition(threading.RLock())
        self.entries: dict[str, dict[str, Any]] = {}
        self.credits: dict[str, dict[str, Any]] = {}
        self.upload_owners: set[tuple[Any, ...]] = set()
        self.closed_episodes: set[tuple[str, str]] = set()
        self.retired_actors: set[str] = set()
        self.occupied = 0
        self.stopped = False
        self.closed = False
        self.discarded_bytes = 0

    def begin(self, envelope: dict[str, Any], *, draining: bool = False) -> str:
        envelope = _json(json_bytes(envelope))
        if envelope.get("format") != TENSOR_SEGMENT_FORMAT:
            raise DataplaneError("SCHEMA_MISMATCH")
        if (envelope.get("run"), envelope.get("training_generation")) != (
            self.run,
            self.generation,
        ):
            raise DataplaneError("IDENTITY_MISMATCH")
        size, sha = envelope.get("packet_size"), envelope.get("packet_sha256")
        kind = envelope.get("payload_kind", "ppo")
        required = {
            "actor",
            "episode",
            "run_id",
            "rows",
            "producer",
            "recipe_digest",
            "use_and_seed_digest",
            "row_bindings_sha256",
        }
        required |= (
            {"segment_id", "mu", "compiler_identity", "input_spec", "original_commit"}
            if kind == "ppo"
            else {"bundle_id", "typed_original"}
        )
        if (
            kind not in {"ppo", "aux"}
            or not required <= envelope.keys()
            or type(envelope.get("rows")) is not int
            or not 1 <= envelope["rows"] <= 64
            or type(size) is not int
            or not 0 < size <= MAX_PACKET_BYTES
            or not isinstance(sha, str)
            or not re.fullmatch(r"[a-f0-9]{64}", sha)
        ):
            raise DataplaneError("INVALID_PACKET")
        if size > self.max_bytes:
            raise DataplaneError("INVALID_PACKET")
        with self.condition:
            owner_key = (
                kind,
                envelope["actor"],
                envelope["episode"],
                envelope.get("segment_id", envelope.get("bundle_id")),
            )
            if owner_key in self.upload_owners:
                raise DataplaneError("IDENTITY_MISMATCH")
            self.upload_owners.add(owner_key)
            try:

                def revoked() -> bool:
                    return (
                        self.closed
                        or (self.stopped and not draining)
                        or envelope["actor"] in self.retired_actors
                        or (envelope["actor"], envelope["episode"]) in self.closed_episodes
                    )

                while (
                    self.occupied + size > self.max_bytes
                    or len(self.entries) + len(self.credits) >= self.max_slots
                ):
                    if revoked():
                        raise DataplaneError("STOPPED")
                    self.condition.wait()
                if revoked():
                    raise DataplaneError("STOPPED")
                credit = secrets.token_hex(16)
                fd, name = tempfile.mkstemp(prefix="packet-", dir=self.root)
                self.credits[credit] = {
                    "envelope": envelope,
                    "path": Path(name),
                    "stream": os.fdopen(fd, "wb"),
                    "hash": hashlib.sha256(),
                    "received": 0,
                    "owner_key": owner_key,
                    "created": time.time(),
                }
                self.occupied += size
                return credit
            except BaseException:
                self.upload_owners.discard(owner_key)
                raise

    def chunk(self, credit: str, raw: bytes) -> None:
        with self.condition:
            state = self.credits[credit]
            if (
                not 0 < len(raw) <= MAX_CHUNK_BYTES
                or state["received"] + len(raw) > state["envelope"]["packet_size"]
            ):
                raise DataplaneError("INVALID_PACKET")
            state["stream"].write(raw)
            state["hash"].update(raw)
            state["received"] += len(raw)

    def finish(self, credit: str) -> BinaryRef:
        with self.condition:
            state = self.credits[credit]
            envelope = state["envelope"]
            if state["received"] != envelope["packet_size"]:
                raise DataplaneError("INVALID_PACKET")
            if state["hash"].hexdigest() != envelope["packet_sha256"]:
                raise DataplaneError("HASH_MISMATCH")
            state["stream"].close()
            state["path"].chmod(0o400)
            ref = BinaryRef(
                TENSOR_SEGMENT_FORMAT,
                f"{self.run}/{self.generation}/{credit}",
                envelope["packet_sha256"],
                envelope["packet_size"],
            )
            self.entries[ref.slot] = {**state, "ref": ref, "owners": {"upload"}, "borrows": 0}
            del self.credits[credit]
            return ref

    def cancel(self, credit: str) -> None:
        with self.condition:
            state = self.credits.pop(credit, None)
            if state is not None:
                self.upload_owners.discard(state["owner_key"])
                state["stream"].close()
                state["path"].unlink(missing_ok=True)
                self.occupied -= state["envelope"]["packet_size"]
                self.condition.notify_all()

    def release_uploads(self, actor: str, episode: str | None = None) -> None:
        """Original close/retire owner revokes uncommitted uploads, never PPO refs.

        A delivered upload may outlive its socket. No TTL guesses acceptance;
        the episode close or exact actor-allocation terminal fact releases it.
        """
        with self.condition:
            if episode is None:
                self.retired_actors.add(actor)
            else:
                self.closed_episodes.add((actor, episode))

            def owned(state: dict[str, Any]) -> bool:
                env = state["envelope"]
                return env["actor"] == actor and (episode is None or env["episode"] == episode)

            for credit, state in list(self.credits.items()):
                if owned(state):
                    self.cancel(credit)
            for slot, state in list(self.entries.items()):
                if owned(state):
                    state["owners"].discard("upload")
                    self._reclaim(slot)
            self.condition.notify_all()

    def _entry(self, ref: BinaryRef) -> dict[str, Any]:
        ref = binary_ref(ref)
        state = self.entries.get(ref.slot)
        if state is None:
            raise DataplaneError("SOURCE_DISCARDED")
        if state["ref"] != ref:
            raise DataplaneError("IDENTITY_MISMATCH")
        return state

    def envelope(self, ref: BinaryRef) -> dict[str, Any]:
        with self.condition:
            return copy.deepcopy(self._entry(ref)["envelope"])

    @contextmanager
    def borrow(self, ref: BinaryRef) -> Iterator[memoryview]:
        with self.condition:
            state = self._entry(ref)
            state["borrows"] += 1
        try:
            with (
                state["path"].open("rb") as stream,
                mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as body,
            ):
                if len(body) != ref.size or hashlib.sha256(body).hexdigest() != ref.sha256:
                    raise DataplaneError("HASH_MISMATCH")
                view = memoryview(body)
                try:
                    yield view
                finally:
                    view.release()
        finally:
            with self.condition:
                state["borrows"] -= 1
                self._reclaim(ref.slot)

    def retain(self, ref: BinaryRef, owner: str) -> None:
        with self.condition:
            self._entry(ref)["owners"].add(owner)

    def release(self, ref: BinaryRef, owner: str) -> None:
        with self.condition:
            self._entry(ref)["owners"].discard(owner)
            self._reclaim(ref.slot)

    def _reclaim(self, slot: str) -> None:
        state = self.entries[slot]
        if not state["owners"] and not state["borrows"]:
            state["path"].unlink(missing_ok=True)
            self.occupied -= state["ref"].size
            self.upload_owners.discard(state["owner_key"])
            del self.entries[slot]
            self.condition.notify_all()

    def stop(self, *, close: bool = False) -> None:
        with self.condition:
            self.stopped = True
            self.closed = self.closed or close
            self.condition.notify_all()

    def status(self) -> dict[str, Any]:
        with self.condition:
            ages = [
                time.time() - s["created"] for s in (*self.entries.values(), *self.credits.values())
            ]
            return {
                "spool_bytes": self.occupied,
                "source_slots": len(self.entries),
                "upload_credits": len(self.credits),
                "source_capacity_bytes": self.max_bytes,
                "source_capacity_slots": self.max_slots,
                "oldest_source_age": max(ages, default=0),
                "training_discarded_bytes": self.discarded_bytes,
            }


class SocketDataplane:
    """Original authenticated listener with separate streaming and control channels."""

    def __init__(
        self,
        *,
        attachment: dict[str, Any],
        auth: bytes,
        control: Any,
        sources: BinarySources,
        address: tuple[str, int] | None = None,
        upload_check: Any = None,
        model_files: Any = None,
        on_failure: Any = None,
    ) -> None:
        if len(auth) != 32:
            raise ValueError("inference_capability_must_be_32_bytes")
        self.binding = copy.deepcopy(attachment)
        self.auth, self.control, self.sources = auth, control, sources
        self.upload_check, self.model_files = upload_check, model_files
        self.on_failure = on_failure
        self.accept_errors = self.fd_exhaustions = 0
        self.accept_failure: dict[str, Any] | None = None
        # Authentication happens in the per-connection worker so a stalled peer
        # cannot prevent the acceptor from accepting STOP/status/receipt.
        self.listener = Listener(
            address or (socket.gethostbyname(socket.gethostname()), 0), backlog=1024, authkey=None
        )
        self.address = self.listener.address
        self.stopping = threading.Event()
        self.connections: set[Any] = set()
        self.workers: list[threading.Thread] = []
        self.lock = threading.RLock()
        self.acceptor = threading.Thread(target=self._accept, name="rl-socket", daemon=True)
        self.acceptor.start()

    def attachment(self) -> dict[str, Any]:
        return {**self.binding, "address": list(self.address), "dataplane": SOCKET_SCHEMA}

    def _accept(self) -> None:
        while not self.stopping.is_set():
            try:
                connection = self.listener.accept()
            except (OSError, EOFError, ValueError) as error:
                if self.stopping.is_set():
                    return
                self.accept_errors += 1
                exhausted = getattr(error, "errno", None) in {errno.EMFILE, errno.ENFILE}
                self.fd_exhaustions += int(exhausted)
                previous = self.accept_failure
                self.accept_failure = {
                    "code": "FD_EXHAUSTED" if exhausted else "SOCKET_ACCEPT_FAILED",
                    "errno": getattr(error, "errno", None),
                    "fatal": not exhausted,
                    "at": time.time(),
                }
                if self.on_failure is not None and (
                    previous is None or previous["code"] != self.accept_failure["code"]
                ):
                    try:
                        self.on_failure(self.accept_failure)
                    except Exception:
                        # Owner notification failure remains a visible fatal
                        # health fact; the acceptor itself never silently exits.
                        self.accept_failure = {**self.accept_failure, "notification_failed": True}
                self.stopping.wait(0.1)
                continue
            with self.lock:
                if self.stopping.is_set():
                    connection.close()
                    return
                self.connections.add(connection)
                worker = threading.Thread(
                    target=self._serve, args=(connection,), daemon=True, name="rl-channel"
                )
                self.workers[:] = [thread for thread in self.workers if thread.is_alive()]
                self.workers.append(worker)
                worker.start()

    def _serve(self, connection: Any) -> None:
        credit = None
        completed_ref = None
        try:
            deliver_challenge(connection, self.auth)
            answer_challenge(connection, self.auth)
            last_id = 0
            while not self.stopping.is_set():
                value = _json(connection.recv_bytes(MAX_CONTROL_BYTES))
                request_id = value.get("request_id")
                if (
                    type(request_id) is not int
                    or request_id != last_id + 1
                    or any(
                        value.get(k) != self.binding.get(k)
                        for k in ("schema", "pool_id", "adapter")
                    )
                ):
                    raise DataplaneError("IDENTITY_MISMATCH")
                last_id = request_id
                operation = value["operation"]
                try:
                    if operation in {"upload_begin", "upload_aux"}:
                        envelope = value["envelope"]
                        draining = (
                            self.upload_check(envelope) if self.upload_check is not None else False
                        )
                        credit = self.sources.begin(envelope, draining=bool(draining))
                        connection.send_bytes(
                            json_bytes(
                                {
                                    "request_id": request_id,
                                    "result": {"credit_id": credit, "chunk_limit": MAX_CHUNK_BYTES},
                                }
                            )
                        )
                        remaining = envelope["packet_size"]
                        while remaining:
                            chunk = connection.recv_bytes(min(remaining, MAX_CHUNK_BYTES))
                            self.sources.chunk(credit, chunk)
                            remaining -= len(chunk)
                        ref = self.sources.finish(credit)
                        credit = None
                        completed_ref = ref
                        result = {"payload": asdict(ref)}
                    elif operation == "get_mu":
                        if self.model_files is None:
                            raise DataplaneError("SCHEMA_MISMATCH")
                        identity, files, publication = self.model_files(
                            value.get("snapshot_sha256"),
                            value.get("node_id"),
                            value.get("cached_snapshots", []),
                        )
                        if identity["snapshot_sha256"] in value.get("cached_snapshots", []):
                            files = []
                        metadata = [
                            {
                                "name": name,
                                "size": path.stat().st_size,
                                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            }
                            for name, path in files
                        ]
                        connection.send_bytes(
                            json_bytes(
                                {
                                    "request_id": request_id,
                                    "result": {
                                        "identity": identity,
                                        "files": metadata,
                                        "publication": publication,
                                    },
                                }
                            )
                        )
                        for _name, path in files:
                            with path.open("rb") as stream:
                                while raw := stream.read(MAX_CHUNK_BYTES):
                                    connection.send_bytes(raw)
                        continue
                    else:
                        if operation in {"episode_open", "begin", "commit", "episode_end"}:
                            from spireagent.rl_exchange import control_input, input_key

                            if value.get("input_key") != input_key(control_input(value)):
                                raise DataplaneError("IDENTITY_MISMATCH")
                        result = self.control(value)
                    connection.send_bytes(json_bytes({"request_id": request_id, "result": result}))
                    completed_ref = None
                except Exception as error:
                    if credit is not None:
                        self.sources.cancel(credit)
                        credit = None
                    connection.send_bytes(
                        json_bytes(
                            {
                                "request_id": request_id,
                                "error": type(error).__name__,
                                "error_code": getattr(error, "code", None),
                            }
                        )
                    )
                    if operation in {"upload_begin", "upload_aux", "get_mu"}:
                        return  # framing after an interrupted stream is never reused
        except (OSError, EOFError, ValueError, TypeError, KeyError, AuthenticationError):
            pass
        finally:
            if credit is not None:
                self.sources.cancel(credit)
            if completed_ref is not None:
                self.sources.release(completed_ref, "upload")
            connection.close()
            with self.lock:
                self.connections.discard(connection)
                current = threading.current_thread()
                self.workers[:] = [thread for thread in self.workers if thread is not current]

    def close(self) -> None:
        self.stopping.set()
        self.sources.stop(close=True)
        # Wake accept without authenticating or touching unrelated channels.
        try:
            with socket.create_connection(cast(tuple[str, int], self.address), timeout=2):
                pass
        except OSError:
            pass
        self.listener.close()
        with self.lock:
            workers = tuple(self.workers)
            for connection in tuple(self.connections):
                try:
                    channel = socket.socket(fileno=connection.fileno())
                    try:
                        channel.shutdown(socket.SHUT_RDWR)
                    finally:
                        channel.detach()
                except OSError:
                    pass
        self.acceptor.join(timeout=5)
        for worker in workers:
            worker.join(timeout=2)
        if self.acceptor.is_alive() or any(t.is_alive() for t in workers):
            raise RuntimeError("RL_socket_owner_did_not_close")

    def health(self) -> dict[str, Any]:
        with self.lock:
            return {
                **fd_health(),
                "socket_connections": len(self.connections),
                "socket_threads": len(self.workers),
                "acceptor_alive": self.acceptor.is_alive(),
                "accept_errors": self.accept_errors,
                "fd_exhaustions": self.fd_exhaustions,
                "accept_failure": self.accept_failure,
            }


def upload(attachment: dict[str, Any], encoded: EncodedSegment) -> BinaryRef:
    """One delivery only. A failed upload is not an accepted PPO mutation."""
    auth = bytes.fromhex(os.environ[AUTH_ENV])
    with Client(tuple(attachment["address"]), authkey=auth) as connection:
        operation = (
            "upload_aux" if encoded.envelope.get("payload_kind") == "aux" else "upload_begin"
        )
        connection.send_bytes(
            json_bytes(
                {
                    **{k: attachment[k] for k in ("schema", "pool_id", "adapter")},
                    "request_id": 1,
                    "operation": operation,
                    "envelope": encoded.envelope,
                }
            )
        )
        reply = _json(connection.recv_bytes(MAX_CONTROL_BYTES))
        if reply.get("request_id") != 1 or "error" in reply:
            raise DataplaneError(reply.get("error_code") or "INVALID_PACKET")
        for offset in range(0, len(encoded.packet), MAX_CHUNK_BYTES):
            connection.send_bytes(encoded.packet[offset : offset + MAX_CHUNK_BYTES])
        reply = _json(connection.recv_bytes(MAX_CONTROL_BYTES))
        if reply.get("request_id") != 1 or "error" in reply:
            raise DataplaneError(reply.get("error_code") or "INVALID_PACKET")
        return binary_ref(reply["result"]["payload"])


class NodeMuCache:
    """One immutable download per node/version; cross-process bounded adoption.

    The local node lock serializes refresh, not begin waits. A shared snapshot
    lock spans begin and model adoption, so eviction cannot race an actor load.
    All paths are cache-owned files on node-local scratch.
    """

    def __init__(
        self,
        attachment: dict[str, Any],
        root: Path,
        *,
        interval: float = 15,
        max_bytes: int = 512 * 1024**2,
        keep: int = 3,
    ) -> None:
        if type(interval) not in (float, int) or interval < 0 or max_bytes < 1 or keep < 2:
            raise ValueError("invalid_node_mu_cache_bounds")
        self.attachment = copy.deepcopy(attachment)
        self.root, self.interval, self.max_bytes, self.keep = root, interval, max_bytes, keep
        self.last_transfer: dict[str, Any] = {}
        root.mkdir(parents=True, exist_ok=True)

    def _download(self) -> dict[str, Any]:
        attachment = self.attachment
        with Client(
            tuple(attachment["address"]), authkey=bytes.fromhex(os.environ[AUTH_ENV])
        ) as connection:
            cached = [p.name for p in self.root.iterdir() if (p / "COMPLETE").is_file()]
            connection.send_bytes(
                json_bytes(
                    {
                        **{k: attachment[k] for k in ("schema", "pool_id", "adapter")},
                        "request_id": 1,
                        "operation": "get_mu",
                        "node_id": socket.gethostname(),
                        "cached_snapshots": cached,
                    }
                )
            )
            reply = _json(connection.recv_bytes(MAX_CONTROL_BYTES))
            if reply.get("request_id") != 1 or "error" in reply:
                raise DataplaneError(reply.get("error_code") or "IDENTITY_MISMATCH")
            identity: dict[str, Any] = reply["result"]["identity"]
            files = reply["result"]["files"]
            publication = reply["result"]["publication"]
            if any(publication.get(k) != value for k, value in identity.items()):
                raise DataplaneError("IDENTITY_MISMATCH")
            self.last_transfer = {
                "published_at": publication["published_at"],
                "download_bytes": sum(f["size"] for f in files),
            }
            key = identity["snapshot_sha256"]
            if (
                not re.fullmatch(r"[a-f0-9]{64}", key)
                or identity.get("run") != attachment["run"]
                or identity.get("training_generation") != attachment["training_generation"]
                or identity.get("adapter") != attachment["adapter"]
            ):
                raise DataplaneError("IDENTITY_MISMATCH")
            expected = {
                "model.tensor-tree",
                "model.json",
                "heads.tensor-tree",
                "model-manifest.json",
                "heads-manifest.json",
            }
            if not files and key in cached:
                if _json((self.root / key / "identity.json").read_bytes()) != identity:
                    raise DataplaneError("IDENTITY_MISMATCH")
                return identity
            if (
                {f["name"] for f in files} != expected
                or len(files) != len(expected)
                or any(
                    type(f["size"]) is not int or not 0 < f["size"] <= MAX_PACKET_BYTES
                    for f in files
                )
                or sum(f["size"] for f in files) > self.max_bytes
            ):
                raise DataplaneError("INVALID_PACKET")
            target = self.root / key
            current = (
                _json((self.root / "CURRENT.json").read_bytes())
                if (self.root / "CURRENT.json").exists()
                else None
            )
            if not self._trim(
                current["identity"]["snapshot_sha256"] if current else "",
                sum(f["size"] for f in files),
            ):
                raise DataplaneError("CAPACITY_WAIT")
            # Drain the current version's bytes only on a real refresh. The
            # service sends immutable bodies; a cached version is not rewritten.
            if (target / "COMPLETE").exists():
                # No second request for this version: get_mu metadata permits
                # the client to close before bodies; the service sees EOF.
                return identity
            staged = Path(tempfile.mkdtemp(prefix="incoming-", dir=self.root))
            try:
                for item in files:
                    remaining, digest = item["size"], hashlib.sha256()
                    with (staged / item["name"]).open("wb") as stream:
                        while remaining:
                            raw = connection.recv_bytes(min(remaining, MAX_CHUNK_BYTES))
                            if not raw:
                                raise DataplaneError("INVALID_PACKET")
                            stream.write(raw)
                            digest.update(raw)
                            remaining -= len(raw)
                    if digest.hexdigest() != item["sha256"]:
                        raise DataplaneError("HASH_MISMATCH")
                from spireagent.artifact_contracts import Manifest

                model = Manifest.from_bytes(
                    (staged / "model-manifest.json").read_bytes(), identity["model_artifact_id"]
                )
                heads = Manifest.from_bytes(
                    (staged / "heads-manifest.json").read_bytes(), identity["heads_artifact_id"]
                )
                if (
                    model.payload("weights").sha256 != identity["model_sha256"]
                    or heads.payload("checkpoint").sha256 != identity["heads_sha256"]
                    or hashlib.sha256((staged / "model.tensor-tree").read_bytes()).hexdigest()
                    != identity["model_sha256"]
                    or hashlib.sha256((staged / "heads.tensor-tree").read_bytes()).hexdigest()
                    != identity["heads_sha256"]
                ):
                    raise DataplaneError("HASH_MISMATCH")
                (staged / "identity.json").write_bytes(json_bytes(identity))
                (staged / "readers.lock").touch()
                (staged / "COMPLETE").write_text(key)
                staged.rename(target)
            finally:
                if staged.exists():
                    for path in staged.iterdir():
                        path.unlink()
                    staged.rmdir()
            return identity

    def _trim(self, current: str, required: int = 0) -> bool:
        from spireagent.instance_lock import instance_lock
        from spireagent.json_boundary import BoundaryError

        versions = sorted(
            (
                p
                for p in self.root.iterdir()
                if p.is_dir() and re.fullmatch(r"[a-f0-9]{64}", p.name)
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        total = sum(f.stat().st_size for p in versions for f in p.iterdir() if f.is_file())
        for index, path in enumerate(versions):
            if (
                index < self.keep - 1 and total + required <= self.max_bytes
            ) or path.name == current:
                continue
            try:
                with instance_lock(path / "readers.lock", create=False):
                    for file in path.iterdir():
                        if file.name != "readers.lock":
                            total -= file.stat().st_size
                            file.unlink()
                (path / "readers.lock").unlink()
                path.rmdir()
            except BoundaryError:
                continue  # a granted/waiting actor owns this immutable snapshot
        return total + required <= self.max_bytes

    @contextmanager
    def latest(self):
        from contextlib import ExitStack

        from spireagent.instance_lock import instance_lock

        with ExitStack() as pin:
            with instance_lock(self.root / "refresh.lock", blocking=True):
                current = self.root / "CURRENT.json"
                record = _json(current.read_bytes()) if current.exists() else None
                if record is None or time.time() - record["checked_at"] >= self.interval:
                    if record is not None and not self._trim(record["identity"]["snapshot_sha256"]):
                        raise DataplaneError("CAPACITY_WAIT")
                    identity = self._download()
                    previous = record or {}
                    record = {
                        "identity": identity,
                        "checked_at": time.time(),
                        "published_at": self.last_transfer["published_at"],
                        "pull_count": previous.get("pull_count", 0) + 1,
                        "download_count": previous.get("download_count", 0)
                        + int(bool(self.last_transfer["download_bytes"])),
                        "download_bytes": previous.get("download_bytes", 0)
                        + self.last_transfer["download_bytes"],
                    }
                    temporary = self.root / "CURRENT.pending"
                    temporary.write_bytes(json_bytes(record))
                    temporary.replace(current)
                identity = record["identity"]
                pin.enter_context(
                    instance_lock(
                        self.root / identity["snapshot_sha256"] / "readers.lock",
                        create=False,
                        shared=True,
                        blocking=True,
                    )
                )
            yield copy.deepcopy(identity)

    @contextmanager
    def materialize(self, identity: dict[str, Any]) -> Iterator[tuple[Path, Manifest, bytes]]:
        from spireagent.artifact_contracts import Manifest
        from spireagent.instance_lock import instance_lock

        root = self.root / identity["snapshot_sha256"]
        with instance_lock(root / "readers.lock", create=False, shared=True, blocking=True):
            if _json((root / "identity.json").read_bytes()) != identity:
                raise DataplaneError("IDENTITY_MISMATCH")
            head = Manifest.from_bytes(
                (root / "heads-manifest.json").read_bytes(), identity["heads_artifact_id"]
            )
            yield root, head, (root / "heads.tensor-tree").read_bytes()

    def model_record(self, generation: str | None) -> dict[str, Any]:
        if generation not in {None, self.attachment["training_generation"]}:
            raise DataplaneError("GENERATION_DISCARDED")
        with self.latest() as identity:
            current = _json((self.root / "CURRENT.json").read_bytes())
            return {
                **identity,
                "published_at": current["published_at"],
                "trajectory_encoding": TENSOR_SEGMENT_FORMAT,
            }

    def bootstrap_model(self, record: dict[str, Any], cache: Path, store: Any) -> Path:
        """One registration copy, from the node cache through the existing Store.

        Actor registration still has its original manifest/Store checks. This
        startup-only import preserves the exact manifest and payload identities;
        subsequent segment mu adoption never visits that shared Store.
        """
        import shutil

        from spireagent.artifact_contracts import Manifest
        from spireagent.instance_lock import instance_lock
        from spireagent.storage.blobs import StoreError

        root = self.root / record["snapshot_sha256"]
        identity = _json((root / "identity.json").read_bytes())
        if any(record.get(key) != value for key, value in identity.items()):
            raise DataplaneError("IDENTITY_MISMATCH")
        cache.mkdir(parents=True, exist_ok=True)
        target: Path = cache / str(identity["model_artifact_id"])
        with self.materialize(identity), instance_lock(self.root / "bootstrap.lock", blocking=True):
            model = Manifest.from_bytes(
                (root / "model-manifest.json").read_bytes(), identity["model_artifact_id"]
            )
            try:
                store.get_manifest(model.artifact_id)
            except StoreError as error:
                if error.code != "object_not_found":
                    raise
                for payload in model.payloads:
                    name = {"weights": "model.tensor-tree", "package_manifest": "model.json"}[
                        payload.role
                    ]
                    with (root / name).open("rb") as stream:
                        copied = store.put_payload(payload.role, stream, payload.media_type)
                    if copied != payload:
                        raise DataplaneError("IDENTITY_MISMATCH") from None
                store.publish(model)
            if not (target / "COMPLETE").exists():
                target.mkdir(parents=True, exist_ok=True)
                for name in ("model.tensor-tree", "model.json"):
                    shutil.copyfile(root / name, target / name)
                (target / "COMPLETE").write_text(identity["model_sha256"])
        return target

    def status(self) -> dict[str, Any]:
        current = self.root / "CURRENT.json"
        if not current.exists():
            return {"ready": False, "mu_pull_interval_seconds": self.interval}
        value = _json(current.read_bytes())
        return {
            "ready": True,
            "mu_pull_interval_seconds": self.interval,
            "snapshot_sha256": value["identity"]["snapshot_sha256"],
            "training_generation": value["identity"]["training_generation"],
            "version": value["identity"]["version"],
            "last_pull_at": value["checked_at"],
            "published_at": value["published_at"],
            "mu_age_seconds": time.time() - value["published_at"],
            "pull_count": value.get("pull_count", 0),
            "download_count": value.get("download_count", 0),
            "download_bytes": value.get("download_bytes", 0),
        }


def require_local_scratch(root: Path) -> dict[str, Any]:
    """Reject shared mounts and prove local write/fsync/readback at startup."""
    import subprocess
    import sys

    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    filesystem = "unknown"
    if sys.platform == "linux":
        mounts = []
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            left, right = line.split(" - ", 1)
            mount = left.split()[4].replace("\\040", " ")
            if root == Path(mount) or Path(mount) in root.parents:
                mounts.append((len(mount), right.split()[0]))
        if mounts:
            filesystem = max(mounts)[1]
        if filesystem in {"nfs", "nfs4", "cifs", "smbfs", "fuse.sshfs", "9p", "unknown"}:
            raise ValueError("binary_dataplane_requires_node_local_scratch")
    elif sys.platform == "darwin":
        for line in subprocess.check_output(["/sbin/mount"], text=True).splitlines():
            if " on " not in line or " (" not in line:
                continue
            mount, options = line.split(" on ", 1)[1].rsplit(" (", 1)
            if (root == Path(mount) or Path(mount) in root.parents) and options.split(",")[0] in {
                "nfs",
                "smbfs",
                "webdav",
            }:
                raise ValueError("binary_dataplane_requires_node_local_scratch")
        filesystem = "darwin_local_mount"
    else:
        raise ValueError("binary_dataplane_local_mount_probe_unsupported")
    fd, name = tempfile.mkstemp(prefix="wal-probe-", dir=root)
    probe = b"RL4.2 local durability probe\n"
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(probe)
            stream.flush()
            os.fsync(stream.fileno())
        if Path(name).read_bytes() != probe:
            raise ValueError("binary_local_scratch_readback_failed")
    finally:
        Path(name).unlink(missing_ok=True)
    return {
        "filesystem": filesystem,
        "device": root.stat().st_dev,
        "write_fsync_readback": True,
        "free_bytes": os.statvfs(root).f_bavail * os.statvfs(root).f_frsize,
    }
