const POLICY_RUNTIME_STATUS_SCHEMA = "sts2.policy-runtime/status-1";
const POLICY_RUNTIME_HTTP_SCHEMA = "sts2.policy-runtime/http-2";

export const POLICY_RUNTIME_MODES = Object.freeze(["human", "shadow", "one_step", "auto"]);
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

export class PolicyRuntimeError extends Error {
  constructor(message, code = "policy_runtime_unavailable") {
    super(message);
    this.name = "PolicyRuntimeError";
    this.code = code;
  }
}

export function validatePolicyRuntimeBaseUrl(value) {
  if (typeof value !== "string" || value.trim() === "") return null;
  let parsed;
  try { parsed = new URL(value); } catch { throw new TypeError("Policy Runtime URL must be a valid URL"); }
  if (parsed.protocol !== "http:" || parsed.username || parsed.password || !LOOPBACK_HOSTS.has(parsed.hostname)) {
    throw new TypeError("Policy Runtime URL must target loopback HTTP without credentials");
  }
  parsed.hash = "";
  parsed.search = "";
  parsed.pathname = parsed.pathname.replace(/\/+$/u, "");
  return parsed.toString().replace(/\/$/u, "");
}

export function decodePolicyRuntimeStatus(value) {
  const status = object(value, "Policy Runtime status");
  const keys = [
    "schema", "runtime", "policy", "run_id", "lifecycle", "mode", "controller", "tainted",
    "taint_reason", "refreshing", "last_snapshot_id", "last_snapshot", "last_decision",
    "last_receipt", "reads", "invalidations", "errors", "environment"
  ];
  if (Object.hasOwn(status, "autonomy_budget")) keys.push("autonomy_budget");
  exactKeys(status, keys, "Policy Runtime status");
  if (status.schema !== POLICY_RUNTIME_STATUS_SCHEMA) invalid("Policy Runtime status schema is unsupported");
  const runtime = object(status.runtime, "Policy Runtime runtime");
  exactKeys(runtime, ["version", "code_sha256"], "Policy Runtime runtime");
  nonEmpty(runtime.version, "runtime.version");
  sha256OrNull(runtime.code_sha256, "runtime.code_sha256");
  if (!POLICY_RUNTIME_MODES.includes(status.mode)) invalid("Policy Runtime mode is unsupported");
  if (!new Set(["held", "released"]).has(status.controller)) invalid("Policy Runtime controller is unsupported");
  if (!new Set(["running", "stopped"]).has(status.lifecycle)) invalid("Policy Runtime lifecycle is unsupported");
  if (typeof status.tainted !== "boolean" || typeof status.refreshing !== "boolean") invalid("Policy Runtime status flags are invalid");
  if (status.autonomy_budget !== undefined) validateAutonomyBudget(status.autonomy_budget);
  stringOrNull(status.taint_reason, "taint_reason");
  stringOrNull(status.last_snapshot_id, "last_snapshot_id");
  const policy = object(status.policy, "Policy Runtime policy");
  exactKeys(policy, ["manifest_id", "policy_id", "policy_version", "provider", "architecture", "artifact_sha256"], "Policy Runtime policy");
  for (const key of ["manifest_id", "policy_id", "policy_version", "provider", "architecture"]) nonEmpty(policy[key], `policy.${key}`);
  if (typeof policy.artifact_sha256 !== "string" || !/^[a-f0-9]{64}$/u.test(policy.artifact_sha256)) invalid("policy artifact SHA-256 is invalid");
  nonEmpty(status.run_id, "run_id");
  if (status.last_snapshot !== null) {
    const snapshot = object(status.last_snapshot, "Policy Runtime last_snapshot");
    exactKeys(snapshot, ["snapshot_id", "sequence", "status", "runtime_instance_id", "environment_fingerprint"], "Policy Runtime last_snapshot");
    nonEmpty(snapshot.snapshot_id, "last_snapshot.snapshot_id");
    if (!Number.isSafeInteger(snapshot.sequence) || snapshot.sequence < 1) invalid("last_snapshot.sequence is invalid");
    if (!new Set(["interactive", "visible_unsupported", "settling", "observed"]).has(snapshot.status)) invalid("last_snapshot.status is invalid");
    nonEmpty(snapshot.runtime_instance_id, "last_snapshot.runtime_instance_id");
    nonEmpty(snapshot.environment_fingerprint, "last_snapshot.environment_fingerprint");
  }
  if (status.last_decision !== null) {
    const decision = object(status.last_decision, "Policy Runtime last_decision");
    exactKeys(decision, ["decision_id", "candidate_digest", "candidate_count", "scores", "selected_index", "bound_action_id", "bound_action_label"], "Policy Runtime last_decision");
    nonEmpty(decision.decision_id, "last_decision.decision_id");
    if (typeof decision.candidate_digest !== "string" || !/^[a-f0-9]{64}$/u.test(decision.candidate_digest)) invalid("last_decision.candidate_digest is invalid");
    if (!Number.isSafeInteger(decision.candidate_count) || decision.candidate_count < 0 || !Array.isArray(decision.scores) || decision.scores.length !== decision.candidate_count || decision.scores.some((score) => typeof score !== "number" || !Number.isFinite(score))) invalid("last_decision score alignment is invalid");
    if (decision.selected_index !== null && (!Number.isSafeInteger(decision.selected_index) || decision.selected_index < 0 || decision.selected_index >= decision.candidate_count)) invalid("last_decision.selected_index is invalid");
    stringOrNull(decision.bound_action_id, "last_decision.bound_action_id");
    stringOrNull(decision.bound_action_label, "last_decision.bound_action_label");
  }
  if (status.last_receipt !== null) {
    const receipt = object(status.last_receipt, "Policy Runtime last_receipt");
    exactKeys(receipt, ["request_id", "delivery", "reason_code", "successor_snapshot_id"], "Policy Runtime last_receipt");
    nonEmpty(receipt.request_id, "last_receipt.request_id");
    if (!new Set(["delivered", "not_delivered", "unknown"]).has(receipt.delivery)) invalid("last_receipt.delivery is invalid");
    stringOrNull(receipt.reason_code, "last_receipt.reason_code");
    stringOrNull(receipt.successor_snapshot_id, "last_receipt.successor_snapshot_id");
  }
  if (!Array.isArray(status.reads)) invalid("reads must be an array");
  for (const [index, value] of status.reads.entries()) {
    const read = object(value, `Policy Runtime reads[${index}]`);
    exactKeys(read, ["read_id", "kind", "content_schema", "target_referent_id"], `Policy Runtime reads[${index}]`);
    for (const key of ["read_id", "kind", "content_schema"]) nonEmpty(read[key], `reads[${index}].${key}`);
    stringOrNull(read.target_referent_id, `reads[${index}].target_referent_id`);
  }
  for (const key of ["invalidations", "errors"]) {
    if (!Array.isArray(status[key]) || status[key].some((item) => typeof item !== "string")) invalid(`${key} must be an array of strings`);
  }
  if (status.environment !== null) {
    const environment = object(status.environment, "Policy Runtime environment");
    exactKeys(environment, ["runtime_instance_id", "environment_fingerprint", "host_kind", "connector_protocol_version", "connector_version", "connector_source_revision", "connector_artifact_sha256", "connector_module_version_id", "game_version", "game_commit", "modset_status", "modset_fingerprint", "loaded_mod_ids"], "Policy Runtime environment");
    if (!["live_ui", "headless", "replay", "test"].includes(environment.host_kind)) invalid("environment.host_kind is invalid");
    if (!Array.isArray(environment.loaded_mod_ids) || environment.loaded_mod_ids.some((id) => typeof id !== "string" || id.length === 0) || new Set(environment.loaded_mod_ids).size !== environment.loaded_mod_ids.length) invalid("environment.loaded_mod_ids is invalid");
    for (const key of ["runtime_instance_id", "environment_fingerprint", "connector_protocol_version", "connector_version", "modset_status", "modset_fingerprint"]) nonEmpty(environment[key], `environment.${key}`);
    for (const key of ["connector_source_revision", "connector_module_version_id", "game_version", "game_commit"]) stringOrNull(environment[key], `environment.${key}`);
    if (environment.connector_artifact_sha256 !== null && (typeof environment.connector_artifact_sha256 !== "string" || !/^[a-f0-9]{64}$/u.test(environment.connector_artifact_sha256))) invalid("environment.connector_artifact_sha256 is invalid");
  }
  return status;
}

