# Native reward and merchant information inputs

This G3 source adapter supplies two exact owner-specific native-logical-v1
information entry slices. It does not qualify runtime rendering, all L06/L07
scenes, Human evidence or G2/V1.

## Ordinary card reward

The actual reward holder's FocusEntered shows native tips. Its AltPressed
invokes the screen's native InspectCard callback, which opens one current
holder model while the exact reward completion task remains pending. The
adapter retains the screen, task, row, holder, CardNode, model and alternative
bindings and rechecks them before input. A refreshed holder, different task,
changed owner, missing presentation or completed request rejects the old input.

Native SetClickable(false) during opening still permits focus. Accordingly the
native-logical branch composes the complete currently available relation:
focus, permitted inspection, all selectable cards, all enabled alternatives,
existing information entries and native potion openers. Selection/alternative
descriptors and delivery remain the existing reward owners. The legacy
clickability-based settling profile is unchanged. Exact roster/alternative
proof does not clear inherited HUD, shared information, consistency or capacity
failures; the final completeness gate closes every leaf on a required gap.

## Merchant stock

Card, relic and potion stock use their actual NMerchantSlot variants, not
player-owned relic inventory or the potion belt. Entry, inventory, slot, hitbox,
rendered node and model references bind the current stocked source. Gold and
purchase eligibility do not authorize or suppress information entry.

All three variants may focus for native tips. Only card and relic variants
have a meaningful native preview override: a fresh disposable confirm
InputEventAction goes through the exact slot's public _GuiInput to OnPreview.
Both open a native singleton inspector. Potion has no preview override and no
invented inspect action. Native Open enables Back before publishing its open/current context.
BlockInput redirects input to its native blocker and disables Back. This exact
blocker/Back tuple is rechecked; known blocking excludes stock information even
if individual hitboxes remain enabled, and an unknown tuple fails closed.
Existing purchase/removal/close leaves and public offer price/eligibility facts
are retained. Sold/replaced stock, changed model,
disabled/unmounted control, owner replacement or an entered inspector rejects
the stale input.

## Information lifecycle and evidence

The existing information owner handles rendered tips and entered inspectors.
Switching an adapter-owned focus emits that exact previous source's native
exit. A repeated focus can reuse only the same retained source, entry and
current live rendered registry set; equal text is not identity. Source
revalidation also applies to unfocus. Callback exceptions remain unknown and
are never automatically replayed.

Host tests exercise the actual composition, stale-reference/task guards,
closed typed capabilities, native ABI, public stock subjects and inherited
completeness failures. They do not execute a Godot scene. The exact combined
candidate still requires the owning build/cold-load/canary gates from
`docs/TESTING.md` at the project root: opening focus, inspect and
return, reroll/stock invalidation, whole-catalog retention and no purchase or
selection from information entry. The separate inspector original/display
witness repair retains its own verification boundary.
