"""A shared local Store with durable asynchronous preservation to a pinned peer.

The normal Store remains authoritative for hot reads. The archive contains the
same immutable manifests, payloads and Exchange records. Publication to the
archive follows complete dependency readback; a local receipt never claims R2
completion. Pending intents survive producer/allocation death on shared scratch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Manifest
from spireagent.instance_lock import instance_lock
from spireagent.json_boundary import BoundaryError, json_bytes

from .blobs import StoreError, safe_key
from .local import LocalBlobStore
from .store import ManifestArtifactStore, copy_artifact

SCHEMA = "spireagent/shared-store-archive-1"
_services: dict[tuple[str, bytes], Archive] = {}
_guard = threading.Lock()


def filesystem_capacity(root: Path) -> dict[str, Any]:
    """Filesystem, inode and (where installed) the site's per-user quota facts."""
    space = shutil.disk_usage(root)
    inodes = os.statvfs(root).f_favail if hasattr(os, "statvfs") else None
    result: dict[str, Any] = {"filesystem_free_bytes": space.free, "free_bytes": space.free,
        "free_inodes": inodes, "quota_status": "not_reported_for_path"}
    usage = shutil.which("usage")
    if usage is None:
        result["quota_status"] = "command_unavailable"
        return result
    try:
        raw = subprocess.check_output([usage], text=True, timeout=10)
        matches: list[tuple[int, Path, int, int]] = []
        for line in raw.splitlines():
            fields = line.split()
            if len(fields) != 3 or not fields[0].startswith('/'):
                continue
            mount = Path(fields[0])
            if not root.is_relative_to(mount):
                continue
            sizes = []
            for word in fields[1:]:
                value = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([KMGTPE]?)B?", word)
                if value is None:
                    raise ValueError("unrecognized_quota_size")
                sizes.append(int(float(value[1]) * 1024 ** (" KMGTPE".index(value[2])
                             if value[2] else 0)))
            matches.append((len(mount.parts), mount, sizes[0], sizes[1]))
        if matches:
            _, mount, used, quota = max(matches)
            result.update(quota_status="measured", quota_path=str(mount),
                          quota_used_bytes=used, quota_limit_bytes=quota,
                          free_bytes=min(space.free, max(0, quota - used)))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result.update(quota_status="observation_failed", quota_error=type(error).__name__)
    return result


def storage_inventory(root: Path, *, stop: threading.Event | None = None) -> dict[str, Any]:
    """Owned Store only: physical categories and logical artifact payloads, no raw reads."""
    physical: dict[str, dict[str, int]] = {}
    for directory, _, files in os.walk(root):
        if stop is not None and stop.is_set():
            return {"at": time.time(), "cancelled": True}
        relative = Path(directory).relative_to(root)
        category = relative.parts[0] if relative.parts else "root"
        counts = physical.setdefault(category, {"bytes": 0, "files": 0})
        for name in files:
            path = Path(directory) / name
            try:
                counts["bytes"] += path.stat().st_size
                counts["files"] += 1
            except FileNotFoundError:
                pass  # Reference release/archiving can remove a live hot/pending leaf.
    logical: dict[str, dict[str, int]] = {}
    seen: set[tuple[str, str]] = set()
    for path in (root / "manifests").glob("*.json"):
        if stop is not None and stop.is_set():
            return {"at": time.time(), "cancelled": True}
        manifest = Manifest.from_bytes(path.read_bytes())
        schema = manifest.parameters.value().get("schema", "")
        category = ("CP" if schema == "stpd/rl-segment-checkpoint-2" else
                    "heads" if schema == "stpd/rl-behavior-heads-1" else manifest.kind)
        counts = logical.setdefault(category, {"payload_bytes": 0, "artifacts": 0})
        counts["artifacts"] += 1
        for payload in manifest.payloads:
            if (category, payload.sha256) not in seen:
                counts["payload_bytes"] += payload.size
                seen.add((category, payload.sha256))
    return {"at": time.time(), "physical": physical, "logical": logical,
            "physical_bytes": sum(c["bytes"] for c in physical.values())}


