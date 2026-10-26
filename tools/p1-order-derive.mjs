#!/usr/bin/env node
/** An explicit, lossless offline view under the approved order-only revision. */
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const sha = value => createHash('sha256').update(value).digest('hex');
export function deriveOrderTrajectory(raw) {
  const original = JSON.parse(raw.toString('utf8'));
  if (original.schema !== 'sts2.p1/trajectory-1' || !Array.isArray(original.points)
      || original.points.length === 0) throw new Error('order_derivation_trajectory_required');
  const trajectory = structuredClone(original), observations = [];
  function derive(observation, path) {
    if (observation?.schema !== 'sts2.p1/observation-1'
        || observation.profile !== 'p1-global-r1' || observation.revision !== 'r1.5')
      throw new Error('order_derivation_requires_exact_r1_5');
    const before = JSON.stringify(observation);
    observation.revision = 'r1.6';
    observations.push({ path, before_sha256: sha(before), after_sha256: sha(JSON.stringify(observation)) });
  }
  trajectory.points.forEach((point, index) => {
    derive(point.observation, `points[${index}].observation`);
    (point.continuity_checks ?? []).forEach((check, checkIndex) =>
      derive(check.observation, `points[${index}].continuity_checks[${checkIndex}].observation`));
  });
  (trajectory.native_presentations ?? []).forEach((span, spanIndex) =>
    (span.native_replay_steps ?? []).forEach((step, stepIndex) =>
      derive(step.observation, `native_presentations[${spanIndex}].native_replay_steps[${stepIndex}].observation`)));
  const bytes = Buffer.from(JSON.stringify(trajectory));
  return { trajectory, bytes, receipt: { schema: 'sts2.p1/order-derivation-1',
    source_sha256: sha(raw), derived_sha256: sha(bytes), from_revision: 'r1.5', to_revision: 'r1.6',
    only_changed_field: 'observation.revision', observations,
    arrays_changed: false, results_changed: false, original_model_input_rewritten: false,
    inference_performed: false,
    nonclaims: ['original_consumer_used_r1_6', 'Human_qualification', 'model_order_invariance', 'Host_equivalence'] } };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [source, output, receipt] = process.argv.slice(2);
  if (!source || !output || !receipt || process.argv.length !== 5)
    throw new Error('usage: p1-order-derive SOURCE OUTPUT RECEIPT');
  if ([output, receipt].some(existsSync) || resolve(output) === resolve(receipt))
    throw new Error('order_derivation_output_exists_or_alias');
  const result = deriveOrderTrajectory(readFileSync(source));
  writeFileSync(output, result.bytes, { flag: 'wx' });
  writeFileSync(receipt, JSON.stringify(result.receipt, null, 2) + '\n', { flag: 'wx' });
}
