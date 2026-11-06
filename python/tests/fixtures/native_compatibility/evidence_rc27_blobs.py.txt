"""Bounded immutable blob protocol shared by local and S3 adapters."""

from __future__ import annotations

import re
from typing import Protocol

from spireagent.json_boundary import BoundaryError

MAX_BLOB_BYTES = 16 * 1024 * 1024


class StoreError(BoundaryError):
    def __init__(
        self, code: str, recovery: str = "verify store configuration and exact objects"
    ) -> None:
        super().__init__("artifact_store", code, recovery)


class BlobStore(Protocol):
    def put_if_absent(self, key: str, data: bytes) -> bool:
        """Atomically create; identical existing bytes are idempotent; collisions fail."""
        ...

    def get(self, key: str) -> bytes: ...

    def keys(self, prefix: str) -> tuple[str, ...]: ...


def safe_key(key: str, *, prefix: bool = False) -> str:
    candidate = key[:-1] if prefix and key.endswith("/") else key
    if not candidate or re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", candidate) is None:
        raise StoreError("invalid_object_key")
    for part in candidate.split("/"):
        stem = part.split(".", 1)[0]
        if (
            part in {"", ".", ".."}
            or part.endswith(".")
            or stem
            in {
                "con",
                "prn",
                "aux",
                "nul",
                *(f"com{i}" for i in range(1, 10)),
                *(f"lpt{i}" for i in range(1, 10)),
            }
        ):
            raise StoreError("nonportable_object_key")
    return key


def bounded(data: bytes) -> None:
    if not isinstance(data, bytes) or len(data) > MAX_BLOB_BYTES:
        raise StoreError("blob_exceeds_bounded_transport")
