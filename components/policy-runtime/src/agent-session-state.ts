import { createHash } from "node:crypto";
import { AgentByteBudget, type AgentByteReservation } from "./agent-session-budget.js";
import {
  AgentSessionError, sessionDigest, sessionInteger, sessionObject, sessionText,
  validateAgentInputSpec, validateAgentScope, type AgentManifest, type AgentPrefix
} from "./agent-session-contracts.js";
import { encodeBoundedAgentJson } from "./agent-session-json.js";

export interface AgentStateMetadata {
  agent_artifact_id: string;
  agent_artifact_sha256: string;
  adapter_code_sha256: string;
  model_bindings: { model_id: string; weights_sha256: string }[];
  input_spec: AgentManifest["input"]["input_spec"];
  profile: "native-logical-v1";
  state_format_version: string;
  stream_generation: string;
  continuity_token: string;
  consumption_id: string;
  state_version: number;
  prefix: AgentPrefix;
  last_acknowledged_basis: {
    acquisition_id: string; capture_sha256: string; snapshot_id: string;
    owner_occurrence: Record<string, unknown>; revision: number;
    included: AgentManifest["input"]["attachment"]["eager_scope"];
    publication_index: string | null;
  };
}
export interface AgentStatePayload { encoding: "base64"; byte_count: number; sha256: string; data_base64: string }
export interface AgentOpaqueState { metadata: AgentStateMetadata; payload: AgentStatePayload }

export function canonicalStateMetadata(value: unknown): string {
  const visit = (item: unknown): unknown => {
    if (Array.isArray(item)) return item.map(visit);
    if (item !== null && typeof item === "object")
      return Object.fromEntries(Object.entries(item).sort(([left], [right]) => left.localeCompare(right)).map(([key, entry]) => [key, visit(entry)]));
    return item;
  };
  return encodeBoundedAgentJson(visit(value), 64 * 1024).toString("utf8");
}

export function validateAgentStateMetadata(value: unknown, manifest: AgentManifest): AgentStateMetadata {
  const metadata = sessionObject(value, ["agent_artifact_id", "agent_artifact_sha256", "adapter_code_sha256", "model_bindings", "input_spec", "profile", "state_format_version", "stream_generation", "continuity_token", "consumption_id", "state_version", "prefix", "last_acknowledged_basis"]);
  for (const key of ["agent_artifact_id", "state_format_version", "stream_generation", "continuity_token", "consumption_id"]) sessionText(metadata[key]);
  sessionDigest(metadata.agent_artifact_sha256); sessionDigest(metadata.adapter_code_sha256);
  sessionInteger(metadata.state_version, true); validateAgentInputSpec(metadata.input_spec);
  if (metadata.agent_artifact_id !== manifest.artifact.id || metadata.agent_artifact_sha256 !== manifest.artifact.sha256
    || metadata.adapter_code_sha256 !== manifest.adapter.code_sha256 || metadata.profile !== manifest.input.profile
    || metadata.state_format_version !== manifest.input.state_format_version
    || canonicalStateMetadata(metadata.input_spec) !== canonicalStateMetadata(manifest.input.input_spec)
    || canonicalStateMetadata(metadata.model_bindings) !== canonicalStateMetadata(manifest.input.state_recovery.model_bindings))
    throw new AgentSessionError("state_package_input_or_model_mismatch");
  const prefix = sessionObject(metadata.prefix, ["continuity_token", "history_mode", "consumption_mode", "received_cursor", "consumed_publication_index", "omissions"]);
  if (prefix.continuity_token !== metadata.continuity_token || prefix.history_mode !== manifest.input.history_mode
    || prefix.consumption_mode !== manifest.input.consumption_mode) throw new AgentSessionError("state_prefix_binding");
  if (prefix.received_cursor !== null) sessionText(prefix.received_cursor, 1024);
  if (prefix.consumed_publication_index !== null && (typeof prefix.consumed_publication_index !== "string" || !/^(0|[1-9][0-9]*)$/u.test(prefix.consumed_publication_index)))
    throw new AgentSessionError("state_publication_index");
  const omissions = sessionObject(prefix.omissions, ["received_unconsumed_count", "missing_scopes", "gap"]);
  if (omissions.received_unconsumed_count !== null) sessionInteger(omissions.received_unconsumed_count);
  validateAgentScope(omissions.missing_scopes);
  if (omissions.gap !== null) sessionObject(omissions.gap);
  const basis = sessionObject(metadata.last_acknowledged_basis, ["acquisition_id", "capture_sha256", "snapshot_id", "owner_occurrence", "revision", "included", "publication_index"]);
  sessionText(basis.acquisition_id); sessionText(basis.snapshot_id); sessionDigest(basis.capture_sha256);
  sessionInteger(basis.revision); validateAgentScope(basis.included); sessionObject(basis.owner_occurrence);
  if (basis.publication_index !== null && basis.publication_index !== prefix.consumed_publication_index) throw new AgentSessionError("state_basis_prefix_binding");
  return metadata as unknown as AgentStateMetadata;
}

