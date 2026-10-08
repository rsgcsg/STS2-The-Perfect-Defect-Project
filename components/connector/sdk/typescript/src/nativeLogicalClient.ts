import { type EnvironmentControllerSession } from "./controllerSession.js";
import { type JsonObject } from "./json.js";
import { type DecodedPlayerPayload } from "./protocol.js";
import {
  NATIVE_LOGICAL_PROFILE, NATIVE_LOGICAL_SCOPE, NATIVE_LOGICAL_MAX_PAGE_BYTES,
  decodeNativeLogicalCapabilities, decodeNativeLogicalAttach, decodeNativeLogicalCurrent,
  decodeNativeLogicalRead, decodeNativeLogicalCatalogPage, decodeNativeLogicalResolve,
  decodeNativeLogicalEvents, decodeNativeLogicalAwait, decodeNativeLogicalCancelWait,
  decodeNativeLogicalDetach, decodeNativeLogicalRenew, decodeNativeLogicalRetain,
  decodeNativeLogicalRelease, decodeNativeLogicalResult, validateNativeLogicalScope,
  validateNativeLogicalPrefix, validateNativeLogicalExpression, validateNativeLogicalRequest,
  isNativeLogicalPendingLookup, type NativeLogicalResult,
  type NativeLogicalTransport, type NativeLogicalTransportOperation, type NativeLogicalTransportOptions,
  type NativeLogicalCapabilities, type NativeLogicalSubscription, type NativeLogicalScopeField,
  type NativeLogicalPrefix, type NativeLogicalExpression, type NativeLogicalEventAvailability,
  type NativeLogicalCapture
} from "./nativeLogical.js";
import { assembleNativeLogicalCapture, assembleNativeLogicalObservation, type NativeLogicalAssemblyInput, type NativeLogicalFullCapture,
  type NativeLogicalScopedAssemblyInput, type NativeLogicalCapturedObservation,
  type NativeLogicalByteBudget } from "./nativeLogicalAssembly.js";

export interface NativeLogicalAttachInput {
  eagerScope: readonly NativeLogicalScopeField[];
  requiredSeams: NativeLogicalCapabilities["capture_coverage"];
  deliveryMode: "full_reference" | "scoped";
  signal?: AbortSignal;
}
export interface NativeLogicalAwaitInput {
  waitId: string;
  afterCursor: string;
  condition: "any_event" | "observation" | "catalog_nonempty" | "terminal";
  timeoutMs: number;
  /** Explicitly opt in to the existing controller owner; passive waits use null. */
  controlDependent?: boolean;
  signal?: AbortSignal;
}
export type NativeLogicalResultLookup = { status: "pending"; requestId: string } |
  { status: "terminal"; result: DecodedPlayerPayload<NativeLogicalResult> };

/** Thin client over the one registered Player Environment session. Scope and
 * event acknowledgements are caller-owned; this class never advances Model memory. */
export class NativeLogicalSession {
  readonly #environment: NativeLogicalTransport;
  readonly #controller: EnvironmentControllerSession;
  #capabilities?: NativeLogicalCapabilities;
  #subscription?: NativeLogicalSubscription;

  constructor(environment: NativeLogicalTransport, controller: EnvironmentControllerSession) {
    this.#environment = environment;
    this.#controller = controller;
  }

