"""Parameter-free vocabulary indexing and text encoding shared by M-std inputs."""
from __future__ import annotations

from typing import Any

from tokenizers import Tokenizer

from .input import is_v2_encoding, normalized, signed_log


class InputEncoding:
    """Only a tokenizer, fixed row tables and an instance-local text cache.

    No learned module is constructed and no random generator is accessed. The
    caller owns its vocabulary; independent compilers take their own copy.
    """

    def __init__(self, vocabulary: dict[str, Any]) -> None:
        self.vocabulary = vocabulary
        self.tokenizer = Tokenizer.from_str(vocabulary["tokenizer"])
        self.tables = {
            key: {name: i for i, name in enumerate(vocabulary[key])}
            for key in ("ids", "kinds", "fields", "actions")
        }
        self.tokens: dict[str, tuple[list[int], list[float]]] = {}

    def index(self, table: str, value: str) -> int:
        return self.tables[table].get(value, 0)

    def tokenize(self, text: str) -> tuple[list[int], list[float]]:
        if text not in self.tokens:
            normalized_text, numbers = normalized(text)
            ids = self.tokenizer.encode(normalized_text).ids or [
                self.tokenizer.token_to_id("<EMPTY>")
                if is_v2_encoding(self.vocabulary.get("input_spec")) else 1
            ]
            numeric = [0.0] * len(ids)
            cursor = 0
            for i, token in enumerate(ids):
                if token == self.tokenizer.token_to_id("<NUM>"):
                    numeric[i] = signed_log(numbers[cursor])
                    cursor += 1
            if cursor != len(numbers):
                raise ValueError("text numeric marker/token binding mismatch")
            self.tokens[text] = ids, numeric
        return self.tokens[text]
