#!/usr/bin/env node
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { isAbsolute, join, resolve } from "node:path";
import { sha256Hex, canonicalJson } from "@rsgcsg/sts2-connector-client/p1-global";
import { fileURLToPath } from "node:url";
import { replayGlobalAgentRun } from "../global-replay.js";
import { convertRecord } from "./convert.js";
import { compareDecisions, comparePair } from "./compare.js";
import { extractTrajectory } from "./trajectory.js";

/**
 * sts2-p1 run      --manifest FILE --adapter-command COMMAND [existing sts2-policy-runtime options]
 * sts2-p1 convert  --record FILE [--out FILE]
 * sts2-p1 compare  --left FILE --right FILE [--mode decision|semantic|exact]
 * sts2-p1 trajectory --run-directory DIR [--out FILE]   (Agent decision points of a GLOBAL run)
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

/** Compatibility frontend. Runtime/Workbench retain the only active lifecycle,
 * control, budget, NDJSON and evidence implementation. Historical records are
 * still converted by the legacy converter below without changing their scope. */
async function run(args: string[]): Promise<void> {
  required(args, "--manifest"); required(args, "--adapter-command");
  process.argv = [process.argv[0]!, fileURLToPath(new URL("../cli.js", import.meta.url)), ...args];
  await import("../cli.js");
}
async function replay(args: string[]): Promise<void> {
  process.stdout.write(JSON.stringify(await replayGlobalAgentRun(abs(required(args, "--run-directory")))) + "\n");
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

function trajectory(args: string[]): void {
  const value = extractTrajectory(abs(required(args, "--run-directory")));
  const out = option(args, "--out");
  if (out) writeFileSync(abs(out), JSON.stringify(value));
  process.stdout.write(JSON.stringify({ schema: value.schema, points: value.points.length, out }) + "\n");
}

const [command, ...rest] = process.argv.slice(2);
const handlers: Record<string, (args: string[]) => void | Promise<void>> = { run, convert, compare, replay, trajectory };
const handler = command ? handlers[command] : undefined;
if (!handler) { process.stderr.write("usage: sts2-p1 run|convert|compare|replay ...\n"); process.exit(2); }
await handler(rest);
