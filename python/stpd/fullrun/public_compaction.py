"""Lossless repeated-object interning for public model text; no field filtering."""
from __future__ import annotations

from collections import Counter
from typing import Any

from spireagent.json_boundary import BoundaryError

from ..canonical import canonical_json

REFERENCE = "$stpd_fact"


def compact_public_state(state: dict) -> dict:
    counts: Counter[str] = Counter()
    originals = {}

    def visit(value: Any) -> None:
        if not isinstance(value, (dict, list)):
            return
        if isinstance(value, dict) and REFERENCE in value:
            raise BoundaryError("public_input", "reserved_compaction_key")
        encoded = canonical_json(value)
        if len(encoded) >= 256:
            counts[encoded] += 1
            originals[encoded] = value
        for child in (value.values() if isinstance(value, dict) else value):
            visit(child)

    visit(state)
    # Content order is deterministic; catalog order and opaque binding IDs are absent.
    keys = {text: f"f{i}" for i, text in enumerate(sorted(t for t, n in counts.items() if n > 1))}

    def encode(value: Any, *, definition: bool = False) -> Any:
        if isinstance(value, (dict, list)):
            key = keys.get(canonical_json(value))
            if key is not None and not definition:
                return {REFERENCE: key}
        if isinstance(value, dict):
            return {k: encode(v) for k, v in value.items()}
        if isinstance(value, list):
            return [encode(v) for v in value]
        return value

    return {"FACTS": {key: encode(originals[text], definition=True) for text, key in keys.items()},
            "STATE": encode(state)}


def expand_public_state(value: dict) -> dict:
    """Diagnostic inverse for this encoder's output, not a new external input parser."""
    def expand(item: Any) -> Any:
        if isinstance(item, dict):
            if set(item) == {REFERENCE}:
                return expand(value["FACTS"][item[REFERENCE]])
            return {k: expand(v) for k, v in item.items()}
        if isinstance(item, list):
            return [expand(v) for v in item]
        return item

    return expand(value["STATE"])  # type: ignore[no-any-return]
