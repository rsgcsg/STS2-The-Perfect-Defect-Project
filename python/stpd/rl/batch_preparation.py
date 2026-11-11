"""Complete parameter-free RL batches, owned by independent CPU processes.

The learner fixes membership and auxiliary reservations before submitting. Each
source is read/decoded once per batch, including all its auxiliary references.
Workers never construct MStd, initialize Adam, publish mu or charge uses.
"""

from __future__ import annotations

import dataclasses
import math
import multiprocessing
import os
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

import torch
import torch.nn.functional as F
from torch import Tensor

from spireagent.rl_contracts import HotPayloadRef, RowRef
from spireagent.rl_dataplane_ports import BinaryRef
from stpd.p1.export import input_spec, runtime_projection
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.tensor_input import JointNodes, Reduction, TensorFrame, joint_nodes, text_batch

from .heads import OUTCOME_CLASSES, outcome_mask
from .prepared_batch import (
    AdvantageNormalization,
    AuxiliaryFrame,
    AuxiliaryInput,
    FrameLayout,
    PreparedBatch,
    PreparedGroup,
    PreparedMinibatch,
    PrepareRequest,
    RowLayout,
)
from .segment_inputs import decode_auxiliary_bytes, verified_auxiliary_bytes
from .segment_targets import recorded_targets


@dataclasses.dataclass(frozen=True)
class PreparedSource:
    segment_id: str
    run_id: str
    generation: str
    frames: tuple[TensorFrame | None, ...]
    floors: tuple[int, ...]
    row_refs: tuple[RowRef | None, ...]
    probabilities: Tensor
    targets: Tensor
    raw_advantages: Tensor
    actions: tuple[int, ...]
    policy_probabilities: tuple[tuple[float, ...], ...]
    metadata: tuple[dict[str, Any], ...]
    seconds: dict[str, float]
    pid: int
    compiler_identity: dict[str, Any]
    labels: dict[str, Any]


# These are per-worker immutable construction facts, never learner parameters.
_worker: dict[str, Any] = {}
T = TypeVar("T")


def validate_training_source(segment: dict[str, Any], excluded: frozenset[str]) -> None:
    identity = segment["identity"]
    seed = identity.get("seed") if isinstance(identity, dict) else None
    if (
        segment["training_split"] != "TRAIN"
        or not isinstance(seed, str)
        or not seed
        or seed in excluded
        or not _training_seed(seed)
    ):
        raise ValueError("segment_seed_is_held_out_or_not_TRAIN")


@lru_cache(maxsize=1024)
def _training_seed(seed: str) -> bool:
    # Frozen suite facts are independent of theta. Do not reread all evaluation
    # manifests for every short segment of the same episode.
    from stpd.evaluation.suites import seed_membership

    from .critic import held_out

    return not seed_membership(seed) and not held_out(seed)


def _initialize(
    vocabulary: dict[str, Any],
    threads: int,
    peer: dict[str, Any] | None,
    run: str,
    reader: Callable[..., dict[str, Any]] | None,
    scratch: str,
    excluded_seeds: frozenset[str],
    gae_lambda: float = 0.99,
) -> None:
    torch.set_num_threads(threads)
    if torch.get_num_interop_threads() != 1:
        torch.set_num_interop_threads(1)
    # Each process opens its own transport; no locks, sockets or credentials are
    # pickled out of the learner. The normal peer reads credentials from env.
    from spireagent.rl_exchange import Exchange

    _worker.update(
        gae_lambda=gae_lambda,
        compiler=FrameCompiler(vocabulary),
        expected_spec=input_spec(vocabulary, runtime_projection(vocabulary)),
        exchange=Exchange(peer, run) if peer is not None else None,
        reader=reader,
        scratch=Path(scratch),
        excluded_seeds=excluded_seeds,
    )


def _read_source(
    payload: HotPayloadRef | BinaryRef,
    member: Any,
    submitted_at: float | None = None,
) -> Any:
    if not isinstance(payload, HotPayloadRef):
        raise ValueError("binary_reference_requires_binary_preparer")
    began = time.perf_counter()
    reader = _worker["reader"]
    read_costs: dict[str, float] = {}
    if reader is None:
        # W3 owns this transport/typed verifier. No artifact reader fallback is
        # permitted on the new live path.
        segment = _worker["exchange"].read_hot_segment(payload, costs=read_costs)
    else:
        segment = reader(_worker["exchange"], payload, scratch=_worker["scratch"])
    read_seconds = time.perf_counter() - began
    if (
        segment["segment_id"] != member.segment_id
        or segment["run_id"] != member.run_id
        or segment["rows"] != member.rows
        or segment["identity"]["intent_id"] != member.episode
        or segment["mu"]["snapshot_sha256"] != member.mu_snapshot_sha256
    ):
        raise ValueError("prepared_source_membership_or_mu_changed")
    source = _prepare_source(
        segment,
        _worker["compiler"],
        _worker["expected_spec"],
        excluded_seeds=_worker["excluded_seeds"],
        gae_lambda=_worker["gae_lambda"],
        public_bindings_verified=reader is None,
    )
    source = dataclasses.replace(source, seconds={**source.seconds, **read_costs,
        "read_verify": read_seconds,
        "source_queue_seconds": began - submitted_at if submitted_at is not None else 0})
    packing = time.perf_counter()
    packed = pack_transport(contiguous_slabs(source)[0])
    # This metadata is part of the source, so transport itself remains unchanged.
    tree, slabs = packed
    return dataclasses.replace(tree, seconds={**source.seconds,
        "source_pack_seconds": time.perf_counter() - packing}), slabs


def prepare_source(
    segment: dict[str, Any],
    compiler: FrameCompiler,
    expected_spec: dict[str, Any],
    *,
    excluded_seeds: frozenset[str] = frozenset(),
    gae_lambda: float = 0.99,
) -> PreparedSource:
    """One verified source read, one decoder call per current row; no boundary frame decode."""
    return _prepare_source(segment, compiler, expected_spec, excluded_seeds=excluded_seeds,
                           gae_lambda=gae_lambda, public_bindings_verified=False)


def _prepare_source(
    segment: dict[str, Any], compiler: FrameCompiler, expected_spec: dict[str, Any], *,
    excluded_seeds: frozenset[str], gae_lambda: float, public_bindings_verified: bool,
) -> PreparedSource:
    began = time.perf_counter()
    validate_training_source(segment, excluded_seeds)
    # FrameCompiler checks the frozen producer against this vocabulary/InputSpec.
    expected_identity = segment["compiler_identity"]
    frames: list[TensorFrame | None] = []
    floors: list[int] = []
    refs: list[RowRef | None] = []
    actions: list[int] = []
    old: list[tuple[float, ...]] = []
    metadata: list[dict[str, Any]] = []
    generation = segment["training_generation"]
    for index, step in enumerate(segment["steps"]):
        value = step["input"]
        if value["input_spec"] != expected_spec:
            raise ValueError("segment_model_input_spec_changed")
        wire = step["behavior"]["compiled_input"]
        # Only the normal Exchange read has already bound these hashes to the
        # original public bytes. Direct/alternate readers still hash the bytes.
        frame = compiler.decode(wire, expected_identity=expected_identity, **(
            {"observation_sha256": wire["observation_sha256"],
             "catalog_sha256": wire["catalog_sha256"]} if public_bindings_verified else
            {"observation": value["observation"]}))
        if not frame.choices:
            raise ValueError("PPO_row_requires_complete_catalog")
        catalog = [choice["choice_id"] for choice in value["catalog"]]
        if len(set(catalog)) != len(catalog) or tuple(catalog) != frame.choices:
            raise ValueError("prepared_catalog_membership_changed")
        frames.append(frame)
        floors.append(step["behavior"]["floor"])
        ref: RowRef = {
            "run_id": segment["run_id"],
            "segment_id": segment["segment_id"],
            "training_generation": generation,
            "row": index,
            "key": f"{segment['run_id']}:{step['step_index']}",
        }
        refs.append(ref)
        lookup = {choice: i for i, choice in enumerate(catalog)}
        action = frame.choices.index(step["directive"]["directive"]["selection"]["action_id"])
        mass = step["sampling"]["probabilities"]
        probabilities = tuple(mass[lookup[choice]] for choice in frame.choices)
        if (
            len(mass) != len(catalog)
            or any(not math.isfinite(p) or p < 0 for p in probabilities)
            or not math.isclose(sum(probabilities), 1, abs_tol=1e-5)
            or probabilities[action] <= 0
        ):
            raise ValueError("invalid_recorded_policy_probability")
        actions.append(action)
        old.append(probabilities)
        metadata.append(
            {
                "generated_at": step.get("generated_at"),
                "generated_timestamp": (
                    datetime.fromisoformat(step["generated_at"]).timestamp()
                    if step.get("generated_at") is not None
                    else None
                ),
                "floor": step["behavior"]["floor"],
                "mu_version": segment["mu"]["version"],
                "mu_updates": segment["mu"]["updates"],
            }
        )
    decoded = time.perf_counter()
    p, targets, advantages = recorded_targets(segment, gae_lambda=gae_lambda)
    return PreparedSource(
        segment["segment_id"],
        segment["run_id"],
        generation,
        tuple(frames),
        tuple(floors),
        tuple(refs),
        p,
        targets,
        advantages,
        tuple(actions),
        tuple(old),
        tuple(metadata),
        {"decode_bind": decoded - began, "lambda_targets": time.perf_counter() - decoded},
        os.getpid(),
        segment["compiler_identity"],
        {
            "segment_id": segment["segment_id"],
            "run_id": segment["run_id"],
            "terminal": segment["terminal"],
            "hp_battles": segment["hp_battles"],
        },
    )


def _bucket(size: int, buckets: tuple[int, ...]) -> int:
    # Oversized complete scenes/catalogs are never truncated. One explicit eager
    # overflow bucket is used for that batch; G1 reports it rather than silently
    # excluding a legal action or switching precision.
    return next((limit for limit in buckets if size <= limit), size)


def _role_metadata(
    frames: list[TensorFrame], key: str, scene_width: int, catalog_width: int, physical_rows: int
) -> tuple[Tensor, Tensor]:
    rows: list[list[int]] = []
    for batch, frame in enumerate(frames):
        reduction = frame.reductions[key]
        unpacked: list[list[int]] = []
        for width, count, indices in reduction.buckets:
            unpacked.extend((indices.reshape(count, width) + batch * scene_width).tolist())
        inverse = reduction.inverse.tolist()
        rows.extend(
            [unpacked[index] for index in inverse]
            + [[] for _ in range(catalog_width - len(frame.choices))]
        )
    rows.extend([[] for _ in range((physical_rows - len(frames)) * catalog_width)])
    width = _bucket(max(map(len, rows), default=0), (1, 2, 4, 8, 16, 32, 64))
    indices = torch.zeros((len(rows), width), dtype=torch.long)
    weights = torch.zeros((len(rows), width), dtype=torch.float32)
    for row, values in enumerate(rows):
        if values:
            indices[row, : len(values)] = torch.tensor(values)
            weights[row, : len(values)] = 1.0 / len(values)
    return indices, weights


PREPARED_LAYOUTS = ("padded-v1", "rows-v2")
ROW_BUCKET_ROWS = 256  # S0-GPU row 8: 256-row buckets
ROW_WIDTH_MULTIPLE = 64  # S0-GPU rows 6-8: bucket widths in multiples of 64


