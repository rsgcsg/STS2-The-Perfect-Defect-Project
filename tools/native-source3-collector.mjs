#!/usr/bin/env node
/** Collect-only application composition over public Host/Agent Runtime owners. */
import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir, readdir, open } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import { spawn } from "node:child_process";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const PIPE_SCHEMA = "spireagent/native-source3-collector-pipe-v2";
export const MAX_PIPE_BYTES = 96 * 1024 * 1024;
const MAX_CONTROL_BYTES = 16 * 1024;
const hash = value => createHash("sha256").update(value).digest("hex");
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const exact = (value, fields) => {
  if (!object(value) || Object.keys(value).length !== fields.length
      || fields.some(field => !Object.hasOwn(value, field))) throw new Error("invalid_pipe_fields");
};
const integer = (value, minimum, maximum) => Number.isSafeInteger(value)
  && value >= minimum && value <= maximum;
const id = value => typeof value === "string" && /^[0-9a-f]{32}$/u.test(value);
const reasonCode = error => typeof error?.message === "string"
  && /^[a-z0-9_]{1,128}$/u.test(error.message) ? error.message : "collector_owner_failed";
const failureDetail = (error, depth = 0) => {
  if (!(error instanceof Error)) return { name: "NonError", message: "non_error_throw" };
  const detail = { name: error.name.slice(0, 128), message: error.message.slice(0, 2048),
    stack: typeof error.stack === "string" ? error.stack.slice(0, 8192) : null };
  if (depth < 2 && Object.hasOwn(error, "cause"))
    detail.cause = failureDetail(error.cause, depth + 1);
  if (depth < 2 && error instanceof AggregateError)
    detail.causes = error.errors.slice(0, 3).map(cause => failureDetail(cause, depth + 1));
  return detail;
};

export function validateOptions(value) {
  exact(value, ["installation", "host_local_root", "output", "endpoint", "seed", "template_id",
    "target_choices", "max_submissions", "deadline_ms", "max_input_bytes", "max_diagnostic_bytes",
    "experimental_build_acknowledged", "experimental_connector_acknowledged", "record_source3",
    "python_executable", "teacher_artifact", "teacher_descriptor"]);
  for (const key of ["installation", "host_local_root", "output"])
    if (typeof value[key] !== "string" || !path.isAbsolute(value[key])) throw new Error("absolute_path_required");
  const endpoint = new URL(value.endpoint);
  if (endpoint.protocol !== "http:" || !["localhost", "127.0.0.1"].includes(endpoint.hostname)
      || endpoint.username || endpoint.password || endpoint.search || endpoint.hash
      || !["", "/"].includes(endpoint.pathname)) throw new Error("loopback_endpoint_required");
  if (typeof value.seed !== "string" || !value.seed || value.seed.length > 128
      || value.template_id !== "defect-a0-s0") throw new Error("fixed_episode_request_required");
  if (!integer(value.target_choices, 1, 300) || !integer(value.max_submissions, 1, 300)
      || value.target_choices > value.max_submissions || !integer(value.deadline_ms, 1, 2_700_000)
      || !integer(value.max_input_bytes, 1024, 64 * 1024 * 1024)
      || !integer(value.max_diagnostic_bytes, 1024, 768 * 1024 * 1024)
      || typeof value.experimental_build_acknowledged !== "boolean"
      || typeof value.experimental_connector_acknowledged !== "boolean"
      || typeof value.record_source3 !== "boolean" || typeof value.python_executable !== "string"
      || !path.isAbsolute(value.python_executable) || !object(value.teacher_artifact)
      || !object(value.teacher_descriptor) || value.deadline_ms > 900_000
      || value.max_submissions > 100 || value.target_choices > 100) throw new Error("finite_collection_limits_required");
  return value;
}

