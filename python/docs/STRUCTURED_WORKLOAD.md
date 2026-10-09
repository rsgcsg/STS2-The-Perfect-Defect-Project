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
by v2 execution; they do not acquire resume guarantees. The declared checkpoint cadence accepts 1–100 completed
boundaries; worker fixture defaults use 1, while applications choose a bounded
cadence and cumulative resource reservation for larger jobs rather than blindly
publishing a full tensor tree at every boundary.

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
boundary and update count must match its verified tensor payload. Periodic
checkpoints reference the run/input and, for a resumed attempt, its explicit
starting `resume_checkpoint`. That anchor stays constant throughout the attempt;
periodic checkpoints never form a data-ancestry chain. Their chronological
previous IDs remain in ordered run events, including repeated publication of the
same immutable state. A completed model therefore does not inherit hundreds of
periodic checkpoint parents.

Each checkpoint's explicit resume ancestry has at most 32 links and is validated against
the exact same run/input/producer/execution identity. Every resume uses a fresh
attempt ID distinct from prior writers in the run events and checkpoint ancestry. The worker rejects
an exhausted ancestry bound before publication or numerical work. This does not
count all attempts branching from older anchors: total-attempt and cumulative
resource budgets remain application-owned. Applications can impose a stricter
total-attempt limit. Cross-run warm starts are unsupported.
This checks bounded attempt-start manifest ancestry and the selected state/output
payloads; it does not scan all historical checkpoint payloads.

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

A copied checkpoint closure may omit sibling run events. A requested resume attempt
ID must therefore be fresh against every checkpoint ancestor as well as local
event history, before engine initialization or publication. A fresh attempt can
resume the same verified copied closure; transferring it does not renew budgets.

## Explicit scoped identities

Existing APIs retain their legacy defaults: package-v1 and run-v2/checkpoint-v2
still bind every `stpd`/`spireagent` Python source and the whole dependency lock.
Their strict checks are not reinterpreted after a schema or digest rename. The
final-only run-v1/checkpoint-v1 path still has no resume guarantee.

New callers may explicitly select `TRAINING_SCOPE` from
`stpd.structured_code_scope` when preparing a workload. This creates run-v3 and
checkpoint-v3. The fixed reviewed source inventory includes projection, graph,
configuration, numerical execution, checkpoint verification, run preparation and
publication owners, their package initializers, and shared storage/codec helpers.
The source closure SHA and complete `uv.lock` SHA are separate exact fields.
Unrelated Workbench/Hub source is excluded. Missing or symlinked source fails
closed; artifacts never select an executable file inventory. Static import and
lazy-export checks supplement clean-process inventories, rather than deriving a
scope from one successful import.

Execute a scoped run with its original run producer as the existing positional
argument and an explicit current `attempt_producer`. The application supplies
current source provenance through its existing trusted source identity owner.
The worker independently recomputes current scoped code, runtime, input and
configuration identity before restoring or advancing. An unrelated application
commit may therefore change the current attempt producer without changing the
numerical identity. Each new checkpoint, event, model and report preserves that
actual attempt producer; the immutable run and package training provenance retain
the original producer. The cumulative budget and explicit resume ancestry remain
unchanged. Scope/schema switches are rejected before numerical restore; rewriting
an old checkpoint is not an exact-resume migration.

A completed scoped run exports package-v2 with `INFERENCE_SCOPE`. Direct scoped
export additionally requires original training and current export producers and
explicit run/training-input/checkpoint IDs. These supplied facts are provenance;
standalone package verification does not independently attest that training ran.
Workload completion separately verifies their immutable parent bindings and
weight equality. Package-v2 separately binds its actual weights, graph/projection,
training data/teacher, code closure, dependency lock and declared CPU/float32
inference ABI. Installation keeps its existing callable APIs and dispatches only
between these verified schemas: config-v2 and adapter version 1.1.0 use the
explicit inference scope. The binder remains a trusted application edge governed
by its installed source provenance; binder changes do not invalidate weights.

Numerical resume remains exact for Python, OS, machine architecture, Torch/Numpy,
codec, threads and deterministic settings. Portable inference instead requires
the declared `structured-inference-cpu-float32-codec-v2` profile: exact Torch
version (including any wheel local-version suffix), codec version, CPU/float32,
graph and weight shape/type. Exporter Python/OS/machine are recorded solely as
provenance, so a different source platform does not automatically reject a model.
Cross-platform fixtures exercise this admission rule; actual remote-to-local
model inference and numerical equivalence still need their own evidence. No
cross-platform bitwise training/resume claim is made.

No old-package migration or checkpoint warm-start migration is implemented.
Old packages remain immutable and require their original strict identity.
Applications must explicitly opt in and dispatch run-v3/checkpoint-v3 before
using scoped workloads; the default application recipe is not silently upgraded.

A durable publication-phase checkpoint can retain an earlier attempt's producer
when a later compatible attempt exports its already-complete numerical state.
The checkpoint creator remains independently validated; result, model, report
and package export provenance must agree on the actual exporting producer.
Reconciliation verifies those existing identities without stamping its own
producer onto them or replaying numerical work.

