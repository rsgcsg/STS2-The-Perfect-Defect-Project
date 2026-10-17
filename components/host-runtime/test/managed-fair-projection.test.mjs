import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { resolveDotnet } from "../src/managed-candidate.mjs";
import { combatState, renamePrivateCounters } from "./fixtures/managed-fair-combat.mjs";
import {
  ManagedPlayerEnvironmentSession,
  projectManagedCandidateDecision
} from "../src/managed-player-environment.mjs";

const identity = {
  runtimeInstanceId: "fair-runtime",
  environmentFingerprint: "fair-environment",
  sequence: 7
};

function publicBundle(projection) {
  // Only the wall-clock capture envelope differs when re-projecting the same
  // public snapshot version. Keep every public ID, descriptor and payload.
  return {
    snapshot: { ...projection.snapshot, observed_at: "same-public-capture-time" },
    reads: [...projection.reads.values()]
  };
}

function bundleDigest(value) {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

test("explicitly absent native stars survive JSON while missing or invalid HUD metrics mark the capture incomplete", () => {
  const state = combatState();
  const complete = projectManagedCandidateDecision({ state, ...identity });
  assert.equal(complete.snapshot.completeness.status, "complete");
  assert.equal(JSON.parse(JSON.stringify(complete.snapshot)).interaction.content.context.player.stars, null);
  for (const key of ["stars", "round", "turn_owner", "energy", "max_energy", "orb_slots",
    "draw_pile_count", "discard_pile_count", "exhaust_pile_count"]) {
    const missing = structuredClone(state);
    delete missing[key];
    const projection = projectManagedCandidateDecision({ state: missing, ...identity });
    assert.equal(projection.snapshot.completeness.status, "partial", key);
  }
  for (const mutate of [value => { value.player.block = undefined; },
    value => { value.draw_pile_count = -1; }, value => { value.energy = Infinity; },
    value => { value.stars = "0"; }]) {
    const invalid = structuredClone(state); mutate(invalid);
    const projection = projectManagedCandidateDecision({ state: invalid, ...identity });
    assert.equal(projection.snapshot.completeness.status, "partial");
  }
});

for (const identityMode of ["crypto", "sequence"]) {
  test(`complete public bundle is invariant to hidden draw permutation and native counters (${identityMode})`, () => {
    const state = combatState();
    const original = projectManagedCandidateDecision({ state, ...identity, identityMode });
    const expected = publicBundle(original);
    for (const order of [[3, 1, 0, 2], [2, 0, 3, 1], [1, 3, 2, 0]]) {
      const hidden = renamePrivateCounters(structuredClone(state));
      hidden.combat_piles[0].cards = order.map((index) => hidden.combat_piles[0].cards[index]);
      hidden.hidden_rng = `unexposed-${order.join("")}`;
      hidden.internal_counter += 100;
      const actual = projectManagedCandidateDecision({ state: hidden, ...identity, identityMode });
      assert.deepEqual(publicBundle(actual), expected);
      assert.equal(bundleDigest(publicBundle(actual)), bundleDigest(expected));
      if (identityMode === "crypto") assert.notEqual(actual.raw_state_sha256, original.raw_state_sha256);
      const play = [...actual.bindings.values()].find((binding) => binding.raw_request.action === "play_card");
      assert.equal(play.raw_request.args.card_ref, "native-9201");
      assert.equal(play.raw_request.args.target_ref, "native-9301");
    }
    const aliases = new Map();
    const allocated = projectManagedCandidateDecision({ state, ...identity, identityMode,
      publicEntityAliases: aliases });
    const permuted = structuredClone(state);
    permuted.combat_piles[0].cards.reverse();
    assert.deepEqual(publicBundle(projectManagedCandidateDecision({ state: permuted, ...identity,
      identityMode, publicEntityAliases: aliases })), publicBundle(allocated));
    assert.equal(JSON.stringify(expected).includes("native-"), false);
    assert.equal(JSON.stringify(expected).includes("hidden_card_state"), false);
  });
}

test("draw canonicalization preserves duplicate visible instances and every projected card distinction", () => {
  const state = combatState();
  const projection = projectManagedCandidateDecision({ state, ...identity });
  const pile = [...projection.reads.values()].find((read) => read.descriptor.kind === "combat_piles");
  const draw = pile.content.zones[0];
  assert.equal(draw.card_count, 4);
  assert.equal(draw.cards.length, 4);
  assert.equal(new Set(draw.cards.map((value) => value.entity_id)).size, 4);
  assert.equal(draw.cards.filter((value) => value.name === "Strike" && value.cost === "1").length, 2);
  const enchanted = draw.cards.find((value) => value.cost === "0");
  assert.equal(enchanted.is_upgraded, true);
  assert.equal(enchanted.existing_enchantment.description, "Deal 2 more damage.");
  assert.equal(enchanted.existing_enchantment.amount, 2);
  assert.equal(enchanted.description, "Deal 6 damage.");

  const changed = structuredClone(state);
  changed.combat_piles[0].cards[2].description = "Deal 9 damage. The revealed card is upgraded.";
  const actual = publicBundle(projectManagedCandidateDecision({ state: changed, ...identity }));
  assert.notEqual(bundleDigest(actual), bundleDigest(publicBundle(projection)));
  assert.equal(JSON.stringify(actual).includes("The revealed card is upgraded."), true);

  const visibleCounter = structuredClone(state);
  visibleCounter.player.relics[0].counter = 3;
  assert.notEqual(bundleDigest(publicBundle(projectManagedCandidateDecision({ state: visibleCounter, ...identity }))),
    bundleDigest(publicBundle(projection)), "a genuinely visible relic counter is retained");

  for (const zone of [1, 2]) {
    const ordered = structuredClone(state);
    ordered.combat_piles[zone].cards = [state.player.deck[0], state.player.deck[3]];
    const reversed = structuredClone(ordered);
    reversed.combat_piles[zone].cards.reverse();
    assert.notEqual(bundleDigest(publicBundle(projectManagedCandidateDecision({ state: ordered, ...identity }))),
      bundleDigest(publicBundle(projectManagedCandidateDecision({ state: reversed, ...identity }))));
  }
});

test("session retains public object links, exact private bindings and snapshot-version stale refusal", async () => {
  const before = combatState();
  // A card visible in both the hand and deck is the same actual object; public
  // aliases must preserve that relationship without exposing its native token.
  before.player.deck.push({ ...before.hand[0] });
  const after = structuredClone(before);
  after.hand.reverse();
  after.enemies.reverse();
  after.enemies[0].hp = 9;
  const calls = [];
  const process = { async request(request) {
    calls.push(request);
    if (request.cmd === "start_run" || request.cmd === "reset_run") return before;
    assert.deepEqual(request, { cmd: "action", action: "play_card",
      args: { card_ref: "native-201", target_ref: "native-301" } });
    return after;
  } };
  const session = new ManagedPlayerEnvironmentSession({ process,
    runtimeInstanceId: identity.runtimeInstanceId, environmentFingerprint: identity.environmentFingerprint });
  const snapshot = await session.mount({ seed: "FAIR-SYNTHETIC" });
  const read = (kind, source = snapshot) => session.read({
    readId: source.reads.find((item) => item.kind === kind).read_id,
    expectedSnapshotId: source.snapshot_id
  });
  const deck = read("run_deck");
  assert.deepEqual({ ...read("run_deck"), observed_at: "same" }, { ...deck, observed_at: "same" });
  assert.equal(session.observe(), snapshot);
  assert.equal(calls.length, 1); // Observe/Read did not create a new projection version.
  const hand = snapshot.interaction.content.context.player.hand;
  assert.equal(new Set(hand.map((value) => value.entity_id)).size, 2);
  assert.equal(deck.content.cards.at(-1).entity_id, hand[0].entity_id);
  const enemies = snapshot.interaction.content.context.enemies;
  assert.equal(new Set(enemies.map((value) => value.entity_id)).size, 2);
  assert.equal(enemies[0].combat_id, before.enemies[0].combat_id); // Typed public native combat id.
  const action = snapshot.bound_actions.actions.find((value) => value.verb === "play"
    && value.subject_referent_id === hand[0].entity_id
    && value.arguments[0].referent_id === enemies[0].entity_id);
  assert.ok(action);
  const receipt = await session.submit({ requestId: "play-once",
    expectedSnapshotId: snapshot.snapshot_id, boundActionId: action.bound_action_id });
  assert.equal(receipt.delivery, "delivered");
  const successor = receipt.successor;
  assert.notEqual(successor.snapshot_id, snapshot.snapshot_id);
  assert.equal(successor.sequence, snapshot.sequence + 1);
  assert.equal(successor.interaction.content.context.player.hand[1].entity_id, hand[0].entity_id);
  assert.equal(successor.interaction.content.context.enemies[1].entity_id, enemies[0].entity_id);
  assert.equal(successor.interaction.content.context.enemies[1].combat_id, enemies[0].combat_id);
  assert.throws(() => read("run_deck"), /stale_snapshot/u);
  const stale = await session.submit({ requestId: "old-snapshot", expectedSnapshotId: snapshot.snapshot_id,
    boundActionId: action.bound_action_id });
  assert.equal(stale.reason_code, "stale_snapshot");
  assert.equal(calls.length, 2);
  const remounted = await session.mount({ seed: "FAIR-SYNTHETIC", reset: true });
  assert.notEqual(remounted.interaction.content.context.player.hand[0].entity_id, hand[0].entity_id);
  assert.notEqual(remounted.interaction.content.context.enemies[0].entity_id, enemies[0].entity_id);
});


test("RunSimulator's actual fair-pile and AnyPlayer helpers feed the strict public reader", async () => {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const patch = readFileSync(path.join(root, "experiments/managed-exact/patches/sts2-cli-d11aa88-v01110.patch"), "utf8");
  const additions = patch.split(/\r?\n/u).filter((line) => line.startsWith("+") && !line.startsWith("+++"))
    .map((line) => line.slice(1)).join("\n");
  const nativePileBody = additions.slice(additions.indexOf("    private Dictionary<string, object?> VisiblePile("),
    additions.indexOf("    private Dictionary<string, object?> DetectPostCombatState("));
  assert.match(nativePileBody, /return FairPileProjection\.Create\(pile, cards\.Select\(card =>/u);
  assert.doesNotMatch(nativePileBody, /native_ref|NativeObjectIdentity|\["index"\]/u);
  const addedFiles = new Map(patch.split(/^diff --git /mu).slice(1).filter((block) =>
    block.includes("\nnew file mode ")).map((block) => [block.split("\n")[0].split(" b/")[1],
    block.split(/\r?\n/u).filter((line) => line.startsWith("+") && !line.startsWith("+++"))
      .map((line) => line.slice(1)).join("\n") + "\n"]));
  const project = "tests/NativePlayWindowAdmissionTests.csproj";
  const projectSource = addedFiles.get(project);
  assert.equal(typeof projectSource, "string");
  const includes = [...projectSource.matchAll(/Compile Include="\.\.\/(.*?)"/gu)].map((match) => match[1]);
  assert.ok(includes.includes("src/Sts2Headless/FairPileProjection.cs"));
  const temporary = mkdtempSync(path.join(os.tmpdir(), "sts2-fair-pile-contract-"));
  try {
    for (const name of [project, "tests/Program.cs", ...includes]) {
      const source = addedFiles.get(name);
      assert.equal(typeof source, "string", `actual committed added source: ${name}`);
      const destination = path.join(temporary, name);
      mkdirSync(path.dirname(destination), { recursive: true });
      writeFileSync(destination, source);
    }
    const dotnet = await resolveDotnet();
    const { stdout } = await promisify(execFile)(dotnet.command,
      ["run", "--project", path.join(temporary, project), "--configuration", "Release", "--no-launch-profile"],
      { cwd: temporary, timeout: 180_000, maxBuffer: 4 * 1024 * 1024,
        env: { ...process.env, DOTNET_PROCESSOR_COUNT: "2", DOTNET_CLI_USE_MSBUILD_SERVER: "0" } });
    assert.match(stdout, /6 fair pile projection cases passed/u);
    const emitted = stdout.split(/\r?\n/u).find((line) => line.startsWith("fair-pile-json="));
    assert.ok(emitted, "the compiled actual producer helper must emit its original JSON");
    const nativePile = JSON.parse(emitted.slice("fair-pile-json=".length));
    const state = combatState();
    state.combat_piles[0] = nativePile;
    state.draw_pile_count = nativePile.cards.length;
    const projection = projectManagedCandidateDecision({ state, ...identity });
    const read = [...projection.reads.values()].find((value) => value.descriptor.kind === "combat_piles");
    assert.equal(read.content.zones[0].card_count, 3);
    assert.equal(read.content.zones[0].cards.filter((value) => value.cost === "1").length, 2);
    assert.equal(new Set(read.content.zones[0].cards.map((value) => value.entity_id)).size, 3);
    assert.equal(JSON.stringify(publicBundle(projection)).includes("private_card_state"), false);

    assert.match(stdout, /12 AnyPlayer target admission cases passed/u);
    const targetLine = stdout.split(/\r?\n/u).find((line) => line.startsWith("any-player-target-json="));
    assert.ok(targetLine, "the actual native admission helper emits its original target domain");
    const targetDomain = JSON.parse(targetLine.slice("any-player-target-json=".length));
    state.player.potions = [{ slot: 0, native_ref: "native-potion", id: "ENERGY_POTION",
      name: "Energy Potion", description: "Gain 2 Energy.", keywords: [], card_previews: [],
      hover_facts_complete: true, target_type: "AnyPlayer", usage: "CombatOnly",
      can_use: true, can_discard: true, ...targetDomain }];
    const withPotion = projectManagedCandidateDecision({ state, ...identity });
    assert.equal(withPotion.snapshot.completeness.status, "complete");
    assert.equal(withPotion.snapshot.bound_actions.status, "complete");
    const use = withPotion.snapshot.bound_actions.actions.find((action) => action.verb === "use");
    assert.deepEqual(use.arguments, [{ role: "target",
      referent_id: withPotion.snapshot.persistent.content.player.entity_id }]);
    assert.equal(withPotion.bindings.get(use.bound_action_id).raw_request.args.target_ref,
      targetDomain.valid_target_refs[0]);
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});
