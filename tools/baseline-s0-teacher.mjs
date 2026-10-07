/** A bounded public-catalog teacher, never a legality provider or Human actor. */
export const TEACHER_ID = "s0-public-menu-teacher";
export const TEACHER_VERSION = "1.0.0";
export const INPUT_SPEC = "s0-admitted-policy-offers-v1";

export class PublicMenuTeacher {
  constructor({ browse = true } = {}) { this.browse = browse; this.browseStep = 0; }

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
    let index = -1;
    // One finite browse, return, revisit, return journey before ordinary play.
    if (this.browse && snapshot.interaction.kind === "combat_turn" && this.browseStep < 7) {
      if (cursor === "root" && (this.browseStep === 0 || this.browseStep === 5)) {
        index = find("open_information");
        this.browseStep = index >= 0 ? this.browseStep + 1 : 7;
      } else if (cursor === "information") {
        if (this.browseStep === 1) {
          index = find("open_card_tips", "open_relic_tips", "open_intent_tips", "open_power_tips");
          this.browseStep = index >= 0 ? 2 : 4;
        }
        if (index < 0) { index = find("back"); this.browseStep = this.browseStep === 6 ? 7 : 5; }
      } else if (cursor !== "root" && cursor !== "card_targets" && cursor !== "card_confirmation") {
        if (this.browseStep === 2) {
          index = actions.findIndex(action => action.kind === "native_input"
            && /^(show_|inspect_)/u.test(action.verb));
          this.browseStep = 3;
        }
        if (index < 0) { index = find("back"); this.browseStep = 4; }
      }
    }
    if (index < 0) {
      if (cursor === "card_targets") index = find("select_target", "cancel_selection");
      else if (cursor === "card_confirmation") index = find("play", "cancel_selection");
      else if (cursor !== "root") index = find("back");
      else if (snapshot.interaction.kind === "combat_turn") index = find("select_card", "play", "end_turn");
      else if (snapshot.interaction.kind === "map_navigation") index = find("travel");
      else {
        // Forward public choices only. An unsupported catalog yields abstention.
        index = actions.findIndex(action => /continue|proceed|leave|skip remaining|继续|离开/iu.test(action.label)
          && action.kind === "native_input");
        if (index < 0) index = find("confirm", "select", "activate", "open", "skip", "close", "cancel");
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
