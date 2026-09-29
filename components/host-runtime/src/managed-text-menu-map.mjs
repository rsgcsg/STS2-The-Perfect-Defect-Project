import { createHash } from "node:crypto";
import { SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL } from "@rsgcsg/sts2-connector-client";

export const MANAGED_TEXT_MENU_PROFILE = "text-menu-v1";
export const MANAGED_TEXT_MENU_SNAPSHOT_SCHEMA = "sts2.player-environment/text-menu-snapshot-1";
export const MANAGED_TEXT_MENU_RESULT_SCHEMA = "sts2.player-environment/text-menu-action-result-1";

function stableActionId(snapshotId, boundActionId) {
  const digest = createHash("sha256").update(`${snapshotId}\0${boundActionId}`).digest("hex").slice(0, 32);
  return `managed_${digest}`;
}

function textSnapshotId(snapshotId) {
  return `managed_tm_${createHash("sha256").update(snapshotId).digest("hex").slice(0, 32)}`;
}

const REVIEWED_TEXT_MENU_SURFACES = new Set([
  "map_navigation", "event_option", "rest_site", "deck_upgrade_selection",
  "deck_card_selection", "shop_inventory", "combat_turn",
  "treasure_chest", "treasure_relic_selection", "treasure_completion",
  "reward_claim", "card_reward_selection", "reward_completion"
]);

function completeCurrentLeaf(snapshot) {
  const interactionId = snapshot?.interaction?.interaction_id;
  const actions = snapshot?.bound_actions?.actions;
  const referents = snapshot?.referents;
  if (!REVIEWED_TEXT_MENU_SURFACES.has(snapshot?.interaction?.kind)
    || snapshot.status !== "interactive"
    || snapshot.completeness?.status !== "complete"
    || snapshot.bound_actions?.status !== "complete"
    || !Array.isArray(referents)
    || !Array.isArray(actions)
    || actions.length === 0
    || !Number.isSafeInteger(snapshot.bound_actions.materialized_count)
    || !Number.isSafeInteger(snapshot.bound_actions.total_count)
    || snapshot.bound_actions.materialized_count !== actions.length
    || snapshot.bound_actions.total_count !== actions.length
    || typeof interactionId !== "string" || interactionId.length === 0) return false;

  const referentIds = new Set();
  for (const referent of referents) {
    if (typeof referent?.referent_id !== "string" || referent.referent_id.length === 0
      || referentIds.has(referent.referent_id)) return false;
    referentIds.add(referent.referent_id);
  }
  const actionIds = new Set();
  return actions.every((action) => {
    if (typeof action?.bound_action_id !== "string" || action.bound_action_id.length === 0
      || actionIds.has(action.bound_action_id)
      || action.interaction_id !== interactionId
      || typeof action.verb !== "string" || action.verb.length === 0
      || typeof action.label !== "string"
      || !Array.isArray(action.arguments)
      || (action.subject_referent_id != null && !referentIds.has(action.subject_referent_id))) return false;
    actionIds.add(action.bound_action_id);
    return action.arguments.every((argument) => typeof argument?.role === "string"
      && argument.role.length > 0
      && typeof argument.referent_id === "string"
      && referentIds.has(argument.referent_id));
  });
}

function completeObservedTerminal(snapshot) {
  const surface = snapshot?.interaction?.content?.surface;
  const bound = snapshot?.bound_actions;
  return snapshot?.status === "observed"
    && snapshot?.interaction?.kind === "game_over"
    && snapshot.interaction.stage === "complete"
    && typeof snapshot.interaction.interaction_id === "string"
    && snapshot.interaction.interaction_id.length > 0
    && Array.isArray(snapshot.interaction.capabilities)
    && snapshot.interaction.capabilities.length === 0
    && surface?.kind === "game_over" && surface.stage === "complete"
    && typeof surface.victory === "boolean"
    && snapshot.completeness?.status === "complete"
    && Array.isArray(snapshot.referents)
    && bound?.status === "complete"
    && bound.materialized_count === 0 && bound.total_count === 0
    && Array.isArray(bound.actions) && bound.actions.length === 0;
}

