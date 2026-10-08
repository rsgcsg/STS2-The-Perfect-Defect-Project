import { z } from "zod";
import { isJsonObject, type JsonObject, type JsonValue } from "./json.js";
import { SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL, type DecodedPlayerPayload } from "./protocol.js";
import { assertNativeLogicalJson, assertNativeLogicalScalar, freezeNativeLogical } from "./nativeLogicalWire.js";
export { parseNativeLogicalJson, digestNativeLogicalActions } from "./nativeLogicalWire.js";

export const NATIVE_LOGICAL_PROFILE = "native-logical-v1" as const;
export const NATIVE_LOGICAL_ROUTE = "/api/player-environment/native-logical";
export const NATIVE_LOGICAL_SCOPE = ["persistent", "interaction", "referents", "catalog"] as const;
export const NATIVE_LOGICAL_MAX_CAPTURE_BYTES = 64 * 1024 * 1024;
export const NATIVE_LOGICAL_MAX_READ_BYTES = 1024 * 1024;
export const NATIVE_LOGICAL_MAX_PAGE_BYTES = 1024 * 1024;
export const NATIVE_LOGICAL_MAX_RESPONSE_BYTES = 64 * 1024 * 1024;
const tag = (name: string) => z.literal(`sts2.player-environment/native-logical-${name}-1`);
const profile = z.literal(NATIVE_LOGICAL_PROFILE);
const scalar = z.string().superRefine((value, ctx) => {
  try { assertNativeLogicalScalar(value); }
  catch { ctx.addIssue({ code: "custom", message: "string contains an unpaired surrogate" }); }
  if (Buffer.byteLength(value, "utf8") > 65536) ctx.addIssue({ code: "custom", message: "string exceeds max_field_bytes" });
});
const id = z.string().min(1).max(128);
const cursor = z.string().min(1).max(1024);
const digest = z.string().regex(/^[0-9a-f]{64}$/u);
const time = z.string().datetime({ offset: true });
const integer = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER);
const positive = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
const u64 = z.string().regex(/^(?:0|[1-9][0-9]*)$/u).refine(value => {
  try { return BigInt(value) <= 18446744073709551615n; } catch { return false; }
}, "source index exceeds U64");
const scope = z.array(z.enum(NATIVE_LOGICAL_SCOPE)).superRefine((fields, ctx) => {
  let previous = -1;
  for (const field of fields) {
    const index = NATIVE_LOGICAL_SCOPE.indexOf(field);
    if (index <= previous) ctx.addIssue({ code: "custom", message: "eager scope must be a canonical ordered duplicate-free subset" });
    previous = index;
  }
});
const session = z.object({ runtime_instance_id: id, environment_fingerprint: scalar }).strict();
const coverageValue = z.enum(["complete_at_seam", "sampled", "unsupported"]);
const coverage = z.object({ source_seam: scalar, version: scalar, coverage: coverageValue }).strict();
const coverageList = z.array(coverage).superRefine((values, ctx) => {
  if (new Set(values.map(value => value.source_seam)).size !== values.length)
    ctx.addIssue({ code: "custom", message: "duplicate source seam" });
});
const json = z.custom<JsonValue>(value => {
  try { assertNativeLogicalJson(value); return true; } catch { return false; }
}, "public content must be scalar JSON");
const requiredJson = json.refine(value => value !== null, "required public JSON node is null");
const content = z.object({ content_schema: scalar, content: requiredJson }).strict();
const referent = z.object({
  referent_id: scalar, role: scalar, kind: z.enum(["entity", "control"]), label: scalar.nullable(),
  state: z.object({ visible: z.boolean(), enabled: z.boolean().nullable(), selected: z.boolean().nullable(),
    focused: z.boolean().nullable(), observation_basis: z.literal("native_visible_fact") }).strict(),
  properties_schema: scalar.nullable(), properties: json.nullable()
}).strict().superRefine((value, ctx) => {
  if (value.properties !== null && value.properties_schema === null)
    ctx.addIssue({ code: "custom", message: "public properties require their schema" });
});
const interaction = z.object({
  interaction_id: scalar, kind: scalar, stage: scalar, prompt: scalar.nullable(), content_schema: scalar,
  content: z.object({ surface: requiredJson, context: requiredJson }).strict(),
  capabilities: z.array(z.object({ verb: scalar, subject_role: scalar.nullable(),
    arguments: z.array(z.object({ role: scalar, required: z.boolean() }).strict()),
    availability_basis: scalar }).strict())
}).strict();
const informationPolicy = z.object({ id: scalar, scope: scalar, includes_hidden_information: z.literal(false),
  unknown_field_behavior: scalar }).strict();
