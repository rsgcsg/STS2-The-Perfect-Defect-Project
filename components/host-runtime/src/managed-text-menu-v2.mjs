import { createHash } from "node:crypto";
import { SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL } from "@rsgcsg/sts2-connector-client";
import { currentManagedNativeVerbs, managedTextMenuV1Contract,
  projectManagedTextMenuV1 } from "./managed-text-menu-map.mjs";

export const MANAGED_TEXT_MENU_V2_PROFILE = "text-menu-v2";
export const MANAGED_TEXT_MENU_V2_CONTEXT_SCHEMA = "sts2.player-environment/text-menu-observation-context-2";
export const MANAGED_TEXT_MENU_V2_SNAPSHOT_SCHEMA = "sts2.player-environment/text-menu-snapshot-2";
export const MANAGED_TEXT_MENU_V2_RESULT_SCHEMA = "sts2.player-environment/text-menu-action-result-2";
const SNAPSHOT_SCHEMA = MANAGED_TEXT_MENU_V2_SNAPSHOT_SCHEMA;
const RESULT_SCHEMA = MANAGED_TEXT_MENU_V2_RESULT_SCHEMA;
const TARGET_ROLES = new Set(["enemy", "target", "ally", "creature", "player", "companion"]);

export function managedTextMenuV2Contract() {
  const v1 = managedTextMenuV1Contract();
  return {
    protocol_version: SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL,
    input_profile: MANAGED_TEXT_MENU_V2_PROFILE,
    snapshot_schema: MANAGED_TEXT_MENU_V2_SNAPSHOT_SCHEMA,
    receipt_schema: MANAGED_TEXT_MENU_V2_RESULT_SCHEMA,
    interaction_kinds: v1.interaction_kinds,
    observed_terminal_kinds: v1.observed_terminal_kinds,
    action_verbs: [...currentManagedNativeVerbs(),
      "select_card", "select_target", "cancel_selection", "back"]
  };
}

function identity(value) {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex").slice(0, 32);
}

function sourceIdentity(source) {
  // The native Snapshot ID alone does not bind a revised public catalog.
  return identity({ session: source.session, snapshot: source.snapshot_id,
    status: source.status, interaction: source.interaction,
    referents: source.referents, completeness: source.completeness,
    bound_actions: source.bound_actions });
}

function selectable(referent, roles) {
  return referent?.kind === "entity" && roles.has(referent.role)
    && referent.state?.visible === true && referent.state.enabled !== false;
}

function menuCapabilities(actions) {
  const values = new Map();
  for (const action of actions) {
    const capability = { verb: action.verb,
      ...(action.subject_referent_id === null ? {} : { subject_role: "subject" }),
      arguments: action.arguments.map((argument) => ({ role: argument.role, required: true })),
      availability_basis: "exact_current_text_menu" };
    values.set(JSON.stringify(capability), capability);
  }
  return [...values.values()];
}

function exactCombatPairs(source) {
  const surface = source.interaction?.content?.surface;
  if (source.interaction?.kind !== "combat_turn" || source.interaction.stage !== "ready"
      || source.interaction.content_schema !== "sts2.player-environment/surface/combat_turn-1"
      || surface?.kind !== "combat_turn" || !Array.isArray(surface.playable_cards)) return null;

  const referents = new Map(source.referents.map((item) => [item.referent_id, item]));
  const pairs = [];
  const byCard = new Map();
  for (const bound of source.bound_actions.actions.filter((action) => action.verb === "play")) {
    const card = referents.get(bound.subject_referent_id);
    if (!selectable(card, new Set(["playable_card"])) || bound.arguments.length > 1) return null;
    const argument = bound.arguments[0];
    const target = argument == null ? null : referents.get(argument.referent_id);
    if (argument != null && (argument.role !== "target" || !selectable(target, TARGET_ROLES))) return null;
    const group = byCard.get(card.referent_id) ?? [];
    if (group.some((pair) => pair.targetId === (target?.referent_id ?? null))) return null;
    const pair = { bound, cardId: card.referent_id, targetId: target?.referent_id ?? null };
    group.push(pair);
    byCard.set(card.referent_id, group);
    pairs.push(pair);
  }

  // This checks agreement between two public projections of the same complete
  // native-backed catalog. It does not infer playable targets or create operands.
  const advertised = surface.playable_cards;
  if (advertised.length !== byCard.size ||
      new Set(advertised.map((item) => item?.entity_id)).size !== advertised.length) return null;
  for (const card of advertised) {
    const group = byCard.get(card?.entity_id);
    if (!group || !Array.isArray(card.target_entity_ids)) return null;
    const targetIds = group.map((pair) => pair.targetId).filter((id) => id !== null);
    if (targetIds.length === 0 && group.length !== 1) return null;
    if (targetIds.length > 0 && targetIds.length !== group.length) return null;
    if (targetIds.length !== card.target_entity_ids.length ||
        new Set(targetIds).size !== targetIds.length ||
        new Set(card.target_entity_ids).size !== card.target_entity_ids.length ||
        targetIds.some((id) => !card.target_entity_ids.includes(id))) return null;
  }
  return pairs;
}

