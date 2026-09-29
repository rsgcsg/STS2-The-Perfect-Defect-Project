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

## SDK-only v2 contract candidate

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
