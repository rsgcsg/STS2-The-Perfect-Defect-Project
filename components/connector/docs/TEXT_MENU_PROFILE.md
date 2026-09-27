# Text menu profile (source candidate)

`text-menu-v1` is an opt-in Player Environment profile. It retains protocol `1.0.0` and the existing capabilities, Snapshot and action routes, but has distinct Snapshot and action-result schemas. The legacy profile and reward profiles keep their original payloads and native Receipt meaning. This document describes the SDK contract in this source branch; it does not claim an installed or live game implementation.

The current text Snapshot contains the same public current-page facts as its native source, plus `menu.cursor`, `menu.revision`, `menu.native_snapshot_id` and `menu_actions`. It has no `bound_actions` or `reads`. A complete `menu_actions` projection contains every choice at the *current text cursor*; it is not the flattened native catalog for deeper menu pages. The only system navigation is root → information → one of seven fixed information lists, with one-level back. Other player actions remain native leaves.

A `system_navigation` action has `effect_domain=text_menu`. Its applied result changes the text cursor and has `native_delivery=null`: no native input or game Commit is implied. A `native_input` leaf has `effect_domain=native_input` and retains delivered/not-delivered/unknown input semantics. Delivered input is not business completion or canonical causal successor. Both kinds use the same request ID, current Snapshot, controller lease and profile selector; stale IDs and profile mismatches fail closed. Unknown native delivery never permits automatic retry.

TypeScript clients call `textMenuCapabilities`, `observeTextMenu`, `submitTextMenu`, and `textMenuResult`. The strict decoder rejects legacy payloads, missing profile/result fields, illegal system edges, unknown current referents, duplicate IDs, incomplete executable catalogs and system results pretending to deliver native input. Reference and Managed Hosts must each prove their native leaf bindings and fair-player facts; schema compatibility alone is not cross-Host qualification.

The two JSON examples in `sdk/typescript/test/fixtures/text-menu-root.json` and `text-menu-system-result.json` are portable contract fixtures. They are synthetic and do not prove native behavior.

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
