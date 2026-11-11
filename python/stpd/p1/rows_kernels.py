"""V3 T6: explicit training-forward levers for rows-v2 (`prepared_kernels`, default off).

Same parameters and the same function as `rows_forward.rows_block[_packed]`; only
how the attention readouts are computed changes (BF16 tolerance, not bitwise).

``heads``  Each value head attends from one learned query. Its key projection
           folds into that query, q.(W_k e + b_k) = e.(W_k^T q) + q.b_k, and the
           constant q.b_k cancels in the softmax over keys; its value projection
           moves after the weighted sum, sum_t w_t (W_v e_t + b_v) =
           W_v (sum_t w_t e_t) + b_v because sum_t w_t = 1. So neither head
           projects every scene token to keys/values. The folded queries of both
           heads (2 x 4) attend in one call straight to the encoded tokens.
``nodes``  The node builder (`MStd.tensor_nodes`) as one function of owner maps:
           every partition reduction (ids, numbers, text: each term has one
           owner) is a scatter-sum of its terms straight onto their owner node,
           scaled by 1/terms, with no width buckets, splits, concatenations or
           inverse gathers; refs (a node may be referenced many times) gather the
           referenced nodes and scatter onto owners the same way. Under
           compile it is one dynamic-shape graph (the original runs eager).
           The scene token gather (a permutation: each real row owns its own
           contiguous node range and all nodes are scene tokens) takes a gather
           as its backward instead of an atomic index_add. A broken partition or
           permutation fails loudly (device assert), never silently.
``cross``  The action cross-attention folds the same way per (action, head):
           catalog x heads folded queries per row attend to the encoded tokens,
           so no key/value projection of the scene. Catalogs longer than
           FOLD_CATALOG_LIMIT keep the original projected attention (shape rule).
``embed``  (with ``nodes``) Every row gather of the node builder (the ids,
           fields, kinds and number slope/bias/missing tables, the text vectors
           and the referenced nodes) takes its backward from the eager
           sort-and-segment-sum kernel (`aten.embedding_dense_backward`, an
           opaque custom op) instead of inductor's decomposition, an atomic
           scatter-add whose same-row atomics serialize on small vocabularies
           (fields: hundreds of thousands of lookups onto a few rows). Same sum,
           different order (FP32).

Packed (CUDA): the folded queries are one-head, full-width (embed) sequences for
flash varlen: query sequence i (heads, or catalog x heads, positions) attends to
exactly the real tokens of row i; no padding anywhere. Masked (CPU/FP32
reference): the same algebra on the padded bucket with dense matmuls.

A row must hold at least one scene token (the original mean divides by it too).
"""

from __future__ import annotations

import math
from typing import Any, cast

import torch
from torch import Tensor
from torch.nn import functional as F

from .model import positions

LEVERS = ("cross", "embed", "heads", "nodes")
# Reductions whose terms each have exactly one owner (`tensor_input.frame_tensors`).
PARTITION_REDUCTIONS = ("ids", "numbers", "text")
# Folding costs ~4*C*H*E per key and saves 4*E*E of key/value projection:
# fold while C*H*(E - d) <= E*E, i.e. C <= 64 for E=192, H=4.
FOLD_CATALOG_LIMIT = 64


def parse_kernels(value: str) -> tuple[str, ...]:
    """'' (off) or a '+'-joined, sorted, duplicate-free subset of LEVERS."""
    if type(value) is not str:
        raise ValueError("invalid_prepared_kernels")
    if not value:
        return ()
    parts = tuple(value.split("+"))
    if (len(set(parts)) != len(parts) or not set(parts) <= set(LEVERS)
            or list(parts) != sorted(parts) or ("embed" in parts and "nodes" not in parts)):
        raise ValueError("invalid_prepared_kernels")
    return parts


def _compute_dtype(reference: Tensor) -> torch.dtype:
    device = reference.device.type
    if torch.is_autocast_enabled(device):
        return torch.get_autocast_dtype(device)
    return reference.dtype


def _parts(module: torch.nn.MultiheadAttention) -> tuple[int, int, int]:
    embed, heads = module.embed_dim, module.num_heads
    return embed, heads, embed // heads


def _fold_keys(module: torch.nn.MultiheadAttention, query: Tensor) -> Tensor:
    """[..., heads, width] projected queries -> [..., heads, embed] folded into the
    key projection, softmax scale included (the key bias cancels)."""
    embed, heads, width = _parts(module)
    keys = module.in_proj_weight[embed:2 * embed].view(heads, width, embed)
    return torch.einsum("...hd,hde->...he", query, keys.to(query.dtype)) * (1.0 / math.sqrt(width))


