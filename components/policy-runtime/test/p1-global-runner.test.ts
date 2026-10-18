import { fileURLToPath } from "node:url";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { runEpisode } from "../src/p1-global/episode.js";
import { convertRecord } from "../src/p1-global/convert.js";
import { EpisodeRecorder, readRecord } from "../src/p1-global/recorder.js";
import { HostDriver } from "../src/p1-global/host-driver.js";

const sdkFixture = (name: string) => JSON.parse(readFileSync(new URL(
  `../../connector/sdk/typescript/test/fixtures/p1-global/${name}.json`, import.meta.url), "utf8")) as { snapshot: any; reads: any[] };
const fakeHostRoot = fileURLToPath(new URL("./fixtures/p1-global-fake-host/", import.meta.url));
const firstAgent = fileURLToPath(new URL("../examples/p1/first_agent.mjs", import.meta.url));

function scenario(steps: unknown[], observe: unknown[] = [], withMap = true) {
  // Synthetic complete map capture for protocol lifecycle checks; not native capture evidence.
  const combat = sdkFixture(withMap ? "synthetic-complete-combat" : "managed-combat_turn"); const over = sdkFixture("managed-game_over");
  const reads: Record<string, unknown> = {};
  for (const basis of [combat, over]) for (const read of basis.reads) if (read.response) reads[read.read_id] = read.response;
  const dir = mkdtempSync(join(tmpdir(), "p1-runner-"));
  const file = join(dir, "scenario.json");
  writeFileSync(file, JSON.stringify({ reset: combat.snapshot, reads, steps, observe: observe.length ? observe : [over.snapshot] }));
  return { dir, file, log: join(dir, "requests.jsonl"), combat, over };
}

async function run(s: ReturnType<typeof scenario>, agent: string[] = [process.execPath, firstAgent], stop = { requested: false }, maxDecisions = 50) {
  process.env.P1_FAKE_SCENARIO = s.file; process.env.P1_FAKE_LOG = s.log;
  return runEpisode({ seed: "STPDTEST", episodeId: "test-episode", recordDir: join(s.dir, "records"),
    host: { kind: "managed", hostRuntimeRoot: fakeHostRoot, location: s.dir, character: "Defect" },
    agent: { command: agent[0]!, args: agent.slice(1), replyTimeoutMs: 5000 },
    maxDecisions, maxEpisodeSeconds: 60, settleTimeoutMs: 2000, stop });
}
const steps = (s: ReturnType<typeof scenario>) => existsSync(s.log)
  ? readFileSync(s.log, "utf8").trim().split("\n").map((line) => JSON.parse(line)).filter((r) => r.command === "step") : [];

