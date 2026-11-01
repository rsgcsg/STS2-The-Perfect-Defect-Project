import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFile, spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { projectPython } from "../../../tools/project-python.mjs";
import { createSession, reduce } from "@rsgcsg/sts2-connector-client/p1-global";
import { resolveDotnet } from "../src/managed-candidate.mjs";
import { ManagedPlayerEnvironmentSession } from "../src/managed-player-environment.mjs";
import { ManagedPeDriverSession } from "../src/managed-pe-driver-session.mjs";

// Fresh privileged Knight/Mittens setup only. Original hand producer and native
// observe_decision dispatch are real. The session below substitutes transport
// with those exact serialized observations; no Reference/runtime qualification.
test("late native hand input survives fallback and normal settling capture", { timeout: 180000 }, async context => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) { context.skip("set STS2_MANAGED_TEST_CANDIDATE for exact native component fixtures"); return; }
  const dotnet = await resolveDotnet();
  const run = promisify(execFile);
  const production = path.join(candidate, "src/Sts2Headless/bin/Release/net9.0");
  const fixture = path.join(candidate, "src/Sts2Headless/bin/U1Tests/net9.0");
  const env = { ...process.env, DOTNET_PROCESSOR_COUNT: "1" };
  const hashes = directory => ["Sts2Headless.dll", "GodotSharp.dll"].map(name =>
    createHash("sha256").update(readFileSync(path.join(directory, name))).digest("hex"));
  const before = hashes(production);
  await run(dotnet.command, ["build", path.join(candidate, "u1-native-tests/U1Native.csproj"), "-c", "Release", "-m:1",
    "-p:Deterministic=true", `-p:PathMap=${path.resolve(candidate)}=/_/candidate/`],
  { cwd: candidate, env, timeout: 90000, maxBuffer: 4 * 1024 * 1024 });
  assert.deepEqual(hashes(production), before);
  assert.deepEqual(hashes(fixture), before);
  for (const [name, count] of [["knight-late", 28], ["knight-read", 29]]) {
    const { stdout } = await run(dotnet.command, [path.join(fixture, "U1Native.dll"), name],
      { cwd: candidate, env, timeout: 30000, maxBuffer: 4 * 1024 * 1024 });
    assert.ok(stdout.includes(`${count} exact native U1 checks passed (${name}, baseline=False)`));
    if (name !== "knight-read") continue;
    const emitted = key => JSON.parse(stdout.split(/\r?\n/u).find(line => line.startsWith(`native-u1-${key}=`)).split(/=(.*)/su)[1]);
    const pending = emitted("knight-read-before"), child = emitted("knight-read-after");
    const calls = [];
    let native = pending;
    const session = new ManagedPlayerEnvironmentSession({
      process: { request: async request => { calls.push(request); return structuredClone(native); } },
      runtimeInstanceId: "u1-knight-native", environmentFingerprint: "u1-knight-component" });
    const driver = new ManagedPeDriverSession({ session, runtime: { adapterRuntimeInstanceId: "u1-knight-native" } });
    const first = await driver.handle({ command: "reset", seed: "U1KNIGHTNATIVECOMPONENT" });
    assert.equal(first.snapshot.status, "settling");
    const old = (await driver.handle({ command: "capture" })).full.basis.snapshot;
    assert.equal(old.snapshot_id, first.snapshot.snapshot_id);
    native = child;
    const capture = (await driver.handle({ command: "capture" })).full;
    assert.notEqual(capture.basis.snapshot.snapshot_id, old.snapshot_id);
    assert.equal(capture.basis.capture_status, "complete");
    const derived = reduce(createSession("u1-knight", "managed", "r1.5"), capture.basis);
    assert.equal(derived.complete, true, String(derived.missing));
    assert.ok(derived.observation.catalog.length > 0);
    assert.deepEqual(calls.map(request => request.cmd), ["start_run", "observe_decision", "observe_decision"]);
    const consumer = spawnSync(projectPython(), ["-c", `
import json, sys, torch
from stpd.p1.input import project, V2_INPUT_SPEC
from stpd.p1.model import MStd
from stpd.p1.vocabulary import public_vocabulary
torch.set_num_threads(1)
observation = json.load(sys.stdin)
frame = project(observation, input_projection=V2_INPUT_SPEC)
assert len(frame.choices) == len(observation['catalog'])
with torch.inference_mode():
    scores = MStd(public_vocabulary()).eval()(frame)
assert len(scores) == len(frame.choices) and torch.isfinite(scores).all()
`], { encoding: "utf8", input: JSON.stringify(derived.observation), timeout: 60000,
      cwd: path.resolve(import.meta.dirname, "../../.."), maxBuffer: 4 * 1024 * 1024 });
    assert.equal(consumer.status, 0, consumer.error?.message ?? consumer.stderr);
  }
  assert.deepEqual(hashes(production), before);
  assert.deepEqual(hashes(fixture), before);
});
