# Player Environment Protocol

Source protocol: `1.0.0`

## Endpoints

```text
GET  /api/player-environment/capabilities
GET  /api/player-environment/snapshot
GET  /api/player-environment/text-menu/observation-context
GET  /api/player-environment/reads/{read_id}?expected_snapshot_id=...
POST /api/player-environment/clients/register
POST /api/player-environment/clients/revoke
GET  /api/player-environment/controller
POST /api/player-environment/controller/acquire|renew|release
POST /api/player-environment/actions
GET  /api/player-environment/actions/{request_id}
POST /api/player-environment/evidence/native-pages/sessions
GET  /api/player-environment/evidence/native-pages/sessions/{session_id}
POST /api/player-environment/evidence/native-pages/sessions/{session_id}/return|recover
```

## Original client lifetime and final revocation (source foundation)

An original client has a 30-minute idle deadline measured by the Host's
monotonic clock. Valid registration or owned control operations extend it;
invalid lease attempts, passive control watches (including capacity denial),
and passive state/result lookups do not. Expiry permanently
closes that original session and revokes only its owned lease, including while
no request or poll is arriving. Closed records remain runtime tombstones within
the existing 4096-client capacity. A later explicit registration receives a new
session identity; it does not reopen an old session.

`POST /api/player-environment/clients/revoke` accepts exactly
`runtime_instance_id` and `client_session_id`: two required original opaque ASCII
identifiers of at most 128 characters in a body of at most 1 KiB. The call uses
the existing Authority gate directly and does not wait for the game thread.
An HTTP 200 acknowledgement has protocol `1.0.0`, schema
`sts2.player-environment/client-revoke-1`, exact original identity echoes,
`status=client_revoked`, `closed=true`, and `controller=null`. The null controller
means the target owns no lease; another client's global lease may still exist.
The same closed target can be acknowledged again. Wrong runtime, unknown target,
malformed body, missing/duplicate/extra fields or oversized body cannot assert
closure. A missing or invalid acknowledgement retains consumer uncertainty.

This foundation additionally exposes private original-client lifetime handles
and post-Authority closure callbacks for the existing request/publication owners.
The shared request owner below consumes these lifetime handles. Owned source
renewal and recording accounting remain separate producer integration work.
These interfaces and source tests do not claim game/runtime qualification.

## Original request and terminal byte lifetime (source candidate)

All Player Environment profiles use one existing runtime-global request namespace.
An admitted original ID is permanently spent within that runtime, including when
its queue is cancelled or its terminal bytes expire. Limits are 65,536 spent IDs
per original client and per runtime. Matching duplicates return the original
pending state or byte-identical terminal; they do not enqueue work or extend client
idle time. Conflicting profile/action/client fingerprints return a conflict.
An expired original result returns HTTP 410 `result_expired` and cannot be retried
or revived by renewing the client or controller. Pending/started results are never
retired as though their outcome was known.

New IDs require a live original client/controller, bounded request shape, available
ID/sender capacity and a physical result reservation before admission and queueing.
Capacity or invalid-client denial spends no ID. A client closure cancels only its
unstarted requests, removes their original queued jobs and releases HTTP waits
without another game-thread drain. A start that already won the existing Authority boundary may
finish its actual outcome after closure; the original in-flight POST sender may
receive those bytes while later lookups report expiry. Successful control release
keeps its original client/passive registration alive.

Original terminal retention lasts 30 monotonic minutes after sealing, independently
of client renewal. Results retain frozen UTF-8 segments rather than typed outcome
or successor graphs. The original POST and replay GET write those exact segments
with reference-counted sender loans. Retirement retains its actual backing-array
charge until the final sender finishes; it cannot reclaim a still-writing buffer.
The result-only arena owns at most 512 MiB of byte-array capacity, including bounded
reusable free segments. This limit is separate from public capture memory, source
and game graphs, CLR metadata, network buffers and process RSS.

The closed terminal encoder requires the signed .NET 9 `System.Text.Json` identity
and a successful read-only private `JsonElement.GetRawValue` ABI probe. It borrows
existing JSON bytes synchronously and emits scalars incrementally through charged
scratch/output blocks, with no serializer/pool/whole-document-copy fallback.
Unsupported framework identity rejects new admission before allocating an ID.
Native terminal output is at most 2 MiB with a 4 MiB reservation; other profiles
use 8 MiB output and 16 MiB reservation. These source bounds and portable serializer
parity are not measurements of loaded-game peak memory or performance.

Applied text-only navigation/selection first computes the complete mandatory
successor without changing its private state and freezes the entire result. Only
then can the original Authority start and state commit occur. Capacity failure
preserves cursor, selection, revision, sequence and native state. Applied text
successors are never omitted. For actual native input, only the optional immediate
post-input diagnostic view may be omitted with explicit
`successor_payload_capacity_exceeded`; original delivery, action, attribution and
known stages remain intact. No result claims native Commit or causal settlement.
An overlarge
pre-input selected-action body may use the existing truthful `action=null` rejection
shell: request/snapshot IDs and the namespace's original immutable request retain
the submitted bound-action identifier. No partial NativeAction is invented and no
input or text effect begins. After input starts, action facts cannot be omitted.

