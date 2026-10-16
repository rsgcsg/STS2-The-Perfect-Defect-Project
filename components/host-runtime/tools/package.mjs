#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { cpSync, mkdirSync, mkdtempSync, lstatSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { readIdentityReport } from '../../../tools/component-identity.mjs';
import { bundleConnectorSdk, npmPackageCommand } from '../../../tools/bundle-connector-sdk.mjs';
export const componentRoot=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const repositoryRoot=path.resolve(componentRoot,'../..');
/** Portable artifact with exact bundled SDK/Zod; source dependency never escapes. */
export function packageHostRuntime(outputDirectory,{allowDirty=false}={}){
  const source=JSON.parse(readFileSync(path.join(componentRoot,'package.json'),'utf8'));
  const lock=JSON.parse(readFileSync(path.join(componentRoot,'package-lock.json'),'utf8'));
  if(Object.keys(source.dependencies??{}).some(name=>name!=='@rsgcsg/sts2-connector-client'))throw Error('Unaccounted Host production dependency');
  const identities=readIdentityReport(repositoryRoot).components,identity=identities['host-runtime'];
  if(!allowDirty&&(identity.source_worktree_status!=='clean'||identities.connector.source_worktree_status!=='clean'))
    throw Error('Commit Host Runtime and Connector SDK source before external packaging');
  const stage=mkdtempSync(path.join(os.tmpdir(),'sts2-host-package-stage-'));
  try{
    // Preserve the existing npm file selection and executable modes.
    const inventory=JSON.parse(npmPackageCommand(['pack','--dry-run','--ignore-scripts','--json'],componentRoot))[0];
    for(const {path:relative}of inventory.files){
      if(relative==='package.json')continue;
      if(path.isAbsolute(relative)||relative.split(/[\\/]/u).includes('..'))throw Error('Unsafe source package path');
      const original=path.join(componentRoot,relative),stat=lstatSync(original);if(!stat.isFile()||stat.isSymbolicLink())throw Error('Source package file must be ordinary');
      const target=path.join(stage,relative);mkdirSync(path.dirname(target),{recursive:true});cpSync(path.join(componentRoot,relative),target);
    }
    const sdk=bundleConnectorSdk(stage,{repositoryRoot,componentPackage:source,componentLock:lock,sdkIdentity:identities.connector});
    const packed={...source,dependencies:sdk.dependencies,bundleDependencies:sdk.bundleDependencies,
      files:[...source.files,'LICENSE','package-identity.json','npm-shrinkwrap.json']};
    delete packed.scripts;delete packed.devDependencies;
    const shrinkwrap={name:source.name,version:source.version,lockfileVersion:3,requires:true,packages:{
      '':{name:source.name,version:source.version,license:source.license,dependencies:sdk.dependencies,bundleDependencies:sdk.bundleDependencies,bin:source.bin,engines:source.engines},...sdk.packages}};
    const packageIdentity={schema:'sts2.host-runtime/package-identity-1',...identity,connector_sdk:sdk.identity};
    cpSync(path.join(repositoryRoot,'LICENSE'),path.join(stage,'LICENSE'));
    for(const[name,value]of Object.entries({'package.json':packed,'npm-shrinkwrap.json':shrinkwrap,'package-identity.json':packageIdentity}))
      writeFileSync(path.join(stage,name),JSON.stringify(value,null,2)+'\n');
    mkdirSync(outputDirectory,{recursive:true});
    const report=JSON.parse(npmPackageCommand(['pack','--ignore-scripts','--json','--pack-destination',outputDirectory],stage))[0];
    const tarball=path.join(outputDirectory,report.filename),sha256=createHash('sha256').update(readFileSync(tarball)).digest('hex');
    const result={schema:'sts2.host-runtime/package-report-1',name:report.name,version:report.version,filename:report.filename,sha256,
      integrity:report.integrity,size:report.size,unpackedSize:report.unpackedSize,identity:packageIdentity,files:report.files};
    writeFileSync(path.join(outputDirectory,'host-runtime-package.json'),JSON.stringify(result,null,2)+'\n');
    writeFileSync(path.join(outputDirectory,'checksums.sha256'),`${sha256}  ${report.filename}\n`);
    return{...result,tarball};
  }finally{rmSync(stage,{recursive:true,force:true});}
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
  const args=process.argv.slice(2);if(args.length!==2||args[0]!=='--output')throw Error('Usage: npm run package -- --output /absolute/output-directory');
  console.log(JSON.stringify(packageHostRuntime(path.resolve(args[1])),null,2));
}
