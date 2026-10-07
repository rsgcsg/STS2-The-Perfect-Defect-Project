import { z } from "zod";
import { isJsonObject, type JsonObject } from "./json.js";
import { persistentVisibleStateSchema } from "./visibleState.js";

export const SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL = "1.0.0" as const;

const hostSchema = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  version: z.string().min(1),
  runtime_instance_id: z.string().min(1),
  host_kind: z.enum(["live_ui", "headless", "replay", "test"]),
  implementation: z.object({
    source_revision: z.string().min(1).nullable().optional(),
    module_version_id: z.string().min(1).nullable().optional(),
    artifact_sha256: z.string().regex(/^[a-f0-9]{64}$/iu).nullable().optional()
  }).strict()
}).strict();

const gameSchema = z.object({
  version: z.string().nullable().optional(),
  commit: z.string().nullable().optional(),
  branch: z.string().nullable().optional(),
  main_assembly_hash: z.number().int().nullable().optional(),
  compatibility: z.object({
    status: z.string().min(1),
    observation_allowed: z.boolean(),
    detail: z.string()
  }).strict(),
  modset: z.object({
    status: z.string().min(1),
    fingerprint: z.string().min(1),
    scope: z.string().min(1),
    loaded_mod_ids: z.array(z.string().min(1)),
    detail: z.string()
  }).strict()
}).strict();

const sessionSchema = z.object({
  runtime_instance_id: z.string().min(1),
  environment_fingerprint: z.string().min(1)
}).strict();

const controlSchema = z.object({
  recommended_renewal_ms: z.number().int().nonnegative()
}).strict();

const evidenceProfileSchema = z.object({
  id: z.string().min(1),
  enabled: z.boolean(),
  supported_kinds: z.array(z.string().min(1)),
  snapshot_bound: z.literal(true),
  runtime_bound: z.literal(true),
  default_in_consumer_flow: z.literal(false),
  creates_mutation_authority: z.literal(false),
  enters_action_ledger: z.literal(false)
}).strict();

const controlClientSchema = z.object({
  client_session_id: z.string().min(1),
  client_instance_id: z.string().min(1)
}).passthrough();

const controlLeaseSchema = z.object({
  controller_lease_id: z.string().min(1),
  controller_generation: z.number().int().positive(),
  client_session_id: z.string().min(1),
  expires_at: z.string().min(1)
}).passthrough();

const clientRegistrationSchema = z.object({
  protocol_version: z.literal(SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL),
  schema: z.literal("sts2.player-environment/control-1"),
  runtime_instance_id: z.string().min(1),
  client: controlClientSchema,
  controller: controlLeaseSchema.nullable().optional()
}).strict();

const controlSnapshotSchema = z.object({
  protocol_version: z.literal(SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL),
  schema: z.literal("sts2.player-environment/control-1"),
  runtime_instance_id: z.string().min(1),
  clients: z.array(controlClientSchema),
  controller: controlLeaseSchema.nullable().optional()
}).strict();

const controllerLeaseResponseSchema = z.object({
  protocol_version: z.literal(SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL),
  schema: z.literal("sts2.player-environment/control-1"),
  runtime_instance_id: z.string().min(1),
  status: z.string().min(1),
  detail: z.string(),
  client: controlClientSchema.nullable().optional(),
  controller: controlLeaseSchema.nullable().optional()
}).strict();

const verbSchema = z.enum([
  "activate", "select", "deselect", "confirm", "cancel", "play",
  "target", "use", "end_turn", "skip", "open", "close"
]);

const boundActionSchema = z.object({
  bound_action_id: z.string().min(1),
  verb: verbSchema,
  interaction_id: z.string().min(1),
  subject_referent_id: z.string().min(1).nullable().optional(),
  arguments: z.array(z.object({
    role: z.string().min(1),
    referent_id: z.string().min(1)
  }).strict()),
  label: z.string().min(1)
}).strict();

