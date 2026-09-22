"""Historical v1 synthetic-length profile; use profile_packed_b.py for current packed B.

No human dataset, optimizer updates, game actions or cloud calls. A synthetic
choice index exercises backward only. This measures
shape/gradient feasibility only; it is not a learned model or real decision latency.
"""
from __future__ import annotations

import argparse
import gc
import json
import time
from dataclasses import asdict
from pathlib import Path

import torch

from spireagent.source import source_identity
from stpd.models.losses import listwise_rank_loss
from stpd.models.stage1a import RECIPES, build_scorer, recipe_for
from stpd.models.token_core import ScratchShape, ScratchTokenCore, TokenCore
from stpd.qwen.portable_backend import PortableQwenBackend
from stpd.qwen.readout_backend import FrozenQwenTokenCore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--device', choices=('cpu', 'mps'), default='cpu')
    parser.add_argument('--lengths', type=int, nargs='+', default=[128, 512, 1024])
    parser.add_argument('--candidates', type=int, default=2)
    args = parser.parse_args()
    if not 2 <= args.candidates <= 8 or any(not 1 <= n <= 4096 for n in args.lengths):
        parser.error('bounded probe requires 2..8 candidates and lengths 1..4096')
    if args.output.exists():
        parser.error('output already exists; preserve prior receipts')
    if args.device == 'mps' and not torch.backends.mps.is_available():
        parser.error('MPS unavailable; no implicit fallback')
    torch.set_num_threads(2)
    device = torch.device(args.device)
    producer = source_identity(Path(__file__).resolve().parents[1])
    report = {
        'schema': 'stpd/stage1a-graph-profile-v1',
        'qualification': 'synthetic_length_real_pf_weights_engineering_only',
        'producer': producer.to_dict(), 'device': args.device, 'status': 'running',
        'rows': [], 'input': 'random_valid_token_ids_not_human_decisions',
        'optimizer_steps': 0, 'seed': 1701,
        'b_execution': 'frozen-prefix-kv-local-branch-v1',
        'warmup': 'none_single_measurements_not_latency_distribution',
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def write() -> None:
        temporary = args.output.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
        temporary.replace(args.output)

    def sync() -> None:
        if args.device == 'mps':
            torch.mps.synchronize()

    backend = None
    started = time.perf_counter()
    write()
    try:
        # Scratch first, then one shared Qwen load. Every graph gets its own new head.
        for recipe_id in sorted(
            (r for r in RECIPES if r.endswith(".v1")),
            key=lambda r: recipe_for(r).backbone == "pf",
        ):
            recipe = recipe_for(recipe_id)
            report['active_recipe'] = recipe_id
            write()
            torch.manual_seed(1701)
            core: TokenCore
            if recipe.backbone == 'pf':
                if backend is None:
                    print('Loading and verifying pinned Qwen locally', flush=True)
                    backend = PortableQwenBackend(args.snapshot, device=args.device)
                    report['qwen'] = backend.runtime_summary()
                core = FrozenQwenTokenCore(backend)
            else:
                shape = ScratchShape(vocab_size=8192)
                core = ScratchTokenCore(shape)
                report['scratch_shape'] = asdict(shape)
            initial = (
                core.readout_initial()
                if isinstance(core, FrozenQwenTokenCore) and recipe.family == 'b' else None
            )
            model = build_scorer(recipe_id, core, readout_initial=initial).to(device).train()
            if isinstance(core, FrozenQwenTokenCore) and recipe.family == 'b':
                tokens = torch.arange(128, device=device)
                query = core.readout_initial().requires_grad_(True)
                weights = torch.linspace(-1, 1, core.width, device=device)
                expected = TokenCore.read_last_query(core, tokens, query)
                expected_grad = torch.autograd.grad(expected @ weights, query)[0]
                actual = core.read_last_query(tokens, query)
                actual_grad = torch.autograd.grad(actual @ weights, query)[0]
                torch.testing.assert_close(actual, expected, atol=1e-4, rtol=1e-4)
                torch.testing.assert_close(actual_grad, expected_grad, atol=1e-3, rtol=1e-4)
                report['real_qwen_prefix_parity'] = {
                    'tokens': 128,
                    'hidden_max_abs': float((actual - expected).detach().abs().max()),
                    'query_gradient_max_abs': float((actual_grad - expected_grad).abs().max()),
                    'hidden_atol': 1e-4, 'gradient_atol': 1e-3, 'rtol': 1e-4,
                }
                del expected, expected_grad, actual, actual_grad, query, weights
                write()
            for length in args.lengths:
                model.zero_grad(set_to_none=True)
                state = torch.randint(0, core.vocab_size, (length,), device=device)
                actions = tuple(
                    torch.randint(0, core.vocab_size, (24 + i,), device=device)
                    for i in range(args.candidates)
                )
                sync()
                before = time.perf_counter()
                scores = model(state, actions)
                sync()
                forward = time.perf_counter() - before
                before = time.perf_counter()
                listwise_rank_loss(scores, 0).backward()
                sync()
                backward = time.perf_counter() - before
                gradients = [p.grad for p in model.parameters() if p.requires_grad]
                if not gradients or not all(
                    g is not None and bool(torch.isfinite(g).all()) for g in gradients
                ):
                    raise ValueError('missing_or_nonfinite_trainable_gradients')
                if recipe.backbone == 'pf' and any(p.grad is not None for p in core.parameters()):
                    raise ValueError('frozen_parameter_received_gradient')
                row = {
                    'recipe': recipe_id, 'state_tokens': length,
                    'action_tokens': [a.numel() for a in actions],
                    'forward_seconds': forward, 'backward_seconds': backward,
                    'trainable_parameters': sum(p.numel() for p in model.parameters()
                                                if p.requires_grad),
                    'finite_scores': bool(torch.isfinite(scores).all()),
                }
                if args.device == 'mps':
                    row['mps_allocated_after_backward'] = torch.mps.current_allocated_memory()
                    row['mps_driver_after_backward'] = torch.mps.driver_allocated_memory()
                report['rows'].append(row)
                write()
                print(json.dumps(row), flush=True)
                del scores, gradients
            del model, core
            gc.collect()
            if args.device == 'mps':
                torch.mps.empty_cache()
        report['status'] = 'completed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error_type'] = type(exc).__name__
        # Detailed private traceback remains in the operator log, never an uploaded artifact.
        raise
    finally:
        report['elapsed_seconds'] = time.perf_counter() - started
        write()
        print('Profile status: ' + report['status'], flush=True)


if __name__ == '__main__':
    main()
