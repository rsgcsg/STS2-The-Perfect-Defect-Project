import { z } from "zod";
import { isJsonObject, type JsonObject } from "./json.js";
import {
  decodeTextMenuCapabilities, decodeTextMenuSnapshot,
  TEXT_MENU_PROFILE, TEXT_MENU_RESULT_SCHEMA, TEXT_MENU_SNAPSHOT_SCHEMA,
  type TextMenuCapabilities, type TextMenuSnapshot
} from "./textMenu.js";
import { SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL, type DecodedPlayerPayload } from "./protocol.js";

export const TEXT_MENU_V2_PROFILE = "text-menu-v2" as const;
export const TEXT_MENU_V2_SNAPSHOT_SCHEMA = "sts2.player-environment/text-menu-snapshot-2" as const;
export const TEXT_MENU_V2_RESULT_SCHEMA = "sts2.player-environment/text-menu-action-result-2" as const;
export const TEXT_MENU_V2_OBSERVATION_CONTEXT_SCHEMA =
  "sts2.player-environment/text-menu-observation-context-2" as const;

const identifier = z.string().min(1).max(128).regex(/^[A-Za-z0-9_.-]+$/u);
const argumentsSchema = z.array(z.object({ role: z.string().min(1), referent_id: z.string().min(1) }).strict());
const selectionSchema = z.array(z.object({ role: z.enum(["card", "target"]), referent_id: z.string().min(1) }).strict()).max(2);
const cursors = ["root", "information", "relic_inspect", "relic_tips", "card_tips",
  "power_tips", "intent_tips", "orb_tips", "topbar_tips", "card_targets", "card_confirmation"] as const;
const stagedCursors = new Set<string>(["card_targets", "card_confirmation"]);
const cardRoles = new Set(["card", "playable_card"]);
const targetRoles = new Set(["target", "enemy", "ally", "creature", "player", "companion"]);
const informationGroups = cursors.slice(2, 9);
const navigation = ["open_information", ...informationGroups.map(group => `open_${group}`), "back"];
const selectionVerbs = ["select_card", "select_target", "cancel_selection"] as const;
const actionSchema = z.object({
  action_id: identifier,
  kind: z.enum(["system_navigation", "system_selection", "native_input"]),
  verb: z.string().min(1), label: z.string().min(1),
  subject_referent_id: z.string().min(1).nullable(),
  arguments: argumentsSchema,
  effect_domain: z.enum(["text_menu", "native_input"])
}).strict().superRefine((action, ctx) => {
  if ((action.kind === "native_input") !== (action.effect_domain === "native_input")) {
    ctx.addIssue({ code: "custom", message: "action kind and effect domain disagree" });
  }
  if (action.kind === "system_navigation" &&
      (!navigation.includes(action.verb) || action.subject_referent_id !== null || action.arguments.length !== 0)) {
    ctx.addIssue({ code: "custom", message: "system navigation must be an operand-free edge" });
  }
  if (action.kind === "system_selection" &&
      (!selectionVerbs.includes(action.verb as typeof selectionVerbs[number]) ||
       action.arguments.length !== 0 ||
       (action.verb === "cancel_selection" ? action.subject_referent_id !== null : action.subject_referent_id === null))) {
    ctx.addIssue({ code: "custom", message: "system selection has invalid verb or subject" });
  }
  if (action.kind === "native_input" && selectionVerbs.includes(action.verb as typeof selectionVerbs[number])) {
    ctx.addIssue({ code: "custom", message: "menu selection verb cannot claim native delivery" });
  }
});
const menuSchema = z.object({
  cursor: z.enum(cursors), revision: z.number().int().nonnegative(),
  native_snapshot_id: z.string().min(1), selection: selectionSchema
}).strict();
const catalogSchema = z.object({
  status: z.enum(["complete", "truncated", "unavailable"]),
  materialized_count: z.number().int().nonnegative(), total_count: z.number().int().nonnegative(),
  ordering_semantics: z.string().min(1), actions: z.array(actionSchema)
}).strict();
const attributionSchema = z.object({
  runtime_instance_id: z.string().min(1), client_session_id: z.string().min(1),
  client_instance_id: z.string().min(1), product_id: z.string().min(1),
  product_name: z.string().min(1), product_version: z.string().min(1),
  controller_lease_id: z.string().min(1), controller_generation: z.number().int().positive()
}).strict();
const resultSchema = z.object({
  protocol_version: z.literal(SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL),
  schema: z.literal(TEXT_MENU_V2_RESULT_SCHEMA), input_profile: z.literal(TEXT_MENU_V2_PROFILE),
  request_id: z.string().min(1), status: z.enum(["applied", "not_applied", "unknown"]),
  effect_domain: z.enum(["text_menu", "native_input"]).nullable(),
  native_delivery: z.enum(["delivered", "not_delivered", "unknown"]).nullable(),
  action: actionSchema.nullable(), reason_code: z.string().nullable(), detail: z.string().nullable(),
  retry: z.enum(["never", "reobserve"]), successor: z.unknown().nullable(),
  attribution: attributionSchema.nullable()
}).strict();

