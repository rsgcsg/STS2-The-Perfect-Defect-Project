# Data Contract

The current recording schemas are defined by
`src/STS2HumanAnnotator.Core/CurrentContracts.cs`. Manifest and compatibility decision wire names remain `-2`. Canonical and
execution-action-space writers use `-3`; their readers also accept `-2`. A current
session directory contains one immutable manifest, a canonical transition stream,
an append-only invalidation stream, a semantic boundary stream,
content-addressed Read/frame/action-space objects, a minimal run journal, and
an atomically replaced coverage summary. Compatibility `run-*.jsonl` files are
optional and may be absent for canonical-only runs; the journal and semantic
streams retain run identity. The current store does not create a native-action
ledger.

## Human text-menu input observations

New recordings declare `text_input_schema_version: 1` and retain a separate
`human-text-inputs.jsonl` stream. Historical manifests without this declaration
keep their original interpretation. The declared file may be empty; a missing
file is not an empty capture. A durable close receipt binds its row count and
exact file digest. This side stream does not increase canonical transition or
compatibility decision counts.

The first supported input is `begin_card_play`. At the actual native
`NPlayerHand.StartCardPlay` entry, the recorder freezes Connector's independent
root text-menu observation, complete ordered menu and exact hand/card/holder
reference mapping. It does not navigate the Agent's menu cursor or deliver an
action. The factory-created `NCardPlay` is correlated only inside that exact
invocation. Acceptance additionally requires normal native return with the
same retained play carrier; factory creation alone is insufficient. External
controller activity excludes the attempt from Human recording.

`accepted_input` means the Human began that native input interaction. It does
not mean `PlayCardAction` committed, damage/block occurred, or a causal successor
was captured. Cancellation/rejection, unavailable capture and failed/ambiguous
mapping retain explicit negative dispositions rather than gaining a positive
label. The frozen observation and mapping are not replaced by later frames.
Opaque witness references bind evidence and are not model-visible strategy
features or executable operands.

Typed bundle verification preserves this stream independently from canonical
projection. The explicit owner's Human-origin attestation remains necessary;
machine validation cannot establish Human origin. Research may derive an
explicitly admitted input-choice view from accepted rows, without inventing a
Connector request, Receipt, native Commit or successor. Agent traces retain
their separate schema and origin. This initial slice does not claim Human
target/confirm/cancel, information-menu navigation or full-run text coverage;
in particular, mouse and controller card-play interactions need their own
native evidence before their later steps can be labeled.

Native-input correlation supports exactly `PlayCardAction`/`play` and
`UsePotionAction`/`use`, with matching native witness type and exact scoped
reference mapping. These inputs may lack a public BoundAction at H; the schema-3
canonical path independently requires their exact operands and exactly-once
membership in the execution catalog at S. Supporting the input format does not
make native acceptance, cancellation or missing successor into canonical proof.

Each compatibility `CurrentDecisionRecord` contains:

- exact environment and artifact identity;
- the full frozen pre Snapshot and catalog digest/count;
- native UI origin, accepted action type, and opaque native witness IDs;
- exact-unique reference-mapping evidence;
- the selected public BoundAction copied from the frozen catalog;
- a different complete interactive successor Snapshot;
- explicit eligibility gates and non-claims.

`audit` independently recomputes and verifies nested Snapshot identity, catalog
digest/count, chosen-action uniqueness, runtime continuity, sequence monotonicity,
and exact identities. Current `export` emits canonical transitions after a
closed-session audit. Explicit `export-compatibility` concatenates compatible
run files; it is not a complete Full-Run export.

Historical `native-action-ledger.jsonl` evidence uses
`sts2.human-annotator/native-action-ledger-event-2`. Each exact-correlated
accepted root has one process-local action witness ID, native queue ID, frozen
read-rich predecessor pre-frame, native witness, exact-unique mapping and selected
BoundAction. Later entries contain only ordered lifecycle facts and exactly one
recorder disposition: `strict_transition_admitted` or
`strict_transition_invalidated`; decision evidence is not repeated or rewritten.

`HistoricalRecordingAuditor` verifies that an admitted ledger decision exactly
matches its historical record. `native-action-ledger-event-1/2` sidecars remain
readable only through the explicit historical reader; the current recorder
neither mutates nor audits a native ledger, and the current bundle packer
excludes it. Historical Decision V1/V2 bytes retain their original meaning;
they are not a current admission, causality or successor authority.

