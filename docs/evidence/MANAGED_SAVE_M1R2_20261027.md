# Managed local staging and fresh parallel readback — M1R2, 2026-10-27

[PR264](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/264) changes execution evidence → Store/R2 → evaluation result transport. New Managed ledgers freeze a reusable node-local Store and bounded read concurrency; completed formal results still require this operation's complete peer typed/hash readback. Existing explicit Store locations remain exact. The [original SOP](../../python/docs/LOCAL_MODEL_TASK_FLOW.md#managed-评估并行调度与强化学习的位置) owns preparation, capacity, seven-day retention, failure recovery and the synchronous M2b seam. No pipeline, tier or reduced evidence is introduced.

The optional Managed `--slot-id` maps the existing dispatcher to a subset of its frozen plan. Validation preserves plan order, the full denominator, all-ledger cleanup fencing and not-started-only admission. It permits a paired storage check without reoffering a baseline unknown; it is not a retry or a different gameplay loop.

## Exact execution and measured cost

Runs used fixed Host46, Runtime52, original 438, original input/progress and at most four actors. Source `d3ed4067` produced the two direct batches, cold staged batch and save-only A/B; `54bf2ed0` added slot selection and produced the paired/hot batches. These are historical exact producers, not labels for later integration. No installed Workbench, recording/Q, model or Platform package was upgraded by this task.

| Ordinary four-worker batch | Offered / natural loss / unknown | Whole batch seconds | Natural games/hour | Natural preservation median seconds |
|---|---:|---:|---:|---:|
| Cold direct baseline | 8 / 6 / 2 | 410.279 | 52.647 | 78.518 |
| Cold staged, new seeds | 8 / 7 / 1 | 495.121 | 50.897 | 12.969 |
| Warm direct baseline | 8 / 6 / 2 | 478.329 | 45.157 | 102.884 |
| Warm staged, new seeds | 8 / 8 / 0 | 201.721 | 142.771 | 11.629 |

Cold staged wall time includes the first interrupted preparation's 111.002 seconds, resumed preparation and final preservation/close tail. Total preparation was 227.386 seconds; prepared model ancestry occupied 2,195,028,835 object bytes. The hot batch reused the same Store and spent 6.930 seconds preparing. Different seeds/outcomes and shared-node competition make whole-batch differences descriptive, not a controlled causal speed factor. Five-second process-ancestry observations found installed Workbench recording diagnostics briefly overlapping the cold staged/direct warm measurements; no M2a batch was observed. Own actor maximum remained four; a brief external diagnostic made five total instances. No external process was stopped.

Both baseline plans have eight rows: all six natural slots in each were paired through the normal selected-slot API, and all **12/12** matched terminal result, highest floor and stop reason. The four baseline unknown rows remained unoffered in the candidates, explicitly covering **12/16** baseline rows. Paired subsets are not used as full-batch throughput baselines. A total of 44 actual game offers (32 distinct seeds, including 12 selected natural replays) yielded 39 natural losses and five unknowns; all 44 obtained complete R2 typed readback and accepted Host closure. Every unknown seed was offered only once. These counts are not independent model-quality or RL qualification evidence.

Save-only A/B on the same 906-file sealed natural original measured direct 142.736 seconds versus staged/parallel 5.899 seconds. Original per-file bytes, artifact ID, content ID, archive hash and transfer hash were unchanged; both paths freshly read and typed-verified R2. Remote payloads already existed, so this is not a new-upload speed factor. New uploads were separately exercised by the ordinary staged batches. Parent readback remains complete; each call creates and destroys its own VerifiedReadSession.

## Why the earlier M1 number was 11.4/hour

Earlier M1 direct four-way was 5 natural/3 unknown over 1,578.636 seconds, versus this cold direct's 6/2 over 410.279 seconds. The 4.617 ratio decomposes arithmetically into natural fraction 1.2 × inverse wall-time ratio 3.8477. Both are direct/serial storage. The old longest unknown executed 1,546.727 seconds, and still ran 909.263 seconds after the other seven ended; Runtime51 lacked the same-snapshot watchdog. This batch used Runtime52's 120-second watchdog. Natural execution medians were 117.838 versus 51.922 seconds, preservation 128.221 versus 78.518, choices 161 versus 127.5, and mean max floor 14.4 versus 11.5. Seeds, Host45/46, episode budget 1800/600 and game-process competition also differed. The old unknowns and denominators remain intact; this is not local-staging causality.

## Verification and retained scope

Source and integration regressions cover fresh sessions, bounded concurrency, wrong parents, missing/tampered payloads, rate-limit failure, saved-identity recovery, sparse frozen-slot selection, invalid IDs, non-reoffer and cleanup fencing. Actual selected CI/integration results belong to the exact PR head and merge, not a prior green label. Local integration passed 81 related tests and scoped Ruff/mypy; the final PR retains its required CI gates. The final merged reader additionally checks an actual legacy R2 original at its exact runtime/model binding.

Detailed authoritative local report: `replies/M1R2-REPORT-20261027.md` in the user's handoff directory. Its delivery evidence contains full per-slot report hashes, artifact/content IDs, R2/close receipts, both eight-row comparisons, original inventories, interrupted preparation costs and process samples. Credentials, game files, weights and Human originals are not in Git. Managed defects/unknowns and whole-game equivalence remain separate work; M2b receives the existing synchronous preservation API, phase/detail timing and pending/full-readback facts.
