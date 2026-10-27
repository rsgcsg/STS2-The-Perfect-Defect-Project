import { PUBLIC_BASIS_ROUTE, PUBLIC_BASIS_PROFILE, assemblePublicBasis,
  decodePublicBasisHandle, decodePublicBasisChunk, decodePublicBasisRelease, decodePublicBasisCapabilities,
  decodePublicBasisReceipt, type PublicBasisReceipt,
  type PublicBasisHandle, type PublicBasisChunk, type PublicBasisRelease, type FullPublicBasis, type PublicBasisCapabilities } from "./publicBasis.js";
import {
  SEALED_OBSERVATION_ROUTE, assembleSealedTextMenuV2, validateSealedChunkLimit,
  decodeSealedObservationCapabilities, decodeSealedObservationCapture,
  decodeSealedObservationChunk, decodeSealedObservationRelease,
  type SealedObservationCapabilities, type SealedObservationCapture,
  type SealedObservationChunk, type SealedObservationRelease, type FullTextMenuV2Observation
} from "./sealedObservation.js";
import { isJsonObject, type JsonObject } from "./json.js";
import { P1_CAPTURE_ROUTE, P1_CAPTURE_PROFILE, assembleP1Capture, decodeP1CaptureCapabilities,
  decodeP1CaptureHandle, decodeP1CaptureChunk, decodeP1CaptureRelease,
  type P1CaptureCapabilities, type P1CaptureHandle, type P1CaptureChunk, type P1CaptureRelease,
  type FullP1Capture } from "./p1Capture.js";
import { NATIVE_LOGICAL_PROFILE, NATIVE_LOGICAL_ROUTE, NATIVE_LOGICAL_MAX_RESPONSE_BYTES,
  parseNativeLogicalJson, type NativeLogicalTransportOperation, type NativeLogicalTransportOptions,
  type NativeLogicalTransportReply, validateNativeLogicalRequest, decodeNativeLogicalCurrent } from "./nativeLogical.js";
import { assertNativeLogicalJson } from "./nativeLogicalWire.js";
import { PLAYER_ENVIRONMENT_CLIENT_REVOCATION_ROUTE, PLAYER_ENVIRONMENT_CLIENT_REVOCATION_MAX_BYTES,
  validatePlayerClientRevocationRequest, decodePlayerClientRevocation,
  type PlayerEnvironmentClientRevocation } from "./clientRevocation.js";
import {
  ORDINARY_REWARD_PAGE_PROFILE,
  decodeRewardPageCapabilities,
  decodeRewardPageSnapshot,
  decodeRewardPageReceipt,
  type RewardPageCapabilities,
  type RewardPageSnapshot,
  type RewardPageReceipt
} from "./rewardPage.js";
import {
  REWARD_POTION_PAGE_PROFILE,
  decodeRewardPotionCapabilities,
  decodeRewardPotionSnapshot,
  decodeRewardPotionReceipt,
  type RewardPotionCapabilities,
  type RewardPotionSnapshot,
  type RewardPotionReceipt
} from "./rewardPotionPage.js";
import {
  TEXT_MENU_PROFILE, TEXT_MENU_RESULT_SCHEMA, decodeTextMenuCapabilities, decodeTextMenuSnapshot,
  decodeTextMenuActionResult, decodeTextMenuObservationContext,
  type TextMenuCapabilities, type TextMenuSnapshot,
  type TextMenuActionResult, type TextMenuObservationContext
} from "./textMenu.js";
import {
  TEXT_MENU_V2_PROFILE, TEXT_MENU_V2_RESULT_SCHEMA,
  decodeTextMenuV2Capabilities, decodeTextMenuV2Snapshot,
  decodeTextMenuV2ActionResult, decodeTextMenuV2ObservationContext,
  type TextMenuV2Capabilities, type TextMenuV2Snapshot,
  type TextMenuV2ActionResult, type TextMenuV2ObservationContext
} from "./textMenuV2.js";
import {
  PLAYER_ENVIRONMENT_CONTROL_ROUTE,
  decodePlayerControlSnapshot,
  type PlayerEnvironmentControlSnapshot,
  decodePlayerClientRegistration,
  decodePlayerCapabilities,
  decodePlayerControllerLeaseResponse,
  decodePlayerSnapshot,
  decodePlayerRead,
  decodePlayerReceipt,
  type DecodedPlayerPayload,
  type PlayerEnvironmentCapabilities,
  type PlayerEnvironmentClientRegistration,
  type PlayerEnvironmentControllerLeaseResponse,
  type PlayerEnvironmentSnapshot,
  type PlayerEnvironmentReadResponse,
  type PlayerEnvironmentReceipt
} from "./protocol.js";

