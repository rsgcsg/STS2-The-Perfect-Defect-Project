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
  fs.chmodSync(file,0o444);
  verify(directory,id);
  assert.throws(() => verify(directory,{...id,key:'another'}), /identity/);
  fs.chmodSync(file,0o644);
  assert.throws(() => verify(directory,id), /read-only|bytes changed/);
  fs.writeFileSync(file,'tampered');
  fs.chmodSync(file,0o444);
  assert.throws(() => verify(directory,id), /bytes changed/);
  fs.chmodSync(file,0o644);
  fs.writeFileSync(file,'locked bytes');
  fs.chmodSync(file,0o444);
  fs.writeFileSync(path.join(directory,'deps','extra'),'unlisted');
  fs.chmodSync(path.join(directory,'deps','extra'),0o444);
  assert.throws(() => verify(directory,id), /bytes changed/);
});

test('workspace and bin links are rebound to this checkout; dependency directories stay shared', async t => {
  const {attachModules} = await import('./shared-env.mjs');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(),'spire-links-'));
  t.after(() => fs.rmSync(directory, {recursive:true, force:true}));
  const shared=path.join(directory,'shared'), local=path.join(directory,'local');
  fs.mkdirSync(path.join(shared,'node_modules/.bin'),{recursive:true});
  fs.mkdirSync(path.join(shared,'node_modules/dependency'));
  fs.writeFileSync(path.join(shared,'node_modules/dependency/file'),'immutable');
  fs.mkdirSync(path.join(shared,'node_modules/@scope'));
  // Workspace target intentionally absent from the dependency skeleton.
  fs.symlinkSync('../../components/workspace',path.join(shared,'node_modules/@scope/workspace'),process.platform==='win32'?'junction':undefined);
  fs.symlinkSync('../@scope/workspace/cli.mjs',path.join(shared,'node_modules/.bin/command'));
  fs.writeFileSync(path.join(shared,'node_modules/.bin/command.cmd'),'@echo fixture');
  attachModules(path.join(shared,'node_modules'),path.join(local,'node_modules'),shared,local);
  fs.unlinkSync(path.join(local,'node_modules/dependency/file'));
  attachModules(path.join(shared,'node_modules'),path.join(local,'node_modules'),shared,local);
  assert.equal(fs.readlinkSync(path.join(local,'node_modules/@scope/workspace')),path.join(local,'components/workspace'));
  assert.equal(fs.statSync(path.join(local,'node_modules/dependency/file')).ino,fs.statSync(path.join(shared,'node_modules/dependency/file')).ino);
  assert.equal(fs.readFileSync(path.join(local,'node_modules/.bin/command.cmd'),'utf8'),'@echo fixture');
  assert.equal(fs.readlinkSync(path.join(local,'node_modules/.bin/command')),path.join(local,'node_modules/@scope/workspace/cli.mjs'));
});

test('shared and private commands both resolve the checkout interpreter before ambient PATH', async () => {
  const {executionEnvironment} = await import('./shared-env.mjs');
  const checkout = path.resolve('fixture-source');
  for (const shared of [true,false]) {
    const env=executionEnvironment({checkout,shared,environment:{PATH:'ambient',PYTHONPATH:'hostile',UV_PROJECT_ENVIRONMENT:'elsewhere',UV_NO_SYNC:'1'}});
    assert.equal(env.PYTHON,path.join(checkout,'python/.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python'));
    assert.equal(env.PATH.split(path.delimiter)[0],path.dirname(env.PYTHON));
    assert.equal(env.UV_PROJECT_ENVIRONMENT,path.join(checkout,'python/.venv'));
    assert.equal(env.UV_NO_SYNC,shared?'1':'0');
    assert.equal(env.PYTHONPATH.split(path.delimiter)[0],path.join(checkout,'python'));
  }
});

test('source command edits reuse dependencies, while package declarations and locks invalidate the key', async t => {
  const {identity}=await import('./shared-env.mjs');
  const {spawnSync}=await import('node:child_process');
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'spire-env-key-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  fs.writeFileSync(path.join(directory,'package.json'),JSON.stringify({name:'fixture',scripts:{check:'old'},dependencies:{example:'1'}}));
  fs.writeFileSync(path.join(directory,'package-lock.json'),'{}');
  for (const args of [['init','-q'],['add','package.json','package-lock.json']]) {
    const result=spawnSync('git',args,{cwd:directory,encoding:'utf8'}); assert.equal(result.status,0,result.stderr);
  }
  const original=identity(directory,{python:'fixture',version:'3.11.fixture'}).key;
  fs.writeFileSync(path.join(directory,'package.json'),JSON.stringify({name:'fixture',scripts:{check:'new'},dependencies:{example:'1'}}));
  assert.equal(identity(directory,{python:'fixture',version:'3.11.fixture'}).key,original);
  fs.writeFileSync(path.join(directory,'package.json'),JSON.stringify({name:'fixture',scripts:{check:'new'},dependencies:{example:'2'}}));
  assert.notEqual(identity(directory,{python:'fixture',version:'3.11.fixture'}).key,original);
  fs.writeFileSync(path.join(directory,'package.json'),JSON.stringify({name:'fixture',scripts:{check:'old'},dependencies:{example:'1'}}));
  fs.writeFileSync(path.join(directory,'package-lock.json'),'{"changed":true}');
  assert.notEqual(identity(directory,{python:'fixture',version:'3.11.fixture'}).key,original);
});

test('read-only shared bytes get local writable directories and retain closed-copy cleanup', async t => {
  const {linkDependencyTree}=await import('./shared-env.mjs');
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'spire-readonly-view-'));
  const shared=path.join(directory,'shared'), local=path.join(directory,'local'), installed=path.join(directory,'installed');
  fs.mkdirSync(shared);fs.writeFileSync(path.join(shared,'LICENSE'),'immutable bytes');
  fs.chmodSync(path.join(shared,'LICENSE'),0o444);fs.chmodSync(shared,0o555);
  t.after(()=>{fs.chmodSync(shared,0o755);fs.rmSync(directory,{recursive:true,force:true});});
  linkDependencyTree(shared,local);
  assert.ok(fs.statSync(local).mode & 0o200);
  assert.equal(fs.statSync(path.join(local,'LICENSE')).ino,fs.statSync(path.join(shared,'LICENSE')).ino);
  fs.cpSync(local,installed,{recursive:true,dereference:true});
  fs.rmSync(installed,{recursive:true});
  assert.equal(fs.readFileSync(path.join(shared,'LICENSE'),'utf8'),'immutable bytes');
  if(process.platform!=='win32')assert.equal(fs.statSync(path.join(shared,'LICENSE')).mode & 0o222,0);
});
