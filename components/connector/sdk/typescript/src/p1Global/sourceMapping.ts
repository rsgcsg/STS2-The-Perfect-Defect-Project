import { decodePlayerRead } from "../protocol.js";
import { decodePublicSourceSnapshot as decodePlayerSnapshot } from "../publicBasis.js";
import { canonicalJson, isObject } from "./canonical.js";
import { createSession, reduce, resolveFollowBasis, type Lowering, type P1Basis, type P1SessionState } from "./reducer.js";
import { completeSelectorFacts, selectorOperation } from "./selectorForward.js";

/** A derived wrapper around verified Source4 originals. The adapter, not this
 * semantic matcher, verifies the bundle, original byte hashes and native origin. */
export interface SourceP1TraceInput {
  readonly [field: string]: unknown;
  readonly basis: P1Basis | null;
  readonly publications_before: readonly SourceP1Publication[];
  readonly publications_after?: readonly SourceP1Publication[];
  readonly source_boundaries?: readonly Readonly<Record<string, unknown>>[];
}
export interface SourceP1Publication {
  readonly [field: string]: unknown;
  readonly basis: P1Basis | null;
}
export interface SourceP1Match {
  readonly status: "matched" | "unmapped" | "ambiguous" | "incomplete" | "context_only";
  readonly choice_id: string | null;
  readonly input_ids: readonly string[];
  readonly reason: string;
}
type ObjectValue = Record<string, any>;
const text = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const same = (a: unknown, b: unknown) => canonicalJson(a) === canonicalJson(b);
const ordinal = (value: unknown): bigint => {
  if (typeof value !== "string" || !/^(0|[1-9][0-9]*)$/u.test(value)) throw new Error("source_ordinal_invalid");
  return BigInt(value);
};
const result = (status: SourceP1Match["status"], reason: string, ids: string[] = [], choice: string | null = null): SourceP1Match =>
  ({ status, choice_id: choice, input_ids: ids, reason });

function snapshot(basis: P1Basis): ObjectValue {
  const value = decodePlayerSnapshot(basis.snapshot).raw as ObjectValue;
  if (value.completeness.status !== "complete") throw new Error("source_snapshot_incomplete");
  if (basis.reads.length !== value.reads.length) throw new Error("source_read_inventory_mismatch");
  const ids = new Set<string>();
  for (const capture of basis.reads) {
    if (ids.has(capture.read_id)) throw new Error("source_read_duplicate");
    ids.add(capture.read_id);
    const listed = value.reads.filter((read: ObjectValue) => read.read_id === capture.read_id);
    if (listed.length !== 1 || capture.error !== null || capture.response === null)
      throw new Error("source_read_incomplete");
    const read = decodePlayerRead(capture.response).raw as ObjectValue;
    if (read.completeness.status !== "complete" || read.kind !== capture.kind || read.read_id !== capture.read_id
      || read.expected_snapshot_id !== value.snapshot_id || read.observed_snapshot_id !== value.snapshot_id
      || !same(read.session, value.session) || !same(read.information_policy, value.information_policy)
      || ["kind", "content_schema", "visibility_basis", "ordering_semantics"].some(key => !same(read[key], listed[0][key]))
      || (read.target_referent_id ?? null) !== (listed[0].target_referent_id ?? null))
      throw new Error("source_read_binding_mismatch");
  }
  return value;
}

function reference(row: ObjectValue, basis: P1Basis, expected?: ObjectValue): ObjectValue {
  const ref = row.captured_parts?.public_basis;
  if (!isObject(ref) || !text(ref.scope_id) || !text(ref.stream_generation) || !text(ref.snapshot_id)
    || ref.epoch_id !== row.epoch_id || ref.snapshot_id !== (basis.snapshot as ObjectValue).snapshot_id
    || row.captured_parts.public_basis_missing_reason !== null) throw new Error("source_original_basis_reference_mismatch");
  const position = row.pre_position ?? row.position;
  if (!isObject(position) || position.epoch_id !== row.epoch_id || position.stream_generation !== ref.stream_generation)
    throw new Error("source_original_position_mismatch");
  ordinal(position.publication_index);
  if (expected && ["epoch_id", "scope_id", "stream_generation"].some(key => ref[key] !== expected[key]))
    throw new Error("source_original_scope_changed");
  return ref;
}

