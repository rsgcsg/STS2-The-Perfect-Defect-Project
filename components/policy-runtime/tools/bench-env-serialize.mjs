#!/usr/bin/env node
// R4/R5 (env35) microbenchmark and equivalence check on real frames (development tool, not shipped).
//
//   npm --prefix components/connector/sdk/typescript run build && npm --prefix components/policy-runtime run build
//   node components/policy-runtime/tools/bench-env-serialize.mjs <new repo root> <base repo root> <rounds> <native-lines.jsonl.gz>...
//
// Each --lines file is one recorded managed-native-compact-1 game (fast-env evidence `native-lines.jsonl.gz`), replayed
// through the Host session exactly as the fast chain runs it (compact output with shareUnchanged, the E5 projection memo,
// the light frozen capture). Every capture is taken by both trees' captureManagedPublicBasis on the same session state, and
// every model view is written by both trees' kernel steps (base: JSON.stringify + pyJsonSha256; new: jsonWithPySha256).
// Round 0 compares bytes, handle values/key order and digests (any difference counts in "diffs"); the CPU time of the two
// is measured interleaved in one process, median of rounds 1..N-1. The wall clock is fixed so both write the same bytes.
import { readFileSync } from "node:fs"; import { gunzipSync } from "node:zlib"; import { pathToFileURL } from "node:url";
// Fixed wall clock so both trees write the same observed_at/captured_at bytes.
const FIXED = Date.parse("2026-01-01T00:00:00Z"), RealDate = Date;
globalThis.Date = class extends RealDate { constructor(...a) { super(...(a.length ? a : [FIXED])); } static now() { return FIXED; } };
const [repoN, repoB, roundsArg, ...files] = process.argv.slice(2);
const impN = (p) => import(pathToFileURL(`${repoN}/${p}`).href), impB = (p) => import(pathToFileURL(`${repoB}/${p}`).href);
const { RAW_LINE } = await impN("components/host-runtime/src/json-line-process.mjs");
const { ManagedPlayerEnvironmentSession } = await impN("components/host-runtime/src/managed-player-environment.mjs");
const { CompactStateReconstructor } = await impN("components/host-runtime/src/managed-native-compact.mjs");
const capN = (await impN("components/host-runtime/src/managed-public-basis.mjs")).captureManagedPublicBasis;
const capB = (await impB("components/host-runtime/src/managed-public-basis.mjs")).captureManagedPublicBasis;
const sdk = await impN("components/connector/sdk/typescript/dist/p1Global/index.js");
const jbN = await impN("components/policy-runtime/dist/fast-env/json-bytes.js"), jbB = await impB("components/policy-runtime/dist/fast-env/json-bytes.js");
const { captureOverCapacity } = await impN("components/policy-runtime/dist/fast-env/kernel.js");
const MAX = 8 * 1024 * 1024;
const VOLATILE = new Set(["capture_id", "captured_at", "expires_at"]);
const handleText = (c) => JSON.stringify(Object.fromEntries(Object.keys(c).map(k => [k, VOLATILE.has(k) ? null : c[k]])));
let captures = 0, decisions = 0, diffs = 0;
const t = { capB: [], capN: [], kB: [], kN: [] };
const rounds = Number(roundsArg);
for (let round = 0; round < rounds; round++) {
  const tot = { capB: 0, capN: 0, kB: 0, kN: 0 }; let caps = 0, decs = 0;
  for (const file of files) {
    const lines = gunzipSync(readFileSync(file)).toString("utf8").split("\n").filter(Boolean);
    const received = (line) => { const m = JSON.parse(line); Object.defineProperty(m, RAW_LINE, { value: line, enumerable: false }); return m; };
    const first = new CompactStateReconstructor().accept(JSON.parse(lines[0])); let next = 1;
    const native = { async request(r) { if (r.cmd === "start_run" || r.cmd === "reset_run") return structuredClone(first); if (r.cmd === "configure_output") return received(lines[0]); if (r.cmd === "action") return received(lines[next++]); throw new Error(r.cmd); } };
    const session = new ManagedPlayerEnvironmentSession({ process: native, runtimeInstanceId: "bench", environmentFingerprint: "bench", identityMode: "sequence", compactOutput: { sampleRate: 0, shareUnchanged: true }, projectionMemo: { checkInterval: 64 } });
    let snapshot = await session.mount({ seed: "BENCH" }); let state = sdk.createSession("bench", "managed", "r1.9");
    for (let o = 1; ; o++) {
      const opts = { generationId: "g", gameContinuityId: "c", ordinal: o, fullCheck: false };
      let fb, fn, s;
      // the kernel's per-capture use: the capacity verdict (base reads total_bytes; new uses the bound)
      const runB = () => { s = performance.now(); fb = capB(session, opts); if (fb.capture.total_bytes > MAX) throw 0; tot.capB += performance.now() - s; };
      const runN = () => { s = performance.now(); fn = capN(session, opts); if (captureOverCapacity(fn, MAX)) throw 0; tot.capN += performance.now() - s; };
      if ((o + round) % 2) { runB(); runN(); } else { runN(); runB(); }
      caps++;
      if (round === 0) {
        if (fb.serializedObservation !== fn.serializedObservation || handleText(fb.capture) !== handleText(fn.capture)
            || JSON.stringify(Object.keys(fb.capture)) !== JSON.stringify(Object.keys(fn.capture))) diffs++;
      }
      const red = sdk.reduce(state, fn.basis); state = red.state;
      if (red.observation.status !== "terminal" && red.observation.catalog.length > 1) {
        decs++;
        const view = sdk.modelView(red.observation);
        let jb, hb, jn, hn;
        const kb = () => { s = performance.now(); jb = JSON.stringify(view); hb = jbB.pyJsonSha256(view); tot.kB += performance.now() - s; };
        const kn = () => { s = performance.now(); const p = jbN.jsonWithPySha256(view); jn = p.json; hn = p.sha256(); tot.kN += performance.now() - s; };
        if ((o + round) % 2) { kb(); kn(); } else { kn(); kb(); }
        if (round === 0 && (jb !== jn || hb !== hn)) diffs++;
      }
      const act = snapshot.bound_actions?.actions?.[0]; if (next >= lines.length || !act) break;
      const rec = await session.submit({ requestId: `r${o}`, expectedSnapshotId: snapshot.snapshot_id, boundActionId: act.bound_action_id }); if (!rec.successor) break; snapshot = rec.successor;
    }
  }
  if (round === 0) { captures = caps; decisions = decs; }
  for (const k of ["capB", "capN"]) t[k].push(tot[k] / caps); for (const k of ["kB", "kN"]) t[k].push(tot[k] / decs);
  console.error(`round ${round}: cap ${(tot.capB / caps).toFixed(3)} -> ${(tot.capN / caps).toFixed(3)} ms/capture; kernel obs ${(tot.kB / decs).toFixed(3)} -> ${(tot.kN / decs).toFixed(3)} ms/decision`);
}
const med = (a) => { const b = a.slice(1).sort((x, y) => x - y); return b[Math.floor(b.length / 2)]; };
console.log(JSON.stringify({ games: files.length, captures, decisions, diffs, capture_ms: { base: med(t.capB), new: med(t.capN) }, kernel_obs_ms: { base: med(t.kB), new: med(t.kN) } }));
process.exitCode = diffs ? 1 : 0;
