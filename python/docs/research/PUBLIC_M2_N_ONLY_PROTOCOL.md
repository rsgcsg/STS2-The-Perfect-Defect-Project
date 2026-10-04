# Public observation-only M2: bounded N-only protocol

This is a new source candidate, not a trained-model, CUDA, live-game or policy-quality
report. It depends on the public M0 foundation at `c8d654b7` and retains older native
text-menu/canonical M2 schemas, numerical behavior and artifacts unchanged.

## Experiment scope

| Arm | Approximate train decisions | Graph | Slots | Reset each decision |
| --- | ---: | --- | ---: | --- |
| A01 | 3,000 | public light-action M0 | none | stateless |
| A02 | 3,000 | public light-action M2 | 8 | no |
| A03 | 3,000 | public light-action M2 | 8 | yes |
| A04 | 3,000 | public light-action M2 | 1 | no |
| A06 | 1,000 | public light-action M2 | 8 | no |
| A08 | 2,000 | public light-action M2 | 8 | no |

The intended common seed is 1701. Five ordered passes produce approximately 75,000
supervised decision exposures across the six arms; these are not optimizer updates.
Actual counts depend on indivisible evidence segments and must be reported. Checkpoints,
weights and dev evaluations at epochs 1, 3 and 5 come from the same run. An epoch-1 or
epoch-3 model is a stage artifact, not evidence that the five-epoch run completed.

All learned parameters start from scratch and remain trainable. N-only means the
scratch token core, not unlabeled learning. The common scratch shape is width 384,
two layers, six heads, feedforward 1536 and dropout 0.1. AdamW uses learning rate
0.0003, weight decay 0 and gradient clipping 1.0. These values, exact limits, model
variant and software identity must be explicit in each run configuration.

M0 retains its existing causal page encoding; M2 retains its existing bidirectional
scratch page encoding and fixed-slot memory graph. Their comparison shares data,
input and update schedule, not all graph internals. A02 versus independently trained
A03 is the matched test of carrying memory within the M2 graph.

## Evidence segments and input

`public_m2_sequences.py` consumes already admitted public-compacted samples and
metadata copied from verified source occurrences. Each sample is bound to its full
verified public-view digest, including text, ordered action texts/keys, target and
split. It does not infer public qualification from a text wrapper or grant data use.
The production intake must obtain this digest from the authoritative public view.

An internal edge requires the same archive, recording segment, session and native
run, consecutive native action ordinals, and exact equality of the preceding proved
successor frame and the next pre frame. A missing row, unknown edge, pause/reload
boundary, changed source or split prevents carry. Ordinal adjacency and repeated
visible text alone do not prove an edge. Recording-segment identity is derived from
the verifier's journal/trace by the intake owner. It must also split at an intervening
unknown or unproved accepted occurrence in the complete action ledger, even if two
retained canonical rows happen to have consecutive ordinals and equal frame hashes.
Capture-active intervals and qualified continuity segments are separate facts; the
input carries the stricter continuity-segment identity. A paused interval is never an
active segment, and complete-run status is not a prerequisite for a bounded segment.

The resulting chain is a maximal selected evidence segment. Its first observed pre
state receives an explicit **model memory reset**, which is not a game reset or a
claim that the run started there. Its end is the last selected transition's proved
successor. A complete game or victory is not required. Report singleton and multi-step
coverage separately, boundary reasons, actual lengths and unresolved physical-game
independence. Singleton supervision alone cannot demonstrate learning memory carry.

There is no 64-step truncation or reset. Whole train segments are ordered by a
seeded identity hash; the nearest whole-prefix counts provide nested approximately
1k/2k/3k subsets. Fixed dev membership remains separate. One shared byte-BPE is fitted
only on the selected smallest train tier; dev and larger-tier-only observations do
not participate in fitting. Every arm uses the exact same tokenizer bytes.

Inputs retain the public Snapshot `public_compact` renderer and the Connector's
complete ordered BoundAction text. Actions use the existing independent byte codec.
No chosen-action, previous-action or feedback feature enters the memory write. The
target is used only by listwise cross entropy. Limits reject the whole affected input;
they never trim text, truncate actions or silently discard candidates.

## Numerical and recovery boundary