class Archive:
    """One lazily started publisher per process/root; OS locks share durable work."""

    def __init__(self, root: Path, target: dict[str, Any]) -> None:
        from .config import open_peer

        self.root, self.binding = root, json.loads(json_bytes(target))
        self.target = open_peer(self.binding)
        self.source = ManifestArtifactStore(LocalBlobStore(root), read_peers=(self.target,))
        self.queue = LocalBlobStore(root / ".archive")
        self.queue.put_if_absent("binding.json", json_bytes({"schema": SCHEMA, "peer": target}))
        self.wake = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.owned: set[str] = set()
        self.owned_done: set[str] = set()
        # Immutable pending intent key/size metadata. Completion, payload bytes and
        # readback remain uncached and are rechecked by _copy.
        self.order_keys: OrderedDict[str, tuple[str, int | None]] = OrderedDict()
        self.verified_at: float | None = None
        self.last_error: dict[str, Any] | None = None
        self.capacity_snapshot: dict[str, Any] | None = None
        self.capacity_stop = threading.Event()
        self.capacity_thread: threading.Thread | None = None
        self.capacity_interval = 2.0
        self.filesystem_snapshot: dict[str, Any] = {}
        self.filesystem_checked = 0.0
        self.inventory_snapshot: dict[str, Any] = {}

    def offer(self, key: str, raw: bytes, *, start: bool = True) -> str:
        safe_key(key)
        digest = hashlib.sha256(raw).hexdigest()
        identity = hashlib.sha256(json_bytes([key, digest])).hexdigest()
        completed = (self.queue.root / "completed" / f"{identity}.json").is_file()
        if not completed:
            self.queue.put_if_absent(f"pending/{identity}.json", json_bytes(
                {"schema": SCHEMA, "key": key, "sha256": digest}))
        with self.lock:
            self.owned.add(identity)
            if completed:
                self.owned_done.add(identity)
            if start and self.thread is None:
                (self.queue.root / "publisher.lock").touch(exist_ok=True)
                self.thread = threading.Thread(target=self._loop,
                    name="shared-store-archive", daemon=True)
                self.thread.start()
        self.wake.set()
        return identity

    def _refresh_capacity(self) -> None:
        """Local outbox metadata only; never wait for R2 or recurse model ancestry."""
        try:
            if time.monotonic() - self.filesystem_checked >= 60 or not self.filesystem_snapshot:
                self.filesystem_snapshot = filesystem_capacity(self.root)
                self.filesystem_checked = time.monotonic()
                self.inventory_snapshot = storage_inventory(self.root, stop=self.capacity_stop)
            # Filesystem/inodes may fill between the slower site-quota observations.
            inodes = os.statvfs(self.root).f_favail if hasattr(os, "statvfs") else None
            free = shutil.disk_usage(self.root).free
            count = size = 0
            completed = {Path(key).stem for key in self.queue.keys("completed/")}
            for key in self.queue.keys("pending/"):
                if self.capacity_stop.is_set():
                    return
                identity = Path(key).stem
                if identity in completed:
                    continue
                try:
                    _key, estimated = self._intent_metadata(identity, measure=True)
                except StoreError as error:
                    if error.code == "object_not_found":
                        continue  # Another archive worker completed this intent.
                    raise
                assert estimated is not None
                size += estimated
                count += 1
            snapshot: dict[str, Any] = {"pending": count, "pending_bytes_estimate": size,
                        **self.filesystem_snapshot,
                        "free_bytes": min(free, self.filesystem_snapshot["free_bytes"]),
                        "free_inodes": inodes, "inventory": self.inventory_snapshot,
                        "verified_at": time.time(), "checked_monotonic": time.monotonic(),
                        "error": None}
        except Exception as error:  # noqa: BLE001 -- stale/error state pauses new leases
            snapshot = {"verified_at": time.time(), "checked_monotonic": time.monotonic(),
                        "error": type(error).__name__}
        with self.lock:
            self.capacity_snapshot = snapshot

    def start_capacity_monitor(self, interval: float = 2) -> None:
        if not 0.01 <= interval <= 30:
            raise ValueError("invalid_archive_capacity_interval")
        if self.capacity_thread is not None:
            raise StoreError("archive_capacity_monitor_already_owned")
        self.capacity_interval = interval
        self.capacity_stop.clear()
        self._refresh_capacity()

        def monitor() -> None:
            while not self.capacity_stop.wait(interval):
                self._refresh_capacity()

        self.capacity_thread = threading.Thread(target=monitor,
            name="shared-store-capacity", daemon=True)
        self.capacity_thread.start()

    def stop_capacity_monitor(self) -> None:
        self.capacity_stop.set()
        if self.capacity_thread:
            self.capacity_thread.join(timeout=max(15, self.capacity_interval + 1))
            if self.capacity_thread.is_alive():
                raise StoreError("archive_capacity_monitor_close_pending")
            self.capacity_thread = None

    def capacity_status(self) -> dict[str, Any]:
        with self.lock:
            value = dict(self.capacity_snapshot or {})
        age = (time.monotonic() - value["checked_monotonic"]
               if "checked_monotonic" in value else None)
        return {**value, "age_seconds": age,
                "fresh": age is not None and age <= max(1, 3 * self.capacity_interval)}

    def _copy(self, identity: str) -> None:
        receipt_key = f"completed/{identity}.json"
        pending = self.queue.root / "pending" / f"{identity}.json"
        if (self.queue.root / receipt_key).is_file():
            pending.unlink(missing_ok=True)
            return
        with instance_lock(self.queue.root / "locks" / f"{identity}.lock"):
            if (self.queue.root / receipt_key).is_file():
                pending.unlink(missing_ok=True)
                return
            task = json.loads(self.queue.get(f"pending/{identity}.json"))
            key = task["key"]
            raw = self.source.blobs.get(key)
            if hashlib.sha256(raw).hexdigest() != task["sha256"]:
                raise StoreError("archive_source_changed")
            artifacts = []
            if key.startswith("manifests/"):
                artifacts.append(Path(key).stem)
            elif key.startswith("exchange/") and key.endswith(".json"):
                value = json.loads(raw)
                artifacts.extend(value[name] for name in (
                    "model_artifact_id", "checkpoint_artifact_id", "shard_artifact_id",
                    "segment_artifact_id", "heads_artifact_id", "input_artifact_id")
                    if value.get(name) is not None and not value[name].startswith("hot:"))
            for artifact in artifacts:
                if key.startswith("manifests/"):
                    copy_artifact(self.source, self.target, artifact, scratch=self.root,
                                  reuse_published_parents=True)
                else:
                    # Exchange indexes name the same immutable artifact. Reuse
                    # its original manifest intent/readback receipt, rather than
                    # streaming the same payload again for every record/index.
                    manifest = self.source.get_manifest(artifact)
                    dependency = self.offer(f"manifests/{artifact}.json",
                                            manifest.to_bytes(), start=False)
                    self._copy(dependency)
            self.target.blobs.put_if_absent(key, raw)
            if self.target.blobs.get(key) != raw:
                raise StoreError("archive_record_readback_changed")
            self.queue.put_if_absent(receipt_key, json_bytes({**task,
                "completed_at": time.time(), "full_readback": True,
                "artifacts": artifacts, "archive": self.binding}))
            pending.unlink(missing_ok=True)  # The completed receipt retains the exact intent.

    def _unexpected_error(self, error: Exception, intent: str | None = None) -> None:
        self.last_error = {"stage": "shared_archive", "error_type": type(error).__name__,
                           "pending": intent, "at": time.time()}
        # Keep the in-memory failure/pending even when the scheduler log is unwritable.
        with suppress(OSError):
            print(json.dumps(self.last_error), file=sys.stderr, flush=True)

    def _record_error(self, error: BoundaryError, identity: str) -> None:
        self.last_error = {"stage": error.stage, "code": error.code,
                           "at": time.time(), "pending": identity}
        failure = {"stage": error.stage, "code": error.code, "pending": identity,
                   "archive": self.binding}
        failure_id = hashlib.sha256(json_bytes(failure)).hexdigest()
        self.queue.put_if_absent(f"errors/{failure_id}.json", json_bytes(failure))

    def _intent_metadata(self, identity: str, *, measure: bool = False) -> tuple[str, int | None]:
        """Bounded immutable key/size facts, shared by ordering and capacity observation."""
        with self.lock:
            entry = self.order_keys.get(identity)
            if entry is not None:
                self.order_keys.move_to_end(identity)
        if entry is None:
            task = json.loads(self.queue.get(f"pending/{identity}.json"))
            key = task["key"]
            safe_key(key)
            entry = key, None
        key, size = entry
        if measure and size is None:
            raw = self.source.blobs.get(key)
            size = len(raw)
            if key.startswith("manifests/"):
                manifest = Manifest.from_bytes(raw)
                size += 2 * sum(payload.size for payload in manifest.payloads)
        with self.lock:
            # An overlapping observer may already have measured this immutable
            # intent. A sorter must not replace its size with an unknown value.
            current = self.order_keys.get(identity)
            if size is None and current is not None:
                size = current[1]
            self.order_keys[identity] = key, size
            if len(self.order_keys) > 16384:
                self.order_keys.popitem(last=False)
        return key, size

    def _ordered_pending(self, identities: list[str],
                         deadline: float | None) -> list[str] | None:
        ranked = []
        for identity in identities:
            if deadline is not None and time.monotonic() >= deadline:
                break
            receipt = self.queue.root / "completed" / f"{identity}.json"
            try:
                key, _size = self._intent_metadata(identity)
            except BoundaryError as error:
                if error.code == "object_not_found" and receipt.is_file():
                    continue  # Another publisher finished after our first check.
                self._record_error(error, identity)
                return None
            except Exception as error:  # noqa: BLE001 -- retain malformed/IO-failed intent
                self._unexpected_error(error, identity)
                return None
            # Input indices already encode generation and zero-padded sequence.
            # Their previous_input DAG must be copied bottom-up, rather than
            # repeatedly discovering a long absent prefix from random SHA roots.
            rank = (0, key, identity) if "/inputs/" in key else (1, identity, identity)
            ranked.append(rank)
        return [identity for _, _, identity in sorted(ranked)]

    def step(self, *, deadline: float | None = None, owned: bool = False) -> int:
        if owned:
            with self.lock:
                identities = sorted(self.owned - self.owned_done)
        else:
            identities = [Path(key).stem for key in self.queue.keys("pending/")]
        ordered = self._ordered_pending(identities, deadline)
        if ordered is None:
            return 0
        completed = 0
        for identity in ordered:
            if deadline is not None and time.monotonic() >= deadline:
                break
            if (self.queue.root / "completed" / f"{identity}.json").is_file():
                continue
            try:
                self._copy(identity)
            except BoundaryError as error:
                if error.code == "already_running":
                    continue
                self._record_error(error, identity)
                return completed
            except Exception as error:  # noqa: BLE001 -- local quota/IO failures retain their intent
                self._unexpected_error(error, identity)
                return completed
            self.last_error = None
            completed += 1
        return completed

    def _loop(self) -> None:
        # One existing publisher also services orphan intents. Keep its OS lock
        # for the thread lifetime; releasing it each round recreates N global
        # scans. Other producers serve their own IDs without listing the outbox.
        with ExitStack() as leadership:
            global_scan = False
            while True:
                self.wake.wait(2)
                self.wake.clear()
                try:
                    if not global_scan:
                        try:
                            leadership.enter_context(instance_lock(
                                self.queue.root / "publisher.lock", create=False))
                        except BoundaryError as error:
                            if error.code != "already_running":
                                raise
                        else:
                            global_scan = True
                    self.step(owned=not global_scan)
                except Exception as error:  # noqa: BLE001 -- retain pending and classified failure
                    self._unexpected_error(error)
                finally:
                    self._refresh_owned()

    def _refresh_owned(self) -> None:
        # Other processes can fulfill our intents. Only the background/drain
        # owner stats still-pending receipts; learner event capture is O(1).
        with self.lock:
            pending = self.owned - self.owned_done
        completed = {i for i in pending
                     if (self.queue.root / "completed" / f"{i}.json").is_file()}
        with self.lock:
            self.owned_done.update(completed)
            self.verified_at = time.time()

    def reconcile(self, run: str | None = None) -> None:
        """Recover publication→intent crash gaps; no gameplay or artifact invention."""
        prefixes = ("manifests/", f"exchange/rl/{run}/" if run else "exchange/")
        for prefix in prefixes:
            for key in self.source.blobs.keys(prefix):
                self.offer(key, self.source.blobs.get(key), start=False)

    def status(self, *, owned: bool = False) -> dict[str, Any]:
        with self.lock:
            planned, done, verified_at = len(self.owned), len(self.owned_done), self.verified_at
        if not owned:
            identities = {Path(k).stem for k in self.queue.keys("pending/")} | {
                Path(k).stem for k in self.queue.keys("completed/")}
            planned = len(identities)
            done = sum((self.queue.root / "completed" / f"{i}.json").is_file() for i in identities)
            verified_at = time.time()
        return {"schema": SCHEMA, "planned": planned, "completed": done,
                "pending": planned - done, "full_readback": done == planned,
                "verified_at": verified_at,
                "last_error": self.last_error, "archive": self.binding}

    def drain(self, *, timeout: float = 120, owned: bool = True) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            self.step(deadline=deadline, owned=owned)
            self._refresh_owned()
            status = self.status(owned=owned)
            if status["full_readback"]:
                return status
            if time.monotonic() >= deadline:
                raise StoreError("shared_archive_incomplete",
                                 "retain shared Store and resume archive drain")
            time.sleep(.1)

    def seed_exchange(self, run: str) -> dict[str, Any]:
        """Copy one existing exchange exactly before switching its frozen peer.

        Artifact reads remain the existing verified Store read-through. This
        imports no other run, changes no records and never reoffers a game.
        """
        from spireagent.rl_exchange import Exchange

        prefix = Exchange({"kind": "local", "directory": str(self.root)}, run).prefix
        keys = self.target.blobs.keys(prefix)
        for key in keys:
            raw = self.target.blobs.get(key)
            self.source.blobs.put_if_absent(key, raw)
            if self.source.blobs.get(key) != raw:
                raise StoreError("shared_exchange_seed_readback_changed")
        return {"schema": SCHEMA, "operation": "seed_exchange", "run": run,
                "records": len(keys), "keys_sha256": hashlib.sha256(json_bytes(keys)).hexdigest(),
                "full_readback": True, "game_offers": 0, "archive": self.binding}


