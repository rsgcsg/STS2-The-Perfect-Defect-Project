# Agent session protocol candidate

Status: E3 native source/test integration candidate, 2026-10-08. Runtime dependency
base `f89a5267f43ea206fe39d081f5fd5d3643953ca8` includes accepted SDK
`651f9cbd163d177805fa641ca0f05b8dcb424877`. Independent source review and
lead integration remain required. This document defines no native, loaded, model
quality, Human or complete G2/V1 qualification.

The [current specification](../../../docs/BASELINE_V1_SPEC.zh-CN.md), especially
sections 2–5, and the Connector-owned
[native profile](../../connector/docs/NATIVE_LOGICAL_PROFILE.md) govern this
addition. [Shared synthetic cases](../contracts/fixtures/agent-session-v1.json)
fix positive and negative examples. Changed native core/SDK fields require
explicit reconciliation; old text-menu source contracts remain separate.

## Compatibility and owners

The new adapter protocol is `sts2.policy-runtime/agent-session-ndjson-1`;
wire schema is `sts2.policy-runtime/agent-session-1`; manifest schema is
`sts2.policy-runtime/agent-manifest-1`. No existing Policy Manifest, decision
schema, ports 1–3, text profile, export or S0 source is reinterpreted. Selection
is explicit; neither side falls back to a legacy port on failure.

Connector owns public capture/catalog occurrence, structural resolution,
controller admission, native dispatch and original-request outcomes. Its SDK
owns strict native-wire decoding, immutable byte/page assembly and digest checks.
The existing Policy Runtime owner retains modes, recovery epoch, controller,
autonomy deadline, submission budget, Stop and append-only evidence. The native
session is an additive branch inside that owner, not a second controller or
another gameplay executor. Shared safety helpers must be extracted if needed.

AgentSpec owns attachment scope, query/acquisition strategy, InputSpec,
projection, history consumption, state and choice/timing. The Runtime validates
mechanical eligibility, known bindings and order. It never guesses omitted input,
chooses a prefix, ranks actions, decides when information is useful or supplies
teacher decisions. STPD owns Model/W/projection and offline replay; no such
implementation belongs in this Runtime packet.

## Manifest

The closed manifest has exactly `schema`, `manifest_id`, `agent`, `adapter`,
`artifact`, `input`, `requirements`, `support`, `limits`, `claims`.

- `agent`: nonempty `id`, `version`, `provider`, `architecture` strings identifying
  the complete Agent, including any programmed timing/acquisition policy.
- `adapter`: `id`, `version`, the exact new `protocol`, `code_sha256`.
- `artifact`: `id`, trusted installation `path`, `sha256`; this path cannot choose
  executable code. Application-owned static registration selects the adapter.
- `input`: `profile:"native-logical-v1"`, `input_spec` (`id`, `version`, `sha256`),
  `projection` (`id`, `version`), `state_format_version`, and `attachment` with exactly
  `eager_scope`, `required_seams`, `delivery_mode`. The latter three reuse native
  Attach grammar. `history_mode` is `full_reference`, `scoped_query`, or `sampled_current`, and
  `gap_policy` is `handoff` or `explicit_reset`. History/scope modes must agree.
  `consumption_mode` is `once_per_occurrence` or `incremental_view`, registered
  with the exact InputSpec. Full-reference uses once_per_occurrence; scoped-query
  may choose either. This is an Agent input-consumption rule, not Runtime-owned
  Model recurrence or a requirement that a Model exists.
  `state_recovery` has exactly `mode`, `max_state_bytes`, `model_bindings`.
  Mode is `opaque` or `none`; opaque permits 1–16 MiB and up to 16 exact
  `{model_id,weights_sha256}` bindings. Full-reference learned Agents use opaque
  export/restore. None declares no restoration with zero bytes/empty bindings;
  sampled Agents still carry W within their live segment.
- `requirements`: the existing exact Connector protocol/environment pin shape,
  plus `required_methods`, a duplicate-free list of native methods. It contains
  no score/index/successor requirement. Runtime checks exact environment and
  advertised methods/seam versions before attaching.
  The actual shared lifecycle minimum is `capabilities`, `attach`, `events`,
  `await`, `cancel_wait`, `detach`, `submit`, `result`, `renew`. Full-reference
  acquisition additionally requires `read`, `catalog`, `retain`, `release`.
  A declared scoped `current` query requires `read`, `retain`, `release` for its
  sealed byte transfer. Other query/structural methods are explicit requirements;
  an undeclared child method or Act expression fails rather than using an
  unadvertised fallback. These are transport/resource prerequisites, not strategy
  or inference methods. Existing Policy Manifest namespaces remain unchanged.
- `support`: duplicate-free `game_versions`, `game_commits`,
  `interaction_kinds`, `action_verbs`. An omitted required mechanism fails
  declared support; the Runtime does not filter the source catalog to fit it.
  Only `interaction_kinds` and `action_verbs` additionally reserve the explicit
  singleton `["*"]`: it accepts every valid source value within this declared
  native-logical-v1 profile, InputSpec and resource bounds. Mixed wildcard/value
  arrays are invalid; game versions and commits remain finite exact pins and
  cannot use a wildcard. Finite vocabulary arrays still reject an entire input
  containing an undeclared kind or catalog verb. This is mechanical vocabulary
  admission, not a claim that all native families exist, are covered or have
  qualified runtime evidence. No catalog is filtered. This unpublished contract
  refinement changes the exact Agent Manifest digest; old artifacts retain their
  prior identity and evidence.
