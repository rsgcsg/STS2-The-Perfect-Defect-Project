import { randomUUID } from "node:crypto";
import type { JsonObject } from "./json.js";
import { PlayerEnvironmentHttpError } from "./client.js";

interface ControlClientRecord {
  readonly client_session_id: string;
  readonly client_instance_id: string;
}

interface ControlLease {
  readonly controller_lease_id: string;
  readonly controller_generation: number;
  readonly client_session_id: string;
  readonly expires_at: string;
}

interface ControlRegistration {
  readonly runtime_instance_id: string;
  readonly client: ControlClientRecord;
  readonly controller?: ControlLease | null;
}

interface ControlLeaseResponse {
  readonly runtime_instance_id: string;
  readonly status?: string;
  readonly controller?: ControlLease | null;
}

interface ControlSnapshot {
  readonly runtime_instance_id: string;
  readonly controller?: ControlLease | null;
}

export class EnvironmentControlUncertainError extends Error {
  constructor(message: string, options?: ErrorOptions) { super(message, options); this.name = "EnvironmentControlUncertainError"; }
}
class ControlEpochChangedError extends Error {
  constructor() { super("Player Environment control was released while credentials were pending"); this.name = "ControlEpochChangedError"; }
}

interface DecodedControlPayload<T> {
  readonly raw: JsonObject;
  readonly data: T;
}

export interface EnvironmentControllerCredentials {
  readonly clientSessionId: string;
  readonly clientInstanceId: string;
  readonly controllerLeaseId: string;
  readonly controllerGeneration: number;
}

export interface EnvironmentControllerClientIdentity {
  readonly productId: string;
  readonly productName: string;
  readonly productVersion: string;
  readonly clientInstanceId?: string;
}

export interface EnvironmentControlClient {
  registerClient(input: {
    clientInstanceId: string;
    productId: string;
    productName: string;
    productVersion: string;
  }): Promise<DecodedControlPayload<ControlRegistration>>;
  acquireController(
    clientSessionId: string
  ): Promise<DecodedControlPayload<ControlLeaseResponse>>;
  renewController(input: {
    clientSessionId: string;
    controllerLeaseId: string;
    controllerGeneration: number;
  }): Promise<DecodedControlPayload<ControlLeaseResponse>>;
  releaseController(input: {
    clientSessionId: string;
    controllerLeaseId: string;
    controllerGeneration: number;
  }): Promise<DecodedControlPayload<ControlLeaseResponse>>;
  /** Optional for old clients; required for explicit uncertain-release reconciliation. */
  controlSnapshot?(): Promise<DecodedControlPayload<ControlSnapshot>>;
}

export class EnvironmentControllerSession {
  private readonly clientInstanceId: string;
  private registration?: ControlRegistration;
  private lease?: ControlLease;
  private renewalTimer?: ReturnType<typeof setTimeout>;
  private operation?: Promise<void>;
  private closed = false;
  private recommendedRenewalMs = 10_000;
  private controlEpoch = 0;
  private releaseRequested = false;
  private releaseUncertain = false;
  private releaseOperation?: Promise<void>;
  private retiredLease?: ControlLease;
  private acquireFailed = false;

  constructor(
    private readonly environment: EnvironmentControlClient,
    private readonly identity: EnvironmentControllerClientIdentity
  ) {
    if (!identity.productId.trim()
        || !identity.productName.trim()
        || !identity.productVersion.trim()) {
      throw new Error("Player Environment controller identity fields must be non-empty");
    }
    this.clientInstanceId = identity.clientInstanceId?.trim()
      || `${identity.productId}-${randomUUID()}`;
  }

  async register(
    runtime: { runtime_instance_id: string },
    coordination: { recommended_renewal_ms: number }
  ): Promise<void> {
    if (this.closed) throw new Error("Player Environment controller session is closed");
    if (this.registration) return;
    const registration = await this.environment.registerClient({
      clientInstanceId: this.clientInstanceId,
      productId: this.identity.productId,
      productName: this.identity.productName,
      productVersion: this.identity.productVersion
    });
    if (registration.data.runtime_instance_id
          !== runtime.runtime_instance_id
        || registration.data.client.client_instance_id
          !== this.clientInstanceId) {
      throw new Error("Player Environment client registration does not match negotiated capabilities");
    }
    this.registration = registration.data;
    this.recommendedRenewalMs = coordination.recommended_renewal_ms;
  }

