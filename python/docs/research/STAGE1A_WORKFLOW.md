# Stage 1a local operator workflow

Use `uv run --locked python -m spireagent.research_cli` from `python/`, with ML/L2 extras.
This page covers local token training and independent model scoring. Native Workbench/Mod
integration is still a separate acceptance item in [the approved plan](STAGE1A.zh-CN.md).

## Shared input and authorization

Use the existing `prepare` operation next to the authoritative operations database to
register training use, freeze a decision allocation and publish a ModelView. Follow
[S01 input preparation](S01_WORKFLOW.md) for purpose, Gold, source transfer and allocation.
An already authorized immutable view can be reused. Never create a fresh local permission
ledger for downloaded data. Token inputs do not replace the parent dataset or membership index.

`--store STORE tokenize --view VIEW --backbone s` fits a byte-level tokenizer on train only.
The actual vocabulary can be smaller than the target 8192. Both scratch graphs consume this
input ID. `--backbone pf --snapshot SNAPSHOT` uses the pinned Qwen tokenizer; both frozen graphs
share its second input ID. Both paths preserve every decision/candidate and reject overflow.
The source allocation, serializer and train/dev members remain identical across the four.

## Bounded training and recovery

For the first scratch D-Simple engineering run:

```text
--store STORE train-tokens --inputs SCRATCH_INPUT --recipe stage1a.dsimple.s.v1
  --backend mps --steps 10 --replicate stage1a-dsimple-s-pilot --stop-after 2
```

Arguments above form one command. CPU is an explicit alternative. Training stays local.
For B use `stage1a.b.s.v2` or `stage1a.b.pf.v2`; D-Simple retains `.v1`.
Frozen recipes use the PF input and `--snapshot`. B v2 packs one observation and all
action readouts; v1 remains historical and must not silently resume into v2.
Do not run frozen models at large input/candidate counts without a bounded cost check.

The CLI fixes seed1701, FP32, one complete decision per optimizer step, AdamW lr3e-4,
weight decay0.01 and gradient clip1. The scratch default is the approved 384-wide, two-layer
encoder; dimensions and full configuration are recorded. This 10-update engineering trial
is not a full epoch over 50 train rows or a useful-policy claim. No implicit GPU/cloud fallback.

The expected first result is `state=paused` with a run and checkpoint ID. Resume in a new
process with identical arguments and exact source/lock, remove `--stop-after`, and add
`--resume CHECKPOINT_ID`. A source/config/input/framework/device/thread change fails admission.
An unfinished prior run cannot silently restart at step zero. Use an explicit new replicate
only when a separate rerun is intended. Old receipts and failures remain visible.

Every completed update is saved in this small pilot. Its I/O cost is recorded by the full
attempt duration and must be revisited before the 1b larger-data runner; step time alone is
not total training time. Long jobs follow the root handoff procedure and run once, without
automatic restart after completion or failure.

## Evaluation and export

On completion, the worker publishes learned weights, a dev report and one RunResult through
the existing Reporter. The report uses the same candidate metrics, n=1/n>1 and run/family
summaries as the pooled worker, plus uniform-legal and train-fitted action-only baselines.
Insufficient independent runs remain explicit. No test or Gold labels are used for debugging.

`--store STORE export-tokens --model MODEL_ID --destination DIRECTORY` exports exactly
`model.json`, `weights.safetensors` and `tokenizer.json`. Frozen Qwen weights and raw recordings
are not copied. `score-tokens --model-directory DIRECTORY --input INPUT_JSON` performs standalone
scoring; PF additionally requires its pinned snapshot. Input JSON is exactly the existing typed
`{"state": SemanticState, "actions": [SemanticAction, ...]}`. All unique action keys are returned.
No training store, human choice, successor state or game execution is needed for this command.

Independent scoring verifies model packaging, not gameplay. The next native adapter must
use this same serializer/scorer and preserve cancellation, local session identity, complete
Connector candidates, execution receipts and manual takeover.

## Public game input parity before further model training

The first D-Simple-S pilot passed recovery/export checks, but its recorded semantic input
contains execution supplements that the online public Snapshot does not expose verbatim.
Do not register that model as game-ready or silently omit those fields at inference.

`python tools/audit_stage1a_live_inputs.py --store STORE --allocation ALLOCATION --output REPORT`
verifies the fixed allocation and replays each original public pre-frame through
`stpd-public-snapshot-lite-v1`. It reports exact catalog-semantic matches, unique human-choice
mapping and explicit exclusions. It neither changes the source/allocation nor starts training,
publishes a new dataset, or issues game commands. The output identifies its producing source.