/** Cancellation exists before init/import/Host launch, and remains idempotent on second signal. */
export function createCollectionLifetime() {
  const abort = new AbortController();
  const listeners = new Set();
  let reason = null, stops = 0;
  return {
    signal: abort.signal,
    get reason() { return reason; },
    get stops() { return stops; },
    stop(code = "external_stop") {
      stops++;
      if (reason !== null) return;
      reason = code;
      abort.abort(new Error(code));
      for (const listener of listeners) listener();
    },
    onStop(listener) {
      listeners.add(listener);
      if (reason !== null) listener();
      return () => listeners.delete(listener);
    },
  };
}

/** Fixed bounded duplex pipe; SDK's strict JSON parser is injected, never request-selected. */
export function createPipePeer(input, output, parseJson, lifetime) {
  let buffer = Buffer.alloc(0), closed = false;
  const queue = [], waiters = [];
  const retired = new Map();
  const rejectAll = error => { for (const waiter of waiters.splice(0)) waiter.reject(error); };
  const dispatch = value => {
    const retiredReply = retired.get(value.message_id);
    if (retiredReply && value.schema === PIPE_SCHEMA && value.operation_id === retiredReply.operationId
        && value.type === retiredReply.responseType) {
      retired.delete(value.message_id); return;
    }
    if (value.type === "stop") {
      exact(value, ["schema", "type", "operation_id", "reason"]);
      if (value.schema !== PIPE_SCHEMA || !id(value.operation_id)
          || typeof value.reason !== "string") throw new Error("invalid_stop_message");
      lifetime.stop("parent_stop");
      return;
    }
    if (waiters.length) waiters.shift().resolve(value);
    else if (queue.length === 0) queue.push(value);
    else throw new Error("unexpected_extra_pipe_message");
  };
  input.on("data", bytes => {
    try {
      if (buffer.length + bytes.length > MAX_PIPE_BYTES) throw new Error("pipe_line_capacity_exceeded");
      buffer = Buffer.concat([buffer, bytes]);
      for (;;) {
        const newline = buffer.indexOf(10);
        if (newline < 0) break;
        const line = buffer.subarray(0, newline);
        buffer = buffer.subarray(newline + 1);
        if (!line.length) throw new Error("empty_pipe_message");
        const parsed = parseJson(new TextDecoder("utf-8", { fatal: true }).decode(line));
        if (line.length > MAX_CONTROL_BYTES && !["runtime_gate", "runtime_tick", "quiesced", "closed"].includes(parsed.type))
          throw new Error("parent_control_capacity");
        dispatch(parsed);
      }
    } catch (error) { closed = true; lifetime.stop("pipe_protocol_failed"); rejectAll(error); }
  });
  const eof = () => { closed = true; lifetime.stop("parent_eof"); rejectAll(new Error("parent_eof")); };
  input.on("end", eof); input.on("error", eof); output.on("error", eof);
  return {
    get closed() { return closed; },
    retireReply(operationId, messageId, responseType) {
      if (retired.size >= 64) throw new Error("retired_reply_capacity");
      retired.set(messageId, { operationId, responseType });
      const index = queue.findIndex(value => value.schema === PIPE_SCHEMA && value.operation_id === operationId
        && value.message_id === messageId && value.type === responseType);
      if (index >= 0) { queue.splice(index, 1); retired.delete(messageId); }
    },
    async send(value) {
      const bytes = Buffer.from(JSON.stringify(value) + "\n", "utf8");
      const maximum = ["runtime_gate", "runtime_tick", "quiesced"].includes(value.type) ? MAX_PIPE_BYTES : MAX_CONTROL_BYTES;
      if (bytes.length > maximum || closed) throw new Error("pipe_write_unavailable");
      await new Promise((resolve, reject) => output.write(bytes, error => error ? reject(error) : resolve()));
    },
    async receive({ allowStopped = false, timeoutMs = 20_000 } = {}) {
      if (closed) throw new Error("parent_eof");
      if (!allowStopped) lifetime.signal.throwIfAborted();
      if (queue.length) return queue.shift();
      return new Promise((resolve, reject) => {
        const waiter = { resolve, reject };
        let timer;
        const finish = (method, value) => {
          clearTimeout(timer);
          lifetime.signal.removeEventListener("abort", abort);
          const index = waiters.indexOf(waiter);
          if (index >= 0) waiters.splice(index, 1);
          method(value);
        };
        waiter.resolve = value => finish(resolve, value);
        waiter.reject = error => finish(reject, error);
        const abort = () => waiter.reject(new Error(lifetime.reason || "external_stop"));
        timer = setTimeout(() => waiter.reject(new Error("pipe_reply_timeout")), timeoutMs);
        if (!allowStopped) lifetime.signal.addEventListener("abort", abort, { once: true });
        waiters.push(waiter);
      });
    },
  };
}

