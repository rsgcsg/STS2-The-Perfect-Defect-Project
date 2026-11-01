import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { resolveDotnet } from "../src/managed-candidate.mjs";

// Privileged setup creates a fresh native Trial through the component entry.
// Yes/No, stale owners, original abandonment/history and the real pending/failing
// Task are exercised through the production Host serializer. This is not a
// Reference, SDK/P1, installed-runtime or gameplay qualification.
test("Trial confirmation preserves native choices and the actual abandon Task", { timeout: 180000 }, async context => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) { context.skip("set STS2_MANAGED_TEST_CANDIDATE for exact native component fixtures"); return; }
  const dotnet = await resolveDotnet();
  const run = promisify(execFile);
  const env = { ...process.env, DOTNET_PROCESSOR_COUNT: "1" };
  const production = path.join(candidate, "src/Sts2Headless/bin/Release/net9.0");
  const fixture = path.join(candidate, "src/Sts2Headless/bin/U1Tests/net9.0");
  const hash = file => createHash("sha256").update(readFileSync(file)).digest("hex");
  const artifacts = ["Sts2Headless.dll", "GodotSharp.dll"];
  const hashes = directory => artifacts.map(name => hash(path.join(directory, name)));
  const before = hashes(production);
  await run(dotnet.command, ["build", path.join(candidate, "u1-native-tests/U1Native.csproj"), "-c", "Release",
    "-m:1", "-p:Deterministic=true", `-p:PathMap=${path.resolve(candidate)}=/_/candidate/`],
  { cwd: candidate, env, timeout: 90000, maxBuffer: 4 * 1024 * 1024 });
  assert.deepEqual(hashes(production), before, "fixture build retains the production artifact bytes");
  assert.deepEqual(hashes(fixture), before, "the fixture loads the exact production Host and Godot shim");
  const cases = { "trial-confirm": 26, "trial-pending": 27, "trial-failed": 24,
    "trial-stale": 7, "trial-left": 7, "trial-observe-pending": 10, "trial-observe-failed": 10 };
  for (const [name, count] of Object.entries(cases)) {
    const { stdout } = await run(dotnet.command, [path.join(fixture, "U1Native.dll"), name],
      { cwd: candidate, env, timeout: 15000, maxBuffer: 4 * 1024 * 1024 });
    assert.ok(stdout.includes(`${count} exact native U1 checks passed (${name}, baseline=False)`), stdout);
  }
  assert.deepEqual(hashes(production), before, "native component checks retain the production artifacts");
  assert.deepEqual(hashes(fixture), before, "native component checks retain the loaded fixture dependencies");
});
