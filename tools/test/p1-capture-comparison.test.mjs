import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { compareP1CaptureRecords, sha256Text } from "../p1-capture-comparison.mjs";
import { inputs, completeSyntheticScope, producerFixture, producerSerializedFixture, renamed } from "./p1-capture-comparison-fixture.mjs";
const compare = async (left, right) => compareP1CaptureRecords(inputs(left, right));
function cli(input, corrupt) {
  const dir = mkdtempSync(path.join(tmpdir(), "p1-c1-comparison-"));
  try {
    const documents = { reference: input.reference.record, candidate: input.candidate.record,
      "reference-report": input.reference.report, "candidate-report": input.candidate.report, declaration: input.declaration };
    const args = [];
    for (const [name, value] of Object.entries(documents)) {
      const file = path.join(dir, `${name}.json`); writeFileSync(file, value); args.push(`--${name}`, file);
    }
    corrupt?.(dir);
    const child = spawnSync(process.execPath, [fileURLToPath(new URL("../compare-p1-captures.mjs", import.meta.url)), ...args], { encoding: "utf8", timeout: 10000 });
    assert.equal(child.signal, null); assert.equal(child.stderr, "");
    return { code: child.status, result: JSON.parse(child.stdout) };
  } finally { rmSync(dir, { recursive: true, force: true }); }
}
test("actual SDK serializer → record → CLI keeps >8 details, bijections and false admission", async () => {
  const input = inputs(); const result = cli(input);
  assert.equal(result.code, 0); assert.equal(result.result.verdict, "c1_scope_match");
  assert.equal(result.result.agent_admissible, false); assert.equal(result.result.p1_status, "unsupported");
  assert.equal(result.result.cross_host_qualification, "incomparable"); assert.equal(result.result.alignment_verified, false);
  assert.equal(result.result.relation_scope, "declared_typed_alias_relations_only");
  assert.equal(result.result.extension_semantics, "literal_values_no_undeclared_relation_inference");
  assert.equal(result.result.environment_alignment, "externally_asserted"); assert.equal(result.result.unsupported_sections.length, 5);
  assert.notEqual(result.result.provenance.reference.public_bytes_sha256, result.result.provenance.candidate.public_bytes_sha256);
  assert.equal(completeSyntheticScope().reads.filter(read => read.kind === "surface_card").length, 15);
});
test("original C#-serialized fixture remains partial and refuses CLI semantic match", () => {
  const text = producerSerializedFixture();
  const input = inputs(producerFixture(), producerFixture(), { reference: text, candidate: text });
  assert.equal(JSON.parse(input.reference.record).serialized_observation, text);
  const result = cli(input);
  assert.equal(result.code, 20); assert.equal(result.result.first_difference.reason, "complete_c1_scope_required");
  assert.match(result.result.original_digests.candidate.record_sha256, /^[a-f0-9]{64}$/u);
});
test("read-instance and coverage collections retain identities while tolerating collection enumeration", async () => {
  const left = completeSyntheticScope(); const right = renamed(left);
  right.reads.reverse(); right.coverage.reverse(); right.coverage.forEach(item => item.read_instance_ids.reverse());
  assert.equal((await compare(left, right)).verdict, "c1_scope_match");
});
test("declared unordered piles tolerate order; ordered hand/deck remain compared", async () => {
  const left = completeSyntheticScope(); const right = renamed(left);
  right.reads.find(read => read.kind === "combat_piles").content.zones[0].cards.reverse();
  assert.equal((await compare(left, right)).verdict, "c1_scope_match");
  right.reads.find(read => read.kind === "run_deck").content.cards.reverse();
  assert.equal((await compare(left, right)).verdict, "c1_scope_mismatch");
});
for (const [name, mutate] of [
  ["detail cost", p => { p.reads[2].content.cost = "9"; }],
  ["thirteenth description", p => { p.reads[14].content.description = "changed public value"; }],
  ["bound action operand", p => { const a = p.native_basis.bound_actions.actions[0]; a.arguments[0].referent_id = a.subject_referent_id; }],
  ["public text resembling an ID", p => { p.reads[2].content.description = p.reads[2].target_entity_id; }],
  ["semantic definition ID", p => { p.reads[2].content.definition_id = "DIFFERENT"; }],
  ["unknown ID remains semantic", p => { p.native_basis.interaction.content.context.custom_id = "changed"; }],
  ["shared entity split", p => { const d = p.reads.find(read => read.display_context === "hand"); d.content = { ...d.content, entity_id: "split" }; d.target_entity_id = "split"; p.native_basis.interaction.content.context.player.hand[0] = { ...d.content }; }],
  ["distinct entities collapse", p => { const a = p.reads[0].content.cards; a[2].entity_id = a[1].entity_id; for (const d of p.reads) if (d.target_entity_id === "card-2") d.target_entity_id = d.content.entity_id = "card-1"; p.reads[1].content.zones[0].cards[1].entity_id = "card-1"; }]
]) test(`valid recomputed digest still detects ${name}`, async () => {
  const left = completeSyntheticScope(); const right = structuredClone(left); mutate(right);
  const result = await compare(left, right); assert.equal(result.verdict, "c1_scope_mismatch");
  if (name === "detail cost") { const actual = cli(inputs(left, right)); assert.equal(actual.code, 10); assert.equal(actual.result.verdict, "c1_scope_mismatch"); }
  assert.ok(result.first_difference.path.length <= 240); assert.equal(Object.hasOwn(result.first_difference, "candidate"), false);
});
for (const [name, mutate] of [
  ["missing thirteenth detail", p => { p.reads.splice(14, 1); }],
  ["wrong aggregate parent", p => { p.reads[2].parent_read_instance_id = "piles-read"; }],
  ["wrong display context", p => { p.reads[2].display_context = "draw"; }],
  ["availability", p => { p.scope_status = "partial"; p.reads[2].availability = "unsupported"; p.reads[2].missing = ["description"]; }],
  ["required coverage", p => { p.coverage = p.coverage.filter(c => c.requirement_id !== "current_public_base"); }],
  ["wrong profile", p => { p.input_profile = "text-menu-v2"; }],
  ["wrong revision", p => { p.revision = "c1-future"; }]
]) test(`CLI refuses ${name} without legacy fallback`, () => {
  const input = inputs(); const record = JSON.parse(input.candidate.record); const packet = JSON.parse(record.serialized_observation); mutate(packet);
  record.serialized_observation = JSON.stringify(packet); record.capture.sha256 = sha256Text(record.serialized_observation);
  record.capture.total_bytes = Buffer.byteLength(record.serialized_observation); input.candidate.record = JSON.stringify(record);
  const declaration = JSON.parse(input.declaration); declaration.candidate.record_sha256 = sha256Text(input.candidate.record);
  declaration.candidate.capture_sha256 = record.capture.sha256; input.declaration = JSON.stringify(declaration);
  assert.equal(cli(input).result.verdict, "c1_incomparable");
});
test("same record is never independent evidence, even under a separately supplied report", async () => {
  const input = inputs(); input.candidate = input.reference;
  const declaration = JSON.parse(input.declaration); declaration.candidate = declaration.reference; input.declaration = JSON.stringify(declaration);
  assert.equal((await compareP1CaptureRecords(input)).verdict, "same_record");
  assert.equal(cli(input).code, 20);
});
test("invalid digest, missing original report and foreign runtime association fail closed", async () => {
  for (const mode of ["digest", "report", "runtime", "record-binding", "seed", "game", "declaration"]) {
    const input = inputs(); const declaration = JSON.parse(input.declaration);
    if (mode === "digest") input.candidate.record = input.candidate.record.replace("public-0", "public-X");
    if (mode === "report") delete input.candidate.report;
    if (mode === "runtime" || mode === "seed" || mode === "game") {
      const report = JSON.parse(input.candidate.report);
      if (mode === "runtime") report.loaded_identity.host.runtime_instance_id = "foreign";
      if (mode === "seed") report.episode_provenance.actual_seed = "FOREIGN";
      if (mode === "game") report.loaded_identity.game.commit = "foreign";
      input.candidate.report = JSON.stringify(report); declaration.candidate.report_sha256 = sha256Text(input.candidate.report);
    }
    if (mode === "record-binding") declaration.candidate.record_sha256 = "0".repeat(64);
    input.declaration = mode === "declaration" ? "{}" : JSON.stringify(declaration);
    assert.equal((await compareP1CaptureRecords(input)).verdict, "c1_incomparable", mode);
  }
});
test("CLI rejects invalid UTF-8 and absent archive without exposing local paths", () => {
  for (const corrupt of [dir => writeFileSync(path.join(dir, "candidate.json"), Buffer.from([0xff, 0xfe])),
    dir => rmSync(path.join(dir, "candidate.json"))]) {
    const result = cli(inputs(), corrupt); assert.equal(result.code, 20); assert.equal(result.result.verdict, "c1_incomparable");
    assert.equal(JSON.stringify(result.result).includes(tmpdir()), false);
  }
});