`semantic-boundary-trace.jsonl` is the current Human causal evidence stream.
Historical schema-1/2/3 rows are handled only by explicit archival readers. New
schema-4 rows store ordered lifecycle/disposition facts plus explicit
`human_observation_ref`, `execution_pre_ref`, `successor_ref` and boundary-state
references. Each reference resolves below the session's
`semantic-frames/sha256/` directory to one exact canonical
`CurrentDecisionFrame`; audit verifies path containment, content digest and
snapshot identity before applying the same causal validator. Roles remain
distinct even when they reference identical content.

Every STS2-accepted Human gameplay decision has one durable auditable
occurrence/disposition: canonical, honest unknown, cancelled/rejected,
unsupported, or failed closed. This does not mean every pointer click is a
decision and it does not require `canonical == clicks`. When an exact accepted
source-local mutation cannot form a root, `invalidations.jsonl` carries an
immutable `human_occurrence` object with its action family/verb, exact native
subject and operands when available, owner, paused-parent lineage, mechanism
and `failed_closed` disposition. For current generated-card select/skip rows,
audit requires that object and validates the exact callback identity, native
owner, and, for select, card and holder identities; available paused-parent
lineage must be structurally complete. These optional additive fields keep the
current schema readable by current readers, while records emitted by current
bytes are held to that stronger audit requirement. It is evidence of an
occurrence, not a mutable
admission ledger, legality engine or second canonical truth.

An execution event may additionally reference one
`sts2.human-annotator/execution-semantic-action-space-3` object below
`semantic-action-spaces/sha256/`. It preserves the exact read-only Native
Foundation semantic state/catalog captured at the native action-binding
boundary, the described native action and its exact-once membership. For a
`GameAction` this boundary is `ActionExecutor.BeforeActionExecuted`; for a
source-local callback it is the callback's pre-admission seam. Schema 2 also
records the exact Human `BoundActionId` joined to the native selection, so
public and native verbs may differ without losing identity. Audit binds the
content digest, action witness, semantic state/catalog digests and typed
Human/native action identity. Schema 1 remains readable historical evidence
under its original same-verb matching rules. This object is evidence of an
STS2-owned semantic decision, not an Annotator legality engine or Connector
delivery catalog.

For native PlayCard accepted during public settling, the scoped input is
correlated by exact subject/operand references at `OnEnqueued`. The trace
retains `native_input` (action key, verb, subject, arguments and label) with
`bound_action = null` and `exact_native_input` mapping. This is an observation,
not a public delivery action or legality proof at H. State/Reads, environment,
modset and controller gates remain enforced. Failed matching stays fail-closed.
Schema-3 execution evidence joins `human_native_action_key` to the same exact
selected native key/operands at `BeforeActionExecuted`; the public-bound and
native-input bindings are mutually exclusive. Canonical schema 3 carries the
same XOR representation. A native-input canonical requires native execution
evidence and cannot use a public-catalog fallback. Cancellation retains the
input and disposition without creating a successful canonical transition.
The legacy compatibility decision stream intentionally omits native-input rows;
consumers must read the versioned canonical/trace contracts, not infer missing
input from legacy counts. Existing schema-2 consumers need an explicit schema-3
adapter update; Platform does not implement their training projection.

The timeline stores Human observation H separately from execution-adjacent
state evidence and records exact action identity,
accepted/started/choice/cancelled/finished facts, state/Read/catalog
completeness, boundary captures, and exactly one semantic disposition:
`transition_proved`, `transition_unknown`, cancelled before/after start, or
aborted before native Commit. A trace-level proved transition requires either a
complete interactive decision boundary or a state-complete capture
synchronously before the next tracked Human effect. The latter does not require
a republished action catalog, so it is not by itself canonical one-step
training eligibility. Native acceptance order is not assumed to equal execution
order; every trace-level pre-state must equal its own exact pre-execution boundary. Audit
rejects a mismatched execution pre-state or another Human action effect between
that action's start and successor boundary. A queued action cancelled before
`started` is retained as Human/native evidence
but is not a successful A. A cancellation after start remains unknown. Audit
rejects contradictory lifecycle/disposition combinations and duplicate
dispositions.

A `native_decision_owner_ready` boundary additionally carries typed domain,
exact process-local owner witness/type and native-mechanism evidence. Audit
requires that evidence and an exact domain match; the owner signal alone is not
state, action-space or successor authority. If the synchronous Connector frame
is partial or mismatched, no proof is emitted.

