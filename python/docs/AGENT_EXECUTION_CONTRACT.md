# Common executable Agent composition and run request

This G2 application contract is implemented in the existing Workbench selection,
registration, process and result owners. It adds no controller, Task service,
execution journal, wallet, gameplay legality or dynamic plugin loader. Its first
structurally different consumers are Public M2's stateful Decision4 and the
native program Teacher's model-less AgentSession; M0/native learned retain their
own model, input, state and driver semantics.

## Composition and authority

`spireagent/agent-composition-v1` is code-owned interpretation of an already
registered, hash-bound manifest/config/original artifact. It separates semantic
Agent definition (program, zero or more model bindings, input/history/state/reset,
Task/program binding and environment requirements), implementation bindings
(adapter/code/manifest/config/artifact/package provenance), and per-run resource/
environment/Runtime identity. Legacy inseparable packages are explicitly bound
compositions; no nonexistent metadata or cross-profile equivalence is invented.
A model-less Teacher has `models: []`, a real program descriptor and ACK-committed
script state, never a dummy Model/weights/checkpoint. `models` unknown is different
from an explicitly empty declaration. Only supported existing zero/one-model
compositions are executable; an array is not arbitrary multi-model support.

Closed trusted domain support supplies composition description, admission,
readiness, launch arguments and original metadata. User/artifact data cannot
supply a module, shell command, executable path or plugin. Teacher and native
learned share `native-logical-v1`, but have distinct trusted adapters/validation.
Teacher readiness requires Runtime/SDK/environment/code and no neural backend.
Other adapters retain their exact existing backend/package checks and masks.

One pure STPD Teacher manifest constructor serves builtin registration and the
Source3 collector. Descriptor/spec construction remains STPD-owned; the existing
Runtime `validateAgentManifest` remains wire authority. No copied Python Runtime
schema validator or duplicated JS versions/limits/seam rules. Collector Host,
control, collection and data admission loops remain unchanged.

## Run admission and compatibility

`spireagent/agent-run-request-v1` fixes registered selection/composition identity,
effective Task/program refs, target/environment/resource, named actual budget
units and operation before dispatch. Task/program choices select an admitted
composition; they never cosmetically override immutable manifests. Unsupported,
stale or mismatched combinations reject before child/control side effects.

Existing `LocalModelService` admission/intent/locks/process/Stop/control are the
sole execution owner. Existing start versus prepare/load/takeover preserve their
exact defaults, modes, installation behavior, intent rules and error semantics;
legacy APIs translate into the common request, without a second intent or loop.
Human load remains non-mutating gameplay; takeover remains the existing compound
intent. Environment/native authorization is obtained through current trusted
Application callbacks, not supplied as authority in an HTTP payload. Named
budget counters retain actual meanings: AgentSession Next/Consume/program calls
are not renamed as NN calls. No new placement, cloud or resume capability is
inferred from the envelope.

An immutable application request artifact is published by the existing state
owner before the first dispatch side effect. It is not mutated when operation,
startup, Runtime run or evidence identifiers arrive; existing session/result
records contain those links. It creates no new journal/registry. Old runs without
original requests stay legacy/unknown; no backfill into sealed Runtime evidence.

### Ordinary native episode binding

Application and its existing LocalEnvironmentService may prepare one owned
native episode, release its bootstrap controller, and admit the existing common
Agent request. A separate attached branch uses the configured existing endpoint
as a fresh cold segment; it creates no Host, profile, seed or speed mutation and
has no Host-close authority. `admission_context.native_episode` is a closed
optional v1 binding by value, omitted for all legacy requests. It records the
opaque resource reference, application/configuration generation, actual runtime
and trusted endpoint binding, exact installed tuple and original handoff proof.
Owned profile/presentation/speed facts require actual Host readback. Attached
requests omit those facts. No private path/token/native operand or resource
metadata enters a Model input. `native_intent` keeps its independent Mod-pair
meaning and cannot be substituted for this binding.

The existing Model admission lock serializes Source guard, native reservation,
immutable request admission and the immediate pre-spawn recheck. Locks are
released before Host/network waits and exact Actor Stop. A preparing, ready or
associated native resource blocks a second prepare and unbound Actor creation;
legacy behavior is unchanged when no native resource exists. Configuration,
generation, runtime, original request or handoff drift rejects before new
authorization. Attached current-start support is composition-specific; no past
history/W or game-save resume is invented.

Host preparation precedes Actor startup. Unknown preparation/handoff is retained
and quarantined through the owned client, never retried or treated as admission.
The reference driver advertises bootstrap handoff before reset; after transfer
it refuses reset/step/reacquire. EOF/cancel reaches the factory AbortSignal before
waiting its queue. While the Actor is associated, no Host-driver observer,
provenance, gameplay or status RPC runs; existing bound Runtime/Connector reads
remain the owners. A late driver exit may stop only its original Actor, and
cannot affect a replacement. No continuous speed-monitoring claim is made.

