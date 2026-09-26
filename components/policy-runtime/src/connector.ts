import {
  EnvironmentControllerSession,
  PlayerEnvironmentHttpError,
  prefetchPlayerEnvironmentDecisionBundle,
  type EnvironmentControlClient,
  type EnvironmentControllerSession as ControllerSession
} from "@rsgcsg/sts2-connector-client";
import { POLICY_RUNTIME_VERSION, type AnyDecisionBundle, type ConnectorAdapterClient, type PolicyConnector } from "./contracts.js";

export class StaleWholeBundleError extends Error {
  readonly code = "stale_state" as const;
  constructor(message = "a Snapshot-bound Read made the whole decision bundle stale") {
    super(message);
    this.name = "StaleWholeBundleError";
  }
}

export interface ConnectorPolicyClientOptions {
  productId?: string;
  productName?: string;
  productVersion?: string;
  clientInstanceId?: string;
}

type ActiveController = { session: ControllerSession; bridge: ControllerControlBridge };

export class ConnectorPolicyClient implements PolicyConnector {
  private capabilitiesValue?: Awaited<ReturnType<ConnectorAdapterClient["capabilities"]>>["data"];
  private controller?: ActiveController;
  private readonly options: Required<Pick<ConnectorPolicyClientOptions, "productId" | "productName" | "productVersion">> & Pick<ConnectorPolicyClientOptions, "clientInstanceId">;

  constructor(private readonly client: ConnectorAdapterClient, options: ConnectorPolicyClientOptions = {}) {
    this.options = {
      productId: options.productId ?? "sts2-policy-runtime",
      productName: options.productName ?? "STS2 Policy Runtime",
      productVersion: options.productVersion ?? POLICY_RUNTIME_VERSION,
      clientInstanceId: options.clientInstanceId
    };
  }

  async capabilities(options?: { fresh?: boolean; inputProfile?: "text-menu-v1" }) {
    if (options?.inputProfile === "text-menu-v1") return (await this.client.textMenuCapabilities()).data;
    // A control precondition must observe the actual endpoint. Never overwrite
    // an admitted/cached identity merely because that endpoint was replaced.
    if (options?.fresh) return (await this.client.capabilities()).data;
    if (!this.capabilitiesValue) this.capabilitiesValue = (await this.client.capabilities()).data;
    return this.capabilitiesValue;
  }

  async observeBundle(requiredReadKinds: readonly string[], inputProfile?: "text-menu-v1"): Promise<AnyDecisionBundle> {
    if (inputProfile === "text-menu-v1") {
      if (requiredReadKinds.length !== 0) throw new Error("text_menu_reads_unsupported");
      return { observation: (await this.client.observeTextMenu()).data, reads: [] };
    }
    const observation = (await this.client.observe()).data;
    const required = new Set(requiredReadKinds);
    for (const kind of required) {
      if (!observation.reads.some((read) => read.kind === kind)) {
        throw new Error(`required_read_unavailable:${kind}`);
      }
    }
    try {
      return await prefetchPlayerEnvironmentDecisionBundle(
        observation,
        async (readId, expectedSnapshotId) => {
          try {
            return (await this.client.read(readId, expectedSnapshotId)).data;
          } catch (error) {
            if (isStale(error)) throw new StaleWholeBundleError(String(error));
            throw error;
          }
        },
        (read) => required.has(read.kind)
      );
    } catch (error) {
      if (error instanceof StaleWholeBundleError) throw error;
      throw error;
    }
  }

  async acquireController(): Promise<void> {
    if (this.controller) {
      if (this.controller.bridge.closing) throw new Error("controller_release_unconfirmed");
      return;
    }
    const capabilities = await this.capabilities();
    const bridge = new ControllerControlBridge(this.client, capabilities.host.runtime_instance_id);
    const session = new EnvironmentControllerSession(
      bridge,
      {
        productId: this.options.productId,
        productName: this.options.productName,
        productVersion: this.options.productVersion,
        clientInstanceId: this.options.clientInstanceId
      }
    );
    await session.register(capabilities.host, capabilities.control);
    await session.credentials();
    this.controller = { session, bridge };
  }

  async releaseController(): Promise<void> {
    const active = this.controller;
    if (!active) return;
    if (active.bridge.closing) throw new Error("controller_release_unconfirmed");
    active.bridge.beginClose();
    await active.bridge.drain();
    await active.session.close();
    active.bridge.assertReleased();
    this.controller = undefined;
  }

