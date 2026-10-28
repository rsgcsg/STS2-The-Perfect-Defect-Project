#!/usr/bin/env node
/** Diagnostic replay of one sealed native recording. Launch facts stay private;
 * the existing strict P1 replay owns comparison, delivery and stopping. */
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readVerifiedP1NativeBundle } from './p1-native-bundle.mjs';
import { convertP1NativeBundle } from './p1-native-to-p1.mjs';
import { replayNativeTrajectory } from './p1-native-replay.mjs';
import { buildRecordedReplayPlan } from './recorded-replay-plan.mjs';
import { matchRecordedChoice } from './recorded-replay-preferences.mjs';
import { HostDriver } from '../components/policy-runtime/dist/p1-global/host-driver.js';
import { EpisodeRecorder } from '../components/policy-runtime/dist/p1-global/recorder.js';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

export function readRecordedLaunch(source, trajectory) {
  const identity = source.manifest.content_identity;
  const runIds = new Set(source.journal.filter(row => row.kind === 'run_started_native').map(row => row.run_id));
  if (runIds.size !== 1) throw new Error('recording_replay_one_native_start_required');
  const [runId] = runIds;
  const reference = `initial-progress/${runId}.json`;
  if (!Object.hasOwn(identity.raw_file_sha256, reference)) throw new Error('recording_replay_launch_witness_missing');
  const bytes = source.bytes(reference), descriptor = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  if (descriptor.schema !== 'sts2.human-annotator/initial-progress-original-1'
      || descriptor.session_id !== identity.session_id || descriptor.timeline_id !== identity.timeline_id
      || descriptor.run_id !== runId || descriptor.status !== 'captured' || descriptor.run_seed?.status !== 'captured'
      || typeof descriptor.run_seed.seed !== 'string' || !descriptor.run_seed.seed)
    throw new Error('recording_replay_captured_launch_required');
  const progressBytes = source.bytes(descriptor.payload_ref);
  if (progressBytes.length !== descriptor.bytes || sha(progressBytes) !== descriptor.sha256)
    throw new Error('recording_replay_progress_integrity');
  const progressJson = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(progressBytes);
  if (!Buffer.from(progressJson).equals(progressBytes)) throw new Error('recording_replay_exact_utf8_required');
  JSON.parse(progressJson);
  const observation = trajectory.points[0]?.observation;
  const character = observation?.player?.content?.character_definition_id;
  const ascension = observation?.run?.content?.ascension;
  if (!['IRONCLAD', 'SILENT', 'DEFECT', 'REGENT', 'NECROBINDER'].includes(character)
      || !Number.isSafeInteger(ascension) || ascension < 0 || ascension > 10)
    throw new Error('recording_replay_original_run_setup_missing');
  return { seed: descriptor.run_seed.seed,
    initialProgress: { recipe: 'captured-native-progress-v1', progress_json: progressJson, progress_sha256: descriptor.sha256 },
    character, ascension, witness: { reference, sha256: sha(bytes), progress_sha256: descriptor.sha256, run_id: runId } };
}

/** Private reset transport is represented by its hash/reference, never copied to
 * diagnostic P1 logs. Unchanged public response bytes keep the original recorder. */
export function privateRecordingSink(recorder, launch) {
  const sensitive = serialized => serialized.includes(launch.seed) || serialized.includes('"progress_json"')
    || serialized.includes('"actual_seed"') || serialized.includes('"requested_seed"');
  const reference = { private_launch_original_ref: launch.witness.reference, private_values_retained_only_in_original: true };
  return {
    append(kind, data) {
      const raw = JSON.stringify(data);
      return sensitive(raw) ? recorder.append("private_launch_transport", { source_kind: kind, ...reference, transport_sha256: sha(raw) })
        : recorder.append(kind, data);
    },
    appendRaw(kind, raw, extra = {}) {
      return sensitive(raw) ? recorder.append("private_launch_transport", { source_kind: kind, ...extra, ...reference, transport_sha256: sha(raw) })
        : recorder.appendRaw(kind, raw, extra);
    }
  };
}

export function publicRecordingReport(report, launch) {
  const result = structuredClone(report);
  delete result.seed;
  delete result.episode_identity;
  // Errors are allowed to quote the native launch command; keep only the safe text.
  return JSON.parse(JSON.stringify(result, (_key, value) => typeof value === 'string'
    ? value.replaceAll(launch.seed, '[private-launch-original]') : value));
}