- `limits`: positive bounded `max_message_bytes`, `max_acquisitions`,
  `max_retained_acquisition_bytes`,
  `max_pending_queries`, `max_queries_per_turn`, `max_query_bytes_per_turn`,
  `max_capture_bytes`, `max_catalog_actions`, `max_cancelled_ids`,
  `agent_timeout_ms`. These are consumer resource requirements, not a new native
  ceiling or permission to truncate input.
- `claims`: `catalog_filtered:false`, `creates_action_authority:false`,
  `creates_native_operands:false`, `human_origin:false`,
  `causal_successor:false`. Runtime evidence never infers scientific qualification
  or learned timing from this manifest.

Initial Runtime hard maxima: one child/session; one active Next and one Consume;
8 pending read-only queries; 256 retained acquisitions; 256 MiB aggregate retained
acquisition bytes; 64 queries and 128 MiB
query bytes per Next; 96 MiB encoded line; 64 MiB decoded capture; 65,536 catalog
members; 256 cancelled correlation IDs; 30 s Agent call. Manifest may lower these
bounds. Announced native limits and the authorization deadline additionally cap
every request. Bound checks precede allocation/parsing. No candidate truncation.
Line framing is UTF-8 NDJSON, exactly one JSON object per newline, closed fields
and finite numeric values. Overlong/unterminated lines close the port. A cancelled
ID is never forgotten to make room: exhausted capacity closes that child.
Accepted consumption IDs and child RPC/report IDs additionally have a 65,536-ID
session ceiling. Assembly reservations, decoded capture/catalog representations,
canonical coherence facts and pending query reply buffers share one byte budget.
Charge announced capture/assembly bytes before allocation/admission; release only
the actual released buffers, not merely because a deadline passed. Consumer limits
can cause honest capacity failure even below a native producer's larger ceiling.

## Session and atomic bootstrap

Runtime creates opaque `session_id` and `continuity_token`; `recovery_epoch`
is the existing owner fence. Startup Ready attests exactly the manifest adapter
identity. Each later message carries schema, session_id, recovery_epoch and a
nonempty correlation `request_id`. Runtime assigns parent-request IDs; the child
assigns query/report IDs. Direction-specific namespaces prevent collisions.
Unknown response IDs, wrong session/epoch, duplicate terminal responses and
unknown fields fail closed. IDs and clocks are scheduling facts, not tensors.
Ready has exactly `schema`, `message_type:"ready"`, `adapter`. Session messages
have exactly the five common envelope fields plus one of `input`, `completion`,
`output`, `result`, `error` as specified below: Consume/Next/query use input;
consumed/consume_ack use completion; directive uses output; query_result uses
result; error uses `{code,message}`. A child-initiated consumed report is a
request, not an unsolicited response; its own request namespace is validated.
State requests use input; `state_exported`/`state_restored` use output.
For the original full-reference/scoped modes, the physical `session_id` stays fixed across an idle Human/operational handoff.
A fresh correlated parent command may carry a strictly newer owner recovery epoch
only while no earlier Consume proposal, query or acknowledgement is pending.
The child then binds that operational epoch without resetting or advancing W or
its acknowledged consumption version. Regressing epochs, another physical session
and acknowledgements from an earlier epoch fail closed. Cancellation of an active
port call closes that child; an idle epoch change is not an implicit recovery of
an unacknowledged call.

Production Attach occurs on one game-main-thread turn. Under one source-hub
lock, register the subscription at reserved publication N, then reserve its
`initial_observation` at N+1 before another native seam can publish. The typed
core dependency is `AttachWithInitialReservation`. A rejected Attach creates no
initial position. A failed initial capture remains an explicit missing N+1.

Full-reference Runtime starts Events from Attach `starting_cursor` N. It waits for the ordered
initial projection N+1 and verifies/assembles that capture before offering it.
It must not bootstrap with a separate Current GET or await an encoder while
holding the main-thread/source lock. The subscription promises no pre-attach
history. Later source positions remain ordered even if encoding finishes first.

The input prefix is independent of a decision or receipt. Native event/gap,
Agent receive, acquisition and reported Model consume are separate records.

## Acquisition and reported consumption

A Runtime `acquisition_id` identifies one SDK-verified native capture and its
public decoded observation, exact catalog descriptor, scope/completeness,
capture SHA/size, generation, owner/focus/binding occurrence and known source
cursor, if published. Releasing/expiring bytes does not rewrite old evidence.
Unknown/expired IDs cannot be consumed or authorize a new action. Runtime-owned
acquisition metadata includes `catalog_materialized`: true only for the SDK's
whole verified array, false with `catalog:null` for a sealed scoped view that
exposes the complete relation descriptor without its candidate values. Original
source completeness is preserved; its `catalog` scope names descriptor exposure,
not a claim that the Agent received all candidates. Full-reference always requires
true and the whole array. Scoped canonical catalog coherence binds descriptor
digest, count and ordering. Lazy pages/Resolve are explicit views, not another
observation or a W advance; they cannot backfill candidate features into an earlier
acknowledgement. The implemented scoped consumer is stateless (`state_recovery:none`).
Opaque scoped recovery requires a registered materialization-aware InputSpec/state
binding before qualification and is explicitly unsupported by the current factory.
Eligible native statuses are `interactive`, `settling`, `observed`, `terminal`;
status alone never establishes readiness, native acceptance, Commit or completion.
Complete observed/empty-C inputs are consumed without inventing an action. Current
source has an observed producer; terminal remains future/test support.
An acquisition
is registered only after complete byte assembly and the declared scope checks.
Catalog pages are separately checked against the retained descriptor; their
retrieval is not an observation or a W update.