test("same capture identity cannot hide semantically changed valid-digest bytes", async () => {
  const left = completeSyntheticScope(); const right = structuredClone(left); right.reads[2].content.cost = "9";
  const input = inputs(left, right); const record = JSON.parse(input.candidate.record);
  record.capture.capture_id = JSON.parse(input.reference.record).capture.capture_id; input.candidate.record = JSON.stringify(record);
  const declaration = JSON.parse(input.declaration); declaration.candidate.capture_id = record.capture.capture_id;
  declaration.candidate.record_sha256 = sha256Text(input.candidate.record); input.declaration = JSON.stringify(declaration);
  const result = await compareP1CaptureRecords(input); assert.equal(result.verdict, "c1_incomparable");
  assert.equal(result.first_difference.reason, "conflicting_bytes_for_same_capture_identity");
});
test("ambiguous unseen unordered aliases are explicitly incomparable", async () => {
  const left = completeSyntheticScope();
  const pile = left.reads.find(read => read.kind === "combat_piles").content.zones[0];
  pile.cards = pile.cards.map((card, index) => ({ ...card, entity_id: `draw-only-${index}` }));
  for (const [index, read] of left.reads.filter(read => read.display_context === "draw").entries()) {
    read.target_entity_id = pile.cards[index].entity_id; read.content = { ...pile.cards[index] };
  }
  const result = await compare(left, renamed(left)); assert.equal(result.verdict, "c1_incomparable");
  assert.equal(result.first_difference.reason, "ambiguous_unordered_alias_mapping");
});
test("pile multiplicity and available-empty versus absent remain distinct", () => {
  for (const mutate of [packet => { packet.reads[1].content.zones[0].cards.pop(); packet.reads[1].content.zones[0].card_count--; },
    packet => { packet.reads[1].content.zones.pop(); }]) {
    const input = inputs(); const record = JSON.parse(input.candidate.record); const packet = JSON.parse(record.serialized_observation); mutate(packet);
    record.serialized_observation = JSON.stringify(packet); record.capture.sha256 = sha256Text(record.serialized_observation);
    record.capture.total_bytes = Buffer.byteLength(record.serialized_observation); input.candidate.record = JSON.stringify(record);
    const declaration = JSON.parse(input.declaration); declaration.candidate.record_sha256 = sha256Text(input.candidate.record);
    declaration.candidate.capture_sha256 = record.capture.sha256; input.declaration = JSON.stringify(declaration);
    assert.equal(cli(input).result.verdict, "c1_incomparable");
  }
});

