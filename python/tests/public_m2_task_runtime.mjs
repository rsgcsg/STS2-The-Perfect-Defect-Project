// Actual Runtime/Port4/adapter/Evidence task seam; synthetic Connector and numerical Model.
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [repository, python, directory, profilePath, snapshotPath] = process.argv.slice(2);
const { PolicyRuntime, NdjsonPolicyPort, AgentRunEvidence } =
  await import(pathToFileURL(join(repository, "components/policy-runtime/dist/index.js")).href);
const { publicManifest, publicCapabilities, publicSnapshot } =
  await import(pathToFileURL(join(repository, "components/policy-runtime/test/fixtures/public-observation-fixture.mjs")).href);
await mkdir(directory, { recursive: true });
const manifest = publicManifest();
manifest.adapter.version = "1.1.0";
manifest.policy.version = "1.1.0";
manifest.support.interaction_kinds = ["combat_turn", "game_over"];
manifest.support.action_verbs = ["end_turn", "activate"];
manifest.adapter_config = JSON.parse(await readFile(profilePath, "utf8"));
const summary = JSON.parse(await readFile(snapshotPath, "utf8"));
const reference = publicSnapshot();
// The Python projection fixture is intentionally minimal. Fill the complete
// published Snapshot envelope for the actual Runtime and typed reader seam.
const terminalSnapshot = { ...reference, ...summary, sequence: reference.sequence + 1,
  observed_at: reference.observed_at, session: reference.session,
  interaction: { ...reference.interaction, ...summary.interaction },
  bound_actions: { ...reference.bound_actions, ...summary.bound_actions },
  completeness: { ...reference.completeness, ...summary.completeness },
  information_policy: { ...reference.information_policy, ...summary.information_policy }
};
let current = reference;
const manifestPath = join(directory, "manifest.json"), tracePath = join(directory, "trace.jsonl");
await writeFile(manifestPath, JSON.stringify(manifest));
const childPath = join(repository, "python/tests/public_m2_child.py");
const port = NdjsonPolicyPort.spawn(python, [childPath, manifestPath, tracePath], {
  cwd: join(repository, "python"), env: { ...process.env, OMP_NUM_THREADS: "2", MKL_NUM_THREADS: "2" }
});
const closed = new Promise(resolve => port.child.once("close", resolve));
let runtime, submitted = 0, released = 0, held = false;
try {
  const evidence = await AgentRunEvidence.create({ root: directory, runId: "public-summary-run",
    policyManifest: manifest, runtimeVersion: "fixture", runtimeCodeSha256: "d".repeat(64), mode: "auto" });
  await evidence.attestAdapter(await port.attest(manifest.adapter));
  const connector = {
    capabilities: async () => ({ ...publicCapabilities(), verbs: ["end_turn", "activate"] }),
    observeBundle: async () => ({ observation: structuredClone(current), reads: [] }),
    acquireController: async () => { held = true; },
    releaseController: async () => { released += 1; held = false; },
    submit: async args => {
      assert.equal(current.interaction.kind, "combat_turn", "summary task must not submit");
      submitted += 1;
      current = terminalSnapshot;
      return { protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1",
        request_id: args.requestId, delivery: "delivered",
        action: { bound_action_id: args.boundActionId, verb: "end_turn", subject_referent_id: null, arguments: [] },
        reason_code: null, detail: null, retry: { allowed: false, reason: "never" },
        successor: structuredClone(current), attribution: null };
    }
  };
  runtime = new PolicyRuntime({ manifest, connector, evidence, mode: "auto",
    runId: "public-summary-run", runtimeIdentity: { version: "fixture", code_sha256: "d".repeat(64) },
    publicStatefulPolicy: (input, signal, offered) => port.decideV4(input, signal, offered),
    statefulOfferBoundary: "port_write", autoBudget: { maxSubmissions: 10, maxPolicyCalls: 10, deadlineMs: 30000 },
    policyTimeoutMs: 10000 });
  assert.equal((await runtime.tick()).type, "delivered");
  assert.equal(held, true);
  const terminal = await runtime.tick();
  assert.equal(terminal.type, "not_executed", JSON.stringify(terminal));
  assert.equal(terminal.decision.disposition, "abstain");
  assert.equal(runtime.status().mode, "human");
  assert.equal(held, false);
  // A second ordinary Runtime tick is Human observation; there is no task loop
  // or automatic reauthorization and no second Model observation consumption.
  await runtime.tick();
  await runtime.stop();
  const trace = (await readFile(tracePath, "utf8")).trim().split("\n").map(JSON.parse);
  assert.equal(trace.length, 2);
  assert.deepEqual(trace[1].output.scores, [0, 0]);
  assert.equal(trace[1].advanced, true);
  assert.equal(trace[1].reset, false);
  assert.notEqual(trace[0].memory_sha256, trace[1].memory_sha256);
  const events = (await readFile(join(evidence.directory, "events.jsonl"), "utf8")).trim().split("\n").map(JSON.parse);
  assert.ok(events.some(event => event.kind === "handoff_to_human" && event.payload.reason === "policy_abstained"));
  process.stdout.write(JSON.stringify({ evidence: evidence.directory, submitted, released,
    terminal_submissions: submitted - 1, reason: "policy_abstained", mode: runtime.status().mode,
    step_calls: trace[1].step_calls, terminal_ordinal: trace[1].control.observation_ordinal,
    selected_index: trace[1].output.selected_index }) + "\n");
} finally {
  await runtime?.stop();
  port.close();
  await closed;
}