export class PlayerEnvironmentHttpError extends Error {
  constructor(message: string, readonly statusCode?: number) {
    super(message);
    this.name = "PlayerEnvironmentHttpError";
  }
}

export interface PublicTransportOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  maxResponseBytes?: number;
  beforeSend?: () => Promise<void>;
  onResponseBytes?: (bytes: Uint8Array) => Promise<void> | void;
}

export class PlayerEnvironmentRestClient {
  constructor(
    private readonly baseUrl: string,
    private readonly timeoutMs: number,
    private readonly fetchImpl: typeof fetch = fetch
  ) {}

  async capabilities(options?: PublicTransportOptions): Promise<DecodedPlayerPayload<PlayerEnvironmentCapabilities>> {
    return decodePlayerCapabilities(await this.get("/api/player-environment/capabilities", options));
  }

  async rewardPageCapabilities(): Promise<DecodedPlayerPayload<RewardPageCapabilities>> {
    return decodeRewardPageCapabilities(await this.get(
      `/api/player-environment/capabilities?input_profile=${ORDINARY_REWARD_PAGE_PROFILE}`));
  }

  async rewardPotionCapabilities(): Promise<DecodedPlayerPayload<RewardPotionCapabilities>> {
    return decodeRewardPotionCapabilities(await this.get(
      `/api/player-environment/capabilities?input_profile=${REWARD_POTION_PAGE_PROFILE}`));
  }

  async textMenuCapabilities(): Promise<DecodedPlayerPayload<TextMenuCapabilities>> {
    return decodeTextMenuCapabilities(await this.get(
      `/api/player-environment/capabilities?input_profile=${TEXT_MENU_PROFILE}`));
  }

  async textMenuV2Capabilities(): Promise<DecodedPlayerPayload<TextMenuV2Capabilities>> {
    return decodeTextMenuV2Capabilities(await this.get(
      `/api/player-environment/capabilities?input_profile=${TEXT_MENU_V2_PROFILE}`));
  }

  async observe(): Promise<DecodedPlayerPayload<PlayerEnvironmentSnapshot>> {
    return decodePlayerSnapshot(await this.get("/api/player-environment/snapshot"));
  }

  async observeRewardPage(): Promise<DecodedPlayerPayload<RewardPageSnapshot>> {
    return decodeRewardPageSnapshot(await this.get(
      `/api/player-environment/snapshot?input_profile=${ORDINARY_REWARD_PAGE_PROFILE}`));
  }

  async observeRewardPotionPage(): Promise<DecodedPlayerPayload<RewardPotionSnapshot>> {
    return decodeRewardPotionSnapshot(await this.get(
      `/api/player-environment/snapshot?input_profile=${REWARD_POTION_PAGE_PROFILE}`));
  }

  async observeTextMenu(): Promise<DecodedPlayerPayload<TextMenuSnapshot>> {
    return decodeTextMenuSnapshot(await this.get(
      `/api/player-environment/snapshot?input_profile=${TEXT_MENU_PROFILE}`));
  }

  async observeTextMenuV2(): Promise<DecodedPlayerPayload<TextMenuV2Snapshot>> {
    return decodeTextMenuV2Snapshot(await this.get(
      `/api/player-environment/snapshot?input_profile=${TEXT_MENU_V2_PROFILE}`));
  }

  async observeTextMenuContext(): Promise<DecodedPlayerPayload<TextMenuObservationContext>> {
    return decodeTextMenuObservationContext(await this.get(
      "/api/player-environment/text-menu/observation-context"));
  }

  async observeTextMenuV2Context(): Promise<DecodedPlayerPayload<TextMenuV2ObservationContext>> {
    return decodeTextMenuV2ObservationContext(await this.get(
      `/api/player-environment/text-menu/observation-context?input_profile=${TEXT_MENU_V2_PROFILE}`));
  }

  async sealedObservationCapabilities(): Promise<DecodedPlayerPayload<SealedObservationCapabilities>> {
    return decodeSealedObservationCapabilities(await this.get(`${SEALED_OBSERVATION_ROUTE}/capabilities`));
  }

