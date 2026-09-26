# In-run text STS2 implementation

Source candidate, 2026-09-26. This implements the user's approved in-run menu
design. Installation, live scene coverage, Human data and policy quality need
their own actual results; this document does not declare them passed.

## State and action contract

The public profile is `text-menu-v1`. Its current state is the native logical
page P and the explicit text cursor U. U is presentation state, not learned
memory. A(S) is the complete menu at that cursor. The model scores every item
once and returns aligned scores plus a selected index; it cannot create native
operands. Connector resolves the exact current action, checks the controller and
fresh snapshot, and dispatches the native input. Unknown delivery is retained
and never automatically retried.

Only eight menu kinds are system navigation: `information` and its seven lists
`relic_inspect`, `relic_tips`, `card_tips`, `power_tips`, `intent_tips`,
`orb_tips`, `topbar_tips`. Opening one or going back changes U only. It reports
`native_delivery=null`, not a native Receipt. There is no added hand menu,
potion menu, shop grouping, deck-detail menu, scroll action or pagination.
Native ownership changes reset U; current native facts and bindings are freshly
read even when U is retained.

The wire Snapshot keeps current public facts and referents but replaces legacy
`bound_actions` and `reads` with `menu` and `menu_actions`. It uses the distinct
`sts2.player-environment/text-menu-snapshot-1` and action-result schema. The
[SDK contract](../../components/connector/docs/TEXT_MENU_PROFILE.md) describes
strict decoding and routes. Other Hosts can implement this public contract with
their own native bindings; Godot object references never cross the wire.

## Current scene menu

| Native page or mode | Current choices | Native binding |
| --- | --- | --- |
| Combat, player can act | Begin each eligible hand card, open each usable potion slot popup, End Turn, actual draw/discard/exhaust controls, Information | `NPlayerHand` holder Pressed, native potion/top-bar controls, existing exact End Turn binding |
| Held card with targets | Current native creature targets and the native cancellation path; focus/confirm reflects targeting state | `NControllerCardPlay`, `NTargetManager.OnNodeHovered` and native select input |
| Held untargeted card | Native confirmation or cancellation while that operation still owns input | `NCardPlay`/`NControllerCardPlay` input and `CancelPlayCard` |
| Potion popup / targeting | Actual enabled use/discard/close controls; native eligible creature target or cancel | Exact current popup and `NTargetManager`; unavailable target families stay unsupported |
| Map | Current native travel destinations and existing map controls | Exact map owner/destination binding; no coordinates or hidden future room information |
| Reward list | Each currently visible reward entry, native continue/skip and actual potion controls | Exact `NRewardsScreen` ordinary or linked child controls; unopened card choices are absent |
| Card reward inner page | Current full native card/alternative options and native return/skip when present | Existing unprofiled exact `CardRewardSurfaceReader`; button wording does not define permanent loss |
| Event / rest / treasure / shop | Current native options and controls, including actual selectors opened by them | Existing exact owner-specific bindings, not a synthetic universal confirm |
| Multi-select / nested selector | One select or deselect at a time; native confirm/cancel only when offered | Existing selector owner, exact current chosen set and native cardinality rules; no subset enumeration |
| Deck, pile, map, relic inspection or displayed tips | Current entered page contents and its real return/preview controls | Native opened page/tooltip owner; no background Read collection |
| Settling, unsupported modal or terminal | Public current facts and no invented executable choice | Observe until an actual ready page, or return control; main menu/start/load/abandon/process operations are outside model A(S) |

The implementation resides in Connector's `PlayerEnvironment/TextMenu`.
`NativeTextMenuFrameBuilder` composes current native facts;
`NativeTextMenuCombat`, `NativeTextMenuPotions`, `NativeTextMenuInformation`
and `NativeTextMenuRewardPages` bind the changed native seams. Existing room and
selector bindings remain owned by Connector/Native Foundation.

Card-play grounding was checked against the local game implementation:
holder Pressed → `NPlayerHand.StartCardPlay` → native card-play operation →
target selection/confirmation → `TryPlayCard` and native enqueue. Connector does
not write private target fields or enqueue its own game action. From mouse
presentation, the exact native controller-input manager may switch input mode
before holder activation; it requires the focused game window and rechecks the
holder afterward. This changes actual native presentation, not just U. Human
mouse input can change that mode again. This path still needs live qualification.

## Input and output example

The model receives current page text separately from the complete current
action texts. The state contains actual visible HP, energy, enemies, hand,
selection/targeting state and displayed tips. Objects are expanded inline with
their current public fields; duplicate visible cards remain separate objects.
Unopened deck/pile/relic details and prior visits are not appended to S. Existing
compact serializers keep their old identities.

For a simple combat page, A might be: begin the first Strike, begin Defend,
open the first potion slot, End Turn, open draw pile, open discard pile,
open exhaust pile, Information. The precise menu depends on current native
enabled controls; this example is not a fixed legal-action list.

After beginning Strike, a real native held-card page may expose enemy targeting
and cancellation. Selecting/confirming a current enemy uses its current exact
binding. After delivery, the system observes again. Immediate observation is
not proof that a game transaction settled or a canonical causal successor.

Native IDs, run IDs, cursor revision counters, snapshot IDs and action IDs stay
in transport/lineage sidecars. Text refers to current public objects and their
ordinals. Re-entry follows the same projection rules; it need not preserve raw
IDs or byte-for-byte text when native facts/order changed. Candidate order and
scores are bound by the ordered action-ID digest, never by sorting scores.

## Recording, data, training and execution

1. Runtime records the exact admitted text Snapshot before its decision, then
   distinct navigation/native delivery outcomes, failures and control events.
   It retains its finite budget and Human/Stop recovery. No outcome is promoted
   to a native Commit or Human origin by renaming an event.
2. Finalized runs pass the Evidence verifier before STPD imports them. Imported
   Agent sources retain the immutable evidence parent and exact event reference.
   Synthetic sources remain explicitly synthetic. New Human observation mapping
   is a separate owner boundary; legacy Human records are not silently relabelled.
3. The text source and derived BC view have new schemas. Failed/unknown attempts
   remain in source evidence; only eligible applied choices become positive BC
   rows. Train/dev separation groups whole runs and duplicate public inputs.
   Existing immutable dataset, archive and Gold contracts continue to apply.
4. Token preparation dispatches by serializer identity and keeps every current
   candidate. The existing small B v2 path supports a synthetic CPU engineering
   test through checkpoint/resume, export and standalone scoring. This is not a
   trained competent full-game policy or an experiment about memory quality.
5. `bind_text_menu_export` binds actual exported bytes and representation to
   explicitly supplied environment/support facts. It does not infer that the
   installed game is compatible. The actual TokenPolicyAdapter and Runtime use
   that manifest and the complete ordered menu.

Runtime source candidates bundle the exact compiled SDK and its locked Zod
dependency, with source and byte identities. Temporary package installation must
exercise both legacy and text routes. No unpublished release URL is fabricated;
legacy installed consumer pins and old artifacts are retained.

## Verification boundaries

Use deterministic session/executor/SDK regressions, exact-game Host build/tests,
Runtime recovery and installed-package checks, Evidence import negatives and the
small synthetic B export path. Then inspect real captured JSON through the strict
SDK and model projection before attempting native actions. Finally verify scene
transitions and safe control recovery against an exact loaded candidate.

A source menu adapter does not prove that every game version or uncommon target
type is supported. At this source stage, non-creature potion targets and tooltip
card-preview children fail closed. Full-run quality, learned memory, new Human
training admission and broader research training are not inferred from these
engineering checks. Actual receipts belong in the candidate PR and dated evidence.
