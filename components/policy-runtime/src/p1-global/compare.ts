import { canonicalJson, modelView, type P1Observation } from "@rsgcsg/sts2-connector-client/p1-global";

/**
 * Two-Host comparison of P1 observations offered at aligned occurrences.
 * "semantic" mode ignores locale/display text (name/label/description/title/prompt/keyword text),
 * which legitimately differs between a Chinese Reference profile and an English Managed process.
 * Nothing else is normalised: information, catalog kinds, aliases, relations and commitments must match.
 */
const TEXT_KEYS = new Set(["name", "label", "description", "title", "prompt", "act_name", "character_name", "text", "detail"]);

function strip(value: unknown, semantic: boolean): unknown {
  if (Array.isArray(value)) return value.map((item) => strip(item, semantic));
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (semantic && TEXT_KEYS.has(key)) continue;
      if (semantic && key === "keywords") continue;
      out[key] = strip(item, semantic);
    }
    return out;
  }
  return value;
}

export interface FirstDifference { path: string; left: unknown; right: unknown }

function firstDifference(left: unknown, right: unknown, path = "$"): FirstDifference | null {
  if (canonicalJson(left) === canonicalJson(right)) return null;
  if (Array.isArray(left) && Array.isArray(right)) {
    if (left.length !== right.length) return { path: `${path}.length`, left: left.length, right: right.length };
    for (let i = 0; i < left.length; i++) { const d = firstDifference(left[i], right[i], `${path}[${i}]`); if (d) return d; }
  }
  if (left && right && typeof left === "object" && typeof right === "object" && !Array.isArray(left) && !Array.isArray(right)) {
    const keys = [...new Set([...Object.keys(left), ...Object.keys(right)])].sort();
    for (const key of keys) {
      const d = firstDifference((left as Record<string, unknown>)[key], (right as Record<string, unknown>)[key], `${path}.${key}`);
      if (d) return d;
    }
  }
  return { path, left, right };
}

export interface PairReport {
  schema: "sts2.p1/pair-report-1"; mode: "exact" | "semantic";
  compared: number; matching: number; first_divergence: (FirstDifference & { occurrence: number }) | null;
  left_occurrences: number; right_occurrences: number;
}

export function comparePair(left: readonly P1Observation[], right: readonly P1Observation[], mode: "exact" | "semantic"): PairReport {
  const report: PairReport = { schema: "sts2.p1/pair-report-1", mode, compared: 0, matching: 0, first_divergence: null,
    left_occurrences: left.length, right_occurrences: right.length };
  const count = Math.min(left.length, right.length);
  for (let i = 0; i < count; i++) {
    const a = strip(modelView(left[i]!), mode === "semantic"); const b = strip(modelView(right[i]!), mode === "semantic");
    report.compared += 1;
    const difference = firstDifference(a, b);
    if (!difference) { report.matching += 1; continue; }
    report.first_divergence = { occurrence: left[i]!.envelope.occurrence, ...difference };
    break;
  }
  return report;
}

/**
 * Decision-level equivalence across Hosts. Public aliases and referent structure are Host-shaped
 * (one Host names a map option, the other the node) and display text is localised, so byte or
 * structural equality is not the cross-Host claim. Instead each occurrence is reduced to a
 * locale-free signature: scene family, the multiset of offered choices by (kind, subject and target
 * public identity), the chosen choice, and the decision-relevant public state.
 */
type Json = Record<string, any>;

function publicIdentity(observation: P1Observation): Map<string, string> {
  const names = new Map<string, string>();
  const visit = (value: unknown): void => {
    if (Array.isArray(value)) { for (const item of value) visit(item); return; }
    if (value === null || typeof value !== "object") return;
    const item = value as Json;
    if (typeof item.entity_id === "string" && !names.has(item.entity_id)) {
      if (typeof item.definition_id === "string") names.set(item.entity_id, `${item.definition_id}${item.is_upgraded ? "+" : ""}`);
      else if (Number.isInteger(item.col) && Number.isInteger(item.row)) names.set(item.entity_id, `${item.point_type ?? "node"}@${item.col},${item.row}`);
      else if (typeof item.kind === "string") names.set(item.entity_id, `${item.kind}`);
      else if (Number.isInteger(item.index)) names.set(item.entity_id, `option#${item.index}`);
    }
    if (typeof item.group_id === "string" && typeof item.kind === "string" && !names.has(item.group_id)) names.set(item.group_id, `${item.kind}`);
    for (const child of Object.values(item)) visit(child);
  };
  visit(modelView(observation));
  return names;
}

