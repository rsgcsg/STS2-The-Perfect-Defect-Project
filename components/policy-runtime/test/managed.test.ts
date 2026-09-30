import { afterEach, describe, expect, it } from "vitest";
import { createServer, type Server } from "node:http";
import { mkdtemp, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { ManagedServicePolicyClient } from "../src/managed.js";
import type { ManagedEnvironmentBinding } from "../src/contracts.js";

const sha = (letter: string) => letter.repeat(64);
const binding: ManagedEnvironmentBinding = {
  schema: "sts2.policy-runtime/managed-environment-binding-1", profile_sha256: sha("a"), input_profile: "text-menu-v2",
  host_package_identity: { package: "@rsgcsg/sts2-host-runtime", version: "1", source_revision: "b".repeat(40),
    component_tree_revision: "c".repeat(40), release_asset_sha256: sha("d"), package_content_sha256: sha("e") },
  candidate_build: { upstream_revision: "upstream", source_patch_sha256: sha("f"), artifact_sha256: sha("1"),
    artifact_mvid: "mvid", original_sts2_sha256: sha("2"), runtime_sts2_sha256: sha("3") }
};
const hostIdentity = { package_name: binding.host_package_identity.package, version: "1",
  source_revision: binding.host_package_identity.source_revision,
  component_tree_revision: binding.host_package_identity.component_tree_revision, source_digest_sha256: sha("4") };
const target = { serviceInstanceId: "service-1", runtimeInstanceId: "runtime-1", gameContinuityId: "game-1" };
const episode = { game_continuity_id: target.gameContinuityId, control_held: false, tainted: false, closed: false };
const ready = () => ({ schema: "sts2.host-runtime/managed-service-ready-1", service_instance_id: target.serviceInstanceId,
  host_identity: hostIdentity, candidate_build: binding.candidate_build,
  exact_game: { version: "game-version", commit: "game-commit", sts2_dll_sha256: sha("3") },
  runtime_identity: { type: "managed", process_id: 1 }, adapter_runtime_instance_id: target.runtimeInstanceId,
  environment_fingerprint: "environment-1", supported_input_profiles: ["text-menu-v2"],
  text_protocol_version: "1.0.0", text_menu_contracts: [
    { protocol_version: "1.0.0", input_profile: "text-menu-v1", snapshot_schema: "sts2.player-environment/text-menu-snapshot-1",
      receipt_schema: "sts2.player-environment/text-menu-action-result-1", interaction_kinds: ["combat_turn"],
      observed_terminal_kinds: ["game_over"], action_verbs: ["end_turn"] },
    { protocol_version: "1.0.0", input_profile: "text-menu-v2", snapshot_schema: "sts2.player-environment/text-menu-snapshot-2",
      receipt_schema: "sts2.player-environment/text-menu-action-result-2", interaction_kinds: ["combat_turn"],
      observed_terminal_kinds: ["game_over"], action_verbs: ["select_card", "end_turn"] }
  ], episode });

describe("Managed Host attachment over loopback HTTP", () => {
  const servers: Server[] = [];
  const directories: string[] = [];
  afterEach(async () => {
    await Promise.all(servers.splice(0).map((server) => new Promise<void>((done) => server.close(() => done()))));
    await Promise.all(directories.splice(0).map((path) => rm(path, { recursive: true, force: true })));
    episode.control_held = false;
  });

  async function fixture(malformedClaim = false) {
    let claims = 0;
    let releases = 0;
    let requests = 0;
    const server = createServer(async (request, response) => {
      requests += 1;
      expect(request.headers.authorization).toBe("Bearer private-token");
      const body = request.method === "POST"
        ? JSON.parse(await new Promise<string>((done) => { let value = ""; request.on("data", (chunk) => { value += chunk; }); request.on("end", () => done(value)); }))
        : null;
      if (body) expect(request.headers["x-sts2-managed-service-id"]).toBe(target.serviceInstanceId);
      let result: unknown;
      if (request.url === "/v1/ready") result = ready();
      else if (body?.command === "episode_identity") result = { schema: "sts2.host-runtime/managed-service-result-1",
        service_instance_id: target.serviceInstanceId,
        result: { type: "episode_identity_result", request_id: body.request_id, identity: {
          candidate_build: binding.candidate_build, runtime_identity: ready().runtime_identity,
          adapter_runtime_instance_id: target.runtimeInstanceId, environment_fingerprint: "environment-1",
          episode_provenance: { verdict: "provenance_pass", requested_seed: "seed", actual_seed: "seed",
            runtime_instance_id: target.runtimeInstanceId } } } };
      else if (body?.command === "claim_control") {
        expect(body.expected_runtime_instance_id).toBe(target.runtimeInstanceId);
        expect(body.expected_game_continuity_id).toBe(target.gameContinuityId);
        claims += 1; episode.control_held = true;
        result = { schema: "sts2.host-runtime/managed-service-result-1", service_instance_id: target.serviceInstanceId,
          result: { type: "claim_control_result", request_id: body.request_id, control_token: "private-control",
            control_epoch: "epoch-1", runtime_instance_id: target.runtimeInstanceId,
            game_continuity_id: target.gameContinuityId },
          control_binding: { control_epoch: "epoch-1", runtime_instance_id: target.runtimeInstanceId,
            game_continuity_id: malformedClaim ? "different-game" : target.gameContinuityId } };
      } else if (body?.command === "release_control") {
        releases += 1; episode.control_held = false;
        result = { schema: "sts2.host-runtime/managed-service-result-1", service_instance_id: target.serviceInstanceId,
          result: { type: "release_control_result", request_id: body.request_id, status: "released",
            control_epoch: "epoch-1", runtime_instance_id: target.runtimeInstanceId,
            game_continuity_id: target.gameContinuityId },
          control_binding: { control_epoch: "epoch-1", runtime_instance_id: target.runtimeInstanceId,
            game_continuity_id: target.gameContinuityId } };
      } else throw new Error(`unexpected request ${request.url}`);
      response.writeHead(200, { "content-type": "application/json" }); response.end(JSON.stringify(result));
    });
    await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
    servers.push(server);
    const address = server.address();
    if (!address || typeof address === "string") throw new Error("loopback fixture unavailable");
    const directory = await realpath(resolve(await mkdtemp(join(tmpdir(), "managed-runtime-test-"))));
    directories.push(directory);
    const bindingPath = join(directory, "binding.json");
    const attachmentPath = join(directory, "attachment.json");
    await writeFile(bindingPath, JSON.stringify(binding));
    await writeFile(attachmentPath, JSON.stringify({ schema: "sts2.host-runtime/managed-service-attachment-1",
      endpoint: `http://127.0.0.1:${address.port}`, service_instance_id: target.serviceInstanceId,
      host_identity: hostIdentity, role: "client", token: "private-token" }), { mode: 0o600 });
    return { bindingPath, attachmentPath, counts: () => ({ claims, releases, requests }) };
  }

  it("attaches to the explicit episode, confirms one claim and release, and leaves Host running", async () => {
    const files = await fixture();
    const client = await ManagedServicePolicyClient.attach(files.bindingPath, files.attachmentPath, target);
    expect(client.initialEnvironment).toMatchObject({ service_instance_id: target.serviceInstanceId,
      runtime_instance_id: target.runtimeInstanceId, game_continuity_id: target.gameContinuityId });
    expect(await client.acquireController()).toMatchObject({ status: "held", control_epoch: "epoch-1" });
    expect(await client.releaseController()).toMatchObject({ status: "released", control_epoch: "epoch-1" });
    expect(files.counts()).toMatchObject({ claims: 1, releases: 1 });
    expect(await client.capabilities()).toMatchObject({ execution_available: true, control_owned: false });
  });

  it("does not infer release or retry when Host claimed but the confirmation is corrupt", async () => {
    const files = await fixture(true);
    const client = await ManagedServicePolicyClient.attach(files.bindingPath, files.attachmentPath, target);
    await expect(client.acquireController()).rejects.toThrow(/binding/);
    await expect(client.acquireController()).rejects.toThrow(/quarantined/);
    await expect(client.releaseController()).rejects.toThrow(/not held/);
    expect(files.counts()).toMatchObject({ claims: 1, releases: 0 });
    expect(episode.control_held).toBe(true);
  });

  it("rejects a stale selected game before claiming", async () => {
    const files = await fixture();
    await expect(ManagedServicePolicyClient.attach(files.bindingPath, files.attachmentPath,
      { ...target, gameContinuityId: "previous-game" })).rejects.toThrow(/explicit target/);
    expect(files.counts().claims).toBe(0);
  });
});