export type TextMenuV2Action = z.infer<typeof actionSchema>;
export type TextMenuV2Snapshot = Omit<TextMenuSnapshot, "schema" | "input_profile" | "menu" | "menu_actions"> & {
  schema: typeof TEXT_MENU_V2_SNAPSHOT_SCHEMA;
  input_profile: typeof TEXT_MENU_V2_PROFILE;
  menu: z.infer<typeof menuSchema>;
  menu_actions: z.infer<typeof catalogSchema>;
};
export type TextMenuV2Capabilities = Omit<TextMenuCapabilities, "input_profile" | "snapshot_schema" | "receipt_schema"> & {
  input_profile: typeof TEXT_MENU_V2_PROFILE;
  snapshot_schema: typeof TEXT_MENU_V2_SNAPSHOT_SCHEMA;
  receipt_schema: typeof TEXT_MENU_V2_RESULT_SCHEMA;
};
export type TextMenuV2ActionResult = Omit<z.infer<typeof resultSchema>, "successor"> & {
  successor: TextMenuV2Snapshot | null;
};
export type TextMenuV2ObservationContext = {
  schema: typeof TEXT_MENU_V2_OBSERVATION_CONTEXT_SCHEMA;
  snapshot: TextMenuV2Snapshot;
  game_continuity_id: string | null;
};

function object(value: unknown, name: string): JsonObject {
  if (!isJsonObject(value)) throw new Error(`${name} is not an object`);
  return value;
}
function exactKeys(value: JsonObject, names: readonly string[], label: string): void {
  if (Object.keys(value).length !== names.length || names.some(name => !Object.hasOwn(value, name))) {
    throw new Error(`${label} has missing or unrecognized fields`);
  }
}
function parse<T>(value: unknown, schema: z.ZodType<T>, label: string): T {
  const parsed = schema.safeParse(value);
  if (!parsed.success) throw new Error(`${label} failed strict decoding: ${parsed.error.issues
    .map(issue => `${issue.path.join(".")}: ${issue.message}`).join("; ")}`);
  return parsed.data;
}
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (isJsonObject(value)) return Object.fromEntries(Object.entries(value)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, item]) => [key, canonical(item)]));
  return value;
}
function same(a: unknown, b: unknown): boolean { return JSON.stringify(canonical(a)) === JSON.stringify(canonical(b)); }