/** Text-only card intent over the existing complete Managed BoundAction catalog. */
export class ManagedTextMenuV2SessionAdapter {
  #session;
  #sourceIdentity = null;
  #cursor = "root";
  #cardId = null;
  #targetId = null;
  #revision = 0;
  #sequence = 0;
  #lastSnapshotId = null;
  #requests = new Map();
  #mutationTail = Promise.resolve();

  constructor(session) {
    if (session == null || typeof session.observe !== "function" || typeof session.submit !== "function") {
      throw new TypeError("ManagedTextMenuV2SessionAdapter requires one Managed Player Environment session.");
    }
    this.#session = session;
  }

  resetSelection() {
    this.#cursor = "root";
    this.#cardId = this.#targetId = null;
    this.#sourceIdentity = null;
    this.#revision++;
  }

  observe() { return this.#project(this.#session.observe()).snapshot; }

  #project(source) {
    const sourceKey = sourceIdentity(source);
    if (sourceKey !== this.#sourceIdentity) {
      this.#sourceIdentity = sourceKey;
      this.#cursor = "root";
      this.#cardId = this.#targetId = null;
      this.#revision++;
    }
    const base = projectManagedTextMenuV1(source, this.#session.tainted !== true);
    const complete = base.menu_actions.status === "complete" && base.status === "interactive";
    const combat = complete && source.interaction.kind === "combat_turn";
    const pairs = combat ? exactCombatPairs(source) : [];
    const ready = complete && (!combat || pairs !== null);
    if (!ready && this.#cursor !== "root") {
      this.#cursor = "root";
      this.#cardId = this.#targetId = null;
      this.#revision++;
    }
    if (this.#cardId !== null && !pairs?.some((pair) => pair.cardId === this.#cardId
        && (this.#targetId === null || pair.targetId === this.#targetId))) {
      this.#cursor = "root";
      this.#cardId = this.#targetId = null;
      this.#revision++;
    }
    const snapshotId = `managed_tm2_${identity({ sourceKey, cursor: this.#cursor,
      card: this.#cardId, target: this.#targetId, revision: this.#revision })}`;
    if (snapshotId !== this.#lastSnapshotId) {
      this.#sequence++;
      this.#lastSnapshotId = snapshotId;
    }
    const actions = [];
    const choices = new Map();
    const referents = new Map((Array.isArray(source.referents) ? source.referents : [])
      .map((item) => [item.referent_id, item]));
    const label = (role, id, ids) => {
      const name = referents.get(id)?.label;
      const same = source.referents.filter((item) => ids.includes(item.referent_id)
        && item.label === name).map((item) => item.referent_id);
      const suffix = same.length > 1 ? ` (${same.indexOf(id) + 1} of ${same.length} shown)` : "";
      return `Choose ${role}${typeof name === "string" && name.trim() ? ` ${name}` : ""}${suffix}`;
    };
    const add = (key, kind, verb, text, subject = null, args = [], bound = null) => {
      const action = { action_id: `managed_tm2_action_${identity({ snapshotId, key, kind })}`,
        kind, verb, label: text, subject_referent_id: subject,
        arguments: args, effect_domain: kind === "native_input" ? "native_input" : "text_menu" };
      actions.push(action);
      choices.set(action.action_id, { action, bound });
    };
    if (ready && this.#cursor === "root") {
      for (const bound of source.bound_actions.actions.filter((item) => !(combat && item.verb === "play"))) {
        add(bound.bound_action_id, "native_input", bound.verb, bound.label,
          bound.subject_referent_id, bound.arguments, bound);
      }
      if (combat) {
        const cardIds = [...new Set(pairs.map((pair) => pair.cardId))];
        for (const cardId of cardIds) add(`select_card:${cardId}`, "system_selection",
          "select_card", label("card", cardId, cardIds), cardId);
      }
    } else if (ready && this.#cursor === "card_targets") {
      const cardPairs = pairs.filter((pair) => pair.cardId === this.#cardId);
      const targetIds = cardPairs.map((pair) => pair.targetId);
      for (const pair of cardPairs) add(`select_target:${pair.targetId}`, "system_selection",
        "select_target", label("target", pair.targetId, targetIds), pair.targetId);
      add("cancel_selection", "system_selection", "cancel_selection", "Cancel selection");
      add("back", "system_navigation", "back", "Back");
    } else if (ready && this.#cursor === "card_confirmation") {
      const pair = pairs.find((item) => item.cardId === this.#cardId
        && item.targetId === this.#targetId);
      if (pair) add(pair.bound.bound_action_id, "native_input", "play", pair.bound.label,
        pair.cardId, pair.bound.arguments, pair.bound);
      add("cancel_selection", "system_selection", "cancel_selection", "Cancel selection");
      add("back", "system_navigation", "back", "Back");
    }
    const selection = this.#cardId === null ? [] : this.#targetId === null
      ? [{ role: "card", referent_id: this.#cardId }]
      : [{ role: "card", referent_id: this.#cardId },
        { role: "target", referent_id: this.#targetId }];
    const supported = ready && actions.length > 0;
    const snapshot = { ...base, schema: SNAPSHOT_SCHEMA, input_profile: MANAGED_TEXT_MENU_V2_PROFILE,
      snapshot_id: snapshotId, sequence: this.#sequence,
      status: supported ? "interactive" : base.status === "observed" ? "observed" : "visible_unsupported",
      interaction: { ...base.interaction, capabilities: supported ? menuCapabilities(actions) : [] },
      menu: { cursor: this.#cursor, revision: this.#revision,
        native_snapshot_id: source.snapshot_id, selection },
      menu_actions: { status: supported || base.status === "observed" ? "complete" : "unavailable",
        materialized_count: actions.length, total_count: actions.length,
        ordering_semantics: "native_order_with_text_card_selection", actions } };
    return { snapshot, choices, pairs, sourceKey };
  }

  async submit({ request_id, expected_snapshot_id, action_id, input_profile, timeout_ms }) {
    for (const value of [request_id, expected_snapshot_id, action_id]) {
      if (typeof value !== "string" || value.length === 0) {
        throw new TypeError("Text-menu v2 submit requires request, Snapshot and action IDs.");
      }
    }
    const fingerprint = JSON.stringify({ input_profile, expected_snapshot_id, action_id });
    const prior = this.#requests.get(request_id);
    if (prior) return prior.fingerprint === fingerprint ? prior.result ?? prior.promise
      : this.#result(request_id, { status: "not_applied", effect_domain: null,
        native_delivery: null, action: null, reason_code: "request_id_conflict",
        detail: "Request ID belongs to another exact action or profile.", retry: "reobserve", successor: null });
    if (input_profile !== MANAGED_TEXT_MENU_V2_PROFILE) {
      const result = this.#result(request_id, { status: "not_applied", effect_domain: null,
        native_delivery: null, action: null, reason_code: "invalid_text_menu_request",
        detail: "An exact text-menu-v2 profile is required.", retry: "reobserve", successor: null });
      this.#requests.set(request_id, { fingerprint, result });
      return result;
    }
    const pending = this.#mutationTail.then(() => this.#dispatch({
      requestId: request_id, expectedSnapshotId: expected_snapshot_id,
      actionId: action_id, timeoutMs: timeout_ms }));
    this.#mutationTail = pending.catch(() => undefined);
    this.#requests.set(request_id, { fingerprint, promise: pending });
    return pending.then((result) => { this.#requests.set(request_id, { fingerprint, result }); return result; });
  }

  async #dispatch({ requestId, expectedSnapshotId, actionId, timeoutMs }) {
    const source = this.#session.observe();
    const current = this.#project(source);
    if (this.#session.tainted === true) {
      return this.#result(requestId, { status: "not_applied", effect_domain: null,
        native_delivery: null, action: null,
        reason_code: this.#session.taintReason === "successor_projection_failed"
          ? "runtime_tainted_after_successor_projection_failure" : "runtime_tainted_after_unknown",
        detail: "Managed mutation authority is closed.", retry: "never", successor: null });
    }
    const choice = current.choices.get(actionId);
    if (expectedSnapshotId !== current.snapshot.snapshot_id || !choice
        || current.snapshot.menu_actions.status !== "complete") {
      return this.#result(requestId, { status: "not_applied", effect_domain: null,
        native_delivery: null, action: null, reason_code: "stale_or_unadvertised_action",
        detail: "Only a current advertised text-menu v2 action may be submitted.",
        retry: "reobserve", successor: current.snapshot });
    }
    const action = choice.action;
    if (choice.bound === null) {
      if (action.verb === "select_card") {
        this.#cardId = action.subject_referent_id;
        this.#targetId = null;
        this.#cursor = current.pairs.some((pair) => pair.cardId === this.#cardId
          && pair.targetId !== null) ? "card_targets" : "card_confirmation";
      } else if (action.verb === "select_target") {
        this.#targetId = action.subject_referent_id;
        this.#cursor = "card_confirmation";
      } else if (action.verb === "cancel_selection" || action.verb === "back") {
        if (action.verb === "back" && this.#cursor === "card_confirmation" && this.#targetId !== null) {
          this.#targetId = null;
          this.#cursor = "card_targets";
        } else {
          this.#cardId = this.#targetId = null;
          this.#cursor = "root";
        }
      }
      this.#revision++;
      return this.#result(requestId, { status: "applied", effect_domain: "text_menu",
        native_delivery: null, action, reason_code: null,
        detail: "Only the text menu selection changed; no native input was delivered.",
        retry: "never", successor: this.#project(source).snapshot });
    }
    const stillBound = source.bound_actions.actions.find((item) =>
      item.bound_action_id === choice.bound.bound_action_id);
    if (!stillBound || JSON.stringify(stillBound) !== JSON.stringify(choice.bound)) {
      this.resetSelection();
      return this.#result(requestId, { status: "not_applied", effect_domain: null,
        native_delivery: null, action: null, reason_code: "stale_snapshot",
        detail: "The complete source binding changed before native submission.",
        retry: "reobserve", successor: this.#project(source).snapshot });
    }
    const receipt = await this.#session.submit({ requestId,
      expectedSnapshotId: source.snapshot_id,
      boundActionId: choice.bound.bound_action_id,
      ...(timeoutMs == null ? {} : { timeoutMs }) });
    if (receipt.delivery === "unknown") {
      this.resetSelection();
      return this.#result(requestId, { status: "unknown", effect_domain: "native_input",
        native_delivery: "unknown", action, reason_code: receipt.reason_code,
        detail: receipt.detail, retry: "never", successor: null });
    }
    const successor = receipt.successor == null ? null : this.#project(receipt.successor).snapshot;
    const delivered = receipt.delivery === "delivered";
    return this.#result(requestId, { status: delivered ? "applied" : "not_applied",
      effect_domain: "native_input", native_delivery: delivered ? "delivered" : "not_delivered",
      action, reason_code: receipt.reason_code, detail: receipt.detail,
      retry: delivered || successor == null ? "never" : "reobserve", successor });
  }

  #result(requestId, result) {
    return { protocol_version: SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL,
      schema: RESULT_SCHEMA, input_profile: MANAGED_TEXT_MENU_V2_PROFILE,
      request_id: requestId, ...result, attribution: null };
  }
}
