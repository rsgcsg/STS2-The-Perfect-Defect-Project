"""Manual CPU cost experiment for W1, with no GPU or World calls.

Run as a module with --input TRAIN-prefix.jsonl --output receipt.json. This is
not a pytest speed/equivalence gate. The frozen corpus supplies real public input
shapes; repeated 64-row windows and synthetic mu/aux labels are disclosed.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import os
import platform
import random
import resource
import time
from pathlib import Path

import torch
from spireagent.rl_contracts import RunSequence
from spireagent.rl_exchange import Exchange
from stpd.p1.data import digest, json_bytes
from stpd.p1.execution import MStdExecution
from stpd.p1.export import input_spec, runtime_projection
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.model import MStd
from stpd.p1.sampling import distribution
from stpd.p1.vocabulary import public_vocabulary
from stpd.rl.batch_preparation import BatchPreparer, read_auxiliary_inputs
from stpd.rl.loss import LossConfig, prepared_update
from stpd.rl.performance import PerformanceConfig
from stpd.rl.prepared_batch import AuxiliarySelection, BatchMember, PrepareRequest
from stpd.rl.segments import minibatch_members
from test_rl41_w1 import training_seed


def build_templates(model, corpus):
    compiler = FrameCompiler(model.vocabulary)
    spec = input_spec(model.vocabulary, runtime_projection(model.vocabulary))
    inputs, frames, floors = [], [], []
    floor = 0
    for i, row in enumerate(corpus):
        observation = row["observation"]
        observed = ((observation.get("run") or {}).get("content") or {}).get("floor")
        floor = max(floor, observed if type(observed) is int else 0)
        inputs.append(
            {
                "acquisition_id": f"sample-{i}",
                "consumption_unit": {"id": f"u-{i}"},
                "input_spec": spec,
                "observation": observation,
                "catalog": observation["catalog"],
            }
        )
        frames.append(compiler.compile(observation))
        floors.append(floor)
    scores, probabilities = [], []
    with torch.no_grad():
        for start in range(0, len(frames), 16):
            output, logits, _ = model.forward_rl4(
                frames[start : start + 16], floors[start : start + 16]
            )
            scores.extend(value.tolist() for value in output)
            probabilities.extend(logits.softmax(-1).tolist())
        _, tail, _ = model.forward_rl4(frames[-1:], floors[-1:], policy=False)
    result = []
    for i, value in enumerate(inputs):
        raw, mass = distribution(scores[i], 1.0, 0.0)
        successor = inputs[i + 1] if i + 1 < len(inputs) else {**value, "acquisition_id": "tail"}
        result.append(
            {
                "schema": "sts2.policy-runtime/verified-global-transition-1",
                "run_id": "shape-sample",
                "step_index": i,
                "input": value,
                "successor": successor,
                "directive": {
                    "directive": {
                        "selection": {"action_id": value["catalog"][0]["choice_id"]},
                        "scores": {"values": scores[i]},
                    }
                },
                "result": {"status": "completed"},
                "sampling": {
                    "mode": "temperature_uniform",
                    "temperature": 1.0,
                    "uniform_mix": 0.0,
                    "probabilities": mass,
                    "model_probabilities": raw,
                    "model_logits": scores[i],
                    "selected_probability": mass[0],
                },
                "behavior": {
                    "scores": scores[i],
                    "floor": floors[i],
                    "outcome_probabilities": probabilities[i],
                    "compiled_input": compiler.encode(value["observation"]),
                    "acquisition_id": value["acquisition_id"],
                    "input_sha256": digest(json_bytes(value)),
                    "observation_sha256": digest(json_bytes(value["observation"])),
                    "catalog_sha256": digest(json_bytes(value["catalog"])),
                    "consumption_unit": value["consumption_unit"]["id"],
                },
            }
        )
    return compiler, result, tail[0].softmax(-1).tolist(), floors[-1], frames


def publish_request(exchange, compiler, templates, tail, floor, number):
    generation = "g-benchmark"
    mu = {
        "run": exchange.run,
        "training_generation": generation,
        "version": 57,
        "updates": 362,
        "snapshot_sha256": "a" * 64,
    }
    members = []
    for i in range(16):
        identity = f"b{number}-a{i}/0"
        run_id = f"b{number}-episode-{i}"
        # Copies are confined to the benchmark producer, outside measured prepare.
        steps = copy.deepcopy(templates)
        for step in steps:
            step["run_id"] = run_id
            step["behavior"].update(mu=mu, lease=identity)
        value = {
            "run": exchange.run,
            "training_generation": generation,
            "identity": {"intent_id": run_id, "seed": training_seed(run_id)},
            "training_split": "TRAIN",
            "run_id": run_id,
            "segment_id": identity,
            "rows": len(steps),
            "mu": mu,
            "steps": steps,
            "terminal": None,
            "hp_battles": [],
            "previous_segment_artifact_id": None,
            "compiler_identity": compiler.identity,
            "compiled_boundary": compiler.encode(steps[-1]["successor"]["observation"]),
            "original_commit": {
                "run_id": run_id,
                "segment_id": identity,
                "representation": "live-prefix",
                "journal_prefix_sha256": "d" * 64,
                "behavior_trace_prefix_sha256": "e" * 64,
            },
            "bootstrap": {
                "mu_snapshot_sha256": mu["snapshot_sha256"],
                "successor_input_sha256": digest(json_bytes(steps[-1]["successor"])),
                "floor": floor,
                "outcome_probabilities": tail,
            },
        }
        payload = exchange.put_hot_segment(value, owner="benchmark")
        members.append(
            BatchMember(identity, payload, run_id, len(steps), mu["snapshot_sha256"], run_id)
        )
    auxiliary = []
    for kind, label, limit in (("terminal", 49.0, 2), ("hp", 0.42, 1)):
        for local in range(128):
            member, row = members[local % 16], (local // 16) * 8
            auxiliary.append(
                AuxiliarySelection(
                    {
                        "run_id": member.run_id,
                        "segment_id": member.segment_id,
                        "training_generation": generation,
                        "row": row,
                        "key": f"{member.run_id}:{row}",
                    },
                    kind,
                    label,
                    0,
                    limit,
                )
            )
    order = list(range(1024))
    random.Random(37 + number).shuffle(order)
    parts = tuple(tuple(order[i] for i in group) for group in minibatch_members(1024))
    return PrepareRequest(
        RunSequence(exchange.run, generation, number),
        tuple(members),
        tuple(auxiliary),
        parts,
        tuple(i % 4 for i in range(256)),
        compiler.identity,
        random.Random(number).getstate(),
        512 * 1024**2,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 4])
    parser.add_argument("--batches", type=int, default=3)
    parser.add_argument("--compute", action="store_true")
    parser.add_argument("--disjoint-auxiliary", action="store_true")
    args = parser.parse_args()
    raw = args.input.read_bytes()
    corpus = [json.loads(line) for line in raw.splitlines()]
    assert len(corpus) == 64 and all(row["reference"]["split"] == "train" for row in corpus)
    torch.set_num_threads(1)
    torch.manual_seed(37)
    model = MStd(
        public_vocabulary(),
        MStdExecution(
            implementation="experimental-packed-v1",
            microbatch_size=32,
            scene_attention_area=67108864,
            text_attention_area=67108864,
            cpu_threads=1,
        ),
    ).eval()
    model.enable_rl4_heads(7)
    compiler, templates, tail, floor, frames = build_templates(model, corpus)
    peer = {"kind": "local", "directory": str(args.output.parent / "benchmark-peer")}
    exchange = Exchange(peer, "w1-benchmark")
    measurements = []
    last_batch = None
    for workers in args.workers:
        preparer = BatchPreparer(
            model.vocabulary,
            workers=workers,
            threads=1,
            peer=peer,
            run=exchange.run,
            scratch=args.output.parent,
        )
        try:
            for iteration in range(args.batches):
                request = publish_request(
                    exchange, compiler, templates, tail, floor, workers * 10 + iteration
                )
                auxiliary_members = []
                if args.disjoint_auxiliary:
                    for extra in range(8):
                        auxiliary_members.extend(
                            publish_request(
                                exchange,
                                compiler,
                                templates,
                                tail,
                                floor,
                                workers * 10000 + iteration * 100 + extra,
                            ).members
                        )
                    auxiliary = []
                    for kind, label, limit, row in (("terminal", 49.0, 2, 0), ("hp", 0.42, 1, 1)):
                        for member in auxiliary_members:
                            auxiliary.append(
                                AuxiliarySelection(
                                    {
                                        "run_id": member.run_id,
                                        "segment_id": member.segment_id,
                                        "training_generation": "g-benchmark",
                                        "row": row,
                                        "key": f"{member.run_id}:{row}",
                                    },
                                    kind,
                                    label,
                                    0,
                                    limit,
                                )
                            )
                    request = dataclasses.replace(request, auxiliary=tuple(auxiliary))
                materialize_started = time.perf_counter()
                futures = [
                    preparer.pool.submit(read_auxiliary_inputs, member, (0, 1))
                    for member in auxiliary_members
                ]
                auxiliary_inputs = tuple(item for future in futures for item in future.result())
                materialize_seconds = time.perf_counter() - materialize_started
                began = time.perf_counter()
                batch = preparer.submit(request, auxiliary_inputs).result()
                wall = time.perf_counter() - began
                sample = {
                    "workers": workers,
                    "iteration": iteration,
                    "cold_process_start": iteration == 0 and not args.disjoint_auxiliary,
                    "auxiliary_materialization_seconds": materialize_seconds,
                    "auxiliary_snapshot_bytes": sum(len(item.body) for item in auxiliary_inputs),
                    "wall_seconds": wall,
                    "timing": batch.timings,
                    "rows": len(batch.metadata),
                    "disjoint_auxiliary_sources": len(auxiliary_members),
                    "auxiliary_rows": len(batch.request.auxiliary),
                    "payload_bytes": batch.payload_bytes,
                    "worker_pids": batch.worker_pids,
                    "groups": sum(len(mb.groups) for mb in batch.minibatches),
                    "scene_widths": sorted(
                        {
                            g.tensors["scene_mask"].shape[1]
                            for mb in batch.minibatches
                            for g in mb.groups
                        }
                    ),
                    "catalog_widths": sorted(
                        {
                            g.tensors["action_types"].shape[1]
                            for mb in batch.minibatches
                            for g in mb.groups
                            if g.policy
                        }
                    ),
                }
                assert os.getpid() not in batch.worker_pids
                measurements.append(sample)
                print(json.dumps(sample), flush=True)
                for member in (*request.members, *auxiliary_members):
                    exchange.release_hot_payload(member.payload, "benchmark")
                last_batch = batch
        finally:
            preparer.shutdown()
    compute = []
    if args.compute:
        optimizer = torch.optim.AdamW(model.parameters(), lr=4e-5, weight_decay=0)
        performance = PerformanceConfig(
            packed=True,
            tensor_cache=True,
            joint_nodes=True,
            microbatch_size=32,
            deduplicate_text=True,
            scene_attention_area=67108864,
            text_attention_area=67108864,
        )
        for i, minibatch in enumerate(last_batch.minibatches):
            result = prepared_update(
                model,
                optimizer,
                minibatch,
                config=LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01),
                performance=performance,
                diagnostics=False,
            )
            compute.append(result)
            print(json.dumps({"compute_minibatch": i, **result}), flush=True)
    receipt = {
        "scope": (
            "CPU-only, sixteen real TRAIN64 shape windows, seeded random MStd+RL4 heads; "
            "synthetic acting and auxiliary labels, no World or formal training"
        ),
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "platform": platform.platform(),
        "torch": torch.__version__,
        "threads_per_worker": 1,
        "cuda_seconds": 0,
        "World_calls": 0,
        "rows": 1024,
        "auxiliary_rows": 256,
        "disjoint_auxiliary": args.disjoint_auxiliary,
        "measurements": measurements,
        "compute": compute,
        "entity_counts_64": [frame.entity_count for frame in frames],
        "max_rss_raw": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