const argument = z.object({ role: scalar, referent_id: scalar }).strict();
const argumentsList = z.array(argument).superRefine((values, ctx) => {
  if (new Set(values.map(value => value.role)).size !== values.length)
    ctx.addIssue({ code: "custom", message: "duplicate argument role" });
});
export const nativeLogicalActionSchema = z.object({
  action_id: scalar, kind: z.literal("native_input"), verb: scalar, label: scalar,
  subject_referent_id: scalar.nullable(), arguments: argumentsList, effect_domain: scalar
}).strict();
const actionList = z.array(nativeLogicalActionSchema).max(65536).superRefine((values, ctx) => {
  if (new Set(values.map(value => value.action_id)).size !== values.length)
    ctx.addIssue({ code: "custom", message: "duplicate action ID" });
});
export const nativeLogicalPrefixSchema = z.object({ verb: scalar.optional(),
  subject_referent_id: scalar.nullable().optional(), arguments: argumentsList.optional() }).strict();
export const nativeLogicalExpressionSchema = z.object({ verb: scalar,
  subject_referent_id: scalar.nullable(), arguments: argumentsList }).strict();
const catalog = z.object({ catalog_ref: id, snapshot_id: id,
  status: z.enum(["complete", "not_captured"]), total_count: integer.nullable(), digest: digest.nullable(),
  ordering_semantics: scalar, access_methods: z.array(scalar), scope_id: id, stream_generation: id
}).strict().superRefine((value, ctx) => {
  if (value.status === "complete" && (value.total_count === null || value.digest === null) ||
      value.status === "not_captured" && (value.total_count !== null || value.digest !== null || value.access_methods.length !== 0))
    ctx.addIssue({ code: "custom", message: "catalog completeness fields disagree" });
});
const observationSchema = z.object({
  protocol_version: z.literal(SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL), schema: tag("observation"), input_profile: profile,
  snapshot_id: id, revision: positive, observed_at: time,
  status: z.enum(["interactive", "settling", "visible_unsupported", "observed", "terminal"]),
  persistent: content.nullable(), interaction: interaction.nullable(), referents: z.array(referent),
  completeness: z.object({ status: z.enum(["complete", "partial", "capacity_exceeded", "failed"]),
    included: scope, missing: z.array(scalar), full_reference_complete: z.boolean() }).strict(),
  session, information_policy: informationPolicy,
  owner_occurrence: z.object({ owner_id: scalar, occurrence_id: scalar, binding_revision: scalar,
    focus_referent_id: scalar.nullable(), focus_occurrence: scalar.nullable() }).strict(), catalog
}).strict().superRefine((value, ctx) => {
  if (value.catalog.snapshot_id !== value.snapshot_id)
    ctx.addIssue({ code: "custom", message: "catalog belongs to a different snapshot" });
  if (new Set(value.referents.map(item => item.referent_id)).size !== value.referents.length)
    ctx.addIssue({ code: "custom", message: "duplicate public referent ID" });
  const full = value.completeness.status === "complete" && value.completeness.included.length === 4 && value.completeness.missing.length === 0;
  if (value.completeness.full_reference_complete !== full)
    ctx.addIssue({ code: "custom", message: "full reference completeness disagrees with captured scope" });
  if (value.completeness.included.includes("interaction") && value.interaction === null)
    ctx.addIssue({ code: "custom", message: "captured interaction domain is required" });
});
const contextSchema = z.object({ schema: tag("context"), input_profile: profile, observation_ref: id, capture_ref: id,
  game_continuity_id: scalar.nullable(), stream_generation: id, publication_cursor: cursor.nullable() }).strict();
