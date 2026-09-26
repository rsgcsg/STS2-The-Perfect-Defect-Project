import { z } from "zod";
import { isJsonObject, type JsonObject } from "./json.js";
import {
  decodePlayerCapabilities, decodePlayerSnapshot,
  SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL,
  type DecodedPlayerPayload, type PlayerEnvironmentCapabilities,
  type PlayerEnvironmentSnapshot, type PlayerEnvironmentBoundActionArgument
} from "./protocol.js";

export const TEXT_MENU_PROFILE = "text-menu-v1" as const;
export const TEXT_MENU_SNAPSHOT_SCHEMA = "sts2.player-environment/text-menu-snapshot-1" as const;
export const TEXT_MENU_RESULT_SCHEMA = "sts2.player-environment/text-menu-action-result-1" as const;

const cursors = ["root", "information", "relic_inspect", "relic_tips", "card_tips",
  "power_tips", "intent_tips", "orb_tips", "topbar_tips"] as const;
const groups = cursors.slice(2);
const navigation = ["open_information", ...groups.map((group) => `open_${group}`), "back"] as const;

const argumentSchema = z.object({ role: z.string().min(1), referent_id: z.string().min(1) }).strict();
const actionSchema = z.object({
  action_id: z.string().min(1),
  kind: z.enum(["system_navigation", "native_input"]),
  verb: z.string().min(1),
  label: z.string().min(1),
  subject_referent_id: z.string().min(1).nullable(),
  arguments: z.array(argumentSchema),
  effect_domain: z.enum(["text_menu", "native_input"])
}).strict().superRefine((action, ctx) => {
  if ((action.kind === "system_navigation") !== (action.effect_domain === "text_menu")) {
    ctx.addIssue({ code: "custom", message: "action kind and effect domain disagree" });
  }
  if (action.kind === "system_navigation" &&
      (!navigation.includes(action.verb as typeof navigation[number]) ||
       action.subject_referent_id !== null || action.arguments.length !== 0)) {
    ctx.addIssue({ code: "custom", message: "system navigation must be a fixed operand-free edge" });
  }
});

const menuSchema = z.object({
  cursor: z.enum(cursors),
  revision: z.number().int().nonnegative(),
  native_snapshot_id: z.string().min(1)
}).strict();
const actionsSchema = z.object({
  status: z.enum(["complete", "truncated", "unavailable"]),
  materialized_count: z.number().int().nonnegative(),
  total_count: z.number().int().nonnegative(),
  ordering_semantics: z.string().min(1),
  actions: z.array(actionSchema)
}).strict();
const capabilitySchema = z.object({
  verb: z.string().min(1),
  subject_role: z.string().min(1).nullable().optional(),
  arguments: z.array(z.object({ role: z.string().min(1), required: z.boolean() }).strict()),
  availability_basis: z.string().min(1)
}).strict();
const attributionSchema = z.object({
  runtime_instance_id: z.string().min(1), client_session_id: z.string().min(1),
  client_instance_id: z.string().min(1), product_id: z.string().min(1),
  product_name: z.string().min(1), product_version: z.string().min(1),
  controller_lease_id: z.string().min(1), controller_generation: z.number().int().positive()
}).strict();
const resultSchema = z.object({
  protocol_version: z.literal(SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL),
  schema: z.literal(TEXT_MENU_RESULT_SCHEMA),
  input_profile: z.literal(TEXT_MENU_PROFILE),
  request_id: z.string().min(1),
  status: z.enum(["applied", "not_applied", "unknown"]),
  effect_domain: z.enum(["text_menu", "native_input"]).nullable(),
  native_delivery: z.enum(["delivered", "not_delivered", "unknown"]).nullable(),
  action: actionSchema.nullable(),
  reason_code: z.string().nullable(),
  detail: z.string().nullable(),
  retry: z.enum(["never", "reobserve"]),
  successor: z.unknown().nullable(),
  attribution: attributionSchema.nullable()
}).strict();

