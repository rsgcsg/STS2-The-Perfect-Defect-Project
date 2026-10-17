# Application-owned remote training execution


Current J-min applicability (2026-10-15): this packet is assembled source for the
existing TrainingService. The authorized diagnostic is synthetic local work with
at most 1000 total optimizer executions and one Root-owned remote profile with a
cumulative cost cap of USD 1, including image/startup/idle/cleanup. Root alone
starts paid resources. No new model or first scientific training configuration is
selected. Historical A/B/C design notes grant no additional budget or execution.
Current outcomes and exact source receipts are in the J-min handoff.

Packet B is a G2 application/worker contract candidate based on PR177
`555ac466e7706a926516569cc16255d8cc6e8662`. It restores a bounded execution
placement within `LocalTrainingService`; it does not introduce another training
manager or reinterpret `ComputeRequest.kind="training"` (frozen FeatureSet).

A trusted deployment configures a closed recipe/worker/placement tuple with its
expected immutable image, code closure, dependency lock, actual runtime/device,
finite slice/work/time/transfer/resource/cost grant and existing recipe domain
adapter. A configuration or receipt hash alone is not runtime qualification.
Untrusted TrainingRequest/API payloads cannot register or override this binding. The default service has no remote binding. Missing qualification or
unsupported tuples reject before an operation or use reservation is written.
Remote requests use a new request schema and remote operations use V4; existing
local V1 requests and V1/V2/V3 operation/archive readers retain their meaning.

The normal application request goes through source preflight, original owner use
reservation and Run preparation, immutable disposable projection, durable
prepare/submit claims, exact AppRef/call handle, bounded provider polling, candidate
quarantine, domain verification and original-owner acceptance. Remote workers
receive no canonical store credentials, completion/use/selection writes or copied
operation journal. CPU/GPU runtime is fixed before Run creation and checked on
returned candidates; a CPU Run cannot silently become a CUDA Run.

Reacquiring the local mutex proves only local supervision has ended. Remote
reconciliation retains the original attempt/AppRef/call handle and never calls
submit again. No new attempt, resume or acceptance is permitted until the exact
provider call is terminal and the exact App is stopped with zero tasks and zero
containers. Cancel ACK records intent only; pause means the end of the currently
bounded slice with a verified checkpoint. Parent death leaves a finite provider
wall timeout and quarantined outputs. Unknown remains blocking without proof.

The source packet supplies synthetic lifecycle regressions and the bounded
J-min CPU/CUDA engineering gate. It does not qualify an image, GPU numerical execution, provider account,
data use, paid allocation, production deployment, performance or model quality.
Rollback removes the configured remote binding; immutable old local and remote
records remain readable and an unknown remote attempt must still be reconciled.

The supported library configuration uses the existing ProjectConfig and original
service. Read a nonsecret target configuration produced from the operator's exact
verified qualification receipt; construct `ConfiguredTrainingTarget.from_dict`,
then `RemoteTrainingBinding.modal(target)`, and supply
`LocalTrainingService(config, remote_bindings=(binding,))`. Call the existing
`start(TrainingRequest(..., placement_id=target.placement_id))`, `status`, `pause`,
`cancel`, `reconcile` and explicit `resume` APIs. The provider SDK is imported
lazily at execution. This entry has no test-only target resolver or GUI owner.

A target's `qualification_id` points to the original qualification evidence; the
configuration is trusted deployment input, not proof that an arbitrary image is
qualified. Its producer/lock, closure, image, runtime/device, recipe capability and
slice resource/cost caps are frozen into each grant. The domain owner checks the
supported tuple before admission and checks the actual returned runtime again.
Credentials remain in the provider's existing environment/profile.

`max_operation_cost_microusd` is the original operation's cumulative reservation
cap. Each durable prepare claim reserves its entire slice `max_cost_microusd`
before an external side effect; unknown does not refund it and resume does not
replenish it. This is a conservative grant ceiling, not a measured bill or an
independent shared budget authority. Root owns this J-min campaign's cumulative USD 1 cap across image build,
qualification, all training slices, startup/idle and cleanup. Historical A/B/C
references to USD20 are not an authorization for this campaign. Retained request/projection/candidate bytes
are charged against the same cumulative scratch envelope before writes.

The original A/B/C packet proposed the CPU2 gate below without provider execution.
J-min now authorizes this existing numerical/backend path with synthetic fixtures,
subject to Root's one cumulative USD 1 campaign and exact image/runtime gate.
It authorizes no new model or first scientific training configuration. The same
service/transport/adoption flow is used for the bounded CPU and CUDA checks:

