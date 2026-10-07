import { describe, expect, it, vi } from "vitest";
import type { TextMenuV2ObservationContext } from "@rsgcsg/sts2-connector-client";
import { ConnectorPolicyClient } from "../src/connector.js";
import type { ConnectorAdapterClient } from "../src/contracts.js";

// SDK tests own strict capsule/schema decoding; these test the actual Runtime
// transport selection, failure propagation and lack of a direct-read fallback.
const context = {
  schema: "sts2.player-environment/text-menu-observation-context-2",
  game_continuity_id: "game-a", snapshot: { snapshot_id: "sealed-source" }
} as TextMenuV2ObservationContext;

function wire() {
  return {
    getFullTextMenuV2: vi.fn(async () => ({ context, capture: { capture_id: "capture-a" } })),
    observeTextMenuV2: vi.fn(async () => ({ data: { snapshot_id: "direct" } })),
    observeTextMenuV2Context: vi.fn(async () => ({ data: context })),
    observeTextMenuContext: vi.fn(async () => ({ data: { schema: "legacy" } }))
  };
}

describe("explicit sealed text-v2 acquisition", () => {
  it("uses verified full acquisition for both stateful and stateless reads", async () => {
    const client = wire();
    const adapter = new ConnectorPolicyClient(client as unknown as ConnectorAdapterClient,
      { observationAcquisition: "sealed-text-menu-v2" });
    expect(await adapter.observeTextMenuContext("text-menu-v2")).toBe(context);
    expect(await adapter.observeBundle([], "text-menu-v2")).toEqual({ observation: context.snapshot, reads: [] });
    expect(client.getFullTextMenuV2).toHaveBeenCalledTimes(2);
    expect(client.observeTextMenuV2).not.toHaveBeenCalled();
    expect(client.observeTextMenuV2Context).not.toHaveBeenCalled();
  });

  it("fails closed on a capsule error or wrong profile without direct fallback", async () => {
    const client = wire();
    client.getFullTextMenuV2.mockRejectedValueOnce(new Error("capsule_digest_mismatch"));
    const adapter = new ConnectorPolicyClient(client as unknown as ConnectorAdapterClient,
      { observationAcquisition: "sealed-text-menu-v2" });
    await expect(adapter.observeTextMenuContext("text-menu-v2")).rejects.toThrow("capsule_digest_mismatch");
    await expect(adapter.observeTextMenuContext("text-menu-v1")).rejects.toThrow("requires_text_menu_v2");
    await expect(adapter.observeBundle([], undefined)).rejects.toThrow("requires_text_menu_v2");
    expect(client.observeTextMenuV2Context).not.toHaveBeenCalled();
    expect(client.observeTextMenuContext).not.toHaveBeenCalled();
    expect(() => new ConnectorPolicyClient({} as ConnectorAdapterClient,
      { observationAcquisition: "sealed-text-menu-v2" })).toThrow("client_unsupported");
  });

  it("keeps the prior acquisition behavior unless explicitly selected", async () => {
    const client = wire();
    const adapter = new ConnectorPolicyClient(client as unknown as ConnectorAdapterClient);
    await adapter.observeTextMenuContext("text-menu-v2");
    await adapter.observeBundle([], "text-menu-v2");
    expect(client.observeTextMenuV2Context).toHaveBeenCalledOnce();
    expect(client.observeTextMenuV2).toHaveBeenCalledOnce();
    expect(client.getFullTextMenuV2).not.toHaveBeenCalled();
  });
});