export function decodeTextMenuV2Capabilities(value: unknown): DecodedPlayerPayload<TextMenuV2Capabilities> {
  const raw = object(value, "text menu v2 capabilities");
  if (raw.input_profile !== TEXT_MENU_V2_PROFILE || raw.snapshot_schema !== TEXT_MENU_V2_SNAPSHOT_SCHEMA ||
      raw.receipt_schema !== TEXT_MENU_V2_RESULT_SCHEMA) throw new Error("text menu v2 capability profile or schema mismatch");
  const legacy = { ...raw, input_profile: TEXT_MENU_PROFILE,
    snapshot_schema: TEXT_MENU_SNAPSHOT_SCHEMA, receipt_schema: TEXT_MENU_RESULT_SCHEMA };
  const decoded = decodeTextMenuCapabilities(legacy).data;
  return { raw, data: { ...decoded, input_profile: TEXT_MENU_V2_PROFILE,
    snapshot_schema: TEXT_MENU_V2_SNAPSHOT_SCHEMA, receipt_schema: TEXT_MENU_V2_RESULT_SCHEMA } };
}

export function decodeTextMenuV2Snapshot(value: unknown): DecodedPlayerPayload<TextMenuV2Snapshot> {
  const raw = object(value, "text menu v2 snapshot");
  exactKeys(raw, ["protocol_version", "schema", "input_profile", "snapshot_id", "sequence", "observed_at",
    "status", "persistent", "interaction", "referents", "completeness", "session",
    "information_policy", "menu", "menu_actions"], "text menu v2 snapshot");
  if (raw.input_profile !== TEXT_MENU_V2_PROFILE || raw.schema !== TEXT_MENU_V2_SNAPSHOT_SCHEMA) {
    throw new Error("text menu v2 snapshot profile or schema mismatch");
  }
  const menu = parse(raw.menu, menuSchema, "text menu v2 cursor");
  const catalog = parse(raw.menu_actions, catalogSchema, "text menu v2 actions");
  const selection = menu.selection;
  if (!stagedCursors.has(menu.cursor) && selection.length !== 0 ||
      menu.cursor === "card_targets" && (selection.length !== 1 || selection[0]?.role !== "card") ||
      menu.cursor === "card_confirmation" && (selection.length < 1 || selection[0]?.role !== "card" ||
        selection.length === 2 && selection[1]?.role !== "target")) {
    throw new Error("text menu v2 cursor and selection disagree");
  }
  const referents = Array.isArray(raw.referents) ? raw.referents : [];
  const publicReferents = new Map(referents.filter(isJsonObject)
    .map(item => [item.referent_id, item]));
  const selectable = (id: string, roles: ReadonlySet<string>): boolean => {
    const referent = publicReferents.get(id);
    const state = referent?.state;
    return referent?.kind === "entity" && roles.has(String(referent.role)) &&
      isJsonObject(state) && state.visible === true && state.enabled !== false;
  };
  const interaction = isJsonObject(raw.interaction) ? raw.interaction : null;
  const content = isJsonObject(interaction?.content) ? interaction.content : null;
  const surface = isJsonObject(content?.surface) ? content.surface : null;
  const hasSystemSelection = catalog.actions.some(action => action.kind === "system_selection");
  if ((stagedCursors.has(menu.cursor) || hasSystemSelection) &&
      (interaction?.kind !== "combat_turn" || interaction.stage !== "ready" ||
       interaction.content_schema !== "sts2.player-environment/surface/combat_turn-1" ||
       surface?.kind !== "combat_turn")) {
    throw new Error("text menu v2 card staging requires a ready public combat page");
  }
  if (selection.some(item => !selectable(item.referent_id,
        item.role === "card" ? cardRoles : targetRoles)) ||
      catalog.actions.some(action => action.kind === "system_selection" &&
        action.verb !== "cancel_selection" && !selectable(action.subject_referent_id!,
          action.verb === "select_card" ? cardRoles : targetRoles))) {
    throw new Error("text menu v2 selection requires an enabled visible public card or target referent");
  }
  const allowedNavigation = menu.cursor === "root" ? ["open_information"] :
    menu.cursor === "information" ? [...navigation.filter(verb => verb !== "open_information")] : ["back"];
  const nav = catalog.actions.filter(action => action.kind === "system_navigation");
  if (nav.some(action => !allowedNavigation.includes(action.verb)) ||
      new Set(nav.map(action => action.verb)).size !== nav.length) {
    throw new Error("illegal or duplicate text menu v2 navigation edge");
  }
  const system = catalog.actions.filter(action => action.kind === "system_selection");
  if (system.some(action => menu.cursor === "root" ? action.verb !== "select_card" :
    menu.cursor === "card_targets" ? !["select_target", "cancel_selection"].includes(action.verb) :
    menu.cursor === "card_confirmation" ? action.verb !== "cancel_selection" : true) ||
    new Set(system.map(action => `${action.verb}:${action.subject_referent_id}`)).size !== system.length) {
    throw new Error("illegal or duplicate text menu v2 selection edge");
  }
  if (menu.cursor === "card_targets" && catalog.actions.some(action => action.kind === "native_input")) {
    throw new Error("card target selection cannot submit a native leaf");
  }
  if (menu.cursor === "card_targets" &&
      (!system.some(action => action.verb === "select_target") ||
       !system.some(action => action.verb === "cancel_selection"))) {
    throw new Error("card target menu lacks target or cancel choice");
  }
  if (menu.cursor === "card_confirmation") {
    const leaves = catalog.actions.filter(action => action.kind === "native_input");
    const expectedArguments = selection.length === 2
      ? [{ role: "target", referent_id: selection[1]!.referent_id }] : [];
    if (leaves.length !== 1 || !system.some(action => action.verb === "cancel_selection") ||
        leaves[0]?.verb !== "play" ||
        leaves[0].subject_referent_id !== selection[0]!.referent_id ||
        !same(leaves[0].arguments, expectedArguments)) {
      throw new Error("card confirmation must bind its public card and optional target");
    }
  }
  // Reuse v1 for every unchanged public Snapshot field, catalog count, referent and
  // interaction invariant. Only the new menu grammar is checked above.
  const normalized: JsonObject = { ...raw, input_profile: TEXT_MENU_PROFILE, schema: TEXT_MENU_SNAPSHOT_SCHEMA,
    menu: { cursor: "root", revision: menu.revision, native_snapshot_id: menu.native_snapshot_id },
    menu_actions: { ...catalog, actions: catalog.actions.map(action => ({ ...action,
      kind: "native_input", effect_domain: "native_input" })) } };
  decodeTextMenuSnapshot(normalized);
  return { raw, data: { ...raw, menu, menu_actions: catalog } as TextMenuV2Snapshot };
}

