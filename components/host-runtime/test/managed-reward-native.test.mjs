import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { resolveDotnet } from "../src/managed-candidate.mjs";
import { JsonLineProcess } from "../src/json-line-process.mjs";
import { ManagedPlayerEnvironmentSession, projectManagedCandidateDecision } from "../src/managed-player-environment.mjs";

import { captureManagedPublicBasis } from "../src/managed-public-basis.mjs";
import { createSession, reduce } from "@rsgcsg/sts2-connector-client/p1-global";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("native return-to-hand delivery, multi-pile choice, Plasma HUD and gardener turns cross the real serializer", async context => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) { context.skip("set STS2_MANAGED_TEST_CANDIDATE for exact proprietary native component fixtures"); return; }
  const dotnet = await resolveDotnet();
  const artifacts = ["Sts2Headless.dll", "GodotSharp.dll"].map(name =>
    path.join(candidate, "src/Sts2Headless/bin/Release/net9.0", name));
  const hashes = () => artifacts.map(file => createHash("sha256").update(readFileSync(file)).digest("hex"));
  const before = hashes();
  const { stdout } = await promisify(execFile)(dotnet.command,
    ["run", "--project", path.join(candidate, "m2a-native-tests/M2aNative.csproj"), "-c", "Release",
      "-p:Deterministic=true", `-p:PathMap=${path.resolve(candidate)}=/_/candidate/`],
    { cwd: candidate, timeout: 180000, maxBuffer: 4 * 1024 * 1024 });
  assert.deepEqual(hashes(), before, "native regressions retain the frozen candidate bytes");
  assert.match(stdout, /51 exact native M2a checks passed/u);
  assert.match(stdout, /exact native gardener turns reached round 4/u);
  assert.match(stdout, /exact native jungle option retained gold hooks and completion/u);
  const counterState = JSON.parse(stdout.split(/\r?\n/u).find(line => line.startsWith("native-m2a-counter-state=")).slice("native-m2a-counter-state=".length));
  const session = new ManagedPlayerEnvironmentSession({ process: { request: async () => counterState },
    runtimeInstanceId: "same-native-counter", environmentFingerprint: "same-native-counter" });
  const mounted = await session.mount({ seed: "NATIVE_COMPONENT_FIXTURE" });
  const beforePublic = structuredClone(mounted);
  const legacy = captureManagedPublicBasis(session, { ordinal: 1 });
  assert.equal(legacy.basis.revision, "1");
  assert.equal(legacy.basis.settled_relic_counters, undefined);
  const captured = captureManagedPublicBasis(session, { ordinal: 2, settledRelicCounters: true });
  assert.equal(captured.basis.revision, "3");
  assert.equal(captured.basis.capture_status, "complete");
  assert.deepEqual(captured.basis.settled_relic_counters.facts.map(v => [v.definition_id,v.display_counter,v.logical_counter]).sort(),
    [["KUSARIGAMA",3,2],["LASTING_CANDY",2,1]]);
  assert.deepEqual(session.observe(), beforePublic, "witness capture does not mutate original public snapshot");
  const derived = reduce(createSession("native-witness", "managed", "r1.7"), captured.basis);
  assert.equal(derived.complete, true);
  assert.deepEqual(derived.observation.player.content.relics.filter(r => ["KUSARIGAMA", "LASTING_CANDY"].includes(r.definition_id)).map(r => r.counter).sort(), [1,2]);
  assert.throws(() => session.settledRelicCounters("foreign-snapshot"), /stale_snapshot/u);
  for (const [prefix, kind] of [["native-m2a-pile-state=", "native_combat_pile_selection"],
    ["native-m2a-hand-state=", "combat_hand_card_selection"]]) {
    const state = JSON.parse(stdout.split(/\r?\n/u).find(line => line.startsWith(prefix)).slice(prefix.length));
    const projection = projectManagedCandidateDecision({ state, sequence: 1,
      runtimeInstanceId: "native-m2a-fixture", environmentFingerprint: "native-m2a-fixture" });
    assert.equal(projection.snapshot.completeness.status, "complete");
    assert.equal(projection.snapshot.interaction.kind, kind);
    assert.ok(projection.snapshot.bound_actions.actions.length > 0);
  }
});

