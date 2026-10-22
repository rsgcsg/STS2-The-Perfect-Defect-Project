"""Pre-batching node implementation retained only as the numerical test oracle."""

from __future__ import annotations

from typing import cast

import torch
from torch import Tensor

from stpd.p1.input import Frame, signed_log
from stpd.p1.model import positions


def original_node_vectors(self, frame: Frame, vectors: Tensor) -> Tensor:
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
    return cast(Tensor, self.node_norm(nodes + self.relations(linked)))
