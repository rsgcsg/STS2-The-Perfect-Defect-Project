# Bounded remote training execution


Current J-min applicability (2026-10-15): this packet is assembled source for the
existing TrainingService. The authorized diagnostic is synthetic local work with
at most 1000 total optimizer executions and one Root-owned remote profile with a
cumulative cost cap of USD 1, including image/startup/idle/cleanup. Root alone
starts paid resources. No new model or first scientific training configuration is
selected. Historical A/B/C budget and unexecuted-gate statements below describe
those earlier source packets; they grant no additional budget or execution.
Current outcomes and exact source receipts are in the J-min handoff.

`spireagent.training_execution_contracts` names immutable original Run/Input/
operation/attempt identities and finite work, wall, startup, payload, CPU, memory
and cost-authorization limits. It does not change `ComputeRequest(kind="training")`,
which still means the historical frozen-feature training contract.

The original application owner implements `TrainingAttemptOwnerPort`. Before a
provider prepare or submit it durably consumes the matching intention. Typed
claims distinguish `CLAIMED`, `PRIOR` and `WITHHELD`; a withheld first effect
returns `NotStarted`, while a prior intent only permits original reconciliation.
No-effect submission does not prove an already prepared App has stopped. The
executor then persists the exact AppRef/handle through that same port. A lost
reply or persistence failure stays unknown. Restarted executors reconcile the
original deterministic App or saved call; they never create another App/submit.
An absent provider inventory row is not proof that creation never happened.

The ordinary library constructor is `ModalTrainingExecutor(owner)`: it creates
a lazy real `SdkModalTrainingBackend`. A trusted configured target/binding must be supplied
by the application's supported configuration owner. Test backend injection is a
conformance seam, not the only implementation path. Construction imports no SDK,
Torch, network client or credentials. Source/device/capability absence is explicit
unavailable; there is no default qualified target or automatic fallback.

`PreparedTrainingAttempt.execution_binding_id` excludes request/projection hashes
so the domain's request can bind the target without a self-referential hash. The
complete prepared identity includes both final payload hashes. Canonical codecs
reject changed source/image/runtime/device, foreign App or result identities,
damaged payloads and exceeded finite counters/byte inventories.

The disposable projection has only `training_projection` and optional explicit
`resume_checkpoint` roles. It carries no canonical Store locator, journal,
credentials or original evidence-archive role. The domain adapter owns the
projection's numerical meaning. Remote code uses a fixed capability registry;
callers cannot name an arbitrary module, executable or research recipe.

The only admitted closed entry is `structured-m2-slice-v1` →
`stpd.workers.structured_remote_domain.execute_structured_remote_request`.
Discovery of the module is not a qualified image/device/worker claim. The domain
handler consumes finite original identity and projected TRAIN bytes and produces
only an isolated candidate delta. It must not create an AlwaysCurrent authority
or copy the local operation journal. Existing numerical code, recipes, source
permission, device validation and candidate interpretation keep their owners.

Candidates permit only `run_event`, `checkpoint`, `model`, `offline_evaluation`,
`analysis` and `run_result` manifests bound to the original Run and expected
producer, with exact payload inventories. A candidate `run_result` is not a
canonical completion slot. The original owner validates the domain result and
selects/publishes its accepted completion only after terminal reconciliation and
an exact App stop inspection confirms stopped, zero tasks and zero containers.

The provider checks live canonical workspace, unique App/deployment version/tag,
image, function identity and ranked resource definitions. CPU and memory
request/max pairs are equal; zero/unlimited observed maxima fail the same finite
resource binding as changed reservations. The locked SDK1.5.5 rank-zero Function
carries startup timeout; its legacy FunctionData field is not
used as a substitute. The unique deployment tag keeps all256 digest bits within
the provider's50-character limit. Submit revalidates the original deployment.

`poll` returns `None` for a known-running saved call, a candidate, a bound known
failure or `Unknown`. An unbound preflight failure cannot end a submitted call.
Polling, cancellation and reconciliation require the application's exact durable
original handle; a different call ID in the same App grants no authority.
Stopping also requires the exact durable AppRef, including its provider App ID.
The owner's `remaining_seconds(prepared_id)` starts at the original attempt,
including preparation, and supplies the current work/cleanup phase allowance.
Every CLI/RPC timeout uses that dynamic remainder; successive lookups never get
a fresh wallet. Submission lookups and the bounded SDK spawn reserve the complete
declared startup/work window. A known late handle is persisted before returning
unknown; it never permits another submission. The application owns the explicit
cleanup reserve and any explicitly authorized finite reconciliation observation,
which cannot extend the training grant or start another attempt. Expired automatic
cleanup remains unknown when no original allowance remains.
The shared `source_identity` preflight uses existing local read-only Git probes
without hard preemption. It may overrun the wall allowance; the budget is checked
again before any provider call. This packet makes no hard total preflight wall
or remote readiness qualification claim. Bounding that shared helper remains a
separate prerequisite for such qualification.
SDK1.5.5's provider adapter does not expose a qualified request-bound call-history
recovery here; a lost unsaved handle stays unknown and the original App may be
stopped/inspected. It is never respawned. Cancel acknowledgement, call terminal,
App stop, candidate validity and accepted completion are distinct facts.

Pause means a bounded numerical slice reaches a checkpoint; this minimum contract
does not promise real-time cooperative pause. CUDA's qualified
`CUBLAS_WORKSPACE_CONFIG=:4096:8` is set before the isolated process imports Torch.
The deployment injects no canonical storage Secret/volume. It executes the fixed
image's locked Python and has no retries or idle warm container. This source does
not deploy, provision, train or incur cost merely because it is installed.

Packet A owns executor/transport/provider/capability code and fake-provider tests.
Application durability/control/selection is packet B; projected numerical
domain/device validation is packet C. Source, mock conformance, real provider,
GPU compatibility and model/data qualification are separate evidence levels.
