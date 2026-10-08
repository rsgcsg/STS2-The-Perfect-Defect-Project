import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import { PassThrough, Writable } from "node:stream";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NdjsonAgentSessionPort, type AgentPortHandlers } from "../src/agent-session-port.js";
import { AgentConsumptionLedger, type AgentAcquisition } from "../src/agent-session-consumption.js";
import { AGENT_SESSION_SCHEMA, type AgentManifest, type AgentNextInput, type AgentQueryResult } from "../src/agent-session-contracts.js";

const shared = JSON.parse(readFileSync(new URL("../contracts/fixtures/agent-session-v1.json", import.meta.url), "utf8")) as {
  manifest: AgentManifest; acquisitions: Record<string, AgentAcquisition>;
};
const context = { session_id: "fixture-session", recovery_epoch: 7 };
const initial: AgentNextInput = { continuity_token: "segment", consumption_id: null,
  state_version: 0, basis_acquisition_id: null, received_cursor: "cursor-10" };
function fixture(maxBytes?: number) {
  const written: Record<string, unknown>[] = [];
  const stdin = new Writable({ write(chunk, _encoding, callback) {
    written.push(JSON.parse(Buffer.from(chunk).toString("utf8")) as Record<string, unknown>); callback();
  } });
  const stdout = new PassThrough(), stderr = new PassThrough();
  const child = Object.assign(new EventEmitter(), { stdin, stdout, stderr, exitCode: null,
    kill: vi.fn(() => true) });
  const limits = { ...shared.manifest.limits, ...(maxBytes === undefined ? {} : { max_message_bytes: maxBytes }) };
  const port = new NdjsonAgentSessionPort(child as unknown as ConstructorParameters<typeof NdjsonAgentSessionPort>[0], shared.manifest.adapter, limits);
  const emit = (type: string, requestId: string, field: string, value: unknown, extras = {}) => {
    stdout.write(JSON.stringify({ schema: AGENT_SESSION_SCHEMA, message_type: type, ...context,
      request_id: requestId, [field]: value, ...extras }) + "\n");
  };
  stdout.write(JSON.stringify({ schema: AGENT_SESSION_SCHEMA, message_type: "ready", adapter: shared.manifest.adapter }) + "\n");
  return { port, child, stdout, stdin, written, emit };
}
function handlers(ledger?: AgentConsumptionLedger): AgentPortHandlers {
  return { query: vi.fn(async () => { throw new Error("unexpected query"); }),
    consumed: vi.fn(report => {
      if (!ledger) throw new Error("unexpected consumption");
      return ledger.accept(report);
    }) };
}
const abstain = { continuity_token: "segment", consumption_id: null, state_version: 0,
  directive: { type: "abstain", reason: "fixture" } };
afterEach(() => { vi.useRealTimers(); });

