/** Build-time package composition shared by standalone Platform components.
 * Contains no runtime authority and never reads a previously copied SDK. */
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
export const SDK_NAME = '@rsgcsg/sts2-connector-client';
export const SDK_SOURCE_DEPENDENCY = 'file:../connector/sdk/typescript';
export function npmPackageCommand(args, cwd) {
  const npm = process.env.npm_execpath;
  const result = npm ? spawnSync(process.execPath,[npm,...args],{cwd,encoding:'utf8'})
    : spawnSync(process.platform==='win32'?'npm.cmd':'npm',args,{cwd,encoding:'utf8',shell:process.platform==='win32'});
  if(result.error)throw result.error;
  if(result.status!==0)throw Error(result.stderr||result.stdout);
  return result.stdout;
}
export function packageFileTreeSha256(root) {
  const hash=createHash('sha256');
  function visit(directory,prefix=''){
    for(const e of readdirSync(directory,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))){
      const relative=prefix?`${prefix}/${e.name}`:e.name,full=path.join(directory,e.name);
      if(e.isDirectory())visit(full,relative);
      else if(e.isFile())hash.update(relative).update('\0').update(readFileSync(full)).update('\0');
      else throw Error(`Unsupported package entry: ${relative}`);
    }
  }
  visit(root);return hash.digest('hex');
}
export function bundleConnectorSdk(stage,{repositoryRoot,componentPackage,componentLock,sdkIdentity}) {
  const sdkRoot=path.join(repositoryRoot,'components/connector/sdk/typescript');
  const source=JSON.parse(readFileSync(path.join(sdkRoot,'package.json'),'utf8'));
  const lock=JSON.parse(readFileSync(path.join(sdkRoot,'package-lock.json'),'utf8'));
  if(componentPackage.dependencies?.[SDK_NAME]!==SDK_SOURCE_DEPENDENCY
    ||componentLock.packages['']?.dependencies?.[SDK_NAME]!==SDK_SOURCE_DEPENDENCY
    ||componentLock.packages[`node_modules/${SDK_NAME}`]?.resolved!=='../connector/sdk/typescript'
    ||componentLock.packages[`node_modules/${SDK_NAME}`]?.link!==true
    ||componentLock.packages['../connector/sdk/typescript']?.version!==source.version
    ||source.name!==SDK_NAME||lock.packages['']?.version!==source.version||source.dependencies?.zod!=='^3.25.76'||Object.keys(source.dependencies).length!==1)
    throw Error('Exact workspace Connector SDK dependency and lock required');
  const zod=lock.packages['node_modules/zod'];
  if(zod?.version!=='3.25.76'||zod.resolved!=='https://registry.npmjs.org/zod/-/zod-3.25.76.tgz'||!/^sha512-/u.test(zod.integrity??''))
    throw Error('Exact locked registry Zod required');
  npmPackageCommand(['run','build'],sdkRoot);
  const scratch=mkdtempSync(path.join(os.tmpdir(),'sts2-sdk-bundle-'));
  try{
    const temporary={name:'sts2-sdk-transitive-lock-check',version:'1.0.0',private:true,dependencies:{zod:zod.version}};
    writeFileSync(path.join(scratch,'package.json'),JSON.stringify(temporary));
    writeFileSync(path.join(scratch,'package-lock.json'),JSON.stringify({name:temporary.name,version:temporary.version,lockfileVersion:3,requires:true,packages:{'':temporary,'node_modules/zod':zod}}));
    npmPackageCommand(['ci','--ignore-scripts','--no-audit','--no-fund'],scratch);
    const sdkStage=path.join(stage,'node_modules',...SDK_NAME.split('/')),zodStage=path.join(stage,'node_modules/zod');
    mkdirSync(sdkStage,{recursive:true});cpSync(path.join(sdkRoot,'package.json'),path.join(sdkStage,'package.json'));
    cpSync(path.join(sdkRoot,'dist'),path.join(sdkStage,'dist'),{recursive:true});
    cpSync(path.join(scratch,'node_modules/zod'),zodStage,{recursive:true});
    const dependencies={[SDK_NAME]:source.version,zod:zod.version},bundleDependencies=[SDK_NAME,'zod'];
    return {dependencies,bundleDependencies,packages:{
      [`node_modules/${SDK_NAME}`]:{version:source.version,inBundle:true,license:source.license,dependencies:source.dependencies,engines:source.engines},
      'node_modules/zod':{version:zod.version,inBundle:true,license:zod.license,funding:zod.funding}},
      identity:{mode:'bundled_source_candidate',name:SDK_NAME,version:source.version,
        source_revision:sdkIdentity.source_revision,component_tree_revision:sdkIdentity.component_tree_revision,
        component_source_digest_sha256:sdkIdentity.component_source_digest_sha256,public_contract_digest_sha256:sdkIdentity.public_contract_digest_sha256,
        bundle_sha256:packageFileTreeSha256(sdkStage),transitive_zod:{version:zod.version,url:zod.resolved,integrity:zod.integrity,bundle_sha256:packageFileTreeSha256(zodStage)}}};
  }finally{rmSync(scratch,{recursive:true,force:true});}
}