## Application opt-in and model composition

The ordinary local training service now exposes the separate trusted recipe
`structured-m2-cpu-v3`. Select it explicitly in `TrainingRequest`; the default
recipe and `structured-m2-cpu-v2` retain their previous meanings. The service
capability descriptor reports the selected code scope and run, checkpoint,
model and package schemas without importing Torch.

| Application recipe | Training scope | Run / checkpoint | Model / package |
| --- | --- | --- | --- |
| `structured-m2-cpu-v2` | legacy whole Python source and lock | v2 / v2 | v2 / v1 |
| `structured-m2-cpu-v3` | reviewed numerical closure and lock | v3 / v3 | v3 / v2 |

The existing operation journal caches the actual current `source_identity(ROOT)`
producer once for each owned scoped attempt. The child independently checks that
producer, selects preparation scope from the trusted recipe, and verifies the
frozen run's schema before resume/reconcile. Parent event and checkpoint checks
bind the actual publisher for that attempt. Selecting an earlier checkpoint
requires its terminal writer and exact historical event/producer; it is never
renamed to the original run producer or the new writer. A publication checkpoint
created by A may be exported by B and later reconciled by C while all three
identities remain distinct. One journal, controls, cumulative budgets and use
reservations continue to apply.

Export, registration and fixed-model evaluation explicitly admit model-v1/v2
with package-v1 and model-v3 with package-v2. Unknown or mismatched versions are
rejected. Scoped model metadata binds graph, qualification, export attempt,
actual exporter and immutable run/input/checkpoint parent IDs. Detached downloads
verify only their own closed package payloads and those IDs, without requesting
private ancestry. Registration delegates broad/config-v1 versus
inference/config-v2 identity checks to the versioned installation owner, so an
unrelated Workbench edit does not falsely stale a scoped registration.

These recipes consume the retained sampled S0 text-menu-v2 I/F-off source;
scoped code identity does not create a new native projection or runtime
qualification. Fixed evaluation still requires original source verification,
current use/Gold/partition and model ancestry/source-group overlap gates before
its existing private child executes. External exposure remains unknown and no
clean held-out claim is inferred. No old checkpoint/package is rewritten.

## Explicit sampled package execution policy

The existing common native sampled recipe and ordered decision-sample recipe
also accept an optional top-level `TrainingRequest.execution_policy`. This is a
package deployment choice, separate from numerical `config`, InputSpec, graph,
features, W, weights, optimizer and execution-identity values. Missing means
legacy behavior and preserves the old request, Run, report and operation-snapshot
field sets. An explicitly present null is rejected. Full-reference, pretraining,
text-menu and other recipes reject the extension before state selection or child
entry. Browser and native training routes use the same request validator; their
existing forms continue to omit the field. Status exposes a copied policy only
for an explicitly opted-in request.

The shared pure parser owns the closed six-field declaration:

```json
{
  "schema": "sts2.policy-runtime/agent-execution-policy-1",
  "current_mode": "reader_owned_v1",
  "known_stale": "fresh_changed_current_v1",
  "operational_outcome": "known_not_started_v1",
  "max_known_stale_rejections": 8,
  "max_consecutive_known_stale_rejections": 3
}
```

Total accepts integer 1–16 and consecutive accepts integer
1–min(total,4); booleans, extra fields and other modes fail. The example is an
explicit choice, never an injected default. Same-intent request comparison
includes policy presence and value. Request parsing and journal serialization
copy the declaration, so changing a caller object or a returned snapshot cannot
change an admitted choice.

The private child freezes its initial validated request policy independently of
later mutable journal reads, before preparation or numerical imports. A fresh
start permits only existing preparation while Run/input references remain absent.
It verifies and binds one actual immutable Run/input pair before its prepared
event and parent ACK. During that bounded ACK wait, only the original missing
pair or the exact joined pair is accepted; publication and numerical entry wait
for the exact ACK. Afterward every operation read requires that same pair, and
missing references cannot reopen the pre-ACK interval. Resume and reconcile bind
the existing Run before numerical imports. None callers, artifact/slot reservation
polls and the check immediately before each delegated write enforce the same frozen
choice and Run binding. The parent separately checks policy at ACK, orphan adoption
and recovery; resume/reconcile provide no policy override.

Preparation copies the policy into the Run's top-level parameters. The worker
checks authority before loading the Run, and validates policy plus the actual
sampled Source/view/control before changing Torch threads or entering the engine.
Finalization forwards the Run's choice only to the supported sampled exporter and
checks the returned AgentSpec before the first model payload or ModelArtifact.
The earliest new model therefore already contains the selected AgentSpec1.2 policy.
Completion verifies that the package and optional report field agree with the
same immutable Run, including when tensor weights match. The immutable model
materializer does not upgrade or rewrite old models. Numerical checkpoint state
needs no policy field because its parent Run already binds the choice.

Actual source edits still change the existing training code digest; keeping the
policy value outside execution identity does not qualify old checkpoints across
changed code. Engineering fixtures exercise policy binding, pause/resume and
completed reconciliation. They do not authorize a real-data fit or prove useful
learning, an installed consumer, game execution or model quality.
