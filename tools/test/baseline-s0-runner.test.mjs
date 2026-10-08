import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { PublicMenuTeacher, S0_TEXT_V2_KINDS, S0_INFORMATION_RETURNS } from "../baseline-s0-teacher.mjs";
import { S0RawRecords, sha256 } from "../baseline-s0-records.mjs";
import { runS0, makeS0Manifest, confirmControlReleased, parseArguments } from "../baseline-s0-runner.mjs";
import { resolveInstallation } from "../../components/host-runtime/src/game-installation.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const base = JSON.parse(await readFile(path.join(root,
  "components/connector/sdk/typescript/test/fixtures/text-menu-v2-targeted-root.json"), "utf8"));
const action = (verb, kind = "system_navigation") => ({ action_id: `a-${verb}`, kind,
  verb, label: verb, subject_referent_id: verb === "select_card" || verb === "play" ? "card-C"
    : verb === "select_target" ? "enemy-E" : null,
  arguments: verb === "play" ? [{ role: "target", referent_id: "enemy-E" }] : [],
  effect_domain: kind === "native_input" ? "native_input" : "text_menu" });
function snapshot(cursor, actions, kind = "combat_turn") {
  const value = structuredClone(base);
  value.menu.cursor = cursor;
  value.menu.selection = cursor === "card_targets" ? [{ role: "card", referent_id: "card-C" }]
    : cursor === "card_confirmation" ? [{ role: "card", referent_id: "card-C" }, { role: "target", referent_id: "enemy-E" }] : [];
  value.interaction.kind = kind;
  value.menu_actions.actions = actions;
  value.menu_actions.materialized_count = value.menu_actions.total_count = actions.length;
  return value;
}
function nativeInformation(kind, verb = S0_INFORMATION_RETURNS[kind]) {
  const value = snapshot("root", [action(verb, "native_input")], kind);
  value.interaction.stage = "native_information_page";
  value.interaction.content_schema = `sts2.player-environment/surface/${kind}_text_menu-1`;
  const surface = verb === "return_native_tips" ? { kind: "native_tips",
    tips: [{ title: "Public tip", description: "Current exposed rule text" }] }
    : kind === "inspect_card" ? { kind, displayed_title: "Public card", displayed_cost: "1",
      displayed_description: "Current exposed description", upgrade_preview: false }
    : kind === "relic_inspect" ? { kind, title: "Public relic", rarity: "Starter", description: "Public rule", flavor: "" }
    : { kind, details: { cards: [] } };
  value.interaction.content = { surface, context: { kind: verb === "return_native_tips" ? "native_tips" : kind } };
  value.referents = [];
  return value;
}
function nativeMap() {
  const value = snapshot("root", [{ ...action("activate", "native_input"), subject_referent_id: "map-option-1",
    label: "Choose monster at (3,0)" }], "native_map");
  value.interaction.stage = "native_information_page";
  value.interaction.content_schema = "sts2.player-environment/surface/map_navigation-1";
  value.interaction.content = { surface: { kind: "map_navigation", travel_enabled: true, traveling: false,
    drawing_mode: "none", next_options: [{ entity_id: "map-option-1", col: 3, row: 0, point_type: "monster" }],
    can_exit_annotation: false }, context: { kind: "native_map" } };
  value.referents = [{ referent_id: "map-option-1", role: "option", kind: "entity",
    state: { visible: true, observation_basis: "native_visible_fact" },
    properties_schema: "sts2.player-environment/referent/option-1",
    properties: value.interaction.content.surface.next_options[0] }];
  return value;
}
function publicRewardRef(descriptor, role = "reward") {
  return { referent_id: descriptor.entity_id, role, kind: "entity", label: descriptor.label ?? descriptor.name,
    state: { visible: true, enabled: descriptor.enabled, observation_basis: "native_visible_fact" },
    properties_schema: `sts2.player-environment/referent/${role}-1`, properties: descriptor };
}
function ordinaryRewards(rewards = [{ entity_id: "reward-gold", kind: "gold", label: "Continue", description: "Public gold", enabled: true }],
  { canProceed = true, skips = true } = {}) {
  const value = snapshot("root", [], "reward_claim");
  value.interaction.content_schema = "sts2.player-environment/surface/reward_claim-1";
  value.interaction.content = { surface: { kind: "reward_claim", rewards, potion_slots_full: false,
    discardable_potions: [], can_proceed: canProceed, proceed_skips_remaining_rewards: skips }, context: { kind: "reward_flow" } };
  value.referents = rewards.map(reward => publicRewardRef(reward));
  value.menu_actions.actions = rewards.filter(reward => reward.enabled).map(reward => ({
    ...action("activate", "native_input"), action_id: `claim-${reward.entity_id}`,
    subject_referent_id: reward.entity_id, label: "Not a semantic discriminant" }));
  if (canProceed) value.menu_actions.actions.unshift({ ...action("activate", "native_input"), action_id: "proceed-rewards",
    label: "Same label as every reward" });
  value.menu_actions.materialized_count = value.menu_actions.total_count = value.menu_actions.actions.length;
  return value;
}
function linkedRewards() {
  const value = snapshot("root", [
    { ...action("skip_rewards", "native_input"), subject_referent_id: "reward-screen" },
    { ...action("claim_linked_reward", "native_input"), subject_referent_id: "linked-choice" }
  ], "reward_claim");
  value.interaction.stage = "native_linked_reward_page";
  value.interaction.content_schema = "sts2.player-environment/surface/linked_rewards_text_menu-1";
  value.interaction.content = { surface: { kind: "reward_claim", entries: [{ kind: "linked_reward_set",
    referent_id: "linked-group", label: "Public group", choices: [{ referent_id: "linked-choice", label: "Public choice", enabled: true }] }],
    proceed_is_skip: true, proceed_enabled: true }, context: { kind: "reward_claim" } };
  value.referents = [publicRewardRef({ entity_id: "linked-choice", label: "Public choice", enabled: true }),
    publicRewardRef({ entity_id: "linked-group", label: "Public group", enabled: false }, "reward_group"),
    { referent_id: "reward-screen", role: "screen", kind: "control", label: "Rewards",
      state: { visible: true, enabled: true, observation_basis: "native_visible_fact" }, properties_schema: null, properties: null }];
  return value;
}
function cardRewards({ cardsAvailable = true } = {}) {
  const card = { entity_id: "reward-card", name: "Public card", description: "Public card rule" };
  const alternative = { entity_id: "reward-alternative", index: 0, label: "Continue", enabled: true };
  const value = snapshot("root", [{ ...action("activate", "native_input"), action_id: "alternative-action",
    subject_referent_id: alternative.entity_id, label: "Continue" }], "card_reward_selection");
  if (cardsAvailable) value.menu_actions.actions.push({ ...action("select", "native_input"), action_id: "card-choice",
    subject_referent_id: card.entity_id, label: "Ignore this label" });
  value.interaction.content_schema = "sts2.player-environment/surface/card_reward_selection-1";
  value.interaction.content = { surface: { kind: "card_reward_selection", cards: cardsAvailable ? [card] : [],
    alternatives: [alternative], selectable_card_entity_ids: cardsAvailable ? [card.entity_id] : [] }, context: { kind: "reward_flow" } };
  value.referents = [publicRewardRef(alternative, "alternative"), ...(cardsAvailable ? [publicRewardRef(card, "card")] : [])];
  value.menu_actions.materialized_count = value.menu_actions.total_count = value.menu_actions.actions.length;
  return value;
}
function input(value) {
  return { run_id: "test-run", bundle: { observation: value, reads: [] },
    continuity_token: "segment-1", candidate_count: value.menu_actions.actions.length,
    candidate_digest: sha256(JSON.stringify(value.menu_actions.actions.map(a => a.action_id))) };
}
function full(value, id = "capture-1", ordinal = 1) {
  const serializedSnapshot = JSON.stringify(value, null, 2); // Preserve original whitespace.
  return { context: { snapshot: value, game_continuity_id: "game-1" }, serializedSnapshot,
    capture: { capture_id: id, capture_ordinal: ordinal, source_snapshot_id: value.snapshot_id,
      sha256: sha256(serializedSnapshot), total_bytes: Buffer.byteLength(serializedSnapshot),
      session: value.session, game_continuity_id: "game-1" } };
}
async function temporary(t, closeResources = async () => {}) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "baseline-s0-test-"));
  t.after(async () => {
    await closeResources();
    await rm(directory, { recursive: true, force: true });
  });
  return directory;
}

