import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { PolicyRuntime, POLICY_RUNTIME_VERSION, startPolicyRuntimeHttpServer } from "@rsgcsg/sts2-policy-runtime";

const manifest = {
  schema: "sts2.policy-runtime/policy-manifest-1", manifest_id: "installed-cpu-smoke",
  policy: { id: "fixture", version: "1", provider: "fixture", architecture: "fixture" },
  adapter: { id: "fixture", version: "1", protocol: "sts2.policy-runtime/decision-only-ndjson-1", code_sha256: "c".repeat(64) },
  artifact: { id: "fixture", path: "artifact.bin", sha256: createHash("sha256").update("synthetic").digest("hex") },
  representation: { id: "fixture", version: "1", input_schema: "sts2.player-environment/snapshot-1" },
  requirements: { connector_protocol_version: "1.0.0", environment: { host_kind: "test", connector_version: "fixture", connector_source_revision: "source", connector_artifact_sha256: "b".repeat(64), connector_module_version_id: "mvid", modset_status: "exact", modset_fingerprint: "modset", loaded_mod_ids: [] }, reads: [], whole_decision_admission: true, candidate_order_digest: "sha256-json-bound-action-id-order", score_count_matches_candidate_count: true, selected_index: true, successor_required: true },
  support: { game_versions: ["fixture"], game_commits: ["fixture"], interaction_kinds: ["test"], action_verbs: ["end_turn"] }, adapter_config: {},
  claims: { full_run: false, selector: false, catalog_filtered: false, creates_action_authority: false, creates_native_operands: false }
};
let sequence = 1;
let submits = 0;
let held = false;
let slowNext = false;
let resolveSlowEntered;
const slowEntered = new Promise((resolve) => { resolveSlowEntered = resolve; });
let resolveSlowDecision;
const slowDecision = new Promise((resolve) => { resolveSlowDecision = resolve; });
const snapshot = () => ({ snapshot_id: `snapshot-${sequence}`, sequence, status: "interactive", session: { runtime_instance_id: "fixture-runtime", environment_fingerprint: "fixture-env" }, completeness: { status: "complete" }, interaction: { kind: "test" }, bound_actions: { status: "complete", total_count: 1, materialized_count: 1, actions: [{ bound_action_id: `action-${sequence}`, verb: "end_turn", label: "End turn" }] } });
const connector = {
  async capabilities() { return { protocol_version: "1.0.0", host: { host_kind: "test", version: "fixture", runtime_instance_id: "fixture-runtime", implementation: { source_revision: "source", artifact_sha256: "b".repeat(64), module_version_id: "mvid" } }, game: { version: "fixture", commit: "fixture", modset: { status: "exact", fingerprint: "modset", loaded_mod_ids: [] } }, environment_fingerprint: "fixture-env", execution_available: true, single_controller: true }; },
  async observeBundle() { return { observation: snapshot(), reads: [] }; },
  async acquireController() { held = true; }, async releaseController() { held = false; },
  async submit(input) { assert.ok(held); submits++; sequence++; return { request_id: input.requestId, action: { bound_action_id: input.boundActionId }, delivery: "delivered", successor: snapshot() }; }
};
const runtime = new PolicyRuntime({ manifest, connector, autoBudget: { maxSubmissions: 4, maxPolicyCalls: 8, deadlineMs: 10_000 }, policy: (input) => {
  if (slowNext) {
    slowNext = false;
    resolveSlowEntered();
    return slowDecision;
  }
  return { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 };
} });
const server = await startPolicyRuntimeHttpServer(runtime);
async function post(address, route, body, runId = runtime.status().run_id) { const response = await fetch(`${address}/v2/${route}`, { method: "POST", headers: { "content-type": "application/json", "x-sts2-policy-run-id": runId }, body: JSON.stringify(body) }); assert.equal(response.status, 200); return response.json(); }
try {
  const bindingResponse = await fetch(`${server.address}/v2/environment`);
  assert.equal(bindingResponse.status, 200);
  const binding = await bindingResponse.json();
  assert.deepEqual(binding, { schema: "sts2.policy-runtime/environment-1", run_id: runtime.status().run_id,
    runtime_instance_id: "fixture-runtime", recovery_epoch: 0 });
  assert.equal(runtime.status().environment, null);
  await post(server.address, "mode", { mode: "human" });
  const stale = await fetch(`${server.address}/v2/mode`, { method: "POST", headers: {
    "content-type": "application/json", "x-sts2-policy-run-id": binding.run_id,
    "x-sts2-game-instance-id": binding.runtime_instance_id, "x-sts2-recovery-epoch": String(binding.recovery_epoch)
  }, body: JSON.stringify({ mode: "auto" }) });
  assert.equal(stale.status, 409);
  assert.deepEqual(await stale.json(), { schema: "sts2.policy-runtime/http-2", error: "runtime_recovery_epoch_mismatch" });
  assert.equal(runtime.status().mode, "human"); assert.equal(submits, 0);
  await post(server.address, "mode", { mode: "shadow" });
  assert.equal((await post(server.address, "tick", { max_ticks: 1 })).results[0].type, "shadow");
  assert.equal(submits, 0); assert.equal(held, false);
  await post(server.address, "mode", { mode: "one_step" });
  assert.equal((await post(server.address, "tick", { max_ticks: 1 })).results[0].type, "delivered");
  assert.equal(runtime.status().mode, "human"); assert.equal(held, false);
  await post(server.address, "mode", { mode: "auto" });
  const budgetRun = await post(server.address, "tick", { max_ticks: 10 });
  assert.equal(submits, 5);
  assert.equal(runtime.status().autonomy_budget.submissions_used, 4);
  assert.equal(runtime.status().autonomy_budget.exhausted_reason, "submission_attempt_limit");
  assert.equal(runtime.status().mode, "human");
  assert.ok(budgetRun.results.some((result) => result.reason === "autonomy_budget_exhausted"));
  await post(server.address, "mode", { mode: "auto" });
  slowNext = true;
  const slowTick = post(server.address, "tick", { max_ticks: 1 });
  await slowEntered;
  const recoveryStarted = Date.now();
  await post(server.address, "mode", { mode: "human" });
  assert.ok(Date.now() - recoveryStarted < 1000, "Human recovery waited for the policy timeout");
  resolveSlowDecision({ candidate_digest: "", scores: [1], selected_index: 0 });
  const slowResult = await slowTick;
  assert.equal(slowResult.results[0].reason, "runtime_recovery_epoch_mismatch");
  assert.equal(runtime.status().controller, "released");
  await post(server.address, "stop", {}); assert.equal(held, false);
} finally { resolveSlowDecision({ candidate_digest: "", scores: [1], selected_index: 0 }); await server.close(); }

