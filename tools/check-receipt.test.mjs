import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {eligibleEvent, validReceipt, findReceipt} from './check-receipt.mjs';
const now = Date.UTC(2026, 8, 17);
const current = {repository: 'owner/project', tree: 'a'.repeat(40), workflow: 'b'.repeat(40), scope: 'full'};
const run = {id: 1, run_attempt: 2, status: 'completed', conclusion: 'success',
  event: 'pull_request', head_repository: {full_name: current.repository}, created_at: new Date(now-1000).toISOString()};
const receipt = {schema: 'spireagent/ci-execution-1', executed: true, repository: current.repository,
  run_id: '1', run_attempt: '2', checkout: 'c'.repeat(40), tree: current.tree, workflow: current.workflow,
  scope: 'full', results: {plan:'success', docs:'skipped', linux:'success', windows:'success'}};
test('only protected integration and release promotion can request reuse', () => {
  assert.ok(eligibleEvent('push','refs/heads/develop'));
  assert.ok(eligibleEvent('push','refs/heads/main'));
  assert.ok(eligibleEvent('pull_request','','main','release/batch'));
  for(const args of [['push','refs/heads/topic'],['pull_request','','develop','release/batch'],
    ['pull_request','','main','fix/topic'],['schedule','refs/heads/main'],['workflow_dispatch','refs/heads/main']])
    assert.equal(eligibleEvent(...args),false);
});
test('receipt binds repository, actual checkout content, executed scope and attempt', () => {
  assert.ok(validReceipt(receipt,run,current,now));
  for(const patch of [{schema:'old'}, {executed:false}, {repository:'other/project'}, {run_id:'2'},
    {run_attempt:'1'}, {checkout:'invalid'}, {tree:'d'.repeat(40)}, {workflow:'e'.repeat(40)},
    {scope:'reuse'}, {scope:'python'}, {results:{...receipt.results, windows:'skipped'}},
    {results:{...receipt.results, plan:'failure'}}, {results:{...receipt.results, docs:'success'}}])
    assert.equal(validReceipt({...receipt,...patch},run,current,now),false,JSON.stringify(patch));
  assert.ok(validReceipt({...receipt,scope:'python'},run,{...current,scope:'python'},now));
  assert.ok(validReceipt(receipt,run,{...current,scope:'python'},now));
});
test('failed, cancelled, foreign and stale executions cannot satisfy a promotion', () => {
  for(const patch of [{status:'in_progress'},{conclusion:'cancelled'},{conclusion:'failure'},
    {head_repository:{full_name:'fork/project'}},{event:'pull_request_target'},
    {created_at:'invalid'},{created_at:new Date(now-8*86400_000).toISOString()},
    {created_at:new Date(now+1).toISOString()}])
    assert.equal(validReceipt(receipt,{...run,...patch},current,now),false,JSON.stringify(patch));
});
test('missing GitHub credentials has an execution fallback without network', async () => {
  assert.equal(await findReceipt(current,{token:'',runId:'1'}),null);
});

test('lookup uses only matching executed content and falls back on corrupt or absent artifacts', async () => {
  const liveRun = {...run, head_sha:'d'.repeat(40),created_at:new Date().toISOString()};
  const artifact = {name:'portable-execution-receipt-2',expired:false,workflow_run:{head_sha:liveRun.head_sha}};
  const options = {token:'fixture',runId:'9',git:()=>current.tree,
    request: async (_repo,suffix) => suffix.includes('/artifacts') ? {artifacts:[artifact]} : {workflow_runs:[liveRun]},
    read: async () => receipt};
  const found = await findReceipt(current,options);
  assert.equal(found.run_id,'1');
  assert.equal(found.checkout,receipt.checkout);
  assert.equal(await findReceipt(current,{...options,runId:'1'}),null);
  assert.equal(await findReceipt(current,{...options,git:()=> 'wrong tree'}),null);
  assert.equal(await findReceipt(current,{...options,git:()=> {throw Error('missing ref');}}),null);
  assert.equal(await findReceipt(current,{...options,read:async()=>({...receipt,executed:false})}),null);
  assert.equal(await findReceipt(current,{...options,read:async()=>{throw Error('digest mismatch');}}),null);
  assert.equal(await findReceipt(current,{...options,request:async()=>{throw Error('API unavailable');}}),null);
});

test('rerunning an old run cannot refresh the age of its successful lanes', () => {
  assert.equal(validReceipt(receipt, {...run, created_at:new Date(now-8*86400_000).toISOString(),
    updated_at:new Date(now).toISOString()}, current, now), false);
});


test('a profile-only Git tree change invalidates an otherwise matching executed receipt', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'cost-receipt-'));
  const git = (...args) => execFileSync('git', args, {cwd: root, encoding: 'utf8', stdio: 'pipe'}).trim();
  try {
    git('init'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid');
    const profile = path.join(root, 'pytest-file-costs.json');
    fs.writeFileSync(profile, JSON.stringify({file_cost_ms: {'tests/one.py': 1}}));
    git('add', '.'); git('commit', '-m', 'original profile');
    const tree = git('rev-parse', 'HEAD^{tree}');
    const original = {...receipt, tree};
    assert.equal(validReceipt(original, run, {...current, tree}, now), true);
    fs.writeFileSync(profile, JSON.stringify({file_cost_ms: {'tests/one.py': 2}}));
    git('add', '.'); git('commit', '-m', 'new profile only');
    const next = git('rev-parse', 'HEAD^{tree}');
    assert.notEqual(next, tree);
    assert.equal(validReceipt(original, run, {...current, tree: next}, now), false);
  } finally { fs.rmSync(root, {recursive: true, force: true}); }
});