test("actual text-v2 native owner resets cursor; teacher returns, revisits and then staged-plays", async () => {
  const teacher = new PublicMenuTeacher();
  const frames = [
    snapshot("root", [action("select_card", "system_selection"), action("open_information")]),
    snapshot("information", [action("back"), action("open_card_tips")]),
    snapshot("card_tips", [action("back"), action("show_card_tips", "native_input")]),
    nativeInformation("card_tips"),
    snapshot("root", [action("select_card", "system_selection"), action("open_information")]),
    snapshot("information", [action("back"), action("open_card_tips")]),
    snapshot("card_tips", [action("back"), action("show_card_tips", "native_input")]),
    nativeInformation("card_tips"),
    snapshot("root", [action("select_card", "system_selection"), action("open_information")]),
    snapshot("card_targets", [action("cancel_selection", "system_selection"), action("select_target", "system_selection")]),
    snapshot("card_confirmation", [action("cancel_selection", "system_selection"), action("play", "native_input")])
  ];
  const { decodeTextMenuV2Snapshot } = await import("../../components/connector/sdk/typescript/dist/index.js");
  const selected = frames.map(raw => {
    const value = decodeTextMenuV2Snapshot(raw).data;
    const result = teacher.decide(input(value));
    assert.equal(result.output.scores.length, value.menu_actions.actions.length);
    assert.equal(result.completion.snapshot_id, value.snapshot_id);
    return value.menu_actions.actions[result.output.selected_index].verb;
  });
  assert.deepEqual(selected, ["open_information", "open_card_tips", "show_card_tips", "return_native_tips",
    "open_information", "open_card_tips", "show_card_tips", "return_native_tips", "select_card", "select_target", "play"]);
  assert.equal(teacher.browseVisits, 2);
});

