// Measurement-only preload (node --import): every ELU_INTERVAL_MS appends the cumulative
// main-thread event-loop utilisation of this process to $ELU_OUT as JSON lines.
// ELU over [t0,t1] = d(active) / (d(active) + d(idle)). Not part of any product code.
import { appendFileSync } from "node:fs";
import { performance } from "node:perf_hooks";
const out = process.env.ELU_OUT;
if (out) {
  const every = Number(process.env.ELU_INTERVAL_MS ?? "2000");
  const sample = () => {
    const e = performance.eventLoopUtilization();
    try { appendFileSync(out, `${JSON.stringify({ t: Date.now(), idle_ms: e.idle, active_ms: e.active, cpu: process.cpuUsage() })}\n`); } catch {}
  };
  sample();
  setInterval(sample, every).unref();
}