test('exact native hand callback stages original objects through explicit confirm and complete Host projection', async context => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) { context.skip('set STS2_MANAGED_TEST_CANDIDATE for exact proprietary native component fixtures'); return; }
  const dotnet = await resolveDotnet();
  const artifact = path.join(candidate, 'src/Sts2Headless/bin/Release/net9.0/Sts2Headless.dll');
  const before = createHash('sha256').update(readFileSync(artifact)).digest('hex');
  const { stdout } = await promisify(execFile)(dotnet.command,
    ['run', '--project', path.join(candidate, 'native-hand-tests/NativeHand.csproj'), '-c', 'Release',
      '-p:Deterministic=true', `-p:PathMap=${path.resolve(candidate)}=/_/candidate/`],
    { cwd: candidate, timeout: 180000, maxBuffer: 4 * 1024 * 1024 });
  assert.equal(createHash('sha256').update(readFileSync(artifact)).digest('hex'), before);
  assert.match(stdout, /31 exact native hand-selection checks passed/u);
  const state = JSON.parse(stdout.split(/\r?\n/u).find(line => line.startsWith('native-hand-state=')).slice('native-hand-state='.length));
  const projection = projectManagedCandidateDecision({ state, sequence: 1,
    runtimeInstanceId: 'native-hand-fixture', environmentFingerprint: 'native-hand-fixture' });
  assert.equal(projection.snapshot.completeness.status, 'complete');
  const surface = projection.snapshot.interaction.content.surface;
  assert.equal(surface.kind, 'combat_hand_card_selection');
  assert.equal(surface.selected_count, 2);
  assert.ok(projection.snapshot.bound_actions.actions.some(action => action.label === 'Confirm selected cards'));
});

test('exact native run-domain generated choice retains callback ownership and complete public projection', async context => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) { context.skip('set STS2_MANAGED_TEST_CANDIDATE for exact proprietary native component fixtures'); return; }
  const dotnet = await resolveDotnet();
  const artifact = path.join(candidate, 'src/Sts2Headless/bin/Release/net9.0/Sts2Headless.dll');
  const before = createHash('sha256').update(readFileSync(artifact)).digest('hex');
  const { stdout } = await promisify(execFile)(dotnet.command,
    ['run', '--project', path.join(candidate, 'native-run-choice-tests/NativeRunChoice.csproj'), '-c', 'Release',
      '-p:Deterministic=true', `-p:PathMap=${path.resolve(candidate)}=/_/candidate/`],
    { cwd: candidate, timeout: 180000, maxBuffer: 4 * 1024 * 1024 });
  assert.equal(createHash('sha256').update(readFileSync(artifact)).digest('hex'), before);
  assert.match(stdout, /10 exact native run generated-choice checks passed/u);
  const state = JSON.parse(stdout.split(/\r?\n/u).find(line => line.startsWith('native-run-choice-state=')).slice('native-run-choice-state='.length));
  const projection = projectManagedCandidateDecision({ state, sequence: 1,
    runtimeInstanceId: 'native-run-choice-fixture', environmentFingerprint: 'native-run-choice-fixture' });
  assert.equal(projection.snapshot.completeness.status, 'complete');
  assert.equal(projection.snapshot.status, 'interactive');
  assert.equal(projection.snapshot.interaction.content.context.kind, 'selection');
  assert.equal(projection.snapshot.bound_actions.actions.length, 2);
  assert.deepEqual(projection.snapshot.bound_actions.actions.map(action => action.label), ['Choose Impatience', 'Choose Salvo']);
});

test("both exact Hosts compile the same native reward/read qualification source", () => {
  const patch = readFileSync(path.join(root, "experiments/managed-exact/patches/sts2-cli-d11aa88-v01110.patch"), "utf8");
  const files = new Map(patch.split(/^diff --git /mu).slice(1).filter(block => block.includes("\nnew file mode "))
    .map(block => [block.split("\n")[0].split(" b/")[1], block.split(/\r?\n/u)
      .filter(line => line.startsWith("+") && !line.startsWith("+++"))
      .map(line => line.slice(1)).join("\n") + "\n"]));
  for (const name of ["NativeCardRewardAlternativePreview.cs", "NativeRunHookReadQualification.cs",
    "NativeCardRewardSelectionObservation.cs", "NativeRewardSetObservation.cs",
    "NativeMapReadProjection.cs", "NativeCardRenderTransaction.cs",
    "NativeCalculatedVariableInventory.cs", "NativeCardPreviewReadQualification.Rendering.cs", "NativeCardPreviewReadQualification.cs",
    "NativeDetachedCardCopy.cs", "NativeDetachedCardPreview.cs", "NativeEnchantmentMethodInventory.cs",
    "NativeSettledRelicCounter.cs", "NativeUpgradeMethodInventory.cs", "NativePublicCardDetails.cs", "NativeVisibleText.cs"])
    assert.equal(files.get(`src/Sts2Headless/${name}`),
      readFileSync(path.join(root, "../native-foundation/src", name), "utf8"));
});

