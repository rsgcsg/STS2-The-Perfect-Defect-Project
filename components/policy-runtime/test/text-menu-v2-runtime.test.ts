import { readFileSync } from "node:fs";
import { mkdtemp, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { PassThrough } from "node:stream";
import { describe, expect, it, vi } from "vitest";
import {
  decodeTextMenuV2ActionResult, decodeTextMenuV2Snapshot,
  type TextMenuV2Action, type TextMenuV2ActionResult,
  type TextMenuV2Capabilities, type TextMenuV2Snapshot
} from "@rsgcsg/sts2-connector-client";
import { PolicyRuntime } from "../src/runtime.js";
import { candidateOrderDigest } from "../src/digest.js";
import { serveStatefulPolicyPort } from "../src/policy-port.js";
import { type PolicyConnector, type PolicyManifest, validatePolicyManifest } from "../src/contracts.js";
import { ManagedServicePolicyClient } from "../src/managed.js";
// @ts-expect-error The integration test intentionally exercises the adjacent in-repo Host runtime implementation.
import { ManagedPlayerEnvironmentSession } from "../../host-runtime/src/managed-player-environment.mjs";
// @ts-expect-error The integration test intentionally exercises the adjacent in-repo Host runtime implementation.
import { startManagedHostService } from "../../host-runtime/src/managed-host-service.mjs";

const sdkFixture = (name: string): unknown => JSON.parse(readFileSync(
  new URL(`../../connector/sdk/typescript/test/fixtures/${name}.json`, import.meta.url), "utf8"));
const root = () => decodeTextMenuV2Snapshot(sdkFixture("text-menu-v2-targeted-root")).data;
const cardSelected = () => decodeTextMenuV2ActionResult(sdkFixture("text-menu-v2-targeted-select"), root()).data;

function manifest(): PolicyManifest {
  return {
    schema: "sts2.policy-runtime/policy-manifest-1", manifest_id: "text-v2-test",
    policy: { id: "test", version: "1", provider: "test", architecture: "test" },
    adapter: { id: "test", version: "1", protocol: "sts2.policy-runtime/decision-only-ndjson-2", code_sha256: "c".repeat(64) },
    artifact: { id: "test", path: "test", sha256: "a".repeat(64) },
    representation: { id: "text-v2", version: "2", input_schema: "sts2.player-environment/text-menu-snapshot-2" },
    requirements: { connector_protocol_version: "1.0.0", environment: { host_kind: "test", connector_version: "1",
      connector_source_revision: "source", connector_artifact_sha256: "b".repeat(64), connector_module_version_id: "mvid",
      modset_status: "exact", modset_fingerprint: "modset", loaded_mod_ids: ["fixture-mod"] },
      reads: [], whole_decision_admission: true, candidate_order_digest: "sha256-json-menu-action-id-order",
      score_count_matches_candidate_count: true, selected_index: true, successor_required: true },
    support: { game_versions: ["fixture-game"], game_commits: ["fixture-commit"], interaction_kinds: ["combat_turn"],
      action_verbs: ["select_card", "select_target", "cancel_selection", "play"] },
    adapter_config: {}, claims: { full_run: false, selector: false, catalog_filtered: false,
      creates_action_authority: false, creates_native_operands: false }
  };
}

function capabilities(): TextMenuV2Capabilities {
  return {
    protocol_version: "1.0.0", snapshot_schema: "sts2.player-environment/text-menu-snapshot-2",
    action_schema: "sts2.player-environment/action-1", receipt_schema: "sts2.player-environment/text-menu-action-result-2",
    control_schema: "sts2.player-environment/control-1", input_profile: "text-menu-v2", status: "ready",
    host: { id: "fixture", name: "fixture", version: "1", runtime_instance_id: "runtime-1", host_kind: "test",
      implementation: { source_revision: "source", module_version_id: "mvid", artifact_sha256: "b".repeat(64) } },
    game: { version: "fixture-game", commit: "fixture-commit", branch: null, main_assembly_hash: null,
      compatibility: { status: "exact", observation_allowed: true, detail: "fixture" },
      modset: { status: "exact", fingerprint: "modset", scope: "fixture", loaded_mod_ids: ["fixture-mod"], detail: "fixture" } },
    environment_fingerprint: "environment-1", verbs: ["select_card", "select_target", "cancel_selection", "play"],
    snapshot_bound: true, single_controller: true, execution_available: true,
    control: { recommended_renewal_ms: 1000 }, evidence_profiles: [], non_claims: []
  } as TextMenuV2Capabilities;
}

function targetSelected(previous: TextMenuV2Snapshot): TextMenuV2ActionResult {
  const target = previous.menu_actions.actions.find(action => action.verb === "select_target")!;
  const play: TextMenuV2Action = { action_id: "v2-play-card-C-target-E", kind: "native_input", verb: "play",
    label: "Play Strike on Jaw Worm", subject_referent_id: "card-C",
    arguments: [{ role: "target", referent_id: "enemy-E" }], effect_domain: "native_input" };
  const cancel = previous.menu_actions.actions.find(action => action.verb === "cancel_selection")!;
  const successor = decodeTextMenuV2Snapshot({ ...previous, snapshot_id: "text-confirm", sequence: previous.sequence + 1,
    menu: { ...previous.menu, cursor: "card_confirmation", revision: previous.menu.revision + 1,
      selection: [...previous.menu.selection, { role: "target", referent_id: "enemy-E" }] },
    menu_actions: { ...previous.menu_actions, actions: [play, cancel] } }).data;
  return decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
    schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
    request_id: "target-request", status: "applied", effect_domain: "text_menu", native_delivery: null,
    action: target, reason_code: null, detail: null, retry: "never", successor, attribution: null }, previous).data;
}

