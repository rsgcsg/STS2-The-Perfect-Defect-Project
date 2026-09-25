import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { collectionSetup, nativeRecordingRoot } from "../collection-setup.mjs";
import { evaluateLoadedEvidence } from "../loaded-evidence.mjs";

function fixture(t) {
  // realpath avoids macOS's /var -> /private/var operating-system symlink.
  const base = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "collection-setup-")));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  const game = path.join(base, "game");
  const mods = path.join(game, "mods");
  fs.mkdirSync(mods, { recursive: true });
  const json = (file, value) => fs.writeFileSync(file, JSON.stringify(value));
  const root = path.join(base, "campaign");
  const original = path.join(base, "original");
  fs.mkdirSync(original);
  fs.writeFileSync(path.join(original, "retained"), "history");
  const configPath = path.join(mods, "STS2_HUMAN_ANNOTATOR.conf");
  const statusPath = path.join(base, "runtime-status.json");
  const configuration = { recording_root: original, runtime_status_path: statusPath, operator_option: "keep" };
  json(configPath, configuration);
  fs.writeFileSync(path.join(game, "SlayTheSpire2"), "game");
  fs.writeFileSync(path.join(mods, "STS2_PLATFORM.dll"), "mod");
  json(path.join(mods, "STS2_PLATFORM.json"), { id: "STS2_PLATFORM", has_dll: true, has_pck: false });
  json(path.join(mods, "STS2_MCP.conf"), { port: 15526 });
  const artifact = { sha256: crypto.createHash("sha256").update("mod").digest("hex"), module_version_id: "mvid" };
  const provenance = { schema: "sts2.platform/game-mod-build-provenance-1", artifact,
    source: { platform: { source_revision: "platform", source_digest_sha256: "platform-digest" }, components: {
      connector: { source_revision: "connector" }, annotator: { source_revision: "annotator" },
      live_ui: { source_revision: "ui", source_digest_sha256: "ui-digest" }
    } } };
  const provenancePath = path.join(base, "build-provenance.json");
  json(provenancePath, provenance);
  const platformIdentity = { loaded_at: "2026-09-15T00:00:00Z", artifact_sha256: artifact.sha256,
    module_version_id: "mvid", platform_source_revision: "platform", platform_source_digest_sha256: "platform-digest",
    connector_source_revision: "connector", annotator_source_revision: "annotator", live_ui_source_revision: "ui" };
  const uiIdentity = { artifact_sha256: artifact.sha256, module_version_id: "mvid", source_revision: "ui", source_digest_sha256: "ui-digest" };
  const log = path.join(base, "godot.log");
  fs.writeFileSync(log, `[STS2 Platform] identity ${JSON.stringify(platformIdentity)}\n[STS2 Platform Live UI] identity ${JSON.stringify(uiIdentity)}\n[STS2 Platform Live UI] panel ready; input=launcher\n`);
  const status = { schema: "sts2.human-annotator/runtime-status-2", status: "ready", process_id: 100, observed_at: "2026-09-15T00:00:01Z", session_id: "none", recording_directory: root };
  json(statusPath, status);
  const capabilities = { execution_available: false, host: { implementation: { artifact_sha256: artifact.sha256,
    module_version_id: "mvid", source_revision: "connector" } }, game: {
    compatibility: { status: "unsupported", observation_allowed: true },
    modset: { status: "exact_platform_modset", loaded_mod_ids: ["STS2_PLATFORM"] }
  } };
  const installation = { game_dir: game, mods_dir: mods, executable: path.join(game, "SlayTheSpire2"), log_file: log };
  const options = { recordings_root: root, mod_provenance: provenancePath };
  const dependencies = { installation, env: {}, platform: "linux", listProcesses: () => [],
    readProcessStartedAt: () => "2026-09-14T23:59:59Z", fetchCapabilities: async () => capabilities,
    resolvePlatform: () => ({ kind: "manual", item_id: null, directory: mods,
      artifact: { sha256: crypto.createHash("sha256").update(fs.readFileSync(path.join(mods, "STS2_PLATFORM.dll"))).digest("hex"),
        module_version_id: "mvid" } }) };
  return { base, options, dependencies, root, original, configPath, configuration, statusPath, status, json,
    provenance, platformIdentity, uiIdentity, capabilities };
}