async function defaultDependencies() {
  const sdk = await import(pathToFileURL(path.join(ROOT, "components/connector/sdk/typescript/dist/index.js")));
  const host = await import(pathToFileURL(path.join(ROOT, "components/host-runtime/src/shipped-player-environment.mjs")));
  const runtime = await import(pathToFileURL(path.join(ROOT, "components/policy-runtime/dist/index.js")));
  const installation = await import(pathToFileURL(path.join(ROOT, "components/host-runtime/src/game-installation.mjs")));
  const bytes = await readFile(path.join(ROOT, "components/connector/contracts/native-logical-publication-profile-v1.json"));
  if (hash(bytes) !== sdk.NATIVE_LOGICAL_PUBLICATION_PROFILE_SHA256)
    throw new Error("fixed_native_profile_changed");
  return { ...sdk, ...runtime, startEpisode: host.startShippedPlayerEnvironmentEpisode,
    resolveInstallation: installation.resolveInstallation,
    runtimeIdentity: { version: runtime.POLICY_RUNTIME_VERSION,
      code_sha256: await compiledIdentity(path.join(ROOT, "components/policy-runtime/dist")) } };
}

function safeHostIdentity(identity) {
  // Only public identity/provenance returned by the Host; never environment or launch tokens.
  return { host: identity.host, game: identity.game, profile: identity.profile,
    episode_provenance: identity.episode_provenance,
    requested_seed: identity.requested_seed, requested_character_id: identity.requested_character_id,
    requested_ascension: identity.requested_ascension };
}

/** Exact compiled identity uses the existing Runtime CLI's directory grammar. */
async function compiledIdentity(directory) {
  const names = (await readdir(directory)).filter(name => name.endsWith(".js")).sort();
  if (!names.length) throw new Error("compiled_runtime_unavailable");
  const digest = createHash("sha256");
  for (const name of names) digest.update(name).update("\0").update(await readFile(path.join(directory, name))).update("\0");
  return digest.digest("hex");
}