const referentSchema = z.object({
  referent_id: z.string().min(1),
  role: z.string().min(1),
  kind: z.enum(["entity", "control"]),
  label: z.string().nullable().optional(),
  state: z.object({
    visible: z.boolean(),
    enabled: z.boolean().nullable().optional(),
    selected: z.boolean().nullable().optional(),
    focused: z.boolean().nullable().optional(),
    observation_basis: z.literal("native_visible_fact")
  }).strict(),
  properties_schema: z.string().regex(/^sts2\.player-environment\/referent\/[a-z0-9_]+-1$/u).nullable().optional(),
  properties: z.unknown().nullable().optional()
}).strict().superRefine((value, context) => {
  if (value.properties != null && !value.properties_schema) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "referent properties require a content schema" });
  }
});

const readSchema = z.object({
  read_id: z.string().min(1),
  kind: z.string().min(1),
  target_referent_id: z.string().nullable().optional(),
  content_schema: z.string().regex(/^sts2\.player-environment\/read\/[a-z0-9_]+-1$/u),
  visibility_basis: z.string().min(1),
  snapshot_bound: z.literal(true),
  ordering_semantics: z.string().min(1),
  hidden_by_policy: z.array(z.string())
}).strict();

const interactionContentSchema = z.object({
  surface: z.object({ kind: z.string().min(1) }).passthrough(),
  context: z.object({ kind: z.string().min(1) }).passthrough()
}).strict();

const snapshotSchema: z.ZodTypeAny = z.object({
  protocol_version: z.literal(SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL),
  schema: z.literal("sts2.player-environment/snapshot-1"),
  snapshot_id: z.string().min(1),
  sequence: z.number().int().positive(),
  observed_at: z.string().min(1),
  status: z.enum(["interactive", "visible_unsupported", "settling", "observed"]),
  persistent: z.object({
    content_schema: z.literal("sts2.player-environment/persistent/run-player-1"),
    content: persistentVisibleStateSchema
  }).strict().nullable(),
  interaction: z.object({
    interaction_id: z.string().min(1),
    kind: z.string().min(1),
    stage: z.string().min(1),
    prompt: z.string().nullable().optional(),
    content_schema: z.string().regex(/^sts2\.player-environment\/surface\/[a-z0-9_]+-1$/u),
    content: interactionContentSchema,
    capabilities: z.array(z.object({
      verb: verbSchema,
      subject_role: z.string().min(1).nullable().optional(),
      arguments: z.array(z.object({ role: z.string().min(1), required: z.boolean() }).strict()),
      availability_basis: z.literal("current_native_interaction")
    }).strict())
  }).strict(),
  referents: z.array(referentSchema),
  bound_actions: z.object({
    schema: z.literal("sts2.player-environment/bound-actions-1"),
    status: z.enum(["complete", "truncated", "unavailable"]),
    materialized_count: z.number().int().nonnegative(),
    total_count: z.number().int().nonnegative(),
    limit: z.number().int().positive(),
    ordering_semantics: z.string().min(1),
    actions: z.array(boundActionSchema)
  }).strict(),
  reads: z.array(readSchema),
  completeness: z.object({
    status: z.enum(["complete", "partial", "visible_unmapped", "unknown"]),
    visible_information: z.string().min(1),
    interaction_discovery: z.string().min(1),
    missing: z.array(z.string()),
    hidden_by_policy: z.array(z.string())
  }).strict(),
  session: sessionSchema,
  information_policy: z.object({
    id: z.string().min(1),
    scope: z.string().min(1),
    includes_hidden_information: z.literal(false),
    unknown_field_behavior: z.string().min(1)
  }).strict()
}).strict().superRefine((value, context) => {
  const boundActionIds = value.bound_actions.actions.map((item) => item.bound_action_id);
  const referentIds = new Set(value.referents.map((item) => item.referent_id));
  if (new Set(boundActionIds).size !== boundActionIds.length) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "bound action ids must be unique" });
  }
  if (referentIds.size !== value.referents.length) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "referent ids must be unique" });
  }
  if (value.bound_actions.actions.some((item) => item.interaction_id !== value.interaction.interaction_id)) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "bound action interaction must match the current interaction" });
  }
  if (value.bound_actions.actions.some((item) => item.subject_referent_id && !referentIds.has(item.subject_referent_id))) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "every bound action subject must be a current referent" });
  }
  if (value.bound_actions.actions.some((item) => item.arguments.some((argument) => !referentIds.has(argument.referent_id)))) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "every bound action argument must be a current referent" });
  }
  if (value.bound_actions.materialized_count !== value.bound_actions.actions.length
      || value.bound_actions.materialized_count > value.bound_actions.total_count
      || value.bound_actions.materialized_count > value.bound_actions.limit) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "bound action projection counts are inconsistent" });
  }
  if (value.bound_actions.status === "complete"
      && value.bound_actions.materialized_count !== value.bound_actions.total_count) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "complete bound action projection must materialize every action" });
  }
  const actionReady = value.bound_actions.status === "complete"
    && value.bound_actions.actions.length > 0;
  if ((value.status === "interactive") !== actionReady) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      message: "interactive status must exactly match a complete non-empty bound action projection"
    });
  }
  if (value.bound_actions.status !== "complete"
      && value.interaction.capabilities.length > 0) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      message: "an incomplete bound action projection cannot advertise interaction capabilities"
    });
  }
  if (value.reads.some((item) => item.target_referent_id && !referentIds.has(item.target_referent_id))) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "every targeted read must reference a current referent" });
  }
});

