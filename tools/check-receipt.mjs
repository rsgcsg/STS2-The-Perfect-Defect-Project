// A bounded reuse of an executed GitHub CI result, never a copied commit status.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';

export const receiptName = 'portable-execution-receipt';
export function eligibleEvent(event, ref, base, branch) {
  return (event === 'push' && ['refs/heads/develop', 'refs/heads/main'].includes(ref)) ||
    (event === 'pull_request' && base === 'main' && branch?.startsWith('release/'));
}
export function validReceipt(receipt, run, current, now = Date.now()) {
  const age = now - Date.parse(run.created_at);
  return Boolean(["full", "python"].includes(current.scope) && receipt && run.status === 'completed' && run.conclusion === 'success' &&
    run.head_repository?.full_name === current.repository &&
    ['push', 'pull_request', 'workflow_dispatch', 'schedule'].includes(run.event) &&
    Number.isFinite(age) && age >= 0 && age <= 7 * 86400_000 &&
    receipt.schema === 'spireagent/ci-execution-1' && receipt.repository === current.repository &&
    receipt.run_id === String(run.id) && receipt.run_attempt === String(run.run_attempt) &&
    receipt.executed === true && /^[0-9a-f]{40}$/.test(receipt.checkout) &&
    receipt.tree === current.tree && receipt.workflow === current.workflow &&
    ['full', 'python'].includes(receipt.scope) &&
    (receipt.scope === 'full' || current.scope === 'python') &&
    receipt.results?.plan === 'success' && receipt.results?.docs === 'skipped' &&
    receipt.results?.linux === 'success');
}

// API data is read only. Download redirects never receive the GitHub credential.
async function api(repository, suffix, token) {
  const response = await fetch(`https://api.github.com/repos/${repository}/${suffix}`, {
    headers: {Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json'},
    signal: AbortSignal.timeout(10_000), redirect: 'error',
  });
  if (!response.ok) throw new Error('ci_receipt_api_unavailable');
  return response.json();
}
async function readArtifact(repository, artifact, token) {
  if (artifact.expired || artifact.size_in_bytes > 100_000 ||
      !/^sha256:[0-9a-f]{64}$/.test(artifact.digest ?? '')) throw new Error('invalid_artifact');
  const response = await fetch(`https://api.github.com/repos/${repository}/actions/artifacts/${artifact.id}/zip`, {
    headers: {Authorization: `Bearer ${token}`}, redirect: 'manual', signal: AbortSignal.timeout(10_000),
  });
  if (response.status !== 302) throw new Error('artifact_download_unavailable');
  const url = new URL(response.headers.get('location'));
  if (url.protocol !== 'https:') throw new Error('invalid_artifact_redirect');
  const archive = await fetch(url, {redirect: 'error', signal: AbortSignal.timeout(10_000)});
  if (!archive.ok) throw new Error('artifact_download_failed');
  const bytes = Buffer.from(await archive.arrayBuffer());
  if (bytes.length > 100_000 || `sha256:${crypto.createHash('sha256').update(bytes).digest('hex')}` !== artifact.digest) throw new Error('artifact_digest_mismatch');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ci-receipt-'));
  try {
    const zip = path.join(directory, 'receipt.zip'); fs.writeFileSync(zip, bytes);
    const raw = execFileSync('unzip', ['-p', zip, 'receipt.json'], {encoding: 'utf8', maxBuffer: 100_000, timeout: 5000});
    return JSON.parse(raw);
  } finally { fs.rmSync(directory, {recursive: true, force: true}); }
}
export async function findReceipt(current, {token, git, runId, request = api, read = readArtifact}) {
  if (!token || !/^[\w.-]+\/[\w.-]+$/.test(current.repository ?? '')) return null;
  try {
    const data = await request(current.repository, 'actions/workflows/ci.yml/runs?status=success&per_page=20', token);
    const deadline = Date.now() + 30_000;
    for (const run of data.workflow_runs) {
      if (Date.now() > deadline) break;
      if (Date.now() - Date.parse(run.created_at) > 7 * 86400_000) continue;
      if (String(run.id) === String(runId) || run.head_repository?.full_name !== current.repository ||
          !/^[0-9a-f]{40}$/.test(run.head_sha)) continue;
      // Cheap content filter before downloading a receipt. Missing history falls back to execution.
      let tree; try { tree = git('rev-parse', `${run.head_sha}^{tree}`).trim(); } catch { continue; }
      if (tree !== current.tree) continue;
      const artifacts = await request(current.repository, `actions/runs/${run.id}/artifacts?per_page=100`, token);
      const artifact = artifacts.artifacts.find(a => a.name === `${receiptName}-${run.run_attempt}` && !a.expired);
      if (!artifact || artifact.workflow_run?.head_sha !== run.head_sha) continue;
      const receipt = await read(current.repository, artifact, token);
      if (validReceipt(receipt, run, current)) return {run_id: String(run.id), checkout: receipt.checkout,
        scope: receipt.scope, url: `https://github.com/${current.repository}/actions/runs/${run.id}`};
    }
  } catch { /* Missing/expired/inaccessible evidence selects real execution. */ }
  return null;
}
