#!/usr/bin/env node
// E7 real-frame replay and microbenchmark (development tool, not shipped).
//
//   npm run build
//   node tools/frame-delta-replay.mjs --out <dir> [--rounds 10] <input-lines.jsonl.gz> ...
//
// Each input is one game's fast-env evidence `input-lines.jsonl.gz` recorded with link-1 lines
// (full observation in every line). Every game's views are framed again in order, exactly as the
// kernel frames them for link-2 (check interval 1: every frame is compared with JSON.stringify(view)
// and pyJsonSha256(view)), and the link-2 decision line is written next to the original one:
// <out>/<n>.link1.jsonl and <out>/<n>.link2.jsonl, one line each (python tests/test_p1_link_frames.py
// reads them with STPD_LINK2_REPLAY=<out>). It then measures, per decision, the CPU time of the link-1
// input (JSON.stringify(view) + pyJsonSha256(view)) against the link-2 frame (ObservationFrames.encode),
// alternating rounds, median of rounds 3..N.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { parseArgs } from "node:util";
import { join } from "node:path";

const { values, positionals } = parseArgs({ allowPositionals: true,
  options: { out: { type: "string" }, rounds: { type: "string", default: "10" } } });
if (!values.out || positionals.length === 0) {
  console.error("usage: frame-delta-replay.mjs --out <dir> [--rounds N] <input-lines.jsonl.gz> ...");
  process.exit(2);
}
const { ObservationFrames } = await import(new URL("../dist/fast-env/frame-delta.js", import.meta.url).href);
const { pyJsonSha256 } = await import(new URL("../dist/fast-env/json-bytes.js", import.meta.url).href);
const LINK2 = "sts2.policy-runtime/fast-env-link-2";

mkdirSync(values.out, { recursive: true });
const games = positionals.map(file => gunzipSync(readFileSync(file)).toString("utf8").split("\n").filter(Boolean)
  .map(record => JSON.parse(record).line));
let fullBytes = 0, frameBytes = 0, rows = 0, sent = 0, total = 0;
games.forEach((lines, n) => {
  const frames = new ObservationFrames({ checkInterval: 1, checkSeed: 1 });
  const link2 = lines.map(line => {
    const message = JSON.parse(line);
    const view = message.input.observation;
    const frame = frames.encode(view, message.row_id);
    if (frame.observationSha256 !== message.observation_sha256) throw new Error(`observation_sha256 differs at row ${message.row_id}`);
    const head = line.slice(0, line.indexOf(',"input":'));
    const out = `${head.replace(/^\{"schema":"[^"]+"/u, `{"schema":"${LINK2}"`)},"input":{"input_spec":${JSON.stringify(message.input.input_spec)},`
      + `"frame":${frame.frame}},"parts":${frame.parts}}`;
    fullBytes += Buffer.byteLength(line); frameBytes += Buffer.byteLength(out); rows += 1; sent += frame.sent; total += frame.total;
    return out;
  });
  writeFileSync(join(values.out, `${n}.link1.jsonl`), `${lines.join("\n")}\n`);
  writeFileSync(join(values.out, `${n}.link2.jsonl`), `${link2.join("\n")}\n`);
});
console.log(JSON.stringify({ games: games.length, rows, line_bytes_link1: Math.round(fullBytes / rows),
  line_bytes_link2: Math.round(frameBytes / rows), parts_sent_per_row: +(sent / rows).toFixed(2), parts_per_row: +(total / rows).toFixed(2) }));

const views = games.map(lines => lines.map(line => JSON.parse(line).input.observation));
const cpu = fn => { const start = process.cpuUsage(); fn(); const used = process.cpuUsage(start); return (used.user + used.system) / 1000; };
const link1 = () => { for (const game of views) for (const view of game) { JSON.stringify(view); pyJsonSha256(view); } };
const link2 = () => {
  for (const game of views) {
    const frames = new ObservationFrames({ checkInterval: 64 });
    game.forEach((view, index) => frames.encode(view, index + 1));
  }
};
const times = { link1: [], link2: [] };
const rounds = Number(values.rounds);
for (let round = 0; round < rounds; round += 1) {
  for (const [name, fn] of round % 2 ? [["link2", link2], ["link1", link1]] : [["link1", link1], ["link2", link2]])
    times[name].push(cpu(fn) / rows);
}
const median = list => { const sorted = list.slice(2).sort((a, b) => a - b); return sorted[Math.floor(sorted.length / 2)]; };
const a = median(times.link1), b = median(times.link2);
console.log(JSON.stringify({ rounds, link1_ms_per_row: +a.toFixed(4), link2_ms_per_row: +b.toFixed(4), ratio: +(b / a).toFixed(3) }));