test("teacher progresses map and abstains on unfamiliar native operation", () => {
  const teacher = new PublicMenuTeacher({ browse: false });
  assert.equal(teacher.decide(input(nativeMap()))
    .output.selected_index, 0);
  assert.equal(teacher.decide(input(snapshot("root", [action("unfamiliar", "native_input")], "event_option")))
    .output.selected_index, null);
  const truncated = snapshot("root", [action("select", "native_input")]);
  truncated.menu_actions.status = "truncated";
  assert.throws(() => teacher.decide(input(truncated)), /complete_public_catalog/u);
});

test("native_map actual public shape and activate leaf are admitted by declared text-v2 scope", async () => {
  const { decodeTextMenuV2Snapshot } = await import("../../components/connector/sdk/typescript/dist/index.js");
  const value = decodeTextMenuV2Snapshot(nativeMap()).data;
  const manifest = makeS0Manifest({ ...capabilities, verbs: ["activate", "return_native_map"] },
    { id: "teacher", path: "teacher", sha256: "c".repeat(64) },
    { id: "teacher", version: "1.1.0", protocol: "sts2.policy-runtime/decision-only-ndjson-2", code_sha256: "d".repeat(64) });
  assert.ok(manifest.support.interaction_kinds.includes(value.interaction.kind));
  assert.ok(manifest.support.action_verbs.includes(value.menu_actions.actions[0].verb));
  const result = new PublicMenuTeacher().decide(input(value));
  assert.equal(value.menu_actions.actions[result.output.selected_index].verb, "activate");
  assert.equal(value.menu_actions.actions[result.output.selected_index].subject_referent_id,
    value.interaction.content.surface.next_options[0].entity_id);
  assert.equal(manifest.support.interaction_kinds.includes("native_information_unresolved"), false);
});

test("map teacher only ranks current C members whose public subjects are next options", () => {
  const value = nativeMap();
  const distraction = { ...action("activate", "native_input"), action_id: "annotation-action",
    subject_referent_id: "annotation-control", label: "Annotation control" };
  value.referents.push({ referent_id: "annotation-control", role: "control", kind: "control",
    state: { visible: true, enabled: true, observation_basis: "native_visible_fact" } });
  value.menu_actions.actions.unshift(distraction, action("return_native_map", "native_input"));
  value.menu_actions.total_count = value.menu_actions.materialized_count = value.menu_actions.actions.length;
  const selected = new PublicMenuTeacher().decide(input(value)).output.selected_index;
  assert.equal(selected, 2);
  value.interaction.content.surface.next_options = [];
  assert.equal(new PublicMenuTeacher().decide(input(value)).output.selected_index, 1);
  const unknown = snapshot("root", [action("activate", "native_input")], "arbitrary_new_scene");
  assert.equal(new PublicMenuTeacher().decide(input(unknown)).output.selected_index, null);
});

