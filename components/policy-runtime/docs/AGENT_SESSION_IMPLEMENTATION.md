# E3 Agent session implementation checkpoint

## Owned Current and bounded stale decisions (2026-10-10 source candidate)

The additive policy in [AGENT_SESSION_PROTOCOL.md](AGENT_SESSION_PROTOCOL.md)
selects the Connector's advertised `current_owned` mechanism. A successful
Current transfers its initial capture pin to the original reader atomically.
The SDK owns a coherent decoded reader before later admission checks, transfers
one small lease through assembly, and disposes observation/catalog buffers.
Runtime retains that lease for the acknowledged basis through a serialized Act;
discarded queries, replacement, failures and Stop release the original reader.
A cleanup failure immediately fences Auto and releases control, and Stop attempts
every remaining lease even after evidence or detach failures. Legacy Current's
120-second initial pin remains unchanged.

The native port receives the exact validated policy as its fifth constructor
argument or explicit spawn option. Runtime checks policy equality before Ready,
registration or native requests. Legacy Next has five fields; an explicitly
selected policy requires the sixth nullable `operational_outcome` field.

An exact original `not_started / stale_snapshot_or_binding` result may request a
new decision under the same W/prefix/child, within both total/streak ceilings and
only while the owning Runtime still admits Auto/control/budget. An unchanged
NativeUnit only waits. Same-occurrence revision/coherence drift fails closed;
a changed unit must strictly advance the qualified revision. The fence clears
only after an advanced new ConsumeACK and completed Next. No old action, request,
basis, native operand, epoch, prefix or count is replayed or reset.

The awaited SDK dispatch hook supplies four immutable original attribution
fields after body validation, before the existing synchronous start notification
and one POST. One extended existing submission-intent fact records them; native
terminal results and reconciliation retain the original binding. Unique joined
terminal results count before operational eligibility, including threshold and
late Stop/deadline/source-loss results. Delivered resets the streak only.
Source-loss/Stop/budget causes retain the first owning stop reason. Typed evidence
can verify recorded binding/count/watermark/deferral consistency; unrecorded live
lease expiry and budget eligibility remain source-tested Runtime guards plus
final native admission, not an invented independent witness.

Required source regressions compose the real game-free C# Store/Projector,
REST SDK, Runtime and a programmed OS child; control/capability/Await replies
remain synthetic. A separate OS Python Teacher composition uses its real code
closure, InputSpec and a private source descriptor with no Torch. These tests do
not qualify a production package, installed/loaded artifact, game, Human origin,
collected research N, model training or learned gameplay. Root integration owns
versions/BOM/pins, installed verifier and final producer tuple, exact-game checks
and any later runtime/data/model promotion. Historical checkpoints below retain
their own evidence limits.

## Current native integration candidate (2026-10-08)

Dependency base `f89a5267f43ea206fe39d081f5fd5d3643953ca8` includes independently
accepted SDK `651f9cbd163d177805fa641ca0f05b8dcb424877`. The actual native branch
now runs through `PolicyRuntime.forAgent`, the shared lifecycle owner, existing
HTTP service and explicit CLI manifest discriminator. It owns one registration
and controller, passive atomic Attach/Events, serial full-reference input,
descriptor-only scoped queries, consume/ack watermarks, cancellable Await,
deadline/Stop, exact original-request reconciliation and typed append-only events.
The details below are historical source checkpoints, not current missing work.

Initial `8ef88c13` canonical component check: TypeScript, 298 required tests, build and
deterministic package check pass. The package check's installed CPU smoke covers
legacy profiles and reports `game_contact:false`. Newly added external-fixture
tests are separately enabled: producer export requires `E3_NATIVE_FIXTURE_OUTPUT`;
numerical interop requires Python/PYTHONPATH/an exact private Model package.
Producer export was executed and its actual 15 events independently checked by
the application verifier. Numerical interop was executed against initialized
Model candidate `73b40366c34ec34af71bbdb6abc6ac64fc799468`: real SDK/HTTP/Runtime/
stdio Consume+ACK, all-three-candidate Shadow scoring, Human epoch change,
opaque ExportState and OneStep/one native HTTP submit pass. This is synthetic
source/test transport with an initialized numerical Model; no fresh numerical
Restore parity, training, provider, game, loaded, Human or G2/V1 qualification.

Remaining limits: the factory rejects `explicit_reset` and scoped opaque recovery
before Attach. Actual native source coverage and installed/runtime qualification
belong to their exact owning packets. Independent Runtime source review, lead
integration, versions/BOM/contract/package promotion and live gates remain pending.

