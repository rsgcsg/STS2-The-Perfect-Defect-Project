import { completeCards, completeSelector } from "./admission.js";
import { canonicalJson, isObject } from "./canonical.js";
import { rewardWorldFacts } from "./rewardForward.js";
import type { P1Basis } from "./reducer.js";

const text = (v: unknown): v is string => typeof v === "string" && v.length > 0;
const operations = new Set(["open_preview", "toggle_upgrade_view", "confirm", "confirm_selection", "cancel_preview", "cancel_selection"]);
const families = new Set(["deck_card", "deck_upgrade", "deck_enchant", "simple", "combat_pile"]);
const sorted = (ids: readonly string[]) => [...ids].sort();

/** An opt-in native producer contract. Legacy selectors keep their explicit inputs. */
export function completeSelectorFacts(surface: unknown): surface is Record<string, any> {
  if (!isObject(surface) || !completeSelector(surface) || !completeCards(surface.cards) || !isObject(surface.selector_request)) return false;
  const r = surface.selector_request;
  if (!text(surface.screen_entity_id) || !text(surface.request_entity_id)
      || r.entity_id !== surface.request_entity_id || r.owner_entity_id !== surface.screen_entity_id
      || !families.has(String(r.family)) || r.stage !== surface.stage
      || r.selection_model !== "native_selected_original_set"
      || !["native_preview_at_max", "native_manual_completion", "native_completion_at_max",
        "native_manual_completion_with_dynamic_roster", "native_completion_with_display_count_clamp"].includes(String(r.add_transition))
      || r.min_select !== surface.min_select || r.max_select !== surface.max_select
      || r.require_manual_confirmation !== surface.require_manual_confirmation
      || typeof r.require_manual_confirmation !== "boolean"
      || !(r.displayed_holder_count === null || Number.isSafeInteger(r.displayed_holder_count) && Number(r.displayed_holder_count) >= 0)
      || typeof r.can_finish_now !== "boolean" || typeof r.can_decline_now !== "boolean"
      || r.mechanics_qualified !== true || r.preview_information_complete !== true || r.preview_projection_qualified !== true
      || r.capability_revision !== "sts2.native-selector-public-facts/1"
      || !Array.isArray(r.missing) || r.missing.length !== 0
      || canonicalJson(r.selected_entity_ids) !== canonicalJson(surface.selected_card_entity_ids)
      || !Array.isArray(surface.preview_options) || surface.preview_options.length !== surface.cards.length
      || !Array.isArray(surface.current_controls)) return false;
  const controls = surface.current_controls;
  const cards = surface.cards;
  if (!controls.every(c => isObject(c) && text(c.entity_id) && operations.has(String(c.operation)))
      || new Set(controls.map(c => c.entity_id)).size !== controls.length
      || new Set(controls.map(c => c.operation)).size !== controls.length) return false;
  return surface.preview_options.every((option: unknown, index: number) => {
    if (!isObject(option) || option.subject_entity_id !== cards[index]!.entity_id
        || !text(option.display_context) || !Array.isArray(option.missing) || option.missing.length !== 0
        || !completeCards([option.before]) || canonicalJson(option.before) !== canonicalJson(cards[index])) return false;
    const requiresAfter = r.family === "deck_upgrade" || r.family === "deck_enchant" && r.max_select === 1;
    return requiresAfter ? option.after_availability === "available" && completeCards([option.after])
      && (option.after as Record<string, unknown>).entity_id === option.subject_entity_id
      : option.after === null && option.after_availability === "not_applicable";
  });
}

export function selectorOperation(surface: Record<string, any>, subject: string | null): string | null {
  return surface.current_controls.find((c: Record<string, any>) => c.entity_id === subject)?.operation ?? null;
}

const cardFacts = (card: Record<string, any>) => {
  const { is_selected: _selected, index: _index, ...facts } = card;
  return facts;
};

/** Only native navigation/selection presentation changes inside the intention. */
function selectorFacts(surface: Record<string, any>): string {
  const { stage: _stage, selected_count: _count, selected_card_entity_ids: _selected,
    selected_card_referent_ids: _oldSelected, selectable_card_entity_ids: _selectable,
    deselectable_card_entity_ids: _deselectable, current_controls: _controls,
    can_confirm: _confirm, cancelable: _cancelable, can_close_peek: _peekClose,
    can_cancel_preview: _cancelPreview, can_cancel_selection: _cancelSelection, can_preview: _preview,
    can_toggle_upgrade_view: _toggle, is_peeking: _peek, showing_upgrade_previews: _showing,
    cards, preview_options, selector_request, ...rest } = surface;
  const { stage: _requestStage, selected_entity_ids: _requestSelected, can_finish_now: _finish,
    can_decline_now: _decline, ...request } = selector_request;
  return canonicalJson({ ...rest, cards: cards.map(cardFacts), selector_request: request,
    preview_options: preview_options.map((option: Record<string, any>) => ({ ...option,
      before: cardFacts(option.before), after: option.after === null ? null : cardFacts(option.after) })) });
}

function detailFacts(basis: P1Basis): string | null {
  const reads = basis.reads.filter(r => r.kind === "surface_card");
  if (reads.some(r => !isObject(r.response) || !isObject(r.response.content))) return null;
  const rows = reads.map(r => {
    const response = r.response as Record<string, any>;
    const { parent_read_id: _locator, ...content } = response.content;
    return { target: response.target_referent_id, schema: response.content_schema,
      visibility: response.visibility_basis, ordering: response.ordering_semantics,
      hidden: response.completeness.hidden_by_policy, content: cardFacts(content) };
  }).map(canonicalJson).sort();
  return canonicalJson(rows);
}

export interface SelectorIntention {
  readonly native_kind: string;
  readonly expected_selected: readonly string[];
  readonly facts: string;
  readonly world: string;
  readonly context: string;
  readonly details: string;
}

/** The Agent chooses an original card/set; only its native preview confirmation follows. */
export function selectorIntention(surface: Record<string, any>, basis: P1Basis,
  verb: string, subject: string | null): SelectorIntention | null {
  if (!completeSelectorFacts(surface) || surface.stage !== "selecting") return null;
  let expected: string[];
  if (verb === "select" && subject && surface.selector_request.add_transition === "native_preview_at_max"
      && surface.cards.some((c: Record<string, any>) => c.entity_id === subject)
      && !surface.selected_card_entity_ids.includes(subject)) {
    expected = [...surface.selected_card_entity_ids, subject];
    if (expected.length !== surface.max_select) return null;
  } else if (selectorOperation(surface, subject) === "open_preview") {
    expected = [...surface.selected_card_entity_ids];
  } else return null;
  const snapshot = basis.snapshot as Record<string, any>;
  const details = detailFacts(basis);
  if (details === null) return null;
  return { native_kind: surface.kind, expected_selected: sorted(expected), facts: selectorFacts(surface),
    world: rewardWorldFacts(basis), context: canonicalJson(snapshot.interaction.content.context), details };
}

export function matchesSelectorIntention(plan: SelectorIntention, basis: P1Basis): boolean {
  const snapshot = basis.snapshot as Record<string, any>;
  const surface = snapshot.interaction.content.surface;
  return snapshot.interaction.kind === plan.native_kind && completeSelectorFacts(surface) && surface.stage === "preview"
    && canonicalJson(sorted(surface.selected_card_entity_ids)) === canonicalJson(plan.expected_selected)
    && selectorFacts(surface) === plan.facts && rewardWorldFacts(basis) === plan.world
    && canonicalJson(snapshot.interaction.content.context) === plan.context && detailFacts(basis) === plan.details;
}
