import { createServer } from "node:http";
import { once } from "node:events";
import { describe, expect, it } from "vitest";
import { NativeLogicalSession, PlayerEnvironmentRestClient, EnvironmentControllerSession } from "../src/index.js";
import { nativeScenario, measuredBudget } from "./nativeLogicalFixtures.js";

describe("native logical synthetic HTTP boundary", () => {
  it("uses the same real HTTP client for registration, immutable transfer and private controller submission", async () => {
    const source = nativeScenario(200);
    const requests: { path: string; method: string; body: Record<string, unknown> }[] = [];
    const server = createServer(async (request, response) => {
      const pieces: Buffer[] = [];
      for await (const chunk of request) pieces.push(Buffer.from(chunk));
      const body = pieces.length ? JSON.parse(Buffer.concat(pieces).toString("utf8")) : {};
      const url = new URL(request.url!, "http://127.0.0.1");
      requests.push({ path: url.pathname, method: request.method!, body });
      const value = source.route(url, body);
      const wire = Buffer.from(JSON.stringify(value));
      response.writeHead(url.pathname.endsWith("/actions") ? 202 : 200, { "content-type": "application/json", "content-length": wire.length });
      // Real transport split, including potential UTF8 boundaries in labels.
      response.write(wire.subarray(0, 19));
      setImmediate(() => response.end(wire.subarray(19)));
    });
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    const address = server.address();
    if (!address || typeof address === "string") throw new Error("synthetic listener unavailable");
    const client = new PlayerEnvironmentRestClient(`http://127.0.0.1:${address.port}`, 2000);
    const controller = new EnvironmentControllerSession(client, { productId: "http-test", productName: "HTTP Test", productVersion: "1", clientInstanceId: "http-instance" });
    const session = new NativeLogicalSession(client, controller);
    const budget = measuredBudget();
    try {
      const capabilities = (await session.capabilities()).data;
      await controller.register(capabilities.session, capabilities.control_policy);
      const capture = await session.getFullCurrent({ budget, chunkBytes: 1024, pageLimit: 73 });
      expect(capture.actions).toEqual(source.actions);
      expect(capture.serializedObservation).toBe(source.serialized);
      expect(requests.filter(request => request.path.includes("/controller/"))).toHaveLength(0);
      const result = await session.submit({ requestId: "http-action", expectedSnapshotId: capture.capture.snapshot_id,
        actionId: capture.actions[0]!.action_id });
      expect(result.status).toBe("terminal");
      if (result.status === "terminal") expect(result.result.data.delivery).toBe("unknown");
      expect(requests.filter(request => request.path.endsWith("/clients/register"))).toHaveLength(1);
      expect(requests.filter(request => request.path.endsWith("/controller/acquire"))).toHaveLength(1);
      const post = requests.find(request => request.path.endsWith("/actions"))!;
      expect(post.method).toBe("POST");
      expect(post.body).toMatchObject({ input_profile: "native-logical-v1", client_session_id: "client-fixture", controller_lease_id: "lease-fixture" });
      await capture.dispose();
      expect(budget.active).toBe(0);
      await controller.releaseControl();
      expect(controller.clientIdentity()?.clientSessionId).toBe("client-fixture");
    } finally {
      await controller.close();
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    }
  });

  it("aborts an unfinished response stream and never retries its read", async () => {
    let reads = 0;
    let cancelled = false;
    const abort = new AbortController();
    const client = new PlayerEnvironmentRestClient("http://127.0.0.1:15526", 2000, (async () => {
      reads++;
      return new Response(new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new TextEncoder().encode('{"schema":')); },
        cancel() { cancelled = true; } }));
    }) as typeof fetch);
    const request = client.nativeLogicalRequest("read", { capture_id: "capture", cursor: "cursor", max_bytes: 10 }, { signal: abort.signal });
    const failed = expect(request).rejects.toThrow(/stop/u);
    abort.abort(new Error("stop"));
    await failed;
    expect(reads).toBe(1);
    expect(cancelled).toBe(true);
  });

  it("rejects over-budget encoded bodies, duplicate fields and invalid UTF8 before exposing records", async () => {
    for (const body of [Buffer.from('{"x":1,"x":2}'), Buffer.from([0xff])]) {
      const client = new PlayerEnvironmentRestClient("http://127.0.0.1:15526", 2000, (async () => new Response(body)) as typeof fetch);
      await expect(client.nativeLogicalRequest("capabilities")).rejects.toThrow();
    }
    const client = new PlayerEnvironmentRestClient("http://127.0.0.1:15526", 2000, (async () => new Response("{}", { headers: { "content-length": "10000" } })) as typeof fetch);
    await expect(client.nativeLogicalRequest("capabilities", undefined, { maxResponseBytes: 100 })).rejects.toThrow(/byte budget/u);
  });
});
