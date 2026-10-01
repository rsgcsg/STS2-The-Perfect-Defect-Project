import assert from "node:assert/strict";
import { request as httpRequest } from "node:http";
import test from "node:test";
import { SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL } from "@rsgcsg/sts2-connector-client";
import { ManagedPlayerEnvironmentSession } from "../src/managed-player-environment.mjs";
import { startManagedHostService } from "../src/managed-host-service.mjs";
import { managedTextMenuV1Contract } from "../src/managed-text-menu-map.mjs";
import { managedTextMenuV2Contract } from "../src/managed-text-menu-v2.mjs";

function decision(floor = 0) {
  return {
    type: "decision", decision: "map_select",
    context: { act: 1, act_index: 0, act_definition_id: "OVERGROWTH",
      act_name: "Overgrowth", floor, total_floor: floor, ascension: 0,
      bosses: [{ id: "VANTOM_BOSS", name: "Vantom", order: 0 }], modifiers: [] },
    choices: [{ col: 3, row: 0, type: "Monster", native_ref: "native-map-point",
      children: [{ col: 2, row: 1, type: "Monster" }] }],
    visible_map: { type: "map", rows: [[{ col: 2, row: 1, type: "Monster",
      children: [{ col: 3, row: 16 }], visited: false, current: false }]],
      boss: { col: 3, row: 16, type: "Boss" }, current_coord: null },
    player: { name: "The Ironclad", character_id: "IRONCLAD", hp: 80,
      max_hp: 80, gold: 99, native_ref: "player-native", max_potion_slots: 3,
      relics: [], potions: [], deck: [] }
  };
}

function fixture() {
  let floor = 0;
  let pendingAction = null;
  let actionEntered = null;
  let unknown = false;
  let stopCount = 0;
  let pendingStop = null;
  let stopEntered = null;
  const calls = [];
  const process = {
    async request(request) {
      calls.push(request);
      if (request.cmd === "start_run" || request.cmd === "reset_run") {
        floor = 0; return decision();
      }
      if (request.cmd === "get_map") return decision(floor).visible_map;
      if (request.cmd === "run_identity") return { type: "run_identity", active: true, seed: "SEED" };
      if (request.cmd === "action") {
        actionEntered?.();
        if (pendingAction) await pendingAction;
        if (unknown) throw new Error("transport unknown after native action");
        floor += 1;
        return decision(floor);
      }
      throw new Error("unexpected native request");
    },
    async stop() {
      stopCount += 1;
      stopEntered?.();
      if (pendingStop) await pendingStop;
      return { code: 0 };
    }
  };
  const runtime = {
    process, manifest: { candidate_id: "synthetic-candidate" },
    exactGame: { version: "test-version", commit: "test-commit" },
    build: { artifact_sha256: "f".repeat(64),
      candidate_directory: "/private/secret/candidate",
      artifact: "/private/secret/candidate/host.dll" },
    runtimeIdentity: { kind: "synthetic", private_path: "/private/secret/runtime" },
    adapterRuntimeInstanceId: "runtime-one"
  };
  return {
    started: { session: new ManagedPlayerEnvironmentSession({ process,
      runtimeInstanceId: "runtime-one", environmentFingerprint: "environment-one" }),
    runtime, environmentFingerprint: "environment-one" },
    calls, get stopCount() { return stopCount; },
    holdAction(promise, entered) { pendingAction = promise; actionEntered = entered; },
    holdStop(promise, entered) { pendingStop = promise; stopEntered = entered; },
    makeUnknown() { unknown = true; }
  };
}

const hostIdentity = { package_name: "@rsgcsg/sts2-host-runtime", version: "test-version",
  distribution_kind: "git_checkout", source_digest_sha256: "a".repeat(64) };

