# Native logical bridge source candidate

Date: 2026-10-08. Class G3: game-native composition, public transport and shared
controller behavior. Base `0b198824e86306b5e6a733e72016e91b6d185044`;
branch `codex/e1-v1-native-bridge`. The original access-lifetime dependency
`2d10f745f02020d641439ff784c85eea9f9589ca` is retained as an ancestor by a normal
merge. An earlier same-content cherry-pick was also retained without rewriting
history; it does not replace the original dependency's review identity.

## Owning fact and composition

The first missing fact was a production connection from current exact native
capture to the already implemented public projector, immutable store and source
publication hub. The bridge supplies that connection rather than another
catalog, legality engine, Recorder or causal tracker.

The game thread captures one coherent `CaptureNativeLogicalFrame`, copies arrays
and deep-clones public JSON values, and retains only the current exact native
closures. The native producer's existing content/relation completeness values
become a typed source certificate. Incomplete sources cannot enter the pure
complete-only projector. Current replies preserve an actual missing reason;
reserved source positions preserve explicit missing outcomes.

Source notices without selected subscribers or an already-acquired Current basis
complete their clock positions without capturing/cloning/encoding a frame. An
existing standalone Current basis still observes native owner re-entry; its reader
lifetime/performance repair remains with the shared lifetime owner.

One bounded serial encoder consumes detached values off the game thread. At
most four pending/active captures are admitted. Projector, store and hub retain
their existing identity, byte, retention and publication authorities. Source
order remains reservation order; an initial attachment reserves N+1 in the same
game-thread turn before freezing its initial values. Encoding failures and
capacity rejection cannot silently remove that position. Source encodings drop
their initial store pin after the hub acquires its independent event pins;
abandoned/late completion likewise releases that owner pin.

Fresh typed fact equality and private binding keys only revalidate the basis of
an existing projector-generated handle. They assign no snapshot or action IDs.
Observed owner A→B→A, focus changes and binding changes invalidate the old basis;
unchanged Current reads retain handles. No native getter runs during immutable
read/catalog/resolve/events/Await access.

The existing controller owns `TryBegin` as the input-start linearization point.
The authority lock is released before native calls, capture or hooks. A Stop
winner prevents a new start; an already-started input retains its actual result
while Stop promptly revokes future starts. Exact lease watches notify outside
the authority lock and observe expiry without a later status query. There is no
second command queue or authority ledger.

The existing cross-profile fingerprint namespace is shared. A pending result
lookup returns HTTP 202 and the existing API error `request_pending`; it cannot
be decoded as a final native result. Terminal results replay exactly. Known
partial stages remain separate from delivery, execution, effects and cancellation.
All result retry policies are `never_automatic`. Legacy `Accepted` is input
acceptance/delivery evidence only; execution/effect/cancel remain unknown.

## Exact native source grounding

The local arm64 `sts2.dll` was rehashed read-only:
`9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`.
Private decompiled files stayed outside the repository. Typed method signatures
are additionally checked against the actual referenced game assembly.

| Implemented seam | Exact native fact | Coverage and limit |
| --- | --- | --- |
| Native Foundation owner-ready event | Existing typed combat/map/game-over ready provider | Sampled; no Recorder active-state dependency and no causal successor proof |
| `NTargetManager.OnNodeHovered(Node)` / `OnNodeUnhovered(Node)` return | Native focus assignment/clear and actual hover signals have returned | Sampled callback observation; a rejected/no-op callback is not promoted to accepted input |
| `NCard.SetPreviewTarget(Creature)` return | A changed target invokes native `UpdateVisuals` before return | Sampled visible-card observation; identical calls may preserve snapshot identity |
| `NInspectCardScreen.UpdateCardDisplay()` return | Native cloned-card presentation and tips have been updated | Sampled entered inspect presentation; no full virtual-list binding claim |
| `NPlayerHand.OnHolderPressed(NCardHolder)` return | Native hand input callback has handled its actual mode and conditions | Sampled native callback observation from either origin; no Human-origin or Commit claim |
| `NGameOverScreen.OpenSummaryScreen(NButton)` return | Native summary animation was started | Sampled terminal-page observation; animation/task completion remains unknown |
| Connector input start | Existing controller admitted the start | Separately named `connector_input_start`; not mislabelled as a game-owned native input callback |
| Initial observation | Same-turn attachment and detached initial frame | `complete_at_seam` bootstrap capability, with explicit missing on capture failure |

Other native exposure families remain explicitly unsupported. The full L01–L64
and G2/V1 denominator remains in the [capture audit](NATIVE_LOGICAL_CAPTURE_L_AUDIT_2026-10-08.md)
and [profile contract](../NATIVE_LOGICAL_PROFILE.md). This source candidate is
not complete native hook coverage, a qualified recording source or G2/V1 approval.

## Required next native publication work

This is a source candidate, not a permanent sampled substitute for the retained
native interaction/history target. Runtime Await after a delivery can stall on
an unhooked owner/catalog change. A later Current poll cannot fill that missed
historical position or satisfy the required ordered exposure prefix.