const captureSchema = z.object({ schema: tag("capture"), capture_id: id, snapshot_id: id, input_profile: profile,
  session, stream_generation: id, scope_id: id, capture_ordinal: u64,
  captured_at: time, expires_at: time, byte_count: positive.max(NATIVE_LOGICAL_MAX_CAPTURE_BYTES),
  sha256: digest, read_cursor: cursor }).strict().superRefine((value, ctx) => {
  if (Date.parse(value.expires_at) <= Date.parse(value.captured_at))
    ctx.addIssue({ code: "custom", message: "capture expires before capture time" });
});
const readSchema = z.object({ schema: tag("read"), capture_id: id, sha256: digest, offset: integer,
  total_bytes: positive.max(NATIVE_LOGICAL_MAX_CAPTURE_BYTES),
  data_base64: z.string().min(4).max(4 * Math.ceil(NATIVE_LOGICAL_MAX_READ_BYTES / 3))
    .regex(/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u),
  next_cursor: cursor.nullable(), complete: z.boolean()
}).strict().superRefine((value, ctx) => {
  if (value.complete !== (value.next_cursor === null))
    ctx.addIssue({ code: "custom", message: "read completion and cursor disagree" });
  const bytes = Buffer.from(value.data_base64, "base64");
  if (bytes.toString("base64") !== value.data_base64 || bytes.length === 0 || value.offset + bytes.length > value.total_bytes ||
      value.complete !== (value.offset + bytes.length === value.total_bytes))
    ctx.addIssue({ code: "custom", message: "read base64 or terminal byte position is inconsistent" });
});
const pageSchema = z.object({ schema: tag("catalog-page"), status: z.enum(["complete", "page_budget_too_small"]),
  catalog_ref: id, snapshot_id: id, stream_generation: id, total_count: integer, digest,
  filtered_count: integer, filtered_digest: digest, actions: actionList,
  next_cursor: cursor.nullable(), minimum_required_bytes: positive.nullable()
}).strict().superRefine((value, ctx) => {
  if (value.filtered_count > value.total_count || value.actions.length > value.filtered_count ||
      value.status === "page_budget_too_small" && (value.actions.length !== 0 || value.minimum_required_bytes === null) ||
      value.status === "complete" && (value.minimum_required_bytes !== null || value.filtered_count === 0 && value.next_cursor !== null))
    ctx.addIssue({ code: "custom", message: "catalog page counts/status disagree" });
});
const resolveSchema = z.object({ schema: tag("resolve"),
  status: z.enum(["unique", "no_match", "ambiguous", "expired", "generation_mismatch", "invalid_expression"]),
  action: nativeLogicalActionSchema.nullable() }).strict().superRefine((value, ctx) => {
  if ((value.status === "unique") !== (value.action !== null))
    ctx.addIssue({ code: "custom", message: "resolve status disagrees with original action" });
});
const subscriptionSchema = z.object({ subscription_id: id, scope_id: id, eager_scope: scope, coverage: coverageList,
  delivery_mode: z.enum(["full_reference", "scoped"]), stream_generation: id,
  starting_cursor: cursor, expires_at: time }).strict().superRefine((value, ctx) => {
  if (value.delivery_mode === "full_reference" && (value.eager_scope.length !== 4 || value.coverage.some(item => item.coverage !== "complete_at_seam")))
    ctx.addIssue({ code: "custom", message: "full-reference subscription lacks its promised scope or seam coverage" });
});
const attachSchema = z.object({ schema: tag("attach"),
  status: z.enum(["attached", "unsupported_scope", "unsupported_seam", "coverage_insufficient", "capacity_exceeded"]),
  subscription: subscriptionSchema.nullable() }).strict().superRefine((value, ctx) => {
  if ((value.status === "attached") !== (value.subscription !== null))
    ctx.addIssue({ code: "custom", message: "attachment status disagrees with subscription" });
});
const gap = z.object({ reason: scalar, from_publication_index: u64, through_publication_index: u64 }).strict()
  .superRefine((value, ctx) => { if (BigInt(value.from_publication_index) > BigInt(value.through_publication_index))
    ctx.addIssue({ code: "custom", message: "gap interval is reversed" }); });