/** These are public facts, not a lossy gameplay signature. Read instances and
 * their metadata are all retained, including repeated kinds and card details.
 * Only current hand selection decoration is omitted for the exact card-drag
 * mechanism; every other context field remains literal. */
function dragWorld(basis: P1Basis, subject: string): string {
  const value = snapshot(basis);
  const context = structuredClone(value.interaction.content);
  delete context.surface;
  if (Array.isArray(context.context?.player?.hand))
    for (const card of context.context.player.hand) if (card.entity_id === subject) delete card.is_selected;
  const reads = basis.reads.map(capture => {
    const read = capture.response as ObjectValue;
    return { kind: read.kind, target: read.target_referent_id ?? null, schema: read.content_schema,
      visibility: read.visibility_basis, ordering: read.ordering_semantics, completeness: read.completeness, content: read.content };
  }).sort((a, b) => { const left = canonicalJson(a), right = canonicalJson(b); return left < right ? -1 : left > right ? 1 : 0; });
  return canonicalJson({ persistent: value.persistent, context, reads, session: value.session, policy: value.information_policy });
}

function dragSurfaceAllowed(original: P1Basis, current: P1Basis): boolean {
  const first = (original.snapshot as ObjectValue).interaction, next = (current.snapshot as ObjectValue).interaction;
  if (first.kind !== "combat_turn" || next.kind !== "combat_turn") return false;
  const before = structuredClone(first.content.surface), after = structuredClone(next.content.surface);
  if (after.can_end_turn !== false && after.can_end_turn !== before.can_end_turn) return false;
  delete before.can_end_turn; delete after.can_end_turn;
  for (const key of ["playable_cards", "usable_potions", "discardable_potions"]) {
    if (before[key] === undefined && after[key] === undefined) continue;
    if (!Array.isArray(before[key]) || !Array.isArray(after[key])
      || after[key].some((value: unknown) => !before[key].some((originalValue: unknown) => same(value, originalValue)))) return false;
    delete before[key]; delete after[key];
  }
  return same(before, after);
}

function args(value: unknown): ObjectValue | null {
  if (!Array.isArray(value)) return null;
  const output: ObjectValue = {};
  for (const argument of value) {
    if (!isObject(argument) || !text(argument.role) || !text(argument.referent_id) || argument.role in output) return null;
    output[argument.role] = argument.referent_id;
  }
  return output;
}
function hasReferents(value: ObjectValue, subject: string | null, arguments_: ObjectValue): boolean {
  const declared = new Set(value.referents.map((referent: ObjectValue) => referent.referent_id));
  return (subject === null || declared.has(subject)) && Object.values(arguments_).every(id => declared.has(id));
}
function accepted(row: ObjectValue): boolean {
  return row.outcome?.mapping_status === "exact" && row.outcome.match_count === 1
    && row.outcome.delivery === "delivered" && row.outcome.reason_code === null
    && row.outcome.native_facts?.origin === "native_input" && row.outcome.native_facts.missing_reason === null;
}
function acceptedAction(row: ObjectValue): boolean {
  const carriers = row.outcome.native_facts.carriers;
  return Array.isArray(carriers) && carriers.length === 2
    && carriers[0].kind === "game_action_submitted" && carriers[1].kind === "game_action_accepted"
    && text(carriers[0].carrier_ref) && carriers[0].carrier_ref === carriers[1].carrier_ref;
}
function cancellationContext(trace: readonly SourceP1TraceInput[], contexts: readonly P1Basis[], basis: P1Basis): boolean {
  if (trace.length !== 2) return false;
  const start = trace[0] as ObjectValue, last = trace[1] as ObjectValue;
  const firstFacts = start.outcome?.native_facts, lastFacts = last.outcome?.native_facts;
  const created = firstFacts?.carriers;
  if (!accepted(start) || start.outcome.native_mechanism !== "NPlayerHand.StartCardPlay"
    || start.outcome.selected_action?.verb !== "begin_card_play"
    || !Array.isArray(created) || created.length !== 1 || created[0].kind !== "card_play_created"
    || !text(created[0].carrier_ref) || !text(firstFacts.owner_ref) || !text(firstFacts.subject_ref)
    || last.outcome?.native_mechanism !== "NCardPlay.TryPlayCard"
    || last.outcome.delivery !== "unknown" || last.outcome.reason_code !== "native_source_scope_ended_without_acceptance"
    || lastFacts?.origin !== "native_input" || lastFacts.missing_reason !== null
    || lastFacts.owner_ref !== created[0].carrier_ref || lastFacts.subject_ref !== firstFacts.subject_ref
    || !Array.isArray(lastFacts.carriers) || lastFacts.carriers.length !== 1
    || lastFacts.carriers[0].kind !== "card_play_canceled" || lastFacts.carriers[0].carrier_ref !== created[0].carrier_ref
    || !Array.isArray(last.publications_after) || !Array.isArray(last.source_boundaries)) return false;
  const cancellations = last.publications_after.filter((publication: ObjectValue) => publication.captured_parts?.native_stage !== null
    && publication.captured_parts?.native_stage !== undefined);
  if (cancellations.length !== 1) return false;
  const stage = cancellations[0].captured_parts.native_stage;
  if (stage.kind !== "card_play_canceled" || stage.evidence !== "native_finished_false" || stage.no_submission !== true
    || stage.owner_ref !== created[0].carrier_ref || stage.subject_ref !== firstFacts.subject_ref
    || !same(stage.arguments, { hand: firstFacts.owner_ref })) return false;
  const world = dragWorld(basis, firstFacts.subject_ref);
  return contexts.every(context => dragSurfaceAllowed(basis, context) && dragWorld(context, firstFacts.subject_ref) === world);
}

