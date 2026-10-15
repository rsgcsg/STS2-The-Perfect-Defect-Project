import { createHash } from "node:crypto";
import { readP1CaptureRecord, P1_CAPTURE_FULL_MISSING, P1_CAPTURE_SCOPE } from "../components/connector/sdk/typescript/dist/index.js";

export const P1_COMPARISON_DECLARATION = "sts2.project/p1-c1-comparison-declaration-1";
export const sha256Text = text => createHash("sha256").update(text, "utf8").digest("hex");
const MAX_DOCUMENT_BYTES = 64 * 1024 * 1024;
// Paths are bound to the current C1 and legacy Snapshot contracts. Unknown JSON
// extension locations remain literal even if a field is named entity_id.
function aliasPolicy(observation) {
  const entityPaths = new Set([
    "$.native_basis.interaction.content.context.player.player_entity_id",
    "$.native_basis.interaction.content.context.player.hand[].entity_id",
    "$.native_basis.interaction.content.context.player.companions[].entity_id",
    "$.native_basis.interaction.content.context.player.potion_states[].entity_id",
    "$.native_basis.interaction.content.context.player.orbs[].entity_id",
    "$.native_basis.interaction.content.context.enemies[].entity_id",
    "$.native_basis.interaction.content.surface.room_entity_id",
    ...["playable_cards", "usable_potions"].flatMap(collection => [
      `$.native_basis.interaction.content.surface.${collection}[].entity_id`,
      `$.native_basis.interaction.content.surface.${collection}[].target_entity_ids[]`]),
    "$.native_basis.persistent.content.player.entity_id",
    "$.native_basis.persistent.content.player.relics[].entity_id",
    "$.native_basis.persistent.content.player.potions[].entity_id",
    "$.native_basis.persistent.content.player.potions[].card_previews[].entity_id",
    "$.native_basis.persistent.content.player.relics[].card_previews[].entity_id",
    "$.native_basis.persistent.content.run.modifiers[].card_previews[].entity_id",
    "$.native_basis.referents[].referent_id",
    "$.native_basis.bound_actions.actions[].subject_referent_id",
    "$.native_basis.bound_actions.actions[].arguments[].referent_id",
    "$.native_basis.reads[].target_referent_id",
    "$.reads.run_deck.target_entity_id", "$.reads.combat_piles.target_entity_id",
    "$.reads.surface_card[].target_entity_id", "$.reads.surface_card[].content.entity_id",
    "$.reads.run_deck.content.cards[].entity_id",
    "$.reads.combat_piles.content.zones[].cards[].entity_id"
  ]);
  const propertyPaths = new Map(Object.entries({
    card: [".entity_id"], hand: [".entity_id"], enemy: [".entity_id"], companion: [".entity_id"],
    potion_state: [".entity_id"], orb: [".entity_id"], potion: [".entity_id"],
    playable_card: [".entity_id", ".target_entity_ids[]"], usable_potion: [".entity_id", ".target_entity_ids[]"],
    relic: [".entity_id", ".card_previews[].entity_id"],
    player: [".player_entity_id", ".hand[].entity_id", ".companions[].entity_id", ".potion_states[].entity_id", ".orbs[].entity_id"]
  }).map(([role, paths]) => [`sts2.player-environment/referent/${role}-1`, paths]));
  return (key, path) => {
    const normalized = path.replace(/\[\d+\]/gu, "[]");
    if (entityPaths.has(normalized) && (!normalized.startsWith("$.native_basis.interaction.content.surface.")
        || observation.native_basis.interaction.content.surface.kind === "combat_turn")) return "entity";
    const property = /^\$\.native_basis\.referents\[(\d+)\]\.properties(.*)$/u.exec(path);
    if (property) {
      const allowed = propertyPaths.get(observation.native_basis.referents[Number(property[1])]?.properties_schema);
      if (allowed?.includes(property[2].replace(/\[\d+\]/gu, "[]"))) return "entity";
    }
    if (["$.source_snapshot_id", "$.native_basis.snapshot_id"].includes(normalized)) return "snapshot";
    if (["$.native_basis.interaction.interaction_id", "$.native_basis.bound_actions.actions[].interaction_id"].includes(normalized)) return "interaction";
    if (normalized === "$.native_basis.bound_actions.actions[].bound_action_id") return "action";
    if (normalized === "$.native_basis.reads[].read_id") return "read";
    if (/^\$\.reads\.(?:run_deck|combat_piles|surface_card\[\])\.(?:instance_id|parent_read_instance_id)$/u.test(normalized)
        || normalized === "$.coverage[].read_instance_ids[]") return "instance";
    if (/^\$\.reads\.(?:run_deck|combat_piles|surface_card\[\])\.source_read_id$/u.test(normalized)) return "read";
    if (normalized === "$.coverage[].requirement_id") return "requirement";
    return null;
  };
}
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const nonempty = value => typeof value === "string" && value.length > 0 && value.length <= 512;
const canonical = value => JSON.stringify(value, (_, child) => object(child)
  ? Object.fromEntries(Object.keys(child).sort().map(key => [key, child[key]])) : child);
