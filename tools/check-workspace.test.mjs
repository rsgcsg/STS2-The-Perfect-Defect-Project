import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import test from "node:test";
import { processExecuting } from "./test/process-witness.mjs";
import { ownedCommand, runStages, scriptIncludesGuard, sourceAccepted, workspaceStages, pytestPartition,
  PYTEST_SHARD_COUNT, PYTEST_SHARD_INDEXES } from "./check-workspace.mjs";

const root = path.resolve(import.meta.dirname, "..");

test("partition union retains every gate once and fresh builds on both leaves", () => {
  for (const scope of ["full", "python"]) {
    const original = workspaceStages(scope);
    const [first, ...others] = PYTEST_SHARD_INDEXES.map(index =>
      workspaceStages(scope, {pytestShard: `${index}/${PYTEST_SHARD_COUNT}`}));
    assert.deepEqual(first.map(stage => stage.id), original.map(stage => stage.id));
    assert.equal(others.length, PYTEST_SHARD_COUNT - 1);
    for (const other of others) {
      assert.deepEqual(other.map(stage => stage.id), ["preflight", "shared-client-build", "python"]);
      assert.deepEqual(other[0].requires, []);
      assert.deepEqual(other[1].requires, ["preflight"]);
      assert.deepEqual(other[2].requires, ["shared-client-build"]);
    }
    for (const [position, stages] of [first, ...others].entries()) {
      const value = `${position + 1}/${PYTEST_SHARD_COUNT}`;
      assert.deepEqual(stages.find(stage => stage.id === "python").args,
        ["run", "check:python", "--", "--pytest-shard", value]);
      assert.equal(pytestPartition(value).complete, false);
    }
  }
  assert.equal(pytestPartition(undefined), null);
  for (const value of ["", "0/3", "4/3", "1/2", "2/2", "1/1", "1/4", "all"]) assert.throws(() => pytestPartition(value));
  assert.throws(() => workspaceStages("components", {pytestShard: `1/${PYTEST_SHARD_COUNT}`}));
});

