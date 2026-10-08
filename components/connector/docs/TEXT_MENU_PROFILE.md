# Text menu profile (source candidate)

`text-menu-v1` is an opt-in Player Environment profile. It retains protocol `1.0.0` and the existing capabilities, Snapshot and action routes, but has distinct Snapshot and action-result schemas. The legacy profile and reward profiles keep their original payloads and native Receipt meaning. This document describes the SDK contract in this source branch; it does not claim an installed or live game implementation.

The current text Snapshot contains the same public current-page facts as its native source, plus `menu.cursor`, `menu.revision`, `menu.native_snapshot_id` and `menu_actions`. It has no `bound_actions` or `reads`. A complete `menu_actions` projection contains every choice at the *current text cursor*; it is not the flattened native catalog for deeper menu pages. The only system navigation is root → information → one of seven fixed information lists, with one-level back. Other player actions remain native leaves.

A `system_navigation` action has `effect_domain=text_menu`. Its applied result changes the text cursor and has `native_delivery=null`: no native input or game Commit is implied. A `native_input` leaf has `effect_domain=native_input` and retains delivered/not-delivered/unknown input semantics. Delivered input is not business completion or canonical causal successor. Both kinds use the same request ID, current Snapshot, controller lease and profile selector; stale IDs and profile mismatches fail closed. Unknown native delivery never permits automatic retry.

TypeScript clients call `textMenuCapabilities`, `observeTextMenu`, `submitTextMenu`, and `textMenuResult`. The strict decoder rejects legacy payloads, missing profile/result fields, illegal system edges, unknown current referents, duplicate IDs, incomplete executable catalogs and system results pretending to deliver native input. Reference and Managed Hosts must each prove their native leaf bindings and fair-player facts; schema compatibility alone is not cross-Host qualification.

## Atomic observation context (opt-in source candidate)

`GET /api/player-environment/text-menu/observation-context` returns exactly
`{schema: "sts2.player-environment/text-menu-observation-context-1", snapshot,
game_continuity_id}`. `snapshot` is the unchanged `text-menu-v1` Snapshot.
`game_continuity_id` is an opaque, process-local scheduling identity for the
current native `RunState` object, or `null` when no run object exists. It is not
a start witness, player-visible fact, model input, action operand, or evidence
of a fresh game. The Reference Host captures the object before the whole
synchronous page build and verifies the same reference afterward on the game
thread under the shared submission gate. A changed reference rejects the
packet with HTTP 409 `run_continuity_changed_during_capture`; no mixed context
is returned. A terminal page can retain its last run object. A new/load run
object gets a different ID, while a return to menu with no run has `null`.

Clients opt in through `observeTextMenuContext` and strictly decode the outer
schema and inner Snapshot. A Host without the route returns 404; clients must
not infer continuity from an ordinary `observeTextMenu` response. The legacy
Snapshot and capabilities schemas are unchanged. Other Hosts must implement
their own native run identity and atomic capture proof before claiming this
route.

The two JSON examples in `sdk/typescript/test/fixtures/text-menu-root.json` and `text-menu-system-result.json` are portable contract fixtures. They are synthetic and do not prove native behavior.

## Opt-in v2 Reference Host source candidate

The SDK also exports strict `text-menu-v2` decoders and synthetic card-selection
fixtures. In this Connector source candidate the Reference Host accepts an
explicit `input_profile=text-menu-v2` on capabilities, Snapshot, action,
result, and atomic observation-context routes. Requests without that selector
retain the v1 behavior and receipts. The v1 bytes and fixed
operand-free navigation grammar remain unchanged. A v2 `system_selection`
has `effect_domain=text_menu`; `select_card` and `select_target` bind a visible
subject on a ready combat page, while `cancel_selection` has no subject or
arguments. A card subject has the current public `card` or `playable_card`
role; a target has a public creature/target role. An explicit `enabled=false`
blocks selection, while a missing enabled fact is not a new legality claim.
The v2 menu
records only staged card/optional target referents. Card-only native leaves
reach confirmation without a fabricated target. A `native_input` leaf keeps
the v1 delivered/not-delivered/unknown semantics; this term does not specify
a Godot device input. A decoded result is not proof of native Commit, a causal
successor, or Human origin.

