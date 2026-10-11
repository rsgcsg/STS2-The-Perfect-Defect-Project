/** A bounded public-catalog teacher, never a legality provider or Human actor. */
export const TEACHER_ID = "s0-public-menu-teacher";
export const TEACHER_VERSION = "1.2.0";
export const INPUT_SPEC = "s0-admitted-policy-offers-v1";

// NativeTextMenuInformation overrides + exact LiveHost/NativeUi passthrough kinds.
// This is a bounded consumer scope, not a claim of native/runtime qualification.
export const S0_INFORMATION_RETURNS = Object.freeze({
  run_deck: "return_native_information", combat_draw_pile: "return_native_information",
  combat_discard_pile: "return_native_information", combat_exhaust_pile: "return_native_information",
  relic_inspect: "return_relic_inspect", inspect_card: "return_card_inspect",
  card_tips: "return_native_tips", relic_tips: "return_native_tips", power_tips: "return_native_tips",
  intent_tips: "return_native_tips", orb_tips: "return_native_tips", topbar_tips: "return_native_tips",
  native_tip: "return_native_tips"
});
export const S0_TEXT_V2_KINDS = Object.freeze([
  "native_map", "map_navigation", "combat_turn", "combat_card_operation", "potion_popup", "potion_targeting",
  ...Object.keys(S0_INFORMATION_RETURNS), "reward_claim", "card_reward_selection", "card_bundle_selection",
  "event_option", "event_dialogue", "rest_site", "treasure_room", "shop_room", "shop_inventory",
  "combat_hand_card_selection", "deck_upgrade_selection", "deck_transform_selection", "deck_enchant_selection",
  "native_boss_relic_selection", "native_generated_card_choice", "native_simple_card_selection",
  "native_combat_pile_selection", "native_deck_card_selection", "game_over"
]);
const SELECTORS = new Set(["card_reward_selection", "card_bundle_selection", "combat_hand_card_selection",
  "deck_upgrade_selection", "deck_transform_selection", "deck_enchant_selection", "native_boss_relic_selection",
  "native_generated_card_choice", "native_simple_card_selection", "native_combat_pile_selection", "native_deck_card_selection"]);

