import type {
  DecodedPlayerPayload,
  PlayerEnvironmentBoundAction,
  PlayerEnvironmentCapabilities,
  PlayerEnvironmentReadResponse,
  PlayerEnvironmentReceipt,
  PlayerEnvironmentSnapshot
} from "@rsgcsg/sts2-connector-client";
import type { TextMenuAction, TextMenuActionResult, TextMenuCapabilities, TextMenuSnapshot, TextMenuObservationContext } from "@rsgcsg/sts2-connector-client";
import type { TextMenuV2Action, TextMenuV2ActionResult, TextMenuV2Capabilities, TextMenuV2Snapshot, TextMenuV2ObservationContext } from "@rsgcsg/sts2-connector-client";

export type TextInputProfile = "text-menu-v1" | "text-menu-v2";
export type TextSnapshot = TextMenuSnapshot | TextMenuV2Snapshot;
export type TextAction = TextMenuAction | TextMenuV2Action;
export type TextActionResult = TextMenuActionResult | TextMenuV2ActionResult;
export type TextObservationContext = TextMenuObservationContext | TextMenuV2ObservationContext;

export const POLICY_MANIFEST_SCHEMA = "sts2.policy-runtime/policy-manifest-1" as const;
export const POLICY_DECISION_SCHEMA = "sts2.policy-runtime/decision-1" as const;
export const AGENT_RUN_SCHEMA = "sts2.policy-runtime/agent-run-1" as const;
export const POLICY_PORT_SCHEMA = "sts2.policy-runtime/policy-port-1" as const;
export const POLICY_PORT_V2_SCHEMA = "sts2.policy-runtime/policy-port-2" as const;
export const POLICY_PORT_V3_SCHEMA = "sts2.policy-runtime/policy-port-3" as const;
export const EVIDENCE_MANIFEST_SCHEMA = "sts2.policy-runtime/immutable-evidence-manifest-1" as const;
export const POLICY_RUNTIME_VERSION = "0.1.0-rc.17" as const;
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
export interface ManagedEnvironmentBinding {
  schema: "sts2.policy-runtime/managed-environment-binding-1";
  /** Digest of the private operator profile only; the profile bytes are not public evidence. */
  profile_sha256: string;
  input_profile: "text-menu-v2";
  host_package_identity: {
    package: "@rsgcsg/sts2-host-runtime";
    version: string;
    source_revision: string;
    component_tree_revision: string;
    release_asset_sha256: string;
    package_content_sha256: string;
  };
  candidate_build: {
    upstream_revision: string;
    source_patch_sha256: string;
    artifact_sha256: string;
    artifact_mvid: string;
    original_sts2_sha256: string;
    runtime_sts2_sha256: string;
  };
}

export interface ManagedEnvironmentStatus {
  kind: "managed_text_v2";
  binding_sha256: string;
  service_instance_id: string;
  runtime_instance_id: string;
  environment_fingerprint: string;
  game_continuity_id: string;
  text_protocol_version: string;
  input_profile: "text-menu-v2";
  host_package_identity: ManagedEnvironmentBinding["host_package_identity"];
  host_identity: { package_name: string; version: string; distribution_kind: "installed_package" | "git_checkout";
    source_revision: string | null; component_tree_revision: string | null; source_digest_sha256: string };
  candidate_build: ManagedEnvironmentBinding["candidate_build"];
  game_version: string;
  game_commit: string;
  game_assembly_sha256: string;
  episode_provenance: { verdict: "provenance_pass"; requested_seed: string; actual_seed: string; runtime_instance_id: string };
}

export interface ManagedCapabilities {
  kind: "managed_text_v2";
  protocol_version: string;
  input_profile: "text-menu-v2";
  snapshot_schema: "sts2.player-environment/text-menu-snapshot-2";
  receipt_schema: "sts2.player-environment/text-menu-action-result-2";
  interaction_kinds: string[];
  observed_terminal_kinds: string[];
  action_verbs: string[];
  execution_available: boolean;
  control_held: boolean;
  control_owned: boolean;
  tainted: boolean;
  environment: ManagedEnvironmentStatus;
}

export interface ManagedControlConfirmation {
  status: "held" | "released";
  service_instance_id: string;
  runtime_instance_id: string;
  game_continuity_id: string;
  control_epoch: string;
}

