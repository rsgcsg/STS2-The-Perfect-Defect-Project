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
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

import torch
from torch import Tensor

from spireagent.rl_contracts import HotPayloadRef, RowRef
from spireagent.rl_dataplane_ports import BinaryRef
from stpd.p1.export import input_spec, runtime_projection
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.tensor_input import TensorFrame, joint_nodes, text_batch

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
    row_order = torch.argsort(slots, stable=True)
    catalog = int(t["action_types"].shape[1]) if group.policy else 0
    buckets, nodes, positions, offsets = [], [], [], []
    roles: dict[str, list[list[Tensor]]] = {"source": [], "target": []}
    present: dict[str, list[Tensor]] = {"source": [], "target": []}
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
    scene_inverse = torch.empty_like(row_order)
    scene_inverse[row_order] = torch.arange(rows)
    return RowLayout(
        text_buckets=tuple(text_buckets), text_order=order, text_inverse=text_slots,
        node_text_indices=inverse[node_text] if len(node_text) else node_text.clone(),
        reductions=reductions, scene_buckets=tuple(buckets), scene_rows=row_order,
        scene_inverse=scene_inverse, scene_nodes=torch.cat(nodes),
        scene_positions=torch.cat(positions), scene_lengths=slots[row_order],
        scene_offsets=torch.cat(offsets), roles=role_layout, role_present=role_flags)


def prepare_group(
    frames: list[TensorFrame],
    rows: tuple[int, ...],
    policy: bool,
    floors: Tensor,
    *,
    deduplicate_text: bool,
    layout: str = "padded-v1",
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
    return dataclasses.replace(group, layout=row_layout(group))


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
) -> PreparedBatch:
    began = time.perf_counter()
    if prepared_layout not in PREPARED_LAYOUTS:
        raise ValueError("unsupported_prepared_layout")
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
        for row in work:
            frame = frames[row]
            width = _bucket(max(text_width, frame.text_width), (8, 16, 32, 64, 128, 256, 512))
            scene = _bucket(max(scene_width, frame.entity_count), (32, 64, 128, 256, 512, 1024))
            if group and (
                len(group) >= microbatch_size
                or (len(group) + 1) * scene**2 > scene_attention_area
                or (text_count + frame.text_count) * width**2 > text_attention_area
            ):
                groups.append(prepare_group([frames[i] for i in group], tuple(group), True,
                    floor_tensor[group], deduplicate_text=deduplicate_text,
                    layout=prepared_layout))
                group, text_count, text_width, scene_width = [], 0, 0, 0
            group.append(row)
            text_count += frame.text_count
            text_width = max(text_width, frame.text_width)
            scene_width = max(scene_width, frame.entity_count)
        if group:
            groups.append(prepare_group([frames[i] for i in group], tuple(group), True,
                floor_tensor[group], deduplicate_text=deduplicate_text,
                    layout=prepared_layout))
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