test("every declared native information owner returns through its actual native verb", async () => {
  const { decodeTextMenuV2Snapshot } = await import("../../components/connector/sdk/typescript/dist/index.js");
  for (const [kind, verb] of Object.entries(S0_INFORMATION_RETURNS)) {
    assert.ok(S0_TEXT_V2_KINDS.includes(kind));
    const value = decodeTextMenuV2Snapshot(nativeInformation(kind)).data;
    const result = new PublicMenuTeacher().decide(input(value));
    assert.equal(value.menu_actions.actions[result.output.selected_index].verb, verb, kind);
  }
});

test("native held-card operation and potion owners use current native controls instead of virtual cursor assumptions", async () => {
  const { decodeTextMenuV2Snapshot } = await import("../../components/connector/sdk/typescript/dist/index.js");
  for (const stage of ["card_targeting", "card_confirm"]) {
    const verb = stage === "card_targeting" ? "confirm_target" : "confirm_card";
    const value = snapshot("root", [action("cancel_card_play", "native_input"), action(verb, "native_input")], "combat_card_operation");
    value.interaction.stage = stage;
    value.interaction.content_schema = "sts2.player-environment/surface/combat_card_operation_text_menu-1";
    value.interaction.content = { surface: { kind: "combat_card_operation", stage,
      held_card_referent_id: "card-C", displayed_title: "Strike", displayed_cost: "1", displayed_description: "Public damage" },
      context: { kind: "combat" } };
    const result = new PublicMenuTeacher().decide(input(decodeTextMenuV2Snapshot(value).data));
    assert.equal(value.menu_actions.actions[result.output.selected_index].verb, verb);
  }
  for (const [kind, verb] of [["potion_popup", "close_potion_popup"], ["potion_targeting", "cancel_potion_target"]]) {
    const value = snapshot("root", [action(verb, "native_input")], kind);
    assert.equal(new PublicMenuTeacher().decide(input(value)).output.selected_index, 0);
  }
});

test("bounded passthrough scene families have explicit choices and browse cannot loop without exposure", () => {
  for (const [kind, verb] of [["card_bundle_selection", "confirm"], ["native_generated_card_choice", "select"],
    ["event_option", "activate"], ["event_dialogue", "activate"], ["rest_site", "activate"],
    ["treasure_room", "activate"], ["shop_inventory", "close"], ["game_over", "activate"]]) {
    assert.equal(new PublicMenuTeacher().decide(input(snapshot("root", [action(verb, "native_input")], kind)))
      .output.selected_index, 0, kind);
  }
  const teacher = new PublicMenuTeacher();
  const combat = snapshot("root", [action("select_card", "system_selection"), action("open_information")]);
  const emptyInformation = snapshot("information", [action("back")]);
  for (let index = 0; index < 16; index += 1)
    teacher.decide(input(index % 2 === 0 ? combat : emptyInformation));
  assert.equal(teacher.browseVisits, 0); // Offered actions never fabricate observed native exposure.
  const result = teacher.decide(input(combat));
  assert.equal(combat.menu_actions.actions[result.output.selected_index].verb, "select_card");
});