export interface ConnectorPolicyRequirements {
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
  candidate_order_digest: "sha256-json-bound-action-id-order" | "sha256-json-menu-action-id-order";
  score_count_matches_candidate_count: true;
  selected_index: true;
  successor_required: true;
}

export interface ManagedPolicyRequirements {
  environment: { kind: "managed_text_v2"; text_protocol_version: string; input_profile: "text-menu-v2" };
  reads: [];
  whole_decision_admission: true;
  candidate_order_digest: "sha256-json-menu-action-id-order";
  score_count_matches_candidate_count: true;
  selected_index: true;
  successor_required: true;
}

export interface PolicyManifest {
  schema: typeof POLICY_MANIFEST_SCHEMA;
  manifest_id: string;
  policy: { id: string; version: string; provider: string; architecture: string };
  adapter: { id: string; version: string; protocol: "sts2.policy-runtime/decision-only-ndjson-1" | "sts2.policy-runtime/decision-only-ndjson-2" | "sts2.policy-runtime/decision-only-ndjson-3"; code_sha256: string };
  artifact: { id: string; path: string; sha256: string };
  representation: { id: string; version: string; input_schema: "sts2.player-environment/snapshot-1" | "sts2.player-environment/text-menu-snapshot-1" | "sts2.player-environment/text-menu-snapshot-2" };
  requirements: ConnectorPolicyRequirements | ManagedPolicyRequirements;
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
  environment_binding?: ManagedEnvironmentBinding;
}

export interface DecisionBundle { observation: PlayerEnvironmentSnapshot; reads: PlayerEnvironmentReadResponse[] }
export interface TextMenuDecisionBundle { observation: TextSnapshot; reads: [] }
export type AnyDecisionBundle = DecisionBundle | TextMenuDecisionBundle;
export type DecisionAction = PlayerEnvironmentBoundAction | TextAction;
export type DecisionResult = PlayerEnvironmentReceipt | TextActionResult;
export function isTextMenuSnapshot(snapshot: AnyDecisionBundle["observation"]): snapshot is TextSnapshot {
  return snapshot.schema === "sts2.player-environment/text-menu-snapshot-1"
    || snapshot.schema === "sts2.player-environment/text-menu-snapshot-2";
}
export function decisionActions(snapshot: AnyDecisionBundle["observation"]): readonly DecisionAction[] {
  return isTextMenuSnapshot(snapshot) ? snapshot.menu_actions.actions : snapshot.bound_actions.actions;
}
export function decisionActionId(action: DecisionAction): string {
  return "action_id" in action ? action.action_id : action.bound_action_id;
}

/** Decision-only adapter output. The adapter never returns a catalog or action. */
export interface AdapterDecision { candidate_digest: string; scores: number[]; selected_index: number | null }

export interface PolicyDecisionInput {
  run_id: string;
  manifest: PolicyManifest;
  bundle: AnyDecisionBundle;
  candidate_digest: string;
  candidate_count: number;
}

/** Runtime-owned scheduling token; the Connector run identity stays outside model text. */
export interface StatefulPolicyDecisionInput extends PolicyDecisionInput {
  continuity_token: string;
  previous_interaction?: ConfirmedInteraction | null;
}

/** Connector-correlated, durably recorded delivery metadata; no inferred effect. */
export interface ConfirmedInteraction {
  decision_id: string;
  snapshot_id: string;
  candidate_digest: string;
  action_id: string;
  request_id: string;
  effect_domain: "text_menu" | "native_input";
  result_kind: "menu_applied" | "native_input_delivered";
}

export interface ObservationCompletion {
  continuity_token: string;
  snapshot_id: string;
  sequence: number;
  previous_interaction_request_id?: string | null;
}

export interface StatefulAdapterDecision {
  output: AdapterDecision;
  completion: ObservationCompletion;
}