const sorted = (values: string[]) => [...values].sort();

export function choiceSignature(observation: P1Observation, choiceId: string, names = publicIdentity(observation)): string | null {
  const choice = observation.catalog.find((item) => item.choice_id === choiceId);
  if (!choice) return null;
  const who = (alias: string | null) => alias === null ? "-" : names.get(alias) ?? "?";
  return `${choice.kind}|${who(choice.subject)}|${who(choice.target)}`;
}

export function decisionSignature(observation: P1Observation, chosen: string | null): Json {
  const names = publicIdentity(observation);
  const player = (observation.player.content ?? {}) as Json;
  const combat = (observation.combat.content ?? null) as Json | null;
  const deck = ((observation.deck.content ?? {}) as Json).cards as Json[] | undefined;
  const defs = (items: unknown) => Array.isArray(items) ? sorted(items.map((item: Json) => `${item.definition_id}${item.is_upgraded ? "+" : ""}`)) : null;
  return {
    status: observation.status, family: observation.scene.family, terminal: observation.terminal,
    player: { hp: player.hp, max_hp: player.max_hp, gold: player.gold, character: player.character_definition_id,
      relics: defs(player.relics), potions: defs(player.potions) },
    deck: defs(deck),
    combat: combat ? { round: combat.round, energy: combat.player?.energy, block: combat.player?.block,
      hand: defs(combat.player?.hand), draw: combat.player?.draw_pile_count, discard: combat.player?.discard_pile_count,
      enemies: sorted((combat.enemies ?? []).map((enemy: Json) => `${enemy.definition_id}:${enemy.hp}/${enemy.max_hp}:${enemy.block}`)) } : null,
    commitments: { ...observation.commitments,
      declined_reward_groups: sorted(observation.commitments.declined_reward_groups.map((alias) => names.get(alias) ?? "?")) },
    catalog: sorted(observation.catalog.map((choice) => choiceSignature(observation, choice.choice_id, names)!)),
    chosen: chosen === null ? null : choiceSignature(observation, chosen, names)
  };
}

export interface DecisionPairReport {
  schema: "sts2.p1/decision-pair-report-1"; compared: number; matching: number;
  first_divergence: (FirstDifference & { occurrence: number }) | null;
  left_occurrences: number; right_occurrences: number; same_length: boolean;
  /** Forced (non-decision) observations left out of the comparison on each side. */
  left_forced: number; right_forced: number;
}

type DecisionPoint = { observation: P1Observation; chosen: string | null; origin?: string | null };

/**
 * Only Agent decision points and the terminal are compared: declared forced inputs (a page with one
 * continue-only input) are Host presentation, not decisions, and Hosts legitimately differ in them.
 */
const decisionPoints = (points: readonly DecisionPoint[]) =>
  points.filter((point) => point.observation.status === "terminal" || (point.origin ?? "agent_choice") === "agent_choice");

export function compareDecisions(allLeft: readonly DecisionPoint[], allRight: readonly DecisionPoint[]): DecisionPairReport {
  const left = decisionPoints(allLeft); const right = decisionPoints(allRight);
  const report: DecisionPairReport = { schema: "sts2.p1/decision-pair-report-1", compared: 0, matching: 0, first_divergence: null,
    left_occurrences: left.length, right_occurrences: right.length, same_length: left.length === right.length,
    left_forced: allLeft.length - left.length, right_forced: allRight.length - right.length };
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    report.compared += 1;
    const difference = firstDifference(decisionSignature(left[i]!.observation, left[i]!.chosen),
      decisionSignature(right[i]!.observation, right[i]!.chosen));
    if (!difference) { report.matching += 1; continue; }
    report.first_divergence = { occurrence: left[i]!.observation.envelope.occurrence, ...difference };
    break;
  }
  return report;
}