test("ordinary rewards join public reward subjects to GenericAction activate, then typed proceed", async () => {
  const { decodeTextMenuV2Snapshot } = await import("../../components/connector/sdk/typescript/dist/index.js");
  const teacher = new PublicMenuTeacher({ browse: false });
  const frame = decodeTextMenuV2Snapshot(ordinaryRewards()).data;
  const original = JSON.stringify(frame.menu_actions);
  const result = teacher.decide(input(frame));
  assert.equal(frame.menu_actions.actions[result.output.selected_index].subject_referent_id, "reward-gold");
  assert.equal(result.output.scores.length, frame.menu_actions.actions.length);
  assert.equal(JSON.stringify(frame.menu_actions), original); // No C filtering or mutation.
  const complete = decodeTextMenuV2Snapshot(ordinaryRewards([], { skips: false })).data;
  const proceed = teacher.decide(input(complete));
  assert.equal(complete.menu_actions.actions[proceed.output.selected_index].subject_referent_id, null);
  const ambiguous = ordinaryRewards([]);
  ambiguous.menu_actions.actions.push({ ...action("activate", "native_input"), action_id: "second-null-subject" });
  ambiguous.menu_actions.materialized_count = ambiguous.menu_actions.total_count = 2;
  assert.equal(teacher.decide(input(ambiguous)).output.selected_index, null);
  assert.equal(teacher.lastDiagnostic.reason, "reward_public_descriptor_unresolved");
  const unresolved = ordinaryRewards();
  unresolved.referents[0].role = "potion"; // Same label/id-like text is not the public reward binding.
  assert.equal(teacher.decide(input(unresolved)).output.selected_index, null);
  assert.equal(teacher.lastDiagnostic.reason, "reward_public_descriptor_unresolved");
  const explicitSkip = ordinaryRewards([]);
  explicitSkip.menu_actions.actions[0].verb = "skip";
  assert.equal(teacher.decide(input(explicitSkip)).output.selected_index, 0);
  explicitSkip.interaction.content.surface.proceed_skips_remaining_rewards = false;
  assert.equal(teacher.decide(input(explicitSkip)).output.selected_index, null);
});

test("linked child choices require the current public group and child, then exact screen skip/proceed", async () => {
  const { decodeTextMenuV2Snapshot } = await import("../../components/connector/sdk/typescript/dist/index.js");
  const teacher = new PublicMenuTeacher({ browse: false });
  const frame = decodeTextMenuV2Snapshot(linkedRewards()).data;
  const result = teacher.decide(input(frame));
  assert.equal(frame.menu_actions.actions[result.output.selected_index].verb, "claim_linked_reward");
  frame.interaction.content.surface.entries[0].choices[0].enabled = false;
  frame.menu_actions.actions = [frame.menu_actions.actions[0]];
  frame.menu_actions.materialized_count = frame.menu_actions.total_count = 1;
  assert.equal(frame.menu_actions.actions[teacher.decide(input(frame)).output.selected_index].verb, "skip_rewards");
  frame.interaction.content.surface.proceed_enabled = false;
  assert.equal(teacher.decide(input(frame)).output.selected_index, null);
});

test("card reward selects a typed public card before label-only alternatives; unknown effects abstain diagnostically", async () => {
  const { decodeTextMenuV2Snapshot } = await import("../../components/connector/sdk/typescript/dist/index.js");
  const teacher = new PublicMenuTeacher({ browse: false });
  const frame = decodeTextMenuV2Snapshot(cardRewards()).data;
  const result = teacher.decide(input(frame));
  assert.equal(frame.menu_actions.actions[result.output.selected_index].verb, "select");
  assert.equal(frame.menu_actions.actions[result.output.selected_index].subject_referent_id, "reward-card");
  const onlyAlternative = decodeTextMenuV2Snapshot(cardRewards({ cardsAvailable: false })).data;
  assert.equal(teacher.decide(input(onlyAlternative)).output.selected_index, null);
  assert.equal(teacher.lastDiagnostic.reason, "card_reward_alternative_effect_not_public");
  assert.equal(teacher.browseVisits, 0);
});

test("ambiguous reward diagnostic stays in raw result metadata outside the strict adapter output", async t => {
  const directory = await temporary(t);
  const records = await S0RawRecords.create(path.join(directory, "raw"), "test", { id: "teacher" });
  const value = cardRewards({ cardsAvailable: false }), request = input(value);
  await records.capture(full(value));
  const offerId = await records.offer(request);
  const teacher = new PublicMenuTeacher({ browse: false });
  const result = teacher.decide(request);
  await records.policyResult(offerId, request, result, teacher.lastDiagnostic);
  await records.close();
  const rows = (await readFile(path.join(records.directory, "records.jsonl"), "utf8")).trim().split("\n").map(JSON.parse);
  const payload = rows.find(row => row.type === "policy_result").payload;
  assert.equal(payload.diagnostic.reason, "card_reward_alternative_effect_not_public");
  assert.equal(payload.chosen_action_id, null);
  assert.deepEqual(Object.keys(payload.output).sort(), ["candidate_digest", "scores", "selected_index"]);
});