  async readCurrent(input: { expectedSnapshotId?: string } = {}): Promise<DecodedPlayerPayload<SealedObservationCapture>> {
    const query = new URLSearchParams({ input_profile: TEXT_MENU_V2_PROFILE });
    if (input.expectedSnapshotId !== undefined) query.set("expected_snapshot_id", input.expectedSnapshotId);
    const capture = decodeSealedObservationCapture(await this.get(`${SEALED_OBSERVATION_ROUTE}/current?${query}`));
    if (input.expectedSnapshotId !== undefined && capture.data.source_snapshot_id !== input.expectedSnapshotId)
      throw new PlayerEnvironmentHttpError("Sealed capture does not match the requested snapshot");
    return capture;
  }

  async readSealed(input: { captureId: string; cursor: string; maxBytes?: number }): Promise<DecodedPlayerPayload<SealedObservationChunk>> {
    if (input.maxBytes !== undefined) validateSealedChunkLimit(input.maxBytes);
    const query = new URLSearchParams({ capture_id: input.captureId, cursor: input.cursor });
    if (input.maxBytes !== undefined) query.set("max_bytes", String(input.maxBytes));
    return decodeSealedObservationChunk(await this.get(`${SEALED_OBSERVATION_ROUTE}/read?${query}`));
  }

  async releaseSealed(captureId: string): Promise<DecodedPlayerPayload<SealedObservationRelease>> {
    const result = decodeSealedObservationRelease(await this.post(`${SEALED_OBSERVATION_ROUTE}/release`, { capture_id: captureId }));
    if (result.data.capture_id !== captureId) throw new PlayerEnvironmentHttpError("Sealed release capture ID mismatch");
    return result;
  }

  async getFullTextMenuV2(input: { expectedSnapshotId?: string; maxBytes?: number } = {}): Promise<FullTextMenuV2Observation> {
    if (input.maxBytes !== undefined) validateSealedChunkLimit(input.maxBytes);
    const capture = (await this.readCurrent(input)).data;
    try {
      return await assembleSealedTextMenuV2(capture, this, input.maxBytes);
    } finally {
      // Idempotent best-effort retention cleanup cannot invalidate already verified bytes
      // or replace the original acquisition failure. TTL still bounds failed cleanup.
      await this.releaseSealed(capture.capture_id).catch(() => undefined);
    }
  }

  async read(readId: string, expectedSnapshotId: string): Promise<DecodedPlayerPayload<PlayerEnvironmentReadResponse>> {
    const encodedRead = encodeURIComponent(readId);
    const encodedSnapshot = encodeURIComponent(expectedSnapshotId);
    return decodePlayerRead(await this.get(`/api/player-environment/reads/${encodedRead}?expected_snapshot_id=${encodedSnapshot}`));
  }

  async p1CaptureCapabilities(): Promise<DecodedPlayerPayload<P1CaptureCapabilities>> {
    return decodeP1CaptureCapabilities(await this.get(`${P1_CAPTURE_ROUTE}/capabilities`));
  }

  async readCurrentP1Capture(input: { expectedSnapshotId?: string } = {}): Promise<DecodedPlayerPayload<P1CaptureHandle>> {
    const query = new URLSearchParams({ input_profile: P1_CAPTURE_PROFILE });
    if (input.expectedSnapshotId !== undefined) query.set("expected_snapshot_id", input.expectedSnapshotId);
    const capture = decodeP1CaptureHandle(await this.get(`${P1_CAPTURE_ROUTE}/current?${query}`));
    if (input.expectedSnapshotId !== undefined && capture.data.source_snapshot_id !== input.expectedSnapshotId)
      throw new PlayerEnvironmentHttpError("P1 capture does not match the requested snapshot");
    return capture;
  }

  async readP1Capture(input: { captureId: string; cursor: string; maxBytes?: number }): Promise<DecodedPlayerPayload<P1CaptureChunk>> {
    if (input.maxBytes !== undefined) validateSealedChunkLimit(input.maxBytes);
    const query = new URLSearchParams({ capture_id: input.captureId, cursor: input.cursor });
    if (input.maxBytes !== undefined) query.set("max_bytes", String(input.maxBytes));
    return decodeP1CaptureChunk(await this.get(`${P1_CAPTURE_ROUTE}/read?${query}`));
  }

