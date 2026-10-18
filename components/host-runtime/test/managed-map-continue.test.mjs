import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { ManagedMapContinueUnknown, saveManagedMapContinue,
  validateManagedMapContinue } from "../src/managed-map-continue.mjs";

function fixture() {
  const directory = mkdtempSync(join(tmpdir(), "managed-map-continue-unit-"));
  const path = join(directory, "map.save"), calls = [];
  let map = true, pending = false, unknown = false;
  const started = {
    session: { tainted: false, observe: () => ({ interaction: { kind: map ? "map_navigation" : "combat_turn" },
      completeness: { status: "complete" } }) },
    runtime: { build: { artifact_sha256: "a".repeat(64), runtime_sts2_sha256: "b".repeat(64) },
      process: { async request(request) {
        calls.push(request);
        if (request.cmd === "run_identity") return { type: "run_identity", active: true,
          seed: "TEST", act: 1, floor: 3, action_executor_running: pending,
          pending_host_operation: false, pending_card_selection: false, pending_relic_selection: false, pending_card_reward: false,
          pending_reward_set: false, pending_bundle: false };
        if (request.cmd === "write_continue_save") {
          writeFileSync(request.path, "synthetic native map save");
          if (unknown) throw new Error("transport disappeared after native write");
          return { type: "save_result", success: true, room_type: "MapRoom", path: request.path };
        }
        throw new Error("unexpected request");
      } } }
  };
  return { started, calls, path, directory, nonMap: () => { map = false; },
    pending: () => { pending = true; }, unknown: () => { unknown = true; },
    close: () => rmSync(directory, { recursive: true, force: true }) };
}

test("native map save returns a file and exact candidate identity without overwriting files", async () => {
  const f = fixture();
  try {
    const save = await saveManagedMapContinue(f.started, f.path, 1000);
    assert.equal(validateManagedMapContinue(f.started, save), save);
    assert.equal(save.floor, 3); assert.equal(save.seed, "TEST");
    assert.equal(save.bytes, readFileSync(f.path).length);
    await assert.rejects(saveManagedMapContinue(f.started, f.path, 1000), { code: "EEXIST" });
    assert.equal(f.calls.filter(c => c.cmd === "write_continue_save").length, 1);
  } finally { f.close(); }
});

test("non-map and pending native work cannot create or write a save", async () => {
  for (const condition of ["nonMap", "pending"]) {
    const f = fixture();
    try {
      f[condition]();
      await assert.rejects(saveManagedMapContinue(f.started, f.path, 1000), /quiescent_map_required/);
      assert.equal(existsSync(f.path), false);
      assert.equal(f.calls.some(c => c.cmd === "write_continue_save"), false);
    } finally { f.close(); }
  }
});

test("changed file or native candidate cannot validate for restore", async () => {
  const f = fixture();
  try {
    const save = await saveManagedMapContinue(f.started, f.path, 1000);
    f.started.runtime.build.artifact_sha256 = "c".repeat(64);
    assert.throws(() => validateManagedMapContinue(f.started, save), /candidate_changed/);
    f.started.runtime.build.artifact_sha256 = save.candidate.artifact_sha256;
    writeFileSync(f.path, "changed synthetic save");
    assert.throws(() => validateManagedMapContinue(f.started, save), /file_changed/);
  } finally { f.close(); }
});

test("unknown native save preserves its file and does not resend", async () => {
  const f = fixture();
  try {
    f.unknown();
    await assert.rejects(saveManagedMapContinue(f.started, f.path, 1000), ManagedMapContinueUnknown);
    assert.equal(readFileSync(f.path, "utf8"), "synthetic native map save");
    assert.equal(f.calls.filter(c => c.cmd === "write_continue_save").length, 1);
  } finally { f.close(); }
});
