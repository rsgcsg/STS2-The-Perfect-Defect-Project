# Source recording v2: attachment epochs and real passive producer

Status: approved wire with Source V2 implementation under source review, 2026-10-08.
Revision addresses exact early setup continuity and Launch-internal callback ordering.
Implementation base is the normal dependency merge `f5a435892b6e90068e1070e20d163b67e7f69f0b`
of accepted root `fa414996f453db765884a54dc6e00f1fe880f828`.
Source v1 repair `a082862176c8b8df92d2b887d3bef3111be146a7` remains an accepted ancestor.
The contract fixture fixes the wire; production implementation and its separately
generated synthetic bundle require their own validation. This document is not a
native coverage, Human, causal, non-interference or G2/V1 receipt.

## Authority and compatibility

One RecordingApplicationService and RecordingSessionStore retain the visible
Ready/Recording/Paused/Closing/Closed lifecycle from Title through native Launch,
run, terminal navigation and summary. There is no new lifecycle or causal tracker.
Connector owns native source generation, reservation, completion, frozen public
bytes, original catalogue and input mapping. Native Foundation or neutral Game Mod
composition forwards exact existing native lifecycle witnesses. Connector never
imports Annotator. The recorder owns declaration tokens and durable bookkeeping;
those tokens cannot execute input or create a native source position.

Source v1 remains byte-compatible and readable through its existing strict reader.
V2 uses explicit new schemas; a v1 reader rejects v2. Old Human capture, bundle3,
attestation and causal accounting are unchanged. No source kind proves a person.
No Source bundle is research admission.

| Object | Exact schema/profile |
|---|---|
| Capture profile | `sts2.annotator/source-capture-profile-2`, `native-logical-source-v2` |
| Recording manifest | `sts2.annotator/source-session-manifest-2`, schema/source version 2 |
| Attachment epoch | `sts2.annotator/source-attachment-epoch-2` |
| Source segment | `sts2.annotator/source-segment-2` |
| Source boundary | `sts2.annotator/source-boundary-2` |
| Public observation | `sts2.annotator/public-observation-2` |
| Native input witness | `sts2.annotator/native-input-witness-2` |
| Close receipt | `sts2.annotator/source-session-close-2` |
| Audit | `sts2.annotator/source-session-audit-2` |
| Generic bundle | `sts2.annotator/source-session-bundle-2`, descriptor `source-session-bundle-v2` |
| Recording command | `sts2.ai-platform/recording-command-3` |
| Recording status | `sts2.ai-platform/recording-status-5`, additive `source_v2` |

Profile v2 declares `input_profile:native-logical-v1`,
`publication_profile_id:native-logical-publication-profile-v1` and
`publication_profile_definition_sha256:c060cfd354c6702e10711e6329848836ec133f2117841750e317b6ab27b244cf`,
canonical requested full scope `persistent, interaction, referents, catalog`, limits
and fixed non-claims. The canonical publication definition is the Connector contract
`native-logical-publication-profile-v1.json`; its target is distinct from the actual
accepted seam coverage. Every immutable epoch binds the same definition identity.
Accepted scope, generation, coverage and continuity belong to immutable epoch rows,
not to a rewritten manifest/profile. Producer environment and initial declaration
are manifest-bound. The same game process/build is required across epochs.

## Native positions and attachment epochs

`NativePosition` has exactly `epoch_id`, `stream_generation`, `publication_index`.
The index is the canonical U64 decimal string supplied by the original source hub.
Recording sequence/epoch ordinal orders bookkeeping only; neither becomes a
replacement native clock or Model feature. Positions compare numerically only
inside the same epoch/generation. Epoch ordering comes from their immutable chain.

`source-attachment-epochs.jsonl` records exact fields:

```text
schema, sequence, session_id, timeline_id, epoch_id, previous_epoch_id,
context { publication_profile_id, publication_profile_definition_sha256,
          scope_id, stream_generation, eager_scope, seam_coverage,
          environment, game_continuity_id },
starting_position, initial_position,
predecessor_seal or null, transition or null, recorded_at
```

The first epoch has no predecessor/transition. Atomic Attach returns native N and
reserves initial observation N+1 on the same native turn before callback interleave.
While recording is attached, its internal source subscription is also selected for
bootstrap reservations requested by other observers. A non-selected position, if any,
requires explicit `not_in_scope` accounting; it is never labelled a missing native
exposure or repaired by Current.
A successor epoch has an exact predecessor seal and a typed transition witness.
`NativeSeal` has `epoch_id`, `stream_generation`, `reserved_through`,
`completed_through`; the two indexes are original hub watermarks, and completion
may initially lag reservation. Its immutable requested boundary never advances
because a later Current read happened.