// Launch the actual installed CLI in Human mode. It never contacts a game.
await writeFile("artifact.bin", "synthetic");
await writeFile("manifest.json", JSON.stringify(manifest));
await writeFile("adapter.mjs", `process.stdout.write(JSON.stringify({schema:'sts2.policy-runtime/policy-port-1',message_type:'ready',adapter:${JSON.stringify(manifest.adapter)}})+'\\n'); process.stdin.resume();`);
const installedEntry = fileURLToPath(import.meta.resolve("@rsgcsg/sts2-policy-runtime"));
const installedPackage = JSON.parse(await readFile(new URL("../package.json", import.meta.resolve("@rsgcsg/sts2-policy-runtime")), "utf8"));
assert.equal(POLICY_RUNTIME_VERSION, installedPackage.version, "runtime and package versions differ");
const cli = new URL("../bin/policy-runtime.mjs", import.meta.resolve("@rsgcsg/sts2-policy-runtime"));
const reservation = createServer();
await new Promise((resolve) => reservation.listen(0, "127.0.0.1", resolve));
const cliPort = reservation.address().port;
await new Promise((resolve) => reservation.close(resolve));
const child = spawn(process.execPath, [fileURLToPath(cli), "--manifest", "manifest.json", "--adapter-command", process.execPath, "--adapter-arg", "adapter.mjs", "--evidence-root", "evidence", "--listen-port", String(cliPort)], { stdio: ["ignore", "pipe", "pipe"] });
const childExit = new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal })));
let stderr = ""; child.stderr.on("data", (chunk) => { stderr += chunk; });
const lines = createInterface({ input: child.stdout });
try {
  const startup = await Promise.race([
    new Promise((resolve, reject) => { lines.once("line", (line) => { try { resolve(JSON.parse(line)); } catch (error) { reject(error); } }); child.once("exit", () => reject(new Error(stderr || "CLI exited before startup"))); }),
    new Promise((_, reject) => { const timer = setTimeout(() => reject(new Error("CLI startup timeout")), 5000); timer.unref(); })
  ]);
  assert.equal(startup.schema, "sts2.policy-runtime/startup-1");
  assert.equal(startup.runtime_version, POLICY_RUNTIME_VERSION);
  assert.deepEqual(startup.autonomy_budget, { maxSubmissions: 16, maxPolicyCalls: 32, deadlineMs: 60000 });
  assert.equal((await (await fetch(`${startup.address}/status`)).json()).status.mode, "human");
  await post(startup.address, "stop", {}, startup.run_id);
  const exitResult = await Promise.race([childExit, new Promise((_, reject) => { const timer = setTimeout(() => reject(new Error("CLI did not exit after POST /v2/stop")), 5000); timer.unref(); })]);
  assert.deepEqual(exitResult, { code: 0, signal: null });
  const sealed = JSON.parse(await readFile(`evidence/${startup.run_id}/evidence-manifest.json`, "utf8"));
  assert.equal(sealed.complete, true);
} finally {
  lines.close();
  if (child.exitCode === null && child.signalCode === null) {
    child.kill("SIGTERM"); await childExit;
  }
}
console.log(JSON.stringify({ imported_package: installedEntry.includes("node_modules"), version: POLICY_RUNTIME_VERSION, environment_recovery_fence: true, slow_recovery_during_unresolved_policy: true, shadow_submissions: 0, synthetic_deliveries: submits, installed_cli_started_sealed_and_exited: true, game_contact: false }));
