"""Test-side writer of `fast-env-link-2` frames, written from the frame format alone.

It shares no code with the environment's `frame-delta.ts` or the receiver `stpd.p1.link_frames`.
A part's text is `json.dumps(value, ensure_ascii=False, separators=(",", ":"))`, JSON.stringify's
text for the values the tests use.
"""
from __future__ import annotations

import hashlib
import json
import struct
from typing import Any


def text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class FrameWriter:
    """One game's sending side: `frame(view, row_id)` -> (frame object, part texts)."""

    def __init__(self) -> None:
        self.base: tuple[int, dict[str, Any]] | None = None

    def frame(self, view: dict[str, Any], row_id: int) -> tuple[dict[str, Any], list[str]]:
        old = self.base[1] if self.base else {}
        keys, layout, parts, kept = [], [], [], {}
        whole = hashlib.sha256()
        for key, value in view.items():
            keys.append(key)
            whole.update(key.encode())
            if isinstance(value, list):
                items = [text(item) for item in value]
                before = old.get(key)
                if isinstance(before, list) and before == items:
                    layout.append("s")
                else:
                    index: dict[str, int] = {}
                    for at, item in enumerate(before if isinstance(before, list) else []):
                        index.setdefault(item, at)
                    refs = []
                    for item in items:
                        if item in index:
                            refs.append(index[item])
                        else:
                            refs.append(-1)
                            parts.append(item)
                    layout.append(refs)
                whole.update(b"\0l" + struct.pack(">I", len(items)))
                for item in items:
                    whole.update(hashlib.sha256(item.encode()).digest())
                kept[key] = items
            else:
                item = text(value)
                if old.get(key) == item:
                    layout.append("s")
                else:
                    layout.append("n")
                    parts.append(item)
                whole.update(b"\0v" + hashlib.sha256(item.encode()).digest())
                kept[key] = item
        frame = {"base_row_id": self.base[0] if self.base else None,
                 "frame_sha256": whole.hexdigest(), "keys": keys, "layout": layout,
                 "lengths": [len(part.encode()) for part in parts]}
        self.base = (row_id, kept)
        return frame, parts


def link2_line(head: str, input_spec: dict[str, Any], frame: dict[str, Any],
               parts: list[str]) -> bytes:
    """`head` is the routing head up to and including its last `,`."""
    return (head + '"input":{"input_spec":' + text(input_spec) + ',"frame":' + text(frame)
            + '},"parts":[' + ",".join(parts) + "]}").encode()
