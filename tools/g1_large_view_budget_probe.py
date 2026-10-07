"""Scale the existing synthetic page fixture; no game, training or network I/O."""

import argparse
import hashlib
import json
import math
from pathlib import Path

import tokenizers
from g1_input_budget_probe import fixture
from tokenizers import Tokenizer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--tokenizer-revision", required=True)
    args = parser.parse_args()
    tok = Tokenizer.from_file(args.tokenizer)
    tok.no_truncation()
    tok.no_padding()
    results = []
    for count in (200, 500, 1000, 10000):
        f = fixture(
            f"pile_{count}",
            cards=count,
            enemies=0,
            relics=50,
            potions=5,
            statuses=0,
            orbs=0,
            mode="pile",
        )
        state = f["state_text"]
        catalog = "\n".join(f["candidate_texts"])
        tokens = len(tok.encode(state, add_special_tokens=False).ids)
        frame = json.dumps(f["frame"], ensure_ascii=False, separators=(",", ":")).encode()
        total_action_bytes = sum(len(a.encode()) + 2 for a in f["candidate_texts"])
        results.append(
            {
                "card_count": count,
                "assumptions": f["assumptions"],
                "state_utf8_bytes": len(state.encode()),
                "frame_json_bytes": len(frame),
                "state_reference_bpe_tokens": tokens,
                "state_plus_catalog_reference_bpe_tokens": len(
                    tok.encode(state + "\n可选操作：\n" + catalog, add_special_tokens=False).ids
                ),
                "candidate_count": len(f["candidate_texts"]),
                "candidate_byte_tokens": total_action_bytes,
                "candidate_cnn_mac_only_d384_k3": total_action_bytes * 3 * 384 * 384,
                "card_entity_matrix_fp32_bytes_d384": count * 384 * 4,
                "dense_entity_attention_one_layer_fp16_six_heads_bytes": count * count * 6 * 2,
                "naive_text_attention_one_layer_fp16_six_heads_bytes": tokens * tokens * 6 * 2,
                "pages_if_128_members": math.ceil(count / 128),
                "state_text_sha256": hashlib.sha256(state.encode()).hexdigest(),
            }
        )
    print(
        json.dumps(
            {
                "schema": "g1-large-synthetic-view-budget-v1",
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "fixture_source_sha256": hashlib.sha256(
                    Path(__file__).with_name("g1_input_budget_probe.py").read_bytes()
                ).hexdigest(),
                "tokenizer": {
                    "repo": "Qwen/Qwen3-0.6B",
                    "revision": args.tokenizer_revision,
                    "sha256": hashlib.sha256(Path(args.tokenizer).read_bytes()).hexdigest(),
                    "library_version": tokenizers.__version__,
                },
                "limitations": [
                    "Synthetic sizing and operation-count arithmetic only, not observed gameplay.",
                    "No neural inference, GPU memory allocation, latency or throughput measured.",
                    "Attention matrices are naive materialized estimates, "
                    "not actual kernel memory.",
                    "Entity matrix excludes local text, relations, W, layers, "
                    "gradients and optimizer.",
                    "Page count excludes metadata, catalog and dependent object reads.",
                    "Reference tokenizer is not the future train-only scratch tokenizer.",
                ],
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
