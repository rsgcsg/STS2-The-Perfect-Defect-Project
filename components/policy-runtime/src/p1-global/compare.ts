import { canonicalJson, completeCards, completeCombatSurface, completeCombat, completeDeck, completeMap, completePiles, completeSelector, createPublicGraphComparator, modelView, PublicGraphDifference, P1G_INFORMATION_SHAPES,
  createPublicIdentityPolicy, normalizePublicPath, publicFieldPath, completeRewardSurface, completeCardRewardSurface, P1G_REVISION, type P1Observation } from "@rsgcsg/sts2-connector-client/p1-global";

/** Formal full-public comparison. No text key is globally erased. Locale-specific
 * transformations require an owning schema/path declaration; r1.3 uses literal text.
 * Entity aliases may be renamed only through one injective global mapping. */
export interface FirstDifference { path: string; left: unknown; right: unknown; reason?: string }
type Verdict = "equal" | "different" | "incomparable";
function closedEntityRelations(observation: P1Observation): boolean {
  const policy = createPublicIdentityPolicy(observation.scene.native_kind, observation.extra_reads);
  const declared = new Set<string>(); const referenced: string[] = [];
  const visit = (value: unknown, path: string): void => {
    const relation = policy.relation(path);
    if (typeof value === "string" && relation) {
      if (relation === "reference") referenced.push(value);
      else declared.add(value);
    } else if (Array.isArray(value)) value.forEach((item, i) => visit(item, `${path}[${i}]`));
    else if (value && typeof value === "object") Object.entries(value).forEach(([key, child]) => visit(child, publicFieldPath(path, key)));
  };
  visit(modelView(observation), "");
  return referenced.every(value => declared.has(value));
}
function graphPolicy(identicalFacts: boolean, observation: P1Observation) {
  const policy = createPublicIdentityPolicy(observation.scene.native_kind, observation.extra_reads);
  // This declared inventory is entirely current-card DTOs. Unknown extra Read
  // schemas retain their literal order and identity policy.
  const cardInventory = observation.extra_reads.every(read => read.kind === "surface_card" && completeCards([read.content]));
  return createPublicGraphComparator({
    alias(value, _key, path) {
      const p = normalizePublicPath(path);
      if (p === "$.chosen" || p === "catalog[].choice_id") return { kind: "action", id: value };
      return policy.relation(path) ? { kind: "entity", id: value } : null;
    },
    unordered(path, parent) {
      // r1.3: only these schema-owned legal target domains are sets. All
      // members still participate in the same global entity bijection.
      const targetDomain = observation.scene.native_kind === "combat_turn" && [
        "$.observation.scene.content.surface.playable_cards[].target_entity_ids",
        "$.observation.scene.content.surface.usable_potions[].target_entity_ids"
      ].includes(path);
      return path === "$.observation.catalog" || targetDomain || !identicalFacts && (
        ["$.observation.deck.content.cards", "$.observation.map.content.nodes"].includes(path)
        || path === "$.observation.extra_reads" && cardInventory
        || path === "$.observation.piles.content.zones[].cards" && parent?.ordering === "unordered_multiset");
    }
  });
}
function supported(observation: P1Observation): string | null {
  if (observation.schema !== "sts2.p1/observation-1" || observation.profile !== "p1-global-r1" || observation.revision !== P1G_REVISION)
    return "unsupported_profile_revision";
  if (observation.scene.content_schema !== `sts2.player-environment/surface/${observation.scene.native_kind}-1`
      || (observation.scene.content as any)?.surface?.kind !== observation.scene.native_kind) return "unsupported_scene_schema_or_kind";
  if (!observation.envelope.required_information_complete) return "incomplete_required_information";
  if (!P1G_INFORMATION_SHAPES.includes(observation.scene.family)) return "unqualified_scene_information";
  if (observation.status === "unsupported") return "unsupported_observation";
  if (observation.status === "terminal") return ["win", "loss"].includes(observation.terminal?.outcome ?? "") ? null : "unknown_terminal";
  for (const part of ["run", "player", "deck", "map"] as const)
    if (observation[part].availability !== "available") return `required_${part}_unavailable`;
  if (!completeDeck(observation.deck.content) || !completeMap(observation.map.content,
      (observation.run.content as Record<string, any>)?.act_index ?? Number((observation.run.content as Record<string, any>)?.act) - 1))
    return "invalid_required_content";
  if (observation.combat.availability === "available" && (!completeCombat(observation.combat.content) || !completePiles(observation.piles.content)))
    return "invalid_combat_content";
  if (observation.scene.family === "combat" || observation.combat.availability === "available")
    for (const part of ["combat", "piles"] as const)
      if (observation[part].availability !== "available") return `required_${part}_unavailable`;
  if (observation.scene.native_kind === "combat_turn" && !completeCombatSurface((observation.scene.content as any)?.surface)) return "invalid_combat_surface";
  if (["selector", "upgrade", "enchant"].includes(observation.scene.family) && !completeSelector((observation.scene.content as any)?.surface))
    return "invalid_selector_content";
  if (observation.scene.native_kind === "reward_claim" && !completeRewardSurface((observation.scene.content as any)?.surface))
    return "invalid_reward_content";
  if (observation.scene.native_kind === "card_reward_selection" && !completeCardRewardSurface((observation.scene.content as any)?.surface))
    return "invalid_card_reward_content";
  if (new Set(observation.catalog.map(choice => choice.choice_id)).size !== observation.catalog.length || !closedEntityRelations(observation))
    return "unbound_or_ambiguous_public_relation";
  if (observation.catalog.some(choice => !choice.mapped)) return "unqualified_native_action_semantics";
  return null;
}
function bounded(value: unknown): boolean {
  const pending: [unknown, number][] = [[value, 0]]; let nodes = 0; let bytes = 0;
  while (pending.length) {
    const [item, depth] = pending.pop()!;
    if (++nodes > 500_000 || depth > 64) return false;
    if (typeof item === "string") bytes += Buffer.byteLength(item, "utf8");
    else if (item && typeof item === "object") {
      for (const [key, child] of Object.entries(item)) { bytes += Buffer.byteLength(key, "utf8"); pending.push([child, depth + 1]); if (pending.length + nodes > 500_000) return false; }
    }
    if (bytes > 8 * 1024 * 1024) return false;
  }
  return true;
}
/** One observation (+ optional chosen choice) against another, through the same full public graph. */
export function comparePublicPoint(left: P1Observation, right: P1Observation, chosenLeft: string | null, chosenRight: string | null):
  { verdict: Verdict; difference: FirstDifference | null } { return comparePoint(left, right, chosenLeft, chosenRight); }
