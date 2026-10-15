import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { assembleP1Capture, decodeP1CapturedObservation, decodeP1CaptureHandle,
  readP1CaptureRecord, serializeP1CaptureRecord, PlayerEnvironmentRestClient,
  P1_CAPTURE_PROFILE, P1_CAPTURE_READ_PROFILE, P1_CAPTURE_HANDLE_SCHEMA,
  P1_CAPTURE_CHUNK_SCHEMA, P1_CAPTURE_RELEASE_SCHEMA, P1_CAPTURE_ROUTE, P1_CAPTURE_SCOPE_REQUIREMENTS, P1_CAPTURE_FULL_MISSING,
  type P1CaptureHandle, type P1CaptureChunk } from "../src/index.js";

function source() {
  // This fixture is emitted and independently checked by the real C# frozen
  // packet producer. It is synthetic/diagnostic, never a captured game claim.
  const serialized = readFileSync(new URL("../../../contracts/fixtures/p1-capture-c1.json", import.meta.url), "utf8");
  const bytes = Buffer.from(serialized, "utf8");
  const capture: P1CaptureHandle = {
    schema: P1_CAPTURE_HANDLE_SCHEMA, read_profile: P1_CAPTURE_READ_PROFILE,
    input_profile: P1_CAPTURE_PROFILE, capture_id: "capture-fixture", source_snapshot_id: "state-fixture",
    session: { runtime_instance_id: "runtime-fixture", environment_fingerprint: "environment-fixture" },
    generation_id: "generation-fixture", game_continuity_id: "run-fixture",
    captured_at: "2026-10-15T00:00:00+00:00", expires_at: "2026-10-15T00:02:00+00:00",
    total_bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex"),
    first_cursor: "cursor-0", capture_ordinal: 1
  };
  const block = ({ cursor, maxBytes = 1024 }: { cursor: string; maxBytes?: number }): P1CaptureChunk => {
    const offset = Number(cursor.slice("cursor-".length));
    const data = bytes.subarray(offset, offset + maxBytes);
    const next = offset + data.length;
    return { schema: P1_CAPTURE_CHUNK_SCHEMA, capture_id: capture.capture_id, sha256: capture.sha256,
      total_bytes: bytes.length, offset, data_base64: data.toString("base64"),
      end: next === bytes.length, next_cursor: next === bytes.length ? null : `cursor-${next}` };
  };
  const readP1Capture = vi.fn(async (input: { captureId: string; cursor: string; maxBytes?: number }) => {
    const data = block(input); return { raw: data as any, data };
  });
  return { serialized, bytes, capture, block, readP1Capture };
}