function project(snapshot, allowActions = true) {
  const supported = allowActions && completeCurrentLeaf(snapshot);
  const terminal = completeObservedTerminal(snapshot);
  const actions = supported ? snapshot.bound_actions.actions.map((bound) => ({
    action_id: stableActionId(snapshot.snapshot_id, bound.bound_action_id),
    kind: "native_input",
    verb: bound.verb,
    label: bound.label,
    subject_referent_id: bound.subject_referent_id,
    arguments: bound.arguments ?? [],
    effect_domain: "native_input"
  })) : [];
  const { bound_actions: _boundActions, reads: _reads, ...publicSnapshot } = snapshot;
  return {
    ...publicSnapshot,
    snapshot_id: textSnapshotId(snapshot.snapshot_id),
    schema: MANAGED_TEXT_MENU_SNAPSHOT_SCHEMA,
    input_profile: MANAGED_TEXT_MENU_PROFILE,
    status: supported && actions.length > 0 ? "interactive"
      : terminal ? "observed" : "visible_unsupported",
    interaction: { ...snapshot.interaction, capabilities: [] },
    menu: { cursor: "root", revision: 0, native_snapshot_id: snapshot.snapshot_id },
    menu_actions: {
      status: supported || terminal ? "complete" : "unavailable",
      materialized_count: actions.length,
      total_count: supported ? actions.length : 0,
      ordering_semantics: supported || terminal
        ? snapshot.bound_actions.ordering_semantics : "unavailable",
      actions
    }
  };
}

/** In-process projection of reviewed complete current Managed decision leaves. */
export class ManagedTextMenuSessionAdapter {
  #session;
  #bindings = new Map();
  // Preserve request-ID replay semantics for this session, including unknown outcomes.
  #requests = new Map();
  #mutationTail = Promise.resolve();

  constructor(session) {
    if (session == null || typeof session.observe !== "function" || typeof session.submit !== "function") {
      throw new TypeError("ManagedTextMenuSessionAdapter requires a Managed Player Environment session.");
    }
    this.#session = session;
  }

  observe() {
    return this.#remember(this.#session.observe());
  }

  #remember(source) {
    const snapshot = project(source, this.#session.tainted !== true);
    const actions = new Map();
    if (snapshot.menu_actions.status === "complete") {
      for (const [index, action] of snapshot.menu_actions.actions.entries()) {
        actions.set(action.action_id, {
          boundActionId: source.bound_actions.actions[index].bound_action_id,
          publicAction: action
        });
      }
    }
    // Only the latest page needs native bindings; older snapshot IDs fail closed.
    this.#bindings.clear();
    this.#bindings.set(snapshot.snapshot_id, { nativeSnapshotId: source.snapshot_id, actions });
    return snapshot;
  }