const capabilitiesSchema = z.object({
  protocol_version: z.literal(SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL),
  snapshot_schema: z.literal("sts2.player-environment/snapshot-1"),
  action_schema: z.literal("sts2.player-environment/action-1"),
  receipt_schema: z.literal("sts2.player-environment/receipt-1"),
  control_schema: z.literal("sts2.player-environment/control-1"),
  status: z.string().min(1),
  host: hostSchema,
  game: gameSchema,
  environment_fingerprint: z.string().min(1),
  verbs: z.array(verbSchema),
  snapshot_bound: z.literal(true),
  single_controller: z.literal(true),
  execution_available: z.boolean(),
  control: controlSchema,
  evidence_profiles: z.array(evidenceProfileSchema),
  non_claims: z.array(z.string())
}).strict();

const attributionSchema = z.object({
  runtime_instance_id: z.string().min(1), client_session_id: z.string().min(1),
  client_instance_id: z.string().min(1), product_id: z.string().min(1), product_name: z.string().min(1),
  product_version: z.string().min(1), controller_lease_id: z.string().min(1),
  controller_generation: z.number().int().positive()
}).strict();

const receiptSchema: z.ZodTypeAny = z.object({
  protocol_version: z.literal(SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL),
  schema: z.literal("sts2.player-environment/receipt-1"),
  request_id: z.string().min(1),
  delivery: z.enum(["delivered", "not_delivered", "unknown"]),
  action: z.object({
    bound_action_id: z.string(),
    verb: verbSchema,
    subject_referent_id: z.string().min(1).nullable().optional(),
    arguments: z.array(z.object({ role: z.string().min(1), referent_id: z.string().min(1) }).strict())
  }).strict(),
  reason_code: z.string().nullable().optional(),
  detail: z.string().nullable().optional(),
  retry: z.object({ allowed: z.boolean(), reason: z.string() }).strict(),
  successor: snapshotSchema.nullable(),
  attribution: attributionSchema.nullable().optional()
}).strict().superRefine((value, context) => {
  if (value.delivery === "unknown" && value.retry.allowed) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "unknown delivery must not allow retry" });
  }
});

const readResponseSchema = z.object({
  protocol_version: z.literal(SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL),
  schema: z.literal("sts2.player-environment/read-1"),
  read_id: z.string().min(1),
  expected_snapshot_id: z.string().min(1),
  observed_snapshot_id: z.string().min(1),
  observed_at: z.string().min(1),
  kind: z.string().min(1),
  target_referent_id: z.string().min(1).nullable().optional(),
  visibility_basis: z.string().min(1),
  ordering_semantics: z.string().min(1),
  content_schema: z.string().regex(/^sts2\.player-environment\/read\/[a-z0-9_]+-1$/u),
  content: z.unknown(),
  completeness: z.object({
    status: z.enum(["complete", "partial", "visible_unmapped", "unknown"]),
    visible_information: z.string().min(1),
    interaction_discovery: z.string().min(1),
    missing: z.array(z.string()),
    hidden_by_policy: z.array(z.string())
  }).strict(),
  session: sessionSchema,
  information_policy: z.object({
    id: z.string().min(1),
    scope: z.string().min(1),
    includes_hidden_information: z.literal(false),
    unknown_field_behavior: z.string().min(1)
  }).strict()
}).strict();