export function decodeTextMenuV2ObservationContext(value: unknown): DecodedPlayerPayload<TextMenuV2ObservationContext> {
  const raw = object(value, "text menu v2 observation context");
  exactKeys(raw, ["schema", "snapshot", "game_continuity_id"], "text menu v2 observation context");
  if (raw.schema !== TEXT_MENU_V2_OBSERVATION_CONTEXT_SCHEMA) throw new Error("text menu v2 context schema mismatch");
  const gameContinuityId = raw.game_continuity_id === null ? null :
    parse(raw.game_continuity_id, identifier, "game continuity identity");
  const snapshot = decodeTextMenuV2Snapshot(raw.snapshot).data;
  return { raw, data: { schema: TEXT_MENU_V2_OBSERVATION_CONTEXT_SCHEMA,
    snapshot, game_continuity_id: gameContinuityId } };
}

function expectedMenuSuccessor(action: TextMenuV2Action, previous: TextMenuV2Snapshot, next: TextMenuV2Snapshot): boolean {
  const current = previous.menu;
  const target = next.menu;
  if (next.snapshot_id === previous.snapshot_id || next.sequence <= previous.sequence ||
      !same(next.session, previous.session) || target.native_snapshot_id !== current.native_snapshot_id ||
      target.revision <= current.revision) return false;
  if (action.kind === "system_selection") {
    if (action.verb === "select_card") return current.cursor === "root" &&
      (target.cursor === "card_targets" || target.cursor === "card_confirmation") &&
      same(target.selection, [{ role: "card", referent_id: action.subject_referent_id }]);
    if (action.verb === "select_target") return current.cursor === "card_targets" &&
      target.cursor === "card_confirmation" && same(target.selection,
        [...current.selection, { role: "target", referent_id: action.subject_referent_id }]);
    return target.cursor === "root" && target.selection.length === 0;
  }
  if (action.verb === "back") {
    if (current.cursor === "card_confirmation") return current.selection.length === 2
      ? target.cursor === "card_targets" && same(target.selection, current.selection.slice(0, 1))
      : target.cursor === "root" && target.selection.length === 0;
    if (current.cursor === "card_targets") return target.cursor === "root" && target.selection.length === 0;
    return target.cursor === (current.cursor === "information" ? "root" : "information") &&
      target.selection.length === 0;
  }
  return action.verb === "open_information" ? current.cursor === "root" && target.cursor === "information" :
    current.cursor === "information" && target.cursor === action.verb.slice(5);
}

