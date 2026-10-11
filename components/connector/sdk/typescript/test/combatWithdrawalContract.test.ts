import { describe, expect, it } from "vitest";
import { measuredBudget, nativeHarness, nativeScenario } from "./nativeLogicalFixtures.js";

describe("complete native combat withdrawal wire contract", () => {
  it("consumes a settling empty relation then recovered interactive relation without control or submit", async () => {
    const empty = nativeScenario(0);
    const ready = nativeScenario(1);
    let source = empty;
    const connection = await nativeHarness(0, (url, body) => {
      const operation = url.pathname.split("/").at(-1)!;
      if (["current", "read", "catalog", "retain", "release"].includes(operation)) return source.route(url, body);
      return undefined;
    });
    try {
      const waiting = await connection.session.getFullCurrent({ budget: measuredBudget(), chunkBytes: 1024, pageLimit: 10 });
      expect(waiting.observation.status).toBe("settling");
      expect(waiting.actions).toEqual([]);
      expect(waiting.observation.completeness.status).toBe("complete");
      await waiting.dispose();
      source = ready;
      const recovered = await connection.session.getFullCurrent({ budget: measuredBudget(), chunkBytes: 1024, pageLimit: 10 });
      expect(recovered.observation.status).toBe("interactive");
      expect(recovered.actions).toHaveLength(1);
      await recovered.dispose();
      expect(connection.calls.filter(call => call.url.pathname.includes("/controller/"))).toEqual([]);
      expect(connection.calls.filter(call => call.url.pathname.includes("/actions"))).toEqual([]);
    } finally { await connection.controller.close(); }
  });
});
