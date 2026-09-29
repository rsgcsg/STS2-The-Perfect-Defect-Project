"""M2 token inputs: one page Transformer write, independent light action reads."""

from __future__ import annotations

from tokenizers import Tokenizer

from spireagent.json_boundary import BoundaryError

from .token_inputs import TokenRow, input_texts


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
