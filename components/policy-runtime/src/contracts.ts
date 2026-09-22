import type {
  DecodedPlayerPayload,
  PlayerEnvironmentBoundAction,
  PlayerEnvironmentCapabilities,
  PlayerEnvironmentReadResponse,
  PlayerEnvironmentReceipt,
  PlayerEnvironmentSnapshot
} from "@rsgcsg/sts2-connector-client";

export const POLICY_MANIFEST_SCHEMA = "sts2.policy-runtime/policy-manifest-1" as const;
export const POLICY_DECISION_SCHEMA = "sts2.policy-runtime/decision-1" as const;
export const AGENT_RUN_SCHEMA = "sts2.policy-runtime/agent-run-1" as const;
export const POLICY_PORT_SCHEMA = "sts2.policy-runtime/policy-port-1" as const;
export const EVIDENCE_MANIFEST_SCHEMA = "sts2.policy-runtime/immutable-evidence-manifest-1" as const;
export const POLICY_RUNTIME_VERSION = "0.1.0-rc.8" as const;
export const RUNTIME_ENVIRONMENT_SCHEMA = "sts2.policy-runtime/environment-1" as const;

/** Read-only observation used by control clients before preparing a command. */
export interface RuntimeEnvironmentBinding {
  schema: typeof RUNTIME_ENVIRONMENT_SCHEMA;
  run_id: string;
  runtime_instance_id: string;
  recovery_epoch: number;
}

/** Additive control preconditions; legacy callers may omit both. */
export interface RuntimeControlPreconditions {
  gameInstanceId?: string;
  recoveryEpoch?: number;
}

export type RuntimeMode = "human" | "shadow" | "one_step" | "auto";
export type DecisionDisposition = "admit" | "abstain";

/**
 * A finite wallet for one Shadow/Auto authorization.  The limits are owned by
 * the Runtime, not by an HTTP request or a background worker.
 */
export interface AutonomyBudgetConfig {
  maxSubmissions: number;
  maxPolicyCalls: number;
  deadlineMs: number;
}

export const DEFAULT_AUTONOMY_BUDGET: AutonomyBudgetConfig = Object.freeze({
  maxSubmissions: 16,
  maxPolicyCalls: 32,
  deadlineMs: 60_000
});

export type AutonomyBudgetExhaustionReason = "submission_attempt_limit" | "policy_call_limit" | "deadline";
export type AutonomyBudgetEndReason = "human_recovery" | "mode_changed" | "stopped";
export type AutonomyBudgetState = "inactive" | "active" | "exhausted";

/** Identity only. A Manifest never carries a per-decision catalog or Agent Run mode. */
export interface PolicyManifest {
  schema: typeof POLICY_MANIFEST_SCHEMA;
  manifest_id: string;
  policy: { id: string; version: string; provider: string; architecture: string };
  adapter: { id: string; version: string; protocol: "sts2.policy-runtime/decision-only-ndjson-1"; code_sha256: string };
  artifact: { id: string; path: string; sha256: string };
  representation: { id: string; version: string; input_schema: "sts2.player-environment/snapshot-1" };
  requirements: {
    connector_protocol_version: string;
    environment: {
      host_kind: PlayerEnvironmentCapabilities["host"]["host_kind"];
      connector_version: string;
      connector_source_revision: string;
      connector_artifact_sha256: string;
      connector_module_version_id: string;
      modset_status: string;
      modset_fingerprint: string;
      loaded_mod_ids: string[];
    };
    reads: string[];
    whole_decision_admission: true;
    candidate_order_digest: "sha256-json-bound-action-id-order";
    score_count_matches_candidate_count: true;
    selected_index: true;
    successor_required: true;
  };
  support: { game_versions: string[]; game_commits: string[]; interaction_kinds: string[]; action_verbs: string[] };
  adapter_config: Record<string, unknown>;
  claims: { full_run: boolean; selector: boolean; catalog_filtered: false; creates_action_authority: false; creates_native_operands: false };
}

