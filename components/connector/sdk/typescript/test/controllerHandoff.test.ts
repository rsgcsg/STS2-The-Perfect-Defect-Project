import { describe, expect, it, vi } from "vitest";
import { EnvironmentControllerSession, EnvironmentControlUncertainError, type EnvironmentControlClient } from "../src/index.js";
import { nativeHarness } from "./nativeLogicalFixtures.js";

function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function owner() {
  let generation = 0;
  let held: { controller_lease_id: string; controller_generation: number; client_session_id: string; expires_at: string } | null = null;
  const response = () => ({ raw: {}, data: { runtime_instance_id: "runtime", status: "controller_acquired", controller: held } });
  const environment = {
    registerClient: vi.fn(async (input: { clientInstanceId: string }) => ({ raw: {}, data: { runtime_instance_id: "runtime",
      client: { client_session_id: "client", client_instance_id: input.clientInstanceId } } })),
    acquireController: vi.fn(async () => { held = { controller_lease_id: `lease-${++generation}`, controller_generation: generation,
      client_session_id: "client", expires_at: new Date(Date.now() + 500).toISOString() }; return response(); }),
    renewController: vi.fn(async () => response()),
    releaseController: vi.fn(async () => { held = null; return { raw: {}, data: { runtime_instance_id: "runtime", status: "controller_released", controller: null } }; }),
    controlSnapshot: vi.fn(async () => response())
  };
  const session = new EnvironmentControllerSession(environment as EnvironmentControlClient,
    { productId: "test", productName: "Test", productVersion: "1", clientInstanceId: "instance" });
  return { environment, session, response, setHeld(value: typeof held) { held = value; }, get held() { return held; } };
}