  async credentials(): Promise<EnvironmentControllerCredentials> {
    const epoch = this.controlEpoch;
    this.assertControl(epoch);
    await this.serialize(async () => {
      this.assertControl(epoch);
      if (!this.registration) {
        throw new Error("Player Environment controller session was not registered");
      }
      if (this.lease && !this.shouldRenew(this.lease)) return;

      if (this.lease) {
        try {
          const renewed = await this.environment.renewController({
            clientSessionId: this.registration.client.client_session_id,
            controllerLeaseId: this.lease.controller_lease_id,
            controllerGeneration: this.lease.controller_generation
          });
          this.acceptLease(renewed.data, epoch, false);
          return;
        } catch {
          this.assertControl(epoch); // Stop cannot fall through into a replacement acquire.
          this.lease = undefined;
        }
      }

      this.assertControl(epoch);
      try {
        const acquired = await this.environment.acquireController(this.registration.client.client_session_id);
        this.acquireFailed = false;
        this.acceptLease(acquired.data, epoch, true);
      } catch (error) {
        if (!(error instanceof ControlEpochChangedError)) {
          // A received 4xx is an original rejection; timeout/transport/invalid
          // success has no correlated terminal acquisition proof.
          this.acquireFailed = !(error instanceof PlayerEnvironmentHttpError &&
            error.statusCode !== undefined && error.statusCode >= 400 && error.statusCode < 500);
          if (this.acquireFailed) this.releaseUncertain = true;
        }
        throw error;
      }
    });

    this.assertControl(epoch);
    if (!this.registration || !this.lease) {
      throw new Error("Player Environment did not provide an active controller lease");
    }
    return {
      clientSessionId: this.registration.client.client_session_id,
      clientInstanceId: this.clientInstanceId,
      controllerLeaseId: this.lease.controller_lease_id,
      controllerGeneration: this.lease.controller_generation
    };
  }

  snapshot(): JsonObject {
    return {
      client_instance_id: this.clientInstanceId,
      registered: Boolean(this.registration),
      client_session_id: this.registration?.client.client_session_id ?? null,
      controller_lease_id: this.lease?.controller_lease_id ?? null,
      controller_generation: this.lease?.controller_generation ?? null,
      controller_expires_at: this.lease?.expires_at ?? null,
      controller_release_uncertain: this.releaseUncertain,
      controller_acquire_uncertain: this.acquireFailed
    };
  }

  /** Lease-only handoff. The registration and passive subscriptions remain alive.
   * A known lease is revoked immediately, without waiting behind a pending renewal. */
  releaseControl(): Promise<void> {
    if (this.releaseOperation) return this.releaseOperation;
    if (this.releaseUncertain && !this.lease)
      return Promise.reject(new EnvironmentControlUncertainError("Reconcile the original owner before another release or acquisition"));
    this.controlEpoch++;
    this.releaseRequested = true;
    this.clearRenewal();
    const original = this.lease;
    this.lease = undefined;
    const previous = this.operation;
    const current = Promise.resolve().then(async () => {
      try {
        if (original) {
          await this.releaseKnown(original);
        } else {
          // An initial acquire has no known token yet. Late credentials are
          // fenced and the actually returned lease is used only for cleanup.
          if (previous) await previous.catch(() => undefined);
          const retired = this.retiredLease;
          this.retiredLease = undefined;
          if (retired) await this.releaseKnown(retired);
          else if (this.acquireFailed) throw new EnvironmentControlUncertainError("Initial acquisition outcome requires original-owner reconciliation");
        }
        if (this.acquireFailed) throw new EnvironmentControlUncertainError("An unidentified acquisition may still arrive; snapshot absence cannot prove it terminal");
        this.releaseUncertain = false;
      } catch (error) {
        this.releaseUncertain = true;
        throw new EnvironmentControlUncertainError("Player Environment lease release is uncertain; no new acquisition is allowed", { cause: error });
      } finally {
        this.releaseRequested = false;
        if (this.releaseOperation === current) this.releaseOperation = undefined;
      }
    });
    this.releaseOperation = current;
    return current;
  }

