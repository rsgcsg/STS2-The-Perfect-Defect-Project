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
``nodes``  Gathers that are permutations by construction take a gather as their
           backward instead of an atomic index_add: the scene token gather (each
           real row owns its own contiguous node range, all nodes are scene
           tokens), each reduction's bucket inverse, and the term gathers of the
           partition reductions (ids, numbers, text: every term has one owner).
           A broken permutation fails loudly (device assert), never silently. The
           text-vector lookup (many terms per unique text) uses the sorted
           embedding backward instead of contended atomics.
``cross``  The action cross-attention folds the same way per (action, head):
           catalog x heads folded queries per row attend to the encoded tokens,
           so no key/value projection of the scene. Catalogs longer than
           FOLD_CATALOG_LIMIT keep the original projected attention (shape rule).

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

LEVERS = ("cross", "heads", "nodes")
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
            or list(parts) != sorted(parts)):
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


def lookup_rows(table: Tensor, indices: Tensor) -> Tensor:
    """table.index_select(0, indices) with the sorted embedding backward."""
    return F.embedding(indices, table)