  async submit(input: { requestId: string; expectedSnapshotId: string; boundActionId: string; inputProfile?: "text-menu-v1" }) {
    if (this.controller?.bridge.closing) throw new Error("controller_release_unconfirmed");
    if (!this.controller) throw new Error("Policy Runtime requires an acquired Connector controller");
    const credentials = await this.controller.session.credentials();
    const payload = {
      requestId: input.requestId,
      expectedSnapshotId: input.expectedSnapshotId,
      boundActionId: input.boundActionId,
      clientSessionId: credentials.clientSessionId,
      controllerLeaseId: credentials.controllerLeaseId,
      controllerGeneration: credentials.controllerGeneration
    };
    return input.inputProfile === "text-menu-v1"
      ? (await this.client.submitTextMenu(payload)).data
      : (await this.client.submit(payload)).data;
  }
}

/** Bridges the SDK's fire-and-forget close to one verified Host release. */
class ControllerControlBridge implements EnvironmentControlClient {
  closing = false;
  private readonly inFlight = new Set<Promise<unknown>>();
  private registeredClientInstanceId: string | null = null;
  private releaseAttempted = false;
  private releaseConfirmed = false;
  private releaseFailure: unknown = null;

  constructor(private readonly client: ConnectorAdapterClient, private readonly runtimeInstanceId: string) {}

  registerClient(input: Parameters<EnvironmentControlClient["registerClient"]>[0]): ReturnType<EnvironmentControlClient["registerClient"]> {
    this.registeredClientInstanceId = input.clientInstanceId;
    return this.client.registerClient(input) as ReturnType<EnvironmentControlClient["registerClient"]>;
  }

  acquireController(input: Parameters<EnvironmentControlClient["acquireController"]>[0]): ReturnType<EnvironmentControlClient["acquireController"]> {
    if (this.closing) return Promise.reject(new Error("controller_session_closing"));
    return this.track(this.client.acquireController(input) as ReturnType<EnvironmentControlClient["acquireController"]>);
  }

  renewController(input: Parameters<EnvironmentControlClient["renewController"]>[0]): ReturnType<EnvironmentControlClient["renewController"]> {
    if (this.closing) return Promise.reject(new Error("controller_session_closing"));
    return this.track(this.client.renewController(input) as ReturnType<EnvironmentControlClient["renewController"]>);
  }

  releaseController(input: Parameters<EnvironmentControlClient["releaseController"]>[0]): ReturnType<EnvironmentControlClient["releaseController"]> {
    if (this.releaseAttempted) return Promise.reject(new Error("controller_release_already_attempted"));
    this.releaseAttempted = true;
    let submitted: Promise<unknown>;
    try { submitted = this.client.releaseController(input); }
    catch (error) { this.releaseFailure = error; return Promise.reject(error); }
    return submitted.then((response) => {
      if (!verifiedReleaseAck(response, this.runtimeInstanceId, input.clientSessionId, this.registeredClientInstanceId)) {
        this.releaseFailure = new Error("controller_release_ack_unconfirmed");
        throw this.releaseFailure;
      }
      this.releaseConfirmed = true;
      return response as Awaited<ReturnType<EnvironmentControlClient["releaseController"]>>;
    }, (error: unknown) => { this.releaseFailure = error; throw error; });
  }

  beginClose(): void { this.closing = true; }

  async drain(): Promise<void> {
    while (this.inFlight.size > 0) await Promise.allSettled([...this.inFlight]);
  }

  assertReleased(): void {
    if (!this.releaseAttempted || !this.releaseConfirmed) {
      throw new Error(`controller_release_unconfirmed${this.releaseFailure === null ? "" : `:${message(this.releaseFailure)}`}`);
    }
  }

  private track<T>(promise: Promise<T>): Promise<T> {
    this.inFlight.add(promise);
    void promise.then(() => { this.inFlight.delete(promise); }, () => { this.inFlight.delete(promise); });
    return promise;
  }
}

function verifiedReleaseAck(value: unknown, runtimeInstanceId: string, clientSessionId: string, clientInstanceId: string | null): boolean {
  if (!value || typeof value !== "object" || !("data" in value)) return false;
  const data = value.data;
  if (!data || typeof data !== "object") return false;
  if (!("client" in data) || !data.client || typeof data.client !== "object") return false;
  return "status" in data && data.status === "controller_released"
    && "runtime_instance_id" in data && data.runtime_instance_id === runtimeInstanceId
    && "controller" in data && data.controller === null
    && "client_session_id" in data.client && data.client.client_session_id === clientSessionId
    && "client_instance_id" in data.client && data.client.client_instance_id === clientInstanceId;
}

function message(error: unknown): string { return error instanceof Error ? error.message : String(error); }

function isStale(error: unknown): boolean {
  return error instanceof PlayerEnvironmentHttpError && error.statusCode === 409;
}