def _values(module: torch.nn.MultiheadAttention, pooled: Tensor) -> Tensor:
    """[..., heads, embed] attention-weighted scene sums -> attention output
    (value projection per head, value bias, out projection)."""
    embed, heads, width = _parts(module)
    weight, bias = module.in_proj_weight, module.in_proj_bias
    values = torch.einsum("...he,hde->...hd", pooled,
                          weight[2 * embed:].view(heads, width, embed).to(pooled.dtype))
    values = values + bias[2 * embed:].view(heads, width)
    return F.linear(values.reshape(*pooled.shape[:-2], embed), module.out_proj.weight,
                    module.out_proj.bias)


def _head_queries(modules: tuple[Any, ...]) -> Tensor:
    queries = []
    for module in modules:
        attention = module.attention
        embed, heads, width = _parts(attention)
        query = F.linear(module.query.reshape(1, embed), attention.in_proj_weight[:embed],
                         attention.in_proj_bias[:embed])
        queries.append(_fold_keys(attention, query.view(heads, width)))
    return torch.cat(queries)  # [modules * heads, embed]


def _head_outputs(modules: tuple[Any, ...], pooled: Tensor, current: Tensor) -> list[Tensor]:
    outputs, start = [], 0
    for module in modules:
        heads = module.attention.num_heads
        read = _values(module.attention, pooled[:, start:start + heads])
        start += heads
        outputs.append(cast(Tensor, module.mlp(torch.cat((read, current), dim=1))))
    return outputs


def _cross_queries(module: torch.nn.MultiheadAttention, action: Tensor) -> Tensor:
    rows, catalog, _ = action.shape
    embed, heads, width = _parts(module)
    query = F.linear(action, module.in_proj_weight[:embed], module.in_proj_bias[:embed])
    return _fold_keys(module, query.view(rows, catalog, heads, width))  # [rows, C, H, embed]


# Masked bucket (CPU / FP32 reference path).

def folded_heads(modules: tuple[Any, ...], padded: Tensor, keep: Tensor,
                 current: Tensor) -> list[Tensor]:
    """`AttentionValueResidual` readouts of every module on a padded bucket."""
    folded = _head_queries(modules).to(padded.dtype)
    scores = torch.matmul(padded, folded.t()).float()  # [rows, longest, modules * heads]
    weights = scores.masked_fill(~keep[:, :, None], -torch.inf).softmax(1)
    pooled = torch.matmul(weights.transpose(1, 2).to(padded.dtype), padded)
    return _head_outputs(modules, pooled, current)


def padded_cross(module: torch.nn.MultiheadAttention, action: Tensor, padded: Tensor,
                 keep: Tensor) -> Tensor:
    """nn.MultiheadAttention(action -> padded scene, eval, need_weights=False)."""
    rows, catalog, _ = action.shape
    embed, heads, width = _parts(module)
    if catalog <= FOLD_CATALOG_LIMIT:
        folded = _cross_queries(module, action).reshape(rows, catalog * heads, embed)
        scores = torch.matmul(folded.to(padded.dtype), padded.transpose(1, 2)).float()
        weights = scores.masked_fill(~keep[:, None, :], -torch.inf).softmax(-1)
        pooled = torch.matmul(weights.to(padded.dtype), padded).view(rows, catalog, heads, embed)
        return _values(module, pooled)
    weight, bias = module.in_proj_weight, module.in_proj_bias
    query = F.linear(action, weight[:embed], bias[:embed]).view(rows, catalog, heads, width)
    longest = padded.shape[1]
    kv = F.linear(padded, weight[embed:], bias[embed:]).view(rows, longest, 2, heads, width)
    key, value = kv.unbind(2)
    scores = torch.einsum("rchd,rlhd->rhcl", query.to(key.dtype), key).float() / math.sqrt(width)
    weights = scores.masked_fill(~keep[:, None, None, :], -torch.inf).softmax(-1)
    values = torch.einsum("rhcl,rlhd->rchd", weights.to(value.dtype), value)
    return F.linear(values.reshape(rows, catalog, embed), module.out_proj.weight,
                    module.out_proj.bias)


# Packed real tokens (CUDA flash varlen path).