function comparePoint(left: P1Observation, right: P1Observation, chosenLeft: string | null, chosenRight: string | null):
  { verdict: Verdict; difference: FirstDifference | null } {
  if (!bounded(left) || !bounded(right)) return { verdict: "incomparable",
    difference: { path: "$", left: null, right: null, reason: "comparison_work_bound" } };
  const unsupported = supported(left) ?? supported(right);
  if (unsupported) return { verdict: "incomparable", difference: { path: "$", left: null, right: null, reason: unsupported } };
  if (chosenLeft !== null && !left.catalog.some(choice => choice.choice_id === chosenLeft)
      || chosenRight !== null && !right.catalog.some(choice => choice.choice_id === chosenRight))
    return { verdict: "incomparable", difference: { path: "$.chosen", left: chosenLeft, right: chosenRight, reason: "choice_not_in_catalog" } };
  const a = { observation: modelView(left), chosen: chosenLeft }; const b = { observation: modelView(right), chosen: chosenRight };
  // Identity is itself a bijection and preserves every field/edge, including duplicates.
  if (canonicalJson(a) === canonicalJson(b)) return { verdict: "equal", difference: null };
  try {
    // Inventory order is transport metadata. Repeated cards/Reads must be solved
    // jointly with their catalog and chosen edges, under one global bijection.
    const graph = graphPolicy(canonicalJson(a.observation) === canonicalJson(b.observation), left);
    graph.compareWhole(a, b);
    return { verdict: "equal", difference: null };
  } catch (error) {
    if (error instanceof PublicGraphDifference) return { verdict: error.incomparable ? "incomparable" : "different",
      difference: { path: error.path, left: null, right: null, reason: error.reason } };
    return { verdict: "incomparable", difference: { path: "$", left: null, right: null, reason: "unsupported_public_structure" } };
  }
}

