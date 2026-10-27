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

from .execution import MStdExecution
from .input import V2_INPUT_SPEC, Frame, Role, normalized, signed_log

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


def _mean_terms(values: Tensor, rows: list[list[int]], empty: Tensor) -> Tensor:
    """Reduce complete ragged rows at their original, unpadded mean length.

    Equal-length buckets avoid N*max-degree storage for a star of references,
    without changing a row's reduction length or replacing mean by sum/divide.
    Host indices are packed before transfer; index_select uses Torch's existing
    deterministic backward rather than an application scatter/atomic reduction.
    """
    buckets: dict[int, list[int]] = {}
    for row, terms in enumerate(rows):
        width = len(terms)
        buckets.setdefault(width, []).append(row)
    parts, order = [], []
    for width, owners in buckets.items():
        order.extend(owners)
        if not width:
            parts.append(empty.expand(len(owners), -1))
            continue
        indices = torch.tensor(
            [index for row in owners for index in rows[row]],
            dtype=torch.long, device=values.device,
        )
        packed = values.index_select(0, indices).reshape(len(owners), width, -1)
        parts.append(packed.mean(1))
    inverse = [0] * len(order)
    for index, row in enumerate(order):
        inverse[row] = index
    return torch.cat(parts).index_select(0, torch.tensor(inverse, device=values.device))


def _role_vectors(scene: Tensor, indices: list[int], absent: Tensor) -> Tensor:
    """Gather complete roles without attaching an unused absence parameter."""
    if any(index < 0 for index in indices):
        values = torch.cat((scene, absent[None]), 0)
        indices = [index if index >= 0 else len(scene) for index in indices]
    else:
        values = scene
    return values.index_select(0, torch.tensor(indices, dtype=torch.long, device=scene.device))


def _view_role_vectors(scene: Tensor, roles: list[Role], absent: Tensor) -> Tensor:
    """Input-v2 role binding: every public view has equal status, no new weights."""
    rows = [list(role) if isinstance(role, tuple) else [role] if role >= 0 else []
            for role in roles]
    return _mean_terms(scene, rows, absent)


