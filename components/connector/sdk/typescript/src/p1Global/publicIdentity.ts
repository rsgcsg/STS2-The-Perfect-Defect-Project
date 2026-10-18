import { visibleCardSchema } from "../visibleState.js";
import { isObject } from "./canonical.js";

/** Paths are relative to the P1 model view. Unknown extension properties are
 * literal facts, even when their names look like native identity fields. */
export const normalizePublicPath = (path: string): string => path
  .replace(/^\$\.observation\./u, "").replace(/^\./u, "").replace(/\[\d+\]/gu, "[]");
export const publicFieldPath = (path: string, key: string): string => /^[A-Za-z0-9_]+$/u.test(key)
  ? (path ? `${path}.${key}` : key) : `${path}[${JSON.stringify(key)}]`;
export type PublicEntityRelation = "declared" | "reference";
const BASE_PATHS = [
  "player.content.entity_id", "player.content.relics[].entity_id", "player.content.potions[].entity_id",
  "commitments.reward_dispositions[].group", "map.content.nodes[].entity_id", "combat.content.player.player_entity_id",
  ...["companions", "potion_states", "orbs"].map(kind => `combat.content.player.${kind}[].entity_id`),
  "combat.content.enemies[].entity_id"
];
const CARD_ROOTS = [
  "deck.content.cards[]", "piles.content.zones[].cards[]", "combat.content.player.hand[]",
  "player.content.relics[].card_previews[]", "player.content.potions[].card_previews[]",
  "run.content.modifiers[].card_previews[]"
];
const REFERENCES = ["commitments.declined_reward_groups[]", "catalog[].subject", "catalog[].target"];
const SELECTORS = new Set(["card_selection", "combat_hand_card_selection", "native_combat_pile_selection",
  "native_deck_card_selection", "native_generated_card_choice", "native_simple_card_selection", "deck_transform_selection",
  "deck_card_selection", "deck_upgrade_selection", "deck_enchant_selection"]);