const eventSchema = z.object({ schema: tag("event"), cursor, stream_generation: id, publication_index: u64,
  kind: scalar, source_seam: scalar, source_phase: scalar, source_index: u64, scope_id: id,
  capture_ref: id.nullable(), missing_reason: scalar.nullable(), coverage: coverageValue,
  payload_reference: captureSchema.nullable() }).strict().superRefine((value, ctx) => {
  if (value.payload_reference !== null && (value.capture_ref !== value.payload_reference.capture_id ||
      value.stream_generation !== value.payload_reference.stream_generation || value.scope_id !== value.payload_reference.scope_id || value.missing_reason !== null) ||
      value.payload_reference === null && (value.capture_ref !== null || value.missing_reason === null))
    ctx.addIssue({ code: "custom", message: "event has inconsistent immutable payload identity or missing outcome" });
});
const availability = z.object({ event: eventSchema, availability: z.enum(["available", "payload_expired", "missing"]) }).strict()
  .superRefine((value, ctx) => {
    if ((value.availability === "missing") !== (value.event.payload_reference === null))
      ctx.addIssue({ code: "custom", message: "current payload availability disagrees with original event" });
  });
const eventBatchSchema = z.object({ schema: tag("events"), events: z.array(availability).max(2048),
  next_cursor: cursor, high_watermark: cursor, retained_start_cursor: cursor, gap: gap.nullable() }).strict()
  .superRefine((value, ctx) => {
    let previous = -1n;
    for (const item of value.events) {
      const index = BigInt(item.event.publication_index);
      if (index <= previous) ctx.addIssue({ code: "custom", message: "event batch publication order is not strictly increasing" });
      previous = index;
    }
  });
const awaitSchema = z.object({ schema: tag("await"),
  status: z.enum(["event", "timeout", "gap", "cancelled", "subscription_expired", "generation_changed", "capacity_exceeded"]),
  event: availability.nullable(), gap: gap.nullable(), reason: scalar.nullable() }).strict()
  .superRefine((value, ctx) => {
    if ((value.status === "event") !== (value.event !== null) || (value.status === "gap") !== (value.gap !== null))
      ctx.addIssue({ code: "custom", message: "Await disposition disagrees with event/gap" });
  });
