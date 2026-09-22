"""Prepare and reload both token inputs from one previously authorized fixed ModelView.

No new allocation, training, Gold authorization, native action or cloud execution.
Run next to the verified local artifact closure; raw text never enters the progress log.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from spireagent.source import source_identity
from spireagent.storage.config import open_store
from stpd.fullrun.token_inputs import input_texts, load_token_inputs, publish_token_inputs
from stpd.fullrun.view_session import verified_model_views


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--view")
    source.add_argument("--public-allocation", help="publish exact public-H BC view first")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; preserve prior receipt")
    runtime = source_identity(Path(__file__).resolve().parents[1])
    store = open_store(args.store)
    report: dict = {
        "schema": "stpd/stage1a-input-preparation-v1", "producer": runtime.to_dict(),
        "status": "running", "model_view": args.view, "inputs": {}, "optimizer_steps": 0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def write() -> None:
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(args.output)

    started = time.perf_counter()
    write()
    try:
        if args.public_allocation:
            from stpd.fullrun.public_bc import publish_public_bc_view

            print("Projecting exact public Human observations from fixed allocation", flush=True)
            view = publish_public_bc_view(store, args.public_allocation, runtime)
            args.view = view.artifact_id
            report["model_view"] = args.view
            disposition = json.loads(b"".join(store.read_payload(view.payload("dispositions"))))
            report["public_admission"] = {k: v for k, v in disposition.items() if k != "rows"}
            write()
            print(json.dumps(report["public_admission"]), flush=True)
        with verified_model_views(store):
            for backbone in ("s", "pf"):
                print("Preparing and revalidating " + backbone, flush=True)
                item = publish_token_inputs(
                    store, args.view, backbone, runtime,
                    snapshot=args.snapshot if backbone == "pf" else None,
                    max_tokens=args.max_tokens,
                )
                loaded = load_token_inputs(store, item.artifact_id)
                if backbone == "pf":
                    from transformers import AutoTokenizer

                    hf = AutoTokenizer.from_pretrained(
                        str(args.snapshot), local_files_only=True, use_fast=True,
                    )
                    for sample, row in zip(loaded.samples, loaded.rows, strict=True):
                        state, actions = input_texts(sample.state_text, sample.action_texts)
                        actual = (tuple(hf.encode(state, add_special_tokens=False)),
                                  tuple(tuple(hf.encode(a, add_special_tokens=False))
                                        for a in actions))
                        if actual != (row.state, row.actions):
                            raise ValueError("pinned_tokenizer_wrapper_mismatch")
                    report["qwen_wrapper_parity"] = "all_rows_and_candidates_equal"
                report["inputs"][backbone] = {
                    "artifact_id": item.artifact_id, **item.parameters.value(),
                }
                write()
                print(json.dumps(report["inputs"][backbone]), flush=True)
        report["status"] = "completed"
    except Exception as error:
        report["status"] = "failed"
        report["error_type"] = type(error).__name__
        raise
    finally:
        report["elapsed_seconds"] = time.perf_counter() - started
        write()
        print("Preparation status: " + report["status"], flush=True)


if __name__ == "__main__":
    main()