`native_continuation_observed` records the exact
`GameAction.BeforePausedForPlayerChoice` parent witness. A successful
continuation is a typed parent Commit seam for opening the immediate nested
Human choice; it is not `GameAction.Finished`, a child action, or `S'`. The
parent's later ready/resume/finish callbacks retain their own lifecycle
meaning. A canonical parent row may therefore name a native terminal/direct
Commit **or** this exact PlayerChoice continuation, but still requires a
separate causal successor and no intervening Human effect.

`native_human_continuation_observed` is distinct: it preserves one exact
screen-owned accepted selection or cancellation inside an already-owned Human
root. An exact parent/root logical invocation scope binds the typed selector
factory, and the resulting native screen object is weak-keyed until its own
terminal callback and completion source agree. The event carries its exact
parent action witness, owner, mechanism and selected native operands. It does
not create another root, count as native Commit, settle the parent, or prove
`S'`. Preview cancellation and an unowned screen exit are not promoted to an
accepted occurrence.

This stream is not corpus admission or research authority. Current audit only
promotes current schema containers; predecessor sessions retain their original
claims only through an explicit archival reader. Evidence is never transferred
or backfilled.

`native-semantic-discriminator.jsonl` is an additive read-only diagnostic
stream. For each exact-correlated root it records ordered native lifecycle and
projections of the public UI catalog and Native Foundation semantic decision.
At the execution boundary it consumes the same immutable capture preserved by
schema 4 rather than recapturing or acting as a second semantic authority.
Lifecycle rows may be `not_sampled`. A `successful_capture_delegated` detail
means the canonical semantic-boundary stream durably owns that sample; it is not
a missing capture and cannot authorize an action. Successful sampled roots must
match exactly once; cancellation and PlayCard pre-Commit abort are separate
dispositions. Player-choice commits are linked to the paused parent action.
The stream is audited by `audit-native-semantic`; it does not authorize input,
change the current Decision record, admit training rows, prove End Turn
completion by itself, or claim Full-Run semantic completeness.

The authoritative `audit` consumes this stream only for envelope/session
integrity and cross-stream accounting. Per-action diagnostic coverage or
membership findings (for example `not_applicable` native catalog coverage) are
retained in the `audit-native-semantic` report but do not invalidate an
otherwise valid semantic/canonical session. Malformed JSON, schema/sequence
errors, identity mismatches, and orphan cross-stream identities remain fatal.

`canonical-transitions.jsonl` schema 3 is the non-authorizing current canonical
projection and the sole durable canonical truth. A row is written only after
one complete semantic state, exact-once selected action in the authoritative
native action space, exact Human/native
correlation, exact native terminal/direct Commit or PlayerChoice continuation,
no intervening Human effect and one
complete causal successor are all present. It references immutable semantic
frame and typed semantic action-space objects. The action-space object records
whether it was captured before `GameAction` execution or before a source-local
native callback admission, plus exactly one correlation form: a Human BoundAction
binding or an exact `native_input` key. Historical
or not-yet-migrated direct UI evidence may name `public_bound_actions` only when
its exact frame has the complete typed public catalog and contains the action
exactly once. Audit
verifies hashes, identities, the unique tracker proof and action membership.
Schema-1 serialized-input rows remain readable historical evidence.

Canonical sequential training evidence has the stricter contract:

```text
S_t + complete A(S_t) -> exact A_t in A(S_t) -> causal S_(t+1)
```

H and its exact public BoundAction or native-input correlation prove Human
choice/correlation but do not define S. Acceptance does not define execution order. Execution S is eligible
only when its same-boundary authoritative action space contains A exactly once;
current public deliverability is not substituted for that semantic fact. A
generic later interactive Snapshot is not causal S' merely because it is
interactive. `calibrate-semantic-training` mechanically classifies semantic
candidates and joins them to the durable canonical stream. It never creates
canonical truth or promotes a historical admission or a `transition_proved`
label by terminology alone.

`SemanticActionReference` may add exact process-local witness, mapping,
BoundAction and native-mechanism metadata. Missing metadata on historical rows
retains its prior meaning. Public-bound-action correlation resolves an
already-published frozen BoundAction; the `native_input` form described above
retains exact native operands without inventing that public correlation. Both
converge on the same tracker and disposition rules and require execution
S + A(S); neither is a second execution API.

`pack-session` creates `sts2.human-annotator/session-bundle-3`. A current bundle contains
the untouched raw session, producer audit, deterministic canonical export, the exact
versioned `HumanCaptureProfile`, a human-origin attestation, a content-identity
manifest, and a complete `checksums.sha256` inventory. The content identity binds
session, worker, campaign, profile, run IDs, raw files, export and audit. Existing
bundles are immutable: an exact retry reuses identical bytes and any changed
retry fails.