def row_layout(group: PreparedGroup, *, bucket_rows: int = ROW_BUCKET_ROWS,
               width_multiple: int = ROW_WIDTH_MULTIPLE) -> RowLayout:
    """S3 rows-v2 layout from one complete CPU PreparedGroup (S0-GPU 0-level items).

    Re-indexes the group's own tensors only. Real text rows, real scene slots and
    nonzero role terms are kept exactly once; every dropped position is padding
    by construction (checked, never assumed), so means, attention membership and
    loss denominators are unchanged. Raises on any layout it cannot prove.
    """
    if type(bucket_rows) is not int or bucket_rows < 1 or type(width_multiple) is not int \
            or width_multiple < 1:
        raise ValueError("invalid_row_layout_bucket")
    t = group.tensors
    rows = len(group.rows)
    if any(t[key].device.type != "cpu" for key in t):
        raise ValueError("row_layout_requires_host_tensors")
    # Text: only the unique texts that nodes reference; padding text rows are
    # never encoded. Width buckets are powers of two up to the prepared width.
    inverse = t["text_inverse"]
    texts = int(inverse.max()) + 1 if len(inverse) else 0
    lengths = t["text_lengths"][:texts]
    prepared_width = int(t["text_ids"].shape[1])
    if texts and (int(lengths.min()) < 1 or int(lengths.max()) > prepared_width):
        raise ValueError("row_layout_text_length_outside_prepared_width")
    text = _text_layout(lengths, prepared_width)
    joint = group.joint
    node_text = joint.tensors["text_indices"]
    if len(node_text) and int(node_text.max()) >= len(inverse):
        raise ValueError("row_layout_text_term_outside_group")
    reductions = {}
    for key, value in joint.reductions.items():
        parts = [indices for width, _count, indices in value.buckets if width]
        reductions[key] = (torch.cat(parts) if parts else torch.zeros(0, dtype=torch.long),
                           tuple((int(width), int(count)) for width, count, _ in value.buckets),
                           value.inverse)
    # Scene: each real row's slots are its own contiguous node range (prepare_group).
    gather, mask = t["scene_gather"], t["scene_mask"]
    width = int(gather.shape[1])
    slots = (~mask[:rows]).sum(1)
    starts = gather[:rows, 0]
    if bool(((~mask[:rows]).long().cumsum(1) != torch.arange(1, width + 1).clamp(
            max=slots[:, None])).any()):
        raise ValueError("row_layout_requires_left_packed_scene_rows")
    for row in range(rows):
        count = int(slots[row])
        if not torch.equal(gather[row, :count], torch.arange(int(starts[row]),
                                                              int(starts[row]) + count)):
            raise ValueError("row_layout_requires_contiguous_scene_rows")
    if t["scene_lengths"][:rows, 0].ne(slots.float()).any():
        raise ValueError("row_layout_scene_lengths_changed")
    scene = _scene_layout(slots, starts, width, bucket_rows, width_multiple)
    catalog = int(t["action_types"].shape[1]) if group.policy else 0
    roles: dict[str, list[list[Tensor]]] = {"source": [], "target": []}
    present: dict[str, list[Tensor]] = {"source": [], "target": []}
    for members, (_count, bucket_width, _longest, _slots) in zip(
            scene.members, scene.buckets, strict=True):
        counts = slots[members]
        if group.policy:
            candidates = (members[:, None] * catalog + torch.arange(catalog)).reshape(-1)
            owner = torch.arange(len(members)).repeat_interleave(catalog)[:, None]
            for key in ("source", "target"):
                indices = t[key + "_indices"][candidates]
                weights = t[key + "_weights"][candidates]
                term_rows, term_slots = indices // width, indices % width
                live = weights != 0
                own_slot = (term_rows == members.repeat_interleave(catalog)[:, None]) & (
                    term_slots < counts.repeat_interleave(catalog)[:, None])
                if bool((live & ~own_slot).any()):
                    raise ValueError("row_layout_role_term_outside_its_real_scene_row")
                candidate, column = live.nonzero(as_tuple=True)
                slot = owner.expand_as(indices)[candidate, column] * bucket_width + term_slots[
                    candidate, column]
                roles[key].append([candidate, slot, weights[candidate, column]])
                present[key].append(weights.sum(1) > 0)
    role_layout: dict[str, tuple[Tensor, Tensor, Tensor, tuple[int, ...]]] = {}
    role_flags: dict[str, Tensor] = {}
    if group.policy:
        for key in ("source", "target"):
            role_layout[key] = (torch.cat([part[0] for part in roles[key]]),
                                torch.cat([part[1] for part in roles[key]]),
                                torch.cat([part[2] for part in roles[key]]),
                                tuple(len(part[0]) for part in roles[key]))
            role_flags[key] = torch.cat(present[key])
    return RowLayout(
        **text, node_text_indices=inverse[node_text] if len(node_text) else node_text.clone(),
        reductions=reductions, **scene.fields(), roles=role_layout, role_present=role_flags)


def _text_layout(lengths: Tensor, prepared_width: int) -> dict[str, Any]:
    """rows-v2 text fields from the real unique text lengths (one rule: `row_layout`
    and `join_forward_inputs`)."""
    texts = len(lengths)
    order = torch.argsort(lengths, stable=True)
    ordered = lengths[order]
    edges = [edge for edge in (8, 16, 32, 64, 128, 256, 512) if edge < prepared_width]
    text_buckets, low = [], 0
    for edge in [*edges, prepared_width]:
        high = int((ordered <= edge).sum())
        if high > low:
            text_buckets.append((high - low, edge))
        low = high
    text_slots = torch.empty_like(order)
    text_slots[order] = torch.arange(texts)
    real = lengths.long()
    starts_text = torch.repeat_interleave(real.cumsum(0) - real, real)
    text_tokens = (torch.repeat_interleave(torch.arange(texts) * prepared_width, real)
                   + torch.arange(int(real.sum())) - starts_text)
    text_offsets = torch.cat((real.new_zeros(1), real.cumsum(0))).to(torch.int32)
    return {"text_buckets": tuple(text_buckets), "text_order": order,
            "text_inverse": text_slots, "text_tokens": text_tokens,
            "text_offsets": text_offsets, "text_longest": int(real.max()) if texts else 0}


@dataclasses.dataclass(frozen=True)
class _SceneLayout:
    buckets: tuple[tuple[int, int, int, int], ...]
    members: tuple[Tensor, ...]  # group rows of each bucket, in bucket order
    row_order: Tensor
    inverse: Tensor
    nodes: Tensor
    positions: Tensor
    lengths: Tensor
    offsets: Tensor

    def fields(self) -> dict[str, Any]:
        return {"scene_buckets": self.buckets, "scene_rows": self.row_order,
                "scene_inverse": self.inverse, "scene_nodes": self.nodes,
                "scene_positions": self.positions, "scene_lengths": self.lengths,
                "scene_offsets": self.offsets}