  /** One read of the actual original authority, never a retry or a new registration.
   * A still-held original client lease stays fenced until an explicit release. */
  async reconcileControl(): Promise<"released" | "still_held" | "unreconciled"> {
    if (this.releaseOperation) await this.releaseOperation.catch(() => undefined);
    if (!this.releaseUncertain) return this.lease ? "still_held" : "released";
    if (this.closed || !this.registration || !this.environment.controlSnapshot)
      throw new EnvironmentControlUncertainError("The original control authority is unavailable for reconciliation");
    let result: "released" | "still_held" | "unreconciled" = "released";
    const epoch = this.controlEpoch;
    await this.serialize(async () => {
      const snapshot = (await this.environment.controlSnapshot!()).data;
      if (this.closed || epoch !== this.controlEpoch || this.releaseRequested) throw new ControlEpochChangedError();
      if (snapshot.runtime_instance_id !== this.registration!.runtime_instance_id)
        throw new EnvironmentControlUncertainError("Control reconciliation returned another runtime");
      if (snapshot.controller?.client_session_id === this.registration!.client.client_session_id) {
        this.lease = snapshot.controller;
        result = "still_held"; // No credentials or renewal while uncertain.
      } else if (this.acquireFailed) {
        this.lease = undefined;
        result = "unreconciled"; // The original request can still grant after this snapshot.
      } else {
        this.lease = undefined;
        this.retiredLease = undefined;
        this.acquireFailed = false;
        this.releaseUncertain = false;
      }
    });
    return result;
  }

  async close(): Promise<void> {
    this.closed = true;
    try {
      await this.releaseControl();
    } catch {
      // TTL is the crash-safe release path; shutdown must not mask the caller's result.
    }
  }

  private acceptLease(response: ControlLeaseResponse, epoch: number, acquired: boolean): void {
    if (!this.registration
        || response.runtime_instance_id !== this.registration.runtime_instance_id
        || !response.controller
        || response.controller.client_session_id
          !== this.registration.client.client_session_id) {
      throw new Error("Player Environment controller response does not match this registered client");
    }
    if (this.closed || epoch !== this.controlEpoch || this.releaseRequested || this.releaseUncertain) {
      if (acquired) this.retiredLease = response.controller;
      throw new ControlEpochChangedError();
    }
    this.lease = response.controller;
    this.scheduleRenewal();
  }

  private scheduleRenewal(): void {
    if (this.renewalTimer) clearTimeout(this.renewalTimer);
    if (!this.lease || this.closed || this.releaseRequested || this.releaseUncertain) return;
    const epoch = this.controlEpoch;
    const expiresInMs = Date.parse(this.lease.expires_at) - Date.now();
    const delayMs = Math.max(
      100,
      expiresInMs - this.recommendedRenewalMs
    );
    this.renewalTimer = setTimeout(() => {
      void this.credentials().catch(() => {
        if (epoch === this.controlEpoch) this.lease = undefined;
      });
    }, delayMs);
    this.renewalTimer.unref?.();
  }

  private shouldRenew(lease: ControlLease): boolean {
    return Date.parse(lease.expires_at) - Date.now()
      <= this.recommendedRenewalMs;
  }

  private async serialize(operation: () => Promise<void>): Promise<void> {
    const previous = this.operation;
    // Publish the pending operation before invoking an injected transport. A
    // synchronous Stop from its acquire callback must see and await this owner.
    const current = Promise.resolve().then(async () => {
      if (previous) {
        try {
          await previous;
        } catch {
          // The new operation gets one independent attempt.
        }
      }
      await operation();
    });
    this.operation = current;
    try {
      await current;
    } finally {
      if (this.operation === current) this.operation = undefined;
    }
  }

  private clearRenewal(): void {
    if (this.renewalTimer) clearTimeout(this.renewalTimer);
    this.renewalTimer = undefined;
  }

  private assertControl(epoch: number): void {
    if (this.closed) throw new Error("Player Environment controller session is closed");
    if (epoch !== this.controlEpoch || this.releaseRequested) throw new ControlEpochChangedError();
    if (this.releaseUncertain) throw new EnvironmentControlUncertainError("Reconcile the original owner before acquiring control");
  }

  private async releaseKnown(lease: ControlLease): Promise<void> {
    if (!this.registration) throw new Error("Player Environment controller session was not registered");
    const response = (await this.environment.releaseController({ clientSessionId: this.registration.client.client_session_id,
      controllerLeaseId: lease.controller_lease_id, controllerGeneration: lease.controller_generation })).data;
    if (response.runtime_instance_id !== this.registration.runtime_instance_id || response.status !== "controller_released" || response.controller != null)
      throw new EnvironmentControlUncertainError("Controller release did not confirm the original lease was revoked");
  }

  /** Passive consumers reuse this registration without acquiring mutation control. */
  clientIdentity(): { runtimeInstanceId: string; clientSessionId: string; clientInstanceId: string } | undefined {
    if (this.closed) throw new Error("Player Environment controller session is closed");
    if (!this.registration) return undefined;
    return { runtimeInstanceId: this.registration.runtime_instance_id,
      clientSessionId: this.registration.client.client_session_id, clientInstanceId: this.clientInstanceId };
  }
}