/** Join existing public choices to current typed descriptors, never create legality. */
function publicRewardChoice(snapshot, actions) {
  const interaction = snapshot.interaction, surface = interaction.content?.surface;
  if (surface?.kind !== interaction.kind) return -1;
  const visible = new Map(snapshot.referents.filter(ref => ref.state?.visible === true)
    .map(ref => [ref.referent_id, ref]));
  const entity = (id, role) => {
    const ref = visible.get(id);
    return typeof id === "string" && ref?.role === role && ref.kind === "entity" && ref.state.enabled !== false;
  };
  const native = action => action.kind === "native_input" && action.effect_domain === "native_input"
    && action.arguments.length === 0;
  const unique = predicate => {
    const indexes = actions.map((action, index) => predicate(action) ? index : -1).filter(index => index >= 0);
    return indexes.length === 1 ? indexes[0] : -1;
  };
  if (interaction.kind === "reward_claim"
    && interaction.content_schema === "sts2.player-environment/surface/reward_claim-1"
    && Array.isArray(surface.rewards)) {
    // Ordinary NRewardButton claims project claim_reward -> choose -> activate.
    // The rewards-screen owner is private: proceed has no public subject/arguments.
    const enabled = surface.rewards.filter(reward => reward.enabled === true);
    const rewards = new Set(enabled.filter(reward => entity(reward.entity_id, "reward"))
      .map(reward => reward.entity_id));
    const claim = actions.findIndex(action => native(action) && action.verb === "activate"
      && rewards.has(action.subject_referent_id));
    if (claim >= 0) return claim;
    if (enabled.length > 0) return -1; // Do not skip an unresolved advertised reward mapping.
    if (surface.can_proceed !== true) return -1;
    return unique(action => native(action) && action.subject_referent_id === null
      && (action.verb === "activate" || action.verb === "skip" && surface.proceed_skips_remaining_rewards === true));
  }
  if (interaction.kind === "reward_claim" && interaction.stage === "native_linked_reward_page"
    && interaction.content_schema === "sts2.player-environment/surface/linked_rewards_text_menu-1"
    && Array.isArray(surface.entries)) {
    const ordinary = new Set(), linked = new Set();
    let enabledRewards = 0;
    for (const entry of surface.entries) {
      if (entry.kind !== "ordinary_reward" && entry.kind !== "linked_reward_set") return -1;
      if (entry.kind === "ordinary_reward" && entry.enabled === true) enabledRewards += 1;
      if (entry.kind === "linked_reward_set" && Array.isArray(entry.choices))
        enabledRewards += entry.choices.filter(choice => choice.enabled === true).length;
      if (entry.kind === "ordinary_reward" && entry.enabled === true && entity(entry.referent_id, "reward"))
        ordinary.add(entry.referent_id);
      const group = visible.get(entry.referent_id);
      if (entry.kind === "linked_reward_set" && group?.role === "reward_group"
        && group.kind === "entity" && Array.isArray(entry.choices))
        for (const choice of entry.choices)
          if (choice.enabled === true && entity(choice.referent_id, "reward")) linked.add(choice.referent_id);
    }
    const claim = actions.findIndex(action => native(action)
      && (action.verb === "claim_reward" && ordinary.has(action.subject_referent_id)
        || action.verb === "claim_linked_reward" && linked.has(action.subject_referent_id)));
    if (claim >= 0) return claim;
    if (enabledRewards > 0) return -1;
    if (surface.proceed_enabled !== true) return -1;
    return unique(action => {
      const ref = visible.get(action.subject_referent_id);
      return native(action) && ref?.role === "screen" && ref.kind === "control" && ref.state.enabled === true
        && action.verb === (surface.proceed_is_skip === true ? "skip_rewards" : "proceed_rewards");
    });
  }
  if (interaction.kind === "card_reward_selection"
    && interaction.content_schema === "sts2.player-environment/surface/card_reward_selection-1"
    && Array.isArray(surface.cards) && Array.isArray(surface.selectable_card_entity_ids)) {
    const selectable = new Set(surface.selectable_card_entity_ids);
    const cards = new Set(surface.cards.filter(card => selectable.has(card.entity_id)
      && entity(card.entity_id, "card")).map(card => card.entity_id));
    // select_card_reward -> select_entity -> select. Alternative activate labels
    // do not disclose whether they reroll or return without completing the reward.
    return actions.findIndex(action => native(action) && action.verb === "select"
      && cards.has(action.subject_referent_id));
  }
  return -1;
}

export class PublicMenuTeacher {
  constructor({ browse = true } = {}) {
    this.browse = browse; this.browseVisits = 0; this.browseOffers = 0;
    this.awaitingTip = false; this.returning = false;
  }

