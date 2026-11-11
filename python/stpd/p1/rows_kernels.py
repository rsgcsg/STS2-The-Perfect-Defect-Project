"""V3 T6: explicit training-forward levers for rows-v2 (`prepared_kernels`, default off).

Same parameters and the same function as `rows_forward.rows_block[_packed]`; only
how the attention readouts are computed changes (BF16 tolerance, not bitwise).
Both levers read one padded [rows, longest, embed] view of the encoded scene
(the packed path builds it from the real tokens; the masked path already has it).

``heads``  Each value head attends from one learned query. Its key projection
           folds into that query, q.(W_k e + b_k) = e.(W_k^T q) + q.b_k, and the
           constant q.b_k cancels in the softmax over keys; its value projection
           moves after the weighted sum, sum_t w_t (W_v e_t + b_v) =
           W_v (sum_t w_t e_t) + b_v because sum_t w_t = 1. So neither head
           projects every scene token to keys/values, and no flash 128-row query
           tile is spent on one query. Both heads share one score matmul.
``cross``  The action cross-attention (catalog queries per row) runs as dense
           batched matmuls on the padded view instead of flash varlen with
           catalog-length queries. Short catalogs fold the key/value projections
           the same way as the heads; long catalogs project keys/values once.

A row must hold at least one scene token (the original mean divides by it too).
"""

from __future__ import annotations

import math
from typing import Any, cast

import torch
from torch import Tensor
from torch.nn import functional as F

LEVERS = ("cross", "heads")
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


def padded_view(encoded: Tensor, positions: Tensor, lengths: Tensor, rows: int, width: int,
                longest: int) -> tuple[Tensor, Tensor]:
    """Packed real tokens -> [rows, longest, embed] in the compute dtype, and the
    key-keep mask. `positions` are bucket slots (row * width + slot)."""
    index = positions.long()
    owner = torch.div(index, width, rounding_mode="floor")
    index = owner * longest + (index - owner * width)
    source = encoded.to(_compute_dtype(encoded))
    padded = source.new_zeros((rows * longest, source.shape[1])).index_copy(0, index, source)
    keep = torch.arange(longest, device=encoded.device)[None, :] < lengths[:, None]
    return padded.view(rows, longest, -1), keep


def _parts(module: torch.nn.MultiheadAttention) -> tuple[int, int, int]:
    embed, heads = module.embed_dim, module.num_heads
    return embed, heads, embed // heads


def folded_heads(modules: tuple[Any, ...], padded: Tensor, keep: Tensor,
                 current: Tensor) -> list[Tensor]:
    """`AttentionValueResidual` readouts of every module, keys/values folded."""
    queries = []
    for module in modules:
        attention = module.attention
        embed, heads, width = _parts(attention)
        weight, bias = attention.in_proj_weight, attention.in_proj_bias
        query = F.linear(module.query.reshape(1, embed), weight[:embed], bias[:embed])
        keys = weight[embed:2 * embed].view(heads, width, embed)
        queries.append(torch.einsum("hd,hde->he", query.view(heads, width), keys)
                       * (1.0 / math.sqrt(width)))
    folded = torch.cat(queries).to(padded.dtype)  # [modules * heads, embed]
    scores = torch.matmul(padded, folded.t()).float()  # [rows, longest, modules * heads]
    weights = scores.masked_fill(~keep[:, :, None], -torch.inf).softmax(1)
    pooled = torch.matmul(weights.transpose(1, 2).to(padded.dtype), padded)
    outputs, start = [], 0
    for module in modules:
        attention = module.attention
        embed, heads, width = _parts(attention)
        weight, bias = attention.in_proj_weight, attention.in_proj_bias
        values = torch.einsum("rhe,hde->rhd", pooled[:, start:start + heads],
                              weight[2 * embed:].view(heads, width, embed).to(padded.dtype))
        values = values + bias[2 * embed:].view(heads, width)
        start += heads
        read = F.linear(values.reshape(-1, embed), attention.out_proj.weight,
                        attention.out_proj.bias)
        outputs.append(cast(Tensor, module.mlp(torch.cat((read, current), dim=1))))
    return outputs


def padded_cross(module: torch.nn.MultiheadAttention, action: Tensor, padded: Tensor,
                 keep: Tensor) -> Tensor:
    """nn.MultiheadAttention(action -> padded scene, eval, need_weights=False)."""
    rows, catalog, _ = action.shape
    embed, heads, width = _parts(module)
    weight, bias = module.in_proj_weight, module.in_proj_bias
    query = F.linear(action, weight[:embed], bias[:embed]).view(rows, catalog, heads, width)
    scale = 1.0 / math.sqrt(width)
    if catalog <= FOLD_CATALOG_LIMIT:
        keys = weight[embed:2 * embed].view(heads, width, embed)
        folded = (torch.einsum("rchd,hde->rche", query, keys.to(query.dtype)) * scale).reshape(
            rows, catalog * heads, embed).to(padded.dtype)
        scores = torch.matmul(folded, padded.transpose(1, 2)).float()  # [rows, C*H, longest]
        weights = scores.masked_fill(~keep[:, None, :], -torch.inf).softmax(-1)
        pooled = torch.matmul(weights.to(padded.dtype), padded).view(rows, catalog, heads, embed)
        values = torch.einsum("rche,hde->rchd", pooled,
                              weight[2 * embed:].view(heads, width, embed).to(padded.dtype))
        values = values + bias[2 * embed:].view(heads, width)
    else:
        longest = padded.shape[1]
        kv = F.linear(padded, weight[embed:], bias[embed:]).view(rows, longest, 2, heads, width)
        key, value = kv.unbind(2)
        scores = torch.einsum("rchd,rlhd->rhcl", query.to(key.dtype), key).float() * scale
        weights = scores.masked_fill(~keep[:, None, None, :], -torch.inf).softmax(-1)
        values = torch.einsum("rhcl,rlhd->rchd", weights.to(value.dtype), value)
    return F.linear(values.reshape(rows, catalog, embed), module.out_proj.weight,
                    module.out_proj.bias)
