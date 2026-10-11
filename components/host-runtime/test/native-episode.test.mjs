import assert from "node:assert/strict";
import test from "node:test";
import { PassThrough } from "node:stream";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import path from "node:path";
import os from "node:os";
import { createHash } from "node:crypto";
import { ShippedPlayerEnvironmentSession } from "../src/shipped-player-environment.mjs";
import { handleReferenceDriverRequest, serveReferenceDriver } from "../tools/reference-pe-driver.mjs";
import { requireExpectedNativeInstallation, requireExpectedNativeDiskTuple, requireExpectedNativeLoadedTuple } from "../src/native-episode-tuple.mjs";

const expected = { game_version: "fixture-game", game_commit: "fixture-commit", game_assembly_sha256: "a".repeat(64),
  host_artifact_sha256: "b".repeat(64), host_source_revision: "c".repeat(40), modset_fingerprint: "d".repeat(64),
  host_module_version_id: "01234567-89ab-cdef-0123-456789abcdef" };
const handoff = { schema: "sts2.host-runtime/reference-controller-handoff-1", runtime_instance_id: "runtime-1",
  controller: null, observed_at: "2026-10-11T00:00:00.000Z", basis: "fresh_control_observation_after_close" };

function episode(events) {
  return { snapshot: { snapshot_id: "initial" }, identity: { host: { runtime_instance_id: "runtime-1" } },
    releaseController: async () => { events.push("release"); return handoff; },
    submit: async () => { events.push("step"); }, close: async () => { events.push("close"); return { code: 0, forced: false }; } };
}

test("production installed/disk helpers reject tuple drift before a world spawn", context => {
  const root = mkdtempSync(path.join(os.tmpdir(), "native-preflight-"));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  writeFileSync(path.join(root, "STS2_PLATFORM.dll"), "synthetic-artifact");
  const artifact = createHash("sha256").update("synthetic-artifact").digest("hex");
  writeFileSync(path.join(root, "STS2_PLATFORM.identity"), JSON.stringify({ source_revision: expected.host_source_revision,
    artifact_sha256: artifact, artifact_mvid: expected.host_module_version_id }));
  const disk = { release: { version: expected.game_version, commit: expected.game_commit }, sts2_assembly: { sha256: expected.game_assembly_sha256 } };
  const actualExpected = { ...expected, host_artifact_sha256: artifact };
  let spawned = 0;
  requireExpectedNativeInstallation(actualExpected, { mods_dir: root }, disk);
  assert.throws(() => { requireExpectedNativeInstallation(expected, { mods_dir: root }, disk); spawned++; });
  assert.equal(spawned, 0);
  for (const bad of [{ status: "missing" }, { status: "verified", installed_sha256: artifact, identity: { source_revision: "e".repeat(40) } }])
    assert.throws(() => requireExpectedNativeDiskTuple(actualExpected, disk, bad));
  requireExpectedNativeDiskTuple(null, null, null); // Existing omitted request keeps its legacy behavior.
});

test("actual loaded capability tuple must match before bootstrap control", () => {
  const caps = { host: { implementation: { source_revision: expected.host_source_revision, artifact_sha256: expected.host_artifact_sha256,
    module_version_id: expected.host_module_version_id } }, game: { version: expected.game_version, commit: expected.game_commit,
    modset: { fingerprint: expected.modset_fingerprint } } };
  requireExpectedNativeLoadedTuple(expected, caps);
  let actions = 0;
  assert.throws(() => { requireExpectedNativeLoadedTuple(expected, { ...caps, game: { ...caps.game, commit: "replacement" } }); actions++; });
  assert.equal(actions, 0);
});

test("real session and public command release once and fence reset/step for driver lifetime", async () => {
  const events = [];
  const session = new ShippedPlayerEnvironmentSession({}, { startEpisode: async () => { events.push("reset"); return episode(events); } });
  await session.reset("CANONICAL");
  await assert.rejects(() => session.releaseBootstrap("wrong-runtime"));
  const result = await handleReferenceDriverRequest(session, { command: "release_bootstrap", request_id: "request-1", expected_runtime_instance_id: "runtime-1" });
  assert.deepEqual(result.handoff, handoff);
  assert.deepEqual(await session.releaseBootstrap("runtime-1"), handoff);
  await assert.rejects(() => session.reset("SECOND"), /transferred/u);
  assert.throws(() => session.submit({}), /transferred/u);
  await session.close();
  assert.deepEqual(events, ["reset", "release", "close"]);
});

test("uncertain release fences reset and does not repeat original release", async () => {
  let releases = 0;
  const e = episode([]);
  e.releaseController = async () => { releases++; throw new Error("lost release response"); };
  const session = new ShippedPlayerEnvironmentSession({}, { startEpisode: async () => e });
  await session.reset("FIRST");
  await assert.rejects(() => session.releaseBootstrap("runtime-1"));
  await assert.rejects(() => session.releaseBootstrap("runtime-1"), /unknown/u);
  await assert.rejects(() => session.reset("SECOND"));
  assert.equal(releases, 1);
});

test("production EOF handler aborts a blocked reset before waiting its serialized queue", async () => {
  const input = new PassThrough();
  const events = [], responses = [];
  const session = new ShippedPlayerEnvironmentSession({}, { startEpisode: ({ signal }) => new Promise((resolve, reject) => {
    events.push("reset"); signal.addEventListener("abort", () => { events.push("abort"); reject(signal.reason); }, { once: true });
  }) });
  const done = serveReferenceDriver(session, { inputStream: input, emit: value => responses.push(value) });
  input.write(`${JSON.stringify({ command: "reset", request_id: "request-1", seed: "FIRST" })}\n`);
  await new Promise(resolve => setImmediate(resolve));
  input.end();
  await done;
  assert.deepEqual(events, ["reset", "abort"]);
  assert.equal(responses[0].request_id, "request-1");
  assert.equal(responses[0].type, "error");
});