  async releaseP1Capture(captureId: string): Promise<DecodedPlayerPayload<P1CaptureRelease>> {
    const result = decodeP1CaptureRelease(await this.post(`${P1_CAPTURE_ROUTE}/release`, { capture_id: captureId }));
    if (result.data.capture_id !== captureId) throw new PlayerEnvironmentHttpError("P1 release capture ID mismatch");
    return result;
  }

  async getFullP1Capture(input: { expectedSnapshotId?: string; maxBytes?: number } = {}): Promise<FullP1Capture> {
    if (input.maxBytes !== undefined) validateSealedChunkLimit(input.maxBytes);
    const capture = (await this.readCurrentP1Capture(input)).data;
    try { return await assembleP1Capture(capture, this, input.maxBytes); }
    finally { await this.releaseP1Capture(capture.capture_id).catch(() => undefined); }
  }

  async publicBasisCapabilities(options?: PublicTransportOptions): Promise<PublicBasisCapabilities> {
    return decodePublicBasisCapabilities(await this.get(`${PUBLIC_BASIS_ROUTE}/capabilities`, options));
  }
  async readCurrentPublicBasis(input: { expectedSnapshotId?: string; settledRelicCounters?: boolean } = {}, options?: PublicTransportOptions): Promise<PublicBasisHandle> {
    const query = new URLSearchParams({ input_profile: PUBLIC_BASIS_PROFILE });
    if (input.settledRelicCounters === true) query.set("settled_relic_counters", "1");
    if (input.expectedSnapshotId !== undefined) query.set("expected_snapshot_id", input.expectedSnapshotId);
    const capture = decodePublicBasisHandle(await this.get(`${PUBLIC_BASIS_ROUTE}/current?${query}`, options));
    if (input.expectedSnapshotId !== undefined && capture.source_snapshot_id !== input.expectedSnapshotId)
      throw new PlayerEnvironmentHttpError("Public basis does not match requested snapshot");
    return capture;
  }
  async readPublicBasis(input: { captureId: string; cursor: string; maxBytes?: number }, options?: PublicTransportOptions): Promise<PublicBasisChunk> {
    if (input.maxBytes !== undefined) validateSealedChunkLimit(input.maxBytes);
    const query = new URLSearchParams({ capture_id: input.captureId, cursor: input.cursor });
    if (input.maxBytes !== undefined) query.set("max_bytes", String(input.maxBytes));
    return decodePublicBasisChunk(await this.get(`${PUBLIC_BASIS_ROUTE}/read?${query}`, options));
  }
  async releasePublicBasis(captureId: string, options?: PublicTransportOptions): Promise<PublicBasisRelease> {
    const release = decodePublicBasisRelease(await this.post(`${PUBLIC_BASIS_ROUTE}/release`, { capture_id: captureId }, false, options));
    if (release.capture_id !== captureId) throw new PlayerEnvironmentHttpError("Public basis release identity mismatch");
    return release;
  }
  async getFullPublicBasis(input: { expectedSnapshotId?: string; maxBytes?: number; settledRelicCounters?: boolean } = {}, options?: PublicTransportOptions): Promise<FullPublicBasis> {
    if (input.maxBytes !== undefined) validateSealedChunkLimit(input.maxBytes);
    const capture = await this.readCurrentPublicBasis(input, options);
    let failure: unknown; let failed = false; let full: FullPublicBasis | undefined;
    try {
      full = await assemblePublicBasis(capture, request => this.readPublicBasis(request, options), input.maxBytes);
      if (input.settledRelicCounters === true && full.basis.revision !== "3")
        throw new PlayerEnvironmentHttpError("Requested settled relic witness requires public basis revision 3");
    }
    catch (error) { failure = error; failed = true; }
    // Cleanup remains bounded and independent of revoked action authorization.
    try { await this.releasePublicBasis(capture.capture_id, { timeoutMs: this.timeoutMs }); }
    catch (cleanup) { throw new AggregateError(failed ? [failure, cleanup] : [cleanup], "Public basis retention cleanup failed"); }
    if (failed) throw failure;
    return full!;
  }

  async submit(input: {
    requestId: string;
    expectedSnapshotId: string;
    boundActionId: string;
    clientSessionId: string;
    controllerLeaseId: string;
    controllerGeneration: number;
  }, options?: PublicTransportOptions): Promise<DecodedPlayerPayload<PlayerEnvironmentReceipt>> {
    return decodePlayerReceipt(await this.post("/api/player-environment/actions", {
      request_id: input.requestId,
      expected_snapshot_id: input.expectedSnapshotId,
      bound_action_id: input.boundActionId,
      client_session_id: input.clientSessionId,
      controller_lease_id: input.controllerLeaseId,
      controller_generation: input.controllerGeneration
    }, true, options));
  }