The SDK checks public referents, cursor/selection shape, complete current
catalogs, and result-to-menu association when the previous Snapshot is
supplied. Only a Host can prove its private complete leaf catalog and
execute-time binding. The Reference Host builds card/target pairs from its
existing native combat catalog, checks them against every current public
playable-card target set, and keeps selected card and target in a private text
cursor. Selection delivers no native input and does not claim a held card in
the game UI. The final `play` leaf uses existing native execute-time
revalidation; delivered input remains distinct from game Commit. An incomplete
pair catalog makes the ready combat text menu unavailable. Synthetic Host
tests verify this source behavior, but do not establish an installed game
runtime, cross-Host equivalence, Human evidence, or training-data admission.
Existing v1 recordings and M2 exports cannot be relabeled as v2 by changing a
schema string: they lack the v2 selection and complete pair witness.

## Semantic interaction versus native input device

The public interaction describes the current game operation: a held card,
target selection, confirmation or cancellation. It does not define separate
mouse and controller model protocols. Device-specific callbacks, Godot objects,
pointer coordinates and input signals belong to the Host's private adapter.
A fast or non-Godot Host can implement the same profile with its own bindings;
it must still prove current-page facts, complete actions, native revalidation
and delivery outcomes. It cannot copy the desktop Host's qualification.

Normalize only facts and effects that are actually equivalent. A mouse-held
card outside its native play zone is not automatically a confirmation-ready
card. In the inspected game, `NMouseCardPlay.StartAsync` checks the play zone
after targeting, and untargeted play also depends on press/release state;
`NControllerCardPlay._Input` instead has explicit confirm/cancel signals.
The current desktop adapter supports controller-held continuations and leaves
mouse-held continuations settling. That is an adapter coverage gap, not a
requirement for models to learn device names, nor a claim that mouse actions
are illegal in the game. No polling or automatic device switch repairs an
already ongoing Human interaction.

Host-private witness bindings correlate the exact card-play operation, card,
and (when applicable) target node with a public menu choice. Annotator records
the actual input mechanism separately as provenance. Evidence validates that
mechanism's allowed public verb; STPD consumes the verified semantic choice,
without reproducing the game's input-method whitelist or placing that metadata
in model text. Begin, confirm, cancel and target confirmation remain input
observations, not proof of card Commit or a causal successor.

## S0 immutable observation reads (additive source candidate)

The `text-menu-v2-sealed-1` read profile retains a complete serialized public
`text-menu-v2` Snapshot. Completeness still means every action at the current
cursor, not a flattened catalog across deeper menus. Capture ID grants no input
authority; `submitTextMenuV2` retains its exact expected snapshot, controller,
fingerprint and native execute-time checks.

Routes below share the existing loopback listener/origin policy. They publish the
same fair-player public observation scope as the existing v2 Snapshot endpoint;
no additional controller or registered-client authority is needed for reads.
Capture handles are unguessable process-local retention references, not credentials.
No private native frame, callback, hidden content or action result enters a capsule.

| Operation | Route / request | Response schema |
| --- | --- | --- |
| Capabilities | `GET /api/player-environment/sealed-observation/capabilities` | `sts2.player-environment/sealed-observation-capabilities-1` |
| ReadCurrent | `GET /api/player-environment/sealed-observation/current?input_profile=text-menu-v2&expected_snapshot_id=OPTIONAL` | `sts2.player-environment/sealed-observation-1` |
| ReadSealed | `GET /api/player-environment/sealed-observation/read?capture_id=ID&cursor=TOKEN&max_bytes=OPTIONAL` | `sts2.player-environment/sealed-observation-chunk-1` |
| Release | `POST /api/player-environment/sealed-observation/release` with `{"capture_id":"ID"}` | `sts2.player-environment/sealed-observation-release-1` |

ReadCurrent calls `ObserveTextMenuV2Context` once and synchronously serializes its
public Snapshot in the same game-main-thread call. The retained byte array is
independent of mutable JSON/native source objects. It does not claim a global
render/world fence, lossless transient exposure history or dirty-based skipped
scans. A new ReadCurrent still performs a new capture. Missing/partial current
state retains its truthful Snapshot status; consumers must gate Model readiness.

The capture envelope contains `schema`, `read_profile`, `input_profile`,
`capture_id`, `source_snapshot_id`, existing `session` (runtime/environment),
`generation_id`, nullable `game_continuity_id`, `captured_at`, `expires_at`,
`total_bytes`, `sha256`, `first_cursor`, and `capture_ordinal`. `generation_id`
is the store's opaque process-local generation, not a native state epoch or a
controller generation. `capture_ordinal` counts only sealed capture attempts,
including failed attempts; it does not count all game/Connector observations.
Continuity and this envelope are scheduling/diagnostic metadata, not Model input.

