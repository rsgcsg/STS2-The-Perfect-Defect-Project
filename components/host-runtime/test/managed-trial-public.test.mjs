import assert from "node:assert/strict";
import { appendFileSync } from "node:fs";
import { execFile } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { createSession, reduce, resolveFollowBasis, modelView } from "@rsgcsg/sts2-connector-client/p1-global";
import { resolveDotnet } from "../src/managed-candidate.mjs";
import { startJsonLineProcess } from "../src/json-line-process.mjs";
import { ManagedPlayerEnvironmentSession } from "../src/managed-player-environment.mjs";
import { ManagedPeDriverSession } from "../src/managed-pe-driver-session.mjs";

// Only EnterRoom/page setup is privileged. Production native dispatcher,
// serializer, pipe, public session/capture and reducer handle the chosen input.
// This is a controlled component chain, not a Reference or full gameplay run.
test("r1.8 explicitly folds the chosen Trial abandonment through the exact native modal", { timeout: 120000 }, async context => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) { context.skip("set STS2_MANAGED_TEST_CANDIDATE for the exact native Trial component"); return; }
  const dotnet = await resolveDotnet();
  const env = { ...process.env, DOTNET_PROCESSOR_COUNT: "1" };
  await promisify(execFile)(dotnet.command, ["build", path.join(candidate, "u1-native-tests/U1Native.csproj"), "-c", "Release", "-m:1",
    "-p:Deterministic=true", `-p:PathMap=${path.resolve(candidate)}=/_/candidate/`],
  { cwd: candidate, env, timeout: 90000, maxBuffer: 4 * 1024 * 1024 });
  const native = await startJsonLineProcess({ command: dotnet.command,
    args: [path.join(candidate, "src/Sts2Headless/bin/U1Tests/net9.0/U1Native.dll"), "trial-driver"], cwd: candidate, env });
  const session = new ManagedPlayerEnvironmentSession({ process: native.process, character: "Defect",
    runtimeInstanceId: "u1-trial-wire", environmentFingerprint: "u1-trial-wire-component" });
  const driver = new ManagedPeDriverSession({ session, runtime: { adapterRuntimeInstanceId: "u1-trial-wire" } });
  try {
    await driver.handle({ command: "reset", seed: "U1TRIALWIRE20261101A" });
    const basis = async () => (await driver.handle({ command: "capture" })).full.basis;
    const parent = await basis();
    assert.equal(reduce(createSession("trial-old", "managed", "r1.5"), parent).complete, false,
      "the old profile cannot silently publish the new abandonment intention");
    const first = reduce(createSession("trial-new", "managed", "r1.8"), parent);
    assert.equal(first.complete, true, String(first.missing));
    if (process.env.STS2_U1_OBSERVATIONS_PATH) appendFileSync(process.env.STS2_U1_OBSERVATIONS_PATH, JSON.stringify(modelView(first.observation)) + "\n");
    const choice = first.observation.catalog.find(value => value.label.startsWith("Confirm abandoning this run:"));
    assert.ok(choice, "abandonment consequences are explicit before model selection");
    assert.equal(first.forced_choice_id, null);
    const lowering = first.lowering[choice.choice_id];
    assert.equal(lowering.follow.length, 1);
    const opened = (await driver.handle({ command: "step", mutation_request_id: "trial-explicit-intention",
      expected_snapshot_id: parent.snapshot.snapshot_id, bound_action_id: lowering.first.bound_action_id })).receipt;
    assert.equal(opened.delivery, "delivered");
    const modal = await basis();
    const projected = reduce(first.state, modal);
    assert.equal(projected.complete, true, String(projected.missing));
    assert.equal(projected.observation.catalog.length, 1);
    assert.ok(projected.excluded.some(value => value.rule === "forward_profile:abandon_confirmation_return_navigation"));
    const follow = resolveFollowBasis(lowering.follow[0], lowering, modal);
    assert.ok(follow);
    for (const field of ["event_entity_id", "room_entity_id", "parent_option_entity_id", "title", "body"]) {
      const changed = structuredClone(modal);
      changed.snapshot.interaction.content.surface[field] += "-changed";
      assert.equal(resolveFollowBasis(lowering.follow[0], lowering, changed), null, field);
    }
    const result = (await driver.handle({ command: "step", mutation_request_id: "trial-explicit-yes",
      expected_snapshot_id: modal.snapshot.snapshot_id, bound_action_id: follow.bound_action_id })).receipt;
    assert.equal(result.delivery, "delivered");
    const terminal = reduce(projected.state, await basis());
    assert.equal(terminal.complete, true, String(terminal.missing));
    assert.equal(terminal.observation.status, "terminal");
    assert.equal(terminal.observation.terminal.outcome, "loss");
  } finally { await driver.shutdown(); }
});
