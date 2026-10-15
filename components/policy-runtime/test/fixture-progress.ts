import { randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";

interface FixtureProgress {
  (phase: string, counts?: Record<string, number>): void;
  begin(operation: string, counts?: Record<string, number>): () => void;
  flush(failed?: boolean): Promise<void>;
}

const MAX_ENTRIES = 1024, MAX_PENDING = 64, FLUSH_MS = 250;

/** Bounded phase observations distinguish a runner's whole-test timeout from
 * product quotas or an incomplete real file/child operation. Failure-only mode
 * buffers the same bounded snapshots; flush(true) reveals them without retries. */
export function fixtureProgress(fixture: string, retain = false,
  directory = new URL(`../../../python/.local/fixture-diagnostics/${fixture}-${randomUUID()}/`, import.meta.url),
  failureOnly = false): FixtureProgress {
  const started = performance.now(); let previous = started;
  let sequence = 0, operationSequence = 0, pendingWrites = 0, writeFailures = 0, pendingOverflow = 0;
  let observerTotal = 0, observerMax = 0;
  let writes = Promise.resolve();
  const pending = new Set<number>();
  const deferred: { entry_sequence: number }[] = [];
  let revealed = !failureOnly;
  const log = (value: unknown) => { try { console.info(JSON.stringify(value)); } catch { /* Observation only. */ } };
  const publish = (value: { entry_sequence: number }) => {
    if (retain) {
      // Freeze each entry before enqueueing. Diagnostic I/O never gates the
      // promise being observed; product evidence keeps its original durability.
      const file = new URL(`${value.entry_sequence}.json`, directory), bytes = JSON.stringify(value) + "\n";
      pendingWrites++;
      writes = writes.then(async () => {
        const writing = performance.now();
        try { await mkdir(directory, { recursive: true }); await writeFile(file, bytes, { flag: "wx" }); }
        catch { writeFailures++; }
        finally {
          const elapsed = performance.now() - writing;
          observerTotal += elapsed; observerMax = Math.max(observerMax, elapsed); pendingWrites--;
        }
      });
    }
    log(value);
  };
  const progress = ((phase: string, counts: Record<string, number> = {}) => {
    if (sequence >= MAX_ENTRIES) return;
    const current = performance.now();
    const numeric = Object.fromEntries(Object.entries(counts).filter(([, value]) => Number.isFinite(value)));
    const value = { fixture_diagnostic: fixture, phase, entry_sequence: ++sequence,
      elapsed_ms: Math.round(current - started), phase_ms: Math.round(current - previous), ...numeric,
      pending_operation_ids: [...pending], pending_operation_overflow: pendingOverflow, observer_pending_writes: pendingWrites,
      observer_write_failures: writeFailures, observer_total_ms: Math.round(observerTotal), observer_max_ms: Math.round(observerMax) };
    if (revealed) publish(value); else deferred.push(value);
    previous = current;
  }) as FixtureProgress;
  progress.begin = (operation, counts = {}) => {
    const id = ++operationSequence, entered = performance.now(), originalCounts = { ...counts };
    const tracked = pending.size < MAX_PENDING;
    if (tracked) pending.add(id); else pendingOverflow++;
    progress(`${operation}-begin`, { ...originalCounts, operation_id: id });
    let ended = false;
    return () => {
      if (ended) return; ended = true;
      if (tracked) pending.delete(id); else pendingOverflow--;
      progress(`${operation}-end`, { ...originalCounts, operation_id: id, operation_ms: Math.round(performance.now() - entered) });
    };
  };
  progress.flush = async (failed = false) => {
    if (!revealed) {
      if (!failed) return; // Successful default fixtures do no diagnostic I/O.
      revealed = true;
      for (const entry of deferred.splice(0)) publish(entry);
    }
    const flushing = performance.now(); let timer: ReturnType<typeof setTimeout> | undefined;
    try { await Promise.race([writes, new Promise<void>(resolve => { timer = setTimeout(resolve, FLUSH_MS); })]); }
    finally {
      clearTimeout(timer);
      log({ fixture_diagnostic: fixture, phase: "observer-flush", flush_ms: Math.round(performance.now() - flushing),
        observer_pending_writes: pendingWrites, observer_write_failures: writeFailures,
        observer_total_ms: Math.round(observerTotal), observer_max_ms: Math.round(observerMax),
        pending_operation_ids: [...pending], pending_operation_overflow: pendingOverflow,
        entry_limit_reached: Number(sequence === MAX_ENTRIES) });
    }
  };
  return progress;
}
