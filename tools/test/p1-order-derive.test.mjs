import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { deriveOrderTrajectory } from '../p1-order-derive.mjs';

test('order-only derivation preserves arrays, unknown Results, choices and source bytes', () => {
  const observation = { schema: 'sts2.p1/observation-1', profile: 'p1-global-r1', revision: 'r1.5',
    scene: { content: { selectable_card_entity_ids: ['b', 'a'] } } };
  const value = { schema: 'sts2.p1/trajectory-1', points: [{ observation, chosen: 'c2',
    result: { status: 'unknown', steps: [] }, continuity_checks: [{ observation }] }] };
  const raw = Buffer.from(JSON.stringify(value)), saved = Buffer.from(raw);
  const result = deriveOrderTrajectory(raw);
  assert.deepEqual(raw, saved);
  assert.equal(result.receipt.source_sha256, createHash('sha256').update(raw).digest('hex'));
  assert.equal(result.receipt.observations.length, 2);
  const restored = structuredClone(result.trajectory);
  restored.points[0].observation.revision = 'r1.5';
  restored.points[0].continuity_checks[0].observation.revision = 'r1.5';
  assert.deepEqual(restored, value);
  for (const revision of ['r1.4', 'r1.6', 'future']) {
    const bad = structuredClone(value); bad.points[0].observation.revision = revision;
    assert.throws(() => deriveOrderTrajectory(Buffer.from(JSON.stringify(bad))), /exact_r1_5/);
  }
});