The current common candidate is four decisions with an aggregate window limit of
98,304 tokens. This counts each page and every complete candidate encoding; it does
not increase the token core's single-sequence length or permit cropping. The earlier
65,536-token pilot bound rejected complete catalogs in the verified input. The
configurable window range remains one to eight, and engine defaults remain unchanged.
Freeze the measured candidate across all six arms before their full runs. Windows do
not cross chains. A final shorter window uses the actual
decision-count mean. Each window performs exactly one AdamW update on mean listwise
cross entropy; it does not scale gradients by total chain length.

Memory remains attached inside a window. At its end the value is detached and carried
to the next window; only the chain boundary or explicit reset variant clears it. M0
must use the same chain/window partition and effective decision accumulation. The old
single-decision/shuffled M0 training engine is not this matched baseline.

Window-boundary checkpoints bind model, AdamW inventory and moments, CPU/CUDA RNG,
chain/epoch/window cursor, carried memory, counters, input, configuration and
implementation. Resume is explicit. Invalid restore fails closed. A crash is resumable
only from an actually persisted checkpoint, not from an in-memory progress event.
Immutable typed inputs avoid whole-dataset serialization on every numerical window;
construction and durable checkpoint/evaluation/export boundaries verify full identity.

Training device is part of provenance. Weights can be loaded on an independently
selected inference device while retaining that training provenance. A CPU test of a
CUDA-tagged header verifies device routing only; actual CUDA execution requires its
own receipt. Dev evaluation replays held-out recorded chains and preserves training
state; it is not a counterfactual game rollout or independent-game qualification.

## Operations and next gates

Reuse the existing ArtifactStore, RunReporter and typed checkpoint codec. Keep one
writer per run. The current M0 owner journal is a singleton beside its curation registry;
changing a generic state directory does not isolate it. Do not fork curation owners
to bypass the shared use/Gold ledger. First qualify the pipeline through a separately
identified, owner-admitted pilot before starting formal A02 or adding concurrent
execution infrastructure. The pilot retains the shared tokenizer's complete train
fit provenance, uses whole evidence segments, and has a finite epoch-1 stage goal.
Its small dev subset is resource/export evidence, not a six-arm evaluation result.
Formal arms start again from seed 1701; pilot weights are not implicitly promoted.

The typed run checks declared parent identities and immutable input bytes. It does
not independently prove source admission or tokenizer fitting. A formal intake must
reproject the authoritative allocation/public view, join the audited source metadata
by exact transition identity, fit the smallest train tier once, and bind the resulting
inputs to that intake receipt. Subsequent runs and resumes reuse these compiled inputs
without re-fitting the codec. This does not replace current owner admission. Existing
owner checks run in one verified-source scope; a new process must revalidate that
scope rather than pretending that a previous process's cache is still available.

The remote bytes path sends typed run/input artifacts and only the source manifests
needed to declare lineage, never their raw evidence payloads. A local acceptance
pass checks the complete returned checkpoint/stage/event inventory before importing
it, and selects completion locally only for a verified epoch-5 result. Checkpoints
inside a disposable worker are not durable until their result bytes are returned and
accepted locally. An unknown submission cannot be retried automatically.

The Modal wrapper runs only standard-library transport code. Model execution uses
the pinned image's locked virtual-environment interpreter. The worker compares actual
source and runtime observations with an independently obtained qualification receipt;
the provider binds image, resources, call identity and the returned receipt. These are
ordinary process/provider checks, not hardware attestation or proof of model quality.

The private batch control owns the authorized raw-cost limit and each attempt's
reservation, image/source/input/resource identity, lifecycle and stop confirmation.
No provider submission follows from this source document. Builds, pilots, retries,
CPU and RAM count toward the same budget; credits do not reduce raw usage.

The first gates are synthetic train/pause/resume/dev/export parity, a verified real
chain inventory and compilation, then one bounded CUDA pilot. Measure the largest
training window with backward enabled and dev windows only through no-grad evaluation;
also check the longest individual state. The public generic Snapshot path uses the
separate port-4 consumer candidate and requires its matching Runtime contract. It
does not hide memory behind the old stateless contract or change Connector action
semantics. Source integration does not establish live M2 readiness.