class MStd(nn.Module):
    def __init__(self, vocabulary: dict[str, Any], execution: MStdExecution | None = None):
        super().__init__()
        self.execution = execution or MStdExecution()
        self.vocabulary = vocabulary
        if vocabulary.get("input_spec") == V2_INPUT_SPEC:
            from .vocabulary import validate_vocabulary

            validate_vocabulary(vocabulary)
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
            ids = self.tokenizer.encode(normalized_text).ids or [
                self.tokenizer.token_to_id("<EMPTY>")
                if self.vocabulary.get("input_spec") == V2_INPUT_SPEC else 1
            ]
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
        # Pack on the host before transfer. Row-by-row device assignment launches
        # several copies/kernels per text, even though these inputs need no grad.
        ids = torch.tensor(
            [row + [0] * (width - len(row)) for row, _ in tokens],
            dtype=torch.long,
            device=device,
        )
        nums = torch.tensor(
            [numbers + [0.0] * (width - len(numbers)) for _, numbers in tokens],
            dtype=torch.float32,
            device=device,
        ).unsqueeze(-1)
        lengths = torch.tensor([len(row) for row, _ in tokens], device=device)
        mask = torch.arange(width, device=device).unsqueeze(0) >= lengths.unsqueeze(1)
        value = self.text_ids(ids) + self.text_numbers(nums)
        value = value + positions(torch.arange(width, device=device), 128)
        encoded = self.text_blocks(value, src_key_padding_mask=mask)
        encoded = (encoded * (~mask).unsqueeze(-1)).sum(1) / (~mask).sum(1).unsqueeze(-1)
        return cast(Tensor, self.text_projection(encoded))

    def node_vectors(self, frame: Frame, vectors: Tensor) -> Tensor:
        device = self.ids.weight.device
        entities = frame.entities
        texts = sorted({text for node in entities for _, text in node.text})
        text_rows = {text: i for i, text in enumerate(texts)}
        id_pairs = [
            (row, key, value) for row, e in enumerate(entities) for key, value in e.identities
        ]
        identities = torch.tensor(
            [self.index("ids", value) for _, _, value in id_pairs], dtype=torch.long, device=device
        )
        if self.training:
            identities = identities.masked_fill(torch.rand(len(identities), device=device) < 0.1, 0)
        id_rows: list[list[int]] = [[] for _ in entities]
        for i, (row, _, _) in enumerate(id_pairs):
            id_rows[row].append(i)
        id_vectors = self.ids(identities)
        if id_pairs:
            id_vectors = id_vectors + self.fields(torch.tensor(
                [self.index("fields", key) for _, key, _ in id_pairs], device=device,
            ))
        node_ids = _mean_terms(id_vectors, id_rows, self.ids.weight[0])
        nodes = node_ids + self.kinds(
            torch.tensor([self.index("kinds", e.kind) for e in entities], device=device)
        )
        nodes = nodes + positions(torch.tensor([e.position for e in entities], device=device), 192)
        present: list[tuple[int, int, float]] = []
        missing: list[tuple[int, int]] = []
        number_owners: list[list[int]] = [[] for _ in entities]
        for row, entity in enumerate(entities):
            for key, number in entity.numeric:
                if number is None:
                    number_owners[row].append(-len(missing) - 1)
                    missing.append((row, self.index("fields", key)))
                else:
                    number_owners[row].append(len(present))
                    present.append((row, self.index("fields", key), signed_log(number)))
        # Do not evaluate absent branches: a zero gradient is not None to AdamW.
        number_parts = []
        if present:
            fields = torch.tensor([key for _, key, _ in present], device=device)
            numbers = torch.tensor([value for _, _, value in present],
                                   dtype=self.number_slope.weight.dtype, device=device)
            number_parts.append(self.number_slope(fields) * numbers[:, None]
                                + self.number_bias(fields))
        if missing:
            number_parts.append(self.number_missing(torch.tensor(
                [key for _, key in missing], device=device,
            )))
        number_owners = [[i if i >= 0 else len(present) - i - 1 for i in row]
                         for row in number_owners]
        number_values = torch.cat(number_parts) if number_parts else nodes.new_zeros((0, 192))
        number_features = _mean_terms(number_values, number_owners, self.no_numbers)
        if self.execution.implementation == "experimental-packed-v1":
            # Same complete terms and per-row reduction lengths; batched gathers
            # change FP32 gradient accumulation. This is a separate trajectory,
            # not the rejected prototype relabeled as an exact-resume success.
            packed_fields: list[int] = []
            text_indices: list[int] = []
            rows: list[list[int]] = []
            for entity in entities:
                row_indices = []
                for key, text in entity.text:
                    row_indices.append(len(packed_fields))
                    packed_fields.append(self.index("fields", key))
                    text_indices.append(text_rows[text])
                rows.append(row_indices)
            if packed_fields:
                packed_text_terms = vectors.index_select(
                    0, torch.tensor(text_indices, device=device))
                packed_text_terms = packed_text_terms + self.fields(
                    torch.tensor(packed_fields, device=device))
            else:
                packed_text_terms = vectors.new_zeros((0, 192))
            packed_features = (_mean_terms(packed_text_terms, rows, nodes.new_zeros(192))
                               + number_features)
            nodes = nodes + packed_features
            linked = _mean_terms(nodes, [entity.refs for entity in entities], nodes.new_zeros(192))
            return cast(Tensor, self.node_norm(nodes + self.relations(linked)))
        features = []
        # Keep the original text and reference autograd accumulation paths:
        # reordering tiny text gradients can exceed the next-Adam-state tolerance.
        for row, entity in enumerate(entities):
            text_terms = [
                vectors[text_rows[text]] + self.fields.weight[self.index("fields", key)]
                for key, text in entity.text
            ]
            if self.execution.implementation == "row-fused-v1":
                features.append(torch.stack(text_terms).mean(0) if text_terms
                                else nodes.new_zeros(192))
            else:
                features.append((torch.stack(text_terms).mean(0) if text_terms else 0)
                                + number_features[row])
        if self.execution.implementation == "row-fused-v1":
            # Numeric rows occur exactly once: adding the complete matrix avoids
            # one SelectBackward/full-zero gradient per entity. Keep text/ref
            # operations and their accumulation paths unchanged.
            features_tensor = torch.stack(features) + number_features
        else:
            features_tensor = torch.stack(features)
        nodes = nodes + features_tensor
        linked = torch.stack(
            [nodes[e.refs].mean(0) if e.refs else torch.zeros_like(nodes[0]) for e in entities]
        )
        return cast(Tensor, self.node_norm(nodes + self.relations(linked)))

    def forward(self, frame: Frame) -> Tensor:
        return self.forward_batch([frame])[0]

    def forward_batch(self, frames: list[Frame], *, check_finite: bool = True) -> list[Tensor]:
        """Independent scenes with full catalogs; padding is never a public input.

        Texts are deduplicated within each frame exactly as in single-frame scoring,
        never across frames (each occurrence owns its training dropout).
        """
        if not frames:
            return []
        device = self.ids.weight.device
        texts = [
            sorted({text for node in frame.entities for _, text in node.text}) for frame in frames
        ]
        all_texts = [text for row in texts for text in row]
        vectors = self.text_vectors(all_texts) if all_texts else self.ids.weight.new_zeros((0, 192))
        nodes = []
        offset = 0
        for frame, row in zip(frames, texts, strict=True):
            nodes.append(self.node_vectors(frame, vectors[offset : offset + len(row)]))
            offset += len(row)
        lengths = torch.tensor([len(row) for row in nodes], device=device)
        packed = nn.utils.rnn.pad_sequence(nodes, batch_first=True)
        mask = torch.arange(packed.shape[1], device=device)[None, :] >= lengths[:, None]
        scene = self.scene(packed, src_key_padding_mask=mask)
        actions = []
        for batch, frame in enumerate(frames):
            action = self.action_types(
                torch.tensor(
                    [self.index("actions", c["kind"]) for c in frame.choices],
                    dtype=torch.long,
                    device=device,
                )
            )
            if frame.choices:
                source_roles = [s for s, _ in frame.roles]
                target_roles = [t for _, t in frame.roles]
                if self.vocabulary.get("input_spec") == V2_INPUT_SPEC:
                    source = _view_role_vectors(scene[batch], source_roles, self.absent_roles[0])
                    target = _view_role_vectors(scene[batch], target_roles, self.absent_roles[1])
                elif any(not isinstance(role, int) for role in source_roles + target_roles):
                    raise ValueError("single-view InputSpec received multi-view roles")
                elif self.execution.implementation == "experimental-packed-v1":
                    source = _role_vectors(scene[batch], cast(list[int], source_roles),
                                           self.absent_roles[0])
                    target = _role_vectors(scene[batch], cast(list[int], target_roles),
                                           self.absent_roles[1])
                else:
                    source = torch.stack(
                        [scene[batch, s] if s >= 0 else self.absent_roles[0]
                         for s in cast(list[int], source_roles)]
                    )
                    target = torch.stack(
                        [scene[batch, t] if t >= 0 else self.absent_roles[1]
                         for t in cast(list[int], target_roles)]
                    )
                action = action + self.source_role(source) + self.target_role(target)
            actions.append(action)
        if not any(frame.choices for frame in frames):
            return [self.ids.weight.new_empty((0,)) for _ in frames]
        action = nn.utils.rnn.pad_sequence(actions, batch_first=True)
        read, _ = self.cross(action, scene, scene, key_padding_mask=mask, need_weights=False)
        current = (scene * (~mask).unsqueeze(-1)).sum(1) / lengths.unsqueeze(-1)
        scores = self.head(
            torch.cat((action + read, current[:, None, :].expand(-1, action.shape[1], -1)), -1)
        ).squeeze(-1)
        outputs = [scores[i, : len(frame.choices)] for i, frame in enumerate(frames)]
        if any(
            score.shape != (len(frame.choices),)
            for score, frame in zip(outputs, frames, strict=True)
        ) or (check_finite and not bool(torch.isfinite(torch.cat(outputs)).all())):
            raise ValueError("non-finite or incomplete model scores")
        return outputs