Define the qualified occurrence key as the native tuple
`(stream_generation, snapshot_id, owner_occurrence.occurrence_id,
owner_occurrence.binding_revision, owner_occurrence.focus_occurrence)`.
It is an equality/binding key, not a content hash or feature embedding. Native
observation `revision` orders different keys in a generation. Any consumption
with revision below the accepted native revision is rejected, including an old
duplicate acquisition. A different key with a non-increasing revision is rejected.
Same-key captures must agree on overlapping included public fields and native
binding facts. Different timestamps, capture/catalog handles or random scope IDs
do not change those facts. Same included scope with changed content at the same
occurrence is coherence drift, not new information or another advance.

Under once_per_occurrence, all captures/pages for that qualified occurrence are
one input-consumption unit. This is the default full-reference structured M2
rule; a new owner/focus occurrence remains a new unit even with equal features.
Under incremental_view, the unit is qualified occurrence plus canonical included
scope, in native `persistent,interaction,referents,catalog` order. Random scope,
capture, catalog or message IDs cannot manufacture another unit. Retransmitting
the same unit is a duplicate. A newly acquired authorized view containing
additional included fields not already consumed for that occurrence may be
consumed with a new accepted state version. An expansion that only repeats known
fields is view-only. Runtime checks known acquisitions, scope addition, native
coherence/order and exact registered mode; it does not implement a Model's update
algorithm. Actual acquisition and scoped history omissions remain recorded.

`consume` (Runtime → child) has exactly `acquisition_id`, `input_spec`,
`continuity_token`, `previous_consumption_id`, `observation`, `catalog` in its
`input`. `catalog` is null for an observation-only/scoped acquisition or the
SDK-verified complete ordered action array for the full-reference scorer.
Empty C is a valid complete catalog; it does not suppress observation delivery.
Incomplete mandatory input is never offered as a successful full-reference input.

`consumed` (child → Runtime) reports exactly `acquisition_id`, `input_spec`,
`continuity_token`, `previous_consumption_id`, `consumption_id`, `state_version`,
`advanced`. For a parent Consume response it reuses that request_id. For an
explicit query-acquired consumption report it uses a fresh child report ID.
Runtime responds with `consume_ack`, containing the accepted `consumption_id`,
`acquisition_id`, `state_version`, `advanced` and current prefix watermark.
Its closed `completion` fields are exactly `consumption_id`, `acquisition_id`,
`state_version`, `advanced`, `prefix`. Prefix has
exactly `continuity_token`, `history_mode`, `consumption_mode`, `received_cursor`,
`consumed_publication_index`, `omissions`. Consumed publication index is null for
a query-current capture. Omissions has `received_unconsumed_count` (integer or
null when a gap prevents exact accounting), `missing_scopes` (native field names),
`gap` (native gap or null). Metadata-only events are not counted as unconsumed
observations. Acknowledgement states the actual known prefix, not restored history.

The first consumption of a new eligible consumption unit reports `advanced:true`, a
fresh opaque consumption_id and exactly previous state_version+1. A duplicate
unit reports `advanced:false` and the unchanged consumption_id
and state_version. Initial state_version is 0, first consumption is 1. Reports
must bind known verified acquisitions, the manifest InputSpec, current continuity,
last acknowledged consumption, and exact eligible order. Overflow closes/reset
is explicit. Consumption acknowledgements do not contain opaque state bytes.
Only after consume_ack may a directive claim that watermark.

This is a correlated **Agent assertion of consumption**, not machine proof of
Model thought or W contents. `advanced` and state_version denote an accepted
Agent consumption-state transition; they do not claim numerical W exists or
changed. A stateless programmed query Agent can retain acknowledgement tracking
without owning a Model or learned memory. Runtime verifies bytes/binding/order;
domain replay
and independent numerical tests separately check actual Model behavior. The
Agent cannot claim an acquisition it never received or backfill a missing source.

Full-reference: every promised eligible observation, including initial and
empty-C observations, is consumed in publication order before Next. Metadata-only
input/receipt/control events do not create Model input. Required missing/expired
positions cause gap handling. Query responses may inspect views, but cannot be
used to jump ahead of pending publication consumption or manufacture another W
advance. All Model inputs retain the actual ordered published capture prefix.

Scoped-query: native notices preserve omissions and received cursors. `current`
can acquire a new actual declared-scope capture; it cannot fill the earlier
notice. Query responses alone never consume or advance. After choosing and
actually consuming a verified acquisition, the child sends the explicit consumed
report. Runtime records the intervening received-but-unconsumed positions and
scope missing fields as omissions. The registered consumption mode decides whether
a new authorized scope is another eligible unit. A stale/regressing view or invalid/unsupported native scope
fails; evidence never promotes this prefix to full-reference history.

The Agent projection is responsible for I/F off: request, receipt, lease,
reason, cursor/time/seed/opaque identity never enter Model features. Actual public
HP/selection/focus/summary remains eligible. A receipt alone cannot trigger W.

## Next and bounded read-only RPC

