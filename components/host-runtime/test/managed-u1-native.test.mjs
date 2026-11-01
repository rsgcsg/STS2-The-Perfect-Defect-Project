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
import { captureManagedPublicBasis } from "../src/managed-public-basis.mjs";

// Privileged component setup uses original game producers and the production
// Host serializer. Only process transport is substituted below, with the exact
// serialized output. This does not qualify normal gameplay or Reference parity.
test("U1 native child inputs and terminal tasks cross SDK, frozen P1 and Input-v2", async context => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) { context.skip("set STS2_MANAGED_TEST_CANDIDATE for exact native component fixtures"); return; }
  const dotnet = await resolveDotnet();
  const run = promisify(execFile);
  const artifacts = ["Sts2Headless.dll", "GodotSharp.dll"].map(name =>
    path.join(candidate, "src/Sts2Headless/bin/Release/net9.0", name));
  const hashes = () => artifacts.map(file => createHash("sha256").update(readFileSync(file)).digest("hex"));
  const before = hashes();
  await run(dotnet.command, ["build", path.join(candidate, "u1-native-tests/U1Native.csproj"), "-c", "Release",
    "-p:Deterministic=true", `-p:PathMap=${path.resolve(candidate)}=/_/candidate/`],
  { cwd: candidate, timeout: 180000, maxBuffer: 4 * 1024 * 1024 });
  assert.deepEqual(hashes(), before, "fixture build retains the admitted artifact bytes");
  const observations = [];
  const cases = { bundle: 9, "rest-mailbox": 5, "rest-dream": 5, simple: 6,
    "kaiser-left": 6, "kaiser-right": 6, "terminal-sync": 6, "terminal-pending": 6,
    "terminal-committing": 6, "terminal-win": 6, "terminal-failed": 4, "terminal-unwitnessed": 2 };
  for (const [name, count] of Object.entries(cases)) {
    const { stdout } = await run(dotnet.command,
      [path.join(candidate, "src/Sts2Headless/bin/U1Tests/net9.0/U1Native.dll"), name],
      { cwd: candidate, timeout: 30000, maxBuffer: 4 * 1024 * 1024 });
    assert.ok(stdout.includes(`${count} exact native U1 checks passed (${name}, baseline=False)`), stdout);
    for (const line of stdout.split(/\r?\n/u).filter(value => value.startsWith("native-u1-"))) {
      const state = JSON.parse(line.slice(line.indexOf("=") + 1));
      const session = new ManagedPlayerEnvironmentSession({ process: { request: async () => state },
        runtimeInstanceId: `u1-${name}`, environmentFingerprint: "u1-native-component" });
      await session.mount({ seed: "U1_NEW_COMPONENT" });
      const capture = captureManagedPublicBasis(session, { ordinal: 1 });
      const derived = reduce(createSession(`u1-${name}`, "managed", "r1.5"),
        JSON.parse(capture.serializedObservation));
      if (name === "terminal-unwitnessed") {
        assert.equal(capture.basis.capture_status, "partial");
        assert.equal(derived.observation.terminal.outcome, "unknown");
        assert.equal(derived.observation.catalog.length, 0);
        assert.equal(capture.basis.snapshot.bound_actions.actions.length, 0);
        continue;
      }
      assert.equal(capture.basis.capture_status, "complete", name);
      assert.equal(derived.complete, true, `${name}: ${derived.missing}`);
      if (name.startsWith("terminal-")) {
        assert.equal(derived.observation.terminal.outcome, name === "terminal-win" ? "win" : "loss");
        assert.equal(derived.observation.catalog.length, 0);
      } else assert.ok(derived.observation.catalog.length > 0, name);
      observations.push(derived.observation);
    }
  }
  const consumer = spawnSync(projectPython(), ["-c", `
import json, sys, torch
from stpd.p1.input import project, V2_INPUT_SPEC
from stpd.p1.model import MStd
from stpd.p1.vocabulary import public_vocabulary
torch.set_num_threads(2)
model = MStd(public_vocabulary()).eval()
rows = json.load(sys.stdin)
with torch.inference_mode():
    for observation in rows:
        frame = project(observation, input_projection=V2_INPUT_SPEC)
        assert len(frame.choices) == len(observation['catalog'])
        if frame.choices:
            scores = model(frame)
            assert len(scores) == len(frame.choices) and torch.isfinite(scores).all()
assert len(rows) == 12
print('12 native serialized observations consumed by frozen Input-v2')
`], { encoding: "utf8", input: JSON.stringify(observations), timeout: 60000,
    cwd: path.resolve(import.meta.dirname, "../../.."), maxBuffer: 4 * 1024 * 1024 });
  assert.equal(consumer.status, 0, consumer.error?.message ?? consumer.stderr);
  assert.match(consumer.stdout, /12 native serialized observations consumed by frozen Input-v2/u);
  assert.deepEqual(hashes(), before, "native and model consumers retain candidate artifacts");
});
