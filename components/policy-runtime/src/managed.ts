import { createHash, randomUUID } from "node:crypto";
import { lstat, readFile, realpath } from "node:fs/promises";
import { isAbsolute, resolve } from "node:path";
import { decodeTextMenuV2ActionResult, decodeTextMenuV2ObservationContext, type TextMenuV2Snapshot } from "@rsgcsg/sts2-connector-client";
import { canonicalJson } from "./evidence.js";
import { validateManagedEnvironmentBinding, type AnyDecisionBundle, type ManagedCapabilities, type ManagedControlConfirmation, type ManagedEnvironmentBinding, type ManagedEnvironmentStatus, type PolicyConnector, type TextInputProfile } from "./contracts.js";

const ATTACHMENT_SCHEMA = "sts2.host-runtime/managed-service-attachment-1";
const READY_SCHEMA = "sts2.host-runtime/managed-service-ready-1";
const RESULT_SCHEMA = "sts2.host-runtime/managed-service-result-1";
const TEXT_STATE_OWNER_CONTRACT = "sts2.host-runtime/text-menu-v2-owner-1";
const MAX_JSON_BYTES = 8 * 1024 * 1024;

type Json = Record<string, unknown>;
type Attachment = { endpoint: string; service_instance_id: string; host_identity: Json; token: string };
export type ManagedTarget = { serviceInstanceId: string; runtimeInstanceId: string; gameContinuityId: string };
type PrivateControl = { token: string; epoch: string; runtimeId: string; continuityId: string };