function fixture() {
  let current = root();
  let successor = current;
  const events: Array<{ kind: string; payload: Record<string, unknown> }> = [];
  const submit = vi.fn(async (input: Parameters<PolicyConnector["submit"]>[0]) => {
    expect(input.inputProfile).toBe("text-menu-v2");
    expect(input.previousSnapshot?.snapshot_id).toBe(current.snapshot_id);
    expect(input.expectedSnapshotId).toBe(current.snapshot_id);
    const action = current.menu_actions.actions.find(item => item.action_id === input.boundActionId)!;
    let result: TextMenuV2ActionResult;
    if (action.verb === "select_card") {
      const selected = cardSelected();
      result = { ...selected, request_id: input.requestId };
    } else if (action.verb === "select_target") {
      result = { ...targetSelected(current), request_id: input.requestId };
    } else {
      result = { protocol_version: "1.0.0", schema: "sts2.player-environment/text-menu-action-result-2",
        input_profile: "text-menu-v2", request_id: input.requestId, status: "applied", effect_domain: "native_input",
        native_delivery: "delivered", action, reason_code: null, detail: null, retry: "never", successor: null, attribution: null };
      successor = decodeTextMenuV2Snapshot({ ...root(), snapshot_id: "text-native-next", sequence: current.sequence + 1,
        menu: { ...root().menu, native_snapshot_id: "managed-source-21" } }).data;
    }
    result = decodeTextMenuV2ActionResult(result, current).data;
    if (result.successor) current = result.successor;
    return result;
  });
  const context = vi.fn(async (profile?: string) => {
    expect(profile).toBe("text-menu-v2");
    return { schema: "sts2.player-environment/text-menu-observation-context-2" as const,
      snapshot: current, game_continuity_id: "game-1" };
  });
  const connector: PolicyConnector = {
    capabilities: vi.fn(async () => capabilities()),
    observeBundle: vi.fn(async (_reads: readonly string[], profile?: string) => { expect(profile).toBe("text-menu-v2"); return { observation: successor, reads: [] as [] }; }),
    observeTextMenuContext: context, acquireController: vi.fn(async () => {}),
    releaseController: vi.fn(async () => {}), submit
  };
  return { connector, context, submit, events, current: () => current,
    setCurrent: (page: TextMenuV2Snapshot) => { current = page; },
    evidence: { append: async (kind: string, payload: Record<string, unknown>) => { events.push({ kind, payload }); } } as never };
}

function managedManifest(): PolicyManifest {
  const value = manifest();
  value.adapter.protocol = "sts2.policy-runtime/decision-only-ndjson-3";
  value.requirements = { environment: { kind: "managed_text_v2", text_protocol_version: "1.0.0",
    input_profile: "text-menu-v2" }, reads: [], whole_decision_admission: true,
    candidate_order_digest: "sha256-json-menu-action-id-order", score_count_matches_candidate_count: true,
    selected_index: true, successor_required: true };
  return value;
}

function managedCapabilities() {
  return { kind: "managed_text_v2" as const, protocol_version: "1.0.0", input_profile: "text-menu-v2" as const,
    snapshot_schema: "sts2.player-environment/text-menu-snapshot-2" as const,
    receipt_schema: "sts2.player-environment/text-menu-action-result-2" as const,
    interaction_kinds: ["combat_turn"], observed_terminal_kinds: ["game_over"],
    action_verbs: ["select_card", "select_target", "cancel_selection", "play"],
    execution_available: true, control_held: false, control_owned: false, tainted: false,
    environment: { kind: "managed_text_v2" as const, binding_sha256: "9".repeat(64),
      service_instance_id: "service-1", runtime_instance_id: "runtime-1",
      environment_fingerprint: "environment-1", game_continuity_id: "game-1",
      text_protocol_version: "1.0.0", input_profile: "text-menu-v2" as const,
      host_package_identity: { package: "@rsgcsg/sts2-host-runtime" as const, version: "1", source_revision: "b".repeat(40),
        component_tree_revision: "c".repeat(40), release_asset_sha256: "d".repeat(64), package_content_sha256: "e".repeat(64) },
      host_identity: { package_name: "@rsgcsg/sts2-host-runtime", version: "1",
        distribution_kind: "installed_package" as const, source_revision: null,
        component_tree_revision: null, source_digest_sha256: "f".repeat(64) },
      candidate_build: { upstream_revision: "upstream", source_patch_sha256: "1".repeat(64),
        artifact_sha256: "2".repeat(64), artifact_mvid: "mvid", original_sts2_sha256: "3".repeat(64),
        runtime_sts2_sha256: "4".repeat(64) },
      game_version: "fixture-game", game_commit: "fixture-commit", game_assembly_sha256: "4".repeat(64),
      episode_provenance: { verdict: "provenance_pass" as const, requested_seed: "seed", actual_seed: "seed", runtime_instance_id: "runtime-1" } }
  };
}

function nativeCombatDecision() {
  return { type: "decision", decision: "combat_play",
    context: { act: 1, act_index: 0, act_definition_id: "OVERGROWTH", act_name: "Overgrowth",
      floor: 2, total_floor: 2, ascension: 0, room_type: "Combat", bosses: [], modifiers: [] },
    encounter_type: "normal", turn_owner: "player", is_play_phase: true, round: 1,
    energy: 3, max_energy: 3, exhaust_pile_count: 0, orb_slots: 0, orbs: [], companions: [],
    player_statuses: [], hand: [{ index: 0, native_ref: "native-strike", id: "CARD.STRIKE",
      name: "Strike", can_play: true, target_type: "AnyEnemy", valid_target_refs: ["native-enemy"],
      type: "Attack", rarity: "Basic", cost: 1 }],
    enemies: [{ index: 0, native_ref: "native-enemy", id: "MONSTER.CULTIST", combat_id: 1,
      name: "Cultist", hp: 10, max_hp: 10, block: 0, statuses: [], intents: [] }],
    player: { name: "The Defect", character_id: "DEFECT", hp: 70, max_hp: 70, gold: 99,
      native_ref: "native-player", max_potion_slots: 3, relics: [], potions: [], deck: [] } };
}