/** In-process implementations mark their call as offered at invocation. */
export type StatefulPolicy = (input: StatefulPolicyDecisionInput, signal: AbortSignal,
  onOffer: () => void) => Promise<StatefulAdapterDecision> | StatefulAdapterDecision;

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
export interface PolicyPortV2DecisionRequest { schema: typeof POLICY_PORT_V2_SCHEMA; message_type: "decide"; request_id: string; input: StatefulPolicyDecisionInput }
export interface PolicyPortV2ReadyResponse { schema: typeof POLICY_PORT_V2_SCHEMA; message_type: "ready"; adapter: PolicyManifest["adapter"] }
export interface PolicyPortV2DecisionResponse { schema: typeof POLICY_PORT_V2_SCHEMA; message_type: "decision"; request_id: string; output: AdapterDecision; completion: ObservationCompletion }
export interface PolicyPortV2ErrorResponse { schema: typeof POLICY_PORT_V2_SCHEMA; message_type: "error"; request_id: string; error: { code: string; message: string } }

export interface PolicyPortV3DecisionRequest { schema: typeof POLICY_PORT_V3_SCHEMA; message_type: "decide"; request_id: string; input: StatefulPolicyDecisionInput }
export interface PolicyPortV3ReadyResponse { schema: typeof POLICY_PORT_V3_SCHEMA; message_type: "ready"; adapter: PolicyManifest["adapter"] }
export interface PolicyPortV3DecisionResponse { schema: typeof POLICY_PORT_V3_SCHEMA; message_type: "decision"; request_id: string; output: AdapterDecision; completion: ObservationCompletion }
export interface PolicyPortV3ErrorResponse { schema: typeof POLICY_PORT_V3_SCHEMA; message_type: "error"; request_id: string; error: { code: string; message: string } }

export interface PolicyConnector {
  capabilities(options?: { fresh?: boolean; inputProfile?: TextInputProfile }): Promise<PlayerEnvironmentCapabilities | TextMenuCapabilities | TextMenuV2Capabilities | ManagedCapabilities>;
  observeBundle(requiredReadKinds: readonly string[], inputProfile?: TextInputProfile): Promise<AnyDecisionBundle>;
  observeTextMenuContext?(inputProfile?: TextInputProfile): Promise<TextObservationContext>;
  acquireController(): Promise<void | ManagedControlConfirmation>;
  releaseController(): Promise<void | ManagedControlConfirmation>;
  submit(input: { requestId: string; expectedSnapshotId: string; boundActionId: string; inputProfile?: TextInputProfile; previousSnapshot?: TextMenuV2Snapshot }): Promise<DecisionResult>;
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
  controller: "held" | "released" | "unknown";
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
  } | ManagedEnvironmentStatus | null;
}

