# Player Environment Protocol

Source protocol: `1.0.0`

## Endpoints

```text
GET  /api/player-environment/capabilities
GET  /api/player-environment/snapshot
GET  /api/player-environment/reads/{read_id}?expected_snapshot_id=...
POST /api/player-environment/clients/register
GET  /api/player-environment/controller
POST /api/player-environment/controller/acquire|renew|release
POST /api/player-environment/actions
GET  /api/player-environment/actions/{request_id}
POST /api/player-environment/evidence/native-pages/sessions
GET  /api/player-environment/evidence/native-pages/sessions/{session_id}
POST /api/player-environment/evidence/native-pages/sessions/{session_id}/return|recover
```

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
