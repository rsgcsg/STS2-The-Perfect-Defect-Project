#!/usr/bin/env node
/** Reference Runtime records the policy's actual choices; Managed strictly
 * replays those choices with the exact Reference-prepared progress. Diagnostic only. */
import { spawn } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { extractTrajectory } from '../components/policy-runtime/dist/p1-global/trajectory.js';
import { EpisodeRecorder } from '../components/policy-runtime/dist/p1-global/recorder.js';
import { HostDriver } from '../components/policy-runtime/dist/p1-global/host-driver.js';
import { replayNativeTrajectory } from './p1-native-replay.mjs';
import { canonicalizeEpisodeSeed } from '../components/host-runtime/src/episode-provenance.mjs';
import { validateInitialProgress } from '../components/host-runtime/src/native-baseline.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const escape = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
export function coverageTable(reports) {
  const rows = {};
  for (const report of reports) {
    for (const [surface, planned] of Object.entries(report.planned_surfaces ?? {})) {
      const row = rows[surface] ??= { planned: 0, compared: 0, matching: 0, differences: [] };
      row.planned += planned;
    }
    for (const [surface, count] of Object.entries(report.by_surface ?? {})) {
      const row = rows[surface] ??= { planned: 0, compared: 0, matching: 0, differences: [] };
      row.compared += count.compared; row.matching += count.matching;
    }
    for (const [index, diff] of (report.differences ?? []).entries()) {
      const surface = diff.surface ?? report.point_surfaces?.[diff.index] ?? report.stopped_surface;
      if (surface && rows[surface]) rows[surface].differences.push(`${report.diagnostic_id ?? report.source_session_id ?? 'diagnostic'}:D${index + 1}`);
    }
  }
  return rows;
}
export function coverageHtml(reports, { plannedJobs = reports.length } = {}) {
  const rows = coverageTable(reports);
  return '<!doctype html><html lang="zh"><meta charset="utf-8"><title>Managed 重演诊断</title>'
    + '<style>body{font:16px system-ui;margin:40px auto;max-width:1100px;padding:0 24px;color:#17212b}table{border-collapse:collapse;width:100%}th,td{padding:10px;border-bottom:1px solid #ccd5df;text-align:left}small{color:#526170}code{overflow-wrap:anywhere}</style>'
    + '<h1>Managed 重演诊断</h1><p>逐点使用原严格比较器。没有走到的点保留在计划分母中；差异、未知和关闭问题原样保留。本页不提供正式成绩。</p>'
    + `<p>计划诊断 ${plannedJobs} 次；已尝试 ${reports.length} 次；未执行 ${Math.max(0, plannedJobs - reports.length)} 次。未执行的界面点数尚未知。</p>`
    + '<table><thead><tr><th>界面</th><th>计划点</th><th>已比较</th><th>一致</th><th>未到达</th><th>差异编号</th></tr></thead><tbody>'
    + Object.entries(rows).map(([surface, row]) => `<tr><td>${escape(surface)}</td><td>${row.planned}</td><td>${row.compared}</td><td>${row.matching}</td><td>${Math.max(0, row.planned - row.compared)}</td><td>${escape(row.differences.join(', '))}</td></tr>`).join('')
    + '</tbody></table><h2>每次诊断</h2>' + reports.map(report => `<section><h3>${escape(report.diagnostic_id ?? report.source_session_id ?? '诊断')}</h3><p>${escape(report.error ?? (report.completed ? '已走完所录轨迹' : report.status ?? '尚未走完'))}；一致 ${report.matching_points ?? 0} / 已比 ${report.compared_points ?? 0} / 计划 ${report.planned_points ?? 0}；unknown ${escape(report.unknown_delivery ?? report.original_unknown ?? false)}。</p><pre><code>${escape(JSON.stringify({ first_divergence: report.first_divergence, report_read_error: report.report_read_error, source_error: report.source_error, source_close: report.source_close, host_close: report.host_close }, null, 2))}</code></pre></section>`).join('')
    + '</html>\n';
}

