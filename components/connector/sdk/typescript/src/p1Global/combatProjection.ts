import { completeCombat } from "./admission.js";
import { isObject } from "./canonical.js";

/** Original bytes stay unchanged. Only native identity metadata and verified
 * compatibility aliases are omitted from the public semantic HUD. */
export function projectCombatContext(source: Record<string, any>): { content: Record<string, any>; missing: string[] } {
  const content = structuredClone(source);
  const missing: string[] = [];
  if (!completeCombat(source)) missing.push("required:combat:invalid_source_content");
  const healthModes: Readonly<Record<string, string>> = {
    Normal: "normal", InfiniteWithNumbers: "infinite_with_numbers", InfiniteWithoutNumbers: "infinite_without_numbers"
  };
  const equivalent = (value: Record<string, any>, alias: string, canonical: string, path: string) => {
    if (!Object.hasOwn(value, alias)) return;
    if (value[alias] !== value[canonical]) missing.push(`required:combat:conflicting_alias:${path}.${alias}`);
    delete value[alias];
  };
  const health = (value: Record<string, any>, path: string) => {
    if (!Object.hasOwn(value, "health_display")) return;
    if (typeof value.health_display !== "string" || healthModes[value.health_display] === undefined
        || healthModes[value.health_display] !== value.hp_display_mode)
      missing.push(`required:combat:conflicting_alias:${path}.health_display`);
    delete value.health_display;
  };
  if (Array.isArray(content.enemies)) for (const [index, enemy] of content.enemies.entries()) {
    if (!isObject(enemy)) continue;
    delete enemy.combat_id; // entity_id already owns the public target relation.
    health(enemy, `enemies[${index}]`);
  }
  if (Array.isArray(content.player?.companions)) for (const [index, pet] of content.player.companions.entries()) {
    if (!isObject(pet)) continue;
    health(pet, `companions[${index}]`); equivalent(pet, "alive", "is_alive", `companions[${index}]`);
  }
  if (Array.isArray(content.player?.orbs)) for (const [index, orb] of content.player.orbs.entries()) {
    if (!isObject(orb)) continue;
    equivalent(orb, "passive", "passive_value", `orbs[${index}]`);
    equivalent(orb, "evoke", "evoke_value", `orbs[${index}]`);
  }
  return { content, missing };
}