function validateAutonomyBudget(value) {
  const budget = object(value, "Policy Runtime autonomy_budget");
  exactKeys(budget, ["state", "max_submissions", "submissions_used", "max_policy_calls", "policy_calls_used", "deadline_ms", "elapsed_ms", "remaining_ms", "exhausted_reason", "ended_reason"], "Policy Runtime autonomy_budget");
  if (!new Set(["inactive", "active", "exhausted"]).has(budget.state)) invalid("autonomy_budget.state is unsupported");
  for (const key of ["max_submissions", "submissions_used", "max_policy_calls", "policy_calls_used", "deadline_ms", "elapsed_ms", "remaining_ms"]) {
    if (!Number.isSafeInteger(budget[key]) || budget[key] < 0 || (["max_submissions", "max_policy_calls", "deadline_ms"].includes(key) && budget[key] < 1)) invalid(`autonomy_budget.${key} is invalid`);
  }
  if (budget.submissions_used > budget.max_submissions || budget.policy_calls_used > budget.max_policy_calls || budget.remaining_ms > budget.deadline_ms) invalid("autonomy_budget counters are invalid");
  if (!["submission_attempt_limit", "policy_call_limit", "deadline", null].includes(budget.exhausted_reason)) invalid("autonomy_budget.exhausted_reason is unsupported");
  if (!["human_recovery", "mode_changed", "stopped", null].includes(budget.ended_reason)) invalid("autonomy_budget.ended_reason is unsupported");
}

