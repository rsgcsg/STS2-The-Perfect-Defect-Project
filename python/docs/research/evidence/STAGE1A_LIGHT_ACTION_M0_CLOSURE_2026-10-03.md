# Stage 1a light-action M0: bounded train-to-game chain closure — 2026-10-03

## Result and scope

This closes only the bounded train-to-game evidence chain recorded here for this
round. It does not mark Stage 1a complete or establish full-scene/native coverage or
model strength.

A new public-snapshot light-action M0 input contains 3,000 train decisions and 16 dev
decisions. The exact scratch D-Simple run completed 9,000 optimizer updates, produced
model `aadfbbbd2b9e2d3da30e736b2868db61e04fbca6efb926641dce919a220818e9`, and was
scored with the CPU weights-only path. A later local Workbench registration and one-step
native canary delivered one `play` action and observed its successor; it ended back in
Human mode with the controller released. A distinct profile-3 canary then delivered
nine OneStep actions and sealed its own evidence. These are separate runs, not a
combined ten-step run. They do not complete Stage 1a or qualify a winning policy,
full-game behavior, broad native coverage, or model quality.

The model view and its dev sample belong to the same training-purpose dataset/allocation.
The evaluation manifest says `engineering_only`,
`within_training_purpose_allocation`, `native_run_independence=false`,
`physical_game_independence=unresolved`, `historical_external_exposure=unknown`, and
`clean_held_out_claim=false`. The serializer is
`stpd-public-snapshot-compact-v2` with status `provisional`. The 16-row aggregate is
therefore diagnostic engineering evidence, not an independent holdout.

## Artifact identities

| Stage | Immutable identity |
|---|---|
| Producer source / lock | `add0c012bd5b2f25ef3a36011d11d630a294e169` / `d5ff237e7a4bed931ae726ebe740b05cf1a1089129c2ca82b43fe894fc5a9218` |
| Dataset / allocation / ModelView | `cf24eb3b9c232265733bdcfef971965c6cca8f8ebeed7cdb4000bb78efa82f79` / `9fcf9b88fd9b6e9f3f327f6f289a400ee67afabf1a9b764c2199d1286818a75a` / `d69551e03ce32ebe57fc7a5c6221fc2e6c7c7afea8d59279426e5376a07621f8` |
| TrainingInput | `e323ab545a9dfa0470e1b423e15b36378c8c11866b56e4eccef2338229c62ec8` (`train=3000`, `dev=16`, `fit_scope=train_only`, `purpose=engineering`) |
| Run / training operation | `a9aaa206f735bec873f830c7056b6b733f73e55f349fc8c4215d69d7b362e980` / `d8466e81100e49449b781073e559e462` |
| Checkpoint / model | `38d7476ce7c458ca555b57da6fc644dfdc6d1acc95c3a0afc84d93dcd4b2c0db` / `aadfbbbd2b9e2d3da30e736b2868db61e04fbca6efb926641dce919a220818e9` |
| Offline evaluation / RunResult | `22aa697b93b4f652fdc3b03379a8bb38df48c75a5740c586b81ea637067d8a95` / `daa39369462f1387a25879229f29a8bf30ba9e38113e7fde99af97142dfd6a4f` |

The run manifest records recipe `stage1a.dsimple.light-action.m0.s.v1`, seed 1701,
`device=cuda`, and 9,000 configured steps. Only the 3,000 training decisions were
sent to Modal; the 16 dev decisions were scored on CPU. The TrainingInput records
train-only model fitting and tokenizer scope, with `purpose=engineering` within the
training-purpose allocation. This is one bounded worker run, not a capacity or
throughput qualification. The evaluation manifest independently records
`device=cpu`, `mode=weights_only`. No new training or evaluation was run to write this
document.

## Reported metrics and comparison boundary

