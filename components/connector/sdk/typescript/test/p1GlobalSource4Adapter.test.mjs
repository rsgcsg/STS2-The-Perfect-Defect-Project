import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import * as core from '../src/p1Global/index.js';
import * as sdk from '../src/index.js';
import { convertOriginals } from '../../../../../tools/source4-to-p1.mjs';
import { selectorForwardPair, selectorReversalPair } from './fixtures/selector-forward.mjs';
import { sourceCardDragPair } from './fixtures/source-card-drag.mjs';

// Synthetic in-memory semantic inputs only. These do not claim Source bundle
// verifier acceptance, native input origin, a SceneTree receipt or P1 coverage.
const original = () => JSON.parse(readFileSync(new URL('./fixtures/p1-global/synthetic-complete-combat.json', import.meta.url), 'utf8'));
const hash = value => createHash('sha256').update(value).digest('hex');
function renamed(basis, suffix) {
  const next = structuredClone(basis); next.snapshot.snapshot_id += suffix;
  for (const read of next.reads) if (read.response)
    read.response.expected_snapshot_id = read.response.observed_snapshot_id = next.snapshot.snapshot_id;
  return next;
}
const selected = (action, verb = action.verb) => ({ action_id: 'native-catalog-other-id', kind: 'native_input', verb,
  label: 'Native fixture', subject_referent_id: action.subject_referent_id ?? null,
  arguments: structuredClone(action.arguments), effect_domain: 'native' });
const acceptedCarrier = () => [{ kind: 'game_action_submitted', carrier_ref: 'exact-action' },
  { kind: 'game_action_accepted', carrier_ref: 'exact-action' }];
function fixture() {
  const blobs = new Map();
  const position = index => ({ epoch_id: 'epoch', stream_generation: '1', publication_index: String(index) });
  function parts(basis, stage = null) {
    const bytes = Buffer.from(JSON.stringify({ schema: 'sts2.player-environment/public-basis-1', revision: '1',
      capture_scope: 'current_public_snapshot_and_advertised_read_instances', capture_status: 'complete',
      snapshot: basis.snapshot, reads: basis.reads, missing: [] }));
    const digest = hash(bytes); const path = `public-bases/sha256/${digest.slice(0, 2)}/${digest}.bin`;
    blobs.set(path, bytes);
    return { public_basis: { epoch_id: 'epoch', scope_id: 'scope', stream_generation: '1', snapshot_id: basis.snapshot.snapshot_id,
      captured_at: basis.snapshot.observed_at, byte_count: bytes.length, sha256: digest, payload_ref: path },
      public_basis_missing_reason: null, native_stage: stage };
  }
  const identity = { session_id: 'source-session', timeline_id: 'timeline', epoch_id: 'epoch', segment_id: 'segment' };
  const source = { bundle: 'synthetic-semantic-input', receipt: { content_id: 'synthetic-only', manifest: { session_id: 'source-session' } },
    epochs: [{ epoch_id: 'epoch' }], observations: [], inputs: [], boundaries: [], bytes: path => blobs.get(path) };
  function input(basis, index, action, facts, mechanism, delivery = 'delivered', reason = null) {
    const row = { ...identity, input_id: `input-${index}`, input_prefix_ordinal: String(index), pre_position: position(index),
      captured_parts: parts(basis), basis_order: { status: 'native_prefix_frozen', reason_code: null },
      outcome: { mapping_status: 'exact', match_count: 1, selected_action: action, native_mechanism: mechanism,
        delivery, reason_code: reason, stages: [], native_facts: { origin: 'native_input', missing_reason: null, ...facts } } };
    source.inputs.push(row); return row;
  }
  function publication(basis, index, stage = null) {
    const row = { ...identity, position: position(index), source_seam: stage ? 'native_input_callback' : 'native_owner_ready',
      source_index: String(index), phase: stage ? 'card_play_canceled' : 'observation', gap_after_index: null,
      captured_parts: parts(basis, stage) }; source.observations.push(row); return row;
  }
  return { source, input, publication };
}
const convert = source => convertOriginals(source, sdk, core, { hostKind: 'reference' });
function canceled() {
  const f = fixture(), basis = original(), held = renamed(basis, '-held'), after = renamed(basis, '-canceled');
  const action = basis.snapshot.bound_actions.actions.find(value => value.verb === 'play' && value.arguments.length);
  f.publication(basis, 1);
  f.input(basis, 1, { action_id: 'native-start', kind: 'native_input', verb: 'begin_card_play', label: 'Start',
    subject_referent_id: action.subject_referent_id, arguments: [], effect_domain: 'native' },
  { owner_ref: 'exact-hand', subject_ref: action.subject_referent_id, arguments: { holder: 'exact-holder' },
    carriers: [{ kind: 'card_play_created', carrier_ref: 'exact-play' }] }, 'NPlayerHand.StartCardPlay');
  f.publication(held, 2);
  f.input(held, 2, selected(action, 'confirm_target'), { owner_ref: 'exact-play', subject_ref: action.subject_referent_id,
    arguments: Object.fromEntries(action.arguments.map(value => [value.role, value.referent_id])),
    carriers: [{ kind: 'card_play_canceled', carrier_ref: 'exact-play' }] }, 'NCardPlay.TryPlayCard',
  'unknown', 'native_source_scope_ended_without_acceptance');
  const stage = { kind: 'card_play_canceled', owner_ref: 'exact-play', subject_ref: action.subject_referent_id,
    arguments: { hand: 'exact-hand' }, evidence: 'native_finished_false', no_submission: true };
  f.publication(after, 3, stage);
  return { ...f, basis, after, stage };
}