test("valid complete packet with reduced pile and required-detail multiplicity diverges", async () => {
  const left = completeSyntheticScope(); const right = structuredClone(left);
  right.reads[1].content.zones[0].cards.pop(); right.reads[1].content.zones[0].card_count--;
  const removed = right.reads.find(read => read.display_context === "draw" && read.target_entity_id === "card-2").instance_id;
  right.reads = right.reads.filter(read => read.instance_id !== removed);
  right.coverage = right.coverage.filter(item => item.requirement_id !== `read_instance:${removed}`);
  right.coverage.forEach(item => { item.read_instance_ids = item.read_instance_ids.filter(id => id !== removed); });
  assert.equal((await compare(left, right)).verdict, "c1_scope_mismatch");
});
test("an unqualified deeply nested basis is bounded before recursive comparison", async () => {
  const left = completeSyntheticScope(); let child = left.native_basis.interaction.content.context;
  for (let i = 0; i < 80; i++) { child.nested = {}; child = child.nested; }
  const result = await compare(left, renamed(left)); assert.equal(result.verdict, "c1_incomparable");
  assert.equal(result.first_difference.reason, "comparison_work_bound");
});

test("opaque-looking fields in unknown semantic extensions stay literal", async () => {
  const left = completeSyntheticScope(); const right = structuredClone(left);
  left.native_basis.interaction.content.context.extension = { entity_id: "opaque-left" };
  right.native_basis.interaction.content.context.extension = { entity_id: "opaque-right" };
  const result = await compare(left, right); assert.equal(result.verdict, "c1_scope_mismatch");
  assert.match(result.first_difference.path, /extension.entity_id$/u);
});
test("original record byte bound rejects before claiming SDK decoding", async () => {
  const input = inputs(); input.candidate.record = "x".repeat(64 * 1024 * 1024 + 1);
  const result = await compareP1CaptureRecords(input); assert.equal(result.verdict, "c1_incomparable");
  assert.equal(result.first_difference.reason, "record_byte_bound_exceeded");
});

