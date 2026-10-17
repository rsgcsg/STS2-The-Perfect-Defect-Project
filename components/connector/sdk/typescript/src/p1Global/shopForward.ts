import { isObject } from "./canonical.js";
import { rewardWorldFacts } from "./rewardForward.js";
import type { P1Basis } from "./reducer.js";

/** Closing the merchant's UI changes its affordances, not the native inventory.
 * Retain every model fact and unknown extension, including full nested cards,
 * prices, stock, affordability and slot relations. Player/world facts retain
 * gold and potion capacity. Only this typed Read's UI-derived fields vary. */
export function shopWorldFacts(basis: P1Basis): string {
  const offer = (value: unknown) => {
    if (!isObject(value)) return value;
    const { visible: _visible, can_purchase: _purchase, blocked_reason: _reason, ...facts } = value;
    return facts;
  };
  const reads = basis.reads.map(read => {
    const response = read.response as Record<string, any> | null;
    const content = response?.content;
    if (read.kind !== "shop_catalog" || response?.content_schema !== "sts2.player-environment/read/shop_catalog-1"
        || !isObject(content) || content.kind !== "shop_catalog"
        || !["inventory_open", "inventory_closed_open_to_inspect"].includes(String(content.access_state))
        || !Array.isArray(content.cards) || !Array.isArray(content.relics) || !Array.isArray(content.potions)) return read;
    const { access_state: _access, ...facts } = content;
    return { ...read, response: { ...response, content: { ...facts,
      cards: content.cards.map(offer), relics: content.relics.map(offer), potions: content.potions.map(offer),
      ...(Object.hasOwn(content, "card_removal") ? { card_removal: offer(content.card_removal) } : {}) } } };
  });
  return rewardWorldFacts({ ...basis, reads });
}