`next` (Runtime → child) supplies `continuity_token`, the acknowledged
`consumption_id`, `state_version`, `basis_acquisition_id`, and the latest received
source cursor. It makes no action or timing decision. During this call the child
may emit correlated `query` messages with exactly `method`, `arguments` in input.
Methods are `current`, `read`, `catalog`, `resolve`, mechanically delegated to
the new Connector SDK. Native parameter grammars remain Connector-owned.
Runtime adds its own client/subscription scope only; no child lease or secret.
For a scoped-query consumer before its first consumption, consumption_id is null,
state_version is 0 and basis_acquisition_id may be null. This allows its first
actual queries without forcing a synthetic Model update. Await/Abstain/Close can
also use this empty prefix. Act requires an accepted consumption and known basis.

`current` additionally uses the SDK's mechanical same-capture byte assembly and
returns a registered acquisition. `read` is bounded immutable chunk access;
`catalog` pages preserve complete and filtered counts/digests; `resolve` returns
the exact native unique/no-match/ambiguous/expired result. Each query_result carries
its original request_id; its closed result is `{method,value,acquisition_id}`.
For current, value is `{capture,observation}` and acquisition_id is its registered
opaque ID; for read/catalog/resolve, value is the corresponding decoded native
reply and acquisition_id is null. The result method must match the pending query.
Unknown/mismatched query results are rejected. Child queries cannot submit,
acquire/release control, change attachment or run arbitrary predicates/programs.
Late results from revoked epochs are discarded; all calls consume query/byte/time
budgets. They cannot outlive the authorization deadline.

The query helper may send a consumed report while Next is pending. Runtime first
validates/acknowledges it, then accepts a directive only if its final watermark
matches the latest accepted report. Unacknowledged/regressing state versions and
reports of unknown acquisitions fail closed. These checks cannot prove a child
ignored acquired-but-unconsumed bytes; actual InputSpec adherence is independently
checked by its domain replay/consumer qualification.
The call's source cursor field is exactly `received_cursor`; it is a known native
same-subscription opaque cursor, or null before attachment establishes one. No
cursor from another client/scope can change this prefix.

`directive` replies to Next with exactly `continuity_token`, `consumption_id`,
`state_version`, `directive`. The directive union is:

| type | Closed fields and behavior |
| --- | --- |
| `act` | `basis_acquisition_id`, `selection`, `scores`; selection is either `{kind:"handle", action_id}` already received from that exact catalog, or `{kind:"expression", expression}` passed to native Resolve. Scores is null or `{catalog_digest, values}` for the verified full ordered catalog. It cannot authorize or change the selection. |
| `await` | `after_cursor`, `condition`, `timeout_ms`; condition is native `any_event`, `observation`, `catalog_nonempty` or `terminal`. Runtime validates known same-subscription cursor and forwards native Await. No predicate code, fake Wait action or invented observation. |
| `abstain` | `reason` (public diagnostic string, at most 256 UTF-8 bytes). Hand off/release according to existing mode lifecycle; no gameplay action. |
| `close` | `reason` (public diagnostic string, at most 256 UTF-8 bytes). End the Agent session using existing lifecycle cleanup; not proof of game/task completion or a natural outcome. |

Act requires current epoch, accepted InputSpec state, a complete source relation
descriptor, same capture/snapshot/catalog/generation binding, and declared support.
A query consumer need not materialize or score every member. A direct handle must
have been SDK-verified in a known page of that relation. A structural expression
must resolve uniquely; it cannot invent an operand. Runtime authorizes one action
and uses the existing controller and shared submit primitive. Connector performs
fresh native revalidation. No client candidate index or arbitrary action object.

Known delivered input with pending effects does not wait for stableSuccessor.
Runtime records the exact native result and resumes public occurrence processing;
child-ready may enable the next Act while parent effects remain pending. Immediate
observed frames remain observations, never causal S'. Partial/unknown/expired
outcomes fence further mutation and hand off; no retry, new-ID replacement or
current-state success inference. Original-result lookup is recovery-only and
never authorizes another submit.

Await creates an owned cancellable waiter. It does not acquire a controller for
an observer. If Auto already holds one, Runtime alone supplies any control binding.
All native finite outcomes are returned literally. Timeout is not an observation
or W update; the Agent chooses the next directive. One-Step ends after its one
actual action, not after a Consume or read-only query. Shadow never submits.

## Stop, deadlines, failure and recovery

Human/Stop/deadline immediately advance/fence the existing recovery epoch and new
submission-start permission before waiting on serialized cleanup. Cancel owned
waits/queries and abort active Consume/Next. Bound or terminate a hung child;
late acknowledgements/directives cannot update the accepted prefix or acquire
control. Deadline expiry itself triggers release/evidence, including idle-after-
delivery and Await; no second tick, status GET or model response is required.
Do not queue Stop behind Await/network serialization or Model computation.

A submission already past Connector's shared start boundary keeps its real
delivery/execution/effect outcome; release never rewrites it as not-started.
An uncertain post-offer Consume/Next leaves Agent state uncertain: no next offer
uses it as a known prefix. Existing release/evidence failure remains fail-closed.

Reconnect verifies exact Agent/package/InputSpec/profile/state-version/generation
and acknowledged prefix. Complete retained captures may be replayed by the Agent
under an explicit recovery record; known state can be resumed only at its exact
matching prefix. Gap, changed generation/model, unacknowledged consumption or
unknown request forbids seamless continuation. `handoff` releases control.
`explicit_reset` is permitted only after a recorded gap/omission, fresh known
initial/current capture and new continuity token, with state_version reset to 0.
It starts a visibly separate segment; it cannot restore full-history qualification
or clear an unknown delivery taint. New leases never revive old actions.

