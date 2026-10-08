import { describe, expect, it } from "vitest";
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
      await f.runtime.setMode("one_step");
      const delivered = await f.runtime.tick();
      expect(delivered.type, JSON.stringify(delivered)).toBe("delivered");
      expect(delivered.status.mode).toBe("human");
      expect(delivered.status.session.state_version).toBe(1);
      expect(f.source.requests.filter(r => r.path.endsWith("/actions"))).toHaveLength(1);
    } catch (error) {
      const diagnostics = Buffer.concat((f.port as unknown as { stderrChunks: Buffer[] }).stderrChunks).toString("utf8");
      throw new Error(`numerical conformance phase failed: ${String(error)}\n${diagnostics}`, { cause: error });
    } finally { await f.close(); }
  }, 30000);
});
