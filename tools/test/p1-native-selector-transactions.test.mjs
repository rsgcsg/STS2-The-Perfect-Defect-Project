import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as sdk from '../../components/connector/sdk/typescript/dist/index.js';
import * as core from '../../components/connector/sdk/typescript/dist/p1Global/index.js';
import { convertNativeSemanticOriginals } from '../p1-native-to-p1.mjs';
import { selectorTransactionProjection } from '../p1-native-selector-transactions.mjs';
import { nativeHandTransaction } from './fixtures/native-hand-transaction.mjs';
const options = { includeSegments: true, selectorTransactions: selectorTransactionProjection };
const convert = fixture => convertNativeSemanticOriginals(fixture.source, sdk, core, options);

test('replacement preserves native first click and creates a separate legal finalized D choice', () => {
  const f = nativeHandTransaction(), originals = JSON.stringify(f.events), c = convert(f);
  assert.equal(c.complete, true, JSON.stringify(c.selector_transaction_candidates));
  assert.equal(c.selector_transactions.length, 1);
  assert.equal(c.derived_transaction_choices.length, 1);
  const d = c.derived_transaction_choices[0], raw = c.actions.find(action => action.decision_id === 'hand-3');
  assert.equal(raw.match_status, 'transaction_context'); assert.equal(raw.choice_id, null);
  assert.equal(f.events[2].bound_action.subject_referent_id, f.cards[0].entity_id);
  assert.equal(d.selector_transaction.final_subject_referent_id, f.cards[1].entity_id);
  assert.equal(d.selector_transaction.original_click_timing_claim, false);
  assert.equal(c.accepted_accounting.accepted, 4); assert.equal(c.accepted_accounting.selector_transaction_context, 2);
  assert.equal(c.accepted_accounting.derived_transaction_labels, 1); assert.equal(c.accepted_accounting.explained_gaps, 0);
  const points = c.segment_output.segments.flatMap(segment => segment.points);
  const p = points.find(point => point.derived_choice_id === d.decision_id);
  assert.equal(p.source_sequence, 3); assert.equal(p.mask.N, true); assert.equal(p.data_class, 'D');
  assert.ok(p.observation.catalog.some(choice => choice.choice_id === p.chosen));
  assert.equal(p.observation.scene.content.surface.selected_count, 0);
  assert.equal(JSON.stringify(p.observation).includes('selector_transaction'), false);
  assert.equal(points.filter(point => point.mask.N).length, 3); // prior, finalized choose, actual finish
  assert.equal(JSON.stringify(f.events), originals);
  assert.equal(c.projection_claims.lossless_p1_trace, false);
});

test('multi-edit then full reset uses the real last empty boundary, with no synthetic early choice', () => {
  const f = nativeHandTransaction([['select', 0], ['select', 1], ['select', 2], ['deselect', 2], ['select', 1], ['confirm']]);
  const c = convert(f);
  assert.equal(c.complete, true, JSON.stringify(c.selector_transaction_candidates));
  assert.equal(c.derived_transaction_choices.length, 0);
  assert.equal(c.selector_transactions[0].zero_label_prefix.length, 4);
  assert.equal(c.selector_transactions[0].supervision, 'actual_reselection_after_equal_net_zero_prefix');
  const points = c.segment_output.segments.flatMap(segment => segment.points);
  assert.deepEqual(points.filter(p => p.mask.N).map(p => p.source_sequence), [1, 11, 13]);
});

test('more than one replacement folds by final instance, not card name or session ID', () => {
  const f = nativeHandTransaction([['select', 0], ['select', 1], ['select', 2], ['confirm']]);
  const c = convert(f);
  assert.equal(c.complete, true); assert.equal(c.derived_transaction_choices.length, 1);
  assert.equal(c.selector_transactions[0].final_subject_referent_id, f.cards[2].entity_id);
});

test('a terminal confirmation separates repeated requests even with the same hand owner and parent', () => {
  const f = nativeHandTransaction([['select', 0], ['select', 1], ['confirm'], ['select', 2], ['select', 0], ['confirm']]);
  const c = convert(f);
  assert.equal(c.complete, true); assert.equal(c.selector_transactions.length, 2);
  assert.equal(c.derived_transaction_choices.length, 2);
  assert.deepEqual(c.selector_transactions.map(t => t.final_subject_referent_id), [f.cards[1].entity_id, f.cards[0].entity_id]);
  assert.ok(c.selector_transactions[0].source_event_range.through_sequence < c.selector_transactions[1].source_event_range.from_sequence);
});

