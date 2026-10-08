# E1 native logical capture: L01–L64 source audit

Date: 2026-10-08. Packet: E1.2 native capture completeness. Engineering class G3.
Base: `ecea8a471e16c603dde73c64a27d92c6f428f489`, dependent topic
`codex/e1-v1-native-completeness`. Contract basis is
the repository-root `docs/BASELINE_V1_SPEC.zh-CN.md`,
`docs/plans/BASELINE_G2_V1_EXECUTION_2026-10-08.md`
and [native-logical-v1](../NATIVE_LOGICAL_PROFILE.md).

The actual installed arm64 `sts2.dll` was hashed read-only and matches
`9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`.
Exact decompilation inspected targeting, controller/mouse card play, hand,
potion holder, Peek, creature, Star counter/card display, deck/grid/cards view
and game-over source. Decompiled game files remain private temporary files;
none is included here. No game launch, install, gameplay, training or provider
operation belongs to this packet.

This table inventories the entire required denominator. **Existing source is
not native runtime qualification.** Rows without an exact-source reread are
explicitly pending; no row inherits S0 or another artifact's evidence.
Publication hooks, sealed transport and consumer/record/data qualification are
separate packets and remain required. All rows need the new profile's actual
source/test/build/runtime/record/data/Agent path before full G2/V1 acceptance.

## Owning defects and repair seams

The first owning defects were capture policy: clearing every leaf when the run
is no longer in progress, clearing information leaves during held operations,
passing noncombat relations through the old 512 projection, and omitting the
actual focused target even if its preview text equals another target's text.
The separate `CaptureNativeLogicalFrame` entry preserves legacy text sessions
and their virtual navigation/staging. It uses the existing exact-native leaf
bindings and shared visibility sanitizers, admits up to 65,536 actions, and
reports capacity failure rather than accepting a complete prefix. The public
core still owns byte accounting, sealing, catalogs, handles, publication and
submission admission.

Exact `NTargetManager.OnNodeUnhovered` clears its current hover and signals the
card-play preview reset; Connector reads the exact current hover but never
writes it. New-profile target focus/unfocus facts are additive. Native
`NHoverTipSet.shouldBlockHoverTips` prevents offering new tooltip entries while
native targeting blocks them. Enabled deck/pile/map entries survive the held
capture. Tips are nonmodal in this profile and do not erase its underlying
native operation. New potion/body/resource tips use their actual native signals.
Generic Peek uses the exact enabled button and native `ForceClick`, with exact
owner/state revalidation. Actual rendered Star/enchantment values remain separate
from logical values; display mismatch supplies no readiness or effect claim.

Compound begin/focus/confirm/use inputs retain known delivered stages and report
partial or unknown delivery after an earlier native input. The additive
`NativeInputResult` stage metadata contains only enumerated stage kinds and
input-boundary evidence, never native operands or Commit/effect proof. Existing
legacy result mappers require the coordinated root repair to map partial to
unknown where their wire cannot express partial.

## E1.2 logical-list follow-up source candidate

The follow-up uses explicitly approved dependent base
`05985976b1fbd9242a9363be91f80033d97d89bc`, branch
`codex/e1-v1-logical-grid`. [The fixed typed adapter](../NATIVE_LOGICAL_GRID_ADAPTER.md)
records six concrete native callbacks, full native grid membership/order,
JSON-ignored exact source proofs, Peek and inspection policies, and unresolved
noninterference/conditional-capability gaps. The table below now distinguishes
this source implementation from its still-pending runtime qualification.

## Complete denominator

References below name owning source families, not qualified support promises.
`Information` means `NativeTextMenuInformation`; `Capture` means the separate
native logical capture adapter; `Combat`/`Potions` mean their text native helpers.
`Selectors` means typed `NativeUi` selector adapters and exact surface readers.

