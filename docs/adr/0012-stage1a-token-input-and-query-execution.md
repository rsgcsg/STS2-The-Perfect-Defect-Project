# ADR-0012: Stage 1a token inputs and frozen-query execution

Status: Accepted for implementation; runtime qualification tracked separately

Execution update: [ADR-0014](0014-packed-bc-readouts.md) supersedes the branch-local
B default with shared-observation packed v2. The text below retains the v1 history;
token input/artifact contracts remain in force.

Date: 2026-09-18

## Grounding and ownership

The approved 1a configurations are B-S, B-PF, D-Simple-S and D-Simple-PF.
B is one Transformer plus a final trainable soft query and score head. D-Simple
uses a shared encoder and vector transition MLP. Existing pooled S01 inputs cannot
represent trainable token encoders or the B query gradient. STPD owns this extension;
Platform remains model-neutral. The existing allocation and use ledger remain authoritative.

## Decision

Add `stpd/stage1a-token-input-v1` as a `training_input` artifact with a single
`model_view` parent, `tokenizer` JSON and `rows` NDJSON payloads. This initially accepts
only fixed decision-allocation train/dev views, with engineering purpose. Each row holds
the full state token sequence and all action token sequences, in verified sample order.
Labels, action bindings, run and source identities remain in the parent view/allocation.
Load recomputes the text projection and token IDs and checks payloads and metadata.

`separate-obs-act-text-v1` prefixes `OBS\n` / `ACT\n` and appends a newline to each
text. Encode each separately without implicit special tokens, padding or truncation.
B concatenates these exact IDs and a soft query; D encodes the same sequences separately.
The common joint input limit includes the query and rejects the whole request on overflow.
Changing this formatting or the legal source facts creates a new input identity.

Scratch byte-level BPE is fit only on train observations and all their candidate texts,
with a full byte alphabet and target vocabulary 8192. Both scratch models share the
same tokenizer artifact. Verify train-only fitting when loading. PF uses the exact pinned
Qwen tokenizer JSON, without fitting; the loaded fast-tokenizer wrapper is checked on
real inputs before a runtime receipt. No source snapshots or weights are required for S.

The frozen causal B implementation may compute the fixed state/action prefix KV without
autograd, then run the final query with gradients. Earlier positions cannot depend on
that query. Only a fully frozen, eval-mode backbone may use this execution path; each
candidate gets a new cache, discarded after its branch. Use `no_grad`, not `inference_mode`,
for the prefix. It is still one Transformer and the same trainable query/head graph.
Do not apply this decomposition to trainable scratch or LoRA prefixes.

## Verification and limits

Regression covers train-only vocabulary, unseen Unicode fallback, complete candidates,
reprojection/tamper rejection, token limits, frozen scope and full-branch versus prefix
output/query-gradient parity. Real pinned weights and MPS numerical tolerance require a
separate receipt. Initial performance measurements exposed a long-input B-PF memory issue;
successful synthetic scores are not real-data throughput or game qualification.

The token input is additive; old pooled workers reject the new schema. Mutable permissions
and Gold/use authorization are not cached or granted by a downloaded token artifact.

## Local token training and export extension

Add `stpd/stage1a-run-v1`, `stpd/stage1a-checkpoint-v1`, `stpd/stage1a-model-v1`,
`stpd/stage1a-ranking-evaluation-v1` and `stpd/stage1a-export-v1`. The token engine lives
beside the pooled engine and reuses ArtifactStore, RunReporter, typed checkpoint codec,
candidate metrics and grouped dev summaries. It is an additional execution capability,
not another dataset registry or cloud orchestration system. The old schemas keep their meaning.

The run binds immutable token input, full graph/optimizer/seed/step configuration, source,
lock, framework, device and CPU thread count. Each completed optimizer update is checkpointed
in the initial short engineering runner. Step-local CPU/MPS RNG is derived from seed and
completed updates; scratch dropout therefore resumes without depending on a previous process.
Checkpoint admission also validates optimizer settings, parameter state shapes and counters.
Never serialize the frozen Qwen weights; resume reloads the fixed backbone separately.

An unfinished prior attempt requires an explicit checkpoint resume or a new replicate,
not a silent restart. Failures are durable events and cannot produce a successful completion.
Dev evaluation reuses existing metrics and train-fitted baselines; it never unlocks test/Gold.
The new evaluation schema is explicitly engineering-only and is not silently accepted by
historical scientific consumers. UI-specific projections remain separate follow-up work.

Exports contain only a content-bound model manifest, learned weights and tokenizer. The
standalone scorer consumes legal state and all supplied candidates, with no dataset store,
labels, successors or authority to execute. The current runtime must match the exported
backbone configuration; cross-device qualification and the native adapter are later gates.
Synthetic tests cover new-process resume, uninterrupted/resumed equality, standalone scoring,
candidate permutations and altered weight bytes. Real training/game success still requires
separate receipts.

## Rollback

Stop producing new token inputs and use the existing consumers for old S01 artifacts.
The reference full-branch query operation remains available for diagnostics. Do not delete
datasets, allocation history, prior receipts, use reservations or Gold protections.
