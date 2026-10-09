import type { ConnectorPolicyRequirements } from "./contracts.js";

export const AGENT_MANIFEST_SCHEMA = "sts2.policy-runtime/agent-manifest-1" as const;
export const AGENT_SESSION_SCHEMA = "sts2.policy-runtime/agent-session-1" as const;
export const AGENT_SESSION_PROTOCOL = "sts2.policy-runtime/agent-session-ndjson-1" as const;
export const NATIVE_SCOPE = ["persistent", "interaction", "referents", "catalog"] as const;
export type AgentScope = typeof NATIVE_SCOPE[number];
export type AgentQueryMethod = "current" | "read" | "catalog" | "resolve";
export type ConsumptionMode = "once_per_occurrence" | "incremental_view";
export type AgentHistoryMode = "full_reference" | "scoped_query" | "sampled_current";
export interface AgentInputSpec { id: string; version: string; sha256: string }
export interface AgentAdapterIdentity { id: string; version: string; protocol: typeof AGENT_SESSION_PROTOCOL; code_sha256: string }
export const AGENT_LIMIT_MAXIMA = Object.freeze({
  max_message_bytes: 96 * 1024 * 1024, max_acquisitions: 256,
  max_retained_acquisition_bytes: 256 * 1024 * 1024,
  max_pending_queries: 8, max_queries_per_turn: 64,
  max_query_bytes_per_turn: 128 * 1024 * 1024,
  max_capture_bytes: 64 * 1024 * 1024, max_catalog_actions: 65_536,
  max_cancelled_ids: 256, agent_timeout_ms: 30_000
});
export type AgentLimits = { -readonly [K in keyof typeof AGENT_LIMIT_MAXIMA]: number };
export interface AgentManifest {
  schema: typeof AGENT_MANIFEST_SCHEMA;
  manifest_id: string;
  agent: { id: string; version: string; provider: string; architecture: string };
  adapter: AgentAdapterIdentity;
  artifact: { id: string; path: string; sha256: string };
  input: {
    profile: "native-logical-v1";
    input_spec: AgentInputSpec;
    projection: { id: string; version: string };
    state_format_version: string;
    state_recovery: { mode: "opaque" | "none"; max_state_bytes: number; model_bindings: { model_id: string; weights_sha256: string }[] };
    history_mode: AgentHistoryMode;
    consumption_mode: ConsumptionMode;
    gap_policy: "handoff" | "explicit_reset";
    attachment: {
      eager_scope: AgentScope[];
      required_seams: { source_seam: string; version: string; coverage: "complete_at_seam" | "sampled" | "unsupported" }[];
      delivery_mode: "full_reference" | "scoped";
    };
  };
  requirements: {
    connector_protocol_version: string;
    environment: ConnectorPolicyRequirements["environment"];
    required_methods: string[];
  };
  support: { game_versions: string[]; game_commits: string[]; interaction_kinds: string[]; action_verbs: string[] };
  limits: AgentLimits;
  claims: { catalog_filtered: false; creates_action_authority: false; creates_native_operands: false; human_origin: false; causal_successor: false };
}
export interface AgentSessionContext { session_id: string; recovery_epoch: number }
export interface AgentConsumption {
  acquisition_id: string;
  input_spec: AgentInputSpec;
  continuity_token: string;
  previous_consumption_id: string | null;
  consumption_id: string;
  state_version: number;
  /** Accepted consumption-state transition, not proof of numerical Model memory. */
  advanced: boolean;
}
export interface AgentPrefix {
  continuity_token: string;
  history_mode: AgentHistoryMode;
  consumption_mode: ConsumptionMode;
  received_cursor: string | null;
  consumed_publication_index: string | null;
  omissions: { received_unconsumed_count: number | null; missing_scopes: AgentScope[]; gap: Record<string, unknown> | null };
}
export interface AgentConsumeAck {
  consumption_id: string; acquisition_id: string; state_version: number;
  advanced: boolean; prefix: AgentPrefix;
}
export interface AgentConsumeInput {
  acquisition_id: string; input_spec: AgentInputSpec; continuity_token: string;
  previous_consumption_id: string | null;
  /** Already decoded/qualified by the owning native SDK before offering. */
  observation: Record<string, unknown>; catalog: readonly Record<string, unknown>[] | null;
}
export interface AgentNextInput {
  continuity_token: string; consumption_id: string | null; state_version: number;
  basis_acquisition_id: string | null; received_cursor: string | null;
}
export type AgentSelection = { kind: "handle"; action_id: string }
  | { kind: "expression"; expression: Record<string, unknown> };
