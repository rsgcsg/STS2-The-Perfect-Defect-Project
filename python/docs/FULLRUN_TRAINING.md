# Full-Run Training and Disposable Workers

This is an engineering implementation, not a Full-Run dataset, cloud or scientific result.
The pooled path below retains its historical contract. The additive [Stage 1a token workflow](research/STAGE1A_WORKFLOW.md)
supports B/D scratch and frozen-Qwen graphs, reusing storage, reporting and metrics with separate versioned schemas.
A pinned Platform bundle3 adapter and a separately scoped synthetic adapter are installed.
Real corpus sufficiency and scientific admission remain separate. Historical combat-v0 and
its scientific protocol are retained unchanged.

## Immutable input pipeline

Dataset -> ModelView -> FeatureSet -> TrainingInput -> Experiment/Run -> Checkpoint -> Model
-> OfflineEvaluation. Every link uses typed manifests, hashes and exact producer/source/lock
identity, not directory names, a mutable database row or a provider's job number.

ModelView is re-derived from its admitted Dataset during loading. Candidate order, labels,
splits and text must agree exactly. FeatureSet is keyed by exact Qwen identity plus state/action
text; consumers recompute the complete index and candidate alignment. Real Qwen uses the
existing exact pinned backend and scientific identity validator; FakeQwen is engineering-only.
Large weights are not fetched in portable CI. Rebuilt features are not source evidence.

TrainingInput freezes Dataset, ModelView, FeatureSet, Qwen, serializer, source, uv.lock,
entrypoint and complete TrainingConfig. Worker admission precedes optimizer creation. A
research-purpose run also requires a frozen, matching protocol and qualified data; fixture
engineering never authorizes a scientific campaign. Sealed test is not the worker's default.

## Shared model and training

One shared Scheme1 Linear or 256-unit GELU MLP scores all exact candidates across surfaces.
The small head factory is shared with the existing live-Qwen scorer, avoiding two architecture
authorities. This changes the reviewed policy source closure, so v2/v3 manifests receive new
code hashes and manifest identities. No earlier runtime/Human evidence transfers to them.

Training uses immutable precompiled features and listwise cross-entropy, train-only sampling,
explicit seeds/dtype/device, finite checks and absent backbone gradients. Candidate permutation
preserves labels; a label-permutation control operates only within train candidate-count
buckets. Step order is deterministic from seed/epoch/step. This is not a claim of identical
floating-point behavior across different GPUs or framework versions.

## Disposable execution and recovery

The provider-neutral execute function consumes a Run and exact runtime Producer. It records
immutable attempt events and segmented metrics. Checkpoints are durable before their events;
a reporting failure never manufactures success. Resume is explicit by exact checkpoint ID
and verifies Run/Input/source/lock/config/feature plan/framework/step identities.

New worker checkpoints use a bounded typed JSON tree and safetensors, not Python pickle.
The codec supports only primitive values, mappings, sequences and finite dense tensors;
unknown kinds, repeated/missing tensor references and excessive size/depth fail closed.
Final model weights are safetensors too. These validations are not permission to trust an
arbitrary external model producer. PyTorch's official security guidance recommends a small
serialization surface and warns that weights_only does not eliminate all risks:
https://github.com/pytorch/pytorch/security/policy
https://docs.pytorch.org/docs/main/notes/serialization.html

RunReporter is a port. ObjectStoreRunReporter persists events as manifests, so an event-index
write failure is recoverable. One conditional immutable completion slot selects the final
RunResult; identical retries are idempotent and conflicting results fail. This does not
claim exactly-once process execution. In the B Hub lane, CandidateReporter writes immutable
candidates and events without that local completion slot. The Hub validates source/plan/bytes
and selects with its durable attempt fence; it never runs a second competing completion
ledger. See [B operations](CLOUD_PIPELINE_B.md).

## Offline evaluation

The worker evaluates dev only, emits Top1/MRR/NLL, margin, confidence/calibration, surface,
family, candidate-count and run stratification, plus whole-run bootstrap intervals when there
are enough independent runs. Equal-score ties are treated as uniform tie permutations, not
resolved by candidate position. Uniform-legal and train-fitted action-only baselines use the
same exact candidate sets. All reports explicitly say scientific verdict not claimed.

Platform qualification, final Standard serializer profiling/freeze, real Human Gold, real
cloud execution and final STS2 live evaluation remain separate later evidence gates. The [local operations](PREFULLRUN_OPERATIONS.md) surface now provides CLI, generic provider
launch, dashboard/analysis, Gold tooling and the clean-checkout CPU E2E. Latest-head
qualification receipts independently establish the result of those gates.