Capsules are bounded to 8 MiB each, 64 MiB total and 32 objects, with 120-second
monotonic retention. Wall-clock expiration is diagnostic; actual expiry uses the
monotonic deadline. Live capsules are never silently evicted for capacity. Expired
ones may be reclaimed. A stale expected snapshot, capture/serialization failure,
oversize or capacity failure publishes no successful handle. Responses use
`stale_snapshot`/409, `capture_failed`/500, `too_large`/413, `capacity`/429.
Run continuity changes retain `run_continuity_changed_during_capture`/409.

ReadSealed never enters the native/main-thread queue. Chunks contain `schema`,
`capture_id`, `sha256`, byte `offset`, `total_bytes`, `data_base64`, nullable
`next_cursor` and `end`. Cursors bind capsule, digest and offset with a process-local
MAC; clients cannot forge skipped offsets or cross-capsule cursors. Requested
chunk sizes are 1024–1048576 bytes, default 65536. Every nonterminal chunk fills
the requested size; the terminal chunk contains the remaining bytes. A valid
cursor can be replayed or read with a different valid size while the capsule lives.
`expired`/410, `not_found`/404, `cursor_mismatch`/400 and `invalid_limit`/400
fail explicitly. Release returns `released:true` even after expiration/release;
it affects retention only, never controller or submitted-input state.

The SDK `getFullTextMenuV2` verifies identity, contiguous byte offsets, full chunk
coverage, terminality, complete SHA256, fatal UTF-8 decoding, strict v2 schema and
snapshot/session agreement. It returns `{context,capture,serializedSnapshot}`;
`serializedSnapshot` is the exact original public JSON text, whose UTF-8 SHA matches
the capture, rather than a reserialization. It releases the retained capsule in
`finally` without replacing the acquisition error if cleanup fails; TTL bounds
failed cleanup. Direct ReadCurrent/ReadSealed users release their own handles.
Repeated sealed reads neither capture native state nor update Model memory.
A reconstructed settling/partial Snapshot remains available for operational wait;
a Model consumer must require its own complete input/menu scope before inference.

Source, exact-game tests, build, install, loaded identity and actual live repeat-read
validation remain separate. This additive source path does not qualify all scenes,
all characters, the final native-flat relation or Human exposure history.

## Native tip entry/return lifecycle

Signal-opened card/orb/top-bar tips retain the exact visible source and whether
entry used native `FocusEntered` or `MouseEntered`. Returning from that owned tip
emits the corresponding `FocusExited` or `MouseExited` on the same current source
before idempotent removal of the exact tip owner. Direct relic tips are created
without such a signal entry and retain removal-only return.

This pairing follows the native control lifecycle. On the exact supported macOS
`9cb4f1ad` game assembly, card focus is latched: removing the rendered hover tip
alone does not unfocus its holder, so another entry on that same holder cannot
create a new tip. Native hand-card unfocus also clears the hand's focused-holder
and hover-tracker state and refreshes presentation layout. The adapter therefore
uses the declared exit signal; it never patches private focus fields or bypasses
native behavior by directly rebuilding another card's tips.

Changed/dead/hidden sources or changed tip owners fail before exit/removal.
A native callback exception remains an unknown delivery through the existing
executor; there is no automatic retry. Exact-game regression tests establish
source behavior only; same-holder open → return → reopen requires a fresh built,
installed and cold-loaded runtime canary for each new artifact.

## Public subjects for native information choices

Each native information choice now carries a visible public subject. The original
Host-private native control closure still dispatches it; public references neither
construct native operands nor grant action authority. Card choices reuse their
card ID and full current-page facts, so equal titles can retain different
cost/upgrade data. Same-ID hand facts can enrich a sparse referent; contextual hand
membership does not restrict pile/generated/other selector cards and does not
replace their already full page-specific representation. Relic inspect/tip choices reuse the displayed inventory relic ID,
title and counter, with no newly exposed unopened description/preview.

Orb choices use independent current UI-control IDs for occupied and empty slots,
with explicit presentation basis, occupancy and native slot order/count. Occupied
UI controls expose their public model identity/name and only actually visible
amount-label text; they do not replace existing logical orb referents or context.
Hidden labels are explicitly marked hidden; missing required label nodes remain
unresolved. This bounded profile declares only the visible creature `owner`
relation, with no inferred UI-to-logical-model relation. Orb, power and intent
choices carry their current visible creature as an `owner` argument.
Power membership must match its typed native model/owner and the frozen public
status definition/amount. Intent membership uses the exact public creature's
`IntentContainer` child order and the already frozen intent type/value. It reads
no private intent fields and adds no unopened hover body. Slot/intent order is a
native display relation used to bind facts, never a candidate ordinal or operand.

