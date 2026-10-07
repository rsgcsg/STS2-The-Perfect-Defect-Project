import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { PublicMenuTeacher } from "../baseline-s0-teacher.mjs";
import { S0RawRecords, sha256 } from "../baseline-s0-records.mjs";
import { runS0, makeS0Manifest, confirmControlReleased, parseArguments } from "../baseline-s0-runner.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const base = JSON.parse(await readFile(path.join(root,
  "components/connector/sdk/typescript/test/fixtures/text-menu-v2-targeted-root.json"), "utf8"));
const action = (verb, kind = "system_navigation") => ({ action_id: `a-${verb}`, kind,
  verb, label: verb, subject_referent_id: null, arguments: [],
  effect_domain: kind === "native_input" ? "native_input" : "text_menu" });
function snapshot(cursor, actions, kind = "combat_turn") {
  const value = structuredClone(base);
  value.menu.cursor = cursor;
  value.interaction.kind = kind;
  value.menu_actions.actions = actions;
  value.menu_actions.materialized_count = value.menu_actions.total_count = actions.length;
  return value;
}
function input(value) {
  return { run_id: "test-run", bundle: { observation: value, reads: [] },
    continuity_token: "segment-1", candidate_count: value.menu_actions.actions.length,
    candidate_digest: sha256(JSON.stringify(value.menu_actions.actions.map(a => a.action_id))) };
}
function full(value, id = "capture-1", ordinal = 1) {
  const serializedSnapshot = JSON.stringify(value, null, 2); // Preserve original whitespace.
  return { context: { snapshot: value, game_continuity_id: "game-1" }, serializedSnapshot,
    capture: { capture_id: id, capture_ordinal: ordinal, source_snapshot_id: value.snapshot_id,
      sha256: sha256(serializedSnapshot), total_bytes: Buffer.byteLength(serializedSnapshot),
      session: value.session, game_continuity_id: "game-1" } };
}
async function temporary(t) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "baseline-s0-test-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  return directory;
}

test("teacher browses, returns, revisits and then plays only complete advertised choices", () => {
  const teacher = new PublicMenuTeacher();
  const frames = [
    snapshot("root", [action("select_card", "system_selection"), action("open_information")]),
    snapshot("information", [action("back"), action("open_card_tips")]),
    snapshot("card_tips", [action("back"), action("show_card_tips", "native_input")]),
    snapshot("card_tips", [action("back"), action("show_card_tips", "native_input")]),
    snapshot("information", [action("back"), action("open_card_tips")]),
    snapshot("root", [action("select_card", "system_selection"), action("open_information")]),
    snapshot("information", [action("back"), action("open_card_tips")]),
    snapshot("root", [action("select_card", "system_selection"), action("open_information")]),
    snapshot("card_targets", [action("cancel_selection", "system_selection"), action("select_target", "system_selection")]),
    snapshot("card_confirmation", [action("cancel_selection", "system_selection"), action("play", "native_input")])
  ];
  const selected = frames.map(value => {
    const result = teacher.decide(input(value));
    assert.equal(result.output.scores.length, value.menu_actions.actions.length);
    assert.equal(result.completion.snapshot_id, value.snapshot_id);
    return value.menu_actions.actions[result.output.selected_index].verb;
  });
  assert.deepEqual(selected, ["open_information", "open_card_tips", "show_card_tips", "back",
    "back", "open_information", "back", "select_card", "select_target", "play"]);
});

test("teacher progresses map and abstains on unfamiliar native operation", () => {
  const teacher = new PublicMenuTeacher({ browse: false });
  assert.equal(teacher.decide(input(snapshot("root", [action("travel", "native_input")], "map_navigation")))
    .output.selected_index, 0);
  assert.equal(teacher.decide(input(snapshot("root", [action("unfamiliar", "native_input")], "event_option")))
    .output.selected_index, null);
  const truncated = snapshot("root", [action("select", "native_input")]);
  truncated.menu_actions.status = "truncated";
  assert.throws(() => teacher.decide(input(truncated)), /complete_public_catalog/u);
});

