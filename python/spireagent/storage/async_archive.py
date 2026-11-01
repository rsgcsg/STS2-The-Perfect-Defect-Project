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
import sys
import threading
import time
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
        self.verified_at: float | None = None
        self.last_error: dict[str, Any] | None = None

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
                self.thread = threading.Thread(target=self._loop,
                    name="shared-store-archive", daemon=True)
                self.thread.start()
        self.wake.set()
        return identity

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
            elif key.startswith("exchange/"):
                value = json.loads(raw)
                artifacts.extend(value[name] for name in (
                    "model_artifact_id", "checkpoint_artifact_id", "shard_artifact_id")
                    if value.get(name) is not None)
            for artifact in artifacts:
                copy_artifact(self.source, self.target, artifact, scratch=self.root,
                              reuse_published_parents=True)
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
        try:
            print(json.dumps(self.last_error), file=sys.stderr, flush=True)
        except OSError:
            pass  # Keep the in-memory failure/pending when even the scheduler log is unwritable.

    def step(self, *, deadline: float | None = None) -> int:
        completed = 0
        for key in self.queue.keys("pending/"):
            if deadline is not None and time.monotonic() >= deadline:
                break
            identity = Path(key).stem
            if (self.queue.root / "completed" / f"{identity}.json").is_file():
                continue
            try:
                self._copy(identity)
            except BoundaryError as error:
                if error.code == "already_running":
                    continue
                self.last_error = {"stage": error.stage, "code": error.code,
                                   "at": time.time(), "pending": identity}
                failure = {"stage": error.stage, "code": error.code, "pending": identity,
                           "archive": self.binding}
                failure_id = hashlib.sha256(json_bytes(failure)).hexdigest()
                self.queue.put_if_absent(f"errors/{failure_id}.json", json_bytes(failure))
                return completed
            except Exception as error:  # noqa: BLE001 -- local quota/IO failures retain their intent
                self._unexpected_error(error, identity)
                return completed
            self.last_error = None
            completed += 1
        return completed

    def _loop(self) -> None:
        while True:
            self.wake.wait(2)
            self.wake.clear()
            try:
                self.step()
            except Exception as error:  # noqa: BLE001 -- preserve pending, expose classified failure
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
            self.step(deadline=deadline)
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
