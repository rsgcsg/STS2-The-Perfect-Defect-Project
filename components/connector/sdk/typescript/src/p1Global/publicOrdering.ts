import { completeCards } from "./admission.js";
import { P1G_ORDER_REVISION, type P1Observation } from "./contracts.js";

const SELECTORS = new Set(["card_selection", "combat_hand_card_selection", "native_combat_pile_selection",
  "native_deck_card_selection", "native_generated_card_choice", "native_simple_card_selection",
  "deck_transform_selection", "deck_card_selection", "deck_upgrade_selection", "deck_enchant_selection"]);
const TREASURES = new Set(["treasure_room", "treasure_chest", "treasure_relic_selection",
  "treasure_completion", "relic_choice", "native_boss_relic_selection"]);

/** r1.6 adds only membership domains, never selected/effect or display order.
 * Same-named extension fields have no declaration. All entity edges still use
 * the one public identity graph; this is not sorting or canonicalizing input. */
export function publicMembershipFields(nativeKind: string): readonly string[] {
  if (SELECTORS.has(nativeKind)) return ["selectable_card_entity_ids", "deselectable_card_entity_ids"];
  if (nativeKind === "card_reward_selection") return ["selectable_card_entity_ids"];
  if (nativeKind === "card_bundle_selection") return ["selectable_bundle_entity_ids"];
  if (TREASURES.has(nativeKind)) return ["selectable_relic_entity_ids"];
  return [];
}

export function publicOrderingIssue(observation: P1Observation): string | null {
  if (observation.revision !== P1G_ORDER_REVISION) return null;
  const surface = (observation.scene.content as Record<string, any>)?.surface;
  for (const field of publicMembershipFields(observation.scene.native_kind)) {
    const value = surface?.[field];
    if (value === undefined) continue; // Optional schema fields stay absent.
    if (!Array.isArray(value) || value.some(id => typeof id !== "string")
        || new Set(value).size !== value.length) return "invalid_public_membership_set";
  }
  return null;
}

/** P1 owns the order declarations consumed by full-public comparers. */
export function createPublicOrderingPolicy(observation: P1Observation, identicalFacts = false) {
  const cardInventory = observation.extra_reads.every(read => read.kind === "surface_card" && completeCards([read.content]));
  const [catalog, ...cards] = observation.extra_reads;
  const content = catalog?.content as Record<string, unknown> | null;
  const shopCardSuffix = catalog?.kind === "shop_catalog" && catalog.availability === "available"
    && content?.kind === "shop_catalog"
    && ["inventory_open", "inventory_closed_open_to_inspect"].includes(String(content.access_state))
    && [content.cards, content.relics, content.potions].every(Array.isArray)
    && cards.every(read => read.kind === "surface_card" && read.availability === "available" && completeCards([read.content]));
  const membership = new Set(observation.revision === P1G_ORDER_REVISION
    ? publicMembershipFields(observation.scene.native_kind).map(field => `$.observation.scene.content.surface.${field}`) : []);
  return {
    unorderedSuffix(path: string): number | null {
      return !identicalFacts && shopCardSuffix && path === "$.observation.extra_reads" ? 1 : null;
    },
    unordered(path: string, parent: Record<string, unknown> | null): boolean {
      const targetDomain = observation.scene.native_kind === "combat_turn" && [
        "$.observation.scene.content.surface.playable_cards[].target_entity_ids",
        "$.observation.scene.content.surface.usable_potions[].target_entity_ids"
      ].includes(path);
      return path === "$.observation.catalog" || targetDomain || membership.has(path) || !identicalFacts && (
        ["$.observation.deck.content.cards", "$.observation.map.content.nodes"].includes(path)
        || path === "$.observation.extra_reads" && cardInventory
        || path === "$.observation.piles.content.zones[].cards" && parent?.ordering === "unordered_multiset");
    }
  };
}
