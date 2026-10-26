import test from 'node:test';
import assert from 'node:assert/strict';
import * as core from '../../components/connector/sdk/typescript/dist/p1Global/index.js';
import { rewardForwardPair } from '../../components/connector/sdk/typescript/test/fixtures/reward-forward.mjs';
import { resolvePresentationInput, replayNativeTrajectory } from '../p1-native-replay.mjs';

function example() {
  const { outer, child } = rewardForwardPair();
  const state = core.createSession('native-replay-test', 'managed');
  const reduced = core.reduce(state, outer);
  const choice = reduced.observation.catalog.find(item => item.kind === 'reward.take_card');
  return { outer, child, state, reduced, witness: { original_terminal_phase: 'finished',
    observation: reduced.observation, reference_choice_id: choice.choice_id,
    bound_action: { verb: 'activate' } } };
}
test('recorded open uses an existing native first step without submitting its P1 intention', () => {
  const { state, reduced, witness } = example();
  const before = JSON.stringify(state);
  const result = resolvePresentationInput(witness, reduced);
  assert.equal(result.verdict, 'equal');
  assert.deepEqual(result.input, { bound_action_id: 'open', verb: 'activate', label: 'open' });
  assert.equal(JSON.stringify(state), before);
  assert.equal(state.reward, null);
  assert.equal(witness.observation.commitments.declined_reward_groups.length, 0);
});
test('native presentation never bypasses changed public facts or an unfinished original', () => {
  const { reduced, witness } = example();
  const changed = structuredClone(reduced);
  changed.observation.scene.content.surface.rewards[0].label += ' changed';
  assert.notEqual(resolvePresentationInput(witness, changed).verdict, 'equal');
  assert.throws(() => resolvePresentationInput({ ...witness, original_terminal_phase: 'unknown' }, reduced), /unfinished/);
  assert.throws(() => resolvePresentationInput({ ...witness, bound_action: { verb: 'skip' } }, reduced), /first_step/);
});
test('unknown native delivery stops without retry and closes its owned driver', async () => {
  const { outer, reduced } = example();
  const choice = reduced.observation.catalog.find(item => item.kind === 'reward.take_card');
  let steps = 0, closed = 0;
  const driver = {
    async start() {}, newMutationId() { return 'mutation-one'; },
    async request(request) {
      if (request.command === 'reset') return {};
      if (request.command === 'episode_identity') return { identity: {} };
      if (request.command === 'capture') return { full: { basis: { ...outer, capture_status: 'complete', missing: [] } } };
      if (request.command === 'step') { steps++; throw new Error('native_delivery_response_lost'); }
      throw new Error('unexpected_command');
    }, async close() { closed++; return { status: 'closed' }; }
  };
  const result = await replayNativeTrajectory({ trajectory: { points: [{ observation: reduced.observation,
    chosen: choice.choice_id }] }, driver, record: { append() {} }, seed: 'fixture', initialProgress: {} });
  assert.equal(result.unknown_delivery, true);
  assert.equal(result.completed, false);
  assert.equal(steps, 1);
  assert.equal(closed, 1);
});

test('diagnostic location retains the full mismatch and never counts it as matching', async () => {
  const { outer, child, reduced } = example();
  const choice = reduced.observation.catalog.find(item => item.kind === 'reward.take_card');
  const reference = structuredClone(reduced.observation);
  reference.scene.content.surface.rewards[0].label += ' Reference difference';
  let steps = 0;
  const driver = {
    async start() {}, newMutationId() { return `diagnostic-${steps}`; },
    async request(request) {
      if (request.command === 'reset') return {};
      if (request.command === 'episode_identity') return { identity: {} };
      if (request.command === 'capture') return { full: { basis: {
        ...(steps === 0 ? outer : child), capture_status: 'complete', missing: [] } } };
      if (request.command === 'step') { steps++; return { receipt: { delivery: 'delivered' } }; }
      throw new Error('unexpected_command');
    }, async close() { return { status: 'closed' }; }
  };
  const result = await replayNativeTrajectory({ trajectory: { points: [{ observation: reference,
    chosen: choice.choice_id }] }, driver, record: { append() {} }, seed: 'fixture', initialProgress: {},
    diagnosticLocator: () => ({ choice_id: choice.choice_id, reason: 'synthetic_unique_locator' }) });
  assert.equal(result.comparison_mode, 'diagnostic_not_equivalence');
  assert.equal(result.completed, false);
  assert.equal(result.matching_points, 0);
  assert.equal(result.matching_choices, 0);
  assert.equal(result.diagnostic_choices, 1);
  assert.equal(result.differences.length, 1);
  assert.equal(result.first_divergence.index, 0);
  assert.equal(result.by_surface.reward_claim.matching, 0);
});