  get subscription(): NativeLogicalSubscription | undefined { return this.#subscription; }

  async capabilities(signal?: AbortSignal) {
    const reply = await this.call("capabilities", undefined, decodeNativeLogicalCapabilities, { signal, maxResponseBytes: 1024 * 1024 });
    const identity = this.#controller.clientIdentity();
    if (identity && reply.data.session.runtime_instance_id !== identity.runtimeInstanceId)
      throw new Error("native logical capabilities do not match the existing registered runtime");
    if (this.#subscription && this.#subscription.stream_generation !== reply.data.stream_generation)
      throw new Error("native logical generation changed; the caller must reconcile and reattach");
    this.#capabilities = reply.data;
    return reply;
  }

  async attach(input: NativeLogicalAttachInput) {
    if (this.#subscription) throw new Error("native logical session already has a subscription; detach before changing scope");
    validateNativeLogicalScope(input.eagerScope);
    const capabilities = await this.negotiated(input.signal);
    const reply = await this.call("attach", { client_session_id: this.identity().clientSessionId,
      eager_scope: [...input.eagerScope], required_seams: input.requiredSeams, delivery_mode: input.deliveryMode },
    decodeNativeLogicalAttach, { signal: input.signal, maxResponseBytes: 1024 * 1024 });
    if (reply.data.subscription) {
      const accepted = reply.data.subscription;
      const advertised = input.requiredSeams.map(required => capabilities.capture_coverage.find(value =>
        value.source_seam === required.source_seam && value.version === required.version));
      if (accepted.stream_generation !== capabilities.stream_generation || accepted.delivery_mode !== input.deliveryMode ||
          JSON.stringify(accepted.eager_scope) !== JSON.stringify(input.eagerScope) ||
          advertised.some(value => value === undefined) || JSON.stringify(accepted.coverage) !== JSON.stringify(advertised))
        throw new Error("native logical attachment changed the requested scope, coverage or generation");
      this.#subscription = accepted;
    }
    return reply;
  }

  async current(input: { eagerScope?: readonly NativeLogicalScopeField[]; expectedSnapshotId?: string | null;
    signal?: AbortSignal } = {}) {
    const eagerScope = input.eagerScope ?? NATIVE_LOGICAL_SCOPE;
    validateNativeLogicalScope(eagerScope);
    await this.negotiated(input.signal);
    const reply = await this.call("current", { client_session_id: this.identity().clientSessionId,
      eager_scope: [...eagerScope], expected_snapshot_id: input.expectedSnapshotId ?? null },
    decodeNativeLogicalCurrent, { signal: input.signal, maxResponseBytes: 1024 * 1024 });
    if (reply.data.capture) {
      this.checkCapture(reply.data.capture);
      if (input.expectedSnapshotId != null && reply.data.capture.snapshot_id !== input.expectedSnapshotId)
        throw new Error("native logical Current returned a different requested snapshot");
    }
    return reply;
  }

  async read(input: { captureId: string; cursor: string; maxBytes?: number; signal?: AbortSignal }) {
    const ceiling = Math.min(1024 * 1024, this.#capabilities?.limits.max_read_bytes ?? 1024 * 1024);
    const maxBytes = input.maxBytes ?? ceiling;
    if (!Number.isInteger(maxBytes) || maxBytes <= 0 || maxBytes > ceiling) throw new Error("native logical read budget is outside profile limits");
    const reply = await this.call("read", { capture_id: input.captureId, cursor: input.cursor, max_bytes: maxBytes },
      decodeNativeLogicalRead, { signal: input.signal, maxResponseBytes: Math.min(2 * 1024 * 1024,
        this.#capabilities?.limits.max_encoded_read_bytes ?? 2 * 1024 * 1024) });
    if (reply.data.capture_id !== input.captureId) throw new Error("native logical read changed capture ID");
    return reply;
  }

  async list(input: { catalogRef: string; streamGeneration: string; prefix?: NativeLogicalPrefix | null;
    cursor?: string | null; limit?: number; maxPageBytes?: number; signal?: AbortSignal }) {
    if (input.prefix != null) validateNativeLogicalPrefix(input.prefix);
    const responseCeiling = Math.min(NATIVE_LOGICAL_MAX_PAGE_BYTES, this.#capabilities?.limits.max_page_bytes ?? NATIVE_LOGICAL_MAX_PAGE_BYTES);
    const actionCeiling = Math.min(65536, this.#capabilities?.limits.max_actions ?? 65536);
    const limit = input.limit ?? Math.min(100, actionCeiling);
    const maxPageBytes = input.maxPageBytes ?? responseCeiling;
    if (!Number.isInteger(limit) || limit <= 0 || limit > actionCeiling || !Number.isInteger(maxPageBytes) ||
        maxPageBytes <= 0 || maxPageBytes > responseCeiling) throw new Error("native logical catalog limits are invalid");
    const reply = await this.call("catalog", { catalog_ref: input.catalogRef, stream_generation: input.streamGeneration,
      prefix: input.prefix ?? null, cursor: input.cursor ?? null, limit, max_page_bytes: maxPageBytes },
    decodeNativeLogicalCatalogPage, { signal: input.signal, maxResponseBytes: responseCeiling });
    if (reply.data.catalog_ref !== input.catalogRef || reply.data.stream_generation !== input.streamGeneration)
      throw new Error("native logical catalog changed reference or generation");
    if (reply.data.status === "complete" && reply.encodedByteCount > maxPageBytes ||
        reply.data.status === "page_budget_too_small" && reply.data.minimum_required_bytes! <= maxPageBytes)
      throw new Error("native logical page disposition disagrees with the requested byte budget");
    return reply;
  }

  async resolve(input: { catalogRef: string; streamGeneration: string; expression: NativeLogicalExpression; signal?: AbortSignal }) {
    validateNativeLogicalExpression(input.expression);
    const reply = await this.call("resolve", { catalog_ref: input.catalogRef, stream_generation: input.streamGeneration,
      expression: input.expression }, decodeNativeLogicalResolve, { signal: input.signal, maxResponseBytes: 1024 * 1024 });
    if (reply.data.action && (reply.data.action.verb !== input.expression.verb ||
        reply.data.action.subject_referent_id !== input.expression.subject_referent_id ||
        JSON.stringify(reply.data.action.arguments) !== JSON.stringify(input.expression.arguments)))
      throw new Error("native logical resolved member does not match the complete structural expression");
    return reply;
  }

  async events(input: { afterCursor: string; limit?: number; signal?: AbortSignal }) {
    const subscription = this.attached();
    const ceiling = Math.min(2048, this.#capabilities?.limits.max_events ?? 2048);
    const limit = input.limit ?? Math.min(100, ceiling);
    if (!Number.isInteger(limit) || limit <= 0 || limit > ceiling) throw new Error("native logical event limit is invalid");
    const reply = await this.call("events", { ...this.subscriber(subscription), after_cursor: input.afterCursor, limit },
      decodeNativeLogicalEvents, { signal: input.signal });
    for (const item of reply.data.events) this.checkEvent(item, subscription);
    return reply; // Repeated retrieval neither rewrites events nor advances an acknowledgement.
  }

  async await(input: NativeLogicalAwaitInput) {
    const subscription = this.attached();
    if (!/^[0-9a-f]{32}$/u.test(input.waitId) || !Number.isInteger(input.timeoutMs) || input.timeoutMs < 0 ||
        input.timeoutMs > Math.min(30000, this.#capabilities?.limits.max_wait_ms ?? 30000) ||
        !["any_event", "observation", "catalog_nonempty", "terminal"].includes(input.condition))
      throw new Error("native logical Await condition, wait ID or timeout is invalid");
    input.signal?.throwIfAborted();
    const credentials = input.controlDependent ? await this.#controller.credentials() : null;
    input.signal?.throwIfAborted();
    const body: JsonObject = { ...this.subscriber(subscription), wait_id: input.waitId, after_cursor: input.afterCursor,
      condition: input.condition, timeout_ms: input.timeoutMs, control_binding: credentials === null ? null : {
        controller_lease_id: credentials.controllerLeaseId, controller_generation: credentials.controllerGeneration } };
    try {
      const reply = await this.call("await", body, decodeNativeLogicalAwait,
        { signal: input.signal, timeoutMs: input.timeoutMs + 1000, maxResponseBytes: 2 * 1024 * 1024 });
      if (reply.data.event) this.checkEvent(reply.data.event, subscription);
      return reply;
    } catch (error) {
      // Cancellation is an owned query cleanup, never a gameplay retry. Its
      // failure remains explicit rather than pretending the waiter was cancelled.
      try { await this.cancelFor(subscription, input.waitId); }
      catch (cleanup) { throw new AggregateError([error, cleanup], "native logical Await and cancellation cleanup failed"); }
      throw error;
    }
  }

  async cancelWait(waitId: string) { return this.cancelFor(this.attached(), waitId); }

  async detach() {
    const subscription = this.attached();
    const reply = await this.call("detach", { client_session_id: this.identity().clientSessionId,
      subscription_id: subscription.subscription_id }, decodeNativeLogicalDetach, { maxResponseBytes: 1024 * 1024 });
    if (reply.data.subscription_id !== subscription.subscription_id) throw new Error("native logical detach changed subscription ID");
    this.#subscription = undefined;
    return reply;
  }

  async renew(afterCursor: string, signal?: AbortSignal) {
    const subscription = this.attached();
    const reply = await this.call("renew", { ...this.subscriber(subscription), after_cursor: afterCursor },
      decodeNativeLogicalRenew, { signal, maxResponseBytes: 1024 * 1024 });
    if (reply.data.subscription) {
      const renewed = reply.data.subscription;
      const originalIdentity = { ...subscription, expires_at: null };
      const renewedIdentity = { ...renewed, expires_at: null };
      if (JSON.stringify(originalIdentity) !== JSON.stringify(renewedIdentity))
        throw new Error("native logical renewal changed scope, generation, history or subscription identity");
      this.#subscription = renewed;
    }
    return reply;
  }

  async retain(captureId: string, signal?: AbortSignal) {
    const reply = await this.call("retain", { client_session_id: this.identity().clientSessionId, capture_id: captureId },
      decodeNativeLogicalRetain, { signal, maxResponseBytes: 1024 * 1024 });
    if (reply.data.retention) {
      if (reply.data.retention.capture.capture_id !== captureId) throw new Error("native logical retain changed capture ID");
      this.checkCapture(reply.data.retention.capture);
    }
    return reply;
  }

  async release(retentionHandleId: string) {
    const reply = await this.call("release", { client_session_id: this.identity().clientSessionId,
      retention_handle_id: retentionHandleId }, decodeNativeLogicalRelease, { maxResponseBytes: 1024 * 1024 });
    if (reply.data.retention_handle_id !== retentionHandleId) throw new Error("native logical release changed owned handle ID");
    return reply;
  }

  async submit(input: { requestId: string; expectedSnapshotId: string; actionId: string; signal?: AbortSignal }): Promise<NativeLogicalResultLookup> {
    await this.negotiated(input.signal);
    input.signal?.throwIfAborted();
    const credentials = await this.#controller.credentials();
    input.signal?.throwIfAborted();
    const response = await this.#environment.nativeLogicalRequest("submit", { request_id: input.requestId, expected_snapshot_id: input.expectedSnapshotId,
      bound_action_id: input.actionId, client_session_id: credentials.clientSessionId,
      controller_lease_id: credentials.controllerLeaseId, controller_generation: credentials.controllerGeneration,
      input_profile: NATIVE_LOGICAL_PROFILE }, { signal: input.signal, maxResponseBytes: 2 * 1024 * 1024 });
    if (response.statusCode === 202 && isNativeLogicalPendingLookup(response.raw))
      return { status: "pending", requestId: input.requestId };
    const reply = decodeNativeLogicalResult(response.raw);
    if (reply.data.request_id !== input.requestId || reply.data.snapshot_id !== input.expectedSnapshotId ||
        reply.data.action !== null && reply.data.action.action_id !== input.actionId)
      throw new Error("native logical submit returned a different request basis");
    this.checkAttribution(reply.data.attribution);
    return { status: "terminal", result: reply }; // Never retry, poll, reinterpret partial input or infer an effect.
  }

  async result(requestId: string, signal?: AbortSignal): Promise<NativeLogicalResultLookup> {
    this.identity();
    const response = await this.#environment.nativeLogicalRequest("result", { request_id: requestId },
      { signal, maxResponseBytes: 2 * 1024 * 1024 });
    if (response.statusCode === 202 && isNativeLogicalPendingLookup(response.raw))
      return { status: "pending", requestId };
    const reply = decodeNativeLogicalResult(response.raw);
    if (reply.data.request_id !== requestId) throw new Error("native logical result changed original request ID");
    this.checkAttribution(reply.data.attribution);
    return { status: "terminal", result: reply };
  }

  async getFull(input: NativeLogicalAssemblyInput): Promise<NativeLogicalFullCapture> {
    const capabilities = await this.admitAssembly(input);
    return assembleNativeLogicalCapture({ ...input,
      chunkBytes: input.chunkBytes ?? Math.min(1024 * 1024, capabilities.limits.max_read_bytes),
      pageLimit: input.pageLimit ?? Math.min(100, capabilities.limits.max_actions),
      maxPageBytes: input.maxPageBytes ?? Math.min(NATIVE_LOGICAL_MAX_PAGE_BYTES, capabilities.limits.max_page_bytes),
      maxCatalogResponseBytes: Math.min(NATIVE_LOGICAL_MAX_PAGE_BYTES, capabilities.limits.max_page_bytes) }, this);
  }

  async getCapture(input: NativeLogicalScopedAssemblyInput): Promise<NativeLogicalCapturedObservation> {
    const capabilities = await this.admitAssembly(input);
    return assembleNativeLogicalObservation({ ...input,
      chunkBytes: input.chunkBytes ?? Math.min(1024 * 1024, capabilities.limits.max_read_bytes) }, this);
  }

  async getCurrentCapture(input: { eagerScope: readonly NativeLogicalScopeField[]; expectedSnapshotId?: string | null;
    signal?: AbortSignal; budget?: NativeLogicalByteBudget; chunkBytes?: number }): Promise<NativeLogicalCapturedObservation> {
    const current = (await this.current(input)).data;
    if ((current.status !== "captured" && current.status !== "partial") || !current.capture || !current.context)
      throw new Error(`native logical Current has no coherent scoped capture: ${current.status}`);
    return this.getCapture({ ...input, capture: current.capture, context: current.context, retention: current.retention });
  }

  async getFullCurrent(input: { expectedSnapshotId?: string | null; signal?: AbortSignal; budget?: NativeLogicalByteBudget;
    chunkBytes?: number; pageLimit?: number; maxPageBytes?: number; maxActions?: number } = {}): Promise<NativeLogicalFullCapture> {
    const current = (await this.current({ eagerScope: NATIVE_LOGICAL_SCOPE, expectedSnapshotId: input.expectedSnapshotId, signal: input.signal })).data;
    if (current.status !== "captured" || !current.capture || !current.context) {
      if (current.retention) await this.release(current.retention.retention_handle_id);
      throw new Error(`native logical Current is not a full captured input: ${current.status}`);
    }
    return this.getFull({ ...input, capture: current.capture, context: current.context, retention: current.retention });
  }

  async getFullEvent(event: NativeLogicalEventAvailability, input: Omit<NativeLogicalAssemblyInput, "capture" | "context" | "retention"> = {}) {
    this.checkEvent(event, this.attached());
    if (event.availability !== "available" || !event.event.payload_reference)
      throw new Error(`native logical historical input is not available: ${event.availability}`);
    return this.getFull({ ...input, capture: event.event.payload_reference });
  }

  private identity() {
    const identity = this.#controller.clientIdentity();
    if (!identity) throw new Error("native logical access requires the existing Player Environment registration");
    if (this.#capabilities && identity.runtimeInstanceId !== this.#capabilities.session.runtime_instance_id)
      throw new Error("native logical access does not match the registered runtime");
    return identity;
  }
  private async admitAssembly(input: NativeLogicalAssemblyInput): Promise<NativeLogicalCapabilities> {
    try {
      const capabilities = await this.negotiated(input.signal);
      this.checkCapture(input.capture);
      return capabilities;
    } catch (error) {
      if (input.retention) {
        try { await this.release(input.retention.retention_handle_id); }
        catch (cleanup) { throw new AggregateError([error, cleanup], "native logical admission and owned retention cleanup failed"); }
      }
      throw error;
    }
  }
  private attached(): NativeLogicalSubscription {
    if (!this.#subscription) throw new Error("native logical session has no attached subscription");
    return this.#subscription;
  }
  private async negotiated(signal?: AbortSignal): Promise<NativeLogicalCapabilities> {
    if (!this.#capabilities) await this.capabilities(signal);
    this.identity();
    return this.#capabilities!;
  }
  private subscriber(subscription: NativeLogicalSubscription): JsonObject {
    return { client_session_id: this.identity().clientSessionId, subscription_id: subscription.subscription_id,
      scope_id: subscription.scope_id };
  }
  private checkCapture(capture: NativeLogicalCapture): void {
    const identity = this.identity();
    if (capture.session.runtime_instance_id !== identity.runtimeInstanceId ||
        this.#capabilities && (capture.session.environment_fingerprint !== this.#capabilities.session.environment_fingerprint ||
          capture.stream_generation !== this.#capabilities.stream_generation))
      throw new Error("native logical capture does not match negotiated session/generation");
  }
  private checkEvent(item: NativeLogicalEventAvailability, subscription: NativeLogicalSubscription): void {
    if (item.event.stream_generation !== subscription.stream_generation || item.event.scope_id !== subscription.scope_id)
      throw new Error("native logical event belongs to another accepted scope or generation");
    if (item.event.payload_reference) this.checkCapture(item.event.payload_reference);
  }
  private checkAttribution(attribution: { runtime_instance_id: string; client_session_id: string } | null): void {
    if (attribution && (attribution.runtime_instance_id !== this.identity().runtimeInstanceId ||
      attribution.client_session_id !== this.identity().clientSessionId)) throw new Error("native logical result belongs to another registered client");
  }
  private async cancelFor(subscription: NativeLogicalSubscription, waitId: string) {
    if (!/^[0-9a-f]{32}$/u.test(waitId)) throw new Error("native logical wait ID must be lowercase hexadecimal");
    const reply = await this.call("cancel_wait", { client_session_id: this.identity().clientSessionId,
      subscription_id: subscription.subscription_id, wait_id: waitId }, decodeNativeLogicalCancelWait, { maxResponseBytes: 1024 * 1024 });
    if (reply.data.subscription_id !== subscription.subscription_id || reply.data.wait_id !== waitId)
      throw new Error("native logical cancellation changed the owned waiter identity");
    return reply;
  }
  private async call<T>(operation: NativeLogicalTransportOperation, body: JsonObject | undefined,
    decode: (value: unknown) => DecodedPlayerPayload<T>, options: NativeLogicalTransportOptions = {}) {
    validateNativeLogicalRequest(operation, body);
    const response = await this.#environment.nativeLogicalRequest(operation, body, options);
    return { ...decode(response.raw), encodedByteCount: response.encodedByteCount };
  }
}
