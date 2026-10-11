import type { RuntimeEnvironmentBinding, RuntimeMode, RuntimeStatus } from "./contracts.js";
import type { AgentAdapterIdentity, AgentDirective, AgentInputSpec, AgentPrefix } from "./agent-session-contracts.js";
import type { NativeLogicalDispatchBinding } from "@rsgcsg/sts2-connector-client";

/** Operational native profile projection. It exposes neither package paths nor
 * adapter commands, environment credentials, native operands or Model state. */
export interface AgentRuntimeStatus {
  schema: "sts2.policy-runtime/agent-session-status-1";
  runtime: RuntimeStatus["runtime"];
  agent: {
    manifest_id: string; agent_id: string; agent_version: string; provider: string;
    architecture: string; artifact_id: string; artifact_sha256: string;
    adapter: AgentAdapterIdentity;
  };
  agent_manifest_sha256: string;
  run_id: string; lifecycle: "running" | "stopped"; mode: RuntimeMode;
  controller: RuntimeStatus["controller"];
  autonomy_budget: RuntimeStatus["autonomy_budget"];
  tainted: boolean; taint_reason: string | null; refreshing: boolean;
  environment: RuntimeStatus["environment"];
  session: {
    session_id: string; recovery_epoch: number; profile: "native-logical-v1";
    input_spec: AgentInputSpec; stream_generation: string | null;
    continuity_token: string; state_version: number; consumption_id: string | null;
    prefix: AgentPrefix; agent_state: "known" | "uncertain";
  };
  last_observation: {
    acquisition_id: string; capture_sha256: string; snapshot_id: string;
    revision: number; status: string; publication_index: string | null;
    included: string[]; missing: string[]; catalog_count: number | null;
  } | null;
  last_directive: AgentDirective | null;
  last_result: {
    request_id: string; snapshot_id: string; action_id: string | null;
    status: "pending" | "terminal"; delivery: string | null; execution: string | null;
    effect: string | null; cancel: string | null; reason: string | null;
  } | null;
  pending_request: AgentPendingRequest | null;
  errors: string[]; invalidations: string[];
}

/** Original owned submission identity. Resolution appends a new fact and never
 * rewrites the pending event or substitutes a request/current-state inference. */
export interface AgentPendingRequest {
  request_id: string; run_id: string; runtime_instance_id: string; session_id: string;
  submission_epoch: number; basis_acquisition_id: string; snapshot_id: string;
  action_id: string; status: "pending" | "unresolved"; reason: string | null;
  dispatch_binding?: Readonly<NativeLogicalDispatchBinding>;
}
export type AgentReconcileResolution = "resolved" | "pending" | "unresolved" | "tainted";
export interface AgentReconcileResult {
  request_id: string; resolution: AgentReconcileResolution; status: AgentRuntimeStatus;
}

export type AgentRuntimeTickResult =
  | { type: "fresh_decision_required"; original_request_id: string; status: AgentRuntimeStatus }
  | { type: "not_delivered"; reason?: string; status: AgentRuntimeStatus }
  | { type: "human" | "observation" | "awaited" | "shadow" | "delivered" | "closed"; status: AgentRuntimeStatus }
  | { type: "not_admitted"; reason: string; status: AgentRuntimeStatus }
  | { type: "unknown"; error: string; status: AgentRuntimeStatus };

export interface AgentRuntimeStartup {
  schema: "sts2.policy-runtime/agent-session-startup-1";
  address: string; run_id: string; agent_manifest_id: string;
  agent_artifact_sha256: string; agent_manifest_sha256: string;
  runtime_version: string; runtime_code_sha256: string; mode: RuntimeMode;
  autonomy_budget: { maxSubmissions: number; maxPolicyCalls: number; deadlineMs: number };
  managed_environment: null; adapter: AgentAdapterIdentity;
}

/** Both profiles share one existing HTTP service and control protocol. */
export interface RuntimeServiceOwner {
  status(): RuntimeStatus | AgentRuntimeStatus;
  readEnvironment(): Promise<RuntimeEnvironmentBinding>;
  setMode(mode: RuntimeMode, expected?: { gameInstanceId?: string; recoveryEpoch?: number }): Promise<RuntimeStatus | AgentRuntimeStatus>;
  stop(): Promise<RuntimeStatus | AgentRuntimeStatus>;
  tick(expected?: { gameInstanceId?: string; recoveryEpoch?: number }): Promise<import("./contracts.js").TickResult | AgentRuntimeTickResult>;
  reconcileOriginalRequest?(requestId: string, expected?: { gameInstanceId?: string; recoveryEpoch?: number }): Promise<AgentReconcileResult>;
}
