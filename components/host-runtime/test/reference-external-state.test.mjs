import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { referenceDriverOptions, resolveReferenceStateRoot } from "../tools/reference-pe-driver.mjs";
import { ShippedPlayerEnvironmentSession } from "../src/shipped-player-environment.mjs";
import { isolatedProfileLaunch } from "../src/profile-isolation.mjs";
import { captureProfileTemplate, instantiateProfileTemplate, profileTemplatePaths } from "../src/profile-template.mjs";

const source = fileURLToPath(new URL("..", import.meta.url));
const directoryLink = process.platform === "win32" ? "junction" : "dir";
function temporary(context) {
  const root = realpathSync(mkdtempSync(path.join(os.tmpdir(), "reference-external-state-")));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  const packageRoot = path.join(root, "package");
  const stateRoot = path.join(root, "state");
  mkdirSync(packageRoot); mkdirSync(stateRoot);
  writeFileSync(path.join(packageRoot, "package.json"), JSON.stringify({ name: "@rsgcsg/sts2-host-runtime", version: "1.1.0-rc.28" }));
  return { root, packageRoot, stateRoot };
}
function digest(root) {
  const hash = createHash("sha256");
  const visit = (directory) => {
    for (const item of readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const name = path.join(directory, item.name);
      if (item.isDirectory()) visit(name);
      else hash.update(path.relative(root, name)).update("\0").update(readFileSync(name)).update("\0");
    }
  };
  visit(root); return hash.digest("hex");
}
const game = { platform: "win32", architecture: "x64",
  release: { version: "fixture-game", commit: "fixture-commit", main_assembly_hash: 123 },
  executable: { sha256: "a".repeat(64) }, runtime_main_assembly_hash: 123,
  sts2_assembly: { sha256: "e".repeat(64) }, godotsharp_assembly: { sha256: "f".repeat(64) } };
function stageTemplate(root) {
  const original = isolatedProfileLaunch(root, "template-source", "win32");
  const settings = path.join(original.expected_user_data_root, "default/1/settings.save");
  mkdirSync(path.dirname(settings), { recursive: true });
  writeFileSync(settings, JSON.stringify({ schema_version: 8, synthetic: true }));
  return captureProfileTemplate({ localRoot: root, profileId: "template-source", templateId: "defect-a0-s0", gameIdentity: game });
}

test("ordinary source checkout retains its declared no-argument local root", () => {
  const state = resolveReferenceStateRoot([]);
  assert.equal(state.localRoot, path.join(source, ".local"));
  assert.equal(state.stateRootReadback.mode, "legacy_source_checkout");
});

test("installed CLI roots reject package overlap, relative paths and symlinked state before a factory", context => {
  const { root, packageRoot, stateRoot } = temporary(context);
  assert.throws(() => resolveReferenceStateRoot([], { packageRoot }), /external --local-root/u);
  for (const supplied of ["relative", packageRoot, root, path.join(packageRoot, ".local")]) {
    if (path.isAbsolute(supplied) && supplied !== root) mkdirSync(supplied, { recursive: true });
    assert.throws(() => resolveReferenceStateRoot(["--local-root", supplied], { packageRoot }));
  }
  const link = path.join(root, "alias"); symlinkSync(stateRoot, link, directoryLink);
  assert.throws(() => resolveReferenceStateRoot(["--local-root", link], { packageRoot }), /canonical/u);
  symlinkSync(packageRoot, path.join(stateRoot, "evidence"), directoryLink);
  assert.throws(() => resolveReferenceStateRoot(["--local-root", stateRoot], { packageRoot }), /canonical/u);
  assert.throws(() => resolveReferenceStateRoot(["--local-root", stateRoot, "--local-root", stateRoot], { packageRoot }), /once/u);
});

