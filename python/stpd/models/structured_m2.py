"""Fixed S-M2-0 graph: public typed tree, one sparse relation layer, K1/d96 memory.

Opaque reference rows are gather indices only. Neither current C nor N enters
the observation encoder or writer. Entity-local typed field pooling preserves
their correspondence before one public-relation message aggregation.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import cast

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from spireagent.json_boundary import BoundaryError, digest

from ..fullrun.structured_inputs import (
    MAX_CANDIDATES,
    MAX_EDGES,
    MAX_FIELD_BYTES,
    MAX_NODES,
    MAX_TEXT_BYTES,
    NODE_TYPES,
    RELATIONS,
    StructuredFrame,
)
from ..native_graph_spec import NativeGraphControl, optional_control

GRAPH_ID = "stpd.structured-observation-only.s-m2-0.v1"
WIDTH = 96
SLOTS = 1
TEXT_BATCH_TOKENS = 32768


class ByteFieldEncoder(nn.Module):
    """Shared local UTF-8 CNN with real-token masks, bounded bucketed batches."""

    def __init__(self) -> None:
        super().__init__()
        self.embedding = nn.Embedding(258, 32)
        self.conv = nn.Conv1d(32, 64, kernel_size=3, padding=1)
        self.projection = nn.Linear(64, WIDTH)

    def forward(self, texts: tuple[str, ...]) -> Tensor:
        if not isinstance(texts, tuple) or not texts:
            raise BoundaryError("structured_model", "text_tuple_required")
        groups: dict[int, list[tuple[int, bytes]]] = defaultdict(list)
        for index, value in enumerate(texts):
            if not isinstance(value, str):
                raise BoundaryError("structured_model", "text_required")
            raw = value.encode("utf-8")
            if len(raw) > MAX_FIELD_BYTES:
                raise BoundaryError("structured_model", "field_text_limit")
            bucket = 1 << (len(raw) + 1).bit_length()
            groups[bucket].append((index, raw))
        result: list[Tensor | None] = [None] * len(texts)
        device = self.embedding.weight.device
        for group in groups.values():
            longest = max(len(raw) + 2 for _, raw in group)
            batch_size = max(1, TEXT_BATCH_TOKENS // longest)
            for start in range(0, len(group), batch_size):
                chunk = group[start : start + batch_size]
                ids = torch.zeros((len(chunk), longest), dtype=torch.long, device=device)
                mask = torch.zeros_like(ids, dtype=torch.bool)
                for row, (_, raw) in enumerate(chunk):
                    values = (256, *raw, 257)
                    ids[row, : len(values)] = torch.tensor(values, dtype=torch.long, device=device)
                    mask[row, : len(values)] = True
                encoded = self.embedding(ids) * mask.unsqueeze(-1)
                convolved = F.gelu(self.conv(encoded.transpose(1, 2))).transpose(1, 2)
                pooled = (convolved * mask.unsqueeze(-1)).sum(1) / mask.sum(1).unsqueeze(-1)
                vectors = self.projection(pooled)
                for row, (index, _) in enumerate(chunk):
                    result[index] = vectors[row]
        return torch.stack([cast(Tensor, item) for item in result])


def validate_structured_frame(frame: StructuredFrame) -> None:
    if (
        not isinstance(frame, StructuredFrame)
        or not isinstance(frame.nodes, tuple)
        or not 0 < len(frame.nodes) <= MAX_NODES
        or not isinstance(frame.edges, tuple)
        or len(frame.edges) > MAX_EDGES
        or not isinstance(frame.candidates, tuple)
        or not 0 <= len(frame.candidates) <= MAX_CANDIDATES
    ):
        raise BoundaryError("structured_model", "frame_shape")
    digest(frame.state_digest, "structured_model.state_digest")
    digest(frame.candidate_digest, "structured_model.candidate_digest")
    budget = 0
    for node in frame.nodes:
        if (
            node.kind not in NODE_TYPES
            or len(node.numeric) != 3
            or any(
                type(value) not in {int, float} or not math.isfinite(value)
                for value in node.numeric
            )
        ):
            raise BoundaryError("structured_model", "node_type_or_numeric")
        for value in (node.field, node.text):
            if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_FIELD_BYTES:
                raise BoundaryError("structured_model", "field_text_limit")
            budget += len(value.encode("utf-8"))
    for source, target, relation in frame.edges:
        if (
            type(source) is not int
            or type(target) is not int
            or type(relation) is not int
            or not 0 <= source < len(frame.nodes)
            or not 0 <= target < len(frame.nodes)
            or not 0 <= relation < len(RELATIONS)
        ):
            raise BoundaryError("structured_model", "edge_binding")
    for owner, member in frame.entity_members:
        if (
            type(owner) is not int
            or type(member) is not int
            or not 0 <= owner < len(frame.nodes)
            or not 0 <= member < len(frame.nodes)
        ):
            raise BoundaryError("structured_model", "member_binding")
    if len(set(frame.action_ids)) != len(frame.action_ids):
        raise BoundaryError("structured_model", "duplicate_candidates")
    for item in frame.candidates:
        if not isinstance(item.action_id, str) or not item.action_id:
            raise BoundaryError("structured_model", "action_binding")
        for value in (item.kind, item.verb, item.label, *(role for role, _ in item.roles)):
            if (
                not isinstance(value, str)
                or not value
                or len(value.encode("utf-8")) > MAX_FIELD_BYTES
            ):
                raise BoundaryError("structured_model", "candidate_text_limit")
            budget += len(value.encode("utf-8"))
        if len({role for role, _ in item.roles}) != len(item.roles):
            raise BoundaryError("structured_model", "duplicate_candidate_roles")
        if any(type(row) is not int or not 0 <= row < len(frame.nodes) for _, row in item.roles):
            raise BoundaryError("structured_model", "candidate_gather_binding")
    if budget > MAX_TEXT_BYTES:
        raise BoundaryError("structured_model", "frame_text_budget")


class StructuredM2(nn.Module):
    """No controller, receipt, history-action, strategy or model download behavior."""

    def __init__(self, *, seed: int = 0, model_control: NativeGraphControl | None = None) -> None:
        super().__init__()
        if type(seed) is not int or seed < 0:
            raise BoundaryError("structured_model", "seed_invalid")
        self.seed = seed
        self.model_control = optional_control(model_control)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            self.text = ByteFieldEncoder()
            self.types = nn.Embedding(len(NODE_TYPES), WIDTH)
            self.numeric = nn.Linear(3, WIDTH, bias=False)
            self.node_norm = nn.LayerNorm(WIDTH)
            self.entity_fields = nn.Linear(WIDTH, WIDTH)
            self.relations = nn.ModuleList(nn.Linear(WIDTH, WIDTH, bias=False) for _ in RELATIONS)
            self.relation_norm = nn.LayerNorm(WIDTH)
            self.write_query = nn.Parameter(torch.randn(SLOTS, WIDTH) * 0.02)
            self.old_query = nn.Linear(WIDTH, WIDTH)
            self.write_key = nn.Linear(WIDTH, WIDTH)
            self.write_value = nn.Linear(WIDTH, WIDTH)
            self.write_norm = nn.LayerNorm(WIDTH)
            self.gate = nn.Linear(2 * WIDTH, WIDTH)
            self.role_projection = nn.Linear(2 * WIDTH, WIDTH)
            self.empty_role = nn.Parameter(torch.randn(WIDTH) * 0.02)
            self.candidate_norm = nn.LayerNorm(WIDTH)
            self.read_query = nn.Linear(WIDTH, WIDTH)
            self.read_key = nn.Linear(WIDTH, WIDTH)
            self.read_value = nn.Linear(WIDTH, WIDTH)
            self.head = nn.Sequential(nn.Linear(2 * WIDTH, WIDTH), nn.GELU(), nn.Linear(WIDTH, 1))

        if self.slots != SLOTS:
            # Initialize common E/W/N parameters exactly as the historical K1 model.
            # Additional learned queries use a separate deterministic RNG stream.
            with torch.random.fork_rng(devices=[]):
                torch.manual_seed(seed + 1)
                extra = torch.randn(self.slots - SLOTS, WIDTH) * 0.02
            self.write_query = nn.Parameter(torch.cat((self.write_query.detach(), extra), dim=0))

    @property
    def slots(self) -> int:
        return SLOTS if self.model_control is None else self.model_control.graph.slots

    def initial_memory(self) -> Tensor:
        return self.write_query.new_zeros((self.slots, WIDTH))

    def validate_parameters(self) -> None:
        if any(
            parameter.device.type != "cpu"
            or parameter.dtype != torch.float32
            or not bool(torch.isfinite(parameter).all())
            for parameter in self.parameters()
        ):
            raise BoundaryError("structured_model", "cpu_fp32_finite_parameters_required")

    def _memory(self, memory: Tensor) -> None:
        if (
            not isinstance(memory, Tensor)
            or memory.shape != (self.slots, WIDTH)
            or memory.device != self.write_query.device
            or memory.dtype != torch.float32
            or not bool(torch.isfinite(memory).all())
        ):
            raise BoundaryError("structured_model", "memory_shape_or_value")

    def encode(self, frame: StructuredFrame) -> Tensor:
        validate_structured_frame(frame)
        self.validate_parameters()
        device = self.write_query.device
        fields = self.text(tuple(node.field for node in frame.nodes))
        values = self.text(tuple(node.text for node in frame.nodes))
        types = torch.tensor(
            [NODE_TYPES.index(node.kind) for node in frame.nodes], dtype=torch.long, device=device
        )
        numeric = torch.tensor(
            [node.numeric for node in frame.nodes], dtype=torch.float32, device=device
        )
        base = self.node_norm(fields + values + self.types(types) + self.numeric(numeric))
        # Local object fields remain attached to their instance, not a global bag.
        local = torch.zeros_like(base)
        counts = base.new_zeros((len(frame.nodes), 1))
        if frame.entity_members:
            owners = torch.tensor(
                [owner for owner, _ in frame.entity_members], dtype=torch.long, device=device
            )
            members = torch.tensor(
                [member for _, member in frame.entity_members], dtype=torch.long, device=device
            )
            local.index_add_(0, owners, base[members])
            counts.index_add_(0, owners, base.new_ones((len(owners), 1)))
        base = base + self.entity_fields(local / counts.clamp_min(1)) * (counts > 0)
        messages = torch.zeros_like(base)
        degrees = base.new_zeros((len(frame.nodes), 1))
        for relation, transform in enumerate(self.relations):
            pairs = [(source, target) for source, target, kind in frame.edges if kind == relation]
            if pairs:
                sources = torch.tensor(
                    [source for source, _ in pairs], dtype=torch.long, device=device
                )
                targets = torch.tensor(
                    [target for _, target in pairs], dtype=torch.long, device=device
                )
                messages.index_add_(0, targets, transform(base[sources]))
                degrees.index_add_(0, targets, base.new_ones((len(targets), 1)))
        encoded: Tensor = self.relation_norm(base + messages / degrees.clamp_min(1))
        if not bool(torch.isfinite(encoded).all()):
            raise BoundaryError("structured_model", "nonfinite_encoding")
        return encoded

    def _entities(self, entities: Tensor) -> None:
        if (
            not isinstance(entities, Tensor)
            or entities.ndim != 2
            or entities.shape[1] != WIDTH
            or not 0 < entities.shape[0] <= MAX_NODES
            or entities.device != self.write_query.device
            or entities.dtype != torch.float32
            or not bool(torch.isfinite(entities).all())
        ):
            raise BoundaryError("structured_model", "entity_shape_or_value")

    def advance(self, entities: Tensor, memory: Tensor) -> Tensor:
        self._entities(entities)
        self._memory(memory)
        if self.model_control is not None and self.model_control.reset.mode == (
            "reset_before_each_actual_advance"
        ):
            memory = self.initial_memory()
        source = torch.cat((memory, entities), dim=0)
        query = self.write_query + self.old_query(memory)
        attention = torch.softmax(query @ self.write_key(source).T / math.sqrt(WIDTH), dim=-1)
        proposal = self.write_norm(attention @ self.write_value(source))
        gate = torch.sigmoid(self.gate(torch.cat((memory, proposal), dim=-1)))
        updated: Tensor = gate * memory + (1 - gate) * proposal
        self._memory(updated)
        return updated

    def score(self, frame: StructuredFrame, entities: Tensor, memory: Tensor) -> Tensor:
        validate_structured_frame(frame)
        self._entities(entities)
        self._memory(memory)
        if entities.shape[0] != len(frame.nodes):
            raise BoundaryError("structured_model", "current_entity_binding_required")
        candidates = frame.candidates
        if not candidates:
            raise BoundaryError("structured_model", "decision_catalog_required")
        base = (
            self.text(tuple(item.kind for item in candidates))
            + self.text(tuple(item.verb for item in candidates))
            + self.text(tuple(item.label for item in candidates))
        )
        roles = [
            (index, role, row) for index, item in enumerate(candidates) for role, row in item.roles
        ]
        gathered = torch.zeros_like(base)
        counts = base.new_zeros((len(candidates), 1))
        if roles:
            role_vectors = self.text(tuple(role for _, role, _ in roles))
            target_rows = torch.tensor(
                [row for _, _, row in roles], dtype=torch.long, device=base.device
            )
            candidate_rows = torch.tensor(
                [index for index, _, _ in roles], dtype=torch.long, device=base.device
            )
            values = F.gelu(
                self.role_projection(torch.cat((role_vectors, entities[target_rows]), -1))
            )
            gathered.index_add_(0, candidate_rows, values)
            counts.index_add_(0, candidate_rows, base.new_ones((len(roles), 1)))
        action = self.candidate_norm(
            base + gathered / counts.clamp_min(1) + (counts == 0) * self.empty_role
        )
        attention = torch.softmax(
            self.read_query(action) @ self.read_key(memory).T / math.sqrt(WIDTH), dim=-1
        )
        read = attention @ self.read_value(memory)
        scores = self.head(torch.cat((action, read), dim=-1)).flatten()
        if scores.shape != (len(candidates),) or not bool(torch.isfinite(scores).all()):
            raise BoundaryError("structured_model", "nonfinite_or_incomplete_scores")
        return cast(Tensor, scores)