export type AgentDirective =
  | { type: "act"; basis_acquisition_id: string; selection: AgentSelection; scores: { catalog_digest: string; values: number[] } | null }
  | { type: "await"; after_cursor: string; condition: "any_event" | "observation" | "catalog_nonempty" | "terminal"; timeout_ms: number }
  | { type: "abstain" | "close"; reason: string };
export interface AgentDirectiveOutput {
  continuity_token: string; consumption_id: string | null; state_version: number;
  directive: AgentDirective;
}
export interface AgentQuery { method: AgentQueryMethod; arguments: Record<string, unknown> }
export interface AgentQueryResult { method: AgentQueryMethod; value: unknown; acquisition_id: string | null }

export class AgentSessionError extends Error {
  constructor(readonly code: string) { super(code); this.name = "AgentSessionError"; }
}
export function sessionObject(value: unknown, keys?: readonly string[]): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new AgentSessionError("object_required");
  const record = value as Record<string, unknown>;
  if (keys && (Object.keys(record).length !== keys.length || Object.keys(record).some(key => !keys.includes(key))))
    throw new AgentSessionError("unknown_or_missing_fields");
  return record;
}
export function sessionText(value: unknown, maxBytes = 256, allowEmpty = false): string {
  if (typeof value !== "string" || (!allowEmpty && value.length === 0)
    || Buffer.byteLength(value, "utf8") > maxBytes || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value))
    throw new AgentSessionError("invalid_text");
  return value;
}
export function sessionInteger(value: unknown, positive = false, maximum = Number.MAX_SAFE_INTEGER): number {
  if (!Number.isSafeInteger(value) || Number(value) < (positive ? 1 : 0) || Number(value) > maximum)
    throw new AgentSessionError("invalid_integer");
  return Number(value);
}
export function sessionDigest(value: unknown): string {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/u.test(value)) throw new AgentSessionError("invalid_digest");
  return value;
}
function strings(value: unknown, nonempty = false): string[] {
  if (!Array.isArray(value) || (nonempty && value.length === 0)) throw new AgentSessionError("string_array_required");
  const result = value.map(item => sessionText(item));
  if (new Set(result).size !== result.length) throw new AgentSessionError("duplicate_item");
  return result;
}
/** Mechanical vocabulary admission under one validated native profile/InputSpec.
 * The reserved singleton is not a native mechanism coverage claim. */
