"""Bounded synthetic measurements through the normal M-std trainer.

This CLI has no Store, provider, credentials, admission or real-data input. Actual
Run measurements are emitted by execute_p1_run on the same progress/event path.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from pathlib import Path
from typing import Any

import torch

from .formal_training import MStdTrainer, Population
from .input import Frame, fit_vocabulary, project
from .spec import MStdConfig


def synthetic_frame(entities: int, *, extra_text: str = "") -> Frame:
    """A complete artificial public observation, independent of recorded data."""
    return project(
        {
            "schema": "sts2.p1/observation-1",
            "profile": "p1-global-r1",
            "revision": "r1.5",
            "status": "decision",
            "scene": {
                "family": "event",
                "content": {
                    "entities": [
                        {
                            "entity_id": f"e{index}",
                            "definition_id": f"D{index}",
                            "description": f"Gain {index + 1} Block. "
                            + (extra_text if index == 0 else ""),
                            "amount": index + 1,
                            "optional": None,
                        }
                        for index in range(entities)
                    ]
                },
            },
            "catalog": [
                {
                    "choice_id": f"c{index}",
                    "kind": "event.choose",
                    "subject": f"e{index}",
                    "target": None,
                    "label": f"Gain {index + 1} Block.",
                    "mapped": True,
                }
                for index in range(entities)
            ],
            "terminal": None,
        }
    )


def profile_trainer(trainer: MStdTrainer, updates: int) -> dict[str, Any]:
    """Advance an explicitly supplied trainer; never clone or silently restart it.

    Caller owns authorization for its inputs and updates. Durations do not enter
    the trainer's checkpoint state, model selection or data identity.
    """
    if type(updates) is not int or updates < 1:
        raise ValueError("positive_profile_update_budget_required")
    initial_step = trainer.step
    began = time.perf_counter()
    samples = []
    for _ in range(updates):
        if trainer.phase != "training":
            break
        samples.append(trainer.advance())
    checkpoint_started = time.perf_counter()
    checkpoint = trainer.checkpoint()
    checkpoint_seconds = time.perf_counter() - checkpoint_started
    elapsed = time.perf_counter() - began
    durations = [sample["timing"]["advance_seconds"] for sample in samples]
    return {
        "schema": "stpd/p1-trainer-profile-1",
        "scope": "normal_trainer_updates_and_checkpoint",
        "identity": trainer.identity,
        "requested_updates": updates,
        "initial_step": initial_step,
        "final_step": trainer.step,
        "phase": trainer.phase,
        "samples": samples,
        "wall_seconds": elapsed,
        "advance_median_seconds": statistics.median(durations) if durations else None,
        "checkpoint_seconds": checkpoint_seconds,
        "checkpoint_bytes": len(checkpoint),
        "cuda_process_peak_allocated_bytes": torch.cuda.max_memory_allocated(trainer.device)
        if trainer.device.type == "cuda"
        else None,
        "cuda_process_peak_reserved_bytes": torch.cuda.max_memory_reserved(trainer.device)
        if trainer.device.type == "cuda"
        else None,
        "scientific_verdict": "not_claimed",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--updates", type=int, default=2)
    parser.add_argument("--examples", type=int, default=64)
    parser.add_argument("--dev-examples", type=int, default=8)
    parser.add_argument("--entities", type=int, default=8)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if min(args.examples, args.dev_examples, args.entities, args.updates) < 1:
        parser.error("examples, dev-examples, entities and updates must be positive")
    began = time.perf_counter()
    # Different scene/catalog lengths exercise padding. No observation is cut.
    frames = (synthetic_frame(args.entities), synthetic_frame(args.entities + 1))
    populations = {
        split: Population(
            tuple(frames[i % 2] for i in range(count)),
            (0,) * count,
            tuple(f"synthetic-{split}-{i}" for i in range(count)),
        )
        for split, count in (("train", args.examples), ("dev", args.dev_examples), ("test", 0))
    }
    vocabulary = fit_vocabulary(list(populations["train"].frames))
    trainer = MStdTrainer(populations, vocabulary, "0" * 64, MStdConfig(device=args.device))
    preparation_seconds = time.perf_counter() - began
    result = profile_trainer(trainer, args.updates)
    result.update(
        {
            "declared_origin": "engineering_fixture",
            "real_data_gradient_updates": 0,
            "preparation_seconds": preparation_seconds,
            "platform": platform.platform(),
            "entities": [len(frame.entities) for frame in frames],
            "catalog_sizes": [len(frame.choices) for frame in frames],
        }
    )
    raw = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw, encoding="utf-8")
    else:
        print(raw, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
