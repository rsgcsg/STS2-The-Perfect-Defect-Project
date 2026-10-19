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
