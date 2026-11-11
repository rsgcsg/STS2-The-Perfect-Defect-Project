import { completeCombat } from "./admission.js";
import { isObject } from "./canonical.js";
import { deepFrozen, memoActive } from "./reduceMemo.js";

/** Original bytes stay unchanged. Only native identity metadata and verified
 * compatibility aliases are omitted from the public semantic HUD. */
export function projectCombatContext(source: Record<string, any>): { content: Record<string, any>; missing: string[] } {
  return memoActive() && deepFrozen(source) ? projectShared(source) : projectCopied(source);
}

function projectCopied(source: Record<string, any>): { content: Record<string, any>; missing: string[] } {
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

/* E6 (fast_chain memo only): the same projection for a deeply frozen source, where every
 * unchanged frozen part keeps one frozen projected object across steps. Each part is
 * what structuredClone gives (computed once per frozen input object), the deletions
 * are the ones above, and `missing` is recomputed in the same order from the source
 * values (structuredClone keeps primitive values and shared references). */
const CLONES = new WeakMap<object, unknown>();
const PROJECTED = { enemy: new WeakMap<object, Record<string, any>>(), companion: new WeakMap<object, Record<string, any>>(),
  orb: new WeakMap<object, Record<string, any>>() };
const HEALTH_MODES: Readonly<Record<string, string>> = {
  Normal: "normal", InfiniteWithNumbers: "infinite_with_numbers", InfiniteWithoutNumbers: "infinite_without_numbers"
};
function freezeAll<T>(value: T): T {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const key of Object.keys(value)) freezeAll((value as Record<string, unknown>)[key]);
  }
  return value;
}
/** structuredClone(value) for a deeply frozen value, kept per frozen object: a plain object or a
 * dense plain array is copied key by key so unchanged children stay shared; anything else is
 * structuredClone itself. */
function sharedClone(value: unknown): unknown {
  if (value === null || typeof value !== "object") return value;
  let clone = CLONES.get(value);
  if (clone === undefined) {
    const proto = Object.getPrototypeOf(value);
    if (Array.isArray(value) && proto === Array.prototype && Object.keys(value).length === value.length)
      clone = Object.freeze(value.map(sharedClone));
    else if (!Array.isArray(value) && proto === Object.prototype) {
      const copy: Record<string, unknown> = {};
      for (const key of Object.keys(value)) copy[key] = sharedClone((value as Record<string, unknown>)[key]);
      clone = Object.freeze(copy);
    } else clone = freezeAll(structuredClone(value));
    CLONES.set(value, clone);
  }
  return clone;
}
/** The frozen source object minus `drop` keys (fixed per source and kind), values as sharedClone gives them. */
function without(memo: WeakMap<object, Record<string, any>>, source: Record<string, any>, drop: readonly string[]): Record<string, any> {
  let projected = memo.get(source);
  if (projected === undefined) {
    const copy: Record<string, any> = {};
    for (const key of Object.keys(source)) if (!drop.includes(key)) copy[key] = sharedClone(source[key]);
    memo.set(source, projected = Object.freeze(copy));
  }
  return projected;
}
/** True when one object stands at two enemy/companion/orb positions: structuredClone keeps
 * that sharing, so the copied projection edits it once and reports it once. */
function sharedEntries(source: Record<string, any>): boolean {
  const seen = new Set<unknown>();
  for (const list of [source.enemies, source.player?.companions, source.player?.orbs]) {
    if (!Array.isArray(list)) continue;
    for (const entry of list) if (isObject(entry)) { if (seen.has(entry)) return true; seen.add(entry); }
  }
  return false;
}
function projectShared(source: Record<string, any>): { content: Record<string, any>; missing: string[] } {
  if (sharedEntries(source)) return projectCopied(source);
  const missing: string[] = [];
  if (!completeCombat(source)) missing.push("required:combat:invalid_source_content");
  const health = (value: Record<string, any>, path: string): string[] => {
    if (!Object.hasOwn(value, "health_display")) return [];
    if (typeof value.health_display !== "string" || HEALTH_MODES[value.health_display] === undefined
        || HEALTH_MODES[value.health_display] !== value.hp_display_mode)
      missing.push(`required:combat:conflicting_alias:${path}.health_display`);
    return ["health_display"];
  };
  const equivalent = (value: Record<string, any>, alias: string, canonical: string, path: string): string[] => {
    if (!Object.hasOwn(value, alias)) return [];
    if (value[alias] !== value[canonical]) missing.push(`required:combat:conflicting_alias:${path}.${alias}`);
    return [alias];
  };
  const content: Record<string, any> = {};
  for (const key of Object.keys(source)) content[key] = sharedClone(source[key]);
  if (Array.isArray(source.enemies)) content.enemies = source.enemies.map((enemy: unknown, index: number) => {
    if (!isObject(enemy)) return sharedClone(enemy);
    const drop = ["combat_id", ...health(enemy, `enemies[${index}]`)];
    return without(PROJECTED.enemy, enemy, drop);
  });
  const player = source.player;
  const companions = Array.isArray(player?.companions) ? player.companions.map((pet: unknown, index: number) => {
    if (!isObject(pet)) return sharedClone(pet);
    const drop = [...health(pet, `companions[${index}]`), ...equivalent(pet, "alive", "is_alive", `companions[${index}]`)];
    return without(PROJECTED.companion, pet, drop);
  }) : null;
  const orbs = Array.isArray(player?.orbs) ? player.orbs.map((orb: unknown, index: number) => {
    if (!isObject(orb)) return sharedClone(orb);
    const drop = [...equivalent(orb, "passive", "passive_value", `orbs[${index}]`),
      ...equivalent(orb, "evoke", "evoke_value", `orbs[${index}]`)];
    return without(PROJECTED.orb, orb, drop);
  }) : null;
  if (companions || orbs) {
    const projectedPlayer: Record<string, any> = {};
    for (const key of Object.keys(player)) projectedPlayer[key] = key === "companions" && companions ? companions
      : key === "orbs" && orbs ? orbs : sharedClone(player[key]);
    content.player = projectedPlayer;
  }
  return { content, missing };
}