| Gap / affected obligations | Next bounded typed source inventory | Admission needed before implementation |
| --- | --- | --- |
| Deck/pile entry/return, tips, inspect leave; L01–L19 | `NCardsViewScreen.AfterCapstoneOpened`, `OnReturnButtonPressed(NButton)`, `OnInspectVisibilityChanged`; `NCapstoneContainer` open/close and typed hover-tip create/remove paths | Reread exact entry/return order and delayed visibility; capture at actual entered/withdrawn public fact, not Task completion |
| Potion popup/use/target/cancel; L29–L33 | `NPotionHolder` and `NPotionPopup` actual open/use/discard/remove callbacks, typed target manager transitions | Establish all compound stages and actual popup ownership; existing hand/target hooks do not cover every potion phase |
| Public combat/resources/effects; L20–L28/L34 | Actual `NPlayerHand` mode/selection callbacks, `NCard.UpdateVisuals`, native resource/creature/power/intent view update callbacks | Cover changed actual displayed facts and native catalog withdrawal; do not infer Commit from visual updates |
| Nested selectors and automatic close; L35–L48 | Exact factories/show methods already inventoried for `NSimpleCardSelectScreen`, `NCombatPileCardSelectScreen`, deck select/upgrade/transform/enchant screens; their native selection/confirm/close callbacks | Reread zero/full/auto cases, child readiness, enabled transitions and native close; no inherited Recorder dependency |
| Reward parent/child/reroll/proceed; L50–L54 | Existing typed `NRewardsScreen.ShowScreen`, `NCardRewardSelectionScreen.ShowScreen/RefreshOptions`, native reward controls | Reuse exact Native Foundation/source seams independently of Recorder state; publish parent withdrawal and child/return observation without asserting Commit |
| Shop/event/rest/treasure/modal; L55–L61 | Native shop open/close/purchase/removal, event option/context refresh, rest option refresh, treasure chest/relic/proceed and modal stack changes | Exact signatures and source order remain to be reread; generic interactivity or later polling cannot replace the callback |
| Map/proceed/act and terminal summary/history/menu; L05/L49/L62–L64 | Existing map-proceed ready provider plus actual map close/drawing changes; native game-over summary controls and run-history/timeline/main-menu branches | Source observation is separate from task-complete/Host exit; management authority remains outside the gameplay catalog |

The next packet should first close a bounded real path (deck open→inspect→return,
reward parent→child→return/proceed), prove each source position and native current
catalog, and exercise the actual SDK Await consumer. Then extend the remaining
typed families using the same hub/projector; do not add a generic polling-ready
framework. Method names above are source-grounded routing candidates, not already
implemented coverage or claims that a synchronous return proves a ready page.

Passive Recorder composition additionally needs the existing hub to expose its
atomic reserved/completed boundary clock, and a typed attachment adapter for
attach-before-activation, retained replay, original frozen exports and a finite
close/drain barrier. That accessor must project the existing counters rather than
create another source clock or causal tracker.

## Transport and boundaries

Capabilities use GET. Current, read, catalog, resolve, attach, events, Await,
cancel_wait, detach, renew, retain and release use bounded strict POST JSON in
`/api/player-environment/native-logical/`. New replies preserve explicit nulls
with the native wire codec. Queries reject duplicate/unknown fields. Immutable
catalog lookup resolves the existing retained store by exact catalog reference.
Actions/results reuse `/api/player-environment/actions` and the new input profile.
No observation/read/resolve operation performs a native interaction.

The bridge imports no Annotator and exports no native dispatch references into
sealed content. Recorder integration must consume original frozen bytes/catalogs
and source positions through its separately reviewed passive application seam.
No worker output, source publication index or immediate observation becomes a
Human witness, Commit or causal successor.

## Validation and remaining gates

The final candidate passed **438 Host tests** with no skipped tests or compiler
warnings, **78 Mod JavaScript tests**, **57 legacy SDK tests** plus typecheck/build,
project-system checks and Connector documentation/contract/boundary checks. The
unified Mod passed a clean Release build against the exact assembly with zero
warnings/errors. Logs remain private under `/tmp/e1-native-bridge-*`; the final
handoff records the exact Git identity. Verification is
source/test and exact build only. Tests exercise the actual composition with
synthetic public frames, the production main-thread queue, pure store/projector,
shared controller and request namespace; these are not game execution evidence.

The shared pre-existing registration/request registry still has runtime lifetime;
client-session expiry and the specified cross-profile session ceiling require a
shared Authority repair. There is no performance or memory qualification of the
four-capture admission policy. Full native denominator, passive Recorder wiring,
consumer/data journeys, version/BOM promotion, installation/load, actual game
execution and Human/scientific qualification remain separate.

Rollback is to revert the bridge source change while retaining/reviewing its
explicit pure-core dependency independently. No installed artifact was replaced.
No game launch, install, raw recording, training, provider spend, push or merge
into a shared branch belongs to this candidate.