## Snapshot

Capabilities carry Host/game/Modset identity, environment fingerprint and
optional Host implementation provenance. The hot Snapshot carries:

```text
snapshot_id, sequence, status, persistent,
interaction { interaction_id, kind, stage, prompt, content_schema, content, capabilities[] },
referents[], reads[], completeness,
bound_actions { status, counts, limit, ordering_semantics, actions[] },
session { runtime_instance_id, environment_fingerprint }, information_policy
```

A referent is a player-visible object or control identity. Facts create
referents independently of action publication. Exact screen, room, hand, slot
and annotation-input bindings never enter Surface facts. Optional `enabled`,
`selected` and `focused` are observed state, not global legality; current C1
does not yet claim keyboard/controller focus coverage. Interaction capabilities
describe current verbs and participant roles without enumerating operand
tuples. A finite bound action has one optional `subject_referent_id` plus role-labelled
`arguments[]`; each reference must exist in the current snapshot. Exact native
operands stay inside the Host.

The in-process C# assembly additionally offers a non-wire witness API for
conformance tools. It freezes the public Snapshot plus exact references from
that observation and can compare an already accepted native action to the
catalog by reference equality. It is read-only, process-local, not transported,
and cannot create or deliver a BoundAction. The frozen witness also supports
exact native owner/operation/operand matching for selector input callbacks.
Private owner bindings are never serialized into the public Snapshot.
Each successful process-local text-menu witness freeze also carries a distinct
capture ordinal assigned under the Host's existing submission gate. It orders
captures even when the public Snapshot identity and state sequence are unchanged;
it is not a public Snapshot field or evidence of native input delivery.

`bound_actions.status=complete` proves every current finite binding was
materialized. `truncated` preserves the Snapshot but grants no consumer input
authority or interaction capability. Every public operand must already name a
current visible Referent. Counts, limit and deterministic ordering make loss
auditable. `status=interactive` is valid exactly when the complete projection
is non-empty.

`status=settling` means the Host has proved a bounded native no-input
lifecycle, not an unsupported interaction. This includes combat/room handoffs,
run-state mounting, and the short `menu_or_no_run` gap while a standard run or
the main menu mounts. The last case is capped at ten seconds and cannot hide a
real modal, menu, run owner, or unknown source. The current exact-runtime bound
is twenty seconds; after it expires the state fails
closed as visible unsupported. A settling Snapshot never publishes mutation
authority: its BoundAction catalog and interaction capabilities are empty even
if one control becomes enabled before the complete current UI finishes
mounting. The next ready Snapshot derives a fresh complete catalog.

Internal action-queue activity is not itself a settling condition. During the
local player's play phase, the shipped hand can accept another card, potion or
enabled End Turn input while an earlier action is queued or executing. The Host
therefore derives combat actionability from the current native hand/control
state, excludes cards already moved to the native play queue, and binds every
remaining action to that exact visible UI state.

`reads[]` advertises all bounded, non-authorizing information reads. Consumers
send the opaque `read_id`; C rejects stale snapshots and arbitrary fields.
Interactive consumers may read lazily. Memoryless consumers may prefetch and
aggregate selected advertised reads, but every result must retain the same
snapshot, runtime and environment identity; that aggregation is a downstream
projection, not a different C ontology.

## Opt-in ordinary reward input profile (source candidate)

`ordinary-reward-page-v1` is a separate, default-off input profile for ready,
complete ordinary `reward_claim` and `card_reward_selection` pages only. A client
requests it with `input_profile=ordinary-reward-page-v1` on capabilities and
Snapshot GET, includes `input_profile` in Submit, and supplies the same query on
Receipt poll. Default requests and responses remain the legacy `snapshot-1`
contract. Profiled capabilities declare
`sts2.player-environment/ordinary-reward-page-snapshot-1`; profiled Snapshots
carry that top-level schema, `input_profile`, and the current reward surface
schema ending in `-2`. The action and Receipt schemas keep their existing names,
but profiled Receipts carry `input_profile`; their optional immediate successor
must carry the same profiled Snapshot identity. Missing, unknown, changed or
cross-profile selectors fail closed before input or before a polled Receipt is
returned. The SDK exposes separate opt-in methods and strict validators. The
old SDK rejects the new payload, and the distinct top-level Snapshot schema
also makes the current legacy Python input projector reject it.

This first profile advertises and materializes **no Reads**. Its outer page
contains only pending currently visible ordinary reward entries plus current
Proceed state; it does not disclose cards inside an unopened reward. Its entered
inner page contains the current complete visible card list (including card
name, displayed cost and description), current alternatives, and a typed effect for an
exactly bound `return_to_rewards_without_claim` alternative. The effect names
the native alternative's declared behavior, not a delivered action, observed
return or causal successor. The inner page publishes no private native parent
object, cross-page history key, unseen reward group or other group's card list.
Snapshot IDs, request IDs and native safety bindings belong to the delivery
envelope, not semantic model history: a consumer's model input for an unchanged
current logical page must not change merely because that envelope is refreshed.
Profile card description and cost come from the already-rendered current
native card nodes; a hidden energy icon yields an empty displayed cost. Missing
or mismatched card nodes make the whole page unsupported.
An outer Proceed/Skip has separate native semantics and is never inferred to be
reopenable from this inner return category.

