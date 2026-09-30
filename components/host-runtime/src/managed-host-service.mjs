import { randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";
import { SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL } from "@rsgcsg/sts2-connector-client";
import { ManagedPeDriverSession } from "./managed-pe-driver-session.mjs";
import { managedTextMenuV1Contract } from "./managed-text-menu-map.mjs";
import { managedTextMenuV2Contract } from "./managed-text-menu-v2.mjs";

const READY_SCHEMA = "sts2.host-runtime/managed-service-ready-1";
const RESULT_SCHEMA = "sts2.host-runtime/managed-service-result-1";
const ERROR_SCHEMA = "sts2.host-runtime/managed-service-error-1";
const CLIENT_COMMANDS = new Set([
  "observe", "read", "text_observe", "episode_identity",
  "claim_control", "release_control", "text_submit"
]);
const PUBLIC_ERRORS = new Set([
  "driver_closed", "managed_episode_unavailable_reset_required",
  "managed_control_held", "managed_control_intent_stale",
  "managed_control_not_held", "managed_control_not_authorized",
  "managed_control_credential_stale", "managed_control_runtime_identity_unavailable",
  "managed_session_tainted_after_unknown",
  "managed_session_tainted_after_successor_projection_failure",
  "stale_game_continuity", "stale_managed_runtime_instance",
  "text_request_id_conflict_across_episodes",
  "mutation_request_id_conflict_between_routes",
  "managed_service_body_too_large", "managed_service_body_required",
  "managed_service_body_must_be_object", "managed_service_request_id_required",
  "managed_service_episode_precondition_required",
  "managed_service_recovery_precondition_required", "managed_service_close_body_invalid"
]);

function fields(value, keys) {
  return Object.fromEntries(keys.map((key) => [key, value?.[key] ?? null]));
}

function publicHostIdentity(value) {
  return fields(value, ["product", "package_name", "version", "distribution_kind",
    "workspace_revision", "component_path", "source_revision",
    "component_tree_revision", "source_worktree_status",
    "workspace_worktree_status", "source_digest_sha256", "source_file_count"]);
}

function publicCandidateManifest(value) {
  return { ...fields(value, ["schema", "candidate_id", "status"]),
    upstream: fields(value?.upstream, ["revision"]),
    expected_build: fields(value?.expected_build,
      ["source_patch_sha256", "artifact_sha256", "artifact_mvid"]) };
}

function publicExactGame(value) {
  return fields(value, ["platform", "architecture", "version", "commit",
    "runtime_main_assembly_hash", "sts2_dll_sha256", "godotsharp_dll_sha256"]);
}

function publicCandidateBuild(value) {
  return fields(value, ["upstream_revision", "source_patch_sha256", "source_patch_bytes",
    "artifact_size", "artifact_sha256", "artifact_mvid",
    "original_sts2_sha256", "runtime_sts2_sha256"]);
}

function publicRuntimeIdentity(value) {
  return fields(value, ["type", "process_id", "host_assembly_sha256",
    "sts2_assembly_sha256"]);
}

function publicEpisodeIdentity(value) {
  return { type: value.type, request_id: value.request_id, identity: {
    candidate_build: publicCandidateBuild(value.identity.candidate_build),
    runtime_identity: publicRuntimeIdentity(value.identity.runtime_identity),
    adapter_runtime_instance_id: value.identity.adapter_runtime_instance_id,
    environment_fingerprint: value.identity.environment_fingerprint,
    episode_provenance: value.identity.episode_provenance
  } };
}

function token() { return randomBytes(32).toString("base64url"); }

function sameSecret(actual, expected) {
  if (typeof actual !== "string") return false;
  const left = Buffer.from(actual);
  const right = Buffer.from(expected);
  return left.length === right.length && timingSafeEqual(left, right);
}

function json(response, status, value) {
  if (response.destroyed) return;
  const body = `${JSON.stringify(value)}\n`;
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store",
    "x-content-type-options": "nosniff"
  });
  response.end(body);
}

function headerCount(request, name) {
  return request.rawHeaders.filter((_value, index) => index % 2 === 0
    && request.rawHeaders[index]?.toLowerCase() === name).length;
}

function localRequestError(request) {
  const authority = request.headers.host;
  const port = request.socket.localPort;
  const allowed = ["127.0.0.1", "localhost", "[::1]"].map((host) => `${host}:${port}`);
  if (headerCount(request, "host") !== 1 || !allowed.includes(authority)) {
    return "managed_service_host_not_allowed";
  }
  const origin = request.headers.origin;
  if (headerCount(request, "origin") > 1 || (origin !== undefined
      && origin !== `http://${authority}`)) {
    return "managed_service_origin_not_allowed";
  }
  return null;
}