def _attend_tokens(folded: Tensor, encoded: Tensor, offsets: Tensor, longest: int) -> Tensor:
    """[rows, Q, embed] folded queries -> [rows, Q, embed] softmax-weighted sums of
    each row's own encoded tokens (one head of full embed width, scale folded)."""
    from .rows_forward import flash_varlen

    rows, count, embed = folded.shape
    tokens = encoded.to(_compute_dtype(encoded)).unsqueeze(1)
    cu_q = torch.arange(rows + 1, device=encoded.device, dtype=torch.int32) * count
    out = flash_varlen(folded.to(tokens.dtype).reshape(rows * count, 1, embed), tokens, tokens,
                       cu_q, offsets, count, longest, scale=1.0)
    return out.view(rows, count, embed)


def packed_heads(modules: tuple[Any, ...], encoded: Tensor, offsets: Tensor, longest: int,
                 current: Tensor) -> list[Tensor]:
    """`AttentionValueResidual` readouts of every module on packed real tokens."""
    rows = current.shape[0]
    folded = _head_queries(modules)
    pooled = _attend_tokens(folded.unsqueeze(0).expand(rows, -1, -1), encoded, offsets, longest)
    return _head_outputs(modules, pooled, current)


def packed_cross(module: torch.nn.MultiheadAttention, action: Tensor, encoded: Tensor,
                 offsets: Tensor, longest: int) -> Tensor:
    """Folded action cross-attention on packed real tokens (catalog <= limit)."""
    rows, catalog, embed = action.shape
    heads = module.num_heads
    folded = _cross_queries(module, action).reshape(rows, catalog * heads, embed)
    pooled = _attend_tokens(folded, encoded, offsets, longest)
    return _values(module, pooled.view(rows, catalog, heads, embed))


# Permutation gathers (eager node region).

def permutation_inverse(perm: Tensor) -> Tensor:
    """inverse[perm[i]] = i; asserts (device side, no sync) that perm is a bijection."""
    inverse = torch.full_like(perm, -1)
    inverse[perm] = torch.arange(perm.numel(), device=perm.device, dtype=perm.dtype)
    torch._assert_async((inverse >= 0).all())
    return inverse


class _Permute(torch.autograd.Function):
    @staticmethod
    def forward(ctx: Any, values: Tensor, perm: Tensor, inverse: Tensor) -> Tensor:
        ctx.save_for_backward(inverse)
        return values.index_select(0, perm)

    @staticmethod
    def backward(ctx: Any, grad: Tensor) -> tuple[Tensor, None, None]:
        (inverse,) = ctx.saved_tensors
        return grad.index_select(0, inverse), None, None


def permute_rows(values: Tensor, perm: Tensor, what: str) -> Tensor:
    """values.index_select(0, perm) for a permutation perm of values' rows; the
    backward is the inverse gather (no atomics)."""
    if perm.numel() != values.shape[0]:
        raise ValueError("nodes_lever_requires_permutation:" + what)
    return cast(Tensor, _Permute.apply(values, perm, permutation_inverse(perm)))


def _owners(flat: tuple[Tensor, tuple[tuple[int, int], ...], Tensor], rows: int,
            terms: int, partition: bool) -> tuple[Tensor, Tensor, Tensor]:
    """(owner per term, 1/terms per row (0 when empty), empty-row mask) of a
    reduction. Partition owners are in original term order; refs owners follow
    the layout's bucket term order (its `indices` say which node each term reads)."""
    indices, buckets, inverse = flat
    device = inverse.device
    order = torch.empty_like(inverse)
    order[inverse] = torch.arange(rows, device=device, dtype=inverse.dtype)
    # Built on the device: a pageable host->device copy would sync the stream.
    per_position = torch.cat([torch.full((count,), width, device=device, dtype=torch.long)
                              for width, count in buckets]) if buckets else inverse.new_zeros(0)
    width = torch.empty(rows, device=device, dtype=torch.long)
    width[order] = per_position
    listed = sum(w * c for w, c in buckets)
    if listed != indices.numel() or (partition and listed != terms):
        raise ValueError("nodes_lever_requires_complete_reduction")
    owner = torch.repeat_interleave(order, per_position, output_size=listed)
    if partition:
        by_term = torch.full((terms,), -1, device=device, dtype=owner.dtype)
        by_term[indices] = owner
        torch._assert_async((by_term >= 0).all())
        owner = by_term
    scale = torch.where(width > 0, 1.0 / width.clamp(min=1).float(), 0.0)
    return owner, scale, width == 0