describe("strict duplex Agent session child port", () => {
  it("attests a distinct Agent identity and accepts score-free directives", async () => {
    const f = fixture(); await f.port.ready();
    const result = f.port.next(context, initial, handlers(), new AbortController().signal, () => {});
    const request = f.written.at(-1)!;
    f.emit("directive", String(request.request_id), "output", abstain);
    expect((await result).directive.type).toBe("abstain"); f.port.close();
  });
  it("consumes complete empty-C observation and sends its bound acknowledgement", async () => {
    const f = fixture(); await f.port.ready();
    const ledger = new AgentConsumptionLedger(shared.manifest, "segment");
    const a = structuredClone(shared.acquisitions.empty!); ledger.register(a);
    const input = { acquisition_id: a.acquisition_id, input_spec: shared.manifest.input.input_spec,
      continuity_token: "segment", previous_consumption_id: null, observation: a.observation, catalog: a.catalog };
    const result = f.port.consume(context, input, handlers(ledger), new AbortController().signal, () => {});
    const request = f.written.at(-1)!;
    f.emit("consumed", String(request.request_id), "completion", { acquisition_id: a.acquisition_id,
      input_spec: input.input_spec, continuity_token: "segment", previous_consumption_id: null,
      consumption_id: "one", state_version: 1, advanced: true });
    expect((await result).state_version).toBe(1);
    expect(f.written.at(-1)).toMatchObject({ message_type: "consume_ack", request_id: request.request_id });
    f.port.close();
  });
  it("forwards an actual query then requires explicit consumption before accepting changed state", async () => {
    const f = fixture(); await f.port.ready();
    const manifest = structuredClone(shared.manifest);
    manifest.input.history_mode = "scoped_query"; manifest.input.consumption_mode = "incremental_view";
    manifest.input.attachment.delivery_mode = "scoped";
    const ledger = new AgentConsumptionLedger(manifest, "segment");
    const a = structuredClone(shared.acquisitions.query_current!);
    const h = handlers(ledger);
    h.query = vi.fn(async input => {
      expect(input).toEqual({ method: "current", arguments: { eager_scope: ["persistent", "interaction", "referents", "catalog"] } });
      ledger.register(a);
      return { method: "current" as const, value: { capture: a.capture, observation: a.observation }, acquisition_id: a.acquisition_id };
    });
    const result = f.port.next(context, initial, h, new AbortController().signal, () => {});
    const request = f.written.at(-1)!;
    f.emit("query", "child-query-1", "input", { method: "current", arguments: { eager_scope: ["persistent", "interaction", "referents", "catalog"] } });
    await vi.waitFor(() => expect(f.written.at(-1)?.message_type).toBe("query_result"));
    expect(ledger.stateVersion).toBe(0); // Query response did not consume any Model/Agent state.
    f.emit("consumed", "child-report-1", "completion", { acquisition_id: a.acquisition_id,
      input_spec: manifest.input.input_spec, continuity_token: "segment", previous_consumption_id: null,
      consumption_id: "one", state_version: 1, advanced: true });
    await vi.waitFor(() => expect(f.written.at(-1)?.message_type).toBe("consume_ack"));
    f.emit("directive", String(request.request_id), "output", { ...abstain, consumption_id: "one", state_version: 1 });
    await result; expect(ledger.stateVersion).toBe(1); f.port.close();
  });
  it.each([
    { message_type: "directive", request_id: "parent-forged", output: abstain },
    { message_type: "directive", request_id: "CURRENT", output: abstain, recovery_epoch: 8 },
    { message_type: "query", request_id: "child-forged", input: { method: "submit", arguments: {} } },
    { message_type: "directive", request_id: "CURRENT", output: { ...abstain, scores: [] } }
  ])("poisons unknown IDs, wrong epoch, mutation RPC and unknown fields", async message => {
    const f = fixture(); await f.port.ready();
    const result = f.port.next(context, initial, handlers(), new AbortController().signal, () => {});
    const rejected = expect(result).rejects.toThrow();
    const requestId = message.request_id === "CURRENT" ? String(f.written.at(-1)!.request_id) : message.request_id;
    f.stdout.write(JSON.stringify({ schema: AGENT_SESSION_SCHEMA, ...context, ...message, request_id: requestId }) + "\n");
    await rejected; expect(f.child.kill).toHaveBeenCalledWith("SIGKILL");
  });
  it("poisons duplicate completed responses and does not let them satisfy a new call", async () => {
    const f = fixture(); await f.port.ready();
    const first = f.port.next(context, initial, handlers(), new AbortController().signal, () => {});
    const firstId = String(f.written.at(-1)!.request_id);
    f.emit("directive", firstId, "output", abstain); await first;
    const next = f.port.next(context, initial, handlers(), new AbortController().signal, () => {});
    const rejected = expect(next).rejects.toThrow("unknown_request_id");
    f.emit("directive", firstId, "output", abstain);
    await rejected;
  });
  it("rejects a reused child query ID even after its response completed", async () => {
    const f = fixture(); await f.port.ready();
    const h = handlers(); h.query = vi.fn(async () => ({ method: "resolve" as const, value: { schema: "sts2.player-environment/native-logical-resolve-1", status: "no_match", action: null }, acquisition_id: null }));
    const result = f.port.next(context, initial, h, new AbortController().signal, () => {});
    f.emit("query", "child-query-1", "input", { method: "resolve", arguments: {} });
    await vi.waitFor(() => expect(f.written.at(-1)?.message_type).toBe("query_result"));
    const rejected = expect(result).rejects.toThrow("child_request_id_reused_or_capacity");
    f.emit("query", "child-query-1", "input", { method: "resolve", arguments: {} });
    await rejected; expect(h.query).toHaveBeenCalledTimes(1);
  });
  it("caps bytes before parsing a fragmented stdout line", async () => {
    const f = fixture(512); await f.port.ready();
    const result = f.port.next(context, initial, handlers(), new AbortController().signal, () => {});
    const rejected = expect(result).rejects.toThrow("message_size_limit");
    f.stdout.write(Buffer.alloc(512, 65)); f.stdout.write(Buffer.from("x"));
    await rejected; expect(f.child.kill).toHaveBeenCalled();
  });
  it("aborts a hung call immediately and discards its late query callback", async () => {
    const f = fixture(); await f.port.ready();
    let complete!: (result: Awaited<ReturnType<AgentPortHandlers["query"]>>) => void;
    const h = handlers(); h.query = vi.fn(() => new Promise<AgentQueryResult>(resolve => { complete = resolve; }));
    const controller = new AbortController();
    const result = f.port.next(context, initial, h, controller.signal, () => {});
    f.emit("query", "child-query-1", "input", { method: "resolve", arguments: {} });
    const rejected = expect(result).rejects.toThrow("agent_call_cancelled");
    controller.abort(); await rejected;
    const before = f.written.length;
    complete({ method: "resolve", value: { schema: "sts2.player-environment/native-logical-resolve-1", status: "no_match", action: null }, acquisition_id: null });
    await Promise.resolve(); await Promise.resolve();
    expect(f.written).toHaveLength(before); expect(f.child.kill).toHaveBeenCalledWith("SIGKILL");
  });
  it("a call timeout terminates the child without a second tick or read", async () => {
    vi.useFakeTimers(); const f = fixture(); await f.port.ready();
    const result = f.port.next(context, initial, handlers(), new AbortController().signal, () => {});
    const rejected = expect(result).rejects.toThrow("agent_call_timeout");
    await vi.advanceTimersByTimeAsync(shared.manifest.limits.agent_timeout_ms);
    await rejected; expect(f.child.kill).toHaveBeenCalledWith("SIGKILL");
  });
});