test("actual reference options and factory instantiate the real external template twice without changing package bytes", async context => {
  const { root, packageRoot, stateRoot } = temporary(context);
  const captured = stageTemplate(stateRoot);
  const before = digest(packageRoot);
  const configuration = referenceDriverOptions(["--game-dir", path.join(root, "synthetic-game"),
    "--local-root", stateRoot, "--template", "defect-a0-s0"], { packageRoot });
  assert.equal(configuration.stateRootReadback.local_root, stateRoot);
  assert.equal(configuration.sessionOptions.evidenceRoot, path.join(stateRoot, "evidence"));
  const generations = [];
  for (const suffix of ["first", "second"]) {
    const session = new ShippedPlayerEnvironmentSession(configuration.sessionOptions, {
      startEpisode: async options => {
        assert.equal(options.localRoot, stateRoot);
        assert.equal(options.evidenceRoot, configuration.stateRootReadback.evidence_root);
        const profile = instantiateProfileTemplate({ localRoot: options.localRoot,
          templateId: options.templateId, profileId: `fixture-${suffix}`, expectedGameIdentity: game });
        generations.push(profile.generation_id);
        mkdirSync(options.evidenceRoot, { recursive: true });
        writeFileSync(path.join(options.evidenceRoot, `${suffix}.json`), JSON.stringify(profile));
        assert.equal(profile.template_payload_sha256, captured.payload_sha256);
        assert.equal(readFileSync(path.join(profile.expected_user_data_root, "default/1/settings.save"), "utf8"),
          readFileSync(path.join(profileTemplatePaths(stateRoot, "defect-a0-s0").user_data, "default/1/settings.save"), "utf8"));
        return { snapshot: { snapshot_id: suffix }, identity: { profile }, close: async () => ({ code: 0, forced: false }) };
      }
    });
    await session.reset(suffix); await session.close();
    assert.equal(digest(packageRoot), before);
  }
  assert.notEqual(generations[0], generations[1]);
});

test("real installed reference CLI reports external roots before reset and rejects an omitted root", context => {
  const { root, stateRoot } = temporary(context);
  const scope = path.join(root, "node_modules/@rsgcsg");
  const packageRoot = path.join(scope, "sts2-host-runtime");
  mkdirSync(packageRoot, { recursive: true });
  cpSync(path.join(source, "src"), path.join(packageRoot, "src"), { recursive: true });
  mkdirSync(path.join(packageRoot, "tools"));
  cpSync(path.join(source, "tools/reference-pe-driver.mjs"), path.join(packageRoot, "tools/reference-pe-driver.mjs"));
  cpSync(path.join(source, "package.json"), path.join(packageRoot, "package.json"));
  const sdk = path.resolve(fileURLToPath(import.meta.resolve("@rsgcsg/sts2-connector-client")), "../..");
  symlinkSync(sdk, path.join(scope, "sts2-connector-client"), directoryLink);
  stageTemplate(stateRoot);
  const before = digest(packageRoot);
  const cli = path.join(packageRoot, "tools/reference-pe-driver.mjs");
  const args = [cli, "--game-dir", path.join(root, "synthetic-game"), "--local-root", stateRoot, "--template", "defect-a0-s0"];
  const result = spawnSync(process.execPath, args, { encoding: "utf8", timeout: 10000,
    input: `${JSON.stringify({ command: "close", request_id: "no-native-reset" })}\n` });
  assert.equal(result.status, 0, result.stderr);
  const ready = JSON.parse(result.stdout.split("\n")[0]);
  assert.equal(ready.headless.distribution_kind, "installed_package");
  assert.deepEqual(ready.state_root, { schema: "sts2.host-runtime/reference-state-root-1",
    mode: "external", local_root: stateRoot, evidence_root: path.join(stateRoot, "evidence") });
  const old = spawnSync(process.execPath, [cli, "--game-dir", path.join(root, "synthetic-game")], { encoding: "utf8", timeout: 10000, input: "" });
  assert.notEqual(old.status, 0); assert.match(old.stderr, /external --local-root/u);
  assert.equal(digest(packageRoot), before);
});
