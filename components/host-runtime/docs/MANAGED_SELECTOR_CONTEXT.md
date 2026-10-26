# Bounded Managed choose and combat selectors

This is an additive legacy Managed/text-menu capability. It does not implement
full P1, C1 capture, native UI parity, Human evidence or research admission.

The pinned native `FromChooseACardScreen` and innermost five-argument
`FromCombatPile` command supply one synchronous, single-use callback frame.
The frame carries the real player, selector, card objects and command preferences;
combat additionally retains the exact pile and native filter. It closes when the
native method returns its Task. Unknown, consumed, overlapping or mismatched frames
never borrow another callback. The native command retains automatic paths that
finish before a callback. Unscoped callbacks do not gain a Host auto-choice.

Choose-card input uses the native canSkip flag independently of callback min0.
It completes on one original offered card or an explicitly permitted skip.
Combat support is bounded to Draw/Discard/Exhaust, min=max1, with ordinary click
completion or real manual select/deselect/confirm. Unsupported preferences and
other selector origins remain unavailable. Deck-enchantment is not admitted.

The producer renders every offered card through the game's Normal preview and
GetDescriptionForPile APIs in the actual owned context. A typed transaction saves
and restores arbitrary prior preview/enchanted values and EnergyVar.ColorPrefix;
clearing to base is not restoration. Unknown variable/context, unresolved text,
inline images without a qualified text projection, affliction details without a
qualified adapter and incomplete hover closure make the whole catalog unavailable.
Native card hover previews recurse with explicit cycle/depth failure. No fallback
localization template or manual variable substitution becomes complete.

Every observation reads the current post-parent combat HUD from the same native
player/combat owner. It does not copy a parent snapshot, pump native actions or
advertise a free combat turn. Health numbers follow native display visibility.
A native intent may have no numeric label (an empty string); its type and hover
title/description remain required. Missing/null labels are not accepted as empty.
Draw candidates are already native-revealed; projection sorts public card facts
while preserving original private bindings and distinct equal card instances.
It never reveals the unoffered draw remainder, true draw order or RNG.

A complete surface declares selector_profile managed-command-selector-1,
selector_kind choose_card/combat_pile, display_context,
all cards and their native-admitted flags, exact selected public entity IDs,
full bounds/control flags and current combat context. The strict SDK checks
card/referent/selection/catalog closure. The SDK source is the canonical validator. The Host ships its generated, self-contained
ESM artifact, checked by the root boundary gate, without requiring an unpublished
SDK export or a runtime sibling-source import. Regenerate with
`node tools/generate-managed-selector-validator.mjs` after canonical changes.
Text-menu accepts only that bounded
complete shape; historical untyped card_selection remains unsupported.

Terminal input revalidates the current selector/run/player, complete original
candidate domain, pile/filter and strict rendered display before returning exact
original objects to the suspended callback. A changed domain rejects even if the
requested card itself remains present. Lifecycle abort cancels the pending Task;
it is not an empty successful native skip. Parent block/damage/potion consumption
already applied before the selector is retained. Unknown delivery fences mutation
and never retries the child or replays the parent.

Validation for this source candidate consists of portable projection/strict SDK/
text-menu tests, exact native model/renderer/core fixtures and an exact build.
The native fixtures invoke the actual prefix/callback producer directly, without
RunManager, a game loop, gameplay effects or GUI. They establish those bounded
mechanisms, not execution of a live suspended native command or whole-game parity.
A later explicitly allocated runtime canary must verify exact hook ordering and
native continuation. No installed, loaded, journey, Human or strategy qualification
is transferred from the previous candidate.

The frozen macOS artifact is produced by normal GitHub candidate preparation,
including SourceLink metadata. An earlier local-remote build had identical tracked
source and method IL but different module/debug identities; it is not the admitted
normal-prepare artifact. Exact SHA/MVID checks remain mandatory.

## Current bundle, reward and combat-pile repair (2026-10-26)

The absent-UI bundle presentation binds the original Player, run, offered bundle
objects and suspended native Task. Select opens the existing pack preview;
cancel retains that Task and exact roster; confirm alone completes it. Replaced
owners, Tasks and changed rosters are rejected. The original raw bundle route is
retained, with invalid indexes rejected rather than redirected to the first pack.

Reward candidates retain the original native public details including upgrade
previews. Empty reward proceed uses the same native eligibility fact as Reference;
its nonterminal dispatch remains the existing skip-rewards operation. Combat
selector powers use the shared native visible-text projection, piles retain their
original-card identity across views, and combat-pile public facts include the
current displayed multiplicity and manual/nonmanual transition classification.
None of these changes alters the P1 contract or Reference implementation.

A preserved current Reference run was replayed through 88 public observation and
catalog points: 87 complete graph matches, one diagnostic choice, matching natural
terminal and no unknown delivery. The diagnostic choice is never an equality pass.
The remaining pack-selection mismatch is the opaque-ID ordering relationship in
`selectable_bundle_entity_ids`; the comparison rule is unchanged and awaits the
user's decision. Original initial-progress bytes were not retained by that
Reference run, so whole-condition equivalence is also unproved. A later native
fixture covers both combat transition classifications and bundle cancel/stale
ownership; that final candidate was not granted the earlier replay's identity.

This is bounded source/build/native-fixture and replay evidence, not all-surface
support, full result-object equality, installed/Human evidence, model performance
or permission to pool Host results. Further experiments stopped at the task's
15GiB free-space limit by the user's explicit choice.

## Native event visual calls in the F2 candidate

The exact `PunchOff.Nab` continuation adds its native Injury before calling
`NGame.Instance.ScreenShakeTrauma`. The absent Managed scene made that pure
visual call fail and left native delivery unknown. The current candidate remaps
that call to a TestMode-only null-scene presentation shim; the event method,
CardPileCmd, RewardsCmd, RNG, original Task and event completion remain native.

An exact assembly call-site inventory found the same gameplay dependency in both
`Amalgamator` combination continuations. Their four calls use the same shim. Each
native async owner and expected call count is checked; UI dispatcher/settings
calls are outside this adapter. A non-TestMode null call still fails normally.
Native component fixtures cover PunchOff's curse/reward/completion and both
Amalgamator branches' original two-card selection/removal and one-card creation.
The prior canary unknown is preserved and never replayed as a recovered slot.
These fixtures do not qualify a new complete model game or broader replay corpus.