test("capsules preserve exact bytes; capture is not an offer; latest full snapshot join is mandatory", async t => {
  let records;
  const directory = await temporary(t, () => records?.close());
  records = await S0RawRecords.create(path.join(directory, "raw"), "test", { id: "teacher" });
  const value = full(base);
  await records.capture(value);
  assert.equal(records.offerCount, 0);
  assert.equal(await readFile(path.join(records.directory, records.latest.snapshot_path), "utf8"),
    value.serializedSnapshot);
  const offer = await records.offer(input(base));
  await records.policyResult(offer, input(base), new PublicMenuTeacher({ browse: false }).decide(input(base)));
  const next = structuredClone(base);
  next.observed_at = "2026-09-26T00:00:01Z"; // Same ID does not authorize stale byte association.
  await records.capture(full(next, "capture-2", 2));
  assert.throws(() => records.beginOffer(input(base)), /latest_exact_capsule/u);
  const wrongCatalog = input(next);
  wrongCatalog.candidate_digest = "f".repeat(64);
  assert.throws(() => records.beginOffer(wrongCatalog), /catalog_binding/u);
  await records.close();
  const rows = (await readFile(path.join(records.directory, "records.jsonl"), "utf8")).trim().split("\n").map(JSON.parse);
  assert.deepEqual(rows.map(row => row.type), ["capture", "policy_offer", "policy_result", "capture"]);
  assert.equal(rows[1].payload.capture_id, "capture-1");
  assert.equal(rows[1].payload.source_kind, "agent");
  assert.equal(rows[1].payload.I, false);
});

test("corrupt or over-budget raw capsules never become policy offers", async t => {
  let records;
  const directory = await temporary(t, () => records?.close());
  records = await S0RawRecords.create(path.join(directory, "raw"), "test", {}, { maxCaptureBytes: 1 });
  const value = full(base);
  const bad = structuredClone(value);
  bad.serializedSnapshot += " ";
  await assert.rejects(records.capture(bad), /identity_mismatch/u);
  await assert.rejects(records.capture(value), /budget_exceeded/u);
  assert.throws(() => records.beginOffer(input(base)), /latest_exact_capsule/u);
});

const capabilities = {
  protocol_version: "1.0.0", input_profile: "text-menu-v2", snapshot_schema: base.schema,
  host: { runtime_instance_id: "runtime-1", host_kind: "test", version: "1",
    implementation: { source_revision: "source", artifact_sha256: "b".repeat(64), module_version_id: "mvid" } },
  game: { version: "fixture", commit: "commit", modset: { status: "exact", fingerprint: "mods", loaded_mod_ids: ["mod"] } },
  verbs: ["select_card", "end_turn"]
};

test("manifest binds actual host/game/modset and limits interaction support", () => {
  const artifact = { id: "a", path: "a", sha256: "c".repeat(64) };
  const adapter = { id: "a", version: "1", protocol: "sts2.policy-runtime/decision-only-ndjson-2", code_sha256: "d".repeat(64) };
  const manifest = makeS0Manifest(capabilities, artifact, adapter);
  assert.equal(manifest.requirements.environment.connector_artifact_sha256, "b".repeat(64));
  assert.deepEqual(manifest.support.game_versions, ["fixture"]);
  assert.ok(manifest.support.interaction_kinds.includes("combat_turn"));
  assert.ok(manifest.support.interaction_kinds.includes("map_navigation"));
  assert.equal(manifest.support.interaction_kinds.includes("unknown_kind"), false);
  assert.deepEqual(manifest.support.action_verbs, capabilities.verbs);
  assert.equal(manifest.claims.full_run, false);
});

