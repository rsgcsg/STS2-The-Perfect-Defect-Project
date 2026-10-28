import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {inventory, verify} from './shared-env.mjs';

test('immutable dependency verification catches edits, extra files and changed identity', t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(),'spire-env-'));
  t.after(() => fs.rmSync(directory, {recursive:true, force:true}));
  fs.mkdirSync(path.join(directory,'deps'));
  const file = path.join(directory,'deps','module');
  fs.writeFileSync(file,'locked bytes');
  const id = {key:'fixture',inputs:{platform:process.platform}};
  fs.writeFileSync(path.join(directory,'receipt.json'),JSON.stringify({...id,inventory:inventory(path.join(directory,'deps'))}));
  verify(directory,id);
  assert.throws(() => verify(directory,{...id,key:'another'}), /identity/);
  fs.writeFileSync(file,'tampered');
  assert.throws(() => verify(directory,id), /bytes changed/);
  fs.writeFileSync(file,'locked bytes');
  fs.writeFileSync(path.join(directory,'deps','extra'),'unlisted');
  assert.throws(() => verify(directory,id), /bytes changed/);
});

test('workspace and bin links are rebound to this checkout; dependency directories stay shared', async t => {
  const {attachModules} = await import('./shared-env.mjs');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(),'spire-links-'));
  t.after(() => fs.rmSync(directory, {recursive:true, force:true}));
  const shared=path.join(directory,'shared'), local=path.join(directory,'local');
  fs.mkdirSync(path.join(shared,'node_modules/.bin'),{recursive:true});
  fs.mkdirSync(path.join(shared,'node_modules/dependency'));
  fs.mkdirSync(path.join(shared,'node_modules/@scope'));
  // Workspace target intentionally absent from the dependency skeleton.
  fs.symlinkSync('../../components/workspace',path.join(shared,'node_modules/@scope/workspace'),process.platform==='win32'?'junction':undefined);
  fs.symlinkSync('../@scope/workspace/cli.mjs',path.join(shared,'node_modules/.bin/command'));
  fs.writeFileSync(path.join(shared,'node_modules/.bin/command.cmd'),'@echo fixture');
  attachModules(path.join(shared,'node_modules'),path.join(local,'node_modules'),shared,local);
  fs.unlinkSync(path.join(local,'node_modules/dependency'));
  attachModules(path.join(shared,'node_modules'),path.join(local,'node_modules'),shared,local);
  assert.equal(fs.readlinkSync(path.join(local,'node_modules/@scope/workspace')),path.join(local,'components/workspace'));
  assert.equal(fs.readlinkSync(path.join(local,'node_modules/dependency')),path.join(shared,'node_modules/dependency'));
  assert.equal(fs.readFileSync(path.join(local,'node_modules/.bin/command.cmd'),'utf8'),'@echo fixture');
  assert.equal(fs.readlinkSync(path.join(local,'node_modules/.bin/command')),path.join(local,'node_modules/@scope/workspace/cli.mjs'));
});