  async submitPublicBasis(input: {
    requestId: string; expectedSnapshotId: string; boundActionId: string;
    clientSessionId: string; controllerLeaseId: string; controllerGeneration: number;
  }, options?: PublicTransportOptions): Promise<DecodedPlayerPayload<PublicBasisReceipt>> {
    return decodePublicBasisReceipt(await this.post("/api/player-environment/actions", {
      request_id: input.requestId, expected_snapshot_id: input.expectedSnapshotId, bound_action_id: input.boundActionId,
      client_session_id: input.clientSessionId, controller_lease_id: input.controllerLeaseId,
      controller_generation: input.controllerGeneration, input_profile: PUBLIC_BASIS_PROFILE
    }, true, options));
  }

  async submitRewardPage(input: {
    requestId: string;
    expectedSnapshotId: string;
    boundActionId: string;
    clientSessionId: string;
    controllerLeaseId: string;
    controllerGeneration: number;
  }): Promise<DecodedPlayerPayload<RewardPageReceipt>> {
    return decodeRewardPageReceipt(await this.post("/api/player-environment/actions", {
      request_id: input.requestId,
      expected_snapshot_id: input.expectedSnapshotId,
      bound_action_id: input.boundActionId,
      client_session_id: input.clientSessionId,
      controller_lease_id: input.controllerLeaseId,
      controller_generation: input.controllerGeneration,
      input_profile: ORDINARY_REWARD_PAGE_PROFILE
    }, true));
  }

  async submitRewardPotionPage(input: {
    requestId: string;
    expectedSnapshotId: string;
    boundActionId: string;
    clientSessionId: string;
    controllerLeaseId: string;
    controllerGeneration: number;
  }): Promise<DecodedPlayerPayload<RewardPotionReceipt>> {
    return decodeRewardPotionReceipt(await this.post("/api/player-environment/actions", {
      request_id: input.requestId,
      expected_snapshot_id: input.expectedSnapshotId,
      bound_action_id: input.boundActionId,
      client_session_id: input.clientSessionId,
      controller_lease_id: input.controllerLeaseId,
      controller_generation: input.controllerGeneration,
      input_profile: REWARD_POTION_PAGE_PROFILE
    }, true));
  }

  async submitTextMenu(input: {
    requestId: string;
    expectedSnapshotId: string;
    boundActionId: string;
    clientSessionId: string;
    controllerLeaseId: string;
    controllerGeneration: number;
  }): Promise<DecodedPlayerPayload<TextMenuActionResult>> {
    return decodeTextMenuActionResult(await this.post("/api/player-environment/actions", {
      request_id: input.requestId,
      expected_snapshot_id: input.expectedSnapshotId,
      bound_action_id: input.boundActionId,
      client_session_id: input.clientSessionId,
      controller_lease_id: input.controllerLeaseId,
      controller_generation: input.controllerGeneration,
      input_profile: TEXT_MENU_PROFILE
    }, true));
  }

  async submitTextMenuV2(input: {
    requestId: string;
    expectedSnapshotId: string;
    boundActionId: string;
    clientSessionId: string;
    controllerLeaseId: string;
    controllerGeneration: number;
  }, previous?: TextMenuV2Snapshot): Promise<DecodedPlayerPayload<TextMenuV2ActionResult>> {
    return decodeTextMenuV2ActionResult(await this.post("/api/player-environment/actions", {
      request_id: input.requestId,
      expected_snapshot_id: input.expectedSnapshotId,
      bound_action_id: input.boundActionId,
      client_session_id: input.clientSessionId,
      controller_lease_id: input.controllerLeaseId,
      controller_generation: input.controllerGeneration,
      input_profile: TEXT_MENU_V2_PROFILE
    }, true), previous);
  }

  async poll(requestId: string): Promise<DecodedPlayerPayload<PlayerEnvironmentReceipt>> {
    return decodePlayerReceipt(await this.get(`/api/player-environment/actions/${encodeURIComponent(requestId)}`));
  }

  async pollPublicBasis(requestId: string): Promise<DecodedPlayerPayload<PublicBasisReceipt>> {
    return decodePublicBasisReceipt(await this.get(
      `/api/player-environment/actions/${encodeURIComponent(requestId)}?input_profile=${PUBLIC_BASIS_PROFILE}`));
  }