function completeSyntheticScope() {
  const packet = JSON.parse(source().serialized);
  const card = (entity_id: string) => ({ ...packet.reads[0].content, entity_id });
  const deckCards = [card("shared"), card("same-definition-second-instance")];
  const drawCard = card("draw-card");
  const aggregates = [
    { instance_id: "deck-read", kind: "run_deck", source_read_id: "read:run_deck", target_entity_id: null,
      display_context: "deck", parent_read_instance_id: null, availability: "available",
      content_schema: "sts2.player-environment/read/run_deck-1", missing: [],
      content: { kind: "run_deck", card_count: 2, cards: deckCards } },
    { instance_id: "piles-read", kind: "combat_piles", source_read_id: "read:combat_piles", target_entity_id: null,
      display_context: "combat_piles", parent_read_instance_id: null, availability: "available",
      content_schema: "sts2.player-environment/read/combat_piles-1", missing: [], content: { kind: "combat_piles",
        zones: ["draw", "discard", "exhaust"].map(zone => ({ zone, card_count: zone === "draw" ? 1 : 0,
          ordering_semantics: "unordered_multiset", cards: zone === "draw" ? [structuredClone(drawCard)] : [] })) } }
  ];
  const detail = (content: any, context: string, parent: string | null, index: number) => ({
    instance_id: `complete-detail-${index}`, kind: "surface_card", source_read_id: null,
    target_entity_id: content.entity_id, display_context: context, parent_read_instance_id: parent,
    availability: "available", content_schema: "sts2.player-environment/p1-current-card-display-c1-1", content: structuredClone(content), missing: []
  });
  const details = [...deckCards.map((card, index) => detail(card, "deck", "deck-read", index)),
    detail(drawCard, "draw", "piles-read", 2), detail(deckCards[0], "hand", null, 3)];
  packet.reads = [...aggregates, ...details]; packet.scope_status = "complete";
  packet.native_basis.completeness = { ...packet.native_basis.completeness, status: "complete", missing: [] };
  packet.native_basis.persistent = { content_schema: "sts2.player-environment/persistent/run-player-1", content: {
    scope: "active_single_player_run", run: { act: 1, act_definition_id: "act", floor: 1, ascension: 0, bosses: [], modifiers: [] },
    player: { entity_id: "player", character_definition_id: "DEFECT", hp: 75, max_hp: 75, gold: 99,
      relics: [], potions: [], max_potion_slots: 3 },
    completeness: { player_visible_semantics: "synthetic_contract_fixture", sources: ["fixture"], missing: [] }
  } };
  packet.native_basis.status = "interactive"; packet.native_basis.interaction.stage = "ready";
  packet.native_basis.bound_actions.materialized_count = 1; packet.native_basis.bound_actions.total_count = 1;
  packet.native_basis.bound_actions.actions = [{ bound_action_id: "end-turn", verb: "end_turn",
    interaction_id: packet.native_basis.interaction.interaction_id, subject_referent_id: null, arguments: [], label: "End Turn" }];
  packet.native_basis.reads = aggregates.map(read => ({ read_id: read.source_read_id, kind: read.kind,
    target_referent_id: null, content_schema: read.content_schema, visibility_basis: "normal_player_read",
    snapshot_bound: true, ordering_semantics: "unordered_multiset", hidden_by_policy: [] }));
  // Fields follow CombatLiveContext/VisibleCombatPlayer, not a new P1 schema.
  packet.native_basis.interaction.content.context = { kind: "combat", encounter_type: "normal", round: 1,
    turn_owner: "player", is_play_phase: true, player: { player_entity_id: "player", block: 0, energy: 3,
      max_energy: 3, hand: [structuredClone(deckCards[0])], draw_pile_count: 1, discard_pile_count: 0, exhaust_pile_count: 0,
      statuses: [{ definition_id: "STRENGTH", name: "Strength", amount: 1, type: "Buff", description: "More damage." }],
      companions: [], potion_states: [], orbs: [] }, enemies: [{ entity_id: "enemy", definition_id: "SLIME",
      hp: 20, max_hp: 20, block: 0, statuses: [], intents: [{ type: "Attack", label: "", title: "Attack", description: "Deal damage." }],
      health_bar_visible: true, hp_display_mode: "normal", hp_numbers_available: true }] };
  packet.coverage = [
    ...P1_CAPTURE_SCOPE_REQUIREMENTS.map(requirement_id => ({ requirement_id, required: true, availability: "available", missing: [],
      read_instance_ids: requirement_id === "run_deck" ? ["deck-read"] : requirement_id === "combat_piles" ? ["piles-read"]
        : requirement_id === "surface_card_instances" ? details.map(detail => detail.instance_id) : [] })),
    ...packet.reads.map((read: any) => ({ requirement_id: `read_instance:${read.instance_id}`, required: true,
      availability: "available", missing: [], read_instance_ids: [read.instance_id] })),
    ...P1_CAPTURE_FULL_MISSING.map(requirement_id => ({ requirement_id, required: true, availability: "unsupported",
      missing: [requirement_id], read_instance_ids: [] }))
  ];
  return packet;
}

