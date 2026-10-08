import type { NativeLogicalAwait, NativeLogicalCapture, NativeLogicalEventAvailability,
  NativeLogicalResult, NativeLogicalSubscription } from "@rsgcsg/sts2-connector-client";
import type { RuntimeMode, RuntimeStatus } from "./contracts.js";
import type { AgentConsumption, AgentConsumeAck, AgentDirectiveOutput } from "./agent-session-contracts.js";
import type { AgentPendingRequest, AgentReconcileResolution } from "./agent-runtime-contracts.js";
import type { AgentStateMetadata } from "./agent-session-state.js";

interface Context { session_id: string; recovery_epoch: number }
interface Operational { autonomy_budget: RuntimeStatus["autonomy_budget"]; controller: RuntimeStatus["controller"] }
/** Immutable input binding, not a replay archive or proof of numerical W. */
export interface AgentAcquisitionWitness {
  acquisition_id: string; capture: NativeLogicalCapture; publication_index: string | null;
  snapshot_id: string; revision: number; owner_occurrence: Record<string, unknown>;
  status: string; included: string[]; missing: string[];
  catalog_digest: string | null; catalog_count: number | null; catalog_materialized: boolean;
}
export interface AgentRuntimeEventPayloads {
  native_session_attached: Context & { subscription: NativeLogicalSubscription; environment: RuntimeStatus["environment"] };
  native_event_received: Context & { original: NativeLogicalEventAvailability; received_cursor: string };
  native_acquisition_registered: Context & { witness: AgentAcquisitionWitness };
  agent_consumed: Context & { report: AgentConsumption; acknowledgement: AgentConsumeAck; witness: AgentAcquisitionWitness };
  agent_directive: Context & { output: AgentDirectiveOutput };
  native_submission_requested: Context & { request_id: string; basis_acquisition_id: string; snapshot_id: string;
    action_id: string; catalog_digest: string; run_id: string; runtime_instance_id: string };
  /** Original durable intent never reached the owning SDK dispatch hook. */
  native_submission_not_started: Context & { request_id: string; submission_epoch: number; reason: string };
  native_result: Context & { result: NativeLogicalResult };
  native_request_pending: Context & { original: AgentPendingRequest };
  native_request_reconciled: Context & { original: AgentPendingRequest; resolution: AgentReconcileResolution; result: NativeLogicalResult | null };
  native_request_unresolved: Context & { original: AgentPendingRequest; reason: string };
  native_await_result: Context & { wait_id: string; after_cursor: string; result: NativeLogicalAwait };
  native_gap: Context & { gap: Record<string, unknown> };
  controller_acquired: Context & { controller: "held" };
  controller_released: Context & { controller: "released" };
  controller_release_unknown: Context & { controller: "unknown"; reason: string };
  mode_changed: Context & Operational & { mode: RuntimeMode };
  autonomy_budget_exhausted: Context & Operational & { reason: string };
  handoff_to_human: Context & Operational & { reason: string };
  fail_closed: Context & { reason: string; agent_state: "known" | "uncertain" };
  runtime_tainted: Context & { reason: string; retry: false };
  agent_state_stored: Context & { metadata: AgentStateMetadata; path: string; metadata_path: string; bytes: number; sha256: string };
  agent_state_restored: Context & { metadata: AgentStateMetadata };
  stopped: Context & Operational & { pending_request: AgentPendingRequest | null; agent_state: "known" | "uncertain" };
}
export type AgentRuntimeEventKind = keyof AgentRuntimeEventPayloads;