The public projection deliberately has a new identity; reusing the old tokenizer/model does
not establish input parity. After replay coverage is known, the same projection must feed
a new verified training view and the decision-only online adapter. Candidate subsets and
native-to-public verb guesses are forbidden. Insufficient coverage must remain visible.

## Exact Human-observation BC view

The pre-frame audit above intentionally tests execution-state parity. A separately
versioned view now follows the original proof's **Human observation**, matching the
recorded public binding exactly. H and execution S stay distinct; this view provides
BC labels only, with no successor supervision. See [ADR-0013](../../../docs/adr/0013-public-human-observation-bc.md).

Use `--store STORE public-view --allocation ALLOCATION` to publish it. It preserves
the original train/dev assignments and writes every exclusion to `dispositions`;
it does not delete or silently replace raw decisions. Use the resulting view with
the existing token/worker commands. For a combined preparation and pinned tokenizer
check, run `tools/prepare_stage1a_inputs.py --store STORE --public-allocation ALLOCATION
--snapshot SNAPSHOT --output NEW_REPORT` (one command). S fits a new train-only BPE;
both PF graphs reuse the pinned vocabulary but receive a new token input identity.

New public models use `score-snapshot --model-directory DIRECTORY --input SNAPSHOT_JSON`
(PF additionally `--snapshot`). The input is the public Snapshot object, without Human
labels, native execution supplements or successors. The semantic `score-tokens` entry
remains for historical exports and cannot accept these models. This establishes a
shared scoring contract; it does not yet register/load the model through the game Mod.

New `public-view` production uses compact v2; the expanded v1 remains readable.
The compact format replaces repeated state objects with a lossless FACTS/STATE
reference table. It changes neither the public facts nor the candidate inventory.

`tokenize`, `train-tokens` and `prepare_stage1a_inputs.py` accept `--max-tokens`, now
defaulting to 16384 at the CLI. This is a configurable resource budget, not a model
family definition or a requirement that data stay below 8192. Standalone inference
uses the exported configuration. Legacy configs without the field mean 8192; when
reproducing an old command, provide that value explicitly and its original source.
PF must also fit the pinned model's actual context capacity. Increase the budget when
appropriate; keep full inputs and monitor real memory/time instead of truncating.

The first compact-public D-Simple-S pilot has completed training, dev evaluation and
original-snapshot export parity checks; see [the exact evidence record](evidence/STAGE1A_PUBLIC_D_SIMPLE_S_2026-09-18.md).
Next, D-Simple-PF uses the same allocation/view and update count as an engineering
configuration comparison. Actual Workbench/Mod execution remains a separate pending gate.

## Compare completed token configurations

`--store STORE compare-tokens --result REFERENCE_RESULT --result OTHER_RESULT`
produces a JSON analysis projection (repeat `--result` for more configurations).
It verifies completed-result lineage and payloads, requires exactly the same ModelView,
and matches every dev row by transition identity. Different tokenizers/backbones are
allowed; missing/duplicate rows, different views and non-dev rows are rejected.
It recomputes decision-weighted, run-weighted and multi-candidate metrics, and reports
paired differences from the first result, explicit config/source identities and each
recorded worker attempt's elapsed time. It does not access test/Gold labels or launch
training. Store the output with the experiment's private receipts; it is a rebuildable
view of existing artifacts, not another database. One attempt's time excludes other
attempts, input preparation and tuning. Shared data and update counts do not isolate
pretraining from differences in backbone size, tokenizer or trainable capacity.

## B/C shared observation clarification

[ADR-0014](../../../docs/adr/0014-packed-bc-readouts.md) fixes the execution contract.
The earlier B-PF v1 run remains operator-paused at step 3/10; do not resume it or
relabel its checkpoint as v2. New performance checks do no optimizer updates.
A new v2 training run requires its own identity and an explicit training handoff.
C1 is the matched B graph with successor objectives when selected; C2 packs K
readouts per candidate. C is documented, not implemented or trained in Stage 1a.

`tools/profile_packed_b.py --store STORE --inputs PF_INPUT --snapshot SNAPSHOT
--output NEW_REPORT --device mps` checks a short real-weight full-packed reference
against the frozen two-phase execution, then measures forward/backward on the first
three decisions of the original seed1701 plan. It never creates an optimizer, updates
weights or resumes a checkpoint. It reports fixed/readout pass times and MPS memory
at phase boundaries (not a sampled peak). Run it durably under the long-job handoff
rule. Old `profile_stage1a.py` explicitly retains v1 for historical reproduction.
