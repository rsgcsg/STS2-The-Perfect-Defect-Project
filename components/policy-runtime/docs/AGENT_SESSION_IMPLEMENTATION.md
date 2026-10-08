# E3 Agent session implementation checkpoint

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
