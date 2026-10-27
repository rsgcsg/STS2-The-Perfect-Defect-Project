import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { coveragePlan, runCoverage, coverageTable, coverageHtml } from '../p1-cross-host-coverage.mjs';

function config(root) { return { seeds: ['ENGINEERING1', 'ENGINEERING2'], strategies: [{ id: 'uniform', command: ['python', 'random agent.py'] }],
  candidate: join(root, 'candidate'), localRoot: join(root, 'reference-state'), out: join(root, 'diagnostics') }; }

test('default plan is inert and preserves command argument boundaries', async context => {
  const root = mkdtempSync(join(tmpdir(), 'coverage-plan-')); context.after(() => rmSync(root, { recursive: true, force: true }));
  const plan = coveragePlan(config(root));
  let called = 0;
  assert.deepEqual(await runCoverage(plan, { suiteRunner: async () => { called++; } }), plan);
  assert.equal(called, 0); assert.equal(existsSync(plan.out), false);
  assert.equal(plan.jobs.length, 2); assert.equal(plan.jobs[0].strategy.command[1], 'random agent.py');
});
for (const unknown of [false, true]) test(`source ${unknown ? 'unknown' : 'failure'} is retained without Managed replay or unconfirmed-close overlap`, async context => {
  const root = mkdtempSync(join(tmpdir(), 'coverage-failure-')); context.after(() => rmSync(root, { recursive: true, force: true }));
  const plan = coveragePlan(config(root)); let calls = 0;
  const result = await runCoverage(plan, { execute: true, suiteRunner: async (args, directory) => {
    calls++; const out = args[args.indexOf('--out') + 1]; mkdirSync(out);
    assert.deepEqual(JSON.parse(args[args.indexOf('--agent-json') + 1]), ['python', 'random agent.py']);
    writeFileSync(join(out, 'report.json'), JSON.stringify({ rows: [{ unknown, terminal: false,
      reference_close: { status: 'unconfirmed' }, error: 'source-fixture-failure' }] }));
    assert.equal(existsSync(join(directory, 'managed-replay.jsonl')), false);
    return { code: 0, signal: null };
  } });
  assert.equal(calls, 1); assert.equal(result.plan.jobs.length, 2); assert.equal(result.reports.length, 1);
  assert.equal(result.reports[0].original_unknown, unknown); assert.equal(result.reports[0].source_close_issue, true);
  assert.equal(result.reports[0].status, 'source_not_replayable');
  assert.equal(result.reports[0].completed, false);
});

test('coverage keeps unreached surfaces and diagnostic differences without accepting them', () => {
  const report = { diagnostic_id: 'fixture', planned_surfaces: { combat_turn: 30, shop_inventory: 2 },
    by_surface: { combat_turn: { compared: 5, matching: 4 } }, differences: [{ index: 4 }], stopped_surface: 'combat_turn',
    first_divergence: { index: 4, reason: '<untrusted>' }, matching_points: 4, compared_points: 5, planned_points: 32 };
  assert.deepEqual(coverageTable([report]).combat_turn, { planned: 30, compared: 5, matching: 4, differences: ['fixture:D1'] });
  const page = coverageHtml([report]); assert.match(page, /shop_inventory/); assert.match(page, /未到达/);
  assert.match(page, /&lt;untrusted&gt;/); assert.doesNotMatch(page, /<untrusted>/);
});

test('engineering seeds reuse Host canonicalization and reject aliases before game use', () => {
  const base = config('/tmp/coverage-canonical-fixture');
  const plan = coveragePlan({ ...base, seeds: ['aoi'] });
  assert.equal(plan.jobs[0].seed, 'A01');
  assert.throws(() => coveragePlan({ ...base, seeds: ['O', '0'] }), /coverage_unique_engineering_seeds_required/);
});

test('Managed language is an explicit plan input and existing unknown sources never launch Reference again', async context => {
  const root = mkdtempSync(join(tmpdir(), 'coverage-reuse-')); context.after(() => rmSync(root, { recursive: true, force: true }));
  const base = { ...config(root), seeds: ['M3A27A'], language: 'zh' };
  const plan = coveragePlan(base); assert.equal(plan.language, 'zh');
  const original = join(root, 'old'); mkdirSync(original);
  const sourceOut = join(original, plan.jobs[0].diagnostic_id, 'reference'); mkdirSync(sourceOut, { recursive: true });
  writeFileSync(join(sourceOut, 'report.json'), JSON.stringify({ rows: [{ unknown: true, terminal: false,
    reference_close: { exit: { code: 0, signal: null } } }] }));
  writeFileSync(join(original, 'coverage.json'), JSON.stringify({ plan, reports: [{
    diagnostic_id: plan.jobs[0].diagnostic_id, source_process_exit: { code: 0, signal: null } }] }));
  let called = false;
  const report = await runCoverage(plan, { execute: true, existingSources: original,
    suiteRunner: async () => { called = true; throw Error('must not launch'); } });
  assert.equal(called, false); assert.equal(report.reports[0].original_unknown, true);
  assert.equal(report.reports[0].source_reused_without_game, true);
  assert.equal(report.reports[0].compared_points, 0);
});