describe("P1 C1 atomic public capture reader and recording seam", () => {
  it("keeps >8 same-kind detail instances and duplicate-entity contexts without claiming P1 admission", async () => {
    const s = source(); const full = await assembleP1Capture(s.capture, s, 1024);
    expect(full.serializedObservation).toBe(s.serialized);
    expect(full.observation.reads).toHaveLength(12);
    expect(full.observation.reads.slice(0, 2).map(read => [read.target_entity_id, read.display_context]))
      .toEqual([["card-shared", "deck"], ["card-shared", "hand"]]);
    expect(full.observation.agent_admissible).toBe(false);
    expect(full.observation.creates_mutation_authority).toBe(false);
    expect(full.observation.p1_status).toBe("unsupported");
    expect(full.observation.native_basis.schema).toBe("sts2.player-environment/snapshot-1");
  });

  it("rejects overflowing JSON numeric tokens even with a recomputed archive digest", async () => {
    for (const location of ["context", "persistent"]) {
      const packet = completeSyntheticScope();
      if (location === "context") packet.native_basis.interaction.content.context.player.block = 123456789;
      else packet.native_basis.persistent.content.player.hp = 123456789;
      const serialized = JSON.stringify(packet).replace("123456789", "1e400");
      const record = { schema: "sts2.player-environment/p1-atomic-capture-record-c1-1", capture: {
        ...source().capture, total_bytes: Buffer.byteLength(serialized),
        sha256: createHash("sha256").update(serialized).digest("hex") }, serialized_observation: serialized };
      await expect(readP1CaptureRecord(JSON.stringify(record))).rejects.toThrow("finite JSON values");
    }
  });

  it("transport, recording and comparison reread exactly one capture without a second native acquisition", async () => {
    const s = source();
    const [consumer, comparator] = await Promise.all([
      assembleP1Capture(s.capture, s, 1024), assembleP1Capture(s.capture, s, 1024)
    ]);
    expect(comparator.serializedObservation).toBe(consumer.serializedObservation);
    const directory = mkdtempSync(join(tmpdir(), "p1-capture-record-"));
    try {
      const archive = join(directory, "capture.json");
      writeFileSync(archive, serializeP1CaptureRecord(consumer), "utf8");
      const callsBeforeArchive = s.readP1Capture.mock.calls.length;
      const recorded = await readP1CaptureRecord(readFileSync(archive, "utf8"));
      expect(recorded).toEqual(consumer);
      expect(s.readP1Capture.mock.calls.length).toBe(callsBeforeArchive);
      // No reconstruction from mutable parsed JSON can alter the stored text.
      consumer.observation.reads[0]!.content = { description: "mutated parsed copy" };
      expect((await readP1CaptureRecord(readFileSync(archive, "utf8"))).serializedObservation).toBe(s.serialized);
    } finally { rmSync(directory, { recursive: true, force: true }); }
  });

  it("strictly rejects old profiles, Actor admission, absent required coverage and lost display contexts", () => {
    const s = source(); const packet = JSON.parse(s.serialized);
    expect(() => decodeP1CaptureHandle({ ...s.capture, input_profile: "text-menu-v2" })).toThrow();
    for (const update of [{ agent_admissible: true }, { creates_mutation_authority: true },
      { p1_status: "complete" }, { native_catalog_semantics: "p1_semantic_catalog" },
      { coverage: packet.coverage.filter((item: any) => item.requirement_id !== "current_act_map") },
      { coverage: packet.coverage.filter((item: any) => item.requirement_id !== "run_deck") },
      { scope_status: "complete" }])
      expect(() => decodeP1CapturedObservation({ ...packet, ...update })).toThrow();
    packet.reads[0].display_context = "";
    expect(() => decodeP1CapturedObservation(packet)).toThrow();
  });

  it("rejects duplicate instances, dangling relations and missing detail falsely marked available", () => {
    const s = source();
    for (const mutate of [
      (p: any) => { p.reads[1].instance_id = p.reads[0].instance_id; },
      (p: any) => { p.reads[0].parent_read_instance_id = "foreign"; },
      (p: any) => { p.reads[0].missing = ["current_display_description"]; },
      (p: any) => { p.reads[0].content = null; },
      (p: any) => { p.reads[0].content = {}; },
      (p: any) => { p.reads[0].content.description = null; },
      (p: any) => { p.reads[0].content.name = ""; },
      (p: any) => { p.reads[0].content.entity_id = "foreign-card"; },
      (p: any) => { p.reads[0].content_schema = "unknown-schema"; },
      (p: any) => { p.reads[0].content.hidden_native_ref = "must-not-be-accepted"; }
    ]) {
      const packet = JSON.parse(s.serialized); mutate(packet);
      expect(() => decodeP1CapturedObservation(packet)).toThrow();
    }
  });

  it("rejects complete-scope self declarations over a partial native basis", () => {
    const packet = JSON.parse(source().serialized);
    packet.scope_status = "complete";
    for (const item of packet.coverage)
      if (!["current_act_map", "p1_semantic_catalog", "p1_protocol_commitments", "complete_public_detail_closure",
        "native_capture_noninterference_qualification"].includes(item.requirement_id)) {
        item.availability = "available"; item.missing = [];
      }
    expect(() => decodeP1CapturedObservation(packet)).toThrow("contradicts the native basis");
  });

  it("binds complete-scope details as an entity/context/parent multiset, not a kind or definition count", () => {
    const complete = completeSyntheticScope();
    expect(decodeP1CapturedObservation(complete).data.scope_status).toBe("complete");
    expect(decodeP1CapturedObservation(complete).data.agent_admissible).toBe(false);
    for (const mutate of [
      (p: any) => {
        const removed = p.reads.find((read: any) => read.kind === "surface_card").instance_id;
        p.reads = p.reads.filter((read: any) => read.instance_id !== removed);
        p.coverage = p.coverage.filter((item: any) => item.requirement_id !== `read_instance:${removed}`);
        p.coverage.find((item: any) => item.requirement_id === "surface_card_instances").read_instance_ids =
          p.reads.filter((read: any) => read.kind === "surface_card").map((read: any) => read.instance_id);
      },
      (p: any) => { p.reads.find((read: any) => read.kind === "surface_card").parent_read_instance_id = "piles-read"; },
      (p: any) => { p.coverage.find((item: any) => item.requirement_id === "run_deck").read_instance_ids = ["piles-read"]; },
      (p: any) => { p.native_basis.interaction.content.context.is_play_phase = false; },
      (p: any) => { p.native_basis.interaction.stage = "settling"; },
      (p: any) => {
        p.reads = p.reads.filter((read: any) => read.kind !== "surface_card");
        p.coverage = p.coverage.filter((item: any) => !item.requirement_id.startsWith("read_instance:complete-detail"));
        p.coverage.find((item: any) => item.requirement_id === "surface_card_instances").read_instance_ids = [];
      }
    ]) {
      const broken = structuredClone(complete); mutate(broken);
      expect(() => decodeP1CapturedObservation(broken)).toThrow();
    }
  });

  it.each([
    ["absent source", (p: any) => { p.reads[0].source_read_id = null; }],
    ["foreign source", (p: any) => { p.reads[0].source_read_id = "read:foreign"; }],
    ["missed advertisement", (p: any) => { p.native_basis.reads.push({ ...p.native_basis.reads[0],
      read_id: "read:extra", kind: "relic_detail", content_schema: "sts2.player-environment/read/relic_detail-1" }); }],
    ["no required advertisements", (p: any) => { p.native_basis.reads = []; }],
    ["wrong source kind", (p: any) => { p.native_basis.reads[0].kind = "other"; }],
    ["wrong source schema", (p: any) => { p.native_basis.reads[0].content_schema = "sts2.player-environment/read/other-1"; }],
    ["reused source", (p: any) => { p.reads[1].source_read_id = p.reads[0].source_read_id; }],
    ["duplicate advertisement", (p: any) => { p.native_basis.reads.push(p.native_basis.reads[0]); }],
    ["same-context definition contradiction", (p: any) => { p.reads[2].content = { ...p.reads[2].content, definition_id: "OTHER" }; }],
    ["same-context description contradiction", (p: any) => { p.reads[2].content = { ...p.reads[2].content, description: "OTHER" }; }],
    ["same-context cost contradiction", (p: any) => { p.reads[2].content = { ...p.reads[2].content, cost: "0" }; }],
    ["invented null enchantment", (p: any) => { p.reads[0].content.cards[0].existing_enchantment = null;
      p.reads[2].content.existing_enchantment = { definition_id: "PHANTOM", amount: 1, name: "Phantom",
        description: "Invented", observation_source: "native" }; }],
    ["invented omitted enchantment", (p: any) => { delete p.reads[0].content.cards[0].existing_enchantment;
      p.reads[2].content.existing_enchantment = { definition_id: "PHANTOM", amount: 1, name: "Phantom",
        description: "Invented", observation_source: "native" }; }],
    ["invented null play legality", (p: any) => { p.reads[0].content.cards[0].can_play = null; p.reads[2].content.can_play = true; }],
    ["nonfinite player block", (p: any) => { p.native_basis.interaction.content.context.player.block = Infinity; }],
    ["nonfinite persistent hp", (p: any) => { p.native_basis.persistent.content.player.hp = Infinity; }],
    ["energy outside native int", (p: any) => { p.native_basis.interaction.content.context.player.energy = 2_147_483_648; }],
    ["combat ID outside native uint", (p: any) => { p.native_basis.interaction.content.context.enemies[0].combat_id = 4_294_967_296; }],
    ["negative pile count", (p: any) => { p.native_basis.interaction.content.context.player.draw_pile_count = -1; }],
    ["persistent missing facts", (p: any) => { p.native_basis.persistent.content.completeness.missing = ["player_hp"]; }],
    ["persistent gold outside native int", (p: any) => { p.native_basis.persistent.content.player.gold = 2_147_483_648; }],
    ["persistent slots outside native int", (p: any) => { p.native_basis.persistent.content.player.max_potion_slots = 2_147_483_648; }],
    ["persistent floor outside native int", (p: any) => { p.native_basis.persistent.content.run.floor = 2_147_483_648; }],
    ["persistent boss order outside native int", (p: any) => { p.native_basis.persistent.content.run.bosses = [
      { definition_id: "BOSS", order: 2_147_483_648 }]; }],
    ["foreign surface kind", (p: any) => { p.native_basis.interaction.content.surface.kind = "shop"; }],
    ["foreign surface schema", (p: any) => { p.native_basis.interaction.content_schema = "sts2.player-environment/surface/shop-1"; }],
    ["foreign combat player", (p: any) => { p.native_basis.interaction.content.context.player.player_entity_id = "other"; }],
    ["contradictory pile count", (p: any) => { p.native_basis.interaction.content.context.player.draw_pile_count = 999; }],
    ["duplicate same-context contradictory source", (p: any) => {
      const card = p.reads[0].content.cards[1]; card.entity_id = p.reads[0].content.cards[0].entity_id;
      card.definition_id = "OTHER";
      p.reads[3].target_entity_id = card.entity_id; p.reads[3].content = structuredClone(card);
    }],
    ["bare combat context", (p: any) => { const hand = p.native_basis.interaction.content.context.player.hand;
      p.native_basis.interaction.content.context = { kind: "combat", is_play_phase: true, player: { hand } }; }],
    ["missing enemies", (p: any) => { delete p.native_basis.interaction.content.context.enemies; }],
    ["missing energy", (p: any) => { delete p.native_basis.interaction.content.context.player.energy; }],
    ["bad status", (p: any) => { delete p.native_basis.interaction.content.context.player.statuses[0].amount; }],
    ["missing companion state", (p: any) => { delete p.native_basis.interaction.content.context.player.companions; }],
    ["missing potion state", (p: any) => { delete p.native_basis.interaction.content.context.player.potion_states; }],
    ["missing orb state", (p: any) => { delete p.native_basis.interaction.content.context.player.orbs; }],
    ["bad health", (p: any) => { delete p.native_basis.interaction.content.context.enemies[0].hp; }],
    ["missing available hp", (p: any) => { p.native_basis.interaction.content.context.enemies[0].hp = null; }],
    ["hidden numeric hp", (p: any) => { p.native_basis.interaction.content.context.enemies[0].hp_numbers_available = false; }],
    ["duplicate completed detail contradiction", (p: any) => {
      const card = structuredClone(p.reads[0].content.cards[0]); delete card.name;
      p.reads[0].content.cards = [card, structuredClone(card)];
      p.reads[2].content = { ...card, name: "A" };
      p.reads[3].target_entity_id = card.entity_id; p.reads[3].content = { ...card, name: "B" };
    }],
    ["bad intent", (p: any) => { p.native_basis.interaction.content.context.enemies[0].intents[0].type = 3; }]
  ] as const)("rejects complete C1 %s", (_, mutate) => {
    const packet = completeSyntheticScope(); mutate(packet);
    expect(() => decodeP1CapturedObservation(packet)).toThrow();
  });

  it("permits distinct dynamic displays for one entity in different contexts and optional text completion", () => {
    const packet = completeSyntheticScope();
    const hand = packet.native_basis.interaction.content.context.player.hand[0];
    packet.native_basis.interaction.content.context.player.hand = [{ ...hand, cost: "0", description: "Hand display" }];
    const detail = packet.reads.find((r: any) => r.display_context === "hand");
    detail.content = { ...detail.content, cost: "0", description: "Hand display" };
    // Reader compatibility permits optional text supplementation. The current
    // C# producer conservatively marks absent source text unsupported.
    delete packet.reads[0].content.cards[0].name;
    expect(decodeP1CapturedObservation(packet).data.scope_status).toBe("complete");
  });

  it("accepts typed companions, potions, orbs and hidden health without inventing public values", () => {
    const packet = completeSyntheticScope(); const context = packet.native_basis.interaction.content.context;
    context.player.stars = 0; context.player.orb_slots = 3;
    context.player.companions = [{ entity_id: "companion", definition_id: "PET", is_alive: true,
      hp: null, max_hp: null, block: 0, statuses: [], health_bar_visible: false,
      hp_display_mode: "infinite_without_numbers", hp_numbers_available: false }];
    context.player.potion_states = [{ entity_id: "potion", target_type: "Self", can_use: true, automatic: false }];
    context.player.orbs = [{ entity_id: "orb", definition_id: "LIGHTNING", passive_value: 3, evoke_value: 8,
      queue_index: 0, is_next_to_evoke: true }];
    expect(decodeP1CapturedObservation(packet).data.scope_status).toBe("complete");
    for (const mutate of [
      (p: any) => { delete p.player.companions[0].is_alive; },
      (p: any) => { delete p.player.potion_states[0].can_use; },
      (p: any) => { delete p.player.orbs[0].passive_value; }
    ]) {
      const broken = structuredClone(packet); mutate(broken.native_basis.interaction.content.context);
      expect(() => decodeP1CapturedObservation(broken)).toThrow();
    }
  });

  it("keeps unqualified diagnostics partial while enforcing advertised provenance and coverage", () => {
    const packet = JSON.parse(source().serialized);
    const opportunity = { read_id: "read:unknown", kind: "future_read", target_referent_id: null,
      content_schema: "sts2.player-environment/read/future_read-1", visibility_basis: "normal_player_read",
      snapshot_bound: true, ordering_semantics: "single_entity", hidden_by_policy: [] };
    packet.native_basis.reads = [opportunity];
    packet.reads.push({ instance_id: "unknown-read", kind: opportunity.kind, source_read_id: opportunity.read_id,
      target_entity_id: null, display_context: "unqualified_surface", parent_read_instance_id: null,
      availability: "unsupported", content_schema: opportunity.content_schema, content: null, missing: ["outside_c1"] });
    packet.coverage.push({ requirement_id: "read_instance:unknown-read", required: true, availability: "unsupported",
      read_instance_ids: ["unknown-read"], missing: ["outside_c1"] });
    expect(decodeP1CapturedObservation(packet).data.scope_status).toBe("partial");
    const targeted = structuredClone(packet);
    targeted.native_basis.referents = [{ referent_id: "public-target", role: "relic", kind: "entity",
      state: { visible: true, observation_basis: "native_visible_fact" } }];
    targeted.native_basis.reads[0].target_referent_id = "public-target";
    targeted.reads.at(-1).target_entity_id = "public-target";
    expect(decodeP1CapturedObservation(targeted).data.scope_status).toBe("partial");
    const wrongTarget = structuredClone(packet);
    wrongTarget.reads.at(-1).target_entity_id = "foreign";
    expect(() => decodeP1CapturedObservation(wrongTarget)).toThrow();
    const absent = structuredClone(packet); absent.reads.pop(); absent.coverage.pop();
    expect(() => decodeP1CapturedObservation(absent)).toThrow();
    const foreign = structuredClone(packet); foreign.reads.at(-1).source_read_id = "read:foreign";
    expect(() => decodeP1CapturedObservation(foreign)).toThrow();
    const available = structuredClone(packet); available.reads.at(-1).availability = "available";
    available.reads.at(-1).content = {}; available.reads.at(-1).missing = [];
    expect(() => decodeP1CapturedObservation(available)).toThrow();
  });

  it.each([
    ["wrong capture", (c: P1CaptureChunk) => ({ ...c, capture_id: "foreign" })],
    ["mixed payload", (c: P1CaptureChunk) => ({ ...c, sha256: "0".repeat(64) })],
    ["gap", (c: P1CaptureChunk) => ({ ...c, offset: c.offset + 1 })],
    ["early end", (c: P1CaptureChunk) => ({ ...c, end: true, next_cursor: null })]
  ] as const)("rejects %s without delivering a reconstructed observation", async (_, mutate) => {
    const s = source(); const readP1Capture = async (input: { captureId: string; cursor: string; maxBytes?: number }) => {
      const data = mutate(s.block(input)); return { raw: data as any, data };
    };
    await expect(assembleP1Capture(s.capture, { readP1Capture }, 1024)).rejects.toThrow();
  });

  it("rejects archived byte tampering and session mismatch despite a shared Snapshot descriptor", async () => {
    const s = source(); const full = await assembleP1Capture(s.capture, s, 1024);
    const record = JSON.parse(serializeP1CaptureRecord(full));
    record.serialized_observation = record.serialized_observation.replace("public-0", "public-X");
    await expect(readP1CaptureRecord(JSON.stringify(record))).rejects.toThrow("digest mismatch");
    const second = JSON.parse(serializeP1CaptureRecord(full));
    second.capture.session.runtime_instance_id = "foreign";
    await expect(readP1CaptureRecord(JSON.stringify(second))).rejects.toThrow("source/session mismatch");
    const forged = JSON.parse(serializeP1CaptureRecord(full));
    const malformed = JSON.parse(forged.serialized_observation);
    malformed.reads[0].content.description = null;
    forged.serialized_observation = JSON.stringify(malformed);
    forged.capture.total_bytes = Buffer.byteLength(forged.serialized_observation, "utf8");
    forged.capture.sha256 = createHash("sha256").update(forged.serialized_observation, "utf8").digest("hex");
    await expect(readP1CaptureRecord(JSON.stringify(forged))).rejects.toThrow("required display fields");
    // Expiration only bounds the live store. Reading immutable archived public
    // bytes neither invokes a live reader nor extends the old handle's TTL.
    expect((await readP1CaptureRecord(serializeP1CaptureRecord(full))).capture.expires_at).toBe(s.capture.expires_at);
  });

  it("the HTTP source consumer only captures, reads retained bytes and releases retention", async () => {
    const s = source(); const paths: string[] = [];
    const fetchImpl = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input)); paths.push(`${init?.method ?? "GET"} ${url.pathname}`);
      let body: unknown;
      if (url.pathname === `${P1_CAPTURE_ROUTE}/current`) {
        expect(url.searchParams.get("input_profile")).toBe(P1_CAPTURE_PROFILE); body = s.capture;
      } else if (url.pathname === `${P1_CAPTURE_ROUTE}/read`) {
        body = s.block({ cursor: url.searchParams.get("cursor")!, maxBytes: Number(url.searchParams.get("max_bytes")) });
      } else if (url.pathname === `${P1_CAPTURE_ROUTE}/release`) {
        body = { schema: P1_CAPTURE_RELEASE_SCHEMA, capture_id: s.capture.capture_id, released: true };
      } else throw new Error("unexpected authority route");
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    }) as unknown as typeof fetch;
    const client = new PlayerEnvironmentRestClient("http://127.0.0.1:15526", 1000, fetchImpl);
    const full = await client.getFullP1Capture({ expectedSnapshotId: "state-fixture", maxBytes: 1024 });
    expect(full.serializedObservation).toBe(s.serialized);
    expect(paths.filter(path => path.endsWith("/current"))).toHaveLength(1);
    expect(paths.filter(path => path.endsWith("/release"))).toEqual([`POST ${P1_CAPTURE_ROUTE}/release`]);
    expect(paths.every(path => path.includes(P1_CAPTURE_ROUTE))).toBe(true);
  });
});