function managedControlConfirmation(status: "held" | "released") {
  return { status, service_instance_id: "service-1", runtime_instance_id: "runtime-1",
    game_continuity_id: "game-1", control_epoch: "epoch-1" } as const;
}

async function cancelledManagedPreparation(phase: "capabilities" | "claim" | "observation", command: "human" | "stop") {
  const f = fixture();
  let enter!: () => void;
  let resume!: () => void;
  const entered = new Promise<void>((resolve) => { enter = resolve; });
  const gate = new Promise<void>((resolve) => { resume = resolve; });
  f.connector.capabilities = vi.fn(async () => {
    if (phase === "capabilities") { enter(); await gate; }
    return managedCapabilities();
  });
  f.connector.acquireController = vi.fn(async () => {
    if (phase === "claim") { enter(); await gate; }
    return managedControlConfirmation("held");
  });
  f.connector.releaseController = vi.fn(async () => managedControlConfirmation("released"));
  f.connector.observeTextMenuContext = vi.fn(async () => {
    if (phase === "observation") { enter(); await gate; }
    return { schema: "sts2.player-environment/text-menu-observation-context-2" as const,
      snapshot: root(), game_continuity_id: "game-1" };
  });
  const inference = vi.fn(async () => { throw new Error("cancelled preparation invoked policy"); });
  const runtime = new PolicyRuntime({ manifest: managedManifest(), connector: f.connector,
    managedBindingSha256: "9".repeat(64), mode: "auto", evidence: {
      append: async (kind: string, payload: Record<string, unknown>) => { f.events.push({ kind, payload }); },
      finalize: async () => {}
    } as never, runtimeIdentity: { version: "test", code_sha256: "8".repeat(64) },
    statefulPolicy: inference });
  const tick = runtime.tick();
  await entered;
  const recovery = command === "human" ? runtime.setMode("human") : runtime.stop();
  expect(inference).not.toHaveBeenCalled();
  expect(f.connector.acquireController).toHaveBeenCalledTimes(phase === "capabilities" ? 0 : 1);
  resume();
  expect(await tick).toMatchObject({ type: "not_admitted", reason: "runtime_recovery_epoch_mismatch" });
  await recovery;
  expect(inference).not.toHaveBeenCalled();
  expect(f.connector.observeTextMenuContext).toHaveBeenCalledTimes(phase === "observation" ? 1 : 0);
  expect(f.connector.releaseController).toHaveBeenCalledTimes(phase === "capabilities" ? 0 : 1);
  expect(runtime.status()).toMatchObject({ mode: "human", lifecycle: command === "stop" ? "stopped" : "running",
    controller: "released", tainted: false });
}