class ArchiveBlobs(LocalBlobStore):
    def __init__(self, root: Path, archive: Archive) -> None:
        super().__init__(root)
        self.archive = archive

    def put_if_absent(self, key: str, data: bytes) -> bool:
        result = super().put_if_absent(key, data)
        if key.startswith("exchange/"):
            self.archive.offer(key, data)
        return result


class ArchivedStore(ManifestArtifactStore):
    def __init__(self, root: Path, binding: dict[str, Any]) -> None:
        identity = (str(root), json_bytes(binding))
        with _guard:
            if identity not in _services:
                _services[identity] = Archive(root, binding)
            archive = _services[identity]
        self.archive = archive
        super().__init__(ArchiveBlobs(root, archive), read_peers=(archive.target,))

    def publish(self, manifest: Manifest) -> str:
        identity = super().publish(manifest)
        self.archive.offer(f"manifests/{identity}.json", manifest.to_bytes())
        return identity


def main() -> None:
    from .config import open_peer

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peer", type=Path, required=True,
                        help="nonsecret frozen local+archive binding")
    parser.add_argument("--run")
    parser.add_argument("--operation", choices=("seed", "drain", "status"), default="drain")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    store = open_peer(json.loads(args.peer.read_bytes()))
    if not isinstance(store, ArchivedStore):
        raise StoreError("explicit_shared_archive_peer_required")
    if args.operation == "seed":
        if not args.run:
            parser.error("seed requires the exact existing run")
        result = store.archive.seed_exchange(args.run)
    elif args.operation == "status":
        result = store.archive.status(owned=False)
    else:
        store.archive.reconcile(args.run)
        result = store.archive.drain(timeout=args.timeout, owned=False)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
