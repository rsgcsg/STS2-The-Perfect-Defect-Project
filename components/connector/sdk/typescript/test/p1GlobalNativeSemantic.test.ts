import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { createSession, matchNativeSemanticChoice, type NativeSemanticAcceptance,
  type P1Basis } from "../src/p1Global/index.js";

function example() {
  const basis = JSON.parse(readFileSync(new URL("./fixtures/p1-global/synthetic-complete-combat.json", import.meta.url),
    "utf8")) as P1Basis;
  const snapshot = basis.snapshot as any;
  const action = snapshot.bound_actions.actions.find((item: any) => item.verb === "play" && item.arguments.length > 0);
  const accepted: NativeSemanticAcceptance = { action_witness_id: "actual-native-action-1",
    basis: { snapshot_id: snapshot.snapshot_id }, bound_action: {
      bound_action_id: action.bound_action_id, verb: action.verb,
      subject_referent_id: action.subject_referent_id, arguments: structuredClone(action.arguments) },
    native_receipt: { kind: "game_action_accepted", carrier_ref: "actual-native-action-1" } };
  return { basis, accepted };
}

describe("P1 native semantic Human acceptance", () => {
  it("matches a real submitted card and target against its original complete basis without raw mouse inputs", () => {
    const { basis, accepted } = example();
    const result = matchNativeSemanticChoice(createSession("human", "reference"), basis, accepted);
    expect(result.status).toBe("matched");
    const choice = result.reduction.observation.catalog.find(item => item.choice_id === result.choice_id)!;
    expect(choice.kind).toBe("combat.play_card");
    expect(result.lowering!.first!.bound_action_id).toBe(accepted.bound_action.bound_action_id);
    expect(result.lowering!.follow).toEqual([]);
  });

  it.each(["carrier", "snapshot", "target"])("does not join a foreign %s to an accepted Human action", defect => {
    const { basis, accepted } = example();
    const changed = structuredClone(accepted) as any;
    if (defect === "carrier") changed.native_receipt.carrier_ref = "other-action";
    if (defect === "snapshot") changed.basis.snapshot_id = "other-H";
    if (defect === "target") changed.bound_action.arguments[0].referent_id = "other-enemy";
    const result = matchNativeSemanticChoice(createSession("human", "reference"), basis, changed);
    expect(result.status).toBe("unmapped");
    expect(result.choice_id).toBeNull();
  });

  it("matches native EndTurn when the original action omits its optional subject", () => {
    const { basis, accepted } = example();
    const snapshot = basis.snapshot as any;
    const action = snapshot.bound_actions.actions.find((item: any) => item.verb === "end_turn");
    delete action.subject_referent_id;
    const result = matchNativeSemanticChoice(createSession("human", "reference"), basis,
      { ...accepted, bound_action: structuredClone(action) });
    expect(result.status).toBe("matched");
    expect(result.reduction.observation.catalog.find(item => item.choice_id === result.choice_id)!.kind)
      .toBe("combat.end_turn");
  });

  it("does not upgrade incomplete original public information into a P1 decision", () => {
    const { basis, accepted } = example();
    const incomplete = { ...basis, reads: basis.reads.slice(1) };
    const result = matchNativeSemanticChoice(createSession("human", "reference"), incomplete, accepted);
    expect(result.status).toBe("incomplete");
    expect(result.reduction.complete).toBe(false);
    expect(result.choice_id).toBeNull();
  });

  it("uses the same exact acceptance seam for a native UI choice outside combat", () => {
    const { basis, accepted } = example();
    const snapshot = basis.snapshot as any;
    const card = snapshot.interaction.content.context.player.hand[0];
    const surface = { kind: "native_generated_card_choice", stage: "choosing", prompt: "Choose one card",
      cards: [card], selectable_card_entity_ids: [card.entity_id], can_skip: true, is_peeking: false };
    Object.assign(snapshot.interaction, { kind: surface.kind, stage: surface.stage,
      content_schema: `sts2.player-environment/surface/${surface.kind}-1`,
      content: { surface, context: { kind: "event", event_id: "NEOW", name: "Neow", ancient: true, in_dialogue: false } } });
    snapshot.reads = snapshot.reads.filter((read: any) => read.kind !== "combat_piles");
    const nonCombat = { ...basis, reads: basis.reads.filter(read => read.kind !== "combat_piles") };
    const action = { ...snapshot.bound_actions.actions[0], bound_action_id: "actual-generated-choice",
      verb: "select", subject_referent_id: card.entity_id, arguments: [] };
    snapshot.bound_actions.actions = [action];
    snapshot.bound_actions.total_count = snapshot.bound_actions.materialized_count = 1;
    const result = matchNativeSemanticChoice(createSession("human", "reference"), nonCombat, {
      ...accepted, bound_action: action, native_receipt: { kind: "semantic_ui_accepted", carrier_ref: accepted.action_witness_id } });
    expect(result.status).toBe("matched");
    expect(result.reduction.observation.catalog.find(choice => choice.choice_id === result.choice_id)!.kind).toBe("selector.choose");
  });
});