async function call(service, token, route, body = undefined, options = {}) {
  const response = await fetch(`${service.endpoint}${route}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { authorization: `Bearer ${token}`,
      ...(body === undefined ? {} : {
        "content-type": "application/json",
        "x-sts2-managed-service-id": service.serviceInstanceId
      }), ...options.headers },
    ...(body === undefined ? {} : { body: JSON.stringify(body) })
  });
  return { status: response.status, body: await response.json() };
}

function command(service, token, value) {
  return call(service, token, "/v1/command", { request_id: crypto.randomUUID(), ...value });
}

async function reset(service, expectedGameContinuityId = null) {
  return call(service, service.managerToken, "/v1/admin/reset", {
    request_id: crypto.randomUUID(), seed: "SEED",
    expected_runtime_instance_id: "runtime-one",
    expected_game_continuity_id: expectedGameContinuityId
  });
}

test("two attached clients see one Managed runtime, owner menu and explicit Host close", async () => {
  const native = fixture();
  const service = await startManagedHostService(native.started, { hostIdentity });
  try {
    const first = await call(service, service.clientToken, "/v1/ready");
    const second = await call(service, service.clientToken, "/v1/ready");
    assert.equal(first.body.service_instance_id, second.body.service_instance_id);
    assert.equal(first.body.adapter_runtime_instance_id, "runtime-one");
    assert.equal(first.body.text_protocol_version, SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL);
    assert.deepEqual(first.body.text_menu_contracts,
      [managedTextMenuV1Contract(), managedTextMenuV2Contract()]);
    assert.deepEqual(first.body.text_menu_contracts.map((item) => item.input_profile),
      first.body.supported_input_profiles);
    assert.equal(first.body.episode.game_continuity_id, null);
    assert.equal(JSON.stringify(first.body).includes(service.clientToken), false);
    assert.equal(JSON.stringify(first.body).includes("/private/secret"), false);
    assert.equal((await reset(service)).status, 200);
    const identity = await command(service, service.clientToken,
      { command: "episode_identity" });
    assert.equal(identity.status, 200);
    assert.equal(JSON.stringify(identity.body).includes("/private/secret"), false);
    const mounted = await call(service, service.clientToken, "/v1/ready");
    const continuity = mounted.body.episode.game_continuity_id;
    assert.ok(continuity?.startsWith("managed_episode_"));
    assert.equal((await call(service, service.clientToken, "/v1/ready"))
      .body.episode.game_continuity_id, continuity);
    assert.equal((await reset(service)).status, 409, "old initial reset cannot replace mounted episode");
    assert.equal((await command(service, service.clientToken,
      { command: "claim_control", expected_runtime_instance_id: "runtime-one",
        expected_game_continuity_id: "stale" })).status, 409);
    const claimResponse = await command(service, service.clientToken,
      { command: "claim_control", expected_runtime_instance_id: "runtime-one",
        expected_game_continuity_id: continuity,
        text_state_owner: `workbench:${"a".repeat(32)}` });
    assert.equal(claimResponse.status, 200);
    const claim = claimResponse.body.result;
    assert.equal(claim.text_state_owner, `workbench:${"a".repeat(32)}`);
    assert.equal(claimResponse.body.control_binding.control_epoch, claim.control_epoch);
    assert.equal((await command(service, service.clientToken,
      { command: "claim_control", expected_runtime_instance_id: "runtime-one",
        expected_game_continuity_id: continuity })).status, 409);
    const ownerBefore = await command(service, service.clientToken,
      { command: "text_observe", input_profile: "text-menu-v2",
        control_token: claim.control_token, control_epoch: claim.control_epoch });
    assert.equal(ownerBefore.status, 200);
    const denied = await command(service, service.clientToken,
      { command: "text_observe", input_profile: "text-menu-v2" });
    assert.equal(denied.status, 409);
    assert.equal(denied.body.error, "managed_control_not_authorized");
    const pageResponse = await command(service, service.clientToken,
      { command: "text_observe", input_profile: "text-menu-v2",
        control_token: claim.control_token, control_epoch: claim.control_epoch });
    assert.equal(pageResponse.status, 200);
    const page = pageResponse.body.result.context;
    assert.equal(page.snapshot.snapshot_id, ownerBefore.body.result.context.snapshot.snapshot_id);
    assert.equal(page.snapshot.sequence, ownerBefore.body.result.context.snapshot.sequence);
    assert.equal(page.game_continuity_id, continuity);
    const status = await call(service, service.managerToken, "/v1/admin/status");
    assert.equal(status.body.status.control.control_epoch, claim.control_epoch);
    assert.equal(JSON.stringify(status.body).includes(claim.control_token), false);
    const applied = await command(service, service.clientToken,
      { command: "text_submit", input_profile: "text-menu-v2",
        control_token: claim.control_token, control_epoch: claim.control_epoch,
        mutation_request_id: "owner-action", expected_game_continuity_id: continuity,
        expected_snapshot_id: page.snapshot.snapshot_id,
        action_id: page.snapshot.menu_actions.actions[0].action_id });
    assert.equal(applied.status, 200);
    assert.equal(applied.body.result.result.status, "applied");
    assert.equal(applied.body.control_binding.game_continuity_id, continuity);
    const released = await command(service, service.clientToken,
      { command: "release_control", control_token: claim.control_token,
        control_epoch: claim.control_epoch });
    assert.equal(released.body.result.status, "released");
    assert.equal(native.stopCount, 0, "release and HTTP detach keep Host alive");
    assert.equal((await call(service, service.clientToken, "/v1/ready"))
      .body.episode.game_continuity_id, continuity);
    const closed = await call(service, service.managerToken, "/v1/admin/close",
      { request_id: "host-close" });
    assert.equal(closed.body.result.type, "close_result");
    assert.equal(native.stopCount, 1);
  } finally {
    await service.close();
  }
});

test("shared v2 rejects a missing owner while the legacy flat v1 path still works", async () => {
  const service = await startManagedHostService(fixture().started, { hostIdentity });
  try {
    await reset(service);
    const continuity = (await call(service, service.clientToken, "/v1/ready"))
      .body.episode.game_continuity_id;
    const legacyClaim = (await command(service, service.clientToken, {
      command: "claim_control", expected_runtime_instance_id: "runtime-one",
      expected_game_continuity_id: continuity
    })).body.result;
    const legacyPage = await command(service, service.clientToken, {
      command: "text_observe", input_profile: "text-menu-v1",
      control_token: legacyClaim.control_token, control_epoch: legacyClaim.control_epoch
    });
    assert.equal(legacyPage.status, 200);
    await command(service, service.clientToken, { command: "release_control",
      control_token: legacyClaim.control_token, control_epoch: legacyClaim.control_epoch });

    const unscopedClaim = (await command(service, service.clientToken, {
      command: "claim_control", expected_runtime_instance_id: "runtime-one",
      expected_game_continuity_id: continuity
    })).body.result;
    const unscopedV2 = await command(service, service.clientToken, {
      command: "text_observe", input_profile: "text-menu-v2",
      control_token: unscopedClaim.control_token, control_epoch: unscopedClaim.control_epoch
    });
    assert.equal(unscopedV2.status, 409);
    assert.equal(unscopedV2.body.error, "managed_text_state_owner_required");
    await command(service, service.clientToken, { command: "release_control",
      control_token: unscopedClaim.control_token, control_epoch: unscopedClaim.control_epoch });
  } finally {
    await service.close();
  }
});

test("manager status attributes a lost claim ack only to current held control", async () => {
  const service = await startManagedHostService(fixture().started, { hostIdentity });
  try {
    assert.equal((await reset(service)).status, 200);
    const continuity = (await call(service, service.clientToken, "/v1/ready"))
      .body.episode.game_continuity_id;
    const requestId = "claim-with-lost-body";
    const body = JSON.stringify({ command: "claim_control", request_id: requestId,
      expected_runtime_instance_id: "runtime-one",
      expected_game_continuity_id: continuity });
    // Receive only the HTTP headers, then lose the credential-bearing body.
    await new Promise((resolve, reject) => {
      const offered = httpRequest(`${service.endpoint}/v1/command`, {
        method: "POST", headers: { authorization: `Bearer ${service.clientToken}`,
          "content-type": "application/json",
          "x-sts2-managed-service-id": service.serviceInstanceId,
          "content-length": Buffer.byteLength(body) }
      }, (response) => {
        assert.equal(response.statusCode, 200);
        response.destroy();
        resolve();
      });
      offered.on("error", reject);
      offered.end(body);
    });
    const denied = await call(service, service.clientToken, "/v1/admin/status");
    assert.equal(denied.status, 403);
    const first = await call(service, service.managerToken, "/v1/admin/status");
    assert.equal(first.body.status.control.claim_request_id, requestId);
    assert.equal(first.body.status.control.runtime_instance_id, "runtime-one");
    assert.equal(first.body.status.control.game_continuity_id, continuity);
    assert.equal(JSON.stringify((await call(service, service.clientToken, "/v1/ready")).body)
      .includes(requestId), false);
    const released = await call(service, service.managerToken, "/v1/admin/recover-control", {
      request_id: "recover-lost-claim",
      expected_service_instance_id: service.serviceInstanceId,
      expected_control_epoch: first.body.status.control.control_epoch,
      expected_runtime_instance_id: "runtime-one",
      expected_game_continuity_id: continuity
    });
    assert.equal(released.body.result.status, "released");
    assert.equal((await call(service, service.managerToken, "/v1/admin/status"))
      .body.status.control, null);

    const newClaim = (await command(service, service.clientToken, {
      command: "claim_control", request_id: "new-claim",
      expected_runtime_instance_id: "runtime-one",
      expected_game_continuity_id: continuity
    })).body.result;
    const current = await call(service, service.managerToken, "/v1/admin/status");
    assert.equal(current.body.status.control.claim_request_id, "new-claim");
    assert.equal(current.body.status.control.control_epoch, newClaim.control_epoch);
    assert.equal(JSON.stringify(current.body).includes(newClaim.control_token), false);
    assert.equal(JSON.stringify((await call(service, service.clientToken, "/v1/ready")).body)
      .includes("new-claim"), false);

    // A driver-level transition can precede the HTTP handler clearing its
    // retained metadata. It must never attribute that metadata to a new owner.
    await service.driver.handle({ command: "release_control", request_id: "direct-release",
      control_token: newClaim.control_token, control_epoch: newClaim.control_epoch });
    const stale = await service.driver.handle({ command: "claim_control",
      request_id: "direct-new-owner", expected_runtime_instance_id: "runtime-one",
      expected_game_continuity_id: continuity });
    const status = await call(service, service.managerToken, "/v1/admin/status");
    assert.equal(status.body.status.control.control_epoch, stale.control_epoch);
    assert.equal(Object.hasOwn(status.body.status.control, "claim_request_id"), false);
    assert.equal(JSON.stringify(status.body).includes(newClaim.control_token), false);
    assert.equal(JSON.stringify(status.body).includes(stale.control_token), false);
  } finally {
    await service.close();
  }
});

function delayedPost(service, token, route, body) {
  const serialized = JSON.stringify(body);
  let request;
  const response = new Promise((resolve, reject) => {
    request = httpRequest(`${service.endpoint}${route}`, {
      method: "POST",
      headers: { authorization: `Bearer ${token}`,
        "content-type": "application/json",
        "x-sts2-managed-service-id": service.serviceInstanceId,
        "content-length": Buffer.byteLength(serialized) }
    }, (received) => {
      const chunks = [];
      received.on("data", (chunk) => chunks.push(chunk));
      received.on("end", () => resolve({ status: received.statusCode,
        body: JSON.parse(Buffer.concat(chunks).toString("utf8")) }));
    });
    request.on("error", reject);
  });
  request.write(serialized.slice(0, 8));
  return { finish() { request.end(serialized.slice(8)); return response; } };
}

test("delayed HTTP claim and reset bodies cannot inherit a later episode", async () => {
  const native = fixture();
  const service = await startManagedHostService(native.started, { hostIdentity });
  try {
    const staleInitialReset = delayedPost(service, service.managerToken, "/v1/admin/reset", {
      request_id: "late-reset", seed: "SEED",
      expected_runtime_instance_id: "runtime-one", expected_game_continuity_id: null
    });
    assert.equal((await reset(service)).status, 200);
    assert.equal((await staleInitialReset.finish()).body.error, "stale_game_continuity");
    const oldContinuity = (await call(service, service.clientToken, "/v1/ready"))
      .body.episode.game_continuity_id;
    const staleClaim = delayedPost(service, service.clientToken, "/v1/command", {
      command: "claim_control", request_id: "late-claim",
      expected_runtime_instance_id: "runtime-one",
      expected_game_continuity_id: oldContinuity
    });
    assert.equal((await reset(service, oldContinuity)).status, 200);
    const stale = await staleClaim.finish();
    assert.equal(stale.status, 409);
    assert.equal(stale.body.error, "stale_game_continuity");
    assert.equal((await call(service, service.clientToken, "/v1/ready"))
      .body.episode.control_held, false);
  } finally {
    await service.close();
  }
});

test("manager recovery waits for in-flight native outcome and preserves unknown taint", async () => {
  const native = fixture();
  const service = await startManagedHostService(native.started, { hostIdentity });
  let releaseNative;
  try {
    await reset(service);
    const continuity = (await call(service, service.clientToken, "/v1/ready"))
      .body.episode.game_continuity_id;
    const claim = (await command(service, service.clientToken,
      { command: "claim_control", expected_runtime_instance_id: "runtime-one",
        expected_game_continuity_id: continuity })).body.result;
    const page = (await command(service, service.clientToken,
      { command: "text_observe", control_token: claim.control_token,
        control_epoch: claim.control_epoch })).body.result.context;
    let entered;
    const nativeEntered = new Promise((resolve) => { entered = resolve; });
    native.holdAction(new Promise((resolve) => { releaseNative = resolve; }), entered);
    native.makeUnknown();
    const action = command(service, service.clientToken,
      { command: "text_submit", control_token: claim.control_token,
        control_epoch: claim.control_epoch, mutation_request_id: "unknown-action",
        expected_game_continuity_id: continuity,
        expected_snapshot_id: page.snapshot.snapshot_id,
        action_id: page.snapshot.menu_actions.actions[0].action_id });
    await nativeEntered;
    const recovery = call(service, service.managerToken, "/v1/admin/recover-control", {
      request_id: "recover", expected_service_instance_id: service.serviceInstanceId,
      expected_control_epoch: claim.control_epoch,
      expected_runtime_instance_id: "runtime-one",
      expected_game_continuity_id: continuity
    });
    let recovered = false;
    recovery.then(() => { recovered = true; });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(recovered, false);
    releaseNative();
    assert.equal((await action).body.result.result.status, "unknown");
    const ack = await recovery;
    assert.equal(ack.body.result.status, "released");
    assert.equal((await call(service, service.clientToken, "/v1/ready"))
      .body.episode.tainted, true);
    assert.equal((await reset(service, continuity)).status, 409);
    assert.equal(native.stopCount, 0);
  } finally {
    releaseNative?.();
    await service.close();
  }
});

test("aborted manager close still closes listener after native cleanup", async () => {
  const native = fixture();
  const service = await startManagedHostService(native.started, { hostIdentity });
  let releaseStop;
  try {
    await reset(service);
    let entered;
    const stopEntered = new Promise((resolve) => { entered = resolve; });
    native.holdStop(new Promise((resolve) => { releaseStop = resolve; }), entered);
    const request = httpRequest(`${service.endpoint}/v1/admin/close`, {
      method: "POST", headers: { authorization: `Bearer ${service.managerToken}`,
        "content-type": "application/json",
        "x-sts2-managed-service-id": service.serviceInstanceId }
    });
    request.on("error", () => undefined);
    const listenerClosed = new Promise((resolve) => service.server.once("close", resolve));
    request.end(JSON.stringify({ request_id: "aborted-close" }));
    await stopEntered;
    request.destroy();
    releaseStop();
    let timeout;
    await Promise.race([listenerClosed,
      new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error("listener stayed open")), 1_000); })
    ]).finally(() => clearTimeout(timeout));
    assert.equal(native.stopCount, 1);
    assert.equal(service.driver.closed, true);
  } finally {
    releaseStop?.();
    await service.close();
  }
});

test("invalid service config reaps an already started native child", async () => {
  const native = fixture();
  await assert.rejects(startManagedHostService(native.started,
    { hostIdentity, host: "0.0.0.0" }), /managed_service_loopback_only/);
  assert.equal(native.stopCount, 1);
});

test("loopback bearer, Host/Origin and command scope fail closed without token echo", async () => {
  const native = fixture();
  const service = await startManagedHostService(native.started, { hostIdentity });
  try {
    assert.equal((await call(service, "wrong", "/v1/ready")).status, 401);
    assert.equal((await call(service, service.clientToken, "/v1/ready",
      undefined, { headers: { origin: "https://evil.example" } })).status, 403);
    const wrongHost = await new Promise((resolve, reject) => {
      const request = httpRequest(`${service.endpoint}/v1/ready`, {
        method: "GET", headers: { host: "evil.example",
          authorization: `Bearer ${service.clientToken}` }
      }, (response) => { response.resume(); response.once("end", () => resolve(response.statusCode)); });
      request.once("error", reject);
      request.end();
    });
    assert.equal(wrongHost, 403);
    assert.equal((await command(service, service.clientToken,
      { command: "close" })).status, 403);
    assert.equal((await command(service, service.clientToken,
      { command: "step" })).status, 403);
    assert.equal((await call(service, service.clientToken, "/v1/admin/close",
      { request_id: "unauthorized-close" })).status, 403);
    const wrongService = await fetch(`${service.endpoint}/v1/command`, {
      method: "POST", headers: { authorization: `Bearer ${service.clientToken}`,
        "content-type": "application/json", "x-sts2-managed-service-id": "stale" },
      body: JSON.stringify({ command: "observe", request_id: "stale-service" })
    });
    assert.equal(wrongService.status, 409);
    const error = await wrongService.text();
    assert.equal(error.includes(service.clientToken), false);
    const oversized = await command(service, service.clientToken,
      { command: "observe", padding: "x".repeat(65 * 1024) });
    assert.equal(oversized.status, 413);
    const malformed = await fetch(`${service.endpoint}/v1/command`, {
      method: "POST", headers: { authorization: `Bearer ${service.clientToken}`,
        "content-type": "application/json",
        "x-sts2-managed-service-id": service.serviceInstanceId },
      body: `{${service.clientToken}`
    });
    assert.equal(malformed.status, 409);
    assert.equal((await malformed.text()).includes(service.clientToken), false);
    assert.equal(native.stopCount, 0);
  } finally {
    await service.close();
  }
});
