/** Offline D supervision for one native, manually confirmed hand selection.
 * Native acceptances and bases are immutable. Only the new projection owns the
 * finalized target; it is never a claim about the Human's first click. */
import { isDeepStrictEqual as same } from 'node:util';

export const selectorTransactionProjection = 'native-selector-finalized-d-1';
const hand = 'MegaCrit.Sts2.Core.Nodes.Combat.NPlayerHand';
const methods = { select: `${hand}.SelectCardInSimpleMode`, deselect: 'MegaCrit.Sts2.Core.Nodes.Combat.NSelectedHandCardContainer.DeselectHolder',
  confirm: `${hand}.SelectModeConfirmButton.Released` };
const sorted = values => [...values].sort();
const surfaceOf = value => value.snapshot.interaction.content.surface;
const complete = value => value?.capture_status === 'complete' && value.missing.length === 0
  && value.snapshot.completeness.status === 'complete'
  && value.snapshot.information_policy.includes_hidden_information === false;
const bindingOf = row => ({ session: row.session_id, timeline: row.timeline_id, run: row.run_id,
  owner: row.native_witness?.selector_owner_witness_id,
  parent: row.native_witness?.parent_action_witness_id,
  parent_owner: row.native_witness?.parent_owner_witness_id,
  factory: row.native_witness?.factory_mechanism, origin: row.native_witness?.native_origin ?? null });

/** Compare every original field/Read after verifying the *only* permitted
 * differences: native selection decoration, its finite controls, and the hand
 * holder order changed by moving a card into/out of the selected container.
 * This view is proof-only. It is never supplied to the reducer or the model. */
