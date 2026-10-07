/** A bounded public-catalog teacher, never a legality provider or Human actor. */
export const TEACHER_ID = "s0-public-menu-teacher";
export const TEACHER_VERSION = "1.1.0";
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

export class PublicMenuTeacher {
  constructor({ browse = true } = {}) {
    this.browse = browse; this.browseVisits = 0; this.browseOffers = 0;
    this.awaitingTip = false; this.returning = false;
  }

  decide(input) {
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
      else if (kind === "reward_claim") index = find("proceed_rewards", "skip_rewards", "claim_reward", "claim_linked_reward");
      else if (SELECTORS.has(kind)) index = find("confirm", "select", "skip", "cancel");
      else if (["event_option", "event_dialogue", "rest_site", "treasure_room", "shop_room", "shop_inventory", "game_over"].includes(kind)) {
        // A declared public heuristic, not a generic fallback for arbitrary new kinds.
        index = actions.findIndex(action => /continue|proceed|leave|skip remaining|继续|离开/iu.test(action.label)
          && action.kind === "native_input");
        if (index < 0) index = find("confirm", "select", "close", "activate", "open", "skip", "cancel");
      }
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