export type TickResult =
  | { type: "human"; status: RuntimeStatus }
  | { type: "not_admitted"; reason: string; status: RuntimeStatus }
  | { type: "shadow"; decision: PolicyDecision; status: RuntimeStatus }
  | { type: "not_executed"; decision: PolicyDecision; status: RuntimeStatus }
  | { type: "delivered"; decision: PolicyDecision; bound_action: PlayerEnvironmentBoundAction; receipt: PlayerEnvironmentReceipt; successor: PlayerEnvironmentSnapshot; status: RuntimeStatus }
  | { type: "not_delivered"; decision: PolicyDecision; receipt: PlayerEnvironmentReceipt; status: RuntimeStatus }
  | { type: "navigated"; decision: PolicyDecision; action: TextAction; result: TextActionResult; successor: TextSnapshot; status: RuntimeStatus }
  | { type: "text_native_delivered"; decision: PolicyDecision; action: TextAction; result: TextActionResult; successor: TextSnapshot; status: RuntimeStatus }
  | { type: "text_not_applied"; decision: PolicyDecision; result: TextActionResult; status: RuntimeStatus }
  | { type: "unknown"; decision: PolicyDecision | null; receipt: DecisionResult | null; error: string; status: RuntimeStatus };

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
  textMenuCapabilities(): Promise<DecodedPlayerPayload<TextMenuCapabilities>>;
  textMenuV2Capabilities(): Promise<DecodedPlayerPayload<TextMenuV2Capabilities>>;
  observeTextMenu(): Promise<DecodedPlayerPayload<TextMenuSnapshot>>;
  observeTextMenuContext(): Promise<DecodedPlayerPayload<TextMenuObservationContext>>;
  observeTextMenuV2(): Promise<DecodedPlayerPayload<TextMenuV2Snapshot>>;
  observeTextMenuV2Context(): Promise<DecodedPlayerPayload<TextMenuV2ObservationContext>>;
  read(readId: string, expectedSnapshotId: string): Promise<DecodedPlayerPayload<PlayerEnvironmentReadResponse>>;
  submit(input: { requestId: string; expectedSnapshotId: string; boundActionId: string; clientSessionId: string; controllerLeaseId: string; controllerGeneration: number }): Promise<DecodedPlayerPayload<PlayerEnvironmentReceipt>>;
  submitTextMenu(input: { requestId: string; expectedSnapshotId: string; boundActionId: string; clientSessionId: string; controllerLeaseId: string; controllerGeneration: number }): Promise<DecodedPlayerPayload<TextMenuActionResult>>;
  submitTextMenuV2(input: { requestId: string; expectedSnapshotId: string; boundActionId: string; clientSessionId: string; controllerLeaseId: string; controllerGeneration: number }, previous?: TextMenuV2Snapshot): Promise<DecodedPlayerPayload<TextMenuV2ActionResult>>;
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
  const adapter = object(root.adapter, "manifest.adapter"); exactKeys(adapter, ["id", "version", "protocol", "code_sha256"]); nonEmpty(adapter, "id"); nonEmpty(adapter, "version"); enumField(adapter, "protocol", ["sts2.policy-runtime/decision-only-ndjson-1", "sts2.policy-runtime/decision-only-ndjson-2", "sts2.policy-runtime/decision-only-ndjson-3"]); sha256Field(adapter, "code_sha256");
  const artifact = object(root.artifact, "manifest.artifact"); exactKeys(artifact, ["id", "path", "sha256"]); nonEmpty(artifact, "id"); nonEmpty(artifact, "path"); sha256Field(artifact, "sha256");
  const representation = object(root.representation, "manifest.representation"); exactKeys(representation, ["id", "version", "input_schema"]); nonEmpty(representation, "id"); nonEmpty(representation, "version"); enumField(representation, "input_schema", ["sts2.player-environment/snapshot-1", "sts2.player-environment/text-menu-snapshot-1", "sts2.player-environment/text-menu-snapshot-2"]);
  const requirements = object(root.requirements, "manifest.requirements");
  const environment = object(requirements.environment, "manifest.requirements.environment");
  const managed = environment.kind === "managed_text_v2";
  if (managed) {
    exactKeys(requirements, ["environment", "reads", "whole_decision_admission", "candidate_order_digest", "score_count_matches_candidate_count", "selected_index", "successor_required"]);
    exactKeys(environment, ["kind", "text_protocol_version", "input_profile"]);
    nonEmpty(environment, "text_protocol_version"); literal(environment, "input_profile", "text-menu-v2");
    literal(representation, "input_schema", "sts2.player-environment/text-menu-snapshot-2");
    literal(adapter, "protocol", "sts2.policy-runtime/decision-only-ndjson-3");
  } else {
    exactKeys(requirements, ["connector_protocol_version", "environment", "reads", "whole_decision_admission", "candidate_order_digest", "score_count_matches_candidate_count", "selected_index", "successor_required"]); nonEmpty(requirements, "connector_protocol_version");
    exactKeys(environment, ["host_kind", "connector_version", "connector_source_revision", "connector_artifact_sha256", "connector_module_version_id", "modset_status", "modset_fingerprint", "loaded_mod_ids"]); enumField(environment, "host_kind", ["live_ui", "headless", "replay", "test"]); nonEmpty(environment, "connector_version"); nonEmpty(environment, "connector_source_revision"); sha256Field(environment, "connector_artifact_sha256"); nonEmpty(environment, "connector_module_version_id"); nonEmpty(environment, "modset_status"); nonEmpty(environment, "modset_fingerprint"); stringArray(environment, "loaded_mod_ids"); uniqueStringArray(environment, "loaded_mod_ids");
  }
  stringArray(requirements, "reads"); uniqueStringArray(requirements, "reads"); literal(requirements, "whole_decision_admission", true); literal(requirements, "candidate_order_digest", representation.input_schema !== "sts2.player-environment/snapshot-1" ? "sha256-json-menu-action-id-order" : "sha256-json-bound-action-id-order"); literal(requirements, "score_count_matches_candidate_count", true); literal(requirements, "selected_index", true); literal(requirements, "successor_required", true);
  if (representation.input_schema !== "sts2.player-environment/snapshot-1" && (requirements.reads as string[]).length !== 0) throw new Error("text menu profile has no Reads");
  if (adapter.protocol !== "sts2.policy-runtime/decision-only-ndjson-1" && representation.input_schema === "sts2.player-environment/snapshot-1") throw new Error("stateful policy port requires text menu input");
  if (representation.input_schema === "sts2.player-environment/text-menu-snapshot-2" && adapter.protocol === "sts2.policy-runtime/decision-only-ndjson-1") throw new Error("text menu v2 requires stateful policy port");
  const support = object(root.support, "manifest.support"); exactKeys(support, ["game_versions", "game_commits", "interaction_kinds", "action_verbs"]); nonEmptyUniqueStringArray(support, "game_versions"); nonEmptyUniqueStringArray(support, "game_commits"); nonEmptyUniqueStringArray(support, "interaction_kinds"); nonEmptyUniqueStringArray(support, "action_verbs");
  object(root.adapter_config, "manifest.adapter_config");
  const claims = object(root.claims, "manifest.claims"); exactKeys(claims, ["full_run", "selector", "catalog_filtered", "creates_action_authority", "creates_native_operands"]); booleanField(claims, "full_run"); booleanField(claims, "selector"); literal(claims, "catalog_filtered", false); literal(claims, "creates_action_authority", false); literal(claims, "creates_native_operands", false);
  return root as unknown as PolicyManifest;
}

