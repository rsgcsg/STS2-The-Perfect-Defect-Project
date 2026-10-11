// Existing production Runtime/Recorder emits synthetic evidence; no game or NN.
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
const [runtimeIndex, repository, root, variant] = process.argv.slice(2);
const handoff = variant.startsWith("handoff-");
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
if (variant === "public-m0" || handoff) {
  manifest.adapter.id = "stpd-public-m0-decision-adapter";
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
    if (!["handoff-continued", "handoff-tainted"].includes(variant)) throw new Error("fixture must never submit");
    const unknown = variant === "handoff-tainted";
    Object.assign(snapshot, publicSnapshot(2, 1));
    return { protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1",
      request_id: args.requestId, delivery: unknown ? "unknown" : "delivered",
      action: { bound_action_id: args.boundActionId, verb: "end_turn", subject_referent_id: null, arguments: [] },
      reason_code: unknown ? "synthetic_unknown_delivery" : null, detail: null,
      retry: { allowed: false, reason: "never" }, successor: unknown ? null : structuredClone(snapshot), attribution: null };
  } };
const runtime = new PolicyRuntime({ manifest, connector, evidence, mode, runId,
  runtimeIdentity: { version: "synthetic-producer", code_sha256: "d".repeat(64) },
  autoBudget: { maxSubmissions: 2, maxPolicyCalls: ["budget", "handoff-budget"].includes(variant) ? 1 : 2, deadlineMs: 30000 },
  policy: async input => ({ candidate_digest: input.candidate_digest,
    scores: [["handoff-continued", "handoff-tainted"].includes(variant) ? 1 : 0],
    selected_index: ["handoff-continued", "handoff-tainted"].includes(variant) ? 0 : null }),
  publicStatefulPolicy: async input => ({ output: { candidate_digest: input.decision.candidate_digest,
    scores: [0], selected_index: null }, completion: publicCompletion(input) }) });
if (variant !== "no-input") await runtime.tick();
if (variant === "budget") await runtime.tick();
if (variant === "conflicting-terminal") {
  snapshot.snapshot_id = "snapshot-2";
  snapshot.sequence = 2;
  snapshot.interaction.content.context.result = "win";
  await runtime.tick();
}
if (variant === "cleanup-failure") await evidence.append("fail_closed", { reason: "synthetic_post_task_cleanup_failure" });
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
process.stdout.write(JSON.stringify({ directory: evidence.directory, runId }) + "\n");
