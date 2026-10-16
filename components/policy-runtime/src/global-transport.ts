import { createHash } from "node:crypto";
import { EnvironmentControllerSession, PlayerEnvironmentRestClient, PUBLIC_BASIS_PROFILE,
  type FullPublicBasis, type PlayerEnvironmentReceipt, type PublicTransportOptions } from "@rsgcsg/sts2-connector-client";
import { canonicalJson } from "./evidence.js";

export interface GlobalIdentity {
  kind: "reference" | "managed"; protocol_version: string;
  session: { runtime_instance_id: string; environment_fingerprint: string };
  game_continuity_id: string | null; producer: Record<string, unknown>; game: Record<string, unknown>;
}
export interface GlobalExpectedEnvironment {
  kind: "global-public-basis"; host_kind: "reference" | "managed"; producer_sha256: string; game_sha256: string;
}
export interface GlobalTransport {
  readonly kind: GlobalIdentity["kind"];
  negotiate(signal: AbortSignal): Promise<GlobalIdentity>;
  capture(options: PublicTransportOptions): Promise<FullPublicBasis>;
  step(input: { requestId: string; expectedSnapshotId: string; boundActionId: string }, options: PublicTransportOptions): Promise<PlayerEnvironmentReceipt>;
  acquire(signal: AbortSignal): Promise<void>;
  controlStatus(): "held" | "released" | "unknown";
  release(): Promise<void>;
  close(): Promise<void>;
}
export const globalDigest = (value: unknown): string => createHash("sha256").update(canonicalJson(value)).digest("hex");
export function assertGlobalIdentity(actual: GlobalIdentity, expected: GlobalExpectedEnvironment, previous?: GlobalIdentity): void {
  if (actual.kind !== expected.host_kind || globalDigest(actual.producer) !== expected.producer_sha256
    || globalDigest(actual.game) !== expected.game_sha256 || previous && canonicalJson(actual) !== canonicalJson(previous))
    throw new Error("global_environment_identity_drift");
}

/** Reference uses the existing SDK control registration/lease and retained public capture. */
export class ReferenceGlobalTransport implements GlobalTransport {
  readonly kind = "reference" as const;
  private identity?: GlobalIdentity;
  readonly controller: EnvironmentControllerSession;
  constructor(readonly client: PlayerEnvironmentRestClient, identity: { productVersion: string; clientInstanceId: string }) {
    this.controller = new EnvironmentControllerSession(client, { productId: "sts2.policy-runtime", productName: "STS2 Policy Runtime", ...identity });
  }
  async negotiate(signal: AbortSignal): Promise<GlobalIdentity> {
    const cap = (await this.client.capabilities({ signal })).data;
    const capture = await this.client.publicBasisCapabilities({ signal });
    if (!cap.execution_available || capture.input_profile !== PUBLIC_BASIS_PROFILE) throw new Error("global_public_capture_unavailable");
    const { runtime_instance_id: runtime, ...producer } = cap.host;
    const identity: GlobalIdentity = { kind: this.kind, protocol_version: cap.protocol_version,
      session: { runtime_instance_id: runtime, environment_fingerprint: cap.environment_fingerprint },
      game_continuity_id: null, producer, game: cap.game as unknown as Record<string, unknown> };
    if (this.identity && canonicalJson(identity) !== canonicalJson(this.identity)) throw new Error("global_reference_replaced");
    signal.throwIfAborted(); await this.controller.register(cap.host, cap.control); signal.throwIfAborted();
    this.identity = identity; return identity;
  }
  capture(options: PublicTransportOptions): Promise<FullPublicBasis> { return this.client.getFullPublicBasis({}, options); }
  async acquire(signal: AbortSignal): Promise<void> { signal.throwIfAborted(); await this.controller.credentials(); signal.throwIfAborted(); }
  async step(input: { requestId: string; expectedSnapshotId: string; boundActionId: string }, options: PublicTransportOptions): Promise<PlayerEnvironmentReceipt> {
    const credentials = await this.controller.credentials(); options.signal?.throwIfAborted();
    return (await this.client.submit({ ...input, ...credentials }, options)).data;
  }
  controlStatus(): "held" | "released" | "unknown" {
    const s = this.controller.snapshot();
    return s.controller_release_uncertain || s.controller_acquire_uncertain ? "unknown" : s.controller_lease_id === null ? "released" : "held";
  }
  release(): Promise<void> { return this.controller.releaseControl(); }
  async close(): Promise<void> { await this.release(); await this.controller.close(); }
}
