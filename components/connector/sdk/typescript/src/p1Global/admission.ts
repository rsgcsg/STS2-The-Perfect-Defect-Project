import { publicCombatContextProblems } from "../publicCombat.js";
import { visibleCardSchema } from "../visibleState.js";
import { isObject } from "./canonical.js";

const text = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const coordinate = (value: unknown): value is Record<string, unknown> => isObject(value)
  && Number.isSafeInteger(value.col) && Number.isSafeInteger(value.row);
export const completeCards = (cards: unknown): cards is Record<string, unknown>[] => Array.isArray(cards)
  && cards.every(card => visibleCardSchema.safeParse(card).success)
  && new Set(cards.map(card => card.entity_id)).size === cards.length;
/** A display-only native upgrade preview, never an action-bearing card instance. */
export function completePublicUpgradePreview(value: unknown): boolean {
  if (!isObject(value)) return false;
  if (value.status === "not_applicable") return value.card === null;
  const card = value.card;
  return value.status === "available" && isObject(card) && card.entity_id === null
    && ["definition_id", "name", "type", "cost", "description", "rarity", "target_type"].every(key => text(card[key]))
    && typeof card.is_upgraded === "boolean" && Array.isArray(card.keywords)
    && card.keywords.every(tip => isObject(tip) && text(tip.name) && text(tip.description))
    && Array.isArray(card.card_previews);
}

export function completeDeck(value: unknown): boolean {
  return isObject(value) && value.kind === "run_deck" && completeCards(value.cards) && value.card_count === value.cards.length;
}
export function completePiles(value: unknown): boolean {
  if (!isObject(value) || value.kind !== "combat_piles" || !Array.isArray(value.zones) || value.zones.length !== 3) return false;
  const zones = value.zones;
  if (new Set(zones.map(zone => isObject(zone) ? zone.zone : null)).size !== 3) return false;
  return ["draw", "discard", "exhaust"].every(name => zones.some(zone => isObject(zone) && zone.zone === name
    && completeCards(zone.cards) && zone.card_count === zone.cards.length
    && (name === "draw" ? zone.ordering_semantics === "unordered_multiset" : zone.ordering_semantics === "native_visible_order")));
}
/** Exact MapLiveContext fields (including explicit null at the pre-travel boundary).
 * Missing topology/current position is not a successful empty map. */
export function completeMap(value: unknown, actIndex: unknown): boolean {
  if (!isObject(value) || value.kind !== "map" || !Number.isSafeInteger(value.act_index) || value.act_index !== actIndex
      || !Object.hasOwn(value, "current_position") || !(value.current_position === null || coordinate(value.current_position))
      || !Array.isArray(value.visited) || !value.visited.every(coordinate)
      || !Array.isArray(value.nodes) || value.nodes.length < 2) return false;
  const key = (node: Record<string, unknown>) => `${node.col},${node.row}`;
  const nodes = value.nodes;
  if (!nodes.every(node => coordinate(node) && text(node.entity_id) && text(node.point_type)
      && text(node.state)
      && Array.isArray(node.children) && node.children.every(coordinate))) return false;
  const coordinates = new Set(nodes.map(key));
  if (coordinates.size !== nodes.length || new Set(nodes.map(node => node.entity_id)).size !== nodes.length) return false;
  return nodes.every(node => node.children.every((child: Record<string, unknown>) => coordinates.has(key(child))))
    && value.visited.every(node => coordinates.has(key(node)))
    && (value.current_position === null ? value.visited.length === 0 : coordinates.has(key(value.current_position)));
}
export function completeCombat(value: unknown): boolean {
  // The same nested native DTO/health-display schema used by C1, with this
  // profile's existing extensible public-card schema. No guessed HUD defaults.
  if (publicCombatContextProblems(value).length > 0 || !isObject(value) || !isObject(value.player)
      || !completeCards(value.player.hand) || !Array.isArray(value.enemies)) return false;
  return new Set(value.enemies.map(enemy => (enemy as Record<string, unknown>).entity_id)).size === value.enemies.length;
}

/** Common typed selector fields; bundle/other shapes need their own contract. */
export function completeSelector(value: unknown): boolean {
  if (isObject(value) && value.kind === "native_generated_card_choice") {
    // This native chooser commits one visible card directly; it is not a
    // min/max grid with a selected set and a separate confirmation stage.
    if (!completeCards(value.cards)) return false;
    const cards = value.cards;
    return value.stage === "choosing" && value.is_peeking === false
      && typeof value.can_skip === "boolean" && (value.prompt === null || typeof value.prompt === "string")
      && Array.isArray(value.selectable_card_entity_ids)
      && new Set(value.selectable_card_entity_ids).size === value.selectable_card_entity_ids.length
      && value.selectable_card_entity_ids.every(id => cards.some(card => card.entity_id === id))
      && cards.every(card => card.is_selected === false);
  }
  if (!isObject(value) || !completeCards(value.cards) || !Number.isSafeInteger(value.min_select)
      || !Number.isSafeInteger(value.max_select) || Number(value.min_select) < 0 || Number(value.max_select) < Number(value.min_select)
      || !Array.isArray(value.selected_card_entity_ids) || new Set(value.selected_card_entity_ids).size !== value.selected_card_entity_ids.length
      || value.selected_count !== value.selected_card_entity_ids.length || Number(value.selected_count) > Number(value.max_select)
      || typeof value.can_confirm !== "boolean"
      || !(typeof value.cancelable === "boolean" || typeof value.can_close_peek === "boolean")) return false;
  const cards = value.cards; const selected = value.selected_card_entity_ids;
  return selected.every(id => cards.some(card => card.entity_id === id))
    && cards.every(card => card.is_selected === selected.includes(card.entity_id));
}

export function completeCombatSurface(value: unknown): boolean {
  const commands = (items: unknown): boolean => Array.isArray(items) && items.every(item => isObject(item)
    && text(item.entity_id) && Array.isArray(item.target_entity_ids) && item.target_entity_ids.every(text)
    && new Set(item.target_entity_ids).size === item.target_entity_ids.length);
  return isObject(value) && value.kind === "combat_turn" && typeof value.can_end_turn === "boolean"
    && commands(value.playable_cards) && commands(value.usable_potions)
    // Native71cf uses VisibleCombatPotion here, not VisibleCombatCommandOption.
    // Execute targets belong to authoritative bound_actions and usable_potions.
    && (value.discardable_potions === undefined || Array.isArray(value.discardable_potions)
      && value.discardable_potions.every(item => isObject(item) && text(item.entity_id) && text(item.definition_id)
        && Number.isSafeInteger(item.slot) && text(item.target_type) && typeof item.can_use === "boolean"
        && typeof item.automatic === "boolean" && ["name", "description"].every(key =>
          item[key] === undefined || item[key] === null || typeof item[key] === "string")));
}