export function validatePolicyMode(value) {
  if (!POLICY_RUNTIME_MODES.includes(value)) throw new PolicyRuntimeError(`Unsupported Policy Runtime mode: ${String(value)}`, "policy_runtime_invalid_mode");
  return value;
}

function decodeHttpStatus(value) {
  const envelope = object(value, "Policy Runtime HTTP response");
  exactKeys(envelope, ["schema", "status"], "Policy Runtime HTTP response");
  if (envelope.schema !== POLICY_RUNTIME_HTTP_SCHEMA) invalid("Policy Runtime HTTP schema is unsupported");
  return decodePolicyRuntimeStatus(envelope.status);
}

function decodeHttpTick(value) {
  const envelope = object(value, "Policy Runtime tick response");
  exactKeys(envelope, ["schema", "results", "status"], "Policy Runtime tick response");
  if (envelope.schema !== `${POLICY_RUNTIME_HTTP_SCHEMA}/tick-1`) invalid("Policy Runtime tick schema is unsupported");
  if (!Array.isArray(envelope.results) || envelope.results.length !== 1) invalid("Policy Runtime one-step response must contain exactly one result");
  const result = object(envelope.results[0], "Policy Runtime tick result");
  if (typeof result.type !== "string" || result.type.length === 0) invalid("Policy Runtime tick result type is invalid");
  return { result, status: decodePolicyRuntimeStatus(envelope.status) };
}

async function readJson(response) {
  const raw = await response.text();
  try { return JSON.parse(raw); } catch { invalid("Policy Runtime returned invalid JSON"); }
}

export class PolicyRuntimeClient {
  constructor(baseUrl, options = {}) {
    this.baseUrl = validatePolicyRuntimeBaseUrl(baseUrl);
    this.timeoutMs = options.timeoutMs ?? 1500;
    this.commandTimeoutMs = options.commandTimeoutMs ?? 45000;
    this.commandOutcomeUnknown = false;
    this.unknownRunId = null;
    this.unknownGeneration = 0;
    this.statusOperation = Promise.resolve();
    this.runId = null;
    if (!Number.isSafeInteger(this.timeoutMs) || this.timeoutMs < 1) throw new TypeError("Policy Runtime timeoutMs must be a positive integer");
    if (!Number.isSafeInteger(this.commandTimeoutMs) || this.commandTimeoutMs < 1) throw new TypeError("Policy Runtime commandTimeoutMs must be a positive integer");
  }

  get configured() { return this.baseUrl !== null; }

