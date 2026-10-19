"""Approved Q3 M-std: text(2x128), scene(3x192/4), action cross-attention.

No memory, O, next/end head or length truncation. Numerical and symbolic field
embeddings are learned from the current train-only vocabulary.
"""

from __future__ import annotations

import math
from typing import Any, cast

import torch
from tokenizers import Tokenizer
from torch import Tensor, nn

from .input import Frame, normalized, signed_log

ARCHITECTURE: dict[str, Any] = {
    "id": "q3-m-std-1",
    "text_width": 128,
    "text_layers": 2,
    "text_heads": 4,
    "scene_width": 192,
    "scene_layers": 3,
    "scene_heads": 4,
    "text_ff": 512,
    "scene_ff": 768,
    "action_cross_layers": 1,
    "dropout": 0.1,
    "id_mask": 0.1,
    "id_capacity": 8192,
    "text_capacity": 4096,
    "memory": False,
    "auxiliary_heads": [],
}


def positions(indices: Tensor, width: int) -> Tensor:
    scale = torch.exp(
        torch.arange(0, width, 2, device=indices.device) * (-math.log(10000.0) / width)
    )
    angles = indices.float().unsqueeze(-1) * scale
    return torch.stack((angles.sin(), angles.cos()), -1).flatten(-2)


