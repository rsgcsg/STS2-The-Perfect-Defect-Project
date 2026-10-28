// Immutable third-party dependencies; workspace source/builds always stay local.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {requireSlot} from './resource-slot.mjs';
import {listGameProcesses} from '../components/host-runtime/src/game-processes.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const cache = () => process.env.SPIREAGENT_ENV_ROOT || path.join(os.homedir(), 'Documents/Codex/shared-env');
function run(cmd, args, cwd = root, env = process.env, capture = false) {
  const result = spawnSync(cmd, args, {cwd, env, encoding: 'utf8', stdio: capture ? 'pipe' : 'inherit', shell: process.platform === 'win32'});
  if (result.error || result.status !== 0) throw Error(`${cmd} failed (${result.status}): ${result.error?.message || result.stderr || ''}`);
  return result.stdout?.trim();
}
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
export function identity(directory = root) {
  const tracked = run('git', ['ls-files', '-z'], directory, process.env, true).split('\0');
  const files = tracked.filter(p => /(^|\/)(package-lock\.json|package\.json)$/u.test(p)
    || ['python/uv.lock', 'python/pyproject.toml', 'python/.python-version'].includes(p)).sort();
  const python = run('uv', ['python', 'find', '3.11'], directory, process.env, true);
  const version = run(python, ['-c', 'import sys;print(sys.version)'], directory, process.env, true);
  const inputs = {schema: 1, node: process.version, python: version, platform: process.platform, arch: process.arch,
    files: Object.fromEntries(files.map(p => [p, digest(fs.readFileSync(path.join(directory, p)))]))};
  return {key: digest(JSON.stringify(inputs)), inputs, python};
}
export function inventory(directory, relative = '', requireReadOnly = false) {
  const result = {};
  for (const name of fs.readdirSync(path.join(directory, relative)).sort()) {
    const p = path.join(relative, name), full = path.join(directory, p), stat = fs.lstatSync(full);
    if (requireReadOnly && process.platform !== 'win32' && !stat.isSymbolicLink() && (stat.mode & 0o222))
      throw Error(`shared dependency is no longer read-only: ${p}`);
    if (stat.isSymbolicLink()) result[p] = {link: fs.readlinkSync(full)};
    else if (stat.isDirectory()) { result[p] = {directory: true}; Object.assign(result, inventory(directory, p, requireReadOnly)); }
    else result[p] = {sha256: digest(fs.readFileSync(full)), size: stat.size, executable: stat.mode & 0o111};
  }
  return result;
}
function readOnly(directory, writable = false) {
  for (const name of fs.readdirSync(directory)) {
    const full = path.join(directory, name), stat = fs.lstatSync(full);
    if (stat.isSymbolicLink()) continue;
    if (stat.isDirectory()) { fs.chmodSync(full, writable ? 0o755 : 0o555); readOnly(full, writable); }
    else fs.chmodSync(full, writable ? (stat.mode | 0o200) : (stat.mode & ~0o222));
  }
  fs.chmodSync(directory, writable ? 0o755 : 0o555);
}
export function verify(directory, expected) {
  const receipt = JSON.parse(fs.readFileSync(path.join(directory, 'receipt.json')));
  if (receipt.key !== expected.key || JSON.stringify(receipt.inputs) !== JSON.stringify(expected.inputs)) throw Error('shared dependency identity mismatch');
  if (JSON.stringify(inventory(path.join(directory, 'deps'), '', true)) !== JSON.stringify(receipt.inventory)) throw Error('shared dependency bytes changed');
  return receipt;
}
function idle() {
  requireSlot('managed'); requireSlot('cpu');
  if (listGameProcesses(process.platform, {failClosed: true}).length) throw Error('user game is running');
}
function build(id, directory) {
  idle();
  fs.mkdirSync(cache(), {recursive: true});
  const lock = `${directory}.building`;
  fs.mkdirSync(lock); // Atomic per-identity creation lock; never reuse a partial build.
  try {
    fs.mkdirSync(directory);
    const deps = path.join(directory, 'deps');
    fs.mkdirSync(deps);
    for (const relative of Object.keys(id.inputs.files)) {
      const target = path.join(deps, relative);
      fs.mkdirSync(path.dirname(target), {recursive: true});
      fs.copyFileSync(path.join(root, relative), target);
      if (path.basename(relative) === 'package.json') {
        // npm may prepare file/workspace links even with ignore-scripts. The
        // dependency skeleton has no component source, so it must have no hooks.
        const manifest = JSON.parse(fs.readFileSync(target, 'utf8'));
        delete manifest.scripts;
        fs.writeFileSync(target, JSON.stringify(manifest));
      }
    }
    run('npm', ['ci', '--ignore-scripts'], deps);
    idle();
    run('npm', ['ci', '--prefix', 'python', '--ignore-scripts'], deps);
    idle();
    run('uv', ['sync', '--project', 'python', '--locked', '--all-extras', '--no-install-project'], deps,
      {...process.env, UV_PROJECT_ENVIRONMENT: path.join(deps, 'python/.venv'), UV_NO_SYNC: '0', UV_LINK_MODE: 'copy'});
    // Run third-party install hooks (not workspace prepare/build scripts).
    run('npm', ['rebuild', '--workspaces=false'], deps);
    run('npm', ['rebuild', '--workspaces=false'], path.join(deps, 'python'));
    // npm workspace links refer to the dependency skeleton, never used as source.
    fs.writeFileSync(path.join(directory, 'receipt.json'), JSON.stringify({key: id.key, inputs: id.inputs,
      created: new Date().toISOString(), inventory: inventory(deps)}));
    fs.mkdirSync(path.join(directory, 'users'));
    readOnly(deps); fs.chmodSync(path.join(directory, 'receipt.json'), 0o444);
  } catch (error) {
    if (fs.existsSync(directory)) { readOnly(directory, true); fs.rmSync(directory, {recursive: true}); }
    throw error;
  } finally { fs.rmdirSync(lock); }
}
function mappedLink(target, shared, local) {
  const relative = path.relative(shared, target);
  return relative.startsWith('..') ? target : path.join(local, relative);
}
export function attachModules(source, target, shared, local) {
  fs.mkdirSync(target, {recursive: true});
  if (fs.lstatSync(target).isSymbolicLink()) throw Error(`dependency directory must stay local: ${target}`);
  for (const name of fs.readdirSync(source)) {
    if (name === '.package-lock.json') continue;
    const from = path.join(source, name), to = path.join(target, name);
    if (name.startsWith('@') || name === '.bin') {
      attachModules(from, to, shared, local); continue;
    }
    const stat = fs.lstatSync(from);
    const destination = stat.isSymbolicLink() ? mappedLink(path.resolve(path.dirname(from), fs.readlinkSync(from)), shared, local) : from;
    // Existing private environments are never replaced.
    let existing;
    try { existing = fs.lstatSync(to); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (existing) {
      if (existing.isSymbolicLink() && path.resolve(path.dirname(to), fs.readlinkSync(to)) === destination) continue;
      if (path.basename(source) === '.bin' && existing.isFile() && !stat.isSymbolicLink()
          && digest(fs.readFileSync(to)) === digest(fs.readFileSync(from))) continue;
      throw Error(`existing dependency path differs: ${to}; preserving it`);
    }
    if (path.basename(source) === '.bin' && !stat.isSymbolicLink()) { fs.copyFileSync(from, to); continue; }
    fs.symlinkSync(destination, to, process.platform === 'win32' && stat.isDirectory() ? 'junction' : undefined);
  }
}
const localVenv = () => path.join(root, 'python/.venv');
const pythonIn = directory => path.join(directory, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
export function executionEnvironment({checkout = root, shared = false, environment = process.env} = {}) {
  const venv = path.join(checkout, 'python/.venv'), executable = pythonIn(venv);
  return {...environment, PYTHON: executable, UV_PROJECT_ENVIRONMENT: venv, UV_NO_SYNC: shared ? '1' : '0',
    PYTHONDONTWRITEBYTECODE: '1',
    PYTHONPATH: [path.join(checkout, 'python'), environment.PYTHONPATH].filter(Boolean).join(path.delimiter),
    PATH: `${path.dirname(executable)}${path.delimiter}${environment.PATH || ''}`};
}
function pythonCommands(directory) {
  const venv = localVenv(), localBin = path.dirname(pythonIn(venv));
  const sharedVenv = path.join(directory, 'deps/python/.venv'), sharedBin = path.dirname(pythonIn(sharedVenv));
  const names = JSON.parse(run(pythonIn(venv), ['-I', '-B', '-c',
    'import importlib.metadata,json;print(json.dumps([e.name for e in importlib.metadata.entry_points(group="console_scripts")]))'], root, process.env, true));
  const quote = value => "'" + value.replaceAll("'", "'\"'\"'") + "'";
  for (const name of new Set(names)) {
    if (path.basename(name) !== name) throw Error('invalid installed console script name');
    const script = path.join(localBin, `${name}-script.py`);
    fs.writeFileSync(script, 'import sys\nfrom importlib.metadata import entry_points\n' +
      `sys.argv[0] = sys.argv[0].removesuffix("-script.py")\nsys.exit(next(e for e in entry_points(group="console_scripts") if e.name == ${JSON.stringify(name)}).load()())\n`);
    if (process.platform === 'win32') fs.writeFileSync(path.join(localBin, `${name}.cmd`),
      `@echo off\r\n"${pythonIn(venv)}" "${script}" %*\r\n`);
    else {
      const target = path.join(localBin, name);
      fs.writeFileSync(target, `#!/bin/sh\nexec ${quote(pythonIn(venv))} ${quote(script)} "$@"\n`); fs.chmodSync(target, 0o755);
    }
  }
  for (const name of fs.readdirSync(sharedBin)) {
    if (/^(?:python|activate|deactivate)/iu.test(name) || names.includes(name.replace(/\.(?:exe|cmd|bat)$/iu, ''))) continue;
    const source = path.join(sharedBin, name), target = path.join(localBin, name);
    if (fs.statSync(source).isDirectory()) continue;
    if (fs.existsSync(target)) continue;
    const header = fs.readFileSync(source).subarray(0, 512).toString('utf8');
    if (process.platform !== 'win32' && header.startsWith('#!') && header.includes('python')) {
      fs.writeFileSync(target, `#!/bin/sh\nexec ${quote(pythonIn(venv))} ${quote(source)} "$@"\n`); fs.chmodSync(target, 0o755);
    } else fs.symlinkSync(source, target);
  }
}
function validatePython(directory) {
  const venv = localVenv(), marker = path.join(venv, '.spireagent-shared-env');
  if (!fs.existsSync(pythonIn(venv)) || fs.lstatSync(venv).isSymbolicLink()
      || !fs.existsSync(marker) || fs.readFileSync(marker, 'utf8') !== directory)
    throw Error('checkout Python environment missing or changed');
  run(pythonIn(venv), ['-I', '-B', '-c',
    `import pathlib,stpd,spireagent,numpy; root=pathlib.Path(${JSON.stringify(path.join(root, 'python'))}); assert pathlib.Path(stpd.__file__).is_relative_to(root); assert pathlib.Path(spireagent.__file__).is_relative_to(root); assert pathlib.Path(numpy.__file__).is_relative_to(pathlib.Path(${JSON.stringify(path.join(directory, 'deps/python/.venv'))}))`],
    root, process.env, true);
}
function attachPython(directory, id) {
  const venv = localVenv(), marker = path.join(venv, '.spireagent-shared-env');
  if (fs.existsSync(venv)) {
    if (fs.lstatSync(venv).isSymbolicLink() || !fs.existsSync(marker) || fs.readFileSync(marker, 'utf8') !== directory)
      throw Error('existing Python environment is private; preserving it');
  } else {
    idle();
    run('uv', ['venv', '--python', id.python, venv]);
    fs.writeFileSync(marker, directory);
    const sharedSite = run(pythonIn(path.join(directory, 'deps/python/.venv')),
      ['-I', '-B', '-c', 'import sysconfig;print(sysconfig.get_path("purelib"))'], root, process.env, true);
    const localSite = run(pythonIn(venv), ['-I', '-B', '-c', 'import sysconfig;print(sysconfig.get_path("purelib"))'], root, process.env, true);
    fs.writeFileSync(path.join(localSite, 'shared-dependencies.pth'), `import site; site.addsitedir(${JSON.stringify(sharedSite)})\n`);
    // Install only this checkout's editable project metadata. Children which
    // deliberately clear PYTHONPATH still resolve the same source as private uv.
    run('uv', ['pip', 'install', '--no-deps', '--python', pythonIn(venv), '--editable', 'python'], root,
      {...process.env, UV_LINK_MODE: 'copy'});
  }
  pythonCommands(directory);
  validatePython(directory);
}
function register(directory) {
  const users = path.join(directory, 'users');
  fs.writeFileSync(path.join(users, `${digest(root)}.json`), JSON.stringify({root, attached: new Date().toISOString()}));
  fs.mkdirSync(path.join(root, '.local'), {recursive: true});
  fs.writeFileSync(path.join(root, '.local/shared-env.json'), JSON.stringify({directory}));
}
function attachment() { return JSON.parse(fs.readFileSync(path.join(root, '.local/shared-env.json'))); }
function detach() {
  const {directory} = attachment();
  const shared = path.join(directory, 'deps');
  // Remove only links that still point into this environment or local workspaces.
  function unlinkLinks(folder) {
    if (!fs.existsSync(folder)) return;
    for (const name of fs.readdirSync(folder)) {
      const p = path.join(folder, name), stat = fs.lstatSync(p);
      if (stat.isSymbolicLink()) {
        const dest = path.resolve(path.dirname(p), fs.readlinkSync(p));
        if (dest.startsWith(`${shared}${path.sep}`) || dest.startsWith(`${root}${path.sep}`)) fs.unlinkSync(p);
      } else if (stat.isDirectory() && (name.startsWith('@') || name === '.bin')) unlinkLinks(p);
      else if (path.basename(folder) === '.bin') {
        const original = path.join(shared, path.relative(root, p));
        if (fs.existsSync(original) && digest(fs.readFileSync(original)) === digest(fs.readFileSync(p))) fs.unlinkSync(p);
      }
    }
    if (!fs.readdirSync(folder).length) fs.rmdirSync(folder);
  }
  unlinkLinks(path.join(root, 'node_modules')); unlinkLinks(path.join(root, 'python/node_modules'));
  const venv = localVenv(), marker = path.join(venv, '.spireagent-shared-env');
  if (fs.existsSync(venv) && !fs.lstatSync(venv).isSymbolicLink() && fs.existsSync(marker)
      && fs.readFileSync(marker, 'utf8') === directory) fs.rmSync(venv, {recursive: true});
  fs.rmSync(path.join(directory, 'users', `${digest(root)}.json`), {force: true});
  fs.unlinkSync(path.join(root, '.local/shared-env.json'));
}
function privateSetup() {
  idle();
  if (fs.existsSync(localVenv()) && fs.lstatSync(localVenv()).isSymbolicLink()) throw Error('foreign Python environment link preserved; detach it before private setup');
  if (fs.existsSync(path.join(root, '.local/shared-env.json'))) detach();
  run('npm', ['ci']); run('npm', ['ci', '--prefix', 'python']); run('npm', ['run', 'setup:python']);
  fs.mkdirSync(path.join(root, '.local'), {recursive: true});
  fs.writeFileSync(path.join(root, '.local/shared-env-private.json'), JSON.stringify({key: identity().key}));
}
async function main() {
  const [command, ...args] = process.argv.slice(2);
  if (command === 'detach') return detach();
  if (command === 'prune') {
    const keep = Number(args[0] || 3);
    if (!Number.isInteger(keep) || keep < 2) throw Error('keep at least two environments');
    fs.mkdirSync(cache(), {recursive: true});
    const entries = fs.readdirSync(cache()).filter(n => /^[a-f0-9]{64}$/u.test(n)).map(n => path.join(cache(), n))
      .filter(d => fs.existsSync(path.join(d, 'receipt.json'))).sort((a,b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
    for (const d of entries.slice(keep)) {
      const users = fs.readdirSync(path.join(d, 'users')).map(n => JSON.parse(fs.readFileSync(path.join(d, 'users', n))));
      // Even a vanished checkout requires explicit detach; never infer it is unused.
      if (users.length || fs.existsSync(`${d}.building`)) { console.log(`retained in use: ${d}`); continue; }
      console.log(`delete rebuildable dependencies: ${d}`);
      readOnly(d, true); fs.rmSync(d, {recursive: true});
    }
    return;
  }
  const id = identity();
  if (command === 'key') { console.log(id.key); return; }
  const directory = path.join(cache(), id.key);
  if (command === 'attach') {
    const start = performance.now();
    try {
      if (!fs.existsSync(directory)) build(id, directory);
      verify(directory, id);
      const reattach = fs.existsSync(path.join(root, '.local/shared-env.json')) && attachment().directory === directory;
      for (const rel of ['node_modules', 'python/node_modules', 'python/.venv']) if (!reattach && fs.existsSync(path.join(root, rel)))
        throw Error(`existing ${rel}; preserving private environment`);
      // Register before links: concurrent prune must see this checkout as in use.
      register(directory);
      attachModules(path.join(directory, 'deps/node_modules'), path.join(root, 'node_modules'), path.join(directory, 'deps'), root);
      attachModules(path.join(directory, 'deps/python/node_modules'), path.join(root, 'python/node_modules'), path.join(directory, 'deps'), root);
      attachPython(directory, id);
      console.log(JSON.stringify({key: id.key, seconds: (performance.now()-start)/1000, directory}));
    } catch (error) {
      console.error(`Shared environment unavailable: ${error.message}. Using private setup.`);
      // Never turn an admission denial into unguarded fallback work.
      privateSetup();
    }
  } else if (command === 'run') {
    const privateReceipt = path.join(root, '.local/shared-env-private.json');
    if (!fs.existsSync(path.join(root, '.local/shared-env.json')) && fs.existsSync(pythonIn(localVenv())) && fs.existsSync(privateReceipt)
        && JSON.parse(fs.readFileSync(privateReceipt)).key === id.key) {
      const child = args[0] === '--' ? args.slice(1) : args;
      if (!child.length) throw Error('env:run requires -- COMMAND');
      run(child[0], child.slice(1), root, executionEnvironment()); return;
    }
    let shared;
    try { shared = attachment().directory; verify(shared, id); validatePython(shared); }
    catch (error) {
      console.error(`Shared environment unavailable: ${error.message}; using private setup in the reserved idle slot.`);
      const mutation = path.join(cache(), '.mutation');
      fs.mkdirSync(mutation); fs.writeFileSync(path.join(mutation, 'pid'), String(process.pid));
      try { privateSetup(); } finally { fs.rmSync(mutation, {recursive: true}); }
      const child = args[0] === '--' ? args.slice(1) : args;
      if (!child.length) throw Error('env:run requires -- COMMAND');
      run(child[0], child.slice(1), root, executionEnvironment()); return;
    }
    const env = executionEnvironment({shared: true});
    const child = args[0] === '--' ? args.slice(1) : args;
    if (!child.length) throw Error('env:run requires -- COMMAND');
    run(child[0], child.slice(1), root, env);
  } else throw Error('usage: shared-env attach|detach|run|key|prune [keep=3]');
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  let locked = false;
  const lock = path.join(cache(), '.mutation');
  try {
    fs.mkdirSync(cache(), {recursive: true});
    if (!['run', 'key'].includes(process.argv[2])) {
      fs.mkdirSync(lock); locked = true;
      fs.writeFileSync(path.join(lock, 'pid'), String(process.pid));
    }
    await main();
  } catch (error) { console.error(error.message); process.exitCode = 1; }
  finally { if (locked) fs.rmSync(lock, {recursive: true}); }
}
