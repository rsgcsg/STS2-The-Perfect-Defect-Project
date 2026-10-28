#!/usr/bin/env node
// Seconds-long environment preflight before portable gates. It only observes the
// worktree; it never installs, repairs or relaxes a later gate.
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { workspaceCapacity } from "./check-workspace.mjs";
import { PROJECT_ROOT, projectVenvPython, resolveProjectPython } from "./project-python.mjs";

const SCOPES = {
  full: { imports: true, dotnet: true, uv: true, clean: true, pythonNode: true },
  python: { imports: true, dotnet: false, uv: true, clean: true, pythonNode: true },
  components: { imports: false, dotnet: true, uv: false, clean: false, pythonNode: false },
  quick: { imports: false, dotnet: false, uv: false, clean: false, pythonNode: true },
  setup: { imports: true, dotnet: false, uv: true, clean: false, pythonNode: true },
};
// Packages whose absence previously surfaced only after a long run. Native
// extensions are imported; the remaining locked extras are located without import.
const IMPORTED = ["torch", "safetensors", "tokenizers"];
const LOCATED = ["pytest", "ruff", "mypy", "jsonschema", "numpy", "transformers", "peft", "boto3", "duckdb",
  "modal", "waitress", "huggingface_hub"];
const SETUP = "npm run setup:worktree";

export function nodeLockMismatches(directory, { read = file => fs.readFileSync(file, "utf8") } = {}) {
  let lock;
  try { lock = JSON.parse(read(path.join(directory, "package-lock.json"))); }
  catch (error) { return { missing_lock: true, problems: [`package-lock.json unreadable: ${error.code ?? error.message}`] }; }
  const problems = [];
  for (const [key, entry] of Object.entries(lock.packages ?? {})) {
    if (!key.includes("node_modules/") || entry.link) continue;
    let installed;
    try { installed = JSON.parse(read(path.join(directory, key, "package.json"))).version; }
    catch { if (!entry.optional) problems.push(`${key} missing`); continue; }
    if (entry.version !== undefined && installed !== entry.version)
      problems.push(`${key} ${installed} != locked ${entry.version}`);
  }
  return { missing_lock: false, problems };
}

function command(executable, args, options = {}) {
  const result = spawnSync(executable, args, { encoding: "utf8", timeout: 30000, windowsHide: true,
    shell: process.platform === "win32" && !path.isAbsolute(executable), ...options });
  return { ok: !result.error && result.status === 0,
    output: `${result.stdout ?? ""}${result.stderr ?? ""}`.trim(), error: result.error?.code };
}

export function preflight(scope, { root = PROJECT_ROOT, environment = process.env, run = command,
  resolve = options => resolveProjectPython(options), lockMismatches = nodeLockMismatches,
  capacity = directory => workspaceCapacity(directory), git = args =>
    execFileSync("git", args, { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }) } = {}) {
  const policy = SCOPES[scope];
  if (!policy) throw new Error(`unknown preflight scope: ${scope}`);
  const checks = [];
  const add = (id, ok, detail, fix) => checks.push({ id, status: ok ? "ok" : "fail", detail, ...(ok ? {} : { fix }) });

  let python;
  try {
    python = resolve({ environment, root });
    add("python", true, `${python.version} ${python.executable} (${python.source})`);
  } catch (error) { add("python", false, error.message, SETUP); }

  const venv = projectVenvPython(root);
  if (policy.imports) {
    const code = `import importlib, importlib.util, sys\nmissing=[]\nfor name in ${JSON.stringify(IMPORTED)}:\n` +
      `  try: importlib.import_module(name)\n  except Exception as error: missing.append(f"{name} ({type(error).__name__})")\n` +
      `missing += [name for name in ${JSON.stringify(LOCATED)} if importlib.util.find_spec(name) is None]\n` +
      `print(", ".join(missing)); sys.exit(1 if missing else 0)`;
    const result = fs.existsSync(venv) ? run(venv, ["-c", code]) : { ok: false, output: `${venv} missing` };
    add("python_packages", result.ok, result.ok ? `python/.venv imports ${IMPORTED.join(", ")} and locates locked extras`
      : `python/.venv lacks: ${result.output || result.error}`, "uv sync --project python --locked --all-extras");
  }

  for (const [id, directory, fix, required] of [["node_modules", root, "npm ci", true],
    ["python_node_modules", path.join(root, "python"), "npm ci --prefix python", policy.pythonNode]]) {
    if (!required) continue;
    const result = lockMismatches(directory);
    add(id, result.problems.length === 0, result.problems.length === 0 ? "matches package-lock.json"
      : `${result.problems.length} differ from package-lock.json: ${result.problems.slice(0, 3).join("; ")}`, fix);
  }

  for (const [id, executable, args, required, fix] of [
    ["uv", "uv", ["--version"], policy.uv, "install uv (https://docs.astral.sh/uv/)"],
    ["dotnet", "dotnet", ["--version"], policy.dotnet, "install the .NET SDK used by CI (actions/setup-dotnet in .github/workflows/ci.yml)"]]) {
    if (!required) continue;
    const result = run(executable, args);
    add(id, result.ok, result.ok ? result.output.split(/\r?\n/u)[0] : `${executable} unavailable: ${result.error ?? result.output}`, fix);
  }

  const space = [capacity(root), capacity(os.tmpdir())];
  add("disk", space.every(item => item.sufficient), space.map(item =>
    `${(Number(item.available_bytes) / 1024 ** 3).toFixed(1)} GiB free, needs ${(Number(item.required_bytes) / 1024 ** 3).toFixed(1)}`).join("; "),
  "free space per python/docs/DATA_LIFECYCLE.md#workstation-capacity-before-long-tasks");

  if (policy.clean) {
    const dirty = git(["status", "--porcelain", "--untracked-files=normal"]).trim();
    add("clean_worktree", dirty === "", dirty === "" ? "clean" : `uncommitted: ${dirty.split("\n").slice(0, 3).join(", ")}`,
      "commit or stash; full and Python checks only accept an unchanged clean candidate");
  }
  return { scope, passed: checks.every(check => check.status === "ok"), python: python?.executable ?? null, checks };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const scope = process.argv[2] ?? "full";
  const started = performance.now();
  const report = preflight(scope);
  for (const check of report.checks)
    process.stdout.write(`${check.status === "ok" ? "ok  " : "FAIL"} ${check.id}: ${check.detail}${check.fix ? `\n     fix: ${check.fix}` : ""}\n`);
  process.stdout.write(`${JSON.stringify({ stage: "preflight", scope, passed: report.passed,
    seconds: Math.round(performance.now() - started) / 1000 })}\n`);
  if (!report.passed) process.exitCode = 1;
}
