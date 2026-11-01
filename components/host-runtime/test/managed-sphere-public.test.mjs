import assert from "node:assert/strict";
import { appendFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { createSession, reduce, modelView } from "@rsgcsg/sts2-connector-client/p1-global";
import { resolveDotnet } from "../src/managed-candidate.mjs";
import { startJsonLineProcess } from "../src/json-line-process.mjs";
import { ManagedPlayerEnvironmentSession } from "../src/managed-player-environment.mjs";
import { ManagedPeDriverSession } from "../src/managed-pe-driver-session.mjs";

// Initial EnterRoom only is privileged. The native production dispatcher,
// serializer, real pipe, public actions and P1 handle every subsequent input.
test("original Sphere completes through normal pipe and all r1.9 tool/cell choices", { timeout: 90000 }, async context => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) { context.skip("set STS2_MANAGED_TEST_CANDIDATE for the exact Sphere native component"); return; }
  const dotnet = await resolveDotnet();
  const native = await startJsonLineProcess({ command: dotnet.command,
    args: [path.join(candidate, "src/Sts2Headless/bin/U1Tests/net9.0/U1Native.dll"), "sphere-driver"],
    cwd: candidate, env: { ...process.env, DOTNET_PROCESSOR_COUNT: "1" } });
  const session = new ManagedPlayerEnvironmentSession({ process: native.process, character: "Defect",
    runtimeInstanceId: "sphere-wire", environmentFingerprint: "sphere-wire-component" });
  const driver = new ManagedPeDriverSession({ session, runtime: { adapterRuntimeInstanceId: "sphere-wire" } });
  let state = createSession("sphere-wire", "managed", "r1.9"), clicks = 0, expectedClicks = null;
  try {
    await driver.handle({ command: "reset", seed: "U1SPHEREWIRE20261101A" });
    for (let step = 0; step < 12; step++) {
      const basis = (await driver.handle({ command: "capture" })).full.basis;
      const result = reduce(state, basis); state = result.state;
      assert.equal(result.complete, true, String(result.missing));
      const observation = result.observation, surface = observation.scene.content.surface;
      if (process.env.STS2_U1_OBSERVATIONS_PATH)
        appendFileSync(process.env.STS2_U1_OBSERVATIONS_PATH, JSON.stringify(modelView(observation)) + "\n");
      if (observation.scene.native_kind === "map_navigation") {
        assert.equal(clicks, expectedClicks); assert.ok(clicks === 3 || clicks === 6); return;
      }
      let choice;
      if (observation.scene.native_kind === "crystal_sphere") {
        expectedClicks ??= surface.remaining_divinations;
        assert.equal(observation.catalog.length, 242);
        assert.equal(surface.remaining_divinations, expectedClicks - clicks);
        const cell = surface.cells.find(value => value.x === 0 && value.y === 0);
        const tool = surface.tools.find(value => value.tool === "Small");
        assert.equal(cell.cleared, true);
        choice = observation.catalog.find(value => value.subject === cell.entity_id && value.target === tool.entity_id);
        clicks++;
      } else {
        assert.equal(observation.scene.native_kind, "event_option");
        assert.equal(reduce(createSession("old", "managed", "r1.8"), basis).complete, false);
        choice = observation.catalog[0];
      }
      assert.ok(choice);
      const binding = result.lowering[choice.choice_id].first;
      const receipt = (await driver.handle({ command: "step", mutation_request_id: `sphere-wire-${step}`,
        expected_snapshot_id: basis.snapshot.snapshot_id, bound_action_id: binding.bound_action_id })).receipt;
      assert.equal(receipt.delivery, "delivered");
    }
    assert.fail("the native Sphere did not return to its map boundary");
  } finally { await driver.shutdown(); }
});