## Opaque state snapshot and restore

The Runtime may copy/hash/store immutable opaque inference-state payloads through
the existing AgentRunEvidence storage. It never interprets, alters, merges or
initializes W/weights. Agent/Model owns encoding, format validation and restoration;
the default STPD implementation uses the existing safe tensor-tree codec, never
pickle. State provenance and consumption acknowledgement remain different facts.

Export occurs only after a durable consume_ack, with no outstanding Next/Consume/
query. `export_state.input` is exactly `{expected_metadata}`. The Runtime constructs
expected metadata from its immutable manifest and durable acknowledged prefix,
not the child's report. `state_exported.output` is exactly `{metadata,payload}`.

Restore occurs in a fresh attested child before any Consume. It requires exact
compatible source history and no unresolved native request: known pending requests
must first be reconciled by their original IDs. `restore_state.input` is exactly
`{expected_metadata,state}`, with state `{metadata,payload}`. Metadata compatibility
and hash/size checks happen before offering to the child. Agent-owned decode then
returns `state_restored.output:{metadata}` echoing the exact accepted metadata.
None mode explicitly rejects export/restore instead of inventing a Model state.

Metadata has exactly `agent_artifact_id`, `agent_artifact_sha256`,
`adapter_code_sha256`, `model_bindings`, `input_spec`, `profile`,
`state_format_version`, `stream_generation`, `continuity_token`, `consumption_id`,
`state_version`, `prefix`, `last_acknowledged_basis`. The latter has exactly
`acquisition_id`, `capture_sha256`, `snapshot_id`, `owner_occurrence`, `revision`,
`included`, `publication_index`. It binds actual known acquisition/occurrence,
the last acknowledged state version and any omissions/gap. Public reference IDs
are correlation metadata, never Model feature embeddings or revived action rights.

Payload has exactly `{encoding:"base64",byte_count,sha256,data_base64}`. Validate
decoded byte_count against the declared 16 MiB maximum and exact Base64 length,
alphabet/padding/canonical trailing bits before allocating its decoded buffer.
Hash the actual bounded bytes. Oversized payloads cannot evade the line/shared
byte bounds. Runtime stores immutable binary and metadata files; partial storage
failure prevents sealing a falsely complete evidence bundle. Exported byte handles
retain their reservation until the caller completes copying and releases them.

Wrong package/weight, InputSpec, state format, generation or unacknowledged prefix
fails before a Model advance/native admission. A new Current view cannot recreate
missing old history. Gap, expired source prefix or unknown outcome uses explicit
reset/handoff; neither state restoration nor a new lease clears unknown taint or
revalidates old catalog actions. Actual numerical restore/parity remains an STPD
packet. The separately executed initialized-Model export/OneStep interop receipt
is recorded in [implementation status](AGENT_SESSION_IMPLEMENTATION.md); the
Runtime-only opaque byte tests do not establish numerical restore parity.

## Implementable paths and handoff

The implemented native branch is selected by `PolicyRuntime.forAgent`; the
existing CLI accepts it through `--manifest` and the same loopback HTTP service
reports `agent-session-startup-1` and `agent-session-status-1`. One shared lifecycle
owner provides serialization, epoch cancellation, wallet/deadline and Stop.
Capabilities, existing registration and atomic Attach precede source Events.
Every SDK-decoded source event carries a captured view or explicit missing fact.
Its open `kind` classifies source/Await timing and does not filter input eligibility:
full-reference consumption includes terminal and future scalar kinds, and missing/
expired payloads record the original publication gap. Terminal kind does not
infer task completion, win/loss, Close or reset.
After all provided batch items and full-reference acknowledgements complete,
`native_event_batch_received` records exactly session context plus
`{after_cursor,next_cursor,high_watermark,retained_start_cursor,event_count}` from
that actual SDK response. It is emitted in the same guarded phase as advancing
the operational received cursor, before later Next/query work. A genuine empty
global batch may advance that cursor without any Model input, W advance or new
consumption. Intermediate full-reference ACKs precede the batch tail and use
their individual event cursors. Gap, failed item or interrupted Consume does not
produce a falsely completed tail. Opaque state metadata remains bound to the
last acknowledged Model prefix; an empty operational tail does not rewrite it.
Full-reference observations use SDK full capture/catalog assembly; scoped-query
observations preserve an explicit descriptor-only materialization fact. Current
is a query view and cannot replace missing historical input or advance W without
an explicit consumption report. The factory currently accepts `gap_policy=handoff`
only; `explicit_reset` is rejected before Attach because no reset command is
implemented. Scoped opaque recovery is likewise explicitly unsupported.

Submit uses one owning SDK dispatch. A recoverable 202 pending result releases
control and fences mutation until explicit original-request reconciliation through
`POST /v2/reconcile`; terminal unknown/partial delivery taints the session. An
expired lookup remains unresolved. Human does not cancel or rewrite a POST already
started. Stop cleans owned subscription, child and buffers even if event writing
fails. Programmed real-process/SDK/HTTP tests are source/test evidence only. The
separately enabled numerical interoperability test requires an external exact
Model package and cannot be inferred from a skipped fixture or a legacy package
smoke check.

