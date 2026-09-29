import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, symlinkSync, truncateSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { runManagedSaveRoundtripProbe } from "../src/managed-save-roundtrip-probe.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SEED = "M2H0ST20260929A";
const OPTIONS = { root: ROOT, candidateDirectory: "synthetic-only", diskIdentity: {},
  seed: SEED, requestTimeoutMs: 1234 };
const FLAGS = ["action_executor_running", "pending_host_operation", "pending_card_selection",
  "pending_card_reward", "pending_reward_set", "pending_bundle"];

function state(index, floor = 0) {
  return {
    type: "decision", decision: "map_select",
    context: { act: 1, act_index: 0, act_definition_id: "OVERGROWTH",
      act_name: "Overgrowth", floor, total_floor: floor, ascension: 0,
      bosses: [{ id: "VANTOM_BOSS", name: "Vantom", order: 0 }], modifiers: [] },
    choices: [{ col: 3, row: floor, type: "Monster", native_ref: `map-${index}-${floor}`,
      children: [{ col: 2, row: floor + 1, type: "Monster" }] }],
    player: { name: "The Defect", character_id: "DEFECT", hp: 75,
      max_hp: 75, gold: 99, native_ref: `player-${index}`, max_potion_slots: 3,
      relics: [], potions: [], deck: [] }
  };
}
function map(floor) {
  return { type: "map", rows: [[{ col: 2, row: floor + 1, type: "Monster",
    children: [{ col: 3, row: 16 }], visited: false, current: false }]],
  boss: { col: 3, row: 16, type: "Boss" }, current_coord: null };
}
function fixture({ alter, onRequest, onRuntime, onStop } = {}) {
  const calls = [];
  const runtimes = [];
  const savePaths = [];
  const exits = [];
  let original = null;
  const startRuntime = async (options) => {
    const index = runtimes.length;
    assert.equal(options.requestTimeoutMs, OPTIONS.requestTimeoutMs);
    assert.equal(options.quietDiagnostics, true);
    let floor = 0;
    let wrote = false;
    const runtime = {
      adapterRuntimeInstanceId: `instance-${index}`,
      build: { artifact_sha256: "artifact", artifact_mvid: "mvid",
        source_patch_sha256: "patch", runtime_sts2_sha256: "game" },
      process: {
        pid: 1000 + index,
        async request(request, timeoutMs) {
          calls.push({ index, request, timeoutMs });
          assert.equal(timeoutMs, OPTIONS.requestTimeoutMs);
          await onRequest?.({ index, request, original, floor });
          let response;
          if (request.cmd === "start_run") {
            assert.equal(index, 0);
            assert.deepEqual(request, { cmd: "start_run", character: "Defect", ascension: 0, seed: SEED, lang: "en" });
            response = state(index, floor);
          } else if (request.cmd === "load_save") {
            assert.ok(index > 0);
            assert.equal(existsSync(request.path), true);
            assert.equal(readFileSync(request.path, "utf8"), "synthetic save 私有");
            assert.notEqual(request.path, original);
            savePaths.push(request.path);
            response = state(index, floor);
          } else if (request.cmd === "run_identity") {
            response = { type: "run_identity", active: true, seed: SEED, act: 1, floor,
              ...Object.fromEntries(FLAGS.map((flag) => [flag, false])) };
          } else if (request.cmd === "get_map") {
            response = map(floor);
          } else if (request.cmd === "write_continue_save") {
            assert.equal(index, 0);
            assert.equal(existsSync(request.path), false);
            original = request.path;
            savePaths.push(original);
            writeFileSync(original, "synthetic save 私有");
            wrote = true;
            response = { type: "save_result", success: true, room_type: "MapRoom",
              path: request.path, size: "synthetic save 私有".length };
          } else if (request.cmd === "action") {
            assert.equal(request.action, "select_map_node");
            // The current runtime binding is used, never a replayed original native ref.
            assert.equal(request.args.map_point_ref, `map-${index}-${floor}`);
            floor += 1;
            response = state(index, floor);
          } else throw new Error(`unexpected command ${request.cmd}`);
          return alter?.({ index, request, response, floor, wrote, original }) ?? response;
        },
        async stop(options) {
          assert.deepEqual(options, { request: { cmd: "quit" }, timeoutMs: 1234 });
          exits.push(index);
          return onStop?.(index) ?? { code: 0, signal: null };
        }
      }
    };
    onRuntime?.(runtime, index);
    runtimes.push(runtime);
    return runtime;
  };
  return { calls, runtimes, savePaths, exits, startRuntime };
}
async function run(f) {
  const result = await runManagedSaveRoundtripProbe(OPTIONS, f);
  assert.deepEqual(f.exits, f.runtimes.map((_, index) => index));
  for (const file of f.savePaths) assert.equal(existsSync(path.dirname(file)), false);
  assert.equal(result.report.gates.private_workspace_removed, "pass");
  return result.report;
}
function failed(report, reason) {
  assert.equal(report.status, "managed_map_save_roundtrip_incomplete");
  assert.equal(report.failure.reason, reason);
}