const delivery = z.enum(["not_started", "rejected_before_input", "delivered", "partially_delivered", "unknown"]);
const stageText = scalar.refine(value => Buffer.byteLength(value, "utf8") <= 128);
const resultSchema = z.object({ protocol_version: z.literal(SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL), schema: tag("result"), input_profile: profile,
  request_id: id, snapshot_id: id, action: nativeLogicalActionSchema.nullable(), delivery,
  execution: z.enum(["not_started", "native_accepted", "native_rejected", "unknown"]),
  effect: z.enum(["not_observed", "pending", "observed", "unknown"]),
  cancel: z.enum(["not_requested", "cancelled_before_start", "too_late", "unknown"]),
  stages: z.array(z.object({ stage: stageText, delivery, evidence: stageText }).strict()).max(16),
  reason: scalar.nullable(), retry: z.literal("never_automatic"), observed_frame: contextSchema.nullable(),
  attribution: z.object({ runtime_instance_id: id, client_session_id: id, client_instance_id: scalar, product_id: scalar,
    product_name: scalar, product_version: scalar, controller_lease_id: id, controller_generation: positive }).strict().nullable()
}).strict();
const limits = z.object({ max_actions: positive, max_capture_bytes: positive, max_retained_bytes: positive,
  max_events: positive, retention_ms: positive, max_read_bytes: positive, max_encoded_read_bytes: positive,
  max_page_bytes: positive, max_wait_ms: positive, max_subscriptions: positive, max_client_subscriptions: positive,
  max_waiters: positive, max_client_waiters: positive, max_captures: positive, max_retention_handles: positive,
  max_client_retention_handles: positive, encoding_deadline_ms: positive, max_field_bytes: positive,
  max_cursor_length: positive, max_wait_ids_per_subscription: positive, max_wait_id_metadata_bytes: positive,
  max_source_seams: positive, max_metadata_field_bytes: positive, max_in_flight_catalog_reads: positive }).strict();
const capabilitiesSchema = z.object({ protocol_version: z.literal(SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL), schema: tag("capabilities"), input_profile: profile,
  host: z.object({ id: scalar, name: scalar, version: scalar, runtime_instance_id: id,
    host_kind: scalar, implementation: z.object({ source_revision: scalar.nullable(),
      module_version_id: scalar.nullable(), artifact_sha256: digest.nullable() }).strict() }).strict(),
  game: z.object({ version: scalar.nullable(), commit: scalar.nullable(), branch: scalar.nullable(), main_assembly_hash: z.number().int().nullable(),
    compatibility: z.object({ status: scalar, observation_allowed: z.boolean(), detail: scalar }).strict(),
    modset: z.object({ status: scalar, fingerprint: scalar, scope: scalar, loaded_mod_ids: z.array(scalar), detail: scalar }).strict() }).strict(),
  session, stream_generation: id, supported_methods: z.array(scalar), implemented_mechanisms: z.array(scalar), capture_coverage: coverageList,
  source_clock: z.object({ publication_index_encoding: z.literal("u64_decimal_string"), source_index_encoding: z.literal("u64_decimal_string"),
    source_position_meaning: z.literal("native_source_occurrence"),
    publication_meaning: z.literal("contiguous_completed_projections_not_causal_commit_order") }).strict(),
  limits, control_policy: z.object({ recommended_renewal_ms: integer }).strict(), non_claims: z.array(scalar)
}).strict().superRefine((value, ctx) => {
  if (value.host.runtime_instance_id !== value.session.runtime_instance_id)
    ctx.addIssue({ code: "custom", message: "capabilities host and session runtime disagree" });
});
const retentionSchema = z.object({ retention_handle_id: id, capture: captureSchema, read_cursor: cursor, expires_at: time }).strict();
const currentSchema = z.object({ schema: tag("current"), input_profile: profile,
  status: z.enum(["captured", "partial", "stale", "capacity_exceeded", "source_capture_incomplete", "failed"]),
  context: contextSchema.nullable(), capture: captureSchema.nullable(), retention: retentionSchema.nullable(), reason: scalar.nullable()
}).strict().superRefine((value, ctx) => {
  if (value.status === "captured" && (value.capture === null || value.context === null || value.reason !== null))
    ctx.addIssue({ code: "custom", message: "captured Current lacks coherent values" });
  if (value.status === "partial" && (value.capture === null || value.context === null || value.reason !== "scope_omission"))
    ctx.addIssue({ code: "custom", message: "partial Current must report actual scope omission with joined references" });
  if (value.status !== "captured" && value.status !== "partial" &&
      (value.context !== null || value.capture !== null || value.retention !== null || value.reason === null))
    ctx.addIssue({ code: "custom", message: "failed Current has no retained references and requires an explicit reason" });
  if ((value.capture === null) !== (value.context === null))
    ctx.addIssue({ code: "custom", message: "Current context and capture must appear together" });
  if (value.capture && value.context && (value.context.capture_ref !== value.capture.capture_id ||
      value.context.observation_ref !== value.capture.snapshot_id || value.context.stream_generation !== value.capture.stream_generation))
    ctx.addIssue({ code: "custom", message: "Current context does not identify its capture" });
  if (value.retention && (!value.capture || JSON.stringify(value.retention.capture) !== JSON.stringify(value.capture)))
    ctx.addIssue({ code: "custom", message: "Current retention must preserve the original capture" });
});
const retainSchema = z.object({ schema: tag("retain"), input_profile: profile,
  status: z.enum(["retained", "payload_expired", "capacity_exceeded"]), retention: retentionSchema.nullable(), reason: scalar.nullable()
}).strict().superRefine((value, ctx) => {
  if ((value.status === "retained") !== (value.retention !== null) || value.status === "retained" && value.reason !== null ||
      value.status !== "retained" && value.reason === null)
    ctx.addIssue({ code: "custom", message: "retention disposition disagrees with handle" });
});
const releaseSchema = z.object({ schema: tag("release"), input_profile: profile, status: z.literal("released"),
  retention_handle_id: id, released: z.literal(true), reason: z.null() }).strict();