class Difference extends Error {
  constructor(path, reason, incomparable = false) { super(reason); this.path = path.slice(0, 240); this.reason = reason; this.incomparable = incomparable; }
}
const fail = (path, reason, incomparable = false) => { throw new Difference(path, reason, incomparable); };
const safeKey = key => /^[A-Za-z0-9_:-]{1,80}$/u.test(key) ? key : "<field>";
function parseDocument(text, path) {
  if (typeof text !== "string" || Buffer.byteLength(text, "utf8") > MAX_DOCUMENT_BYTES) fail(path, "missing_or_oversized_document", true);
  try { return JSON.parse(text); } catch { fail(path, "invalid_json", true); }
}
function verifyAssociation(side, input, full, declaration) {
  const binding = declaration[side];
  const capture = full.capture;
  if (!object(binding) || binding.record_sha256 !== sha256Text(input.record) || binding.report_sha256 !== sha256Text(input.report)
      || binding.capture_id !== capture.capture_id || binding.capture_sha256 !== capture.sha256
      || binding.source_snapshot_id !== capture.source_snapshot_id || canonical(binding.session) !== canonical(capture.session))
    fail(`$.${side}.association`, "exact_record_report_capture_session_association_required", true);
  const report = parseDocument(input.report, `$.${side}.report`);
  const loaded = report?.loaded_identity;
  const target = declaration.semantic_target;
  if (loaded?.host?.runtime_instance_id !== capture.session.runtime_instance_id)
    fail(`$.${side}.report.loaded_identity`, "foreign_or_missing_runtime", true);
  if (loaded?.protocol !== target.protocol_version || canonical(loaded?.game && {
    version: loaded.game.version, commit: loaded.game.commit, main_assembly_hash: loaded.game.main_assembly_hash
  }) !== canonical(target.game_build) || full.observation.native_basis.information_policy.id !== target.information_policy_id)
    fail(`$.${side}.report.loaded_identity`, "semantic_target_not_bound", true);
  if (report?.episode_provenance?.actual_seed !== declaration.scenario.seed)
    fail(`$.${side}.report.episode_provenance`, "declared_seed_not_bound", true);
  const implementation = loaded.host.implementation;
  if (!/^[a-z0-9_]{1,64}$/u.test(loaded.host.host_kind ?? "") || !/^[a-f0-9]{40}$/u.test(implementation?.source_revision ?? "")
      || !/^[a-f0-9]{64}$/u.test(implementation?.artifact_sha256 ?? "")
      || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/u.test(implementation?.module_version_id ?? "")
      || !nonempty(capture.session.runtime_instance_id) || !nonempty(capture.session.environment_fingerprint))
    fail(`$.${side}.report.loaded_identity`, "missing_implementation_provenance", true);
  return { record_sha256: binding.record_sha256, report_sha256: binding.report_sha256,
    public_bytes_sha256: capture.sha256, capture_id: capture.capture_id,
    source_snapshot_id: capture.source_snapshot_id, session: capture.session,
    captured_at: capture.captured_at, generation_id: capture.generation_id,
    game_continuity_id: capture.game_continuity_id, capture_ordinal: capture.capture_ordinal,
    host_kind: loaded.host.host_kind, implementation: { source_revision: implementation.source_revision,
      artifact_sha256: implementation.artifact_sha256, module_version_id: implementation.module_version_id } };
}

/** Offline diagnostic only. Original records are validated exclusively by the current Connector SDK.
 * A supplied report/declaration is an external association assertion, not a producer witness. */