Explicit finish fences new admissions, stops/exits the exact Actor through the
existing Model owner, seals its original result when available, then closes only
the owned Host. Seal failure does not skip Host cleanup; independent Task facts,
cleanup uncertainty and driver/native exit receipts remain separate. Attach
only detaches. Repeated finish returns original cleanup facts. Controlled App
shutdown preserves Actor-before-owned-Host ordering. Crash/EOF is containment,
not a sealed result or proof of that order; persisted active resources reopen
as recovery_required with no auto-adoption, reset or spawn.
Selection/profile/goal/requirements changes require explicit re-admission; missing
source/context/SDK capability remains blocked. UNKNOWN never permits automatic
retry or reauthorization.

Native preparation intent/result history stays under this Environment owner.
It uses existing immutable local-byte publication and atomic operation records,
with one live resource pointer, rather than another controller or binding
registry. Original accepted typed bytes and body digest are durable before
spawn; serial experiments do not overwrite old request/result identity. A
historical duplicate or finish is a recorded-result read and cannot address the
new live Actor/Host. Interrupted, missing or corrupt retained state has no
reconstructed handle or fresh permission. History has no automatic retention
expiry; storage loss never authorizes silently replaying an old world mutation.

## Result witness and original identity

Catalog, readiness, launch and results use the same registered composition
interpretation. Operation completion, Task completion, observed win/loss and
strategy quality remain independent. Existing Public M2 qualified ordinal/cache/
terminal step rules and native learned witnesses are unchanged. Original artifact,
manifest, weights, old results and historical schemas remain immutable.

Teacher Task completion is a closed composition-selected witness mapping:
qualified consumed current ready-summary body with matching exact ConsumeACK,
its input/consumption/state watermark, and exact Close reason
`natural_ready_summary_win` or `natural_ready_summary_loss` matching that body's
result. A generic Close, reason prefix, later poll, unACKed body, wrong watermark,
ordinary cancellation or budget stop cannot prove completion. Taint/unknown/status
precedence and original-byte verification are retained. Missing Teacher descriptor
or original request is unknown, not a current-default substitute.

## Scope and acceptance

The product slice includes ordinary builtin Teacher registration through the
same registration lock/private roster, explicit task/program selection and
common runtime owner. Requiring an operator-built collector manifest alone is
only an executable intermediate milestone. All registration config is real
installation metadata, not fake model data. Only manifest construction is factored
out of Source3; collection/data/Host authority remains where it is.

Acceptance uses actual trusted launch and child/Runtime/installed SDK seams for
Public M2 and Teacher, legacy start/prepare/load/takeover parity, immutable
request and missing-original failure tests, exact ACK/Close positive/negative
witness tests and unsafe command/path/identity/target rejection before spawn.
Portable tests do not qualify a game, original model quality, Human or training.
Versions/BOM/pins are lead-owned closeout. The current conservative M0 code digest
may change with application source; old selections remain original and current
registrations rebind strictly rather than weakening the digest.

## Concrete application interfaces

`LocalModelService.composition(selection_id)` resolves the effective original;
`agent_request(selection_id, operation, run_profile)` returns a detached effective
request. `run_agent(request)` validates it again before existing admission and
executes `start`, `prepare-and-load` or `prepare-and-takeover` through their
existing owners. Trusted native callers may pass the same native context and
authorizer callbacks as before; their references enter request metadata. The
HTTP payload cannot provide these callbacks or obtain authority by inventing
context fields. Ordinary browser/control-client guards are unchanged.

The existing Application exposes `POST /api/local-models/run-agent` for that
exact request, and its normal local registration owner exposes two closed
convenience routes. `register-teacher` accepts `variant` (`baseline`, `owned`,
`continuous`, default `continuous`). `register-composition` accepts a model ID,
optional existing environment kind, and explicit `task_option` (`default`,
`none`, `ready-standard-summary`) plus `program_option` (`none`,
`card-reward-take`). Public M0/M2 owning TaskSpec factories resolve these names;
the card chooser is Public M0-only. Original model registration routes preserve
their existing defaults. Choices produce a new real registered composition or
reuse an exact match; they do not update old manifests or load a game.

Request files under the existing service's `requests/` directory contain an
operation ID and fixed effective input. Session state separately stores the
request reference with the accepted startup's exact run ID. A later failed
operation using the same manifest cannot relabel that startup or its evaluation.
Application results report `agent_execution.status` as `bound`, `unknown` or
`legacy_unknown`; legacy requests are never invented. Teacher application Task
completion also requires the original request association; missing association
keeps an independently proved terminal observation while completion is unknown.
Standalone typed-evidence projection can still report its original Task witness
without claiming an application request association.

The shared Teacher manifest factory lives in
`stpd.policy.native_teacher_manifest`; the collector uses its bounded pure JSON
bridge and keeps its existing Host/control/data loop. The factory's fields are
construction declarations, not a second wire validator. Runtime remains the
required wire admission authority.

## Managed research evaluation results

The adjacent `spireagent.managed_task_outcome` read-only projection
accepts the versioned raw Managed evaluation file-index grammar and externally
retained index SHA, rechecks exact files/source/config and original episode joins,
and then produces the common Task/terminal/operation shape. It does not run or
change the world. This is scoped catalog-only engineering evidence and is never
sealed Policy Runtime evidence. Old `project()` verification and native/public
TaskSpecs remain unchanged. See [Managed suite evaluation](MANAGED_SUITE_EVALUATION.md).