function crossesPausedInterval(boundaries: readonly ObjectValue[], first: ObjectValue, last: ObjectValue, through: bigint): boolean {
  const from = ordinal(first.pre_position.publication_index), firstInput = ordinal(first.input_prefix_ordinal);
  const lastInput = ordinal(last.input_prefix_ordinal);
  for (const boundary of boundaries) {
    if (boundary.session_id !== first.session_id || boundary.timeline_id !== first.timeline_id)
      throw new Error("source_boundary_session_mismatch");
    if (!Array.isArray(boundary.paused_intervals)) throw new Error("source_boundary_intervals_missing");
    for (const interval of boundary.paused_intervals) {
      if (interval.epoch_id !== first.epoch_id || interval.stream_generation !== first.pre_position.stream_generation) continue;
      if (ordinal(interval.after_index) < through && ordinal(interval.through_index) >= from
        || ordinal(interval.after_input_ordinal) < lastInput && ordinal(interval.through_input_ordinal) >= firstInput) return true;
    }
  }
  return false;
}
function originalActionMatches(row: ObjectValue, action: ObjectValue, basis: P1Basis, play = false,
  controlOperation: string | null = null): boolean {
  if (!accepted(row)) return false;
  const selected = row.outcome.selected_action, facts = row.outcome.native_facts;
  const expectedArgs = args(action.arguments), actualArgs = args(selected?.arguments);
  if (!expectedArgs || !actualArgs || !isObject(facts.arguments)
    || !same(expectedArgs, facts.arguments) || !same(actualArgs, facts.arguments)
    || (selected.subject_referent_id ?? null) !== (facts.subject_ref ?? null)
    || (action.subject_referent_id ?? null) !== (facts.subject_ref ?? null)
    || !hasReferents(snapshot(basis), action.subject_referent_id ?? null, expectedArgs)) return false;
  // Native-logical IDs and Player Environment IDs are separate catalogs. Only
  // the original object-bound verb/operands are compared, never action ID text.
  if (controlOperation !== null) {
    const publicVerb = controlOperation === "confirm" || controlOperation === "confirm_selection" ? "confirm"
      : controlOperation === "open_preview" || controlOperation === "toggle_upgrade_view" ? "open"
      : controlOperation === "cancel_preview" || controlOperation === "cancel_selection" ? "cancel" : null;
    return publicVerb !== null && action.verb === publicVerb && selected.verb === controlOperation;
  }
  if (play) return action.verb === "play" && ["confirm_card", "confirm_target"].includes(selected.verb);
  if (row.outcome.native_mechanism === "NRewardButton.OnRelease"
    && ["claim_reward", "claim_linked_reward"].includes(selected.verb)) return action.verb === "activate";
  return selected.verb === action.verb;
}