export type PlayerVerb = z.infer<typeof verbSchema>;
export type PlayerEnvironmentHostIdentity = z.infer<typeof hostSchema>;
export type PlayerEnvironmentGameIdentity = z.infer<typeof gameSchema>;
export type PlayerEnvironmentReferent = z.infer<typeof referentSchema>;
export type PlayerEnvironmentRead = z.infer<typeof readSchema>;
export type PlayerEnvironmentBoundActionArgument = z.infer<typeof boundActionSchema>["arguments"][number];

export interface PlayerEnvironmentBoundAction {
  bound_action_id: string;
  verb: PlayerVerb;
  interaction_id: string;
  subject_referent_id?: string | null;
  arguments: PlayerEnvironmentBoundActionArgument[];
  label: string;
}

export interface PlayerEnvironmentSnapshot {
  protocol_version: typeof SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL;
  schema: "sts2.player-environment/snapshot-1";
  snapshot_id: string;
  sequence: number;
  observed_at: string;
  status: "interactive" | "visible_unsupported" | "settling" | "observed";
  persistent: { content_schema: "sts2.player-environment/persistent/run-player-1"; content: unknown } | null;
  interaction: { interaction_id: string; kind: string; stage: string; prompt?: string | null; content_schema: string; content: { surface: { kind: string; [key: string]: unknown }; context: { kind: string; [key: string]: unknown } }; capabilities: Array<{ verb: PlayerVerb; subject_role?: string | null; arguments: Array<{ role: string; required: boolean }>; availability_basis: "current_native_interaction" }> };
  referents: PlayerEnvironmentReferent[];
  bound_actions: { schema: "sts2.player-environment/bound-actions-1"; status: "complete" | "truncated" | "unavailable"; materialized_count: number; total_count: number; limit: number; ordering_semantics: string; actions: PlayerEnvironmentBoundAction[] };
  reads: PlayerEnvironmentRead[];
  completeness: { status: "complete" | "partial" | "visible_unmapped" | "unknown"; visible_information: string; interaction_discovery: string; missing: string[]; hidden_by_policy: string[] };
  session: { runtime_instance_id: string; environment_fingerprint: string };
  information_policy: { id: string; scope: string; includes_hidden_information: false; unknown_field_behavior: string };
}

export interface PlayerEnvironmentCapabilities {
  protocol_version: typeof SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL;
  snapshot_schema: "sts2.player-environment/snapshot-1";
  action_schema: "sts2.player-environment/action-1";
  receipt_schema: "sts2.player-environment/receipt-1";
  control_schema: "sts2.player-environment/control-1";
  status: string;
  host: PlayerEnvironmentHostIdentity;
  game: PlayerEnvironmentGameIdentity;
  environment_fingerprint: string;
  verbs: PlayerVerb[];
  snapshot_bound: true;
  single_controller: true;
  execution_available: boolean;
  control: { recommended_renewal_ms: number };
  evidence_profiles: Array<{
    id: string;
    enabled: boolean;
    supported_kinds: string[];
    snapshot_bound: true;
    runtime_bound: true;
    default_in_consumer_flow: false;
    creates_mutation_authority: false;
    enters_action_ledger: false;
  }>;
  non_claims: string[];
}

