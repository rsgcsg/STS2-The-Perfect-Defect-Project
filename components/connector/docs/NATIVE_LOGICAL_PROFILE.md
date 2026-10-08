# Native logical profile and publication contract

Status: implementation contract candidate for E1.2/E2/E3, 2026-10-08.
No complete native, capture or V1 qualification is claimed by this document.
The root [specification](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/develop/docs/BASELINE_V1_SPEC.zh-CN.md) and
[acceptance matrix](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/develop/docs/plans/BASELINE_G2_V1_EXECUTION_2026-10-08.md)
retain the required scope and user approval boundary.

## Ownership and compatibility

`native-logical-v1` is a new Player Environment input profile. It exposes current
native semantic leaves, including information and held-card operations, without
the text-menu presentation-group cursor or text-v2 virtual card/target staging.
Reuse `NativeTextMenuFrame`/typed NativeUi facts and exact closures; do not derive
another legality engine or create native operands from client expressions.
Legacy snapshot/text-v1/text-v2/S0 retain their original schema and behavior.

One Connector owner captures, binds, publishes and resolves. REST/MCP/SDK only
transport and validate. Native references/dispatch callbacks stay process-local;
sealed stores contain public immutable values/bytes only. The existing single
controller, request fingerprint namespace and execute-time checks remain shared.
Refactor a shared admission/delivery primitive if required rather than copying
slightly different authorization/unknown behavior into another executor.

## Wire objects

All JSON uses snake_case. Profile-specific schemas are versioned under
`sts2.player-environment/native-logical-*-1`; the existing Player Environment
transport version remains explicit and separate from the profile version.

| Object | Required fields / meaning |
| --- | --- |
| Capabilities | schema/profile, exact host/game/session, supported methods and implemented mechanism/capture coverage, source-clock definition, limits, control policy; implementation is not runtime qualification |
| Observation | schema/profile, snapshot_id, revision, observed_at, status, persistent, interaction, referents, completeness, session, information_policy, owner_occurrence, catalog descriptor |
| Observation context | observation/capture reference, actual game_continuity_id or unknown, stream_generation, publication cursor when published; scheduling identity is not Model input |
| Catalog descriptor | catalog_ref, snapshot_id, status, total_count or explicit unknown, digest, ordering_semantics, access methods; no guessed complete count |
| Action | action_id, kind=native_input, verb, label, subject_referent_id or null, ordered role/referent arguments, effect_domain; distinct public subjects/roles express actual differences |
| Capture envelope | capture_id, snapshot_id, profile, session/generation, capture ordinal/time, byte count/hash, expiry and opaque read cursor |
| Public event | cursor, stream_generation, publication_index, kind, source_seam/phase, capture_ref or explicit missing, coverage/gap, public payload reference; source position is not causal proof |
| Event batch | ordered events, next cursor, high watermark, retained-start cursor, gap or null; repeated retrieval preserves original event identity |
| Result | request_id, profile, exact selected action/basis, input delivery disposition, effect knowledge separately, reason/retry rule, observed frame reference where available, attribution without secrets |

Observation fields reuse the existing public page/referent/content contracts;
they do not embed old `bound_actions` as a competing action authority. The new
catalog contains all current native leaves across groups. Native order is retained
where public and meaningful; internal lookup order is not a learned ordinal.
Opaque identities bind objects but are not feature embeddings.

Snapshot identity includes stream/game identity, owner occurrence, binding revision,
public facts and the complete native leaf relation. A real observed A→B→A owner
transition gets a new binding revision even when A's text repeats. ReadCurrent
repetition without a source change does not manufacture a new semantic occurrence.
New generations never accept old action handles. A failed required capture creates
explicit missing/gap accounting, not an unobservable hole in the source clock.

Public referent IDs are opaque cryptographically random aliases, independent of
native enumeration or hidden values. They remain stable for the same live native
object in one runtime; object replacement or runtime restart creates a new alias.
An alias is exposed only together with authorized public content. Cross-view
continuity may identify an already publicly known card, but never reveal the
ordering of a hidden pile. Neither alias allocation nor a public sort tie-breaker
may encode private enumeration. Exact native references remain private. Historical
recordings retain their original IDs and declared producer, including old defects.
Held-input pages include the actual public focus referent (or null), selection
state and focus occurrence; equal preview text does not erase a focus change.

