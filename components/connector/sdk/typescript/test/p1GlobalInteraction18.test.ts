import { expect, it } from "vitest";
import { createSession, reduce, resolveFollowBasis } from "../src/p1Global/index.js";
import { completeSelectorFacts } from "../src/p1Global/selectorForward.js";
import { selectorForwardPair } from "./fixtures/selector-forward.mjs";

function transformPair() {
  const pair = selectorForwardPair();
  for (const basis of [pair.outer, pair.preview]) {
    const snapshot = basis.snapshot, surface = snapshot.interaction.content.surface;
    snapshot.interaction.kind = surface.kind = "deck_transform_selection";
    snapshot.interaction.content_schema = "sts2.player-environment/surface/deck_transform_selection-1";
    surface.selector_request.family = "deck_transform";
    surface.selector_request.transformation_preview = "deterministic_native_after";
    surface.preview_options[0].display_context = "native_deterministic_transform_after";
  }
  return pair;
}

it("r1.8 admits the native deterministic preview and preserves the original confirmation binding", () => {
  const { outer, preview } = transformPair();
  expect(reduce(createSession("old", "managed"), outer).complete).toBe(false);
  const result = reduce(createSession("new", "managed", "r1.8"), outer);
  expect(result.complete).toBe(true);
  const plan = result.lowering[result.observation.catalog[0]!.choice_id]!;
  expect(resolveFollowBasis(plan.follow[0]!, plan, preview)?.bound_action_id).toBe("exact-confirm");
  preview.snapshot.interaction.content.surface.preview_options[0].display_context = "native_committed_owned_result";
  expect(completeSelectorFacts(preview.snapshot.interaction.content.surface, true)).toBe(false);
  expect(resolveFollowBasis(plan.follow[0]!, plan, preview)).toBeNull();
});

function parent() {
  const basis = selectorForwardPair().outer;
  const snapshot = basis.snapshot, ref = snapshot.referents[0], action = snapshot.bound_actions.actions[0];
  const option = { entity_id: "trial-option", index: 0, title: "Double down", description: "Continue the Trial",
    is_enabled: true, is_locked: false, is_proceed: false, was_chosen: false, will_kill_player: false, tooltips: [] };
  snapshot.interaction.kind = "event_option";
  snapshot.interaction.content_schema = "sts2.player-environment/surface/event_option-1";
  snapshot.interaction.content.context = { kind: "event", event_id: "TRIAL" };
  snapshot.interaction.content.surface = { kind: "event_option", options: [option], requires_profile_revision: "r1.8",
    abandon_confirmation: { kind: "abandon_run_confirmation", event_entity_id: "trial-event", room_entity_id: "trial-room",
      parent_option_entity_id: option.entity_id, title: "Abandon this run?", body: "This ends the current run." } };
  snapshot.referents.push({ ...ref, referent_id: option.entity_id, role: "option", label: option.title, properties: option });
  snapshot.bound_actions.actions = [{ ...action, verb: "activate", subject_referent_id: option.entity_id, arguments: [], label: option.title }];
  snapshot.bound_actions.total_count = snapshot.bound_actions.materialized_count = 1;
  return basis;
}

it("a new Trial capability cannot enter the old profile by dropping its revision marker", () => {
  const basis = parent();
  delete basis.snapshot.interaction.content.surface.requires_profile_revision;
  expect(reduce(createSession("old", "managed"), basis).complete).toBe(false);
  const current = reduce(createSession("new", "managed", "r1.8"), basis);
  expect(current.complete).toBe(true);
  expect(current.forced_choice_id).toBeNull();
  expect(current.observation.catalog[0]!.label).toContain("Confirm abandoning this run:");
});

it.each(["missing-option", "missing-action", "duplicate-action", "duplicate-target-action"])("refuses a Trial capability with %s", fault => {
  const basis = parent(), snapshot = basis.snapshot;
  if (fault === "missing-option") snapshot.interaction.content.surface.abandon_confirmation.parent_option_entity_id += "-changed";
  else if (fault === "missing-action") {
    const option = { ...snapshot.interaction.content.surface.options[0], entity_id: "other-option" };
    snapshot.interaction.content.surface.options.push(option);
    snapshot.referents.push({ ...snapshot.referents[0], referent_id: option.entity_id, role: "option", properties: option });
    snapshot.bound_actions.actions[0].subject_referent_id = option.entity_id;
  }
  else snapshot.bound_actions.actions.push({ ...snapshot.bound_actions.actions[0], bound_action_id: "duplicate",
    arguments: fault === "duplicate-target-action" ? [{ role: "target", referent_id: "trial-option" }] : [] });
  snapshot.bound_actions.total_count = snapshot.bound_actions.materialized_count = snapshot.bound_actions.actions.length;
  const result = reduce(createSession("new", "managed", "r1.8"), basis);
  expect(result.complete).toBe(false);
  expect(result.missing).toContain("required:abandon_intention_public_facts_or_binding");
});
