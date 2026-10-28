import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { probePython, projectVenvPython, resolveProjectPython } from "./project-python.mjs";
import { nodeLockMismatches, preflight } from "./workspace-preflight.mjs";

const supported = executable => ({ ok: true, version: "3.11.9", resolved: executable });
const system39 = () => ({ ok: false, problem: "Python 3.9.6 < 3.11" });

test("project Python uses explicit aliases, then PYTHON, then this worktree venv, never PATH", () => {
  const root = "/worktree";
  const venv = projectVenvPython(root, "linux");
  assert.equal(venv, path.join(root, "python", ".venv", "bin", "python"));
  assert.equal(projectVenvPython(root, "win32"), path.join(root, "python", ".venv", "Scripts", "python.exe"));
  const resolve = (environment, exists = () => true) => resolveProjectPython({ environment, root, platform: "linux",
    exists, probe: supported });
  assert.deepEqual(resolve({ STS2_EVIDENCE_PYTHON: "/a", PYTHON: "/b" }), { executable: "/b", version: "3.11.9",
    resolved: "/b", source: "PYTHON" }, "an alias is honoured only when the caller names it");
  assert.equal(resolveProjectPython({ aliases: ["STS2_EVIDENCE_PYTHON"], environment: { STS2_EVIDENCE_PYTHON: "/a", PYTHON: "/b" },
    root, exists: () => true, probe: supported }).executable, "/a");
  assert.equal(resolve({}).executable, venv);
  assert.equal(resolve({}).source, "worktree_venv");
});

test("a missing venv or a system 3.9 fails immediately with the repair command", () => {
  const root = "/worktree";
  for (const [environment, exists, probe, pattern] of [
    [{}, () => false, supported, /no project Python >=3.11 found.*npm run setup:worktree.*worktree_venv=.*missing/su],
    [{}, () => true, system39, /python\/\.venv is not a usable.*3\.9\.6 < 3\.11/su],
    [{ PYTHON: "/usr/bin/python3" }, () => true, system39, /PYTHON does not name a usable.*3\.9\.6/su],
    [{ PYTHON: " " }, () => true, supported, /PYTHON is set but empty/u],
  ]) {
    const started = performance.now();
    assert.throws(() => resolveProjectPython({ environment, root, exists, probe }), error =>
      error.code === "project_python_unavailable" && pattern.test(error.message));
    assert.ok(performance.now() - started < 1000);
  }
  // An unusable explicit choice never falls through to a later candidate.
  let probed = [];
  assert.throws(() => resolveProjectPython({ aliases: ["E6_OWNED_STALE_TEACHER_PYTHON"],
    environment: { E6_OWNED_STALE_TEACHER_PYTHON: "/old", PYTHON: "/good" }, root, exists: () => true,
    probe: executable => { probed.push(executable); return executable === "/old" ? system39() : supported(executable); } }));
  assert.deepEqual(probed, ["/old"]);
});

test("the real probe reads a version and reports a missing executable", () => {
  assert.equal(probePython(path.join(os.tmpdir(), "absent-python-for-test")).ok, false);
  const fake = (version) => () => ({ status: 0, stdout: `${version}\n/somewhere/python\n` });
  assert.deepEqual(probePython("x", { spawn: fake("3.11.2") }), { ok: true, version: "3.11.2", resolved: "/somewhere/python" });
  assert.equal(probePython("x", { spawn: fake("3.12.0") }).ok, true);
  assert.deepEqual(probePython("x", { spawn: fake("3.9.6") }), { ok: false, problem: "Python 3.9.6 < 3.11" });
  assert.equal(probePython("x", { spawn: () => ({ status: 1, stdout: "" }) }).ok, false);
});

test("the CLI exits nonzero in a worktree without a project Python", { skip: process.platform === "win32" }, () => {
  const temporary = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "project-python-")));
  try {
    fs.mkdirSync(path.join(temporary, "tools"));
    fs.copyFileSync(path.join(import.meta.dirname, "project-python.mjs"), path.join(temporary, "tools", "project-python.mjs"));
    const environment = { ...process.env }; delete environment.PYTHON;
    const result = spawnSync(process.execPath, [path.join(temporary, "tools", "project-python.mjs")],
      { env: environment, encoding: "utf8", timeout: 10000 });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /npm run setup:worktree/u);
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
});

test("lock comparison names missing and drifted packages, ignoring links and absent optionals", () => {
  const files = {
    "/r/package-lock.json": JSON.stringify({ packages: { "": {}, "components/x": { version: "1.0.0" },
      "node_modules/a": { version: "1.0.0" }, "node_modules/b": { version: "2.0.0" },
      "node_modules/c": { version: "3.0.0" }, "node_modules/@s/d": { version: "1.0.0", optional: true },
      "node_modules/w": { link: true, resolved: "components/x" } } }),
    "/r/node_modules/a/package.json": JSON.stringify({ version: "1.0.0" }),
    "/r/node_modules/b/package.json": JSON.stringify({ version: "2.1.0" }),
  };
  const read = file => { if (!(file in files)) throw Object.assign(new Error("missing"), { code: "ENOENT" }); return files[file]; };
  assert.deepEqual(nodeLockMismatches("/r", { read }).problems,
    ["node_modules/b 2.1.0 != locked 2.0.0", "node_modules/c missing"]);
  assert.equal(nodeLockMismatches("/elsewhere", { read }).missing_lock, true);
});

test("preflight reports every missing prerequisite at once with its repair", () => {
  const capacity = () => ({ sufficient: true, available_bytes: String(40 * 1024 ** 3), required_bytes: String(8 * 1024 ** 3) });
  const broken = preflight("full", { root: "/missing-root", environment: {},
    resolve: () => { throw new Error("no project Python >=3.11 found"); },
    lockMismatches: () => ({ problems: ["node_modules/typescript missing"] }),
    run: () => ({ ok: false, output: "", error: "ENOENT" }), capacity, git: () => " M file\n" });
  assert.equal(broken.passed, false);
  const failed = Object.fromEntries(broken.checks.filter(check => check.status === "fail").map(check => [check.id, check.fix]));
  assert.deepEqual(Object.keys(failed), ["python", "python_packages", "node_modules", "python_node_modules", "uv", "dotnet", "clean_worktree"]);
  assert.equal(failed.python, "npm run setup:worktree");
  assert.equal(failed.node_modules, "npm ci");
  assert.equal(failed.python_node_modules, "npm ci --prefix python");
  const quick = preflight("quick", { root: "/missing-root", resolve: () => ({ executable: "/p", version: "3.11.9", source: "PYTHON" }),
    lockMismatches: () => ({ problems: [] }), run: () => { throw new Error("quick runs no external command"); }, capacity,
    git: () => { throw new Error("quick does not require a clean worktree"); } });
  assert.equal(quick.passed, true);
  assert.deepEqual(quick.checks.map(check => check.id), ["python", "node_modules", "python_node_modules", "disk"]);
  const full = preflight("full", { root: "/missing-root", resolve: () => ({ executable: "/p", version: "3.11.9", source: "PYTHON" }),
    lockMismatches: () => ({ problems: [] }), run: () => ({ ok: true, output: "ok" }), capacity: () => ({ ...capacity(), sufficient: false }),
    git: () => "" });
  assert.deepEqual(full.checks.filter(check => check.status === "fail").map(check => check.id), ["disk"]);
  assert.throws(() => preflight("native"), /unknown preflight scope/u);
});
