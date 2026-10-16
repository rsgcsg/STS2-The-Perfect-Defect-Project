import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { createSession, matchSourceP1Choice, reduce, type P1Basis } from "../src/p1Global/index.js";

// Synthetic complete public facts, with explicit original-invocation relations.
// This is a semantic mapping regression, not a native capture/origin witness.
const original = (): any => JSON.parse(readFileSync(new URL("./fixtures/p1-global/synthetic-complete-combat.json", import.meta.url), "utf8"));
const session = () => createSession("source-original", "reference");
const rename = (basis: any, suffix: string) => {
  const next = structuredClone(basis); next.snapshot.snapshot_id += suffix;
  for (const capture of next.reads) if (capture.response)
    capture.response.expected_snapshot_id = capture.response.observed_snapshot_id = next.snapshot.snapshot_id;
  return next;
};
const reference = (basis: any) => ({ epoch_id: "epoch-original", scope_id: "scope-original", stream_generation: "1",
  snapshot_id: basis.snapshot.snapshot_id });
const position = (index: string) => ({ epoch_id: "epoch-original", stream_generation: "1", publication_index: index });
function row(basis: any, ordinal: string, selected: any, facts: any, mechanism: string) {
  return { input_id: "input-" + ordinal, session_id: "source-session", timeline_id: "timeline", epoch_id: "epoch-original",
    segment_id: "segment", pre_position: position(ordinal), input_prefix_ordinal: ordinal,
    basis_order: { status: "native_prefix_frozen", reason_code: null },
    captured_parts: { public_basis: reference(basis), public_basis_missing_reason: null }, basis, publications_before: [] as any[],
    outcome: { mapping_status: "exact", match_count: 1, selected_action: selected, native_mechanism: mechanism,
      delivery: "delivered", reason_code: null, stages: [], native_facts: { origin: "native_input", missing_reason: null, ...facts } } };
}
const accepted = (carrier = "exact-action") => [
  { kind: "game_action_submitted", carrier_ref: carrier }, { kind: "game_action_accepted", carrier_ref: carrier }];
function play() {
  const basis = original(), next = rename(basis, "-held");
  const action = basis.snapshot.bound_actions.actions.find((value: any) => value.verb === "play" && value.arguments.length);
  const facts = { owner_ref: "exact-play", subject_ref: action.subject_referent_id,
    arguments: Object.fromEntries(action.arguments.map((value: any) => [value.role, value.referent_id])), carriers: accepted() };
  const start = row(basis, "1", { action_id: "native-begin-not-pe-id", verb: "begin_card_play",
    subject_referent_id: action.subject_referent_id, arguments: [] },
  { owner_ref: "exact-hand", subject_ref: action.subject_referent_id, arguments: { holder: "exact-holder" },
    carriers: [{ kind: "card_play_created", carrier_ref: "exact-play" }] }, "NPlayerHand.StartCardPlay");
  const submit = row(next, "2", { ...action, action_id: "native-confirm-not-pe-id", verb: "confirm_target" }, facts, "NCardPlay.TryPlayCard");
  return { basis, action, trace: [start, submit] };
}

