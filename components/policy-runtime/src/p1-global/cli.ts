#!/usr/bin/env node
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { isAbsolute, join, resolve } from "node:path";
import { sha256Hex, canonicalJson } from "@rsgcsg/sts2-connector-client/p1-global";
import { runEpisode, type EpisodeRow } from "./episode.js";
import { convertRecord } from "./convert.js";
import { compareDecisions, comparePair } from "./compare.js";

/**
 * sts2-p1 run      --host managed|reference --location DIR --host-runtime DIR --agent "cmd args" --seeds S1,S2|--suite FILE --out DIR
 * sts2-p1 convert  --record FILE [--out FILE]
 * sts2-p1 compare  --left FILE --right FILE [--mode decision|semantic|exact]
 */
function option(args: string[], name: string, fallback: string | null = null): string | null {
  const index = args.indexOf(name);
  return index >= 0 && args[index + 1] !== undefined ? args[index + 1]! : fallback;
}
function required(args: string[], name: string): string {
  const value = option(args, name);
  if (value === null) throw new Error(`missing ${name}`);
  return value;
}
function abs(path: string): string { return isAbsolute(path) ? path : resolve(process.cwd(), path); }

function seedsFrom(args: string[]): { suite: string | null; seeds: string[] } {
  const suite = option(args, "--suite");
  if (suite) {
    const manifest = JSON.parse(readFileSync(abs(suite), "utf8")) as Record<string, any>;
    const seeds = (manifest.seeds as (string | { seed: string })[]).map((item) => typeof item === "string" ? item : item.seed);
    const limit = Number(option(args, "--limit", String(seeds.length)));
    return { suite: String(manifest.suite_id ?? manifest.id ?? suite), seeds: seeds.slice(0, limit) };
  }
  return { suite: null, seeds: required(args, "--seeds").split(",").filter(Boolean) };
}

function summarize(rows: EpisodeRow[]): Record<string, unknown> {
  const count = (fn: (row: EpisodeRow) => boolean) => rows.filter(fn).length;
  const censor: Record<string, number> = {}; const families: Record<string, number> = {}; const kinds: Record<string, number> = {};
  for (const row of rows) {
    if (row.censor_reason) censor[row.censor_reason] = (censor[row.censor_reason] ?? 0) + 1;
    for (const [k, v] of Object.entries(row.families)) families[k] = (families[k] ?? 0) + v;
    for (const [k, v] of Object.entries(row.choice_kinds)) kinds[k] = (kinds[k] ?? 0) + v;
  }
  const floors = rows.map((row) => row.max_floor ?? 0).sort((a, b) => a - b);
  return { planned: rows.length, started: count((r) => r.started), natural_terminal: count((r) => r.terminal_outcome !== null),
    wins: count((r) => r.terminal_outcome === "win"), losses: count((r) => r.terminal_outcome === "loss"),
    censored: count((r) => r.censor_reason !== null), censor_reasons: censor, unknown_delivery: count((r) => r.unknown_delivery),
    decisions: rows.reduce((s, r) => s + r.decisions, 0), agent_choices: rows.reduce((s, r) => s + r.agent_choices, 0),
    forced_steps: rows.reduce((s, r) => s + r.forced_steps, 0), fold_steps: rows.reduce((s, r) => s + r.fold_steps, 0),
    native_steps: rows.reduce((s, r) => s + r.native_steps, 0), unmapped_choices: rows.reduce((s, r) => s + r.unmapped_choices, 0),
    floors, median_floor: floors.length ? floors[Math.floor((floors.length - 1) / 2)] : null,
    max_act: Math.max(0, ...rows.map((r) => r.max_act ?? 0)), scene_families: families, choice_kinds: kinds,
    elapsed_seconds: rows.reduce((s, r) => s + r.elapsed_seconds, 0) };
}

async function run(args: string[]): Promise<void> {
  const out = abs(required(args, "--out")); mkdirSync(out, { recursive: true });
  const { suite, seeds } = seedsFrom(args);
  const hostKind = required(args, "--host") as "managed" | "reference";
  const agentCommand = required(args, "--agent").split(" ").filter(Boolean);
  const stop = { requested: false };
  process.on("SIGINT", () => { stop.requested = true; });
  process.on("SIGTERM", () => { stop.requested = true; });
  const rows: EpisodeRow[] = [];
  for (const [index, seed] of seeds.entries()) {
    if (stop.requested) break;
    const row = await runEpisode({
      seed, episodeId: `${hostKind}-${String(index + 1).padStart(4, "0")}-${seed}`, recordDir: join(out, "records"),
      host: { kind: hostKind, hostRuntimeRoot: abs(required(args, "--host-runtime")), location: abs(required(args, "--location")),
        character: option(args, "--character", "Defect")!,
        extraArgs: hostKind === "reference" ? ["--template", option(args, "--template", "defect-a0-s0")!, "--experimental-connector"] : [] },
      agent: { command: agentCommand[0]!, args: agentCommand.slice(1), replyTimeoutMs: Number(option(args, "--agent-timeout-ms", "60000")) },
      maxDecisions: Number(option(args, "--max-decisions", "4000")),
      maxEpisodeSeconds: Number(option(args, "--max-episode-seconds", "1800")),
      settleTimeoutMs: Number(option(args, "--settle-timeout-ms", "60000")), stop
    });
    rows.push(row);
    process.stdout.write(JSON.stringify({ episode: row.episode_id, outcome: row.terminal_outcome, censor: row.censor_reason,
      floor: row.max_floor, decisions: row.decisions, seconds: row.elapsed_seconds }) + "\n");
  }
  const report = { schema: "sts2.p1/suite-report-1", suite, host_kind: hostKind, seeds, agent: agentCommand,
    summary: summarize(rows), episodes: rows };
  writeFileSync(join(out, "report.json"), JSON.stringify(report, null, 2));
  process.stdout.write(JSON.stringify({ report: join(out, "report.json"), summary: report.summary }) + "\n");
}

function convert(args: string[]): void {
  const record = abs(required(args, "--record"));
  const report = convertRecord(record, sha256Hex(readFileSync(record)));
  const { converted, chosen: _chosen, origins: _origins, ...summary } = report;
  const outPath = option(args, "--out");
  if (outPath) writeFileSync(abs(outPath), JSON.stringify({ ...summary, converted_sha256: sha256Hex(canonicalJson(converted)) }, null, 2));
  process.stdout.write(JSON.stringify(summary) + "\n");
  if (report.verdict !== "identical") process.exitCode = 1;
}

function compare(args: string[]): void {
  const left = convertRecord(abs(required(args, "--left")), "");
  const right = convertRecord(abs(required(args, "--right")), "");
  const mode = option(args, "--mode", "decision");
  const pair = (side: typeof left) => side.converted.map((observation, index) => ({ observation, chosen: side.chosen[index] ?? null,
    origin: side.origins[index] ?? null }));
  const report = mode === "decision" ? compareDecisions(pair(left), pair(right))
    : comparePair(left.converted, right.converted, mode as "semantic" | "exact");
  process.stdout.write(JSON.stringify(report, null, 2) + "\n");
}

const [command, ...rest] = process.argv.slice(2);
const handlers: Record<string, (args: string[]) => void | Promise<void>> = { run, convert, compare };
const handler = command ? handlers[command] : undefined;
if (!handler) { process.stderr.write("usage: sts2-p1 run|convert|compare ...\n"); process.exit(2); }
await handler(rest);