function selectionWorld(value, initial, core) {
  if (!complete(value)) return null;
  core.compareOriginalPresentationBases(value, value); // validates own advertised Reads
  const s = surfaceOf(value), first = surfaceOf(initial);
  if (s.kind !== 'combat_hand_card_selection' || s.selection_mode !== 'simple_select'
    || s.min_select !== 1 || s.max_select !== 1 || s.require_manual_confirmation !== true
    || s.is_peeking !== false || s.can_close_peek !== false
    || !Array.isArray(s.selected_card_entity_ids) || s.selected_card_entity_ids.length > 1
    || s.selected_count !== s.selected_card_entity_ids.length) return null;
  const candidates = sorted(first.cards.map(card => card.entity_id)), selected = s.selected_card_entity_ids;
  if (new Set(candidates).size !== candidates.length || !same(sorted(s.cards.map(card => card.entity_id)), candidates)
    || !selected.every(id => candidates.includes(id))
    || !same(sorted(s.selectable_card_entity_ids), candidates.filter(id => !selected.includes(id)))
    || !same(sorted(s.deselectable_card_entity_ids), sorted(selected))
    || s.can_confirm !== (selected.length === 1)
    || !s.cards.every(card => card.is_selected === selected.includes(card.entity_id))) return null;
  const actions = value.snapshot.bound_actions;
  if (actions.status !== 'complete' || actions.total_count !== candidates.length + selected.length
    || actions.materialized_count !== actions.total_count || actions.actions.length !== actions.total_count) return null;
  const initialActions = initial.snapshot.bound_actions.actions;
  const owner = initialActions[0]?.interaction_id;
  if (!owner || !actions.actions.every(action => action.interaction_id === owner && same(action.arguments, []))) return null;
  const actionFields = new Set(['bound_action_id', 'verb', 'interaction_id', 'subject_referent_id', 'arguments', 'label']);
  if (!actions.actions.every(action => {
    if (Object.keys(action).some(key => !actionFields.has(key))) return false;
    const card = s.cards.find(card => card.entity_id === action.subject_referent_id);
    return action.label === (action.verb === 'confirm' ? 'Confirm selected cards'
      : `${action.verb === 'deselect' ? 'Deselect' : selected.length ? 'Replace current selection with' : 'Select'} ${card?.name}`);
  })) return null;
  const expected = candidates.map(id => [selected.includes(id) ? 'deselect' : 'select', id]);
  if (selected.length) expected.push(['confirm', null]);
  const signature = action => `${action.verb}:${action.subject_referent_id ?? ''}`;
  if (!same(sorted(actions.actions.map(signature)), sorted(expected.map(([verb, id]) => `${verb}:${id ?? ''}`)))) return null;
  // An existing select must retain its exact native BoundAction identity. A
  // deselect/confirm is checked against its own original, never invented here.
  if (!actions.actions.filter(a => a.verb === 'select').every(action => initialActions.some(firstAction =>
    firstAction.bound_action_id === action.bound_action_id && firstAction.verb === 'select'
    && firstAction.subject_referent_id === action.subject_referent_id))) return null;
  const capabilities = value.snapshot.interaction.capabilities;
  const expectedCapabilities = structuredClone(initial.snapshot.interaction.capabilities)
    .filter(capability => capability.verb !== 'select' || candidates.length > selected.length);
  if (selected.length) expectedCapabilities.push(
    { verb: 'confirm', arguments: [], availability_basis: 'current_native_interaction' },
    { verb: 'deselect', subject_role: 'card', arguments: [], availability_basis: 'current_native_interaction' });
  const canonical = object => JSON.stringify(Object.fromEntries(Object.entries(object).sort(([a], [b]) => a.localeCompare(b))));
  if (!same(sorted(capabilities.map(canonical)), sorted(expectedCapabilities.map(canonical)))) return null;
  const copy = structuredClone(value), clean = surface => {
    surface.selected_count = 0; surface.selected_card_entity_ids = [];
    surface.selectable_card_entity_ids = candidates; surface.deselectable_card_entity_ids = [];
    surface.can_confirm = false;
    surface.cards = surface.cards.map(card => ({ ...card, is_selected: false }))
      .sort((a, b) => a.entity_id.localeCompare(b.entity_id));
  };
  clean(surfaceOf(copy));
  copy.snapshot.bound_actions.actions = structuredClone(initial.snapshot.bound_actions.actions);
  copy.snapshot.bound_actions.total_count = initial.snapshot.bound_actions.total_count;
  copy.snapshot.bound_actions.materialized_count = initial.snapshot.bound_actions.materialized_count;
  copy.snapshot.interaction.capabilities = structuredClone(initial.snapshot.interaction.capabilities);
  for (const ref of copy.snapshot.referents) {
    if (candidates.includes(ref.referent_id)) {
      if (Object.hasOwn(ref.state, 'selected')) {
        if (ref.state.selected !== selected.includes(ref.referent_id)) return null;
        ref.state.selected = false;
      }
      if (Object.hasOwn(ref.properties, 'is_selected')) {
        if (ref.properties.is_selected !== selected.includes(ref.referent_id)) return null;
        ref.properties.is_selected = false;
      }
    }
    if (ref.properties?.kind === s.kind) clean(ref.properties);
  }
  for (const read of copy.reads) if (read.kind === 'surface_card' && candidates.includes(read.response.target_referent_id)
    && read.response.content.display_context === 'surface:combat_hand_card_selection'
    && Object.hasOwn(read.response.content, 'is_selected')) {
    if (read.response.content.is_selected !== selected.includes(read.response.target_referent_id)) return null;
    read.response.content.is_selected = false;
  }
  return copy;
}