class MStd(nn.Module):
    def __init__(self, vocabulary: dict[str, Any]):
        super().__init__()
        self.vocabulary = vocabulary
        self.tokenizer = Tokenizer.from_str(vocabulary["tokenizer"])
        self.tables = {
            key: {name: i for i, name in enumerate(vocabulary[key])}
            for key in ("ids", "kinds", "fields", "actions")
        }
        if len(vocabulary["ids"]) > ARCHITECTURE["id_capacity"]:
            raise ValueError("symbolic ID vocabulary exceeds approved capacity")
        self.text_ids = nn.Embedding(4096, 128, padding_idx=0)
        self.text_numbers = nn.Linear(1, 128, bias=False)
        self.text_blocks = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(128, 4, 512, dropout=0.1, batch_first=True),
            2,
            enable_nested_tensor=False,
        )
        self.text_projection = nn.Linear(128, 192)
        self.ids = nn.Embedding(8192, 192)
        self.kinds = nn.Embedding(len(vocabulary["kinds"]), 192)
        self.fields = nn.Embedding(len(vocabulary["fields"]), 192)
        # Each field has its own scalar-to-vector linear transformation.
        self.number_slope = nn.Embedding(len(vocabulary["fields"]), 192)
        self.number_bias = nn.Embedding(len(vocabulary["fields"]), 192)
        self.number_missing = nn.Embedding(len(vocabulary["fields"]), 192)
        self.no_numbers = nn.Parameter(torch.zeros(192))
        self.relations = nn.Linear(192, 192, bias=False)
        self.node_norm = nn.LayerNorm(192)
        self.scene = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(192, 4, 768, dropout=0.1, batch_first=True),
            3,
            enable_nested_tensor=False,
        )
        self.action_types = nn.Embedding(len(vocabulary["actions"]), 192)
        self.source_role = nn.Linear(192, 192, bias=False)
        self.target_role = nn.Linear(192, 192, bias=False)
        self.absent_roles = nn.Parameter(torch.zeros(2, 192))
        self.cross = nn.MultiheadAttention(192, 4, dropout=0.1, batch_first=True)
        self.head = nn.Sequential(nn.Linear(384, 192), nn.GELU(), nn.Linear(192, 1))
        self._tokens: dict[str, tuple[list[int], list[float]]] = {}

    def index(self, table: str, value: str) -> int:
        return self.tables[table].get(value, 0)

    def tokenize(self, text: str) -> tuple[list[int], list[float]]:
        if text not in self._tokens:
            normalized_text, numbers = normalized(text)
            ids = self.tokenizer.encode(normalized_text).ids or [1]
            numeric = [0.0] * len(ids)
            cursor = 0
            for i, token in enumerate(ids):
                if token == self.tokenizer.token_to_id("<NUM>"):
                    numeric[i] = signed_log(numbers[cursor])
                    cursor += 1
            if cursor != len(numbers):
                raise ValueError("text numeric marker/token binding mismatch")
            self._tokens[text] = ids, numeric
        return self._tokens[text]

    def text_vectors(self, texts: list[str]) -> Tensor:
        device = self.ids.weight.device
        tokens = [self.tokenize(text) for text in texts]
        width = max(len(ids) for ids, _ in tokens)
        ids = torch.zeros(len(tokens), width, dtype=torch.long, device=device)
        nums = torch.zeros(len(tokens), width, 1, device=device)
        mask = torch.ones(len(tokens), width, dtype=torch.bool, device=device)
        for i, (row, numbers) in enumerate(tokens):
            ids[i, : len(row)] = torch.tensor(row, device=device)
            nums[i, : len(row), 0] = torch.tensor(numbers, device=device)
            mask[i, : len(row)] = False
        value = self.text_ids(ids) + self.text_numbers(nums)
        value = value + positions(torch.arange(width, device=device), 128)
        encoded = self.text_blocks(value, src_key_padding_mask=mask)
        encoded = (encoded * (~mask).unsqueeze(-1)).sum(1) / (~mask).sum(1).unsqueeze(-1)
        return cast(Tensor, self.text_projection(encoded))

    def forward(self, frame: Frame) -> Tensor:
        device = self.ids.weight.device
        entities = frame.entities
        texts = sorted({text for node in entities for _, text in node.text})
        text_rows = {text: i for i, text in enumerate(texts)}
        vectors = self.text_vectors(texts) if texts else self.ids.weight.new_zeros((0, 192))
        id_pairs = [
            (row, key, value) for row, e in enumerate(entities) for key, value in e.identities
        ]
        identities = torch.tensor(
            [self.index("ids", value) for _, _, value in id_pairs], dtype=torch.long, device=device
        )
        if self.training:
            identities = identities.masked_fill(torch.rand(len(identities), device=device) < 0.1, 0)
        id_vectors = self.ids(identities)
        by_owner: dict[int, list[Tensor]] = {}
        for i, (row, key, _) in enumerate(id_pairs):
            by_owner.setdefault(row, []).append(
                id_vectors[i] + self.fields.weight[self.index("fields", key)]
            )
        node_ids = torch.stack(
            [
                torch.stack(by_owner[row]).mean(0) if row in by_owner else self.ids.weight[0]
                for row in range(len(entities))
            ]
        )
        nodes = node_ids + self.kinds(
            torch.tensor([self.index("kinds", e.kind) for e in entities], device=device)
        )
        nodes = nodes + positions(torch.tensor([e.position for e in entities], device=device), 192)
        features = []
        for entity in entities:
            text_terms = [
                vectors[text_rows[text]] + self.fields.weight[self.index("fields", key)]
                for key, text in entity.text
            ]
            number_terms = []
            for key, number in entity.numeric:
                row = self.index("fields", key)
                number_terms.append(
                    self.number_missing.weight[row]
                    if number is None
                    else self.number_slope.weight[row] * signed_log(number)
                    + self.number_bias.weight[row]
                )
            features.append(
                (torch.stack(text_terms).mean(0) if text_terms else 0)
                + (torch.stack(number_terms).mean(0) if number_terms else self.no_numbers)
            )
        nodes = nodes + torch.stack(features)
        linked = torch.stack(
            [nodes[e.refs].mean(0) if e.refs else torch.zeros_like(nodes[0]) for e in entities]
        )
        nodes = self.node_norm(nodes + self.relations(linked))
        scene = self.scene(nodes.unsqueeze(0))
        if not frame.choices:
            return self.ids.weight.new_empty((0,))
        action = self.action_types(
            torch.tensor([self.index("actions", c["kind"]) for c in frame.choices], device=device)
        )
        source = torch.stack(
            [scene[0, s] if s >= 0 else self.absent_roles[0] for s, _ in frame.roles]
        )
        target = torch.stack(
            [scene[0, t] if t >= 0 else self.absent_roles[1] for _, t in frame.roles]
        )
        action = action + self.source_role(source) + self.target_role(target)
        read, _ = self.cross(action.unsqueeze(0), scene, scene, need_weights=False)
        current = scene.mean(1).expand(len(action), -1)
        scores = self.head(torch.cat((action + read[0], current), -1)).flatten()
        if scores.shape != (len(frame.choices),) or not bool(torch.isfinite(scores).all()):
            raise ValueError("non-finite or incomplete model scores")
        return cast(Tensor, scores)