function originalSelectorActionMatches(row: ObjectValue, action: ObjectValue, basis: P1Basis): boolean {
  const surface = snapshot(basis).interaction.content.surface;
  return completeSelectorFacts(surface) && row.outcome.native_facts.owner_ref === surface.screen_entity_id
    && originalActionMatches(row, action, basis, false, selectorOperation(surface, action.subject_referent_id ?? null));
}

/** Match original native inputs to one complete offered P1 intention. A matched
 * physical input is not an Agent choice, a Commit proof or a Human-origin claim. */
export function matchSourceP1Choice(state: P1SessionState, basis: P1Basis,
  trace: readonly SourceP1TraceInput[]): SourceP1Match {
  try {
    if (trace.length === 0 || trace.length > 4096) return result("incomplete", "source_trace_empty_or_over_capacity");
    const initial = snapshot(basis), reduction = reduce(state, basis);
    if (!reduction.complete || reduction.observation.status !== "decision")
      return result("incomplete", "source_initial_p1_basis_incomplete");
    const first = trace[0] as ObjectValue;
    if (!first.basis || !same(first.basis, basis)) return result("incomplete", "source_trace_does_not_start_at_original_basis");
    const original = reference(first, basis); const ids: string[] = [];
    let previousOrdinal = ordinal(first.input_prefix_ordinal) - 1n;
    let position = ordinal(first.pre_position.publication_index);
    const contexts: P1Basis[] = [];
    for (let index = 0; index < trace.length; index++) {
      const row = trace[index] as ObjectValue;
      if (!text(row.input_id) || ids.includes(row.input_id) || row.session_id !== first.session_id
        || row.timeline_id !== first.timeline_id || row.epoch_id !== first.epoch_id || row.segment_id !== first.segment_id
        || row.basis_order?.status !== "native_prefix_frozen" || row.basis_order.reason_code !== null
        || ordinal(row.input_prefix_ordinal) !== previousOrdinal + 1n || !row.basis)
        return result("incomplete", "source_prefix_order_or_context_unproven");
      previousOrdinal++; reference(row, row.basis, original);
      const current = snapshot(row.basis);
      if (!same(current.session, initial.session)) return result("incomplete", "source_runtime_session_changed");
      const nextPosition = ordinal(row.pre_position.publication_index);
      if (nextPosition < position || !Array.isArray(row.publications_before)) return result("incomplete", "source_publication_order_invalid");
      // Publications before the selected initial boundary are history, not an
      // intermediate state of this intention. Later publications are all checked.
      if (index > 0) for (const publication of row.publications_before as ObjectValue[]) {
        if (publication.session_id !== first.session_id || publication.timeline_id !== first.timeline_id
          || publication.segment_id !== first.segment_id || publication.gap_after_index !== null || !publication.basis)
          return result("incomplete", "source_intermediate_publication_gap");
        reference(publication, publication.basis, original);
        const at = ordinal(publication.position.publication_index);
        if (at < position || at > nextPosition) return result("incomplete", "source_intermediate_publication_order_invalid");
        position = at; contexts.push(publication.basis);
      }
      position = nextPosition; ids.push(row.input_id);
      if (index > 0) contexts.push(row.basis);
    }

    const last = trace.at(-1) as ObjectValue;
    if (last.publications_after !== undefined) {
      if (!Array.isArray(last.publications_after)) return result("incomplete", "source_tail_publications_invalid");
      for (const publication of last.publications_after as ObjectValue[]) {
        if (publication.session_id !== first.session_id || publication.timeline_id !== first.timeline_id
          || publication.segment_id !== first.segment_id || publication.gap_after_index !== null || !publication.basis)
          return result("incomplete", "source_tail_publication_gap");
        reference(publication, publication.basis, original);
        const at = ordinal(publication.position.publication_index);
        if (at < position) return result("incomplete", "source_tail_publication_order_invalid");
        position = at; contexts.push(publication.basis);
      }
    }
    if (last.source_boundaries !== undefined && (!Array.isArray(last.source_boundaries)
      || crossesPausedInterval(last.source_boundaries, first, last, position)))
      return result("incomplete", "source_span_crosses_recording_pause");
    if (cancellationContext(trace, contexts, basis))
      return result("context_only", "original_canceled_drag_without_submission_preserves_public_world", ids);
    if (trace.some(row => !accepted(row))) return result("incomplete", "source_original_delivery_or_native_binding_unproven");

    const matches: string[] = [];
    const originalActions = initial.bound_actions.actions as ObjectValue[];
    for (const [choice, lowering] of Object.entries(reduction.lowering)) {
      if (!lowering.first) continue;
      const action = originalActions.find(value => value.bound_action_id === lowering.first!.bound_action_id);
      if (!action) continue;
      if (trace.length === 1 && lowering.follow.length === 0 && originalActionMatches(first, action, basis)) {
        if (action.verb === "end_turn" && (!acceptedAction(first)
          || !["native_end_turn_ui", "native_ftue_end_turn_ui"].includes(first.outcome.native_mechanism))) continue;
        // Card submission requires its original StartCardPlay carrier chain.
        if (action.verb === "play") continue;
        matches.push(choice); continue;
      }
      if (trace.length === 2 && lowering.follow.length === 0 && action.verb === "play") {
        const last = trace[1] as ObjectValue, facts = first.outcome.native_facts;
        const carriers = facts.carriers;
        if (first.outcome.native_mechanism !== "NPlayerHand.StartCardPlay"
          || first.outcome.selected_action?.verb !== "begin_card_play"
          || first.outcome.selected_action.subject_referent_id !== facts.subject_ref
          || last.outcome.native_mechanism !== "NCardPlay.TryPlayCard"
          || !Array.isArray(carriers) || carriers.length !== 1 || carriers[0].kind !== "card_play_created"
          || !text(carriers[0].carrier_ref) || last.outcome.native_facts.owner_ref !== carriers[0].carrier_ref
          || facts.subject_ref !== last.outcome.native_facts.subject_ref || !text(facts.owner_ref)
          || !text(facts.arguments?.holder) || Object.keys(facts.arguments).length !== 1
          || !acceptedAction(last) || !originalActionMatches(last, action, basis, true)) continue;
        const world = dragWorld(basis, facts.subject_ref);
        if (contexts.some(value => !dragSurfaceAllowed(basis, value) || dragWorld(value, facts.subject_ref) !== world)) continue;
        matches.push(choice); continue;
      }
      if (trace.length === 2 && lowering.follow.length === 1 && lowering.reward_intention
        && lowering.reward_intention.kind !== "decline" && originalActionMatches(first, action, basis)) {
        // Physical Skip does not establish an irreversible P1 decline: a human
        // can reopen it, and native return may change a later reward hook.
        const last = trace[1] as ObjectValue;
        const follow = resolveFollowBasis(lowering.follow[0]!, lowering, last.basis);
        if (!follow) continue;
        const actual = (last.basis.snapshot as ObjectValue).bound_actions.actions.find((value: ObjectValue) => value.bound_action_id === follow.bound_action_id);
        if (!actual || !originalActionMatches(last, actual, last.basis)) continue;
        if (contexts.some(value => !same(value, last.basis) && !rewardContextMatches(lowering, value))) continue;
        matches.push(choice);
      }
      if (trace.length === 2 && lowering.selector_intention && lowering.follow.length === 1
        && lowering.follow[0]!.rule_id === "selector_intention"
        && originalSelectorActionMatches(first, action, basis)) {
        const follow = resolveFollowBasis(lowering.follow[0]!, lowering, last.basis);
        if (!follow) continue;
        const actual = snapshot(last.basis).bound_actions.actions.find((value: ObjectValue) => value.bound_action_id === follow.bound_action_id);
        if (!actual || !originalSelectorActionMatches(last, actual, last.basis)) continue;
        if (contexts.some(value => !resolveFollowBasis(lowering.follow[0]!, lowering, value))) continue;
        matches.push(choice);
      }
    }
    return matches.length === 1 ? result("matched", "original_native_trace_matches_one_p1_intention", ids, matches[0]!)
      : matches.length > 1 ? result("ambiguous", "original_native_trace_matches_multiple_p1_intentions")
      : result("unmapped", "original_native_trace_has_no_qualified_p1_intention");
  } catch (error) {
    return result("incomplete", error instanceof Error ? error.message : "source_trace_invalid");
  }
}

