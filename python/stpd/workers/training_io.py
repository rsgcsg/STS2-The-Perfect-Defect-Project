"""Reusable learner I/O: one latest progress record and one frozen checkpoint.

The learner owns losses, sampling, authority, metadata and event order. This
module owns cadence and immutable publication through the existing Store. It
never accepts a model, reads a mutable trainer or selects a result/lease.
"""
from __future__ import annotations

import io
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, TypeAlias

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.store import ArtifactStore

from .checkpoint_codec import MAX_BYTES

CheckpointPublication: TypeAlias = tuple[Manifest, dict[str, float | int]]
PROGRESS_STEPS = 50
PROGRESS_SECONDS = 30
AUTHORITY_SECONDS = 30


class ProgressCadence:
    """Coalesce ordinary progress; callers force DEV and publication boundaries."""

    def __init__(self, step: int, *, steps: int = PROGRESS_STEPS, seconds: float = PROGRESS_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.steps, self.seconds, self.clock = steps, seconds, clock
        self.last_step, self.last_at = step, clock()
        self.pending: dict[str, Any] | None = None

    def offer(self, step: int, details: dict[str, Any]) -> bool:
        if type(step) is not int or step < self.last_step:
            raise BoundaryError("training_io", "monotone_optimizer_step_required")
        self.pending = {**details, "optimizer_updates": step}
        return step - self.last_step >= self.steps or self.clock() - self.last_at >= self.seconds

    def published(self) -> None:
        if self.pending is not None:
            self.last_step = self.pending["optimizer_updates"]
        self.last_at, self.pending = self.clock(), None


@dataclass(frozen=True)
class FrozenCheckpoint:
    """Capture bytes and every parent/metadata field on the learner owner thread."""

    raw: bytes
    parents: tuple[Parent, ...]
    parameters: FrozenObject
    encoded_seconds: float

    def __post_init__(self) -> None:
        if type(self.raw) is not bytes or not 0 < len(self.raw) <= MAX_BYTES:
            raise BoundaryError("training_io", "bounded_immutable_checkpoint_required")


class CheckpointPublisher:
    """One background immutable publication; acknowledgment stays on the owner.

    A fenced parent/child Store must use background=False: its artifact
    reservation channel has no concurrent writer contract. Cloud candidate
    Stores can use background=True; final Hub attempt/fence acceptance is still
    outside this helper. All original Store verification remains in publish().
    """

    def __init__(self, store: ArtifactStore, producer: Producer, *, background: bool) -> None:
        self.store, self.producer = store, producer
        self.owner = threading.get_ident()
        self.pool = (ThreadPoolExecutor(max_workers=1, thread_name_prefix="p1-checkpoint")
                     if background else None)
        self._pending: Future[CheckpointPublication] | None = None

    @property
    def background(self) -> bool:
        return self.pool is not None

    @property
    def pending(self) -> bool:
        return self._pending is not None

    def _owner(self) -> None:
        if threading.get_ident() != self.owner:
            raise BoundaryError("training_io", "checkpoint_owner_thread_required")

    def publish(self, snapshot: FrozenCheckpoint, *,
                before_publish: Callable[[], None] | None = None) -> CheckpointPublication:
        if before_publish is not None or not self.background:
            self._owner()
        began = time.perf_counter()
        payload = self.store.put_payload(
            "checkpoint", io.BytesIO(snapshot.raw), "application/vnd.stpd.tensor-tree")
        manifest = Manifest("checkpoint", self.producer, snapshot.parents,
                            (payload,), snapshot.parameters)
        if before_publish is not None:
            before_publish()
        self.store.publish(manifest)  # Includes complete payload readback, never bypassed.
        elapsed = time.perf_counter() - began
        return manifest, {"encode_seconds": snapshot.encoded_seconds, "publish_seconds": elapsed,
                          "checkpoint_seconds": snapshot.encoded_seconds + elapsed,
                          "checkpoint_bytes": len(snapshot.raw)}

    def submit(self, snapshot: FrozenCheckpoint) -> None:
        self._owner()
        if self.pool is None or self._pending is not None:
            raise BoundaryError("training_io", "single_background_checkpoint_required")
        self._pending = self.pool.submit(self.publish, snapshot)

    def take(self, *, wait: bool) -> CheckpointPublication | None:
        self._owner()
        future = self._pending
        if future is None or not wait and not future.done():
            return None
        self._pending = None
        return future.result()  # Propagate upload and verification failures to the owner.

    def __enter__(self) -> CheckpointPublisher:
        return self

    def __exit__(self, exception_type: Any, *_args: Any) -> None:
        if self.pool is not None:
            self.pool.shutdown(wait=True)
        if exception_type is None and self._pending is not None:
            self._pending.result()
            raise BoundaryError("training_io", "checkpoint_acknowledgment_required")
