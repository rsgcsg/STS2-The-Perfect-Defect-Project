#!/usr/bin/env node
// The one project Python lookup for Node tests and check tools. It never falls
// back to an arbitrary interpreter on PATH: a missing or old Python fails early
// with the exact repair instead of running a long suite on the system 3.9.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const MINIMUM_PYTHON = Object.freeze([3, 11]);
export const PYTHON_REPAIR = "run `npm run setup:worktree` in this worktree " +
  "(uv sync --project python --locked --all-extras creates python/.venv), " +
  "or set PYTHON to an existing Python >=3.11 interpreter";

export class ProjectPythonError extends Error {
  constructor(message, attempts) {
    super(`${message}; ${PYTHON_REPAIR}. Checked: ${attempts.map(item =>
      `${item.source}=${item.executable} (${item.problem})`).join("; ") || "nothing"}`);
    this.name = "ProjectPythonError";
    this.code = "project_python_unavailable";
    this.attempts = attempts;
  }
}

export function projectVenvPython(root = PROJECT_ROOT, platform = process.platform) {
  return path.join(root, "python", ".venv", ...(platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"]));
}

export function probePython(executable, { spawn = spawnSync, timeout = 10000 } = {}) {
  const result = spawn(executable, ["-c",
    "import sys;print('%d.%d.%d' % sys.version_info[:3]);print(sys.executable)"],
  { encoding: "utf8", timeout, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  if (result.error) return { ok: false, problem: result.error.code ?? String(result.error) };
  if (result.status !== 0) return { ok: false, problem: `exit ${result.status}` };
  const [version = "", resolved = ""] = String(result.stdout).trim().split(/\r?\n/u);
  const parts = version.split(".").map(Number);
  if (parts.length !== 3 || parts.some(Number.isNaN)) return { ok: false, problem: "unreadable version" };
  const supported = parts[0] > MINIMUM_PYTHON[0] || (parts[0] === MINIMUM_PYTHON[0] && parts[1] >= MINIMUM_PYTHON[1]);
  return supported ? { ok: true, version, resolved: resolved.trim() || executable }
    : { ok: false, problem: `Python ${version} < ${MINIMUM_PYTHON.join(".")}` };
}

// Order: legacy explicit aliases (kept for compatibility), PYTHON, this
// worktree's python/.venv. An explicitly configured interpreter that is
// unusable fails instead of silently selecting a later candidate.
export function resolveProjectPython({ aliases = [], environment = process.env, root = PROJECT_ROOT,
  platform = process.platform, exists = fs.existsSync, probe = probePython } = {}) {
  const attempts = [];
  for (const name of [...aliases, "PYTHON"]) {
    const value = environment[name];
    if (value === undefined) continue;
    if (value.trim() === "") throw new ProjectPythonError(`${name} is set but empty`, attempts);
    const observed = probe(value);
    if (observed.ok) return { executable: value, version: observed.version, resolved: observed.resolved, source: name };
    attempts.push({ source: name, executable: value, problem: observed.problem });
    throw new ProjectPythonError(`${name} does not name a usable Python >=3.11`, attempts);
  }
  const venv = projectVenvPython(root, platform);
  if (!exists(venv)) {
    attempts.push({ source: "worktree_venv", executable: venv, problem: "missing" });
    throw new ProjectPythonError("no project Python >=3.11 found", attempts);
  }
  const observed = probe(venv);
  if (observed.ok) return { executable: venv, version: observed.version, resolved: observed.resolved, source: "worktree_venv" };
  attempts.push({ source: "worktree_venv", executable: venv, problem: observed.problem });
  throw new ProjectPythonError("the worktree python/.venv is not a usable Python >=3.11", attempts);
}

const cache = new Map();
// Convenience for tests and tools: one probe per process and alias set.
export function projectPython(aliases = []) {
  const key = aliases.join("\0");
  if (!cache.has(key)) cache.set(key, resolveProjectPython({ aliases }).executable);
  return cache.get(key);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    const found = resolveProjectPython();
    process.stdout.write(process.argv.includes("--json") ? `${JSON.stringify(found)}\n` : `${found.executable}\n`);
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
