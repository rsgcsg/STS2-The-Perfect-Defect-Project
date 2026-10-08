# Native logical source recording profile

Status: E2 source-recording foundation candidate, 2026-10-08. Base
`86fc11e1da78d3cdbf0957caf8967c4ac64ac5a1`. This is an additive implementation
contract, not native exposure, Human, causal or full G2/V1 qualification.
The Connector `components/connector/docs/NATIVE_LOGICAL_PROFILE.md` contract
owns the public facts, exact input relation, source clock and capture bytes.

## One owner and explicit source

The existing RecordingApplicationService and RecordingSessionStore own lifecycle
and persistence. `native-logical-source-v1` is an explicit new capture profile.
Legacy Human profiles, streams, bundle3 and Human attestation checks retain their
meaning. The new profile writes no Human canonical transitions and adds no causal
tracker. Game outcome/Continue-ready does not automatically close this profile;
explicit Close or the owning task's complete boundary closes it after required
summary navigation. An unavailable producer bridge rejects Start, not a silent
fallback to sampled or legacy capture.

`SourceDeclaration` has exactly `source_kind`, `actor_id`, `declaration_id`,
`machine_verifiable`. Kinds are `declared_human`, `agent_native_ui`,
`agent_protocol`, `unknown`. Identifiers are bounded opaque public identifiers,
not credentials. `machine_verifiable` must be false. A declaration states the
operator's assertion; a native UI event cannot prove a person. No new bundle
contains `human_origin_attested:true` or borrows old Human qualification.

Start requires a declaration and an actual bridge attachment containing exact
producer/environment identity, accepted scope/coverage and source clock. A
source clock is `{stream_generation, publication_index}`; its index is a canonical
U64 decimal string referring to the source hub's reserved position. The source
profile records the producer's advertised coverage, including unsupported seams;
it never upgrades them. Normal required-seam gaps remain qualification failures.

Source changes use `ChangeSource` only while Paused, with the exact prior
`segment_id`. The owner obtains the actual boundary clock from the bridge,
appends a new immutable segment and makes it effective only for later work.
Clock generation must match attachment; boundary cannot move backwards. Recording
pause/resume boundaries are durable source accounting in `source-boundaries.jsonl`,
not native inputs. Pause stops admission of new inputs/observation occurrences;
already reserved pre-pause captures and input scopes may finish. The passive
bridge stays attached to drain those packets. Resume records the intervening
source interval as `recording_paused`; it never fills that interval from Current.
Every input scope retains its original segment at Begin; later completion cannot
adopt a newer actor. No existing observation or input is retroactively relabelled.

## Immutable additive streams

The new recording manifest is `sts2.annotator/source-session-manifest-1`; its
profile is `sts2.annotator/source-capture-profile-1`. It binds session/timeline,
producer/environment, attachment scope/coverage, limits and initial declaration.
Legacy Human manifest validation does not accept this as a Human manifest.

`source-segments.jsonl` contains `sts2.annotator/source-segment-1` records:
sequence, session/timeline, segment ID, previous segment ID or null, declaration,
boundary clock and recorded time. The first segment is the initial attachment boundary.

`public-observations.jsonl` contains `sts2.annotator/public-observation-1` records:
sequence, session/timeline, active declared segment ID, generation/publication
index/scope, source seam/index/phase, snapshot/owner occurrence where available,
capture/catalog references or null, completeness/coverage and explicit missing/gap reason.
The declared segment records collection context, not causal authorship of automatic
world changes. Association follows the occurrence's original source clock and
immutable segment boundaries, never the latest segment at delayed append time.
Duplicate source projection retrieval is idempotent; its content
cannot change. Generation changes are explicit gaps and require a new attachment.

`native-input-witnesses.jsonl` contains `sts2.annotator/native-input-witness-1`
records: sequence, session/timeline, input ID, frozen segment ID, pre-input clock,
actual pre-capture and complete catalogue, exact mapping status/count, selected public action or null,
native mechanism, delivery disposition and reason. Mapping is `exact`,
`unmapped`, `ambiguous`, or `capture_missing`; delivery is `rejected_before_input`,
`delivered`, `partially_delivered`, or `unknown`. Native witness references are
opaque evidence IDs, not native objects or executable callbacks. Mapping cannot
create action authority. Input-pre/callback bookkeeping does not advance Model W
when snapshot/owner occurrence is unchanged.

Capture blobs live under `public-captures/sha256/<prefix>/<digest>.bin`. Persist
the exact supplied frozen bytes, length and SHA-256; never recanonicalize JSON.
References bind capture ID, snapshot, scope, generation, byte count, digest and
relative payload path. Original publication payloads never change on expiry.

