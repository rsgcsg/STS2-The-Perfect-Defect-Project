import { describe, it, expect } from "vitest";
import { mkdtemp, writeFile, realpath, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
// The actual Host service/actor and Managed projection, with only native process input synthetic.
// @ts-expect-error Host owner uses native ESM, consumed here without a TS reimplementation.
import { startManagedHostService } from "../../host-runtime/src/managed-host-service.mjs";
// @ts-expect-error same original Host module.
import { ManagedPlayerEnvironmentSession } from "../../host-runtime/src/managed-player-environment.mjs";
// @ts-expect-error Shared synthetic DTO follows the original Host terminal owner.
import { gameOver } from "../../host-runtime/test/fixtures/managed-game-over.mjs";
import { inProcessManagedChannel, ManagedServicePolicyClient, type ManagedInProcessService } from "../src/managed.js";
import { globalRuntimeFixture } from "./global-runtime-fixtures.js";
import { replayGlobalAgentRun } from "../src/global-replay.js";

/** One synthetic native world behind the real Host service (same fixture as global-managed-runtime). */
async function managedSource() {
  let terminal = false, actions = 0;
  const context = (floor: number) => ({ act: 1, act_index: 0, act_definition_id: "OVERGROWTH", act_name: "Overgrowth", floor, total_floor: floor, ascension: 0, bosses: [], modifiers: [] });
  const player = (hp: number) => ({ name: "The Ironclad", character_id: "IRONCLAD", hp, max_hp: 80, gold: 99, native_ref: "player", max_potion_slots: 3, relics: [], potions: [], deck: [] });
  const decision = () => terminal ? gameOver({ type: "decision", context: context(1), player: player(0) }) : {
    type: "decision", decision: "map_select", context: context(0),
    choices: [{ col: 3, row: 1, type: "Monster", native_ref: "point", children: [] }],
    visible_map: { type: "map", rows: [[{ col: 3, row: 0, type: "Monster", visited: true, children: [{ col: 3, row: 1 }] }]], boss: { col: 3, row: 16, type: "Boss" }, current_coord: { col: 3, row: 0 } },
    player: player(80) };
  const process = { async request(value: any) {
    if (value.cmd === "start_run") return decision();
    if (value.cmd === "get_map") return decision().visible_map;
    if (value.cmd === "run_identity") return { type: "run_identity", active: true, seed: "SEED" };
    if (value.cmd === "action") { actions++; terminal = true; return decision(); }
    throw Error("unexpected:" + value.cmd);
  }, async stop() { return { code: 0 }; } };
  const build = { upstream_revision: "upstream", source_patch_sha256: "f".repeat(64), artifact_sha256: "1".repeat(64), artifact_mvid: "mvid", original_sts2_sha256: "2".repeat(64), runtime_sts2_sha256: "3".repeat(64) };
  const host = { package_name: "@rsgcsg/sts2-host-runtime", version: "1", distribution_kind: "git_checkout", source_revision: "b".repeat(40), component_tree_revision: "c".repeat(40), source_digest_sha256: "4".repeat(64) };
  const session = new ManagedPlayerEnvironmentSession({ process, runtimeInstanceId: "managed-runtime", environmentFingerprint: "managed-environment" });
  const runtime = { process, manifest: { candidate_id: "synthetic" }, exactGame: { version: "fixture", commit: "fixture" }, build, runtimeIdentity: { kind: "synthetic" }, adapterRuntimeInstanceId: "managed-runtime" };
  const service = await startManagedHostService({ session, runtime, environmentFingerprint: "managed-environment" }, { hostIdentity: host });
  try {
    const manager = { authorization: `Bearer ${service.managerToken}`, "content-type": "application/json", "x-sts2-managed-service-id": service.serviceInstanceId };
    const reset = await fetch(service.endpoint + "/v1/admin/reset", { method: "POST", headers: manager,
      body: JSON.stringify({ request_id: "reset", seed: "SEED", expected_runtime_instance_id: "managed-runtime", expected_game_continuity_id: null }) });
    expect(reset.status).toBe(200);
    const ready = await (await fetch(service.endpoint + "/v1/ready", { headers: { authorization: `Bearer ${service.clientToken}` } })).json() as any;
    const directory = await realpath(await mkdtemp(join(tmpdir(), "managed-inprocess-")));
    const bindingPath = join(directory, "binding.json"), attachmentPath = join(directory, "attachment.json");
    await writeFile(bindingPath, JSON.stringify({ schema: "sts2.policy-runtime/managed-environment-binding-1", profile_sha256: "a".repeat(64), input_profile: "public-basis-1",
      host_package_identity: { package: host.package_name, version: host.version, source_revision: host.source_revision, component_tree_revision: host.component_tree_revision, release_asset_sha256: "d".repeat(64), package_content_sha256: "e".repeat(64) }, candidate_build: build }));
    await writeFile(attachmentPath, JSON.stringify({ schema: "sts2.host-runtime/managed-service-attachment-1", endpoint: service.endpoint, service_instance_id: service.serviceInstanceId,
      host_identity: ready.host_identity, role: "client", token: service.clientToken }), { mode: 0o600 });
    const target = { serviceInstanceId: service.serviceInstanceId, runtimeInstanceId: "managed-runtime", gameContinuityId: ready.episode.game_continuity_id };
    return { service, bindingPath, attachmentPath, target, get actions() { return actions; } };
  } catch (error) { await service.close(); throw error; }
}

const RUN = (n: number) => `run-00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const kinds = async (directory: string) => (await readFile(join(directory, "events.jsonl"), "utf8")).trim().split("\n").map((line) => JSON.parse(line).kind as string);

describe("in-process Managed transport (S1 ③a-1)", () => {
  it("returns the exact bytes HTTP returns for the same request on the same service", async () => {
    const source = await managedSource();
    try {
      const ready = await fetch(source.service.endpoint + "/v1/ready", { headers: { authorization: `Bearer ${source.service.clientToken}` } });
      const httpBytes = Buffer.from(await ready.arrayBuffer());
      const local = await source.service.inProcess.request({ method: "GET", route: "/v1/ready", authorization: `Bearer ${source.service.clientToken}` });
      expect(local.status).toBe(ready.status);
      expect(Buffer.from(local.bytes).equals(httpBytes)).toBe(true);
      const command = (id: string) => JSON.stringify({ command: "episode_identity", request_id: id });
      const post = { authorization: `Bearer ${source.service.clientToken}`, "content-type": "application/json", "x-sts2-managed-service-id": source.service.serviceInstanceId };
      const httpIdentity = Buffer.from(await (await fetch(source.service.endpoint + "/v1/command", { method: "POST", headers: post, body: command("same-id") })).arrayBuffer());
      const localIdentity = await source.service.inProcess.request({ method: "POST", route: "/v1/command", authorization: post.authorization,
        serviceInstanceId: post["x-sts2-managed-service-id"], contentType: post["content-type"], body: command("same-id") });
      expect(Buffer.from(localIdentity.bytes).equals(httpIdentity)).toBe(true);
    } finally { await source.service.close(); }
  });

  it("keeps the HTTP admission: client role only, bearer, service instance, content type, body bound", async () => {
    const source = await managedSource();
    const s = source.service;
    const parse = (reply: { bytes: Uint8Array }) => JSON.parse(Buffer.from(reply.bytes).toString("utf8"));
    try {
      const manager = await s.inProcess.request({ method: "GET", route: "/v1/admin/status", authorization: `Bearer ${s.managerToken}` });
      expect([manager.status, parse(manager).error]).toEqual([403, "managed_service_inprocess_client_only"]);
      const managerReset = await s.inProcess.request({ method: "POST", route: "/v1/admin/reset", authorization: `Bearer ${s.managerToken}`,
        serviceInstanceId: s.serviceInstanceId, contentType: "application/json", body: JSON.stringify({ request_id: "r", seed: "S", expected_runtime_instance_id: "managed-runtime", expected_game_continuity_id: null }) });
      expect([managerReset.status, parse(managerReset).error]).toEqual([403, "managed_service_inprocess_client_only"]);
      const wrongToken = await s.inProcess.request({ method: "GET", route: "/v1/ready", authorization: "Bearer not-the-token" });
      expect([wrongToken.status, parse(wrongToken).error]).toEqual([401, "managed_service_unauthorized"]);
      const wrongService = await s.inProcess.request({ method: "POST", route: "/v1/command", authorization: `Bearer ${s.clientToken}`,
        serviceInstanceId: "managed_service_other", contentType: "application/json", body: JSON.stringify({ command: "episode_identity", request_id: "x" }) });
      expect([wrongService.status, parse(wrongService).error]).toEqual([409, "managed_service_instance_mismatch"]);
      const wrongType = await s.inProcess.request({ method: "POST", route: "/v1/command", authorization: `Bearer ${s.clientToken}`,
        serviceInstanceId: s.serviceInstanceId, contentType: "text/plain", body: "{}" });
      expect([wrongType.status, parse(wrongType).error]).toEqual([415, "managed_service_json_required"]);
      const oversized = await s.inProcess.request({ method: "POST", route: "/v1/command", authorization: `Bearer ${s.clientToken}`,
        serviceInstanceId: s.serviceInstanceId, contentType: "application/json", body: JSON.stringify({ command: "episode_identity", request_id: "y", pad: "x".repeat(70 * 1024) }) });
      expect([oversized.status, parse(oversized).error]).toEqual([413, "managed_service_body_too_large"]);
      const managerStep = await s.inProcess.request({ method: "POST", route: "/v1/command", authorization: `Bearer ${s.clientToken}`,
        serviceInstanceId: s.serviceInstanceId, contentType: "application/json", body: JSON.stringify({ command: "performance", request_id: "z", expected_runtime_instance_id: "managed-runtime", expected_game_continuity_id: source.target.gameContinuityId }) });
      expect([managerStep.status, parse(managerStep).error]).toEqual([403, "managed_service_command_not_allowed"]);
      await expect(ManagedServicePolicyClient.attachGlobal(source.bindingPath, source.attachmentPath, source.target, RUN(11),
        { serviceInstanceId: "managed_service_other", request: s.inProcess.request })).rejects.toThrow("Managed in-process service differs from attachment");
    } finally { await s.close(); }
  });

  it("offers nothing when aborted before send; an abort after send only stops waiting", async () => {
    const source = await managedSource();
    let offered = 0, release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const counted: ManagedInProcessService = { serviceInstanceId: source.service.serviceInstanceId,
      async request(input) { offered++; await gate; return source.service.inProcess.request(input); } };
    const channel = inProcessManagedChannel(counted);
    const headers = { authorization: `Bearer ${source.service.clientToken}` };
    try {
      const before = new AbortController(); before.abort(new Error("stop_before_send"));
      await expect(channel.request({ method: "GET", route: "/v1/ready", headers, signal: before.signal, maxResponseBytes: 1 << 20 })).rejects.toThrow("stop_before_send");
      expect(offered).toBe(0);
      const after = new AbortController();
      const waiting = channel.request({ method: "GET", route: "/v1/ready", headers, signal: after.signal, maxResponseBytes: 1 << 20 });
      expect(offered).toBe(1);
      after.abort(new Error("stop_after_send"));
      await expect(waiting).rejects.toThrow("stop_after_send");
      release();
      const small = await channel.request({ method: "GET", route: "/v1/ready", headers, signal: new AbortController().signal, maxResponseBytes: 16 }).catch((error: Error) => error.message);
      expect(small).toBe("Managed service response is oversized");
    } finally { release(); await source.service.close(); }
  });

  it.each(["event", "decision"] as const)("runs the same %s-durability game over HTTP and in-process", async (durability) => {
    const runs: { kinds: string[]; actions: number; verdict: string }[] = [];
    for (const [index, local] of [[1, false], [2, true]] as const) {
      const source = await managedSource();
      let f: Awaited<ReturnType<typeof globalRuntimeFixture>> | undefined;
      try {
        const transport = await ManagedServicePolicyClient.attachGlobal(source.bindingPath, source.attachmentPath, source.target, RUN(index),
          local ? source.service.inProcess : undefined);
        f = await globalRuntimeFixture({ transport, evidenceDurability: durability });
        expect((await f.runtime.tick()).type).toBe("delivered");
        expect((await f.runtime.tick()).type).toBe("closed");
        expect(source.service.driver.status().control_held).toBe(false);
        runs.push({ kinds: await kinds(f.evidence.directory), actions: source.actions, verdict: (await replayGlobalAgentRun(f.evidence.directory)).verdict });
      } finally { await f?.close(); await source.service.close(); }
    }
    expect(runs[1]).toEqual(runs[0]);
    expect(runs[1]!.verdict).toBe("verified");
    expect(runs[1]!.actions).toBe(1);
  }, 20000);

  it("classifies a Stop after the step was offered as unknown and fences further mutation", async () => {
    const source = await managedSource();
    let entered!: () => void, release!: () => void;
    const stepEntered = new Promise<void>((resolve) => { entered = resolve; });
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const slowStep: ManagedInProcessService = { serviceInstanceId: source.service.serviceInstanceId,
      async request(input) {
        if (input.body?.includes('"command":"step"')) { entered(); await gate; }
        return source.service.inProcess.request(input);
      } };
    let f: Awaited<ReturnType<typeof globalRuntimeFixture>> | undefined;
    try {
      const transport = await ManagedServicePolicyClient.attachGlobal(source.bindingPath, source.attachmentPath, source.target, RUN(3), slowStep);
      f = await globalRuntimeFixture({ transport });
      const ticking = f.runtime.tick();
      await stepEntered;
      const stopping = f.runtime.stop();
      const outcome = await ticking;
      release();
      await stopping;
      expect(outcome.type).toBe("unknown");
      expect(f.runtime.status()).toMatchObject({ tainted: true });
      expect(transport.controlStatus()).not.toBe("held");
      await expect(transport.step({ requestId: "again", expectedSnapshotId: "s", boundActionId: "b" }, {})).rejects.toThrow();
      const events = await kinds(f.evidence.directory);
      expect(events.filter((kind) => kind === "global_dispatch")).toHaveLength(1);
    } finally { release(); await f?.close(); await source.service.close(); }
  }, 20000);
});
