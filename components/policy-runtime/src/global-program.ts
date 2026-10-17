import { createHash } from "node:crypto";
import { P1G_PROFILE, P1G_REVISION } from "@rsgcsg/sts2-connector-client/p1-global";
import { AGENT_SESSION_PROTOCOL, AGENT_LIMIT_MAXIMA, DEFAULT_AGENT_TIMEOUT_MS, type AgentManifest, type AgentAdapterIdentity } from "./agent-session-contracts.js";
import type { GlobalIdentity, GlobalExpectedEnvironment } from "./global-transport.js";
import { globalDigest } from "./global-transport.js";
import { p1CoreIdentity } from "./p1-global/core-identity.js";

/** Fixed model-neutral program descriptor, not a learned Input/Target specification. */
export const GLOBAL_PROGRAM_INPUT = Object.freeze({ id: "sts2-global-public-program-input", version: "1",
  sha256: globalDigest({ profile: P1G_PROFILE, revision: P1G_REVISION, view: "modelView", catalog: "complete_semantic_choices",
    context: "once_per_semantic_occurrence", state_recovery: "none", models: [] }) });
export function globalEnvironmentRequirement(identity: GlobalIdentity): GlobalExpectedEnvironment {
  return { kind: "global-public-basis", host_kind: identity.kind, producer_sha256: globalDigest(identity.producer), game_sha256: globalDigest(identity.game) };
}
export function createGlobalProgramManifest(input: { episodeId: string; identity: GlobalIdentity; artifactPath: string; artifactBytes: Uint8Array;
  adapter: AgentAdapterIdentity; manifestId?: string; agentTimeoutMs?: number }): AgentManifest {
  const agentTimeoutMs = input.agentTimeoutMs ?? DEFAULT_AGENT_TIMEOUT_MS;
  if (!Number.isSafeInteger(agentTimeoutMs) || agentTimeoutMs <= 0 || agentTimeoutMs > AGENT_LIMIT_MAXIMA.agent_timeout_ms)
    throw new Error(`agentTimeoutMs must be a positive integer at most ${AGENT_LIMIT_MAXIMA.agent_timeout_ms}`);
  return { schema: "sts2.policy-runtime/agent-manifest-1", manifest_id: input.manifestId ?? `global-${input.episodeId}`,
    agent: { id: "global-public-program", version: "1", provider: "project-program", architecture: "model-less-program" },
    artifact: { id: "global-public-program", path: input.artifactPath, sha256: createHash("sha256").update(input.artifactBytes).digest("hex") }, adapter: input.adapter,
    input: { profile: P1G_PROFILE, global: { revision: P1G_REVISION, core_sha256: p1CoreIdentity().module_digest },
      input_spec: GLOBAL_PROGRAM_INPUT, projection: { id: P1G_PROFILE, version: P1G_REVISION }, state_format_version: "program-1",
      state_recovery: { mode: "none", max_state_bytes: 0, model_bindings: [] }, history_mode: "full_reference",
      consumption_mode: "once_per_occurrence", gap_policy: "handoff", attachment: { eager_scope: [], required_seams: [
        { source_seam: "public-basis-1", version: "1", coverage: "complete_at_seam" }], delivery_mode: "full_reference" } },
    requirements: { connector_protocol_version: input.identity.protocol_version, environment: globalEnvironmentRequirement(input.identity), required_methods: ["capabilities", "capture", "submit"] },
    support: { game_versions: [String(input.identity.game.version)], game_commits: [String(input.identity.game.commit)], interaction_kinds: ["*"], action_verbs: ["*"] },
    limits: { ...AGENT_LIMIT_MAXIMA, agent_timeout_ms: agentTimeoutMs }, claims: { catalog_filtered: false, creates_action_authority: false, creates_native_operands: false, human_origin: false, causal_successor: false } };
}
export const GLOBAL_PROGRAM_PROTOCOL = AGENT_SESSION_PROTOCOL;
