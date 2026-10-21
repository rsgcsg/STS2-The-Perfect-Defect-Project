import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

/** Synthetic, codec-complete fixture; contains no Human data or game execution. */
export function nativeHandTransaction(operations = [['select', 0], ['select', 1], ['confirm']]) {
  const combat = JSON.parse(readFileSync(new URL('../../../components/connector/sdk/typescript/test/fixtures/'
    + 'p1-global/synthetic-complete-combat.json', import.meta.url), 'utf8'));
  const cards = combat.snapshot.interaction.content.context.player.hand.slice(0, 3).map(card => {
    const { target_type: _target, can_play: _canPlay, ...value } = card; return value;
  });
  for (const card of cards) for (const display of ['hand', 'surface:combat_hand_card_selection']) {
    const id = `read:${display}:${card.entity_id}`, metadata = { read_id: id, kind: 'surface_card',
      target_referent_id: card.entity_id, content_schema: 'sts2.player-environment/read/surface_card-1',
      visibility_basis: 'normal_player_visible_surface_card', ordering_semantics: 'single_entity' };
    combat.snapshot.reads.push({ ...structuredClone(combat.snapshot.reads[0]), ...metadata, hidden_by_policy: [] });
    combat.reads.push({ read_id: id, kind: 'surface_card', error: null,
      response: { ...structuredClone(combat.reads[0].response), ...metadata,
        content: { ...card, public_details_revision: '1', display_context: display, parent_read_id: null,
          display_owner_entity_id: null, keywords: [], card_previews: [] },
        completeness: { status: 'complete', visible_information: 'synthetic_card_detail', interaction_discovery: 'read_only',
          missing: [], hidden_by_policy: [] } } });
  }
  const events = [], blobs = new Map();
  const methods = { select: 'MegaCrit.Sts2.Core.Nodes.Combat.NPlayerHand.SelectCardInSimpleMode',
    deselect: 'MegaCrit.Sts2.Core.Nodes.Combat.NSelectedHandCardContainer.DeselectHolder',
    confirm: 'MegaCrit.Sts2.Core.Nodes.Combat.NPlayerHand.SelectModeConfirmButton.Released' };
  function retain(value) {
    const v = { schema: 'sts2.player-environment/public-basis-1', revision: '1',
      capture_scope: 'current_public_snapshot_and_advertised_read_instances', capture_status: 'complete', missing: [],
      ...structuredClone(value) };
    const bytes = Buffer.from(JSON.stringify(v)), sha256 = createHash('sha256').update(bytes).digest('hex');
    const payload_ref = `public-bases/sha256/${sha256.slice(0, 2)}/${sha256}.json`;
    blobs.set(payload_ref, bytes);
    return { snapshot_id: v.snapshot.snapshot_id, sha256, byte_count: bytes.length, payload_ref };
  }
  function basis(selected = [], suffix = events.length) {
    const value = structuredClone(combat), snap = value.snapshot;
    snap.snapshot_id = `hand-fixture-${suffix}`; snap.sequence = suffix;
    value.reads.forEach(read => read.response.expected_snapshot_id = read.response.observed_snapshot_id = snap.snapshot_id);
    for (const read of value.reads) if (read.kind === 'surface_card'
      && read.response.content.display_context === 'surface:combat_hand_card_selection')
      read.response.content.is_selected = selected.includes(read.response.target_referent_id);
    const selectedCards = cards.map(card => ({ ...card, is_selected: selected.includes(card.entity_id) }));
    Object.assign(snap.interaction, { interaction_id: 'hand-owner', kind: 'combat_hand_card_selection', stage: 'ready',
      content_schema: 'sts2.player-environment/surface/combat_hand_card_selection-1',
      content: { context: snap.interaction.content.context, surface: { kind: 'combat_hand_card_selection',
        prompt: 'Choose a card to Exhaust.', selection_mode: 'simple_select', min_select: 1, max_select: 1,
        require_manual_confirmation: true, selected_count: selected.length, selected_card_entity_ids: [...selected],
        selectable_card_entity_ids: cards.filter(card => !selected.includes(card.entity_id)).map(card => card.entity_id),
        deselectable_card_entity_ids: [...selected], can_confirm: selected.length === 1,
        can_close_peek: false, is_peeking: false,
        cards: [...selectedCards.filter(c => !c.is_selected), ...selectedCards.filter(c => c.is_selected)] } },
      capabilities: [{ verb: 'select', subject_role: 'card', arguments: [], availability_basis: 'current_native_interaction' },
        ...(selected.length ? [{ verb: 'confirm', arguments: [], availability_basis: 'current_native_interaction' },
          { verb: 'deselect', subject_role: 'card', arguments: [], availability_basis: 'current_native_interaction' }] : [])] });
    snap.referents = selectedCards.map(card => ({ referent_id: card.entity_id, role: 'card', kind: 'entity', label: card.name,
      state: { visible: true, selected: card.is_selected, observation_basis: 'native_visible_fact' },
      properties_schema: 'sts2.player-environment/referent/card-1', properties: card }));
    snap.bound_actions.actions = [
      ...selectedCards.map(card => ({ bound_action_id: `${card.is_selected ? 'deselect' : 'select'}-${card.entity_id}`,
        verb: card.is_selected ? 'deselect' : 'select', subject_referent_id: card.entity_id, interaction_id: 'hand-owner',
        arguments: [], label: `${card.is_selected ? 'Deselect' : selected.length ? 'Replace current selection with' : 'Select'} ${card.name}` })),
      ...(selected.length ? [{ bound_action_id: 'confirm-hand', verb: 'confirm', subject_referent_id: null,
        interaction_id: 'hand-owner', arguments: [], label: 'Confirm selected cards' }] : [])];
    snap.bound_actions.total_count = snap.bound_actions.materialized_count = snap.bound_actions.actions.length;
    return value;
  }
  function row(kind, fields) {
    const sequence = events.length + 1;
    const event = { schema: 'sts2.human-annotator/p1-native-event-1', sequence, session_id: 'test-session',
      timeline_id: 'test-timeline', run_id: 'run-1', event_id: `event-${sequence}`, kind, ...fields };
    events.push(event); return event;
  }
  // A real complete nonselector boundary permits the existing segmenter to start.
  const before = retain(combat), prior = row('decision_accepted', { decision_id: 'prior', action_witness_id: 'prior-action',
    native_action_type: 'EndPlayerTurnAction', native_mechanism: 'game_action', basis: before,
    native_receipt: { kind: 'game_action_accepted', carrier_ref: 'prior-action' },
    bound_action: combat.snapshot.bound_actions.actions.find(action => action.verb === 'end_turn') });
  row('lifecycle', { decision_id: prior.decision_id, action_witness_id: prior.action_witness_id,
    phase: 'finished', successor_basis: retain(basis()) });
  let selected = [];
  for (const [verb, index] of operations) {
    const value = basis(selected), ref = retain(value), subject = verb === 'confirm' ? null : cards[index].entity_id;
    const action = value.snapshot.bound_actions.actions.find(action => action.verb === verb && action.subject_referent_id === subject);
    const id = `hand-${events.length + 1}`;
    const accepted = row('decision_accepted', { decision_id: id, action_witness_id: `witness-${id}`,
      native_action_type: methods[verb], native_mechanism: 'native_selector_input', basis: ref, bound_action: action,
      native_witness: { selector_owner_witness_id: 'selector-owner', selected_subject_witness_id: subject,
        parent_action_witness_id: 'native-parent', parent_owner_witness_id: 'native-parent',
        factory_mechanism: 'MegaCrit.Sts2.Core.Nodes.Combat.NPlayerHand.SelectCards', native_origin: null },
      native_receipt: { kind: 'semantic_ui_accepted', carrier_ref: `witness-${id}`,
        selector_callback: { native_callback: methods[verb], terminal: verb === 'confirm', native_acceptance_proven: true } },
      missing_reason: null });
    if (verb === 'select') selected = [subject]; else if (verb === 'deselect') selected = [];
    const after = verb === 'confirm' ? structuredClone(combat) : basis(selected);
    row('lifecycle', { decision_id: id, action_witness_id: accepted.action_witness_id, phase: 'finished',
      native_completion: { native_callback: methods[verb], terminal: verb === 'confirm' },
      successor_basis: retain(after), successor_missing_reason: null });
    if (verb === 'confirm') selected = [];
  }
  return { cards, events, blobs, retain, basis, row, source: { bundle: '/synthetic-hand-transaction', manifest: {
    schema: 'sts2.human-annotator/p1-native-session-bundle-1', bundle_content_id: 'synthetic-hand',
    content_identity: { session_id: 'test-session', declared_origin: 'agent_native_ui', human_origin_attestation: { attested: false } } },
    events, journal: [], bytes: path => blobs.get(path) } };
}
