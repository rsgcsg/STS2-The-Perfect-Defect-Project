# Source recording v2: attachment epochs and real passive producer

Status: proposed wire freeze for the approved E2 producer work, 2026-10-08.
Implementation base is the normal dependency merge `f5a435892b6e90068e1070e20d163b67e7f69f0b`
of accepted root `fa414996f453db765884a54dc6e00f1fe880f828`.
Source v1 repair `a082862176c8b8df92d2b887d3bef3111be146a7` remains an accepted ancestor.
This document and its conformance fixture precede production changes. They are
not a live producer, native coverage, Human, causal, non-interference or G2/V1 receipt.

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

Profile v2 declares `input_profile:native-logical-v1`, canonical requested full
scope `persistent, interaction, referents, catalog`, limits and fixed non-claims.
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
context { scope_id, stream_generation, eager_scope, seam_coverage,
          environment, game_continuity_id },
starting_position, initial_position,
predecessor_seal or null, transition or null, recorded_at
```

The first epoch has no predecessor/transition. Atomic Attach returns native N and
reserves initial observation N+1 on the same native turn before callback interleave.
A successor epoch has an exact predecessor seal and a typed transition witness.
`NativeSeal` has `epoch_id`, `stream_generation`, `reserved_through`,
`completed_through`; the two indexes are original hub watermarks, and completion
may initially lag reservation. Its immutable requested boundary never advances
because a later Current read happened.

`NativeTransition` has `witness_id`, `kind`, `mechanism`,
`previous_game_continuity_id`, `game_continuity_id`, `start_provenance`,
`graceful`, `victory`. Nullable fields are explicit. Kinds are `launch`,
`terminal`, `cleanup`, `process_exit`; provenance is `new`, `saved`, `unknown`
for Launch and null otherwise. Only Launch/Cleanup that changes actual native
continuity creates an epoch. Terminal entry retains the current epoch and permits
required summary navigation. Exact existing Launch/setup/OnEnded/CleanUp patches
produce these facts. Unknown setup provenance cannot claim a new native start.
A run identity mismatch without an exact boundary fails accounting; polling,
status changes, elapsed time and equality of unrelated callbacks are not witnesses.

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
Kinds are pause/resume/epoch_transition/terminal/close. Each paused interval is
`{epoch_id, stream_generation, after_index, through_index, reason:recording_paused}`.
A pause crossing rollover records one original interval per affected epoch.
Pre-pause admitted scopes and reservations may finish with their original token.
New occurrences in paused intervals are excluded explicitly, including a successor
bootstrap reserved during Pause. Resume does not read Current to fill that interval.

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
accounting_complete/status. Success means durable accounting of the stated facts,
not zero-gap native qualification. Old callbacks are fenced to exact store,
attachment/session/timeline/epoch. Obsolete callbacks are ignored before packet
validation or failure marking; queued old work retains its original live epoch.

## Passive producer boundary and resource admission

Connector's public passive SourceRecording API exposes only atomic Attach, accepted
context, retained Events/Await replay, original ExportFrozen, exact native boundary
and input witness notifications, Renew, Seal/IsDrained/Release/Dispose. It exposes
no Submit, controller lease acquisition, native object, delegate or executable
operand. Native Foundation/Game Mod composition owns witness emission; consumer
calls cannot claim a new native witness.

One bounded encoder-to-recorder worker persists off the game thread. Pins are
acknowledged/released after append or explicit failure. Resource limits include
256 epochs/session, 2 retiring epochs, 32 queued metadata packets, 128 MiB copied
public payloads and 128 admitted input tokens. Existing per-capture/row/stream/
public-byte limits still apply and all retained/late encoder buffers remain charged
until actual disposal. Limits are provisional announced admission, not measured
non-interference. Saturation records a bounded original-position gap where possible;
if durable loss accounting cannot be written, the session fails instead of guessing.

## Contract fixture and next validation

`contracts/fixtures/source-session-v2.json` is synthetic contract conformance,
not a packer-emitted v2 bundle or actual native history. It fixes Title/Launch/run
positions, delayed old-epoch token/source declarations, pause intervals, partial
promotion rejection, generation mismatch without a native witness, finite close
barriers and resource errors. Production serializer/packer/verifier will later
emit and independently verify their own immutable synthetic bundle.

Required gates cover actual Title→Launch→run→summary lifecycle, original epoch/actor
completion after rollover, repeated generation/queue saturation, paused rollover,
metadata/capture bytes, old v1/Human readability, source callback fencing and native
main-thread versus off-thread timing. L01–L64 source hook coverage and native
non-interference remain separately measured obligations; sampled seams stay sampled.