| Evaluation | Decisions | Top-1 | NLL | Evidence status |
|---|---:|---:|---:|---|
| 9,000-step model, current dev summary | 16 | 0.541667 | 0.980435 | Read from `eval.summary.overall`; engineering-only, not independent |
| Same run's step-3,000 checkpoint on the new fixed-16 dev slice | 16 | 0.458333 | 0.930856 | Analysis `3e8bbf9b9deb27aa72d21f85e1eccbddddb3715ddf10f5118fbd839d0e0ba46c`, `summary.overall` |
| Uniform legal-candidate baseline on that dev slice | 16 | 0.362605 | 1.308335 | `baselines.uniform_legal.overall` in final evaluation payload `22aa697b93b4f652fdc3b03379a8bb38df48c75a5740c586b81ea637067d8a95` |
| Same run's step-3,000 checkpoint on the historical fixed-16 slice | 16 | 0.375000 | 1.538898 | Analysis `a7a4be99c2df701404c694a694524d5096407400dc2ba7008b4f4a9bce047190`, `summary.overall` |

The old fixed-16 result is the same training run's step-3,000 checkpoint, not a
different earlier model; the 9,000-step checkpoint was not evaluated on that old-16
slice. The new-16 and old-16 comparisons remain separate cohorts. The current
summary's bootstrap status is `unknown`
(`native_run_independence_unknown_across_sessions`, four reported groups).

## Export, registration, and one-step native receipt

Export operation `d520164983ac4909add84ce2be749ea3` completed with a 32,636,244-byte
package (`0a34a42cefdec37ad55fda3e0c2ea1e875f7cd2e92c0a4b5a1b8ee1c8cec7eb5`). The
closeout says the older export and registry were unchanged. The first registration
attempt ended `registration_timeout` after 62.841 seconds. Source `507dc4ccd03fa2fa6aa66dbdc5503089108765ee`
separates local source/use/export verification (up to 300 seconds) from the following
Runtime/Connector and roster phase (45 seconds). These are cooperative phase budgets,
not hard process-kill deadlines. The later receipt reports `registered` in 65.647
seconds (66.555 seconds total) under selector
`local-public-m0-f7ee054b3cb0428591f3b59fc47d2d87`.

The initial owner registration from source `507dc4c` succeeded under selector
`local-public-m0-f7ee054b3cb0428591f3b59fc47d2d87`. An older active Workbench then
reported policy-identity drift; it was stopped and reopened from source `507dc4c`,
and the locked offline Node install doctor returned PASS. The reopened Workbench
loaded the existing selector; it did not register the export a second time. The native
closeout records run `run-28194e5c-8fe6-4294-ae97-8dc7511e5b2e`, evidence report
`b59c7d91a96391efc49558b59e352792cc2822ce0643a23604662cf57e934029`, PASS with nine
events, one delivered `play`, and both immediate and observed successor identities.
Final status was Human mode, controller released, and no recording open. The paired
preservation receipts report 977 files, zero added, zero changed, and unchanged hashes
and modification times. This is one accepted native action with a successor; it is not
a full run or a game outcome claim.

### Separate profile-3 nine-step run

The sealed closeout for run `run-f23f3f56-a34e-47a4-b07f-081676e27010` records a
16-request / 300-second controller budget. It stopped after nine OneSteps at 282.708
seconds to leave cleanup margin. The run covered one ordinary combat on floor 1,
rounds 2, 3, and 4; the battle remained unfinished. It delivered nine actions: four
`Strike`, two `Defend`, and three `end_turn` (six `play` and three `end_turn` at the
control-verb layer). Each new decision and successor was checked. The closeout records
nine controller acquisitions and releases, zero rejected/unknown/retry/taint outcomes,
and the exact unchanged environment. Its evaluation identity is
`2bb9302b58f07691f58d2430544d7109fa6505eac44ae3bda81691b8cdbfe788`, evidence content
identity `ed7239950ffd570cb0824474cf1b712c9f1fba03847dbd30fc6bd614b2f7fd6d`; the
independent verifier passed all 65 events with zero findings. The closeout records a
bounded runtime operation, `game_outcome=not_measured`, and `scientific_verdict=not_claimed`.
The run was not resumed or topped up.

