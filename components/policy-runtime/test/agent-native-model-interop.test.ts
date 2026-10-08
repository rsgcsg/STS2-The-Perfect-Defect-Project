import { describe, expect, it } from "vitest";
import { cp, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { nativeRuntimeFixture } from "./native-runtime-fixtures.js";

const python = process.env.E3_NUMERICAL_AGENT_PYTHON;
const pythonPath = process.env.E3_NUMERICAL_AGENT_PYTHONPATH;
const packagePath = process.env.E3_NUMERICAL_AGENT_PACKAGE;

describe("separately enabled actual numerical native Agent interoperability", () => {
  it.skipIf(!python || !pythonPath || !packagePath)("runs the fixed initialized M2 package through actual SDK/HTTP/Runtime/stdin and exports its real opaque state", async () => {
    const f = await nativeRuntimeFixture({ count: 3, mode: "shadow", numericalAgent: { python: python!, pythonPath: pythonPath!, packagePath: packagePath! } });
    try {
      const shadow = await f.runtime.tick();
      expect(shadow.type, JSON.stringify(shadow)).toBe("shadow");
      expect(shadow.status.session.state_version).toBe(1);
      expect(shadow.status.last_directive).toMatchObject({ type: "act" });
      if (shadow.status.last_directive?.type === "act") expect(shadow.status.last_directive.scores?.values).toHaveLength(3);
      expect(f.source.requests.filter(r => r.path.includes("/controller/"))).toHaveLength(0);
      await f.runtime.setMode("human"); const stored = await f.runtime.exportAgentState();
      expect(stored.receipt.bytes).toBeGreaterThan(0);
      expect(stored.state.metadata).toMatchObject({ profile: "native-logical-v1", state_version: 1, input_spec: f.manifest.input.input_spec });
      const route = f.source.route.bind(f.source);
      f.source.route = (url, body) => {
        const reply = route(url, body);
        if (url.pathname.endsWith("/events")) {
          const batch = reply.value as { events: unknown[]; next_cursor: string; high_watermark: string };
          batch.events = []; batch.next_cursor = "numerical-other-subscription-global-tail"; batch.high_watermark = batch.next_cursor;
        }
        return reply;
      };
      await f.runtime.setMode("one_step");
      const delivered = await f.runtime.tick();
      expect(delivered.type, JSON.stringify(delivered)).toBe("delivered");
      expect(delivered.status.mode).toBe("human");
      expect(delivered.status.session.state_version).toBe(1);
      expect(f.source.requests.filter(r => r.path.endsWith("/actions"))).toHaveLength(1);
      expect(f.runtime.status().session.prefix.received_cursor).toBe("numerical-other-subscription-global-tail");
      const afterTail = await f.runtime.exportAgentState();
      expect(afterTail.state.metadata.prefix.received_cursor).toBe(stored.state.metadata.prefix.received_cursor);
      expect(afterTail.state.payload.data_base64).toBe(stored.state.payload.data_base64);
    } catch (error) {
      const diagnostics = Buffer.concat((f.port as unknown as { stderrChunks: Buffer[] }).stderrChunks).toString("utf8");
      throw new Error(`numerical conformance phase failed: ${String(error)}\n${diagnostics}`, { cause: error });
    } finally { await f.close(); }
    const terminal = await nativeRuntimeFixture({ count: 0, status: "observed", mode: "shadow",
      numericalAgent: { python: python!, pythonPath: pythonPath!, packagePath: packagePath! } });
    terminal.source.eventKind = "terminal";
    try {
      expect((await terminal.runtime.tick()).type).toBe("awaited");
      expect((await terminal.runtime.tick()).type).toBe("awaited");
      expect(terminal.runtime.status().session).toMatchObject({ state_version: 1,
        prefix: { consumed_publication_index: "1", omissions: { received_unconsumed_count: 0, gap: null } } });
      await terminal.runtime.setMode("human"); const state = await terminal.runtime.exportAgentState();
      expect(state.state.metadata.state_version).toBe(1);
      expect(state.state.metadata.prefix.consumed_publication_index).toBe("1");
      expect(terminal.source.requests.filter(r => /\/(actions|current)$/.test(r.path))).toHaveLength(0);
      if (process.env.E3_NATIVE_ALTERNATIVE_OUTPUT) {
        await terminal.runtime.stop(); await mkdir(process.env.E3_NATIVE_ALTERNATIVE_OUTPUT, { recursive: true });
        await cp(terminal.evidence.directory, join(process.env.E3_NATIVE_ALTERNATIVE_OUTPUT, "actual-numerical-terminal"), { recursive: true });
      }
    } finally { await terminal.close(); }
  }, 30000);
});