Files are append-only by Recorder behavior, not cryptographically tamper-proof.
The SHA-256 of an exported JSONL is one source identity, not the whole bundle
identity. Evidence independently verifies bundle-3 inventory, typed canonical
evidence and raw/export equivalence; it does not grant research admission.
`pack-session-compatibility` explicitly produces bundle 2 with record-2 export.
Existing STPD record-1/2 consumers require a separately versioned canonical-3
adapter and their own admission checks before current Full-Run evidence enters a
corpus. Predecessor bundle contracts retain their archival meanings. Preserve
both raw sessions and accepted bundles read-only.

## Decision identity extension

[ADR-0006](../../../docs/adr/0006-decision-occurrences-within-causal-roots.md)
distinguishes `CausalRoot` from `DecisionOccurrence`. New runtime manifests set
`decision_schema_version: 2`. Every semantic trace action and canonical row
carries `decision` with `schema_version`, `decision_id`, `causal_root_id`,
`parent_decision_id`, `surface`, `family`, `decision_kind` and
`native_owner_witness_id`. Root owners may be null; nested owners must be exact.
Audit checks earlier parent acceptance in the same session/timeline/run,
immutable identity, canonical-to-trace equality and required metadata presence.

Nested decisions retain their own frozen execution pre, complete Connector
catalog, chosen BoundAction and successor or explicit unknown in the existing
streams. A null native queue ID is intentional: this is a native UI decision,
not an invented GameAction. Selected cards and available pile provenance remain
in the content-addressed Snapshot/Read evidence; opaque witnesses never grant
access to native objects. Cancel/preview/deselect inputs are retained separately.
Historical continuation-only records have no independent selector S/catalog
claim and must not be upgraded by a consumer.

## Recording application decision projection

The additive `RecordingCounters.Decisions` snapshot counts accepted roots and
children, proved/unresolved dispositions and canonical roots/children only after
the corresponding authoritative append succeeds. `Records` retains its older
compatibility-record meaning. Recorded-family scope and LastRecord follow the
canonical stream. Application action metadata optionally copies decision identity,
pre/successor IDs, catalog count and recorded pile type; old producers omit it.
These fields do not authorize actions or confer research qualification.

An exact selector input-owner handoff may close a started direct-UI decision
before its enclosing native Task returns. The durable continuation names that
exact owner and lineage; no GameAction pause or completion is fabricated. Queued
GameActions still require their actual pause/finished lifecycle. Current canonical
family filtering consumes the family already attached at decision admission,
rather than interpreting the public verb a second time.

## Native launch provenance

`run_started_native` requires both the exact RunState's new-singleplayer setup
and its native Launch. Saved-singleplayer setup produces `run_resumed_native`;
a Launch without exact setup provenance produces
`run_launched_native_origin_unknown`. Neither proves a fresh complete run.
A prior polling `run_observed_in_progress` does not suppress a later native
marker. Historical sessions that recorded every Launch as `run_started_native`
retain their bytes and require source-version-aware qualification; start/end
counts alone never admit a Full Run.

## Decision identity version 2

New manifests declare `decision_schema_version=2`; audit supports historical 1
but requires each explicit decision to match its manifest. `native_selector`
adds an independently witnessed Human input with no Human parent and an exact
real native action or exact blocking choice context as its causal root. `native_origin` carries
`native_action_witness_id`, `native_action_type`, `choice_context_type` and
`factory_mechanism`. The native input witness repeats the exact origin and
selector-owner identities for typed audit. It is a `direct_ui_commit` decision,
never a fabricated GameAction or queue entry. Existing root/nested selector
identities remain valid in version 2. No-origin factories remain fail-closed.

For `BlockingPlayerChoiceContext`, the historical `native_action_witness_id`
and `native_action_type` fields identify the exact native context object and its
actual CLR type, not a GameAction. `CardSelectCmd.FromChooseACardScreen` carries
this context to its screen factory through the exact async invocation. An
existing exact Event/Reward parent takes precedence; a standalone native choice
has no invented Human parent. Throwing or mismatched contexts remain fail-closed.

The UI's roots/entries counters count parentless Human decisions, including
native-origin selector inputs; they do not deduplicate by CausalRootId. Every
input remains separately represented. Native-origin decisions are eligible for
the existing nested-selector capture family only after ordinary canonical
state/action-space/successor validation. Research admission remains external.