  decide(input) {
    this.lastDiagnostic = null;
    const snapshot = input.bundle.observation;
    const catalog = snapshot.menu_actions;
    const actions = catalog?.actions;
    if (snapshot.input_profile !== "text-menu-v2" || snapshot.status !== "interactive"
      || snapshot.completeness?.status !== "complete" || catalog?.status !== "complete"
      || !actions?.length || catalog.total_count !== actions.length
      || catalog.materialized_count !== actions.length || input.candidate_count !== actions.length)
      throw new Error("teacher_requires_complete_public_catalog");
    const find = (...verbs) => {
      for (const verb of verbs) {
        const index = actions.findIndex(action => action.verb === verb);
        if (index >= 0) return index;
      }
      return -1;
    };
    const cursor = snapshot.menu.cursor;
    const kind = snapshot.interaction.kind;
    const surface = snapshot.interaction.content?.surface;
    let index = -1;
    if (S0_TEXT_V2_KINDS.includes(kind)) {
      // Actual native pages take ownership and reset the virtual cursor to root.
      // Public arrival at that page counts a visit; a chosen but stale leaf does not.
      if (Object.hasOwn(S0_INFORMATION_RETURNS, kind)
        && snapshot.interaction.stage === "native_information_page") {
        if (this.awaitingTip && S0_INFORMATION_RETURNS[kind] === "return_native_tips") {
          this.browseVisits += 1;
          this.awaitingTip = false;
        }
        this.returning = false;
        index = find(S0_INFORMATION_RETURNS[kind]);
      } else if (kind === "native_map" || kind === "map_navigation") {
        if (surface?.kind === "map_navigation" && Array.isArray(surface.next_options)) {
          const next = new Map(surface.next_options.map(option => [option.entity_id, option]));
          const choices = actions.map((action, position) => ({ action, position }))
            .filter(({ action }) => action.kind === "native_input" && action.verb === "activate"
              && next.has(action.subject_referent_id));
          index = (choices.find(({ action }) => next.get(action.subject_referent_id).point_type === "monster")
            ?? choices[0])?.position ?? find("return_native_map");
        }
      } else if (kind === "combat_card_operation") {
        index = snapshot.interaction.stage === "card_targeting"
          ? find("confirm_target", "focus_target", "cancel_card_play")
          : snapshot.interaction.stage === "card_confirm" ? find("confirm_card", "cancel_card_play") : -1;
      } else if (kind === "potion_popup") index = find("close_potion_popup");
      else if (kind === "potion_targeting") index = find("cancel_potion_target");
      // Two finite native tip exposures with a return/revisit between them.
      else if (this.browse && kind === "combat_turn" && this.browseVisits < 2 && this.browseOffers < 16) {
        if (cursor === "root") { index = find("open_information"); this.returning = false; }
        else if (cursor === "information" && !this.returning)
          index = find("open_card_tips", "open_relic_tips", "open_intent_tips", "open_power_tips");
        else if (!this.returning && ["card_tips", "relic_tips", "intent_tips", "power_tips"].includes(cursor)) {
          index = find(`show_${cursor}`);
          if (index >= 0) { this.awaitingTip = true; this.returning = true; }
        }
        if (index < 0 && cursor !== "root") { index = find("back"); this.returning = true; }
        if (index >= 0) this.browseOffers += 1;
      }
    }
    if (index < 0 && S0_TEXT_V2_KINDS.includes(kind)) {
      if (cursor === "card_targets") index = find("select_target", "cancel_selection");
      else if (cursor === "card_confirmation") index = find("play", "cancel_selection");
      else if (cursor !== "root") index = find("back");
      else if (kind === "combat_turn") index = find("select_card", "play", "end_turn");
      else if (kind === "reward_claim" || kind === "card_reward_selection") index = publicRewardChoice(snapshot, actions);
      else if (SELECTORS.has(kind)) index = find("confirm", "select", "skip", "cancel");
      else if (["event_option", "event_dialogue", "rest_site", "treasure_room", "shop_room", "shop_inventory", "game_over"].includes(kind)) {
        // A declared public heuristic, not a generic fallback for arbitrary new kinds.
        index = actions.findIndex(action => /continue|proceed|leave|skip remaining|继续|离开/iu.test(action.label)
          && action.kind === "native_input");
        if (index < 0) index = find("confirm", "select", "close", "activate", "open", "skip", "cancel");
      }
    }
    if (index < 0 && (kind === "reward_claim" || kind === "card_reward_selection")) {
      this.lastDiagnostic = { interaction_kind: kind, content_schema: snapshot.interaction.content_schema,
        reason: kind === "card_reward_selection" && Array.isArray(surface?.selectable_card_entity_ids)
          && surface.selectable_card_entity_ids.length === 0 && Array.isArray(surface?.alternatives)
          && surface.alternatives.some(alternative => alternative.enabled === true)
          ? "card_reward_alternative_effect_not_public" : "reward_public_descriptor_unresolved" };
    }
    return {
      output: { candidate_digest: input.candidate_digest,
        scores: actions.map((_, position) => position === index ? 1 : 0),
        selected_index: index < 0 ? null : index },
      completion: { continuity_token: input.continuity_token,
        snapshot_id: snapshot.snapshot_id, sequence: snapshot.sequence }
    };
  }
}