/** The only decision object emitted by the runtime. It contains no catalog or bound action. */
export interface PolicyDecision {
  schema: typeof POLICY_DECISION_SCHEMA;
  decision_id: string;
  run_id: string;
  manifest_id: string;
  snapshot_id: string;
  candidate_digest: string;
  candidate_count: number;
  scores: number[];
  selected_index: number | null;
  disposition: DecisionDisposition;
  issued_at: string;
}

export interface AgentRunManifest {
  schema: typeof AGENT_RUN_SCHEMA;
  run_id: string;
  manifest_id: string;
  policy_manifest_sha256: string;
  policy_id: string;
  policy_version: string;
  policy_artifact_sha256: string;
  runtime_version: string;
  runtime_code_sha256: string;
  started_at: string;
  ended_at: string | null;
  status: "running" | "completed" | "stopped" | "tainted";
  mode: RuntimeMode;
  tainted: boolean;
  append_only: true;
}

export interface DecisionBundle { observation: PlayerEnvironmentSnapshot; reads: PlayerEnvironmentReadResponse[] }

/** Decision-only adapter output. The adapter never returns a catalog or action. */
export interface AdapterDecision { candidate_digest: string; scores: number[]; selected_index: number | null }

export interface PolicyDecisionInput {
  run_id: string;
  manifest: PolicyManifest;
  bundle: DecisionBundle;
  candidate_digest: string;
  candidate_count: number;
}

/**
 * A policy receives a recovery signal for the current decision attempt. Policy
 * implementations should stop work when it is aborted; the Runtime also
 * fences and ignores the result when a policy cannot cancel in-process.
 */
export type Policy = (input: PolicyDecisionInput, signal?: AbortSignal) => Promise<AdapterDecision> | AdapterDecision;

export interface PolicyPortDecisionRequest { schema: typeof POLICY_PORT_SCHEMA; message_type: "decide"; request_id: string; input: PolicyDecisionInput }
export interface PolicyPortReadyResponse { schema: typeof POLICY_PORT_SCHEMA; message_type: "ready"; adapter: PolicyManifest["adapter"] }
export interface PolicyPortDecisionResponse { schema: typeof POLICY_PORT_SCHEMA; message_type: "decision"; request_id: string; output: AdapterDecision }
export interface PolicyPortErrorResponse { schema: typeof POLICY_PORT_SCHEMA; message_type: "error"; request_id: string; error: { code: string; message: string } }

export interface PolicyConnector {
  capabilities(options?: { fresh?: boolean }): Promise<PlayerEnvironmentCapabilities>;
  observeBundle(requiredReadKinds: readonly string[]): Promise<DecisionBundle>;
  acquireController(): Promise<void>;
  releaseController(): Promise<void>;
  submit(input: { requestId: string; expectedSnapshotId: string; boundActionId: string }): Promise<PlayerEnvironmentReceipt>;
}

export type RuntimeCommand =
  | { type: "set_mode"; mode: RuntimeMode }
  | { type: "tick" }
  | { type: "status" }
  | { type: "stop" };