## API and access

Use a dedicated `/api/player-environment/native-logical/` route family and matching
SDK methods; MCP must preserve the same semantics. Startup/menu/process management
does not become ordinary gameplay authority. Native terminal/summary navigation
remains in the task until its separate task-complete boundary.

| Method | Request | Reply / behavior |
| --- | --- | --- |
| capabilities | no mutation | typed capabilities/limits/implemented coverage |
| attach | client session, requested eager scope, required seam coverage, delivery mode | immutable accepted subscription scope or explicit unsupported/capacity; no implicit fallback |
| renew | client session, subscription_id, scope_id, after cursor | extend only an active subscription resource lease; preserve scope, generation, history and event cursors |
| current | client_session_id, eager_scope, nullable expected_snapshot_id | fresh actual coherent capture or stale/partial/capacity; never pretend it is an earlier notice |
| read | capture_id, opaque cursor, max_bytes | immutable same-capture bytes/chunks and digest; no game access |
| catalog | catalog_ref, cursor, limit, optional structural prefix | ordered matching actions, next cursor, full-relation and filtered counts/digests; prefix is not strategy |
| resolve | catalog_ref and complete public structural expression | unique original action handle; no-match/ambiguous/expired explicit |
| submit | existing exact action request with new input_profile | authorize and fresh native revalidation, then existing exact dispatch; unknown never auto-retries |
| result | original request_id | exact retained request outcome, never a newly submitted attempt |
| events | subscription_id, scope_id, after cursor, bounded event/page count | immutable ordered subscription projection or explicit retention/generation gap |
| await | wait_id, subscription_id, scope_id, after cursor, known public condition, bounded timeout, optional control_binding | inspect retained projection then atomically register; finite outcomes defined below |
| cancel_wait | client session, subscription_id, wait_id | cancel that client's waiter; idempotent, no native effect |
| detach | client session, subscription_id | end that subscription, cancel its waiters and release its pins |
| retain | client session, capture_id | own reader handle plus fresh read cursor over still-retained immutable bytes |
| release | client session, retention_handle_id | idempotent own-handle release, no effect on controller or delivered actions |

The Attach request has exactly `client_session_id`, `eager_scope`,
`required_seams`, `delivery_mode`. `eager_scope` is an ordered, duplicate-free
subset of `persistent`, `interaction`, `referents`, `catalog` in that canonical
order. `required_seams` is a duplicate-free list of advertised seam identifiers;
each requested entry binds an exact advertised coverage version. Delivery mode
is `full_reference` or `scoped`. Full-reference requires all four eager fields
and `complete_at_seam` coverage for every required seam. Scoped mode permits
explicit omissions, which remain listed in every capture's completeness value.
The response carries `subscription_id`, `scope_id`, accepted scope and coverage,
stream generation, starting cursor and expiry. Rejected attachment creates no
subscription and returns `unsupported_scope`, `unsupported_seam`,
`coverage_insufficient` or `capacity_exceeded`. Changing scope requires a new
attachment; it cannot retroactively change retained events. A passive observer
needs no controller lease. Detach cancels only its own waiters and pins.

Renew requires an active, client-bound subscription and its exact scope/cursor.
It extends the subscription resource lease by the announced retention duration;
it never revives an expired or detached subscription, changes scope/generation,
resets publication position or wait-ID history, renews a controller, or extends
capture/reader/event pin leases. A retention gap remains a gap after renewal.
Event cursors are immutable signed positions independent of that resource lease;
each request still checks the live subscription, generation and retained-start
watermark. Re-querying an original event after renewal preserves its original
content and cursor. Renewal does not extend an already registered wait deadline.

One source occurrence owns one reserved publication index. Each accepted scope
gets an immutable projection of that occurrence containing its own capture
reference or explicit missing outcome. Event identity remains generation/index;
projection identity additionally includes `scope_id`. A repeated query of the
same projection cannot change its payload. Cursors bind client session,
subscription, scope, generation and index. Passing a cursor to another scope or
subscription fails even if the numeric index matches. Attach takes effect under
the source hub lock and returns a starting cursor at the last **reserved** index,
not the last encoded index: it subscribes only to subsequent occurrences. It
does not promise pre-attach history. Identical scope captures may share immutable
storage without sharing release authority. Every subscription checks its own
accepted seam/scope obligations; another subscriber's weaker scope cannot reduce
them. Frozen bytes from different occurrences are never combined into one input.

