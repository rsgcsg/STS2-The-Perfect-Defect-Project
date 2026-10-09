import { describe, expect, it } from "vitest";
import { readFile, cp, mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { nativeRuntimeFixture } from "./native-runtime-fixtures.js";
import { NdjsonAgentSessionPort } from "../src/agent-session-port.js";
import { CHILD } from "./native-runtime-fixtures.js";

function gate() { let release!: () => void; const promise = new Promise<void>(resolve => { release = resolve; }); return { promise, release }; }
async function eventually(condition: () => boolean | Promise<boolean>, timeout = 1000): Promise<void> {
  const start = performance.now();
  while (!await condition()) {
    if (performance.now() - start > timeout) throw new Error("bounded synthetic condition not reached");
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}
function sourceKind(f: Awaited<ReturnType<typeof nativeRuntimeFixture>>, kind: string, availability = "available") {
  const route = f.source.route.bind(f.source);
  f.source.route = (url, body) => {
    const result = route(url, body);
    if (url.pathname.endsWith("/events")) {
      const batch = result.value as { events: { event: Record<string, unknown>; availability: string }[] };
      for (const entry of batch.events) {
        entry.event.kind = kind; entry.event.source_seam = "native_terminal_entry"; entry.event.source_phase = "summary_animation_started";
        entry.availability = availability;
        if (availability === "missing") { entry.event.capture_ref = null; entry.event.payload_reference = null; entry.event.missing_reason = "source_capture_missing"; }
      }
    }
    return result;
  };
}
async function archive(f: Awaited<ReturnType<typeof nativeRuntimeFixture>>, name: string) {
  if (!process.env.E3_NATIVE_ALTERNATIVE_OUTPUT) return;
  await f.runtime.stop();
  await mkdir(process.env.E3_NATIVE_ALTERNATIVE_OUTPUT, { recursive: true });
  await cp(f.evidence.directory, join(process.env.E3_NATIVE_ALTERNATIVE_OUTPUT, name), { recursive: true });
}
function emptyGlobalTail(f: Awaited<ReturnType<typeof nativeRuntimeFixture>>) {
  const route = f.source.route.bind(f.source);
  f.source.route = (url, body) => {
    const result = route(url, body);
    if (url.pathname.endsWith("/events") && body.after_cursor !== "cursor-0") {
      const batch = result.value as { events: unknown[]; next_cursor: string; high_watermark: string };
      batch.events = []; batch.next_cursor = "other-subscription-global-tail"; batch.high_watermark = "other-subscription-global-tail";
    }
    return result;
  };
}

describe("native Agent branch with real SDK, HTTP transport and stdio process", () => {
  it("records an actual empty global batch advance without another full-reference Consume or W advance", async () => {
    const f = await nativeRuntimeFixture({ mode: "shadow" }); emptyGlobalTail(f);
    try {
      expect((await f.runtime.tick()).type).toBe("shadow");
      const initialState = await f.runtime.exportAgentState();
      expect((await f.runtime.tick()).type).toBe("shadow");
      expect(f.runtime.status().session).toMatchObject({ state_version: 1, prefix: {
        received_cursor: "other-subscription-global-tail", consumed_publication_index: "1", omissions: { received_unconsumed_count: 0 } } });
      const events = await f.events(), tails = events.filter(e => e.kind === "native_event_batch_received");
      expect(tails).toHaveLength(2); expect(tails[1].payload).toMatchObject({ after_cursor: "cursor-1", next_cursor: "other-subscription-global-tail",
        high_watermark: "other-subscription-global-tail", retained_start_cursor: "cursor-0", event_count: 0 });
      expect(events.filter(e => e.kind === "agent_consumed")).toHaveLength(1);
      const afterTail = await f.runtime.exportAgentState();
      expect(afterTail.state.metadata.prefix.received_cursor).toBe("cursor-1");
      expect(afterTail.state.payload.data_base64).toBe(initialState.state.payload.data_base64);
      await archive(f, "empty-global-batch-full-reference");
    } finally { await f.close(); }
  });
  it("uses the actual empty batch tail as the later scoped-query ACK received cursor", async () => {
    const f = await nativeRuntimeFixture({ child: "query", mode: "shadow" }); emptyGlobalTail(f);
    try {
      expect((await f.runtime.tick()).type).toBe("shadow"); expect((await f.runtime.tick()).type).toBe("shadow");
      const events = await f.events(), consumed = events.filter(e => e.kind === "agent_consumed");
      expect(consumed).toHaveLength(2);
      expect(consumed[1].payload.acknowledgement.prefix).toMatchObject({ received_cursor: "other-subscription-global-tail",
        consumed_publication_index: null, omissions: { received_unconsumed_count: 1 } });
      expect(consumed[1].payload.report).toMatchObject({ state_version: 1, advanced: false });
      const tailIndex = events.findIndex(e => e.kind === "native_event_batch_received" && e.payload.event_count === 0);
      expect(events.indexOf(consumed[1])).toBeGreaterThan(tailIndex);
      await archive(f, "empty-global-batch-scoped-query-ack");
    } finally { await f.close(); }
  });
  it("records each full-reference ACK at its actual event cursor before the completed batch tail", async () => {
    const f = await nativeRuntimeFixture({ mode: "shadow" });
    try {
      await f.runtime.tick();
      const events = await f.events(), ackIndex = events.findIndex(e => e.kind === "agent_consumed"), tailIndex = events.findIndex(e => e.kind === "native_event_batch_received"), nextIndex = events.findIndex(e => e.kind === "agent_directive");
      expect(events[ackIndex].payload.acknowledgement.prefix.received_cursor).toBe("cursor-1");
      expect(events[tailIndex].payload).toMatchObject({ after_cursor: "cursor-0", next_cursor: "cursor-1", event_count: 1 });
      expect(tailIndex).toBeGreaterThan(ackIndex); expect(nextIndex).toBeGreaterThan(tailIndex);
    } finally { await f.close(); }
  });
  it("does not record a completed batch tail when an offered full-reference Consume is interrupted", async () => {
    const f = await nativeRuntimeFixture({ child: "hang_consume", mode: "shadow" });
    const offered = gate(), consume = f.port.consume.bind(f.port);
    // Observe the real port offer, then interrupt that call through Human recovery.
    // Startup speed is not the interrupted-batch contract; deadline tests are separate.
    f.port.consume = (context, input, handlers, signal, onOffer) => consume(
      context, input, handlers, signal, requestId => { onOffer(requestId); offered.release(); });
    try {
      const tick = f.runtime.tick();
      await Promise.race([offered.promise, tick.then(() => {
        throw new Error("Consume was not offered before the tick finished");
      })]);
      const human = f.runtime.setMode("human");
      await tick; await human;
      expect(f.runtime.status()).toMatchObject({ mode: "human", session: { agent_state: "uncertain" } });
      const events = await f.events();
      expect(events.filter(e => e.kind === "native_event_received")).toHaveLength(1);
      expect(events.filter(e => e.kind === "agent_consumed")).toHaveLength(0);
      expect(events.filter(e => e.kind === "native_event_batch_received")).toHaveLength(0);
      expect(f.runtime.status().session.prefix.omissions.received_unconsumed_count).toBe(1);
      await archive(f, "interrupted-consume-no-completed-batch");
    } finally { await f.close(); }
  });
  it.each(["terminal", "future_publication_kind"])("consumes every %s source view once, including observed empty C, without inferring Close", async kind => {
    const f = await nativeRuntimeFixture({ count: 0, status: "observed", mode: "shadow" }); sourceKind(f, kind);
    try {
      expect((await f.runtime.tick()).type).toBe("awaited");
      expect((await f.runtime.tick()).type).toBe("awaited");
      expect(f.runtime.status()).toMatchObject({ lifecycle: "running", controller: "released", session: { state_version: 1,
        prefix: { consumed_publication_index: "1", omissions: { received_unconsumed_count: 0, gap: null } } } });
      expect((await f.events()).filter(e => e.kind === "agent_consumed")).toHaveLength(1);
      expect(f.source.requests.filter(r => /\/(current|actions)$/.test(r.path))).toHaveLength(0);
      await archive(f, `${kind}-captured`);
    } finally { await f.close(); }
  });
  it.each(["missing", "payload_expired"])("records the original terminal %s view as a gap without Current substitution", async availability => {
    const f = await nativeRuntimeFixture({ count: 0, status: "observed", mode: "auto" }); sourceKind(f, "terminal", availability);
    try {
      expect((await f.runtime.tick()).type).toBe("not_admitted");
      expect(f.runtime.status()).toMatchObject({ mode: "human", session: { state_version: 0, prefix: { omissions: {
        gap: { reason: availability === "missing" ? "source_capture_missing" : "payload_expired", from_publication_index: "1", through_publication_index: "1" } } } } });
      expect(f.source.requests.filter(r => /\/(current|read|catalog|actions)$/.test(r.path))).toHaveLength(0);
      expect((await f.events()).some(e => e.kind === "native_event_batch_received")).toBe(false);
      await expect(f.runtime.setMode("auto")).rejects.toMatchObject({ code: "runtime_source_gap" });
      await archive(f, `terminal-${availability}`);
    } finally { await f.close(); }
  });
  it("counts the terminal source view omitted by an explicit scoped-query consumer", async () => {
    const f = await nativeRuntimeFixture({ child: "query", mode: "shadow" }); sourceKind(f, "terminal");
    try {
      expect((await f.runtime.tick()).type).toBe("shadow");
      expect(f.runtime.status().session.prefix).toMatchObject({ consumed_publication_index: null,
        omissions: { received_unconsumed_count: 1, gap: null } });
      await archive(f, "scoped-terminal-omitted");
    } finally { await f.close(); }
  });
  it("keeps the same original child and opaque state when Human cancels Restore during fresh-runtime validation", async () => {
    const f = await nativeRuntimeFixture({ mode: "shadow" }); const capabilities = gate(); let replacement: NdjsonAgentSessionPort | undefined;
    try {
      await f.runtime.tick(); await f.runtime.setMode("human"); const saved = await f.runtime.exportAgentState();
      const child = (f.port as unknown as { child: { pid: number }; closed: boolean }).child;
      replacement = NdjsonAgentSessionPort.spawn(process.execPath, [CHILD, f.manifestPath, "act"], f.manifest.adapter, f.manifest.limits, { byteBudget: f.port.byteBudget });
      const before = f.source.requests.filter(r => r.path.endsWith("/capabilities")).length; f.source.capabilitiesGate = capabilities.promise;
      const restoring = f.runtime.restoreAgentState(saved.state, replacement);
      const rejected = expect(restoring).rejects.toMatchObject({ code: "runtime_recovery_epoch_mismatch" });
      await eventually(() => f.source.requests.filter(r => r.path.endsWith("/capabilities")).length > before);
      const human = f.runtime.setMode("human"); capabilities.release(); await rejected; await human; f.source.capabilitiesGate = null;
      expect((f.port as unknown as { closed: boolean }).closed).toBe(false);
      expect((f.runtime as unknown as { port: NdjsonAgentSessionPort }).port).toBe(f.port);
      expect(f.runtime.status().session).toMatchObject({ state_version: 1, agent_state: "known" });
      await f.runtime.setMode("shadow"); expect((await f.runtime.tick()).type).toBe("shadow");
      expect((f.port as unknown as { child: { pid: number } }).child.pid).toBe(child.pid);
      await f.runtime.setMode("human"); const after = await f.runtime.exportAgentState();
      expect(after.state.payload.data_base64).toBe(saved.state.payload.data_base64);
      await archive(f, "restore-human-original-retained");
    } finally { capabilities.release(); replacement?.close(); await f.close(); }
  });
  it.each(["controller_acquired", "native_submission_requested"] as const)("records Human after %s at its actual original dispatch boundary", async kind => {
    const f = await nativeRuntimeFixture({ mode: "auto" }); const paused = gate(), seen = gate();
    const append = f.evidence.append.bind(f.evidence);
    f.evidence.append = async (...args) => { const value = await append(...args); if (args[0] === kind) { seen.release(); await paused.promise; } return value; };
    try {
      const tick = f.runtime.tick(); await seen.promise;
      const human = f.runtime.setMode("human"); paused.release(); await tick; await human;
      expect(f.source.requests.filter(r => r.path.endsWith("/actions"))).toHaveLength(0);
      const events = await f.events(), intent = events.find(e => e.kind === "native_submission_requested"), closure = events.find(e => e.kind === "native_submission_not_started");
      if (kind === "controller_acquired") { expect(intent).toBeUndefined(); expect(closure).toBeUndefined(); }
      else {
        expect(closure.payload).toMatchObject({ request_id: intent.payload.request_id, submission_epoch: intent.payload.recovery_epoch,
          recovery_epoch: f.runtime.status().session.recovery_epoch, reason: "runtime_recovery_epoch_mismatch" });
      }
      await archive(f, kind === "controller_acquired" ? "human-pre-intent" : "original-intent-not-started");
    } finally { paused.release(); await f.close(); }
  });
  it("preserves the passive session when Human fences an already written Close directive", async () => {
    const f = await nativeRuntimeFixture({ child: "close", mode: "shadow" }); const paused = gate(), seen = gate();
    const append = f.evidence.append.bind(f.evidence);
    f.evidence.append = async (...args) => { const result = await append(...args); if (args[0] === "agent_directive") { seen.release(); await paused.promise; } return result; };
    try {
      const tick = f.runtime.tick(); await seen.promise; const human = f.runtime.setMode("human"); paused.release(); await tick; await human;
      expect(f.runtime.status()).toMatchObject({ lifecycle: "running", mode: "human", session: { state_version: 1, agent_state: "known" } });
      expect(f.source.requests.filter(r => r.path.endsWith("/detach"))).toHaveLength(0);
      await archive(f, "human-fenced-close");
    } finally { paused.release(); await f.close(); }
  });
  it("ends an unattested child startup at the active autonomy deadline", async () => {
    const start = performance.now();
    await expect(nativeRuntimeFixture({ child: "hang_ready", mode: "auto", deadlineMs: 100 })).rejects.toThrow("autonomy_budget_exhausted");
    expect(performance.now() - start).toBeLessThan(1500);
  });
  it("rejects an unimplemented explicit reset before attaching or offering input", async () => {
    await expect(nativeRuntimeFixture({ gapPolicy: "explicit_reset" })).rejects.toThrow("native_explicit_reset_unsupported");
  });
  it("returns every Current candidate to a full-reference query without advancing the same occurrence twice", async () => {
    const f = await nativeRuntimeFixture({ child: "current_full", count: 200, mode: "shadow" });
    try {
      const result = await f.runtime.tick();
      expect(result.type, JSON.stringify(result)).toBe("shadow");
      expect(result.status.session.state_version).toBe(1);
      expect(f.source.requests.filter(r => r.path.endsWith("/current"))).toHaveLength(1);
      expect(f.source.requests.filter(r => r.path.endsWith("/catalog"))).toHaveLength(4);
      const acquisitions = (await f.events()).filter(e => e.kind === "native_acquisition_registered");
      expect(acquisitions).toHaveLength(2);
      expect(acquisitions.every(e => e.payload.witness.catalog_materialized === true)).toBe(true);
    } finally { await f.close(); }
  });
  it("cleans the actual subscription, child and shared buffers when Stop evidence fails", async () => {
    const f = await nativeRuntimeFixture({ mode: "auto" });
    try {
      expect((await f.runtime.tick()).type).toBe("delivered");
      const append = f.evidence.append.bind(f.evidence);
      f.evidence.append = async (...args) => {
        if (args[0] === "stopped") throw new Error("synthetic evidence failure");
        return append(...args);
      };
      await expect(f.runtime.stop()).rejects.toThrow("synthetic evidence failure");
      expect(f.runtime.status()).toMatchObject({ lifecycle: "stopped", mode: "human", tainted: true, controller: "released" });
      expect(f.port.byteBudget.used).toBe(0);
      expect(f.source.requests.filter(r => r.path.endsWith("/detach"))).toHaveLength(1);
      expect(f.source.requests.filter(r => r.path.endsWith("/controller/release"))).toHaveLength(1);
      const child = (f.port as unknown as { child: { exitCode: number | null; signalCode: string | null } }).child;
      await eventually(() => child.exitCode !== null || child.signalCode !== null);
    } finally { await f.close(); }
  });
  it("retains an original missing publication as a gap without substituting Current", async () => {
    const f = await nativeRuntimeFixture({ mode: "auto" });
    const route = f.source.route.bind(f.source);
    f.source.route = (url, body) => {
      const result = route(url, body);
      if (url.pathname.endsWith("/events")) {
        const batch = result.value as { events: { event: Record<string, unknown>; availability: string }[] };
        for (const entry of batch.events) {
          entry.availability = "missing"; entry.event.capture_ref = null;
          entry.event.payload_reference = null; entry.event.missing_reason = "source_capture_missing";
        }
      }
      return result;
    };
    try {
      const result = await f.runtime.tick();
      expect(result.type).toBe("not_admitted");
      expect(result.status).toMatchObject({ mode: "human", session: { state_version: 0,
        prefix: { omissions: { gap: { reason: "source_capture_missing", from_publication_index: "1", through_publication_index: "1" } } } } });
      expect(f.source.requests.filter(r => /\/(current|read|catalog|actions)$/.test(r.path))).toHaveLength(0);
      await expect(f.runtime.setMode("auto")).rejects.toMatchObject({ code: "runtime_source_gap" });
    } finally { await f.close(); }
  });
  it.skipIf(!process.env.E3_NATIVE_FIXTURE_OUTPUT)("exports a finalized actual producer conformance run for the independent application verifier", async () => {
    const f = await nativeRuntimeFixture({ mode: "shadow" });
    try {
      await f.runtime.tick(); await f.runtime.exportAgentState();
      f.source.behavior = "pending"; await f.runtime.setMode("auto");
      await f.runtime.tick(); const id = f.runtime.status().pending_request!.request_id;
      expect((await f.runtime.reconcileOriginalRequest(id)).resolution).toBe("resolved");
      await f.runtime.stop();
      const target = process.env.E3_NATIVE_FIXTURE_OUTPUT!;
      await mkdir(target, { recursive: true });
      await cp(f.evidence.directory, join(target, "run"), { recursive: true });
      await writeFile(join(target, "fixture-scope.json"), JSON.stringify({ source: "actual Native Runtime factory/SDK/HTTP/stdio programmed consumer",
        scope: "synthetic source/test conformance only; no game, loaded, Human, numerical Model or qualification claims",
        sdk_revision: "651f9cbd163d177805fa641ca0f05b8dcb424877", runtime_base: "f89a5267f43ea206fe39d081f5fd5d3643953ca8",
        runtime_source_revision: process.env.E3_NATIVE_FIXTURE_RUNTIME_REVISION ?? "uncommitted native integration draft",
        runtime_tree: "identity values fixture-only; not component qualification", original_request_id: id }, null, 2));
    } finally { await f.close(); }
  });
  it("attaches once without control and uses only the original initial publication before one actual SDK submit", async () => {
    const f = await nativeRuntimeFixture();
    try {
      expect(f.runtime.status().schema).toBe("sts2.policy-runtime/agent-session-status-1");
      expect(f.runtime.status()).not.toHaveProperty("policy");
      expect(f.source.requests.filter(r => r.path.includes("/controller/"))).toHaveLength(0);
      await f.runtime.setMode("one_step");
      const result = await f.runtime.tick();
      expect(result.type, JSON.stringify(result)).toBe("delivered");
      expect(result.status.mode).toBe("human");
      expect(result.status.last_result).toMatchObject({ delivery: "delivered", effect: "pending" });
      expect(result.status.session.state_version).toBe(1);
      expect(f.source.requests.filter(r => r.path.endsWith("/clients/register"))).toHaveLength(1);
      expect(f.source.requests.filter(r => r.path.endsWith("/current"))).toHaveLength(0);
      expect(f.source.requests.filter(r => r.path.endsWith("/actions"))).toHaveLength(1);
      const events = await f.events();
      expect(events.find(e => e.kind === "agent_consumed").payload.acknowledgement.prefix.consumed_publication_index).toBe("1");
      expect(events.some(e => e.kind === "successor")).toBe(false);
    } finally { await f.close(); }
  });
  it.each([200, 500])("preserves the full %s-member relation without shortlist or candidate indices", async count => {
    const f = await nativeRuntimeFixture({ count, mode: "shadow" });
    try {
      const result = await f.runtime.tick();
      expect(result.type).toBe("shadow"); expect(result.status.last_observation?.catalog_count).toBe(count);
      expect(f.source.requests.filter(r => r.path.endsWith("/catalog"))).toHaveLength(Math.ceil(count / 100));
      expect(f.source.requests.filter(r => r.path.includes("/controller/"))).toHaveLength(0);
      expect(f.source.requests.filter(r => r.path.endsWith("/actions"))).toHaveLength(0);
    } finally { await f.close(); }
  });
  it("consumes a genuine complete observed/empty-C input and leaves timeout as a read-only outcome", async () => {
    const f = await nativeRuntimeFixture({ count: 0, status: "observed", mode: "auto" });
    try {
      expect((await f.runtime.tick()).type).toBe("awaited");
      expect(f.runtime.status().session.state_version).toBe(1);
      expect((await f.runtime.tick()).type).toBe("awaited");
      expect(f.runtime.status().session.state_version).toBe(1);
      expect(f.source.requests.filter(r => r.path.endsWith("/actions"))).toHaveLength(0);
      expect(f.source.requests.filter(r => r.path.includes("/controller/"))).toHaveLength(0);
    } finally { await f.close(); }
  });
  it("supports a real non-scoring current/descriptor/structural Resolve consumer without reading the full catalog", async () => {
    const f = await nativeRuntimeFixture({ child: "query", mode: "one_step", count: 500 });
    try {
      const result = await f.runtime.tick();
      expect(result.type, JSON.stringify(result)).toBe("delivered");
      expect(result.status.session.prefix.history_mode).toBe("scoped_query");
      expect(result.status.session.prefix.omissions.received_unconsumed_count).toBe(1);
      expect(f.source.requests.filter(r => r.path.endsWith("/catalog"))).toHaveLength(0);
      expect(f.source.requests.filter(r => r.path.endsWith("/resolve"))).toHaveLength(2);
      const acquired = (await f.events()).find(e => e.kind === "native_acquisition_registered");
      expect(acquired.payload.witness).toMatchObject({ catalog_count: 500, catalog_materialized: false });
      expect(result.status.last_directive).toMatchObject({ type: "act", scores: null });
    } finally { await f.close(); }
  });
  it("fences 202 pending, performs only an explicit original lookup, and stays Human after known terminal resolution", async () => {
    const f = await nativeRuntimeFixture({ mode: "auto" }); f.source.behavior = "pending";
    try {
      const pending = await f.runtime.tick();
      expect(pending.type).toBe("unknown"); expect(pending.status.tainted).toBe(false);
      expect(pending.status.pending_request?.status).toBe("pending"); expect(pending.status.mode).toBe("human");
      await expect(f.runtime.setMode("auto")).rejects.toMatchObject({ code: "runtime_pending_request_unresolved" });
      expect(f.source.requests.filter(r => r.path.includes("/actions/"))).toHaveLength(0);
      await expect(f.runtime.reconcileOriginalRequest("other-id")).rejects.toMatchObject({ code: "runtime_pending_request_mismatch" });
      expect(f.source.requests.filter(r => r.path.includes("/actions/"))).toHaveLength(0);
      const id = pending.status.pending_request!.request_id;
      const resolved = await f.runtime.reconcileOriginalRequest(id);
      expect(resolved).toMatchObject({ request_id: id, resolution: "resolved", status: { mode: "human", pending_request: null } });
      expect(f.source.requests.filter(r => r.path.includes("/actions/"))).toHaveLength(1);
      expect(f.source.requests.filter(r => r.path.endsWith("/actions"))).toHaveLength(1);
      const events = await f.events();
      expect(events.find(e => e.kind === "native_request_pending").payload.original.request_id).toBe(id);
      expect(events.find(e => e.kind === "native_request_reconciled").payload.original.request_id).toBe(id);
    } finally { await f.close(); }
  });
  it.each(["unknown", "partial", "transport_error"] as const)("fences %s delivery without another submit", async delivery => {
    const f = await nativeRuntimeFixture({ mode: "auto" }); f.source.behavior = delivery;
    try {
      expect((await f.runtime.tick()).type).toBe("unknown");
      expect(f.runtime.status().tainted).toBe(true);
      await expect(f.runtime.setMode("auto")).rejects.toMatchObject({ code: "runtime_tainted" });
      expect(f.source.requests.filter(r => r.path.endsWith("/actions"))).toHaveLength(1);
    } finally { await f.close(); }
  });
  it("stores real opaque programmed state and restores a fresh process only at the exact retained acknowledgement", async () => {
    const f = await nativeRuntimeFixture({ mode: "shadow" });
    try {
      expect((await f.runtime.tick()).type).toBe("shadow");
      await f.runtime.setMode("human");
      const saved = await f.runtime.exportAgentState();
      const bytes = await readFile(join(f.evidence.directory, saved.receipt.path));
      expect(bytes.length).toBe(saved.receipt.bytes);
      const replacement = NdjsonAgentSessionPort.spawn(process.execPath, [CHILD, f.manifestPath, "act"],
        f.manifest.adapter, f.manifest.limits, { byteBudget: f.port.byteBudget });
      await f.runtime.restoreAgentState(saved.state, replacement);
      expect(f.runtime.status().session).toMatchObject({ state_version: 1, agent_state: "known" });
      await f.runtime.setMode("one_step");
      expect((await f.runtime.tick()).type).toBe("delivered");
      expect(f.runtime.status().session.state_version).toBe(1);
    } finally { await f.close(); }
  });
  it("Human aborts an owned Await and releases before serialized cleanup without another action", async () => {
    const f = await nativeRuntimeFixture({ count: 0, mode: "auto" }); const wait = gate(); f.source.waitGate = wait.promise;
    try {
      const tick = f.runtime.tick();
      await eventually(() => f.source.requests.some(r => r.path.endsWith("/await")));
      const start = performance.now();
      const human = await f.runtime.setMode("human");
      expect(performance.now() - start).toBeLessThan(1000);
      expect(human).toMatchObject({ mode: "human", controller: "released", session: { agent_state: "known" } });
      await tick;
      expect(f.source.requests.filter(r => r.path.endsWith("/cancel_wait"))).toHaveLength(1);
      expect(f.source.requests.filter(r => r.path.endsWith("/actions"))).toHaveLength(0);
    } finally { wait.release(); await f.close(); }
  });
  it("Human after POST keeps its actual original terminal result and cannot submit another action", async () => {
    const f = await nativeRuntimeFixture({ mode: "auto" }); const submit = gate(); f.source.submitGate = submit.promise;
    try {
      const tick = f.runtime.tick();
      await eventually(() => f.source.requests.some(r => r.path.endsWith("/actions")));
      const human = f.runtime.setMode("human");
      await eventually(() => f.source.requests.some(r => r.path.endsWith("/controller/release")));
      expect(f.runtime.status().mode).toBe("human");
      submit.release();
      const result = await tick; await human;
      expect(result.type).toBe("delivered");
      expect(f.runtime.status()).toMatchObject({ mode: "human", controller: "released", tainted: false,
        last_result: { delivery: "delivered", effect: "pending" } });
      expect(f.source.requests.filter(r => r.path.endsWith("/actions"))).toHaveLength(1);
    } finally { submit.release(); await f.close(); }
  });
  it("Human before pending acquisition completion fences the returned credentials and performs no POST", async () => {
    const f = await nativeRuntimeFixture({ mode: "auto" }); const acquire = gate(); f.source.acquireGate = acquire.promise;
    try {
      const tick = f.runtime.tick();
      await eventually(() => f.source.requests.some(r => r.path.endsWith("/controller/acquire")));
      const human = f.runtime.setMode("human"); acquire.release();
      await tick; await human;
      expect(f.source.requests.filter(r => r.path.endsWith("/actions"))).toHaveLength(0);
      expect(f.source.requests.filter(r => r.path.endsWith("/controller/acquire"))).toHaveLength(1);
      expect(f.source.requests.filter(r => r.path.endsWith("/controller/release"))).toHaveLength(1);
    } finally { acquire.release(); await f.close(); }
  });
  it("deadline expires idle after delivery with no further tick or status GET", async () => {
    const f = await nativeRuntimeFixture();
    try {
      // The test starts its short wallet only after startup/stdio warmup.
      (f.runtime as unknown as { owner: { budget: { deadlineMs: number } } }).owner.budget.deadlineMs = 150;
      await f.runtime.setMode("auto"); expect((await f.runtime.tick()).type).toBe("delivered");
      await eventually(() => f.source.requests.some(r => r.path.endsWith("/controller/release")), 1000);
      await eventually(async () => (await f.events()).some(e => e.kind === "autonomy_budget_exhausted"));
      expect((await f.events()).some(e => e.kind === "autonomy_budget_exhausted")).toBe(true);
      expect(f.runtime.status()).toMatchObject({ mode: "human", controller: "released", autonomy_budget: { state: "exhausted", exhausted_reason: "deadline" } });
    } finally { await f.close(); }
  });
  it("expired original pending outcomes stay unresolved and close as tainted without inferring non-delivery", async () => {
    const f = await nativeRuntimeFixture({ mode: "auto" }); f.source.behavior = "pending"; f.source.lookup = "expired";
    try {
      await f.runtime.tick(); const id = f.runtime.status().pending_request!.request_id;
      const result = await f.runtime.reconcileOriginalRequest(id);
      expect(result.resolution).toBe("unresolved"); expect(result.status.pending_request?.status).toBe("unresolved");
      await f.runtime.stop();
      const run = JSON.parse(await readFile(join(f.evidence.directory, "manifest.json"), "utf8"));
      expect(run).toMatchObject({ status: "tainted", tainted: true });
      expect(f.source.requests.filter(r => r.path.endsWith("/actions"))).toHaveLength(1);
    } finally { await f.close(); }
  });
});