| ID | Existing source / exact native finding | Capture repair or remaining gap |
| --- | --- | --- |
| L01 | Information deck button and complete run-deck Read | Full native deck grid/order and per-model inspect bindings added; return/sort controls preserved; runtime pending |
| L02 | Information draw-pile button/Read | Full entered public multiset; true draw order excluded; registry identity audit separate |
| L03 | Information discard-pile button/Read | Entered pile identity/return retained; inspection coverage pending |
| L04 | Information exhaust-pile button/Read | Entered pile identity/return retained; inspection coverage pending |
| L05 | Information exact map/back controls | Retained native open/back/travel; map drawing-mode audit pending |
| L06 | Information actual card-holder focus signals | Native actual holder tips added for current selector scope; no offscreen holder fabricated; full mechanism runtime pending |
| L07 | Information deck/bundle native inspect controls | Full deck and five valid selector request-list inspect bindings added; combat-pile empty native inspect list requires capability canary |
| L08 | Information exact inspect Upgrade tickbox | Native toggles retained; offscreen requested upgrade presentation is explicit partial until safe native read path exists |
| L09 | Information inspect left/right buttons | Deck inspector uses full sorted logical list; selector uses exact native request-list order; no consumer index |
| L10 | Information relic focus/show/remove | Existing native tips; retained with actual rendered facts |
| L11 | Information relic inspect/arrows/close | Existing exact controls; full inventory/native conditions runtime pending |
| L12 | NPotionHolder.OnFocus/OnUnfocus show/remove tooltip independently of popup | New-profile potion focus leaf and native return; native blocking respected |
| L13 | Information NPower/NIntent native hover | Existing exact owner-bound entries; native retirement checks retained |
| L14 | Information orb controls; NStarCounter.MouseEntered shows Star tip | Visible Star tip and actual counter label added; other resource families audit pending |
| L15 | NCreature.Hitbox focus shows Entity.HoverTips separately from power/intent | Confirmed native capability, body-tip entry added; actual distinct content runtime pending |
| L16 | Information topbar native focus/hover | Existing HP/gold/floor/Boss/map/deck entries retained |
| L17 | NPeekButton.OnRelease toggles SetPeeking | Same grid task/roster/selection survives hidden holders; choice/bundle Peek returns to same child; runtime pending |
| L18 | Information actual rendered text/card tips | Existing rendered tips retained; new current card Star/enchantment presentation added |
| L19 | Native focus exit removes tips; creature changes refresh them | Clear-tip native action retained; typed publication trigger packet pending |
| L20 | Combat holder Pressed starts actual controller NCardPlay | Existing native Begin retained; compound mode-input stages added |
| L21 | NTargetManager.OnNodeHovered emits CreatureHovered and preview changes | Exact public focus identity added alongside native preview |
| L22 | Same native focus method supports another valid target | Focus identity distinguishes equal preview text; native switching/runtime pending |
| L23 | NTargetManager.OnNodeUnhovered emits unhover and clears preview | Exact owner-bound native unfocus leaf added |
| L24 | Combat actual manager select input | Existing direct focus+confirm, now known-stage partial/unknown accounting |
| L25 | Controller play has native no-single-target confirmation | Existing exact controller confirm retained; mouse release condition still unimplemented |
| L26 | NCardPlay.CancelPlayCard; manager cancel input | Existing native cancel retained; actual cancellation/execution outcomes runtime pending |
| L27 | Native topbar/pile controls may remain enabled during held operation | Information leaves retained instead of unconditional clear; new tips blocked when native blocks them |
| L28 | Combat exact end-turn binding | Retained; source/readiness/native runtime pending |
| L29 | NPotionHolder.ForceClick opens actual popup | Existing enabled opener retained |
| L30 | Actual popup Use control | Retained; controller-mode+use stage accounting added |
| L31 | Native potion manager supports current target focus/unhover/select/cancel | Independent focus/unfocus and public focus added; exact predicate/holder/model/slot probe captures native-origin targeting; previews/runtime pending |
| L32 | Actual popup Discard control | Existing binding retained; runtime confirmation conditions pending |
| L33 | Actual popup Remove path | Existing native close retained |
| L34 | Native actions/resources/turns have public changing facts | Capture values only; typed eager publication/hooks/queue cancellation separate required packet |
| L35 | Hand selector source selects/deselects/replaces native holder | Existing source; enabled/min semantics belong to E1.1; exact-source reread pending |
| L36 | Hand selector native confirm control | Existing source; no new confirmation rule; actual auto-complete cases pending |
| L37 | NativeSimpleCardSelection | Full native grid models feed concrete native callback; existing buttons own zero/min/manual semantics; runtime pending |
| L38 | NativeGeneratedCardChoice | Existing native selection/skip; generic Peek added; opening-ready audit pending |
| L39 | NativeCombatPileSelection | Full filtered/sorted grid callback binding added; native displayed-count completion preserved; inspect capability canary pending |
| L40 | NativeDeckCardSelection | Full current qualified grid model bindings added without reapplying native filter or fabricating holders |
| L41 | NativeDeckCardSelection preview confirm/cancel | Exact current preview/confirm/cancel native controls retained with task/stage revalidation; runtime pending |
| L42 | NativeDeckUpgradeSelection | Full qualified grid model callbacks/control binding added; actual preview text captured without future factory calls; runtime pending |
| L43 | NativeDeckTransformSelection | Full qualified grid model callbacks/control binding added; actual preview text captured without future factory calls; runtime pending |
| L44 | DeckEnchantSelectionSurfaceReader | Full qualified grid model callbacks/control binding added; actual preview text captured without future factory calls; runtime pending |
| L45 | CardBundleSelectionSurfaceReader + NativeTextMenuBundle | Existing native bundle controls retained; Peek no longer loses same child/selection; full bundle runtime pending |
| L46 | NativeBossRelicSelection | Existing typed relic/skip source; exact applicable native conditions pending |
| L47 | ActiveInputResolver and child typed selector owners | Exact selector task/owner binding added; multilayer occurrence/child-ready publication remains separate pending |
| L48 | Selector native zero/full/automatic/close behavior | E1.1/source conditions and actual transient publication remain pending |
| L49 | MapNavigationSurfaceReader | Existing exact destination bindings; drawing-mode native capability still to audit |
| L50 | RewardClaim + NativeTextMenuRewardPages | Existing exact rewards; new full projection avoids old 512 cap; teacher packet independent |
| L51 | NativeTextMenuRewardPages linked groups | Existing current child bindings; exact exclusivity/reentry full journey pending |
| L52 | CardRewardSurfaceReader | Existing complete current card source; full new-profile/data qualification pending |
| L53 | Card reward alternative/skip/reroll source | Existing source; distinction between return/final abandon and all alternatives pending |
| L54 | RewardClaim proceed source | Existing native source; exact enabled/final abandon and Agent progression pending |
| L55 | ShopInventorySurfaceReader and shop open/close/proceed source | Existing source; full native condition/journey reread pending |
| L56 | Shop native purchase bindings | Existing native affordability/revalidation; full new relation untruncated below announced bound |
| L57 | Shop removal + deck child selector | Existing source; full logical nested selector coverage pending |
| L58 | EventOptionSurface + Event surface readers | Existing source; every built-in special layout/native branch pending |
| L59 | NativeRestSite | Existing native dynamic options source; full conditions/readiness pending |
| L60 | TreasureRoomSurfaceReader | Existing exact chest/relic/proceed source; prior reward/control conditions pending |
| L61 | Tutorial surface reader | Existing source; default managed disable is separate, unexpected/tutorial profile pending |
| L62 | Room/proceed/act source | Existing source; cross-act/second-Boss readiness and typed publication pending |
| L63 | Exact GameOver reader and native Continue/MainMenu stages | New capture no longer clears terminal controls; actual rendered summary text added; view-run/timeline/full terminal journey pending |
| L64 | Management/task stop authority, not default policy menu | Remains management-only; native abandon confirmation source/result qualification pending |