export async function replayRecording({ bundle, candidate, out, language, budgetMs = 180000,
  driverFactory = (launch, sink) => new HostDriver({ kind: 'managed', hostRuntimeRoot: resolve(root, 'components/host-runtime'),
    location: resolve(candidate), character: launch.character, extraArgs: ['--language', language, '--ascension', String(launch.ascension)],
    requestTimeoutMs: 30000, stepTimeoutMs: 30000 }, sink) }) {
  if (!['en', 'zh'].includes(language)) throw new Error('recording_replay_language_required');
  if (!Number.isSafeInteger(budgetMs) || budgetMs < 1 || budgetMs > 1200000) throw new Error('recording_replay_budget_invalid');
  if (existsSync(out)) throw new Error('recording_replay_output_exists_no_retry');
  const original = readVerifiedP1NativeBundle(bundle);
  const source = await convertP1NativeBundle({ bundle, includeNativeReplayWitnesses: true });
  const trajectory = source.complete && source.whole_run_recording_complete && source.trajectory && source.raw_unknown_count === 0
    ? source.trajectory : buildRecordedReplayPlan(original, source);
  const launch = readRecordedLaunch(original, trajectory);
  mkdirSync(out, { recursive: true, mode: 0o700 });
  const recorder = new EpisodeRecorder(resolve(out, 'replay.jsonl'), { schema: 'spireagent/managed-diagnostic-log-1', scope: 'recorded_launch_managed_diagnostic',
    source_content_id: source.source_content_id, source_session_id: source.source_session_id,
    launch_witness: launch.witness, language, character: launch.character, ascension: launch.ascension,
    comparator_changed: true, comparison_exception_ids: ["M060"], formal_evaluation: false });
  const sink = privateRecordingSink(recorder, launch);
  const driver = driverFactory(launch, sink);
  // Verify manager-only launch identity before any action is offered.
  let launchVerified = false;
  const originalRequest = driver.request.bind(driver);
  driver.request = async (request, timeout) => {
    const response = await originalRequest(request, timeout);
    if (request.command === 'episode_identity') {
      const identity = response.identity, proof = identity?.initial_progress;
      if (identity?.episode_provenance?.verdict !== 'provenance_pass'
          || identity.episode_provenance.actual_seed !== launch.seed || proof?.recipe !== launch.initialProgress.recipe
          || proof.progress_sha256 !== launch.initialProgress.progress_sha256 || proof.run_unlock_state_matches !== true)
        throw new Error('recording_replay_launch_unverified');
      launchVerified = true;
    }
    return response;
  };
  const result = publicRecordingReport(await replayNativeTrajectory({ trajectory,
    driver, record: sink, seed: launch.seed, initialProgress: launch.initialProgress, budgetMs,
    pointMatcher: matchRecordedChoice }), launch);
  Object.assign(result, { schema: 'spireagent/recorded-managed-diagnostic-1', source_content_id: source.source_content_id,
    source_session_id: source.source_session_id, launch_witness: launch.witness, character: launch.character,
    ascension: launch.ascension, language,
    startup_scope: { seed_and_progress: "verified_original", character_and_ascension: "recorded_p1",
      language: "manager_configuration", managed_wait_timing: "instant",
      original_speed_witness: "not_recorded", full_presentation_reconstruction: "not_established" },
    launch_verified: launchVerified, formal_evaluation: false, comparator_changed: true,
    comparison_policy: 'strict_public_with_user_approved_M060_toggle_preference',
    replay_scope: trajectory.replay_scope ?? { kind: 'original_complete_p1_trajectory' },
    stopped_surface: trajectory.points[result.stopped_index]?.observation.scene.native_kind,
    planned_uncompared_native_inputs: trajectory.points.filter(point => point.native_bridge).length,
    source_p1_points: source.trajectory?.points.length ?? source.actions.filter(action =>
      action.match_status === 'matched' && action.result?.status === 'completed').length,
    planned_surfaces: trajectory.points.filter(point => !point.native_bridge && !point.replay_checkpoint && !point.bounded_end).reduce((rows, point) => {
      const kind = point.observation.scene.native_kind; rows[kind] = (rows[kind] ?? 0) + 1; return rows;
    }, {}) });
  result.record = recorder.close({ completed: result.completed, matching_points: result.matching_points });
  writeFileSync(resolve(out, 'report.json'), JSON.stringify(result, null, 2) + '\n');
  return result;
}

async function main() {
  const options = {}, names = { '--bundle': 'bundle', '--candidate': 'candidate', '--out': 'out', '--language': 'language', '--budget-ms': 'budget' };
  const args = process.argv.slice(2);
  for (let i = 0; i < args.length; i += 2) {
    if (!names[args[i]] || !args[i + 1]) throw new Error('recording_replay_named_arguments_required');
    options[names[args[i]]] = args[i + 1];
  }
  for (const name of ['bundle', 'candidate', 'out', 'language']) if (!options[name]) throw new Error(`missing_${name}`);
  const report = await replayRecording({ ...options, ...(options.budget ? { budgetMs: Number(options.budget) } : {}) });
  console.log(JSON.stringify(report));
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
