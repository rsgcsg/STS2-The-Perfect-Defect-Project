"""One bounded numerical cache for all active immutable mu snapshots.

Blocks own full output storages, so evicting a row cannot accidentally retain a
large unaccounted matrix through Tensor views. Keys/container overhead is counted
conservatively alongside actual tensor storage. This is never training state.
"""

from __future__ import annotations

import collections
import sys
import threading
from typing import Any

import torch


class TextVectorCache:
    def __init__(self, limit: int = 512 * 1024**2, *, stream_safe: bool = False) -> None:
        self.limit = limit
        self.stream_safe = stream_safe
        self.lock = threading.RLock()
        self.rows: dict[tuple[str, bytes], tuple[int, int]] = {}
        self.blocks: collections.OrderedDict[
            int, tuple[str, tuple[bytes, ...], torch.Tensor, int, torch.cuda.Event | None]
        ] = collections.OrderedDict()
        self.sequence = self.bytes = self.hits = self.misses = self.evictions = 0

    def __deepcopy__(self, memo: dict[int, Any]) -> TextVectorCache:
        # Model publication deep-copies parameters before assigning a new mu.
        # Cached vectors, CUDA events and the mutation lock belong to the old
        # owner, not to those parameters. A copy starts cold with the same bound;
        # memo retains aliases within the new object graph, never across owners.
        with self.lock:
            copied = TextVectorCache(self.limit, stream_safe=self.stream_safe)
            memo[id(self)] = copied
            return copied

    def get(self, namespace: str, key: bytes) -> torch.Tensor | None:
        with self.lock:
            found = self.rows.get((namespace, key))
            if found is None:
                self.misses += 1
                return None
            block, row = found
            self.blocks.move_to_end(block)
            self.hits += 1
            _, _, values, _, ready = self.blocks[block]
            if ready is not None:
                consumer = torch.cuda.current_stream(values.device)
                consumer.wait_event(ready)
                values.record_stream(consumer)
            return values[row]

    def put(self, namespace: str, keys: tuple[bytes, ...], values: torch.Tensor) -> None:
        if values.requires_grad or torch.is_grad_enabled() or len(keys) != len(values):
            raise ValueError("immutable_no_grad_text_cache_only")
        if not keys:
            return
        with self.lock:
            if len(keys) != len(set(keys)):
                raise ValueError("text_cache_duplicate_miss")
            if any((namespace, key) in self.rows for key in keys):
                if self.stream_safe:
                    # Concurrent frozen-mu forwards can observe the same miss.
                    # The first published block wins; this forward keeps its own
                    # complete output, so dropping an optional cache insert does
                    # not change any value or overwrite an in-use allocation.
                    return
                raise ValueError("text_cache_duplicate_miss")
            # Count the full backing storage, not merely row views.
            values = values.detach().contiguous()
            size = (
                values.untyped_storage().nbytes()
                + sum(sys.getsizeof(key) + 256 for key in keys) + 512
            )
            if size > self.limit:
                return
            while self.blocks and self.bytes + size > self.limit:
                self._evict(next(iter(self.blocks)))
            ready = None
            if self.stream_safe and values.is_cuda:
                ready = torch.cuda.Event()
                ready.record(torch.cuda.current_stream(values.device))
            self.sequence += 1
            block = self.sequence
            self.blocks[block] = namespace, keys, values, size, ready
            for index, key in enumerate(keys):
                self.rows[namespace, key] = block, index
            self.bytes += size

    def _evict(self, block: int) -> None:
        namespace, keys, _, size, _ = self.blocks.pop(block)
        for key in keys:
            self.rows.pop((namespace, key))
        self.bytes -= size
        self.evictions += 1

    def retire(self, protected: set[str]) -> None:
        with self.lock:
            for block, (namespace, _, _, _, _) in list(self.blocks.items()):
                if namespace not in protected:
                    self._evict(block)

    def report(self) -> dict[str, Any]:
        with self.lock:
            return {
                "limit_bytes": self.limit,
                "accounted_bytes": self.bytes,
                "rows": len(self.rows),
                "blocks": len(self.blocks),
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
            }