export function supportsProfileValue(declared: readonly string[], value: string): boolean {
  sessionText(value, 65_536);
  return declared.length === 1 && declared[0] === "*" || declared.includes(value);
}
function choice<T extends string>(value: unknown, options: readonly T[]): T {
  if (typeof value !== "string" || !options.includes(value as T)) throw new AgentSessionError("invalid_literal");
  return value as T;
}
export function validateAgentScope(value: unknown): AgentScope[] {
  const fields = strings(value);
  if (fields.some(field => !NATIVE_SCOPE.includes(field as AgentScope))
    || fields.join(",") !== NATIVE_SCOPE.filter(field => fields.includes(field)).join(","))
    throw new AgentSessionError("invalid_scope");
  return fields as AgentScope[];
}
export function validateAgentInputSpec(value: unknown): AgentInputSpec {
  const spec = sessionObject(value, ["id", "version", "sha256"]);
  sessionText(spec.id); sessionText(spec.version); sessionDigest(spec.sha256);
  return spec as unknown as AgentInputSpec;
}
export function validateAgentAdapter(value: unknown): AgentAdapterIdentity {
  const adapter = sessionObject(value, ["id", "version", "protocol", "code_sha256"]);
  sessionText(adapter.id); sessionText(adapter.version); sessionDigest(adapter.code_sha256);
  choice(adapter.protocol, [AGENT_SESSION_PROTOCOL]);
  return adapter as unknown as AgentAdapterIdentity;
}
export function validateAgentManifest(value: unknown): AgentManifest {
  const manifest = sessionObject(value, ["schema", "manifest_id", "agent", "adapter", "artifact", "input", "requirements", "support", "limits", "claims"]);
  choice(manifest.schema, [AGENT_MANIFEST_SCHEMA]); sessionText(manifest.manifest_id);
  const agent = sessionObject(manifest.agent, ["id", "version", "provider", "architecture"]);
  for (const item of Object.values(agent)) sessionText(item);
  validateAgentAdapter(manifest.adapter);
  const artifact = sessionObject(manifest.artifact, ["id", "path", "sha256"]);
  sessionText(artifact.id); sessionText(artifact.path, 4096); sessionDigest(artifact.sha256);
  const input = sessionObject(manifest.input, ["profile", "input_spec", "projection", "state_format_version", "state_recovery", "history_mode", "consumption_mode", "gap_policy", "attachment"]);
  choice(input.profile, ["native-logical-v1"]); validateAgentInputSpec(input.input_spec);
  const projection = sessionObject(input.projection, ["id", "version"]);
  sessionText(projection.id); sessionText(projection.version); sessionText(input.state_format_version);
  const recovery = sessionObject(input.state_recovery, ["mode", "max_state_bytes", "model_bindings"]);
  const recoveryMode = choice(recovery.mode, ["opaque", "none"]);
  sessionInteger(recovery.max_state_bytes, recoveryMode === "opaque", 16 * 1024 * 1024);
  if (!Array.isArray(recovery.model_bindings) || recovery.model_bindings.length > 16) throw new AgentSessionError("invalid_model_bindings");
  for (const value of recovery.model_bindings) {
    const binding = sessionObject(value, ["model_id", "weights_sha256"]);
    sessionText(binding.model_id); sessionDigest(binding.weights_sha256);
  }
  if (new Set(recovery.model_bindings.map(value => (value as Record<string, unknown>).model_id)).size !== recovery.model_bindings.length)
    throw new AgentSessionError("duplicate_model_binding");
  if (recoveryMode === "none" && (recovery.max_state_bytes !== 0 || recovery.model_bindings.length !== 0)) throw new AgentSessionError("stateless_state_binding");
  const history = choice(input.history_mode, ["full_reference", "scoped_query", "sampled_current"]);
  const mode = choice(input.consumption_mode, ["once_per_occurrence", "incremental_view"]);
  choice(input.gap_policy, ["handoff", "explicit_reset"]);
  const attachment = sessionObject(input.attachment, ["eager_scope", "required_seams", "delivery_mode"]);
  const scope = validateAgentScope(attachment.eager_scope);
  const delivery = choice(attachment.delivery_mode, ["full_reference", "scoped"]);
  if (!Array.isArray(attachment.required_seams) || attachment.required_seams.length === 0 || attachment.required_seams.length > 256)
    throw new AgentSessionError("required_seams_required");
  const seams = attachment.required_seams.map(value => {
    const seam = sessionObject(value, ["source_seam", "version", "coverage"]);
    sessionText(seam.source_seam, 128); sessionText(seam.version, 128);
    choice(seam.coverage, ["complete_at_seam", "sampled", "unsupported"]);
    return seam;
  });
  if (new Set(seams.map(seam => seam.source_seam)).size !== seams.length) throw new AgentSessionError("duplicate_seam");
  if ((history === "full_reference") !== (delivery === "full_reference")
    || (history === "full_reference" && (mode !== "once_per_occurrence" || scope.length !== 4 || seams.some(seam => seam.coverage !== "complete_at_seam"))))
    throw new AgentSessionError("history_scope_mismatch");
  if (history === "sampled_current" && (mode !== "once_per_occurrence" || scope.length !== 0
    || delivery !== "scoped" || recoveryMode !== "none" || input.gap_policy !== "handoff"))
    throw new AgentSessionError("sampled_current_contract_mismatch");
  const requirements = sessionObject(manifest.requirements, ["connector_protocol_version", "environment", "required_methods"]);
  sessionText(requirements.connector_protocol_version);
  const environment = sessionObject(requirements.environment, ["host_kind", "connector_version", "connector_source_revision", "connector_artifact_sha256", "connector_module_version_id", "modset_status", "modset_fingerprint", "loaded_mod_ids"]);
  choice(environment.host_kind, ["live_ui", "headless", "replay", "test"]);
  for (const key of ["connector_version", "connector_source_revision", "connector_module_version_id", "modset_status"]) sessionText(environment[key]);
  sessionDigest(environment.connector_artifact_sha256); sessionDigest(environment.modset_fingerprint);
  strings(environment.loaded_mod_ids);
  const methods = strings(requirements.required_methods, true);
  const allowed = ["capabilities", "attach", "current", "read", "catalog", "resolve", "submit", "result", "events", "await", "cancel_wait", "detach", "renew", "retain", "release"];
  const minimum = ["capabilities", "attach", "events", "await", "cancel_wait", "detach", "submit", "result", "renew"];
  if (history === "sampled_current") minimum.push("current", "read", "catalog", "retain", "release");
  else if (history === "full_reference") minimum.push("read", "catalog", "retain", "release");
  else if (methods.includes("current")) minimum.push("read", "retain", "release");
  if (methods.some(method => !allowed.includes(method)) || minimum.some(method => !methods.includes(method)))
    throw new AgentSessionError("required_methods_mismatch");
  const support = sessionObject(manifest.support, ["game_versions", "game_commits", "interaction_kinds", "action_verbs"]);
  for (const [key, item] of Object.entries(support)) {
    const values = strings(item, true);
    if (values.includes("*") && (values.length !== 1 || key === "game_versions" || key === "game_commits"))
      throw new AgentSessionError("invalid_support_wildcard");
  }
  const limits = sessionObject(manifest.limits, Object.keys(AGENT_LIMIT_MAXIMA));
  for (const [key, maximum] of Object.entries(AGENT_LIMIT_MAXIMA)) sessionInteger(limits[key], true, maximum);
  const claims = sessionObject(manifest.claims, ["catalog_filtered", "creates_action_authority", "creates_native_operands", "human_origin", "causal_successor"]);
  if (Object.values(claims).some(item => item !== false)) throw new AgentSessionError("authority_or_qualification_claim");
  return manifest as unknown as AgentManifest;
}
export function validateAgentConsumption(value: unknown): AgentConsumption {
  const completion = sessionObject(value, ["acquisition_id", "input_spec", "continuity_token", "previous_consumption_id", "consumption_id", "state_version", "advanced"]);
  for (const key of ["acquisition_id", "continuity_token", "consumption_id"]) sessionText(completion[key]);
  validateAgentInputSpec(completion.input_spec);
  if (completion.previous_consumption_id !== null) sessionText(completion.previous_consumption_id);
  sessionInteger(completion.state_version);
  if (typeof completion.advanced !== "boolean") throw new AgentSessionError("invalid_advance");
  return completion as unknown as AgentConsumption;
}
export function validateAgentNextInput(value: unknown): AgentNextInput {
  const input = sessionObject(value, ["continuity_token", "consumption_id", "state_version", "basis_acquisition_id", "received_cursor"]);
  sessionText(input.continuity_token); sessionInteger(input.state_version);
  for (const key of ["consumption_id", "basis_acquisition_id", "received_cursor"]) if (input[key] !== null) sessionText(input[key], key === "received_cursor" ? 1024 : 256);
  if ((input.state_version === 0) !== (input.consumption_id === null)) throw new AgentSessionError("state_watermark_mismatch");
  return input as unknown as AgentNextInput;
}
export function validateAgentDirective(value: unknown): AgentDirectiveOutput {
  const output = sessionObject(value, ["continuity_token", "consumption_id", "state_version", "directive"]);
  sessionText(output.continuity_token); sessionInteger(output.state_version);
  if (output.consumption_id !== null) sessionText(output.consumption_id);
  if ((output.state_version === 0) !== (output.consumption_id === null)) throw new AgentSessionError("state_watermark_mismatch");
  const directive = sessionObject(output.directive);
  switch (directive.type) {
    case "act": {
      sessionObject(directive, ["type", "basis_acquisition_id", "selection", "scores"]);
      sessionText(directive.basis_acquisition_id);
      const selection = sessionObject(directive.selection);
      if (selection.kind === "handle") { sessionObject(selection, ["kind", "action_id"]); sessionText(selection.action_id, 65_536); }
      else if (selection.kind === "expression") { sessionObject(selection, ["kind", "expression"]); sessionObject(selection.expression); }
      else throw new AgentSessionError("invalid_selection");
      if (directive.scores !== null) {
        const scores = sessionObject(directive.scores, ["catalog_digest", "values"]);
        sessionDigest(scores.catalog_digest);
        if (!Array.isArray(scores.values) || scores.values.length > AGENT_LIMIT_MAXIMA.max_catalog_actions || scores.values.some(item => typeof item !== "number" || !Number.isFinite(item)))
          throw new AgentSessionError("invalid_scores");
      }
      if (output.consumption_id === null) throw new AgentSessionError("Act_requires_consumption");
      break;
    }
    case "await":
      sessionObject(directive, ["type", "after_cursor", "condition", "timeout_ms"]);
      sessionText(directive.after_cursor, 1024); choice(directive.condition, ["any_event", "observation", "catalog_nonempty", "terminal"]);
      sessionInteger(directive.timeout_ms, true, 30_000); break;
    case "abstain": case "close":
      sessionObject(directive, ["type", "reason"]); sessionText(directive.reason, 256, true); break;
    default: throw new AgentSessionError("invalid_directive");
  }
  return output as unknown as AgentDirectiveOutput;
}
export function validateAgentQuery(value: unknown): AgentQuery {
  const query = sessionObject(value, ["method", "arguments"]);
  choice(query.method, ["current", "read", "catalog", "resolve"]);
  sessionObject(query.arguments);
  // Native parameter semantics and decoding are delegated to the owning SDK.
  return query as unknown as AgentQuery;
}
export function validateAgentSessionContext(value: unknown): AgentSessionContext {
  const context = sessionObject(value, ["session_id", "recovery_epoch"]);
  sessionText(context.session_id); sessionInteger(context.recovery_epoch);
  return context as unknown as AgentSessionContext;
}