export type TextMenuCursor = typeof cursors[number];
export type TextMenuAction = z.infer<typeof actionSchema>;
export type TextMenuArgument = PlayerEnvironmentBoundActionArgument;
export type TextMenuCapabilities = Omit<PlayerEnvironmentCapabilities, "snapshot_schema" | "receipt_schema" | "verbs"> & {
  input_profile: typeof TEXT_MENU_PROFILE;
  snapshot_schema: typeof TEXT_MENU_SNAPSHOT_SCHEMA;
  receipt_schema: typeof TEXT_MENU_RESULT_SCHEMA;
  verbs: string[];
};
export type TextMenuSnapshot = Omit<PlayerEnvironmentSnapshot, "schema" | "interaction" | "bound_actions" | "reads"> & {
  schema: typeof TEXT_MENU_SNAPSHOT_SCHEMA;
  input_profile: typeof TEXT_MENU_PROFILE;
  interaction: Omit<PlayerEnvironmentSnapshot["interaction"], "capabilities"> & {
    capabilities: z.infer<typeof capabilitySchema>[];
  };
  menu: z.infer<typeof menuSchema>;
  menu_actions: z.infer<typeof actionsSchema>;
};
export type TextMenuActionResult = Omit<z.infer<typeof resultSchema>, "successor"> & {
  successor: TextMenuSnapshot | null;
};

function parse<T>(value: unknown, schema: z.ZodType<T>, label: string): T {
  const result = schema.safeParse(value);
  if (!result.success) throw new Error(`${label} failed strict decoding: ${result.error.issues
    .map((issue) => `${issue.path.join(".")}: ${issue.message}`).join("; ")}`);
  return result.data;
}
function exactKeys(object: JsonObject, keys: readonly string[], label: string): void {
  const expected = new Set(keys);
  if (Object.keys(object).some((key) => !expected.has(key)) ||
      keys.some((key) => !Object.hasOwn(object, key))) {
    throw new Error(`${label} has missing or unrecognized fields`);
  }
}
function asObject(value: unknown, label: string): JsonObject {
  if (!isJsonObject(value)) throw new Error(`${label} is not an object`);
  return value;
}

export function decodeTextMenuCapabilities(value: unknown): DecodedPlayerPayload<TextMenuCapabilities> {
  const raw = asObject(value, "text menu capabilities");
  if (raw.input_profile !== TEXT_MENU_PROFILE || raw.snapshot_schema !== TEXT_MENU_SNAPSHOT_SCHEMA ||
      raw.receipt_schema !== TEXT_MENU_RESULT_SCHEMA) throw new Error("text menu capability profile or schema mismatch");
  const verbs = parse(raw.verbs, z.array(z.string().min(1)), "text menu verbs");
  if (new Set(verbs).size !== verbs.length) throw new Error("text menu capability verbs must be unique");
  const normalized: JsonObject = { ...raw, snapshot_schema: "sts2.player-environment/snapshot-1",
    receipt_schema: "sts2.player-environment/receipt-1", verbs: [] };
  delete normalized.input_profile;
  const decoded = decodePlayerCapabilities(normalized);
  return { raw, data: { ...decoded.data, input_profile: TEXT_MENU_PROFILE,
    snapshot_schema: TEXT_MENU_SNAPSHOT_SCHEMA, receipt_schema: TEXT_MENU_RESULT_SCHEMA, verbs } };
}