def _scene_layout(slots: Tensor, starts: Tensor, width: int, bucket_rows: int,
                  width_multiple: int) -> _SceneLayout:
    """Rows sorted by real scene length, cut into buckets (one rule: `row_layout`
    and `join_forward_inputs`). `starts` is each row's first node."""
    rows = len(slots)
    row_order = torch.argsort(slots, stable=True)
    buckets, members_list, nodes, positions, offsets = [], [], [], [], []
    for first in range(0, rows, bucket_rows):
        members = row_order[first:first + bucket_rows]
        counts = slots[members]
        longest = int(counts.max())
        bucket_width = min(width, -(-longest // width_multiple) * width_multiple)
        local = torch.repeat_interleave(torch.arange(len(members)), counts)
        within = torch.arange(int(counts.sum())) - torch.repeat_interleave(
            counts.cumsum(0) - counts, counts)
        nodes.append(torch.repeat_interleave(starts[members], counts) + within)
        positions.append(local * bucket_width + within)
        offsets.append(torch.cat((counts.new_zeros(1), counts.cumsum(0))).to(torch.int32))
        buckets.append((len(members), bucket_width, longest, int(counts.sum())))
        members_list.append(members)
    inverse = torch.empty_like(row_order)
    inverse[row_order] = torch.arange(rows)
    return _SceneLayout(tuple(buckets), tuple(members_list), row_order, inverse,
                        torch.cat(nodes), torch.cat(positions), slots[row_order],
                        torch.cat(offsets))


def prepare_group(
    frames: list[TensorFrame],
    rows: tuple[int, ...],
    policy: bool,
    floors: Tensor,
    *,
    deduplicate_text: bool,
    layout: str = "padded-v1",
    bucket_rows: int = ROW_BUCKET_ROWS,
) -> PreparedGroup:
    """CPU tensor-only forward inputs, including trainable-text occurrence mapping."""
    scene_width = _bucket(
        max(frame.entity_count for frame in frames), (32, 64, 128, 256, 512, 1024)
    )
    physical_rows = _bucket(len(frames), (1, 2, 4, 8, 16, 32, 64))
    catalog_width = (
        _bucket(max(len(frame.choices) for frame in frames), (8, 32, 64, 128, 256)) if policy else 0
    )
    combined = joint_nodes(frames)
    text_ids, text_numbers, lengths = text_batch(frames)
    if deduplicate_text:
        keys = tuple(key for frame in frames for key in frame.text_keys)
        unique: dict[bytes, int] = {}
        selection: list[int] = []
        inverse: list[int] = []
        for i, text_key in enumerate(keys):
            if text_key not in unique:
                unique[text_key] = len(selection)
                selection.append(i)
            inverse.append(unique[text_key])
        selection_tensor = torch.tensor(selection, dtype=torch.long)
        text_ids = text_ids.index_select(0, selection_tensor)
        text_numbers = text_numbers.index_select(0, selection_tensor)
        lengths = lengths.index_select(0, selection_tensor)
    else:
        inverse = list(range(len(lengths)))
    # Padding changes compute shapes, never membership or actual text lengths.
    text_width = _bucket(text_ids.shape[1], (8, 16, 32, 64, 128, 256, 512))
    text_ids = torch.nn.functional.pad(text_ids, (0, text_width - text_ids.shape[1]))
    text_numbers = torch.nn.functional.pad(
        text_numbers, (0, 0, 0, text_width - text_numbers.shape[1])
    )
    text_rows = _bucket(len(lengths), (32, 128, 512, 2048)) if len(lengths) else 0
    if text_rows > len(lengths):
        text_ids = torch.nn.functional.pad(text_ids, (0, 0, 0, text_rows - len(lengths)))
        text_numbers = torch.nn.functional.pad(
            text_numbers, (0, 0, 0, 0, 0, text_rows - len(lengths))
        )
        lengths = torch.nn.functional.pad(lengths, (0, text_rows - len(lengths)), value=1)
    node_count = sum(frame.entity_count for frame in frames)
    gather = torch.full((physical_rows, scene_width), node_count, dtype=torch.long)
    scene_mask = torch.ones_like(gather, dtype=torch.bool)
    cursor = 0
    for i, frame in enumerate(frames):
        gather[i, : frame.entity_count] = torch.arange(cursor, cursor + frame.entity_count)
        scene_mask[i, : frame.entity_count] = False
        cursor += frame.entity_count
    # Padding rows have one finite, zero node and are sliced out before loss.
    # They are never dataset members or recorded/charged as native actions.
    scene_mask[len(frames) :, 0] = False
    physical_floors = torch.nn.functional.pad(floors, (0, physical_rows - len(frames)))
    data = {
        "text_ids": text_ids,
        "text_numbers": text_numbers,
        "text_lengths": lengths,
        "text_inverse": torch.tensor(inverse, dtype=torch.long),
        "scene_gather": gather,
        "scene_mask": scene_mask,
        "scene_lengths": torch.tensor(
            [f.entity_count for f in frames] + [1] * (physical_rows - len(frames)),
            dtype=torch.float32,
        )[:, None],
        "outcome_valid": outcome_mask(physical_floors),
    }
    if policy:
        action_types = torch.zeros((physical_rows, catalog_width), dtype=torch.long)
        catalog_mask = torch.ones_like(action_types, dtype=torch.bool)
        for i, frame in enumerate(frames):
            count = len(frame.choices)
            action_types[i, :count] = frame.tensors["actions"]
            catalog_mask[i, :count] = False
        data.update(action_types=action_types, catalog_mask=catalog_mask)
        for key in ("source", "target"):
            indices, weights = _role_metadata(
                frames, key, scene_width, catalog_width, physical_rows
            )
            data[key + "_indices"] = indices
            data[key + "_weights"] = weights
    group = PreparedGroup(rows, policy, combined, data)
    if layout == "padded-v1":
        return group
    if layout != "rows-v2":
        raise ValueError("unsupported_prepared_layout")
    return dataclasses.replace(group, layout=row_layout(group, bucket_rows=bucket_rows))


def _merged_reduction(parts: list[tuple[Reduction, dict[str, int]]], key: str,
                      totals: dict[str, int]) -> Reduction:
    """`joint_nodes` reduction order from already joined sub-group reductions."""
    buckets: dict[int, list[tuple[int, Tensor, Tensor]]] = {}
    for value, offset in parts:
        owners_packed = value.inverse.argsort()
        cursor = 0
        for width, count, indices in value.buckets:
            owners = owners_packed[cursor:cursor + count] + offset["refs"]
            cursor += count
            if key == "numbers":  # [numbers..., missing...] of the joined graph
                present = offset["numbers_total"]
                indices = torch.where(indices < present, indices + offset["numbers"],
                                      indices - present + totals["numbers"] + offset["missing"])
            else:
                indices = indices + offset[key]
            buckets.setdefault(width, []).append((count, indices, owners))
    combined: list[tuple[int, int, Tensor]] = []
    order: list[Tensor] = []
    for width, items in buckets.items():
        combined.append((width, sum(item[0] for item in items),
                         torch.cat([item[1] for item in items])))
        order.extend(item[2] for item in items)
    inverse = torch.empty(totals["refs"], dtype=torch.long)
    if order:
        inverse[torch.cat(order)] = torch.arange(totals["refs"])
    return Reduction(tuple(combined), inverse)


def concatenate_groups(groups: Sequence[PreparedGroup], *,
                       rows: tuple[int, ...] | None = None) -> PreparedGroup:
    """One PreparedGroup from already prepared sub-groups, rows in sub-group order.

    The C-SCALE merge for a forward over several prepared parts (lead 11-09 (a);
    rl-inference-3 `_forward_parts`). Only real rows, texts, nodes and role terms
    are copied and re-offset; every padded size is re-bucketed over the union, so
    the result equals `prepare_group` of all frames at once, except that a text
    repeated in two sub-groups keeps one row per sub-group (an encoded duplicate,
    same function). A rows-v2 result gets its layout from `row_layout`. `rows`
    defaults to the sub-groups' rows in order (rl-inference-3 parts each count
    from 0, so pass `range(total)`). Output row i is part row i in order.
    """
    if not groups:
        raise ValueError("concatenate_requires_groups")
    policy = groups[0].policy
    has_layout = groups[0].layout is not None
    if any(g.policy != policy or (g.layout is not None) != has_layout for g in groups):
        raise ValueError("concatenate_requires_one_policy_and_layout")
    counts = [len(g.rows) for g in groups]
    total = sum(counts)
    rows = tuple(r for g in groups for r in g.rows) if rows is None else tuple(rows)
    if len(rows) != total:
        raise ValueError("concatenate_rows_mismatch")
    ts = [g.tensors for g in groups]
    if any(value.device.type != "cpu" for t in ts for value in t.values()):
        raise ValueError("concatenate_requires_host_tensors")
    entities = [t["scene_lengths"][:n, 0].long() for t, n in zip(ts, counts, strict=True)]
    entity = torch.cat(entities)
    scene_width = _bucket(int(entity.max()), (32, 64, 128, 256, 512, 1024))
    physical_rows = _bucket(total, (1, 2, 4, 8, 16, 32, 64))
    # Text: each sub-group's real (unique) text rows, then re-padded once.
    texts = [int(t["text_inverse"].max()) + 1 if len(t["text_inverse"]) else 0 for t in ts]
    text_width = max(int(t["text_ids"].shape[1]) for t in ts)
    ids = torch.cat([F.pad(t["text_ids"][:k], (0, text_width - t["text_ids"].shape[1]))
                     for t, k in zip(ts, texts, strict=True)])
    numbers = torch.cat([F.pad(t["text_numbers"][:k],
                               (0, 0, 0, text_width - t["text_numbers"].shape[1]))
                         for t, k in zip(ts, texts, strict=True)])
    lengths = torch.cat([t["text_lengths"][:k] for t, k in zip(ts, texts, strict=True)])
    text_offsets = [sum(texts[:i]) for i in range(len(ts))]
    inverse = torch.cat([t["text_inverse"] + o for t, o in zip(ts, text_offsets, strict=True)])
    text_rows = _bucket(len(lengths), (32, 128, 512, 2048)) if len(lengths) else 0
    if text_rows > len(lengths):
        ids = F.pad(ids, (0, 0, 0, text_rows - len(lengths)))
        numbers = F.pad(numbers, (0, 0, 0, 0, 0, text_rows - len(lengths)))
        lengths = F.pad(lengths, (0, text_rows - len(lengths)), value=1)
    node_count = int(entity.sum())
    gather = torch.full((physical_rows, scene_width), node_count, dtype=torch.long)
    scene_mask = torch.ones_like(gather, dtype=torch.bool)
    starts = entity.cumsum(0) - entity
    for i in range(total):
        count = int(entity[i])
        gather[i, :count] = torch.arange(int(starts[i]), int(starts[i]) + count)
        scene_mask[i, :count] = False
    scene_mask[total:, 0] = False
    data = {
        "text_ids": ids, "text_numbers": numbers, "text_lengths": lengths,
        "text_inverse": inverse, "scene_gather": gather, "scene_mask": scene_mask,
        "scene_lengths": torch.cat((entity.float(), torch.ones(physical_rows - total)))[:, None],
        "outcome_valid": torch.cat([t["outcome_valid"][:n] for t, n in zip(ts, counts, strict=True)]
                                   + [outcome_mask(torch.zeros(physical_rows - total,
                                                               dtype=torch.long))]),
    }
    if policy:
        catalog = max(int(t["action_types"].shape[1]) for t in ts)
        data["action_types"] = F.pad(torch.cat([
            F.pad(t["action_types"][:n], (0, catalog - t["action_types"].shape[1]))
            for t, n in zip(ts, counts, strict=True)]), (0, 0, 0, physical_rows - total))
        data["catalog_mask"] = F.pad(torch.cat([
            F.pad(t["catalog_mask"][:n], (0, catalog - t["catalog_mask"].shape[1]), value=True)
            for t, n in zip(ts, counts, strict=True)]), (0, 0, 0, physical_rows - total),
            value=True)
        for key in ("source", "target"):
            width = max(int(t[key + "_indices"].shape[1]) for t in ts)
            indices = torch.zeros((physical_rows * catalog, width), dtype=torch.long)
            weights = torch.zeros((physical_rows * catalog, width), dtype=torch.float32)
            first = 0
            for t, n in zip(ts, counts, strict=True):
                local_catalog = int(t["action_types"].shape[1])
                local_width = int(t["scene_gather"].shape[1])
                part_indices = t[key + "_indices"][:n * local_catalog]
                part_weights = t[key + "_weights"][:n * local_catalog]
                columns = part_indices.shape[1]
                moved = (part_indices // local_width + first) * scene_width \
                    + part_indices % local_width
                moved = torch.where(part_weights != 0, moved, torch.zeros_like(moved))
                target = ((first + torch.arange(n))[:, None] * catalog
                          + torch.arange(local_catalog)).reshape(-1)
                indices[target, :columns] = moved
                weights[target, :columns] = part_weights
                first += n
            data[key + "_indices"] = indices
            data[key + "_weights"] = weights
    # Joint node graph: same tensors and reduction order as `joint_nodes`.
    names = ("identities", "identity_fields", "kinds", "positions", "number_fields",
             "numbers", "missing_fields", "text_fields")
    joints = [g.joint for g in groups]
    tensors = {key: torch.cat([j.tensors[key] for j in joints]) for key in names}
    offsets, cumulative = [], dict.fromkeys(("ids", "numbers", "missing", "text", "refs"), 0)
    for j, n_refs in zip(joints, (int(e.sum()) for e in entities), strict=True):
        offsets.append({**cumulative, "numbers_total": j.tensors["numbers"].numel()})
        cumulative["ids"] += j.tensors["identities"].numel()
        cumulative["numbers"] += j.tensors["numbers"].numel()
        cumulative["missing"] += j.tensors["missing_fields"].numel()
        cumulative["text"] += j.tensors["text_fields"].numel()
        cumulative["refs"] += n_refs
    vector_offsets = [0]
    for t in ts:
        vector_offsets.append(vector_offsets[-1] + len(t["text_inverse"]))
    tensors["text_indices"] = torch.cat([j.tensors["text_indices"] + o
                                         for j, o in zip(joints, vector_offsets, strict=False)])
    reductions = {key: _merged_reduction([(j.reductions[key], o)
                                          for j, o in zip(joints, offsets, strict=True)],
                                         key, cumulative)
                  for key in ("ids", "numbers", "text", "refs")}
    group = PreparedGroup(rows, policy, JointNodes(tensors, reductions), data)
    return dataclasses.replace(group, layout=row_layout(group)) if has_layout else group


# C-SCALE step 3 (inference transfer, lead 11-09): only what `forward_rows` reads,
# int32 indices, one byte arena per job, one H2D copy, sub-groups joined on device.
FORWARD_TENSORS = ("text_ids", "text_numbers", "text_lengths", "outcome_valid",
                   "action_types", "catalog_mask")
FORWARD_JOINT = ("identities", "identity_fields", "kinds", "positions", "number_fields",
                 "numbers", "missing_fields", "text_fields")
FORWARD_REDUCTIONS = ("ids", "numbers", "text", "refs")
ARENA_ALIGN = 64
_INT32 = (-(2 ** 31), 2 ** 31 - 1)


def _index_cast(value: Tensor, dtype: torch.dtype, name: str) -> Tensor:
    if value.dtype != torch.int64 or dtype == torch.int64:
        return value
    if value.numel():
        low, high = torch.aminmax(value)
        if int(low) < _INT32[0] or int(high) > _INT32[1]:
            raise ValueError(f"forward_index_exceeds_int32:{name}")
    return value.to(dtype)


def is_forward_inputs(group: PreparedGroup) -> bool:
    """A `forward_inputs` group: rows-v2 layout, no padded-v1 / loss-only tensors."""
    return group.layout is not None and "scene_gather" not in group.tensors


def forward_inputs(group: PreparedGroup, *, index_dtype: torch.dtype = torch.int32
                   ) -> PreparedGroup:
    """The rows-v2 group `forward_rows` reads, nothing else; integer indices in
    `index_dtype` (int32: checked to fit, raises otherwise; `text_lengths` stays).

    Drops the padded-v1 view (scene gather/mask, padded role indices, text
    occurrence inverse, bucketed joint reductions): the rows-v2 forward never
    reads them, so outputs are the same values (CPU fp32 bitwise). Not a training
    loss input: the learner keeps whole groups (`stage_batch`).
    """
    if index_dtype not in (torch.int32, torch.int64):
        raise ValueError("forward_index_dtype_must_be_int32_or_int64")
    if group.layout is None:
        raise ValueError("forward_inputs_require_rows_v2_layout")
    if is_forward_inputs(group):
        return group
    layout = group.layout

    def cast(value: Any, name: str) -> Any:
        return _index_cast(value, index_dtype, name) if isinstance(value, Tensor) else value

    tensors = {key: group.tensors[key] if key == "text_lengths" else cast(group.tensors[key], key)
               for key in FORWARD_TENSORS if key in group.tensors}
    joint = JointNodes({key: cast(group.joint.tensors[key], key) for key in FORWARD_JOINT}, {})
    fields = {}
    for item in dataclasses.fields(layout):
        value = getattr(layout, item.name)
        if item.name == "reductions":
            value = {key: tuple(cast(part, f"{key}.{i}") for i, part in enumerate(value[key]))
                     for key in FORWARD_REDUCTIONS}
        elif item.name == "roles":
            value = {key: tuple(cast(part, f"roles.{key}.{i}") for i, part in enumerate(parts))
                     for key, parts in value.items()}
        elif isinstance(value, Tensor):
            value = cast(value, item.name)
        fields[item.name] = value
    return PreparedGroup(group.rows, group.policy, joint, tensors, RowLayout(**fields))


@dataclasses.dataclass(frozen=True)
class _ArenaSlot:
    offset: int  # bytes from the part's start
    dtype: torch.dtype
    shape: tuple[int, ...]


@dataclasses.dataclass(frozen=True)
class ForwardPart:
    """Where one prepared group's forward inputs sit in a byte arena. Picklable and
    small (no tensors): a prepare process sends this instead of the group."""
    start: int
    end: int
    tree: Any  # the forward-inputs PreparedGroup, tensors replaced by _ArenaSlot


def _arena_plan(value: Any) -> tuple[Any, list[tuple[int, Tensor]], int]:
    writes: list[tuple[int, Tensor]] = []
    cursor = 0

    def place(node: Any) -> Any:
        nonlocal cursor
        if not isinstance(node, Tensor):
            return node
        if node.device.type != "cpu":
            raise ValueError("forward_arena_requires_host_tensors")
        cursor = -(-cursor // ARENA_ALIGN) * ARENA_ALIGN
        writes.append((cursor, node))
        slot = _ArenaSlot(cursor, node.dtype, tuple(node.shape))
        cursor += node.numel() * node.element_size()
        return slot

    tree = _map_structure(value, place)
    return tree, writes, cursor


def _arena_view(base: Tensor, slot: _ArenaSlot, start: int) -> Tensor:
    size = math.prod(slot.shape) * torch.empty((), dtype=slot.dtype).element_size()
    return base[start + slot.offset:start + slot.offset + size].view(slot.dtype).view(slot.shape)


def _arena_tree(base: Tensor, tree: Any, start: int) -> Any:
    return _map_structure(tree, lambda node: _arena_view(base, node, start)
                          if isinstance(node, _ArenaSlot) else node)


def allocate_forward_arena(nbytes: int, *, shared: bool = False, pinned: bool = False) -> Tensor:
    """A host byte arena for forward inputs. `shared`: torch shared memory, so the
    handle can be sent once to prepare processes (each writes its part in place);
    the device process then pins it with `pin_host_arena`. `pinned`: page-locked
    here (one process; not combinable with `shared`)."""
    if shared and pinned:
        raise ValueError("pin_a_shared_arena_with_pin_host_arena")
    arena = torch.empty(int(nbytes), dtype=torch.uint8, pin_memory=pinned)
    return arena.share_memory_() if shared else arena


def pin_host_arena(arena: Tensor) -> Callable[[], None]:
    """Page-lock an existing host arena in this process (cudaHostRegister), so its
    H2D copy is one asynchronous DMA. Raises if CUDA refuses. Returns the unpin."""
    cudart = torch.cuda.cudart()
    pointer, size = arena.data_ptr(), arena.numel() * arena.element_size()
    error = cudart.cudaHostRegister(pointer, size, 0)
    if int(error) != 0:
        raise RuntimeError(f"cudaHostRegister_failed:{int(error)}")
    return lambda: cudart.cudaHostUnregister(pointer)


def forward_arena_bytes(group: PreparedGroup, *, index_dtype: torch.dtype = torch.int32) -> int:
    return _arena_plan(forward_inputs(group, index_dtype=index_dtype))[2]


def write_forward_inputs(arena: Tensor, offset: int, group: PreparedGroup, *,
                         index_dtype: torch.dtype = torch.int32) -> ForwardPart:
    """Write `forward_inputs(group)` into `arena` at `offset` (rounded up to 64 B).

    Raises `forward_arena_overflow` if it does not fit (never truncates). The
    caller owns slot assignment and must not overwrite a part before the forward
    that read it has returned its outputs to the host.
    """
    if arena.dtype != torch.uint8 or arena.dim() != 1 or arena.device.type != "cpu":
        raise ValueError("forward_arena_must_be_a_host_uint8_vector")
    tree, writes, size = _arena_plan(forward_inputs(group, index_dtype=index_dtype))
    start = -(-int(offset) // ARENA_ALIGN) * ARENA_ALIGN
    if start + size > arena.numel():
        raise ValueError("forward_arena_overflow")
    for local, tensor in writes:
        slot = _ArenaSlot(local, tensor.dtype, tuple(tensor.shape))
        _arena_view(arena, slot, start).copy_(tensor)
    return ForwardPart(start, start + size, tree)


def read_forward_part(arena: Tensor, part: ForwardPart) -> PreparedGroup:
    """The part as views of `arena` (no copy)."""
    return cast(PreparedGroup, _arena_tree(arena, part.tree, part.start))


def _copy_once(arena: Tensor, parts: Sequence[ForwardPart], device: torch.device
               ) -> list[PreparedGroup]:
    low, high = min(p.start for p in parts), max(p.end for p in parts)
    staged = arena[low:high].to(device, non_blocking=True)
    return [cast(PreparedGroup, _arena_tree(staged, p.tree, p.start - low)) for p in parts]


def stage_group(group: PreparedGroup, device: torch.device, *,
                index_dtype: torch.dtype = torch.int32) -> PreparedGroup:
    """One H2D copy of a host group's forward inputs (pinned, non-blocking)."""
    if device.type == "cpu":
        return group
    tree, writes, size = _arena_plan(forward_inputs(group, index_dtype=index_dtype))
    arena = torch.empty(size, dtype=torch.uint8, pin_memory=True)
    for local, tensor in writes:
        _arena_view(arena, _ArenaSlot(local, tensor.dtype, tuple(tensor.shape)), 0).copy_(tensor)
    return _copy_once(arena, [ForwardPart(0, size, tree)], device)[0]


def stage_forward_parts(arena: Tensor, parts: Sequence[ForwardPart], device: torch.device, *,
                        rows: tuple[int, ...] | None = None) -> PreparedGroup:
    """One forward group from parts written in `arena`, planned here.

    Equals `forward_inputs(concatenate_groups(...))`. A device process should
    take the planned path instead (`plan_forward_join` in a prepare/joiner
    process, then `stage_joined` here): this function plans on this thread and
    copies the plan separately.
    """
    if not parts:
        raise ValueError("stage_requires_parts")
    if len(parts) == 1:
        group = read_forward_part(arena, parts[0]) if device.type == "cpu" else _copy_once(
            arena, parts, device)[0]
        return group if rows is None else dataclasses.replace(group, rows=tuple(rows))
    tensors, plan = _plan_join(arena, parts, rows)
    buffer = torch.empty(_plan_bytes(tensors), dtype=torch.uint8,
                         pin_memory=device.type == "cuda")
    return stage_joined(arena, _write_plan(buffer, 0, tensors, plan, in_arena=False), device,
                        plan_arena=buffer)


@dataclasses.dataclass(frozen=True)
class _Gather:
    """One dtype's joined bulk tensors: runs over the parts' span (`g<i>.*` tables)."""
    dtype: torch.dtype
    total: int  # elements
    mode: Literal["copy", "add", "where"]
    zeros: bool
    outputs: tuple[tuple[str, int, tuple[int, ...]], ...]  # name, element offset, shape


@dataclasses.dataclass(frozen=True)
class JoinPlan:
    """A job's parts joined ahead of the device (`plan_forward_join`). Picklable and
    small: byte spans, a flat slot table of the plan tensors written next to the
    parts, and the few Python values of the layout."""
    rows: tuple[int, ...]
    policy: bool
    low: int  # the parts' byte span in the arena
    high: int
    plan_start: int  # the plan tensors' byte span (arena, or `plan_arena`)
    plan_end: int
    in_arena: bool
    slots: tuple[tuple[str, int, int, torch.dtype, tuple[int, ...]], ...]  # name, offset, bytes
    gathers: tuple[_Gather, ...]
    meta: dict[str, Any]


def plan_forward_join(arena: Tensor, parts: Sequence[ForwardPart], offset: int, *,
                      rows: tuple[int, ...] | None = None,
                      plan_arena: Tensor | None = None) -> JoinPlan:
    """Plan the join of `parts` (all host index maps) and write the plan tensors at
    `offset` (rounded up to 64 B) of `plan_arena`, default `arena`.

    For a prepare/joiner process: the device process then runs `stage_joined`
    (one H2D copy of parts plus plan, a fixed few device ops, no per-part Python).
    Put the plan next to the parts (same job slot) so one copy covers both.
    Raises `forward_arena_overflow` if it does not fit (never truncates).
    """
    if not parts:
        raise ValueError("join_requires_parts")
    tensors, plan = _plan_join(arena, parts, rows)
    target = arena if plan_arena is None else plan_arena
    return _write_plan(target, offset, tensors, plan, in_arena=plan_arena is None)


def stage_joined(arena: Tensor, plan: JoinPlan, device: torch.device, *,
                 plan_arena: Tensor | None = None) -> PreparedGroup:
    """The joined forward group on `device`: one non-blocking H2D copy (two if the
    plan is in a separate `plan_arena`), then gathers on the device.

    With a pinned arena the copy is asynchronous: keep parts and plan unchanged
    until this forward's outputs have reached the host.
    """
    if plan.in_arena != (plan_arena is None):
        raise ValueError("stage_joined_plan_arena_mismatch")
    if plan.in_arena:
        low, high = min(plan.low, plan.plan_start), max(plan.high, plan.plan_end)
        staged = arena[low:high]
        staged = staged if device.type == "cpu" else staged.to(device, non_blocking=True)
        span = staged[plan.low - low:plan.high - low]
        planned = staged[plan.plan_start - low:plan.plan_end - low]
    else:
        assert plan_arena is not None
        span, planned = arena[plan.low:plan.high], plan_arena[plan.plan_start:plan.plan_end]
        if device.type != "cpu":
            span = span.to(device, non_blocking=True)
            planned = planned.to(device, non_blocking=True)
    view = {name: planned[start:start + size].view(dtype).view(shape)
            for name, start, size, dtype, shape in plan.slots}
    out: dict[str, Tensor] = {}
    for i, gather in enumerate(plan.gathers):
        if gather.total:
            length = view[f"g{i}.length"]

            def spread(name: str, length: Tensor = length, total: int = gather.total) -> Tensor:
                return torch.repeat_interleave(view[name], length, output_size=total)

            source = span[:span.numel() // gather.dtype.itemsize * gather.dtype.itemsize]
            index = torch.arange(gather.total, device=span.device) + spread(f"g{i}.delta")
            values = source.view(gather.dtype).index_select(0, index)
            if gather.zeros:
                values = values.masked_fill(spread(f"g{i}.zero"), 0)
            if gather.mode == "add":
                values = values + spread(f"g{i}.lo")
            elif gather.mode == "where":
                values = values + torch.where(values < spread(f"g{i}.thr"), spread(f"g{i}.lo"),
                                              spread(f"g{i}.hi"))
        else:
            values = torch.empty(0, dtype=gather.dtype, device=span.device)
        for name, start, shape in gather.outputs:
            out[name] = values[start:start + math.prod(shape)].view(shape)
    meta, policy = plan.meta, plan.policy
    tensors = {"text_ids": out["text_ids"], "text_numbers": out["text_numbers"],
               "text_lengths": view["lengths"], "outcome_valid": view["outcome_valid"]}
    roles, role_present = {}, {}
    if policy:
        tensors.update(action_types=view["action_types"], catalog_mask=view["catalog_mask"])
        for key in ("source", "target"):
            roles[key] = (view[f"roles.{key}.candidate"], view[f"roles.{key}.slot"],
                          out[f"weights.{key}"], meta["role_terms"][key])
            role_present[key] = view[f"present.{key}"]
    joint = JointNodes({key: out[f"joint.{key}"] for key in FORWARD_JOINT}, {})
    reductions = {key: (out[f"reduction.{key}"], meta["reduction_buckets"][key],
                        view[f"inverse.{key}"]) for key in FORWARD_REDUCTIONS}
    layout = RowLayout(
        text_buckets=meta["text_buckets"], text_order=view["text.text_order"],
        text_inverse=view["text.text_inverse"], node_text_indices=out["node_text"],
        reductions=reductions, scene_buckets=meta["scene_buckets"],
        **{name: view[f"scene.{name}"] for name in _SCENE_TENSORS},
        roles=roles, role_present=role_present, text_tokens=view["text.text_tokens"],
        text_offsets=view["text.text_offsets"], text_longest=meta["text_longest"])
    return PreparedGroup(plan.rows, policy, joint, tensors, layout)


def join_forward_inputs(parts: Sequence[PreparedGroup], *,
                        rows: tuple[int, ...] | None = None) -> PreparedGroup:
    """`forward_inputs(concatenate_groups(groups))` from each group's forward inputs
    (host, via a scratch arena; the planned path on the CPU)."""
    if not parts:
        raise ValueError("join_requires_parts")
    _check_join(parts)
    arena = allocate_forward_arena(sum(forward_arena_bytes(p) + ARENA_ALIGN for p in parts))
    written, cursor = [], 0
    for part in parts:
        written.append(write_forward_inputs(arena, cursor, part))
        cursor = written[-1].end
    tensors, plan = _plan_join(arena, written, rows)
    buffer = torch.empty(_plan_bytes(tensors), dtype=torch.uint8)
    return stage_joined(arena, _write_plan(buffer, 0, tensors, plan, in_arena=False),
                        torch.device("cpu"), plan_arena=buffer)


_SCENE_TENSORS = ("scene_rows", "scene_inverse", "scene_nodes", "scene_positions",
                  "scene_lengths", "scene_offsets")


def _check_join(parts: Sequence[PreparedGroup]) -> torch.dtype:
    policy = parts[0].policy
    if any(p.policy != policy or not is_forward_inputs(p) for p in parts):
        raise ValueError("join_requires_forward_inputs_of_one_policy")
    index = cast(RowLayout, parts[0].layout).scene_rows.dtype
    if any(cast(RowLayout, p.layout).scene_rows.dtype != index for p in parts):
        raise ValueError("join_requires_one_index_dtype")
    return index


def _plan_bytes(tensors: dict[str, Tensor]) -> int:
    cursor = 0
    for tensor in tensors.values():
        cursor = -(-cursor // ARENA_ALIGN) * ARENA_ALIGN + tensor.numel() * tensor.element_size()
    return max(cursor, 1)


def _write_plan(target: Tensor, offset: int, tensors: dict[str, Tensor], plan: JoinPlan, *,
                in_arena: bool) -> JoinPlan:
    if target.dtype != torch.uint8 or target.dim() != 1 or target.device.type != "cpu":
        raise ValueError("forward_arena_must_be_a_host_uint8_vector")
    start = -(-int(offset) // ARENA_ALIGN) * ARENA_ALIGN
    end = start + _plan_bytes(tensors)
    if end > target.numel():
        raise ValueError("forward_arena_overflow")
    slots, cursor = [], 0
    for name, tensor in tensors.items():
        cursor = -(-cursor // ARENA_ALIGN) * ARENA_ALIGN
        size = tensor.numel() * tensor.element_size()
        target[start + cursor:start + cursor + size].view(tensor.dtype).view(
            tuple(tensor.shape)).copy_(tensor)
        slots.append((name, cursor, size, tensor.dtype, tuple(tensor.shape)))
        cursor += size
    return dataclasses.replace(plan, plan_start=start, plan_end=end, in_arena=in_arena,
                               slots=tuple(slots))


class _Runs:
    """Joined bulk tensors as element runs over the parts' byte span, one table per
    dtype: out = span[run start ...] (+ per-run add; `where`: two adds split at a
    per-run threshold; `zero` runs are padding)."""

    def __init__(self) -> None:
        self.groups: dict[torch.dtype, dict[str, Any]] = {}

    def add(self, name: str, dtype: torch.dtype, shape: tuple[int, ...], src: Tensor,
            length: Tensor, *, lo: Tensor | None = None, hi: Tensor | None = None,
            thr: Tensor | None = None, zero: Tensor | None = None) -> None:
        group = self.groups.setdefault(dtype, {
            "src": [], "length": [], "lo": [], "hi": [], "thr": [], "zero": [],
            "outputs": [], "total": 0, "add": False, "where": False, "zeros": False})
        numel = math.prod(shape)
        if int(length.sum()) != numel:
            raise ValueError(f"join_runs_mismatch:{name}")
        group["outputs"].append((name, group["total"], tuple(shape)))
        group["total"] += numel
        runs = len(length)
        low = lo if lo is not None else torch.zeros(runs, dtype=torch.long)
        group["src"].append(src)
        group["length"].append(length)
        group["lo"].append(low)
        group["hi"].append(hi if hi is not None else low)
        integer = not dtype.is_floating_point and dtype != torch.bool
        group["thr"].append(thr if thr is not None else torch.full(
            (runs,), torch.iinfo(dtype).max if integer else 0, dtype=torch.long))
        group["zero"].append(zero if zero is not None else torch.zeros(runs, dtype=torch.bool))
        group["add"] |= lo is not None and bool((low != 0).any())
        group["where"] |= thr is not None
        group["zeros"] |= zero is not None and bool(zero.any())

    def finish(self, tensors: dict[str, Tensor]) -> tuple[_Gather, ...]:
        gathers = []
        for i, (dtype, group) in enumerate(self.groups.items()):
            length = torch.cat(group["length"]).long()
            tensors[f"g{i}.delta"] = torch.cat(group["src"]).long() - (length.cumsum(0) - length)
            tensors[f"g{i}.length"] = length
            mode: Literal["copy", "add", "where"] = (
                "where" if group["where"] else "add" if group["add"] else "copy")
            if mode != "copy" and dtype.is_floating_point:
                raise ValueError("join_runs_add_on_float")
            names = {"where": ("lo", "hi", "thr"), "add": ("lo",), "copy": ()}[mode]
            for name in names:
                tensors[f"g{i}.{name}"] = torch.cat(group[name]).to(dtype)
            if group["zeros"]:
                tensors[f"g{i}.zero"] = torch.cat(group["zero"])
            gathers.append(_Gather(dtype, group["total"], mode, group["zeros"],
                                   tuple(group["outputs"])))
        return tuple(gathers)


def _plan_join(arena: Tensor, written: Sequence[ForwardPart], rows: tuple[int, ...] | None
               ) -> tuple[dict[str, Tensor], JoinPlan]:
    """All host work of the join: the index maps of `forward_inputs(concatenate_groups(
    ...))` (same rules and order as the CPU merge) and the runs of the bulk arrays."""
    parts = [read_forward_part(arena, part) for part in written]
    trees = [cast(PreparedGroup, part.tree) for part in written]
    index = _check_join(parts)
    policy = parts[0].policy
    layouts = [cast(RowLayout, p.layout) for p in parts]
    tree_layouts = [cast(RowLayout, t.layout) for t in trees]
    counts = [len(p.rows) for p in parts]
    total = sum(counts)
    rows = tuple(r for p in parts for r in p.rows) if rows is None else tuple(rows)
    if len(rows) != total:
        raise ValueError("join_rows_mismatch")
    low = min(part.start for part in written)
    high = max(part.end for part in written)

    def element(p: int, slot: _ArenaSlot) -> int:
        start = written[p].start + slot.offset - low
        if start % slot.dtype.itemsize:
            raise ValueError("forward_arena_misaligned")
        return start // slot.dtype.itemsize

    hosts = [p.tensors for p in parts]
    row_offsets = [sum(counts[:i]) for i in range(len(parts))]
    # Scene rows: entity counts in each part's row order, as one row_layout.
    entities = [layout.scene_lengths.long()[layout.scene_inverse.long()] for layout in layouts]
    entity = torch.cat(entities)
    nodes = int(entity.sum())
    scene_width = _bucket(int(entity.max()), (32, 64, 128, 256, 512, 1024))
    scene = _scene_layout(entity, entity.cumsum(0) - entity, scene_width, ROW_BUCKET_ROWS,
                          ROW_WIDTH_MULTIPLE)
    physical_rows = _bucket(total, (1, 2, 4, 8, 16, 32, 64))
    # Text: each part's real unique texts in part order, then re-padded once.
    texts = [len(layout.text_order) for layout in layouts]
    text_offsets = [sum(texts[:i]) for i in range(len(parts))]
    width = max(int(t["text_ids"].shape[1]) for t in hosts)
    lengths = torch.cat([t["text_lengths"][:k] for t, k in zip(hosts, texts, strict=True)])
    text = _text_layout(lengths, width)
    text_rows = _bucket(len(lengths), (32, 128, 512, 2048)) if len(lengths) else 0
    padded_lengths = F.pad(lengths, (0, text_rows - len(lengths)), value=1)
    sizes = {key: [int(p.joint.tensors[name].numel()) for p in parts] for key, name in (
        ("ids", "identities"), ("numbers", "numbers"), ("missing", "missing_fields"),
        ("text", "text_fields"))}
    sizes["refs"] = [int(e.sum()) for e in entities]
    if max(nodes, sum(sizes["ids"]), sum(sizes["numbers"]) + sum(sizes["missing"]),
           sum(sizes["text"]), int(padded_lengths.numel()) * width,
           physical_rows * scene_width, high - low) > _INT32[1]:
        raise ValueError("forward_index_exceeds_int32:join")
    before = {key: [sum(values[:i]) for i in range(len(values))] for key, values in sizes.items()}
    tensors: dict[str, Tensor] = {f"text.{key}": value for key, value in text.items()
                                  if isinstance(value, Tensor)}
    tensors.update({f"scene.{key}": value for key, value in scene.fields().items()
                    if isinstance(value, Tensor)})
    runs = _Runs()
    meta: dict[str, Any] = {"text_buckets": text["text_buckets"],
                            "text_longest": text["text_longest"],
                            "scene_buckets": scene.buckets, "reduction_buckets": {},
                            "role_terms": {}}
    # Reductions: `_merged_reduction` order (widths in first-seen order, parts in
    # order); the inverse maps each node to its row in that order.
    for key in FORWARD_REDUCTIONS:
        grouped: dict[int, list[tuple[int, int, int, int, int, int]]] = {}
        for p, layout in enumerate(layouts):
            flat_cursor = row_cursor = 0
            for number, (bucket_width, count) in enumerate(layout.reductions[key][1]):
                size = bucket_width * count
                grouped.setdefault(bucket_width, []).append(
                    (p, flat_cursor, size, count, row_cursor, number))
                flat_cursor += size
                row_cursor += count
        position = 0
        moves: list[dict[int, tuple[int, int]]] = [{} for _ in layouts]  # bucket: shift
        chunks: list[tuple[int, int, int]] = []
        for bucket_width, items in grouped.items():
            for p, flat_start, size, count, row_start, number in items:
                moves[p][number] = (position - row_start, count)
                position += count
                if bucket_width:
                    chunks.append((p, flat_start, size))
        meta["reduction_buckets"][key] = tuple((w, sum(item[3] for item in items))
                                               for w, items in grouped.items())
        inverses = []
        for move, layout in zip(moves, layouts, strict=True):
            shifts = [move[start] for start in sorted(move)]  # part bucket order
            owners = len(layout.reductions[key][2])
            mapping = torch.arange(owners) + torch.repeat_interleave(
                torch.tensor([shift for shift, _ in shifts], dtype=torch.long),
                torch.tensor([count for _, count in shifts], dtype=torch.long),
                output_size=owners)
            inverses.append(mapping[layout.reductions[key][2].long()])
        tensors[f"inverse.{key}"] = torch.cat(inverses)
        flats = [cast(_ArenaSlot, t.reductions[key][0]) for t in tree_layouts]
        src = torch.tensor([element(p, flats[p]) + a for p, a, _ in chunks], dtype=torch.long)
        length = torch.tensor([n for _, _, n in chunks], dtype=torch.long)
        if key == "numbers":
            present = torch.tensor([sizes["numbers"][p] for p, _, _ in chunks], dtype=torch.long)
            lo = torch.tensor([before["numbers"][p] for p, _, _ in chunks], dtype=torch.long)
            hi = torch.tensor([sum(sizes["numbers"]) + before["missing"][p]
                               for p, _, _ in chunks], dtype=torch.long) - present
            runs.add(f"reduction.{key}", index, (int(length.sum()),), src, length, lo=lo,
                     hi=hi, thr=present)
        else:
            lo = torch.tensor([before[key][p] for p, _, _ in chunks], dtype=torch.long)
            runs.add(f"reduction.{key}", index, (int(length.sum()),), src, length, lo=lo)
    # Text tokens: each part's first rows, re-padded to the joined width.
    for key in ("text_ids", "text_numbers"):
        slots = [cast(_ArenaSlot, t.tensors[key]) for t in trees]
        extra = math.prod(slots[0].shape[2:])
        srcs, lens, zeros = [], [], []
        for p, (text_slot, k) in enumerate(zip(slots, texts, strict=True)):
            if not k:
                continue
            base, own_width = element(p, text_slot), text_slot.shape[1]
            if own_width == width:
                srcs.append(torch.tensor([base]))
                lens.append(torch.tensor([k * width * extra]))
                zeros.append(torch.tensor([False]))
            else:
                firsts = base + torch.arange(k) * own_width * extra
                srcs.append(torch.stack((firsts, torch.zeros_like(firsts)), 1).reshape(-1))
                lens.append(torch.tensor([own_width * extra, (width - own_width) * extra]
                                         ).repeat(k))
                zeros.append(torch.tensor([False, True]).repeat(k))
        pad = text_rows - len(lengths)
        if pad:
            srcs.append(torch.zeros(pad, dtype=torch.long))
            lens.append(torch.full((pad,), width * extra))
            zeros.append(torch.ones(pad, dtype=torch.bool))
        runs.add(key, slots[0].dtype, (text_rows, width, *slots[0].shape[2:]),
                 torch.cat(srcs) if srcs else torch.zeros(0, dtype=torch.long),
                 torch.cat(lens).long() if lens else torch.zeros(0, dtype=torch.long),
                 zero=torch.cat(zeros) if zeros else torch.zeros(0, dtype=torch.bool))
    for key in FORWARD_JOINT:
        slots = [cast(_ArenaSlot, t.joint.tensors[key]) for t in trees]
        if any(s.dtype != slots[0].dtype or s.shape[1:] != slots[0].shape[1:] for s in slots):
            raise ValueError(f"join_requires_one_joint_shape:{key}")
        runs.add(f"joint.{key}", slots[0].dtype,
                 (sum(s.shape[0] for s in slots), *slots[0].shape[1:]),
                 torch.tensor([element(p, s) for p, s in enumerate(slots)], dtype=torch.long),
                 torch.tensor([math.prod(s.shape) for s in slots], dtype=torch.long))
    texts_slots = [cast(_ArenaSlot, t.node_text_indices) for t in tree_layouts]
    runs.add("node_text", index, (sum(math.prod(s.shape) for s in texts_slots),),
             torch.tensor([element(p, s) for p, s in enumerate(texts_slots)], dtype=torch.long),
             torch.tensor([math.prod(s.shape) for s in texts_slots], dtype=torch.long),
             lo=torch.tensor(text_offsets, dtype=torch.long))
    if policy:
        catalogs = [int(t["action_types"].shape[1]) for t in hosts]
        catalog = max(catalogs)
        tensors["action_types"] = F.pad(torch.cat([
            F.pad(t["action_types"][:n], (0, catalog - c))
            for t, n, c in zip(hosts, counts, catalogs, strict=True)]),
            (0, 0, 0, physical_rows - total))
        tensors["catalog_mask"] = F.pad(torch.cat([
            F.pad(t["catalog_mask"][:n], (0, catalog - c), value=True)
            for t, n, c in zip(hosts, counts, catalogs, strict=True)]),
            (0, 0, 0, physical_rows - total), value=True)
        joined_widths = torch.tensor([b[1] for b in scene.buckets])
        for key in ("source", "target"):
            order_keys, candidates, term_slots, term_buckets = [], [], [], []
            present = torch.zeros((total, catalog), dtype=torch.bool)
            for p, layout in enumerate(layouts):
                candidate, slot, _weight, terms = layout.roles[key]
                own = layout.scene_buckets
                starts = torch.tensor([sum(b[0] for b in own[:i]) for i in range(len(own))],
                                      dtype=torch.long)
                widths = torch.tensor([b[1] for b in own], dtype=torch.long)
                bucket = torch.repeat_interleave(torch.arange(len(own)), torch.tensor(terms))
                local = candidate.long().div(catalogs[p], rounding_mode="floor")
                choice = candidate.long() - local * catalogs[p]
                term_slot = slot.long() - local * widths[bucket]
                part_rows = layout.scene_rows.long()
                rank = scene.inverse[row_offsets[p] + part_rows[starts[bucket] + local]]
                joined_bucket = rank.div(ROW_BUCKET_ROWS, rounding_mode="floor")
                within = rank - joined_bucket * ROW_BUCKET_ROWS
                order_keys.append(rank * catalog + choice)
                candidates.append(within * catalog + choice)
                term_slots.append(within * joined_widths[joined_bucket] + term_slot)
                term_buckets.append(joined_bucket)
                flags = layout.role_present[key].view(counts[p], catalogs[p])
                present[scene.inverse[row_offsets[p] + part_rows], :catalogs[p]] = flags
            permutation = torch.argsort(torch.cat(order_keys), stable=True)
            meta["role_terms"][key] = tuple(torch.bincount(
                torch.cat(term_buckets)[permutation], minlength=len(scene.buckets)).tolist())
            tensors[f"roles.{key}.candidate"] = torch.cat(candidates)[permutation]
            tensors[f"roles.{key}.slot"] = torch.cat(term_slots)[permutation]
            tensors[f"present.{key}"] = present.reshape(-1)
            weights = [cast(_ArenaSlot, t.roles[key][2]) for t in tree_layouts]
            row = math.prod(weights[0].shape[1:])
            first = torch.cat([torch.full((w.shape[0],), element(p, w), dtype=torch.long)
                               for p, w in enumerate(weights)])
            local_term = torch.cat([torch.arange(w.shape[0]) for w in weights])
            src = (first + local_term * row)[permutation]
            runs.add(f"weights.{key}", weights[0].dtype,
                     (len(permutation), *weights[0].shape[1:]), src,
                     torch.full((len(permutation),), row, dtype=torch.long))
    tensors["outcome_valid"] = torch.cat(
        [t["outcome_valid"][:n] for t, n in zip(hosts, counts, strict=True)]
        + [outcome_mask(torch.zeros(physical_rows - total, dtype=torch.long))])
    tensors = {name: value.to(index) if value.dtype == torch.int64 else value
               for name, value in tensors.items()}
    tensors["lengths"] = padded_lengths  # text_lengths keeps its prepared dtype
    gathers = runs.finish(tensors)
    return tensors, JoinPlan(rows, policy, low, high, 0, 0, False, (), gathers, meta)


def assemble(
    request: PrepareRequest,
    sources: tuple[PreparedSource, ...],
    *,
    microbatch_size: int = 32,
    deduplicate_text: bool = True,
    scene_attention_area: int = 67108864,
    text_attention_area: int = 67108864,
    auxiliary_frames: tuple[AuxiliaryFrame, ...] = (),
    prepared_layout: str = "padded-v1",
    group_shards: int = 1,
    prepared_bucket_rows: int = ROW_BUCKET_ROWS,
) -> PreparedBatch:
    began = time.perf_counter()
    if prepared_layout not in PREPARED_LAYOUTS:
        raise ValueError("unsupported_prepared_layout")
    if (type(prepared_bucket_rows) is not int or prepared_bucket_rows < 1
            or (prepared_bucket_rows != ROW_BUCKET_ROWS and prepared_layout != "rows-v2")):
        raise ValueError("invalid_prepared_bucket_rows")
    if type(group_shards) is not int or not 1 <= group_shards <= 64:
        raise ValueError("invalid_group_shards")
    if type(request.epochs) is not int or request.epochs < 1:
        raise ValueError("positive_prepared_epochs_required")
    by_id = {source.segment_id: source for source in sources}
    if len(by_id) != len(sources):
        raise ValueError("duplicate_preparation_source")
    if any(
        source.generation != request.identity.training_generation
        or source.compiler_identity != request.compiler_identity
        for source in sources
    ):
        raise ValueError("prepared_source_generation_or_compiler_changed")
    ppo = [by_id[member.segment_id] for member in request.members]
    aux_by_row = {
        (item.source["segment_id"], item.source["row"]): item for item in auxiliary_frames
    }
    source_frames = [frame for source in ppo for frame in source.frames]
    if any(frame is None for frame in source_frames):
        raise ValueError("complete_PPO_frames_required")
    frames = cast(list[TensorFrame], source_frames)
    refs = cast(list[RowRef], [ref for source in ppo for ref in source.row_refs])
    floors = [floor for source in ppo for floor in source.floors]
    actions = [action for source in ppo for action in source.actions]
    old = [row for source in ppo for row in source.policy_probabilities]
    metadata = [row for source in ppo for row in source.metadata]
    raw_advantages = torch.cat([source.raw_advantages for source in ppo])
    ppo_targets = torch.cat([source.targets for source in ppo])
    probabilities = torch.cat([source.probabilities for source in ppo])
    n = len(frames)
    partitions = [i for group in request.minibatch_rows for i in group]
    if (
        sorted(partitions) != list(range(n))
        or len(request.auxiliary_minibatches) != len(request.auxiliary)
        or any(not 0 <= i < len(request.minibatch_rows) for i in request.auxiliary_minibatches)
    ):
        raise ValueError("prepared_minibatches_must_partition_dataset")
    kinds: list[Literal["ppo", "terminal", "hp"]] = ["ppo"] * n
    terminal_labels, hp_labels = {}, {}
    for local, aux in enumerate(request.auxiliary):
        row = aux.source["row"]
        if aux.source["segment_id"] in by_id:
            source = by_id[aux.source["segment_id"]]
            bound_ref = source.row_refs[row]
            aux_frame = cast(TensorFrame, source.frames[row])
            aux_floor = source.floors[row]
        else:
            prepared = aux_by_row[aux.source["segment_id"], row]
            bound_ref, aux_frame, aux_floor = prepared.source, prepared.frame, prepared.floor
        if (
            bound_ref != aux.source
            or not math.isfinite(aux.label)
            or not 0 <= aux.uses_before < aux.use_limit
            or type(aux.use_limit) is not int or aux.use_limit < 1
        ):
            raise ValueError("auxiliary_binding_or_successful_use_limit_changed")
        frames.append(aux_frame)
        refs.append(aux.source)
        floors.append(aux_floor)
        actions.append(-1)
        old.append(())
        kinds.append(aux.kind)
        if aux.kind == "terminal":
            if not float(aux.label).is_integer() or not 0 <= int(aux.label) < OUTCOME_CLASSES:
                raise ValueError("invalid_terminal_label")
            terminal_labels[n + local] = int(aux.label)
        else:
            if aux.label < 0:
                raise ValueError("invalid_public_hp_label")
            hp_labels[n + local] = aux.label
    total = len(frames)
    floor_tensor = torch.tensor(floors, dtype=torch.long)
    policy_mask = torch.tensor([i < n and len(f.choices) > 1 for i, f in enumerate(frames)])
    td_mask = torch.arange(total) < n
    terminal_mask = torch.tensor([kind == "terminal" for kind in kinds])
    hp_mask = torch.tensor([kind == "hp" for kind in kinds])
    advantages = torch.zeros(total)
    multi = raw_advantages[policy_mask[:n]]
    mean = float(multi.mean()) if len(multi) else 0.0
    scale = max(float(multi.std(unbiased=False)), 1e-8) if len(multi) else 1.0
    advantages[:n][policy_mask[:n]] = (multi - mean) / scale
    targets = torch.zeros((total, OUTCOME_CLASSES))
    targets[:n] = ppo_targets
    hp_targets = torch.zeros(total)
    for row, label in terminal_labels.items():
        targets[row, label] = 1
    for row, hp_value in hp_labels.items():
        hp_targets[row] = hp_value
    valid = outcome_mask(floor_tensor)
    if bool(targets.masked_select(~valid).ne(0).any()):
        raise ValueError("target_before_observed_floor")
    offsets = [0]
    for mass in old:
        offsets.append(offsets[-1] + len(mass))
    old_flat = torch.tensor([p for mass in old for p in mass], dtype=torch.float32)
    partitions_ready = time.perf_counter()
    minibatches = []
    for index, members in enumerate(request.minibatch_rows):
        work = list(members) + [
            n + local for local, owner in enumerate(request.auxiliary_minibatches) if owner == index
        ]
        groups = []
        group: list[int] = []
        text_count = text_width = scene_width = 0
        # Data parallel (same update): never let one physical group cross a
        # rank's equal share of this minibatch, so every rank has its own rows.
        cuts = {len(work) * k // group_shards for k in range(1, group_shards)}
        for position, row in enumerate(work):
            frame = frames[row]
            width = _bucket(max(text_width, frame.text_width), (8, 16, 32, 64, 128, 256, 512))
            scene = _bucket(max(scene_width, frame.entity_count), (32, 64, 128, 256, 512, 1024))
            if group and (
                position in cuts
                or len(group) >= microbatch_size
                or (len(group) + 1) * scene**2 > scene_attention_area
                or (text_count + frame.text_count) * width**2 > text_attention_area
            ):
                groups.append(prepare_group([frames[i] for i in group], tuple(group), True,
                    floor_tensor[group], deduplicate_text=deduplicate_text,
                    layout=prepared_layout, bucket_rows=prepared_bucket_rows))
                group, text_count, text_width, scene_width = [], 0, 0, 0
            group.append(row)
            text_count += frame.text_count
            text_width = max(text_width, frame.text_width)
            scene_width = max(scene_width, frame.entity_count)
        if group:
            groups.append(prepare_group([frames[i] for i in group], tuple(group), True,
                floor_tensor[group], deduplicate_text=deduplicate_text,
                    layout=prepared_layout, bucket_rows=prepared_bucket_rows))
        order = [row for group in groups for row in group.rows]
        selector = torch.tensor(order, dtype=torch.long)
        policy_indices = torch.nonzero(policy_mask[selector], as_tuple=True)[0]
        width = max(
            (group.tensors["action_types"].shape[1] for group in groups if group.policy), default=1
        )
        old_padded = torch.full((len(order), width), 1.0 / width)
        catalog_valid = torch.ones((len(order), width), dtype=torch.bool)
        for local, row in enumerate(order):
            count = len(frames[row].choices)
            catalog_valid[local, count:] = False
            old_padded[local].zero_()
            if row < n:
                old_padded[local, :count] = torch.tensor(old[row])
            else:
                old_padded[local, :count] = 1.0 / count  # no policy loss on auxiliary rows
        mb_data = {
            "policy_indices": policy_indices,
            "td_indices": torch.nonzero(td_mask[selector], as_tuple=True)[0],
            "terminal_indices": torch.nonzero(terminal_mask[selector], as_tuple=True)[0],
            "hp_indices": torch.nonzero(hp_mask[selector], as_tuple=True)[0],
            "actions": torch.tensor(actions, dtype=torch.long)[selector].clamp_min(0),
            "old_probabilities": old_padded,
            "advantages": advantages[selector],
            "targets": targets[selector],
            "hp_targets": hp_targets[selector],
            "outcome_valid": valid[selector],
            "catalog_valid": catalog_valid,
            "policy_mask": policy_mask[selector].float(),
            "td_mask": td_mask[selector].float(),
            "terminal_mask": terminal_mask[selector].float(),
            "hp_mask": hp_mask[selector].float(),
        }
        epoch_auxiliary, epoch_counts = [], []
        if request.epochs > 1:
            for epoch in range(request.epochs):
                eligible = torch.tensor([
                    row < n or (request.auxiliary[row - n].uses_before + epoch
                                < request.auxiliary[row - n].use_limit)
                    for row in order], dtype=torch.float32)
                masks = {name: mb_data[name] * eligible
                         for name in ("terminal_mask", "hp_mask")}
                epoch_auxiliary.append(masks)
                epoch_counts.append((int(masks["terminal_mask"].sum()),
                                     int(masks["hp_mask"].sum())))
        minibatches.append(
            PreparedMinibatch(
                tuple(groups),
                len(policy_indices),
                len(mb_data["td_indices"]),
                len(mb_data["terminal_indices"]),
                len(mb_data["hp_indices"]),
                mb_data,
                tuple(epoch_auxiliary),
                tuple(epoch_counts),
            )
        )
    metadata_ready = time.perf_counter()
    # Clear derived Python key caches; forward consumes only the prepared tensors.
    for frame in frames:
        frame.metadata_cache.clear()
    layouts = tuple(
        FrameLayout(frame.choices, frame.entity_count, frame.text_count, frame.text_width)
        for frame in frames
    )
    batch = PreparedBatch(
        request,
        tuple(refs),
        layouts,
        tuple(kinds),
        floor_tensor,
        torch.tensor(actions),
        torch.tensor(offsets),
        old_flat,
        torch.cat((probabilities, torch.zeros((total - n, OUTCOME_CLASSES)))),
        targets,
        hp_targets,
        advantages,
        policy_mask,
        td_mask,
        terminal_mask,
        hp_mask,
        AdvantageNormalization(len(multi), mean, scale),
        tuple(minibatches),
        time.time(),
        0,
        tuple(metadata),
        {
            "normalize_and_partition": partitions_ready - began,
            "metadata": metadata_ready - partitions_ready,
            **{
                key: sum(source.seconds.get(key, 0) for source in sources)
                for key in ("read_verify", "decode_bind", "lambda_targets",
                            "source_queue_seconds", "source_pack_seconds",
                            "hot_read_wall_seconds", "hot_json_seconds", "hot_validate_seconds")
            },
        },
        tuple(sorted({source.pid for source in sources} | {os.getpid()})),
        tuple(source.labels for source in ppo),
    )
    batch, size = contiguous_slabs(batch)
    if size > request.max_output_bytes:
        raise ValueError("prepared_batch_exceeds_current_plus_next_byte_budget")
    return dataclasses.replace(
        batch,
        payload_bytes=size,
        timings={**batch.timings, "slab_pack": time.perf_counter() - metadata_ready},
    )


def contiguous_slabs(value: T) -> tuple[T, int]:
    """Two/three shared storages rather than tens of thousands of IPC handles.

    Preserve shape/dtype exactly. Every returned tensor is contiguous and owns
    no external source allocation; views share only this bounded batch's slabs.
    """
    leaves: dict[int, Tensor] = {}

    def visit(node: Any) -> None:
        if isinstance(node, Tensor):
            leaves[id(node)] = node
        elif dataclasses.is_dataclass(node) and not isinstance(node, type):
            for field in dataclasses.fields(node):
                visit(getattr(node, field.name))
        elif isinstance(node, dict):
            for item in node.values():
                visit(item)
        elif isinstance(node, (tuple, list)):
            for item in node:
                visit(item)

    visit(value)
    groups: dict[torch.dtype, list[Tensor]] = defaultdict(list)
    for tensor in leaves.values():
        groups[tensor.dtype].append(tensor)
    replacements = {}
    size = 0
    for tensors in groups.values():
        slab = torch.cat([tensor.reshape(-1) for tensor in tensors])
        size += slab.numel() * slab.element_size()
        cursor = 0
        for tensor in tensors:
            replacements[id(tensor)] = slab[cursor : cursor + tensor.numel()].view(tensor.shape)
            cursor += tensor.numel()

    def replace(node: Any) -> Any:
        if isinstance(node, Tensor):
            return replacements[id(node)]
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            return dataclasses.replace(
                node, **{f.name: replace(getattr(node, f.name)) for f in dataclasses.fields(node)}
            )
        if isinstance(node, dict):
            return {k: replace(v) for k, v in node.items()}
        if isinstance(node, tuple):
            return tuple(replace(v) for v in node)
        if isinstance(node, list):
            return [replace(v) for v in node]
        return node

    return cast(T, replace(value)), size


class BatchPreparer:
    """One current + one next request, sharded over configurable spawn workers.

    The coordinator only waits for futures/IPC. Decode, target normalization,
    partition metadata and slab packing all execute in independent CPU processes.
    """

    def __init__(
        self,
        vocabulary: dict[str, Any],
        *,
        workers: int,
        threads: int,
        peer: dict[str, Any] | None,
        run: str,
        scratch: Path,
        reader: Callable[..., dict[str, Any]] | None = None,
        microbatch_size: int = 32,
        deduplicate_text: bool = True,
        scene_attention_area: int = 67108864,
        text_attention_area: int = 67108864,
        excluded_seeds: frozenset[str] = frozenset(),
        gae_lambda: float = 0.99,
        prepared_layout: str = "padded-v1",
        prepared_bucket_rows: int = ROW_BUCKET_ROWS,
    ) -> None:
        if prepared_layout not in PREPARED_LAYOUTS:
            raise ValueError("unsupported_prepared_layout")
        self.pool = ProcessPoolExecutor(
            max_workers=workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_initialize,
            initargs=(vocabulary, threads, peer, run, reader,
                      str(scratch), excluded_seeds, gae_lambda),
        )
        self.coordinator = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rl-ready-coordinator"
        )
        self.options: dict[str, Any] = dict(
            microbatch_size=microbatch_size,
            deduplicate_text=deduplicate_text,
            scene_attention_area=scene_attention_area,
            text_attention_area=text_attention_area,
        )
        if prepared_layout != "padded-v1":
            self.options["prepared_layout"] = prepared_layout
        if prepared_bucket_rows != ROW_BUCKET_ROWS:
            self.options["prepared_bucket_rows"] = prepared_bucket_rows
        self.pending: Future[PreparedBatch] | None = None

    def submit(
        self, request: PrepareRequest, auxiliary_inputs: tuple[AuxiliaryInput, ...] = ()
    ) -> Future[PreparedBatch]:
        if self.pending is not None and not self.pending.done():
            raise ValueError("one_pending_preparation_request_required")
        ppo_ids = {member.segment_id for member in request.members}
        cached = {
            (item.source["segment_id"], item.source["row"]): item for item in auxiliary_inputs
        }
        selected_inputs = {}
        for selection in request.auxiliary:
            if selection.source["segment_id"] in ppo_ids:
                continue  # current PPO read already provides this exact public frame
            key = selection.source["segment_id"], selection.source["row"]
            item = cached[key]
            if (
                item.source != selection.source
                or item.source["training_generation"] != request.identity.training_generation
                or item.member.payload.slot.split("/")[:2]
                != [request.identity.run, request.identity.training_generation]
                or item.member.segment_id != item.source["segment_id"]
                or item.member.run_id != item.source["run_id"]
            ):
                raise ValueError("auxiliary_snapshot_membership_changed")
            selected_inputs[key] = item
        futures = [
            self.pool.submit(_read_source, member.payload, member, time.perf_counter())
            for member in request.members
        ]
        inputs = tuple(selected_inputs.values())
        auxiliary_futures = [
            self.pool.submit(
                _decode_auxiliary_inputs, inputs[start : start + 32], request.compiler_identity
            )
            for start in range(0, len(inputs), 32)
        ]
        started = time.perf_counter()

        def collect() -> PreparedBatch:
            sources = tuple(future.result() for future in futures)
            sources_at = time.perf_counter()
            auxiliary = tuple(future.result() for future in auxiliary_futures)
            assembly_at = time.perf_counter()
            packed = self.pool.submit(
                _assemble_transport, request, sources, self.options, auxiliary, assembly_at
            ).result()
            returned_at = time.perf_counter()
            batch = cast(PreparedBatch, unpack_transport(packed))
            return dataclasses.replace(
                batch,
                timings={**batch.timings, "request_wall_seconds": time.perf_counter() - started,
                    "sources_ready_wall_seconds": sources_at - started,
                    "auxiliary_ready_wait_seconds": assembly_at - sources_at,
                    "assemble_roundtrip_seconds": returned_at - assembly_at,
                    "batch_unpack_seconds": time.perf_counter() - returned_at},
            )

        self.pending = self.coordinator.submit(collect)
        return self.pending

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = True) -> None:
        self.coordinator.shutdown(wait=wait, cancel_futures=cancel_futures)
        self.pool.shutdown(wait=wait, cancel_futures=cancel_futures)


@dataclasses.dataclass(frozen=True)
class _TensorView:
    slab: int
    offset: int
    shape: tuple[int, ...]


def _map_structure(value: Any, transform: Callable[[Any], Any]) -> Any:
    mapped = transform(value)
    if mapped is not value:
        return mapped
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.replace(
            value,
            **{
                field.name: _map_structure(getattr(value, field.name), transform)
                for field in dataclasses.fields(value)
            },
        )
    if isinstance(value, dict):
        return {key: _map_structure(item, transform) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_map_structure(item, transform) for item in value)
    if isinstance(value, list):
        return [_map_structure(item, transform) for item in value]
    return value


def pack_transport(value: Any) -> Any:
    """IPC uses contiguous array slabs, not Torch's POSIX shared-memory reducer.

    This is the sole process transport on all platforms. It avoids thousands of
    handles and works in the macOS CPU sandbox as well as Linux. Only internal
    parameter-free metadata/snapshots use this; durable CP still uses tensor-tree.
    """
    storages: dict[tuple[int, torch.dtype], int] = {}
    slabs: list[Any] = []

    def replace(node: Any) -> Any:
        if not isinstance(node, Tensor):
            return node
        if node.device.type != "cpu" or not node.is_contiguous():
            raise ValueError("CPU_contiguous_tensor_transport_required")
        storage = node.untyped_storage()
        key = storage.data_ptr(), node.dtype
        if key not in storages:
            storages[key] = len(slabs)
            flat = torch.empty(0, dtype=node.dtype).set_(
                storage, 0, (storage.nbytes() // node.element_size(),), (1,)
            )
            slabs.append(flat.numpy())
        return _TensorView(storages[key], int(node.storage_offset()), tuple(node.shape))

    tree = _map_structure(value, replace)
    return tree, tuple(slabs)


def unpack_transport(packed: Any) -> Any:
    tree, arrays = packed
    slabs = [torch.from_numpy(array) for array in arrays]

    def replace(node: Any) -> Any:
        if not isinstance(node, _TensorView):
            return node
        count = math.prod(node.shape)
        return slabs[node.slab][node.offset : node.offset + count].view(node.shape)

    return _map_structure(tree, replace)


def stage_pinned_slabs(tree: Any, slabs: list[Tensor], lease: Any,
                       device: torch.device) -> tuple[PreparedBatch, int]:
    """CUDA: H2D each pinned slab once and map the packed tree onto device views.

    Same tensors as `unpack_transport` + pin + `stage_batch`, with one tree walk.
    The pinned lease is released only after this copy's event completes.
    """
    stream = torch.cuda.Stream(device=device)
    with torch.cuda.stream(stream):
        moved = [slab.to(device, non_blocking=True) for slab in slabs]
        ready = torch.cuda.Event()
        ready.record(stream)
    consumer = torch.cuda.current_stream(device)
    consumer.wait_event(ready)
    for tensor in moved:
        tensor.record_stream(consumer)
    lease.copied(ready)

    def replace(node: Any) -> Any:
        if not isinstance(node, _TensorView):
            return node
        count = math.prod(node.shape)
        return moved[node.slab][node.offset : node.offset + count].view(node.shape)

    return cast(PreparedBatch, _map_structure(tree, replace)), len(moved)


def stage_batch(batch: PreparedBatch, device: torch.device) -> tuple[PreparedBatch, int]:
    """One H2D copy per dtype slab, reused by all four optimizer minibatches.

    The immutable CPU owner remains alive until the dataset finishes. Copies and
    forwards use the learner's stream, so no per-leaf synchronization or host
    mutation is needed. CPU/meta smoke does not establish CUDA transfer timing.
    """
    if device.type == "cpu":
        return batch, 0
    slabs: dict[tuple[int, torch.dtype], Tensor] = {}
    pinned_owners: dict[int, Any] = {}

    def move(node: Any) -> Any:
        if not isinstance(node, Tensor):
            return node
        owner = getattr(node, "_rl_pinned_lease", None)
        if owner is not None:
            pinned_owners[id(owner)] = owner
        storage = node.untyped_storage()
        key = storage.data_ptr(), node.dtype
        if key not in slabs:
            flat = torch.empty(0, dtype=node.dtype).set_(
                storage, 0, (storage.nbytes() // node.element_size(),), (1,)
            )
            slabs[key] = flat.to(device, non_blocking=True)
        offset = int(node.storage_offset())
        return slabs[key][offset : offset + node.numel()].view(node.shape)

    if device.type == "cuda":
        # A dedicated copy stream overlaps the preceding dataset's SGD. Each
        # pinned lease remains unavailable until its own H2D event completes.
        stream = torch.cuda.Stream(device=device)
        with torch.cuda.stream(stream):
            result = cast(PreparedBatch, _map_structure(batch, move))
            ready = torch.cuda.Event()
            ready.record(stream)
        consumer = torch.cuda.current_stream(device)
        consumer.wait_event(ready)
        for slab in slabs.values():
            slab.record_stream(consumer)
        for owner in pinned_owners.values():
            owner.copied(ready)
        return result, len(slabs)
    return cast(PreparedBatch, _map_structure(batch, move)), len(slabs)


def _decode_auxiliary_inputs(inputs: tuple[AuxiliaryInput, ...], identity: dict[str, Any]) -> Any:
    began = time.perf_counter()
    frames = []
    for item in inputs:
        frame, floor = decode_auxiliary_bytes(_worker["compiler"], item.body, identity)
        frames.append(AuxiliaryFrame(item.source, frame, floor))
    packed = pack_transport(contiguous_slabs(tuple(frames))[0])
    return packed, time.perf_counter() - began


def _assemble_transport(
    request: PrepareRequest,
    packed: tuple[Any, ...],
    options: dict[str, Any],
    auxiliary: tuple[Any, ...] = (),
    submitted_at: float | None = None,
) -> Any:
    began = time.perf_counter()
    aux_frames = tuple(
        frame for result, _seconds in auxiliary for frame in unpack_transport(result)
    )
    sources = tuple(unpack_transport(source) for source in packed)
    unpacked_at = time.perf_counter()
    batch = assemble(
        request,
        sources,
        auxiliary_frames=aux_frames,
        **options,
    )
    return pack_transport(
        dataclasses.replace(
            batch,
            timings={
                **batch.timings,
                "auxiliary_decode_seconds": sum(seconds for _result, seconds in auxiliary),
                "assemble_queue_seconds": began - submitted_at if submitted_at is not None else 0,
                "assemble_unpack_seconds": unpacked_at - began,
                "assemble_cpu_seconds": time.perf_counter() - unpacked_at,
            },
        )
    )


def _snapshot_auxiliary_input(member: Any, segment: dict[str, Any], index: int) -> AuxiliaryInput:
    step = segment["steps"][index]
    if step["input"]["input_spec"] != _worker["expected_spec"]:
        raise ValueError("auxiliary_model_input_spec_changed")
    ref: RowRef = {
        "run_id": segment["run_id"],
        "segment_id": segment["segment_id"],
        "training_generation": segment["training_generation"],
        "row": index,
        "key": f"{segment['run_id']}:{step['step_index']}",
    }
    return AuxiliaryInput(ref, member, verified_auxiliary_bytes(step))


def read_auxiliary_inputs(member: Any, rows: tuple[int, ...]) -> tuple[AuxiliaryInput, ...]:
    """Normal CPU input materialization for benchmarks; labels confer no authority here.

    In production verify_auxiliary already read the source to bind a typed label,
    and snapshots its selected rows in that same pass, without another read.
    """
    reader = _worker["reader"]
    segment = (
        reader(_worker["exchange"], member.payload, scratch=_worker["scratch"])
        if reader is not None
        else _worker["exchange"].read_hot_segment(member.payload)
    )
    validate_training_source(segment, _worker["excluded_seeds"])
    if (
        segment["segment_id"] != member.segment_id
        or segment["run_id"] != member.run_id
        or segment["identity"]["intent_id"] != member.episode
        or segment["rows"] != member.rows
        or segment["mu"]["snapshot_sha256"] != member.mu_snapshot_sha256
    ):
        raise ValueError("auxiliary_snapshot_source_changed")
    return tuple(_snapshot_auxiliary_input(member, segment, row) for row in rows)


def verify_auxiliary(
    request: dict[str, Any], members: tuple[Any, ...]
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    # ProcessPool's textual RemoteTraceback is otherwise lost at the RPC seam.
    # Attach only stack locations before pickling the original exception back.
    from spireagent.local_diagnostics import exception_facts

    try:
        return _verify_auxiliary(request, members)
    except Exception as error:
        cast(Any, error).remote_diagnostic = exception_facts(error)
        raise


def _verify_auxiliary(
    request: dict[str, Any], members: tuple[Any, ...]
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """Validate late typed/public labels in a CPU process, outside learner locks.

    Native/Human origin remains the W3 reader/verifier's responsibility. This
    consumer checks the exact terminal witness, source row and public HP binding;
    it never upgrades an unknown or reconstructs combat legality.
    """
    return verify_auxiliary_sources(request, members,
        read_source=lambda member: (
            _worker["reader"](_worker["exchange"], member.payload, scratch=_worker["scratch"])
            if _worker["reader"] is not None
            else _worker["exchange"].read_hot_segment(member.payload)),
        snapshot_row=_snapshot_auxiliary_input,
        excluded_seeds=_worker["excluded_seeds"])


def verify_auxiliary_sources(
    request: dict[str, Any], members: tuple[Any, ...], *,
    read_source: Callable[[Any], dict[str, Any]],
    snapshot_row: Callable[[Any, dict[str, Any], int], AuxiliaryInput],
    excluded_seeds: frozenset[str] = frozenset(),
    successor_digest: Callable[[dict[str, Any]], str] | None = None,
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """The one label rule implementation, independent of JSON/binary transport."""
    from stpd.p1.data import digest, json_bytes

    from .heads import class_reward_values

    by_slot = {member.payload.slot: member for member in members}
    by_segment = {member.segment_id: member for member in members}
    loaded: dict[str, dict[str, Any]] = {}
    inputs: dict[tuple[str, int], AuxiliaryInput] = {}

    def load(member: Any) -> dict[str, Any]:
        if member.segment_id not in loaded:
            segment = read_source(member)
            validate_training_source(segment, excluded_seeds)
            if (
                segment["segment_id"] != member.segment_id
                or segment["run_id"] != member.run_id
                or segment["identity"]["intent_id"] != member.episode
                or segment["rows"] != member.rows
                or segment["mu"]["snapshot_sha256"] != member.mu_snapshot_sha256
                or segment["training_generation"] != request["training_generation"]
                or segment["run_id"] != request["run_id"]
                or ("episode" in request and segment["identity"]["intent_id"] != request["episode"])
            ):
                raise ValueError("auxiliary_source_generation_or_run_changed")
            loaded[member.segment_id] = segment
        return loaded[member.segment_id]

    def source(reference: dict[str, Any]) -> tuple[Any, dict[str, Any], int, dict[str, Any]]:
        ref = reference["source"]
        member = by_segment[ref["segment_id"]]
        index, key = ref["row"], ref["key"]
        segment = load(member)
        if type(index) is not int or not 0 <= index < segment["rows"]:
            raise ValueError("auxiliary_row_binding_changed")
        step = segment["steps"][index]
        ref = {
            "run_id": segment["run_id"],
            "segment_id": segment["segment_id"],
            "training_generation": segment["training_generation"],
            "row": index,
            "key": f"{segment['run_id']}:{step['step_index']}",
        }
        if key != ref["key"] or reference["source"] != ref:
            raise ValueError("auxiliary_row_binding_changed")
        return member, segment, index, ref

    def snapshot(member: Any, segment: dict[str, Any], index: int) -> AuxiliaryInput:
        key = member.segment_id, index
        if key not in inputs:
            inputs[key] = snapshot_row(member, segment, index)
        return inputs[key]

    terminal = request.get("terminal")
    final = None
    if terminal is not None:
        if request.get("unqualified"):
            raise ValueError("unqualified_has_no_terminal_labels")
        label = terminal["class"]
        if (
            type(label) is not int
            or not 0 <= label < OUTCOME_CLASSES
            or not math.isclose(
                terminal["reward"], float(class_reward_values()[label]), abs_tol=1e-6
            )
            or terminal.get("typed_original") != request.get("typed_original")
        ):
            raise ValueError("terminal_class_reward_or_typed_original_changed")
        final_ref = request["segments"][-1]
        if "payload" in final_ref:
            final_member = by_slot[final_ref["payload"]["slot"]]
        else:
            slot = final_ref["segment_artifact_id"].removeprefix("hot:").removeprefix("tensor:")
            final_member = by_slot[slot]
        final = load(final_member)
        if (
            final["terminal"] != terminal
            or final["steps"][-1]["successor"]["observation"]["status"] != "terminal"
        ):
            raise ValueError("terminal_original_binding_changed")
    elif request.get("outcome_states"):
        raise ValueError("nonterminal_outcome_labels")
    groups = []
    if terminal is not None:
        rows = []
        for reference in request["outcome_states"]:
            member, segment, index, ref = source(reference)
            if (
                type(reference.get("outcome")) is not int
                or reference["outcome"] != terminal["class"]
            ):
                raise ValueError("auxiliary_terminal_label_changed")
            rows.append(
                {
                    "key": ref["key"],
                    "source": ref,
                    "member": member,
                    "outcome": terminal["class"],
                    "prepared_input": snapshot(member, segment, index),
                }
            )
        groups.append(("terminal", request["run_id"], rows))
    for battle in request.get("hp_battles", []):
        if "boundary_step_index" in battle:
            # Reward labels are checked against the source's real confirmed
            # successor, including delayed first-stable-reward boundaries.
            boundary_source = next(
                (
                    load(member)
                    for member in members
                    if any(
                        step["step_index"] == battle["boundary_step_index"]
                        for step in load(member)["steps"]
                    )
                ),
                None,
            )
            if boundary_source is None:
                raise ValueError("battle_hp_boundary_changed")
            boundary_step = next(
                step
                for step in boundary_source["steps"]
                if step["step_index"] == battle["boundary_step_index"]
            )
            boundary = boundary_step["successor"]
            observation = boundary["observation"]
            if (
                boundary["acquisition_id"] != battle["boundary_acquisition_id"]
                or (
                    battle.get("boundary_input_sha256") is not None
                    and battle["boundary_input_sha256"] != (successor_digest(boundary_step)
                        if successor_digest else digest(json_bytes(boundary)))
                )
                or not str((observation.get("scene") or {}).get("native_kind", "")).startswith(
                    "reward"
                )
                or (observation.get("combat") or {}).get("availability") == "available"
            ):
                raise ValueError("battle_hp_boundary_changed")
        else:
            if final is None:
                raise ValueError("terminal_hp_requires_typed_terminal")
            observation = final["steps"][-1]["successor"]["observation"]
        public_hp = ((observation.get("player") or {}).get("content") or {}).get("hp")
        if (
            not isinstance(public_hp, (float, int))
            or isinstance(public_hp, bool)
            or not math.isfinite(public_hp)
            or public_hp < 0
        ):
            raise ValueError("public_post_combat_hp_required")
        target = public_hp / 100
        if (
            "hp" in battle
            and battle["hp"] != target
            or "post_hp" in battle
            and battle["post_hp"] != public_hp
        ):
            raise ValueError("public_hp_scale_or_label_changed")
        rows = []
        for reference in battle["states"]:
            member, segment, index, ref = source(reference)
            step = segment["steps"][index]
            if (
                type(reference.get("hp")) not in (float, int)
                or reference["hp"] != target
                or (
                    "start_step" in battle
                    and not battle["start_step"] <= step["step_index"] < battle["end_step"]
                )
            ):
                raise ValueError("battle_hp_state_binding_changed")
            rows.append(
                {
                    "key": ref["key"],
                    "source": ref,
                    "member": member,
                    "hp": target,
                    "prepared_input": snapshot(member, segment, index),
                }
            )
        groups.append(("hp", battle["battle_id"], rows))
    return groups
