import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import test from "node:test";
import { ownedCommand, runStages, scriptIncludesGuard, sourceAccepted, workspaceStages } from "./check-workspace.mjs";

const root = path.resolve(import.meta.dirname, "..");

test("full and Python gates keep their required inventory; short real-consumer checks precede broad tests", () => {
  const full = workspaceStages("full"), python = workspaceStages("python");
  assert.deepEqual(full.map(stage => stage.id), ["repository", "shared-client-build", "s0-consumers",
    "python", "connector", "host-runtime", "annotator", "evidence", "policy-runtime", "workbench", "ingame-ui", "game-mod"]);
  assert.deepEqual(python.map(stage => stage.id), ["repository", "shared-client-build", "s0-consumers", "python"]);
  for (const scope of ["full", "python", "components"]) {
    const visited = new Set();
    for (const stage of workspaceStages(scope)) {
      assert.ok(!visited.has(stage.id));
      for (const dependency of stage.requires) assert.ok(visited.has(dependency), `${stage.id}: missing prior ${dependency}`);
      visited.add(stage.id);
      const prefix = stage.args.indexOf("--prefix");
      const owner = prefix < 0 ? root : path.join(root, stage.args[prefix + 1]);
      const scripts = JSON.parse(fs.readFileSync(path.join(owner, "package.json"))).scripts;
      assert.ok(scripts[stage.args[stage.args.indexOf("run") + 1]], `missing ${stage.id} command`);
    }
  }
  assert.throws(() => workspaceStages("native"), /unknown_workspace_scope/);
});

test("an Annotator failure does not hide independent Evidence, Runtime or Python outcomes and aggregate stays failed", async () => {
  const calls = [];
  const result = await runStages(workspaceStages("full"), async stage => {
    calls.push(stage.id); return { code: stage.id === "annotator" || stage.id === "python" ? 1 : 0 };
  });
  assert.equal(result.passed, false);
  assert.equal(result.results.find(stage => stage.id === "annotator").status, "failed");
  assert.equal(result.results.find(stage => stage.id === "python").status, "failed");
  assert.ok(calls.includes("evidence") && calls.includes("policy-runtime"));
  assert.equal(calls.length, 12);
});

test("failed generated-client build blocks dependent consumers but preserves unrelated owner checks", async () => {
  const calls = [];
  const result = await runStages(workspaceStages("full"), async stage => {
    calls.push(stage.id); return { code: stage.id === "shared-client-build" ? 1 : 0 };
  });
  assert.equal(result.passed, false);
  assert.ok(calls.includes("annotator") && calls.includes("evidence"));
  assert.ok(calls.includes("workbench"));
  for (const id of ["s0-consumers", "policy-runtime", "python"]) {
    assert.ok(!calls.includes(id));
    assert.deepEqual(result.results.find(stage => stage.id === id).blocked_by, ["shared-client-build"]);
  }
});

test("repository failure, cancelled process and spawn errors never become pass or execute dependent work", async () => {
  const guard = await runStages(workspaceStages("full"), async () => ({ code: 1 }));
  assert.equal(guard.results.filter(stage => stage.status === "failed").length, 1);
  assert.equal(guard.results.filter(stage => stage.status === "blocked").length, 11);
  const cancelled = await runStages(workspaceStages("full"), async () => ({ code: null, signal: "SIGTERM" }));
  assert.equal(cancelled.passed, false);
  assert.equal(cancelled.results[0].status, "cancelled");
  assert.equal(cancelled.results.filter(stage => stage.status === "blocked").length, 11);
  const spawned = await runStages(workspaceStages("python"), async () => { throw new Error("ENOENT"); });
  assert.equal(spawned.passed, false);
  assert.match(spawned.results[0].error, /ENOENT/);
});

test("successful stages cannot qualify a changed or dirty full candidate", async () => {
  assert.equal((await runStages(workspaceStages("full"), async () => ({ code: 0 }))).passed, true);
  const source = { head: "head", tree: "tree", dirty: false };
  assert.equal(sourceAccepted(source, source, "full"), true);
  assert.equal(sourceAccepted(source, { ...source, head: "new" }, "full"), false);
  assert.equal(sourceAccepted(source, { ...source, dirty: true }, "python"), false);
  assert.equal(sourceAccepted({ ...source, dirty: true }, source, "full"), false);
  assert.equal(sourceAccepted({ ...source, dirty: true }, { ...source, dirty: true }, "components"), true);
});

test("repository validators follow real script references and reject missing, recursive or merely named guards", () => {
  const scripts = JSON.parse(fs.readFileSync(path.join(root, "package.json"))).scripts;
  for (const guard of ["project:check", "check:governance"]) {
    assert.equal(scriptIncludesGuard(scripts, "check", guard), true);
    const corrupted = { ...scripts, "check:repository": "node other.mjs" };
    assert.equal(scriptIncludesGuard(corrupted, "check", guard), false);
    assert.equal(scriptIncludesGuard({ check: "echo npm-not-a-guard", [guard]: "node x.mjs" }, "check", guard), false);
    assert.equal(scriptIncludesGuard({ check: "npm run loop", loop: "npm run check" }, "check", guard), false);
    assert.equal(scriptIncludesGuard({ check: `npm run ${guard}` }, "check", guard), false);
  }
});

test("cancelling an actual owned parent terminates its continuing grandchild before returning", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "workspace-owned-child-"));
  const marker = path.join(directory, "grandchild"), pids = path.join(directory, "pids.json");
  const grandchild = "require('node:fs').appendFileSync(process.argv[1], 'x'); setInterval(() => require('node:fs').appendFileSync(process.argv[1], 'x'), 20);";
  const parent = "const c=require('node:child_process').spawn(process.execPath,['-e',process.argv[1],process.argv[2]],{stdio:'ignore'});require('node:fs').writeFileSync(process.argv[3],JSON.stringify([process.pid,c.pid]));setInterval(()=>{},1000);";
  const owned = ownedCommand(process.execPath, ["-e", parent, grandchild, marker, pids], { stdio: "ignore" });
  try {
    const deadline = Date.now() + 5000;
    while ((!fs.existsSync(pids) || !fs.existsSync(marker)) && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 20));
    assert.ok(fs.existsSync(marker), "actual grandchild started");
    await owned.cancel("SIGTERM");
    const result = await owned.completion;
    assert.equal(result.signal, "SIGTERM");
    assert.equal(result.error, undefined, result.error);
    const bytes = fs.readFileSync(marker);
    await new Promise(resolve => setTimeout(resolve, 80));
    assert.deepEqual(fs.readFileSync(marker), bytes, "grandchild continues writing after cancellation");
    for (const pid of JSON.parse(fs.readFileSync(pids))) assert.throws(() => process.kill(pid, 0), /ESRCH|no such process/i);
  } finally {
    await owned.cancel("SIGKILL"); fs.rmSync(directory, { recursive: true, force: true });
  }
});