`NativeTransition` has `witness_id`, `kind`, `mechanism`,
`previous_game_continuity_id`, `game_continuity_id`, `start_provenance`,
`graceful`, `victory`. Nullable fields are explicit. Emitted kinds are
`setup_handoff`, `launch`, `terminal`, `cleanup`; provenance is `new`, `saved`,
`unknown` for setup/Launch and null otherwise. `setup_handoff` proves only that
an exactly staged native setup invocation's actual RunState now owns continuity;
it never proves native Launch or a fresh start. Only exact setup handoff or Cleanup
that changes actual continuity creates an epoch. Launch and terminal entry retain
the existing epoch, including required terminal summary navigation. `process_exit`
is reserved and unproduced in this implementation: only an actual Host-exit witness
could support it; ExitTree, Task completion or a cleanup callback cannot do so.

The exact native order matters. SetUpNewSingleplayer assigns State before its
initialization calls. SetUpSavedSingleplayer assigns State before its first await.
Launch invokes RunStarted synchronously before the existing Launch postfix.
The existing typed setup patches therefore stage a bounded exact invocation context
at Prefix, binding its RunState object and new/saved provenance before the body can
publish new-continuity callbacks. The sole native owner consumes that context only
when its actual current RunState is the same exact staged object. It seals the old
Hub generation and creates the prepared epoch before capturing that first callback.
Staging alone neither turns over the stream nor claims that assignment succeeded.
If no earlier public callback occurred, the typed setup postfix verifies that its
actual State is the staged object and consumes the same handoff before clearing the
invocation context. Saved setup returning its Task is not initialization completion.
Failure or abandonment clears an unconsumed exact context; Task completion may clean
up that context but cannot produce a native Launch or process-exit witness.
A failing setup which never owns State creates no epoch; a prepared state that fails
later remains prepared and cannot acquire a Launch proof from timing or Task state.

This per-invocation/per-object context is not a global last-root or inferred run match.
Unknown/conflicting setup remains unknown or fails accounting; it cannot claim a new
native start. The existing exact Launch postfix supplies a separate `launch` boundary
with its original invocation and provenance after native RunStarted callbacks; those
earlier callbacks already belong to the prepared epoch and are never relabelled or
backfilled by that later witness. Exact existing setup/Launch/OnEnded/CleanUp patches
are reused; no duplicate native hook or causal tracker is introduced. Cleanup source
forwarding uses actual pre/post continuity before the legacy IsInProgress gate, since
terminal cleanup can occur after in-progress ended; legacy Human behavior is preserved.
An actual continuity change with no matching typed staged setup/cleanup witness fails
accounting before the old generation is discarded. Polling, status changes, elapsed
time and equality of unrelated callbacks are not witnesses.

Connector seals a source attachment before generation turnover, preserving only
its bounded selected reservations, original event metadata and payload pins.
Ordinary execution/old generation access becomes invalid immediately. The retiring
source view can still complete through its original seal; late encodes are accepted
only for the exact original reserved slot until its original encoder deadline.
Completed/missing/gapped positions stay in original order. No Current backfill.
The successor bootstrap is a fresh native occurrence; it is never an old gap repair.

## Declaration and native input tokens

Declarations keep the v1 exact fields/kinds and `machine_verifiable:false`.
`source-segments.jsonl` v2 contains `schema, sequence, session_id, timeline_id,
segment_id, previous_segment_id, declaration, boundary_position, recorded_at`.
An automatic epoch transition carries the existing declaration forward and does
not invent a new actor segment. Explicit ChangeSource remains Paused-only and
requires the exact prior segment and current native epoch boundary.

The recording owner freezes an immutable admitted token at the actual input
prefix, before capture encoding and native dispatch: exact store/session/timeline,
epoch, segment, original native pre-position and input ID. Token reservation is
bounded and performs no filesystem I/O. The disk writer cannot hold its short
metadata gate. Later basis registration/completion consumes that issued token;
it cannot read the current actor or lifecycle to relabel prior work. Catalogue
mapping remains Connector-owned; no coordinate/index operand construction.