function authorize(request, clientToken, managerToken) {
  if (headerCount(request, "authorization") !== 1) return null;
  const value = request.headers.authorization;
  if (typeof value !== "string" || !value.startsWith("Bearer ")) return null;
  const supplied = value.slice("Bearer ".length);
  if (sameSecret(supplied, managerToken)) return "manager";
  if (sameSecret(supplied, clientToken)) return "client";
  return null;
}

async function readBody(request, maxBodyBytes) {
  const contentLength = request.headers["content-length"];
  if (typeof contentLength === "string" && Number(contentLength) > maxBodyBytes) {
    throw new Error("managed_service_body_too_large");
  }
  let size = 0;
  const chunks = [];
  for await (const chunk of request) {
    size += chunk.byteLength;
    if (size > maxBodyBytes) throw new Error("managed_service_body_too_large");
    chunks.push(chunk);
  }
  if (chunks.length === 0) throw new Error("managed_service_body_required");
  const value = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (value == null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("managed_service_body_must_be_object");
  }
  return value;
}

function exactKeys(value, keys) {
  const actual = Object.keys(value);
  return actual.length === keys.length && actual.every((key) => keys.includes(key));
}

function nonempty(value) { return typeof value === "string" && value.length > 0; }

/** A local attach point for exactly one Managed driver and its native child. */
export async function startManagedHostService(started, {
  hostIdentity, host = "127.0.0.1", port = 0,
  requestTimeoutMs = 10_000, maxBodyBytes = 64 * 1024,
  bodyTimeoutMs = 5_000
} = {}) {
  let driver;
  try {
    driver = new ManagedPeDriverSession(started, { requestTimeoutMs });
    if (!["127.0.0.1", "::1"].includes(host)) throw new Error("managed_service_loopback_only");
    if (!Number.isSafeInteger(port) || port < 0 || port > 65535
      || !Number.isSafeInteger(requestTimeoutMs) || requestTimeoutMs < 1 || requestTimeoutMs > 60_000
      || !Number.isSafeInteger(maxBodyBytes) || maxBodyBytes < 1
      || !Number.isSafeInteger(bodyTimeoutMs) || bodyTimeoutMs < 1 || bodyTimeoutMs > 60_000) {
      throw new Error("managed_service_invalid_bounds");
    }
    if (!hostIdentity || !nonempty(hostIdentity.package_name)
      || !nonempty(hostIdentity.version) || !nonempty(hostIdentity.source_digest_sha256)) {
      throw new Error("managed_service_host_identity_required");
    }
  } catch (cause) {
    try {
      if (driver) await driver.shutdown({ force: true });
      else await started?.session?.close?.({ force: true, timeoutMs: 1_000 });
    } catch (cleanup) {
      throw new AggregateError([cause, cleanup], "managed_service_start_cleanup_unconfirmed");
    }
    throw cause;
  }
  const serviceInstanceId = `managed_service_${randomUUID().replaceAll("-", "")}`;
  const clientToken = token();
  const managerToken = token();
  const runtimeInstanceId = started.runtime.adapterRuntimeInstanceId;
  let heldClaim = null;
  let closing = false;
  let nativeClosed = false;
  let cleanupError = null;
  let serviceClose = null;
  let server;

  const ready = () => {
    const status = driver.status();
    return {
      schema: READY_SCHEMA,
      service_instance_id: serviceInstanceId,
      host_identity: publicHostIdentity(hostIdentity),
      candidate_manifest: publicCandidateManifest(started.runtime.manifest),
      exact_game: publicExactGame(started.runtime.exactGame),
      candidate_build: publicCandidateBuild(started.runtime.build),
      runtime_identity: publicRuntimeIdentity(started.runtime.runtimeIdentity),
      adapter_runtime_instance_id: runtimeInstanceId,
      environment_fingerprint: started.environmentFingerprint,
      supported_input_profiles: ["text-menu-v1", "text-menu-v2"],
      text_protocol_version: SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL,
      text_menu_contracts: [managedTextMenuV1Contract(), managedTextMenuV2Contract()],
      episode: {
        game_continuity_id: status.game_continuity_id,
        control_held: status.control_held,
        tainted: status.tainted,
        closed: status.closed
      }
    };
  };
  const result = (value, controlBinding = undefined) => ({
    schema: RESULT_SCHEMA, service_instance_id: serviceInstanceId,
    result: value, ...(controlBinding ? { control_binding: controlBinding } : {})
  });
  const binding = (controlEpoch, gameContinuityId) => ({
    control_epoch: controlEpoch,
    game_continuity_id: gameContinuityId,
    runtime_instance_id: runtimeInstanceId
  });
  const error = (response, status, code) => json(response, status, {
    schema: ERROR_SCHEMA, service_instance_id: serviceInstanceId, error: code
  });
  const managerStatus = () => {
    const status = driver.status();
    const control = status.control;
    if (heldClaim != null && status.control_held && control != null
      && control.control_epoch === heldClaim.epoch
      && control.runtime_instance_id === heldClaim.runtimeInstanceId
      && control.game_continuity_id === heldClaim.gameContinuityId) {
      status.control = { ...control, claim_request_id: heldClaim.requestId };
    }
    return status;
  };
  const requireService = (request, response) => {
    if (headerCount(request, "x-sts2-managed-service-id") !== 1
      || request.headers["x-sts2-managed-service-id"] !== serviceInstanceId) {
      error(response, 409, "managed_service_instance_mismatch");
      return false;
    }
    return true;
  };

  async function dispatch(request, response) {
    try {
      if (closing) { request.resume(); error(response, 409, "managed_service_closing"); return; }
      const denied = localRequestError(request);
      if (denied) { request.resume(); error(response, 403, denied); return; }
      const role = authorize(request, clientToken, managerToken);
      if (role == null) { request.resume(); error(response, 401, "managed_service_unauthorized"); return; }
      if (request.method === "GET" && request.url === "/v1/ready") {
        json(response, 200, ready()); return;
      }
      if (request.method === "GET" && request.url === "/v1/admin/status") {
        if (role !== "manager") { error(response, 403, "managed_service_manager_required"); return; }
        json(response, 200, { schema: READY_SCHEMA, service_instance_id: serviceInstanceId,
          status: managerStatus() });
        return;
      }
      if (request.method !== "POST" || ![
        "/v1/command", "/v1/admin/reset", "/v1/admin/recover-control", "/v1/admin/close"
      ].includes(request.url)) {
        request.resume(); error(response, 404, "managed_service_not_found"); return;
      }
      if (!requireService(request, response)) { request.resume(); return; }
      if (!/^application\/json(?:\s*;\s*charset=utf-8)?$/iu.test(request.headers["content-type"] ?? "")) {
        request.resume(); error(response, 415, "managed_service_json_required"); return;
      }
      if (request.url !== "/v1/command" && role !== "manager") {
        request.resume(); error(response, 403, "managed_service_manager_required"); return;
      }
      const body = await readBody(request, maxBodyBytes);
      if (!nonempty(body.request_id)) throw new Error("managed_service_request_id_required");

      if (request.url === "/v1/command") {
        const command = body.command;
        if (!CLIENT_COMMANDS.has(command) && !(role === "manager" && command === "step")) {
          error(response, 403, "managed_service_command_not_allowed"); return;
        }
        if ((command === "step" || command === "text_submit" || command === "release_control")
          && (!nonempty(body.control_token) || !nonempty(body.control_epoch))) {
          error(response, 428, "managed_service_control_required"); return;
        }
        if (command === "claim_control" && (!nonempty(body.expected_runtime_instance_id)
          || !nonempty(body.expected_game_continuity_id))) {
          error(response, 428, "managed_service_episode_precondition_required"); return;
        }
        // Only the controller may project a text menu while control is held.
        // The driver checks this inside its existing queue, not against a GET.
        const offered = command === "text_observe"
          ? { ...body, require_control: true } : body;
        const value = await driver.handle(offered);
        if (command === "claim_control") {
          heldClaim = {
            token: value.control_token, epoch: value.control_epoch,
            requestId: body.request_id,
            runtimeInstanceId: value.runtime_instance_id,
            gameContinuityId: value.game_continuity_id
          };
          json(response, 200, result(value, binding(value.control_epoch, value.game_continuity_id)));
          return;
        }
        if (command === "release_control") {
          if (heldClaim?.epoch === value.control_epoch) heldClaim = null;
          json(response, 200, result(value, binding(value.control_epoch, value.game_continuity_id)));
          return;
        }
        if (command === "text_submit") {
          json(response, 200, result(value,
            binding(body.control_epoch, body.expected_game_continuity_id)));
          return;
        }
        json(response, 200, result(command === "episode_identity"
          ? publicEpisodeIdentity(value) : value));
        return;
      }

      if (request.url === "/v1/admin/reset") {
        if (!exactKeys(body, ["request_id", "seed", "expected_runtime_instance_id",
          "expected_game_continuity_id"]) || !nonempty(body.expected_runtime_instance_id)
          || !(body.expected_game_continuity_id === null
            || nonempty(body.expected_game_continuity_id))) {
          throw new Error("managed_service_episode_precondition_required");
        }
        // In-queue reset checks the expected episode and unheld control.
        const value = await driver.handle({ ...body, command: "reset" });
        json(response, 200, result(value));
        return;
      }
      if (request.url === "/v1/admin/recover-control") {
        if (!exactKeys(body, ["request_id", "expected_service_instance_id",
          "expected_control_epoch", "expected_runtime_instance_id",
          "expected_game_continuity_id"]) || body.expected_service_instance_id !== serviceInstanceId
          || !nonempty(body.expected_control_epoch)
          || !nonempty(body.expected_runtime_instance_id)
          || !nonempty(body.expected_game_continuity_id)) {
          throw new Error("managed_service_recovery_precondition_required");
        }
        const claim = heldClaim;
        if (claim == null || claim.epoch !== body.expected_control_epoch
          || claim.runtimeInstanceId !== body.expected_runtime_instance_id
          || claim.gameContinuityId !== body.expected_game_continuity_id) {
          error(response, 409, "managed_service_control_epoch_mismatch"); return;
        }
        // This is the normal release path. The driver's queue waits for a
        // prior native result; a new claim/release changes its generation.
        const value = await driver.handle({ command: "release_control",
          request_id: body.request_id, control_token: claim.token,
          control_epoch: claim.epoch });
        if (heldClaim === claim) heldClaim = null;
        json(response, 200, result(value, binding(value.control_epoch, value.game_continuity_id)));
        return;
      }
      if (!exactKeys(body, ["request_id"])) throw new Error("managed_service_close_body_invalid");
      const value = await driver.handle({ command: "close", request_id: body.request_id });
      nativeClosed = true;
      closing = true;
      const closeAfterReply = () => { void closeListener(); };
      response.once("finish", closeAfterReply);
      response.once("close", closeAfterReply);
      if (response.destroyed || response.writableFinished) closeAfterReply();
      json(response, 200, result(value));
    } catch (cause) {
      request.resume();
      const closeUnconfirmed = request.url === "/v1/admin/close" && driver.closed;
      if (closeUnconfirmed) {
        closing = true;
        cleanupError = cause;
        void driver.shutdown({ force: true }).catch((error) => {
          cleanupError = error;
        }).finally(() => { void closeListener(); });
      }
      // Never echo a request body, bearer, or controller token in an error.
      const message = cause instanceof Error ? cause.message : "";
      const code = closeUnconfirmed ? "managed_service_host_close_unconfirmed"
        : PUBLIC_ERRORS.has(message) ? message : "managed_service_request_failed";
      const status = closeUnconfirmed ? 500 : code === "managed_service_body_too_large" ? 413 : 409;
      error(response, status, code);
    }
  }

  server = createServer((request, response) => { void dispatch(request, response); });
  server.headersTimeout = bodyTimeoutMs;
  server.requestTimeout = bodyTimeoutMs;
  server.maxHeadersCount = 32;
  try {
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(port, host, () => { server.off("error", reject); resolve(); });
    });
  } catch (cause) {
    await driver.shutdown({ force: true });
    throw cause;
  }
  const address = server.address();
  if (!address || typeof address === "string") {
    await driver.shutdown({ force: true });
    throw new Error("managed_service_address_unavailable");
  }
  const endpoint = `http://${host === "::1" ? "[::1]" : host}:${address.port}`;

  function closeListener() {
    if (serviceClose != null) return serviceClose;
    serviceClose = new Promise((resolve, reject) => {
      server.close((cause) => {
        clearTimeout(deadline);
        if (cause) reject(cause);
        else resolve();
      });
      // Stop accepting before forcing only remaining HTTP sockets. Native
      // dispatch and its real Receipt remain owned by the driver queue.
      const deadline = setTimeout(() => server.closeAllConnections(), bodyTimeoutMs + 1_000);
      deadline.unref();
    });
    return serviceClose;
  }

  return {
    server, driver, endpoint, serviceInstanceId, clientToken, managerToken,
    ready, get cleanupError() { return cleanupError; },
    async close({ force = true } = {}) {
      closing = true;
      const stopped = nativeClosed ? Promise.resolve() : driver.shutdown({ force });
      await Promise.all([closeListener(), stopped]);
    }
  };
}
