#!/usr/bin/env node
// Two-minute pre-commit/pre-push feedback for failures that previously surfaced
// only after a long full run. It is a subset for earlier feedback, never a
// replacement for the selected full/Python gate or hosted CI.
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { projectVenvPython } from "./project-python.mjs";
import { preflight } from "./workspace-preflight.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const npm = process.platform === "win32" ? "npm.cmd" : "npm";

export function quickSteps({ base = "origin/develop", mypy = false } = {}) {
  const python = projectVenvPython(root);
  return [
    { id: "ruff", command: python, args: ["-m", "ruff", "check", "."], cwd: "python" },
    { id: "typescript", command: npm, args: ["--prefix", "components/policy-runtime", "run", "typecheck"] },
    { id: "repository", command: npm, args: ["run", "check:repository"],
      covers: "governance, CI contract, identity, BOM, boundaries, history, workspace Markdown links" },
    { id: "component-doc-links", command: npm, args: ["--prefix", "components/host-runtime", "run", "check:docs"] },
    { id: "connector-docs-contract", command: npm, args: ["--prefix", "components/connector", "run", "check:docs"] },
    { id: "runtime-version-admission", command: python, cwd: "python", args: ["-m", "pytest", "-q", "-p", "no:cacheprovider",
      "tests/test_local_models.py::test_current_policy_runtime_version_is_explicitly_admitted",
      "tests/test_local_models.py::test_unknown_runtime_version_never_selects_a_guessed_startup_contract"] },
    { id: "scope-preview", command: process.execPath, args: ["tools/check-plan.mjs", "--base", base], informational: true },
    ...(mypy ? [{ id: "mypy", command: python, args: ["-m", "mypy", "stpd", "spireagent", "tools"], cwd: "python" }] : []),
  ];
}

function execute(step) {
  const started = performance.now();
  return new Promise(resolve => {
    let output = "";
    const child = spawn(step.command, step.args, { cwd: path.join(root, step.cwd ?? "."),
      shell: process.platform === "win32" && step.command === npm, stdio: ["ignore", "pipe", "pipe"] });
    child.stdout.on("data", chunk => { output += chunk; });
    child.stderr.on("data", chunk => { output += chunk; });
    child.on("error", error => { output += String(error); });
    child.on("close", code => resolve({ ...step, code, output, seconds: Math.round(performance.now() - started) / 1000 }));
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const base = args.includes("--base") ? args[args.indexOf("--base") + 1] : "origin/develop";
  const started = performance.now();
  const environment = preflight("quick");
  for (const check of environment.checks.filter(check => check.status !== "ok"))
    process.stdout.write(`FAIL preflight ${check.id}: ${check.detail}\n     fix: ${check.fix}\n`);
  if (!environment.passed) { process.exitCode = 1; process.exit(); }
  process.env.PYTHON = environment.python;
  // Independent read-only checks run together; only the TypeScript step
  // rebuilds the Connector SDK output, which no other quick step reads.
  const results = await Promise.all(quickSteps({ base, mypy: args.includes("--mypy") }).map(execute));
  let failed = 0;
  for (const result of results) {
    const ok = result.code === 0;
    if (!ok && !result.informational) failed++;
    if (result.id === "scope-preview" && ok) {
      const plan = result.output.match(/"scope":\s*"([^"]+)"[\s\S]*?"reason":\s*"([^"]+)"/u);
      process.stdout.write(`info scope-preview: ${plan ? `${plan[1]} (${plan[2]})` : "see check:plan"} ${result.seconds}s\n`);
      continue;
    }
    process.stdout.write(`${ok ? "ok  " : result.informational ? "info" : "FAIL"} ${result.id} ${result.seconds}s\n`);
    if (!ok) process.stdout.write(`${result.output.trim().split("\n").slice(-40).map(line => `     ${line}`).join("\n")}\n`);
  }
  const seconds = Math.round(performance.now() - started) / 1000;
  process.stdout.write(`${JSON.stringify({ stage: "quick_check", passed: failed === 0, failed, seconds,
    non_claims: ["not_full_gate", "not_ci", "mypy_only_with_--mypy"] })}\n`);
  if (failed) process.exitCode = 1;
}