export function coveragePlan({ seeds, strategies, candidate, localRoot, out, template = 'defect-a0-s0', language = 'en' }) {
  if (!Array.isArray(seeds) || !seeds.length || seeds.some(seed => typeof seed !== 'string')) throw new Error('coverage_unique_engineering_seeds_required');
  seeds = seeds.map(canonicalizeEpisodeSeed);
  if (new Set(seeds).size !== seeds.length) throw new Error('coverage_unique_engineering_seeds_required');
  if (!['en', 'zh'].includes(language)) throw new Error('coverage_language_required');
  if (!Array.isArray(strategies) || !strategies.length || strategies.some(strategy => !strategy.id || !Array.isArray(strategy.command)
      || !strategy.command.length || strategy.command.some(arg => typeof arg !== 'string' || !arg))) throw new Error('coverage_agent_commands_required');
  if (new Set(strategies.map(strategy => strategy.id)).size !== strategies.length) throw new Error('coverage_unique_strategies_required');
  if (![candidate, localRoot, out].every(value => typeof value === 'string' && value)) throw new Error('coverage_paths_required');
  return { schema: 'spireagent/cross-host-coverage-plan-1', diagnostic_only: true, comparator_changed: false,
    character: 'DEFECT', ascension: 0, language, template, jobs: strategies.flatMap(strategy => seeds.map(seed => ({
      seed, strategy, diagnostic_id: `diagnostic-${strategies.indexOf(strategy) + 1}-${seeds.indexOf(seed) + 1}` }))),
    candidate: resolve(candidate), local_root: resolve(localRoot), out: resolve(out),
    game_required: 'Reference headless; user approval and free GAME-SLOT required before --execute' };
}