function teacherManifest(options, capabilities, deps) {
  const descriptor = options.teacher_descriptor;
  exact(descriptor, ["schema", "agent_spec", "input_spec_body", "input_spec", "adapter", "code_files", "runtime_provenance"]);
  if (descriptor.schema !== "stpd/native-program-teacher-artifact-v1"
      || descriptor.agent_spec.learned !== false || descriptor.agent_spec.scores !== null
      || descriptor.agent_spec.model_bindings?.length !== 0)
    throw new Error("explicit_program_teacher_required");
  const c = capabilities, artifact = options.teacher_artifact;
  exact(artifact, ["id", "path", "sha256"]);
  if (!path.isAbsolute(artifact.path) || path.dirname(artifact.path) !== options.output)
    throw new Error("owned_teacher_artifact_path_required");
  return deps.validateAgentManifest({ schema: "sts2.policy-runtime/agent-manifest-1",
    manifest_id: "native-program-teacher-" + artifact.sha256.slice(0, 16),
    agent: { id: descriptor.agent_spec.id, version: descriptor.agent_spec.version, provider: "stpd",
      architecture: "explicit_native_public_program_teacher" }, adapter: descriptor.adapter, artifact,
    input: { profile: "native-logical-v1", input_spec: descriptor.input_spec,
      projection: { id: "stpd-native-program-teacher-current-v1", version: "1.0.0" },
      state_format_version: "stpd/native-program-teacher-state-v1",
      state_recovery: { mode: "none", max_state_bytes: 0, model_bindings: [] },
      history_mode: "sampled_current", consumption_mode: "once_per_occurrence", gap_policy: "handoff",
      attachment: { eager_scope: [], required_seams: deps.NATIVE_LOGICAL_PUBLICATION_PROFILE.required_seams,
        delivery_mode: "scoped" } },
    requirements: { connector_protocol_version: c.protocol_version,
      environment: { host_kind: c.host.host_kind, connector_version: c.host.version,
        connector_source_revision: c.host.implementation.source_revision,
        connector_artifact_sha256: c.host.implementation.artifact_sha256,
        connector_module_version_id: c.host.implementation.module_version_id,
        modset_status: c.game.modset.status, modset_fingerprint: c.game.modset.fingerprint,
        loaded_mod_ids: c.game.modset.loaded_mod_ids },
      required_methods: ["capabilities", "attach", "current", "read", "catalog", "submit", "result",
        "events", "await", "cancel_wait", "detach", "renew", "retain", "release"] },
    support: { game_versions: [c.game.version], game_commits: [c.game.commit],
      interaction_kinds: ["*"], action_verbs: ["*"] },
    limits: { max_message_bytes: MAX_PIPE_BYTES, max_acquisitions: 256,
      max_retained_acquisition_bytes: 256 * 1024 * 1024, max_pending_queries: 8,
      max_queries_per_turn: 8, max_query_bytes_per_turn: 128 * 1024 * 1024,
      max_capture_bytes: Math.min(options.max_input_bytes, 8 * 1024 * 1024), max_catalog_actions: 16384,
      max_cancelled_ids: 256, agent_timeout_ms: 30_000 },
    claims: { catalog_filtered: false, creates_action_authority: false, creates_native_operands: false,
      human_origin: false, causal_successor: false } });
}

/** One static program child, with actual process receipts outside the public port. */
function startTeacher(options, manifestPath, manifest, deps) {
  const environment = {};
  for (const name of ["PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP"])
    if (process.env[name] !== undefined) environment[name] = process.env[name];
  environment.PYTHONPATH = path.join(ROOT, "python");
  const child = spawn(options.python_executable, ["-m", "stpd.policy.native_teacher_agent",
    "--manifest", manifestPath], { cwd: ROOT, env: environment, stdio: ["pipe", "pipe", "pipe"] });
  const stderr = [], stdout = [];
  let stdoutBytes = 0;
  child.stdout.on("data", bytes => {
    const retained = bytes.subarray(0, Math.max(0, 64 * 1024 - stdoutBytes));
    if (retained.length) { stdout.push(Buffer.from(retained)); stdoutBytes += retained.length; }
  });
  let stderrBytes = 0;
  child.stderr.on("data", bytes => {
    const retained = bytes.subarray(0, Math.max(0, 64 * 1024 - stderrBytes));
    if (retained.length) { stderr.push(Buffer.from(retained)); stderrBytes += retained.length; }
  });
  const exited = new Promise(resolve => child.once("close", (code, signal) => resolve({
    pid: child.pid ?? null, code, signal, owner: "public_NdjsonAgentSessionPort", actual_exit: true })));
  // Public constructor keeps Runtime's complete duplex law and kill/closure owner;
  // the application tracks only the original ChildProcess's public PID/exit event.
  return { port: new deps.NdjsonAgentSessionPort(child, manifest.adapter, manifest.limits), exited,
    pid: child.pid ?? null, diagnostics: () => Buffer.concat(stderr),
    protocolDiagnostics: () => Buffer.concat(stdout) };
}