Production attachment also establishes an ordered initial observation. The native
bridge executes registration and `AttachWithInitialReservation` on one asserted
game-main-thread turn: the subscription starts after reserved index N and its
initial observation occupies N+1, targeted to that new subscription. It freezes
the actual requested public scope before another native callback can interleave.
Encoding remains asynchronous and never holds the game thread or hub lock while
waiting. Failure at that position is an explicit missing outcome. An attached
subscription is not ready for full-reference inference until that initial capture
is complete and validated. Bootstrap capture is advertised as its own source
seam; it does not establish completeness of other native exposure seams.

Consumers bootstrap through Events from the returned starting cursor, including
the initial observation in publication order. A separate Current read must not
be consumed ahead of earlier retained publications. Bootstrap neither promises
pre-attach history nor invents a semantic change: an unchanged source retains
its snapshot/owner occurrence. Recorder composition attaches first, initializes
its durable source context, then activates delivery with retained replay from
the attachment cursor; no callback-before-store race may discard the initial
capture. Closing a passive subscriber must account for already reserved source
positions through its close barrier, with explicit finite-time gaps on failure.

Current accepts the same scope grammar, returning its scope ID. A source notice
with `capture_ref:null` and `missing_reason:not_eager` cannot satisfy a promised
full-reference position. A later Current call is a new current capture. It can
never fill the notice's missing historical values. Capability availability,
complete requested scope and complete full-reference input are separate fields.
Native-seam coverage is one of `complete_at_seam`, `sampled`, `unsupported`;
capture completeness is `complete`, `partial`, `capacity_exceeded`, `failed`.

The native producer supplies a required `SourceCompleteness` certificate with
status `complete` and an empty missing list for the first pure core. Freeze and
Current reject any other source certificate as `source_capture_incomplete`
before creating a capture or relation. The core does not infer native/game
completeness. Requested scope omissions create a partial view of a complete
source; requesting all four fields cannot turn a partial native list or missing
source field into complete input. An explicitly complete source with zero
native actions is valid for settling or terminal observations.

Current uses `native-logical-current-1`; Context uses `native-logical-context-1`,
under the same schema prefix and profile. A `captured` Current reply requires its
coherent Context and Capture references and a null reason; an optional retention
reference must point to that original capture. A `partial` Current reply is an
actual captured view with requested scope omissions: it requires joined Context
and Capture references and `reason:scope_omission`, with the same optional
retention join. The other statuses (`stale`, `capacity_exceeded`,
`source_capture_incomplete`, `failed`) require null Context, Capture and
Retention references and an explicit reason. A partial native source is a
capture failure, never a partial successful Current view. Failures remain explicit, without
substituting an older view. One-shot Current allocates its own immutable scope ID.
Context's `game_continuity_id` is the actual native value or null, never a scheduling
token. Retain returns the original immutable capture plus a fresh reader cursor
and finite reader expiry. If the original capture cursor expires while another
valid pin keeps the payload, read through that fresh retention cursor; never
rewrite the original envelope. Release rejects a live foreign handle and is
idempotent for an owned or absent handle. Renew, Retain and Release use their
own `native-logical-{renew,retain,release}-1` reply schemas. Cancel Wait and
Detach use `native-logical-cancel-wait-1` and `native-logical-detach-1`; their
booleans preserve the actual owned operation. A nonpending wait is not reported
as cancelled, absent detach is idempotently false, and a live foreign
subscription cannot be detached. Result `retry` is exactly `never_automatic`
for every disposition; a separately chosen fresh decision is not a retry.

Only submit performs a gameplay/native interaction. Opening a deck, changing target
focus or selecting a card is an action, not a hidden side effect of read/resolve.
Structural prefixes match only registered public fields (`verb`, subject and named
arguments); unknown fields are invalid, duplicate roles are invalid, and a match
never creates a native object. An action ID can identify an otherwise public-equivalent
member, but a missing meaningful public distinction remains a coverage defect.