  async pollRewardPage(requestId: string): Promise<DecodedPlayerPayload<RewardPageReceipt>> {
    return decodeRewardPageReceipt(await this.get(
      `/api/player-environment/actions/${encodeURIComponent(requestId)}?input_profile=${ORDINARY_REWARD_PAGE_PROFILE}`));
  }

  async pollRewardPotionPage(requestId: string): Promise<DecodedPlayerPayload<RewardPotionReceipt>> {
    return decodeRewardPotionReceipt(await this.get(
      `/api/player-environment/actions/${encodeURIComponent(requestId)}?input_profile=${REWARD_POTION_PAGE_PROFILE}`));
  }

  async textMenuResult(requestId: string): Promise<DecodedPlayerPayload<TextMenuActionResult>> {
    return decodeTextMenuActionResult(await this.get(
      `/api/player-environment/actions/${encodeURIComponent(requestId)}?input_profile=${TEXT_MENU_PROFILE}`));
  }

  async textMenuV2Result(requestId: string): Promise<DecodedPlayerPayload<TextMenuV2ActionResult>> {
    return decodeTextMenuV2ActionResult(await this.get(
      `/api/player-environment/actions/${encodeURIComponent(requestId)}?input_profile=${TEXT_MENU_V2_PROFILE}`));
  }

  /** Read current ownership; omitted/null controller means no held lease. */
  async controlSnapshot(): Promise<DecodedPlayerPayload<PlayerEnvironmentControlSnapshot>> {
    return decodePlayerControlSnapshot(await this.get(PLAYER_ENVIRONMENT_CONTROL_ROUTE));
  }

  async registerClient(input: {
    clientInstanceId: string; productId: string; productName: string; productVersion: string;
  }): Promise<DecodedPlayerPayload<PlayerEnvironmentClientRegistration>> {
    return decodePlayerClientRegistration(await this.post("/api/player-environment/clients/register", {
      client_instance_id: input.clientInstanceId,
      product_id: input.productId,
      product_name: input.productName,
      product_version: input.productVersion
    }));
  }

  async acquireController(clientSessionId: string): Promise<DecodedPlayerPayload<PlayerEnvironmentControllerLeaseResponse>> {
    return decodePlayerControllerLeaseResponse(await this.post("/api/player-environment/controller/acquire", {
      client_session_id: clientSessionId
    }));
  }

  async renewController(input: { clientSessionId: string; controllerLeaseId: string; controllerGeneration: number }): Promise<DecodedPlayerPayload<PlayerEnvironmentControllerLeaseResponse>> {
    return decodePlayerControllerLeaseResponse(await this.post("/api/player-environment/controller/renew", {
      client_session_id: input.clientSessionId,
      controller_lease_id: input.controllerLeaseId,
      controller_generation: input.controllerGeneration
    }));
  }

  async releaseController(input: { clientSessionId: string; controllerLeaseId: string; controllerGeneration: number }): Promise<DecodedPlayerPayload<PlayerEnvironmentControllerLeaseResponse>> {
    return decodePlayerControllerLeaseResponse(await this.post("/api/player-environment/controller/release", {
      client_session_id: input.clientSessionId,
      controller_lease_id: input.controllerLeaseId,
      controller_generation: input.controllerGeneration
    }));
  }