test("control release must be freshly confirmed with exact runtime and null owner", async () => {
  const control = { schema: "sts2.player-environment/control-1", protocol_version: "1.0.0",
    runtime_instance_id: "runtime-1", clients: [], controller: null };
  const fetchControl = value => async (url, options) => {
    // Literal production route, reached through the actual built SDK/client and its decoder.
    assert.equal(String(url), "http://fixture/api/player-environment/controller");
    assert.equal(options.method, "GET");
    return new Response(JSON.stringify(value), { status: 200, headers: { "content-type": "application/json" } });
  };
  const confirmed = await confirmControlReleased("http://fixture", "runtime-1", fetchControl(control));
  assert.equal(confirmed.confirmed, true);
  assert.equal(confirmed.read_route, "/api/player-environment/controller");
  const omitted = { ...control };
  delete omitted.controller; // Actual C# WhenWritingNull serialization.
  assert.equal((await confirmControlReleased("http://fixture", "runtime-1", fetchControl(omitted))).confirmed, true);
  await assert.rejects(confirmControlReleased("http://fixture", "other", fetchControl(control)), /unconfirmed/u);
  await assert.rejects(confirmControlReleased("http://fixture", "runtime-1", fetchControl({ ...control,
    controller: { controller_lease_id: "lease", controller_generation: 1, client_session_id: "client",
      expires_at: "2026-10-08T00:00:00Z" } })), /unconfirmed/u);
  await assert.rejects(confirmControlReleased("http://fixture", "runtime-1", fetchControl({
    schema: control.schema, protocol_version: "1.0.0", runtime_instance_id: "runtime-1" })));
  for (const malformed of [{ ...control, clients: [{}] }, { ...control, clients: [null] },
    { ...control, unexpected: true }, null, []]) {
    await assert.rejects(confirmControlReleased("http://fixture", "runtime-1", fetchControl(malformed)));
  }
  assert.equal((await confirmControlReleased("http://fixture", "runtime-1", fetchControl({ ...omitted,
    clients: [{ client_session_id: "client", client_instance_id: "instance", product_id: "product",
      product_name: "name", product_version: "1", registered_at: "2026-10-08T00:00:00Z",
      last_seen_at: "2026-10-08T00:00:00Z" }] }))).confirmed, true);
  let failedRequests = 0;
  await assert.rejects(confirmControlReleased("http://fixture", "runtime-1", async (url, options) => {
    failedRequests += 1;
    assert.equal(String(url), "http://fixture/api/player-environment/controller");
    assert.equal(options.method, "GET");
    return new Response(JSON.stringify({ error: "not found" }), { status: 404 });
  }), /HTTP 404/u);
  assert.equal(failedRequests, 1);
});

function lifecycleDependencies(trace, { failStop = false, unknown = true, exitReceipt = {
  code: 0, signal: null, forced: false, host_shutdown: { status: "requested", http_status: 200,
    response: { status: "shutdown_requested", runtime_instance_id: "runtime-1" }, error: null }
}, failClose = false } = {}) {
  class Client {
    async textMenuV2Capabilities() { return { data: capabilities }; }
    async getFullTextMenuV2() { return full(base); }
  }
  class Connector {
    constructor(client, options) { assert.equal(options.observationAcquisition, "sealed-text-menu-v2"); this.client = client; }
    async observeTextMenuContext() { return (await this.client.getFullTextMenuV2()).context; }
  }
  class Runtime {
    constructor(options) { this.options = options; trace.push("runtime-created"); }
    async tick() {
      trace.push("tick");
      const context = await this.options.connector.observeTextMenuContext();
      const request = { ...input(context.snapshot), manifest: this.options.manifest };
      await this.options.statefulPolicy(request);
      return { type: unknown ? "unknown" : "text_native_delivered", status: { tainted: unknown } };
    }
    status() { return { mode: "human", lifecycle: "running" }; }
    async stop() { trace.push("stop"); if (failStop) throw new Error("failed-stop"); return { lifecycle: "stopped" }; }
  }
  return {
    async startEpisode(options) {
      trace.push("start"); assert.equal(options.characterId, "DEFECT"); assert.equal(options.ascension, 0);
      return { identity: { endpoint: "http://fixture", host: capabilities.host },
        async releaseController() { trace.push("handoff"); return { confirmed: true }; },
        async close() { trace.push("episode-close"); if (failClose) throw new Error("close-failed"); return exitReceipt; } };
    },
    PlayerEnvironmentRestClient: Client, ConnectorPolicyClient: Connector, PolicyRuntime: Runtime,
    POLICY_RUNTIME_VERSION: "fixture", validatePolicyManifest: () => {},
    runtimeCodeDigest: async () => "e".repeat(64),
    AgentRunEvidence: { create: async () => ({ attestAdapter: async () => {}, finalize: async () => {} }) },
    async confirmControlReleased() { trace.push("control-confirm"); return { confirmed: true }; }
  };
}

