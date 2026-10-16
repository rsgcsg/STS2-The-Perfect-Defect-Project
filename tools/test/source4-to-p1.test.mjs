import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { cpSync, mkdtempSync, rmSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, delimiter } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../..', import.meta.url));
const fixture = resolve(root, 'components/evidence/tests/fixtures/source_session_v4_p1/bundle');
const python = process.env.PYTHON ?? 'python';
function cli(bundle) {
  return spawnSync(process.execPath, ['tools/source4-to-p1.mjs', '--bundle', bundle,
    '--python', python, '--host-kind', 'reference'], { cwd: root, encoding: 'utf8' });
}
function mutate(bundle, code) {
  // Reuse the verifier's integrity-mutation helper: repairing outer hashes must
  // not make missing Reads or foreign bindings acceptable.
  const result = spawnSync(python, ['-c', `from pathlib import Path
import sys
from test_source_session_bundle_v4 import SourceSessionBundleV4Tests
case=SourceSessionBundleV4Tests()
case.bundle=Path(sys.argv[1])
${code}`, bundle], { encoding: 'utf8', env: { ...process.env,
    PYTHONPATH: [resolve(root, 'components/evidence'), resolve(root, 'components/evidence/tests')].join(delimiter) } });
  assert.equal(result.status, 0, result.stderr);
}
function privateBundle(run) {
  const directory = mkdtempSync(resolve(tmpdir(), 'source4-p1-test-'));
  try { cpSync(fixture, directory, { recursive: true }); run(directory); }
  finally { rmSync(directory, { recursive: true, force: true }); }
}
test('production Store/packer Reference-shaped prefix verifies and converts through the actual CLI', () => {
  const result = cli(fixture); assert.equal(result.status, 0, result.stderr);
  const report = JSON.parse(result.stdout);
  assert.equal(report.schema, 'sts2.p1/source4-conversion-report-2');
  assert.equal(report.complete, true); assert.equal(report.actions.length, 1);
  assert.equal(report.actions[0].choice.kind, 'combat.end_turn');
  assert.deepEqual(report.unresolved_inputs, []); assert.deepEqual(report.unresolved_contexts, []);
  assert.deepEqual(report.captures.map(c => c.classification), ['p1_initial_boundary', 'source_repeated_public_snapshot',
    'p1_decision_boundary', 'source_settling_context', 'p1_publication_boundary', 'source_repeated_public_snapshot']);
  assert.ok(report.captures.every(c => ['matched', 'context_only'].includes(c.status) && c.reason));
  assert.equal(report.captures.filter(c => c.source_kind === 'source_input').length, report.native_trace.length);
  assert.equal(report.scope.kind, 'bounded_recorded_prefix'); assert.equal(report.scope.natural_terminal_observed, false);
  const rows = readFileSync(resolve(fixture, 'raw/native-input-witnesses.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
  assert.deepEqual(report.native_trace, rows);
  assert.equal(report.captures[4].observation.player.content.gold, 100);
});
test('retained minimal historical golden still has zero actions and incomplete conversion', () => {
  const result = cli(resolve(root, 'components/evidence/tests/fixtures/source_session_v4/bundle'));
  assert.equal(result.status, 0, result.stderr); const report = JSON.parse(result.stdout);
  const historical = JSON.parse(readFileSync(resolve(fixture, '../historical-conversion-result.json'), 'utf8'));
  assert.equal(report.complete, historical.complete); assert.equal(report.actions.length, historical.actions_count);
  assert.equal(report.unresolved_inputs[0].reason, historical.input_reason);
  assert.ok(report.captures.every(c => c.missing.includes('p1_public_basis_decoder_refused_original')));
  assert.ok(report.captures.every(c => c.status === 'unresolved' && c.reason));
});
for (const defect of ['bytes', 'missing-read', 'read-binding']) test(`actual CLI refuses ${defect} even after outer hash repair where applicable`, () => privateBundle(bundle => {
  if (defect === 'bytes') {
    const row = JSON.parse(readFileSync(resolve(bundle, 'raw/public-observations.jsonl'), 'utf8').split('\n')[0]);
    const path = resolve(bundle, 'raw', row.captured_parts.public_basis.payload_ref);
    writeFileSync(path, Buffer.concat([readFileSync(path), Buffer.from(' ')]));
  } else mutate(bundle, defect === 'missing-read'
    ? 'case.mutate_basis(lambda value: value["reads"].pop())'
    : 'case.mutate_basis(lambda value: value["reads"][0]["response"].update(observed_snapshot_id="foreign"))');
  const result = cli(bundle); assert.notEqual(result.status, 0); assert.equal(result.stdout, '');
}));
test('valid integrity cannot promote a foreign native carrier to a semantic action', () => privateBundle(bundle => {
  mutate(bundle, `rows=case.rows("native-input-witnesses.jsonl")
rows[0]["outcome"]["native_facts"]["carriers"][1]["carrier_ref"]="foreign-carrier"
case.write_rows("native-input-witnesses.jsonl",rows)
case.reseal()`);
  const result = cli(bundle); assert.equal(result.status, 0, result.stderr); const report = JSON.parse(result.stdout);
  assert.equal(report.complete, false); assert.deepEqual(report.actions, []);
  assert.equal(report.unresolved_inputs.length, 1); assert.ok(report.unresolved_inputs[0].reason);
}));