describe("P1 runner lifecycle against a scripted Host", () => {
  it.each([undefined, "normal", "fast", "instant"])("forwards the explicit Reference speed %s exactly once", async mode => {
    const root = mkdtempSync(join(tmpdir(), "p1-reference-argv-"));
    mkdirSync(join(root, "tools"));
    writeFileSync(join(root, "tools/reference-pe-driver.mjs"), `
      import readline from 'node:readline';
      console.log(JSON.stringify({type:'ready',argv:process.argv.slice(2)}));
      for await (const line of readline.createInterface({input:process.stdin})) {
        const request=JSON.parse(line);
        if(request.command==='close') { console.log(JSON.stringify({type:'close_result',request_id:request.request_id,exit:{code:0}})); process.exit(0); }
      }
    `);
    const recorder = new EpisodeRecorder(join(root, "record.jsonl"), {});
    const driver = new HostDriver({ kind: "reference", hostRuntimeRoot: root, location: root,
      character: "Defect", extraArgs: mode === undefined ? [] : ["--native-speed", mode] }, recorder);
    try {
      const ready = await driver.start(), argv = ready.argv as string[];
      expect(argv.filter(arg => arg === "--native-speed")).toHaveLength(mode === undefined ? 0 : 1);
      if (mode !== undefined) expect(argv[argv.indexOf("--native-speed") + 1]).toBe(mode);
    } finally { await driver.close(); recorder.close({}); }
  });
  it("refuses missing unadvertised required map before any gameplay step", async () => {
    const s = scenario([{ delivery: "delivered", successor: null }], [], false);
    const row = await run(s);
    expect(row.censor_reason).toBe("incomplete_observation");
    expect(row.incomplete_reasons).toContain("required:map");
    expect(steps(s)).toHaveLength(0);
  });

  it("ends naturally on the native terminal and the record converts byte-identically", async () => {
    const s = scenario([{ delivery: "delivered", successor: null }]);
    const row = await run(s);
    expect(row.terminal_outcome).toBe("loss");
    expect(row.censor_reason).toBeNull();
    const report = convertRecord(row.record!.path, row.record!.sha256);
    expect(report.verdict).toBe("identical");
    expect(report.observations).toBeGreaterThanOrEqual(2);
  });

  it("fences an unknown delivery: episode stops, nothing is re-sent", async () => {
    const s = scenario([{ delivery: "unknown", successor: null }, { delivery: "delivered" }]);
    const row = await run(s);
    expect(row.unknown_delivery).toBe(true);
    expect(row.censor_reason).toBe("unknown_delivery");
    expect(steps(s)).toHaveLength(1);
  });

  it("treats a missing step response as unknown and never retries it", async () => {
    const s = scenario([{ hang: true }]);
    process.env.P1_FAKE_SCENARIO = s.file; process.env.P1_FAKE_LOG = s.log;
    const row = await runEpisode({ seed: "STPDTEST", episodeId: "hang", recordDir: join(s.dir, "records"),
      host: { kind: "managed", hostRuntimeRoot: fakeHostRoot, location: s.dir, character: "Defect", requestTimeoutMs: 1500, stepTimeoutMs: 1500 },
      agent: { command: process.execPath, args: [firstAgent], replyTimeoutMs: 5000 },
      maxDecisions: 50, maxEpisodeSeconds: 60, settleTimeoutMs: 2000, stop: { requested: false } });
    expect(row.unknown_delivery).toBe(true);
    expect(steps(s)).toHaveLength(1);
  }, 20_000);

  it("on a known non-delivery observes again and lets the Agent choose anew", async () => {
    const s = scenario([{ delivery: "not_delivered", reason_code: "stale_state" }]);
    const row = await run(s);
    expect(row.unknown_delivery).toBe(false);
    expect(row.terminal_outcome).toBe("loss");
    const results = readRecord(row.record!.path).filter((line) => line.kind === "p1_result").map((line) => (line.result as any).status);
    expect(results[0]).toBe("not_started");
  });

  it("rejects an Agent choice outside the offered catalog without touching the Host", async () => {
    const s = scenario([{ delivery: "delivered" }]);
    const bad = join(s.dir, "bad-agent.mjs");
    writeFileSync(bad, `import readline from "node:readline";
for await (const l of readline.createInterface({ input: process.stdin })) { const m = JSON.parse(l);
  if (m.type === "hello") console.log(JSON.stringify({ type: "ready", protocol: m.protocol, agent_id: "bad" }));
  else if (m.type === "observation" && m.requires_choice) console.log(JSON.stringify({ type: "choose", occurrence: m.observation.envelope.occurrence, choice_id: "c9999" })); }`);
    const row = await run(s, [process.execPath, bad]);
    expect(row.censor_reason).toBe("agent_choice_not_in_catalog");
    expect(steps(s)).toHaveLength(0);
  });

  it("honours an external Stop before any mutation and still closes the Host and record", async () => {
    const s = scenario([{ delivery: "delivered" }]);
    const row = await run(s, undefined, { requested: true });
    expect(row.censor_reason).toBe("stop_requested");
    expect(steps(s)).toHaveLength(0);
    const lines = readRecord(row.record!.path);
    expect(lines.at(-1)!.kind).toBe("record_close");
    expect(lines.some((line) => line.kind === "host_close")).toBe(true);
  });

  it("records environment bytes before the Agent sees them (env line precedes the observation)", async () => {
    const s = scenario([{ delivery: "delivered", successor: null }]);
    const row = await run(s);
    const lines = readRecord(row.record!.path);
    const firstObservation = lines.find((line) => line.kind === "p1_observation")!;
    const firstOut = lines.find((line) => line.kind === "agent_message_out" && line.message_type === "observation")!;
    const basisSeq = (firstObservation.basis_refs as any).snapshot_seq as number;
    expect(basisSeq).toBeLessThan(firstObservation.seq);
    expect(firstObservation.seq).toBeLessThan(firstOut.seq);
  });
});