/** Expected is obtained from durable acknowledged evidence, never the child's self-report. */
export function requireAgentStateMetadata(value: unknown, expected: AgentStateMetadata, manifest: AgentManifest): AgentStateMetadata {
  const metadata = validateAgentStateMetadata(value, manifest);
  validateAgentStateMetadata(expected, manifest);
  if (canonicalStateMetadata(metadata) !== canonicalStateMetadata(expected)) throw new AgentSessionError("state_durable_prefix_mismatch");
  return metadata;
}

export function validateAgentStatePayload(value: unknown, maximum: number): AgentStatePayload {
  const payload = sessionObject(value, ["encoding", "byte_count", "sha256", "data_base64"]);
  if (payload.encoding !== "base64") throw new AgentSessionError("state_encoding");
  const count = sessionInteger(payload.byte_count, true, maximum); sessionDigest(payload.sha256);
  if (typeof payload.data_base64 !== "string" || payload.data_base64.length !== Math.ceil(count / 3) * 4)
    throw new AgentSessionError("state_encoded_size");
  const text = payload.data_base64;
  const padding = count % 3 === 0 ? 0 : 3 - count % 3;
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  for (let index = 0; index < text.length; index += 1) {
    if (index >= text.length - padding ? text[index] !== "=" : alphabet.indexOf(text[index]!) < 0)
      throw new AgentSessionError("state_base64");
  }
  const last = padding === 0 ? 0 : alphabet.indexOf(text[text.length - padding - 1]!);
  if ((padding === 2 && (last & 15) !== 0) || (padding === 1 && (last & 3) !== 0)) throw new AgentSessionError("state_base64_noncanonical");
  return payload as unknown as AgentStatePayload;
}

/** Runtime only bounds, hashes and copies bytes. It never decodes a Model codec. */
export function acquireAgentStateBytes(payloadValue: unknown, maximum: number, budget: AgentByteBudget): { bytes: Buffer; reservation: AgentByteReservation } {
  const payload = validateAgentStatePayload(payloadValue, maximum);
  const reservation = budget.reserve(payload.byte_count);
  try {
    const bytes = Buffer.from(payload.data_base64, "base64");
    if (bytes.length !== payload.byte_count || createHash("sha256").update(bytes).digest("hex") !== payload.sha256)
      throw new AgentSessionError("state_payload_integrity");
    return { bytes, reservation };
  } catch (error) { reservation.release(); throw error; }
}

export function makeAgentStatePayload(bytes: Uint8Array, maximum: number): AgentStatePayload {
  sessionInteger(bytes.byteLength, true, maximum);
  const value = Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return { encoding: "base64", byte_count: value.length,
    sha256: createHash("sha256").update(value).digest("hex"), data_base64: value.toString("base64") };
}
