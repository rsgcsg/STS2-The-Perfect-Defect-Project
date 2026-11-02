"""One bounded numerical cache for all active immutable mu snapshots.

Blocks own full output storages, so evicting a row cannot accidentally retain a
large unaccounted matrix through Tensor views. Keys/container overhead is counted
conservatively alongside actual tensor storage. This is never training state.
"""

from __future__ import annotations

import collections
import sys
from typing import Any

import torch


class TextVectorCache:
    def __init__(self, limit: int = 512 * 1024**2) -> None:
        self.limit = limit
        self.rows: dict[tuple[str, bytes], tuple[int, int]] = {}
        self.blocks: collections.OrderedDict[
            int, tuple[str, tuple[bytes, ...], torch.Tensor, int]
        ] = collections.OrderedDict()
        self.sequence = self.bytes = self.hits = self.misses = self.evictions = 0

    def get(self, namespace: str, key: bytes) -> torch.Tensor | None:
        found = self.rows.get((namespace, key))
        if found is None:
            self.misses += 1
            return None
        block, row = found
        self.blocks.move_to_end(block)
        self.hits += 1
        return self.blocks[block][2][row]

    def put(self, namespace: str, keys: tuple[bytes, ...], values: torch.Tensor) -> None:
        if values.requires_grad or torch.is_grad_enabled() or len(keys) != len(values):
            raise ValueError("immutable_no_grad_text_cache_only")
        if not keys:
            return
        # Projection creates one contiguous output matrix. Count the backing
        # storage, not merely each row view's logical elements.
        values = values.detach().contiguous()
        size = (
            values.untyped_storage().nbytes() + sum(sys.getsizeof(key) + 256 for key in keys) + 512
        )
        if size > self.limit:
            return
        while self.blocks and self.bytes + size > self.limit:
            self._evict(next(iter(self.blocks)))
        self.sequence += 1
        block = self.sequence
        self.blocks[block] = namespace, keys, values, size
        for index, key in enumerate(keys):
            if (namespace, key) in self.rows:
                raise ValueError("text_cache_duplicate_miss")
            self.rows[namespace, key] = block, index
        self.bytes += size

    def _evict(self, block: int) -> None:
        namespace, keys, _, size = self.blocks.pop(block)
        for key in keys:
            self.rows.pop((namespace, key))
        self.bytes -= size
        self.evictions += 1

    def retire(self, protected: set[str]) -> None:
        for block, (namespace, _, _, _) in list(self.blocks.items()):
            if namespace not in protected:
                self._evict(block)

    def report(self) -> dict[str, Any]:
        return {
            "limit_bytes": self.limit,
            "accounted_bytes": self.bytes,
            "rows": len(self.rows),
            "blocks": len(self.blocks),
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
        }