describe("direct P1 CLI shop continuation uses a sealed successor basis", () => {
  function shopScenario(drift: "none" | "owner" | "world") {
    const s = scenario([]);
    const inventory = structuredClone(s.combat.snapshot);
    inventory.reads = inventory.reads.filter((read: any) => read.kind !== "combat_piles");
    inventory.interaction.kind = "shop_inventory";
    inventory.interaction.stage = "ready";
    inventory.interaction.content_schema = "sts2.player-environment/surface/shop_inventory-1";
    inventory.interaction.content.context = { kind: "shop" };
    inventory.interaction.content.surface = { kind: "shop_inventory", shop_session_entity_id: "native-merchant-room",
      cards: [], relics: [], potions: [], card_removal: null, can_close: true };
    const action = { ...inventory.bound_actions.actions[0], bound_action_id: "close-inventory", verb: "close",
      subject_referent_id: null, arguments: [], label: "Close inventory" };
    inventory.bound_actions.actions = [action];
    inventory.bound_actions.total_count = inventory.bound_actions.materialized_count = 1;
    const room = structuredClone(inventory);
    room.snapshot_id += "-closed";
    room.interaction.kind = "shop_room";
    room.interaction.content_schema = "sts2.player-environment/surface/shop_room-1";
    room.interaction.content.surface = { kind: "shop_room", shop_session_entity_id: "native-merchant-room",
      can_open_inventory: true, can_proceed: true };
    room.bound_actions.actions = [
      { ...action, bound_action_id: "reopen", verb: "open", label: "Open inventory" },
      { ...action, bound_action_id: "proceed-same-room", verb: "activate", label: "Leave shop" }
    ];
    room.bound_actions.total_count = room.bound_actions.materialized_count = 2;
    if (drift === "owner") room.interaction.content.surface.shop_session_entity_id = "different-native-room";
    if (drift === "world") room.persistent.content.player.gold++;
    const original = JSON.parse(readFileSync(s.file, "utf8"));
    writeFileSync(s.file, JSON.stringify({ ...original, reset: inventory,
      steps: [{ delivery: "delivered", successor: room }, { delivery: "delivered", successor: s.over.snapshot }] }));
    return s;
  }

  it.each(["none", "owner", "world"] as const)("handles %s continuation through the real HostDriver interface", async drift => {
    const s = shopScenario(drift);
    const row = await run(s, undefined, undefined, 1);
    const lines = readRecord(row.record!.path);
    const result = lines.find(line => line.kind === "p1_result")!.result as any;
    if (drift === "none") {
      expect(steps(s).map(step => step.bound_action_id)).toEqual(["close-inventory", "proceed-same-room"]);
      expect(row.fold_steps).toBe(1);
      expect(result.status).toBe("completed");
      const requests = readFileSync(s.log, "utf8").trim().split("\n").map(line => JSON.parse(line));
      const close = requests.findIndex(request => request.bound_action_id === "close-inventory");
      const proceed = requests.findIndex(request => request.bound_action_id === "proceed-same-room");
      expect(requests.slice(close + 1, proceed).filter(request => request.command === "read").length).toBeGreaterThan(0);
    } else {
      expect(steps(s).map(step => step.bound_action_id)).toEqual(["close-inventory"]);
      expect(row.fold_steps).toBe(0);
      expect(result.status).toBe("interrupted_known");
    }
    expect(row.unknown_delivery).toBe(false);
  });
});
