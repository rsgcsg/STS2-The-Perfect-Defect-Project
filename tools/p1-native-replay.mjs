#!/usr/bin/env node
/** Offline strict native-recording replay. Uses existing public Host driver and
 * P1 core; native presentation inputs are replay evidence, never Agent choices. */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID, createHash } from 'node:crypto';
import * as core from '../components/connector/sdk/typescript/dist/p1Global/index.js';
import { HostDriver } from '../components/policy-runtime/dist/p1-global/host-driver.js';
import { EpisodeRecorder } from '../components/policy-runtime/dist/p1-global/recorder.js';
import { matchReferenceChoice } from '../components/policy-runtime/dist/p1-global/trajectory.js';
import { convertP1NativeBundle } from './p1-native-to-p1.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

/** A unique P1-equivalent existing choice is used only to locate its existing
 * native first step. No choice or commitment is submitted for the presentation. */
export function resolvePresentationInput(witness, reduction) {
  if (witness.original_terminal_phase !== 'finished' || !reduction.complete)
    throw new Error('native_replay_unfinished_or_incomplete');
  const match = matchReferenceChoice({ observation: witness.observation,
    chosen: witness.reference_choice_id }, reduction.observation);
  if (match.verdict !== 'equal' || match.choice_id == null)
    return { ...match, input: null };
  const lowering = reduction.lowering[match.choice_id];
  const input = lowering?.first;
  if (!input || input.verb !== witness.bound_action.verb)
    throw new Error('native_replay_existing_first_step_required');
  return { ...match, input };
}

export async function replayNativeTrajectory({ trajectory, driver, core: kernel = core,
  record, seed, initialProgress, budgetMs = 1200000, diagnosticLocator = null }) {
  const deadline = Date.now() + budgetMs;
  let state = kernel.createSession(`native-strict-${randomUUID()}`, 'managed');
  const report = { schema: 'sts2.p1/native-strict-replay-1', seed,
    planned_points: trajectory.points.length, matching_choices: 0, matching_terminal: false,
    native_presentations_replayed: 0, native_presentation_steps: 0,
    native_steps: 0, unknown_delivery: false, first_divergence: null, completed: false,
    comparison_mode: diagnosticLocator ? 'diagnostic_not_equivalence' : 'strict',
    compared_points: 0, matching_points: 0, diagnostic_choices: 0, differences: [], by_surface: {} };
  let index = 0;
  const log = (kind, data) => record.append(kind, data);
  const compare = (reference, actual, detail) => {
    const match = matchReferenceChoice(reference, actual);
    log('p1_observation', { index, comparison: match, ...detail, observation: actual });
    if (detail.phase === 'choice') {
      report.compared_points++;
      report.matching_points += Number(match.verdict === 'equal');
      const counts = report.by_surface[reference.observation.scene.native_kind] ??= { compared: 0, matching: 0 };
      counts.compared++; counts.matching += Number(match.verdict === 'equal');
    }
    if (match.verdict !== 'equal') {
      const difference = { index, ...detail, ...match };
      report.first_divergence ??= difference;
      report.differences.push(difference);
      if (diagnosticLocator && detail.phase === 'choice' && reference.chosen !== null) {
        const located = diagnosticLocator(reference, actual, index);
        log('diagnostic_locator', { index, equivalence_claim: false, evidence: located });
        if (located?.choice_id && actual.catalog.some(choice => choice.choice_id === located.choice_id))
          return { ...match, choice_id: located.choice_id, diagnostic: true };
      }
      throw new Error('strict_public_difference');
    }
    return match;
  };
  const call = async (command, extra = {}) => {
    if (Date.now() >= deadline) throw new Error('offline_replay_budget_exhausted');
    return driver.request({ command, ...extra }, Math.min(60000, deadline - Date.now()));
  };
  async function capture() {
    const full = (await call('capture')).full;
    if (full?.basis?.capture_status !== 'complete' || full.basis.missing.length !== 0)
      throw new Error('offline_replay_public_basis_incomplete');
    return full.basis;
  }
  async function step(input, basis, origin) {
    if (!basis.snapshot.bound_actions.actions.some(action => action.bound_action_id === input.bound_action_id
      && action.verb === input.verb)) throw new Error('native_replay_not_in_current_catalog');
    const mutationId = driver.newMutationId();
    log('p1_step', { index, origin, input, expected_snapshot_id: basis.snapshot.snapshot_id, mutation_request_id: mutationId });
    let receipt;
    try { receipt = (await call('step', { mutation_request_id: mutationId,
      expected_snapshot_id: basis.snapshot.snapshot_id, bound_action_id: input.bound_action_id })).receipt; }
    catch (error) { report.unknown_delivery = true; throw error; }
    report.native_steps++;
    if (receipt?.delivery === 'unknown' || receipt?.delivery == null) {
      report.unknown_delivery = true; throw new Error('offline_replay_unknown_delivery');
    }
    if (receipt.delivery !== 'delivered') throw new Error('offline_replay_native_not_delivered');
    return capture();
  }
  try {
    await driver.start();
    await call('reset', { seed, initial_progress: initialProgress });
    report.episode_identity = (await call('episode_identity')).identity;
    let basis = await capture();
    for (; index < trajectory.points.length; index++) {
      let reduced = kernel.reduce(state, basis);
      if (!reduced.complete) throw new Error('offline_replay_p1_incomplete');
      const point = trajectory.points[index];
      const checks = point.continuity_checks ?? [];
      for (const check of checks) {
        if (check.phase === 'presentation') {
          const span = trajectory.native_presentations.find(item => item.decision_id === check.decision_id);
          if (!span) throw new Error('offline_replay_presentation_missing');
          compare({ observation: check.observation, chosen: null }, reduced.observation,
            { phase: check.phase, boundary: check.boundary, decision_id: check.decision_id });
          if (check.boundary === 'before' && span.disposition === 'presentation_exception') {
            if (span.kind !== 'reward_open_return' || span.exception?.id !== 'stop_relic_refresh'
              || !Array.isArray(span.native_replay_steps) || span.native_replay_steps.length !== 2)
              throw new Error('offline_replay_unqualified_native_presentation');
            // All intermediate facts are compared. The preserved protocol state
            // receives no decline/consume commitment from these zero-label inputs.
            for (const witness of span.native_replay_steps) {
              const actual = kernel.reduce(state, basis);
              const mapped = resolvePresentationInput(witness, actual);
              compare({ observation: witness.observation, chosen: witness.reference_choice_id }, actual.observation,
                { phase: 'native_presentation_input', decision_id: witness.decision_id });
              if (mapped.input == null) throw new Error('offline_replay_native_action_unmapped');
              basis = await step(mapped.input, basis, 'recorded_native_presentation');
              report.native_presentation_steps++;
            }
            report.native_presentations_replayed++;
            reduced = kernel.reduce(state, basis);
            if (!reduced.complete) throw new Error('offline_replay_native_successor_incomplete');
          }
        } else {
          compare({ observation: check.observation, chosen: null }, reduced.observation,
            { phase: check.phase, boundary: check.boundary, decision_id: check.decision_id });
        }
      }
      const match = compare(point, reduced.observation, { phase: 'choice' });
      state = reduced.state;
      if (point.chosen === null) {
        if (index !== trajectory.points.length - 1 || point.observation.status !== 'terminal'
          || !['win', 'loss'].includes(point.observation.terminal?.outcome))
          throw new Error('offline_replay_reference_terminal_required');
        report.matching_terminal = true;
        report.reached_reference_terminal = true;
        report.completed = report.differences.length === 0;
        break;
      }
      if (match.choice_id == null) throw new Error('offline_replay_choice_unmapped');
      const lowering = reduced.lowering[match.choice_id];
      if (!lowering) throw new Error('offline_replay_lowering_missing');
      log('p1_choice', { index, choice_id: match.choice_id, reference_choice: point.chosen });
      if (lowering.first) basis = await step(lowering.first, basis, 'p1_choice');
      for (const rule of lowering.follow) {
        const follow = kernel.resolveFollowBasis(rule, lowering, basis);
        if (!follow) { if (rule.optional) break; throw new Error('offline_replay_required_follow_missing'); }
        basis = await step(follow, basis, 'p1_follow');
      }
      state = kernel.applyCommitment(state, lowering, true);
      if (!lowering.first) basis = await capture();
      if (match.diagnostic) report.diagnostic_choices++;
      else report.matching_choices++;
    }
  } catch (error) {
    report.error = error.message;
    report.stopped_index = index;
  } finally {
    report.host_close = await driver.close();
  }
  return report;
}