## Virtual list constraint

Exact `NCardGrid` keeps logical cards in `_cards` but allocates a sliding window
of `_cardRows`. `GetCardHolder(model)` and `CurrentlyDisplayedCardHolders` only
address that allocated window. The prior information adapter could not bind every logical member. The follow-up
now reads the exact full native grid roster, invokes six declared game-owned
model callbacks, and provides full deck/request-list inspection where the native
policy is valid. No fabricated holder or client index is introduced. Conditional
combat-pile inspection remains unresolved; native source alone does not replace
its required capability canary.

`NCardsViewScreen.ShowCardDetail` is protected and also manages back-button and
VisibilityChanged return wiring. Its native inspect list is
`_grid.CurrentlyDisplayedCards`, not the whole logical list. Public
`NInspectCardScreen.Open` accepts game-owned models/list. The follow-up admits the
full entered deck grid list with exact membership, both-mode native permission
and native return policy. Selector inspection preserves its actual request-list
order and mouse-only policy; combat-pile's empty request list is not replaced. New-profile capture checks full native grid/deck source proofs against its
published entered-list content and exact private action bindings. Without that
proof it still compares the source roster to the allocated binding window. Missing content and missing action
bindings have distinct partial reasons; a complete deck Read cannot repair a
missing full inspect relation. Unsupported derived grid source bindings fail
partial. Legacy profiles retain their prior behavior.

No protected invocation, arbitrary reflection,
coordinate/index mutation, fabricated holder or consumer-created card operand was
added to close this gap. The full requirement remains active.

## Native-origin potion targeting constraint

Exact `NPotionHolder.TargetNode` passes instance-bound `ShouldCancelTargeting` to
`NTargetManager.StartTargeting`. The legacy potion helper tracks only targeting
entered through its own popup dispatch. New-profile capture now reads the exact
current manager predicate, requires its fixed native method and holder target,
then checks visible/live holder, local owner, belt membership, slot and potion
model identity. Dispatch revalidates that immutable private binding including the
same predicate instance. The probe never invokes the predicate or writes native
state. Wrong method/holder, replaced model/slot, absent method and retired source
have negative fixture coverage; native runtime capture remains unqualified.

## Validation and non-claims

Focused tests cover the optional full finite projection, unchanged legacy cap,
capacity failure without a dispatchable prefix, same-frame information leaves,
logical/displayed resource separation and frozen compound-input stages. Synthetic
10,000-member membership checks are protocol/source tests, not a real-game load
or timing benchmark. Exact compilation/test results belong to the packet handoff
and reviewed head; this audit does not claim a loaded artifact, eager complete
publication, full L coverage, Human origin, consumer learning or G2/V1 approval.
