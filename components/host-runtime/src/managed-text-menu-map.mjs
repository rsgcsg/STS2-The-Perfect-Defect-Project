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

function completeCurrentLeaf(snapshot) {
  const restReferents = (snapshot?.referents ?? []).filter((referent) =>
    (referent.role === "rest_option" || referent.role === "rest_room")
    && referent.state.enabled === true);
  const restCanProceed = snapshot?.interaction?.content?.surface?.can_proceed === true;
  const map = snapshot?.interaction?.kind === "map_navigation"
    && snapshot.status === "interactive"
    && snapshot.completeness?.visible_information === "contract_complete_for_visible_singleplayer_map_navigation"
    && snapshot.completeness?.interaction_discovery === "derived_from_exact_current_travelable_map_point_controls";
  const rest = snapshot?.interaction?.kind === "rest_site"
    && snapshot.interaction.stage === "choosing"
    && snapshot.status === "interactive"
    && snapshot.completeness?.visible_information === "contract_complete_for_current_native_interaction"
    && snapshot.completeness?.interaction_discovery === "derived_from_same_current_native_interaction_as_execution"
    && restReferents.filter((referent) => referent.role === "rest_room").length === (restCanProceed ? 1 : 0)
    && snapshot.bound_actions?.actions.length === restReferents.length
    && snapshot.bound_actions.actions.every((action) => restReferents.some((referent) =>
      referent.referent_id === action.subject_referent_id));
  const upgrade = snapshot?.interaction?.kind === "deck_upgrade_selection"
    && ["selecting", "preview"].includes(snapshot.interaction.stage)
    && snapshot.status === "interactive"
    && snapshot.completeness?.visible_information === "contract_complete_for_current_native_interaction"
    && snapshot.completeness?.interaction_discovery === "derived_from_same_current_native_interaction_as_execution"
    && snapshot.bound_actions?.actions.every((action) =>
      ["select", "deselect", "cancel", "confirm"].includes(action.verb)
      && (action.verb === "select" || action.verb === "deselect"
        ? snapshot.referents.some((referent) => referent.role === "card"
          && referent.referent_id === action.subject_referent_id)
        : action.subject_referent_id == null));
  const combat = snapshot?.interaction?.kind === "combat_turn"
    && snapshot.interaction.stage === "ready"
    && snapshot.status === "interactive"
    && snapshot.completeness?.visible_information === "contract_complete_for_immediate_combat_turn_including_visible_companions; pile contents available through a separate read-only Player Environment Read"
    && snapshot.completeness?.interaction_discovery === "derived_from_same_validator_as_execution"
    && snapshot.interaction.content?.context?.kind === "combat"
    && snapshot.interaction.content.context.turn_owner === "player"
    && snapshot.interaction.content.context.is_play_phase === true
    && snapshot.interaction.content.surface?.can_end_turn === true;
  const referentById = new Map((snapshot?.referents ?? []).map((referent) => [referent.referent_id, referent]));
  const targetArgumentValid = (action, subject) => {
    const args = action.arguments ?? [];
    if (args.length === 0) return true;
    if (args.length !== 1 || args[0].role !== "target") return false;
    const target = referentById.get(args[0].referent_id);
    return target?.role === "enemy"
      && (subject?.properties?.target_entity_ids ?? []).includes(target.referent_id);
  };
  const combatActionValid = (action) => {
    const subject = action.subject_referent_id == null ? null : referentById.get(action.subject_referent_id);
    const context = snapshot.interaction.content.context;
    const targetType = subject?.role === "playable_card"
      ? context.player?.hand?.find((card) => card.entity_id === subject.referent_id)?.target_type
      : subject?.role === "usable_potion"
        ? context.player?.potion_states?.find((potion) => potion.entity_id === subject.referent_id)?.target_type
        : null;
    if (action.verb === "end_turn") return subject === null && (action.arguments ?? []).length === 0;
    if (action.verb === "play") {
      return subject?.role === "playable_card"
        && (targetType === "AnyEnemy"
          ? (action.arguments ?? []).length === 1 && targetArgumentValid(action, subject)
          : ["Self", "AllEnemies", "AllCharacters", "None"].includes(targetType)
            && (action.arguments ?? []).length === 0);
    }
    if (action.verb === "use") {
      return subject?.role === "usable_potion"
        && (targetType === "AnyEnemy"
          ? (action.arguments ?? []).length === 1 && targetArgumentValid(action, subject)
          : ["Self", "AllEnemies", "AllCharacters", "None"].includes(targetType)
            && (action.arguments ?? []).length === 0);
    }
    return action.verb === "activate" && subject?.role === "usable_potion"
      && (action.arguments ?? []).length === 0;
  };
  return (map || rest || upgrade || combat)
    && snapshot.bound_actions?.status === "complete"
    && snapshot.bound_actions.actions.length > 0
    && snapshot.bound_actions.actions.every((action) => (combat
      ? combatActionValid(action)
      : upgrade
        ? true
        : action.verb === "activate")
      && typeof action.bound_action_id === "string"
      && typeof action.label === "string"
      && (combat || (action.arguments ?? []).length === 0)
      && (combat || upgrade || (action.subject_referent_id != null
        && snapshot.referents.some((referent) => referent.referent_id === action.subject_referent_id))));
}

function project(snapshot, allowActions = true) {
  const supported = allowActions && completeCurrentLeaf(snapshot);
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
    status: supported && actions.length > 0 ? "interactive" : "visible_unsupported",
    interaction: { ...snapshot.interaction, capabilities: [] },
    menu: { cursor: "root", revision: 0, native_snapshot_id: snapshot.snapshot_id },
    menu_actions: {
      status: supported ? "complete" : "unavailable",
      materialized_count: actions.length,
      total_count: supported ? actions.length : 0,
      ordering_semantics: supported ? snapshot.bound_actions.ordering_semantics : "unavailable",
      actions
    }
  };
}

/** In-process projection of complete current Managed map, rest, deck-upgrade, and direct combat leaves. */
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