The observation bytes contain the catalogue descriptor, not its full action array.
Persist a separately frozen `FrozenPublicCatalog` with exact UTF-8 full-array bytes,
payload SHA-256, structural catalogue digest/count, catalogue reference, snapshot,
scope and generation. Every full-reference observation and every complete input
basis joins both objects. Catalogue payload bytes are separately content-addressed;
the array's membership/order and structural digest are independently verified.

Per-stream sequence and identities are checked before append. Limits bound capture
bytes, total persisted public bytes, row bytes, segment count and per-stream count.
Capacity failure appends a reserved bounded gap/accounting row when possible.
Disk/append uncertainty marks source accounting failed and prevents a successful
close receipt or bundle; no subsequent current read backfills the lost position.
Pause and Close drain already admitted input scopes explicitly; unfinished scopes
close as unknown, preserving their original pre-capture/declaration. Close flushes
all source streams and includes exact counts/hashes in its source close receipt.

## Passive composition and export API

The Core interface `ISourceRecordingBridge` exposes only `Attach()` and
the resulting attachment's `Activate(observer)`, `ReadBoundaryClock()`,
`IsDrainedThrough(boundary)` and `Dispose()`. Attach returns an
immutable context (profile, scope, coverage, environment, starting clock) and
reserves the initial observation without invoking an observer. Activate occurs
only after the store/lifecycle exists and replays from the starting cursor,
including that initial reservation. Close drains through its actual source
boundary; a pending encoding becomes a typed gap by the producer's finite deadline,
not a fabricated current capture. The bridge exposes no Submit, native operands
or controller methods. Connector/native producer code owns publication hooks.

Core store signatures:

```csharp
RecordingSessionStore.CreateSource(root, manifest, SourceCaptureProfile profile,
    SourceDeclaration initialSource, SourceClockReference initialClock);
SourceSegment ChangeSource(SourceDeclaration declaration, string expectedSegmentId,
    SourceClockReference clock, RecordingLifecycleState state);
PublicCaptureReference PersistPublicCapture(FrozenPublicCapture capture);
PublicCatalogReference PersistPublicCatalog(FrozenPublicCatalog catalog);
void AppendPublicObservation(SourceObservationPacket packet);
SourceInputScope BeginSourceInput(string inputId, SourceClockReference clock,
    PublicCaptureReference? preCapture, PublicCatalogReference? catalog,
    RecordingLifecycleState state);
void CompleteSourceInput(SourceInputScope scope, SourceInputOutcome outcome);
SourceInputScope BindSourceInputBasis(SourceInputScope scope,
    PublicCaptureReference? preCapture, PublicCatalogReference? catalog);
```

The application command adds SourceDeclaration and expected segment fields;
source requests preserve exact command fingerprints. The Mod composes the passive
bridge through its existing RecordingApplicationService. Root supplies the real
bridge later; this packet implements only the typed boundary and faithful fixtures.
Source input scopes have an announced count bound. Identical completion retries
are idempotent; a changed completion for an existing input ID is rejected.
Begin may reserve the source token with null payload references before any capture
encoding work; Bind then sets that same pre-input basis exactly once and retains
the original segment. It never reads a later current state to repair missing basis.
Declaration/clock/segment identifiers are scheduling evidence, not Model features.

`SourceSessionAudit.Audit(directory)` checks streams, source bindings, exact bytes,
gaps and close receipt. `SourceSessionBundlePacker.Export(directory, output)` copies
the audited source observation/input/segment streams and required capture blobs.
`Pack(directory, workerId, campaignId, output, packerSourceRevision)` publishes
`sts2.annotator/source-session-bundle-1` with exact inventory/checksums, source-kind
summary, producer audit and export identity. Identical retries reuse exact bytes;
changed retries fail. A separately registered Evidence verifier checks this schema
and raw/export equivalence. The old Human packer rejects source-profile sessions
even when a caller supplies a Human attestation boolean.

## Required foundation checks

Check missing/invalid declarations; paused exact-segment source changes; original
segment retention across asynchronous completion; exact capture bytes and corrupted
hashes; duplicate projection/input identities; explicit coverage/gaps; capacity and
disk failure without false closed state; unknown unfinished input at Close;
GameOver not closing the source profile; generic bundle verification/tampering and
idempotence; old Human pack rejection and unchanged legacy fixtures. Synthetic CPU
checks qualify the foundation only. Every required native L mechanism and source
hook remains required follow-up work.
