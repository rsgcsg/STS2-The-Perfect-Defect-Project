"""Bounded local engineering run/resume; no network, cloud or game launch."""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import torch

from stpd.workers.checkpoint_codec import decode_checkpoint

from .data import SegmentInput, digest, json_bytes, read_segments
from .export import export_model, register
from .input import Frame, fit_vocabulary, project
from .training import Trainer, maximum_difference, model_hash


def prepared(
    source: Path, sha: str, vocabulary: dict[str, Any] | None = None
) -> tuple[SegmentInput, list[Frame], list[int], dict[str, Any]]:
    data = read_segments(source, sha)
    selected = [e for e in data.examples if e.mask["N"]]
    frames = [project(e.observation) for e in selected]
    targets = [
        [c["choice_id"] for c in frame.choices].index(e.chosen)
        for frame, e in zip(frames, selected, strict=True)
    ]
    return data, frames, targets, vocabulary or fit_vocabulary(frames)


def resume(args: argparse.Namespace) -> None:
    raw = args.checkpoint.read_bytes()
    state = decode_checkpoint(raw)
    identity = state["identity"]
    vocabulary = json.loads(args.vocabulary.read_bytes())
    _, frames, targets, _ = prepared(args.source, identity["source_sha256"], vocabulary)
    trainer = Trainer(
        frames, targets, vocabulary, identity["source_sha256"], identity["total_steps"]
    )
    trainer.restore(raw)
    while trainer.step < trainer.total_steps:
        result = trainer.advance()
        print(json.dumps({"resume_step": result["step"], "loss": result["loss"]}), flush=True)
    args.out.write_bytes(trainer.checkpoint())