test("capsules preserve exact bytes; capture is not an offer; latest full snapshot join is mandatory", async t => {
  const directory = await temporary(t);
  const records = await S0RawRecords.create(path.join(directory, "raw"), "test", { id: "teacher" });
  t.after(() => records.close());
  const value = full(base);
  await records.capture(value);
  assert.equal(records.offerCount, 0);
  assert.equal(await readFile(path.join(records.directory, records.latest.snapshot_path), "utf8"),
    value.serializedSnapshot);
  const offer = await records.offer(input(base));
  await records.policyResult(offer, input(base), new PublicMenuTeacher({ browse: false }).decide(input(base)));
  const next = structuredClone(base);
  next.observed_at = "2026-09-26T00:00:01Z"; // Same ID does not authorize stale byte association.
  await records.capture(full(next, "capture-2", 2));
  assert.throws(() => records.beginOffer(input(base)), /latest_exact_capsule/u);
  const wrongCatalog = input(next);
  wrongCatalog.candidate_digest = "f".repeat(64);
  assert.throws(() => records.beginOffer(wrongCatalog), /catalog_binding/u);
  await records.close();
  const rows = (await readFile(path.join(records.directory, "records.jsonl"), "utf8")).trim().split("\n").map(JSON.parse);
  assert.deepEqual(rows.map(row => row.type), ["capture", "policy_offer", "policy_result", "capture"]);
  assert.equal(rows[1].payload.capture_id, "capture-1");
  assert.equal(rows[1].payload.source_kind, "agent");
  assert.equal(rows[1].payload.I, false);
});

test("corrupt or over-budget raw capsules never become policy offers", async t => {
  const directory = await temporary(t);
  const records = await S0RawRecords.create(path.join(directory, "raw"), "test", {}, { maxCaptureBytes: 1 });
  t.after(() => records.close());
  const value = full(base);
  const bad = structuredClone(value);
  bad.serializedSnapshot += " ";
  await assert.rejects(records.capture(bad), /identity_mismatch/u);
  await assert.rejects(records.capture(value), /budget_exceeded/u);
  assert.throws(() => records.beginOffer(input(base)), /latest_exact_capsule/u);
});

const capabilities = {
  protocol_version: "1.0.0", input_profile: "text-menu-v2", snapshot_schema: base.schema,
  host: { runtime_instance_id: "runtime-1", host_kind: "test", version: "1",
    implementation: { source_revision: "source", artifact_sha256: "b".repeat(64), module_version_id: "mvid" } },
  game: { version: "fixture", commit: "commit", modset: { status: "exact", fingerprint: "mods", loaded_mod_ids: ["mod"] } },
  verbs: ["select_card", "end_turn"]
};

test("manifest binds actual host/game/modset and limits interaction support", () => {
  const artifact = { id: "a", path: "a", sha256: "c".repeat(64) };
  const adapter = { id: "a", version: "1", protocol: "sts2.policy-runtime/decision-only-ndjson-2", code_sha256: "d".repeat(64) };
  const manifest = makeS0Manifest(capabilities, artifact, adapter);
  assert.equal(manifest.requirements.environment.connector_artifact_sha256, "b".repeat(64));
  assert.deepEqual(manifest.support.game_versions, ["fixture"]);
  assert.ok(manifest.support.interaction_kinds.includes("combat_turn"));
  assert.ok(manifest.support.interaction_kinds.includes("map_navigation"));
  assert.equal(manifest.support.interaction_kinds.includes("unknown_kind"), false);
  assert.deepEqual(manifest.support.action_verbs, capabilities.verbs);
  assert.equal(manifest.claims.full_run, false);
});

