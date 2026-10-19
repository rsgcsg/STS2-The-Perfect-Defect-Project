import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm, realpath } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const parser = pathToFileURL(join(root, 'components/host-runtime/tools/reference-pe-driver.mjs')).href;
// Exercise the real suite child-process/NDJSON seam and the production argument
// parser. The fixture never creates a Host; bootstrap transfer intentionally fails.
for (const closeFails of [false, true]) test(`Reference suite retains original identity and ${closeFails ? 'unconfirmed' : 'known'} close`, async () => {
  const dir = await realpath(await mkdtemp(join(tmpdir(), 'p1-suite-reference-')));
  try {
    const W = join(dir, 'workspace'), state = join(dir, 'state'), game = join(dir, 'game path');
    for (const p of ['components/policy-runtime/dist','components/host-runtime/tools', 'node_modules/@rsgcsg/sts2-connector-client/dist'])
      await mkdir(join(W, p), {recursive:true});
    await mkdir(state); await mkdir(game);
    await writeFile(join(W,'package.json'), '{"type":"module"}');
    await writeFile(join(W,'components/policy-runtime/dist/managed.js'), 'export class ManagedServicePolicyClient {}');
    await writeFile(join(W,'components/policy-runtime/dist/global-program.js'), 'export function createGlobalProgramManifest() {}');
    await writeFile(join(W,'components/policy-runtime/dist/global-transport.js'), 'export class ReferenceGlobalTransport {}');
    await writeFile(join(W,'node_modules/@rsgcsg/sts2-connector-client/package.json'), '{"type":"module"}');
    await writeFile(join(W,'node_modules/@rsgcsg/sts2-connector-client/dist/index.js'), 'export class PlayerEnvironmentRestClient {}');
    await mkdir(join(W,'components/host-runtime/src'));
    const baseline = pathToFileURL(join(root,'components/host-runtime/src/native-baseline.mjs')).href;
    await writeFile(join(W,'components/host-runtime/src/native-baseline.mjs'), `export {validateInitialProgress} from ${JSON.stringify(baseline)};`);
    const tuple = {game_version:'fixture', host_artifact_sha256:'a'.repeat(64)};
    const identity = {host:{runtime_instance_id:'runtime-fixture'}, profile:{template_payload_sha256:'b'.repeat(64)},
      episode_provenance:{verdict:'provenance_pass',actual_seed:'TEST'}, marker:'original-identity'};
    await writeFile(join(W,'components/host-runtime/tools/reference-pe-driver.mjs'), `
import readline from 'node:readline';
import {writeFileSync} from 'node:fs';
import {referenceDriverOptions} from ${JSON.stringify(parser)};
const options=referenceDriverOptions(process.argv.slice(2),{packageRoot:${JSON.stringify(join(root,'components/host-runtime'))}});
writeFileSync(${JSON.stringify(join(dir,'parsed.json'))},JSON.stringify({tuple:options.sessionOptions.expectedInstalledTuple,game:options.sessionOptions.installation.game_dir}));
const write=x=>process.stdout.write(JSON.stringify(x)+'\\n');
write({type:'ready'});
for await(const line of readline.createInterface({input:process.stdin})) {
 const req=JSON.parse(line), id={request_id:req.request_id};
 if(req.command==='episode_identity') write({...id,type:'episode_identity',identity:${JSON.stringify(identity)}});
 else if(req.command==='release_bootstrap') write({...id,type:'error',code:'fixture',message:'intentional_no_game_fixture'});
 else if(req.command==='close') write(${closeFails ? "{...id,type:'error',code:'fixture_close',message:'original_close_failed'}" : "{...id,type:'closed',exit:{code:0,signal:null,forced:false}}"});
 else write({...id,type:'reset'});
}`);
    const result = spawnSync(process.execPath, [join(root,'tools/p1-suite.mjs'), '--worktree', W,
      '--host','reference','--local-root',state,'--game-dir',game,'--expected-installed-tuple',JSON.stringify(tuple),
      '--seeds','TEST','--agent','node unused.mjs','--out',join(dir,'out')], {encoding:'utf8',timeout:10000});
    assert.equal(result.status,0,result.stderr);
    assert.doesNotMatch(result.stdout,/driver_exited/,result.stdout);
    const parsed=JSON.parse(await readFile(join(dir,'parsed.json'),'utf8'));
    assert.deepEqual(parsed.tuple,tuple); assert.equal(parsed.game,game);
    const ep=join(dir,'out/reference-0001-TEST');
    assert.deepEqual(JSON.parse(await readFile(join(ep,'reference-identity.json'),'utf8')),identity);
    const receipt=JSON.parse(await readFile(join(ep,'reference-close.json'),'utf8'));
    if(closeFails) { assert.equal(receipt.status,'unconfirmed'); assert.match(receipt.error,/original_close_failed/); }
    else assert.deepEqual(receipt.exit,{code:0,signal:null,forced:false});
    const report=JSON.parse(await readFile(join(dir,'out/report.json'),'utf8'));
    assert.deepEqual(report.rows[0].reference_close,receipt);
    assert.match(report.rows[0].error,/intentional_no_game_fixture/);
    assert.equal(report.summary.terminal,0);
  } finally { await rm(dir,{recursive:true,force:true}); }
});
