"""S3 rows-v2: the RL training forward over a CPU row layout, without padding work.

Same parameters and the same function as `MStd.forward_prepared_rl4` (padded-v1):
every row sees the same nodes, text, attention membership, role terms and heads;
only work on padding is removed (S0-GPU 0-level items, measured 2026-11-07):
  1. scene/role gathers become index_copy/index_add over real slots only;
  2. each joint reduction is one index_select instead of one per width bucket;
  3. unique real texts are encoded in power-of-two length buckets and node text
     terms gather them directly in fp32 (no duplicate-occurrence tensor);
  4. scene rows are sorted by length into buckets (width a multiple of 64);
  5. attention is batch-first with the same in/out projections; on CUDA under
     BF16/FP16 autocast (sm80+) the scene encoder runs varlen flash attention on
     real tokens only, otherwise the same math on the bucket with a key mask;
  6. optional torch.compile of the per-bucket block (explicit selection).
Float accumulation order differs from padded-v1 (BF16 tolerance, not bitwise).
Dropout is not implemented here: the prepared RL step runs in eval mode and this
forward refuses training mode instead of silently skipping dropout.
"""

from __future__ import annotations

import math
from typing import Any

import torch
from torch import Tensor
from torch.nn import functional as F

from .tensor_input import JointNodes


def mean_terms_flat(values: Tensor, flat: tuple[Tensor, tuple[tuple[int, int], ...], Tensor],
                    empty: Tensor) -> Tensor:
    """`model._mean_terms` for a Reduction, with one gather for all width buckets.

    Same rows, same per-row mean length and term order; the backward is one
    index_add instead of one full-size gradient per width bucket.
    """
    indices, buckets, inverse = flat
    sizes = [width * count for width, count in buckets if width]
    gathered = values.index_select(0, indices).split(sizes) if sizes else ()
    parts, cursor = [], 0
    for width, count in buckets:
        if width:
            parts.append(gathered[cursor].reshape(count, width, -1).mean(1))
            cursor += 1
        else:
            parts.append(empty.expand(count, -1))
    return torch.cat(parts).index_select(0, inverse)


def attention(module: torch.nn.MultiheadAttention, query: Tensor, kv: Tensor,
              key_padding_mask: Tensor) -> Tensor:
    """nn.MultiheadAttention(batch_first, need_weights=False) in eval, same weights,
    without its sequence-first transposes and copies."""
    embed, heads = module.embed_dim, module.num_heads
    width = embed // heads
    weight, bias = module.in_proj_weight, module.in_proj_bias
    batch, queries = query.shape[:2]
    keys = kv.shape[1]
    keep = (~key_padding_mask)[:, None, None, :]
    if query is kv:
        q, k, v = F.linear(query, weight, bias).view(batch, queries, 3, heads, width).permute(
            2, 0, 3, 1, 4)
    else:
        q = F.linear(query, weight[:embed], bias[:embed]).view(
            batch, queries, heads, width).transpose(1, 2)
        k, v = F.linear(kv, weight[embed:], bias[embed:]).view(
            batch, keys, 2, heads, width).permute(2, 0, 3, 1, 4)
    out = F.scaled_dot_product_attention(q, k, v, attn_mask=keep)
    return F.linear(out.transpose(1, 2).reshape(batch, queries, embed),
                    module.out_proj.weight, module.out_proj.bias)


def _check_encoder(encoder: torch.nn.TransformerEncoder) -> None:
    for layer in encoder.layers:
        if layer.norm_first or layer.activation is not F.relu or not layer.self_attn.batch_first:
            raise ValueError("rows_forward_requires_post_norm_relu_batch_first_layers")
    if encoder.norm is not None:
        raise ValueError("rows_forward_requires_encoder_without_final_norm")


def _layer_tail(layer: Any, x: Tensor, attended: Tensor) -> Tensor:
    x = layer.norm1(x + attended)
    return layer.norm2(x + layer.linear2(F.relu(layer.linear1(x))))


