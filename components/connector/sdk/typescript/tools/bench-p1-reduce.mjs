#!/usr/bin/env node
// P1 reduce microbenchmark on real frames (development tool, not shipped).
//
//   npm run build
//   node tools/bench-p1-reduce.mjs --lines <native-lines.jsonl.gz> [--base <other SDK dist dir>] [--rounds 40]
//
// --lines is one recorded managed-native-compact-1 game (fast-env evidence `native-lines.jsonl.gz`).
// It is replayed through the workspace Host session exactly as the fast chain runs it (compact
// output with shareUnchanged, the E5 projection memo, the light frozen capture), and every captured
// P1 basis is reduced in order with the reduction state carried, as the kernel does.
// With --base (the `dist` of another build of this SDK, e.g. the merge base) both builds reduce the
// same bases: their Reductions must be byte-identical (exit 1 otherwise) and the per-reduction CPU
// time of the two is measured in alternating rounds in one process (median of rounds 6..N).
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { gunzipSync } from "node:zlib";
import { parseArgs } from "node:util";
import { fileURLToPath, pathToFileURL } from "node:url";

const { values } = parseArgs({ options: { lines: { type: "string" }, base: { type: "string" }, rounds: { type: "string", default: "40" } } });
if (!values.lines) { console.error("usage: bench-p1-reduce.mjs --lines <native-lines.jsonl.gz> [--base <dist>] [--rounds N]"); process.exit(2); }
const host = (name) => import(fileURLToPath(new URL(`../../../../host-runtime/src/${name}`, import.meta.url)));
const { RAW_LINE } = await host("json-line-process.mjs");
const { ManagedPlayerEnvironmentSession } = await host("managed-player-environment.mjs");
const { CompactStateReconstructor } = await host("managed-native-compact.mjs");
const { captureManagedPublicBasis } = await host("managed-public-basis.mjs");

const lines = gunzipSync(readFileSync(values.lines)).toString("utf8").split("\n").filter(Boolean);
const received = (line) => { const message = JSON.parse(line); Object.defineProperty(message, RAW_LINE, { value: line, enumerable: false }); return message; };
const first = new CompactStateReconstructor().accept(JSON.parse(lines[0]));
let next = 1;
// The recorded native answers every action with its next line; which action is taken does not change the frames.
const native = { async request(request) {
  if (request.cmd === "start_run" || request.cmd === "reset_run") return structuredClone(first);
  if (request.cmd === "configure_output") return received(lines[0]);
  if (request.cmd === "action") return received(lines[next++]);
  throw new Error(`unexpected native request ${request.cmd}`);
} };
const session = new ManagedPlayerEnvironmentSession({ process: native, runtimeInstanceId: "bench", environmentFingerprint: "bench",
  identityMode: "sequence", compactOutput: { sampleRate: 0, shareUnchanged: true }, projectionMemo: { checkInterval: 64 } });
let snapshot = await session.mount({ seed: "BENCH" });
const bases = [];
for (let ordinal = 1; ; ordinal++) {
  bases.push(captureManagedPublicBasis(session, { generationId: "g", gameContinuityId: "c", ordinal, fullCheck: false }).basis);
  const action = snapshot.bound_actions?.actions?.[0];
  if (next >= lines.length || action === undefined) break;
  const receipt = await session.submit({ requestId: `r${ordinal}`, expectedSnapshotId: snapshot.snapshot_id, boundActionId: action.bound_action_id });
  if (!receipt.successor) throw new Error(`replay stopped at line ${next}: ${receipt.reason_code}`);
  snapshot = receipt.successor;
}

const builds = { new: await import(new URL("../dist/p1Global/index.js", import.meta.url).href) };
if (values.base) builds.base = await import(pathToFileURL(`${values.base}/p1Global/index.js`).href);
const reductionsText = (sdk) => {
  let state = sdk.createSession("bench", "managed", "r1.9");
  return bases.map(basis => {
    const reduction = sdk.reduce(state, basis); state = reduction.state;
    return JSON.stringify(reduction);
  });
};
const texts = Object.fromEntries(Object.entries(builds).map(([name, sdk]) => [name, reductionsText(sdk)]));
const digest = (list) => createHash("sha256").update(list.join("\n")).digest("hex").slice(0, 16);
console.log(`frames ${bases.length}  reductions ${Object.entries(texts).map(([name, list]) => `${name}=${digest(list)}`).join(" ")}`);
if (texts.base) {
  const index = texts.new.findIndex((text, at) => text !== texts.base[at]);
  if (index >= 0) { console.error(`reduction ${index} differs between the two builds`); process.exit(1); }
}
const time = (sdk) => {
  let state = sdk.createSession("bench", "managed", "r1.9");
  const start = process.cpuUsage();
  for (const basis of bases) state = sdk.reduce(state, basis).state;
  const used = process.cpuUsage(start);
  return (used.user + used.system) / 1000 / bases.length;
};
const samples = Object.fromEntries(Object.keys(builds).map(name => [name, []]));
const names = Object.keys(builds);
for (let round = 0; round < Number(values.rounds); round++)
  for (const name of round % 2 ? names : [...names].reverse()) samples[name].push(time(builds[name]));
const median = (list) => { const kept = list.slice(5).sort((a, b) => a - b); return kept[kept.length >> 1]; };
console.log(names.map(name => `${name} ${median(samples[name]).toFixed(3)} ms/reduce`).join("  ")
  + (builds.base ? `  ratio ${(median(samples.new) / median(samples.base)).toFixed(3)}` : ""));