## Independent native review repairs

The independent `8ef88c13` review reproduced three Runtime P2 defects: terminal
publications were skipped despite carrying captured views; Human during fresh
Restore validation closed the still-valid original child before the stale epoch
was rejected; passive renewal queued behind a long Model/Await operation let an
accepted 400ms subscription expire. The owning repair counts every public source
view, checks Restore authorization before replacing the child, and renews through
one separate bounded passive SDK flight. Human preserves that flight; Stop and
failed initialization quiesce it before detach. Actual five-second Await and long
Consume tests retain the short subscription, and delayed ignored-abort tests
verify that neither Stop nor failed initialization permit a late SDK response to
resurrect it. Failure cancels active work/releases control immediately and seals
only after its queued gap is recorded.

The original source, review reproductions and numerical initialization package
remain unchanged in private `/tmp` snapshots. A new exact producer fact binds
durable original submission intent to an SDK-hook-proven not-started outcome;
generic fail-closed strings are not substituted for that fact. These repairs need
their own coherent freeze and independent recheck. The fresh repair canonical
check passes TypeScript, 315 required tests, build and deterministic package checks;
the two external-fixture cases remain separately enabled. Numerical fresh-child
Restore reproduces the same three scores and opaque payload, source advance once,
stale/pending recovery rejection, and the retained original numerical child after
Human during fresh validation. They do not
promote `8ef88c13` checks or synthetic expiry probes into game/runtime qualification.

## Actual batch-tail provenance refinement

The `4917eb31` producer advanced the SDK batch `next_cursor` without a typed tail
fact. Native Hub global positions can legitimately yield an empty selected-scope
batch, so an individual event cursor cannot substitute for that operational tail.
The new `native_event_batch_received` records the exact original request cursor,
SDK next/high/retained-start cursors and actual event count after all provided
events and full-reference ACKs complete. Empty global advance and scoped-query
ACK tests compare opaque cursor equality only; no cursor parsing, guessed order,
new Model input or W advance is introduced. Interrupted/gapped batches emit no
completed tail. Opaque Model metadata remains at the last acknowledged prefix.
Historical `4917eb31` bundles retain their original event schema and claims;
source/test verification of the refined producer requires newly generated facts.
Fresh refined-producer canonical checks pass TypeScript, 319 required tests,
build and deterministic package checks; the two external-fixture cases are
separately enabled for the exact final source receipt.

## Historical initial checkpoint

Packet base: `1597141bf2d829c8b24eebb83a5177398101cf39`.
Branch: `codex/e3-v1-agent-session`. Engineering class G2, Runtime-owned
Agent contract/transport/consumption/evidence. This is an implementation checkpoint,
not acceptance of the whole E3 native session or G2/V1.

The [protocol](AGENT_SESSION_PROTOCOL.md) and
[synthetic fixtures](../contracts/fixtures/agent-session-v1.json) were reviewed
by the lead before production edits. Subsequent reviewed refinements preserve
incremental query views, opaque inference-state recovery and one aggregate byte
budget. No legacy port/manifest 1–3, STPD projection/model or native SDK code changed.

Implemented source:

- Strict distinct AgentManifest and session envelope/directive validators.
- Binary bounded UTF-8 NDJSON framing, pre-serialization encoded-size measurement,
  duplex query/consumption handling, startup attestation, unknown-ID poisoning,
  bounded timeouts and abort/late-result fencing.
- Acquisition/consumption ledger for once-per-occurrence and incremental-view
  modes; canonical scope deduplication, coherent overlapping fields, revision
  ordering, known prefix/version, empty C and explicit gap/reset boundaries.
- Shared retained-byte reservation mechanism for SDK assembly, retained captures/
  catalog/coherence facts and pending query replies. Native assembly integration
  must reserve before allocating; a deadline cannot free still-owned buffers.
- Opaque state metadata/Base64/size/hash validation, export/restore port requests,
  exact durable-prefix expectations and explicit stateless mode. Runtime never
  decodes Model state. Existing AgentRunEvidence copies/fsyncs immutable binary
  and metadata files; new session records have explicit new schema identities.

Actual checks at this checkpoint: Runtime TypeScript typecheck and build; all
232 Runtime Node tests, including 41 targeted tests across contract/consumption,
duplex port and opaque state/evidence suites. Closeout and diff checks also ran.
The opaque state tests use synthetic byte payloads, not a numerical learned Model.
These receipts apply to this source checkpoint only; later changes need impact
review and applicable fresh checks.
The closeout tool uses the current develop comparison and also listed inherited
stack changes; this writer's own delta is bounded to the Runtime paths above.
Root full/identity/BOM/package-install gates remain pending integration-owner work.