export function createPublicIdentityPolicy(nativeKind: string,
  extraReads: readonly { readonly kind: string; readonly content: unknown }[] = [],
  sceneContent?: unknown) {
  const declared = new Set(BASE_PATHS); const references = new Set(REFERENCES); const cards = [...CARD_ROOTS];
  const surface = "scene.content.surface.";
  const add = (fields: readonly string[]) => fields.forEach(field => declared.add(surface + field));
  const refs = (fields: readonly string[]) => fields.forEach(field => references.add(surface + field));
  const card = (root: string) => cards.push(surface + root);
  if (SELECTORS.has(nativeKind)) {
    card("cards[]"); add(["screen_entity_id", "hand_entity_id", "request_entity_id", "current_controls[].entity_id"]);
    refs(["selected_card_entity_ids[]", "selectable_card_entity_ids[]", "deselectable_card_entity_ids[]",
      "selector_request.entity_id", "selector_request.owner_entity_id", "selector_request.selected_entity_ids[]",
      "preview_options[].subject_entity_id", "selection_progress.selected_card_entity_ids[]"]);
    card("preview_options[].before"); card("preview_options[].after");
    if (nativeKind === "deck_upgrade_selection") card("preview_cards[]");
  } else if (nativeKind === "combat_turn") {
    add(["room_entity_id"]);
    for (const key of ["playable_cards", "usable_potions", "discardable_potions"]) {
      add([`${key}[].entity_id`]); refs([`${key}[].target_entity_ids[]`]);
    }
  } else if (nativeKind === "map_navigation") add(["next_options[].entity_id"]);
  else if (nativeKind === "reward_claim") {
    add(["reward_set_id", "rewards[].entity_id", "rewards[].group_id", "discardable_potions[].entity_id"]);
    card("rewards[].card_options[]");
  } else if (nativeKind === "card_reward_selection") {
    card("cards[]"); add(["alternatives[].entity_id", "screen_entity_id", "reward_set_id", "parent_group_id"]);
    refs(["selectable_card_entity_ids[]"]);
  } else if (nativeKind === "card_bundle_selection") {
    add(["bundles[].entity_id", "screen_entity_id"]); card("bundles[].cards[]");
    refs(["selected_bundle_entity_id", "selectable_bundle_entity_ids[]"]);
  } else if (["event_option", "event_dialogue", "rest_site"].includes(nativeKind))
    add(["options[].entity_id", "screen_entity_id", "room_entity_id"]);
  else if (["treasure_room", "treasure_chest", "treasure_relic_selection", "treasure_completion", "relic_choice", "native_boss_relic_selection"].includes(nativeKind)) {
    add(["room_entity_id", "screen_entity_id", "relics[].entity_id"]); refs(["selectable_relic_entity_ids[]"]);
  } else if (nativeKind === "shop_inventory") {
    add(["screen_entity_id", "shop_session_entity_id", "cards[].slot_entity_id", "relics[].entity_id", "relics[].slot_entity_id",
      "relics[].relic.entity_id", "potions[].entity_id", "potions[].slot_entity_id", "card_removal.entity_id", "card_removal.slot_entity_id"]);
    card("cards[]"); card("cards[].card"); card("relics[].relic.card_previews[]");
  } else if (nativeKind === "shop_room") add(["room_entity_id", "shop_session_entity_id"]);
  const eventCards = new Set<string>();
  const eventSurface = isObject(sceneContent) && isObject(sceneContent.surface) ? sceneContent.surface : null;
  if (["event_option", "event_dialogue"].includes(nativeKind) && Array.isArray(eventSurface?.options))
    eventSurface.options.forEach((option, optionIndex) => {
      if (!isObject(option) || !Array.isArray(option.tooltips)) return;
      option.tooltips.forEach((tip, tipIndex) => {
        if (isObject(tip) && tip.kind === "card" && visibleCardSchema.safeParse(tip.card).success)
          eventCards.add(`scene.content.surface.options[${optionIndex}].tooltips[${tipIndex}].card`);
      });
    });
  // These Reads are the owning typed current card DTO, not arbitrary detail blobs.
  const detailCards = new Set(extraReads.flatMap((read, index) => read.kind === "surface_card"
    && visibleCardSchema.safeParse(read.content).success ? [`extra_reads[${index}].content`] : []));
  const shopDeclarations = new Set<string>();
  const shopCards = new Set<string>();
  extraReads.forEach((read, index) => {
    const content = read.content as Record<string, unknown> | null;
    if (read.kind !== "shop_catalog" || content?.kind !== "shop_catalog"
        || ![content.cards, content.relics, content.potions].every(Array.isArray)) return;
    const root = `extra_reads[${index}].content.`;
    for (const field of ["cards[].entity_id", "cards[].slot_entity_id", "relics[].entity_id", "relics[].slot_entity_id",
      "relics[].relic.entity_id", "potions[].entity_id", "potions[].slot_entity_id", "card_removal.entity_id", "card_removal.slot_entity_id"])
      shopDeclarations.add(root + field);
    shopCards.add(root + "cards[].card");
    shopCards.add(root + "relics[].relic.card_previews[]");
  });
  if (detailCards.size > 0) references.add("extra_reads[].content.display_owner_entity_id");
  const cardIdentity = (path: string, root: string) => path.startsWith(root)
    && /^(?:\.card_previews\[\])*\.entity_id$/u.test(path.slice(root.length).replace(/\[\d+\]/gu, "[]"));
  return {
    relation(path: string): PublicEntityRelation | null {
      const normalized = normalizePublicPath(path);
      if (references.has(normalized)) return "reference";
      const literalPath = path.replace(/^\$\.observation\./u, "").replace(/^\./u, "");
      const detailPath = literalPath
        .replace(/^(extra_reads\[\d+\])(.*)$/u, (_, root: string, tail: string) => root + tail.replace(/\[\d+\]/gu, "[]"));
      if (declared.has(normalized) || cards.some(root => cardIdentity(normalized, root))
          || [...eventCards].some(root => cardIdentity(literalPath, root))
          || shopDeclarations.has(detailPath)
          || [...detailCards, ...shopCards].some(root => cardIdentity(detailPath, root))) return "declared";
      return null;
    }
  };
}
