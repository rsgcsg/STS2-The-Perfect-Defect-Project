import { decodePlayerRead } from "../protocol.js";
import { decodePublicSourceSnapshot as decodePlayerSnapshot } from "../publicBasis.js";
import { canonicalJson, isObject } from "./canonical.js";
import { createSession, reduce, resolveFollowBasis, type Lowering, type P1Basis, type P1SessionState } from "./reducer.js";
import { completeSelectorFacts, selectorOperation } from "./selectorForward.js";
import { settledRewardTriggerDisplay } from "./rewardForward.js";

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
// TryPlayCard is reached after its mouse confirmation was consumed. Its
// current catalog may correctly offer only Cancel. The original actual call
// and submitted/accepted carrier still bind to the earlier complete Play.
function consumedPhysicalPlay(row: ObjectValue): boolean {
  return row.outcome?.mapping_status === "unmapped" && row.outcome.match_count === 0
    && row.outcome.selected_action === null && row.outcome.delivery === "delivered" && row.outcome.reason_code === null
    && row.outcome.native_mechanism === "NCardPlay.TryPlayCard"
    && row.outcome.native_facts?.origin === "native_input" && row.outcome.native_facts.missing_reason === null
    && acceptedAction(row);
}
function completedPhysicalPlay(row: ObjectValue, action: ObjectValue, basis: P1Basis): boolean {
  if (!consumedPhysicalPlay(row)) return acceptedAction(row) && originalActionMatches(row, action, basis, true);
  const operands = args(action.arguments), facts = row.outcome.native_facts;
  return action.verb === "play" && operands !== null && isObject(facts.arguments)
    && action.subject_referent_id === facts.subject_ref && same(operands, facts.arguments)
    && hasReferents(snapshot(basis), action.subject_referent_id, operands);
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

// E3 is deliberately limited to the observed native deck-card callback before
// max-selection opens a preview. It adds no Agent action or reducer exception.
export function isSourceP1ReversibleSelect(input: SourceP1TraceInput): boolean {
  try {
    const row = input as ObjectValue, surface = snapshot(row.basis).interaction.content.surface;
    return accepted(row) && completeSelectorFacts(surface)
      && surface.kind === "native_deck_card_selection" && surface.stage === "selecting"
      && surface.selector_request.family === "deck_card"
      && surface.selector_request.add_transition === "native_preview_at_max"
      && surface.selected_count + 1 < surface.max_select
      && row.outcome.native_mechanism === "MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NDeckCardSelectScreen.OnCardClicked"
      && row.outcome.selected_action?.verb === "select"
      && row.outcome.native_facts.owner_ref === surface.screen_entity_id
      && surface.selectable_card_entity_ids.includes(row.outcome.native_facts.subject_ref)
      && row.outcome.native_facts.carriers?.length === 0 && row.outcome.stages?.length === 0;
  } catch { return false; }
}

/** Shared original-world comparison for a completed Human presentation round trip.
 * Preserve the complete PB1/catalog and every Read. Only each original's checked
 * snapshot locator, source sequence and observation timestamp are transport metadata.
 * This is the same boundary used by the existing Source4 selector-reversal proof. */
function originalPresentationBasis(basis: P1Basis): ObjectValue {
  snapshot(basis); // Validate every advertised Read against its own frozen snapshot.
  const copy = structuredClone(basis) as ObjectValue;
  // PB3 native witnesses are capture provenance, not an extra public-world field.
  // The original displayed counters remain in snapshot/Reads and compare strictly.
  delete copy.settled_relic_counters;
  delete copy.snapshot.snapshot_id; delete copy.snapshot.sequence; delete copy.snapshot.observed_at;
  for (const read of copy.reads) {
    delete read.response.expected_snapshot_id; delete read.response.observed_snapshot_id; delete read.response.observed_at;
  }
  return copy;
}

export function compareOriginalPresentationBases(before: P1Basis, after: P1Basis): {
  readonly equal: boolean; readonly changes: readonly { path: string; before: unknown; after: unknown }[];
} {
  const changes: { path: string; before: unknown; after: unknown }[] = [];
  function visit(a: unknown, b: unknown, path: string): void {
    if (canonicalJson(a) === canonicalJson(b)) return;
    if (a !== null && b !== null && typeof a === "object" && typeof b === "object"
        && Array.isArray(a) === Array.isArray(b)) {
      const left = a as ObjectValue, right = b as ObjectValue;
      for (const key of new Set([...Object.keys(left), ...Object.keys(right)])) visit(left[key], right[key], `${path}.${key}`);
    } else changes.push({ path, before: a, after: b });
  }
  visit(originalPresentationBasis(before), originalPresentationBasis(after), "$");
  return { equal: changes.length === 0, changes };
}

/** Called only after the native adapter has proved a same-reward open/return.
 * Raw equality/differences stay strict; the approved display rule is separate. */
export function compareRewardPresentationBases(before: P1Basis, after: P1Basis) {
  const comparison = compareOriginalPresentationBases(before, after);
  const display = settledRewardTriggerDisplay((before.snapshot as ObjectValue).persistent,
    (after.snapshot as ObjectValue).persistent);
  const displayChanges = display ? comparison.changes.filter(change => change.path === display.path
    && change.before === 2 && change.after === 0) : [];
  return { ...comparison,
    presentation_equal: comparison.changes.length === displayChanges.length,
    display_animation: displayChanges.length === 1 ? {
      id: "lasting_candy_trigger_display_2_to_0", changes: displayChanges,
      originals_preserved: true, model_input_unchanged: true
    } : null };
}

// Preserve all original public fields except the declared selection decoration
// of this one card. Locator/time fields are checked against each own original
// before comparing worlds; no later basis repairs an absent original.
function selectorReversalView(basis: P1Basis, subject: string, decoration: boolean): string {
  const copy = originalPresentationBasis(basis), value = snapshot(basis);
  const surface = value.interaction.content.surface;
  if (!completeSelectorFacts(surface) || !reduce(createSession("source-reversal-check", "reference"), basis).complete)
    throw new Error("source_selector_reversal_basis_incomplete");
  for (const read of copy.reads) {
    if (decoration && read.kind === "surface_card" && read.response.target_referent_id === subject)
      read.response.content.is_selected = false;
  }
  if (decoration) {
    const cleanSurface = (s: ObjectValue) => {
      const selected = s.selected_card_entity_ids.includes(subject);
      s.selected_count -= selected ? 1 : 0;
      for (const key of ["selected_card_entity_ids", "selectable_card_entity_ids", "deselectable_card_entity_ids"])
        s[key] = s[key].filter((id: string) => id !== subject);
      s.selector_request.selected_entity_ids = s.selector_request.selected_entity_ids.filter((id: string) => id !== subject);
      for (const card of s.cards) if (card.entity_id === subject) card.is_selected = false;
      for (const option of s.preview_options) if (option.subject_entity_id === subject) option.before.is_selected = false;
    };
    cleanSurface(copy.snapshot.interaction.content.surface);
    for (const ref of copy.snapshot.referents) {
      if (ref.referent_id === subject) { ref.state.selected = false; ref.properties.is_selected = false; }
      if (ref.properties?.kind === surface.kind && ref.properties.screen_entity_id === surface.screen_entity_id)
        cleanSurface(ref.properties);
    }
    for (const action of copy.snapshot.bound_actions.actions)
      if (action.subject_referent_id === subject && ["select", "deselect"].includes(action.verb)) {
        action.verb = "select"; action.label = action.label.replace(/^(?:Select|Deselect)(?=\s|$)/u, ""); action.bound_action_id = null;
      }
    // The native capability inventory adds deselect for the selected card.
    copy.snapshot.interaction.capabilities = copy.snapshot.interaction.capabilities.filter((cap: ObjectValue) =>
      !(cap.verb === "deselect" && cap.subject_role === "card" && same(cap.arguments, [])
        && cap.availability_basis === "current_native_interaction"));
  }
  return canonicalJson(copy);
}

function selectorReversalContext(trace: readonly SourceP1TraceInput[], contexts: readonly P1Basis[], basis: P1Basis,
  following: SourceP1TraceInput | null): boolean {
  if (trace.length !== 2 || !isSourceP1ReversibleSelect(trace[0]!)) return false;
  const first = trace[0] as ObjectValue, last = trace[1] as ObjectValue;
  if (last.outcome?.selected_action?.verb !== "deselect") return false;
  const subject = first.outcome.native_facts.subject_ref, surface = snapshot(basis).interaction.content.surface;
  const selected = snapshot(last.basis).interaction.content.surface;
  const actual = snapshot(last.basis).bound_actions.actions.find((a: ObjectValue) =>
    a.verb === "deselect" && a.subject_referent_id === subject);
  if (!accepted(last) || !actual || !originalActionMatches(last, actual, last.basis)
    || last.outcome.native_mechanism !== first.outcome.native_mechanism
    || last.outcome.native_facts.owner_ref !== surface.screen_entity_id
    || !same(last.outcome.native_facts.carriers, []) || !same(last.outcome.stages, [])
    || !same(first.outcome.native_facts.arguments, {})
    || !same(last.outcome.native_facts.arguments, {}) || selected.stage !== "selecting"
    || !same(selected.selected_card_entity_ids, [...surface.selected_card_entity_ids, subject])) throw new Error("source_selector_reversal_input_not_exact");
  const select = snapshot(basis).bound_actions.actions.find((a: ObjectValue) =>
    a.verb === "select" && a.subject_referent_id === subject);
  if (!select || !originalActionMatches(first, select, basis) || !Array.isArray(last.source_boundaries)) throw new Error("source_selector_reversal_boundaries_or_select_missing");
  const tail = [...(last.publications_after ?? [])] as ObjectValue[];
  if (following) {
    const next = following as ObjectValue;
    if (!next.basis || next.session_id !== first.session_id || next.timeline_id !== first.timeline_id
      || next.epoch_id !== first.epoch_id || next.segment_id !== first.segment_id
      || next.basis_order?.status !== "native_prefix_frozen" || next.basis_order.reason_code !== null
      || ordinal(next.input_prefix_ordinal) !== ordinal(last.input_prefix_ordinal) + 1n
      || ordinal(next.pre_position.publication_index) < ordinal(last.pre_position.publication_index)) return false;
    reference(next, next.basis, first.captured_parts.public_basis);
    if (!Array.isArray(next.publications_before)) return false;
    let at = ordinal(last.pre_position.publication_index);
    for (const publication of next.publications_before) {
      if (!publication.basis || publication.gap_after_index !== null || publication.session_id !== first.session_id
        || publication.timeline_id !== first.timeline_id || publication.segment_id !== first.segment_id) return false;
      reference(publication, publication.basis, first.captured_parts.public_basis);
      const position = ordinal(publication.position.publication_index);
      if (position < at || position > ordinal(next.pre_position.publication_index)) return false;
      at = position; tail.push(publication);
    }
    if (crossesPausedInterval(last.source_boundaries, first, next, ordinal(next.pre_position.publication_index))) return false;
    tail.push(next);
  }
  if (tail.length === 0 || [...last.publications_before, ...tail].some(row =>
    row.captured_parts?.native_stage != null)) return false;
  const world = selectorReversalView(basis, subject, true), restored = selectorReversalView(basis, subject, false);
  if (![...contexts, ...tail.map(row => row.basis)].every(context => selectorReversalView(context, subject, true) === world))
    throw new Error("source_selector_reversal_public_world_changed");
  if (!tail.every(row => selectorReversalView(row.basis, subject, false) === restored))
    throw new Error("source_selector_reversal_not_restored");
  return true;
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
 * physical input is not an Agent choice, a Commit proof or a Human-origin claim.
 * followingInput is an unconsumed original pre-basis endpoint for E3 only. */
export function matchSourceP1Choice(state: P1SessionState, basis: P1Basis,
  trace: readonly SourceP1TraceInput[], followingInput: SourceP1TraceInput | null = null): SourceP1Match {
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
    if (selectorReversalContext(trace, contexts, basis, followingInput))
      return result("context_only", "original_adjacent_selector_reversal_restores_public_world", ids);
    if (cancellationContext(trace, contexts, basis))
      return result("context_only", "original_canceled_drag_without_submission_preserves_public_world", ids);
    const consumedPlayTail = trace.length === 2 && accepted(first)
      && first.outcome.native_mechanism === "NPlayerHand.StartCardPlay" && consumedPhysicalPlay(last);
    if (trace.some(row => !accepted(row)) && !consumedPlayTail)
      return result("incomplete", "source_original_delivery_or_native_binding_unproven");

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
          || !completedPhysicalPlay(last, action, basis)) continue;
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
