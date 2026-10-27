import { z } from "zod";

/** Exact native build and the two approved r1.7 read-only counter rules. This
 * witness is capture provenance; it must never enter a P1/model observation. */
export const SETTLED_RELIC_COUNTERS_SCHEMA = "sts2.player-environment/settled-relic-counters-1" as const;
export const SETTLED_RELIC_GAME_MVID = "57785517-0b16-42b9-8b36-bad6fb28384b" as const;
const rules = {
  KUSARIGAMA: { rule: "kusarigama-attacks-mod3-1", threshold: 3 },
  LASTING_CANDY: { rule: "lasting-candy-rewards-mod2-1", threshold: 2 }
} as const;
type Definition = keyof typeof rules;
const factSchema = z.object({ entity_id: z.string().min(1), definition_id: z.enum(["KUSARIGAMA", "LASTING_CANDY"]),
  display_counter: z.number().finite().int().safe(), logical_counter: z.number().finite().int().safe(),
  rule_id: z.enum(["kusarigama-attacks-mod3-1", "lasting-candy-rewards-mod2-1"])
}).strict();
const witnessSchema = z.object({ schema: z.literal(SETTLED_RELIC_COUNTERS_SCHEMA), source_snapshot_id: z.string().min(1),
  session: z.object({ runtime_instance_id: z.string().min(1), environment_fingerprint: z.string().min(1) }).strict(),
  game_mvid: z.literal(SETTLED_RELIC_GAME_MVID), source_kind: z.literal("native_same_capture"),
  facts: z.array(factSchema).max(10_000), missing: z.array(z.string().min(1))
}).strict();
export type SettledRelicCounters = z.infer<typeof witnessSchema>;
export interface SettledCounterBasis {
  readonly snapshot: unknown;
  readonly reads: readonly { readonly response: unknown | null }[];
  readonly settled_relic_counters?: unknown;
}
const object = (v: unknown): v is Record<string, any> => v !== null && typeof v === "object" && !Array.isArray(v);
interface Carrier { entity: string; definition: Definition; display: number; value: Record<string, any> }

/** Only actual relic carriers, not arbitrary extension objects or card previews.
 * Missing/null counter fields remain absent/null, including unshown offers. */
function carriers(basis: SettledCounterBasis): Carrier[] {
  const result: Carrier[] = [];
  const relic = (v: unknown) => {
    if (!object(v) || !Object.hasOwn(rules, v.definition_id) || v.counter == null) return;
    const rule = rules[v.definition_id as Definition];
    if (typeof v.entity_id !== "string" || !v.entity_id || !Number.isSafeInteger(v.counter)
        || v.counter < 0 || v.counter > rule.threshold) throw new Error("settled_relic_counter_invalid_public_carrier");
    result.push({ entity: v.entity_id, definition: v.definition_id, display: v.counter, value: v });
  };
  const offers = (v: unknown) => { if (Array.isArray(v)) for (const item of v) relic(object(item) && object(item.relic) ? item.relic : item); };
  const snapshot = basis.snapshot as Record<string, any>;
  offers(snapshot?.persistent?.content?.player?.relics);
  offers(snapshot?.interaction?.content?.surface?.relics);
  if (Array.isArray(snapshot?.referents)) for (const ref of snapshot.referents) {
    if (ref.role === "relic") relic(ref.properties);
    if (ref.role === "shop_entry" && object(ref.properties)) relic(ref.properties.relic);
  }
  for (const read of basis.reads) if (object(read.response) && read.response.kind === "shop_catalog")
    offers(read.response.content?.relics);
  return result;
}
function inventory(basis: SettledCounterBasis): Map<string, Carrier> {
  const found = new Map<string, Carrier>();
  for (const carrier of carriers(basis)) {
    const previous = found.get(carrier.entity);
    if (previous && (previous.definition !== carrier.definition || previous.display !== carrier.display))
      throw new Error("settled_relic_counter_carriers_disagree");
    found.set(carrier.entity, carrier);
  }
  return found;
}

/** PB3 has exact same-snapshot/session/build binding. With no native capture
 * gaps every shown eligible relic must have a fact; partial witnesses may omit
 * facts but never weaken validation of the facts they actually supply. */