function rewardContextMatches(lowering: Lowering, basis: P1Basis): boolean {
  // The shared continuation verifier checks all public facts and exact owner,
  // not a native stage label or snapshot equality inferred from nearby rows.
  return !!resolveFollowBasis(lowering.follow[0]!, lowering, basis)
    && reduce(createSession("source-context-check", "reference"), basis).complete;
}

/** Publication interpretation v1: observations are not choices. Keep the entire
 * public basis (including every Read instance and binding); only capture time is
 * ignored for repeated delivery of the same snapshot. A changed Read under the
 * same snapshot is a new boundary, never navigation noise. */
export function classifySourceP1Publication(state: P1SessionState, row: SourceP1Publication,
  previous: SourceP1Publication | null): { status: "context_only" | "unresolved";
    classification: string; reason: string; advance: boolean } {
  const unresolved = (reason: string) => ({ status: "unresolved" as const,
    classification: "unresolved_context", reason, advance: false });
  try {
    const current = row as ObjectValue;
    if (!current.basis || current.gap_after_index !== null) return unresolved("source_publication_basis_missing_or_gap");
    const value = snapshot(current.basis); reference(current, current.basis);
    if (value.completeness.missing.length || value.bound_actions.status !== "complete")
      return unresolved("source_publication_public_information_incomplete");
    if (previous) {
      const prior = previous as ObjectValue;
      if (current.session_id !== prior.session_id || current.timeline_id !== prior.timeline_id)
        return unresolved("source_publication_session_changed");
      if (current.epoch_id === prior.epoch_id) {
        reference(current, current.basis, prior.captured_parts.public_basis);
        if (ordinal(current.position.publication_index) <= ordinal(prior.position.publication_index))
          return unresolved("source_publication_order_invalid");
      }
    }
    const reduction = reduce(state, current.basis);
    // A fully captured waiting state has no choice. Structural/Read omissions
    // remain unresolved; waiting is not permission to waive required facts.
    if (value.status === "settling" && value.bound_actions.actions.length === 0
      && reduction.missing.every(item => item === `scene:${value.interaction.kind}:settling`))
      return { status: "context_only", classification: "source_settling_context",
        reason: "original_complete_public_state_waits_for_native_input_owner", advance: false };
    if (!reduction.complete) return unresolved("source_publication_p1_information_incomplete");
    const view = (basis: P1Basis) => {
      const copy = structuredClone(basis) as ObjectValue;
      delete copy.snapshot.observed_at;
      for (const read of copy.reads) if (read.response) delete read.response.observed_at;
      return canonicalJson(copy);
    };
    if (previous && (previous as ObjectValue).basis && current.epoch_id === (previous as ObjectValue).epoch_id
      && current.segment_id === (previous as ObjectValue).segment_id
      && view(current.basis) === view((previous as ObjectValue).basis))
      return { status: "context_only", classification: "source_repeated_public_snapshot",
        reason: "same_original_snapshot_and_all_read_instances_except_capture_time", advance: false };
    return { status: "context_only", classification: reduction.observation.status === "terminal"
      ? "p1_terminal_boundary" : previous === null && state.last_snapshot_id === null ? "p1_initial_boundary" : "p1_publication_boundary",
      reason: "original_public_information_boundary_without_a_native_choice", advance: true };
  } catch (error) { return unresolved(error instanceof Error ? error.message : "source_publication_invalid"); }
}
