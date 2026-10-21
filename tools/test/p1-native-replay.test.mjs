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