test("unknown extensions cannot acquire collection semantics through familiar field names", async () => {
  for (const extension of [{ ordering_semantics: "unordered_multiset", cards: ["first", "second"] },
    { read_instance_ids: ["first", "second"] }]) {
    const left = completeSyntheticScope(); left.native_basis.interaction.content.context.extension = extension;
    const right = structuredClone(left); const changed = right.native_basis.interaction.content.context.extension;
    (changed.cards ?? changed.read_instance_ids).reverse();
    assert.equal((await compare(left, right)).verdict, "c1_scope_mismatch");
  }
});

// Source shapes: VisibleCombatPlayer/VisibleOrb and VisibleOwnedPotion in
// LiveHostContracts.cs; these are native-visible identities, not extensions.
function ordinaryOrbScope() {
  const packet = completeSyntheticScope();
  const orb = { entity_id: "orb-a", definition_id: "LIGHTNING", name: "Lightning", description: "Deal damage.",
    passive_value: 3, evoke_value: 8, queue_index: 0, is_next_to_evoke: true };
  const player = packet.native_basis.interaction.content.context.player;
  player.player_entity_id = "player"; player.orbs = [orb]; player.orb_slots = 3;
  packet.native_basis.referents.push({ referent_id: orb.entity_id, role: "orb", kind: "entity", label: "Lightning",
    state: { visible: true, observation_basis: "native_visible_fact" },
    properties_schema: "sts2.player-environment/referent/orb-1", properties: structuredClone(orb) });
  packet.native_basis.referents.push({ referent_id: player.player_entity_id, role: "player", kind: "entity", label: "Defect",
    state: { visible: true, observation_basis: "native_visible_fact" },
    properties_schema: "sts2.player-environment/referent/player-1", properties: structuredClone(player) });
  return packet;
}
test("ordinary Defect orb and player property aliases match through actual CLI", () => {
  const left = ordinaryOrbScope(); const right = renamed(left);
  // This field is explicitly named by VisibleCombatPlayer, separate from entity_id.
  right.native_basis.interaction.content.context.player.player_entity_id = right.native_basis.persistent.content.player.entity_id;
  right.native_basis.referents.at(-1).properties.player_entity_id = right.native_basis.persistent.content.player.entity_id;
  const result = cli(inputs(left, right)); assert.equal(result.code, 0); assert.equal(result.result.verdict, "c1_scope_match");
});
test("ordinary orb referent cannot split from its context/property entity through actual CLI", () => {
  const left = ordinaryOrbScope(); const right = structuredClone(left);
  right.native_basis.referents.find(ref => ref.role === "orb").referent_id = "orb-split";
  const result = cli(inputs(left, right)); assert.equal(result.code, 10); assert.equal(result.result.verdict, "c1_scope_mismatch");
});
test("owned potion preview card aliases match through actual CLI", () => {
  const left = completeSyntheticScope();
  left.native_basis.persistent.content.player.potions = [{ entity_id: "potion-a", definition_id: "COLORLESS_POTION",
    name: "Colorless Potion", description: "Choose a card.", slot: 0, keywords: [],
    card_previews: [{ ...left.reads[0].content.cards[0], entity_id: "potion-preview-a" }] }];
  const result = cli(inputs(left, renamed(left))); assert.equal(result.code, 0); assert.equal(result.result.verdict, "c1_scope_match");
});