def encode_masked(encoder: torch.nn.TransformerEncoder, x: Tensor, mask: Tensor) -> Tensor:
    for layer in encoder.layers:
        x = _layer_tail(layer, x, attention(layer.self_attn, x, x, mask))
    return x


def encode_varlen(encoder: torch.nn.TransformerEncoder, tokens: Tensor, offsets: Tensor,
                  longest: int) -> Tensor:
    """Self-attention over ragged rows packed as (tokens, embed); no padding anywhere."""
    for layer in encoder.layers:
        module = layer.self_attn
        embed, heads = module.embed_dim, module.num_heads
        count = tokens.shape[0]
        q, k, v = F.linear(tokens, module.in_proj_weight, module.in_proj_bias).view(
            count, 3, heads, embed // heads).unbind(1)
        out = torch.ops.aten._flash_attention_forward(
            q.contiguous(), k.contiguous(), v.contiguous(), offsets, offsets, longest, longest,
            0.0, False, False, scale=1.0 / math.sqrt(embed // heads))[0]
        attended = F.linear(out.reshape(count, embed), module.out_proj.weight,
                            module.out_proj.bias)
        tokens = _layer_tail(layer, tokens, attended)
    return tokens


def varlen_supported(device: torch.device) -> bool:
    """Flash varlen needs CUDA sm80+ and a half-precision autocast (BF16 here)."""
    return (device.type == "cuda" and torch.is_autocast_enabled("cuda")
            and torch.get_autocast_dtype("cuda") in (torch.bfloat16, torch.float16)
            and torch.cuda.get_device_capability(device) >= (8, 0))


def head(module: Any, scene: Tensor, mask: Tensor, mean: Tensor) -> Tensor:
    """AttentionValueResidual.forward with batch-first attention."""
    pooled = attention(module.attention, module.query.expand(scene.shape[0], -1, -1), scene, mask)
    return module.mlp(torch.cat((pooled[:, 0], mean), dim=1))


def rows_block(model: Any, tokens: Tensor, positions: Tensor, lengths: Tensor, offsets: Tensor,
               rows: int, width: int, longest: int, varlen: bool, outcome_valid: Tensor,
               action_types: Tensor | None, catalog_mask: Tensor | None,
               roles: tuple[tuple[Tensor, Tensor, Tensor, Tensor], ...]) -> tuple[
                   Tensor | None, Tensor, Tensor]:
    """Scene encoder + heads + action readout for one length bucket of rows."""
    embed = tokens.shape[1]
    mask = torch.arange(width, device=tokens.device)[None, :] >= lengths[:, None]
    if varlen:
        encoded = encode_varlen(model.scene, tokens, offsets, longest)
        scene = encoded.new_zeros((rows * width, embed)).index_copy(0, positions, encoded)
    else:
        packed = tokens.new_zeros((rows * width, embed)).index_copy(0, positions, tokens)
        scene = encode_masked(model.scene, packed.view(rows, width, embed), mask)
    scene = scene.view(rows, width, -1)
    current = (scene * (~mask).unsqueeze(-1)).sum(1) / lengths[:, None].float()
    logits = head(model.rl4_heads.outcome, scene, mask, current).float()
    logits = logits.masked_fill(~outcome_valid, -torch.inf)
    hp = head(model.rl4_heads.hp, scene, mask, current)[:, 0].float()
    if action_types is None or catalog_mask is None:
        return None, logits, hp
    action = model.action_types(action_types)
    flat = scene.reshape(-1, scene.shape[-1])
    means = []
    for index, (candidate, slot, weight, present) in enumerate(roles):
        values = flat.index_select(0, slot) * weight.unsqueeze(-1).to(flat.dtype)
        mean = flat.new_zeros((action.shape[0] * action.shape[1], flat.shape[1])).index_add(
            0, candidate, values)
        mean = torch.where(present[:, None], mean, model.absent_roles[index])
        means.append(mean.reshape(action.shape))
    action = action + model.source_role(means[0]) + model.target_role(means[1])
    read = attention(model.cross, action, scene, mask)
    scores = model.head(torch.cat((action + read,
                                   current[:, None, :].expand(-1, action.shape[1], -1)), -1))
    return scores.squeeze(-1).masked_fill(catalog_mask, -torch.inf), logits, hp


def _compiled_block(model: Any) -> Any:
    block = getattr(model, "_rows_block_compiled", None)
    if block is None:
        block = torch.compile(rows_block, dynamic=None)
        model._rows_block_compiled = block  # learner-process runtime only, not state
    return block


def forward_rows(model: Any, group: Any, *, compile: bool = False) -> tuple[
        Tensor, Tensor, Tensor]:
    if model.training:
        raise ValueError("rows_forward_requires_eval_mode")
    layout = group.layout
    device = model.ids.weight.device
    nonblocking = model.nonblocking_inputs

    def move(value: Tensor) -> Tensor:
        return value.to(device, non_blocking=nonblocking)

    t = {key: move(value) for key, value in group.tensors.items()}
    rows = len(group.rows)
    if layout.text_buckets:
        order = move(layout.text_order)
        parts, cursor = [], 0
        for count, width in layout.text_buckets:
            selected = order[cursor:cursor + count]
            cursor += count
            parts.append(model._encode_text(
                t["text_ids"].index_select(0, selected)[:, :width],
                t["text_numbers"].index_select(0, selected)[:, :width],
                t["text_lengths"].index_select(0, selected)))
        vectors = torch.cat(parts).index_select(0, move(layout.text_inverse)).float()
    else:
        vectors = model.ids.weight.new_empty((0, model.ids.weight.shape[1]))
    joint = JointNodes({**group.joint.tensors, "text_indices": layout.node_text_indices},
                       group.joint.reductions)
    flat = {key: (move(indices), buckets, move(inverse))
            for key, (indices, buckets, inverse) in layout.reductions.items()}
    nodes = model.tensor_nodes(joint, vectors, flat=flat)
    _check_encoder(model.scene)
    varlen = varlen_supported(device)
    block = _compiled_block(model) if compile else rows_block
    buckets = layout.scene_buckets
    row_sizes = [bucket[0] for bucket in buckets]
    slot_sizes = [bucket[3] for bucket in buckets]
    tokens = nodes.index_select(0, move(layout.scene_nodes)).split(slot_sizes)
    positions = move(layout.scene_positions).split(slot_sizes)
    lengths = move(layout.scene_lengths).split(row_sizes)
    offsets = move(layout.scene_offsets).split([size + 1 for size in row_sizes])
    members = move(layout.scene_rows).split(row_sizes)
    role_parts: list[tuple[tuple[Tensor, Tensor, Tensor, Tensor], ...]] = [()] * len(buckets)
    if group.policy:
        catalog = t["action_types"].shape[1]
        split: dict[str, list[tuple[Tensor, Tensor, Tensor, Tensor]]] = {}
        for key in ("source", "target"):
            candidate, slot, weight, terms = layout.roles[key]
            split[key] = list(zip(move(candidate).split(list(terms)),
                                  move(slot).split(list(terms)),
                                  move(weight).split(list(terms)),
                                  move(layout.role_present[key]).split(
                                      [size * catalog for size in row_sizes]), strict=True))
        role_parts = [(split["source"][i], split["target"][i]) for i in range(len(buckets))]
    scores, logits, hp = [], [], []
    for index, (count, width, longest, _slots) in enumerate(buckets):
        member = members[index]
        s, l, h = block(
            model, tokens[index], positions[index], lengths[index], offsets[index], count, width,
            longest, varlen, t["outcome_valid"].index_select(0, member),
            t["action_types"].index_select(0, member) if group.policy else None,
            t["catalog_mask"].index_select(0, member) if group.policy else None,
            role_parts[index])
        logits.append(l)
        hp.append(h)
        if s is not None:
            scores.append(s)
    inverse = move(layout.scene_inverse)
    all_logits = torch.cat(logits).index_select(0, inverse)
    all_hp = torch.cat(hp).index_select(0, inverse)
    if not group.policy:
        return all_logits.new_empty((rows, 0)), all_logits, all_hp
    return torch.cat(scores).index_select(0, inverse), all_logits, all_hp
