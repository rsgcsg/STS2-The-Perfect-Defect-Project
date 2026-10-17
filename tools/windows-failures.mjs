// One checklist from original Windows logs; never rerun tests or change their verdicts.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

export function failureNames(log) {
  const names = new Set();
  for (const raw of log.split('\n')) {
    const line = raw.replace(/\x1b\[[0-9;]*m/gu, '').replace(/^\d{4}-\d\d-\d\dT\S+\s+/u, '');
    for (const pattern of [
      /^FAILED\s+(\S+::\S+)/u, /^\s*not ok \d+ - (.+)/u,
      /^\s*FAIL\s+(.+)/u, /^\s*Failed\s+(.+?)\s+\[/u,
    ]) {
      const match = pattern.exec(line);
      if (match) names.add(match[1].trim());
    }
    if (line.includes('Windows fatal exception: access violation')) names.add('Python process: access violation');
    if (line.includes('"stage":"workspace_gate"')) {
      const stage = JSON.parse(line.slice(line.indexOf('{')));
      if (['failed', 'cancelled', 'blocked'].includes(stage.status)) names.add(`gate: ${stage.id} (${stage.status})`);
    }
  }
  return [...names].sort();
}

export function updateChecklist(previous, failures, {run, date}) {
  const entries = new Map(previous.map(entry => [entry.test, {...entry}]));
  for (const test of [...new Set(failures)]) {
    const entry = entries.get(test) ?? {test, first_seen: date, runs: []};
    if (!entry.runs.includes(run)) entry.runs = [...entry.runs, run];
    entry.first_seen = [entry.first_seen, date].sort()[0];
    entry.last_seen = [entry.last_seen ?? date, date].sort().at(-1);
    entries.set(test, entry);
  }
  return [...entries.values()].sort((a, b) => a.test.localeCompare(b.test));
}

export function mergeChecklists(left, right) {
  const entries = new Map(left.map(entry => [entry.test, {...entry}]));
  for (const entry of right) {
    const earlier = entries.get(entry.test);
    entries.set(entry.test, earlier ? {test: entry.test,
      first_seen: [earlier.first_seen, entry.first_seen].sort()[0],
      last_seen: [earlier.last_seen, entry.last_seen].sort().at(-1),
      runs: [...new Set([...earlier.runs, ...entry.runs])]} : {...entry});
  }
  return [...entries.values()];
}

async function main() {
  const repository = process.env.GITHUB_REPOSITORY, run = process.env.GITHUB_RUN_ID;
  const gh = args => execFileSync('gh', args, {encoding: 'utf8', maxBuffer: 30_000_000});
  const api = suffix => JSON.parse(gh(['api', `repos/${repository}/${suffix}`]));
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'windows-failures-'));
  try {
    // Use the latest completed checklist in this repository. A missing first history
    // is empty; an inaccessible or malformed existing history is a visible failure.
    const pages = JSON.parse(gh(['api', '--paginate', '--slurp',
      `repos/${repository}/actions/artifacts?name=windows-failures-history&per_page=100`]));
    const artifacts = pages.flatMap(page => page.artifacts)
      .filter(item => !item.expired && String(item.workflow_run.id) !== run)
      .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
    let previous = [];
    const consumed = new Set();
    for (const artifact of artifacts) {
      const id = String(artifact.workflow_run.id);
      if (consumed.has(id)) continue;
      const target = path.join(directory, String(artifact.id));
      gh(['run', 'download', id, '--repo', repository,
        '--name', 'windows-failures-history', '--dir', target]);
      const history = JSON.parse(fs.readFileSync(path.join(target, 'windows-failures.json'), 'utf8'));
      previous = mergeChecklists(previous, history.entries);
      for (const old of history.consumed_runs) consumed.add(old);
      consumed.add(id);
    }
    const info = api(`actions/runs/${run}`);
    const jobs = JSON.parse(gh(['api', '--paginate', `repos/${repository}/actions/runs/${run}/jobs?per_page=100`])).jobs;
    const windows = jobs.filter(job => job.name.startsWith('windows-portability ('));
    if (windows.length !== 2 || windows.some(job => job.status !== 'completed')) throw new Error('Windows job inventory incomplete');
    const failures = [];
    for (const job of windows) {
      if (job.conclusion === 'success') continue;
      const log = gh(['api', '--allow-escape-sequences', `repos/${repository}/actions/jobs/${job.id}/logs`]);
      failures.push(...failureNames(log), `job: ${job.name} (${job.conclusion})`);
    }
    const date = new Intl.DateTimeFormat("en-CA", {timeZone: "Australia/Brisbane",
      year: "numeric", month: "2-digit", day: "2-digit"}).format(new Date(info.created_at));
    const entries = updateChecklist(previous, failures, {run, date});
    const report = {run, checkout: info.head_sha, consumed_runs: [...consumed, run],
      date_basis: 'Australia/Brisbane run creation date', entries};
    const escape = value => String(value).replaceAll('|', '\\|').replaceAll('\n', ' ');
    const markdown = `# Windows failure checklist\n\nOriginal run: ${info.html_url}. No automatic rerun. Prior failures remain listed after a passing day.\n\n` +
      '| Test or failed gate | First seen (Brisbane) | Last seen (Brisbane) | Repeated | Runs |\n|---|---|---|---|---|\n' +
      entries.map(entry => `| ${escape(entry.test)} | ${entry.first_seen} | ${entry.last_seen} | ${entry.runs.length > 1 ? 'yes' : 'no'} | ${entry.runs.length} |`).join('\n') +
      (entries.length ? '\n' : '\nNo failures recorded.\n');
    fs.mkdirSync('.local/ci', {recursive: true});
    fs.writeFileSync('.local/ci/windows-failures.json', JSON.stringify(report, null, 2) + '\n');
    fs.writeFileSync('.local/ci/windows-failures.md', markdown);
    if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, markdown);
  } finally { fs.rmSync(directory, {recursive: true, force: true}); }
}
if (process.argv[1] === fileURLToPath(import.meta.url)) await main();