describe("one registered owner through lease handoff", () => {
  it("releases only mutation control and reacquires explicitly on the same registration", async () => {
    const source = owner();
    await source.session.register({ runtime_instance_id: "runtime" }, { recommended_renewal_ms: 1000 });
    const identity = source.session.clientIdentity();
    const first = await source.session.credentials();
    await source.session.releaseControl();
    expect(source.session.clientIdentity()).toEqual(identity);
    expect(source.session.snapshot().controller_lease_id).toBeNull();
    const next = await source.session.credentials();
    expect(next.clientSessionId).toBe(first.clientSessionId);
    expect(next.controllerGeneration).toBeGreaterThan(first.controllerGeneration);
    expect(source.environment.registerClient).toHaveBeenCalledTimes(1);
    expect(source.environment.acquireController).toHaveBeenCalledTimes(2);
    await source.session.close();
  });

  it("keeps passive scope/cursors after a known control release", async () => {
    const source = await nativeHarness();
    try {
      await source.session.attach({ eagerScope: ["persistent", "interaction", "referents", "catalog"], requiredSeams: source.capabilities.capture_coverage,
        deliveryMode: "full_reference" });
      const subscription = source.session.subscription!;
      await source.controller.credentials();
      await source.controller.releaseControl();
      await source.session.events({ afterCursor: subscription.starting_cursor });
      await source.session.renew(subscription.starting_cursor);
      expect(source.session.subscription!.subscription_id).toBe(subscription.subscription_id);
      expect(source.session.subscription!.starting_cursor).toBe(subscription.starting_cursor);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/clients/register"))).toHaveLength(1);
      expect(source.calls.filter(call => call.url.pathname.endsWith("/controller/acquire"))).toHaveLength(1);
    } finally { await source.controller.close(); }
  });

  it("does not wait behind pending renewal or accept its late credentials after Stop", async () => {
    const source = owner();
    await source.session.register({ runtime_instance_id: "runtime" }, { recommended_renewal_ms: 1000 });
    const first = await source.session.credentials();
    const reply = deferred<ReturnType<typeof source.response>>();
    source.environment.renewController.mockImplementationOnce(() => reply.promise);
    const pending = source.session.credentials();
    const rejected = expect(pending).rejects.toThrow(/released/u);
    expect(source.environment.renewController).toHaveBeenCalledTimes(1);
    await source.session.releaseControl();
    expect(source.environment.releaseController).toHaveBeenCalledTimes(1);
    expect(source.environment.acquireController).toHaveBeenCalledTimes(1);
    reply.resolve({ raw: {}, data: { runtime_instance_id: "runtime", status: "controller_renewed", controller: {
      controller_lease_id: first.controllerLeaseId, controller_generation: first.controllerGeneration,
      client_session_id: first.clientSessionId, expires_at: new Date(Date.now() + 60000).toISOString() } } });
    await rejected;
    expect(source.session.snapshot().controller_lease_id).toBeNull();
    await source.session.close();
  });

  it("cannot fall back from a late failed renewal into acquisition after Stop", async () => {
    const source = owner();
    await source.session.register({ runtime_instance_id: "runtime" }, { recommended_renewal_ms: 1000 });
    await source.session.credentials();
    const reply = deferred<ReturnType<typeof source.response>>();
    source.environment.renewController.mockImplementationOnce(() => reply.promise);
    const pending = source.session.credentials();
    const rejected = expect(pending).rejects.toThrow(/released/u);
    await source.session.releaseControl();
    reply.reject(new Error("renewal connection ended"));
    await rejected;
    expect(source.environment.acquireController).toHaveBeenCalledTimes(1);
    await source.session.close();
  });

  it("fences an initial pending acquire and releases its actual returned token without giving it to a caller", async () => {
    const source = owner();
    await source.session.register({ runtime_instance_id: "runtime" }, { recommended_renewal_ms: 1000 });
    const original = deferred<ReturnType<typeof source.response>>();
    source.environment.acquireController.mockImplementationOnce(() => original.promise);
    const pending = source.session.credentials();
    const rejected = expect(pending).rejects.toThrow(/released/u);
    const release = source.session.releaseControl();
    expect(source.environment.releaseController).not.toHaveBeenCalled();
    original.resolve({ raw: {}, data: { runtime_instance_id: "runtime", status: "controller_acquired", controller: {
      controller_lease_id: "actual-late", controller_generation: 77, client_session_id: "client", expires_at: new Date(Date.now() + 60000).toISOString() } } });
    await rejected;
    await release;
    expect(source.environment.releaseController).toHaveBeenCalledWith({ clientSessionId: "client", controllerLeaseId: "actual-late", controllerGeneration: 77 });
    expect(source.session.snapshot().controller_lease_id).toBeNull();
    await source.session.close();
  });

  it("fences unknown release until one actual original-owner query confirms that exact lease gone", async () => {
    const source = owner();
    await source.session.register({ runtime_instance_id: "runtime" }, { recommended_renewal_ms: 1000 });
    await source.session.credentials();
    source.environment.releaseController.mockRejectedValueOnce(new Error("release response lost"));
    await expect(source.session.releaseControl()).rejects.toBeInstanceOf(EnvironmentControlUncertainError);
    await expect(source.session.credentials()).rejects.toBeInstanceOf(EnvironmentControlUncertainError);
    await expect(source.session.releaseControl()).rejects.toBeInstanceOf(EnvironmentControlUncertainError);
    expect(source.environment.releaseController).toHaveBeenCalledTimes(1);
    expect(await source.session.reconcileControl()).toBe("still_held");
    await expect(source.session.credentials()).rejects.toBeInstanceOf(EnvironmentControlUncertainError);
    await source.session.releaseControl(); // Explicit original release after a known held query.
    expect(source.environment.releaseController).toHaveBeenCalledTimes(2);
    expect(source.session.snapshot().controller_release_uncertain).toBe(false);
    await source.session.credentials();
    await source.session.close();
  });

  it("does not clear an unidentified timed-out acquire from snapshot absence or allow a late grant to resurrect authority", async () => {
    const source = owner();
    await source.session.register({ runtime_instance_id: "runtime" }, { recommended_renewal_ms: 1000 });
    source.environment.acquireController.mockRejectedValueOnce(new Error("original acquire timeout; server may still receive it"));
    await expect(source.session.credentials()).rejects.toThrow(/timeout/u);
    expect(await source.session.reconcileControl()).toBe("unreconciled");
    await expect(source.session.credentials()).rejects.toBeInstanceOf(EnvironmentControlUncertainError);
    source.setHeld({ controller_lease_id: "late-server-grant", controller_generation: 9, client_session_id: "client", expires_at: new Date(Date.now() + 60000).toISOString() });
    expect(await source.session.reconcileControl()).toBe("still_held");
    await expect(source.session.releaseControl()).rejects.toBeInstanceOf(EnvironmentControlUncertainError);
    expect(await source.session.reconcileControl()).toBe("unreconciled");
    await expect(source.session.credentials()).rejects.toBeInstanceOf(EnvironmentControlUncertainError);
    expect(source.environment.acquireController).toHaveBeenCalledTimes(1);
    await source.session.close();
  });
});