async function main() {
  const args = process.argv.slice(2), options = {};
  for (let i = 0; i < args.length; i += 2) {
    const names = { '--bundle': 'bundle', '--trajectory': 'trajectory', '--candidate': 'candidate',
      '--seed': 'seed', '--initial-progress': 'progress', '--out': 'out', '--budget-ms': 'budget' };
    if (!names[args[i]] || !args[i + 1]) throw new Error('native strict replay requires named arguments');
    options[names[args[i]]] = args[i + 1];
  }
  for (const name of ['bundle', 'trajectory', 'candidate', 'seed', 'progress', 'out'])
    if (!options[name]) throw new Error(`missing_${name}`);
  if (existsSync(options.out)) throw new Error('offline_replay_output_exists');
  mkdirSync(options.out, { recursive: true });
  const source = await convertP1NativeBundle({ bundle: options.bundle, includeNativeReplayWitnesses: true });
  if (!source.complete || !source.whole_run_recording_complete || !source.trajectory)
    throw new Error('offline_replay_verified_whole_trajectory_required');
  const referenceBytes = readFileSync(options.trajectory), reference = JSON.parse(referenceBytes);
  // Extra native witnesses are separate replay evidence. All original protocol
  // points, choices and continuity comparisons must remain byte-for-byte equal.
  if (JSON.stringify(reference.points) !== JSON.stringify(source.trajectory.points)
    || reference.run_directory !== source.trajectory.run_directory)
    throw new Error('offline_replay_trajectory_not_same_original_derivation');
  writeFileSync(resolve(options.out, 'native-presentations.json'), JSON.stringify(source.trajectory.native_presentations));
  const recorder = new EpisodeRecorder(resolve(options.out, 'replay.jsonl'), {
    scope: 'offline_strict_native_replay', source_bundle: options.bundle,
    source_content_id: source.source_content_id, reference_sha256: sha(referenceBytes),
    p1_choices_changed: false, comparator_changed: false });
  const driver = new HostDriver({ kind: 'managed', hostRuntimeRoot: resolve(root, 'components/host-runtime'),
    location: resolve(options.candidate), character: 'Defect', extraArgs: ['--language', 'en'],
    requestTimeoutMs: 60000, stepTimeoutMs: 60000 }, recorder);
  const report = await replayNativeTrajectory({ trajectory: source.trajectory, driver, record: recorder,
    seed: options.seed, initialProgress: JSON.parse(readFileSync(options.progress, 'utf8')),
    budgetMs: options.budget ? Number(options.budget) : 1200000 });
  report.record = recorder.close({ completed: report.completed, matching_choices: report.matching_choices });
  writeFileSync(resolve(options.out, 'report.json'), JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify(report) + '\n');
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  main().catch(error => { process.stderr.write(`${error.stack ?? error}\n`); process.exitCode = 1; });
