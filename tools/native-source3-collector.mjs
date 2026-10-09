#!/usr/bin/env node
/** Collect-only Host/native SDK transport. Strategy lives in the Python STPD parent. */
import { createHash, randomUUID } from "node:crypto";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const PIPE_SCHEMA = "spireagent/native-source3-collector-pipe-v1";
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

export function validateOptions(value) {
  exact(value, ["installation", "host_local_root", "output", "endpoint", "seed", "template_id",
    "target_choices", "max_submissions", "deadline_ms", "max_input_bytes", "max_diagnostic_bytes",
    "experimental_build_acknowledged", "experimental_connector_acknowledged"]);
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
      || typeof value.experimental_connector_acknowledged !== "boolean") throw new Error("finite_collection_limits_required");
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
  const rejectAll = error => { for (const waiter of waiters.splice(0)) waiter.reject(error); };
  const dispatch = value => {
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
        if (line.length > MAX_CONTROL_BYTES) throw new Error("parent_control_capacity");
        dispatch(parseJson(new TextDecoder("utf-8", { fatal: true }).decode(line)));
      }
    } catch (error) { closed = true; lifetime.stop("pipe_protocol_failed"); rejectAll(error); }
  });
  const eof = () => { closed = true; lifetime.stop("parent_eof"); rejectAll(new Error("parent_eof")); };
  input.on("end", eof); input.on("error", eof); output.on("error", eof);
  return {
    get closed() { return closed; },
    async send(value) {
      const bytes = Buffer.from(JSON.stringify(value) + "\n", "utf8");
      const maximum = ["current", "result"].includes(value.type) ? MAX_PIPE_BYTES : MAX_CONTROL_BYTES;
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
  const installation = await import(pathToFileURL(path.join(ROOT, "components/host-runtime/src/game-installation.mjs")));
  const bytes = await readFile(path.join(ROOT, "components/connector/contracts/native-logical-publication-profile-v1.json"));
  if (hash(bytes) !== sdk.NATIVE_LOGICAL_PUBLICATION_PROFILE_SHA256)
    throw new Error("fixed_native_profile_changed");
  return { ...sdk, startEpisode: host.startShippedPlayerEnvironmentEpisode,
    resolveInstallation: installation.resolveInstallation };
}

function inputBudget(maximum) {
  let used = 0;
  return { reserve({ bytes, signal }) {
    signal?.throwIfAborted();
    if (!integer(bytes, 1, maximum) || used + bytes > maximum) throw new Error("whole_current_capacity_exceeded");
    used += bytes;
    let held = bytes, released = false;
    return { resize(size) {
      if (released || !integer(size, 0, held)) throw new Error("invalid_byte_reservation");
      used += size - held; held = size;
    }, release() {
      if (released) throw new Error("duplicate_byte_release");
      released = true; used -= held;
    } };
  }, get used() { return used; } };
}

function safeHostIdentity(identity) {
  // Only public identity/provenance returned by the Host; never environment or launch tokens.
  return { host: identity.host, game: identity.game, profile: identity.profile,
    episode_provenance: identity.episode_provenance,
    requested_seed: identity.requested_seed, requested_character_id: identity.requested_character_id,
    requested_ascension: identity.requested_ascension };
}

/** Production flow with fixed owner injection for pure transport/lifecycle tests. No strategy here. */
export async function runNativeSource3(options, operationId, peer, dependencies, lifetime = createCollectionLifetime()) {
  validateOptions(options);
  if (!id(operationId)) throw new Error("invalid_collection_operation");
  let deps, profile;
  let episode, controller, native, capabilities, admissionRequested = false, sourceClosed = false;
  let hostStarted = false, hostExit = null, controlRelease = null, messageId = 0, pendingRequest = null;
  let bootstrapHandoff = null;
  let sourceReason = "collection_owner_failed", timer, cursor, subscriptionRenewed = 0, outputBytes = 0;
  let releasePromise;
  const errors = [], counts = { submissions: 0, known_delivered_choices: 0, result_queries: 0 };
  const advisory = { history_claimed: false, eager_scope: [], events: 0, gaps: 0,
    current_reads: 0, wake_timeouts: 0, event_payloads_archived: false };
  const budget = inputBudget(options.max_input_bytes);
  const started = performance.now();
  const check = () => {
    lifetime.signal.throwIfAborted();
    if (performance.now() - started >= options.deadline_ms) {
      lifetime.stop("deadline"); lifetime.signal.throwIfAborted();
    }
  };
  const common = type => ({ schema: PIPE_SCHEMA, type, operation_id: operationId });
  const reply = async (type, payload, responseType, allowStopped = false) => {
    const sentId = ++messageId;
    await peer.send({ ...common(type), message_id: sentId, ...payload });
    const received = await peer.receive({ allowStopped, timeoutMs: allowStopped ? 20_000 : 30_000 });
    if (received.schema !== PIPE_SCHEMA || received.operation_id !== operationId
        || received.message_id !== sentId || received.type !== responseType)
      throw new Error("pipe_reply_binding_changed");
    return received;
  };
  const record = async (name, value) => {
    const bytes = Buffer.from(JSON.stringify(value) + "\n");
    if (outputBytes + bytes.length > options.max_diagnostic_bytes) throw new Error("diagnostic_capacity_exceeded");
    await writeFile(path.join(options.output, name), bytes, { flag: "wx", mode: 0o600 });
    outputBytes += bytes.length;
  };
  const release = () => {
    // A stop before construction must not cache an empty release operation.
    if (!controller) return Promise.resolve();
    if (!releasePromise) releasePromise = controller.releaseControl();
    return releasePromise;
  };
  const removeRelease = lifetime.onStop(() => { void release().catch(() => {}); });
  timer = setTimeout(() => lifetime.stop("deadline"), options.deadline_ms);
  try {
    check();
    deps = dependencies ?? await defaultDependencies();
    check();
    profile = deps.NATIVE_LOGICAL_PUBLICATION_PROFILE;
    if (!profile || profile.profile_id !== "native-logical-publication-profile-v1"
        || profile.input_profile !== "native-logical-v1") throw new Error("fixed_native_profile_required");
    await mkdir(path.join(options.output, "host"), { mode: 0o700 });
    const startup = new AbortController();
    const stopStartup = () => startup.abort(new Error(lifetime.reason || "external_stop"));
    lifetime.signal.addEventListener("abort", stopStartup, { once: true });
    try {
      check();
      episode = await deps.startEpisode({ installation: deps.resolveInstallation(options.installation),
        localRoot: options.host_local_root, evidenceRoot: path.join(options.output, "host"),
        endpoint: options.endpoint, seed: options.seed, templateId: options.template_id,
        characterId: "DEFECT", ascension: 0, signal: startup.signal,
        experimentalBuildAcknowledged: options.experimental_build_acknowledged,
        experimentalConnectorAcknowledged: options.experimental_connector_acknowledged });
      hostStarted = true;
    } catch (error) {
      hostStarted = error?.host_started === true;
      hostExit = error?.host_exit ?? null;
      throw error;
    } finally { lifetime.signal.removeEventListener("abort", stopStartup); }
    check();
    if (episode.identity.endpoint !== options.endpoint) throw new Error("host_endpoint_changed");
    const runtime = episode.identity.host.runtime_instance_id;
    const handoff = await episode.releaseController();
    bootstrapHandoff = handoff;
    check();
    admissionRequested = true;
    const response = await reply("ready", { runtime_instance_id: runtime, endpoint: options.endpoint,
      host_identity: safeHostIdentity(episode.identity), bootstrap_control_release: handoff }, "source_ready");
    exact(response, ["schema", "type", "operation_id", "message_id", "source_context"]);
    const context = response.source_context;
    exact(context, ["runtime_instance_id", "recording_session_id", "source_segment_id",
      "source_epoch_id", "declaration"]);
    exact(context.declaration, ["source_kind", "actor_id", "declaration_id", "machine_verifiable"]);
    if (context.runtime_instance_id !== runtime
        || [context.recording_session_id, context.source_segment_id, context.source_epoch_id,
          context.declaration.actor_id, context.declaration.declaration_id]
          .some(value => typeof value !== "string" || !value)
        || context.declaration?.source_kind !== "agent_protocol"
        || context.declaration?.machine_verifiable !== false) throw new Error("known_source_start_required");
    check();
    const client = new deps.PlayerEnvironmentRestClient(options.endpoint, 15_000);
    controller = new deps.EnvironmentControllerSession(client, { productId: "spireagent-source3-collector",
      productName: "Source3 collect-only client", productVersion: "1.0.0", clientInstanceId: "source3-" + operationId });
    check();
    native = new deps.NativeLogicalSession(client, controller);
    capabilities = (await native.capabilities(lifetime.signal)).data;
    check();
    if (capabilities.session.runtime_instance_id !== runtime) throw new Error("native_runtime_changed");
    await controller.register(capabilities.host, capabilities.control_policy);
    check();
    const attached = (await native.attach({ eagerScope: [], requiredSeams: profile.required_seams,
      deliveryMode: "scoped", signal: lifetime.signal })).data;
    if (!attached.subscription || attached.subscription.delivery_mode !== "scoped"
        || attached.subscription.eager_scope.length !== 0) throw new Error("sampled_attachment_required");
    cursor = attached.next_cursor; subscriptionRenewed = performance.now();
    const generation = attached.subscription.stream_generation;
    let lastSubmittedSnapshot = null, lastOpportunity = performance.now();
    const wake = async () => {
      check();
      const batch = (await native.events({ afterCursor: cursor, limit: 64, signal: lifetime.signal })).data;
      advisory.events += batch.events.length;
      if (batch.gap) advisory.gaps++;
      cursor = batch.next_cursor;
      if (performance.now() - subscriptionRenewed >= Math.max(1000, capabilities.limits.retention_ms / 2)) {
        const renewed = (await native.renew(cursor, lifetime.signal)).data;
        if (renewed.status !== "renewed" || renewed.subscription.stream_generation !== generation)
          throw new Error("native_subscription_changed");
        cursor = renewed.next_cursor; subscriptionRenewed = performance.now();
      }
      if (!batch.events.length) {
        const waited = (await native.await({ waitId: randomUUID().replaceAll("-", ""),
          afterCursor: cursor, condition: "any_event", timeoutMs: 200, signal: lifetime.signal })).data;
        if (waited.status === "timeout") advisory.wake_timeouts++;
        else if (waited.status === "gap") advisory.gaps++;
        else if (waited.status !== "event") throw new Error("native_wake_unavailable");
      }
    };
    while (counts.submissions < options.max_submissions
        && counts.known_delivered_choices < options.target_choices) {
      check();
      let full;
      try {
        full = await native.getFullCurrent({ budget, maxActions: 65536, signal: lifetime.signal });
        advisory.current_reads++;
        if (full.capture.session.runtime_instance_id !== runtime
            || full.observation.catalog.stream_generation !== generation)
          throw new Error("current_runtime_or_generation_changed");
        const observation = full.observation, actions = full.actions;
        if (observation.status === "visible_unsupported") throw new Error("current_surface_unsupported");
        if (full.capture.snapshot_id === lastSubmittedSnapshot
            || (actions.length === 0 && observation.status !== "terminal")
            || observation.status === "settling") {
          await full.dispose(); full = null;
          if (performance.now() - lastOpportunity > 20_000) throw new Error("current_opportunity_timeout");
          await wake(); continue;
        }
        lastOpportunity = performance.now();
        const raw = Buffer.from(full.serializedObservation, "utf8");
        const basis = { capture_id: full.capture.capture_id, snapshot_id: full.capture.snapshot_id,
          runtime_instance_id: runtime, stream_generation: generation, capture_sha256: full.capture.sha256,
          byte_count: full.capture.byte_count, catalog_digest: observation.catalog.digest,
          total_count: observation.catalog.total_count };
        if (raw.length !== basis.byte_count || hash(raw) !== basis.capture_sha256
            || actions.length !== basis.total_count) throw new Error("original_current_integrity_failed");
        if (raw.length + Buffer.byteLength(JSON.stringify(actions)) > options.max_input_bytes)
          throw new Error("whole_current_capacity_exceeded");
        const ordinal = counts.submissions + 1;
        const choice = await reply("current", { ordinal, basis, observation_base64: raw.toString("base64"),
          catalog: actions }, "choice");
        exact(choice, ["schema", "type", "operation_id", "message_id", "ordinal", "basis",
          "action_id", "stop_reason", "teacher_state"]);
        if (choice.ordinal !== ordinal || !object(choice.basis)
            || Object.keys(choice.basis).length !== Object.keys(basis).length
            || Object.keys(basis).some(key => choice.basis[key] !== basis[key]))
          throw new Error("choice_basis_changed");
        if (choice.action_id === null) {
          if (typeof choice.stop_reason !== "string") throw new Error("explicit_teacher_stop_required");
          sourceReason = choice.stop_reason; break;
        }
        if (choice.stop_reason !== null || typeof choice.action_id !== "string"
            || !actions.some(action => action.action_id === choice.action_id)) throw new Error("choice_not_in_original_catalog");
        check();
        await record(`choice-${String(ordinal).padStart(4, "0")}.json`, { basis, ordinal,
          action_id: choice.action_id, teacher_state: choice.teacher_state, acquisition: "complete_current",
          advisory_event_history_required: false });
        check();
        const requestId = "source3-" + randomUUID(); pendingRequest = requestId;
        let dispatched = false;
        let result = await native.submit({ requestId, expectedSnapshotId: basis.snapshot_id,
          actionId: choice.action_id, preSubmitSignal: lifetime.signal,
          onSubmitStart() { check(); dispatched = true; counts.submissions++; } });
        if (!dispatched) throw new Error("native_submit_without_admission");
        lastSubmittedSnapshot = basis.snapshot_id;
        let queries = 0;
        const resultDeadline = performance.now() + 2000;
        while (result.status === "pending" && queries < 40 && performance.now() < resultDeadline) {
          check();
          await new Promise(resolve => setTimeout(resolve, 50));
          check();
          queries++; counts.result_queries++;
          result = await native.result(requestId, AbortSignal.any([lifetime.signal,
            AbortSignal.timeout(Math.max(1, Math.ceil(resultDeadline - performance.now())))]));
        }
        await record(`result-${String(ordinal).padStart(4, "0")}.json`, { request_id: requestId,
          lookup_status: result.status, result: result.status === "terminal" ? result.result.data : null,
          result_queries: queries, automatic_retry: false });
        const disposition = result.status === "terminal" ? result.result.data : null;
        if (disposition?.delivery === "delivered") counts.known_delivered_choices++;
        const response = await reply("result", { ordinal, request_id: requestId,
          lookup_status: result.status, result: disposition, result_queries: queries }, "continue");
        exact(response, ["schema", "type", "operation_id", "message_id", "continue", "reason"]);
        if (disposition?.delivery !== "delivered" || disposition.execution === "native_rejected") {
          sourceReason = disposition ? "native_choice_not_delivered_or_rejected" : "original_result_unresolved";
          break;
        }
        pendingRequest = null;
        if (response.continue !== true) { sourceReason = response.reason || "application_source_stop"; break; }
        lastOpportunity = performance.now();
        await full.dispose(); full = null;
        await wake();
      } finally { if (full) await full.dispose(); }
    }
    if (sourceReason === "collection_owner_failed") {
      if (counts.known_delivered_choices >= options.target_choices) sourceReason = "target_choices_reached";
      else if (counts.submissions >= options.max_submissions) sourceReason = "submission_budget";
    }
  } catch (error) {
    sourceReason = lifetime.reason || reasonCode(error);
    // The attempt's failure/censoring is its reason, independent of cleanup.
    if (pendingRequest && counts.submissions) sourceReason = lifetime.reason || "original_result_unresolved";
  } finally {
    clearTimeout(timer);
    // App Close belongs to the parent, including canceled/partial attempts. Never bypass it on EOF.
    try {
      if (admissionRequested) {
        const response = await reply("quiesced", { reason: sourceReason, counts,
          pending_request_id: pendingRequest }, "source_closed", true);
        exact(response, ["schema", "type", "operation_id", "message_id", "known_closed",
          "source_status", "close_outcome"]);
        sourceClosed = response.known_closed === true;
        if (!sourceClosed) errors.push("source_close_unconfirmed");
      }
    } catch (error) { errors.push(reasonCode(error)); }
    if (native?.subscription) {
      try { await native.detach(); } catch { errors.push("native_detach_unconfirmed"); }
    }
    if (controller) {
      try {
        await release();
        const observed = (await new deps.PlayerEnvironmentRestClient(options.endpoint, 5000).controlSnapshot()).data;
        if (observed.runtime_instance_id !== episode.identity.host.runtime_instance_id || observed.controller != null)
          throw new Error("original_control_release_unconfirmed");
        controlRelease = { confirmed: true, observation: observed };
      } catch { errors.push("original_control_release_unconfirmed"); }
      try { await controller.close(); } catch { errors.push("controller_close_unconfirmed"); }
    } else if (episode && bootstrapHandoff) {
      // Admission can fail after Host handoff but before SDK controller construction.
      // A fresh exact-runtime control view still checks the real bootstrap release;
      // absence is not used to settle any native input or unknown Start.
      try {
        const observed = (await new deps.PlayerEnvironmentRestClient(options.endpoint, 5000).controlSnapshot()).data;
        if (observed.runtime_instance_id !== episode.identity.host.runtime_instance_id || observed.controller != null)
          throw new Error("original_control_release_unconfirmed");
        controlRelease = { confirmed: true, observation: observed,
          native_controller_constructed: false };
      } catch { errors.push("original_control_release_unconfirmed"); }
    }
    if (episode) {
      try { hostExit = await episode.close(); }
      catch (error) { hostExit = error?.host_exit ?? null; errors.push("host_close_failed"); }
    }
    removeRelease();
  }
  const final = { ...common("closed"), reason: sourceReason, counts, source_closed: sourceClosed,
    control_release: controlRelease, host_exit: hostExit, host_started: hostStarted,
    cleanup_errors: errors, advisory, live_byte_reservations: budget.used,
    partial_source_prefix: true, learned_evaluation: false };
  await peer.send(final).catch(() => {});
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