async function runSuite(args, directory) {
  const child = spawn(process.execPath, [join(root, 'tools/p1-suite.mjs'), ...args], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  const stdout = [], stderr = [];
  child.stdout.on('data', bytes => stdout.push(bytes)); child.stderr.on('data', bytes => stderr.push(bytes));
  const exit = await new Promise((ok, fail) => { child.once('error', fail); child.once('exit', (code, signal) => ok({ code, signal })); });
  writeFileSync(join(directory, 'suite.stdout'), Buffer.concat(stdout)); writeFileSync(join(directory, 'suite.stderr'), Buffer.concat(stderr));
  return exit;
}

export async function runCoverage(plan, { execute = false, suiteRunner = runSuite, budgetMs = 180000,
  template = plan.template, endpoint = 'http://127.0.0.1:15526', expectedInstalledTuple = null,
  gameDir = null, existingSources = null } = {}) {
  if (!execute) return plan; // Plan mode never probes, boots or installs a game.
  if (!Number.isSafeInteger(budgetMs) || budgetMs < 1 || budgetMs > 1200000) throw new Error('coverage_budget_invalid');
  let sourceBatch = null;
  if (existingSources) {
    sourceBatch = JSON.parse(readFileSync(join(resolve(existingSources), 'coverage.json'), 'utf8'));
    if (JSON.stringify(sourceBatch.plan.jobs) !== JSON.stringify(plan.jobs))
      throw new Error('coverage_original_source_jobs_changed');
  }
  if (existsSync(plan.out)) throw new Error('coverage_output_exists_no_retry');
  mkdirSync(plan.out, { recursive: true, mode: 0o700 });
  const reports = [];
  const save = () => { writeFileSync(join(plan.out, 'coverage.json'), JSON.stringify({ plan, reports, by_surface: coverageTable(reports) }, null, 2));
    writeFileSync(join(plan.out, 'index.html'), coverageHtml(reports, { plannedJobs: plan.jobs.length })); };
  save();
  for (const job of plan.jobs) {
    const directory = join(plan.out, job.diagnostic_id); mkdirSync(directory);
    const report = { diagnostic_id: job.diagnostic_id, strategy: job.strategy.id, seed: job.seed,
      formal_evaluation: false, planned_points: 0, compared_points: 0, matching_points: 0, completed: false };
    reports.push(report); save();
    try {
      const sourceOut = existingSources ? join(resolve(existingSources), job.diagnostic_id, 'reference') : join(directory, 'reference');
      const args = ['--worktree', root, '--host', 'reference', '--local-root', plan.local_root, '--template', template,
        '--display-mode', 'headless', '--endpoint', endpoint, '--seeds', job.seed, '--out', sourceOut,
        '--agent-json', JSON.stringify(job.strategy.command), '--episode-budget-ms', String(budgetMs)];
      if (gameDir) args.push('--game-dir', gameDir);
      if (expectedInstalledTuple) args.push('--expected-installed-tuple', JSON.stringify(expectedInstalledTuple));
      if (existingSources) {
        const original = sourceBatch.reports.find(row => row.diagnostic_id === job.diagnostic_id);
        if (!original?.source_process_exit) throw new Error('coverage_original_source_missing');
        report.source_process_exit = original.source_process_exit;
        report.source_reference_directory = sourceOut;
        report.source_reused_without_game = true;
      } else report.source_process_exit = await suiteRunner(args, directory);
      const suite = JSON.parse(readFileSync(join(sourceOut, 'report.json'), 'utf8'));
      const row = suite.rows[0]; report.source_close = row.reference_close; report.source_error = row.error ?? null;
      const close = row.reference_close?.exit;
      if (!close || close.code !== 0 || close.signal != null) report.source_close_issue = true;
      // Read recorded offers for coverage even when an unknown forbids replay.
      let trajectory = null;
      if (row.run_dir) {
        try {
          trajectory = await extractTrajectory(row.run_dir);
          report.planned_points = trajectory.points.length;
          report.point_surfaces = trajectory.points.map(point => point.observation.scene.native_kind);
          report.planned_surfaces = report.point_surfaces.reduce((counts, surface) => { counts[surface] = (counts[surface] ?? 0) + 1; return counts; }, {});
        } catch (error) { report.source_trajectory_error = error.message; }
      }
      // Any source unknown remains in the denominator, without offering its actions again.
      if (row.unknown || row.taint || !row.terminal || row.error || report.source_process_exit.code !== 0) {
        report.original_unknown = !!row.unknown; report.status = 'source_not_replayable';
        throw new Error('coverage_source_not_replayable');
      }
      const identity = JSON.parse(readFileSync(join(dirname(dirname(row.run_dir)), 'reference-identity.json'), 'utf8'));
      const prepared = identity.baseline?.prepared;
      const initialProgress = validateInitialProgress({ recipe: identity.baseline?.recipe,
        progress_json: prepared?.progress_json, progress_sha256: prepared?.progress_sha256 });
      if (identity.episode_provenance?.verdict !== 'provenance_pass'
          || identity.episode_provenance.actual_seed !== job.seed || !initialProgress) throw new Error('coverage_reference_launch_unverified');
      if (!trajectory) throw new Error('coverage_recorded_trajectory_unavailable');
      const recorder = new EpisodeRecorder(join(directory, 'managed-replay.jsonl'), { scope: 'cross_host_diagnostic',
        source_runtime_directory: row.run_dir, source_trajectory_sha256: sha(JSON.stringify(trajectory)), formal_evaluation: false });
      const driver = new HostDriver({ kind: 'managed', hostRuntimeRoot: join(root, 'components/host-runtime'),
        location: plan.candidate, character: 'Defect', extraArgs: ['--language', plan.language], requestTimeoutMs: 30000 }, recorder);
      const result = await replayNativeTrajectory({ trajectory, driver, record: recorder, seed: job.seed, initialProgress, budgetMs });
      Object.assign(report, result, { diagnostic_id: job.diagnostic_id });
      report.record = recorder.close({ completed: report.completed });
    } catch (error) { report.error = error.message; }
    finally { save(); }
    // An unconfirmed Reference close must not overlap another Reference launch.
    if (report.source_close_issue) break;
  }
  return { plan, reports, by_surface: coverageTable(reports) };
}

async function main() {
  const args = process.argv.slice(2), options = {};
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--execute') { options.execute = true; continue; }
    if (!['--config', '--sources'].includes(args[i]) || !args[i + 1]) throw new Error('usage: --config FILE [--execute]');
    const key = args[i] === '--sources' ? 'sources' : 'config';
    options[key] = args[++i];
  }
  if (!options.config) throw new Error('coverage_config_required');
  const config = JSON.parse(readFileSync(options.config, 'utf8'));
  const plan = coveragePlan(config);
  console.log(JSON.stringify(await runCoverage(plan, { ...config, execute: options.execute === true, existingSources: options.sources ?? null }), null, 2));
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main().catch(error => { console.error(error.message); process.exitCode = 1; });
