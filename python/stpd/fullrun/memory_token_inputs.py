"""M2 token inputs: one page Transformer write, independent light action reads."""

from __future__ import annotations

from typing import Any

from tokenizers import Tokenizer

from spireagent.json_boundary import BoundaryError, decode_json, json_bytes

from .confirmed_interaction import HISTORY_INPUT_PROFILE, V2_HISTORY_INPUT_PROFILE
from .memory_projection_config import (
    RENDERER_IDENTITY as RENDERER_IDENTITY,
)
from .memory_projection_config import (
    V2_RENDERER_IDENTITY as V2_RENDERER_IDENTITY,
)
from .memory_projection_config import (
    renderer_identity_for_profile as renderer_identity_for_profile,
)
from .text_menu_inputs import (
    INPUT_PROFILE,
    V2_INPUT_PROFILE,
    TextMenuInput,
    project_text_menu_snapshot,
    project_text_menu_v2_snapshot,
)
from .token_inputs import TokenRow, input_texts

MAX_TOKENIZER_BYTES = 16 * 1024 * 1024


def project_memory_snapshot(snapshot: dict[str, Any]) -> TextMenuInput:
    """One M2 rendering order for verified training sources and live bytes.

    JSON object key order is not page information. Arrays retain native order;
    this changes no historical four-graph renderer or stored token artifact.
    """
    return project_text_menu_snapshot(decode_json(json_bytes(snapshot)))


def project_memory_v2_snapshot(snapshot: dict[str, Any]) -> TextMenuInput:
    """Explicit v2 renderer; existing M2 artifacts keep their v1 identity."""
    return project_text_menu_v2_snapshot(decode_json(json_bytes(snapshot)))


def project_memory_profile_snapshot(snapshot: dict[str, Any],
                                    input_profile: str) -> TextMenuInput:
    """Render only a caller-selected, closed text-menu input profile."""
    if input_profile in {INPUT_PROFILE, HISTORY_INPUT_PROFILE}:
        return project_memory_snapshot(snapshot)
    if input_profile in {V2_INPUT_PROFILE, V2_HISTORY_INPUT_PROFILE}:
        return project_memory_v2_snapshot(snapshot)
    raise BoundaryError("memory_tokens", "unknown_text_menu_profile")


def encode_memory_texts(
    tokenizer: Tokenizer, state: str, actions: tuple[str, ...], *,
    max_tokens: int, slots: int, previous_actual_action: bool = False,
    feedback: bool = False,
) -> TokenRow:
    """Encode a complete M2 menu with its actual page and action capacities."""
    if (type(max_tokens) is not int or max_tokens <= 0
            or type(slots) is not int or slots <= 0
            or type(previous_actual_action) is not bool or type(feedback) is not bool):
        raise BoundaryError("memory_tokens", "invalid_token_budget")
    if not actions:
        raise BoundaryError("memory_tokens", "empty_catalog")
    page_text, action_texts = input_texts(state, actions)
    row = TokenRow(
        tuple(tokenizer.encode(page_text, add_special_tokens=False).ids),
        tuple(tuple(tokenizer.encode(action, add_special_tokens=False).ids)
              for action in action_texts),
    )
    if not row.state or any(not action for action in row.actions):
        raise BoundaryError("memory_tokens", "empty_encoding")
    if (2 * slots + len(row.state) + int(previous_actual_action) + int(feedback)
            > max_tokens or any(len(action) > max_tokens for action in row.actions)):
        raise BoundaryError("memory_tokens", "m2_limit_exceeded_no_truncation")
    return row
