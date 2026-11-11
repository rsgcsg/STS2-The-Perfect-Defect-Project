"""C-SCALE: real online minibatches as offline-step inputs (`p3-offline-step-input-1`).

One file is one optimizer attempt exactly as the SGD process was about to run it:
the pre-step theta and Adam state, the complete prepared minibatch (rows-v2 layout
included when selected), loss/performance configuration and target KL. Replaying
it offline is the same `prepared_update` call on the same bytes, so an offline
benchmark measures the online batch distribution instead of a few light snapshots.

The format is the R42-P5 LEARNER-ENTRY snapshot (`offline-step.tensor-tree` plus an
`offline-step-input.json` receipt with its sha256) that the S0-GPU/S3 tools read,
with three added fields (`target_kl`, `minibatch`, `epoch`) and the bytes/row facts
the gate strata need. These files are diagnostics: never a CP, never Store input.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from torch import Tensor

from spireagent.json_boundary import BoundaryError

SCHEMA = "p3-offline-step-input-1"
TREE = "offline-step.tensor-tree"
RECEIPT = "offline-step-input.json"


@dataclass(frozen=True)
class OfflineDumpOptions:
    """`dataplane.sgd_offline_dump_every` selects the switch; absent = off."""

    every: int  # first non-diagnostic attempt at or after each multiple of `every` updates
    limit: int  # files per SGD process; bounds disk at limit x (state + one minibatch)
    directory: str | None  # None: `<learner work>/offline-steps`


def dump_options(dataplane: dict[str, Any]) -> OfflineDumpOptions | None:
    every = dataplane.get("sgd_offline_dump_every")
    extra = {key for key in ("sgd_offline_dump_limit", "sgd_offline_dump_directory")
             if key in dataplane}
    if every is None:
        if extra:
            raise ValueError("sgd_offline_dump_options_without_every")
        return None
    limit = dataplane.get("sgd_offline_dump_limit", 20)
    directory = dataplane.get("sgd_offline_dump_directory")
    if type(every) is not int or every < 1:
        raise ValueError("invalid_sgd_offline_dump_every")
    if type(limit) is not int or not 1 <= limit <= 10_000:
        raise ValueError("invalid_sgd_offline_dump_limit")
    if directory is not None and (not isinstance(directory, str) or not directory):
        raise ValueError("invalid_sgd_offline_dump_directory")
    return OfflineDumpOptions(every, limit, directory)


def cpu_tree(node: Any) -> Any:
    """Owned CPU copies; dataclasses become field dicts (the P5 snapshot shape)."""
    if isinstance(node, Tensor):
        return node.detach().to("cpu", copy=True).contiguous()
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        return {f.name: cpu_tree(getattr(node, f.name)) for f in dataclasses.fields(node)}
    if isinstance(node, dict):
        return {key: cpu_tree(value) for key, value in node.items()}
    if isinstance(node, tuple):
        return tuple(cpu_tree(value) for value in node)
    if isinstance(node, list):
        return [cpu_tree(value) for value in node]
    return node


def tensor_bytes(node: Any, seen: set[int] | None = None) -> int:
    """Distinct tensor bytes, the same id-deduplicated measure as `contiguous_slabs`."""
    seen = set() if seen is None else seen
    if isinstance(node, Tensor):
        if id(node) in seen:
            return 0
        seen.add(id(node))
        return node.numel() * node.element_size()
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        return sum(tensor_bytes(getattr(node, f.name), seen) for f in dataclasses.fields(node))
    if isinstance(node, dict):
        return sum(tensor_bytes(value, seen) for value in node.values())
    if isinstance(node, (list, tuple)):
        return sum(tensor_bytes(value, seen) for value in node)
    return 0


def minibatch_facts(minibatch: Any) -> dict[str, Any]:
    """Row counts and bytes per PPO row for the gate's bytes/row strata."""
    size = tensor_bytes(minibatch)
    return {"ppo_rows": minibatch.td_rows, "multi_rows": minibatch.policy_rows,
            "terminal_rows": minibatch.terminal_rows, "hp_rows": minibatch.hp_rows,
            "group_rows": [len(group.rows) for group in minibatch.groups],
            "minibatch_tensor_bytes": size,
            "minibatch_bytes_per_ppo_row": size / minibatch.td_rows if minibatch.td_rows else None,
            "layout": ("rows-v2" if minibatch.groups and minibatch.groups[0].layout is not None
                       else "padded-v1")}