function intrinsicMenuSuccessor(action: TextMenuV2Action, next: TextMenuV2Snapshot): boolean {
  const target = next.menu;
  if (action.kind === "system_selection") {
    if (action.verb === "select_card") return (target.cursor === "card_targets" ||
      target.cursor === "card_confirmation") &&
      same(target.selection, [{ role: "card", referent_id: action.subject_referent_id }]);
    if (action.verb === "select_target") return target.cursor === "card_confirmation" &&
      target.selection.length === 2 && target.selection[1]?.referent_id === action.subject_referent_id;
    return target.cursor === "root" && target.selection.length === 0;
  }
  if (action.verb === "open_information") return target.cursor === "information";
  if (action.verb === "back") return ["root", "information", "card_targets"].includes(target.cursor);
  return target.cursor === action.verb.slice(5);
}

export function decodeTextMenuV2ActionResult(value: unknown, previous?: TextMenuV2Snapshot): DecodedPlayerPayload<TextMenuV2ActionResult> {
  const raw = object(value, "text menu v2 action result");
  const parsed = parse(raw, resultSchema, "text menu v2 action result");
  const action = parsed.action;
  if (previous !== undefined && (action === null ? parsed.status !== "not_applied" :
    !previous.menu_actions.actions.some(candidate => same(candidate, action)))) {
    throw new Error("text menu v2 result action is not in the previous complete menu");
  }
  if (parsed.status === "applied") {
    if (action === null || parsed.effect_domain !== action.effect_domain || parsed.retry !== "never" ||
        parsed.native_delivery !== (action.effect_domain === "text_menu" ? null : "delivered")) {
      throw new Error("applied text menu v2 result contradicts its effect domain");
    }
  } else if (parsed.status === "unknown") {
    if (action?.kind !== "native_input" || parsed.effect_domain !== "native_input" ||
        parsed.native_delivery !== "unknown" || parsed.retry !== "never" || parsed.successor !== null) {
      throw new Error("unknown native delivery must never permit retry or successor");
    }
  } else if (parsed.native_delivery === "delivered" || parsed.native_delivery === "unknown" ||
      parsed.effect_domain === "text_menu" && parsed.native_delivery !== null ||
      action === null && parsed.effect_domain !== null ||
      action !== null && parsed.effect_domain !== action.effect_domain ||
      parsed.successor !== null && parsed.retry !== "reobserve") {
    throw new Error("not-applied text menu v2 result has inconsistent delivery");
  }
  const successor = parsed.successor === null ? null : decodeTextMenuV2Snapshot(parsed.successor).data;
  if (successor !== null && previous !== undefined && !same(successor.session, previous.session)) {
    throw new Error("text menu v2 result successor changed session identity");
  }
  if (parsed.status === "applied" && action?.effect_domain === "text_menu") {
    if (successor === null || !intrinsicMenuSuccessor(action, successor) ||
        previous !== undefined && !expectedMenuSuccessor(action, previous, successor)) {
      throw new Error("system text menu v2 result lacks a valid menu successor");
    }
  }
  return { raw, data: { ...parsed, successor } };
}
