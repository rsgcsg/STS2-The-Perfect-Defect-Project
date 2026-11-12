"""Receiving side of `sts2.policy-runtime/fast-env-link-2` decision frames (P-REWRITE v3 E7).

The environment (`components/policy-runtime/src/fast-env/frame-delta.ts`) sends the first
decision line of a game as a full frame and every later one as the parts that changed since
the game's previous decision line. A part is one top-level value of the observation, or one
element of a top-level list. A decision line is

    {...routing head..., "input": {"input_spec": {...}, "frame": FRAME}, "parts": [P1, P2, ...]}

with `parts` the last field and FRAME

    base_row_id   null (full frame) | row_id of this game's previous decision line
    frame_sha256  digest of the whole observation text (below)
    keys          top-level keys in insertion order (`[A-Za-z0-9_]+`)
    layout        per key: "n" = the next part (never a list), "s" = the base's value unchanged,
                  or a list value: -1 = the next part, k >= 0 = element k of the base's list
    lengths       UTF-8 byte length of each part, in order

frame_sha256 = sha256 over the keys in order of utf8(key) 0x00 then `v` sha256(text) for a
value, or `l` uint32be(n) sha256(e1) .. sha256(en) for a list (binary digests), where a
part's text is its JSON text as sent (JSON.stringify on the environment side). It fixes the
key sequence and every part's bytes, and the observation text of a link-1 line is
`{"k":text,...}` with a list's text `[e1,...]`; so a frame whose rebuilt part digests give
frame_sha256 rebuilds that text byte for byte, and the parsed observation equals `json.loads`
of it, insertion order included. Each part is parsed and hashed once, from its own bytes; the
header is parsed without the parts. The caller also checks the encoded `observation_sha256`
against the line's. Any failure raises `FrameRefused`; the game's state is dropped and nothing
falls back to a full frame or is guessed.

Reused values are the very objects of the previous frame; nothing here or in the encoder
mutates an observation. `Frame.marshal` keeps the encoder's marshal bytes of this frame's
top-level values and top-level list elements, each with its object, for the next frame.
"""
from __future__ import annotations

import hashlib
import re
import struct
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from spireagent.json_boundary import BoundaryError

from .rows_encoder import parse_public_json

LINK_SCHEMA = "sts2.policy-runtime/fast-env-link-2"
FRAME_KEY = re.compile(r"[A-Za-z0-9_]+\Z")
PARTS_OPEN = b',"parts":['
LINE_TAIL = b"]}"
MAX_LANES = 4096
_FRAME_FIELDS = frozenset({"base_row_id", "frame_sha256", "keys", "layout", "lengths"})
_COUNT = struct.Struct(">I")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class FrameRefused(BoundaryError):
    """This decision line's frame does not rebuild exactly: the row is refused, never repaired."""

    def __init__(self, code: str) -> None:
        super().__init__("p1_link_frames", code)


def split_line(line: bytes) -> tuple[dict[str, Any], list[bytes]]:
    """(header without `parts`, each part's bytes) of one link-2 decision line."""
    at = line.find(PARTS_OPEN)
    if at < 0 or not line.endswith(LINE_TAIL):
        raise FrameRefused("frame_line_layout")
    header = parse_public_json(line[:at] + b"}")
    if not isinstance(header, dict):
        raise FrameRefused("frame_line_layout")
    source = header.get("input")
    frame = source.get("frame") if isinstance(source, dict) else None
    lengths = frame.get("lengths") if isinstance(frame, dict) else None
    if not isinstance(lengths, list) or any(type(n) is not int or n < 1 for n in lengths):
        raise FrameRefused("frame_lengths")
    cursor, end = at + len(PARTS_OPEN), len(line) - len(LINE_TAIL)
    parts = []
    for index, length in enumerate(lengths):
        if index:
            if line[cursor:cursor + 1] != b",":
                raise FrameRefused("frame_lengths")
            cursor += 1
        parts.append(line[cursor:cursor + length])
        cursor += length
    if cursor != end:
        raise FrameRefused("frame_lengths")
    return header, parts


@dataclass
class Frame:
    """One game's last accepted frame: values, their part digests and marshal bytes."""
    row_id: int
    values: dict[str, Any]
    # key -> sha256 of the value's text, or the list of its elements' digests
    digests: dict[str, bytes | list[bytes]]
    marshal: dict[int, tuple[Any, bytes]] = field(default_factory=dict)


