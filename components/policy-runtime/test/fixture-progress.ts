import { randomUUID } from "node:crypto";
import { closeSync, fsyncSync, mkdirSync, openSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

/** Bounded phase observations distinguish a runner's whole-test timeout from
 * product quotas or an incomplete real file/child operation. No retries. */
export function fixtureProgress(fixture: string, retain = false): (phase: string, counts?: Record<string, number>) => void {
  const started = performance.now(); let previous = started;
  const directory = new URL(`../../../python/.local/fixture-diagnostics/${fixture}-${randomUUID()}/`, import.meta.url);
  let sequence = 0;
  return (phase, counts = {}) => {
    const current = performance.now();
    const value = { fixture_diagnostic: fixture, phase,
      elapsed_ms: Math.round(current - started), phase_ms: Math.round(current - previous), ...counts };
    if (retain) {
      // Entered milestones survive fixture cleanup. No payloads, environment,
      // manifest/config or child stdout/stderr are copied by this observer.
      try {
        mkdirSync(directory, { recursive: true });
        const file = new URL(`${++sequence}.json`, directory);
        const descriptor = openSync(fileURLToPath(file), "wx");
        try { writeFileSync(descriptor, JSON.stringify(value) + "\n"); fsyncSync(descriptor); }
        finally { closeSync(descriptor); }
      } catch { /* A diagnostic write cannot change the fixture outcome. */ }
    }
    try { console.info(JSON.stringify(value)); } catch { /* Preserve the original outcome. */ }
    previous = current;
  };
}
