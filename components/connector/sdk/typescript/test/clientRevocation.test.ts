import { describe, expect, it, vi } from "vitest";
import { decodePlayerClientRevocation, validatePlayerClientRevocationRequest, PlayerEnvironmentRestClient,
  EnvironmentControlUncertainError, type JsonObject } from "../src/index.js";
import { nativeHarness, nativeScenario } from "./nativeLogicalFixtures.js";

const runtime = "runtime-fixture";
const original = "client-fixture";
const ack = (changes: Record<string, unknown> = {}) => ({ protocol_version: "1.0.0",
  schema: "sts2.player-environment/client-revoke-1", runtime_instance_id: runtime,
  client_session_id: original, status: "client_revoked", closed: true, controller: null, ...changes });
function deferred<T>() { let resolve!: (value: T) => void;
  const promise = new Promise<T>(yes => { resolve = yes; }); return { promise, resolve }; }

describe("strict final original-client revocation wire", () => {
  it("requires every closed ACK field and rejects unknown keys/types instead of inferring closure", () => {
    expect(decodePlayerClientRevocation(ack()).data.closed).toBe(true);
    expect(Object.isFrozen(decodePlayerClientRevocation(ack()).data)).toBe(true);
    for (const field of Object.keys(ack())) {
      const missing = ack();
      delete missing[field as keyof typeof missing];
      expect(() => decodePlayerClientRevocation(missing), field).toThrow();
    }
    for (const value of [ack({ closed: false }), ack({ closed: null }), ack({ controller: {} }), ack({ status: "released" }),
      ack({ schema: "sts2.player-environment/control-1" }), ack({ protocol_version: "2.0.0" }), ack({ runtime_instance_id: "坏" }),
      ack({ client_session_id: "a".repeat(129) }), ack({ extra: true })]) expect(() => decodePlayerClientRevocation(value)).toThrow();
  });

  it("uses the original two-key request and admits only the exact HTTP200 matching ACK", async () => {
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      expect(url).toBe("http://127.0.0.1:15526/api/player-environment/clients/revoke");
      expect(init.method).toBe("POST");
      expect(JSON.parse(String(init.body))).toEqual({ runtime_instance_id: runtime, client_session_id: original });
      expect(Buffer.byteLength(String(init.body))).toBeLessThanOrEqual(1024);
      return new Response(JSON.stringify(ack()));
    });
    const client = new PlayerEnvironmentRestClient("http://127.0.0.1:15526", 1000, fetchImpl as typeof fetch);
    expect((await client.revokeClient({ runtimeInstanceId: runtime, clientSessionId: original })).data).toEqual(ack());
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    for (const [status, body] of [[201, ack()], [202, ack()], [400, ack()], [404, ack()], [409, ack()], [200, ack({ runtime_instance_id: "other" })],
      [200, ack({ client_session_id: "other" })]] as const) {
      const rejected = new PlayerEnvironmentRestClient("http://127.0.0.1:15526", 1000,
        (async () => new Response(JSON.stringify(body), { status })) as typeof fetch);
      await expect(rejected.revokeClient({ runtimeInstanceId: runtime, clientSessionId: original })).rejects.toThrow();
    }
  });

  it("rejects malformed request identifiers before transport and strict raw response loss before ACK", async () => {
    for (const value of [{ runtime_instance_id: runtime, client_session_id: original, extra: true },
      { runtime_instance_id: runtime }, { runtime_instance_id: "", client_session_id: original },
      { runtime_instance_id: "a".repeat(129), client_session_id: original }, { runtime_instance_id: "with space", client_session_id: original }])
      expect(() => validatePlayerClientRevocationRequest(value)).toThrow();
    const send = vi.fn(async () => new Response(JSON.stringify(ack())));
    const client = new PlayerEnvironmentRestClient("http://127.0.0.1:15526", 1000, send as typeof fetch);
    await expect(client.revokeClient({ runtimeInstanceId: "坏", clientSessionId: original })).rejects.toThrow();
    expect(send).not.toHaveBeenCalled();
    for (const raw of [JSON.stringify(ack()).replace('"closed":true', '"closed":false,"closed":true'),
      Buffer.from([0xff]), " ".repeat(1025)]) {
      const malformed = new PlayerEnvironmentRestClient("http://127.0.0.1:15526", 1000,
        (async () => new Response(raw)) as typeof fetch);
      await expect(malformed.revokeClient({ runtimeInstanceId: runtime, clientSessionId: original })).rejects.toThrow();
    }
  });
});