The current Full-Run profile is `human-full-run-read-rich-v4`, adding
`potion_belt.discard` for the native potion popup across rooms. Historical v3
profiles retain their exact stored meaning. Native-origin decisions can name an
exact PlayCardAction in GatheringPlayerChoice when no Human parent was admitted;
this does not assert automatic origin. `unrecorded_human_effect_before_successor`
means an accepted but uncaptured Human input fenced a pending transition. No
later frame may be projected as that transition's causal successor.

## Queued execution catalog phase

A `game_action` or an action with `native_queue_id` requires its execution
semantic action space to have phase `before_execution`. Admission-time native
catalogs remain useful H evidence but cannot qualify a queued execution S.
`before_native_action_admission` is valid for direct callbacks without queued
carriers. Current audit/calibration reject historical rows that violate this
existing causal boundary; they do not rewrite the historical files or recover
missing H admission evidence.

## Current writer, compatibility readers and dispositions

Current writers emit canonical-transition-evidence-3 and
execution-semantic-action-space-3. Schema2 remains readable for concrete
predecessor data; schema1 is archive-only. Internal normalized trace2 is a
validator representation, not another production append authority. Current
bundle3 exports canonical rows; record2/bundle2 are explicit compatibility.

Recording status5/event batch2 separate Pending, Recorded, Unresolved,
Cancelled, Aborted, Failed closed, Diagnostic and Unsupported. Session totals
come from successfully appended authoritative facts, not retained UI rows.
`RealFailures` counts unique in-scope failed decision witness IDs: trace unknown,
accepted capture loss or canonical persistence loss. It excludes native cancel,
abort before Commit, presentation cancel, internal diagnostics and unsupported
non-decisions. Interrupted or failed evidence accounting returns unavailable,
never a fabricated zero. Repeated diagnostics do not create decisions.

New producer manifests declare `disposition_schema_version=1`. Invalidation2
then requires explicit `disposition`; `failed_closed` requires `decision_failure`
with exact decision witness, capture/persistence kind and action family. Capture
failure names the same immutable HumanOccurrence; persistence failure names the
actual admitted action. Old manifests without this extension retain unknown
classification, not inferred all-valid accounting. UI projection cannot author
these dispositions.

New manifests also declare `close_schema_version=1`. `session_closed` in the
journal is a close request's terminal log record; completed closure additionally
requires matching `session-close-receipt.json` (`session-close-1`). The receipt
is published only after evidence stream flush/close and flushed receipt bytes.
Close failure keeps accounting unavailable and does not publish application
Closed. This is exception/durability evidence, not a power-loss atomicity claim.

Compatibility adapter failure after canonical append is diagnostic and cannot
undo canonical success or strand the decision presentation. A damaged current
stream still fails audit. Structural validity and a deliverable bundle do not
assert Human origin, full coverage or research admission.


## Continuous recording and interrupted copies (2026-09-17)

Status 5 adds `continuous`: armed, observed fresh/resumed start, interruption,
terminal/outcome, native boundary completeness and sealed/finished counts. These
are lifecycle facts, not complete Human or sequence qualification. Start/Resume
arms; Pause interrupts and disarms; manual Close disarms even between sessions.
A native terminal seals the current session without disarming. The next exact
Launch opens the next session. `IsAbandoned` at OnEnded distinguishes abandonment
from defeat. Cleanup/exit seals a partial segment without inventing a terminal.
Packing and upload remain asynchronous Workbench/Evidence operations.

`continuous_schema_version=1` permits explicitly empty current streams when a
recording was armed and stopped without any Human decisions. It does not permit
missing canonical/projection rows or unknown schema versions.

`recovery_schema_version=1` declares a process-owned exclusive `recording-owner.lock`.
The collection tool's `recover-interrupted` runs only after that lease is available.
It preserves the original directory and builds a separate staged copy. A receipt
`sts2.human-annotator/interrupted-recovery-1` binds every original relative file's
byte length and SHA-256, their canonical inventory digest, and an interrupted
partial disposition. Only missing accepted-action terminals may be appended, as
`transition_unknown` / `process_interrupted`; no successor or native completion
is invented. The journal gains `recording_interrupted` and one final close.
The normal auditor and independent Evidence verifier check original byte prefixes,
unchanged other files, exact inventory and unknown-only suffix. Only a passing
copy is atomically published for delivery. Torn/corrupt streams remain incidents.
A live lease, duplicate recovery or historical session without this ownership
contract is never automatically repaired. Session-local run IDs remain unchanged.

Recovery is not a native event or full-run proof. Existing immutable canonical
rows remain available for separately admitted decision views; interrupted
recordings cannot qualify as uninterrupted sequence evidence. A completed delivery
generation includes both the verified recovered copy and unchanged original bytes.