export interface RuntimeStatus {
  schema: "sts2.policy-runtime/status-1";
  runtime: { version: string; code_sha256: string | null };
  policy: { manifest_id: string; policy_id: string; policy_version: string; provider: string; architecture: string; artifact_sha256: string };
  run_id: string;
  lifecycle: "running" | "stopped";
  mode: RuntimeMode;
  controller: "held" | "released";
  autonomy_budget: {
    state: AutonomyBudgetState;
    max_submissions: number;
    submissions_used: number;
    max_policy_calls: number;
    policy_calls_used: number;
    deadline_ms: number;
    elapsed_ms: number;
    remaining_ms: number;
    exhausted_reason: AutonomyBudgetExhaustionReason | null;
    ended_reason: AutonomyBudgetEndReason | null;
  };
  tainted: boolean;
  taint_reason: string | null;
  refreshing: boolean;
  last_snapshot_id: string | null;
  last_snapshot: { snapshot_id: string; sequence: number; status: PlayerEnvironmentSnapshot["status"]; runtime_instance_id: string; environment_fingerprint: string } | null;
  last_decision: { decision_id: string; candidate_digest: string; candidate_count: number; scores: number[]; selected_index: number | null; bound_action_id: string | null; bound_action_label: string | null } | null;
  last_receipt: { request_id: string; delivery: PlayerEnvironmentReceipt["delivery"]; reason_code: string | null; successor_snapshot_id: string | null } | null;
  reads: { read_id: string; kind: string; content_schema: string; target_referent_id: string | null }[];
  invalidations: string[];
  errors: string[];
  environment: {
    runtime_instance_id: string;
    environment_fingerprint: string;
    host_kind: PlayerEnvironmentCapabilities["host"]["host_kind"];
    connector_protocol_version: string;
    connector_version: string;
    connector_source_revision: string | null;
    connector_artifact_sha256: string | null;
    connector_module_version_id: string | null;
    game_version: string | null;
    game_commit: string | null;
    modset_status: string;
    modset_fingerprint: string;
    loaded_mod_ids: string[];
  } | null;
}

export type TickResult =
  | { type: "human"; status: RuntimeStatus }
  | { type: "not_admitted"; reason: string; status: RuntimeStatus }
  | { type: "shadow"; decision: PolicyDecision; status: RuntimeStatus }
  | { type: "not_executed"; decision: PolicyDecision; status: RuntimeStatus }
  | { type: "delivered"; decision: PolicyDecision; bound_action: PlayerEnvironmentBoundAction; receipt: PlayerEnvironmentReceipt; successor: PlayerEnvironmentSnapshot; status: RuntimeStatus }
  | { type: "not_delivered"; decision: PolicyDecision; receipt: PlayerEnvironmentReceipt; status: RuntimeStatus }
  | { type: "unknown"; decision: PolicyDecision | null; receipt: PlayerEnvironmentReceipt | null; error: string; status: RuntimeStatus };

export type ApplicationResult =
  | { type: "status"; status: RuntimeStatus }
  | { type: "mode_changed"; status: RuntimeStatus }
  | { type: "tick"; result: TickResult }
  | { type: "stopped"; status: RuntimeStatus };

export interface EvidenceFileEntry { path: string; bytes: number; sha256: string }
export interface ImmutableEvidenceManifest { schema: typeof EVIDENCE_MANIFEST_SCHEMA; run_id: string; complete: true; append_only: true; files: EvidenceFileEntry[]; manifest_sha256: string }

export interface ConnectorAdapterClient {
  capabilities(): Promise<DecodedPlayerPayload<PlayerEnvironmentCapabilities>>;
  observe(): Promise<DecodedPlayerPayload<PlayerEnvironmentSnapshot>>;
  read(readId: string, expectedSnapshotId: string): Promise<DecodedPlayerPayload<PlayerEnvironmentReadResponse>>;
  submit(input: { requestId: string; expectedSnapshotId: string; boundActionId: string; clientSessionId: string; controllerLeaseId: string; controllerGeneration: number }): Promise<DecodedPlayerPayload<PlayerEnvironmentReceipt>>;
  registerClient(input: { clientInstanceId: string; productId: string; productName: string; productVersion: string }): Promise<DecodedPlayerPayload<{ runtime_instance_id: string; client: { client_session_id: string; client_instance_id: string } }>>;
  acquireController(clientSessionId: string): Promise<DecodedPlayerPayload<{ runtime_instance_id: string; controller?: { controller_lease_id: string; controller_generation: number; client_session_id: string; expires_at: string } | null }>>;
  renewController(input: { clientSessionId: string; controllerLeaseId: string; controllerGeneration: number }): Promise<unknown>;
  releaseController(input: { clientSessionId: string; controllerLeaseId: string; controllerGeneration: number }): Promise<unknown>;
}

