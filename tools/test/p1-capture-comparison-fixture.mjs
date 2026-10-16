import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { decodeP1CapturedObservation, decodeP1CaptureHandle, serializeP1CaptureRecord,
  P1_CAPTURE_HANDLE_SCHEMA, P1_CAPTURE_PROFILE, P1_CAPTURE_READ_PROFILE,
  P1_CAPTURE_SCOPE_REQUIREMENTS, P1_CAPTURE_FULL_MISSING } from "../../components/connector/sdk/typescript/dist/index.js";
import { P1_COMPARISON_DECLARATION, sha256Text } from "../p1-capture-comparison.mjs";

export const producerSerializedFixture = () => readFileSync(new URL("../../components/connector/contracts/fixtures/p1-capture-c1.json", import.meta.url), "utf8");
export const producerFixture = () => JSON.parse(producerSerializedFixture());
// Complete shape follows the existing strict SDK completeSyntheticScope fixture.
// Native basis/aggregates below are synthetic, not claimed C# capture output.
export function completeSyntheticScope() {
  const packet = producerFixture();
  const card = entity_id => ({ ...packet.reads[0].content, entity_id });
  const deckCards = Array.from({ length: 12 }, (_, i) => card(`card-${i}`));
  const aggregates = [
    { instance_id: "deck-read", kind: "run_deck", source_read_id: "read:run_deck", target_entity_id: null,
      display_context: "deck", parent_read_instance_id: null, availability: "available",
      content_schema: "sts2.player-environment/read/run_deck-1", missing: [],
      content: { kind: "run_deck", card_count: deckCards.length, cards: deckCards } },
    { instance_id: "piles-read", kind: "combat_piles", source_read_id: "read:combat_piles", target_entity_id: null,
      display_context: "combat_piles", parent_read_instance_id: null, availability: "available",
      content_schema: "sts2.player-environment/read/combat_piles-1", missing: [], content: { kind: "combat_piles",
        zones: ["draw", "discard", "exhaust"].map(zone => ({ zone, card_count: zone === "draw" ? 2 : 0,
          ordering_semantics: "unordered_multiset", cards: zone === "draw" ? structuredClone([deckCards[1], deckCards[2]]) : [] })) } }
  ];
  const detail = (content, context, parent, index) => ({ instance_id: `complete-detail-${index}`, kind: "surface_card", source_read_id: null,
    target_entity_id: content.entity_id, display_context: context, parent_read_instance_id: parent,
    availability: "available", content_schema: "sts2.player-environment/p1-current-card-display-c1-1", content: structuredClone(content), missing: [] });
  const details = [...deckCards.map((card, index) => detail(card, "deck", "deck-read", index)),
    detail(deckCards[1], "draw", "piles-read", 12), detail(deckCards[2], "draw", "piles-read", 13), detail(deckCards[0], "hand", null, 14)];
  packet.reads = [...aggregates, ...details]; packet.scope_status = "complete";
  packet.native_basis.completeness = { ...packet.native_basis.completeness, status: "complete", missing: [] };
  packet.native_basis.persistent = { content_schema: "sts2.player-environment/persistent/run-player-1", content: {
    scope: "active_single_player_run", run: { act: 1, act_definition_id: "act", floor: 1, ascension: 0, bosses: [], modifiers: [] },
    player: { entity_id: "player", character_definition_id: "DEFECT", hp: 75, max_hp: 75, gold: 99,
      relics: [], potions: [], max_potion_slots: 3 },
    completeness: { player_visible_semantics: "synthetic_contract_fixture", sources: ["fixture"], missing: [] }
  } };
  packet.native_basis.status = "interactive"; packet.native_basis.interaction.stage = "ready";
  packet.native_basis.referents = deckCards.slice(0, 2).map(card => ({ referent_id: card.entity_id, role: "card", kind: "entity", label: "Strike",
    state: { visible: true, enabled: true, observation_basis: "native_visible_fact" } }));
  packet.native_basis.bound_actions.materialized_count = 1; packet.native_basis.bound_actions.total_count = 1;
  packet.native_basis.bound_actions.actions = [{ bound_action_id: "play-card", verb: "play",
    interaction_id: packet.native_basis.interaction.interaction_id, subject_referent_id: deckCards[0].entity_id,
    arguments: [{ role: "target", referent_id: deckCards[1].entity_id }], label: "Play Strike" }];
  packet.native_basis.reads = aggregates.map(read => ({ read_id: read.source_read_id, kind: read.kind,
    target_referent_id: null, content_schema: read.content_schema, visibility_basis: "normal_player_read",
    snapshot_bound: true, ordering_semantics: "unordered_multiset", hidden_by_policy: [] }));
  // Fields follow CombatLiveContext/VisibleCombatPlayer, not a new P1 schema.
  packet.native_basis.interaction.content.context = { kind: "combat", encounter_type: "normal", round: 1,
    turn_owner: "player", is_play_phase: true, player: { player_entity_id: "player", block: 0, energy: 3,
      max_energy: 3, hand: [structuredClone(deckCards[0])], draw_pile_count: 2, discard_pile_count: 0, exhaust_pile_count: 0,
      statuses: [{ definition_id: "STRENGTH", name: "Strength", amount: 1, type: "Buff", description: "More damage." }],
      companions: [], potion_states: [], orbs: [] }, enemies: [{ entity_id: "enemy", definition_id: "SLIME",
      hp: 20, max_hp: 20, block: 0, statuses: [], intents: [{ type: "Attack", label: "", title: "Attack", description: "Deal damage." }],
      health_bar_visible: true, hp_display_mode: "normal", hp_numbers_available: true }] };
  packet.coverage = [
    ...P1_CAPTURE_SCOPE_REQUIREMENTS.map(requirement_id => ({ requirement_id, required: true, availability: "available", missing: [],
      read_instance_ids: requirement_id === "run_deck" ? ["deck-read"] : requirement_id === "combat_piles" ? ["piles-read"]
        : requirement_id === "surface_card_instances" ? details.map(detail => detail.instance_id) : [] })),
    ...packet.reads.map(read => ({ requirement_id: `read_instance:${read.instance_id}`, required: true,
      availability: "available", missing: [], read_instance_ids: [read.instance_id] })),
    ...P1_CAPTURE_FULL_MISSING.map(requirement_id => ({ requirement_id, required: true, availability: "unsupported", missing: [requirement_id], read_instance_ids: [] }))
  ];
  return packet;
}
export function archive(packet, captureSuffix = "a", text = JSON.stringify(packet)) {
  const capture = decodeP1CaptureHandle({ schema: P1_CAPTURE_HANDLE_SCHEMA, read_profile: P1_CAPTURE_READ_PROFILE,
    input_profile: P1_CAPTURE_PROFILE, capture_id: `capture-${captureSuffix}`, source_snapshot_id: packet.source_snapshot_id,
    session: packet.session, generation_id: `generation-${captureSuffix}`, game_continuity_id: `run-${captureSuffix}`,
    captured_at: packet.captured_at, expires_at: "2027-10-15T00:02:00+00:00", total_bytes: Buffer.byteLength(text),
    sha256: createHash("sha256").update(text).digest("hex"), first_cursor: "cursor-0", capture_ordinal: 1 }).data;
  return serializeP1CaptureRecord({ capture, serializedObservation: text, observation: decodeP1CapturedObservation(packet).data });
}
export function renamed(packet) {
  const aliases = new Map();
  const keys = new Set(["entity_id", "target_entity_id", "referent_id", "subject_referent_id", "instance_id", "parent_read_instance_id",
    "source_read_id", "read_id", "player_entity_id", "read_instance_ids", "bound_action_id", "interaction_id", "snapshot_id", "source_snapshot_id"]);
  const alias = value => aliases.get(value) ?? (aliases.set(value, `alias-${aliases.size}`), aliases.get(value));
  function visit(value, key) {
    if (typeof value === "string" && keys.has(key)) return alias(value);
    if (key === "requirement_id" && typeof value === "string" && value.startsWith("read_instance:")) return `read_instance:${alias(value.slice(14))}`;
    if (Array.isArray(value)) return value.map(child => visit(child, key));
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, visit(v, k)]));
    return value;
  }
  const copy = visit(structuredClone(packet));
  copy.session = { runtime_instance_id: "runtime-other", environment_fingerprint: "environment-other" };
  copy.native_basis.session = copy.session; copy.captured_at = "2026-10-16T01:00:00+00:00";
  copy.native_basis.observed_at = copy.captured_at; copy.native_basis.sequence = 99;
  return copy;
}
export function inputs(left = completeSyntheticScope(), right = renamed(left), originalTexts = {}) {
  const target = { protocol_version: "1.0.0", information_policy_id: "player_visible_v1", game_build: { version: "v0.111.0", commit: "41cef1ea", main_assembly_hash: 1010476334 } };
  const declaration = { schema: P1_COMPARISON_DECLARATION, semantic_target: target,
    scenario: { scenario_id: "synthetic-combat", seed: "SYNTHETIC", observation_boundary: "explicit-synthetic-sample", alignment_basis: "externally_asserted" } };
  const result = {};
  for (const [side, packet, suffix] of [["reference", left, "a"], ["candidate", right, "b"]]) {
    const record = archive(packet, suffix, originalTexts[side]); const capture = JSON.parse(record).capture;
    const report = JSON.stringify({ synthetic: true, loaded_identity: { protocol: target.protocol_version,
      host: { runtime_instance_id: capture.session.runtime_instance_id, host_kind: "synthetic_fixture", implementation: {
        source_revision: "0".repeat(40), artifact_sha256: "1".repeat(64), module_version_id: "00000000-0000-0000-0000-000000000001" } }, game: target.game_build },
      episode_provenance: { actual_seed: "SYNTHETIC" } });
    result[side] = { record, report };
    declaration[side] = { record_sha256: sha256Text(record), report_sha256: sha256Text(report), capture_id: capture.capture_id,
      capture_sha256: capture.sha256, source_snapshot_id: capture.source_snapshot_id, session: capture.session };
  }
  return { ...result, declaration: JSON.stringify(declaration) };
}
