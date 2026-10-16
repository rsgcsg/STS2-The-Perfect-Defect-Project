import { canonicalJson, isObject } from "./canonical.js";
import { completeCards } from "./admission.js";
import type { P1Basis } from "./reducer.js";

const text = (v: unknown): v is string => typeof v === "string" && v.length > 0;
const outcomes = new Set(["do_nothing", "end_selection_and_complete_reward", "end_selection_and_do_not_complete_reward"]);

/** Native option identity/outcome are facts; neither its title nor its return
 * outcome alone proves that an alternative is the ordinary effect-free Skip. */
export function completeRewardAlternatives(value: unknown, opened = false): value is Record<string, any>[] {
  return Array.isArray(value) && value.every(v => isObject(v) && text(v.option_id) && text(v.title)
    && outcomes.has(String(v.after_selected)) && typeof v.is_pure_return === "boolean"
    && (!v.is_pure_return || v.after_selected === "end_selection_and_do_not_complete_reward")
    && (!opened || text(v.entity_id) && typeof v.enabled === "boolean"))
    && new Set(value.map(v => v.option_id)).size === value.length
    && (!opened || new Set(value.map(v => v.entity_id)).size === value.length);
}

export function completeRewardGroup(group: unknown): group is Record<string, any> {
  if (!isObject(group) || !text(group.entity_id) || !text(group.group_id) || !text(group.kind)
    || typeof group.label !== "string" || typeof group.enabled !== "boolean"
    || group.group_relation !== "independent") return false;
  return group.kind !== "card" || completeCards(group.card_options)
    && group.card_alternatives_complete === true && completeRewardAlternatives(group.card_alternatives)
    && typeof group.can_skip_selection === "boolean" && typeof group.can_reroll === "boolean"
    && typeof group.selection_completes_reward === "boolean";
}

export function completeRewardSurface(value: unknown): boolean {
  return isObject(value) && value.kind === "reward_claim" && text(value.reward_set_id)
    && typeof value.can_proceed === "boolean" && typeof value.can_leave_with_unclaimed_rewards === "boolean"
    && (!value.can_leave_with_unclaimed_rewards || value.can_proceed)
    && typeof value.potion_slots_full === "boolean" && Array.isArray(value.discardable_potions)
    && value.discardable_potions.every(v => isObject(v) && text(v.entity_id))
    && Array.isArray(value.rewards) && value.rewards.every(completeRewardGroup)
    && new Set(value.rewards.map(v => v.group_id)).size === value.rewards.length
    && new Set(value.rewards.map(v => v.entity_id)).size === value.rewards.length;
}

export function completeCardRewardSurface(value: unknown): boolean {
  if (!isObject(value) || value.kind !== "card_reward_selection" || !completeCards(value.cards)
    || !completeRewardAlternatives(value.alternatives, true)
    || !Array.isArray(value.selectable_card_entity_ids)
    || new Set(value.selectable_card_entity_ids).size !== value.selectable_card_entity_ids.length
    || !value.selectable_card_entity_ids.every(id => (value.cards as Record<string, unknown>[]).some(card => card.entity_id === id))
    || typeof value.selection_completes_reward !== "boolean") return false;
  return value.parent_status === "bound" && text(value.reward_set_id) && text(value.parent_group_id)
    || value.parent_status === "standalone" && value.reward_set_id === null && value.parent_group_id === null;
}

/** Only declared mechanical presentation fields are removed. All other current
 * card fields, including extensions and original instance relations, must match. */
export function rewardCardFacts(card: Record<string, any>): Record<string, unknown> {
  const { index: _index, is_selected: _selected, ...facts } = card;
  return facts;
}
export function rewardAlternativeFacts(option: Record<string, any>): Record<string, unknown> {
  const { entity_id: _entity, index: _index, label: _label, enabled: _enabled, ...facts } = option;
  return facts;
}

/** Public world facts outside the mechanical reward UI transition. No capture
 * timestamp, read id or private source cursor participates. The complete basis
 * has already been admitted by the shared reducer before this is compared. */
export function rewardWorldFacts(basis: P1Basis): string {
  const snapshot = basis.snapshot as Record<string, any>;
  const reads = basis.reads.filter(r => r.kind !== "surface_card").map(r => {
    const value = r.response as Record<string, any>;
    return { kind: r.kind, target: value?.target_referent_id ?? null, schema: value?.content_schema,
      visibility: value?.visibility_basis, ordering: value?.ordering_semantics, content: value?.content };
  }).sort((a, b) => { const x = canonicalJson(a), y = canonicalJson(b); return x < y ? -1 : x > y ? 1 : 0; });
  return canonicalJson({ persistent: snapshot.persistent, reads, session: snapshot.session,
    information_policy: snapshot.information_policy });
}

export interface RewardIntention {
  readonly owner: string;
  readonly group: string;
  readonly kind: "card" | "alternative" | "decline";
  readonly option: string;
  readonly cards: string;
  readonly alternatives: string;
  readonly completes: boolean;
  readonly world: string;
  readonly details: string;
}

/** Same-instance current detail inventory is part of the offered option. Multiple
 * Read instances are retained, not collapsed by kind or entity. */
function rewardDetailFacts(basis: P1Basis, cards: readonly Record<string, any>[]): string {
  const ids = new Set(cards.map(card => card.entity_id));
  const values = basis.reads.filter(read => read.kind === "surface_card").map(read => read.response as Record<string, any>)
    .filter(read => read && ids.has(read.target_referent_id)).map(read => ({ target: read.target_referent_id,
      schema: read.content_schema, visibility: read.visibility_basis, ordering: read.ordering_semantics,
      hidden: read.completeness.hidden_by_policy, content: read.content }));
  return canonicalJson(values.sort((a, b) => { const x = canonicalJson(a), y = canonicalJson(b); return x < y ? -1 : x > y ? 1 : 0; }));
}

export function rewardIntention(owner: string, group: Record<string, any>, basis: P1Basis,
  kind: RewardIntention["kind"], option: string): RewardIntention {
  return { owner, group: String(group.group_id), kind, option,
    cards: canonicalJson(group.card_options.map(rewardCardFacts)),
    alternatives: canonicalJson(group.card_alternatives.map(rewardAlternativeFacts)),
    completes: group.selection_completes_reward, world: rewardWorldFacts(basis), details: rewardDetailFacts(basis, group.card_options) };
}

export function matchesRewardIntention(plan: RewardIntention, basis: P1Basis): boolean {
  const snapshot = basis.snapshot as Record<string, any>;
  const surface = snapshot.interaction.content.surface;
  return completeCardRewardSurface(surface) && surface.parent_status === "bound"
    && surface.reward_set_id === plan.owner && surface.parent_group_id === plan.group
    && surface.selection_completes_reward === plan.completes
    && canonicalJson(surface.cards.map(rewardCardFacts)) === plan.cards
    && canonicalJson(surface.alternatives.map(rewardAlternativeFacts)) === plan.alternatives
    && rewardWorldFacts(basis) === plan.world && rewardDetailFacts(basis, surface.cards) === plan.details;
}