@torch.library.custom_op("stpd_rows::embedding_backward", mutates_args=())
def _embedding_backward(grad: Tensor, indices: Tensor, rows: int) -> Tensor:
    """Weight gradient of an embedding lookup by the eager kernel (CUDA: sort and
    segment-sum); opaque to inductor, so it is not decomposed to atomics."""
    return torch.ops.aten.embedding_dense_backward(grad, indices, rows, -1, False)


@_embedding_backward.register_fake
def _(grad: Tensor, indices: Tensor, rows: int) -> Tensor:
    return grad.new_empty((rows, grad.shape[-1]))


class _Lookup(torch.autograd.Function):
    @staticmethod
    def forward(ctx: Any, weight: Tensor, indices: Tensor) -> Tensor:
        ctx.save_for_backward(indices)
        ctx.rows, ctx.dtype = weight.shape[0], weight.dtype
        return F.embedding(indices, weight)

    @staticmethod
    def backward(ctx: Any, grad: Tensor) -> tuple[Tensor, None]:
        (indices,) = ctx.saved_tensors
        return _embedding_backward(grad.contiguous(), indices, ctx.rows).to(ctx.dtype), None


def lookup(table: Any, indices: Tensor, *, segment: bool) -> Tensor:
    """`table(indices)`; with `segment` the weight gradient is a segment sum."""
    if not segment:
        return cast(Tensor, table(indices))
    return cast(Tensor, _Lookup.apply(table.weight, indices))


def gather(values: Tensor, indices: Tensor, *, segment: bool) -> Tensor:
    """`values.index_select(0, indices)`; with `segment` its backward too."""
    if not segment:
        return values.index_select(0, indices)
    return cast(Tensor, _Lookup.apply(values, indices))


def _node_builder(model: Any, t: dict[str, Tensor], vectors: Tensor,
                  owners: dict[str, tuple[Tensor, Tensor, Tensor]], refs: Tensor,
                  segment: bool = False) -> Tensor:
    """`MStd.tensor_nodes` (eval) from owner maps; same parameters and function."""
    rows = t["kinds"].shape[0]
    embed = model.ids.embedding_dim

    def table(module: Any, key: str) -> Tensor:
        return lookup(module, t[key], segment=segment)

    def mean(key: str, values: Tensor, empty: Tensor | None) -> Tensor:
        owner, scale, empty_rows = owners[key]
        total = values.new_zeros((rows, embed)).index_add(0, owner, values) * scale[:, None]
        if empty is None:
            return total
        return torch.where(empty_rows[:, None], empty.to(total.dtype), total)

    identities = table(model.ids, "identities") + table(model.fields, "identity_fields")
    nodes = mean("ids", identities, model.ids.weight[0])
    nodes = nodes + table(model.kinds, "kinds")
    nodes = nodes + positions(t["positions"], embed)
    numbers = torch.cat((table(model.number_slope, "number_fields") * t["numbers"][:, None]
                         + table(model.number_bias, "number_fields"),
                         table(model.number_missing, "missing_fields")))
    number_features = mean("numbers", numbers, model.no_numbers)
    terms = (gather(vectors, t["text_indices"], segment=segment)
             + table(model.fields, "text_fields"))
    features = mean("text", terms, None)
    nodes = nodes + (features + number_features)
    linked = mean("refs", gather(nodes, refs, segment=segment), None)
    return cast(Tensor, model.node_norm(nodes + model.relations(linked)))


def owner_nodes(model: Any, t: dict[str, Tensor], flat: dict[str, Any], vectors: Tensor, *,
                compile: bool, segment: bool = False) -> Tensor:
    """Node vectors of a rows-v2 group through the owner-map builder."""
    rows = t["kinds"].shape[0]
    terms = {"ids": t["identities"].numel(),
             "numbers": t["numbers"].numel() + t["missing_fields"].numel(),
             "text": t["text_fields"].numel(), "refs": 0}
    owners = {key: _owners(flat[key], rows, terms[key], key in PARTITION_REDUCTIONS)
              for key in ("ids", "numbers", "text", "refs")}
    builder = _node_builder
    if compile:
        builder = getattr(model, "_rows_nodes_compiled", None)
        if builder is None:
            builder = torch.compile(_node_builder, dynamic=True)
            setattr(model, "_rows_nodes_compiled", builder)  # learner-process runtime only
    return cast(Tensor, builder(model, t, vectors.float(), owners, flat["refs"][0], segment))
