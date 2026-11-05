"""Frozen RL4.2 byte-source contract shared by control and tensor preparation.

The application owns transport and reference lifetime; research owns packet
encoding and numerical interpretation. No transport or codec implementation
belongs in this module. Historical JSON/HotPayloadRef schemas are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ContextManager, Protocol

TENSOR_SEGMENT_FORMAT = "stpd/rl-tensor-segment-1"
TENSOR_SEGMENT_SCHEMA = TENSOR_SEGMENT_FORMAT
TENSOR_SEGMENT_MAGIC = b"STPDRL01"
CONTROL_SCHEMA = "spireagent/rl-control-input-2"
SEGMENT_INPUT_SCHEMA = "spireagent/rl-segment-input-2"
EPISODE_END_INPUT_SCHEMA = "spireagent/rl-episode-end-input-2"
SOCKET_SCHEMA = "spireagent/rl-socket-1"
WAL_SCHEMA = "spireagent/rl-local-wal-1"
MAX_PACKET_BYTES = 256 * 1024 * 1024
MAX_HEADER_BYTES = 8 * 1024 * 1024
MAX_CHUNK_BYTES = 1024 * 1024
MAX_SEGMENT_ROWS = 64


@dataclass(frozen=True)
class BinaryRef:
    """Exact complete-packet identity and opaque run/generation/source locator."""

    format: str
    slot: str
    sha256: str
    size: int


@dataclass(frozen=True)
class EncodedSegment:
    """Producer-sealed metadata and packet, immutable by ownership after encode."""

    envelope: dict[str, Any]
    packet: bytes


class BinarySourceProvider(Protocol):
    """Immutable verified bytes; a borrow pins storage until its context exits.

    Providers check the complete packet size/hash before exposing a read-only
    view. Retained owners independently pin a source. Spawn workers receive
    P1-owned shared-slot descriptors, never this provider or a borrowed view.
    """

    def borrow(self, ref: BinaryRef) -> ContextManager[memoryview]: ...
    def retain(self, ref: BinaryRef, owner: str) -> None: ...
    def release(self, ref: BinaryRef, owner: str) -> None: ...