Top-bar choices have separate `topbar_deck`, `topbar_map`, `topbar_floor`,
`topbar_boss`, `topbar_gold` and `topbar_hp` control roles. Their properties contain
only the role and already captured shown HUD values/icons; a boss icon is not
expanded into a hidden boss definition or unopened text. Action labels use these
public subjects/roles. Identical public instances may remain observationally
symmetric; neither labels nor Model features embed opaque IDs to force distinction.

Missing or inconsistent required public bindings explicitly mark completeness
partial and make the menu unavailable. Reward projections merge only the subjects
and owners of appended information choices, not unrelated underlying room objects.
Changes in public leaf or card-play labels now participate in the v2 source
signature and invalidate old selections/action IDs even if the native token stays
unchanged. Wire schema and exact native submission checks remain the existing v2
contract. New source tests do not qualify every native mechanism or old datasets.

Creature-owned tips require an exact current room UI owner, not merely a visible
scene descendant. Native death/removal moves a creature from `CreatureNodes` to
`RemovingCreatureNodes` while its rendered subtree can remain during animation;
intent hover resolves through the current `GetCreatureNode` lookup. Such positively
retired controls are excluded from current information choices. An unregistered
or ambiguous owner with no native retirement fact remains unresolved and marks
required completeness partial; missing public facts for a current owner are never
ignored. No HP inference, blanket settling conversion or guessed timeout supplies
retirement. Queued-for-deletion ancestors also retire their full subtree before
individual descendants disappear. These are native UI ownership/lifetime facts,
not causal successor proof or a new gameplay legality engine.

Orb information sources use the native manager's current navigation roster rather
than all rendered children. `DefaultFocusOwner` anchors that roster; forward
`FocusNeighborLeft` links and inverse right links must form a bounded unique ring
under the same live manager/container. Membership, order and slot count come from
this UI ring independently of the logical OrbQueue. Native `TryEnqueue` adds the
logical orb before awaiting `SmallWait`; `Channel` creates its UI control after
that wait. Input can already be ready during this legitimate difference, so the
previous strict logical/UI equality requirement was invalid and is replaced here.
Evoked orbs can remain as fading scene children after native retirement; they are
excluded only after a valid current ring is established. The exact creature-hitbox
anchor proves no current UI orb controls even if logical capacity differs; one
slot permits both self-links. Broken navigation or missing required UI facts remain
explicitly partial. No private roster reflection, cached historical facts, hidden
hover bodies, logical-value copying or guessed waits supply the missing evidence.

A valid native hover-tip set with both required containers and zero children now
returns typed empty `text_tips`/`card_previews` arrays. Some ordinary cards have no
additional native tips. Missing containers or any malformed, hidden or unready
actual child remain unresolved; no card statistics fabricate a tooltip body.

Incomplete information bindings retain a native capture's already-declared
`settling` status, partial diagnostics and zero action leaves. Other statuses with
the same missing bindings remain `visible_unsupported`; missing facts never infer
settling or authorize Model consumption. This preserves the native startup/readiness
owner and existing bounded Runtime observation behavior without changing Runtime.

The v2-only exposure projection clones combat context and referent properties,
removing unopened orb descriptions, intent titles/descriptions and player/enemy/
companion status descriptions on their declared collection paths. It preserves
public names/types/amounts/order, visible hand-card descriptions and bodies read
from an actually entered native tip surface. Existing legacy producers and v1
inputs are unchanged. Historical captures keep their original exposure identity;
this source correction does not relabel them as conforming new data.

## Native selector confirmation fidelity

Selector request `min_select`/`max_select` and manual-confirm preferences remain
facts about the caller's request. Actual confirmation availability comes from the
exact current visible/enabled native button and is rechecked at delivery; these
request facts do not form another confirmation legality engine.

On exact game assembly `9cb4f1ad`, the combat-pile screen may enable confirmation
below raw minimum after applying its native effective displayed-card count. The
simple screen does not share that clamp: it initially enables zero-minimum
confirmation even in automatic mode, then updates its own button and completion
state after selections. Empty/no-screen and automatic completion remain native
caller outcomes; Connector adds no synthetic confirm/cancel or repeated parent
input. Grid selected-card cleanup remains native-owned.

Upgrade and transform preview confirmation also follow their actual preview
controls, rather than an extra generic raw-minimum veto. Original-card membership,
exact stage/owner checks, native max-selection behavior and preview-cancel versus
whole-selector close distinctions remain intact. These source/test fixes do not
qualify all L35–48 scenarios or substitute for their live, Human or caller gates.
