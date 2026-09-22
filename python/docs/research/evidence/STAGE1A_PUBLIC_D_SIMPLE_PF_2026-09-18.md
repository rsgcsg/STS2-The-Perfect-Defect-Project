# Public compact v2 D-Simple-PF pilot and paired analysis

Engineering only; producer `872fda037753f254e33df50ac30f87135a02d558`.
Relative to the S pilot producer, this commit changes documentation only. It uses
the same public v2 view `2553accb6bf393809f0f813f94f73a1ccd266cdb6824a38e1591b1a1ebd478fd`,
49 train / 16 dev decisions, seed 1701, ten updates and MPS FP32. Token budget 16384;
no truncation. Frozen Qwen revision is `da87bfb608c14b7cf20ba1ce41287e8de496c0cd`.

| Artifact | Identity |
|---|---|
| PF input | `e5eb679cd2fb4873010af2be1995539cafacff47572d8d9b64b5aa62028b303e` |
| Run | `10671e14f063b14f2023723da2b46d3e1217a88e6fbccc63616623323570914b` |
| Checkpoint | `16323e724b32661ce89b75a5f12c470c47c6d4a171eef0464f1bb64fa2927939` |
| Result | `298fa42eef2209353d365e92286e132c9bd1d869e11a3c841dcbeac235016dff` |
| Model | `b15a6abc35a9d3b4af532d3fc5e45002e4566350950fff7a6961c40488b6c6da` |
| Evaluation | `f799db0ee3512e14985d1156ff2df473555712e1664c3dbf888d4295d9b0f651` |

## Measured comparison

The comparison reader verifies completed artifact closure, the shared view and each
dev transition before joining results. This is a rebuildable analysis, not a second
experiment database. The dev set contains just one independent run.

| Measurement | D-Simple-S | D-Simple-PF |
|---|---:|---:|
| Dev Top-1 | 8/16 | 4/16 |
| Multi-candidate Top-1 | 6/14 | 2/14 |
| Dev NLL | 1.720264 | 1.721246 |
| Dev MRR | 0.642783 | 0.381424 |
| Worker attempt including model construction, updates and evaluation | 9.769 s | 76.375 s |
| CLI including preceding input validation and preparation of run metadata | 30.349 s | 97.733 s |

PF wrapper wall time is 99.165 seconds. The S wrapper also prepared both token input
artifacts; its 74.984 seconds cannot be compared to PF's wrapper as training cost.
Neither timing is total research cost or controlled throughput. PF stderr contains
normal weight-loading progress, not a training exception.

Both NLLs are near the uniform-legal 1.721220 baseline. Ten updates and one dev run
are insufficient to judge the value of pretraining. PF and S also differ in size,
tokenizer and trainable capacity; this is a system comparison, not an isolated
pretraining experiment. No test/Gold labels or new gameplay outcomes are used.

## Bounded export check

The restored checkpoint's trainable weights equal the published weights, and every
Qwen core parameter is frozen. The three-file export loads with the pinned Qwen
snapshot. One original public dev snapshot with 18 candidates reproduces checkpoint
scores exactly; reversing candidates reverses scores exactly. This is one-snapshot
coverage, not the S pilot's sixteen-snapshot coverage. Scorer load took 1.582 seconds
and that decision took 5.627 seconds; neither is a latency benchmark.

Private receipt directory: `stage1a-public-dsimple-pf-20260918-184522` under the research
root; `bounded-export-check.json` and `comparison.json` retain the details. Export:
`stage1a-exports/dsimple-pf-public-v2`. No raw data or weights enter Git. Actual
Workbench/Mod execution is still pending. Next run is the approved B-PF configuration
on the same view; it is not a second Transformer stacked on Qwen.
