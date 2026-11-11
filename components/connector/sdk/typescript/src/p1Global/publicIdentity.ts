import { P1G_NONCOMBAT_POTION_SCENES, P1G_REVISION, type P1Revision } from "./contracts.js";
import { visibleCard } from "./admission.js";
import { isObject } from "./canonical.js";
import type { PublicPath } from "./publicPath.js";

/** Paths are relative to the P1 model view. Unknown extension properties are
 * literal facts, even when their names look like native identity fields. */
export const normalizePublicPath = (path: string): string => path
  .replace(/^\$\.observation\./u, "").replace(/^\./u, "").replace(/\[\d+\]/gu, "[]");
// Pure helpers are memoized by their exact input; results are unchanged.
const MEMO_LIMIT = 1 << 16;
const plainKeys = new Map<string, boolean>();
// Relation memos: at most 64 path-set signatures x 16k answers each (by path and by node).
const POLICY_LIMIT = 64;
const RELATION_LIMIT = 1 << 14;
interface RelationMemo {
  byPath: Map<string, PublicEntityRelation | null>;
  byNode: WeakMap<PublicPath, PublicEntityRelation | null>; nodes: number;
}
const POLICY_RELATIONS = new Map<string, RelationMemo>();
export const publicFieldPath = (path: string, key: string): string => {
  let plain = plainKeys.get(key);
  if (plain === undefined) {
    plain = /^[A-Za-z0-9_]+$/u.test(key);
    if (plainKeys.size >= MEMO_LIMIT) plainKeys.clear();
    plainKeys.set(key, plain);
  }
  return plain ? (path ? `${path}.${key}` : key) : `${path}[${JSON.stringify(key)}]`;
};
export type PublicEntityRelation = "declared" | "reference";
/**
 * @internal A key such that, under one policy, two paths with equal keys give equal
 * relations for themselves and for every descendant path. Outside `scene.` and
 * `extra_reads` every path set is matched on the index-free form, so the key drops
 * list indices there; under those two roots event and detail roots name literal
 * indices, so the key is the literal path.
 */
export function relationMemoKey(node: PublicPath): string {
  let key = node.relationKey;
  if (key === undefined) {
    const literal = node.path.replace(/^\$\.observation\./u, "").replace(/^\./u, "");
    key = node.relationKey = literal.startsWith("scene.") || literal.startsWith("extra_reads")
      ? `L${literal}` : `N${normalizePublicPath(node.path)}`;
  }
  return key;
}
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
  sceneContent?: unknown, revision: P1Revision = P1G_REVISION) {
  const declared = new Set(BASE_PATHS); const references = new Set(REFERENCES); const cards = [...CARD_ROOTS];
  const surface = "scene.content.surface.";
  const add = (fields: readonly string[]) => fields.forEach(field => declared.add(surface + field));
  const refs = (fields: readonly string[]) => fields.forEach(field => references.add(surface + field));
  const card = (root: string) => cards.push(surface + root);
  if (P1G_NONCOMBAT_POTION_SCENES.includes(nativeKind)) refs(["non_combat_discardable_potions[].entity_id"]);
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
  else if (nativeKind === "game_over") add(["other_controls[].entity_id"]);
  if (["r1.8", "r1.9"].includes(revision)) {
    if (nativeKind === "event_option") {
      add(["abandon_confirmation.event_entity_id", "abandon_confirmation.room_entity_id"]);
      refs(["abandon_confirmation.parent_option_entity_id"]);
    } else if (nativeKind === "abandon_run_confirmation")
      add(["modal_entity_id", "event_entity_id", "room_entity_id", "parent_option_entity_id", "controls[].entity_id"]);
  }
  if (revision === "r1.9" && nativeKind === "crystal_sphere")
    add(["sphere_entity_id", "event_entity_id", "room_entity_id", "cells[].entity_id",
      "visible_items[].entity_id", "tools[].entity_id"]);
  const eventCards = new Set<string>();
  const eventSurface = isObject(sceneContent) && isObject(sceneContent.surface) ? sceneContent.surface : null;
  if (["event_option", "event_dialogue"].includes(nativeKind) && Array.isArray(eventSurface?.options))
    eventSurface.options.forEach((option, optionIndex) => {
      if (!isObject(option) || !Array.isArray(option.tooltips)) return;
      option.tooltips.forEach((tip, tipIndex) => {
        if (isObject(tip) && tip.kind === "card" && visibleCard(tip.card))
          eventCards.add(`scene.content.surface.options[${optionIndex}].tooltips[${tipIndex}].card`);
      });
    });
  // These Reads are the owning typed current card DTO, not arbitrary detail blobs.
  const detailCards = new Set(extraReads.flatMap((read, index) => read.kind === "surface_card"
    && visibleCard(read.content) ? [`extra_reads[${index}].content`] : []));
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
  const eventRoots = [...eventCards]; const detailRoots = [...detailCards, ...shopCards];
  // relation() depends only on these path sets, so equal sets share one memo across steps.
  const signature = JSON.stringify([declared, references, cards, eventRoots, shopDeclarations, detailRoots]
    .map(paths => [...paths].sort()));
  let memo = POLICY_RELATIONS.get(signature);
  if (memo === undefined) {
    if (POLICY_RELATIONS.size >= POLICY_LIMIT) POLICY_RELATIONS.clear();
    POLICY_RELATIONS.set(signature, memo = { byPath: new Map(), byNode: new WeakMap(), nodes: 0 });
  }
  const relations = memo; const byPath = relations.byPath;
  const relation = (path: string): PublicEntityRelation | null => {
    let found = byPath.get(path);
    if (found === undefined) {
      found = resolve(path);
      if (byPath.size >= RELATION_LIMIT) byPath.clear();
      byPath.set(path, found);
    }
    return found;
  };
  const resolve = (path: string): PublicEntityRelation | null => {
      const normalized = normalizePublicPath(path);
      if (references.has(normalized)) return "reference";
      const literalPath = path.replace(/^\$\.observation\./u, "").replace(/^\./u, "");
      const detailPath = literalPath
        .replace(/^(extra_reads\[\d+\])(.*)$/u, (_, root: string, tail: string) => root + tail.replace(/\[\d+\]/gu, "[]"));
      if (declared.has(normalized) || cards.some(root => cardIdentity(normalized, root))
          || eventRoots.some(root => cardIdentity(literalPath, root))
          || shopDeclarations.has(detailPath)
          || detailRoots.some(root => cardIdentity(detailPath, root))) return "declared";
      return null;
  };
  return {
    relation,
    /** @internal Stable for every policy with these path sets: equal tokens give equal relations. */
    memoToken: relations as object,
    /** @internal The same answer as relation(node.path), for the reducer's interned paths. */
    relationAt(node: PublicPath): PublicEntityRelation | null {
      let found = relations.byNode.get(node);
      if (found === undefined) {
        found = relation(node.path);
        if (relations.nodes >= RELATION_LIMIT) { relations.byNode = new WeakMap(); relations.nodes = 0; }
        relations.byNode.set(node, found); relations.nodes++;
      }
      return found;
    }
  };
}