  async revokeClient(input: { runtimeInstanceId: string; clientSessionId: string }): Promise<DecodedPlayerPayload<PlayerEnvironmentClientRevocation>> {
    const body = validatePlayerClientRevocationRequest({ runtime_instance_id: input.runtimeInstanceId, client_session_id: input.clientSessionId });
    const originalRuntimeId = body.runtime_instance_id;
    const originalClientId = body.client_session_id;
    let status = 0;
    const raw = await this.request(PLAYER_ENVIRONMENT_CLIENT_REVOCATION_ROUTE,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }, false,
      { maxResponseBytes: PLAYER_ENVIRONMENT_CLIENT_REVOCATION_MAX_BYTES, onResponseBytes() {}, onResponseStatus(value) { status = value; } });
    if (status !== 200) throw new PlayerEnvironmentHttpError("Client revocation requires an exact HTTP 200 acknowledgement", status);
    const reply = decodePlayerClientRevocation(raw);
    if (reply.data.runtime_instance_id !== originalRuntimeId || reply.data.client_session_id !== originalClientId)
      throw new Error("Client revocation acknowledgement does not match the original runtime/client");
    return reply;
  }

  /** The native facade shares this client's fetch/error boundary and never owns a lease. */
  async nativeLogicalRequest(operation: NativeLogicalTransportOperation, body?: JsonObject,
    options: NativeLogicalTransportOptions = {}): Promise<NativeLogicalTransportReply> {
    const operations: readonly string[] = ["capabilities", "attach", "current", "current_owned", "read", "catalog", "resolve", "events",
      "await", "cancel_wait", "detach", "renew", "retain", "release", "submit", "result"];
    if (!operations.includes(operation)) throw new Error("Unknown native logical operation");
    validateNativeLogicalRequest(operation, body);
    const maxResponseBytes = options.maxResponseBytes ?? NATIVE_LOGICAL_MAX_RESPONSE_BYTES;
    if (!Number.isSafeInteger(maxResponseBytes) || maxResponseBytes <= 0 || maxResponseBytes > NATIVE_LOGICAL_MAX_RESPONSE_BYTES)
      throw new Error("native logical response byte limit is outside client limits");
    if (options.timeoutMs !== undefined && (!Number.isSafeInteger(options.timeoutMs) || options.timeoutMs <= 0))
      throw new Error("native logical transport timeout must be positive");
    if (body !== undefined) assertNativeLogicalJson(body);
    let path = `${NATIVE_LOGICAL_ROUTE}/${operation}`;
    let init: RequestInit;
    if (operation === "capabilities") {
      if (body !== undefined) throw new Error("native logical capabilities has no request body");
      init = { method: "GET" };
    } else if (operation === "result") {
      if (!body || Object.keys(body).length !== 1 || typeof body.request_id !== "string")
        throw new Error("native logical result requires only its original request_id");
      path = `/api/player-environment/actions/${encodeURIComponent(body.request_id)}?input_profile=${NATIVE_LOGICAL_PROFILE}`;
      init = { method: "GET" };
    } else {
      if (!body) throw new Error("native logical operation requires its closed JSON request body");
      if (operation === "submit") path = "/api/player-environment/actions";
      const serialized = JSON.stringify(body);
      if (Buffer.byteLength(serialized, "utf8") > 1024 * 1024) throw new Error("native logical request exceeds client byte limit");
      init = { method: "POST", headers: { "Content-Type": "application/json" }, body: serialized };
    }
    let encodedByteCount = 0;
    let statusCode = 0;
    const raw = await this.request(path, init, operation === "submit" || operation === "result", { ...options, maxResponseBytes, operation,
      onResponseBytes: bytes => { encodedByteCount = bytes; }, onResponseStatus: status => { statusCode = status; } });
    return { raw, encodedByteCount, statusCode };
  }

  private async get(path: string, options?: PublicTransportOptions): Promise<JsonObject> {
    return this.request(path, { method: "GET" }, false, undefined, options);
  }

  private async post(
    path: string,
    body: JsonObject,
    acceptReceiptOnError = false,
    options?: PublicTransportOptions
  ): Promise<JsonObject> {
    return this.request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }, acceptReceiptOnError, undefined, options);
  }

  private async request(
    path: string,
    init: RequestInit,
    acceptReceiptOnError = false,
    native?: NativeLogicalTransportOptions & { operation?: NativeLogicalTransportOperation; maxResponseBytes: number; onResponseBytes(bytes: number): void; onResponseStatus(status: number): void },
    transport?: PublicTransportOptions
  ): Promise<JsonObject> {
    let response: Response;
    const scope = transport ?? native;
    const responseLimit = transport?.maxResponseBytes ?? native?.maxResponseBytes ?? 16 * 1024 * 1024;
    if (transport && (!Number.isSafeInteger(responseLimit) || responseLimit < 1 || responseLimit > 16 * 1024 * 1024))
      throw new PlayerEnvironmentHttpError("Invalid response byte budget");
    const requestSignal = scope?.signal ? AbortSignal.any([scope.signal, AbortSignal.timeout(scope.timeoutMs ?? this.timeoutMs)])
      : AbortSignal.timeout(scope?.timeoutMs ?? this.timeoutMs);
    requestSignal.throwIfAborted();
    if (transport?.beforeSend) await boundedHook(transport.beforeSend, requestSignal);
    requestSignal.throwIfAborted();
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        ...init,
        signal: requestSignal
      });
    } catch (error) {
      throw new PlayerEnvironmentHttpError(`Player Environment transport failed: ${safeMessage(error)}`);
    }
    const value: unknown = native || transport ? await readNativeLogicalResponse(response, responseLimit, requestSignal,
      native?.onResponseBytes ?? (() => undefined), transport?.onResponseBytes) : await response.json().catch(() => ({}));
    native?.onResponseStatus(response.status);
    const isReceipt = isJsonObject(value)
      && (value.schema === "sts2.player-environment/receipt-1"
        || value.schema === TEXT_MENU_RESULT_SCHEMA
        || value.schema === TEXT_MENU_V2_RESULT_SCHEMA
        || native !== undefined && value.schema === "sts2.player-environment/native-logical-result-1");
    const typedCurrentFailure = (native?.operation === "current" || native?.operation === "current_owned") && isNativeCurrentFailure(response.status, value);
    if (!response.ok && !(acceptReceiptOnError && isReceipt) && !typedCurrentFailure) {
      throw new PlayerEnvironmentHttpError(
        `Player Environment request failed with HTTP ${response.status}: ${safeMessage(value)}`,
        response.status
      );
    }
    if (typeof value !== "object" || value === null || Array.isArray(value)) {
      throw new PlayerEnvironmentHttpError("Player Environment response was not a JSON object");
    }
    return value as JsonObject;
  }
}