Only an exact, complete whole finite menu receives action authority. Linked
reward sets, partial/settling pages, unsupported alternative effects, extra
potion-discard menus and all other page families are `visible_unsupported` with
empty action authority in this profile. There is no consumer-side action
filtering. Legacy and profile views share one observed native-state generation
for stale-token invalidation across interleaved observations, while each view
keeps its own projection identity. This detects observed A→B→A transitions;
it does not assert unobserved causal history. This source candidate has no
Policy Runtime adapter, STPD input, installed game artifact or trained model.

## Opt-in ordinary reward and potion navigation profile (source candidate)

`ordinary-reward-potion-page-v2` is a separate default-off profile with top-level
Snapshot schema `sts2.player-environment/ordinary-reward-potion-page-snapshot-2`
and current surface schema ending in `-3`. It uses the same REST selector on
capabilities, Snapshot GET, Submit and Receipt poll. Its Receipt and immediate
successor must match that selector. Legacy and v1 requests retain their existing
schemas and meanings. The TypeScript SDK has explicit v2 methods and validator;
the optional MCP adapter currently sends no selector and remains legacy-only.

The v2 action scope is the **ordinary current reward page, its entered ordinary
card-reward page, and an exact potion popup opened over that reward owner**.
Within this scope the Host publishes the complete finite current reward and
Proceed choices, currently selectable cards and alternatives, every exactly
clickable potion-holder opener, and every enabled native popup Use, Discard and
Close button. Other top-bar navigation is outside this versioned action scope;
it is not declared illegal in STS2. Any page or button set that cannot be proved
complete is `visible_unsupported` with no action authority. There are no Reads.
The first holder qualification is conservative: a transitioning TopBar, an
unreadable exact holder state, or any occupied potion holder that cannot open
its native popup makes the whole reward page unsupported. This does not imply
the other reward controls are illegal, and this candidate does not yet prove a
fully automated loop through such temporary states.

Opening the popup is a separate native holder click; v2 never uses the legacy
outer-page direct-discard enqueue. Popup Use clicks its current native Use
button once. The game may use the potion immediately or enter native targeting;
target selection is a later page and currently unsupported by this profile.
The `controls` on a popup distinguish Use and Discard even though both project
to public `activate` actions. The public potion slot and control identities
bind the current menu; exact holder/button references remain Host-private.
Submit rechecks the same native owner, holder, potion, slot and control before
input. A failed precheck is `not_delivered`; uncertainty after a native click
uses the existing `unknown` delivery semantics, never an automatic retry.

Only the current native logical page enters a model input. Opaque Snapshot,
request, catalog and native IDs are execution-envelope data; a consumer must
not feed them to a memory model or reconstruct earlier reward groups as current
facts. Popup closing or reward re-entry supplies a fresh current observation,
not a causal `S'` or manually provided history. This source candidate does not
claim a published SDK, upgraded Policy Runtime pin, installed Mod, live v2
delivery, Human coverage, or trained small B.

## Action And Receipt

An action request contains request ID, expected snapshot ID, opaque bound-action
ID and controller lease identity. C rebuilds the interaction, referents and
exact native binding immediately before delivery.

Receipts are `delivered`, `not_delivered` or `unknown`. Delivered proves native input
delivery, not business completion. Unknown delivery never permits automatic
retry. The receipt repeats the public subject/arguments and may include a
Snapshot observed immediately after delivery. The field remains named
`successor` for protocol 1.0.0 compatibility, but it is not causal settlement
or canonical next-decision `S'`; consumers observe later state separately.

## Current Host Scope

The Host is embedded in the real game and reports whether the active Godot
display driver is `live_ui` or `headless`. Process launch/lifecycle, profile
isolation, reset/seed control, save/load, clone/fork, fast-step, scenario
mutation, training rewards and tensors are outside this contract and
repository scope.

## Exclusions

C contains no source authority, SourceContract, business Outcome, hidden state,
arbitrary reflection, coordinate input, model-generated native operand,
strategy, reward or privileged simulator control.

## Native-Page Evidence

`native_pages.v1` is a default-off operator evidence profile, not a consumer
action API. Sessions are snapshot/runtime bound, reserve the current input owner,
suppress mutation, verify open/read/return and expose explicit recovery. They do
not create mutation authority or enter the action ledger. A successful return
requires the exact page close path and restoration of the pre-page input owner
in the same runtime. Opening and closing a real native page may advance the
Snapshot, so `post_snapshot_id` is a fresh successor rather than a promise that
the old token becomes current again. Any prior Snapshot/Read/action token stays
stale.