test("owning episode is handed off, unknown stops without a second tick, Stop precedes close", async t => {
  const directory = await temporary(t), trace = [];
  const result = await runS0({ mode: "collect", evidenceRoot: directory, seed: "1", browse: false },
    lifecycleDependencies(trace));
  assert.deepEqual(trace, ["start", "handoff", "runtime-created", "tick", "stop", "control-confirm", "episode-close"]);
  assert.equal(result.termination, "unknown_delivery_or_successor");
  assert.equal(result.offers, 1);
  assert.equal(result.stop_confirmed, true);
  assert.equal(result.native_exit.status, "reported");
  assert.equal(result.native_exit.receipt.code, 0);
  assert.equal(result.native_exit.receipt.forced, false);
});

test("new runs persist the actual forced exit tuple separately from control release and policy offers", async t => {
  const directory = await temporary(t), trace = [];
  const receipt = { code: null, signal: "SIGTERM", forced: true, host_shutdown: {
    status: "transport_error", response: null, error: "socket closed" } };
  const result = await runS0({ mode: "collect", evidenceRoot: directory, seed: "1", browse: false },
    lifecycleDependencies(trace, { exitReceipt: receipt }));
  assert.deepEqual(result.native_exit.receipt, receipt);
  assert.equal(result.native_exit.runtime_instance_id, "runtime-1");
  const rows = (await readFile(path.join(result.directory, "records.jsonl"), "utf8")).trim().split("\n").map(JSON.parse);
  assert.deepEqual(rows.at(-1).payload.native_exit, result.native_exit);
  assert.equal(result.stop_confirmed, true); // Released control never implies graceful process exit.
  assert.equal(result.offers, 1);
});

test("missing or failed new close receipt is recorded without an invented native exit", async t => {
  for (const [options, expected] of [[{ exitReceipt: null }, "not_reported"], [{ failClose: true }, "close_failed"]]) {
    const directory = await temporary(t), trace = [];
    await assert.rejects(runS0({ mode: "collect", evidenceRoot: directory, seed: "1", browse: false },
      lifecycleDependencies(trace, options)), error => {
        assert.equal(error.summary.native_exit.status, expected);
        assert.equal(error.summary.native_exit.receipt, null);
        assert.equal(error.summary.stop_confirmed, true);
        assert.ok(error.summary.errors.length > 0);
        return true;
      });
    assert.equal(trace.at(-1), "episode-close");
  }
});

test("Stop failure still closes owning episode and does not claim release", async t => {
  const directory = await temporary(t), trace = [];
  await assert.rejects(runS0({ mode: "collect", evidenceRoot: directory, seed: "1", browse: false },
    lifecycleDependencies(trace, { failStop: true })), error => {
      assert.equal(error.summary.stop_confirmed, false);
      assert.match(error.message, /failed-stop/u);
      return true;
    });
  assert.equal(trace.at(-1), "episode-close");
  assert.equal(trace.includes("control-confirm"), false);
});

test("finite CLI options reject duplicate flags and increased autonomy", async () => {
  assert.throws(() => parseArguments(["collect", "--seed", "1", "--seed", "2"]), /duplicate/u);
  await assert.rejects(runS0({ mode: "collect", maxCalls: 61 }), /finite_budget/u);
});

test("actual CLI directory parser is resolved by the Host installation resolver before Driver start", async t => {
  const directory = await temporary(t), trace = [];
  const options = parseArguments(["collect", "--installation", path.join(directory, "game"),
    "--local-root", path.join(directory, "local"), "--evidence-root", path.join(directory, "evidence"),
    "--seed", "1", "--browse", "false"]);
  assert.equal(typeof options.installation, "string");
  const deps = lifecycleDependencies(trace);
  const start = deps.startEpisode;
  let resolved;
  deps.resolveInstallation = value => {
    assert.equal(value, options.installation);
    resolved = resolveInstallation(value);
    return resolved;
  };
  deps.startEpisode = actual => {
    assert.equal(actual.installation, resolved);
    assert.equal(actual.installation.game_dir, options.installation);
    assert.equal(typeof actual.installation.data_dir, "string");
    assert.equal(typeof actual.installation.release_info, "string");
    assert.equal(typeof actual.installation.executable, "string");
    return start(actual);
  };
  const result = await runS0(options, deps);
  assert.equal(result.stop_confirmed, true);
  assert.equal(trace.at(-1), "episode-close");
});
