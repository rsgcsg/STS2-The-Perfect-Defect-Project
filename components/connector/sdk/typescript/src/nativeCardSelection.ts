import { isJsonObject } from "./json.js";

// Strict consumer boundary for the bounded Managed choose/combat selector
// surface. Unsupported historical card_selection observations remain readable.
export function nativeCardSelectionProblems(snapshot: unknown): string[] {
  if (!isJsonObject(snapshot) || !isJsonObject(snapshot.interaction)
      || snapshot.interaction.kind !== "card_selection") return [];
  const content = snapshot.interaction.content;
  // This additive Managed extension does not redefine other Hosts' existing
  // card_selection contracts or historical readers.
  if (!isJsonObject(content) || !isJsonObject(content.surface)
      || content.surface.selector_profile == null) return [];
  const surface = content.surface;
  const bound = snapshot.bound_actions;
  // Unavailable/partial diagnostics carry no claim of an actionable catalog.
  // A complete catalog must close even when every actual action was removed.
  if (!isJsonObject(bound) || bound.status !== "complete") return [];
  if (surface.selector_profile !== "managed-command-selector-1")
    return ["native card selection requires its explicit Managed selector profile"];
  if (!isJsonObject(content.context))
    return ["native card selection requires typed current content"];
  if (!Array.isArray(bound.actions))
    return ["native card selection requires the complete current action catalog"];
  const cards = surface.cards;
  const text = (v: unknown): v is string => typeof v === "string" && v.trim().length > 0
    && !/[{}]|\[img/iu.test(v);
  const cardComplete = (v: unknown, depth = 0): boolean => {
    if (!isJsonObject(v) || depth >= 16) return false;
    return ["entity_id", "definition_id", "name", "description", "cost", "type", "rarity", "target_type"]
      .every((key) => text(v[key])) && typeof v.is_upgraded === "boolean"
      && Array.isArray(v.keywords) && v.keywords.every((tip) => isJsonObject(tip)
        && text(tip.name) && text(tip.description))
      && Array.isArray(v.card_previews) && v.card_previews.every((card) => cardComplete(card, depth + 1));
  };
  if (!Array.isArray(cards) || cards.length === 0 || !cards.every((card) => cardComplete(card)))
    return ["native card selection requires every strictly rendered card and hover detail"];
  const values = cards.filter(isJsonObject);
  const ids = values.map((card) => card.entity_id);
  const selected = surface.selected_card_entity_ids;
  const choose = surface.selector_kind === "choose_card";
  const combat = surface.selector_kind === "combat_pile";
  const manual = surface.require_manual_confirmation;
  if (!(choose || combat) || new Set(ids).size !== ids.length || surface.native_option_count !== cards.length
      || !Array.isArray(selected) || selected.length > 1 || new Set(selected).size !== selected.length
      || selected.some((id) => !ids.includes(id)) || surface.selected_count !== selected.length
      || surface.kind !== "card_selection" || surface.stage !== "choosing" || surface.cancelable !== false
      || typeof manual !== "boolean" || typeof surface.can_skip !== "boolean"
      || surface.max_select !== 1 || surface.can_confirm !== (manual && selected.length === 1)
      || (choose ? manual || selected.length !== 0 || cards.length > 3 || surface.display_context !== "none"
          || surface.min_select !== (surface.can_skip ? 0 : 1)
        : surface.can_skip || surface.min_select !== 1 || !["draw", "discard", "exhaust"].includes(String(surface.display_context)))
      || values.some((card) => card.is_selected !== selected.some((id) => id === card.entity_id)
        || card.is_selectable !== (!card.is_selected && selected.length === 0)
        || card.is_deselectable !== (manual && card.is_selected)))
    return ["native card selection purpose, bounds, flags or selected identities are inconsistent"];
  const ctx = content.context;
  const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
  const statusesComplete = (values: unknown): boolean => Array.isArray(values) && values.every((value) =>
    isJsonObject(value) && ["definition_id", "name", "type", "description"].every((key) => text(value[key]))
    && finite(value.amount));
  const healthComplete = (value: Record<string, unknown>): boolean => {
    if (typeof value.health_bar_visible !== "boolean"
        || !["Normal", "InfiniteWithNumbers", "InfiniteWithoutNumbers"].includes(String(value.health_display))) return false;
    const numbers = value.health_bar_visible && value.health_display !== "InfiniteWithoutNumbers";
    return numbers ? finite(value.hp) && finite(value.max_hp) : value.hp === null && value.max_hp === null;
  };
  const creatureComplete = (value: unknown): value is Record<string, unknown> => isJsonObject(value)
    && text(value.entity_id) && text(value.definition_id) && finite(value.block)
    && healthComplete(value) && statusesComplete(value.statuses);
  const persistent = snapshot.persistent;
  const persistentContent = isJsonObject(persistent) ? persistent.content : null;
  const owner = isJsonObject(persistentContent) && isJsonObject(persistentContent.player)
    ? persistentContent.player.entity_id : null;
  const hud = ctx.combat;
  const player = isJsonObject(hud) ? hud.player : null;
  if (ctx.kind !== "selection" || !isJsonObject(hud) || hud.kind !== "combat"
      || !Number.isSafeInteger(hud.round) || (hud.round as number) < 1 || !text(hud.turn_owner)
      || !isJsonObject(player) || !text(player.player_entity_id) || player.player_entity_id !== owner
      || !["hp", "max_hp", "block", "energy", "max_energy", "stars", "draw_pile_count", "discard_pile_count",
        "exhaust_pile_count", "orb_slots"].every((key) => finite(player[key]))
      || !statusesComplete(player.statuses)
      || !Array.isArray(player.orbs) || !player.orbs.every((orb) => isJsonObject(orb)
        && ["entity_id", "definition_id", "name"].every((key) => text(orb[key]))
        && finite(orb.passive) && finite(orb.evoke) && Array.isArray(orb.keywords)
        && orb.keywords.every((tip) => isJsonObject(tip) && text(tip.name) && text(tip.description)))
      || !Array.isArray(player.companions) || !player.companions.every((pet) => creatureComplete(pet)
        && typeof pet.alive === "boolean")
      || !Array.isArray(hud.enemies) || !hud.enemies.every((enemy) => creatureComplete(enemy) && text(enemy.name)
        && Array.isArray(enemy.intents) && enemy.intents.every((intent) => isJsonObject(intent)
          && ["type", "label", "title", "description"].every((key) => text(intent[key])))))
    return ["native card selection requires complete current post-prefix combat context for the persistent player"];
  const referents = snapshot.referents;
  if (!Array.isArray(referents)) return ["native card selection requires current card referents"];
  const cardRefs = referents.filter((ref) => isJsonObject(ref) && ref.role === "card");
  if (cardRefs.length !== cards.length || cardRefs.some((ref) => !isJsonObject(ref)
      || !ids.includes(ref.referent_id))) return ["native card selection card/referent closure is incomplete"];
  const expected = values.flatMap((card) => [
    ...(card.is_selectable ? [`select:${card.entity_id}`] : []),
    ...(card.is_deselectable ? [`deselect:${card.entity_id}`] : [])
  ]);
  if (surface.can_skip) expected.push("skip:");
  if (surface.can_confirm) expected.push("confirm:");
  const actual = bound.actions.map((action) => isJsonObject(action) && Array.isArray(action.arguments)
    && action.arguments.length === 0 ? `${action.verb}:${action.subject_referent_id ?? ""}` : "invalid");
  return JSON.stringify(expected.sort()) === JSON.stringify(actual.sort()) ? []
    : ["native card selection must publish exactly every current card and permitted control"];
}