test("bind preserves old root and unrelated config, archives exact bytes, and requires fresh load", async t => {
  const f = fixture(t);
  const before = fs.readFileSync(f.configPath);
  const result = await collectionSetup("bind", f.options, f.dependencies);
  assert.equal(result.status, "configured");
  assert.equal(result.bound, false);
  assert.equal(result.next_action, "launch_game");
  assert.deepEqual(fs.readFileSync(result.backup_path), before);
  assert.deepEqual(JSON.parse(fs.readFileSync(f.configPath)), { ...f.configuration, recording_root: f.root });
  assert.equal(fs.readFileSync(path.join(f.original, "retained"), "utf8"), "history");
  assert.equal((await collectionSetup("bind", f.options, f.dependencies)).backup_path, undefined);
});

test("bind refuses active process, environment override, unknown discovery and late game launch", async t => {
  const f = fixture(t);
  const before = fs.readFileSync(f.configPath);
  const variants = [
    { listProcesses: () => ["100 SlayTheSpire2"] },
    { env: { STS2_HUMAN_ANNOTATOR_RECORDING_ROOT: f.root } },
    { env: { STS2_HUMAN_ANNOTATOR_STATUS_PATH: f.statusPath } },
    { listProcesses: () => { throw new Error("enumeration failed"); } }
  ];
  for (const variant of variants) {
    assert.equal((await collectionSetup("bind", f.options, { ...f.dependencies, ...variant })).status, "blocked");
    assert.deepEqual(fs.readFileSync(f.configPath), before);
  }
  let calls = 0;
  assert.equal((await collectionSetup("bind", f.options, { ...f.dependencies, listProcesses: () => ++calls === 1 ? [] : ["100 SlayTheSpire2"] })).reason, "game_must_be_stopped");
  assert.deepEqual(fs.readFileSync(f.configPath), before);
});

test("failed atomic replacement leaves exact old configuration and evidence", async t => {
  const f = fixture(t);
  const before = fs.readFileSync(f.configPath);
  const result = await collectionSetup("bind", f.options, { ...f.dependencies, replaceConfiguration: () => { throw new Error("disk error"); } });
  assert.equal(result.status, "blocked");
  assert.deepEqual(fs.readFileSync(f.configPath), before);
  assert.equal(fs.readFileSync(path.join(f.original, "retained"), "utf8"), "history");
});

test("session roots, symlink paths, ambiguous install and wrong artifact fail closed", async t => {
  const f = fixture(t);
  fs.mkdirSync(f.root);
  f.json(path.join(f.root, "recording-manifest.json"), {});
  assert.equal((await collectionSetup("bind", f.options, f.dependencies)).reason, "recording_session_is_not_root");
  fs.rmSync(path.join(f.root, "recording-manifest.json"));
  const link = path.join(f.base, "link");
  try { fs.symlinkSync(f.root, link, "dir"); }
  catch (error) { if (error.code !== "EPERM") throw error; }
  if (fs.existsSync(link)) assert.equal((await collectionSetup("bind", { ...f.options, recordings_root: link }, f.dependencies)).reason, "symlink_path_refused");
  const predecessor = path.join(f.dependencies.installation.mods_dir, "STS2_HUMAN_ANNOTATOR.json");
  f.json(predecessor, {});
  assert.equal((await collectionSetup("bind", f.options, f.dependencies)).reason, "ambiguous_mod_installation");
  fs.rmSync(predecessor);
  fs.writeFileSync(path.join(f.dependencies.installation.mods_dir, "STS2_PLATFORM.dll"), "wrong");
  assert.equal((await collectionSetup("bind", f.options, f.dependencies)).reason, "installed_artifact_mismatch");
});