  async request(pathname, init = {}) {
    if (!this.baseUrl) throw new PolicyRuntimeError("Policy Runtime URL is not configured", "policy_runtime_not_configured");
    const controller = new AbortController();
    const mutation = init.method === "POST";
    const timer = setTimeout(() => controller.abort(), mutation ? this.commandTimeoutMs : this.timeoutMs);
    try {
      const response = await fetch(`${this.baseUrl}${pathname}`, { ...init, signal: controller.signal, headers: { accept: "application/json", ...(init.headers ?? {}) } });
      const value = await readJson(response);
      if ([409, 428].includes(response.status) && value?.schema === POLICY_RUNTIME_HTTP_SCHEMA && ["runtime_run_precondition_required", "runtime_run_mismatch"].includes(value.error)) {
        throw new PolicyRuntimeError(`Policy Runtime rejected the command before dispatch: ${value.error}`, "policy_runtime_run_rejected");
      }
      if (!response.ok) throw new PolicyRuntimeError(`Policy Runtime returned HTTP ${response.status}`, "policy_runtime_unavailable");
      return value;
    } catch (error) {
      if (error instanceof PolicyRuntimeError) throw error;
      const detail = error?.name === "AbortError" ? "request timed out" : error?.message ?? String(error);
      throw new PolicyRuntimeError(`Policy Runtime request failed: ${detail}`);
    } finally { clearTimeout(timer); }
  }

  async readStatus() {
    const read = async () => {
      const wasUnknown = this.commandOutcomeUnknown;
      const generation = this.unknownGeneration;
      const status = decodeHttpStatus(await this.request("/status"));
      // Only a GET begun after the uncertain command may observe replacement.
      // The owning server restricts the command to the exact requested run.
      if (wasUnknown && this.commandOutcomeUnknown && generation === this.unknownGeneration) {
        if (this.unknownRunId === null) this.unknownRunId = status.run_id;
        else if (this.unknownRunId !== status.run_id) {
          this.commandOutcomeUnknown = false;
          this.unknownRunId = null;
        }
      }
      this.runId = status.run_id;
      return status;
    };
    const operation = this.statusOperation.then(read, read);
    this.statusOperation = operation.then(() => undefined, () => undefined);
    return operation;
  }

  assertCommandAvailable() {
    if (this.commandOutcomeUnknown) throw new PolicyRuntimeError("A previous Runtime command has an unknown outcome; do not retry. Return to Human and start a new Runtime run.", "policy_runtime_command_unknown");
  }

  async command(pathname, body, decode, expectedRunId = null) {
    if (expectedRunId === null) {
      if (this.runId === null) await this.readStatus();
      expectedRunId = this.runId;
    }
    try {
      const decoded = decode(await this.request(`/v2${pathname}`, {
        method: "POST", headers: { "content-type": "application/json", "x-sts2-policy-run-id": expectedRunId }, body: JSON.stringify(body)
      }));
      if ((decoded.status ?? decoded).run_id !== expectedRunId) invalid("Policy Runtime command response changed run identity");
      return decoded;
    } catch (error) {
      if (error instanceof PolicyRuntimeError && error.code === "policy_runtime_run_rejected") throw error;
      this.commandOutcomeUnknown = true;
      this.unknownRunId = expectedRunId;
      this.unknownGeneration += 1;
      throw new PolicyRuntimeError("The Runtime command may still complete. Query status, return to Human, and start a new Runtime run before another policy command; do not retry.", "policy_runtime_command_unknown");
    }
  }

  async setMode(mode) {
    validatePolicyMode(mode);
    if (mode !== "human") this.assertCommandAvailable();
    const status = await this.command("/mode", { mode }, decodeHttpStatus);
    this.runId = status.run_id;
    return { schema: POLICY_RUNTIME_HTTP_SCHEMA, mode, status };
  }


  async tick(expectedRunId = null) {
    this.assertCommandAvailable();
    return this.command("/tick", { max_ticks: 1 }, decodeHttpTick, expectedRunId);
  }
}

function object(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) invalid(`${label} must be an object`);
  return value;
}
function exactKeys(value, keys, label) {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) invalid(`${label} has an unsupported schema shape`);
}
function nonEmpty(value, label) { if (typeof value !== "string" || value.length === 0) invalid(`${label} must be a non-empty string`); }
function stringOrNull(value, label) { if (value !== null && typeof value !== "string") invalid(`${label} must be a string or null`); }
function sha256OrNull(value, label) { if (value !== null && (typeof value !== "string" || !/^[a-f0-9]{64}$/u.test(value))) invalid(`${label} must be a lowercase SHA-256 or null`); }
function invalid(message) { throw new PolicyRuntimeError(message, "policy_runtime_invalid_schema"); }

export { POLICY_RUNTIME_HTTP_SCHEMA, POLICY_RUNTIME_STATUS_SCHEMA };
