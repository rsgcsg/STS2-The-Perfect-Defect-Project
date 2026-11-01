import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { resolveDotnet } from "../src/managed-candidate.mjs";

// Exact native producer + public serializer component checks. Privileged setup
// never replays a recorded unknown and does not qualify live Reference parity.
test("native Claws deterministic public preview preserves native reads and explicit conversion", async context => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) { context.skip("set STS2_MANAGED_TEST_CANDIDATE for exact native Claws fixtures"); return; }
  const dotnet = await resolveDotnet();
  const run = promisify(execFile);
  const artifacts = ["Sts2Headless.dll", "GodotSharp.dll"].map(name =>
    path.join(candidate, "src/Sts2Headless/bin/Release/net9.0", name));
  const hashes = () => artifacts.map(file => createHash("sha256").update(readFileSync(file)).digest("hex"));
  const before = hashes();
  await run(dotnet.command, ["build", path.join(candidate, "u1-native-tests/U1Native.csproj"), "-c", "Release",
    "--no-restore", "-m:1", "-p:Deterministic=true", `-p:PathMap=${path.resolve(candidate)}=/_/candidate/`],
  { cwd: candidate, timeout: 180000, maxBuffer: 4 * 1024 * 1024 });
  assert.deepEqual(hashes(), before, "fixture build retains admitted artifact bytes");
  for (const [name, count] of Object.entries({ "claws-zero": 13, "claws-one": 17, "claws-many": 19, "claws-unknown": 3 })) {
    const { stdout } = await run(dotnet.command,
      [path.join(candidate, "src/Sts2Headless/bin/U1Tests/net9.0/U1Native.dll"), name],
      { cwd: candidate, timeout: 30000, maxBuffer: 4 * 1024 * 1024 });
    assert.ok(stdout.includes(`${count} exact native U1 checks passed (${name}, baseline=False)`), stdout);
    if (name === "claws-unknown") continue;
    const prefix = "native-u1-claws-preview-state=";
    const state = JSON.parse(stdout.split(/\r?\n/u).find(line => line.startsWith(prefix)).slice(prefix.length));
    const facts = state.selector_public;
    assert.equal(facts.selector_request.transformation_preview, "deterministic_native_after");
    assert.equal(facts.selector_request.preview_information_complete, true);
    assert.ok(facts.preview_options.length > 0);
    for (const option of facts.preview_options) {
      assert.equal(option.display_context, "native_deterministic_transform_after");
      assert.equal(option.after_availability, "available");
      assert.equal(option.after.definition_id, "MAUL");
      assert.equal(option.after.entity_id, option.subject_entity_id);
    }
  }
  assert.deepEqual(hashes(), before, "native read/conversion checks retain candidate artifacts");
});
