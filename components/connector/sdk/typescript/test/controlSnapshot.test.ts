import { describe, expect, it, vi } from "vitest";
import {
  PLAYER_ENVIRONMENT_CONTROL_ROUTE, PlayerEnvironmentRestClient,
  decodePlayerControlSnapshot
} from "../src/index.js";

const base = () => ({ protocol_version: "1.0.0", schema: "sts2.player-environment/control-1",
  runtime_instance_id: "runtime", clients: [{ client_session_id: "client", client_instance_id: "instance",
    product_id: "consumer", registered_at: "2026-10-08T00:00:00Z" }] });
const held = () => ({ ...base(), controller: { status: "held", controller_lease_id: "lease",
  controller_generation: 1, client_session_id: "client", acquired_at: "2026-10-08T00:00:00Z",
  expires_at: "2026-10-08T00:01:00Z" } });

describe("canonical current controller status", () => {
  it.each(["Host handoff", "runner stop verification"])("%s reads the actual controller route", async () => {
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      expect(url).toBe("http://127.0.0.1:15526/api/player-environment/controller");
      expect(init.method).toBe("GET");
      return new Response(JSON.stringify(held()));
    });
    const client = new PlayerEnvironmentRestClient("http://127.0.0.1:15526", 1000, fetchImpl as typeof fetch);
    const status = (await client.controlSnapshot()).data;
    expect(PLAYER_ENVIRONMENT_CONTROL_ROUTE).toBe("/api/player-environment/controller");
    expect(status.runtime_instance_id).toBe("runtime");
    expect(status.controller?.controller_lease_id).toBe("lease");
    expect(status.controller?.controller_generation).toBe(1);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("accepts omitted and explicit null controller from native null-omitting serialization", () => {
    expect(decodePlayerControlSnapshot(base()).data.controller).toBeUndefined();
    expect(decodePlayerControlSnapshot({ ...base(), controller: null }).data.controller).toBeNull();
    expect(decodePlayerControlSnapshot({ ...base(), clients: [], controller: null }).data.clients).toEqual([]);
  });

  it("does not interpret malformed held state or missing required status identity as released", async () => {
    const malformed = [
      { ...base(), controller: { status: "held" } },
      { ...held(), controller: { ...held().controller, controller_generation: 0 } },
      { ...held(), controller: { ...held().controller, client_session_id: "" } },
      { ...held(), controller: { ...held().controller, expires_at: "" } },
      { ...base(), runtime_instance_id: "" },
      { ...base(), clients: [{ client_instance_id: "instance" }] },
      { ...base(), clients: undefined },
      { ...base(), schema: "sts2.player-environment/receipt-1" },
      { ...base(), protocol_version: "2.0.0" },
      { ...base(), unrecognized: true }
    ];
    for (const value of malformed) {
      expect(() => decodePlayerControlSnapshot(value)).toThrow("strict decoding");
      const client = new PlayerEnvironmentRestClient("http://127.0.0.1:15526", 1000,
        vi.fn(async () => new Response(JSON.stringify(value))) as typeof fetch);
      await expect(client.controlSnapshot()).rejects.toThrow("strict decoding");
    }
  });
});