Passive subscription renewal uses one separate abortable, bounded SDK flight
outside the Model operation queue, scheduled from half the actual remaining TTL
with a five-second maximum cadence. It uses only the actual received cursor and
never acquires control, emits a new view or advances W. Human keeps the passive
subscription. Stop and initialization failure abort and await the flight before
detach, including a bounded transport that ignores abort. Renewal failure fences
mutation, cancels active work and releases control immediately; the existing
operation queue records its gap/handoff before sealing. It does not retry failure.

After durable `native_submission_requested`, a `native_submission_not_started`
event has exactly session context plus `{request_id,submission_epoch,reason}` and
closes that original intent only when the owning SDK dispatch hook never ran.
The context epoch may reflect a subsequent Human handoff; `submission_epoch`
binds the original intent epoch. This is not a NativeResult, Receipt, Commit or
controller/effect claim. A started POST with an unknown outcome cannot receive
this closure. Human after controller-acquired evidence is checked before any
intent is constructed; Human after the intent is recorded receives this explicit
known-not-started fact. Restore rechecks its captured authorization immediately
after fresh-runtime validation, before closing the original idle child.

Runtime owner: maintain Agent contract/validators, strict bounded duplex child
port and native branch in the existing Runtime/CLI/server lifecycle. Legacy tests
remain unchanged regression scope.

SDK owner: export accepted native types and typed SDK methods/assembly plus atomic
initial Attach semantics. Production Runtime import waits for its exact accepted
commit; this contract candidate does not vendor another SDK or mutable native core.

STPD/E2 owners: new projection/InputSpec, actual state updates, sequence replay,
versioned closed package and consumption/source evidence. The Runtime packet must
not modify their files or claim its assertion checks prove their numerical work.

Conformance first exercises empty C; same-key new handles; same-feature new owner/
focus; A→B→A; pending-parent/ready-child; query-only then explicit consumption;
once-per-occurrence versus incremental scope expansion; stateless query Agent;
same-scope coherence drift; ambiguity; Stop during Await/Model hang; idle deadline; unknown
delivery; gap/reset/handoff; bootstrap events between Attach/initial encoding;
and both full-reference and actual multi-query consumers. Source/portable checks
precede and never replace native trigger, installation or full G2/V1 qualification.


## Sampled Current carry addition

The shared [sampled contract and vectors](../contracts/fixtures/sampled-current-carry-v1.json)
freeze the acquisition law, complete-C frames, exact InputSpec/AgentSpec and
cross-language message chain. This is an additive source candidate; earlier
full-reference/scoped artifacts and records keep their original meaning.

`sampled_current` requires `once_per_occurrence`, `gap_policy:handoff`,
`state_recovery:none`, `delivery_mode:scoped` and an empty eager attachment
scope. In addition to the lifecycle/action methods it requires `current`, `read`,
`catalog`, `retain` and `release`. Attachment/seam pins are wakeup capabilities;
they do not promise complete history exposure. Every sampled child Current query
requests all four scopes and receives the existing SDK's whole frozen capture
and every ordered action. Partial Current fails. Full-reference null-publication
advance rejection remains unchanged.

One fresh live Runtime session is one sampled segment. Next can begin at version0
without an initial publication basis: parent Next N → child query Q → query_result Q
→ child consumed C → consume_ack C → directive N. Q/C IDs use the existing
`child-` namespace. Sample acquisitions and ACK prefixes have null publication
index. Versions count accepted memory advances, not Source/native positions.
A consecutive unchanged NativeUnit is a readiness check, with no consumed report
or W advance; Runtime releases its unused acquisition after Next. Map→Inspect→Map
has three ordered advances even when both Map feature encodings agree. An Agent
uses native wakeups plus a fixed 250 ms bounded Current recheck, with no visited
list. A complete empty-C nonterminal input is readiness-only; an actual qualified
ready-summary input is a final unlabelled sample, ACKed before the existing task's
Close. No timer, empty C or budget stop establishes completion.

Post-start Human, OneStep completion, failure or interruption ends the segment.
Same-session Auto reentry is fenced; the existing Stop/fresh-load owner starts a
new one. Initial Human before any sample does not end a nonexistent segment.
Unknown delivery, an original pending request and uncertain controller disposition
remain fenced and cannot be erased by a segment reset. Advisory publication
retention gaps use the explicitly labelled `agent_sample_publication_gap`; they
are not fabricated missing Current samples or full-history evidence. Required
Current/control/environment failures still hand off.

### Original sample evidence

`AgentRunEvidence.storeSampleAcquisition` persists the original validated UTF-8
observation bytes and canonical SDK-assembled ordered catalog bytes before an
accepted proposal can ACK. It uses three run-local content-addressed files per
sample and closed `sts2.policy-runtime/agent-sample-input-1` metadata; the shared
fixture owns every field. Observation SHA/size bind the original capture;
catalog payload SHA/size and native structural digest/count independently bind
all complete members and their order. Assembled catalog bytes are not literal
network page bytes. No native object, closure, credential or model state is stored.

Typed events distinguish acquisition, actual query-result write attempt,
consume-proposed stored payload, ledger acceptance, ACK write attempt, later valid
directive, discarded readiness and ended segment. `agent_consumed` retains its
original ledger-transition meaning. Neither it nor ACK write-attempt proves
that the child read ACK or committed W; a later exact directive watermark can
substantiate resumed known state. Sampled Next requested/completed events bind that
reply to the original parent request, context, basis and watermark. Agent state
remains uncertain throughout an unresolved Next, including after ACK write.
On an interrupted call, preserve offered or
proposed original payloads and uncertainty rather than guess continuation.
Disk failure before ACK prevents ACK; partial storage cannot be sealed complete.
The independent Evidence verifier rejects missing, tampered, extra or unbound
samples and any sampled directive without its stored original input/ACK join.