test("exact native reward factory preserves Skip/reroll/sacrifice without selection effects", async context => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) { context.skip("set STS2_MANAGED_TEST_CANDIDATE for exact proprietary native component fixtures"); return; }
  const dotnet = await resolveDotnet();
  const artifact = path.join(candidate, "src/Sts2Headless/bin/Release/net9.0/Sts2Headless.dll");
  const before = createHash("sha256").update(readFileSync(artifact)).digest("hex");
  const { stdout } = await promisify(execFile)(dotnet.command,
    ["run", "--project", path.join(candidate, "native-tests/NativeMechanisms.csproj"), "--configuration", "Release",
      "-p:Deterministic=true", `-p:PathMap=${path.resolve(candidate)}=/_/candidate/`],
    { cwd: candidate, timeout: 180_000, maxBuffer: 4 * 1024 * 1024 });
  assert.equal(createHash("sha256").update(readFileSync(artifact)).digest("hex"), before,
    "native mechanism tests must retain the prepared candidate artifact identity");
  assert.match(stdout, /17 exact native reward factory checks passed/u);
  assert.match(stdout, /8 exact Managed selector view binding checks passed/u);
  assert.match(stdout, /8 exact Managed bundle owner checks passed/u);
  assert.match(stdout, /23 exact native relic command checks passed/u);
  assert.match(stdout, /7 exact native free travel checks passed/u);
  assert.match(stdout, /1 exact native wire null serialization check passed/u);
  assert.match(stdout, /15 exact native card parent checks passed/u);
  assert.match(stdout, /3 exact native screenshake scope checks passed/u);
  const paired = stdout.split(/\r?\n/u).find(line => line.startsWith("native-reward-card-pair="));
  assert.ok(paired, "original native outer and opened callback card renderings must be retained");
  const { outer, opened } = JSON.parse(paired.slice("native-reward-card-pair=".length));
  assert.deepEqual(opened, outer);
  assert.equal(outer.render_complete, true);
  assert.equal(outer.description, "Deal 6 damage.");
  assert.equal(outer.target_type, "AnyEnemy");
  assert.equal(outer.public_details.upgrade_preview.status, "available");
  assert.equal(outer.public_details.upgrade_preview.card.is_upgraded, true);
  const emitted = stdout.split(/\r?\n/u).find(line => line.startsWith("native-reward-json="));
  assert.ok(emitted);
  const facts = JSON.parse(emitted.slice("native-reward-json=".length));
  assert.equal(facts.Complete, true);
  assert.deepEqual(facts.Alternatives.map(option => option.IsNativeSkip), [true,false]);
  assert.deepEqual(facts.Alternatives.map(option => [option.OptionId, option.AfterSelected]), [
    ["Skip", "end_selection_and_do_not_complete_reward"], ["SACRIFICE", "end_selection_and_complete_reward"]
  ]);
});