test("synthetic map-to-map fixture uses two fresh runtimes and compares each current binding", async () => {
  const f = fixture();
  const report = await run(f);
  assert.equal(report.status, "managed_map_save_roundtrip_pass", JSON.stringify(report.failure));
  assert.equal(f.runtimes.length, 3);
  assert.equal(f.calls.filter(({ request }) => request.cmd === "action").length, 3);
  assert.equal(new Set(f.savePaths).size, 3);
  assert.equal(report.save.bytes, Buffer.byteLength("synthetic save 私有"));
  assert.equal(new Set(report.processes.map((entry) => entry.map_digest)).size, 1);
  assert.equal(new Set(report.processes.map((entry) => entry.successor_digest)).size, 1);
  const json = JSON.stringify(report);
  for (const privateValue of [...f.savePaths, "synthetic save 私有", "map-0-0", "player-0"])
    assert.equal(json.includes(privateValue), false);
});

test("non-map start is refused before any save or gameplay action", async () => {
  const f = fixture({ alter: ({ request, response }) => request.cmd === "start_run"
    ? { ...response, decision: "event" } : response });
  failed(await run(f), "native_boundary_not_map");
  assert.equal(f.runtimes.length, 1);
  assert.equal(f.calls.some(({ request }) => ["write_continue_save", "action"].includes(request.cmd)), false);
});

for (const flag of FLAGS) {
  test(`pending ${flag} cannot pass`, async () => {
    const f = fixture({ alter: ({ request, response }) => request.cmd === "run_identity"
      ? { ...response, [flag]: true } : response });
    failed(await run(f), "run_not_quiescent_or_seed_changed");
    assert.equal(f.savePaths.length, 0);
  });
}

test("missing quiescence or run-position evidence and changed seed cannot pass", async () => {
  for (const change of [{ pending_card_selection: undefined }, { seed: "OTHER" }, { floor: undefined }]) {
    const f = fixture({ alter: ({ request, response }) => request.cmd === "run_identity"
      ? { ...response, ...change } : response });
    failed(await run(f), "run_not_quiescent_or_seed_changed");
  }
});

test("position change while saving cannot pass the map boundary", async () => {
  const f = fixture({ alter: ({ request, response, wrote }) => request.cmd === "run_identity" && wrote
    ? { ...response, floor: 2 } : response });
  failed(await run(f), "run_position_changed_before_map_action");
  assert.equal(f.calls.some(({ request }) => request.cmd === "action"), false);
});

for (const result of [{ success: false }, { room_type: "CombatRoom" }, { room_type: null },
  { type: "error", message: "private raw payload" }, { path: "different.save" }]) {
  test(`unconfirmed native save fails closed: ${JSON.stringify(result)}`, async () => {
    const f = fixture({ alter: ({ request, response }) => request.cmd === "write_continue_save"
      ? { ...response, ...result } : response });
    failed(await run(f), "native_map_save_not_confirmed");
    assert.equal(f.runtimes.length, 1);
  });
}

test("map change while saving cannot use the pre-save page as a successful re-observation", async () => {
  const f = fixture({ alter: ({ request, response, wrote }) => {
    if (request.cmd === "get_map" && wrote) return { ...response, current_coord: { col: 3, row: 0 } };
    return response;
  } });
  failed(await run(f), "map_changed_during_save");
});

test("restored public HP and action-space changes are not normalized away", async () => {
  for (const mutate of [
    (response) => ({ ...response, player: { ...response.player, hp: 74 } }),
    (response) => ({ ...response, choices: [...response.choices,
      { ...response.choices[0], col: 4, native_ref: "new-other-point" }] })
  ]) {
    const f = fixture({ alter: ({ index, request, response }) => index === 1 && request.cmd === "load_save"
      ? mutate(response) : response });
    failed(await run(f), "restored_public_map_changed");
  }
});

test("different public successor fails despite an identical restored map", async () => {
  const f = fixture({ alter: ({ index, request, response }) => index === 1 && request.cmd === "action"
    ? { ...response, player: { ...response.player, gold: 100 } } : response });
  failed(await run(f), "restored_public_successor_changed");
  assert.equal(f.runtimes.length, 2);
});