export interface PlayerEnvironmentReceipt {
  protocol_version: typeof SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL;
  schema: "sts2.player-environment/receipt-1";
  request_id: string;
  delivery: "delivered" | "not_delivered" | "unknown";
  action: { bound_action_id: string; verb: PlayerVerb; subject_referent_id?: string | null; arguments: PlayerEnvironmentBoundActionArgument[] };
  reason_code?: string | null;
  detail?: string | null;
  retry: { allowed: boolean; reason: string };
  /** Immediate post-delivery observation; not business completion or canonical causal S'. */
  successor: PlayerEnvironmentSnapshot | null;
  attribution?: { runtime_instance_id: string; client_session_id: string; client_instance_id: string; product_id: string; product_name: string; product_version: string; controller_lease_id: string; controller_generation: number } | null;
}

export type PlayerEnvironmentReadResponse = z.infer<typeof readResponseSchema>;

/** Canonical GET status route; control commands remain subroutes of this resource. */
export const PLAYER_ENVIRONMENT_CONTROL_ROUTE = "/api/player-environment/controller" as const;
export type PlayerEnvironmentControlSnapshot = z.infer<typeof controlSnapshotSchema>;

export interface PlayerEnvironmentClientRegistration {
  protocol_version: typeof SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL;
  schema: "sts2.player-environment/control-1";
  runtime_instance_id: string;
  client: { client_session_id: string; client_instance_id: string; [key: string]: unknown };
  controller?: PlayerEnvironmentControllerLease | null;
}

export interface PlayerEnvironmentControllerLease {
  controller_lease_id: string;
  controller_generation: number;
  client_session_id: string;
  expires_at: string;
  [key: string]: unknown;
}

export interface PlayerEnvironmentControllerLeaseResponse {
  protocol_version: typeof SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL;
  schema: "sts2.player-environment/control-1";
  runtime_instance_id: string;
  status: string;
  detail: string;
  client?: { client_session_id: string; client_instance_id: string; [key: string]: unknown } | null;
  controller?: PlayerEnvironmentControllerLease | null;
}

export interface DecodedPlayerPayload<T> { raw: JsonObject; data: T }

export function decodePlayerCapabilities(value: unknown): DecodedPlayerPayload<PlayerEnvironmentCapabilities> {
  return decode<PlayerEnvironmentCapabilities>(value, capabilitiesSchema, "Player Environment capabilities");
}
export function decodePlayerSnapshot(value: unknown): DecodedPlayerPayload<PlayerEnvironmentSnapshot> {
  return decode<PlayerEnvironmentSnapshot>(value, snapshotSchema, "Player Environment observation");
}
export function decodePlayerReceipt(value: unknown): DecodedPlayerPayload<PlayerEnvironmentReceipt> {
  return decode<PlayerEnvironmentReceipt>(value, receiptSchema, "Player Environment receipt");
}
export function decodePlayerRead(value: unknown): DecodedPlayerPayload<PlayerEnvironmentReadResponse> {
  return decode<PlayerEnvironmentReadResponse>(value, readResponseSchema, "Player Environment read");
}
export function decodePlayerControlSnapshot(value: unknown): DecodedPlayerPayload<PlayerEnvironmentControlSnapshot> {
  return decode<PlayerEnvironmentControlSnapshot>(value, controlSnapshotSchema, "Player Environment control snapshot");
}
export function decodePlayerClientRegistration(value: unknown): DecodedPlayerPayload<PlayerEnvironmentClientRegistration> {
  return decode<PlayerEnvironmentClientRegistration>(value, clientRegistrationSchema, "Player Environment client registration");
}
export function decodePlayerControllerLeaseResponse(value: unknown): DecodedPlayerPayload<PlayerEnvironmentControllerLeaseResponse> {
  return decode<PlayerEnvironmentControllerLeaseResponse>(value, controllerLeaseResponseSchema, "Player Environment controller lease");
}

function decode<T>(value: unknown, schema: z.ZodType<T>, label: string): DecodedPlayerPayload<T> {
  if (!isJsonObject(value)) {
    throw new Error(`${label} was not a JSON object`);
  }
  const parsed = schema.safeParse(value);
  if (!parsed.success) {
    const issues = parsed.error.issues
      .map((issue) => `${issue.path.join(".")}: ${issue.message}`)
      .join("; ");
    throw new Error(`${label} failed strict decoding: ${issues}`);
  }
  return { raw: value, data: parsed.data };
}