def write(directory: Path, state: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    """One new directory per attempt; tree and receipt are each published by rename."""
    from .checkpoint_codec import encode_checkpoint

    if state.get("schema") != SCHEMA:
        raise BoundaryError("rl_offline_step", "offline_step_schema_changed")
    raw = encode_checkpoint(state)
    directory.mkdir(parents=True, exist_ok=False)
    digest = hashlib.sha256(raw).hexdigest()
    for name, data in ((TREE, raw), (RECEIPT, (json.dumps(
            {"schema": SCHEMA, **receipt, "sha256": digest, "bytes": len(raw)},
            indent=2, sort_keys=True) + "\n").encode())):
        partial = directory / ("." + name + ".partial")
        with partial.open("xb") as stream:
            os.chmod(partial, 0o600)
            stream.write(data)
        partial.replace(directory / name)
    return {"path": str(directory), "sha256": digest, "bytes": len(raw)}


def _minibatch(value: dict[str, Any]) -> Any:
    from stpd.p1.tensor_input import JointNodes, Reduction

    from .prepared_batch import PreparedGroup, PreparedMinibatch, RowLayout

    groups = []
    for group in value["groups"]:
        joint = group["joint"]
        layout = group.get("layout")
        groups.append(PreparedGroup(**{
            **group, "joint": JointNodes(joint["tensors"], {
                key: Reduction(**item) for key, item in joint["reductions"].items()}),
            "layout": RowLayout(**layout) if layout is not None else None}))
    return PreparedMinibatch(**{**value, "groups": tuple(groups)})


def load(root: Path) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    """CPU-only decode (no model, no CUDA); also reads the historical P5 snapshots."""
    from .checkpoint_codec import decode_checkpoint

    raw = (root / TREE).read_bytes()
    receipt = json.loads((root / RECEIPT).read_bytes())
    if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
        raise BoundaryError("rl_offline_step", "offline_step_integrity_changed")
    value = decode_checkpoint(raw)
    if value.get("schema") != SCHEMA:
        raise BoundaryError("rl_offline_step", "offline_step_schema_changed")
    return value, _minibatch(value["minibatch"]), receipt


def replay(value: dict[str, Any], minibatch: Any, *, device: str = "cpu",
           target_kl: float | None = None) -> tuple[Any, Any, dict[str, Any]]:
    """The same `prepared_update` call on the dumped bytes; returns model, optimizer, result."""
    from .batch_preparation import stage_batch
    from .checkpoint_state import ModelConstruction, construct_model
    from .loss import LossConfig, prepared_update
    from .performance import PerformanceConfig

    construction = dict(value["construction"])
    if value.get("sgd_execution") is not None:  # the SGD process's own model execution
        construction["numerical"] = {**construction["numerical"],
                                     "execution": value["sgd_execution"]}
    model = construct_model(ModelConstruction(**construction))
    model.load_state_dict(value["weights"], strict=True)
    model = model.to(device).eval().requires_grad_(True)
    groups = value["optimizer"]["param_groups"]
    optimizer = torch.optim.AdamW(
        [{"params": [p for name, p in model.named_parameters()
                     if not name.startswith("rl4_heads.")], "lr": groups[0]["lr"]},
         {"params": list(model.rl4_heads.parameters()), "lr": groups[1]["lr"]}])
    optimizer.load_state_dict(value["optimizer"])
    staged = stage_batch(minibatch, torch.device(device))[0] if device != "cpu" else minibatch
    result = prepared_update(
        model, optimizer, staged, config=LossConfig(**value["loss"]),
        performance=PerformanceConfig(**value["performance"]),
        target_kl=value.get("target_kl", 0.03) if target_kl is None else target_kl,
        diagnostics=value["diagnostics"], collect_policy_rows=False)
    return model, optimizer, result


# Compile warm-up coverage (`dataplane.sgd_compile.warmup`, at most 8 dumps). With
# `prepared_compile` and levers the block and node builder compile shape-generic, yet
# dynamo still specializes on a few facts (V3 T6 recompile probes 1458888/1458938):
# the inductor `can_fuse` size guard (192 * size >= 5242880 at embed 192), 0/1-sized
# dimensions, the per-group cross fold (catalog <= 64) and value-only groups.
FUSE_GUARD_SIZE = -(-5242880 // 192)  # 27307


def _size_class(value: int) -> str:
    return "0" if value == 0 else "1" if value == 1 else "2" if value == 2 else "n"


def warmup_signatures(minibatch: Any, *, bucket_rows: int) -> set[tuple[str, ...]]:
    """Compiled-shape classes this minibatch exercises under `bucket_rows` (rows-v2)."""
    from stpd.p1.rows_kernels import FOLD_CATALOG_LIMIT

    from .batch_preparation import row_layout

    found: set[tuple[str, ...]] = set()
    for group in minibatch.groups:
        layout = row_layout(group, bucket_rows=bucket_rows)
        catalog = int(group.tensors["action_types"].shape[1]) if group.policy else 0
        kind = ("value" if not group.policy
                else "fold" if catalog <= FOLD_CATALOG_LIMIT else "wide")
        nodes = int(group.joint.tensors["kinds"].numel())
        found.add(("nodes", "large" if nodes >= FUSE_GUARD_SIZE else "small"))
        for index, (rows, _width, _longest, tokens) in enumerate(layout.scene_buckets):
            terms = (tuple(_size_class(int(layout.roles[key][3][index]))
                           for key in ("source", "target")) if group.policy else ("-", "-"))
            found.add(("block", kind, "large" if tokens >= FUSE_GUARD_SIZE else "small",
                       _size_class(rows), *terms))
    return found


def warmup_cover(paths: list[Path], *, bucket_rows: int, limit: int = 8) -> dict[str, Any]:
    """Greedy choice of at most `limit` dumps covering the most shape classes.

    Reports every class seen and the ones the choice leaves uncovered (those still
    compile on first use online; nothing is hidden or treated as covered).
    """
    if type(limit) is not int or not 1 <= limit <= 8:
        raise ValueError("invalid_warmup_limit")
    seen: dict[str, set[tuple[str, ...]]] = {}
    for path in paths:
        _value, minibatch, _receipt = load(path)
        seen[str(path)] = warmup_signatures(minibatch, bucket_rows=bucket_rows)
    every = set().union(*seen.values()) if seen else set()
    chosen: list[str] = []
    covered: set[tuple[str, ...]] = set()
    while len(chosen) < limit:
        best = max(sorted(set(seen) - set(chosen)), default=None,
                   key=lambda path: len(seen[path] - covered))
        if best is None or not seen[best] - covered:
            break
        chosen.append(best)
        covered |= seen[best]
    return {"bucket_rows": bucket_rows, "warmup": chosen, "classes": len(every),
            "covered": len(covered), "uncovered": sorted("/".join(c) for c in every - covered),
            "scanned": len(seen)}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m stpd.rl.offline_step")
    commands = parser.add_subparsers(dest="command", required=True)
    cover = commands.add_parser("warmup-cover", help="choose <= 8 dumps for sgd_compile.warmup")
    cover.add_argument("--bucket-rows", type=int, required=True,
                       help="performance.prepared_bucket_rows of the run being warmed")
    cover.add_argument("--limit", type=int, default=8)
    cover.add_argument("paths", type=Path, nargs="+")
    args = parser.parse_args(argv)
    print(json.dumps(warmup_cover(args.paths, bucket_rows=args.bucket_rows, limit=args.limit),
                     indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