1. Run `python/tests/test_remote_training_application.py` on the assembled exact
   source with dependencies A/C and the recipe seam. These pure provider/domain
   seams exercise the actual service/journal/control/recovery path; they cannot
   qualify numerical checkpoints, the Modal SDK or physical provider stop.
2. Pin one qualified CPU image and matching runtime/code/lock target through the
   ordinary library entry above: CPU 2000 millicores, memory 8192 MiB, at most two
   optimizer updates/four boundaries, wall 30 s/startup 60 s, request 64 KiB,
   projection/candidate at most 16 MiB each, at most 32 artifacts, and a Root
   allocated finite cost grant within the current cumulative USD 1 campaign,
   including image build, startup, idle and cleanup. Use synthetic
   preverified TRAIN frames only and a fresh private original-owner store.
3. Observe the original IDs, immutable grant, AppRef/handle, actual runtime,
   counters and candidate checkpoint; require provider terminal plus exact App
   stopped/zero tasks/zero containers before domain validation/acceptance.
   Explicit resume must retain Run/input/config and allocate one distinct attempt
   within the original cumulative budget. No performance or real-data claim.
4. Reproduce pause-at-slice, cancel-ACK-before-stop, lost response/restart and
   wrong-runtime/foreign-handle/unconfirmed-stop rejection at the faithful provider
   seam; retain unknown and prove zero automatic resubmissions. A separately
   allocated physical parent-death gate must inspect the original finite provider
   timeout and quarantine/reconciliation, not infer remote death from local flock.

At the original B snapshot, only AST, Ruff, diff hygiene and static closeout had
executed. J-min has subsequently executed the synthetic source tests; the actual
provider/device gate remains separate until its exact receipt is recorded. The closeout
planner selects full because this approved dependent branch contains prior work;
Root owns the assembled exact-head test dispatch and independent review.
The earlier CPU2 proposal is historical context, not an additional resource grant.

## Stable monitoring through the existing application

ProjectConfig.remote_training accepts one typed RemoteTrainingConfiguration
containing fixed Modal configured targets. Its persisted
spireagent/remote-training-configuration-v1 value uses configured-training-target-v1
targets, with no factory, module, shell command, executable or credential field.
Legacy project files omit this optional value and retain their original representation.
Setup preserves an explicitly selected remote configuration. Application constructs
the fixed bindings and passes them to its existing local_training service, which
already serves the browser HTTP and native API; those surfaces share its journal.

A configured target is an expected tuple and receipt reference. Discovery does
not call the provider or infer runtime qualification from configuration. Missing
trusted recipe lifecycle hooks produce remote_recipe_unavailable; missing or
changed composition on reopen preserves the original recorded identity and
removes executable actions. No GET admits data use, submits a job, reconciles a
provider or constructs a model.

The recipe's existing source verifier produces the frozen training_plan once
before the operation journal is written. It contains the actual labelled chunk
plan (updates_per_epoch, epochs, update_cap, planned_optimizer_updates,
planned_boundaries, chunk_count_per_epoch, original fixed tbptt_advances=4),
source/digest references, distinct source counts and the whole-cohort target.
progress.total comes from this saved planned work; an update cap is separate.
accepted_counters holds verified cumulative optimizer updates, repeated label
uses/observations, boundaries, completed epochs and whole-cohort passes. The
application validates shape/digest and projects these facts; it does not compute
research counts on a monitor GET or select new model/training parameters.

last_progress_at records advancement in accepted domain counters.
last_checkpoint_at and checkpoint_progress identify an accepted checkpoint.
observed_at is only the query time. A fresh response or same-count checkpoint
must not appear to advance learning. Repeated GET and already accepted reconcile
leave the original journal and accepted timestamps unchanged.

remote_execution contains configured_target, declarative expected_device and
configured_runtime, independently labelled readiness and qualification reference,
and observed_runtime only after exact domain acceptance. It supplies original
provider_refs (prepared/App/function/call IDs), a durable provider_phase, stop
inspection, finite slice_bounds and within_slice_progress. The current executor
exposes terminal slices; work inside a live/unknown slice is explicitly unknown,
never a fabricated percent. The cost projection labels cumulative reserved maximum
and original cap; metered_microusd=null and meter_state=unknown are retained without
a meter receipt.