export function decodeTextMenuSnapshot(value: unknown): DecodedPlayerPayload<TextMenuSnapshot> {
  const raw = asObject(value, "text menu snapshot");
  exactKeys(raw, ["protocol_version", "schema", "input_profile", "snapshot_id", "sequence", "observed_at",
    "status", "persistent", "interaction", "referents", "completeness", "session",
    "information_policy", "menu", "menu_actions"], "text menu snapshot");
  if (raw.input_profile !== TEXT_MENU_PROFILE || raw.schema !== TEXT_MENU_SNAPSHOT_SCHEMA) {
    throw new Error("text menu snapshot profile or schema mismatch");
  }
  const menu = parse(raw.menu, menuSchema, "menu cursor");
  const menuActions = parse(raw.menu_actions, actionsSchema, "menu actions");
  const interaction = asObject(raw.interaction, "text menu interaction");
  const capabilities = parse(interaction.capabilities, z.array(capabilitySchema), "text menu capabilities");
  const referents = Array.isArray(raw.referents) ? raw.referents : [];
  const referentIds = new Set(referents.map((item) => isJsonObject(item) ? item.referent_id : null));
  const ids = menuActions.actions.map((item) => item.action_id);
  if (new Set(ids).size !== ids.length) throw new Error("menu action ids must be unique");
  if (menuActions.actions.some((item) => (item.subject_referent_id !== null && !referentIds.has(item.subject_referent_id)) ||
      item.arguments.some((argument) => !referentIds.has(argument.referent_id)))) {
    throw new Error("menu action references an unknown current referent");
  }
  const allowed = menu.cursor === "root" ? ["open_information"] :
    menu.cursor === "information" ? [...navigation.filter((item) => item !== "open_information"), "back"] : ["back"];
  const navActions = menuActions.actions.filter((item) => item.kind === "system_navigation");
  if (navActions.some((item) => !allowed.includes(item.verb)) ||
      new Set(navActions.map((item) => item.verb)).size !== navActions.length) {
    throw new Error("illegal or duplicate system menu edge");
  }
  if (menuActions.materialized_count !== menuActions.actions.length ||
      menuActions.materialized_count > menuActions.total_count ||
      (menuActions.status === "complete" && menuActions.materialized_count !== menuActions.total_count)) {
    throw new Error("menu action counts are inconsistent");
  }
  if (menuActions.status !== "complete" && menuActions.actions.length !== 0) {
    throw new Error("incomplete menu cannot advertise executable actions");
  }
  const interactive = menuActions.status === "complete" && menuActions.actions.length > 0;
  if ((raw.status === "interactive") !== interactive) throw new Error("menu interactive status mismatch");
  if (menuActions.status !== "complete" && capabilities.length > 0) {
    throw new Error("incomplete menu cannot advertise capabilities");
  }
  // Validate all unchanged public facts through the existing strict schema. The legacy
  // action projection is a validation placeholder only and is never exposed.
  const normalized: JsonObject = { ...raw, schema: "sts2.player-environment/snapshot-1",
    status: "observed", interaction: { ...interaction,
      content_schema: `sts2.player-environment/surface/${interaction.kind}-1`, capabilities: [] },
    bound_actions: { schema: "sts2.player-environment/bound-actions-1", status: "complete",
      materialized_count: 0, total_count: 0, limit: 512,
      ordering_semantics: "validation_only", actions: [] }, reads: [] };
  delete normalized.input_profile;
  delete normalized.menu;
  delete normalized.menu_actions;
  decodePlayerSnapshot(normalized);
  return { raw, data: { ...raw, interaction: { ...interaction, capabilities },
    menu, menu_actions: menuActions } as TextMenuSnapshot };
}

export function decodeTextMenuActionResult(value: unknown): DecodedPlayerPayload<TextMenuActionResult> {
  const raw = asObject(value, "text menu action result");
  const parsed = parse(raw, resultSchema, "text menu action result");
  if (parsed.status === "applied") {
    if (parsed.action === null || parsed.effect_domain !== parsed.action.effect_domain ||
        parsed.native_delivery !== (parsed.effect_domain === "text_menu" ? null : "delivered") ||
        parsed.retry !== "never") throw new Error("applied text menu result contradicts its effect domain");
  } else if (parsed.status === "unknown") {
    if (parsed.effect_domain !== "native_input" || parsed.native_delivery !== "unknown" ||
        parsed.retry !== "never" || parsed.action?.kind !== "native_input") {
      throw new Error("unknown native input must never permit retry");
    }
  } else if (parsed.native_delivery === "delivered" || parsed.native_delivery === "unknown" ||
      (parsed.effect_domain === "text_menu" && parsed.native_delivery !== null) ||
      (parsed.action && parsed.effect_domain && parsed.action.effect_domain !== parsed.effect_domain)) {
    throw new Error("not_applied text menu result has inconsistent delivery");
  }
  if (parsed.status !== "applied" && parsed.successor !== null && parsed.retry !== "reobserve") {
    throw new Error("non-applied result with successor must reobserve");
  }
  const successor = parsed.successor === null ? null : decodeTextMenuSnapshot(parsed.successor).data;
  return { raw, data: { ...parsed, successor } };
}
