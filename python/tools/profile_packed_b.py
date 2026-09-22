"""No-update B-PF v2 parity and real-input cost probe. Never resumes training."""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import torch

from spireagent.source import source_identity
from spireagent.storage.config import open_store
from stpd.fullrun.token_inputs import load_token_inputs
from stpd.models.losses import listwise_rank_loss
from stpd.models.stage1a import build_scorer
from stpd.models.token_core import TokenCore
from stpd.qwen.portable_backend import PortableQwenBackend
from stpd.qwen.readout_backend import FrozenQwenTokenCore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--device", choices=("cpu", "mps"), default="mps")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("preserve previous profile")
    producer = source_identity(Path(__file__).resolve().parents[1])
    report: dict = {"schema": "stpd/packed-b-profile-v1", "status": "running",
                    "producer": producer.to_dict(), "input_id": args.inputs,
                    "optimizer_steps": 0, "recipe": "stage1a.b.pf.v2", "rows": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def write() -> None:
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2))
        temporary.replace(args.output)

    def sync() -> None:
        if args.device == "mps":
            torch.mps.synchronize()

    def memory() -> dict:
        return ({"allocated_bytes": torch.mps.current_allocated_memory(),
                 "driver_bytes": torch.mps.driver_allocated_memory()}
                if args.device == "mps" else {})

    write()
    try:
        torch.set_num_threads(2)
        inputs = load_token_inputs(open_store(args.store), args.inputs)
        if inputs.manifest.parameters.value()["backbone"] != "pf":
            raise ValueError("PF input required")
        torch.manual_seed(1701)
        backend = PortableQwenBackend(args.snapshot, device=args.device)
        core = FrozenQwenTokenCore(backend, max_tokens=16384)
        model = build_scorer("stage1a.b.pf.v2", core,
                             readout_initial=core.readout_initial()).to(args.device).train()
        # Real pinned weights, small independent reference; no optimizer update.
        state = torch.arange(64, device=args.device)
        actions: tuple[torch.Tensor, ...] = (
            torch.arange(8, device=args.device), torch.arange(11, device=args.device),
        )
        query = core.readout_initial().requires_grad_(True)
        full = TokenCore.read_action_queries(core, state, actions, query)
        g_full = torch.autograd.grad(full.square().mean(), query)[0]
        optimized = core.read_action_queries(state, actions, query)
        g_optimized = torch.autograd.grad(optimized.square().mean(), query)[0]
        torch.testing.assert_close(optimized, full, atol=1e-4, rtol=1e-4)
        torch.testing.assert_close(g_optimized, g_full, atol=1e-3, rtol=1e-4)
        report["real_weight_parity"] = {
            "hidden_max_abs": float((optimized - full).abs().max().detach()),
            "query_gradient_max_abs": float((g_optimized - g_full).abs().max()),
            "hidden_atol": 1e-4, "gradient_atol": 1e-3, "rtol": 1e-4,
        }
        del full, optimized, g_full, g_optimized, query
        order = [i for i, sample in enumerate(inputs.samples) if sample.split == "train"]
        random.Random("stage1a:1701:0").shuffle(order)
        for step in (1, 2, 3):
            index = order[step - 1]
            row = inputs.rows[index]
            report["active_original_step"] = step
            write()
            phases: list[dict] = []
            start = [0.0]

            def before(*_args: object, start: list[float] = start) -> None:
                sync()
                start[0] = time.perf_counter()

            def after(*_args: object, start: list[float] = start,
                      phases: list[dict] = phases) -> None:
                sync()
                phases.append({"seconds": time.perf_counter() - start[0], **memory()})

            hook1 = core.model.register_forward_pre_hook(before)
            hook2 = core.model.register_forward_hook(after)
            state = torch.tensor(row.state, device=args.device)
            actions = tuple(torch.tensor(a, device=args.device) for a in row.actions)
            model.zero_grad(set_to_none=True)
            sync()
            started = time.perf_counter()
            try:
                scores = model(state, actions)
            finally:
                hook1.remove()
                hook2.remove()
            sync()
            forward_seconds = time.perf_counter() - started
            started = time.perf_counter()
            loss = listwise_rank_loss(scores, inputs.samples[index].chosen_index)
            loss.backward()
            sync()
            if len(phases) != 2 or any(p.grad is not None for p in core.parameters()):
                raise ValueError("packed_calls_or_frozen_scope")
            if not all(p.grad is not None and bool(torch.isfinite(p.grad).all())
                       for p in model.parameters() if p.requires_grad):
                raise ValueError("invalid_trainable_gradients")
            measurement = {"original_step": step, "state_tokens": len(row.state),
                           "action_lengths": [len(a) for a in row.actions],
                           "packed_tokens": len(row.state) + sum(len(a) + 1 for a in row.actions),
                           "forward_seconds": forward_seconds,
                           "backward_seconds": time.perf_counter() - started,
                           "fixed_pass": phases[0], "readout_pass": phases[1],
                           "after_backward": memory()}
            report["rows"].append(measurement)
            print(json.dumps(measurement), flush=True)
            del scores, loss
            write()
        report["status"] = "completed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        write()


if __name__ == "__main__":
    main()