export function validatePolicyManifest(value: unknown): PolicyManifest {
  const root = object(value, "Policy Manifest");
  exactKeys(root, ["schema", "manifest_id", "policy", "adapter", "artifact", "representation", "requirements", "support", "adapter_config", "claims"]);
  literal(root, "schema", POLICY_MANIFEST_SCHEMA); nonEmpty(root, "manifest_id");
  const policy = object(root.policy, "manifest.policy"); exactKeys(policy, ["id", "version", "provider", "architecture"]); nonEmpty(policy, "id"); nonEmpty(policy, "version"); nonEmpty(policy, "provider"); nonEmpty(policy, "architecture");
  const adapter = object(root.adapter, "manifest.adapter"); exactKeys(adapter, ["id", "version", "protocol", "code_sha256"]); nonEmpty(adapter, "id"); nonEmpty(adapter, "version"); literal(adapter, "protocol", "sts2.policy-runtime/decision-only-ndjson-1"); sha256Field(adapter, "code_sha256");
  const artifact = object(root.artifact, "manifest.artifact"); exactKeys(artifact, ["id", "path", "sha256"]); nonEmpty(artifact, "id"); nonEmpty(artifact, "path"); sha256Field(artifact, "sha256");
  const representation = object(root.representation, "manifest.representation"); exactKeys(representation, ["id", "version", "input_schema"]); nonEmpty(representation, "id"); nonEmpty(representation, "version"); literal(representation, "input_schema", "sts2.player-environment/snapshot-1");
  const requirements = object(root.requirements, "manifest.requirements"); exactKeys(requirements, ["connector_protocol_version", "environment", "reads", "whole_decision_admission", "candidate_order_digest", "score_count_matches_candidate_count", "selected_index", "successor_required"]); nonEmpty(requirements, "connector_protocol_version");
  const environment = object(requirements.environment, "manifest.requirements.environment"); exactKeys(environment, ["host_kind", "connector_version", "connector_source_revision", "connector_artifact_sha256", "connector_module_version_id", "modset_status", "modset_fingerprint", "loaded_mod_ids"]); enumField(environment, "host_kind", ["live_ui", "headless", "replay", "test"]); nonEmpty(environment, "connector_version"); nonEmpty(environment, "connector_source_revision"); sha256Field(environment, "connector_artifact_sha256"); nonEmpty(environment, "connector_module_version_id"); nonEmpty(environment, "modset_status"); nonEmpty(environment, "modset_fingerprint"); stringArray(environment, "loaded_mod_ids"); uniqueStringArray(environment, "loaded_mod_ids");
  stringArray(requirements, "reads"); uniqueStringArray(requirements, "reads"); literal(requirements, "whole_decision_admission", true); literal(requirements, "candidate_order_digest", "sha256-json-bound-action-id-order"); literal(requirements, "score_count_matches_candidate_count", true); literal(requirements, "selected_index", true); literal(requirements, "successor_required", true);
  const support = object(root.support, "manifest.support"); exactKeys(support, ["game_versions", "game_commits", "interaction_kinds", "action_verbs"]); nonEmptyUniqueStringArray(support, "game_versions"); nonEmptyUniqueStringArray(support, "game_commits"); nonEmptyUniqueStringArray(support, "interaction_kinds"); nonEmptyUniqueStringArray(support, "action_verbs");
  object(root.adapter_config, "manifest.adapter_config");
  const claims = object(root.claims, "manifest.claims"); exactKeys(claims, ["full_run", "selector", "catalog_filtered", "creates_action_authority", "creates_native_operands"]); booleanField(claims, "full_run"); booleanField(claims, "selector"); literal(claims, "catalog_filtered", false); literal(claims, "creates_action_authority", false); literal(claims, "creates_native_operands", false);
  return root as unknown as PolicyManifest;
}

