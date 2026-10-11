// Existing production Runtime/Recorder emits synthetic evidence; no game or NN.
import { mkdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
const [runtimeIndex, repository, root, variant] = process.argv.slice(2);
const handoff = variant.startsWith("handoff-");
const legacyM0 = variant.startsWith("m0-legacy-");
const { PolicyRuntime, AgentRunEvidence } = await import(pathToFileURL(runtimeIndex).href);
const { publicManifest, publicCapabilities, publicSnapshot, publicCompletion } = await import(
  pathToFileURL(join(repository, "components/policy-runtime/test/fixtures/public-observation-fixture.mjs")).href);
const manifest = publicManifest();
manifest.adapter.id = "stpd-public-m2-decision-adapter";
manifest.adapter.version = "1.1.0";
manifest.policy.version = "1.1.0";
manifest.support.interaction_kinds = ["combat_turn", "game_over"];
manifest.support.action_verbs = ["end_turn", "activate"];
manifest.adapter_config.public_m2_installation = { task_spec: variant === "no-task" ? null : {
  schema: "stpd/public-m2-task-spec-v1", id: "ready-standard-summary-v1",
  input_schema: "sts2.player-environment/snapshot-1", terminal_observation: "normal-step-once-v1",
  abstention_scores: "program-zero-v1"
}};
if (variant === "wrong-task") manifest.adapter_config.public_m2_installation.task_spec.id = "other-task";
if (variant === "public-m0" || handoff || legacyM0) {
  manifest.adapter.id = "stpd-public-m0-decision-adapter";
  if (legacyM0) manifest.adapter.version = "1.2.0";
  manifest.adapter.protocol = "sts2.policy-runtime/decision-only-ndjson-1";
  manifest.adapter_config = { public_m0: { task_spec: {
    schema: "stpd/public-m0-task-spec-v1", id: "ready-standard-summary-v1",
    input_schema: "sts2.player-environment/snapshot-1", abstention_scores: "program-zero-v1"
  } } };
}
const snapshot = publicSnapshot(1, 1);
snapshot.interaction = { interaction_id: "summary", kind: "game_over", stage: "summary",
  content_schema: "sts2.player-environment/surface/game_over-1", capabilities: [],
  content: { context: { kind: "game_over", game_mode: "standard", result: "loss",
    score: 10, floor_reached: 1, ascension: 0 }, surface: { kind: "game_over", stage: "summary",
    can_return: true, can_advance_summary: false, return_destination: "timeline", other_controls: [] } }};
snapshot.bound_actions.actions[0] = { ...snapshot.bound_actions.actions[0], interaction_id: "summary", verb: "activate" };
const readySummary = structuredClone(snapshot);
if (legacyM0) Object.assign(snapshot, publicSnapshot(1, 1));
if (["ordinary-abstain", "budget"].includes(variant)) Object.assign(snapshot, publicSnapshot(1, 1));
if (handoff) {
  Object.assign(snapshot, publicSnapshot(1, 0));
  snapshot.status = "observed";
}
await mkdir(root, { recursive: true });
const runId = "outcome-" + variant;
const mode = ["budget", "conflicting-terminal"].includes(variant) ? "shadow" : "auto";
const evidence = await AgentRunEvidence.create({ root, runId, policyManifest: manifest,
  runtimeVersion: "synthetic-producer", runtimeCodeSha256: "d".repeat(64), mode });
await evidence.attestAdapter(manifest.adapter);
const connector = { capabilities: async () => ({ ...publicCapabilities(), verbs: ["end_turn", "activate"] }),
  observeBundle: async () => ({ observation: structuredClone(snapshot), reads: [] }),
  acquireController: async () => {}, releaseController: async () => {},
  submit: async args => {
    if (!legacyM0 && !["handoff-continued", "handoff-tainted"].includes(variant)) throw new Error("fixture must never submit");
    const unknown = variant === "handoff-tainted";
    if (legacyM0) Object.assign(snapshot, structuredClone(readySummary), {
      snapshot_id: "snapshot-" + (snapshot.sequence + 1), sequence: snapshot.sequence + 1
    });
    else Object.assign(snapshot, publicSnapshot(2, 1));
    return { protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1",
      request_id: args.requestId, delivery: unknown ? "unknown" : "delivered",
      action: { bound_action_id: args.boundActionId, verb: "end_turn", subject_referent_id: null, arguments: [] },
      reason_code: unknown ? "synthetic_unknown_delivery" : null, detail: null,
      retry: { allowed: false, reason: "never" }, successor: unknown ? null : structuredClone(snapshot), attribution: null };
  } };
const runtime = new PolicyRuntime({ manifest, connector, evidence, mode, runId,
  runtimeIdentity: { version: "synthetic-producer", code_sha256: "d".repeat(64) },
  autoBudget: { maxSubmissions: 2, maxPolicyCalls: legacyM0 ? 4 : ["budget", "handoff-budget"].includes(variant) ? 1 : 2, deadlineMs: 30000 },
  policy: async input => ({ candidate_digest: input.candidate_digest,
    scores: [["handoff-continued", "handoff-tainted"].includes(variant)
      || legacyM0 && input.bundle.observation.interaction.kind !== "game_over" ? 1 : 0],
    selected_index: ["handoff-continued", "handoff-tainted"].includes(variant)
      || legacyM0 && input.bundle.observation.interaction.kind !== "game_over" ? 0 : null }),
  publicStatefulPolicy: async input => ({ output: { candidate_digest: input.decision.candidate_digest,
    scores: [0], selected_index: null }, completion: publicCompletion(input) }) });
if (variant !== "no-input") await runtime.tick();
if (legacyM0) await runtime.tick();
if (variant === "budget") await runtime.tick();
if (variant === "conflicting-terminal") {
  snapshot.snapshot_id = "snapshot-2";
  snapshot.sequence = 2;
  snapshot.interaction.content.context.result = "win";
  await runtime.tick();
}
if (variant === "cleanup-failure") await evidence.append("fail_closed", { reason: "synthetic_post_task_cleanup_failure" });
if (variant === "m0-legacy-cleanup-failure") await evidence.append("fail_closed", {
  reason: "synthetic_post_task_cleanup_failure"
});
if (variant === "m0-legacy-later-mode" || variant === "m0-legacy-later-submission") {
  await runtime.setMode("auto");
  if (variant === "m0-legacy-later-submission") {
    Object.assign(snapshot, publicSnapshot(3, 1));
    await runtime.tick();
  }
}
if (["handoff-continued", "handoff-resumed-no-step", "handoff-tainted", "handoff-two-epochs", "handoff-budget"].includes(variant)) {
  Object.assign(snapshot, publicSnapshot(1, 1));
  await runtime.setMode(variant === "handoff-budget" ? "shadow" : "auto");
  if (variant !== "handoff-resumed-no-step") await runtime.tick();
  if (variant === "handoff-budget") await runtime.tick();
}
if (variant === "handoff-failed") await evidence.append("fail_closed", { reason: "synthetic_post_handoff_cleanup_failure" });
if (variant === "handoff-cleanup") await evidence.append("controller_released", {});
if (variant === "handoff-substantive") await evidence.append("one_step_completed", {});
if (variant === "handoff-unknown") await evidence.append("future_substantive_record", {});
await runtime.stop();
let directory = evidence.directory;
if (legacyM0 && !["m0-legacy-summary", "m0-legacy-cleanup-failure", "m0-legacy-later-mode",
  "m0-legacy-later-submission"].includes(variant)) {
  // Counterexamples use a fresh instance of the production Recorder. The
  // original Runtime-produced sealed run stays immutable; only copied synthetic
  // payloads/order vary. No checksum handwritten by the application reader.
  const events = (await readFile(join(directory, "events.jsonl"), "utf8")).trim().split("\n").map(JSON.parse);
  const policy = structuredClone(manifest);
  const finalDecision = events.findLast(event => event.kind === "decision");
  const summaryBodies = events.filter(event => ["receipt", "successor"].includes(event.kind))
    .map(event => event.kind === "successor" ? event.payload.successor : event.payload.receipt.successor);
  if (variant === "m0-legacy-win") for (const body of summaryBodies) body.interaction.content.context.result = "win";
  if (variant === "m0-legacy-timestamp-only") summaryBodies.at(-1).observed_at = "2026-10-11T00:00:01.000Z";
  if (variant === "m0-legacy-conflicting-body") summaryBodies.at(-1).interaction.content.context.score += 1;
  if (variant === "m0-legacy-conflicting-catalog") summaryBodies.at(-1).bound_actions.actions[0].bound_action_id = "different-candidate";
  if (variant === "m0-legacy-conflicting-sequence") summaryBodies.at(-1).sequence += 1;
  if (variant === "m0-legacy-incomplete") for (const body of summaryBodies) body.completeness.status = "partial";
  if (variant === "m0-legacy-missing-field") for (const body of summaryBodies) body.completeness.missing = ["surface"];
  if (variant === "m0-legacy-partial-catalog") for (const body of summaryBodies) {
    body.status = "observed";
    body.bound_actions.status = "truncated";
    body.bound_actions.total_count = 2;
  }
  if (variant === "m0-legacy-noninteractive") for (const body of summaryBodies) {
    body.status = "observed";
    Object.assign(body.bound_actions, { actions: [], materialized_count: 0, total_count: 0 });
  }
  if (variant === "m0-legacy-wrong-schema") for (const body of summaryBodies) body.schema = "foreign/snapshot-1";
  if (variant === "m0-legacy-not-ready") for (const body of summaryBodies) {
    body.interaction.stage = "intro";
    body.interaction.content.surface.stage = "intro";
  }
  if (variant === "m0-legacy-nonzero") finalDecision.payload.decision.scores = [0.5];
  if (variant === "m0-legacy-selected") {
    finalDecision.payload.decision.scores = [1];
    finalDecision.payload.decision.selected_index = 0;
    finalDecision.payload.decision.disposition = "admit";
    finalDecision.payload.resolved_bound_action_id = "candidate-0";
  }
  if (variant === "m0-legacy-wrong-snapshot") finalDecision.payload.decision.snapshot_id = "unrecorded-summary";
  if (variant === "m0-legacy-wrong-digest") finalDecision.payload.decision.candidate_digest = "f".repeat(64);
  if (variant === "m0-legacy-wrong-count") {
    finalDecision.payload.decision.candidate_count = 2;
    finalDecision.payload.decision.scores = [0, 0];
  }
  if (variant === "m0-legacy-wrong-task") policy.adapter_config.public_m0.task_spec.id = "unrecognized-task";
  if (variant === "m0-legacy-no-task") policy.adapter_config.public_m0.task_spec = null;
  if (variant === "m0-legacy-wrong-adapter") policy.adapter.id = "unrecognized-m0-adapter";
  if (variant === "m0-legacy-wrong-port") policy.adapter.protocol = "sts2.policy-runtime/decision-only-ndjson-2";
  if (["m0-legacy-foreign-runtime", "m0-legacy-foreign-environment"].includes(variant)) {
    const admitted = structuredClone(events.find(event => event.kind === "environment_admitted"));
    const field = variant === "m0-legacy-foreign-runtime" ? "runtime_instance_id" : "environment_fingerprint";
    admitted.payload.environment[field] = "foreign-owner";
    events.splice(events.indexOf(finalDecision), 0, admitted);
  }
  if (variant === "m0-legacy-future-only") {
    const bodies = events.filter(event => ["receipt", "successor"].includes(event.kind));
    for (const body of bodies) events.splice(events.indexOf(body), 1);
    events.splice(events.indexOf(finalDecision) + 1, 0, ...bodies);
  }
  if (variant === "m0-legacy-no-handoff") events.splice(events.findIndex(event => event.kind === "handoff_to_human"), 1);
  if (variant === "m0-legacy-wrong-handoff") events.find(event => event.kind === "handoff_to_human").payload.reason = "operator_requested_stop";
  if (variant === "m0-legacy-later-decision") {
    const later = structuredClone(finalDecision);
    later.payload.decision.decision_id += "-later";
    events.splice(-1, 0, later);
  }
  if (variant === "m0-legacy-substantive-tail") events.splice(-1, 0, { kind: "one_step_completed", payload: {} });
  if (variant === "m0-legacy-unknown-tail") events.splice(-1, 0, { kind: "future_substantive_record", payload: {} });
  if (variant === "m0-legacy-budget-stop") Object.assign(events.at(-1).payload.autonomy_budget, {
    state: "exhausted", max_policy_calls: events.at(-1).payload.autonomy_budget.policy_calls_used,
    exhausted_reason: "policy_call_limit", ended_reason: null
  });
  if (variant === "m0-legacy-tainted-cleanup") events.splice(-1, 0, {
    kind: "runtime_tainted", payload: { reason: "synthetic_post_task_cleanup_uncertain", retry: false }
  });
  if (variant === "m0-legacy-no-stop") events.pop();
  const counterexampleRoot = join(root, "counterexample");
  await mkdir(counterexampleRoot);
  const counterexample = await AgentRunEvidence.create({ root: counterexampleRoot, runId,
    policyManifest: policy, runtimeVersion: "synthetic-producer", runtimeCodeSha256: "d".repeat(64), mode });
  await counterexample.attestAdapter(policy.adapter);
  for (const event of events) await counterexample.append(event.kind, event.payload, event.recorded_at);
  const tainted = ["m0-legacy-tainted-cleanup", "m0-legacy-tainted-manifest"].includes(variant);
  await counterexample.finalize({ status: tainted ? "tainted" : "stopped", tainted, mode: "human" });
  directory = counterexample.directory;
}
process.stdout.write(JSON.stringify({ directory, runId }) + "\n");