Local sample limits are 1024 samples/3072 files/512 MiB aggregate, 8 pending
samples/128 MiB pending copies and 64 KiB metadata, separately from unchanged
opaque state limits. Runtime's acquisition/assembly budget also charges retained
original sample buffers. Quotas cause explicit failure, never payload truncation.
The legacy 16 MiB Agent-evidence share-upload compatibility limit is unchanged;
a locally verified large sample run is not thereby shareable. Future transfer
support and actual representative game payload measurements remain owner gates.

## Opt-in owned Current and bounded fresh decisions

The default remains legacy Current and handoff on non-delivery. This addition
separates an exact known stale refusal from uncertainty while preserving the
accepted observation prefix. It does not automatically retry an old action.
Its normative examples are
[owned-current-known-stale-v1.json](../contracts/fixtures/owned-current-known-stale-v1.json).

### Closed manifest, port and Next

Only sampled_current/once_per_occurrence/scoped-empty attachment with
state_recovery:none can declare this optional top-level manifest field:

```json
{"execution_policy":{"schema":"sts2.policy-runtime/agent-execution-policy-1",
 "current_mode":"reader_owned_v1","known_stale":"fresh_changed_current_v1",
 "operational_outcome":"known_not_started_v1",
 "max_known_stale_rejections":8,"max_consecutive_known_stale_rejections":3}}
```

Total is an integer1..16; consecutive is1..min(total,4). No boolean/zero/extra
fields. Absence means legacy, without inserting a default into historical
manifest bytes. Required methods include both visible child `current` and
transport `current_owned`, plus existing lifecycle/read/catalog/retain/release.
Initialization of an opted-in manifest records the existing mode_changed event
after native_session_attached with the actual initial mode/budget/controller,
before any deferral. Evidence does not guess Auto from mode=None. Legacy
initialization remains unchanged.

Missing capability fails; Query remains `current`, all four scopes and the whole
original orderedC. No feature/InputSpec/I/F/tensor/NativeUnit law changes.

`NdjsonAgentSessionPort` takes explicit optional executionPolicy as constructor
argument5 after byteBudget; staticspawn forwards options.executionPolicy. It
validates/clones/freezes a read-only getter. Runtime requires exact policy
equality with the validated manifest before ready/native registration/mutation.
A restore replacement must match before old-port/epoch/native changes; this new
none-recovery policy adds no restore capability. No mode is inferred from input
field presence. Old absence requires exactly the existing five Next fields.
New policy requires exactly those five plus nullable `operational_outcome`.
The public native port is exported by the main package; the existing ./child-port
export is a legacy decision-only launcher and is not repurposed.
Python adapters bind this parser to their declared artifact/spec policy; old
actual immutable adapter/package bytes remain legacy.

Outcome fields are exactly `schema`, `basis_acquisition_id`, `action_id`,
`consumption_id`, `state_version`, `result`. Schema is
`sts2.policy-runtime/known-not-started-outcome-1`; result is the full exact SDK
NativeLogicalResult. It joins the original submission/basis/watermark and repeats
idempotently through unchanged readiness until a new accepted input commits.
It is control metadata outside Consume features, not previous_actual_action or
feedback. A learned I/F-off adapter validates/ignores it for W/scoring. Teacher
may stage an exact refused-intent correction only with a genuinely changed new
input and commit it on that new ConsumeACK; Runtime never edits/rewinds its state.

### Ownership and qualified readiness

Runtime owns the SDK reader lease per acquisition. The SDK temporary reply owner
precedes all throwing post-reply checks; assembly adopts before admission; a
stack owner/map-commit flag protects register/evidence/byte/map insertion. It
transfers only the reader lease, then disposes large assembly buffers. Readiness
discard, acknowledged replacement and Stop release it. Release takes/removes the
lease first and finally releases despite local/evidence failure. Offered
uncertain bytes are persisted before dropping local bytes; write failure taints
but cannot withhold native cleanup. Stop revokes control immediately; serialized
resolve/started-submit ownership is unchanged. Final cleanup attempts every lease
before closing registration, regardless of renewal/detach/Evidence failure.

Freshness uses the existing qualified NativeUnit: occurrence=(stream_generation,
snapshot_id,owner.occurrence_id,binding_revision,focus_occurrence), revision and
normalized complete coherence. Coherence excludes observation observed_at and
catalog_ref/scope_id mechanics; it retains the original complete facts/catalog.
An unchanged occurrence requires equal revision/coherence. A new occurrence
requires a strictly newer revision in the same generation. Runtime explicitly
checks the refused accepted unit before offering a readiness query/allowing
Await: existing ledger field-coherence checks alone do not cover readiness
revision drift. Generation/regression/same-unit drift fails. New capture/scope/
time/IDs alone and child advanced booleans do not suffice. Legitimate Map→Inspect→Map
may still advance even when model features agree.

An exact new advanced ConsumeACK/completedNext can enable a new independent
choice from its fresh originalC. An unchanged unit only Awaits/Closes/Abstains;
old action/request/basis IDs cannot be repeated/rebound. No W/continuity/epoch
reset, copied directive, old-ID retry or success inference. Unknown/partial,
pending/expired, transport-after-start, controller uncertainty, arbitrary
not_started reasons and required Current/source failures still fence/handoff.

