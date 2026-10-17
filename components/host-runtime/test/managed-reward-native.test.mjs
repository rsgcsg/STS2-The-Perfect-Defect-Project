import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { resolveDotnet } from "../src/managed-candidate.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

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
    "NativeUpgradeMethodInventory.cs", "NativePublicCardDetails.cs", "NativeVisibleText.cs"])
    assert.equal(files.get(`src/Sts2Headless/${name}`),
      readFileSync(path.join(root, "../native-foundation/src", name), "utf8"));
});

test("exact native reward factory preserves Skip/reroll/sacrifice without selection effects", async context => {
  const candidate = process.env.STS2_MANAGED_TEST_CANDIDATE;
  if (!candidate) { context.skip("set STS2_MANAGED_TEST_CANDIDATE for exact proprietary native component fixtures"); return; }
  const dotnet = await resolveDotnet();
  const { stdout } = await promisify(execFile)(dotnet.command,
    ["run", "--project", path.join(candidate, "native-tests/NativeMechanisms.csproj"), "--configuration", "Release"],
    { cwd: candidate, timeout: 180_000, maxBuffer: 4 * 1024 * 1024 });
  assert.match(stdout, /14 exact native reward factory checks passed/u);
  assert.match(stdout, /23 exact native relic command checks passed/u);
  assert.match(stdout, /7 exact native free travel checks passed/u);
  assert.match(stdout, /1 exact native wire null serialization check passed/u);
  assert.match(stdout, /15 exact native card parent checks passed/u);
  const paired = stdout.split(/\r?\n/u).find(line => line.startsWith("native-reward-card-pair="));
  assert.ok(paired, "original native outer and opened callback card renderings must be retained");
  const { outer, opened } = JSON.parse(paired.slice("native-reward-card-pair=".length));
  assert.deepEqual(opened, outer);
  assert.equal(outer.render_complete, true);
  assert.equal(outer.description, "Deal 6 damage.");
  assert.equal(outer.target_type, "AnyEnemy");
  const emitted = stdout.split(/\r?\n/u).find(line => line.startsWith("native-reward-json="));
  assert.ok(emitted);
  const facts = JSON.parse(emitted.slice("native-reward-json=".length));
  assert.equal(facts.Complete, true);
  assert.deepEqual(facts.Alternatives.map(option => option.IsNativeSkip), [true,false]);
  assert.deepEqual(facts.Alternatives.map(option => [option.OptionId, option.AfterSelected]), [
    ["Skip", "end_selection_and_do_not_complete_reward"], ["SACRIFICE", "end_selection_and_complete_reward"]
  ]);
});
