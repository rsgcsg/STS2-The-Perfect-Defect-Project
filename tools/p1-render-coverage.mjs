#!/usr/bin/env node
import { readFileSync, writeFileSync, renameSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { coverageHtml } from './p1-cross-host-coverage.mjs';
const args = process.argv.slice(2);
const opt = name => { const index = args.indexOf(name); if (index < 0 || !args[index + 1]) throw new Error(`missing_${name}`); return args[index + 1]; };
const directory = resolve(opt('--directory')), out = resolve(opt('--out'));
const reports = [];
const ledger = JSON.parse(readFileSync(resolve(opt('--ledger')), 'utf8'));
if (ledger.schema !== 'spireagent/managed-replay-view-1' || !ledger.jobs) throw new Error('diagnostic_owner_view_required');
for (const [identity, job] of Object.entries(ledger.jobs)) {
  if (!/^[a-f0-9]{64}$/.test(identity)) throw new Error('diagnostic_source_identity_required');
  let report = {};
  try { report = JSON.parse(readFileSync(join(directory, identity, 'replay/report.json'), 'utf8')); }
  catch (error) {
    if (error.code === 'ENOENT') report = {};
    else if (error instanceof SyntaxError) report = { report_read_error: 'incomplete_or_invalid_report_json' };
    else throw error;
  }
  reports.push({ ...report, diagnostic_id: identity, status: job.status,
    source_session_id: job.session_id, storage_status: job.storage_status, storage_receipt: job.storage_receipt, unknown_delivery: Object.hasOwn(job, "unknown_delivery") ? job.unknown_delivery : report.unknown_delivery, error: report.error ?? job.error ?? job.disposition });
}
writeFileSync(out + '.pending', coverageHtml(reports));
renameSync(out + '.pending', out);