export function validateManagedEnvironmentBinding(value: unknown): ManagedEnvironmentBinding {
  const binding = object(value, "Managed Environment Binding");
  exactKeys(binding, ["schema", "profile_sha256", "input_profile", "host_package_identity", "candidate_build"]);
  literal(binding, "schema", "sts2.policy-runtime/managed-environment-binding-1");
  sha256Field(binding, "profile_sha256");
  literal(binding, "input_profile", "text-menu-v2");
  const pin = object(binding.host_package_identity, "binding.host_package_identity");
  exactKeys(pin, ["package", "version", "source_revision", "component_tree_revision", "release_asset_sha256", "package_content_sha256"]);
  literal(pin, "package", "@rsgcsg/sts2-host-runtime");
  nonEmpty(pin, "version");
  for (const key of ["source_revision", "component_tree_revision"] as const) hexField(pin, key, 40);
  for (const key of ["release_asset_sha256", "package_content_sha256"] as const) sha256Field(pin, key);
  const build = object(binding.candidate_build, "binding.candidate_build");
  exactKeys(build, ["upstream_revision", "source_patch_sha256", "artifact_sha256", "artifact_mvid", "original_sts2_sha256", "runtime_sts2_sha256"]);
  nonEmpty(build, "upstream_revision"); nonEmpty(build, "artifact_mvid");
  for (const key of ["source_patch_sha256", "artifact_sha256", "original_sts2_sha256", "runtime_sts2_sha256"] as const) sha256Field(build, key);
  return binding as unknown as ManagedEnvironmentBinding;
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
function hexField(value: Record<string, unknown>, key: string, length: number): void { if (typeof value[key] !== "string" || value[key].length !== length || !/^[a-f0-9]+$/u.test(value[key])) throw new Error(`${key} must be lowercase hex`); }
function stringArray(value: Record<string, unknown>, key: string): void { if (!Array.isArray(value[key]) || value[key].some((item) => typeof item !== "string" || item.length === 0)) throw new Error(`${key} must be an array of non-empty strings`); }
function uniqueStringArray(value: Record<string, unknown>, key: string): void { const items = value[key] as string[]; if (new Set(items).size !== items.length) throw new Error(`${key} must not contain duplicates`); }
function nonEmptyUniqueStringArray(value: Record<string, unknown>, key: string): void { stringArray(value, key); const items = value[key] as string[]; if (items.length === 0) throw new Error(`${key} must not be empty`); uniqueStringArray(value, key); }
function booleanField(value: Record<string, unknown>, key: string): void { if (typeof value[key] !== "boolean") throw new Error(`${key} must be a boolean`); }
function positiveOrZeroInteger(value: Record<string, unknown>, key: string): void { if (!Number.isSafeInteger(value[key]) || Number(value[key]) < 0) throw new Error(`${key} must be a non-negative integer`); }