type Lease = { controller_lease_id: string; controller_generation: number; client_session_id: string; expires_at: string };
type Server = { closed: boolean; held: Lease | null; acquire?: (body: JsonObject) => unknown | Promise<unknown>;
  revoke?: (body: JsonObject) => unknown | Promise<unknown>; renew?: () => unknown | Promise<unknown>;
  native?: (url: URL, body: JsonObject) => unknown | Promise<unknown> };
const lease = (client = original, generation = 1): Lease => ({ controller_lease_id: "lease-" + generation,
  controller_generation: generation, client_session_id: client, expires_at: new Date(Date.now() + 60000).toISOString() });
function control(server: Server) { return { protocol_version: "1.0.0", schema: "sts2.player-environment/control-1",
  runtime_instance_id: runtime, status: "controller_acquired", detail: "mock original Authority", controller: server.held }; }
function closeOriginal(server: Server) { server.closed = true;
  if (server.held?.client_session_id === original) server.held = null;
  return ack(); }
async function harness() {
  const server: Server = { closed: false, held: null };
  const source = await nativeHarness(3, (url, body) => {
    if (url.pathname.endsWith("/controller/acquire")) return server.acquire?.(body) ?? (server.closed
      ? new Response(JSON.stringify({ error: { code: "client_session_revoked", detail: "original closed" } }), { status: 409 })
      : (() => { server.held = lease(); return control(server); })());
    if (url.pathname.endsWith("/clients/revoke")) {
      expect(body).toEqual({ runtime_instance_id: runtime, client_session_id: original });
      return server.revoke?.(body) ?? closeOriginal(server);
    }
    if (url.pathname.endsWith("/controller/renew") && server.renew) return server.renew();
    if (url.pathname.endsWith("/controller")) return { protocol_version: "1.0.0", schema: "sts2.player-environment/control-1",
      runtime_instance_id: runtime, clients: [{ client_session_id: original, client_instance_id: "sdk-fixture" }], controller: server.held };
    if (url.pathname.endsWith("/controller/release")) {
      if (server.held?.client_session_id === original) server.held = null;
    }
    return server.native?.(url, body);
  });
  return { ...source, server };
}
const count = (source: Awaited<ReturnType<typeof harness>>, route: string) => source.calls.filter(call => call.url.pathname.endsWith(route)).length;

