import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { createSession, reduce, reduceNativeExecution, matchNativeExecutionChoice, matchNativeSemanticChoice, type NativeSemanticAcceptance,
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

function executionExample() {
  const { basis, accepted } = example();
  const snapshot = basis.snapshot as any;
  const actions = snapshot.bound_actions.actions.map((action: any, i: number) => ({
    key: `native-key-${i}`, verb: action.verb, subject_referent_id: action.subject_referent_id ?? null,
    arguments: Object.fromEntries(action.arguments.map((arg: any) => [arg.role, arg.referent_id])),
    native_legality_basis: "same-boundary-native-catalog" }));
  const action = actions.find((item: any) => item.verb === accepted.bound_action.verb
    && item.subject_referent_id === accepted.bound_action.subject_referent_id
    && JSON.stringify(item.arguments) === JSON.stringify(Object.fromEntries(accepted.bound_action.arguments.map(arg => [arg.role, arg.referent_id]))));
  const native_input = { action_key: action.key, verb: action.verb,
    subject_referent_id: action.subject_referent_id, arguments: action.arguments };
  const execution = { action_witness_id: accepted.action_witness_id, basis: accepted.basis,
    phase: "before_execution", missing_reason: null, native_input,
    execution_action_space: { schema: "sts2.human-annotator/execution-semantic-action-space-3",
      action_witness_id: accepted.action_witness_id, phase: "before_execution", status: "captured",
      actions, observed_action_key: action.key, observed_membership: "exact_once", observed_match_count: 1 } };
  const queued = structuredClone(basis);
  const q = queued.snapshot as any;
  q.status = "settling"; q.interaction.stage = "settling";
  q.bound_actions.actions = []; q.bound_actions.total_count = q.bound_actions.materialized_count = 0;
  q.interaction.content.surface.playable_cards = []; q.interaction.content.surface.usable_potions = [];
  q.interaction.content.surface.can_end_turn = false;
  return { basis, queued, accepted: { ...accepted, bound_action: null, native_input }, execution };
}

it.each([null, undefined, [], { target: "foreign-target" }])("rejects an incomplete native execution catalog without selecting an action: %s", argumentsValue => {
  const { queued, execution } = executionExample();
  const actions = structuredClone(execution.execution_action_space.actions) as any[];
  actions[0].arguments = argumentsValue;
  const result = reduceNativeExecution(createSession("same", "reference"), queued, actions);
  expect(result.complete).toBe(false);
  expect(result.missing).toContain("required:native_execution_catalog_operands");
});

describe("P1 execution S and native A(S)", () => {
  it("retains original empty public catalog and projects the same complete P1 choice facts", () => {
    const { basis, queued, accepted, execution } = executionExample();
    const bytes = JSON.stringify(queued);
    const actual = matchNativeExecutionChoice(createSession("same", "reference"), queued, accepted, execution);
    const online = reduce(createSession("same", "reference"), basis);
    expect(actual.status).toBe("matched");
    expect(actual.reduction.missing).toEqual([]);
    expect(actual.reduction.observation).toEqual(online.observation);
    expect(actual.reduction.observation.catalog).toEqual(online.observation.catalog);
    expect(actual.lowering?.first).toBeNull();
    expect(actual.lowering?.native_input_key).toBe(execution.native_input.action_key);
    expect(JSON.stringify(queued)).toBe(bytes);
  });

  it.each(["carrier", "key", "target", "duplicate", "membership", "missing-read", "missing-hand"])(
    "records an explicit failure for %s instead of borrowing Human-time facts", defect => {
      const { queued, accepted, execution } = executionExample();
      if (defect === "carrier") execution.action_witness_id = "foreign";
      if (defect === "key") accepted.native_input.action_key = "foreign";
      if (defect === "target") accepted.native_input.arguments = { target: "dead-target" };
      if (defect === "duplicate") execution.execution_action_space.actions.push(execution.execution_action_space.actions[0]);
      if (defect === "membership") execution.execution_action_space.observed_match_count = 0;
      if (defect === "missing-read") (queued as any).reads = queued.reads.slice(1);
      if (defect === "missing-hand") (queued.snapshot as any).interaction.content.context.player.hand = [];
      const result = matchNativeExecutionChoice(createSession("same", "reference"), queued, accepted, execution);
      expect(result.status).not.toBe("matched");
      expect(result.choice_id).toBeNull();
    });
});

it("matches H public input to S native input while ignoring descriptive metadata, never action operands", () => {
  const { basis, queued, accepted, execution } = executionExample();
  const selected = (basis.snapshot as any).bound_actions.actions.find((item: any) => item.verb === accepted.native_input.verb
    && item.subject_referent_id === accepted.native_input.subject_referent_id);
  const human = { ...accepted, native_input: null, bound_action: selected };
  const original = { ...execution, native_input: { ...execution.native_input, label: "native observation metadata" } };
  expect(matchNativeExecutionChoice(createSession("same", "reference"), queued, human, original).status).toBe("matched");
  original.native_input.arguments = { target: "changed-target" };
  expect(matchNativeExecutionChoice(createSession("same", "reference"), queued, human, original).status).toBe("unmapped");
});

it("uses only a real S public BoundAction when that execution representation is available", () => {
  const { basis, accepted, execution } = executionExample();
  const selected = (basis.snapshot as any).bound_actions.actions.find((item: any) => item.verb === accepted.native_input.verb
    && item.subject_referent_id === accepted.native_input.subject_referent_id);
  const original = { ...execution, native_input: null, bound_action: selected,
    execution_action_space: { ...execution.execution_action_space, human_bound_action_id: selected.bound_action_id,
      human_native_action_key: null } };
  const human = { ...accepted, native_input: null, bound_action: { ...selected, bound_action_id: "earlier-H-action-id" } };
  const result = matchNativeExecutionChoice(createSession("same", "reference"), basis, human, original);
  expect(result.status).toBe("matched");
  expect(result.lowering?.first?.bound_action_id).toBe(selected.bound_action_id);
  human.bound_action.arguments = [{ role: "target", referent_id: "changed-target" }];
  expect(matchNativeExecutionChoice(createSession("same", "reference"), basis, human, original).status).toBe("unmapped");
});

it("a public S binding never authorizes a different native subject or target", () => {
  const { basis, accepted, execution } = executionExample();
  const selected = (basis.snapshot as any).bound_actions.actions.find((item: any) => item.verb === accepted.native_input.verb
    && item.subject_referent_id === accepted.native_input.subject_referent_id);
  const human = { ...accepted, native_input: null, bound_action: selected };
  const original = { ...execution, native_input: null, bound_action: selected,
    execution_action_space: { ...execution.execution_action_space, human_bound_action_id: selected.bound_action_id } };
  const native = original.execution_action_space.actions.find((item: any) => item.key === original.execution_action_space.observed_action_key);
  native.subject_referent_id = "another-real-card";
  expect(matchNativeExecutionChoice(createSession("same", "reference"), basis, human, original).status).toBe("unmapped");
  native.subject_referent_id = selected.subject_referent_id;
  native.arguments = { target: "another-target" };
  expect(matchNativeExecutionChoice(createSession("same", "reference"), basis, human, original).status).toBe("unmapped");
});

it("retains exact H native input when S has its own real public BoundAction", () => {
  const { basis, accepted, execution } = executionExample();
  const bound = (basis.snapshot as any).bound_actions.actions.find((item: any) => item.verb === accepted.native_input.verb
    && item.subject_referent_id === accepted.native_input.subject_referent_id);
  const original = { ...execution, native_input: null, bound_action: bound,
    execution_action_space: { ...execution.execution_action_space, human_bound_action_id: bound.bound_action_id } };
  const result = matchNativeExecutionChoice(createSession("same", "reference"), basis, accepted, original);
  expect(result.status).toBe("matched");
  expect(result.lowering?.first?.bound_action_id).toBe(bound.bound_action_id);
  accepted.native_input.arguments = { target: "changed-target" };
  expect(matchNativeExecutionChoice(createSession("same", "reference"), basis, accepted, original).status).toBe("unmapped");
});

function discardExecutionExample() {
  const data = executionExample();
  const potion = { entity_id: "actual-potion-slot", definition_id: "REVIVE", name: "Revive",
    description: "Revive on death", slot: 0, target_type: "Self", can_use: false, automatic: true, keywords: [], card_previews: [] };
  for (const value of [data.basis, data.queued]) {
    const snapshot = value.snapshot as any;
    snapshot.persistent.content.player.potions.push(structuredClone(potion));
    snapshot.interaction.content.context.player.potion_states.push({ entity_id: potion.entity_id,
      target_type: potion.target_type, can_use: false, automatic: true });
    snapshot.referents.push({ ...snapshot.referents[0], referent_id: potion.entity_id, role: "discardable_potion",
      properties_schema: "sts2.player-environment/referent/potion-1", properties: structuredClone(potion) });
  }
  const snapshot = data.basis.snapshot as any;
  const { keywords: _keywords, card_previews: _previews, ...surfacePotion } = potion;
  snapshot.interaction.content.surface.discardable_potions = [surfacePotion];
  snapshot.bound_actions.actions.push({ ...snapshot.bound_actions.actions[0], bound_action_id: "actual-discard",
    verb: "activate", subject_referent_id: potion.entity_id, arguments: [], label: "Discard Revive" });
  snapshot.bound_actions.total_count = snapshot.bound_actions.materialized_count = snapshot.bound_actions.actions.length;
  const discard = { key: "native-discard-slot", verb: "discard", subject_referent_id: potion.entity_id, arguments: {},
    native_legality_basis: "native_slot+exact_current_enabled_potion_holder" };
  data.execution.execution_action_space.actions.push(discard);
  return { ...data, discard };
}
it("retains the same independent discard opportunity in captured execution A(S) and the online P1 catalog", () => {
  const { basis, queued, accepted, execution } = discardExecutionExample();
  const original = JSON.stringify(queued);
  const actual = matchNativeExecutionChoice(createSession("discard-S", "reference"), queued, accepted, execution);
  const online = reduce(createSession("discard-S", "reference"), basis);
  expect(actual.status).toBe("matched");
  expect(actual.reduction.observation).toEqual(online.observation);
  const choice = actual.reduction.observation.catalog.find(choice => choice.kind === "potion.discard")!;
  expect(actual.reduction.lowering[choice.choice_id]?.native_input_key).toBe("native-discard-slot");
  expect(actual.reduction.lowering[choice.choice_id]?.first).toBeNull();
  expect(JSON.stringify(queued)).toBe(original);
  // Absence in old A(S) is not repaired from inventory or an earlier H.
  execution.execution_action_space.actions.pop();
  const missing = matchNativeExecutionChoice(createSession("discard-S", "reference"), queued, accepted, execution);
  expect(missing.reduction.observation.catalog.some(choice => choice.kind === "potion.discard")).toBe(false);
});
it("keeps published potion-slot order when the exact execution action inventory is permuted", () => {
  const { basis, queued, accepted, execution, discard } = discardExecutionExample();
  const first = (basis.snapshot as any).persistent.content.player.potions[0];
  const second = { ...first, entity_id: "second-slot", definition_id: "OTHER", name: "Other", slot: 1 };
  for (const value of [basis, queued]) {
    const snapshot = value.snapshot as any;
    snapshot.persistent.content.player.potions.push(structuredClone(second));
    snapshot.interaction.content.context.player.potion_states.push({ entity_id: second.entity_id,
      target_type: second.target_type, can_use: false, automatic: true });
    snapshot.referents.push({ ...snapshot.referents.find((ref: any) => ref.referent_id === first.entity_id),
      referent_id: second.entity_id, label: second.name, properties: structuredClone(second) });
  }
  const snapshot = basis.snapshot as any;
  const { keywords: _keywords, card_previews: _previews, ...surfacePotion } = second;
  snapshot.interaction.content.surface.discardable_potions.push(surfacePotion);
  snapshot.bound_actions.actions.push({ ...snapshot.bound_actions.actions.at(-1), bound_action_id: "second-discard",
    subject_referent_id: second.entity_id, label: "Discard Other" });
  snapshot.bound_actions.total_count = snapshot.bound_actions.materialized_count = snapshot.bound_actions.actions.length;
  execution.execution_action_space.actions.unshift({ ...discard, key: "second-discard", subject_referent_id: second.entity_id });
  const actual = matchNativeExecutionChoice(createSession("slots", "reference"), queued, accepted, execution);
  const online = reduce(createSession("slots", "reference"), basis);
  expect(actual.status).toBe("matched");
  expect(actual.reduction.observation).toEqual(online.observation);
});
it.each(["subject", "target", "inventory"])("does not create discard operands from missing or foreign %s", defect => {
  const { queued, accepted, execution, discard } = discardExecutionExample();
  if (defect === "subject") discard.subject_referent_id = "foreign-slot";
  if (defect === "target") (discard.arguments as any).target = (queued.snapshot as any).referents[0].referent_id;
  if (defect === "inventory") (queued.snapshot as any).persistent.content.player.potions = [];
  expect(matchNativeExecutionChoice(createSession("discard-S", "reference"), queued, accepted, execution).status).toBe("incomplete");
});

function popupExecutionExample() {
  const data = executionExample();
  const snapshot = data.queued.snapshot as any;
  const target = snapshot.interaction.content.context.player.player_entity_id;
  if (!snapshot.referents.some((ref: any) => ref.referent_id === target)) snapshot.referents.push({
    ...snapshot.referents[0], referent_id: target, role: 'player' });
  for (const [id, slot] of [['queued-potion', 0], ['visible-other-potion', 2]] as const) {
    const potion = { entity_id: id, definition_id: 'ENERGY_POTION', name: id, description: 'Gain energy.',
      slot, target_type: 'Self', can_use: true, automatic: false, keywords: [], card_previews: [] };
    snapshot.persistent.content.player.potions.push(potion);
    snapshot.interaction.content.context.player.potion_states.push({ entity_id: id, target_type: 'Self', can_use: true, automatic: false });
    snapshot.referents.push({ ...snapshot.referents[0], referent_id: id, role: 'potion',
      properties_schema: 'sts2.player-environment/referent/potion-1', properties: potion });
  }
  Object.assign(snapshot.interaction, { kind: 'potion_popup', content_schema: 'sts2.player-environment/surface/potion_popup-1' });
  snapshot.interaction.content.surface = { kind: 'potion_popup', potion_entity_id: 'visible-other-potion',
    definition_id: 'ENERGY_POTION', name: 'visible-other-potion', slot: 2, can_use: true, can_discard: true,
    direct_combat_use: true, use_target_entity_ids: [target] };
  const selected = { key: 'use|queued-potion|target=' + target, verb: 'use', subject_referent_id: 'queued-potion',
    arguments: { target }, native_legality_basis: 'same-boundary-native-potion-catalog' };
  data.execution.execution_action_space.actions.push(selected);
  const native_input = { action_key: selected.key, verb: selected.verb, subject_referent_id: selected.subject_referent_id,
    arguments: selected.arguments };
  data.accepted.native_input = native_input;
  data.execution.native_input = native_input;
  data.execution.execution_action_space.observed_action_key = selected.key;
  return data;
}
it('maps exact queued use from full combat S/A(S) while retaining a different visible potion popup', () => {
  const { queued, accepted, execution } = popupExecutionExample();
  const bytes = JSON.stringify(queued);
  const actual = matchNativeExecutionChoice(createSession('popup-execution', 'reference'), queued, accepted, execution);
  expect(actual.status).toBe('matched'); expect(actual.reduction.missing).toEqual([]);
  expect(actual.reduction.observation.catalog.find(choice => choice.choice_id === actual.choice_id)?.kind).toBe('potion.use');
  expect(actual.reduction.observation.scene.native_kind).toBe('potion_popup');
  expect((actual.reduction.observation.scene.content as any).surface.slot).toBe(2);
  expect(actual.reduction.observation.catalog.some(choice => choice.kind === 'combat.end_turn')).toBe(true);
  expect(actual.reduction.observation.catalog.some(choice => !choice.mapped)).toBe(false);
  expect(JSON.stringify(queued)).toBe(bytes);
});
it.each(['foreign_target', 'noncombat_context', 'partial_snapshot'])('keeps queued popup execution incomplete for %s', defect => {
  const { queued, accepted, execution } = popupExecutionExample();
  const snapshot = queued.snapshot as any;
  if (defect === 'foreign_target') execution.execution_action_space.actions[0]!.arguments = { target: 'not-in-S' };
  if (defect === 'noncombat_context') snapshot.interaction.content.context.kind = 'event';
  if (defect === 'partial_snapshot') { snapshot.completeness.status = 'partial'; snapshot.completeness.missing = ['uncaptured']; }
  expect(matchNativeExecutionChoice(createSession('popup-execution', 'reference'), queued, accepted, execution).status).toBe('incomplete');
});
