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
     BF16/FP16 autocast (sm80+) every attention runs varlen flash attention on
     real tokens only ("packed", C-SCALE step 2): the text encoder over packed
     unique texts, the scene encoder, both value heads (one query per row) and
     the action cross-attention (catalog queries per row) read the packed scene
     tokens directly, so no padded scene tensor or key mask is built. Elsewhere
     (CPU, FP32) the same math runs on the bucket with a key mask;
  6. optional torch.compile of the per-bucket block (explicit selection);
  7. optional V3 T6 levers (`rows_kernels`, explicit `prepared_kernels`): the value
     heads and the action cross-attention fold their key/value projections into
     the queries and attend straight to the encoded scene tokens.
Float accumulation order differs from padded-v1 (BF16 tolerance, not bitwise).
Dropout is not implemented here: the prepared RL step runs in eval mode and this
forward refuses training mode instead of silently skipping dropout.
"""

from __future__ import annotations

import math
from typing import Any, cast

import torch
from torch import Tensor
from torch.nn import functional as F

from .model import positions
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
    return cast(Tensor, layer.norm2(x + layer.linear2(F.relu(layer.linear1(x)))))


def encode_masked(encoder: torch.nn.TransformerEncoder, x: Tensor, mask: Tensor) -> Tensor:
    for layer in encoder.layers:
        x = _layer_tail(layer, x, attention(layer.self_attn, x, x, mask))
    return x


def flash_varlen(q: Tensor, k: Tensor, v: Tensor, cu_q: Tensor, cu_k: Tensor, max_q: int,
                 max_k: int, scale: float | None = None) -> Tensor:
    """Ragged attention over packed (tokens, heads, width) sequences, no mask.

    CUDA: the flash varlen kernel. Elsewhere: the same math one sequence at a
    time (a reference for CPU tests; never selected by the GPU step)."""
    if scale is None:
        scale = 1.0 / math.sqrt(q.shape[-1])
    if q.is_cuda:
        return cast(Tensor, torch.ops.aten._flash_attention_forward(
            q.contiguous(), k.contiguous(), v.contiguous(), cu_q, cu_k, max_q, max_k,
            0.0, False, False, scale=scale)[0])
    bounds_q, bounds_k = cu_q.tolist(), cu_k.tolist()
    parts = [F.scaled_dot_product_attention(
        q[a:b].transpose(0, 1), k[c:d].transpose(0, 1), v[c:d].transpose(0, 1),
        scale=scale).transpose(0, 1)
        for a, b, c, d in zip(bounds_q[:-1], bounds_q[1:], bounds_k[:-1], bounds_k[1:],
                              strict=True)]
    return torch.cat(parts) if parts else q.new_empty(q.shape)


def packed_attention(module: torch.nn.MultiheadAttention, query: Tensor, kv: Tensor,
                     cu_q: Tensor, cu_k: Tensor, max_q: int, max_k: int) -> Tensor:
    """nn.MultiheadAttention (eval, need_weights=False) over packed rows: query
    tokens of sequence i attend exactly to kv tokens of sequence i."""
    embed, heads = module.embed_dim, module.num_heads
    width = embed // heads
    weight, bias = module.in_proj_weight, module.in_proj_bias
    if query is kv:
        q, k, v = F.linear(query, weight, bias).view(-1, 3, heads, width).unbind(1)
    else:
        q = F.linear(query, weight[:embed], bias[:embed]).view(-1, heads, width)
        k, v = F.linear(kv, weight[embed:], bias[embed:]).view(-1, 2, heads, width).unbind(1)
    out = flash_varlen(q, k, v, cu_q, cu_k, max_q, max_k)
    return F.linear(out.reshape(-1, embed), module.out_proj.weight, module.out_proj.bias)


def encode_varlen(encoder: torch.nn.TransformerEncoder, tokens: Tensor, offsets: Tensor,
                  longest: int) -> Tensor:
    """Self-attention over ragged rows packed as (tokens, embed); no padding anywhere."""
    for layer in encoder.layers:
        attended = packed_attention(layer.self_attn, tokens, tokens, offsets, offsets,
                                    longest, longest)
        tokens = _layer_tail(layer, tokens, attended)
    return tokens


def packed_text(model: Any, ids: Tensor, numbers: Tensor, layout: Any, move: Any) -> Tensor:
    """`model._encode_text` for every unique text at once, real tokens only.

    Same embeddings, positions, encoder, mean over real tokens and projection;
    output in unique-text order (no bucket inverse needed)."""
    tokens = move(layout.text_tokens)
    offsets = move(layout.text_offsets)
    width = ids.shape[1]
    texts = offsets.shape[0] - 1
    owner = torch.div(tokens, width, rounding_mode="floor")
    value = (model.text_ids(ids.reshape(-1).index_select(0, tokens))
             + model.text_numbers(numbers.reshape(-1, numbers.shape[-1]).index_select(0, tokens))
             + positions(tokens - owner * width, model.text_ids.embedding_dim))
    _check_encoder(model.text_blocks)
    encoded = encode_varlen(model.text_blocks, value, offsets, layout.text_longest)
    lengths = (offsets[1:] - offsets[:-1]).to(encoded.dtype)
    mean = encoded.new_zeros((texts, encoded.shape[1])).index_add(0, owner, encoded)
    return cast(Tensor, model.text_projection(mean / lengths[:, None]))


def varlen_supported(device: torch.device) -> bool:
    """Flash varlen needs CUDA sm80+ and a half-precision autocast (BF16 here)."""
    return (device.type == "cuda" and torch.is_autocast_enabled("cuda")
            and torch.get_autocast_dtype("cuda") in (torch.bfloat16, torch.float16)
            and torch.cuda.get_device_capability(device) >= (8, 0))


def compile_counters() -> dict[str, int]:
    """This process's dynamo counters (the SGD process runs only this model).

    frames_ok counts compiles and recompiles; frames_total - frames_ok counts
    frames dynamo did not compile (for example past cache_size_limit: eager)."""
    from torch._dynamo.utils import counters

    return {"dynamo_frames_ok": int(counters["frames"]["ok"]),
            "dynamo_frames_total": int(counters["frames"]["total"]),
            "dynamo_unique_graphs": int(counters["stats"]["unique_graphs"])}


def head(module: Any, scene: Tensor, mask: Tensor, mean: Tensor) -> Tensor:
    """AttentionValueResidual.forward with batch-first attention."""
    pooled = attention(module.attention, module.query.expand(scene.shape[0], -1, -1), scene, mask)
    return cast(Tensor, module.mlp(torch.cat((pooled[:, 0], mean), dim=1)))


def rows_block(model: Any, tokens: Tensor, positions: Tensor, lengths: Tensor, offsets: Tensor,
               rows: int, width: int, longest: int, outcome_valid: Tensor,
               action_types: Tensor | None, catalog_mask: Tensor | None,
               roles: tuple[tuple[Tensor, Tensor, Tensor, Tensor], ...], *,
               kernels: tuple[str, ...] = ()) -> tuple[Tensor | None, Tensor, Tensor]:
    """Scene encoder + heads + action readout for one length bucket of rows
    (masked: padded bucket and key mask; CPU/FP32 and the reference)."""
    embed = tokens.shape[1]
    mask = torch.arange(width, device=tokens.device)[None, :] >= lengths[:, None]
    packed = tokens.new_zeros((rows * width, embed)).index_copy(0, positions.long(), tokens)
    scene = encode_masked(model.scene, packed.view(rows, width, embed), mask)
    scene = scene.view(rows, width, -1)
    current = (scene * (~mask).unsqueeze(-1)).sum(1) / lengths[:, None].float()
    padded = scene.to(_kernel_dtype(scene)) if kernels else scene
    if "heads" in kernels:
        from .rows_kernels import folded_heads

        outcome, value = folded_heads((model.rl4_heads.outcome, model.rl4_heads.hp), padded,
                                      ~mask, current)
        logits, hp = outcome.float(), value[:, 0].float()
    else:
        logits = head(model.rl4_heads.outcome, scene, mask, current).float()
        hp = head(model.rl4_heads.hp, scene, mask, current)[:, 0].float()
    logits = logits.masked_fill(~outcome_valid, -torch.inf)
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
    if "cross" in kernels:
        from .rows_kernels import padded_cross

        read = padded_cross(model.cross, action, padded, ~mask)
    else:
        read = attention(model.cross, action, scene, mask)
    scores = model.head(torch.cat((action + read,
                                   current[:, None, :].expand(-1, action.shape[1], -1)), -1))
    return scores.squeeze(-1).masked_fill(catalog_mask, -torch.inf), logits, hp


def rows_block_packed(model: Any, tokens: Tensor, positions: Tensor, lengths: Tensor,
                      offsets: Tensor, rows: int, width: int, longest: int,
                      outcome_valid: Tensor, action_types: Tensor | None,
                      catalog_mask: Tensor | None,
                      roles: tuple[tuple[Tensor, Tensor, Tensor, Tensor], ...], *,
                      kernels: tuple[str, ...] = ()) -> tuple[Tensor | None, Tensor, Tensor]:
    """`rows_block` on packed real scene tokens only (CUDA BF16 flash varlen).

    Same function: the mean, both heads and the cross-attention read exactly the
    real slots of their own row; role terms map bucket slots to packed tokens."""
    encoded = encode_varlen(model.scene, tokens, offsets, longest)
    owner = torch.div(positions, width, rounding_mode="floor")
    current = encoded.new_zeros((rows, encoded.shape[1])).index_add(0, owner, encoded)
    current = current / lengths[:, None].to(current.dtype)
    one = torch.arange(rows + 1, device=tokens.device, dtype=torch.int32)

    def pooled(module: Any) -> Tensor:
        query = module.query.reshape(1, -1).expand(rows, -1)
        value = packed_attention(module.attention, query, encoded, one, offsets, 1, longest)
        return cast(Tensor, module.mlp(torch.cat((value, current), dim=1)))

    if "heads" in kernels:
        from .rows_kernels import packed_heads

        outcome, value = packed_heads((model.rl4_heads.outcome, model.rl4_heads.hp), encoded,
                                      offsets, longest, current)
        logits, hp = outcome.float(), value[:, 0].float()
    else:
        logits = pooled(model.rl4_heads.outcome).float()
        hp = pooled(model.rl4_heads.hp)[:, 0].float()
    logits = logits.masked_fill(~outcome_valid, -torch.inf)
    if action_types is None or catalog_mask is None:
        return None, logits, hp
    action = model.action_types(action_types)
    catalog = action.shape[1]
    starts = offsets.long()
    means = []
    for index, (candidate, slot, weight, present) in enumerate(roles):
        row = torch.div(slot, width, rounding_mode="floor")
        token = starts.index_select(0, row) + slot - row * width
        values = encoded.index_select(0, token) * weight.unsqueeze(-1).to(encoded.dtype)
        mean = encoded.new_zeros((rows * catalog, encoded.shape[1])).index_add(
            0, candidate, values)
        mean = torch.where(present[:, None], mean, model.absent_roles[index])
        means.append(mean.reshape(action.shape))
    action = action + model.source_role(means[0]) + model.target_role(means[1])
    if "cross" in kernels:  # forward_rows selects it only for catalogs <= FOLD_CATALOG_LIMIT
        from .rows_kernels import packed_cross

        read = packed_cross(model.cross, action, encoded, offsets, longest)
    else:
        read = packed_attention(model.cross, action.reshape(rows * catalog, -1), encoded,
                                one * catalog, offsets, catalog, longest).view(action.shape)
    scores = model.head(torch.cat((action + read,
                                   current[:, None, :].expand(-1, catalog, -1)), -1))
    return scores.squeeze(-1).masked_fill(catalog_mask, -torch.inf), logits, hp


def _kernel_dtype(reference: Tensor) -> torch.dtype:
    from .rows_kernels import _compute_dtype

    return _compute_dtype(reference)


def _block(packed: bool, kernels: tuple[str, ...]) -> Any:
    function: Any = rows_block_packed if packed else rows_block
    if not kernels:
        return function

    # `kernels` is keyword-only in both blocks: the 12 positional block arguments
    # can never fill it.
    def block(*args: Any) -> Any:
        return function(*args, kernels=kernels)

    return block


def _compiled_block(model: Any, packed: bool, kernels: tuple[str, ...] = ()) -> Any:
    name = "_rows_block_packed_compiled" if packed else "_rows_block_compiled"
    if kernels:
        name += "_" + "_".join(kernels)
    block = getattr(model, name, None)
    if block is None:
        # Levered blocks start shape-generic: rows, tokens, widths and catalogs vary per
        # group, and each first change would otherwise cost a recompile (V3 T6 probe).
        block = torch.compile(_block(packed, kernels), dynamic=True if kernels else None)
        setattr(model, name, block)  # learner-process runtime only, not state
    return block


def forward_rows(model: Any, group: Any, *, compile: bool = False,
                 packed: bool | None = None, kernels: str = "") -> tuple[Tensor, Tensor, Tensor]:
    """`packed` defaults to the device: CUDA sm80+ under BF16/FP16 autocast.
    Tests may force it on CPU (reference attention, one sequence at a time).
    `kernels` selects V3 T6 levers (`rows_kernels.LEVERS`); '' is the original block."""
    if model.training:
        raise ValueError("rows_forward_requires_eval_mode")
    from .rows_kernels import parse_kernels

    levers = parse_kernels(kernels)
    device = model.ids.weight.device
    if device.type == "cuda" and any(value.device.type == "cpu"
                                     for value in group.tensors.values()):
        # A host group (inference): its forward inputs in one pinned non-blocking
        # copy instead of ~50 pageable per-tensor copies (each a stream sync).
        # int64 here: casting/checking on this thread cost more than it saved
        # (Friday 1454515); int32 inputs come from `write_forward_inputs` in the
        # prepare processes. The learner's groups are already staged: untouched.
        from stpd.rl.batch_preparation import stage_group

        group = stage_group(group, device, index_dtype=torch.int64)
    layout = group.layout
    nonblocking = model.nonblocking_inputs

    def move(value: Tensor) -> Tensor:
        return value.to(device, non_blocking=nonblocking)

    t = {key: move(value) for key, value in group.tensors.items()}
    rows = len(group.rows)
    if packed is None:
        packed = varlen_supported(device)
    if packed and layout.text_buckets and layout.text_tokens is not None:
        vectors = packed_text(model, t["text_ids"], t["text_numbers"], layout, move).float()
    elif layout.text_buckets:
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
    nodes_lever = "nodes" in levers
    if nodes_lever:
        from .rows_kernels import owner_nodes, permute_rows

        nodes = owner_nodes(model, {key: move(value) for key, value in joint.tensors.items()},
                            flat, vectors, compile=compile)
    else:
        nodes = model.tensor_nodes(joint, vectors, flat=flat)
    _check_encoder(model.scene)
    from .rows_kernels import FOLD_CATALOG_LIMIT

    # The fold is chosen per group outside the compiled block (no catalog guard inside).
    block_levers = tuple(lever for lever in levers if lever == "heads" or (
        lever == "cross" and group.policy
        and int(group.tensors["action_types"].shape[1]) <= FOLD_CATALOG_LIMIT))
    block = (_compiled_block(model, packed, block_levers) if compile
             else _block(packed, block_levers))
    buckets = layout.scene_buckets
    row_sizes = [bucket[0] for bucket in buckets]
    slot_sizes = [bucket[3] for bucket in buckets]
    if nodes_lever:
        tokens = permute_rows(nodes, move(layout.scene_nodes), "scene").split(slot_sizes)
    else:
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
        score, logit, value = block(
            model, tokens[index], positions[index], lengths[index], offsets[index], count, width,
            longest, t["outcome_valid"].index_select(0, member),
            t["action_types"].index_select(0, member) if group.policy else None,
            t["catalog_mask"].index_select(0, member) if group.policy else None,
            role_parts[index])
        logits.append(logit)
        hp.append(value)
        if score is not None:
            scores.append(score)
    inverse = move(layout.scene_inverse)
    all_logits = torch.cat(logits).index_select(0, inverse)
    all_hp = torch.cat(hp).index_select(0, inverse)
    if not group.policy:
        return all_logits.new_empty((rows, 0)), all_logits, all_hp
    return torch.cat(scores).index_select(0, inverse), all_logits, all_hp