`native-input-witnesses.jsonl` v2 fields are `schema, sequence, session_id,
timeline_id, input_id, epoch_id, segment_id, pre_position, pre_capture, catalog,
outcome, recorded_at`. References include original `epoch_id` and the unchanged
native capture/catalog metadata and content-addressed exact payload bytes.
Outcome retains v1 mapping/count/action/mechanism/delivery/reason and adds `stages`
(up to 16 native `{stage, delivery, evidence}` objects, fields at most 128 UTF-8
bytes). Mapping statuses remain exact/unmapped/ambiguous/capture_missing.
Delivery remains rejected_before_input/delivered/partially_delivered/unknown.
Unknown is never converted to success or retried. Input intent/delivery is not
execution, Commit, effects or a causal successor.

Exact input and complete observation require the actual captured full-reference
certificate, typed included domain shapes and catalogue operand membership.
Persistent may be explicit null under the nullable native contract; included
Interaction must be a real object. Scope omission can be stored explicitly partial
but cannot be promoted by an outer complete/exact label. Source v1 integrity rules
are reused without changing its old schema interpretation.

## Observation, pause and close accounting

Delayed observations, including encodes completing after epoch rollover and paused
ChangeSource, retain the original epoch and declaration segment selected at their
original native position. The disk worker never uses the current actor to classify
a prior reserved occurrence.

`public-observations.jsonl` v2 fields are `schema, sequence, session_id,
timeline_id, epoch_id, segment_id, position, source_seam, source_index, phase,
snapshot_id, owner_occurrence, game_continuity_id, completeness, capture, catalog,
missing_reason, gap_after_index`. Captures/catalogues are exact frozen original
UTF-8 bytes, not reserialized JSON. Full-array payload SHA and structural catalogue
digest are separately joined. Per-epoch source indexes/order and duplicate content
identity are checked; old epoch completion cannot become a current epoch event.

Pause stops new recording admissions, not gameplay or already admitted work.
`source-boundaries.jsonl` v2 contains `schema, sequence, session_id, timeline_id,
kind, segment_id, position, sealed_epochs, paused_intervals, transition, recorded_at`.
Kinds are pause/resume/epoch_transition/launch/terminal/close. Each paused interval is
`{epoch_id, stream_generation, after_index, through_index, reason:recording_paused}`.
A pause crossing rollover records one original interval per affected epoch.
Pre-pause admitted scopes and reservations may finish with their original token.
New occurrences in paused intervals are excluded explicitly, including a successor
bootstrap reserved during Pause. Resume does not read Current to fill that interval.
Close while Paused closes the final open interval in every touched epoch through
that epoch's exact original reserved boundary; leaving it open cannot yield a clean Close.

Close atomically disables new admissions and seals the current actual reserved
position, then drains all current/retiring seals and admitted tokens off-thread.
Original native encoder deadline (2 seconds by default) accounts missing projections;
unfinished input tokens become unknown. No native thread waits for encoding/disk.
Close remains Pending until exact barriers and durable streams finish. A bounded
3-second barrier grace beyond the latest original encoder deadline is failure,
not a clean close. Scope/generation/runtime mismatch, lost native witness, unsafe
queue loss or append/disk uncertainty marks accounting failed and withholds a
successful receipt/bundle. No automatic new child session/manual restart is used
to conceal an epoch gap.

The v2 close receipt freezes session/timeline, every epoch's original seal, final
position, per-stream counts/hashes, gap/input/epoch summary, source kinds and
accounting_complete/status. It also freezes `final_drains`: one row per epoch with
`epoch_id, stream_generation, sealed_reserved_through, completed_through,
durable_through, admitted_inputs_terminal`. The first value is the unchanged requested
seal boundary. The completion value is the actual final Hub watermark in that original
generation, not the seal's earlier completion watermark. Durable through is the writer's
acknowledgement of original positions, not a new native clock. Successful audit verifies
original-position rows/gaps/paused or explicit not-in-scope accounting through every
sealed reserved watermark and all admitted tokens terminal. An initial 12/10 seal,
aggregate row count or a later epoch's watermark cannot prove that drain. Success means durable accounting of the stated facts,
not zero-gap native qualification. Old callbacks are fenced to exact store,
attachment/session/timeline/epoch. Obsolete callbacks are ignored before packet
validation or failure marking; queued old work retains its original live epoch.

## Passive producer boundary and resource admission

Connector's public passive SourceRecording API exposes only atomic Attach, accepted
context, retained Events/Await replay, original ExportFrozen, exact native boundary
and input witness notifications, Renew, Seal/IsDrained/Release/Dispose. It exposes
no Submit, controller lease acquisition, native object, native dispatch delegate
or executable operand. Bounded typed observer/token-admission callbacks are passive
notification seams and cannot authorize input or create native source positions. Native Foundation/Game Mod composition owns witness emission; consumer
calls cannot claim a new native witness.

