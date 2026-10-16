import { describe, expect, it } from "vitest";
import { applyCommitment, createSession, reduce, resolveFollowBasis, type P1Basis } from "../src/p1Global/index.js";

import { rewardForwardPair as pair } from "./fixtures/reward-forward.mjs";
const project = (basis: P1Basis) => reduce(createSession("reward-test", "reference"), basis);

describe("complete forward reward intentions", () => {
  it("offers exact card/alternative/decline outcomes without a browse decision", () => {
    const { outer, child } = pair(); const r = project(outer);
    expect(r.missing).toEqual([]); expect(r.complete).toBe(true);
    expect(r.observation.catalog.map(c => c.kind)).toEqual([
      "reward.alternative", "reward.decline_group", "reward.leave_remaining", "reward.take_card", "reward.take_card"]);
    const take = r.observation.catalog.find(c => c.kind === "reward.take_card")!;
    const low = r.lowering[take.choice_id]!;
    expect(resolveFollowBasis(low.follow[0]!, low, child)?.bound_action_id).toBe("select-0");
    const alt = r.observation.catalog.find(c => c.kind === "reward.alternative")!;
    expect(alt.option).toBe("REROLL");
    expect(resolveFollowBasis(r.lowering[alt.choice_id]!.follow[0]!, r.lowering[alt.choice_id]!, child)?.bound_action_id).toBe("reroll");
  });
  it.each(["parent", "instance", "card", "alternative", "world", "missing-read", "disabled", "duplicate-binding"])("refuses %s drift before its second native step", change => {
    const { outer, child } = pair(); const r = project(outer);
    const take = r.observation.catalog.find(c => c.kind === "reward.take_card")!; const low = r.lowering[take.choice_id]!;
    const surface = child.snapshot.interaction.content.surface;
    if (change === "parent") surface.reward_set_id = "new-owner";
    if (change === "instance") surface.cards[0].entity_id = "same-definition-new-instance";
    if (change === "card") surface.cards[0].description += " changed";
    if (change === "alternative") surface.alternatives[1].title += " changed";
    if (change === "world") child.snapshot.persistent.content.player.gold++;
    if (change === "missing-read") child.reads.pop();
    if (change === "disabled") surface.selectable_card_entity_ids.shift();
    if (change === "duplicate-binding") {
      child.snapshot.bound_actions.actions.push({ ...child.snapshot.bound_actions.actions[0], bound_action_id: "duplicate" });
      child.snapshot.bound_actions.total_count++; child.snapshot.bound_actions.materialized_count++;
    }
    expect(resolveFollowBasis(low.follow[0]!, low, child)).toBeNull();
  });
  it("requires every current surface_card detail instance to remain the offered one", () => {
    const { outer, child } = pair();
    const add = (basis: any, id: string) => {
      const card = basis.snapshot.interaction.content.surface.cards?.[0] ?? basis.snapshot.interaction.content.surface.rewards[0].card_options[0];
      if (!basis.snapshot.referents.some((r: any) => r.referent_id === card.entity_id)) basis.snapshot.referents.push({
        ...basis.snapshot.referents[0], referent_id: card.entity_id, role: "card", properties_schema: "sts2.player-environment/referent/card-1" });
      const listed = { ...basis.snapshot.reads[0], read_id: id, kind: "surface_card", target_referent_id: card.entity_id,
        content_schema: "sts2.player-environment/read/surface_card-1" };
      const response = { ...basis.reads[0].response, read_id: id, kind: listed.kind, target_referent_id: card.entity_id,
        content_schema: listed.content_schema, content: structuredClone(card) };
      basis.snapshot.reads.push(listed); basis.reads.push({ read_id: id, kind: listed.kind, response, error: null });
    };
    add(outer, "detail-one"); add(child, "detail-one"); add(outer, "detail-two"); add(child, "detail-two");
    const r = project(outer), choice = r.observation.catalog.find(c => c.kind === "reward.take_card")!, low = r.lowering[choice.choice_id]!;
    expect(r.complete && project(child).complete).toBe(true);
    expect(resolveFollowBasis(low.follow[0]!, low, child)?.bound_action_id).toBe("select-0");
    child.reads.at(-1).response.content.description += " newly available public fact";
    expect(project(child).complete).toBe(true);
    expect(resolveFollowBasis(low.follow[0]!, low, child)).toBeNull();
  });
  it("decline executes ordinary Skip before its pending native-disposition commitment", () => {
    const { outer, child } = pair(); const r = project(outer);
    const choice = r.observation.catalog.find(c => c.kind === "reward.decline_group")!; const low = r.lowering[choice.choice_id]!;
    expect(low.first?.bound_action_id).toBe("open");
    expect(resolveFollowBasis(low.follow[0]!, low, child)?.bound_action_id).toBe("skip");
    expect(applyCommitment(r.state, low, false).reward?.declined).toEqual([]);
    const state = applyCommitment(r.state, low, true); const next = reduce(state, outer);
    expect(next.observation.catalog.map(c => c.kind)).toEqual(["reward.leave_remaining"]);
    expect(next.observation.commitments.reward_dispositions?.[0]?.native_disposition).toBe("pending_owner_skip");
    expect(next.excluded[0]?.rule).toBe("forward_profile:declined_exact_reward_group");
    outer.snapshot.interaction.content.surface.reward_set_id = "set-b";
    expect(reduce(state, outer).observation.catalog.some(c => c.kind === "reward.take_card")).toBe(true);
  });
  it("does not lose a group that permits taking more than one card", () => {
    const { outer, child, group } = pair(); group.selection_completes_reward = false;
    child.snapshot.interaction.content.surface.selection_completes_reward = false;
    const r = project(outer); const take = r.observation.catalog.find(c => c.kind === "reward.take_card")!;
    const low = r.lowering[take.choice_id]!;
    expect(resolveFollowBasis(low.follow[0]!, low, child)?.bound_action_id).toBe("select-0");
    expect(reduce(applyCommitment(r.state, low, true), outer).observation.catalog.some(c => c.kind === "reward.take_card")).toBe(true);
  });
  it("retains aggregated taking and owner exit without inventing a per-group Skip", () => {
    const { outer, group } = pair(); group.can_skip_selection = false;
    group.card_alternatives = group.card_alternatives.filter((a: any) => !a.is_pure_return);
    const r = project(outer); expect(r.complete).toBe(true);
    expect(r.observation.catalog.some(c => c.kind === "reward.decline_group")).toBe(false);
    expect(r.observation.catalog.some(c => c.kind === "reward.leave_remaining")).toBe(true);
    outer.snapshot.interaction.content.surface.can_leave_with_unclaimed_rewards = false;
    expect(project(outer).observation.catalog.some(c => c.kind === "reward.decline_group")).toBe(false);
  });
});
