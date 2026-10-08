# Structured workload execution

`stpd.structured_workload_contracts` is a pure descriptor/configuration and
application-to-worker contract module. It imports no tensor runtime. The existing
`models.structured_training.StructuredTrainingConfig` and
`workers.structured_control` names re-export these contracts for compatibility.
The historical `workers.checkpoint_codec` remains a shared safe tensor-tree codec;
it contains no operation lifecycle or application authority.

The application calls `prepare_structured_workload` with an authorized typed
dataset, producer, fixed CPU recipe, and explicit operation ID. This freezes a
v2 run, source/configuration/execution identity, and existing artifact parents.
It calls `execute_structured_workload` with a closed workload request, reporter,
store, application-owned `AttemptAuthority`, and optional `ExecutionControl`.
The application owns permissions, process reconciliation, terminal prior-writer
proof, and exact checkpoint selection. The worker has no replacement journal,
latest-checkpoint search, or automatic retry. `reconcile` only verifies an existing
complete result; it cannot restart an incomplete attempt.

A v2 checkpoint is saved at a completed TBPTT chunk: losses/gradients are cleared,
W is detached, and the cursor points to the next unconsumed row. The state retains
model parameters, AdamW state, RNG states, configuration/runtime/source/code
identity, cumulative counters and metrics. Resume validates these before loading.
Each checkpoint also retains a per-parameter count of actual gradient participation,
tracked independently of AdamW's lazily populated state. Exact parameter names
bind its index order. Restore requires precisely the positive-count parameter
state entries and an exact scalar step count per participating parameter; unused
parameters keep zero counts and no state. This detects an omitted entire entry,
not only malformed fields inside entries. Tensor shapes/types and cumulative
bounds are checked before model mutation. These checks validate checkpoint
completeness/consistency against its saved inventory and immutable artifact
identity; they do not independently prove the history of coherently rewritten
weights and metadata.

The cumulative optimizer budget never resets across attempts. Historical v1 runs
and final-only checkpoints remain readable by their existing APIs and are rejected
by v2 execution; they do not acquire resume guarantees.

Pause/cancel are checked between complete chunks and before/after the fixed-weight
evaluation pass. They publish a checkpoint and explicit terminal event when the
application fence remains valid. Abrupt process loss remains unknown; a later
attempt requires application reconciliation and an explicit checkpoint. Training
completion is durable before evaluation. Evaluation interrupted before its
publication may replay with fixed weights without an optimizer step; the replay
is recorded. A final complete result is verified against its checkpoint/export
and may be reconciled without new training. Completion verification closes the
exact run/training-input/experiment/checkpoint/model/result role inventory and
producer/identity bindings. The selected checkpoint's manifest cursor, phase,
boundary and update count must match its verified tensor payload. Its optional
previous-checkpoint reference is checked for direct manifest identity; this
verification does not scan all historical ancestor payloads.

Every selected source, checkpoint, model package, weight and report payload is
read within its declared bound and independently checked against declared size
and SHA-256. Reports have an 8 MiB bound and a strict canonical schema binding
producer, source, configuration, metrics, execution identity, operation/attempt
and explicit run/input/checkpoint/model artifact IDs. Package contents and model
weights match the selected checkpoint. Imported datasets retain their original
producer; source authorization remains with the application rather than being
rewritten to the training producer.

Every worker publication asserts the injected application fence. This hook must
use the application's existing writer exclusion mechanism; it is not a storage
transaction or a replacement for process ownership. Per-run reporter steps are
monotonic across attempts. A failed event/completion publication may already be
durable; execution propagates the error without appending an invented failure
or retrying publication. The application reconciles the explicit run before
resuming or verifying its completed result. Synthetic tests cover exact recovery, cumulative
budgets, old-schema rejection, crash/replay, and denied/stale authority. They do
not qualify real data, remote processes, model quality, or production recovery.