describe("Source4 original-input to P1 intention mapping", () => {
  it("matches exact native EndTurn using its own submitted/accepted carrier, never catalog IDs", () => {
    const basis = original(), action = basis.snapshot.bound_actions.actions.find((value: any) => value.verb === "end_turn");
    const input = row(basis, "7", { ...action, action_id: "native-other-catalog-id" },
      { owner_ref: "end-button", subject_ref: null, arguments: {}, carriers: accepted() }, "native_end_turn_ui");
    const match = matchSourceP1Choice(session(), basis, [input]);
    expect(match.status).toBe("matched");
    expect(reduce(session(), basis).observation.catalog.find(choice => choice.choice_id === match.choice_id)?.kind).toBe("combat.end_turn");
    input.outcome.native_facts.carriers[1].carrier_ref = "another-action";
    expect(matchSourceP1Choice(session(), basis, [input]).status).toBe("unmapped");
  });
  it("matches StartCardPlay plus the exact created carrier's accepted targeted input", () => {
    const { basis, trace } = play();
    const match = matchSourceP1Choice(session(), basis, trace);
    expect(match.status).toBe("matched"); expect(match.input_ids).toEqual(["input-1", "input-2"]);
    expect(reduce(session(), basis).observation.catalog.find(choice => choice.choice_id === match.choice_id)?.kind).toBe("combat.play_card");
  });
  it.each(["owner", "subject", "target", "carrier", "duplicate", "epoch", "prefix", "unknown", "missing-read", "basis-ref"])(
    "does not infer a physical play after %s corruption", change => {
      const { basis, trace } = play(), last: any = trace[1];
      if (change === "owner") last.outcome.native_facts.owner_ref = "different-play";
      if (change === "subject") last.outcome.native_facts.subject_ref = "same-definition-new-card";
      if (change === "target") last.outcome.native_facts.arguments.target = "different-target";
      if (change === "carrier") last.outcome.native_facts.carriers[1].carrier_ref = "different-accepted-action";
      if (change === "duplicate") last.outcome.native_facts.carriers.push(last.outcome.native_facts.carriers[0]);
      if (change === "epoch") last.epoch_id = "next-epoch";
      if (change === "prefix") last.input_prefix_ordinal = "3";
      if (change === "unknown") last.outcome.delivery = "unknown";
      if (change === "missing-read") last.basis.reads.pop();
      if (change === "basis-ref") last.captured_parts.public_basis.snapshot_id = "different-snapshot";
      expect(matchSourceP1Choice(session(), basis, trace).status).not.toBe("matched");
    });
  it("inspects intermediate publications even when the eventual prefix returns to identical facts", () => {
    const { basis, trace } = play(); const changed = rename(basis, "-intermediate");
    changed.snapshot.persistent.content.player.gold++;
    trace[1]!.publications_before.push({ session_id: "source-session", timeline_id: "timeline", epoch_id: "epoch-original",
      segment_id: "segment", position: position("2"), gap_after_index: null,
      captured_parts: { public_basis: reference(changed), public_basis_missing_reason: null }, basis: changed });
    expect(matchSourceP1Choice(session(), basis, trace).status).toBe("unmapped");
    changed.snapshot.persistent.content.player.gold--;
    expect(matchSourceP1Choice(session(), basis, trace).status).toBe("matched");
    trace[1]!.publications_before[0].gap_after_index = "1";
    expect(matchSourceP1Choice(session(), basis, trace).status).toBe("incomplete");
  });
  it("does not discard a new surface fact or an intervening selector as card-drag navigation", () => {
    const { basis, trace } = play(); const last: any = trace[1];
    last.basis.snapshot.interaction.content.surface.new_public_choice = { label: "new option" };
    expect(matchSourceP1Choice(session(), basis, trace).status).toBe("unmapped");
    delete last.basis.snapshot.interaction.content.surface.new_public_choice;
    last.basis.snapshot.interaction.kind = "card_selection";
    expect(matchSourceP1Choice(session(), basis, trace).status).toBe("unmapped");
  });
  it("retains the original unknown delivery while classifying a proven no-submission cancel as context only", () => {
    const { basis, trace } = play(); const last: any = trace[1];
    last.outcome.delivery = "unknown"; last.outcome.reason_code = "native_source_scope_ended_without_acceptance";
    last.outcome.native_facts.carriers = [{ kind: "card_play_canceled", carrier_ref: "exact-play" }];
    const after = rename(basis, "-canceled");
    const stage = { kind: "card_play_canceled", owner_ref: "exact-play", subject_ref: last.outcome.native_facts.subject_ref,
      arguments: { hand: "exact-hand" }, evidence: "native_finished_false", no_submission: true as boolean | null };
    last.publications_after = [{ session_id: "source-session", timeline_id: "timeline", epoch_id: "epoch-original",
      segment_id: "segment", position: position("3"), gap_after_index: null,
      captured_parts: { public_basis: reference(after), public_basis_missing_reason: null, native_stage: stage }, basis: after }];
    last.source_boundaries = [];
    const immutable = JSON.stringify(trace);
    expect(matchSourceP1Choice(session(), basis, trace)).toMatchObject({ status: "context_only", choice_id: null,
      input_ids: ["input-1", "input-2"] });
    expect(JSON.stringify(trace)).toBe(immutable);
    stage.no_submission = null;
    expect(matchSourceP1Choice(session(), basis, trace).status).toBe("incomplete");
    stage.no_submission = true;
    last.source_boundaries.push({ session_id: "source-session", timeline_id: "timeline", paused_intervals: [{
      epoch_id: "epoch-original", stream_generation: "1", after_index: "1", through_index: "3",
      after_input_ordinal: "1", through_input_ordinal: "2", reason: "recording_paused" }] });
    expect(matchSourceP1Choice(session(), basis, trace).status).toBe("incomplete");
  });
});