The earlier one-action run `run-28194e5c-8fe6-4294-ae97-8dc7511e5b2e` remains a separate
receipt and is not counted into this run. The profile-2 preservation before/after
receipts cover 977 files: the file identities, SHA-256 values, and modification times
match, with zero added or changed files. The typed final task-status receipt uses
`sts2.platform/task-status-1`; it records Human mode, released controller, stopped model
service, completed stop operation, and recording lifecycle `ready` with a null recording
ID. The recorder was off at final status. These are bounded environment-preservation
and shutdown checks, not a full-game result.

## Issue and handoff ledger

| Issue | Verified disposition | Remaining boundary |
|---|---|---|
| Training/dev history and independence | Only the 3,000 training decisions were sent to Modal; 16 dev decisions were scored on CPU. The TrainingInput records train-only fit and tokenizer scope within one engineering-purpose allocation. | Physical-game independence remains unresolved; more decisions from the same archive do not establish an independent holdout. |
| Slow repeated export verification / registration timeout | Commit `507dc4c` separates local verification from the 45-second Runtime/Connector phase; the later registration succeeded. | The budgets are cooperative. This receipt does not establish a hard wall-clock cap or cross-process verification cache. |
| Repeated remote-result parsing | PR #144 includes commit `33d1b434`, which reuses the parsed remote M0 result on the existing result path. | No general serialization or end-to-end latency claim is established by this repair. |
| STPD-to-Workbench ownership dependency | Commit `d378c2e` keeps the checkpoint owner dependency structural through an owner protocol. The exact-head architecture guard passed. | This is a source-boundary repair, not a new model or runtime qualification. |
| Registration-budget test fixture | `test_public_m0_registration_budget.py` builds a `tmp_path` synthetic workspace with `bundle3(public_bindings=True)`, prepares `public_lite` with 8 train and 4 dev decisions, runs one step, then exercises export and registration. The temporary fixture is scoped to this regression. | This fixture does not explain or represent every earlier disk-usage issue. |
| Repeated local pytest disk use and incomplete logs | Repeated local pytest outputs accumulated until disk space was exhausted; a locally maintained cleanup step removes generated pytest outputs. Early local stdout was not separately saved and disk-full tool output was truncated and unrecoverable. | Remote failed artifact `11257458559` and successful artifact `11259226386` retain complete JUnit; local output retention remains incomplete. |
| Workbench source/version alignment | Stop/open on `507dc4c` plus the locked offline install produced a doctor PASS and successful registration. | This proves the exact local session only; it does not qualify arbitrary upgrades or Windows native operation. |
| Longer profile-3 action bound | The closeout records a 16-request/300-second budget; the run stopped after 9 actions at 282.708 seconds for cleanup margin and was not resumed or topped up. | The receipt makes no game-outcome or scientific-verdict claim. |
| Profile-2 preservation and final status | Before/after receipts match all 977 file identities, SHA-256 values, and modification times, with no additions or changes; typed final status records Human/released/stopped and no active recording ID. | This closes only the scoped preservation and shutdown checks for this run. |

## Current CI snapshot and non-claims

PR #144 is still a draft at head `1d00b60fad2829ec58a0d79e95cc38cd99a64ecc`,
based on `885e85ec1cf0e8ab66f56e6105e7bfc9e54d3486` and including `507dc4c` through
a normal merge. Exact-head run [37080401408](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/37080401408)
reports the plan check, Linux and Windows portable gates, and portable summary as
successful; the documentation check was skipped. This is the CI result for PR #144's
exact code head, not for this separate documentation branch. PR #144 remains a draft;
this scoped train-to-game closure does not claim complete Stage 1a. See [PR #144](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/144).

This evidence does not claim a clean held-out result, physical-game independence,
training quality, a full-game win, all-surface execution, complete Stage 1a, Windows
native parity, or production throughput. Only immutable manifest metadata, the
allow-listed `eval.summary.overall`, bounded control/closeout receipts, and source were
used; no Gold/test/raw input rows, private game-state rows, raw game logs, or
`runtime.json` contents were read.

## Read-only aggregate extraction

For the stored evaluation payload, select only the public aggregate used above:

```sh
jq '.summary.overall | {count, top1, nll}' "$EVAL_SUMMARY_PAYLOAD"
```

The second command selects `summary.overall` only; do not select the payload's `rows`.