A structural prefix is an object containing any subset of `verb`,
`subject_referent_id`, `arguments`. An absent subject matches every subject;
explicit null matches only a subjectless member. Arguments are an ordered array
of `{role, referent_id}` pairs and match the leading argument sequence exactly.
An empty array matches every member. Unknown fields, non-string values except
nullable subject and duplicate roles are invalid. Argument order is significant
and follows the actual retained member; a different order does not match. There
is no client-side sorting or inferred per-verb role registry.
Resolve requires exactly all three fields and an exact argument sequence; it
does not accept a prefix, label, candidate index or executable expression.
It returns `unique` with the original action, `no_match`, `ambiguous`,
`expired`, `generation_mismatch` or `invalid_expression`; equivalent members
remain ambiguous rather than being selected by storage order.

The authoritative first implementation materializes a finite full catalog. List
and prefix queries are access paths over it; neither a 512-item projection nor a
shortlist can be called complete. An over-budget build returns an honest capacity
failure. A later factorized representation requires its own completeness proof and
contract revision, not a changed count field.

## Digests and resource limits

Catalog integrity is SHA-256, represented by exactly 64 lowercase hexadecimal
characters, over the following bytes in order. The prefix is exact UTF-8
`sts2.native-logical.catalog.v1` followed by one zero byte. Append action count
as U32BE. For each ordered action append string fields `action_id`, `kind`,
`verb`, `label`; nullable `subject_referent_id`; argument count as U32BE;
each ordered argument's `role`, `referent_id`; finally `effect_domain`.
Every string is U32BE UTF-8 byte length then its bytes. Nullable subject is byte
0 for null, otherwise byte 1 followed by the normal encoded string. Strings must
contain only Unicode scalar values; reject unpaired surrogates. No Unicode
normalization, trimming, locale folding or platform newline conversion occurs.
Empty strings and null are different. Duplicate action IDs/argument roles,
unknown fields and counts/lengths outside U32 are rejected before hashing.

Identity construction is acyclic. First freeze public facts and native leaves
without snapshot-bound action IDs. Assign a fresh opaque `snapshot_id` when the
public facts, owner occurrence or private binding revision changes; an unchanged
current capture reuses it. An ID is identity, not a content-hash claim. Then
derive distinct opaque action handles bound to that snapshot and calculate the
catalog digest above. `catalog_ref` is a fresh immutable opaque relation handle;
its descriptor binds snapshot, digest, count, scope and generation. Capture
payload SHA-256 covers the exact emitted UTF-8 bytes, so readers verify bytes
without reconstructing a JSON serializer. Shared multilingual/null/empty and
duplicate-role fixtures must agree in C#, TypeScript and Python.

Repeated capture of the same unchanged snapshot/relation reuses the exact ordered
action handles and therefore the same catalog digest, even when a new capture or
catalog reference is allocated. Freshly reading a catalog cannot itself invalidate
a current action. Only a changed source/binding fact or controller admission can.

Initial candidate resource profile: at most 65,536 current actions, 64 MiB per
capture, 512 MiB retained public bytes, 2,048 events, 120 s retention, 1 MiB maximum
read chunk, and 30 s maximum server wait. These are declared engineering limits,
not measured performance qualification. Pins, streams and captures share accounted
bytes; no double-counted or unlimited hidden retention. Control/Stop cancellation
does not wait behind network serialization or an unbounded capture loop.

Read `max_bytes` means decoded capture-payload bytes, at most 1 MiB. The transport
response including Base64/JSON envelope has a separate 2 MiB encoded limit. Catalog
pages have a 1 MiB encoded UTF-8 response limit including their envelope; the
server accounts encoding overhead before adding a member. Before advertising a
complete accessible catalog, validate that every individual member fits one page
with the largest permitted envelope. Otherwise capture returns capacity failure.
A caller-selected smaller page budget can return `page_budget_too_small` and
`minimum_required_bytes`, without consuming or skipping that member. The complete
relation remains retrievable with the advertised maximum. Field and cursor length
limits are part of the fixed wire schema, not arbitrary serializer allocations.