function object(value: unknown, label: string): Json {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be an object`);
  return value as Json;
}
function string(value: unknown, label: string): string {
  if (typeof value !== "string" || !value) throw new Error(`${label} must be a non-empty string`);
  return value;
}
function same(left: unknown, right: unknown): boolean { return canonicalJson(left) === canonicalJson(right); }
function subset(value: Json, keys: readonly string[]): Json {
  return Object.fromEntries(keys.map((key) => [key, value[key]]));
}
function vocabulary(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string" || !item)
      || new Set(value).size !== value.length) throw new Error(`${label} is invalid`);
  return value as string[];
}
function sha256(value: Buffer | string): string { return createHash("sha256").update(value).digest("hex"); }

async function ordinaryPath(path: string): Promise<void> {
  if (!isAbsolute(path) || resolve(path) !== path || await realpath(path) !== path) throw new Error("Managed path is not absolute and canonical");
  const stat = await lstat(path);
  if (!stat.isFile()) throw new Error("Managed path is not an ordinary file");
}

async function privateJson(path: string, maximum: number, privateMode: boolean): Promise<{ value: Json; bytes: Buffer }> {
  await ordinaryPath(path);
  const stat = await lstat(path);
  if (stat.size > maximum || (privateMode && process.platform !== "win32" && (stat.mode & 0o077) !== 0)) {
    throw new Error("Managed private descriptor is unsafe");
  }
  const bytes = await readFile(path);
  if (bytes.length > maximum) throw new Error("Managed private descriptor is oversized");
  return { value: object(JSON.parse(bytes.toString("utf8")), "Managed private descriptor"), bytes };
}

export async function loadManagedAttachment(bindingPath: string, attachmentPath: string): Promise<{
  binding: ManagedEnvironmentBinding; bindingSha256: string; attachment: Attachment;
}> {
  const { value: publicBinding } = await privateJson(bindingPath, 64 * 1024, false);
  const binding = validateManagedEnvironmentBinding(publicBinding);
  const { value: descriptor } = await privateJson(attachmentPath, 8 * 1024, true);
  const attachmentKeys = ["schema", "endpoint", "service_instance_id", "host_identity", "role", "token"];
  if (Object.keys(descriptor).length !== attachmentKeys.length || Object.keys(descriptor).some((key) => !attachmentKeys.includes(key))
      || descriptor.schema !== ATTACHMENT_SCHEMA || descriptor.role !== "client") {
    throw new Error("Managed attachment schema or role is invalid");
  }
  const endpoint = new URL(string(descriptor.endpoint, "Managed service endpoint"));
  if (endpoint.protocol !== "http:" || !["127.0.0.1", "[::1]", "::1"].includes(endpoint.hostname)
      || endpoint.pathname !== "/" || endpoint.search || endpoint.hash || endpoint.username || endpoint.password || !endpoint.port) {
    throw new Error("Managed service endpoint must be an explicit loopback origin");
  }
  const identity = object(descriptor.host_identity, "Managed attached Host identity");
  if (identity.package_name !== binding.host_package_identity.package
      || identity.version !== binding.host_package_identity.version
      || (identity.distribution_kind !== "installed_package" && identity.distribution_kind !== "git_checkout")
      || (identity.distribution_kind === "installed_package"
        ? identity.source_revision !== null || identity.component_tree_revision !== null
        : identity.source_revision !== binding.host_package_identity.source_revision
          || identity.component_tree_revision !== binding.host_package_identity.component_tree_revision)
      || !/^[a-f0-9]{64}$/u.test(String(identity.source_digest_sha256))) {
    throw new Error("Managed attachment Host identity differs from installed pin");
  }
  return { binding, bindingSha256: sha256(canonicalJson(binding)),
    attachment: { endpoint: endpoint.origin, service_instance_id: string(descriptor.service_instance_id, "service instance"),
      host_identity: identity, token: string(descriptor.token, "Managed client bearer") } };
}

async function boundedJson(response: Response): Promise<Json> {
  const chunks: Uint8Array[] = [];
  let total = 0;
  for await (const chunk of response.body ?? []) {
    total += chunk.byteLength;
    if (total > MAX_JSON_BYTES) throw new Error("Managed service response is oversized");
    chunks.push(chunk);
  }
  return object(JSON.parse(Buffer.concat(chunks).toString("utf8")), "Managed service response");
}

export class ManagedServicePolicyClient implements PolicyConnector {
  private control: PrivateControl | null = null;
  private cachedContext: Awaited<ReturnType<ManagedServicePolicyClient["observeTextMenuContext"]>> | null = null;
  private admission: ManagedEnvironmentStatus | null = null;
  private blockedMutations = false;
  private readonly textStateOwner: string;

  private constructor(private readonly attachment: Attachment,
                      readonly binding: ManagedEnvironmentBinding,
                      readonly bindingSha256: string,
                      private readonly target: ManagedTarget,
                      runId: string) {
    if (!/^run-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/u.test(runId)) {
      throw new Error("Managed Runtime run ID is invalid");
    }
    this.textStateOwner = `policy-runtime:${runId}`;
  }

  get initialEnvironment(): ManagedEnvironmentStatus {
    if (!this.admission) throw new Error("Managed environment was not admitted");
    return this.admission;
  }

  static async attach(bindingPath: string, attachmentPath: string, target: ManagedTarget,
                      runId: string): Promise<ManagedServicePolicyClient> {
    for (const value of [target.serviceInstanceId, target.runtimeInstanceId, target.gameContinuityId]) string(value, "Managed expected target");
    const verified = await loadManagedAttachment(bindingPath, attachmentPath);
    if (verified.attachment.service_instance_id !== target.serviceInstanceId) throw new Error("Managed service differs from explicit target");
    const client = new ManagedServicePolicyClient(verified.attachment, verified.binding,
      verified.bindingSha256, target, runId);
    await client.capabilities({ fresh: true, inputProfile: "text-menu-v2" });
    return client;
  }

  private async exchange(method: "GET" | "POST", route: string, body?: Json): Promise<Json> {
    const headers: Record<string, string> = { authorization: `Bearer ${this.attachment.token}` };
    if (method === "POST") {
      headers["content-type"] = "application/json";
      headers["x-sts2-managed-service-id"] = this.attachment.service_instance_id;
    }
    const response = await fetch(`${this.attachment.endpoint}${route}`, {
      method, headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(method === "GET" ? 10_000 : 65_000)
    });
    const value = await boundedJson(response);
    if (!response.ok) throw new Error(`Managed service rejected request: ${String(value.error ?? response.status)}`);
    if (value.service_instance_id !== this.attachment.service_instance_id) throw new Error("Managed service instance drift");
    return value;
  }

  private async command(command: string, payload: Json = {}): Promise<{ result: Json; controlBinding: Json | null }> {
    const requestId = `runtime_${randomUUID()}`;
    const wrapper = await this.exchange("POST", "/v1/command", { command, request_id: requestId, ...payload });
    if (wrapper.schema !== RESULT_SCHEMA) throw new Error("Managed service result schema drift");
    const result = object(wrapper.result, "Managed driver result");
    if (result.request_id !== requestId) throw new Error("Managed driver response request ID drift");
    return { result, controlBinding: wrapper.control_binding === undefined ? null : object(wrapper.control_binding, "Managed control binding") };
  }

  async capabilities(options?: { fresh?: boolean; inputProfile?: TextInputProfile }): Promise<ManagedCapabilities> {
    if (options?.inputProfile && options.inputProfile !== "text-menu-v2") throw new Error("Managed Runtime supports text-menu-v2 only");
    const ready = await this.exchange("GET", "/v1/ready");
    if (ready.schema !== READY_SCHEMA || !same(ready.host_identity, this.attachment.host_identity)) throw new Error("Managed Host ready identity drift");
    const episode = object(ready.episode, "Managed episode status");
    if (episode.closed !== false || episode.tainted !== false || episode.game_continuity_id !== this.target.gameContinuityId
        || ready.adapter_runtime_instance_id !== this.target.runtimeInstanceId) {
      throw new Error("Managed episode is unavailable or differs from explicit target");
    }
    const { result: identityResult } = await this.command("episode_identity");
    if (identityResult.type !== "episode_identity_result") throw new Error("Managed episode identity result drift");
    const identity = object(identityResult.identity, "Managed episode identity");
    const secondReady = await this.exchange("GET", "/v1/ready");
    if (secondReady.schema !== READY_SCHEMA || !same(secondReady.episode, episode)
        || secondReady.adapter_runtime_instance_id !== ready.adapter_runtime_instance_id
        || secondReady.environment_fingerprint !== ready.environment_fingerprint
        || !same(secondReady.host_identity, ready.host_identity)
        || !same(secondReady.candidate_build, ready.candidate_build)
        || secondReady.text_protocol_version !== ready.text_protocol_version
        || !same(secondReady.text_menu_contracts, ready.text_menu_contracts)) throw new Error("Managed episode changed during admission");
    const readyBuild = object(ready.candidate_build, "Managed ready candidate build");
    const contracts = ready.text_menu_contracts;
    if (!Array.isArray(contracts) || contracts.length !== 2) throw new Error("Managed text contract set is invalid");
    const v2 = object(contracts.find((item) => typeof item === "object" && item !== null
      && (item as Json).input_profile === "text-menu-v2"), "Managed text-menu-v2 contract");
    if (contracts.filter((item) => typeof item === "object" && item !== null
      && (item as Json).input_profile === "text-menu-v2").length !== 1
        || v2.protocol_version !== ready.text_protocol_version
        || v2.snapshot_schema !== "sts2.player-environment/text-menu-snapshot-2"
        || v2.receipt_schema !== "sts2.player-environment/text-menu-action-result-2"
        || v2.text_state_owner_contract !== TEXT_STATE_OWNER_CONTRACT) {
      throw new Error("Managed text-menu-v2 owner contract is unavailable or drifted");
    }
    const interactionKinds = vocabulary(v2.interaction_kinds, "Managed interaction kinds");
    const terminalKinds = vocabulary(v2.observed_terminal_kinds, "Managed observed terminal kinds");
    const actionVerbs = vocabulary(v2.action_verbs, "Managed action verbs");
    const auditKeys = Object.keys(this.binding.candidate_build);
    const build = subset(readyBuild, auditKeys) as ManagedEnvironmentBinding["candidate_build"];
    const host = object(ready.host_identity, "Managed Host ready identity");
    const hostIdentity = subset(host, ["package_name", "version", "distribution_kind", "source_revision", "component_tree_revision", "source_digest_sha256"]) as ManagedEnvironmentStatus["host_identity"];
    const game = object(ready.exact_game, "Managed exact game");
    const provenance = object(identity.episode_provenance, "Managed episode provenance");
    const runtimeId = string(ready.adapter_runtime_instance_id, "Managed runtime instance");
    const fingerprint = string(ready.environment_fingerprint, "Managed environment fingerprint");
    if (!same(build, this.binding.candidate_build) || !same(identity.candidate_build, ready.candidate_build)
        || !same(identity.runtime_identity, ready.runtime_identity)
        || identity.adapter_runtime_instance_id !== runtimeId
        || identity.environment_fingerprint !== fingerprint
        || game.sts2_dll_sha256 !== build.runtime_sts2_sha256
        || provenance.verdict !== "provenance_pass"
        || provenance.requested_seed !== provenance.actual_seed
        || provenance.runtime_instance_id !== runtimeId
        || !Array.isArray(ready.supported_input_profiles)
        || !ready.supported_input_profiles.includes("text-menu-v2")) {
      throw new Error("Managed ready and episode identity differ from exact binding");
    }
    const environment: ManagedEnvironmentStatus = {
      kind: "managed_text_v2", binding_sha256: this.bindingSha256,
      service_instance_id: this.attachment.service_instance_id, runtime_instance_id: runtimeId,
      environment_fingerprint: fingerprint, game_continuity_id: string(episode.game_continuity_id, "game continuity"),
      text_protocol_version: string(ready.text_protocol_version, "Managed text protocol"), input_profile: "text-menu-v2",
      host_package_identity: this.binding.host_package_identity, host_identity: hostIdentity,
      candidate_build: build, game_version: string(game.version, "game version"),
      game_commit: string(game.commit, "game commit"),
      game_assembly_sha256: string(game.sts2_dll_sha256, "game assembly"),
      episode_provenance: { verdict: "provenance_pass", requested_seed: string(provenance.requested_seed, "requested seed"),
        actual_seed: string(provenance.actual_seed, "actual seed"), runtime_instance_id: runtimeId }
    };
    if (this.control && (this.control.runtimeId !== runtimeId || this.control.continuityId !== environment.game_continuity_id
        || episode.control_held !== true)) throw new Error("Managed controller identity drift");
    this.admission = environment;
    return { kind: "managed_text_v2", protocol_version: environment.text_protocol_version, input_profile: "text-menu-v2",
      snapshot_schema: "sts2.player-environment/text-menu-snapshot-2",
      receipt_schema: "sts2.player-environment/text-menu-action-result-2",
      interaction_kinds: interactionKinds, observed_terminal_kinds: terminalKinds, action_verbs: actionVerbs,
      execution_available: !this.blockedMutations, control_held: episode.control_held === true,
      control_owned: this.control !== null, tainted: false, environment };
  }

  async observeTextMenuContext(inputProfile: TextInputProfile = "text-menu-v2") {
    if (inputProfile !== "text-menu-v2") throw new Error("Managed Runtime supports text-menu-v2 only");
    const control = this.control;
    const { result } = await this.command("text_observe", {
      input_profile: "text-menu-v2",
      ...(control ? { control_token: control.token, control_epoch: control.epoch } : {})
    });
    if (result.type !== "text_observe_result") throw new Error("Managed text observation result drift");
    const context = decodeTextMenuV2ObservationContext(result.context).data;
    const admitted = this.admission;
    if (!admitted || context.game_continuity_id !== admitted.game_continuity_id
        || context.snapshot.protocol_version !== admitted.text_protocol_version
        || context.snapshot.session.runtime_instance_id !== admitted.runtime_instance_id
        || context.snapshot.session.environment_fingerprint !== admitted.environment_fingerprint) {
      throw new Error("Managed text observation differs from admitted episode");
    }
    this.cachedContext = context;
    return context;
  }

  async observeBundle(requiredReadKinds: readonly string[], inputProfile?: TextInputProfile): Promise<AnyDecisionBundle> {
    if (requiredReadKinds.length || (inputProfile && inputProfile !== "text-menu-v2")) throw new Error("Managed text Reads or profile unsupported");
    const context = this.cachedContext ?? await this.observeTextMenuContext();
    this.cachedContext = null;
    return { observation: context.snapshot, reads: [] };
  }

  private confirmation(raw: Json | null, status: "held" | "released"): ManagedControlConfirmation {
    const admitted = this.admission;
    if (!raw || !admitted || raw.runtime_instance_id !== admitted.runtime_instance_id
        || raw.game_continuity_id !== admitted.game_continuity_id
        || typeof raw.control_epoch !== "string" || !raw.control_epoch) {
      throw new Error("Managed control binding differs from admitted episode");
    }
    return { status, service_instance_id: admitted.service_instance_id,
      runtime_instance_id: admitted.runtime_instance_id,
      game_continuity_id: admitted.game_continuity_id, control_epoch: raw.control_epoch };
  }

  async acquireController(): Promise<ManagedControlConfirmation> {
    if (this.blockedMutations) throw new Error("Managed mutation authority is quarantined");
    if (this.control) throw new Error("Managed controller is already held");
    const admitted = this.admission;
    if (!admitted) throw new Error("Managed environment was not admitted");
    try {
      const { result, controlBinding } = await this.command("claim_control", {
        text_state_owner: this.textStateOwner,
        expected_runtime_instance_id: admitted.runtime_instance_id,
        expected_game_continuity_id: admitted.game_continuity_id
      });
      if (result.type !== "claim_control_result") throw new Error("Managed claim result drift");
      if (result.text_state_owner !== this.textStateOwner) {
        throw new Error("Managed text state owner binding differs from the control lease");
      }
      const confirmation = this.confirmation(controlBinding, "held");
      if (result.control_epoch !== confirmation.control_epoch
          || result.runtime_instance_id !== confirmation.runtime_instance_id
          || result.game_continuity_id !== confirmation.game_continuity_id) throw new Error("Managed claim/result binding drift");
      this.control = { token: string(result.control_token, "Managed control token"), epoch: confirmation.control_epoch,
        runtimeId: confirmation.runtime_instance_id, continuityId: confirmation.game_continuity_id };
      return confirmation;
    } catch (error) { this.blockedMutations = true; throw error; }
  }

  async releaseController(): Promise<ManagedControlConfirmation> {
    const control = this.control;
    if (!control) throw new Error("Managed controller is not held");
    try {
      const { result, controlBinding } = await this.command("release_control", {
        control_token: control.token, control_epoch: control.epoch
      });
      const confirmation = this.confirmation(controlBinding, "released");
      if (result.type !== "release_control_result" || result.status !== "released"
          || result.control_epoch !== control.epoch || confirmation.control_epoch !== control.epoch
          || result.runtime_instance_id !== confirmation.runtime_instance_id
          || result.game_continuity_id !== confirmation.game_continuity_id) throw new Error("Managed release confirmation drift");
      this.control = null;
      return confirmation;
    } catch (error) { this.blockedMutations = true; throw error; }
  }

  async submit(input: { requestId: string; expectedSnapshotId: string; boundActionId: string;
                        inputProfile?: TextInputProfile; previousSnapshot?: TextMenuV2Snapshot }) {
    const control = this.control;
    if (!control || this.blockedMutations || input.inputProfile !== "text-menu-v2" || !input.previousSnapshot) {
      throw new Error("Managed text submission lacks an active confirmed controller and v2 Snapshot");
    }
    try {
      const { result, controlBinding } = await this.command("text_submit", {
        input_profile: "text-menu-v2", mutation_request_id: input.requestId,
        expected_snapshot_id: input.expectedSnapshotId, action_id: input.boundActionId,
        expected_game_continuity_id: control.continuityId,
        control_token: control.token, control_epoch: control.epoch
      });
      const confirmation = this.confirmation(controlBinding, "held");
      if (result.type !== "text_submit_result" || confirmation.control_epoch !== control.epoch) {
        throw new Error("Managed text submit control binding drift");
      }
      const decoded = decodeTextMenuV2ActionResult(result.result, input.previousSnapshot).data;
      if (decoded.protocol_version !== this.admission?.text_protocol_version
          || decoded.request_id !== input.requestId
          || (decoded.action !== null && decoded.action.action_id !== input.boundActionId)) {
        throw new Error("Managed text submit result request/action drift");
      }
      if (decoded.status === "unknown") this.blockedMutations = true;
      return decoded;
    } catch (error) { this.blockedMutations = true; throw error; }
  }
}