const renewSchema = z.object({ schema: tag("renew"), input_profile: profile, status: z.enum(["renewed", "subscription_expired"]),
  subscription: subscriptionSchema.nullable(), next_cursor: cursor.nullable(), high_watermark: cursor.nullable(),
  retained_start_cursor: cursor.nullable(), gap: gap.nullable(), reason: scalar.nullable()
}).strict().superRefine((value, ctx) => {
  if (value.status === "renewed" && (value.subscription === null || value.next_cursor === null || value.high_watermark === null || value.retained_start_cursor === null || value.reason !== null) ||
      value.status === "subscription_expired" && (value.subscription !== null || value.next_cursor !== null || value.high_watermark !== null || value.retained_start_cursor !== null || value.gap !== null || value.reason === null))
    ctx.addIssue({ code: "custom", message: "renewal disposition disagrees with preserved subscription" });
});
const cancelWaitSchema = z.object({ schema: tag("cancel-wait"), input_profile: profile,
  status: z.enum(["cancelled", "not_pending"]), subscription_id: id,
  wait_id: z.string().regex(/^[0-9a-f]{32}$/u), cancelled: z.boolean(), reason: scalar.nullable()
}).strict().superRefine((value, ctx) => {
  if ((value.status === "cancelled") !== value.cancelled)
    ctx.addIssue({ code: "custom", message: "wait cancellation status disagrees with actual cancellation" });
});
const detachSchema = z.object({ schema: tag("detach"), input_profile: profile, status: z.literal("detached"),
  subscription_id: id, detached: z.boolean(), reason: scalar.nullable() }).strict();

