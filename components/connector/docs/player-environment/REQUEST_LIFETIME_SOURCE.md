# Shared request lifetime source evidence

Date: 2026-10-08. Change class: G4, cross-layer identity/lifetime behavior.
Workstream: `codex/e1-v1-request-lifetime`. Baseline integration:
`a3499647be6f6c6cbd4c312380e0e9f320a26e41`; frozen lifecycle foundation:
`caef0f60a951f6c6a06836da187cebea710ca319`.

The first incorrect fact was that a runtime-global request fingerprint could remain
while its original typed result disappeared, or that an unbounded typed-result map
could keep complete successor/native graphs. One existing shared request namespace
now owns every profile's original pending state, permanent spent identity and frozen
terminal bytes. Client expiry/revoke comes from the existing Authority. No new
legality engine, gameplay authority, submission retry or result ledger is added.

Native results use 2 MiB output/4 MiB reservation; legacy/text results use 8 MiB
output/16 MiB reservation. Reservations allocate controlled 64-KiB blocks before
admission. One result-only arena retains/reuses at most 512 MiB of backing-array
capacity. This is separate from public-capture memory, process RSS, object metadata,
transient typed inputs, native game graphs and network/kernel buffers. A sender loan
keeps actual backing blocks charged until its last write finishes, even after expiry.
No 512-MiB physical-pressure or loaded-game performance exercise was run here.

Text-only effects prepare and encode their complete mandatory successor before
committing cursor/selection/revision. Capacity denial leaves those facts unchanged.
After actual native input, only an optional immediate diagnostic observation can be
omitted. Original native delivery/action/attribution/known stages remain intact.
Pre-input oversized actions may use the existing action-null rejection shell; the
original immutable request retains its submitted bound ID. Started originals remain
pending until their actual terminal seals, including when the client closes.

Queued closure cancels its original `MainThreadWorkQueue` token outside owner locks.
It removes pending work and completes the original sender without another drain.
Already-started callbacks keep their actual result. The native source prefix and
once-only terminal observer run outside namespace, Authority and preparation locks;
the observer synchronously extracts compact metadata and retains no terminal graph.

The bounded terminal codec uses a deliberately closed, read-only .NET 9 private
`JsonElement.GetRawValue` ABI. Runtime major, signed assembly identity, actual MVID
and informational version are recorded; a probe checks root/nested/raw numeric bytes.
Unsupported identity denies new IDs, with no serializer/reflection-copy fallback.
This approved prototype remains a portability liability and needs independent review
and exact final loaded-framework qualification before promotion.

Fresh source validation before the owner-lifetime repair: Host Release compiled
with zero warnings/errors; all 477 Host tests and all 86 portable tests passed
with zero skips. Portable coverage includes
17 lifecycle cases and 16 request/arena/codec cases: original serializer parity for
escaped Unicode/raw numeric/nested property/null/date values, unsupported ABI before
admission, exact envelope boundaries, charged loans through expiry and blocked IO,
physical pool reuse, mandatory prepared commit/discard, duplicate byte replay,
quota/close/admission races, timer-only retirement, once-only observer lock release,
and actual queued-versus-started cancellation without another drain. Host regressions
also cover mandatory text capacity preserving state and closed/expired/foreign Current,
Attach and Retain denying allocation without extending idle time. Existing SDK193 tests,
typecheck and build passed; remaining component/root gates are recorded in the private
exact-head packet before lead integration.

Rollback is the parent source commit plus a normal component merge preserving
path-scoped source provenance; installed rollback belongs to the final composition
owner. Cross-repository pins, component versions/BOM, install, game launch, source-v2
recording integration, SDK final-revoke integration, model/training/research admission
and Human/runtime qualification are outside this source packet. Tests here are source
and exact-game-ABI checks; they do not prove native input, native Commit or G2/V1 readiness.

Independent review of `0582258bb1d36e68842927c8edd8e041943f22ec` found two source gaps:
public Renew did not invoke its bound idle/liveness touch, and early service activity
checks could race closure cleanup before Current/Attach/Retain allocated resources.
The successor validates Renew ownership/cursor before its existing Authority touch,
and makes final no-touch admission and closure cleanup use the actual Hub/Store gates.
Current pin ownership is one optional field on the existing capture Entry, with no new
client registry. Source and foreign pins remain independent. Actual Host composition
always binds the check; unbound standalone portable owners preserve their old semantics.

The reviewer's original four probe sources were rerun unchanged against the actual
Host project and all passed. The repair additionally exercises valid and invalid Renew,
closed Store admission without retained charge, and a real admission-wins barrier:
A closes after Store admission, B's pin and admitted catalog loan keep bytes charged,
and the independent Source capture survives A cleanup. Final source checks and exact
successor identity are recorded in the private frozen repair packet.

The final owner-repair source compiled with zero warnings/errors. All 482 Host and
94 portable tests passed with zero skips. Five Host barriers include all three
closed-before-allocation cases plus Attach and Retain admission winning before
closure: cleanup sees late resources, the service cannot return a stale success,
and B's subscription/pin/controller lease survives. These are source tests against
the actual queue, Hub, Store and Authority, not a loaded game or Human qualification.
