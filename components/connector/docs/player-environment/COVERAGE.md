# Player Environment Coverage

## Interaction

LiveHost readers cover menu/run entry, combat cards/potions/end turn, map,
event, rewards/card rewards, shop, rest, treasure, generated choices, combat
hand/piles, upgrade/removal/transform/enchant/bundle selectors and game over.

Visible state is explicitly projected before finite action projection; native
owner/slot/control IDs do not enter public Surface facts. Referents are derived
only from those facts. A complete bound-action
catalog expresses exact public subject/argument combinations while native
objects remain Host-local. Unknown owner or target fails closed. Unknown
business provenance alone does not suppress an exact visible native control.
In combat, queue occupancy and a currently executing card or potion effect do
not suppress the Surface when the shipped hand still accepts input. Cards
already moved from `NPlayerHand.ActiveHolders` to the native play queue are
excluded, and End Turn is published only while its real native button remains
enabled. This preserves the game's own queueing behavior without creating a
second legality model.
The opt-in text menu publishes a mouse-held card operation only for
single-enemy targeting while the current `NTargetManager` retains the exact
`NMouseCardPlay` exit predicate and its targeting signal connection. Its
complete menu uses native focus, target selection and cancellation paths;
selection delivery does not prove the later mouse play-zone check or card
Commit. Missing private owner binding, any-ally targeting and untargeted
mouse drag/release remain settling with no partial cancel-only catalog.
The no-owner frame between a delivered standard-run entry and the mounted run
is a bounded `settling` lifecycle, not a transient unsupported Surface. A real
unknown owner remains fail-closed. The exact event-room model also settles while
its native room node mounts; this does not authorize an event option before the
real visible controls exist. Card rewards likewise remain settling while their
visible card holders are mounting but not yet clickable; an already-enabled
Skip control cannot prematurely turn that partial action set into authority.

## Information

Implemented:

- persistent run/player summary;
- tagged current interaction content;
- visible referents and directly observed state;
- `run_deck`, `combat_piles`, `shop_catalog` and `surface_card` reads;
- default-off native-page evidence for run deck, combat draw/discard/exhaust
  piles and shop catalog.

The exact `v0.111.0/41cef1ea` assembly exposes only `HoverTip` and
`CardHoverTip` as concrete `IHoverTip` implementations; both are typed and an
exact-game test rejects subtype drift. Supported bounded lists/grids project
their complete player-reachable logical collections without requiring scroll
gestures.

Partial/unsupported: current keyboard/controller focus, generic hover/scroll
gestures, future unknown tooltip subtypes and native pages outside the fixed
profile.
See the repository [Player Environment Information Closure](../INFORMATION_CLOSURE.md).

## Evidence

Source `e065102...` has 130 exact-game Host tests plus portable SDK, package,
contract, boundary, CLI, Python and documentation checks. Its reproducible
`c1877f1a.../64765ea1...` artifact was built, installed and cold-loaded.
Exact runtime evidence proves card-reward incomplete-catalog settling with no
authority and the subsequent complete four-action catalog. Two shipped
Reference terminal journeys exercised the same artifact with zero unknown.
This is named operational coverage, not exhaustive interaction qualification.

The later native-human recorder audit on source `9a929cc...` proved exact
process-local mapping for 60 accepted combat inputs and exposed that 22
additional accepted inputs occurred while the old Host incorrectly reported
queue-driven settling. Current source removes that non-native gate and filters
the visible hand by real active holders. See the
[queue-authority audit](../evidence/NATIVE_HUMAN_RECORDER_QUEUE_AUTHORITY_AUDIT_2026-08-23.md).
The correction remains pending exact-runtime evidence on its own artifact.

## Unsupported

There is no arbitrary click/reflection, visual computer-use fallback, Headless
process lifecycle or profile isolation, Training authority, hidden-state
projection or arbitrary-version/Mod compatibility claim. Retired V2/V3
protocols are not fallbacks.

### Native potion popup (PR25 candidate)

`potion_popup` observes the exact visible top-bar popup independently of the
underlying room. Enabled native use/discard buttons and native close form its
finite catalog. Delivery revalidates popup, potion, slot and enabled control.
Opening use can initiate native targeting; delivered input is not potion-effect
settlement. Source/exact-game coverage requires a fresh Human runtime canary.