export type NativeLogicalAction = z.infer<typeof nativeLogicalActionSchema>;
export type NativeLogicalPrefix = z.infer<typeof nativeLogicalPrefixSchema>;
export type NativeLogicalExpression = z.infer<typeof nativeLogicalExpressionSchema>;
export type NativeLogicalObservation = z.infer<typeof observationSchema>;
export type NativeLogicalContext = z.infer<typeof contextSchema>;
export type NativeLogicalCapture = z.infer<typeof captureSchema>;
export type NativeLogicalReadChunk = z.infer<typeof readSchema>;
export type NativeLogicalCatalogPage = z.infer<typeof pageSchema>;
export type NativeLogicalResolve = z.infer<typeof resolveSchema>;
export type NativeLogicalSubscription = z.infer<typeof subscriptionSchema>;
export type NativeLogicalAttach = z.infer<typeof attachSchema>;
export type NativeLogicalEvent = z.infer<typeof eventSchema>;
export type NativeLogicalEventAvailability = z.infer<typeof availability>;
export type NativeLogicalEvents = z.infer<typeof eventBatchSchema>;
export type NativeLogicalAwait = z.infer<typeof awaitSchema>;
export type NativeLogicalResult = z.infer<typeof resultSchema>;
export type NativeLogicalCapabilities = z.infer<typeof capabilitiesSchema>;
export type NativeLogicalRetention = z.infer<typeof retentionSchema>;
export type NativeLogicalCurrent = z.infer<typeof currentSchema>;
export type NativeLogicalRetain = z.infer<typeof retainSchema>;
export type NativeLogicalRelease = z.infer<typeof releaseSchema>;
export type NativeLogicalRenew = z.infer<typeof renewSchema>;
export type NativeLogicalCancelWait = z.infer<typeof cancelWaitSchema>;
export type NativeLogicalDetach = z.infer<typeof detachSchema>;
export type NativeLogicalScopeField = typeof NATIVE_LOGICAL_SCOPE[number];
export type NativeLogicalTransportOperation = "capabilities" | "attach" | "current" | "read" | "catalog" | "resolve" |
  "events" | "await" | "cancel_wait" | "detach" | "renew" | "retain" | "release" | "submit" | "result";
export interface NativeLogicalTransportOptions {
  signal?: AbortSignal;
  maxResponseBytes?: number;
  timeoutMs?: number;
}
export interface NativeLogicalTransportReply { raw: JsonObject; encodedByteCount: number; statusCode: number; }
export interface NativeLogicalTransport {
  nativeLogicalRequest(operation: NativeLogicalTransportOperation, body?: JsonObject,
    options?: NativeLogicalTransportOptions): Promise<NativeLogicalTransportReply>;
}

const attachRequestSchema = z.object({ client_session_id: id, eager_scope: scope,
  required_seams: coverageList, delivery_mode: z.enum(["full_reference", "scoped"]) }).strict();
const currentRequestSchema = z.object({ client_session_id: id, eager_scope: scope, expected_snapshot_id: id.nullable() }).strict();
const subscriptionRequestFields = { client_session_id: id, subscription_id: id, scope_id: id, after_cursor: cursor };
const requestSchemas = {
  attach: attachRequestSchema,
  current: currentRequestSchema,
  read: z.object({ capture_id: id, cursor, max_bytes: positive.max(NATIVE_LOGICAL_MAX_READ_BYTES) }).strict(),
  catalog: z.object({ catalog_ref: id, stream_generation: id, prefix: nativeLogicalPrefixSchema.nullable(),
    cursor: cursor.nullable(), limit: positive.max(65536), max_page_bytes: positive.max(NATIVE_LOGICAL_MAX_PAGE_BYTES) }).strict(),
  resolve: z.object({ catalog_ref: id, stream_generation: id, expression: nativeLogicalExpressionSchema }).strict(),
  events: z.object({ ...subscriptionRequestFields, limit: positive.max(2048) }).strict(),
  await: z.object({ ...subscriptionRequestFields, wait_id: z.string().regex(/^[0-9a-f]{32}$/u),
    condition: z.enum(["any_event", "observation", "catalog_nonempty", "terminal"]), timeout_ms: integer.max(30000),
    control_binding: z.object({ controller_lease_id: id, controller_generation: positive }).strict().nullable() }).strict(),
  cancel_wait: z.object({ client_session_id: id, subscription_id: id, wait_id: z.string().regex(/^[0-9a-f]{32}$/u) }).strict(),
  detach: z.object({ client_session_id: id, subscription_id: id }).strict(),
  renew: z.object(subscriptionRequestFields).strict(),
  retain: z.object({ client_session_id: id, capture_id: id }).strict(),
  release: z.object({ client_session_id: id, retention_handle_id: id }).strict(),
  submit: z.object({ request_id: id, expected_snapshot_id: id, bound_action_id: id, client_session_id: id,
    controller_lease_id: id, controller_generation: positive, input_profile: profile }).strict(),
  result: z.object({ request_id: id }).strict()
};