test("orb semantic values and known nested alias relations remain compared through CLI", () => {
  for (const mutate of [packet => { packet.native_basis.interaction.content.context.player.orbs[0].passive_value = 99; },
    packet => { packet.native_basis.referents.find(ref => ref.role === "player").properties.orbs[0].entity_id = "orb-split"; }]) {
    const left = ordinaryOrbScope(); const right = structuredClone(left); mutate(right);
    const result = cli(inputs(left, right)); assert.equal(result.code, 10); assert.equal(result.result.verdict, "c1_scope_mismatch");
  }
});
test("orb-looking unknown extensions and unknown referent schemas remain literal", () => {
  for (const location of ["extension", "recognized-properties-extension", "unknown-schema"]) {
    const left = ordinaryOrbScope(); const right = structuredClone(left);
    if (location === "extension") {
      left.native_basis.interaction.content.context.player.orbs[0].extension = { entity_id: "opaque-left" };
      right.native_basis.interaction.content.context.player.orbs[0].extension = { entity_id: "opaque-right" };
    } else if (location === "recognized-properties-extension") {
      left.native_basis.referents.find(ref => ref.role === "orb").properties.extension = { entity_id: "opaque-left" };
      right.native_basis.referents.find(ref => ref.role === "orb").properties.extension = { entity_id: "opaque-right" };
    } else {
      for (const packet of [left, right]) packet.native_basis.referents.find(ref => ref.role === "orb").properties_schema = "sts2.player-environment/referent/unknown_orb-1";
      right.native_basis.referents.find(ref => ref.role === "orb").properties.entity_id = "opaque-right";
    }
    assert.equal(cli(inputs(left, right)).result.verdict, location === "unknown-schema" ? "c1_incomparable" : "c1_scope_mismatch");
  }
});
test("owned potion preview cannot lose a shared public card alias relation through CLI", () => {
  const left = completeSyntheticScope();
  left.native_basis.persistent.content.player.potions = [{ entity_id: "potion-a", definition_id: "COLORLESS_POTION",
    name: "Colorless Potion", description: "Choose a card.", slot: 0, keywords: [],
    card_previews: [structuredClone(left.reads[0].content.cards[0])] }];
  const right = structuredClone(left); right.native_basis.persistent.content.player.potions[0].card_previews[0].entity_id = "preview-split";
  const result = cli(inputs(left, right)); assert.equal(result.code, 10); assert.equal(result.result.verdict, "c1_scope_mismatch");
});
test("unknown structured referent schema is incomparable even with equal literal alias-looking values", () => {
  const left = completeSyntheticScope(); const right = renamed(left);
  for (const packet of [left, right]) {
    packet.native_basis.referents[0].properties_schema = "sts2.player-environment/referent/future_card_link-1";
    packet.native_basis.referents[0].properties = { entity_id: "card-0" };
  }
  const result = cli(inputs(left, right)); assert.equal(result.code, 20); assert.equal(result.result.verdict, "c1_incomparable");
  assert.equal(result.result.first_difference.reason, "unsupported_referent_properties_schema");
});

test("unknown nonnull referent schema with no properties is still incomparable", () => {
  for (const properties of [null, undefined]) {
    const left = completeSyntheticScope(); const right = renamed(left);
    for (const packet of [left, right]) {
      packet.native_basis.referents[0].properties_schema = "sts2.player-environment/referent/future_card_link-1";
      if (properties === undefined) delete packet.native_basis.referents[0].properties;
      else packet.native_basis.referents[0].properties = properties;
    }
    const result = cli(inputs(left, right)); assert.equal(result.code, 20); assert.equal(result.result.verdict, "c1_incomparable");
  }
});
test("equal alias-looking extension literals do not infer an undeclared entity relation", () => {
  const left = completeSyntheticScope(); const right = renamed(left);
  for (const packet of [left, right]) packet.native_basis.interaction.content.context.extension = { entity_id: "card-0" };
  const result = cli(inputs(left, right)); assert.equal(result.code, 0); assert.equal(result.result.verdict, "c1_scope_match");
  assert.equal(result.result.relation_scope, "declared_typed_alias_relations_only");
  assert.equal(result.result.extension_semantics, "literal_values_no_undeclared_relation_inference");
});
