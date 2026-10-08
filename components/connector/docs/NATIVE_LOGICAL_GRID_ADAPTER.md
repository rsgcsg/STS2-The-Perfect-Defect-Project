# Native logical grid adapter: E1.2 source candidate

Basis: full G2/V1, `native-logical-v1`, exact game arm64 assembly
`9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4`.
Dependent base: `05985976b1fbd9242a9363be91f80033d97d89bc`.
Engineering class G3; source/test/build are distinct from loaded/runtime/Human
qualification. This candidate does not qualify all L requirements or G2/V1.

## Accepted native input seam

Exact decompilation found one native chain for six grid selectors:
actual holder Pressed -> grid HolderPressed -> source-owned closure -> the
concrete screen's `OnCardClicked(CardModel)`. The concrete callback owns native
selection, deselection, highlighting, preview entry, confirmation visibility and
automatic completion. Grid highlight/unhighlight stores logical model references
and tolerates a model with no currently allocated holder.

The lead explicitly admitted a bounded compiled `UnsafeAccessor` seam for these
six declared callbacks: `NDeckCardSelectScreen`, `NDeckUpgradeSelectScreen`,
`NDeckTransformSelectScreen`, `NDeckEnchantSelectScreen`,
`NSimpleCardSelectScreen`, `NCombatPileCardSelectScreen`.
This replaces the earlier packet's blanket protected-invocation prohibition only
at these source-grounded inputs. There is no artifact/client-selected member,
type, signature, reflection Invoke, fabricated holder, client index or coordinate.
The exact current game-owned model is passed to the actual native callback.
Unknown classes/member drift fail closed.

[Microsoft's .NET 9 documentation](https://learn.microsoft.com/en-us/dotnet/api/system.runtime.compilerservices.unsafeaccessorattribute?view=net-9.0)
defines the first accessor argument as the declared lookup type and states that
lookup does not walk its hierarchy. Tests also verify runtime virtual dispatch:
a matching base virtual slot dispatches its override; an inherited nonvirtual
member absent from the declared lookup type fails. Production still uses each
concrete declared override to keep the permitted surface set closed.

## Full roster, controls and current binding

The current plain native grid's `_cards` supplies the qualified full public list
and its actual sorting order. Original request lists, full deck, pile contents or
consumer filters cannot replace it. In particular the combat-pile native filter
and public draw sorting have already run before this grid list is captured.
No native filter is reinvoked and no hidden draw order is projected.

Capture freezes owner/grid/completion-task references, roster/selected references,
request facts, stage, upgrade-view state and exact controls. Dispatch revalidates
all those bindings. Selecting/deselecting invokes the native callback; preview,
confirm, cancel and toggle use actual current enabled native buttons. Native raw
min/max/manual fields are facts, not another confirmation rule. The combat-pile
callback's own displayed-count completion behavior remains game-owned.

The plain native holder's pre-callback clickability and enabled guard is preserved
when that holder exists. An offscreen member uses the admitted logical input
seam; it is not labelled an actual displayed holder. Private models, tasks and
proofs never enter wire data. The full-membership proof is JSON-ignored, and the
completeness gate independently checks frozen public membership and exact action
subjects. Legacy profiles continue using their existing source path.

The qualified binder replaces only the corresponding legacy selector/window
omissions for the same native source type or screen identity. Inherited persistent
HUD, shared information bindings, page/entity/catalog consistency and unexplained
failure status remain required. A selector proof cannot promote those omissions
to complete input. Final native capture closes every action leaf, including Peek,
when any required scope remains partial. A stable identity, binding or capacity
failure reports partial/unsupported instead of inventing settling; source-proved
transient and completed states retain their own readiness.

## Peek and inspection

Peek keeps the same native child owner, task, roster and current selection while
native targets hide the grid. The new capture preserves that state, the actually
revealed battlefield and the actual return button; it does not invent selection
while hidden. Current accessible card/battlefield tips are bound to the exact
selector scope. Choice/bundle Peek keeps the same actual child and public parent
selection; it does not aggregate unopened bundle contents.

Five grid selectors' native inspection uses their source request-list order and
mouse-only policy, through the existing public `NInspectCardScreen.Open`. The
index is computed privately from the exact model reference. Combat-pile source
sets that request list empty while its actual grid is populated; this candidate
does not repair that native path by inventing an inspector list. Whether that
conditional native capability actually works requires an exact runtime canary.

The entered deck view uses its complete native sorted grid list for inspection,
per the admitted logical-list extension. It preserves native both-mode entry,
upgrade flag and Back-disable/visible-close-return policy. VisibilityChanged is
emitted before native Close later updates ActiveScreenContext: return checks the
still-owned capstone/back reference rather than requiring that later update.
Known Back-disable and inspect-open stages remain explicit if a later step fails.
Native sort and upgrade toggle controls retain their own behavior.

## Required unresolved upgrade presentation

Current and actually displayed values are distinct. Reads use only the original
logical values and already game-created rendered preview models. They never call
`MutableClone` to fabricate offscreen preview data.

Exact native `MutableClone` shallow-copies events, calls DeepCloneFields, then
AfterCloned clears events. For enchanted/afflicted cards DeepCloneFields calls
native enchant/afflict helpers that invoke the copied subscribers first. Thus a
read could refresh original UI or trigger observation callbacks. This is a
read-noninterference counterexample, not permission to suppress native events.

Public save/recreate APIs avoid original subscribers but omit current owner,
combat modifiers/dynamic state and affliction and require native card scope;
run/combat cloning APIs mutate that scope. No custom clone, rule engine or global
patch was introduced. Required offscreen upgrade presentation therefore reports
an explicit partial gap when requested but unavailable. The requirement remains
active until a faithful safe native presentation path is established.

## Verification scope

Tests cover 200/500 actual game-assembly model references beyond a 24-holder
window, source/reference order, exact closed callback ABI, runtime accessor
behavior, stale/Peek fencing, native callback-owned selection/preview behavior,
unknown after callback exception and exact inspector return ordering. Golden
negative captures also retain failed persistent HUD and consistency gaps after a
qualified grid/choice/bundle replacement, suppress every dispatch leaf, and test
conditional readiness for stable identity/binding/capacity failures. These are
binding/ABI/control fixtures, not real-game selection or performance receipts.
Exact builds and a later bounded runtime canary are still required for the loaded
callback, native UI/execution effects, cancellation, inspection and publication.
