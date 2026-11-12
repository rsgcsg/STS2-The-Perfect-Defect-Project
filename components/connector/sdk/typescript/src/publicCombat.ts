import { z } from "zod";
import { visibleCardSchema } from "./visibleState.js";

// Response DTO validation shared by capture profiles. This checks public field
// shape and health-display consistency; it grants no native/action authority.
const nativeInt = z.number().int().min(-2_147_483_648).max(2_147_483_647);
export function combatContextSchemaFor<T extends z.ZodTypeAny>(cardContentSchema: T) {
  const publicText = z.string().nullable().optional();
  const statusContentSchema = z.object({ definition_id: z.string().min(1), name: publicText,
    amount: z.number(), type: z.string().min(1), description: publicText }).passthrough();
  const healthFields = {
    hp: z.number().nullable(), max_hp: z.number().nullable(), block: z.number(),
    health_bar_visible: z.boolean(), hp_display_mode: z.string().min(1), hp_numbers_available: z.boolean()
  };
  const coherentHealthDisplay = (value: { hp: number | null; max_hp: number | null;
    hp_numbers_available: boolean; health_bar_visible: boolean }) => value.hp_numbers_available
      ? value.health_bar_visible && value.hp !== null && value.max_hp !== null
      : value.hp === null && value.max_hp === null;
  const combatContextSchema = z.object({
    kind: z.literal("combat"), encounter_type: z.string().min(1), round: nativeInt,
    turn_owner: z.string().min(1), is_play_phase: z.boolean(),
    player: z.object({
      player_entity_id: z.string().min(1), block: z.number(), energy: nativeInt, max_energy: nativeInt,
      stars: nativeInt.nullable().optional(), hand: z.array(cardContentSchema),
      draw_pile_count: nativeInt.nonnegative(), discard_pile_count: nativeInt.nonnegative(), exhaust_pile_count: nativeInt.nonnegative(),
      statuses: z.array(statusContentSchema),
      companions: z.array(z.object({ entity_id: z.string().min(1), definition_id: z.string().min(1),
        name: publicText, is_alive: z.boolean(), ...healthFields, statuses: z.array(statusContentSchema) }).passthrough().refine(coherentHealthDisplay, "incoherent public health display")),
      potion_states: z.array(z.object({ entity_id: z.string().min(1), target_type: z.string().min(1),
        can_use: z.boolean(), automatic: z.boolean() }).passthrough()),
      orbs: z.array(z.object({ entity_id: z.string().min(1), definition_id: z.string().min(1), name: publicText,
        description: publicText, passive_value: z.number(), evoke_value: z.number(), queue_index: nativeInt,
        is_next_to_evoke: z.boolean() }).passthrough()), orb_slots: nativeInt.nullable().optional()
    }).passthrough(),
    enemies: z.array(z.object({ entity_id: z.string().min(1), combat_id: z.number().int().min(0).max(4_294_967_295).nullable().optional(),
      definition_id: z.string().min(1), name: publicText, ...healthFields, statuses: z.array(statusContentSchema),
      intents: z.array(z.object({ type: z.string().min(1), label: publicText, title: publicText,
        description: publicText }).passthrough()) }).passthrough().refine(coherentHealthDisplay, "incoherent public health display"))
  }).passthrough();
  return combatContextSchema;
}

/** False when any number reachable through own enumerable values is not finite (each object visited once). */
export function hasFinitePublicNumbers(value: unknown): boolean {
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value !== "object" || value === null) return true;
  const pending: object[] = [value];
  const seen = new Set<object>(pending);
  while (pending.length > 0) {
    const values = Object.values(pending.pop()!);
    for (let index = 0; index < values.length; index++) {
      const item = values[index];
      if (typeof item === "number") { if (!Number.isFinite(item)) return false; }
      else if (typeof item === "object" && item !== null && !seen.has(item)) { seen.add(item); pending.push(item); }
    }
  }
  return true;
}
const currentPublicCombatSchema = combatContextSchemaFor(visibleCardSchema);
export function publicCombatContextProblems(value: unknown): string[] {
  if (!hasFinitePublicNumbers(value)) return ["public numbers must be finite JSON values"];
  const parsed = currentPublicCombatSchema.safeParse(value);
  return parsed.success ? [] : parsed.error.issues.map(issue =>
    `${issue.path.join(".")}: ${issue.message}`);
}