test("control release must be freshly confirmed with exact runtime and null owner", async () => {
  const control = { schema: "sts2.player-environment/control-1", protocol_version: "1.0.0",
    runtime_instance_id: "runtime-1", controller: null };
  assert.equal((await confirmControlReleased("http://fixture", "runtime-1", async () => ({ ok: true,
    json: async () => control }))).confirmed, true);
  await assert.rejects(confirmControlReleased("http://fixture", "other", async () => ({ ok: true,
    json: async () => control })), /unconfirmed/u);
  await assert.rejects(confirmControlReleased("http://fixture", "runtime-1", async () => ({ ok: true,
    json: async () => ({ ...control, controller: { held: true } }) })), /unconfirmed/u);
});

function lifecycleDependencies(trace, { failStop = false, unknown = true } = {}) {
  class Client {
    async textMenuV2Capabilities() { return { data: capabilities }; }
    async getFullTextMenuV2() { return full(base); }
  }
  class Connector {
    constructor(client, options) { assert.equal(options.observationAcquisition, "sealed-text-menu-v2"); this.client = client; }
    async observeTextMenuContext() { return (await this.client.getFullTextMenuV2()).context; }
  }
  class Runtime {
    constructor(options) { this.options = options; trace.push("runtime-created"); }
    async tick() {
      trace.push("tick");
      const context = await this.options.connector.observeTextMenuContext();
      const request = { ...input(context.snapshot), manifest: this.options.manifest };
      await this.options.statefulPolicy(request);
      return { type: unknown ? "unknown" : "text_native_delivered", status: { tainted: unknown } };
    }
    status() { return { mode: "human", lifecycle: "running" }; }
    async stop() { trace.push("stop"); if (failStop) throw new Error("failed-stop"); return { lifecycle: "stopped" }; }
  }
  return {
    async startEpisode(options) {
      trace.push("start"); assert.equal(options.characterId, "DEFECT"); assert.equal(options.ascension, 0);
      return { identity: { endpoint: "http://fixture", host: capabilities.host },
        async releaseController() { trace.push("handoff"); return { confirmed: true }; },
        async close() { trace.push("episode-close"); } };
    },
    PlayerEnvironmentRestClient: Client, ConnectorPolicyClient: Connector, PolicyRuntime: Runtime,
    POLICY_RUNTIME_VERSION: "fixture", validatePolicyManifest: () => {},
    runtimeCodeDigest: async () => "e".repeat(64),
    AgentRunEvidence: { create: async () => ({ attestAdapter: async () => {}, finalize: async () => {} }) },
    async confirmControlReleased() { trace.push("control-confirm"); return { confirmed: true }; }
  };
}

test("owning episode is handed off, unknown stops without a second tick, Stop precedes close", async t => {
  const directory = await temporary(t), trace = [];
  const result = await runS0({ mode: "collect", evidenceRoot: directory, seed: "1", browse: false },
    lifecycleDependencies(trace));
  assert.deepEqual(trace, ["start", "handoff", "runtime-created", "tick", "stop", "control-confirm", "episode-close"]);
  assert.equal(result.termination, "unknown_delivery_or_successor");
  assert.equal(result.offers, 1);
  assert.equal(result.stop_confirmed, true);
});

test("Stop failure still closes owning episode and does not claim release", async t => {
  const directory = await temporary(t), trace = [];
  await assert.rejects(runS0({ mode: "collect", evidenceRoot: directory, seed: "1", browse: false },
    lifecycleDependencies(trace, { failStop: true })), error => {
      assert.equal(error.summary.stop_confirmed, false);
      assert.match(error.message, /failed-stop/u);
      return true;
    });
  assert.equal(trace.at(-1), "episode-close");
  assert.equal(trace.includes("control-confirm"), false);
});

test("finite CLI options reject duplicate flags and increased autonomy", async () => {
  assert.throws(() => parseArguments(["collect", "--seed", "1", "--seed", "2"]), /duplicate/u);
  await assert.rejects(runS0({ mode: "collect", maxCalls: 61 }), /finite_budget/u);
});