After all named typed native hooks register successfully, the single Service/Hub
owner installs the reviewed publication profile and actual coverage atomically.
Capabilities and source Attach read that same Hub declaration. This composition-only
registration seam is internal; passive consumers cannot claim native hook registration.
An already issued epoch never receives a retroactive coverage or definition upgrade.

One bounded encoder-to-recorder worker persists off the game thread. Pins are
acknowledged/released after append or explicit failure. Resource limits include
256 epochs/session, 2 retiring epochs, 32 queued metadata packets, 128 MiB copied
public payloads and 128 admitted input tokens. Existing per-capture/row/stream/
public-byte limits still apply and all retained/late encoder buffers remain charged
until actual disposal. Limits are provisional announced admission, not measured
non-interference. Saturation records a bounded original-position gap where possible;
if durable loss accounting cannot be written, the session fails instead of guessing.

An explicit new recording attachment registers its own new original client.
Automatic epoch turnover within that recording reuses the same client; it never
re-registers, revives a closed client or retries a failed attachment implicitly.

The native owner temporarily suppresses passive renewal only while sealing an
original epoch and establishing its exact successor on the same native turn.
It does not renew the old scope or claim that the successor is ready. Original
client revocation marks matching active, retiring or closing Source accounting
failed under the Hub metadata gate; observer callbacks and disk writes do not run
under that gate. A different client's revocation cannot poison the recorder.

Frozen public-copy admission reserves a conservative 64 times the original
capture/catalog byte size before copying, including validation and parser scratch;
input projection separately reserves 64 MiB scratch and at most 1 MiB encoded
Source basis. These charges share the 128 MiB copy budget. They are finite admission
limits, not a runtime memory measurement. Oversize or saturated captures produce
an explicit original-position missing record rather than allocating and measuring
afterward.

`SourceSessionAuditV2` and `SourceSessionBundlePackerV2` expose strict recording
audit and immutable export/packing. The Tool commands are `audit-source-v2`,
`export-source-v2`, and `pack-source-v2`. The Evidence package registers
`source-session-bundle-v2` and exports `SourceSessionBundleV2`,
`SourceSessionBundleV2Verifier`, and `verify_source_session_bundle_v2`; v1 and Human
descriptors retain their existing readers. Packing audits its actual copied raw
snapshot and exports those same copied bytes before content identity is sealed.
The Evidence CLI exposes `verify-source-bundle-v2` and accepts the same descriptor
for typed `receive --verify-type source-session-bundle-v2`.

## Contract fixture and next validation

`contracts/fixtures/source-session-v2.json` is synthetic contract conformance,
not a packer-emitted v2 bundle or actual native history. It fixes Title/prepared/Launch/run
positions, early new/saved setup callbacks and native RunStarted inside Launch, delayed
old-epoch observation/token source declarations, paused Close intervals, partial
promotion rejection, generation mismatch without a native witness, finite close
barriers and resource errors. The production Source worker and C# packer additionally
emit the synthetic bundle under Evidence `tests/fixtures/source_session_v2`.
The fixture uses the actual request namespace, original native dispatch and
terminal byte writer, preserving a Title input's original epoch and actor across
setup/Launch before close. The independent Python verifier checks the saved bytes
and rejects hash-repaired semantic corruptions. This is synthetic source/test
evidence; physical UI ingress and runtime qualification remain separate work.

Required gates cover actual Title→Launch→run→summary lifecycle, original epoch/actor
completion after rollover, repeated generation/queue saturation, paused rollover,
metadata/capture bytes, old v1/Human readability, source callback fencing and native
main-thread versus off-thread timing. L01–L64 source hook coverage and native
non-interference remain separately measured obligations; sampled seams stay sampled.

The portable Core cases live in
`tests/STS2HumanAnnotator.Core.Tests/SourceSessionV2Tests.cs`; run the existing
Core project with filter `FullyQualifiedName~SourceSessionV2Tests`. The separate
`tests/STS2HumanAnnotator.SourceNative.Tests` project references the real Connector
Host and links the production Source worker. Its synthetic native owner exercises
typed setup/Launch/terminal/cleanup callbacks and the actual protocol request
boundary; it does not launch STS2. Run it with `dotnet test -c Release` and the exact
local `STS2GameDir` property under the game's closed build gate. The optional
`STS2_SOURCE_V2_SYNTHETIC_GOLDEN` output is emitted only after the actual complete
producer/packer test passes; a prepared zero-input fixture has a separate
`STS2_SOURCE_V2_PREPARED_GOLDEN` output and cannot stand in for that input test.