export function classifyNativeHandSelectionTransaction({ row, acceptedRows, events, terminalById, basis, state, core }) {
  if (row.native_action_type !== methods.select || !row.basis) return null;
  const initial = basis(row.basis), initialSurface = surfaceOf(initial);
  if (initialSurface.kind !== 'combat_hand_card_selection' || initialSurface.selected_count !== 0
    || initialSurface.selection_mode !== 'simple_select' || initialSurface.min_select !== 1
    || initialSurface.max_select !== 1 || initialSurface.require_manual_confirmation !== true) return null;
  const binding = bindingOf(row);
  if (![binding.owner, binding.parent, binding.parent_owner].every(value => typeof value === 'string' && value.length)
    || binding.factory !== `${hand}.SelectCards`) return null;
  const inputs = [];
  for (const next of acceptedRows.slice(acceptedRows.indexOf(row))) {
    if (!same(bindingOf(next), binding) || !Object.values(methods).includes(next.native_action_type)) break;
    inputs.push(next);
    if (next.native_action_type === methods.confirm) break;
  }
  // Normal one-select/confirm keeps the old exact per-input interpretation.
  if (inputs.length <= 2 && !inputs.some(input => input.native_action_type === methods.deselect)) return null;
  const reject = reason => ({ qualified: false, reason, from_sequence: row.sequence,
    decision_ids: inputs.map(input => input.decision_id) });
  const confirm = inputs.at(-1);
  if (confirm?.native_action_type !== methods.confirm) return reject('selector_transaction_exact_confirmation_missing');
  const end = terminalById.get(confirm.decision_id);
  if (!end?.successor_basis || end.phase !== 'finished' || end.successor_missing_reason != null)
    return reject('selector_transaction_confirmation_successor_missing');
  const initialWorld = selectionWorld(initial, initial, core);
  if (!initialWorld || !core.reduce(state, initial).complete) return reject('selector_transaction_initial_information_incomplete');
  const sameWorld = value => {
    const world = selectionWorld(value, initial, core);
    return world && core.compareOriginalPresentationBases(initialWorld, world).equal;
  };
  let selected = [], anchor = row, lastEdit = null;
  for (const input of inputs) {
    const terminal = terminalById.get(input.decision_id), value = input.basis ? basis(input.basis) : null;
    const terminalInput = input === confirm;
    const verb = terminalInput ? 'confirm' : input.native_action_type === methods.select ? 'select' : 'deselect';
    if (!same(bindingOf(input), binding) || input.missing_reason != null || input.native_mechanism !== 'native_selector_input'
      || input.native_receipt?.kind !== 'semantic_ui_accepted' || input.native_receipt.carrier_ref !== input.action_witness_id
      || input.native_receipt.selector_callback?.native_callback !== methods[verb]
      || input.native_receipt.selector_callback.native_acceptance_proven !== true
      || input.native_receipt.selector_callback.terminal !== terminalInput
      || !terminal || terminal.action_witness_id !== input.action_witness_id || terminal.phase !== 'finished'
      || terminal.native_completion?.native_callback !== methods[verb] || terminal.native_completion.terminal !== terminalInput
      || terminal.sequence <= input.sequence || !terminal.successor_basis || terminal.successor_missing_reason != null
      || !value || !sameWorld(value) || !same(surfaceOf(value).selected_card_entity_ids, selected))
      return reject('selector_transaction_input_or_world_not_exact');
    const action = value.snapshot.bound_actions.actions.filter(action => action.bound_action_id === input.bound_action?.bound_action_id
      && action.verb === verb && (action.subject_referent_id ?? null) === (input.bound_action.subject_referent_id ?? null)
      && same(action.arguments, input.bound_action.arguments));
    if (action.length !== 1) return reject('selector_transaction_input_not_in_own_catalog');
    if (terminalInput) {
      if (selected.length !== 1) return reject('selector_transaction_final_set_invalid');
    } else {
      if (selected.length === 0) anchor = input; // real returned empty boundary wins
      const subject = action[0].subject_referent_id;
      if (verb === 'select') selected = [subject];
      else if (selected.length === 1 && selected[0] === subject) selected = [];
      else return reject('selector_transaction_deselection_not_exact');
      const after = basis(terminal.successor_basis);
      if (!sameWorld(after) || !same(surfaceOf(after).selected_card_entity_ids, selected))
        return reject('selector_transaction_edit_successor_changed');
      lastEdit = terminal;
    }
  }
  const originalSpan = events.filter(event => event.sequence >= row.sequence && event.sequence <= end.sequence);
  const inputIds = new Set(inputs.map(input => input.decision_id));
  for (const event of originalSpan) {
    if (event.session_id !== row.session_id || event.timeline_id !== row.timeline_id || event.run_id !== row.run_id
      || event.kind === 'issue' || event.kind === 'execution'
      || event.kind === 'decision_accepted' && !inputIds.has(event.decision_id)
      || event.kind === 'lifecycle' && !inputIds.has(event.decision_id)
        && ['finished', 'cancelled', 'aborted', 'unknown'].includes(event.phase))
      return reject('selector_transaction_interleaved_native_effect_or_issue');
    const reference = event.basis ?? event.successor_basis;
    if (reference && event.sequence < end.sequence && !sameWorld(basis(reference)))
      return reject('selector_transaction_intermediate_information_changed');
    if (event.kind === 'observation' && !reference && event.basis_missing_reason !== 'native_diagnostic_not_decision_boundary')
      return reject('selector_transaction_intermediate_information_missing');
  }
  const after = basis(end.successor_basis), afterReduction = core.reduce(state, after);
  // A complete native post-confirmation PB can honestly be settling. It is
  // retained as such; the converter still requires its real later complete P1
  // successor and never turns this settling PB into a model decision.
  const settlingOnly = after.snapshot.status === 'settling'
    && afterReduction.missing.every(reason => reason === `scene:${after.snapshot.interaction.kind}:settling`);
  if (!complete(after) || !(afterReduction.complete || settlingOnly) || surfaceOf(after).kind === initialSurface.kind)
    return reject('selector_transaction_commit_successor_not_complete');
  const anchorBasis = basis(anchor.basis), reduction = core.reduce(state, anchorBasis);
  const finalSubject = selected[0];
  const choices = reduction.observation.catalog.filter(choice => {
    const lowering = reduction.lowering[choice.choice_id];
    return lowering?.first?.verb === 'select' && anchorBasis.snapshot.bound_actions.actions.some(action =>
      action.bound_action_id === lowering.first.bound_action_id && action.subject_referent_id === finalSubject);
  });
  if (!reduction.complete || choices.length !== 1) return reject('selector_transaction_final_choice_not_unique_initial_legal');
  const tail = inputs.filter(input => input.sequence >= anchor.sequence && input !== confirm);
  const synthetic = tail.length > 1;
  const context = inputs.filter(input => input !== confirm && (synthetic || input.sequence < anchor.sequence));
  const receipt = { projection_version: selectorTransactionProjection, kind: 'native_manual_single_hand_selection',
    data_class: 'D', binding, request_binding: 'uninterrupted_native_factory_await_until_exact_terminal_callback',
    source_event_range: { from_sequence: row.sequence, through_sequence: end.sequence },
    decision_ids: inputs.map(input => input.decision_id), context_decision_ids: context.map(input => input.decision_id),
    initial_event_id: row.event_id, initial_public_basis: row.basis,
    anchor_event_id: anchor.event_id, anchor_public_basis: anchor.basis, anchor_decision_id: anchor.decision_id,
    confirmation_event_id: confirm.event_id, confirmation_decision_id: confirm.decision_id,
    confirmation_public_basis: confirm.basis, committed_successor_event_id: end.event_id,
    committed_successor_public_basis: end.successor_basis, final_subject_referent_id: finalSubject,
    supervision: synthetic ? 'finalized_choice_at_recorded_empty_basis' : 'actual_reselection_after_equal_net_zero_prefix',
    zero_label_prefix: inputs.filter(input => input.sequence < anchor.sequence).map(input => input.decision_id),
    original_click_timing_claim: false, online_replace_or_deselect: false,
    checks: { complete_initial_and_all_intermediate_originals: true, unchanged_public_world_and_information: true,
      exact_native_selection_transitions: true, same_owner_parent_and_uninterrupted_request: true,
      final_choice_in_anchor_catalog: true, exact_confirmation_and_real_complete_successor: true } };
  return { qualified: true, receipt, contextIds: new Set(receipt.context_decision_ids),
    anchor, confirm, end, lastEdit, synthetic, choice: choices[0], reduction };
}