Browser/panel closure leaves the existing supervised owner running. Backend death
preserves a blocking original attempt and requires exact reconciliation; this
packet does not promise uninterrupted training across backend death. The browser
watcher and labelled native rendering belong to their existing UI writer and
must consume these same fields before claiming whole-product monitor readiness.

The initial B packet's proposed source gate was 30 pure functions (36 cases) in
tests/test_remote_training_application.py, without Torch or provider calls.
J-min has expanded and executed those regressions and the real numerical recipe
with a provider substitute. Current exact-head counts are execution receipts,
not this historical proposed count.
It covers ordinary typed Application configuration, closed/missing/stale bindings,
same owner HTTP status, frozen plan versus cap/data/repetition, accepted timestamps
and original identities after reopen, unknown current slice and metering, plus
the existing lifecycle/stop/recovery/control/budget negatives. These fixtures were originally
authored but unexecuted; J-min now exercises the assembled source and reports its
exact local test receipts separately. Actual provider/device qualification remains a separate execution gate;
it does not follow from the source tests.

Current J-min boundary: finish the reviewed reusable source, selected full gate,
local synthetic diagnostic and Root's bounded USD 1 remote campaign. Long fits,
game runs and real-data training remain outside this packet. The existing graph,
Input/Target, history and fixed four-advance TBPTT semantics remain selected;
independent batch execution and CUDA are the authorized numerical backend change.
A new model or first scientific training configuration still needs user approval.

## Original wall budget and known no-effect control

The unreleased configured target now requires explicit cleanup_seconds, with no
default. Admission requires the original request wall budget to contain the full
declared provider startup plus execution window and that cleanup reserve.
The original attempt's absolute deadline is persisted before domain preparation,
projection writes, deployment or submission. The provider's remaining budget
always comes from this original owner wallet, reserving cleanup while executing.
Each provider operation and internal lookup rechecks that same shrinking budget;
submission cannot offer a full slice after preparation has consumed its room.
Budget/cost/history are never replenished for an unknown original attempt.

Trusted remote preparation and acceptance receive the original owner budget
fence. Input preparation checks it at its existing bounded input/write boundaries.
Read-only source-identity/preflight helpers are not hard-preempted in this source
packet; an overrun is accounted and must be rechecked before any provider effect.
No hard total preflight timing or current runtime readiness is claimed.

When the original wallet expires, automatic execution leaves the original
identity unknown and makes no fresh provider call. An explicit existing reconcile
request may use only the original saved cleanup_seconds as a separately recorded
finite control-only allowance. It records start/result and immutable prior
control receipts, cannot claim a new prepare/submit, and never extends the old
training grant. Function timeout alone never establishes an App stop with zero
tasks/containers.

Claims distinguish first admission, durable prior intent and control-withheld
admission. A pause/cancel before first prepare is a known no-effect terminal with
no provider lookup. Withholding submission after a prepared App still requires
exact original App stop/zero-resource proof; it does not invent a call. Any
consumed intent with an uncertain outcome remains unknown and cannot be retried.

A known no-effect terminal is sealed in a private immutable receipt bound to the
original operation, attempt, request, grant and App stop proof. Only a new explicit
ordinary remote-V2 TrainingRequest may name that exact predecessor using the
existing after_completed_operation_id field. Local-V1 retains completed-only
semantics. The owner requires released writer ownership, the sealed receipt,
no checkpoint/result/accepted work and no unknown/live/resource claim; otherwise
the current operation stays blocking. It archives the full prior journal and
retains its original Run, use and budget history before a distinct new operation.
No GET/reconcile allocates that successor, and no-checkpoint resume stays disabled.

The snapshot publishes next_start_precondition and start_new only after the owner
verifies this exact released predecessor. Existing GUI/native start forms copy
that precondition rather than infer eligibility or verify receipts themselves.
Original provider phase adds not_started/not_started_cleanup_pending; original
deadline, cleanup reserve and explicit control observation are exposed as facts.

The historical B gate listed 30 authored functions (36 parametrized cases).
J-min has executed the expanded source regressions, including fake-clock
prepare/input/submit/poll/cleanup accounting, pause/cancel barriers, uncertain
prior claims, explicit control-only reconciliation and sealed successor admission.
Actual provider/image/CUDA qualification and billing remain separately recorded
in Root's campaign receipts. These lifecycle fixes choose no new ModelPlan and
permit no long fit or real-data training.