test('normal choose and confirmation retain the previous exact interpretation', () => {
  const f = nativeHandTransaction([['select', 1], ['confirm']]);
  const old = convertNativeSemanticOriginals(f.source, sdk, core, { includeSegments: true }), c = convert(f);
  assert.equal(c.complete, true); assert.deepEqual(c.selector_transactions, []);
  assert.deepEqual(c.segment_output.segments, old.segment_output.segments);
});

test('old default still isolates replacements and never silently adopts a new projection', () => {
  const f = nativeHandTransaction(), c = convertNativeSemanticOriginals(f.source, sdk, core, { includeSegments: true });
  assert.equal(c.complete, false); assert.equal(c.derived_transaction_choices.length, 0);
  assert.ok(c.blocking_issues.some(issue => issue.reason === 'accepted_action_has_no_p1_choice'));
  assert.throws(() => convertNativeSemanticOriginals(f.source, sdk, core, { selectorTransactions: 'latest' }), /projection_unknown/);
});

test('changed world, required new information, and incomplete initial information never become D labels', () => {
  for (const change of ['world', 'read', 'initial', 'other_display', 'instruction', 'catalog_metadata']) {
    const f = nativeHandTransaction(), event = f.events[change === 'initial' ? 2 : 4];
    const b = JSON.parse(f.source.bytes(event.basis.payload_ref));
    if (change === 'world') b.snapshot.persistent.content.player.gold++;
    if (change === 'read') b.reads[0].response.content.extra_visible_fact = 'new fact after selecting';
    if (change === 'other_display') b.reads.find(read => read.kind === 'surface_card'
      && read.response.content.display_context === 'hand').response.content.is_selected = true;
    if (change === 'instruction') b.snapshot.bound_actions.actions[0].label = 'Select with a different game effect';
    if (change === 'catalog_metadata') b.snapshot.bound_actions.ordering_semantics = 'different_catalog_contract';
    if (change === 'initial') {
      b.capture_status = 'partial'; b.missing = ['snapshot:partial'];
      b.snapshot.completeness.status = 'partial'; b.snapshot.completeness.missing = ['synthetic_information_missing'];
    }
    event.basis = f.retain(b);
    const c = convert(f);
    assert.equal(c.derived_transaction_choices.length, 0, change); assert.equal(c.complete, false, change);
    assert.ok(c.blocking_issues.length > 0, change);
  }
});

test('different owner, missing confirmation and unknown completion do not bind a transaction', () => {
  for (const change of ['owner', 'parent', 'unconfirmed', 'unknown']) {
    const f = nativeHandTransaction();
    if (change === 'owner') f.events[4].native_witness.selector_owner_witness_id = 'another-owner';
    if (change === 'parent') f.events[4].native_witness.parent_action_witness_id = 'another-parent';
    if (change === 'unconfirmed') f.source.events = f.events.slice(0, -2);
    if (change === 'unknown') f.events.at(-1).phase = 'unknown';
    const c = convert(f);
    assert.equal(c.derived_transaction_choices.length, 0, change); assert.equal(c.complete, false, change);
  }
});

test('unknown / missing intermediate original and multi-select mechanism stay excluded', () => {
  for (const change of ['intermediate', 'multiselect', 'callback']) {
    const f = nativeHandTransaction();
    if (change === 'intermediate') f.events[3].successor_basis = null;
    if (change === 'callback') f.events[4].native_receipt.selector_callback.native_acceptance_proven = false;
    if (change === 'multiselect') {
      const e = f.events[2], b = JSON.parse(f.source.bytes(e.basis.payload_ref));
      b.snapshot.interaction.content.surface.max_select = 3; e.basis = f.retain(b);
    }
    const c = convert(f); assert.equal(c.derived_transaction_choices.length, 0, change);
    assert.equal(c.complete, false, change);
  }
});

test('a real settling confirmation successor is retained, never promoted into a fabricated decision', () => {
  const f = nativeHandTransaction(), last = f.events.at(-1), b = JSON.parse(f.source.bytes(last.successor_basis.payload_ref));
  b.snapshot.status = 'settling'; b.snapshot.interaction.stage = 'settling';
  b.snapshot.bound_actions.actions = []; b.snapshot.bound_actions.total_count = b.snapshot.bound_actions.materialized_count = 0;
  b.snapshot.interaction.content.surface.can_end_turn = false; b.snapshot.interaction.content.surface.playable_cards = [];
  last.successor_basis = f.retain(b);
  const c = convert(f); assert.equal(c.selector_transactions.length, 1);
  assert.equal(c.complete, false); // exact converter successor requirement is still in force
  assert.ok(c.blocking_issues.some(issue => issue.reason === 'accepted_action_without_complete_successor'));
  assert.ok(c.captures.some(capture => capture.origin === 'native_lifecycle_finished' && !capture.complete));
});