for (const original of ['unknown', 'missing_result']) {
  test(`original ${original} is rejected before any Host start or world offer`, async () => {
    const { reduced } = example();
    const choice = reduced.observation.catalog[0];
    let starts = 0, commands = 0;
    const driver = { async start() { starts++; }, async request() { commands++; }, async close() { return {}; } };
    const point = { observation: reduced.observation, chosen: choice.choice_id };
    if (original === 'unknown') point.result = { schema: 'sts2.p1/result-1', occurrence: 1,
      choice_id: point.chosen, status: 'unknown', steps: [{ index: 0, origin: 'agent_choice',
        native_verb: 'activate', label: 'unknown', delivery: 'unknown', reason_code: 'response_unclassified' }],
      commitment_delta: null };
    const result = await replayNativeTrajectory({ trajectory: { result_source: 'runtime_global_result',
      points: [point, { chosen: null, observation: { status: 'terminal', terminal: { outcome: 'loss' } } }] },
      driver, record: { append() {} }, seed: 'not-offered', initialProgress: {} });
    assert.match(result.error, original === 'unknown' ? /original_unknown_must_not_be_reoffered/ : /original_result_missing_or_unbound/);
    assert.equal(result.original_unknown, original === 'unknown');
    assert.equal(starts, 0);
    assert.equal(commands, 0);
    assert.equal(result.full_p1_equivalence_complete, false);
  });
}

for (const failure of ['capture_incomplete', 'capture_throws', 'required_follow_missing', 'not_delivered', 'unknown', 'response_lost']) {
  test(`records and compares the whole Result prefix after ${failure}`, async () => {
    const { outer, reduced } = example();
    const choice = reduced.observation.catalog.find(item => item.kind === 'reward.take_card');
    const first = reduced.lowering[choice.choice_id].first;
    const delivery = failure === 'not_delivered' ? 'not_delivered'
      : ['unknown', 'response_lost'].includes(failure) ? 'unknown' : 'delivered';
    const expected = { schema: 'sts2.p1/result-1', occurrence: reduced.observation.envelope.occurrence,
      choice_id: choice.choice_id, status: delivery === 'unknown' ? 'unknown'
        : delivery === 'not_delivered' ? 'not_started' : 'interrupted_known',
      steps: [{ index: 0, origin: 'agent_choice', native_verb: first.verb,
        label: first.label, delivery, reason_code: failure === 'response_lost' ? 'response_unclassified' : null }],
      commitment_delta: null };
    let steps = 0, closes = 0;
    const events = [];
    const driver = {
      async start() {}, newMutationId() { return `failure-${failure}`; },
      async request(request) {
        if (request.command === 'reset') return {};
        if (request.command === 'episode_identity') return { identity: {} };
        if (request.command === 'step') {
          steps++;
          if (failure === 'response_lost') throw new Error('response_lost');
          return { receipt: { delivery } };
        }
        if (request.command === 'capture') {
          if (steps && failure === 'capture_throws') throw new Error('capture_failed');
          const partial = steps && failure === 'capture_incomplete';
          return { full: { basis: { ...outer, capture_status: partial ? 'partial' : 'complete',
            missing: partial ? ['original_partial_read'] : [] } } };
        }
        throw new Error('unexpected_command');
      }, async close() { closes++; return { status: 'closed' }; }
    };
    const report = await replayNativeTrajectory({ trajectory: { result_source: 'runtime_global_result', points: [
      { observation: reduced.observation, chosen: choice.choice_id, result: expected },
      { chosen: null, observation: { status: 'terminal', terminal: { outcome: 'loss' } } }
    ] }, driver, record: { append(kind, data) { events.push({ kind, data }); } }, seed: 'fixture', initialProgress: {} });
    // Unknown original records must never be re-offered. For actual unknown
    // execution test a known original instead, retaining the failure unchanged.
    if (delivery === 'unknown') {
      assert.equal(steps, 0);
      const known = structuredClone(expected); known.status = 'completed'; known.steps[0].delivery = 'delivered'; known.steps[0].reason_code = null;
      const second = await replayNativeTrajectory({ trajectory: { result_source: 'runtime_global_result', points: [
        { observation: reduced.observation, chosen: choice.choice_id, result: known },
        { chosen: null, observation: { status: 'terminal', terminal: { outcome: 'loss' } } }
      ] }, driver, record: { append(kind, data) { events.push({ kind, data }); } }, seed: 'fixture', initialProgress: {} });
      assert.equal(second.unknown_delivery, true);
      assert.equal(second.compared_results, 1);
    } else {
      assert.equal(report.compared_results, 1);
      assert.equal(report.matching_results, 1);
    }
    const actual = events.find(event => event.kind === 'p1_result').data.actual;
    assert.deepEqual(actual, expected);
    assert.equal(steps, 1);
    assert.ok(closes >= 1);
    assert.equal(report.completed, false);
  });
}

for (const field of ['occurrence', 'steps', 'schema']) {
  test(`invalid original Result ${field} fails before Host start`, async () => {
    const { reduced } = example();
    const choice = reduced.observation.catalog[0];
    const result = { schema: 'sts2.p1/result-1', occurrence: reduced.observation.envelope.occurrence,
      choice_id: choice.choice_id, status: 'completed', steps: [], commitment_delta: null };
    result[field] = field === 'occurrence' ? result.occurrence + 1 : field === 'steps' ? null : 'foreign-schema';
    let started = 0;
    const report = await replayNativeTrajectory({ trajectory: { result_source: 'runtime_global_result', points: [
      { observation: reduced.observation, chosen: choice.choice_id, result },
      { chosen: null, observation: { status: 'terminal', terminal: { outcome: 'loss' } } }
    ] }, driver: { async start() { started++; }, async close() { return {}; } },
    record: { append() {} }, seed: 'not-offered', initialProgress: {} });
    assert.match(report.error, /original_result_invalid_or_occurrence_unbound/);
    assert.equal(started, 0);
  });
}
