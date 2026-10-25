"""One operation's immutable read cache, bounded by an owning scratch admission.

Every first read uses the original Store verifier. Repeated reads use the exact
verified bytes, and still check the whole hash. This is neither a persisted
verification receipt nor a different Store or lineage authority.
"""
from __future__ import annotations

import hashlib
import tempfile
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from spireagent.artifact_contracts import Manifest, Payload

from .blobs import StoreError
from .store import ArtifactStore, BinarySource

T = TypeVar("T")


class VerifiedReadSession:
    def __init__(self, store: ArtifactStore, *, scratch: Path | None = None) -> None:
        if scratch is not None:
            scratch.mkdir(parents=True, exist_ok=True)
        self.store = store
        self._directory = tempfile.TemporaryDirectory(prefix="verified-read-", dir=scratch)
        self._manifests: dict[str, Manifest] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._values: dict[tuple[str, str], Any] = {}
        self.closed = False

    @property
    def blobs(self) -> Any:
        return self.store.blobs  # type: ignore[attr-defined]

    def memoize(self, namespace: str, identity: str, load: Callable[[], T]) -> T:
        if self.closed:
            raise StoreError("read_session_closed")
        key = namespace, identity
        if key not in self._values:
            self._values[key] = load()
        return self._values[key]  # type: ignore[no-any-return]

    def get_manifest(self, identity: str) -> Manifest:
        if self.closed:
            raise StoreError("read_session_closed")
        if identity not in self._manifests:
            self._manifests[identity] = self.store.get_manifest(identity)
        return self._manifests[identity]

    def read_payload(self, payload: Payload) -> Iterator[bytes]:
        if self.closed:
            raise StoreError("read_session_closed")
        with self._guard:
            lock = self._locks.setdefault(payload.sha256, threading.Lock())
        path = Path(self._directory.name) / payload.sha256
        with lock:
            if not path.exists():
                pending = path.with_suffix(".pending")
                try:
                    with pending.open("wb") as stream:
                        for chunk in self.store.read_payload(payload):
                            stream.write(chunk)
                    pending.replace(path)
                finally:
                    pending.unlink(missing_ok=True)
        whole = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(8 * 1024**2):
                whole.update(chunk)
                size += len(chunk)
                yield chunk
        if size != payload.size or whole.hexdigest() != payload.sha256:
            raise StoreError("read_session_integrity_failure")

    def prefetch(self, payloads: tuple[Payload, ...], *, concurrency: int = 8) -> None:
        def read(payload: Payload) -> None:
            for _ in self.read_payload(payload):
                pass

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            list(pool.map(read, {p.sha256: p for p in payloads}.values()))

    def put_payload(self, role: str, source: BinarySource,
                    media_type: str = "application/octet-stream") -> Payload:
        return self.store.put_payload(role, source, media_type)

    def publish(self, manifest: Manifest) -> str:
        return self.store.publish(manifest)

    def manifest_ids(self) -> tuple[str, ...]:
        return self.store.manifest_ids()

    def close(self) -> None:
        self._values.clear()
        self._manifests.clear()
        self._directory.cleanup()
        self.closed = True

    def __enter__(self) -> VerifiedReadSession:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