Remaining dependency and work:

1. Freeze/independently accept native core and typed SDK, including atomic initial
   Attach and renewed long-lived subscriptions. There is no copied SDK or parser
   fallback in this packet.
2. Add the native branch to the existing PolicyRuntime/CLI/server/controller owner
   using that exact SDK: serial full-reference publication consumption, scoped
   queries, actual delivery/effect classifications, known original-result recovery,
   cancel/Await/deadline and source/evidence bindings. Legacy lifecycle gates stay.
3. Verify shared client registration before passive Attach, the same existing
   controller for Act, immediate Stop fencing, and no stable-successor gate in
   the native path. No second Runtime/controller service is permitted.
4. STPD owns the default Agent's real projection, state codec and numerical restore
   parity; E2 owns complete source capture/replay and evidence qualification.
   These facts are not established by a consumption assertion or opaque hash.
5. Root owner handles component identity/BOM/version/integration gates after final
   source review. This writer does not edit them or claim installed qualification.

No game, production package installation, provider, real data/training, publication,
push or merge occurred. Developer dependency preparation and synthetic temporary
evidence directories are the only external filesystem effects of this checkpoint.

## Independent c91 review repairs

Independent review reproduced two P2 defects on `c91c139676c9f3eba15bee7b52ce27dbec2b8ef9`:
replaying an acknowledged publication decremented the remaining observation count
again, and port timeout killed the child without aborting the dispatched query.
The repair counts only a newly acknowledged source publication, preserving a new
publication of the same occurrence without another state advance. Each port call
now owns a cancellation scope shared with its handlers; timeout, close, protocol
failure and external abort revoke it before rejecting the call.

Six faithful regressions cover replay accounting, new-position duplicate units,
and all four cancellation causes. Fresh typecheck, all 238 Runtime tests (47 new
session tests), and build pass. Actual SDK/native lifecycle integration remains the
separate pending work above; these repairs do not complete that branch or G2/V1.

## Concurrent Evidence state-count repair

The actual storeAgentState API accepted 130 distinct concurrent one-byte synthetic
snapshots on the previous head, producing 260 files despite its 256-file bound.
The existing owner now reserves two pending file slots before queue entry and
rechecks before a new write. The faithful regression accepts 128 snapshots,
rejects two for capacity, and verifies exactly 256 state files in sealed checksums.
No new queue or schema was introduced. Fresh state-suite/typecheck, all 239 Runtime
tests (48 session tests), and build pass; native SDK integration remains pending.

## Core nullable persistent compatibility

Core `18af56cca83532765acac146329a3cc21409c654` declares Persistent nullable;
its full-scope projector preserves an explicit null while completeness remains
complete. The Runtime now accepts exactly that present persistent:null value,
without synthesizing an empty object. Missing/undefined persistent and null
interaction remain rejected. The shared regression derives the actual Core wire
fixture shape and binds its rederived capture bytes/hash. Fresh contract/typecheck,
all 240 Runtime tests (49 session tests), and build pass; this is source compatibility,
not native installation or Model qualification.


## Sampled Current carry source candidate (2026-10-09)

Bounded packet base `0aedf3491d75d550740736d3b1373600dcde0ce5`; independent
writer branch `codex/e6-sampled-carry-runtime`. The new shared sampled definition
and complete synthetic vectors precede dependent Runtime/Agent/data work.
The original full-reference/scoped fixtures and publication rules remain fixed.

Runtime selects existing full Current assembly, stores exact original input
payloads before sampled proposal ACK, disposes unchanged queries, and fences
post-sample Human/OneStep/failure segments. Duplex port hooks record write-attempt
boundaries without upgrading them to child receipt. Independent Evidence checks
closed sample inventories, original capture/catalog content and ACK/directive joins.
The Python Agent stages actual carry and commits only exact ACK; the original
feature encoder and full ordered catalog are unchanged. No visited list or
opaque-state recovery was introduced.

Checks are focused pure TypeScript/Node scripted-child/SDK and independent
Python Evidence verification. Numerical tests require the separately assigned
lead heavy slot; no generic component gate implicitly starts them. The lead owns
final source review, component version/BOM/source pins, package convergence,
exact native installation, real training/canary and qualification. This source
candidate has no game, installed, Human, useful learning or natural-run claim.