test('exact PunchOff Nab retains native curse, relic reward and event completion without a scene', async context => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) { context.skip('set STS2_MANAGED_TEST_CANDIDATE for exact proprietary native component fixtures'); return; }
  const artifact = path.join(candidate, 'src/Sts2Headless/bin/Release/net9.0/Sts2Headless.dll');
  const before = createHash('sha256').update(readFileSync(artifact)).digest('hex');
  const dotnet = await resolveDotnet();
  const native = new JsonLineProcess({ command: dotnet.command, args: [artifact], cwd: candidate });
  try {
    assert.equal((await native.nextMessage(60000)).type, 'ready');
    const initial = await native.request({ cmd: 'reset_run', character: 'Defect', ascension: 0,
      seed: 'PUNCHOFF_NATIVE_COMPONENT_FIXTURE', lang: 'en' }, 60000);
    assert.equal(initial.type, 'decision');
    // Explicit component fixture setup, not a gameplay evaluation or a retry of
    // the failed canary. The bound option itself executes the original event.
    const event = await native.request({ cmd: 'enter_room', type: 'event', event: 'PUNCH_OFF' }, 60000);
    assert.equal(event.type, 'decision');
    assert.equal(event.event_id, 'PUNCH_OFF');
    const injuries = value => value.player.deck.filter(card => card.id === 'INJURY').length;
    const originalInjuries = injuries(event), originalSize = event.player.deck_size;
    const reward = await native.request({ cmd: 'action', action: 'choose_option', args: {
      option_index: 0, room_ref: event.room_ref, event_ref: event.event_ref,
      option_ref: event.options[0].native_ref
    } }, 60000);
    assert.equal(reward.type, 'decision', JSON.stringify(reward));
    assert.equal(reward.decision, 'reward_set');
    assert.equal(injuries(reward), originalInjuries + 1, 'original CardPileCmd adds the real Injury once');
    assert.equal(reward.player.deck_size, originalSize + 1);
    assert.equal(reward.rewards.length, 1);
    assert.equal(reward.rewards[0].kind, 'relic', 'original RewardsCmd/RelicReward remains awaited');
    const completed = await native.request({ cmd: 'action', action: 'select_reward', args: {
      reward_ref: reward.rewards[0].native_ref
    } }, 60000);
    assert.equal(completed.type, 'decision', JSON.stringify(completed));
    assert.equal(completed.decision, 'event_choice');
    assert.equal(injuries(completed), originalInjuries + 1);
    assert.equal(completed.options.length, 1);
    assert.equal(completed.options[0].is_proceed, true, 'original Nab finishes only after its reward');
  } finally {
    const exit = await native.stop({ request: { cmd: 'quit' }, timeoutMs: 5000 });
    assert.equal(exit.code, 0);
    assert.equal(createHash('sha256').update(readFileSync(artifact)).digest('hex'), before);
  }
});

for (const [optionIndex, oldCard, newCard] of [[0, 'STRIKE_DEFECT', 'ULTIMATE_STRIKE'], [1, 'DEFEND_DEFECT', 'ULTIMATE_DEFEND']]) {
  test(`exact Amalgamator option ${optionIndex} keeps two native removals and one creation around both visual calls`, async context => {
    const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
    if (!candidate) { context.skip('set STS2_MANAGED_TEST_CANDIDATE for exact proprietary native component fixtures'); return; }
    const dotnet = await resolveDotnet();
    const native = new JsonLineProcess({ command: dotnet.command,
      args: [path.join(candidate, 'src/Sts2Headless/bin/Release/net9.0/Sts2Headless.dll')], cwd: candidate });
    try {
      await native.nextMessage(60000);
      await native.request({ cmd: 'reset_run', character: 'Defect', ascension: 0,
        seed: `AMALGAMATOR_NATIVE_FIXTURE_${optionIndex}`, lang: 'en' }, 60000);
      const event = await native.request({ cmd: 'enter_room', type: 'event', event: 'AMALGAMATOR' }, 60000);
      assert.equal(event.type, 'decision');
      const count = (state, id) => state.player.deck.filter(card => card.id === id).length;
      const before = count(event, oldCard), size = event.player.deck_size;
      let state = await native.request({ cmd: 'action', action: 'choose_option', args: {
        option_index: optionIndex, room_ref: event.room_ref, event_ref: event.event_ref,
        option_ref: event.options[optionIndex].native_ref
      } }, 60000);
      assert.equal(state.decision, 'deck_card_select');
      assert.equal(state.min_select, 2);
      assert.equal(state.max_select, 2);
      for (let index = 0; index < 2; index++) {
        const card = state.cards.find(item => item.is_selectable);
        assert.ok(card);
        state = await native.request({ cmd: 'action', action: 'select_deck_card', args: {
          selector_ref: state.selector_ref, card_ref: card.native_ref
        } }, 60000);
        assert.equal(state.type, 'decision', JSON.stringify(state));
      }
      assert.equal(state.stage, 'preview');
      assert.equal(state.player.deck_size, size, 'tentative presentation does not perform native removals');
      const completed = await native.request({ cmd: 'action', action: 'confirm_deck_selection', args: {
        selector_ref: state.selector_ref, selected_refs: state.selected_refs.join(',')
      } }, 60000);
      assert.equal(completed.type, 'decision', JSON.stringify(completed));
      assert.equal(completed.decision, 'event_choice');
      assert.equal(count(completed, oldCard), before - 2);
      assert.equal(count(completed, newCard), 1);
      assert.equal(completed.player.deck_size, size - 1);
      assert.equal(completed.options[0].is_proceed, true);
    } finally {
      assert.equal((await native.stop({ request: { cmd: 'quit' }, timeoutMs: 5000 })).code, 0);
    }
  });
}