test("full and Python gates keep their required inventory; short real-consumer checks precede broad tests", () => {
  const full = workspaceStages("full"), python = workspaceStages("python");
  assert.deepEqual(full.map(stage => stage.id), ["preflight", "repository", "shared-client-build", "s0-consumers",
    "python", "connector", "host-runtime", "annotator", "evidence", "policy-runtime", "workbench", "ingame-ui", "game-mod"]);
  assert.deepEqual(python.map(stage => stage.id), ["preflight", "repository", "shared-client-build", "s0-consumers", "python"]);
  for (const scope of ["full", "python", "components"]) {
    const [first, ...later] = workspaceStages(scope);
    assert.deepEqual(first, { id: "preflight", args: ["run", "check:preflight", "--", scope], requires: [] });
    for (const stage of later.filter(stage => !stage.requires.includes("shared-client-build")))
      assert.ok(stage.requires.includes("preflight"), `${scope}/${stage.id} must wait for the environment preflight`);
  }
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
  assert.equal(calls.length, 13);
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
  assert.equal(guard.results.filter(stage => stage.status === "blocked").length, 12);
  const repository = await runStages(workspaceStages("full"), async stage => ({ code: stage.id === "repository" ? 1 : 0 }));
  assert.deepEqual(repository.results.filter(stage => stage.status !== "blocked").map(stage => stage.id), ["preflight", "repository"]);
  const cancelled = await runStages(workspaceStages("full"), async () => ({ code: null, signal: "SIGTERM" }));
  assert.equal(cancelled.passed, false);
  assert.equal(cancelled.results[0].status, "cancelled");
  assert.equal(cancelled.results.filter(stage => stage.status === "blocked").length, 12);
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
    assert.equal(result.error, undefined, JSON.stringify({ error: result.error,
      cleanup_diagnostic: result.cleanup_diagnostic }));
    const bytes = fs.readFileSync(marker);
    await new Promise(resolve => setTimeout(resolve, 80));
    assert.deepEqual(fs.readFileSync(marker), bytes, "grandchild continues writing after cancellation");
    // A reparented grandchild may stay a zombie until init reaps it; it can no longer execute.
    for (const pid of JSON.parse(fs.readFileSync(pids))) assert.equal(processExecuting(pid), false, `pid ${pid} still executing`);
  } finally {
    await owned.cancel("SIGKILL"); fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("cancellation EPERM retains its original error and exact owned-group diagnostic",
  { skip: process.platform === "win32" }, async t => {
    for (const phase of ["send", "probe"]) await t.test(phase, async t => {
      // A short-lived actual child permits a denied send without requiring an
      // unrelated compensating signal. Only this exact group's syscall is injected.
      const owned = ownedCommand(process.execPath,
        ["-e", "process.stdout.write('ready\\n');setTimeout(()=>{},150)"],
        { stdio: ["ignore", "pipe", "ignore"] });
      await new Promise(resolve => owned.child.stdout.once("data", resolve));
      const original = process.kill.bind(process);
      const error = Object.assign(new Error("kill EPERM"),
        { code: "EPERM", errno: -1, syscall: "kill" });
      t.mock.method(process, "kill", (pid, signal) => {
        if (pid === -owned.child.pid && signal === (phase === "probe" ? 0 : "SIGTERM")) throw error;
        return original(pid, signal);
      });
      await owned.cancel("SIGTERM");
      const result = await owned.completion;
      assert.equal(result.error, String(error));
      assert.deepEqual({ ...result.cleanup_diagnostic, group_state: undefined }, {
        phase, signal: phase === "probe" ? 0 : "SIGTERM", pid: owned.child.pid,
        pgid: owned.child.pid, uid: process.getuid(), error_code: "EPERM",
        errno: -1, syscall: "kill", group_state: undefined,
      });
      const snapshot = result.cleanup_diagnostic.group_state;
      assert.equal(snapshot.observation, "post_failure_sample");
      assert.equal(snapshot.status, "observed");
      assert.ok(snapshot.members.length <= 16);
      assert.ok(snapshot.members.every(member => member.pgid === owned.child.pid));
      assert.ok(snapshot.members.every(member => Object.keys(member).sort().join(",") ===
        "pgid,pid,ppid,state,uid"));
      const denied = await runStages([{ id: "owned", args: [], requires: [] }], async () => result);
      assert.equal(denied.passed, false);
      assert.equal(denied.results[0].error, String(error));
      assert.deepEqual(denied.results[0].cleanup_diagnostic, result.cleanup_diagnostic);
    });
  });

test("capacity checks the actual path and rejects low space before a full gate can spawn", async () => {
  const { workspaceCapacity } = await import("./check-workspace.mjs");
  const gib = 1024n ** 3n;
  const seen = [];
  const observe = (directory, options) => {
    seen.push([directory, options.bigint]); return { bavail: 8n * gib, bsize: 1n };
  };
  assert.equal(workspaceCapacity("actual-workspace", {environment: {}, observe}).sufficient, true);
  assert.deepEqual(seen, [["actual-workspace", true]]);
  assert.equal(workspaceCapacity("actual-workspace", {
    environment: {SPIREAGENT_MIN_FREE_BYTES: String(3n * gib)}, observe,
  }).sufficient, false);
  for (const value of ["", "-1", "1.5", "9223372036854775808"])
    assert.throws(() => workspaceCapacity("unused", {
      environment: {SPIREAGENT_MIN_FREE_BYTES: value}, observe,
    }), /invalid_workspace_reserve/u);
  const { spawnSync } = await import("node:child_process");
  const refused = spawnSync(process.execPath, [path.join(root, "tools/check-workspace.mjs"), "full"], {
    encoding: "utf8", env: {...process.env, SPIREAGENT_MIN_FREE_BYTES: String(1n << 60n)},
  });
  assert.equal(refused.status, 1);
  assert.equal(JSON.parse(refused.stdout).stage, "workspace_capacity");
  assert.ok(!refused.stdout.includes('"stage":"workspace_gate"'));
});