describe('Source4 adapter using the actual shared SDK matcher', () => {
  it.each(['valid', 'following-endpoint', 'settling-only', 'changed-world', 'changed-held-read',
    'foreign-ui', 'no-submission-unknown', 'paused', 'interleaved-input'])(
    'settles only a proved original single-Start cancellation and complete restoration: %s', change => {
      const f = fixture(), { before, held, action } = sourceCardDragPair();
      const after = renamed(before, '-restored');
      f.publication(before, 1);
      f.input(before, 1, selected(action, 'begin_card_play'), { owner_ref: 'original-hand',
        subject_ref: action.subject_referent_id, arguments: { holder: 'original-holder' },
        carriers: [{ kind: 'card_play_created', carrier_ref: 'original-created-ui' }] }, 'NPlayerHand.StartCardPlay');
      if (change === 'changed-held-read') held.reads.find(r => r.kind === 'surface_card').response.content.keywords[0].description += ' changed';
      if (change === 'changed-world') held.snapshot.persistent.content.player.gold++;
      f.publication(held, 2);
      if (change === 'interleaved-input') f.input(held, 2, selected(action, 'begin_card_play'), {
        owner_ref: 'original-hand', subject_ref: action.subject_referent_id, arguments: { holder: 'other-holder' },
        carriers: [{ kind: 'card_play_created', carrier_ref: 'other-ui' }] }, 'NPlayerHand.StartCardPlay');
      const stage = { kind: 'card_play_canceled', owner_ref: change === 'foreign-ui' ? 'other-ui' : 'original-created-ui',
        subject_ref: action.subject_referent_id, arguments: { hand: 'original-hand' },
        evidence: 'native_finished_false', no_submission: change === 'no-submission-unknown' ? null : true };
      f.publication(['following-endpoint', 'settling-only'].includes(change) ? held : after, 3, stage);
      if (change === 'paused') f.source.boundaries.push({ session_id: 'source-session', timeline_id: 'timeline',
        paused_intervals: [{ epoch_id: 'epoch', stream_generation: '1', after_index: '1', through_index: '3',
          after_input_ordinal: '1', through_input_ordinal: '1', reason: 'recording_paused' }] });
      if (change === 'following-endpoint') {
        const end = after.snapshot.bound_actions.actions.find(a => a.verb === 'end_turn');
        const input = f.input(after, 2, selected(end), { owner_ref: 'end-button', subject_ref: null,
          arguments: {}, carriers: acceptedCarrier() }, 'native_end_turn_ui');
        input.pre_position.publication_index = '4';
      }
      const raw = JSON.stringify(f.source.inputs), report = convert(f.source);
      if (['valid', 'following-endpoint'].includes(change)) {
        expect(report.complete, JSON.stringify(report.unresolved_inputs)).toBe(true);
        expect(report.context_spans).toHaveLength(1);
        expect(report.context_spans[0].source_input_ids).toEqual(['input-1']);
        expect(report.actions.map(a => a.choice.kind)).toEqual(change === 'following-endpoint' ? ['combat.end_turn'] : []);
      } else {
        expect(report.complete).toBe(false); expect(report.context_spans).toEqual([]); expect(report.actions).toEqual([]);
        expect(report.unresolved_inputs[0].input_id).toBe('input-1');
      }
      expect(JSON.stringify(report.native_trace)).toBe(raw);
      expect(report.native_trace[0].outcome.delivery).toBe('delivered');
    });
  it.each(['valid', 'changed-held-read', 'foreign-ui'])(
    'matches the original full pre-Begin Play and consumed physical submission: %s', change => {
      const f = fixture(), { before, held, action } = sourceCardDragPair();
      f.publication(before, 1);
      f.input(before, 1, selected(action, 'begin_card_play'), { owner_ref: 'original-hand',
        subject_ref: action.subject_referent_id, arguments: { holder: 'original-holder' },
        carriers: [{ kind: 'card_play_created', carrier_ref: 'original-created-ui' }] }, 'NPlayerHand.StartCardPlay');
      if (change === 'changed-held-read') held.reads.find(r => r.kind === 'surface_card').response.content.keywords[0].description += ' changed';
      f.publication(held, 2);
      const submit = f.input(held, 2, null, { owner_ref: change === 'foreign-ui' ? 'other-ui' : 'original-created-ui',
        subject_ref: action.subject_referent_id, arguments: {}, carriers: acceptedCarrier() }, 'NCardPlay.TryPlayCard');
      Object.assign(submit.outcome, { mapping_status: 'unmapped', match_count: 0 });
      const raw = JSON.stringify(f.source.inputs), report = convert(f.source);
      if (change === 'valid') {
        expect(report.complete, JSON.stringify(report.unresolved_inputs)).toBe(true);
        expect(report.actions).toHaveLength(1); expect(report.actions[0].choice.kind).toBe('combat.play_card');
        expect(report.actions[0].source_input_ids).toEqual(['input-1', 'input-2']);
      } else {
        expect(report.complete).toBe(false); expect(report.actions).toEqual([]);
        expect(report.unresolved_inputs.map(r => r.input_id)).toEqual(['input-1', 'input-2']);
      }
      expect(JSON.stringify(report.native_trace)).toBe(raw);
      expect(report.native_trace[1].outcome.selected_action).toBe(null);
      expect(report.native_trace[1].outcome.mapping_status).toBe('unmapped');
    });
  it.each(['valid', 'publication-return', 'missing-return', 'foreign-owner', 'public-world', 'unknown', 'missing-read'])(
    'defers select labels until the original adjacent reversal span is classified: %s', change => {
      const f = fixture(), { outer, selected: held, restored } = selectorReversalPair();
      const select = outer.snapshot.bound_actions.actions[0], deselect = held.snapshot.bound_actions.actions[0];
      const facts = () => ({ owner_ref: 'owner-one', subject_ref: select.subject_referent_id, arguments: {}, carriers: [] });
      const mechanism = 'MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NDeckCardSelectScreen.OnCardClicked';
      f.input(outer, 1, selected(select), facts(), mechanism);
      const second = f.input(held, 2, selected(deselect), facts(), mechanism);
      if (change === 'foreign-owner') second.outcome.native_facts.owner_ref = 'different-owner';
      if (change === 'unknown') second.outcome.delivery = 'unknown';
      if (change === 'public-world') restored.snapshot.persistent.content.player.gold++;
      if (change === 'missing-read') restored.reads.pop();
      if (change === 'publication-return') f.publication(restored, 3);
      else if (change !== 'missing-return') f.input(restored, 3, selected(select), facts(), mechanism);
      const raw = JSON.stringify(f.source.inputs), report = convert(f.source);
      if (['valid', 'publication-return'].includes(change)) {
        expect(report.context_spans[0]?.source_input_ids, JSON.stringify(report.unresolved_inputs)).toEqual(['input-1', 'input-2']);
        expect(report.actions).toHaveLength(change === 'valid' ? 1 : 0);
        if (change === 'valid') expect(report.actions[0].source_input_ids).toEqual(['input-3']);
        expect(report.complete).toBe(true);
      } else {
        expect(report.context_spans).toEqual([]); expect(report.complete).toBe(false);
        expect(report.unresolved_inputs.map(row => row.input_id)).toContain('input-1');
        expect(report.unresolved_inputs.map(row => row.input_id)).toContain('input-2');
      }
      expect(report.actions.some(action => action.source_input_ids.includes('input-1'))).toBe(false);
      expect(JSON.stringify(report.native_trace)).toBe(raw);
    });
  it('classifies the original select and preview confirmation as the offered folded selector intention', () => {
    const f = fixture(); const { outer, preview } = selectorForwardPair();
    const select = outer.snapshot.bound_actions.actions.find(action => action.verb === 'select');
    const confirm = preview.snapshot.bound_actions.actions.find(action => action.verb === 'confirm');
    f.publication(outer, 1);
    f.input(outer, 1, selected(select), { owner_ref: 'owner-one', subject_ref: select.subject_referent_id,
      arguments: {}, carriers: [] }, 'MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NDeckUpgradeSelectScreen.OnCardClicked');
    f.publication(preview, 2);
    f.input(preview, 2, selected(confirm), { owner_ref: 'owner-one', subject_ref: confirm.subject_referent_id,
      arguments: {}, carriers: [] }, 'MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NDeckUpgradeSelectScreen.Confirm.Released');
    const originalBytes = JSON.stringify(f.source.inputs), report = convert(f.source);
    expect(report.actions.map(action => action.choice.kind), JSON.stringify(report.unresolved_inputs)).toEqual(['upgrade.choose']);
    expect(report.actions[0].source_input_ids).toEqual(['input-1', 'input-2']);
    expect(report.unresolved_inputs).toEqual([]); expect(report.complete).toBe(true);
    expect(JSON.stringify(f.source.inputs)).toBe(originalBytes);
  });
  it('does not consume a foreign native confirmation into the pending selector intention', () => {
    const f = fixture(); const { outer, preview } = selectorForwardPair();
    const select = outer.snapshot.bound_actions.actions.find(action => action.verb === 'select');
    const confirm = preview.snapshot.bound_actions.actions.find(action => action.verb === 'confirm');
    // This is a different actual offered native control, not the typed current
    // selector confirm. The original input remains separately visible.
    confirm.subject_referent_id = 'foreign-control';
    preview.snapshot.referents.push({ ...preview.snapshot.referents.find(ref => ref.referent_id === 'confirm'),
      referent_id: 'foreign-control' });
    f.input(outer, 1, selected(select), { owner_ref: 'owner-one', subject_ref: select.subject_referent_id,
      arguments: {}, carriers: [] }, 'MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NDeckUpgradeSelectScreen.OnCardClicked');
    f.input(preview, 2, selected(confirm), { owner_ref: 'owner-one', subject_ref: 'foreign-control',
      arguments: {}, carriers: [] }, 'MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NDeckUpgradeSelectScreen.Other.Released');
    const originalBytes = JSON.stringify(f.source.inputs), report = convert(f.source);
    expect(report.actions.some(action => action.choice.kind === 'upgrade.choose')).toBe(false);
    expect(report.unresolved_inputs.map(input => input.input_id)).toContain('input-1');
    expect(report.complete).toBe(false); expect(JSON.stringify(report.native_trace)).toBe(originalBytes);
  });
  it('binds candidates to the input bytes when an earlier publication has the same snapshot ID', () => {
    const f = fixture(), basis = original(), earlier = structuredClone(basis);
    earlier.snapshot.observed_at = '1970-01-01T00:00:00.001+00:00'; f.publication(earlier, 1);
    const action = basis.snapshot.bound_actions.actions.find(value => value.verb === 'end_turn');
    f.input(basis, 1, selected(action), { owner_ref: 'end-button', subject_ref: null, arguments: {}, carriers: acceptedCarrier() }, 'native_end_turn_ui');
    const raw = JSON.stringify(f.source.inputs), report = convert(f.source);
    expect(report.actions).toHaveLength(1); expect(report.actions[0].choice.kind).toBe('combat.end_turn');
    expect(report.unresolved_inputs).toEqual([]); expect(JSON.stringify(f.source.inputs)).toBe(raw);
    // The shared publication classifier retains the bootstrap as an observation.
    expect(report.complete).toBe(true); expect(report.unresolved_contexts).toEqual([]);
    expect(report.captures[0].classification).toBe('p1_initial_boundary');
  });
  it('settles a proven cancel on its publication before the following native input, without a play or commitment', () => {
    const f = canceled(); const end = f.after.snapshot.bound_actions.actions.find(value => value.verb === 'end_turn');
    f.input(f.after, 3, selected(end), { owner_ref: 'end-button', subject_ref: null, arguments: {}, carriers: acceptedCarrier() }, 'native_end_turn_ui');
    const raw = JSON.stringify(f.source.inputs), report = convert(f.source);
    expect(report.context_spans).toHaveLength(1); expect(report.context_spans[0].source_input_ids).toEqual(['input-1', 'input-2']);
    expect(report.actions.map(value => value.choice.kind)).toEqual(['combat.end_turn']);
    expect(report.unresolved_inputs).toEqual([]); expect(JSON.stringify(report.native_trace)).toBe(raw);
    expect(report.native_trace[1].outcome.delivery).toBe('unknown');
  });
  it.each(['no-submission-unknown', 'foreign-carrier', 'paused-gap'])('retains unresolved native input after %s', change => {
    const f = canceled();
    if (change === 'no-submission-unknown') f.stage.no_submission = null;
    if (change === 'foreign-carrier') f.source.inputs[1].outcome.native_facts.owner_ref = 'foreign-play';
    if (change === 'paused-gap') f.source.boundaries.push({ session_id: 'source-session', timeline_id: 'timeline',
      paused_intervals: [{ epoch_id: 'epoch', stream_generation: '1', after_index: '1', through_index: '3',
        after_input_ordinal: '1', through_input_ordinal: '2', reason: 'recording_paused' }] });
    const report = convert(f.source); expect(report.actions).toEqual([]); expect(report.context_spans).toEqual([]);
    expect(report.unresolved_inputs.map(value => value.input_id)).toEqual(['input-1', 'input-2']); expect(report.complete).toBe(false);
  });
  it('does not discard a new public fact after an already settled direct native input', () => {
    const f = fixture(), basis = original(), changed = renamed(basis, '-new-world');
    const action = basis.snapshot.bound_actions.actions.find(value => value.verb === 'end_turn');
    f.input(basis, 1, selected(action), { owner_ref: 'end-button', subject_ref: null, arguments: {}, carriers: acceptedCarrier() }, 'native_end_turn_ui');
    changed.snapshot.persistent.content.player.gold++; f.publication(changed, 2);
    const report = convert(f.source); expect(report.actions).toHaveLength(1);
    expect(report.captures.at(-1).classification).toBe('p1_publication_boundary'); expect(report.complete).toBe(true);
    expect(report.captures.at(-1).observation.player.content.gold).toBe(changed.snapshot.persistent.content.player.gold);
  });
  it('recognizes a fresh producer capture of the same snapshot and all Reads as repeated context', () => {
    const f = fixture(), basis = original(), repeated = structuredClone(basis);
    f.publication(basis, 1);
    repeated.snapshot.observed_at = '2026-10-16T00:01:00+00:00';
    for (const read of repeated.reads) read.response.observed_at = repeated.snapshot.observed_at;
    f.publication(repeated, 2);
    const report = convert(f.source); expect(report.complete).toBe(true);
    expect(report.captures[1].classification).toBe('source_repeated_public_snapshot');
  });
  it('keeps changed Read facts under the same snapshot as a new boundary', () => {
    const f = fixture(), basis = original(), changed = structuredClone(basis);
    f.publication(basis, 1);
    changed.reads.find(read => read.kind === 'run_deck').response.content.cards[0].description += ' changed';
    f.publication(changed, 2);
    const report = convert(f.source);
    expect(report.complete).toBe(true);
    expect(report.captures.map(capture => capture.classification)).toEqual(['p1_initial_boundary', 'p1_publication_boundary']);
    expect(report.captures[1].observation.deck).not.toEqual(report.captures[0].observation.deck);
  });
  it.each(['gap', 'missing-read', 'partial-settling', 'unknown-scene'])('leaves %s publication unresolved with its cause', defect => {
    const f = fixture(), basis = original(); f.publication(basis, 1);
    const next = renamed(basis, '-bad');
    if (defect === 'missing-read') next.reads.pop();
    if (defect === 'partial-settling') {
      next.snapshot.status = 'settling'; next.snapshot.bound_actions.actions = [];
      next.snapshot.bound_actions.total_count = next.snapshot.bound_actions.materialized_count = 0;
      next.snapshot.interaction.capabilities = [];
      next.snapshot.persistent.content.player.hp = null;
    }
    if (defect === 'unknown-scene') next.snapshot.interaction.kind = 'not-qualified';
    const publication = f.publication(next, 2);
    if (defect === 'gap') publication.gap_after_index = '1';
    const report = convert(f.source);
    expect(report.complete).toBe(false); expect(report.captures[1].status).toBe('unresolved');
    expect(report.unresolved_contexts[0].reason).toBeTruthy();
  });
  it('does not declare a complete prefix across pause-excluded publications', () => {
    const f = fixture(), basis = original(); f.publication(basis, 1); f.publication(basis, 3);
    f.source.boundaries.push({ boundary_id: 'pause-boundary', paused_intervals: [{
      epoch_id: 'epoch', stream_generation: '1', after_index: '1', through_index: '2',
      after_input_ordinal: '0', through_input_ordinal: '0' }] });
    const report = convert(f.source); expect(report.complete).toBe(false);
    expect(report.unresolved_contexts[0].reason).toBe('recording_pause_excludes_original_publications');
  });
  it('does not call an empty prefix complete', () => {
    expect(convert(fixture().source).complete).toBe(false);
  });

});
