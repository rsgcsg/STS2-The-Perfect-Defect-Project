import { visibleCardSchema } from "../visibleState.js";
import { isObject } from "./canonical.js";

const text = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const coordinate = (value: unknown): value is Record<string, unknown> => isObject(value)
  && Number.isSafeInteger(value.col) && Number.isSafeInteger(value.row);
export const completeCards = (cards: unknown): cards is Record<string, unknown>[] => Array.isArray(cards)
  && cards.every(card => visibleCardSchema.safeParse(card).success)
  && new Set(cards.map(card => card.entity_id)).size === cards.length;
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
  if (!isObject(value) || value.kind !== "combat" || !Number.isSafeInteger(value.round) || !text(value.turn_owner)
      || !isObject(value.player) || !text(value.player.player_entity_id) || !completeCards(value.player.hand)) return false;
  const player = value.player;
  if (!["block", "energy", "max_energy", "draw_pile_count", "discard_pile_count", "exhaust_pile_count"].every(key => finite(player[key]))
      || !["statuses", "companions", "potion_states", "orbs"].every(key => Array.isArray(player[key]))
      || !Array.isArray(value.enemies)) return false;
  if (new Set(value.enemies.map(enemy => isObject(enemy) ? enemy.entity_id : null)).size !== value.enemies.length) return false;
  return value.enemies.every(enemy => isObject(enemy) && text(enemy.entity_id) && text(enemy.definition_id)
    && finite(enemy.block) && (finite(enemy.hp) || enemy.hp === null) && (finite(enemy.max_hp) || enemy.max_hp === null)
    && Array.isArray(enemy.statuses) && Array.isArray(enemy.intents)
    && enemy.intents.every(intent => isObject(intent) && text(intent.type)));
}

/** Common typed selector fields; bundle/other shapes need their own contract. */
export function completeSelector(value: unknown): boolean {
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
    && text(item.entity_id) && Array.isArray(item.target_entity_ids) && item.target_entity_ids.every(text));
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