def _layout_entry(entry: Any) -> bool:
    return entry in ("n", "s") or (isinstance(entry, list) and all(
        type(k) is int and k >= -1 for k in entry))


def rebuild(base: Frame | None, row_id: Any, frame: Any,
            parts: list[bytes]) -> tuple[dict[str, Any], Frame]:
    """The observation of one frame and the state it leaves (not yet stored)."""
    if not isinstance(frame, dict) or set(frame) != _FRAME_FIELDS:
        raise FrameRefused("frame_fields")
    keys, layout, base_row = frame["keys"], frame["layout"], frame["base_row_id"]
    if (type(row_id) is not int or row_id < 1 or not isinstance(keys, list)
            or not isinstance(layout, list) or len(layout) != len(keys)
            or any(type(k) is not str or FRAME_KEY.match(k) is None for k in keys)
            or len(set(keys)) != len(keys) or not all(_layout_entry(e) for e in layout)
            or not (isinstance(frame["frame_sha256"], str) and _SHA.match(frame["frame_sha256"]))
            or not (base_row is None or (type(base_row) is int and base_row >= 1))):
        raise FrameRefused("frame_fields")
    if base_row is None:
        if base is not None:
            raise FrameRefused("frame_restart")  # a game sends one full frame, its first
    elif base is None or base.row_id != base_row:
        raise FrameRefused("frame_base")
    values: dict[str, Any] = {}
    digests: dict[str, bytes | list[bytes]] = {}
    whole = hashlib.sha256()
    taken = 0

    def take() -> tuple[bytes, Any]:
        nonlocal taken
        if taken >= len(parts):
            raise FrameRefused("frame_parts")
        raw = parts[taken]
        taken += 1
        return hashlib.sha256(raw).digest(), parse_public_json(raw)

    for key, entry in zip(keys, layout, strict=True):
        if entry == "s":
            if base is None or key not in base.values:
                raise FrameRefused("frame_layout")
            value, digest = base.values[key], base.digests[key]
        elif entry == "n":
            digest, value = take()
            if isinstance(value, list):
                raise FrameRefused("frame_layout")  # list values are sent element by element
        else:
            old = base.values.get(key) if base is not None else None
            old_digests = base.digests[key] if old is not None and base is not None else None
            value, digest = [], []
            for k in entry:
                if k < 0:
                    item_digest, item = take()
                elif (isinstance(old, list) and isinstance(old_digests, list)
                      and k < len(old)):
                    item_digest, item = old_digests[k], old[k]
                else:
                    raise FrameRefused("frame_layout")
                value.append(item)
                digest.append(item_digest)
        values[key] = value
        digests[key] = digest
        whole.update(key.encode())
        if isinstance(digest, list):
            whole.update(b"\0l" + _COUNT.pack(len(digest)))
            whole.update(b"".join(digest))
        else:
            whole.update(b"\0v" + digest)
    if taken != len(parts):
        raise FrameRefused("frame_parts")
    if whole.hexdigest() != frame["frame_sha256"]:
        raise FrameRefused("frame_sha256")
    return values, Frame(row_id, values, digests)


class Lanes:
    """Per-game frames of one encoder process (lane = game_instance_id), bounded."""

    def __init__(self, max_lanes: int = MAX_LANES) -> None:
        self.max_lanes = max_lanes
        self.frames: OrderedDict[str, Frame] = OrderedDict()
        self.stats = {"frames": 0, "full_frames": 0, "parts": 0, "refused": 0, "evicted": 0}

    def get(self, lane: str) -> Frame | None:
        return self.frames.get(lane)

    def store(self, lane: str, frame: Frame) -> None:
        self.frames[lane] = frame
        self.frames.move_to_end(lane)
        while len(self.frames) > self.max_lanes:
            self.frames.popitem(last=False)
            self.stats["evicted"] += 1

    def drop(self, lane: str) -> None:
        self.frames.pop(lane, None)


def rebuild_lines(lines: list[bytes]) -> list[dict[str, Any]]:
    """Offline: the observations of one game's link-2 decision lines, in order (originals)."""
    base: Frame | None = None
    out = []
    for line in lines:
        header, parts = split_line(line)
        observation, base = rebuild(base, header.get("row_id"), header["input"]["frame"], parts)
        out.append(observation)
    return out