function decode<T>(value: unknown, schema: z.ZodType<T, z.ZodTypeDef, unknown>, label: string): DecodedPlayerPayload<T> {
  if (!isJsonObject(value)) throw new Error(`native logical ${label} is not an object`);
  assertNativeLogicalJson(value);
  const data = schema.parse(value);
  return { raw: freezeNativeLogical(value as JsonObject), data: freezeNativeLogical(data) };
}
export const decodeNativeLogicalAction = (value: unknown) => decode(value, nativeLogicalActionSchema, "action");
export const decodeNativeLogicalObservation = (value: unknown) => decode(value, observationSchema, "observation");
export const decodeNativeLogicalContext = (value: unknown) => decode(value, contextSchema, "context");
export const decodeNativeLogicalCapture = (value: unknown) => decode(value, captureSchema, "capture");
export const decodeNativeLogicalRead = (value: unknown) => decode(value, readSchema, "read");
export const decodeNativeLogicalCatalogPage = (value: unknown) => decode(value, pageSchema, "catalog page");
export const decodeNativeLogicalResolve = (value: unknown) => decode(value, resolveSchema, "resolve");
export const decodeNativeLogicalAttach = (value: unknown) => decode(value, attachSchema, "attach");
export const decodeNativeLogicalEvent = (value: unknown) => decode(value, eventSchema, "event");
export const decodeNativeLogicalEvents = (value: unknown) => decode(value, eventBatchSchema, "events");
export const decodeNativeLogicalAwait = (value: unknown) => decode(value, awaitSchema, "await");
export const decodeNativeLogicalResult = (value: unknown) => decode(value, resultSchema, "result");
export const decodeNativeLogicalCapabilities = (value: unknown) => decode(value, capabilitiesSchema, "capabilities");
export const decodeNativeLogicalCurrent = (value: unknown) => decode(value, currentSchema, "current");
export const decodeNativeLogicalRetain = (value: unknown) => decode(value, retainSchema, "retain");
export const decodeNativeLogicalRelease = (value: unknown) => decode(value, releaseSchema, "release");
export const decodeNativeLogicalRenew = (value: unknown) => decode(value, renewSchema, "renew");
export const decodeNativeLogicalRetention = (value: unknown) => decode(value, retentionSchema, "retention reference");
export const decodeNativeLogicalCancelWait = (value: unknown) => decode(value, cancelWaitSchema, "cancel wait");
export const decodeNativeLogicalDetach = (value: unknown) => decode(value, detachSchema, "detach");
export const decodeNativeLogicalAttachRequest = (value: unknown) => decode(value, attachRequestSchema, "attach request");
export function validateNativeLogicalRequest(operation: NativeLogicalTransportOperation, body?: JsonObject): void {
  if (operation === "capabilities") { if (body !== undefined) throw new Error("capabilities has no request body"); return; }
  assertNativeLogicalJson(body);
  requestSchemas[operation].parse(body);
}
const pendingLookupSchema = z.object({ error: z.object({ code: z.literal("request_pending"), detail: scalar }).strict() }).strict();
export function isNativeLogicalPendingLookup(value: unknown): boolean {
  return pendingLookupSchema.safeParse(value).success;
}
export function validateNativeLogicalScope(value: readonly NativeLogicalScopeField[]): void { scope.parse(value); }
export function validateNativeLogicalPrefix(value: NativeLogicalPrefix): void { assertNativeLogicalJson(value); nativeLogicalPrefixSchema.parse(value); }
export function validateNativeLogicalExpression(value: NativeLogicalExpression): void { assertNativeLogicalJson(value); nativeLogicalExpressionSchema.parse(value); }