def run(args: argparse.Namespace) -> None:
    start = time.monotonic()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    producer_raw = args.producer_receipt.read_bytes()
    producer = json.loads(producer_raw)
    source = output / "segments.json"
    shutil.copyfile(args.source, source)
    data, frames, targets, vocabulary = prepared(source, producer["segments_sha256"])
    for key in ("source_content_id", "branch", "profile", "revision"):
        if data.receipt[key] != producer[key]:
            raise ValueError("producer/consumer identity mismatch: " + key)
    for consumer, upstream in (
        ("N", "candidate_N"),
        ("next", "next_edges"),
        ("segments", "segments"),
    ):
        if data.receipt[consumer] != producer[upstream]:
            raise ValueError("producer/consumer count mismatch: " + consumer)
    vocabulary_path = output / "vocabulary.json"
    vocabulary_path.write_bytes(json_bytes(vocabulary))
    receipt = {
        **data.receipt,
        "producer_receipt_sha256": digest(producer_raw),
        "source_file": str(args.source.resolve()),
        "snapshot_file": str(source),
        "snapshot_sha256": producer["segments_sha256"],
        "producer_identity": producer,
        "input_spec": vocabulary["input_spec"],
        "vocabulary_sha256": digest(vocabulary_path.read_bytes()),
    }
    (output / "consumption.json").write_bytes(json_bytes(receipt))
    print(
        json.dumps({"consumed": receipt["N"], "next": receipt["next"], "end": receipt["end"]}),
        flush=True,
    )
    trainer = Trainer(frames, targets, vocabulary, receipt["source_sha256"], args.steps)
    initial_hash = model_hash(trainer.model)
    trainer.model.eval()
    with torch.inference_mode():
        before = trainer.model(frames[0]).tolist()
    lengths = [
        len(trainer.model.tokenize(text)[0])
        for f in frames
        for e in f.entities
        for _, text in e.text
    ]
    (output / "lengths.json").write_bytes(
        json_bytes(
            {
                "frames": len(frames),
                "entities_min": min(len(f.entities) for f in frames),
                "entities_max": max(len(f.entities) for f in frames),
                "max_text_tokens": max(lengths),
                "tokens_in_all_text_fields": sum(lengths),
                "truncated_fields": 0,
                "effective_batch": 64,
                "execution_microbatch": 1,
                "cpu_threads": 2,
                "vocabulary_actual": trainer.model.tokenizer.get_vocab_size(),
                "vocabulary_capacity": 4096,
                "parameters": sum(p.numel() for p in trainer.model.parameters()),
            }
        )
    )
    midpoint = max(1, args.steps // 2)
    while trainer.step < args.steps:
        t = time.monotonic()
        result = trainer.advance()
        print(
            json.dumps(
                {
                    "step": result["step"],
                    "loss": result["loss"],
                    "elapsed_seconds": time.monotonic() - t,
                }
            ),
            flush=True,
        )
        if trainer.step == midpoint:
            (output / "midpoint.tensor-tree").write_bytes(trainer.checkpoint())
    checkpoint = output / "final.tensor-tree"
    checkpoint.write_bytes(trainer.checkpoint())
    trainer.model.eval()
    with torch.inference_mode():
        after = trainer.model(frames[0]).tolist()
    descriptor = export_model(trainer.model, checkpoint, output / "export", trainer.identity)
    # A replacement process restores optimizer, all used RNGs, sampler and step.
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "stpd.p1.t0",
            "resume",
            "--source",
            str(source),
            "--vocabulary",
            str(vocabulary_path),
            "--checkpoint",
            str(output / "midpoint.tensor-tree"),
            "--out",
            str(output / "resumed.tensor-tree"),
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=1800,
    )
    (output / "resume-process.log").write_text(child.stdout + child.stderr)
    expected = decode_checkpoint(checkpoint.read_bytes())
    restored = decode_checkpoint((output / "resumed.tensor-tree").read_bytes())
    difference = maximum_difference(expected, restored)
    if difference != 0.0:
        raise ValueError("replacement-process resume differs from uninterrupted reference")
    changed = initial_hash != model_hash(trainer.model)
    output_changed = before != after
    if not changed or not output_changed:
        raise ValueError("real parameter/output dependence was not demonstrated")
    proof = {
        "schema": "stpd/p1-t0-training-proof-1",
        "scope": "CPU train-only engineering",
        "initial_parameters_sha256": initial_hash,
        "final_parameters_sha256": model_hash(trainer.model),
        "parameters_changed": changed,
        "before_scores": before,
        "after_scores": after,
        "model_output_changed": output_changed,
        "first_catalog": frames[0].choices,
        "argmax_before": frames[0].choices[max(range(len(before)), key=before.__getitem__)][
            "choice_id"
        ],
        "argmax_after": frames[0].choices[max(range(len(after)), key=after.__getitem__)][
            "choice_id"
        ],
        "steps": args.steps,
        "update_examples": args.steps * 64,
        "history": trainer.history,
        "resume": {
            "replacement_process": True,
            "checkpoint_at": midpoint,
            "same_following_batches": True,
            "atol": 0.0,
            "rtol": 0.0,
            "maximum_difference_all_state": difference,
            "compared": ["parameters", "AdamW", "RNG", "sampler", "steps", "batch/loss history"],
        },
        "export": descriptor,
        "elapsed_seconds": time.monotonic() - start,
        "peak_rss_host_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "cloud_calls": 0,
        "reference_started": False,
    }
    (output / "training-proof.json").write_bytes(json_bytes(proof))
    print(
        json.dumps(
            {
                "proof": str(output / "training-proof.json"),
                "resume_difference": difference,
                "elapsed_seconds": proof["elapsed_seconds"],
            }
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--source", type=Path, required=True)
    run_parser.add_argument("--producer-receipt", type=Path, required=True)
    run_parser.add_argument("--out", type=Path, required=True)
    run_parser.add_argument("--steps", type=int, default=4)
    resume_parser = commands.add_parser("resume")
    for name in ("source", "vocabulary", "checkpoint", "out"):
        resume_parser.add_argument("--" + name, type=Path, required=True)
    register_parser = commands.add_parser("register")
    register_parser.add_argument("--export", dest="export_path", type=Path, required=True)
    register_parser.add_argument("--environment-manifest", type=Path, required=True)
    register_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "register":
        result = register(
            args.export_path, args.out, json.loads(args.environment_manifest.read_bytes())
        )
        print(json.dumps(result))
    else:
        (run if args.command == "run" else resume)(args)


if __name__ == "__main__":
    main()