describe("explicit text-menu-v2 Runtime consumer", () => {
  it("claims the real Managed Host only for observation and releases before policy", async () => {
    const build = { upstream_revision: "upstream", source_patch_sha256: "1".repeat(64),
      artifact_sha256: "2".repeat(64), artifact_mvid: "candidate-mvid",
      original_sts2_sha256: "3".repeat(64), runtime_sts2_sha256: "4".repeat(64) };
    const hostPackage = { package: "@rsgcsg/sts2-host-runtime" as const, version: "1",
      source_revision: "b".repeat(40), component_tree_revision: "c".repeat(40),
      release_asset_sha256: "d".repeat(64), package_content_sha256: "e".repeat(64) };
    const hostIdentity = { product: "fixture", package_name: hostPackage.package, version: "1", distribution_kind: "git_checkout" as const,
      workspace_revision: "a".repeat(40), component_path: "components/host-runtime",
      source_revision: hostPackage.source_revision, component_tree_revision: hostPackage.component_tree_revision,
      source_worktree_status: "clean", workspace_worktree_status: "clean",
      source_digest_sha256: "f".repeat(64), source_file_count: 1 };
    const binding = { schema: "sts2.policy-runtime/managed-environment-binding-1" as const,
      profile_sha256: "a".repeat(64), input_profile: "text-menu-v2" as const,
      host_package_identity: hostPackage, candidate_build: build };
    let actualSeed = "";
    const process = {
      async request(request: { cmd: string; seed?: string }) {
        if (request.cmd === "start_run" || request.cmd === "reset_run") {
          actualSeed = request.seed ?? ""; return nativeCombatDecision();
        }
        if (request.cmd === "run_identity") return { type: "run_identity", active: true, seed: actualSeed };
        if (request.cmd === "action") return nativeCombatDecision();
        throw new Error(`unexpected native request ${request.cmd}`);
      },
      async stop() { return { code: 0 }; }
    };
    const session = new ManagedPlayerEnvironmentSession({ process,
      runtimeInstanceId: "managed-runtime-1", environmentFingerprint: "managed-environment-1", sequence: 1 });
    const started = { session, runtime: {
      manifest: { candidate_id: "test", status: "ready", upstream: { revision: build.upstream_revision },
        expected_build: { source_patch_sha256: build.source_patch_sha256,
          artifact_sha256: build.artifact_sha256, artifact_mvid: build.artifact_mvid } },
      exactGame: { version: "fixture-game", commit: "fixture-commit", sts2_dll_sha256: build.runtime_sts2_sha256 },
      process, build, runtimeIdentity: { type: "managed", process_id: 1, sts2_assembly_sha256: build.runtime_sts2_sha256 },
      adapterRuntimeInstanceId: "managed-runtime-1" }, environmentFingerprint: "managed-environment-1" };
    const service = await startManagedHostService(started, { hostIdentity });
    const directory = await realpath(resolve(await mkdtemp(join(tmpdir(), "policy-observation-lease-"))));
    try {
      const reset = await fetch(`${service.endpoint}/v1/admin/reset`, { method: "POST",
        headers: { authorization: `Bearer ${service.managerToken}`, "x-sts2-managed-service-id": service.serviceInstanceId,
          "content-type": "application/json" },
        body: JSON.stringify({ request_id: "reset-observation-lease", seed: "OBSLEASE",
          expected_runtime_instance_id: "managed-runtime-1", expected_game_continuity_id: null }) });
      expect(reset.status).toBe(200);
      const ready = await fetch(`${service.endpoint}/v1/ready`, {
        headers: { authorization: `Bearer ${service.clientToken}` } }).then(response => response.json());
      const bindingPath = join(directory, "binding.json");
      const attachmentPath = join(directory, "attachment.json");
      await writeFile(bindingPath, JSON.stringify(binding));
      await writeFile(attachmentPath, JSON.stringify({ schema: "sts2.host-runtime/managed-service-attachment-1",
        endpoint: service.endpoint, service_instance_id: service.serviceInstanceId, host_identity: hostIdentity,
        role: "client", token: service.clientToken }), { mode: 0o600 });
      const client = await ManagedServicePolicyClient.attach(bindingPath, attachmentPath, {
        serviceInstanceId: service.serviceInstanceId, runtimeInstanceId: ready.adapter_runtime_instance_id,
        gameContinuityId: ready.episode.game_continuity_id
      }, "run-00000000-0000-4000-8000-000000000001");
      const order: string[] = [];
      const acquire = client.acquireController.bind(client);
      const observe = client.observeTextMenuContext.bind(client);
      const release = client.releaseController.bind(client);
      const submit = client.submit.bind(client);
      client.acquireController = async () => { order.push("claim"); return acquire(); };
      client.observeTextMenuContext = async profile => { order.push("observe"); return observe(profile); };
      client.releaseController = async () => { order.push("release"); return release(); };
      client.submit = async input => { order.push("submit"); return submit(input); };
      const inference = vi.fn(async (input: Parameters<NonNullable<ConstructorParameters<typeof PolicyRuntime>[0]["statefulPolicy"]>>[0]) => {
        order.push("policy");
        const actions = input.bundle.observation.schema === "sts2.player-environment/snapshot-1" ? []
          : input.bundle.observation.menu_actions.actions;
        const selected = actions.findIndex(action => action.verb === "select_card" || action.verb === "select_target");
        return { output: { candidate_digest: input.candidate_digest,
          scores: actions.map((_action, index) => -index), selected_index: selected },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence,
            previous_interaction_request_id: input.previous_interaction?.request_id ?? null } };
      });
      const hostManifest = managedManifest();
      hostManifest.support.action_verbs.push("end_turn", "back");
      const runtime = new PolicyRuntime({ manifest: hostManifest, connector: client,
        managedBindingSha256: client.bindingSha256, mode: "auto", runtimeIdentity: { version: "test", code_sha256: "8".repeat(64) },
        evidence: { append: async () => {}, finalize: async () => {} } as never,
        statefulPolicy: inference });
      const firstResult = await runtime.tick();
      expect(firstResult.type).toBe("navigated");
      expect(order).toEqual(["claim", "observe", "release", "policy", "claim", "submit"]);
      expect(service.driver.status().control_held).toBe(true);
      const beforeSecondTick = order.length;
      const continued = await runtime.tick();
      expect(continued.type).toBe("navigated");
      expect(order.slice(beforeSecondTick)).toEqual(["observe", "policy", "submit"]);
      expect(order.filter(item => item === "claim")).toHaveLength(2);
      expect(order.filter(item => item === "release")).toHaveLength(1);
      expect(inference).toHaveBeenCalledTimes(2);
      expect(service.driver.status().control_held).toBe(true);
    } finally {
      await service.close();
      await rm(directory, { recursive: true, force: true });
    }
  }, 15_000);

  it.each(["capabilities", "claim", "observation"] as const)("does not start Managed inference after Human cancels pending %s", async phase => {
    await cancelledManagedPreparation(phase, "human");
  });

  it.each(["capabilities", "claim", "observation"] as const)("does not start Managed inference after Stop cancels pending %s", async phase => {
    await cancelledManagedPreparation(phase, "stop");
  });

  it("keeps a successful but malformed Managed claim unknown without a second claim or release", async () => {
    const m = managedManifest();
    const f = fixture();
    const claim = vi.fn(async () => ({ status: "held" as const, service_instance_id: "service-1",
      runtime_instance_id: "runtime-1", game_continuity_id: "wrong-game", control_epoch: "epoch-1" }));
    const release = vi.fn(async () => undefined);
    f.connector.capabilities = vi.fn(async () => managedCapabilities());
    f.connector.acquireController = claim;
    f.connector.releaseController = release;
    const runtime = new PolicyRuntime({ manifest: m, connector: f.connector, managedBindingSha256: "9".repeat(64),
      mode: "auto", evidence: { append: async (kind: string, payload: Record<string, unknown>) => {
        f.events.push({ kind, payload }); }, finalize: async () => {} } as never,
      runtimeIdentity: { version: "test", code_sha256: "8".repeat(64) },
      statefulPolicy: async (input, _signal, onOffer) => {
        onOffer(); return { output: { candidate_digest: input.candidate_digest,
          scores: input.bundle.observation.schema === "sts2.player-environment/snapshot-1" ? []
            : input.bundle.observation.menu_actions.actions.map((_action, index) => -index), selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence,
            previous_interaction_request_id: input.previous_interaction?.request_id ?? null } };
      } });
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(runtime.status()).toMatchObject({ mode: "human", controller: "unknown", tainted: true });
    expect(claim).toHaveBeenCalledTimes(1);
    expect(release).not.toHaveBeenCalled();
    await runtime.stop();
    expect(runtime.status()).toMatchObject({ lifecycle: "stopped", controller: "unknown", tainted: true });
    expect(release).not.toHaveBeenCalled();
  });

  it("releases a transient observation claim after a known read failure", async () => {
    const f = fixture();
    const order: string[] = [];
    f.connector.capabilities = vi.fn(async () => managedCapabilities());
    f.connector.acquireController = vi.fn(async () => { order.push("claim"); return managedControlConfirmation("held"); });
    f.connector.observeTextMenuContext = vi.fn(async () => { order.push("observe"); throw new Error("known_read_rejection"); });
    f.connector.releaseController = vi.fn(async () => { order.push("release"); return managedControlConfirmation("released"); });
    const runtime = new PolicyRuntime({ manifest: managedManifest(), connector: f.connector,
      managedBindingSha256: "9".repeat(64), mode: "one_step",
      evidence: f.evidence, runtimeIdentity: { version: "test", code_sha256: "8".repeat(64) },
      statefulPolicy: async () => { throw new Error("policy must not run"); } });
    expect(await runtime.tick()).toMatchObject({ type: "not_admitted", reason: "observation_failed" });
    expect(runtime.status().errors).toContain("observation_failed:known_read_rejection");
    expect(order).toEqual(["claim", "observe", "release"]);
    expect(runtime.status()).toMatchObject({ mode: "human", controller: "released", tainted: false });
  });

  it("marks a transient observation release failure unknown without retry", async () => {
    const f = fixture();
    f.connector.capabilities = vi.fn(async () => managedCapabilities());
    f.connector.acquireController = vi.fn(async () => managedControlConfirmation("held"));
    f.connector.observeTextMenuContext = vi.fn(async () => { throw new Error("read_failed"); });
    f.connector.releaseController = vi.fn(async () => { throw new Error("release_ack_lost"); });
    const runtime = new PolicyRuntime({ manifest: managedManifest(), connector: f.connector,
      managedBindingSha256: "9".repeat(64), mode: "one_step", evidence: f.evidence,
      runtimeIdentity: { version: "test", code_sha256: "8".repeat(64) },
      statefulPolicy: async () => { throw new Error("policy must not run"); } });
    expect(await runtime.tick()).toMatchObject({ type: "not_admitted", reason: expect.stringContaining("observation_failed:read_failed") });
    expect(runtime.status()).toMatchObject({ mode: "human", controller: "unknown", tainted: true });
    expect(f.connector.acquireController).toHaveBeenCalledTimes(1);
    expect(f.connector.releaseController).toHaveBeenCalledTimes(1);
    await expect(runtime.stop()).rejects.toThrow("controller_release_unconfirmed");
    expect(f.connector.releaseController).toHaveBeenCalledTimes(1);
  });
  it("keeps old manifests and port-2 v1 valid but rejects v2 on port-1", () => {
    expect(validatePolicyManifest(manifest()).representation.input_schema).toBe("sts2.player-environment/text-menu-snapshot-2");
    const legacy = manifest(); legacy.representation.input_schema = "sts2.player-environment/text-menu-snapshot-1";
    expect(validatePolicyManifest(legacy).adapter.protocol).toBe("sts2.policy-runtime/decision-only-ndjson-2");
    const wrongPort = manifest(); wrongPort.adapter.protocol = "sts2.policy-runtime/decision-only-ndjson-1";
    expect(() => validatePolicyManifest(wrongPort)).toThrow(/requires stateful policy port/u);
  });

  it("scores complete current catalogs through select card, select target, then one native leaf", async () => {
    const f = fixture();
    const offered: Array<{ ids: string[]; count: number; snapshot: string; reads: number; token: string }> = [];
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto", evidence: f.evidence,
      runId: "v2-flow", runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      successorPoll: { maxAttempts: 1, baseBackoffMs: 0 }, autoBudget: { maxSubmissions: 3, maxPolicyCalls: 3, deadlineMs: 60_000 },
      statefulPolicy: async (input, _signal, onOffer) => {
        onOffer();
        const ids = input.bundle.observation.schema === "sts2.player-environment/snapshot-1" ? []
          : input.bundle.observation.menu_actions.actions.map(action => action.action_id);
        offered.push({ ids, count: input.candidate_count, snapshot: input.bundle.observation.snapshot_id,
          reads: input.bundle.reads.length, token: input.continuity_token });
        return { output: { candidate_digest: input.candidate_digest, scores: ids.map((_id, i) => -i), selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence } };
      } });
    expect((await runtime.tick()).type).toBe("navigated");
    expect((await runtime.tick()).type).toBe("navigated");
    const third = await runtime.tick();
    expect(third.type).toBe("text_native_delivered");
    expect(offered.map(item => item.ids)).toEqual([
      ["v2-select-card-C"], ["v2-select-target-E", "v2-cancel-card-C"],
      ["v2-play-card-C-target-E", "v2-cancel-card-C"]
    ]);
    expect(offered.every(item => item.ids.length === item.count && item.reads === 0)).toBe(true);
    expect(f.events.filter(event => event.kind === "decision").map(event =>
      (event.payload.decision as { candidate_digest: string }).candidate_digest)).toEqual(
      offered.map(item => candidateOrderDigest(item.ids)));
    expect(new Set(offered.map(item => item.token)).size).toBe(1);
    expect(f.submit).toHaveBeenCalledTimes(3);
    expect(f.events.filter(event => event.kind === "menu_navigation")).toHaveLength(2);
    expect(f.events.filter(event => event.kind === "text_native_delivery")).toHaveLength(1);
    expect(f.events.filter(event => event.kind === "text_decision_input")
      .every(event => Object.keys(event.payload).sort().join(",") === "decision_id,snapshot")).toBe(true);
    expect(f.events.filter(event => event.kind === "text_observed_successor")).toHaveLength(1);
    expect(f.events.filter(event => event.kind === "text_menu_dispatch_attempt").map(event => [
      event.payload.native_submissions_used, event.payload.menu_navigations_used
    ])).toEqual([[0, 1], [0, 2], [1, 2]]);
    expect(runtime.status()).toMatchObject({ mode: "auto", tainted: false, autonomy_budget: { submissions_used: 3, policy_calls_used: 3 } });
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(runtime.status().mode).toBe("human");
  });

  it("keeps Human passive and Shadow score-only", async () => {
    const f = fixture();
    const score = vi.fn(async (input: Parameters<NonNullable<ConstructorParameters<typeof PolicyRuntime>[0]["statefulPolicy"]>>[0], _signal: AbortSignal, onOffer: () => void) => {
      onOffer();
      return { output: { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 },
        completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
          sequence: input.bundle.observation.sequence } };
    });
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, statefulPolicy: score });
    expect((await runtime.tick()).type).toBe("human");
    expect(f.context).not.toHaveBeenCalled();
    await runtime.setMode("shadow");
    expect((await runtime.tick()).type).toBe("shadow");
    expect(score).toHaveBeenCalledTimes(1);
    expect(f.submit).not.toHaveBeenCalled();
  });

  it("retains the exact unknown native action, taints, and never revives it", async () => {
    const f = fixture();
    const selected = cardSelected().successor!;
    const confirmation = targetSelected(selected).successor!;
    f.setCurrent(confirmation);
    f.submit.mockImplementationOnce(async input => {
      const action = confirmation.menu_actions.actions[0]!;
      return decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
        schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
        request_id: input.requestId, status: "unknown", effect_domain: "native_input", native_delivery: "unknown",
        action, reason_code: "native_delivery_unknown", detail: null, retry: "never", successor: null, attribution: null }, confirmation).data;
    });
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto", evidence: f.evidence,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) }, statefulPolicy: async (input, _signal, onOffer) => {
        onOffer(); return { output: { candidate_digest: input.candidate_digest, scores: [1, 0], selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence } };
      } });
    const result = await runtime.tick();
    expect(result.type).toBe("unknown");
    if (result.type === "unknown") expect(result.receipt).toMatchObject({ action: confirmation.menu_actions.actions[0], retry: "never" });
    expect(runtime.status()).toMatchObject({ mode: "human", tainted: true });
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(f.submit).toHaveBeenCalledTimes(1);
    expect(f.events.some(event => event.kind === "text_native_unknown")).toBe(true);
  });

  it("reobserves a stale v2 intent and makes a fresh decision with new IDs", async () => {
    const f = fixture();
    const confirmation = targetSelected(cardSelected().successor!).successor!;
    f.setCurrent(confirmation);
    const fresh = decodeTextMenuV2Snapshot({ ...root(), snapshot_id: "text-fresh-root",
      sequence: confirmation.sequence + 1, menu: { ...root().menu, native_snapshot_id: "native-fresh" } }).data;
    f.submit.mockImplementationOnce(async input => {
      f.setCurrent(fresh);
      return decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
        schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
        request_id: input.requestId, status: "not_applied", effect_domain: null, native_delivery: null,
        action: null, reason_code: "stale_snapshot", detail: null,
        retry: "reobserve", successor: fresh, attribution: null }, confirmation).data;
    });
    f.submit.mockImplementationOnce(async input => {
      const selected = cardSelected();
      const next = decodeTextMenuV2Snapshot({ ...selected.successor!, snapshot_id: "text-fresh-card",
        sequence: fresh.sequence + 1, menu: { ...selected.successor!.menu,
          native_snapshot_id: fresh.menu.native_snapshot_id } }).data;
      f.setCurrent(next);
      return decodeTextMenuV2ActionResult({ ...selected, request_id: input.requestId,
        successor: next }, fresh).data;
    });
    const offered: string[] = [];
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto", evidence: f.evidence,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      statefulPolicy: async (input, _signal, onOffer) => {
        onOffer(); offered.push(input.bundle.observation.snapshot_id);
        return { output: { candidate_digest: input.candidate_digest,
          scores: input.bundle.observation.schema === "sts2.player-environment/snapshot-1" ? []
            : input.bundle.observation.menu_actions.actions.map((_action, i) => -i), selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence } };
      } });
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect(runtime.status()).toMatchObject({ mode: "auto", tainted: false });
    expect(f.connector.releaseController).toHaveBeenCalledTimes(1);
    expect((await runtime.tick()).type).toBe("navigated");
    expect(offered).toEqual([confirmation.snapshot_id, fresh.snapshot_id]);
    expect(f.submit).toHaveBeenCalledTimes(2);
    expect(f.submit.mock.calls[0]![0].requestId).not.toBe(f.submit.mock.calls[1]![0].requestId);
    expect(f.submit.mock.calls[1]![0].expectedSnapshotId).toBe(fresh.snapshot_id);
    expect(f.events.filter(event => event.kind === "text_menu_not_applied")).toHaveLength(1);
    expect(f.events.filter(event => event.kind === "menu_navigation")).toHaveLength(1);
  });

  it("hands off after three consecutive v2 stale submissions", async () => {
    const f = fixture();
    let ordinal = 0;
    f.submit.mockImplementation(async input => {
      ordinal += 1;
      const next = decodeTextMenuV2Snapshot({ ...root(), snapshot_id: `text-stale-${ordinal}`,
        sequence: root().sequence + ordinal, menu: { ...root().menu,
          native_snapshot_id: `native-stale-${ordinal}` },
        menu_actions: { ...root().menu_actions, actions: root().menu_actions.actions.map(action =>
          ({ ...action, action_id: `v2-select-card-${ordinal}` })) } }).data;
      f.setCurrent(next);
      return decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
        schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
        request_id: input.requestId, status: "not_applied", effect_domain: null,
        native_delivery: null, action: null, reason_code: "stale_snapshot", detail: null,
        retry: "reobserve", successor: next, attribution: null }).data;
    });
    const policy = vi.fn(async (input: Parameters<NonNullable<ConstructorParameters<typeof PolicyRuntime>[0]["statefulPolicy"]>>[0],
      _signal: AbortSignal, onOffer: () => void) => {
      onOffer(); return { output: { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 },
        completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
          sequence: input.bundle.observation.sequence } };
    });
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto", evidence: f.evidence,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) }, statefulPolicy: policy });
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect(runtime.status().mode).toBe("auto");
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect(runtime.status().mode).toBe("auto");
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect(runtime.status()).toMatchObject({ mode: "human", tainted: false });
    expect(f.submit).toHaveBeenCalledTimes(3);
    expect(policy).toHaveBeenCalledTimes(3);
    expect(f.connector.releaseController).toHaveBeenCalledTimes(3);
    expect((await runtime.tick()).type).toBe("human");
    expect(f.submit).toHaveBeenCalledTimes(3);
  });

  it("resets the consecutive stale count after an applied text selection", async () => {
    const f = fixture();
    let submitted = 0;
    f.submit.mockImplementation(async input => {
      submitted += 1;
      const previous = f.current();
      if (submitted === 2) {
        const selected = cardSelected();
        const next = decodeTextMenuV2Snapshot({ ...selected.successor!, snapshot_id: "text-reset-selected",
          sequence: previous.sequence + 1, menu: { ...selected.successor!.menu,
            native_snapshot_id: previous.menu.native_snapshot_id } }).data;
        f.setCurrent(next);
        return decodeTextMenuV2ActionResult({ ...selected, request_id: input.requestId,
          successor: next }, previous).data;
      }
      const next = decodeTextMenuV2Snapshot({ ...root(), snapshot_id: `text-reset-${submitted}`,
        sequence: previous.sequence + 1, menu: { ...root().menu,
          native_snapshot_id: `native-reset-${submitted}` } }).data;
      f.setCurrent(next);
      return decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
        schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
        request_id: input.requestId, status: "not_applied", effect_domain: null,
        native_delivery: null, action: null, reason_code: "stale_snapshot", detail: null,
        retry: "reobserve", successor: next, attribution: null }, previous).data;
    });
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto", evidence: f.evidence,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      statefulPolicy: async (input, _signal, onOffer) => {
        onOffer(); const count = input.bundle.observation.schema === "sts2.player-environment/snapshot-1" ? 0
          : input.bundle.observation.menu_actions.actions.length;
        return { output: { candidate_digest: input.candidate_digest,
          scores: Array.from({ length: count }, (_item, i) => -i), selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence } };
      } });
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect((await runtime.tick()).type).toBe("navigated");
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect(runtime.status()).toMatchObject({ mode: "auto", tainted: false });
    expect(f.submit).toHaveBeenCalledTimes(4);
  });

  it.each([
    ["nonstale", "menu_action_not_current", "reobserve", null],
    ["no-reobserve", "stale_snapshot", "never", null],
    ["native-not-delivered", "stale_snapshot", "reobserve", "not_delivered"],
    ["no-evidence-writer", "stale_snapshot", "reobserve", null]
  ] as const)("hands %s back to Human", async (name, reason, retry, nativeDelivery) => {
    const f = fixture();
    const confirmation = targetSelected(cardSelected().successor!).successor!;
    f.setCurrent(confirmation);
    f.submit.mockImplementationOnce(async input => decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
      schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
      request_id: input.requestId, status: "not_applied", effect_domain: "native_input",
      native_delivery: nativeDelivery, action: confirmation.menu_actions.actions[0],
      reason_code: reason, detail: null, retry, successor: null, attribution: null }, confirmation).data);
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto",
      ...(name === "no-evidence-writer" ? {} : { evidence: f.evidence,
        runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) } }),
      statefulPolicy: async (input, _signal, onOffer) => {
        onOffer(); return { output: { candidate_digest: input.candidate_digest, scores: [1, 0], selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence } };
      } });
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect(runtime.status()).toMatchObject({ mode: "human", tainted: false });
    expect(f.submit).toHaveBeenCalledTimes(1);
  });

  it.each(["evidence", "release"] as const)("does not continue after %s failure", async failure => {
    const f = fixture();
    f.submit.mockImplementationOnce(async input => decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
      schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
      request_id: input.requestId, status: "not_applied", effect_domain: null, native_delivery: null,
      action: null, reason_code: "stale_snapshot", detail: null, retry: "reobserve",
      successor: root(), attribution: null }).data);
    if (failure === "release") vi.mocked(f.connector.releaseController).mockRejectedValueOnce(new Error("release failed"));
    const evidence = failure === "evidence" ? {
      append: async (kind: string) => { if (kind === "text_menu_not_applied") throw new Error("write failed"); }
    } as never : f.evidence;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto", evidence,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      statefulPolicy: async (input, _signal, onOffer) => {
        onOffer(); return { output: { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence } };
      } });
    if (failure === "release") await expect(runtime.tick()).rejects.toThrow(/release failed/u);
    else expect((await runtime.tick()).type).toBe("text_not_applied");
    expect(runtime.status()).toMatchObject({ mode: "human", tainted: true });
    expect((await runtime.tick()).type).toBe("not_admitted");
    expect(f.submit).toHaveBeenCalledTimes(1);
  });

  it("does not continue when Human cancels during the stale evidence append", async () => {
    const f = fixture();
    f.submit.mockImplementationOnce(async input => decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
      schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
      request_id: input.requestId, status: "not_applied", effect_domain: null, native_delivery: null,
      action: null, reason_code: "stale_snapshot", detail: null, retry: "reobserve",
      successor: root(), attribution: null }).data);
    let appendStarted!: () => void;
    let finishAppend!: () => void;
    const started = new Promise<void>(resolve => { appendStarted = resolve; });
    const appendGate = new Promise<void>(resolve => { finishAppend = resolve; });
    const evidence = { append: async (kind: string) => {
      if (kind === "text_menu_not_applied") { appendStarted(); await appendGate; }
    } } as never;
    const runtime = new PolicyRuntime({ manifest: manifest(), connector: f.connector, mode: "auto", evidence,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      statefulPolicy: async (input, _signal, onOffer) => {
        onOffer(); return { output: { candidate_digest: input.candidate_digest, scores: [1], selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence } };
      } });
    const pending = runtime.tick();
    await started;
    const human = runtime.setMode("human");
    finishAppend();
    expect((await pending).type).toBe("text_not_applied");
    await human;
    expect(runtime.status()).toMatchObject({ mode: "human", tainted: false });
    expect((await runtime.tick()).type).toBe("human");
    expect(f.submit).toHaveBeenCalledTimes(1);
  });

  it("offers a confirmed interaction once across a stale v2 decision", async () => {
    const f = fixture();
    const third = decodeTextMenuV2Snapshot({ ...root(), snapshot_id: "text-third",
      sequence: root().sequence + 3, menu: { ...root().menu, native_snapshot_id: "native-third" } }).data;
    let submitted = 0;
    f.submit.mockImplementation(async input => {
      submitted += 1;
      if (submitted === 1) {
        const selected = cardSelected(); f.setCurrent(selected.successor!);
        return decodeTextMenuV2ActionResult({ ...selected, request_id: input.requestId }, root()).data;
      }
      if (submitted === 2) {
        f.setCurrent(third);
        return decodeTextMenuV2ActionResult({ protocol_version: "1.0.0",
          schema: "sts2.player-environment/text-menu-action-result-2", input_profile: "text-menu-v2",
          request_id: input.requestId, status: "not_applied", effect_domain: null,
          native_delivery: null, action: null, reason_code: "stale_snapshot", detail: null,
          retry: "reobserve", successor: third, attribution: null }, cardSelected().successor!).data;
      }
      const selected = cardSelected();
      const next = decodeTextMenuV2Snapshot({ ...selected.successor!, snapshot_id: "text-third-selected",
        sequence: third.sequence + 1, menu: { ...selected.successor!.menu,
          native_snapshot_id: third.menu.native_snapshot_id } }).data;
      f.setCurrent(next);
      return decodeTextMenuV2ActionResult({ ...selected, request_id: input.requestId, successor: next }, third).data;
    });
    const m = manifest(); m.adapter.protocol = "sts2.policy-runtime/decision-only-ndjson-3";
    const feedback: Array<string | null> = [];
    const runtime = new PolicyRuntime({ manifest: m, connector: f.connector, mode: "auto", evidence: f.evidence,
      runtimeIdentity: { version: "test", code_sha256: "f".repeat(64) },
      statefulPolicy: async (input, _signal, onOffer) => {
        onOffer(); const prior = input.previous_interaction?.request_id ?? null;
        feedback.push(prior);
        const count = input.bundle.observation.schema === "sts2.player-environment/snapshot-1" ? 0
          : input.bundle.observation.menu_actions.actions.length;
        return { output: { candidate_digest: input.candidate_digest,
          scores: Array.from({ length: count }, (_item, i) => -i), selected_index: 0 },
          completion: { continuity_token: input.continuity_token, snapshot_id: input.bundle.observation.snapshot_id,
            sequence: input.bundle.observation.sequence, previous_interaction_request_id: prior } };
      } });
    expect((await runtime.tick()).type).toBe("navigated");
    const firstRequest = f.submit.mock.calls[0]![0].requestId;
    expect((await runtime.tick()).type).toBe("text_not_applied");
    expect((await runtime.tick()).type).toBe("navigated");
    expect(feedback).toEqual([null, firstRequest, null]);
    expect(f.events.filter(event => event.kind === "menu_navigation")).toHaveLength(2);
    expect(f.events.filter(event => event.kind === "text_menu_not_applied")).toHaveLength(1);
  });

  it("port-2 accepts a strict v2 bundle with exact count and digest", async () => {
    const input = new PassThrough();
    const output = new PassThrough();
    const policy = vi.fn(async (request: Parameters<NonNullable<ConstructorParameters<typeof PolicyRuntime>[0]["statefulPolicy"]>>[0]) => ({
      output: { candidate_digest: request.candidate_digest, scores: [1], selected_index: 0 },
      completion: { continuity_token: request.continuity_token, snapshot_id: request.bundle.observation.snapshot_id,
        sequence: request.bundle.observation.sequence }
    }));
    const serving = serveStatefulPolicyPort(policy, input, output);
    const page = root();
    const decisionInput = { run_id: "run", manifest: manifest(), bundle: { observation: page, reads: [] },
      candidate_digest: candidateOrderDigest(page.menu_actions.actions), candidate_count: 1, continuity_token: "token-1" };
    const reply = () => new Promise<Record<string, unknown>>(resolve => output.once("data", chunk => resolve(JSON.parse(String(chunk)))));
    const accepted = reply();
    input.write(JSON.stringify({ schema: "sts2.policy-runtime/policy-port-2", message_type: "decide",
      request_id: "exact", input: decisionInput }) + "\n");
    expect(await accepted).toMatchObject({ message_type: "decision", request_id: "exact",
      completion: { continuity_token: "token-1", snapshot_id: page.snapshot_id } });
    const rejected = reply();
    input.write(JSON.stringify({ schema: "sts2.policy-runtime/policy-port-2", message_type: "decide",
      request_id: "wrong-digest", input: { ...decisionInput, candidate_digest: "0".repeat(64) } }) + "\n");
    expect(await rejected).toMatchObject({ message_type: "error", request_id: "wrong-digest" });
    expect(policy).toHaveBeenCalledTimes(1);
    input.end();
    await serving;
  });
});