/** Generic Runtime drives acquisition/ACK/Act/Await/result/deadline; no SDK gameplay loop here. */
export async function runNativeSource3(options, operationId, peer, dependencies, lifetime = createCollectionLifetime()) {
  validateOptions(options);
  if (!id(operationId)) throw new Error("invalid_collection_operation");
  let deps, episode, runtime, evidence, teacher, finalStatus = null;
  let hostStarted = false, hostExit = null, controlRelease = null, teacherExit = null;
  let admissionRequested = false, sourceClosed = false, messageId = 0;
  let reason = "collection_owner_failed", failures = [], diagnostics = [], directEvidence = null;
  let runtimeStop, privateBytes = 0;
  const privateWrite = async (name, bytes) => {
    if (privateBytes + bytes.length > options.max_diagnostic_bytes) throw new Error("aggregate_private_diagnostic_capacity");
    const file = await open(path.join(options.output, name), "wx", 0o600);
    try { await file.writeFile(bytes); await file.sync(); } finally { await file.close(); }
    privateBytes += bytes.length;
  };
  const seenResults = new Set(), counts = { result_messages: 0, known_delivered_choices: 0 };
  const common = type => ({ schema: PIPE_SCHEMA, type, operation_id: operationId });
  const check = () => lifetime.signal.throwIfAborted();
  const reply = async (type, payload, responseType, allowStopped = false) => {
    const current = ++messageId;
    await peer.send({ ...common(type), message_id: current, ...payload });
    let value;
    try { value = await peer.receive({ allowStopped, timeoutMs: allowStopped ? 20_000 : 30_000 }); }
    catch (error) {
      // Retire only this canceled application permission reply. Native request
      // Results remain entirely in Runtime and are never dropped or retried here.
      peer.retireReply?.(operationId, current, responseType);
      throw error;
    }
    if (value.schema !== PIPE_SCHEMA || value.operation_id !== operationId
        || value.message_id !== current || value.type !== responseType) throw new Error("pipe_reply_binding_changed");
    return value;
  };
  const stopRuntime = () => {
    if (runtime && !runtimeStop) runtimeStop = runtime.stop();
    return runtimeStop ?? Promise.resolve();
  };
  const removeStop = lifetime.onStop(() => {
    if (runtime) void stopRuntime().catch(() => {});
    else teacher?.port.close();
  });
  const timer = setTimeout(() => lifetime.stop("deadline"), options.deadline_ms);
  const began = performance.now();
  try {
    check(); deps = dependencies ?? await defaultDependencies(); check();
    await mkdir(path.join(options.output, "host"), { mode: 0o700 });
    const startup = new AbortController(), abort = () => startup.abort(new Error(lifetime.reason || "external_stop"));
    lifetime.signal.addEventListener("abort", abort, { once: true });
    try {
      check();
      episode = await deps.startEpisode({ installation: deps.resolveInstallation(options.installation),
        localRoot: options.host_local_root, evidenceRoot: path.join(options.output, "host"), endpoint: options.endpoint,
        seed: options.seed, templateId: options.template_id, characterId: "DEFECT", ascension: 0, signal: startup.signal,
        experimentalBuildAcknowledged: options.experimental_build_acknowledged,
        experimentalConnectorAcknowledged: options.experimental_connector_acknowledged });
      hostStarted = true;
    } catch (error) { hostStarted = error?.host_started === true; hostExit = error?.host_exit ?? null; throw error; }
    finally { lifetime.signal.removeEventListener("abort", abort); }
    check();
    if (episode.identity.endpoint !== options.endpoint) throw new Error("host_endpoint_changed");
    const runtimeId = episode.identity.host.runtime_instance_id;
    const handoff = await episode.releaseController(); check();
    admissionRequested = true;
    const ready = await reply("ready", { runtime_instance_id: runtimeId, endpoint: options.endpoint,
      host_identity: safeHostIdentity(episode.identity), bootstrap_control_release: handoff,
      record_source3: options.record_source3 }, "source_ready");
    exact(ready, ["schema", "type", "operation_id", "message_id", "source_context", "source_recording"]);
    if (options.record_source3) {
      const context = ready.source_context;
      exact(context, ["runtime_instance_id", "recording_session_id", "source_segment_id", "source_epoch_id", "declaration"]);
      if (ready.source_recording !== "known_recording" || context.runtime_instance_id !== runtimeId
          || context.declaration?.source_kind !== "agent_protocol" || context.declaration?.machine_verifiable !== false)
        throw new Error("known_source_start_required");
    } else if (ready.source_context !== null || ready.source_recording !== "not_requested")
      throw new Error("explicit_source_opt_out_required");
    check();
    const environment = new deps.PlayerEnvironmentRestClient(options.endpoint, 30_000);
    const c = deps.decodeNativeLogicalCapabilities((await environment.nativeLogicalRequest("capabilities")).raw).data;
    if (c.session.runtime_instance_id !== runtimeId) throw new Error("runtime_binding_changed");
    const bytes = await readFile(options.teacher_artifact.path);
    if (hash(bytes) !== options.teacher_artifact.sha256 || deps.canonicalJson(JSON.parse(bytes)) !== deps.canonicalJson(options.teacher_descriptor))
      throw new Error("real_teacher_artifact_binding");
    const manifest = teacherManifest(options, c, deps), manifestPath = path.join(options.output, "teacher-agent-manifest.json");
    await privateWrite(path.basename(manifestPath), Buffer.from(deps.canonicalJson(manifest) + "\n"));
    const root = path.join(options.output, "agent-runs"); await mkdir(root, { mode: 0o700 });
    evidence = await deps.AgentRunEvidence.createSession({ root, agentManifest: manifest,
      runtimeVersion: deps.runtimeIdentity.version, runtimeCodeSha256: deps.runtimeIdentity.code_sha256, mode: "auto" });
    directEvidence = { schema: "sts2.policy-runtime/agent-session-run-1", run_id: evidence.runId,
      directory: evidence.directory, manifest_id: manifest.manifest_id, artifact_sha256: manifest.artifact.sha256,
      input_spec: manifest.input.input_spec, training_admission: "not_run" };
    check(); teacher = startTeacher(options, manifestPath, manifest, deps);
    // Initialization is already under the public Runtime's deadline/Stop owner.
    // Stop during construction is checked immediately after the factory returns.
    runtime = await deps.PolicyRuntime.forAgent({ manifest, environment, port: teacher.port, evidence,
      runtimeIdentity: deps.runtimeIdentity, mode: "auto", autoBudget: {
        maxSubmissions: options.max_submissions, maxPolicyCalls: 1200,
        deadlineMs: Math.max(1, Math.floor(options.deadline_ms - (performance.now() - began))) } });
    check();
    while (true) {
      check();
      const allowed = await reply("runtime_gate", { status: runtime.status(), direct_evidence: directEvidence }, "runtime_continue");
      exact(allowed, ["schema", "type", "operation_id", "message_id", "continue", "reason"]);
      if (allowed.continue !== true) { reason = allowed.reason || "application_source_stop"; break; }
      const tick = await runtime.tick(); finalStatus = tick.status;
      const result = tick.status.last_result;
      if (result && !seenResults.has(result.request_id)) {
        seenResults.add(result.request_id); counts.result_messages++;
        if (result.status === "terminal" && result.delivery === "delivered") counts.known_delivered_choices++;
      }
      const continuation = await reply("runtime_tick", { tick, direct_evidence: directEvidence }, "runtime_continue");
      exact(continuation, ["schema", "type", "operation_id", "message_id", "continue", "reason"]);
      if (continuation.continue !== true) { reason = continuation.reason || "application_source_stop"; break; }
      if (result?.status === "terminal" && result.execution === "native_rejected") {
        reason = "native_choice_rejected"; break;
      }
      if (counts.known_delivered_choices >= options.target_choices) { reason = "target_choices_reached"; break; }
      if (tick.type === "closed") { reason = tick.status.last_directive?.reason || "agent_closed"; break; }
      if (tick.type === "unknown" || tick.type === "not_delivered" || tick.type === "not_admitted"
          || tick.status.mode !== "auto" || tick.status.tainted || tick.status.lifecycle === "stopped") {
        reason = tick.type === "unknown" ? "original_runtime_outcome_unresolved" : "runtime_handoff"; break;
      }
    }
  } catch (error) {
    reason = lifetime.reason || reasonCode(error); diagnostics.push(failureDetail(error));
  } finally {
    clearTimeout(timer);
    try { await stopRuntime(); if (runtime) finalStatus = runtime.status(); }
    catch (error) { failures.push("runtime_stop_unconfirmed"); diagnostics.push(failureDetail(error)); }
    if (!runtime && teacher) {
      teacher.port.close();
      if (evidence) await evidence.finalize({ status: "tainted", tainted: true, mode: "human" }).catch(() => failures.push("evidence_finalize_unconfirmed"));
    }
    if (admissionRequested) {
      try {
        const closed = await reply("quiesced", { reason, runtime_status: finalStatus,
          direct_evidence: directEvidence, counts, record_source3: options.record_source3 }, "source_closed", true);
        exact(closed, ["schema", "type", "operation_id", "message_id", "known_closed", "source_status", "close_outcome"]);
        sourceClosed = options.record_source3 ? closed.known_closed === true : closed.close_outcome === "not_requested";
        if (!sourceClosed) failures.push("source_close_unconfirmed");
      } catch (error) { failures.push("source_close_unconfirmed"); diagnostics.push(failureDetail(error)); }
    }
    if (teacher) {
      let exitTimer;
      try { teacherExit = await Promise.race([teacher.exited, new Promise((_, reject) => {
        exitTimer = setTimeout(() => reject(new Error("teacher_exit_unconfirmed")), 5000);
      })]); }
      catch { failures.push("teacher_exit_unconfirmed"); }
      finally { clearTimeout(exitTimer); }
    }
    if (episode) {
      try {
        const observed = (await new deps.PlayerEnvironmentRestClient(options.endpoint, 5000).controlSnapshot()).data;
        const publicReleased = runtime ? finalStatus?.controller === "released" : true;
        if (!publicReleased || observed.runtime_instance_id !== episode.identity.host.runtime_instance_id || observed.controller != null)
          throw new Error("original_control_release_unconfirmed");
        controlRelease = { confirmed: true, observation: observed };
      } catch { failures.push("original_control_release_unconfirmed"); }
      try { hostExit = await episode.close(); }
      catch (error) { hostExit = error?.host_exit ?? null; failures.push("host_close_failed"); }
    }
    removeStop();
  }
  let diagnosticRef = null;
  if (diagnostics.length || teacher?.diagnostics().length || finalStatus?.errors.length) {
    try {
      const bytes = Buffer.from(JSON.stringify({ failures: diagnostics.slice(0, 4),
        teacher_stderr: teacher?.diagnostics().toString("utf8") ?? null,
        teacher_protocol_tail: teacher?.protocolDiagnostics().toString("utf8") ?? null,
        runtime_errors: finalStatus?.errors ?? [] }) + "\n");
      const name = "collector-failure-detail.json";
      if (bytes.length > Math.min(options.max_diagnostic_bytes, 256 * 1024)) throw new Error("diagnostic_capacity_exceeded");
      await privateWrite(name, bytes);
      diagnosticRef = { path: name, bytes: bytes.length, sha256: hash(bytes) };
    } catch { failures.push("private_failure_diagnostics_write_failed"); }
  }
  const unknownProjection = finalStatus?.tainted && !finalStatus?.pending_request
    && finalStatus?.autonomy_budget?.submissions_used > counts.result_messages
    ? "not_exposed_by_public_status_see_original_immutable_evidence" : null;
  const complete = { schema: "spireagent/native-source3-collector-final-full-v2", operation_id: operationId,
    reason, counts, runtime_status: finalStatus, direct_evidence: directEvidence, teacher_exit: teacherExit,
    source_closed: sourceClosed, record_source3: options.record_source3, control_release: controlRelease,
    host_exit: hostExit, host_started: hostStarted, cleanup_errors: [...failures], failure_details: diagnosticRef,
    partial_source_prefix: true, learned_evaluation: false, unknown_request_id_projection: unknownProjection };
  let fullRecordRef = null;
  try {
    const bytes = Buffer.from(JSON.stringify(complete) + "\n");
    if (bytes.length > options.max_diagnostic_bytes) throw new Error("private_final_capacity");
    const name = "collector-final-full.json";
    await privateWrite(name, bytes);
    fullRecordRef = { path: name, bytes: bytes.length, sha256: hash(bytes) };
  } catch { failures.push("private_final_record_write_failed"); }
  // Explicit small application projection, not a fabricated public Runtime status.
  // Original complete status/control/Host receipts are retained in the immutable
  // private file. These mandatory scalars survive failure of that diagnostic write.
  const summary = finalStatus === null ? null : {
    schema: "spireagent/native-agent-runtime-summary-v1", public_status_schema: finalStatus.schema,
    lifecycle: finalStatus.lifecycle, mode: finalStatus.mode, controller: finalStatus.controller,
    tainted: finalStatus.tainted, agent_state: finalStatus.session.agent_state,
    submissions_used: finalStatus.autonomy_budget.submissions_used,
    policy_calls_used: finalStatus.autonomy_budget.policy_calls_used,
    state_version: finalStatus.session.state_version,
    pending_request: finalStatus.pending_request === null ? null : {
      request_id: finalStatus.pending_request.request_id, status: finalStatus.pending_request.status },
    last_result: finalStatus.last_result === null ? null : {
      request_id: finalStatus.last_result.request_id, status: finalStatus.last_result.status,
      delivery: finalStatus.last_result.delivery, execution: finalStatus.last_result.execution,
      effect: finalStatus.last_result.effect, cancel: finalStatus.last_result.cancel } };
  const final = { ...common("closed"), reason, counts, runtime_summary: summary,
    direct_evidence: directEvidence, teacher_exit: teacherExit, source_closed: sourceClosed,
    record_source3: options.record_source3, control_release: controlRelease === null ? null : {
      confirmed: controlRelease.confirmed, runtime_instance_id: controlRelease.observation.runtime_instance_id },
    host_exit: hostExit === null ? null : { code: hostExit.code, signal: hostExit.signal, forced: hostExit.forced },
    host_started: hostStarted, cleanup_errors: failures, failure_details: diagnosticRef,
    full_record_ref: fullRecordRef, partial_source_prefix: true, learned_evaluation: false,
    unknown_request_id_projection: unknownProjection };
  if (Buffer.byteLength(JSON.stringify(final) + "\n") > MAX_CONTROL_BYTES)
    throw new Error("mandatory_final_projection_capacity");
  await peer.send(final);
  return final;
}

export async function childMain() {
  const lifetime = createCollectionLifetime();
  const stop = () => lifetime.stop("external_signal");
  process.on("SIGINT", stop); process.on("SIGTERM", stop);
  process.stdin.on("end", () => lifetime.stop("parent_eof"));
  let peer;
  try {
    const deps = await defaultDependencies();
    peer = createPipePeer(process.stdin, process.stdout, deps.parseNativeLogicalJson, lifetime);
    const init = await peer.receive({ timeoutMs: 30_000 });
    exact(init, ["schema", "type", "operation_id", "options"]);
    if (init.schema !== PIPE_SCHEMA || init.type !== "init" || !id(init.operation_id))
      throw new Error("collector_init_required");
    const result = await runNativeSource3(init.options, init.operation_id, peer, deps, lifetime);
    return result.cleanup_errors.length ? 1 : 0;
  } catch (error) {
    process.stderr.write(JSON.stringify({ error: reasonCode(error), automatic_retry: false }) + "\n");
    return 1;
  } finally { process.off("SIGINT", stop); process.off("SIGTERM", stop); }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  childMain().then(code => { process.exitCode = code; });
