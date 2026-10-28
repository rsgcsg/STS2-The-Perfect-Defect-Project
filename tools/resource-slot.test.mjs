import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {acquire, release, requireSlot} from './resource-slot.mjs';

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'spire-slot-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  return root;
}
const request = () => ({card: 'E2', until: new Date(Date.now()+60000).toISOString(), pid: process.pid});
test('one owner across checkouts; wrong token cannot launch or release', t => {
  const root = fixture(t), [record] = acquire(request(), root);
  assert.throws(() => acquire({...request(), card: 'other'}, root), /occupied by E2/);
  assert.throws(() => requireSlot('managed', 'wrong', root), /lock required/);
  assert.equal(requireSlot('managed', record.token, root).card, 'E2');
  assert.throws(() => release(['managed'], 'wrong', root), /owner token/);
  release(['managed'], record.token, root);
  assert.throws(() => requireSlot('managed', record.token, root), /lock required/);
  assert.match(fs.readFileSync(path.join(root,'GAME-SLOT.md'),'utf8'), /开始.*\n.*结束/u);
});
test('dead owner AND deadline required; live owner never expires', t => {
  const root = fixture(t), [record] = acquire(request(), root);
  const file = path.join(root,'managed.json');
  fs.writeFileSync(file, JSON.stringify({...record, until: '2000-01-01T00:00:00Z'}));
  assert.throws(() => acquire(request(), root), /occupied/);
  fs.writeFileSync(file, JSON.stringify({...record, pid: 2147483647}));
  assert.throws(() => acquire(request(), root), /occupied/);
  fs.writeFileSync(file, JSON.stringify({...record, pid: 2147483647, until: '2000-01-01T00:00:00Z'}));
  acquire(request(), root);
  assert.match(fs.readFileSync(path.join(root,'GAME-SLOT.md'),'utf8'), /过期锁接管/u);
});
test('two-resource acquisition is all-or-nothing on conflict', t => {
  const root = fixture(t);
  acquire({...request(), resources:['cpu']}, root);
  assert.throws(() => acquire({...request(), resources:['managed','cpu']}, root), /occupied/);
  assert.equal(fs.existsSync(path.join(root,'managed.json')), false);
});
test('journal failure never grants reservation', t => {
  const root = fixture(t);
  fs.writeFileSync(path.join(root,'config.json'), JSON.stringify({journal:path.join(root,'missing','journal')}));
  assert.throws(() => acquire(request(), root));
  assert.equal(fs.existsSync(path.join(root,'managed.json')), false);
});

test('CLI wrapper holds a live reservation for its child and releases after failure', async t => {
  const {spawnSync} = await import('node:child_process');
  const root = fixture(t);
  const child = spawnSync(process.execPath, ['tools/resource-slot.mjs','run','--card','wrapper',
    '--until',new Date(Date.now()+60000).toISOString(),'--',process.execPath,'--input-type=module','-e',
    `import {requireSlot} from './tools/resource-slot.mjs'; requireSlot(); process.exitCode=7;`],
    {encoding:'utf8',env:{...process.env,SPIREAGENT_SLOT_ROOT:root}});
  assert.equal(child.status,7,child.stderr);
  assert.equal(fs.existsSync(path.join(root,'managed.json')),false);
  assert.match(fs.readFileSync(path.join(root,'GAME-SLOT.md'),'utf8'), /开始.*\n.*结束/u);
});

test('user game appearing during CPU setup stops only the owned process group', async t => {
  const {runReservedCommand} = await import('./resource-slot.mjs');
  const started = Date.now();
  const outcome = await runReservedCommand([process.execPath,'-e','setInterval(()=>{},1000)'],
    {resources:['managed','cpu'],token:'fixture',gamePresent:()=>true,intervalMs:20});
  assert.equal(outcome.interruptedForGame,true);
  assert.equal(outcome.signal,'SIGTERM');
  assert.ok(Date.now()-started < 5000);
});