Additional admission limits per runtime are 64 subscriptions, 128 waiters,
256 unstarted main-thread jobs, 256 live captures and 1,024 explicit retention
handles. Per client limits are 4 subscriptions, 4 waiters and 64 retention handles.
Count and byte admission are atomic. In-flight encodings count against bytes;
capacity rejection creates an accounted missing source event if a source position
already existed. Event metadata uses a preallocated bounded ring and retained-start
watermark: saturation advances the watermark and reports the lost interval as a
gap without allocating a new payload or an unbounded list of gaps. Per-scope
missing placeholders have fixed bounded fields. Runtime generation/watermarks
remain available even at full payload capacity. Pending/expired encodings retain
their byte charge until their actual frozen buffers are released; a timeout alone
does not replenish memory while a late encoder still owns those buffers. These
are configurable announced limits, not permissions to truncate a catalog.

Retention belongs to immutable objects with shared references: subscription,
event, recorder and explicit reader handles can independently retain one object.
Release removes only the calling client's handle. It never invalidates another
live handle or rewrites an event. Leases and maximum byte/time limits remain
finite; expiry preserves the original immutable event/projection and capture
identity/hash. Retrieval reports `payload_expired` in a separate current
availability envelope or emits a new gap event; it never rewrites the original
event's payload. Catalog reads resolve retained values
only; a cursor binds generation, catalog, normalized filter, position and expiry
and cannot be reused across any of those boundaries. A catalog expires together
with its capture unless another valid shared handle retains both. No live native
getter or dispatch runs during read/list/resolve.

Requests share one cross-profile idempotency registry. Its admission lifetime is
the client session: retain request fingerprints and terminal outcomes until that
session expires. An announced 65,536-request session ceiling rejects new IDs once
full rather than evicting IDs and risking replay. Session expiry/revocation makes
all submissions using that session permanently invalid; old request outcome may
become `result_expired`, never `not_submitted`. Retained result lookup cannot
authorize a retry. New client session creation does not revive an old request.

## Admission, Stop and delivery outcomes

The shared controller owner linearizes a submission start against Stop/release
under its existing authority lock. A fresh capture and exact binding check happen
before this final start check. Queued work carries requesting client, lease and
generation; it cannot acquire a replacement lease. Stop atomically revokes new
start permission and cancels matching unstarted jobs. Work that wins the start
boundary may finish; Stop never relabels it undelivered or blocks while waiting
for its native effect. Main-thread jobs check drain budget before dequeue,
release capacity on cancellation and never drop a task without completing it.

Result `delivery` is exactly `not_started`, `rejected_before_input`, `delivered`,
`partially_delivered` or `unknown`. `execution` is separately `not_started`,
`native_accepted`, `native_rejected` or `unknown`; `effect` is `not_observed`,
`pending`, `observed` or `unknown`. An observed immediate frame carries no causal
settlement claim. `cancel` is `not_requested`, `cancelled_before_start`,
`too_late` or `unknown`. Partial compound dispatch cannot report
`rejected_before_input`: after an actual native hover/focus stage changes, a
later confirmation rejection is `partially_delivered` with exact known stages.
If it is unclear which stage ran, report `unknown`. No automatic retry follows
partial delivery, unknown delivery, or an expired result. Requests rejected before
input can be followed only by a separately chosen action on a new observation.

Result `stages` is a required ordered array of at most 16 public
`{stage, delivery, evidence}` records, including an empty array when no exact
stage fact is known. Stage and evidence are Unicode scalar strings of at most
128 UTF-8 bytes; delivery uses the same finite disposition values above. Retain
already-known compound input facts without inventing stages. These records do
not prove native execution or causal settlement. In particular, a legacy
`Accepted` value alone cannot produce `execution:native_accepted`; execution
remains `unknown` without an exact native witness.

Current normal 200/500 and 10,000 pressure cases must measure actual membership,
capture, encoding, transfer, query/resolve and peak retention. A concrete Model's
input limit is separate. Default full-reference acquisition must assemble one
coherent actual capture; partial/building/inconsistent is not full success.

## Publication and capture

Publication positions are source facts owned by Connector. Required typed triggers
cover owner enter/leave/change, input delivery boundaries, focus/unfocus and preview
changes, selection/child-ready, meaningful public state/resource changes and native
terminal/summary. A source seam is allowed to mark dirty only when no required
transient content is lost. A periodic sampler must say sampled, not complete.
Required-seam coverage is advertised honestly and gates full-profile admission.