export function validateAdapterDecision(value: unknown, expectedDigest: string, expectedCount: number): AdapterDecision {
  const output = object(value, "Adapter Decision"); exactKeys(output, ["candidate_digest", "scores", "selected_index"]);
  if (output.candidate_digest !== expectedDigest) throw new Error("adapter candidate digest drift");
  if (!Array.isArray(output.scores) || output.scores.length !== expectedCount || output.scores.some((score) => typeof score !== "number" || !Number.isFinite(score))) throw new Error("adapter score count or value drift");
  if (output.selected_index !== null && (!Number.isSafeInteger(output.selected_index) || Number(output.selected_index) < 0 || Number(output.selected_index) >= expectedCount)) throw new Error("adapter selected_index drift");
  return output as unknown as AdapterDecision;
}

export function validatePolicyDecision(value: unknown): PolicyDecision {
  const decision = object(value, "Policy Decision"); exactKeys(decision, ["schema", "decision_id", "run_id", "manifest_id", "snapshot_id", "candidate_digest", "candidate_count", "scores", "selected_index", "disposition", "issued_at"]); literal(decision, "schema", POLICY_DECISION_SCHEMA);
  for (const key of ["decision_id", "run_id", "manifest_id", "snapshot_id"]) nonEmpty(decision, key); sha256Field(decision, "candidate_digest"); positiveOrZeroInteger(decision, "candidate_count");
  validateAdapterDecision({ candidate_digest: decision.candidate_digest, scores: decision.scores, selected_index: decision.selected_index }, decision.candidate_digest as string, decision.candidate_count as number);
  if (decision.disposition !== "admit" && decision.disposition !== "abstain") throw new Error("Policy Decision disposition is invalid");
  if (!Number.isFinite(Date.parse(String(decision.issued_at)))) throw new Error("Policy Decision issued_at is invalid");
  return decision as unknown as PolicyDecision;
}

export function assertAdapterDecision(value: unknown): asserts value is AdapterDecision { const output = object(value, "Adapter Decision"); exactKeys(output, ["candidate_digest", "scores", "selected_index"]); }
function object(value: unknown, label: string): Record<string, unknown> { if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be an object`); return value as Record<string, unknown>; }
function exactKeys(value: Record<string, unknown>, keys: string[]): void { const expected = new Set(keys); const actual = Object.keys(value); if (actual.length !== expected.size || actual.some((key) => !expected.has(key))) throw new Error("strict contract rejected unknown or missing fields"); }
function literal(value: Record<string, unknown>, key: string, expected: unknown): void { if (value[key] !== expected) throw new Error(`${key} has an invalid literal`); }
function enumField(value: Record<string, unknown>, key: string, expected: readonly string[]): void { if (typeof value[key] !== "string" || !expected.includes(value[key])) throw new Error(`${key} has an invalid value`); }
function nonEmpty(value: Record<string, unknown>, key: string): void { if (typeof value[key] !== "string" || value[key].length === 0) throw new Error(`${key} must be a non-empty string`); }
function sha256Field(value: Record<string, unknown>, key: string): void { if (typeof value[key] !== "string" || !/^[a-f0-9]{64}$/u.test(value[key])) throw new Error(`${key} must be a lowercase SHA-256`); }
function stringArray(value: Record<string, unknown>, key: string): void { if (!Array.isArray(value[key]) || value[key].some((item) => typeof item !== "string" || item.length === 0)) throw new Error(`${key} must be an array of non-empty strings`); }
function uniqueStringArray(value: Record<string, unknown>, key: string): void { const items = value[key] as string[]; if (new Set(items).size !== items.length) throw new Error(`${key} must not contain duplicates`); }
function nonEmptyUniqueStringArray(value: Record<string, unknown>, key: string): void { stringArray(value, key); const items = value[key] as string[]; if (items.length === 0) throw new Error(`${key} must not be empty`); uniqueStringArray(value, key); }
function booleanField(value: Record<string, unknown>, key: string): void { if (typeof value[key] !== "boolean") throw new Error(`${key} must be a boolean`); }
function positiveOrZeroInteger(value: Record<string, unknown>, key: string): void { if (!Number.isSafeInteger(value[key]) || Number(value[key]) < 0) throw new Error(`${key} must be a non-negative integer`); }
