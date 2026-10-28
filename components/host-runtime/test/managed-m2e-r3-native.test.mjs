import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { resolveDotnet } from "../src/managed-candidate.mjs";
import { projectManagedCandidateDecision } from "../src/managed-player-environment.mjs";

test("native visible grid, bundle publication, event rest and death stay bound across serialization", async context => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) { context.skip("set STS2_MANAGED_TEST_CANDIDATE for the exact native component fixture"); return; }
  const dotnet = await resolveDotnet();
  const artifacts = ["Sts2Headless.dll", "GodotSharp.dll"].map(name => path.join(candidate, "src/Sts2Headless/bin/Release/net9.0", name));
  const hashes = () => artifacts.map(file => createHash("sha256").update(readFileSync(file)).digest("hex"));
  const before = hashes();
  const { stdout } = await promisify(execFile)(dotnet.command,
    ["run", "--project", path.join(candidate, "m2e-r3-native-tests/M2eR3Native.csproj"), "-c", "Release",
      "-p:Deterministic=true", `-p:PathMap=${path.resolve(candidate)}=/_/candidate/`],
    { cwd: candidate, timeout: 180000, maxBuffer: 4 * 1024 * 1024 });
  assert.deepEqual(hashes(), before, "component regression retains the frozen candidate artifacts");
  assert.match(stdout, /46 exact native M2e R3 checks passed/u);
  const prefix = "native-m2e-r3-dense-state=";
  const state = JSON.parse(stdout.split(/\r?\n/u).find(line => line.startsWith(prefix)).slice(prefix.length));
  const projection = projectManagedCandidateDecision({ state, sequence: 1,
    runtimeInstanceId: "m2e-r3-component", environmentFingerprint: "m2e-r3-component" });
  assert.equal(projection.snapshot.completeness.status, "complete");
  assert.equal(projection.snapshot.interaction.kind, "event_option");
  assert.equal(projection.snapshot.interaction.content.surface.options.length, 1);
  assert.equal(projection.snapshot.bound_actions.actions.length, 1);
  const trial = JSON.parse(stdout.split(/\r?\n/u).find(line => line.startsWith("native-m2e-r3-trial-state=")).slice("native-m2e-r3-trial-state=".length));
  const trialProjection = projectManagedCandidateDecision({ state: trial, sequence: 2,
    runtimeInstanceId: "m2e-r3-trial", environmentFingerprint: "m2e-r3-trial" });
  assert.equal(trialProjection.snapshot.completeness.status, "complete");
  assert.equal(trialProjection.snapshot.bound_actions.actions.length, 2);
  const modal = JSON.parse(stdout.split(/\r?\n/u).find(line => line.startsWith("native-m2e-r3-modal-state=")).slice("native-m2e-r3-modal-state=".length));
  const modalProjection = projectManagedCandidateDecision({ state: modal, sequence: 3,
    runtimeInstanceId: "m2e-r3-modal", environmentFingerprint: "m2e-r3-modal" });
  assert.notEqual(modalProjection.snapshot.completeness.status, "complete");
  assert.equal(modalProjection.snapshot.bound_actions.actions.length, 0);
  assert.match(JSON.stringify(modalProjection.snapshot.completeness), /native_trial_abandon_confirmation_modal_unavailable/u);

});