Event identity is `(stream_generation, publication_index)` with monotonic U64
publication index represented as a decimal JSON string. Source position is
`(stream_generation, source_seam, source_index)`, where source index is a
per-seam U64 counter in that generation, also represented as a decimal string.
The shared hub reserves a global publication index synchronously at each native
seam under its lock; this orders source observations across seams. It captures
all promised eager scopes before returning from a transient seam. Background
encoders can finish in any order, but only a contiguous sequence of completed
projections or explicit missing placeholders advances the publication high
watermark. A failed encode reserves its original position and becomes a missing
entry; it is never removed or renumbered. Pending encodings have a declared
finite 2-second monotonic deadline; expiry seals `encoding_timeout` and any late
result is discarded. Publication order cannot be inferred to be causal Commit
order. U64 rollover closes the generation, cancels waiters and requires reattach.
Events queries and Await validate generation and cursor before examining retained
entries. Await supports only `any_event`, `observation`, `catalog_nonempty`,
`terminal`, with no caller code/predicate. Under one hub lock it checks retained
entries and registers the waiter; publication checks waiters under the same lock.
It uses a monotonic deadline and returns exactly `event`, `timeout`, `gap`,
`cancelled`, `subscription_expired`, `generation_changed` or `capacity_exceeded`.
An observer's waiter does not fail merely because another controller loses its
lease. Stop cancellation applies only to owned control-dependent work.

`wait_id` is a client-chosen 32-character lowercase hexadecimal token, unique for
the live subscription. Optional `control_binding` is null by default; otherwise it
contains exactly `controller_lease_id` and `controller_generation` for that same
client session. The authority owner validates it when registering and returns
`cancelled` with reason `control_lost` if it is revoked. `cancel_wait` can affect
only that client/subscription/wait ID. Detach is the explicit whole-subscription
cancellation seam. An issued Attach starting cursor above the contiguous high
watermark is valid; the waiter skips all pre-attach reserved positions and waits
for a subsequent eligible occurrence rather than rejecting its own issued cursor.

Freeze necessary public values on the game main thread at their actual seam.
Record actual-displayed versus publicly-available-at-seam where they differ; neither
means Human mental knowledge. Never read transient values later through live getters
and call them old observations. Background encoding/storage/transport uses frozen
values only. Source capture failure, queue/disk failure and retention overflow have
separate gap reasons and accounting; none changes game legality or guesses success.

Large material not yet captured can be a notice with a current-read capability.
It cannot be a sealed historical view. Subscriptions/InputSpecs declare required
eager fields; a full-reference subscriber needs the complete input at each promised
observation position. Query-oriented consumers may request a new actual capture
for additional scope. Same-capture pages are mechanical assembly, not repeated W
updates. Reliable dirty/static reuse must prove dependency validity.

Use one source publication hub for online consumption and passive recorder reference.
Subscribers cannot dispatch or expose private witness objects. Recorder persistence,
Actor/SourceDeclaration and causal Commit/successor remain their existing owners.
Source events, Agent receive acknowledgements and Model consumption acknowledgements
are separate. I/F-off Model filters control/receipt/input-command metadata; actual
public observations can update W even when no action is available.

## Required examples and implementation checks

1. Begin real held card→focus target→actual changed preview→confirm/cancel. A second
   native input may be ready while the first effect remains pending; no effect fence.
2. Enter a large public list→capture/query all pages of one version→resolve one
   exact member→native revalidate. A changed world does not rewrite retained bytes.
3. Parent pending→child-ready→child selection→native continuation. Child completion
   does not prove parent Commit or its causal successor.
4. A→B→A with identical A text retains distinct occurrence; duplicate event delivery
   retains the same occurrence and cannot advance W twice.
5. Slow consumer/reconnect/gap and concurrent Await registration/Stop: gap is explicit,
   Stop wins new submission permission, unknown old request remains unknown.
6. Game outcome known→summary controls→return menu→task complete. Budget/transport
   interruption cannot produce a fake game result.

First implement projection/catalog/sealed access and the publication/Await mechanisms
with faithful fixtures. Add each exact native trigger and each missing L mechanism,
then qualify the loaded artifact and full consumer/recorder/data paths. Existence of
the stream API does not by itself qualify all required native exposure coverage.
