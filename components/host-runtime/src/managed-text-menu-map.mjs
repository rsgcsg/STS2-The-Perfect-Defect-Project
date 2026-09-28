import { createHash } from "node:crypto";

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

function completeMap(snapshot) {
  return snapshot?.interaction?.kind === "map_navigation"
    && snapshot.status === "interactive"
    && snapshot.completeness?.visible_information === "contract_complete_for_visible_singleplayer_map_navigation"
    && snapshot.completeness?.interaction_discovery === "derived_from_exact_current_travelable_map_point_controls"
    && snapshot.bound_actions?.status === "complete"
    && snapshot.bound_actions.actions.length > 0
    && snapshot.bound_actions.actions.every((action) => action.verb === "activate"
      && typeof action.bound_action_id === "string"
      && typeof action.label === "string"
      && action.subject_referent_id != null
      && (action.arguments ?? []).length === 0
      && snapshot.referents.some((referent) => referent.referent_id === action.subject_referent_id));
}

function project(snapshot, allowActions = true) {
  const supported = allowActions && completeMap(snapshot);
  const actions = supported ? snapshot.bound_actions.actions.map((bound) => ({
    action_id: stableActionId(snapshot.snapshot_id, bound.bound_action_id),
    kind: "native_input",
    verb: bound.verb,
    label: bound.label,
    subject_referent_id: bound.subject_referent_id,
    arguments: [],
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

/** In-process, opt-in projection of exact Managed map bindings into text-menu-v1. */
export class ManagedTextMenuMapSessionAdapter {
  #session;
  #bindings = new Map();

  constructor(session) {
    if (session == null || typeof session.observe !== "function" || typeof session.submit !== "function") {
      throw new TypeError("ManagedTextMenuMapSessionAdapter requires a Managed Player Environment session.");
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
        actions.set(action.action_id, source.bound_actions.actions[index].bound_action_id);
      }
    }
    this.#bindings.set(snapshot.snapshot_id, { nativeSnapshotId: source.snapshot_id, actions });
    return snapshot;
  }

  async submit({ request_id, expected_snapshot_id, action_id, input_profile, timeout_ms }) {
    if (input_profile !== MANAGED_TEXT_MENU_PROFILE) {
      throw new TypeError("Managed text-menu adapter requires input_profile text-menu-v1.");
    }
    if (typeof request_id !== "string" || request_id.length === 0
        || typeof expected_snapshot_id !== "string" || expected_snapshot_id.length === 0
        || typeof action_id !== "string" || action_id.length === 0) {
      throw new TypeError("Text-menu submit requires request_id, expected_snapshot_id, and action_id.");
    }
    const binding = this.#bindings.get(expected_snapshot_id);
    const boundActionId = binding?.actions.get(action_id);
    if (boundActionId == null) {
      const current = this.observe();
      return {
        protocol_version: current.protocol_version,
        schema: MANAGED_TEXT_MENU_RESULT_SCHEMA,
        input_profile: MANAGED_TEXT_MENU_PROFILE,
        request_id,
        status: "not_applied",
        effect_domain: "native_input",
        native_delivery: "not_delivered",
        action: null,
        reason_code: "stale_or_unadvertised_action",
        detail: "Only a current advertised map leaf can be submitted.",
        retry: "reobserve",
        successor: current,
        attribution: null
      };
    }
    const publicAction = project(this.#session.observe()).menu_actions.actions
      .find((action) => action.action_id === action_id) ?? null;
    const receipt = await this.#session.submit({
      requestId: request_id,
      expectedSnapshotId: binding.nativeSnapshotId,
      boundActionId,
      ...(timeout_ms == null ? {} : { timeoutMs: timeout_ms })
    });
    const nativeDelivery = receipt.delivery === "delivered" ? "delivered"
      : receipt.delivery === "unknown" ? "unknown" : "not_delivered";
    const unknown = nativeDelivery === "unknown";
    const successor = receipt.successor == null ? null : this.#remember(receipt.successor);
    if (unknown) this.#bindings.clear();
    return {
      protocol_version: receipt.protocol_version,
      schema: MANAGED_TEXT_MENU_RESULT_SCHEMA,
      input_profile: MANAGED_TEXT_MENU_PROFILE,
      request_id,
      status: unknown ? "unknown" : nativeDelivery === "delivered" ? "applied" : "not_applied",
      effect_domain: "native_input",
      native_delivery: nativeDelivery,
      action: publicAction,
      reason_code: receipt.reason_code,
      detail: receipt.detail,
      retry: unknown ? "never" : nativeDelivery === "delivered" ? "never" : successor == null ? "never" : "reobserve",
      successor,
      attribution: null
    };
  }

}
