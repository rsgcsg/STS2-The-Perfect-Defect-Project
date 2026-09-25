import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { resolvePlatformInstallation } from "../platform-installation.mjs";
import { collectionSetup } from "../collection-setup.mjs";

function fixture(t) {
  const base = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "platform-install-")));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  const apps = path.join(base, "steamapps");
  const game = path.join(apps, "common", "Slay the Spire 2");
  const mods = path.join(game, "mods");
  const workshop = path.join(apps, "workshop", "content", "2868840", "3806646116");
  fs.mkdirSync(mods, { recursive: true });
  fs.mkdirSync(workshop, { recursive: true });
  fs.writeFileSync(path.join(apps, "appmanifest_2868840.acf"),
    '"AppState" { "appid" "2868840" "installdir" "Slay the Spire 2" }');
  const metadata = path.join(apps, "workshop", "appworkshop_2868840.acf");
  fs.writeFileSync(metadata, '"AppWorkshop" { "WorkshopItemsInstalled"\n{\n "3806646116" { "size" "3" }\n}\n"WorkshopItemDetails" {} }');
  const manifest = { id: "STS2_PLATFORM", has_dll: true, has_pck: false };
  const put = dir => {
    fs.writeFileSync(path.join(dir, "STS2_PLATFORM.dll"), "dll");
    fs.writeFileSync(path.join(dir, "STS2_PLATFORM.json"), JSON.stringify(manifest));
  };
  const installation = { game_dir: game, mods_dir: mods };
  const identity = () => ({ sha256: "a".repeat(64), module_version_id: "mvid" });
  return { mods, workshop, metadata, put, installation, identity };
}

test("Workshop installed record and exact item select the release without writing to it", t => {
  const f = fixture(t);
  f.put(f.workshop);
  const before = fs.readdirSync(f.workshop);
  const result = resolvePlatformInstallation(f.installation, { identity: f.identity });
  assert.equal(result.kind, "workshop");
  assert.equal(result.item_id, "3806646116");
  assert.equal(result.directory, f.workshop);
  assert.deepEqual(fs.readdirSync(f.workshop), before);
});

test("manual installation remains supported without Workshop subscription", t => {
  const f = fixture(t);
  f.put(f.mods);
  assert.equal(resolvePlatformInstallation(f.installation, { identity: f.identity }).kind, "manual");
});

test("duplicate, incomplete, missing metadata and wrong manifest fail closed", t => {
  const f = fixture(t);
  f.put(f.workshop);
  f.put(f.mods);
  assert.throws(() => resolvePlatformInstallation(f.installation, { identity: f.identity }), /ambiguous_mod_installation/u);
  fs.rmSync(path.join(f.mods, "STS2_PLATFORM.dll"));
  assert.throws(() => resolvePlatformInstallation(f.installation, { identity: f.identity }), /incomplete_manual_mod_installation/u);
  fs.rmSync(path.join(f.mods, "STS2_PLATFORM.json"));
  fs.writeFileSync(path.join(f.workshop, "STS2_PLATFORM.json"), '{"id":"OTHER","has_dll":true,"has_pck":false}');
  assert.throws(() => resolvePlatformInstallation(f.installation, { identity: f.identity }), /unified_mod_manifest_mismatch/u);
  fs.rmSync(f.metadata);
  assert.throws(() => resolvePlatformInstallation(f.installation, { identity: f.identity }), /platform_mod_not_installed/u);
});

test("collection status reports Workshop bytes and legacy mutable state without modifying either", async t => {
  const f = fixture(t);
  f.put(f.workshop);
  fs.writeFileSync(path.join(f.workshop, "STS2_HUMAN_ANNOTATOR.runtime.json"), "{}");
  const state = path.join(path.dirname(path.dirname(f.installation.game_dir)), "user-state");
  const writable = path.join(state, "spireagent", "annotator");
  fs.mkdirSync(writable, { recursive: true });
  const root = path.join(state, "campaign-recordings");
  fs.writeFileSync(path.join(writable, "STS2_HUMAN_ANNOTATOR.conf"), JSON.stringify({
    recording_root: root, runtime_status_path: path.join(writable, "STS2_HUMAN_ANNOTATOR.runtime.json")
  }));
  const provenance = path.join(state, "provenance.json");
  fs.writeFileSync(provenance, JSON.stringify({
    schema: "sts2.platform/game-mod-build-provenance-1",
    artifact: f.identity(),
    source: { platform: {}, components: { live_ui: {} } }
  }));
  const executable = path.join(f.installation.game_dir, "SlayTheSpire2.exe");
  fs.writeFileSync(executable, "game");
  const result = await collectionSetup("status", { recordings_root: root, mod_provenance: provenance }, {
    installation: { ...f.installation, executable }, env: { LOCALAPPDATA: state },
    platform: "win32", listProcesses: () => [],
    resolvePlatform: installation => resolvePlatformInstallation(installation, { identity: f.identity })
  });
  assert.equal(result.status, "configured");
  assert.equal(result.installation_kind, "workshop");
  assert.equal(result.workshop_item_id, "3806646116");
  assert.equal(result.legacy_workshop_state, true);
  assert.equal(result.execution_available, null);
});