  async submit({ request_id, expected_snapshot_id, action_id, input_profile, timeout_ms }) {
    if (typeof request_id !== "string" || request_id.length === 0
        || typeof expected_snapshot_id !== "string" || expected_snapshot_id.length === 0
        || typeof action_id !== "string" || action_id.length === 0) {
      throw new TypeError("Text-menu submit requires request_id, expected_snapshot_id, and action_id.");
    }
    const fingerprint = JSON.stringify({
      input_profile, expected_snapshot_id, action_id
    });
    const previous = this.#requests.get(request_id);
    if (previous != null) {
      return previous.fingerprint === fingerprint
        ? previous.result ?? previous.promise
        : this.#result(request_id, {
          status: "not_applied", effect_domain: null, native_delivery: null,
          action: null, reason_code: "request_id_conflict",
          detail: "This request ID already belongs to another exact action or profile.",
          retry: "reobserve", successor: null
        });
    }
    if (input_profile !== MANAGED_TEXT_MENU_PROFILE) {
      return this.#save(request_id, fingerprint, this.#result(request_id, {
        status: "not_applied", effect_domain: null, native_delivery: null,
        action: null, reason_code: "invalid_text_menu_request",
        detail: "An exact text-menu-v1 profile is required.", retry: "reobserve", successor: null
      }));
    }
    const binding = this.#bindings.get(expected_snapshot_id);
    const entry = binding?.actions.get(action_id);
    if (entry == null) {
      const current = this.observe();
      return this.#save(request_id, fingerprint, this.#result(request_id, {
        status: "not_applied", effect_domain: null, native_delivery: null,
        action: null, reason_code: "stale_or_unadvertised_action",
        detail: "Only a current advertised Managed text-menu leaf can be submitted.",
        retry: "reobserve", successor: current
      }));
    }
    const pending = this.#mutationTail.then(() => this.#dispatch({
      requestId: request_id,
      fingerprint,
      binding,
      entry,
      timeoutMs: timeout_ms
    }));
    this.#mutationTail = pending.catch(() => undefined);
    this.#requests.set(request_id, { fingerprint, promise: pending });
    return pending;
  }

  async #dispatch({ requestId, fingerprint, binding, entry, timeoutMs }) {
    const current = this.#session.observe();
    if (this.#session.tainted === true) {
      const successor = this.#remember(current);
      return this.#save(requestId, fingerprint, this.#result(requestId, {
        status: "not_applied", effect_domain: null, native_delivery: null,
        action: null, reason_code: "runtime_tainted_after_unknown",
        detail: "Unknown native delivery closed mutation authority for this Managed session.",
        retry: "reobserve", successor
      }));
    }
    if (current.snapshot_id !== binding.nativeSnapshotId
        || !current.bound_actions?.actions.some((action) => action.bound_action_id === entry.boundActionId)) {
      const successor = this.#remember(current);
      return this.#save(requestId, fingerprint, this.#result(requestId, {
        status: "not_applied", effect_domain: null, native_delivery: null,
        action: null, reason_code: "stale_snapshot",
        detail: "The Managed page changed before this queued action could execute.",
        retry: "reobserve", successor
      }));
    }
    const receipt = await this.#session.submit({
      requestId,
      expectedSnapshotId: binding.nativeSnapshotId,
      boundActionId: entry.boundActionId,
      ...(timeoutMs == null ? {} : { timeoutMs })
    });
    const nativeDelivery = receipt.delivery === "delivered" ? "delivered"
      : receipt.delivery === "unknown" ? "unknown" : "not_delivered";
    const unknown = nativeDelivery === "unknown";
    const successor = receipt.successor == null ? null : this.#remember(receipt.successor);
    if (unknown) this.#bindings.clear();
    return this.#save(requestId, fingerprint, this.#result(requestId, {
      status: unknown ? "unknown" : nativeDelivery === "delivered" ? "applied" : "not_applied",
      effect_domain: "native_input",
      native_delivery: nativeDelivery,
      action: entry.publicAction,
      reason_code: receipt.reason_code,
      detail: receipt.detail,
      retry: unknown ? "never" : nativeDelivery === "delivered" ? "never" : successor == null ? "never" : "reobserve",
      successor
    }));
  }

  #result(requestId, result) {
    return {
      protocol_version: SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL,
      schema: MANAGED_TEXT_MENU_RESULT_SCHEMA,
      input_profile: MANAGED_TEXT_MENU_PROFILE,
      request_id: requestId,
      ...result,
      attribution: null
    };
  }

  #save(requestId, fingerprint, result) {
    this.#requests.set(requestId, { fingerprint, result });
    return result;
  }
}

// Retain the original consumer import while the supported scene set grows.
export const ManagedTextMenuMapSessionAdapter = ManagedTextMenuSessionAdapter;
