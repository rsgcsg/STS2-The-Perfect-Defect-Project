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
Selection/profile/goal/requirements changes require explicit re-admission; missing
source/context/SDK capability remains blocked. UNKNOWN never permits automatic
retry or reauthorization.

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