export function decodeSettledRelicCounters(value: unknown, basis: SettledCounterBasis): SettledRelicCounters {
  const witness = witnessSchema.parse(value), snapshot = basis.snapshot as Record<string, any>;
  if (witness.source_snapshot_id !== snapshot?.snapshot_id
      || witness.session.runtime_instance_id !== snapshot?.session?.runtime_instance_id
      || witness.session.environment_fingerprint !== snapshot?.session?.environment_fingerprint)
    throw new Error("settled_relic_counter_capture_binding_mismatch");
  const shown = inventory(basis), seen = new Set<string>();
  for (const fact of witness.facts) {
    const carrier = shown.get(fact.entity_id), rule = rules[fact.definition_id];
    if (seen.has(fact.entity_id) || !carrier || carrier.definition !== fact.definition_id
        || carrier.display !== fact.display_counter || fact.rule_id !== rule.rule
        || fact.logical_counter < 0 || fact.logical_counter >= rule.threshold
        || fact.display_counter < 0 || fact.display_counter > rule.threshold
        || fact.display_counter < rule.threshold && fact.display_counter !== fact.logical_counter)
      throw new Error("settled_relic_counter_fact_mismatch");
    seen.add(fact.entity_id);
  }
  if (witness.missing.length === 0 && seen.size !== shown.size)
    throw new Error("settled_relic_counter_inventory_incomplete");
  return witness;
}

export interface SettledRelicCounterProjection {
  readonly schema: "sts2.p1/settled-relic-counter-projection-1";
  readonly revision: "r1.7";
  readonly source_snapshot_id: string;
  readonly complete: boolean;
  readonly facts: readonly { readonly entity_id: string; readonly definition_id: Definition;
    readonly display_counter: number; readonly logical_counter: number | null; readonly rule_id: string;
    readonly recovery: "native_same_capture" | "legacy_same_snapshot_public_rule" | "ambiguous_missing_witness" }[];
  readonly missing: readonly string[];
}

/** Pure r1.7 reprojection. Native witnesses permit all in-range residues. Old
 * originals permit only K=0/1/2 or Candy=0/1: the native nonactivation getter
 * makes those public values unambiguous. K=3/Candy=2 have no unique inverse
 * (a further trigger can run while the one-second display is still active).
 * They remain incomplete, never default to zero and never borrow another frame.
 * Only counter fields that already exist are replaced on a deep copy. */
export function reprojectSettledRelicCounters<T extends SettledCounterBasis>(basis: T): {
  basis: T; receipt: SettledRelicCounterProjection;
} {
  const projected = structuredClone(basis), shown = inventory(projected);
  const witness = basis.settled_relic_counters === undefined ? null
    : decodeSettledRelicCounters(basis.settled_relic_counters, basis);
  const native = new Map(witness?.facts.map(fact => [fact.entity_id, fact]) ?? []);
  const missing = (witness?.missing ?? []).map(reason => `settled_relic_counters:${reason}`);
  const values = new Map<string, number>();
  const facts: SettledRelicCounterProjection["facts"][number][] = [];
  for (const [entity, carrier] of shown) {
    const rule = rules[carrier.definition], original = native.get(entity);
    const logical = original?.logical_counter ?? (carrier.display < rule.threshold ? carrier.display : null);
    const recovery = original ? "native_same_capture" : logical === null
      ? "ambiguous_missing_witness" : "legacy_same_snapshot_public_rule";
    if (logical === null) missing.push(`settled_relic_counters:${entity}:ambiguous_missing_witness`);
    else values.set(entity, logical);
    facts.push({ entity_id: entity, definition_id: carrier.definition, display_counter: carrier.display,
      logical_counter: logical, rule_id: rule.rule, recovery });
  }
  for (const carrier of carriers(projected)) if (values.has(carrier.entity)) carrier.value.counter = values.get(carrier.entity)!;
  return { basis: projected, receipt: { schema: "sts2.p1/settled-relic-counter-projection-1", revision: "r1.7",
    source_snapshot_id: String((basis.snapshot as Record<string, any>).snapshot_id), complete: missing.length === 0,
    facts, missing } };
}