function isNativeCurrentFailure(status: number, value: unknown): boolean {
  if (status !== 409 && status !== 429) return false;
  try {
    const current = decodeNativeLogicalCurrent(value).data;
    return status === 429 ? current.status === "capacity_exceeded"
      : current.status === "stale" || current.status === "source_capture_incomplete" || current.status === "failed";
  } catch { return false; }
}

async function readNativeLogicalResponse(response: Response, maxBytes: number, signal: AbortSignal | undefined,
  observedBytes: (bytes: number) => void, originalBytes?: (bytes: Uint8Array) => Promise<void> | void): Promise<JsonObject> {
  const declared = response.headers.get("content-length");
  if (declared !== null && (!/^[0-9]+$/u.test(declared) || Number(declared) > maxBytes)) {
    await response.body?.cancel();
    throw new PlayerEnvironmentHttpError("native logical response exceeds its byte budget");
  }
  const reader = response.body?.getReader();
  if (!reader) throw new PlayerEnvironmentHttpError("native logical response has no JSON body");
  const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
  const blocks: Uint8Array[] = [];
  let bytes = 0;
  const aborted = () => { void reader.cancel(signal?.reason).catch(() => undefined); };
  signal?.addEventListener("abort", aborted, { once: true });
  try {
    for (;;) {
      signal?.throwIfAborted();
      const block = await reader.read();
      signal?.throwIfAborted();
      if (block.done) break;
      bytes += block.value.byteLength;
      if (bytes > maxBytes) throw new PlayerEnvironmentHttpError("native logical response exceeds its byte budget");
      blocks.push(block.value.slice());
    }
    const joined = new Uint8Array(bytes);
    let offset = 0; for (const block of blocks) { joined.set(block, offset); offset += block.length; }
    if (originalBytes) await boundedHook(() => originalBytes(joined.slice()), signal);
    signal?.throwIfAborted();
    const result = parseNativeLogicalJson(decoder.decode(joined));
    observedBytes(bytes);
    return result;
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  } finally {
    signal?.removeEventListener("abort", aborted);
    blocks.length = 0;
    reader.releaseLock();
  }
}

// A hung caller hook must not retain transport authorization or defer capture cleanup.
// Promise.race observes late rejection; no late completion can resume this request.
async function boundedHook(hook: () => void | Promise<void>, signal?: AbortSignal): Promise<void> {
  signal?.throwIfAborted();
  if (!signal) { await hook(); return; }
  let abort: () => void = () => undefined;
  const cancelled = new Promise<never>((_, reject) => {
    abort = () => reject(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
  });
  try {
    await Promise.race([cancelled, Promise.resolve().then(() => { signal.throwIfAborted(); return hook(); })]);
  } finally { signal.removeEventListener("abort", abort); }
}

function safeMessage(value: unknown): string {
  if (value instanceof Error) return value.message.slice(0, 500);
  try {
    return JSON.stringify(value).slice(0, 500);
  } catch {
    return String(value).slice(0, 500);
  }
}
