import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { appendFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { createSession, reduce, resolveFollowBasis, modelView } from "@rsgcsg/sts2-connector-client/p1-global";
import { resolveDotnet } from "../src/managed-candidate.mjs";
import { startJsonLineProcess } from "../src/json-line-process.mjs";
import { ManagedPlayerEnvironmentSession } from "../src/managed-player-environment.mjs";
import { ManagedPeDriverSession } from "../src/managed-pe-driver-session.mjs";

// Only native relic acquisition/setup is privileged. Every chosen input uses
// the production native dispatcher, JSON serializer, pipe and public capture.
test("r1.8 consumes a native Claws preview and explicitly commits its selected source", { timeout: 120000 }, async context => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) { context.skip("set STS2_MANAGED_TEST_CANDIDATE for the exact native Claws component"); return; }
  const dotnet = await resolveDotnet(), env = { ...process.env, DOTNET_PROCESSOR_COUNT: "1" };
  await promisify(execFile)(dotnet.command, ["build", path.join(candidate, "u1-native-tests/U1Native.csproj"), "-c", "Release", "-m:1",
    "-p:Deterministic=true", `-p:PathMap=${path.resolve(candidate)}=/_/candidate/`],
    { cwd: candidate, env, timeout: 90000, maxBuffer: 4 * 1024 * 1024 });
  const native = await startJsonLineProcess({ command: dotnet.command,
    args: [path.join(candidate, "src/Sts2Headless/bin/U1Tests/net9.0/U1Native.dll"), "claws-driver"], cwd: candidate, env });
  const session = new ManagedPlayerEnvironmentSession({ process: native.process, character: "Defect",
    runtimeInstanceId: "u1-claws-wire", environmentFingerprint: "u1-claws-wire-component" });
  const driver = new ManagedPeDriverSession({ session, runtime: { adapterRuntimeInstanceId: "u1-claws-wire" } });
  try {
    await driver.handle({ command: "reset", seed: "U1CLAWSWIRE20261101A" });
    const basis = async () => (await driver.handle({ command: "capture" })).full.basis;
    let state = createSession("claws-new", "managed", "r1.8");
    const project = raw => {
      const result = reduce(state, raw); assert.equal(result.complete, true, String(result.missing)); state = result.state;
      if (process.env.STS2_U1_OBSERVATIONS_PATH) appendFileSync(process.env.STS2_U1_OBSERVATIONS_PATH, JSON.stringify(modelView(result.observation)) + "\n");
      return result;
    };
    const parent = await basis();
    assert.equal(reduce(createSession("claws-old", "managed"), parent).complete, false);
    let current = project(parent);
    const initial = current.observation.scene.content.surface;
    assert.equal(initial.selector_request.transformation_preview, "deterministic_native_after");
    assert.ok(initial.preview_options.every(option => option.after_availability === "available" && option.after.definition_id === "MAUL"));
    const source = initial.cards.find(card => card.is_upgraded);
    assert.ok(source);
    const choose = current.observation.catalog.find(choice => choice.subject === source.entity_id && choice.kind === "selector.choose");
    assert.ok(choose);
    const send = async (raw, action, id) => {
      const result = (await driver.handle({ command: "step", mutation_request_id: id,
        expected_snapshot_id: raw.snapshot.snapshot_id, bound_action_id: action.bound_action_id })).receipt;
      assert.equal(result.delivery, "delivered");
    };
    await send(parent, current.lowering[choose.choice_id].first, "claws-select");
    const selected = await basis(); current = project(selected);
    assert.equal(current.observation.scene.content.surface.selected_count, 1);
    const finish = current.observation.catalog.find(choice => choice.kind === "selector.finish"); assert.ok(finish);
    const plan = current.lowering[finish.choice_id]; assert.equal(plan.follow.length, 1);
    await send(selected, plan.first, "claws-preview");
    const preview = await basis(); project(preview);
    const follow = resolveFollowBasis(plan.follow[0], plan, preview); assert.ok(follow);
    const changed = structuredClone(preview); changed.snapshot.interaction.content.surface.preview_options[0].after.description += "changed";
    assert.equal(resolveFollowBasis(plan.follow[0], plan, changed), null);
    await send(preview, follow, "claws-explicit-confirm");
    const committed = project(await basis());
    assert.equal(committed.observation.deck.content.cards.filter(card => card.definition_id === "MAUL").length, 1);
    assert.equal(committed.observation.deck.content.cards.some(card => card.entity_id === source.entity_id && card.definition_id !== "MAUL"), false);
  } finally { await driver.shutdown(); }
});