describe("one existing coordinator's explicit final uncertain-acquire fence", () => {
  it("revokes after Acquire wins but its response is lost, and closes native passive access without re-registration", async () => {
    const source = await harness();
    await source.session.attach({ eagerScope: ["persistent", "interaction", "referents", "catalog"],
      requiredSeams: source.capabilities.capture_coverage, deliveryMode: "full_reference" });
    source.server.acquire = () => { source.server.held = lease(); throw new Error("acquire response lost"); };
    await expect(source.controller.credentials()).rejects.toThrow(/lost/u);
    expect(source.server.held?.client_session_id).toBe(original);
    const acknowledgement = await source.controller.revokeUncertainClient();
    expect(acknowledgement.data).toEqual(ack());
    expect(source.server.held).toBeNull();
    expect(source.controller.snapshot()).toMatchObject({ registered: false, client_session_closed: true, client_revoked: true,
      controller_acquire_uncertain: false, controller_release_uncertain: false, controller_lease_id: null });
    expect(source.session.subscription).toBeUndefined();
    const before = source.calls.length;
    await expect(source.controller.credentials()).rejects.toThrow(/closed/u);
    await expect(source.controller.register(source.capabilities.session, source.capabilities.control_policy)).rejects.toThrow(/closed/u);
    await expect(source.controller.reconcileControl()).rejects.toThrow(/closed/u);
    await expect(source.session.capabilities()).rejects.toThrow(/closed/u);
    await expect(source.session.read({ captureId: "old", cursor: "old" })).rejects.toThrow(/closed/u);
    await expect(source.session.list({ catalogRef: "old", streamGeneration: "old" })).rejects.toThrow(/closed/u);
    await expect(source.session.resolve({ catalogRef: "old", streamGeneration: "old", expression: { verb: "inspect", subject_referent_id: null, arguments: [] } })).rejects.toThrow(/closed/u);
    expect(source.calls).toHaveLength(before);
    expect(count(source, "/clients/register")).toBe(1);
    expect(count(source, "/controller/acquire")).toBe(1);
    expect(count(source, "/clients/revoke")).toBe(1);
    await source.controller.close();
  });

  it("Revoke wins before the delayed original Acquire enters Authority, without waiting for its response", async () => {
    const source = await harness();
    const entered = deferred<void>();
    const proceed = deferred<void>();
    source.server.acquire = async () => { entered.resolve(); await proceed.promise;
      if (source.server.closed) return new Response(JSON.stringify({ error: { code: "client_session_revoked", detail: "original closed" } }), { status: 409 });
      source.server.held = lease(); return control(source.server); };
    const credentials = source.controller.credentials();
    const rejected = expect(credentials).rejects.toThrow();
    await entered.promise;
    await source.controller.revokeUncertainClient();
    expect(source.controller.registrationClosed).toBe(true);
    expect(source.server.held).toBeNull();
    proceed.resolve();
    await rejected;
    expect(source.controller.snapshot()).toMatchObject({ client_revoked: true, controller_acquire_uncertain: false,
      controller_release_uncertain: false, controller_lease_id: null });
    expect(count(source, "/controller/acquire")).toBe(1);
    expect(count(source, "/clients/revoke")).toBe(1);
    await source.controller.close();
  });

  it("does not restore a late grant response after the matching final closure ACK", async () => {
    const source = await harness();
    const entered = deferred<void>();
    const response = deferred<unknown>();
    source.server.acquire = () => { source.server.held = lease(); entered.resolve(); return response.promise; };
    const credentials = source.controller.credentials();
    const rejected = expect(credentials).rejects.toThrow(/released/u);
    await entered.promise;
    const grantBeforeClose = structuredClone(control(source.server));
    await source.controller.revokeUncertainClient();
    expect(source.server.held).toBeNull();
    response.resolve(grantBeforeClose);
    await rejected;
    expect(source.controller.snapshot()).toMatchObject({ registered: false, client_revoked: true,
      controller_acquire_uncertain: false, controller_release_uncertain: false, controller_lease_id: null });
    expect(count(source, "/controller/release")).toBe(0);
    await source.controller.close();
  });

  it.each(["foreign_runtime", "foreign_client", "lost", "invalid"] as const)
    ("keeps the original owner fenced after %s ACK and never automatically retries", async scenario => {
      const source = await harness();
      source.server.acquire = () => { throw new Error("original acquire lost"); };
      await expect(source.controller.credentials()).rejects.toThrow(/lost/u);
      source.server.revoke = () => {
        if (scenario === "lost") throw new Error("revocation response lost");
        return scenario === "foreign_runtime" ? ack({ runtime_instance_id: "foreign-runtime" })
          : scenario === "foreign_client" ? ack({ client_session_id: "foreign-client" }) : ack({ closed: false });
      };
      await expect(source.controller.revokeUncertainClient()).rejects.toBeInstanceOf(EnvironmentControlUncertainError);
      expect(source.controller.registrationClosed).toBe(false);
      expect(source.controller.snapshot()).toMatchObject({ registered: true, client_revoked: false,
        controller_acquire_uncertain: true, controller_release_uncertain: true });
      await expect(source.controller.credentials()).rejects.toBeInstanceOf(EnvironmentControlUncertainError);
      expect(count(source, "/controller/acquire")).toBe(1);
      expect(count(source, "/clients/revoke")).toBe(1);
      expect(count(source, "/clients/register")).toBe(1);
      await source.controller.close();
      expect(count(source, "/clients/revoke")).toBe(1);
    });

  it("preserves another client's held lease: ACK null is target closure, never global-unheld proof", async () => {
    const source = await harness();
    source.server.acquire = () => { throw new Error("original acquire transport unknown"); };
    await expect(source.controller.credentials()).rejects.toThrow(/unknown/u);
    const other = lease("client-B", 9);
    source.server.held = other;
    await source.controller.revokeUncertainClient();
    expect(source.server.held).toEqual(other);
    expect((await source.client.controlSnapshot()).data.controller?.client_session_id).toBe("client-B");
    expect(count(source, "/controller/release")).toBe(0);
    await source.controller.close();
    expect(source.server.held).toEqual(other);
  });

  it("coalesces only one explicit in-flight revocation and requires a caller-chosen later attempt after loss", async () => {
    const source = await harness();
    source.server.acquire = () => { throw new Error("original acquire lost"); };
    await expect(source.controller.credentials()).rejects.toThrow();
    const response = deferred<unknown>();
    source.server.revoke = () => response.promise;
    const one = source.controller.revokeUncertainClient();
    const duplicate = source.controller.revokeUncertainClient();
    expect(one).toBe(duplicate);
    const rejected = expect(one).rejects.toBeInstanceOf(EnvironmentControlUncertainError);
    response.resolve(ack({ status: "not_closed" }));
    await rejected;
    expect(count(source, "/clients/revoke")).toBe(1);
    source.server.revoke = () => closeOriginal(source.server);
    await source.controller.revokeUncertainClient(); // Explicit closure retry, not a game Acquire retry.
    expect(count(source, "/clients/revoke")).toBe(2);
    expect(count(source, "/controller/acquire")).toBe(1);
    await source.controller.close();
  });

  it("keeps known Human release lease-only and rejects final revocation without uncertain Acquire", async () => {
    const source = await harness();
    const identity = source.controller.clientIdentity();
    await source.controller.credentials();
    await expect(source.controller.revokeUncertainClient()).rejects.toBeInstanceOf(EnvironmentControlUncertainError);
    await source.controller.releaseControl();
    expect(source.controller.clientIdentity()).toEqual(identity);
    expect(source.controller.registrationClosed).toBe(false);
    expect(count(source, "/clients/revoke")).toBe(0);
    await source.controller.credentials();
    expect(count(source, "/clients/register")).toBe(1);
    await source.controller.close();
  });

  it("rejects a late passive response after final client closure instead of exposing a revived subscription", async () => {
    const source = await harness();
    await source.session.attach({ eagerScope: ["persistent", "interaction", "referents", "catalog"],
      requiredSeams: source.capabilities.capture_coverage, deliveryMode: "full_reference" });
    source.server.acquire = () => { throw new Error("original acquire lost"); };
    await expect(source.controller.credentials()).rejects.toThrow();
    const entered = deferred<void>();
    const response = deferred<unknown>();
    source.server.native = url => { if (url.pathname.endsWith("/events")) { entered.resolve(); return response.promise; } };
    const passive = source.session.events({ afterCursor: source.session.subscription!.starting_cursor });
    const rejected = expect(passive).rejects.toThrow(/closed/u);
    await entered.promise;
    await source.controller.revokeUncertainClient();
    response.resolve(source.fixture.wire_samples.event_batch);
    await rejected;
    expect(source.session.subscription).toBeUndefined();
    await source.controller.close();
  });

  it("preserves an already-started native POST's original Result when a later uncertain acquisition is finally revoked", async () => {
    const source = await harness();
    const fake = nativeScenario();
    const entered = deferred<void>();
    const response = deferred<unknown>();
    let acquisitions = 0;
    source.server.acquire = () => {
      if (++acquisitions > 1) throw new Error("later acquisition response lost");
      source.server.held = { ...lease(), expires_at: new Date(Date.now() + 500).toISOString() };
      return control(source.server);
    };
    source.server.renew = () => { throw new Error("renewal response lost"); };
    source.server.native = url => { if (url.pathname.endsWith("/actions")) { entered.resolve(); return response.promise; } };
    const post = source.session.submit({ requestId: "original-started", expectedSnapshotId: source.capture.snapshot_id,
      actionId: source.actions[0]!.action_id });
    await entered.promise;
    await expect(source.controller.credentials()).rejects.toThrow(/lost/u);
    await source.controller.revokeUncertainClient();
    response.resolve(fake.route(new URL("http://fixture/api/player-environment/actions"), { request_id: "original-started" }));
    const result = await post;
    expect(result.status).toBe("terminal");
    if (result.status === "terminal") {
      expect(result.result.data.request_id).toBe("original-started");
      expect(result.result.data.delivery).toBe("unknown");
    }
    expect(count(source, "/api/player-environment/actions")).toBe(1);
    expect(count(source, "/clients/revoke")).toBe(1);
    expect(source.controller.registrationClosed).toBe(true);
    await source.controller.close();
  });
});