test("incomplete public map catalog and unavailable get_map cannot pass", async () => {
  for (const response of [{ type: "map" }, { type: "error" }]) {
    const f = fixture({ alter: ({ request, response: original }) => request.cmd === "get_map" ? response : original });
    const report = await run(f);
    assert.equal(report.status, "managed_map_save_roundtrip_incomplete");
    assert.equal(f.savePaths.length, 0);
  }
});

test("source or restore-copy mutation fails and does not start another runtime", async () => {
  for (const target of ["original", "copy"]) {
    const f = fixture({ alter: ({ index, request, response, original }) => {
      if (index === 1 && request.cmd === "load_save")
        writeFileSync(target === "original" ? original : request.path, "changed");
      return response;
    } });
    failed(await run(f), "private_save_digest_drift");
    assert.equal(f.runtimes.length, 2);
  }
});

test("symlink, empty and oversized saves are rejected before consumption", async () => {
  for (const replacement of ["link", "empty", "oversized"]) {
    const f = fixture({ alter: ({ request, response, original }) => {
      if (request.cmd === "write_continue_save") {
        if (replacement === "link") {
          const target = `${original}.target`;
          writeFileSync(target, "synthetic");
          rmSync(original);
          symlinkSync(target, original);
        } else if (replacement === "oversized") truncateSync(original, 8 * 1024 * 1024 + 1);
        else writeFileSync(original, "");
      }
      return response;
    } });
    failed(await run(f), "invalid_private_save_file");
    assert.equal(f.runtimes.length, 1);
  }
});

test("repeated process/runtime instance and candidate digest drift are refused before load", async () => {
  for (const mutate of [
    (runtime) => { runtime.process.pid = 1000; },
    (runtime) => { runtime.adapterRuntimeInstanceId = "instance-0"; },
    (runtime) => { runtime.build.artifact_sha256 = "changed"; }
  ]) {
    const f = fixture({ onRuntime: (runtime, index) => { if (index === 1) mutate(runtime); } });
    const report = await run(f);
    assert.equal(report.status, "managed_map_save_roundtrip_incomplete");
    assert.equal(f.calls.some(({ request }) => request.cmd === "load_save"), false);
  }
});

test("transport loss after delivery is terminal, sanitized, never retried, and cleaned", async () => {
  const f = fixture({ onRequest: ({ request }) => {
    if (request.cmd === "action") throw new Error("unknown delivery: /private/secret-save raw bytes");
  } });
  const report = await run(f);
  failed(report, "probe_operation_failed");
  assert.equal(f.runtimes.length, 1);
  assert.equal(f.calls.filter(({ request }) => request.cmd === "action").length, 1);
  assert.equal(JSON.stringify(report).includes("secret-save"), false);
});

test("unclean or failed shutdown cannot produce a pass or start a replacement", async () => {
  for (const onStop of [() => ({ code: 1 }), () => { throw new Error("stop failed"); }]) {
    const f = fixture({ onStop });
    assert.equal((await run(f)).status, "managed_map_save_roundtrip_incomplete");
    assert.equal(f.runtimes.length, 1);
  }
});

test("cleanup failure remains separate and cannot overwrite the primary boundary failure", async () => {
  const f = fixture({ alter: ({ request, response }) => request.cmd === "start_run"
    ? { ...response, decision: "event" } : response,
  onStop: () => { throw new Error("private stop detail"); } });
  const report = await run(f);
  failed(report, "native_boundary_not_map");
  assert.deepEqual(report.cleanup_failures, [{ stage: "process_1_stop", reason: "probe_operation_failed" }]);
  assert.equal(JSON.stringify(report).includes("private stop detail"), false);
});

test("optional evidence includes only the report and private saves are removed", async () => {
  const directory = mkdtempSync(path.join(tmpdir(), "map-save-report-test-"));
  try {
    const f = fixture();
    const result = await runManagedSaveRoundtripProbe({ ...OPTIONS, evidenceRoot: directory }, f);
    assert.equal(result.report.status, "managed_map_save_roundtrip_pass");
    assert.deepEqual(JSON.parse(readFileSync(result.reportFile, "utf8")), result.report);
    for (const file of f.savePaths) assert.equal(existsSync(file), false);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test("invalid bounds refuse before runtime startup and CLI requires an explicit candidate", async () => {
  for (const change of [{ requestTimeoutMs: 0 }, { requestTimeoutMs: Infinity }, { seed: null }]) {
    const f = fixture();
    await assert.rejects(runManagedSaveRoundtripProbe({ ...OPTIONS, ...change }, f));
    assert.equal(f.runtimes.length, 0);
  }
  const result = spawnSync(process.execPath, [path.join(ROOT, "tools/managed-exact.mjs"), "save-roundtrip"],
    { encoding: "utf8" });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /save-roundtrip requires --candidate/u);
});