test("passive exact identity binds current native root without granting mutation admission", async t => {
  const f = fixture(t);
  const result = await collectionSetup("status", f.options, { ...f.dependencies, listProcesses: () => ["100 SlayTheSpire2"] });
  assert.equal(result.bound, true);
  assert.equal(result.connected, true);
  assert.equal(result.configured, false); // Actual loaded config can differ from later disk edits.
  assert.equal(result.actual_recordings_root, f.root);
  assert.equal(result.execution_available, false);
  assert.equal(result.compatibility.status, "unsupported");
  const mutation = evaluateLoadedEvidence({ status: f.status, capabilities: f.capabilities,
    platformIdentity: f.platformIdentity, liveUiIdentity: f.uiIdentity, installed: f.provenance,
    uiPanelReady: true, gameProcessIds: ["100"] });
  assert.equal(mutation.ready, false);
  assert.ok(mutation.errors.includes("connector_execution_not_available"));
});

test("stopped or stale process, stale generation and wrong native root never bind", async t => {
  const f = fixture(t);
  assert.equal((await collectionSetup("status", f.options, f.dependencies)).bound, false);
  assert.equal((await collectionSetup("status", f.options, { ...f.dependencies, listProcesses: () => ["101 SlayTheSpire2"] })).actual_recordings_root, null);
  const running = { ...f.dependencies, listProcesses: () => ["100 SlayTheSpire2"] };
  assert.equal((await collectionSetup("status", f.options, { ...running, readProcessStartedAt: () => "2026-09-15T00:00:02Z" })).bound, false);
  f.json(f.statusPath, { ...f.status, observed_at: "2026-09-14T00:00:00Z" });
  assert.equal((await collectionSetup("status", f.options, running)).bound, false);
  f.json(f.statusPath, { ...f.status, recording_directory: f.original });
  const mismatch = await collectionSetup("status", f.options, running);
  assert.equal(mismatch.connected, true);
  assert.equal(mismatch.bound, false);
  assert.equal(mismatch.actual_recordings_root, f.original);
  assert.equal(mismatch.next_action, "close_game");
});

test("session destination uses only exact current native session ID", () => {
  const root = path.resolve("recordings");
  assert.equal(nativeRecordingRoot({ session_id: "session-1", recording_directory: path.join(root, "session-1") }), root);
  assert.equal(nativeRecordingRoot({ session_id: "session-2", recording_directory: path.join(root, "session-1") }), null);
  assert.equal(nativeRecordingRoot({ session_id: "none", recording_directory: "relative" }), null);
});

test("successful Close reports the native root while retaining the closed session identity", async t => {
  const f = fixture(t);
  const closed = { ...f.status, status: "recording_closed", session_id: "session-closed" };
  f.json(f.statusPath, closed);
  const running = { ...f.dependencies, listProcesses: () => ["100 SlayTheSpire2"] };
  const result = await collectionSetup("status", f.options, running);
  assert.equal(result.status, "bound");
  assert.equal(result.connected, true);
  assert.equal(result.bound, true);
  assert.equal(result.actual_recordings_root, f.root);
  assert.equal(result.execution_available, false);

  assert.equal((await collectionSetup("status", f.options, {
    ...running, listProcesses: () => ["101 SlayTheSpire2"]
  })).bound, false);
  assert.equal((await collectionSetup("status", f.options, {
    ...running, readProcessStartedAt: () => "2026-09-15T00:00:02Z"
  })).bound, false);
  f.json(f.statusPath, { ...closed, recording_directory: f.original });
  const mismatch = await collectionSetup("status", f.options, running);
  assert.equal(mismatch.connected, true);
  assert.equal(mismatch.bound, false);
  assert.equal(mismatch.actual_recordings_root, f.original);
  assert.equal(mismatch.reason, "current_runtime_root_mismatch");
});

test("only completed Close decodes a direct root with a valid retained session identity", () => {
  const root = path.resolve("recordings");
  const closed = { status: "recording_closed", session_id: "session-closed", recording_directory: root };
  assert.equal(nativeRecordingRoot(closed), root);
  for (const status of ["recording", "recording_closing", "close_durable_flush_failed", "unknown"])
    assert.equal(nativeRecordingRoot({ ...closed, status }), null);
  for (const session_id of [undefined, null, 1, "", "none", ".", "..", "../session-closed", "session\\closed", "session\0closed"])
    assert.equal(nativeRecordingRoot({ ...closed, session_id }), null);
  for (const recording_directory of [undefined, null, "relative", `${root}\0`])
    assert.equal(nativeRecordingRoot({ ...closed, recording_directory }), null);
});