export interface PairReport {
  schema: "sts2.p1/pair-report-1"; mode: "exact" | "semantic"; verdict: Verdict;
  compared: number; matching: number; first_divergence: (FirstDifference & { occurrence: number }) | null;
  left_occurrences: number; right_occurrences: number; same_length: boolean;
  locale_policy: "literal_no_normalization";
}
export function comparePair(left: readonly P1Observation[], right: readonly P1Observation[], mode: "exact" | "semantic"): PairReport {
  const report: PairReport = { schema: "sts2.p1/pair-report-1", mode, verdict: "equal", compared: 0, matching: 0,
    first_divergence: null, left_occurrences: left.length, right_occurrences: right.length,
    same_length: left.length === right.length, locale_policy: "literal_no_normalization" };
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    const result = comparePoint(left[i]!, right[i]!, null, null); report.compared++;
    if (result.verdict === "equal") { report.matching++; continue; }
    report.verdict = result.verdict; report.first_divergence = { occurrence: left[i]!.envelope.occurrence, ...result.difference! }; return report;
  }
  if (!report.same_length) { report.verdict = "different"; report.first_divergence = { occurrence: left.length + 1,
    path: "$.occurrences.length", left: left.length, right: right.length, reason: "sequence_length_changed" }; }
  return report;
}

/** Diagnostic only: preserve each instance. This string is never an equivalence proof. */
export function choiceSignature(observation: P1Observation, choiceId: string): string | null {
  const choice = observation.catalog.find(item => item.choice_id === choiceId);
  return choice ? canonicalJson(choice) : null;
}
export function decisionSignature(observation: P1Observation, chosen: string | null): unknown {
  return { observation: modelView(observation), chosen };
}
export interface DecisionPairReport {
  schema: "sts2.p1/decision-pair-report-1"; verdict: Verdict; compared: number; matching: number;
  first_divergence: (FirstDifference & { occurrence: number }) | null;
  left_occurrences: number; right_occurrences: number; same_length: boolean;
  left_forced: number; right_forced: number; locale_policy: "literal_no_normalization";
}
type DecisionPoint = { observation: P1Observation; chosen: string | null; origin?: string | null };
export function compareDecisions(left: readonly DecisionPoint[], right: readonly DecisionPoint[]): DecisionPairReport {
  const report: DecisionPairReport = { schema: "sts2.p1/decision-pair-report-1", verdict: "equal", compared: 0, matching: 0,
    first_divergence: null, left_occurrences: left.length, right_occurrences: right.length, same_length: left.length === right.length,
    left_forced: left.filter(point => point.origin === "forced").length, right_forced: right.filter(point => point.origin === "forced").length,
    locale_policy: "literal_no_normalization" };
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    // r1.1 has no qualified mandatory proof. Do not erase observations labelled forced by an old producer.
    if (left[i]!.origin === "forced" || right[i]!.origin === "forced") {
      report.verdict = "incomparable"; report.first_divergence = { occurrence: left[i]!.observation.envelope.occurrence,
        path: "$.origin", left: left[i]!.origin, right: right[i]!.origin, reason: "unqualified_forced_input" }; return report;
    }
    const result = comparePoint(left[i]!.observation, right[i]!.observation, left[i]!.chosen, right[i]!.chosen); report.compared++;
    if (result.verdict === "equal") { report.matching++; continue; }
    report.verdict = result.verdict; report.first_divergence = { occurrence: left[i]!.observation.envelope.occurrence, ...result.difference! }; return report;
  }
  if (!report.same_length) { report.verdict = "different"; report.first_divergence = { occurrence: left.length + 1,
    path: "$.occurrences.length", left: left.length, right: right.length, reason: "sequence_length_changed" }; }
  return report;
}