### Original dispatch event and result

Opted-in Runtime supplies SDK onDispatchBinding over the actual prepared body.
It emits ONE existing native_submission_requested event with the old fields plus
`dispatch_binding` (exact runtime/client/lease/generation). Legacy shape/timing is
unchanged. It awaits this hook before the existing synchronous budget callback
and transport. Hook/evidence failure before start has no hidden HTTP/start
charge. A native_submission_not_started closure requires a corresponding
durable intent; failed intent proof is unavailable, not an orphan invented event.
Terminal SDK attribution and Evidence joins all four original fields even after
renewal or Stop. Opted-in pending original/reconciliation stores that same binding;
no new request/authority ledger. Human reconciliation never enables deferral.

### Count first, then decide whether continuation is allowed

Preserve every full joined original terminal native_result. For each original
request exactly once, classify only delivery=not_started,
reason=stale_snapshot_or_binding, action=null, stages=[], retry=never_automatic,
with exact original attribution. Count matching results BEFORE checking Auto,
control, epoch, known state, no-pending/taint, deadline/budget. Thus threshold and
late Stop/deadline refusals count. Delivered resets consecutive only; total never
resets. Other dispositions do not increment/reset stale counts. Existing terminal
reconciliation shares this dedup/classifier, never a Human continuation.

Only an otherwise authorized Auto owner strictly below both ceilings may emit
`native_stale_decision_deferred`, whose payload has exactly normal context plus
`request_id`, `basis_acquisition_id`, `consumption_id`, `state_version`,
`known_stale_rejections`, `consecutive_known_stale_rejections`. These counts equal
independently rebuilt full original results. Deferral-event count is not refusal
count. The threshold result counts but has no deferral/new decision. Return
`fresh_decision_required` with original_request_id and status, allowing the next
ordinary Next cycle only. An already winning Human/Stop/deadline/control reason
is retained, not replaced with stale handoff.

An opted-in stopped `not_delivered` tick has exactly type, reason and status;
reason is the sticky actual Runtime owner termination cause. Legacy ticks keep
their old shape without reason. The native Result reason remains the independent
refusal cause. For example a third consecutive stale result returns reason
known_stale_streak_limit; the same refusal arriving after Stop keeps stopped or
human_recovery, and after the deadline keeps autonomy_budget_exhausted. Counts
still include that original refusal. Consumers must not infer termination from
stale counters/result or fabricate a runtime_handoff fallback when an opted-in
owner reason is missing.

For ceilings8/3: below-limit stale takes total2/streak0→3/1 and may defer;
unchanged Awaits leave3/1; changed-unit independent delivered resets3/0. A third
consecutive stale takes2/2→3/3, counts and stops without deferral. Total7/0→8/1
likewise counts/stops. A result after Human/Stop/deadline still counts, cannot
defer/reacquire/reset, and keeps the actual stop reason.

### Evidence and migration limits

The Evidence owner must explicitly support conditional closed manifest/Next/
submission/pending/deferred shapes; old shapes/producer tuples remain accepted
unchanged. It validates original mode/epoch/prefix/no-pending/taint, exact original
four-field binding and rebuilt counts/contract-consistent deferral. Existing
records do NOT prove live lease expiry/current budget validity: those mandatory
checks are source-tested Runtime owner facts with final native admission, not
independent proof from omitted fields. Evidence outputs/tests retain this limit;
no extra current-expiry witness/framework is added. Incomplete evidence makes
final count proof unavailable/tainted, never reconstructed from a status projection.

Collector attempts retain the existing used-submission budget, including a
started request without terminal result. Terminal results, stale counts and
known deliveries are distinct. Default max100 is unchanged; an explicit opted-in
request may select max200 with target100 and the original deadline. No automatic
increase/reset. Collector stops at100 known deliveries; later100–300 admittedN,
coverage/exclusions/training are separate gates, not promised by delivery count.

Source/test acceptance precedes exact integration of the new Evidence verifier;
Root then pins/locks/installs it before freezing the final Teacher1.0.6 tuple with
the NEW lock SHA. Historical1.0.5/1.0.4 tuples keep their original hashes/locks.
Version/BOM/package/install/game/data/use/fit decisions belong to Root and their
owning consumers. This source contract claims no new installed/live/Human/N/
model-quality or fullG2 qualification.

### Consumer joins and verified terminal summary

The child has its last intention's basis/action/Consume/W/snapshot/runtime, but
has not observed the actual dispatch request ID or original client/controller
binding before the result. It validates full outcome grammar and those available
joins, then the repeated request-ID/body equality. It does not independently
prove the first request/controller attribution. Runtime/SDK and Evidence own
that exact recorded dispatch join; no extra child control capability is supplied.

For a newly verified opted-in run, Evidence exposes a fresh dict copy as
`value.terminal_summary`; legacy verified values expose None. Its exact fields
are schema (`sts2.evidence/agent-session-terminal-summary-1`), run_id, content_id,
original_submission_count, terminal_result_count, known_delivered,
known_stale_rejections, consecutive_known_stale_rejections,
proof_scope (`recorded_dispatch_and_terminal_results`) and
live_eligibility_proved (false). Counters are nonboolean nonnegative integers.
original_submission_count counts recorded intents; it does not prove SDK start
or used-budget attempts. Collector retains Runtime's budget attempt count
separately. Unknown/incomplete evidence supplies no guessed verified summary.