export async function compareP1CaptureRecords({ reference, candidate, declaration: declarationText } = {}) {
  const result = { schema: "sts2.project/p1-c1-comparison-1", verdict: "c1_incomparable",
    diagnostic_only: true, agent_admissible: false, creates_mutation_authority: false, p1_status: "unsupported",
    capture_scope: P1_CAPTURE_SCOPE, environment_alignment: "externally_asserted",
    cross_host_qualification: "incomparable", alignment_verified: false,
    compared_sections: [], unsupported_sections: [...P1_CAPTURE_FULL_MISSING],
    provenance: {}, original_digests: {}, first_difference: null };
  for (const [side, input] of [["reference", reference], ["candidate", candidate]]) {
    result.original_digests[side] = {};
    for (const name of ["record", "report"]) if (typeof input?.[name] === "string"
        && Buffer.byteLength(input[name], "utf8") <= MAX_DOCUMENT_BYTES)
      result.original_digests[side][`${name}_sha256`] = sha256Text(input[name]);
  }
  try {
    const declaration = parseDocument(declarationText, "$.declaration");
    if (declaration?.schema !== P1_COMPARISON_DECLARATION || !object(declaration.semantic_target)
        || !nonempty(declaration.semantic_target.protocol_version) || !nonempty(declaration.semantic_target.information_policy_id)
        || !object(declaration.semantic_target.game_build) || !nonempty(declaration.semantic_target.game_build.version)
        || !nonempty(declaration.semantic_target.game_build.commit) || !Number.isSafeInteger(declaration.semantic_target.game_build.main_assembly_hash)
        || !nonempty(declaration.scenario?.scenario_id) || !nonempty(declaration.scenario?.seed)
        || !nonempty(declaration.scenario?.observation_boundary) || declaration.scenario?.alignment_basis !== "externally_asserted")
      fail("$.declaration", "explicit_semantic_target_scenario_boundary_required", true);
    const records = [];
    for (const [side, input] of [["reference", reference], ["candidate", candidate]]) {
      if (!input || typeof input.record !== "string" || typeof input.report !== "string") fail(`$.${side}`, "original_record_and_report_required", true);
      let full;
      if (Buffer.byteLength(input.record, "utf8") > MAX_DOCUMENT_BYTES) fail(`$.${side}.record`, "record_byte_bound_exceeded", true);
      try { full = await readP1CaptureRecord(input.record); } catch { fail(`$.${side}.record`, "strict_sdk_record_verification_failed", true); }
      result.provenance[side] = verifyAssociation(side, input, full, declaration);
      if (full.observation.scope_status !== "complete") fail(`$.${side}.scope_status`, "complete_c1_scope_required", true);
      records.push(full);
    }
    const [left, right] = records;
    const sameCapture = left.capture.capture_id === right.capture.capture_id
      && left.capture.session.runtime_instance_id === right.capture.session.runtime_instance_id;
    if (sameCapture && left.capture.sha256 !== right.capture.sha256)
      fail("$.capture", "conflicting_bytes_for_same_capture_identity", true);
    if (reference.record === candidate.record || sameCapture) {
      result.verdict = "same_record"; return result;
    }
    const a = left.observation; const b = right.observation;
    const aliasKind = aliasPolicy(a);
    const forward = new Map(); const reverse = new Map();
    function alias(kind, a, b, path) {
      const ka = `${kind}:${a}`; const kb = `${kind}:${b}`;
      if ((forward.has(ka) && forward.get(ka) !== b) || (reverse.has(kb) && reverse.get(kb) !== a))
        fail(path, "opaque_alias_equality_or_bijection_changed");
      forward.set(ka, b); reverse.set(kb, a);
    }
    // Fingerprints only select a unique candidate. They never establish equality;
    // compare() still verifies every field and a single global bijection.
    function selector(value, side, key = "", path = "$", depth = 0, mapping = { forward, reverse }) {
      if (depth > 64) fail("$", "comparison_depth_bound", true);
      // Unknown aliases on either side have no identifying power.
      if (typeof value === "string" && aliasKind(key, path) && aliasKind(key, path) !== "requirement") {
        const kind = aliasKind(key, path);
        return ["alias", kind, side === "left" ? (mapping.forward.has(`${kind}:${value}`) ? value : "<unbound>")
          : mapping.reverse.get(`${kind}:${value}`) ?? "<unbound>"];
      }
      if (typeof value === "string" && aliasKind(key, path) === "requirement" && value.startsWith("read_instance:"))
        return ["requirement_instance", side === "left" ? value.slice(14) : mapping.reverse.get(`instance:${value.slice(14)}`) ?? "<unbound>"];
      if (Array.isArray(value)) {
        const children = value.map((child, index) => selector(child, side, key, `${path}[${index}]`, depth + 1, mapping));
        return path.replace(/\[\d+\]/gu, "[]") === "$.coverage[].read_instance_ids" ? children.sort((a, b) => canonical(a).localeCompare(canonical(b))) : children;
      }
      if (!object(value)) return value;
      return Object.fromEntries(Object.keys(value).sort().map(child => [child, selector(value[child], side, child, `${path}.${safeKey(child)}`, depth + 1, mapping)]));
    }
    function unordered(a, b, path, key, identityOnly = false) {
      if (a.length !== b.length) fail(path, "collection_multiplicity_changed");
      const groups = new Map();
      // Freeze the selector view: matching earlier members can grow the global
      // bijection, but cannot invalidate keys already indexed in this collection.
      const mapping = { forward: new Map(forward), reverse: new Map(reverse) };
      const signature = (value, side) => canonical(selector(identityOnly ? {
        kind: value.kind, target_entity_id: value.target_entity_id, display_context: value.display_context,
        parent_read_instance_id: value.parent_read_instance_id
      } : value, side, key, `${path}[0]`, 0, mapping));
      for (const value of b) { const sig = signature(value, "right"); const group = groups.get(sig) ?? []; group.push(value); groups.set(sig, group); }
      for (let index = 0; index < a.length; index++) {
        const group = groups.get(signature(a[index], "left"));
        if (!group?.length) fail(`${path}[${index}]`, "collection_member_or_relation_changed");
        if (group.length !== 1) fail(`${path}[${index}]`, "ambiguous_unordered_alias_mapping", true);
        compare(a[index], group.pop(), `${path}[${index}]`, key);
      }
    }
    let comparedNodes = 0;
    function compare(a, b, path, key = "", parent = null, depth = 0) {
      if (++comparedNodes > 500_000 || depth > 64) fail("$", "comparison_work_bound", true);
      if (typeof a === "string" && typeof b === "string") {
        if (aliasKind(key, path) === "requirement" && a.startsWith("read_instance:") && b.startsWith("read_instance:"))
          return alias("instance", a.slice(14), b.slice(14), path);
        const kind = aliasKind(key, path); if (kind && kind !== "requirement") return alias(kind, a, b, path);
      }
      if (Array.isArray(a) && Array.isArray(b)) {
        const normalized = path.replace(/\[\d+\]/gu, "[]");
        if (normalized === "$.coverage[].read_instance_ids" || normalized === "$.reads.combat_piles.content.zones[].cards"
            && parent?.ordering_semantics === "unordered_multiset")
          return unordered(a, b, path, key);
        if (a.length !== b.length) fail(path, "sequence_length_changed");
        for (let i = 0; i < a.length; i++) compare(a[i], b[i], `${path}[${i}]`, key, null, depth + 1);
        return;
      }
      if (object(a) && object(b)) {
        const ak = Object.keys(a).sort(); const bk = Object.keys(b).sort();
        if (canonical(ak) !== canonical(bk)) fail(path, "field_presence_changed");
        for (const child of ak) compare(a[child], b[child], `${path}.${safeKey(child)}`, child, a, depth + 1);
        return;
      }
      if (a !== b) fail(path, "semantic_value_changed");
    }
    const basis = observation => { const { session, sequence, observed_at, ...semantic } = observation.native_basis; return semantic; };
    compare(a.source_snapshot_id, b.source_snapshot_id, "$.source_snapshot_id", "source_snapshot_id");
    compare(basis(a), basis(b), "$.native_basis"); result.compared_sections.push("native_basis");
    // Aggregates establish card aliases before matching the instance collection.
    for (const kind of ["run_deck", "combat_piles"]) {
      compare(a.reads.find(read => read.kind === kind), b.reads.find(read => read.kind === kind), `$.reads.${kind}`);
    }
    unordered(a.reads.filter(read => read.kind === "surface_card"), b.reads.filter(read => read.kind === "surface_card"), "$.reads.surface_card", "", true);
    if (a.reads.some(read => !["surface_card", "run_deck", "combat_piles"].includes(read.kind))
        || b.reads.some(read => !["surface_card", "run_deck", "combat_piles"].includes(read.kind)))
      fail("$.reads", "unsupported_read_mapping", true);
    result.compared_sections.push("all_read_instances");
    unordered(a.coverage, b.coverage, "$.coverage", ""); result.compared_sections.push("required_coverage");
    const metadata = observation => { const { session, captured_at, source_snapshot_id, native_basis, reads, coverage, ...rest } = observation; return rest; };
    compare(metadata(a), metadata(b), "$"); result.compared_sections.push("scope_and_full_p1_gaps");
    result.verdict = "c1_scope_match";
  } catch (error) {
    if (!(error instanceof Difference)) {
      result.first_difference = { path: "$", reason: "unsupported_document_structure" };
      return result;
    }
    result.verdict = error.incomparable ? "c1_incomparable" : "c1_scope_mismatch";
    result.first_difference = { path: error.path, reason: error.reason };
  }
  return result;
}
