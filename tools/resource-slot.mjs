// Node-local development coordination. No gameplay or component identity authority.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {ownedCommand} from './check-workspace.mjs';
import {listGameProcesses} from '../components/host-runtime/src/game-processes.mjs';

export const slotRoot = () => process.env.SPIREAGENT_SLOT_ROOT || path.join(os.homedir(), 'Documents/Codex/resource-slots');
const alive = pid => { try { process.kill(pid, 0); return true; } catch (e) { return e.code !== 'ESRCH'; } };
const valid = name => { if (!['managed', 'cpu', 'training'].includes(name)) throw Error(`unknown resource: ${name}`); return name; };
function read(root, name) {
  try {
    const value = JSON.parse(fs.readFileSync(path.join(root, `${valid(name)}.json`), 'utf8'));
    if (!Number.isSafeInteger(value.pid) || value.pid < 1 || !Number.isFinite(Date.parse(value.until))
        || typeof value.card !== 'string' || typeof value.token !== 'string') throw Error(`${name}: malformed reservation; inspect before recovery`);
    return value;
  }
  catch (e) { if (e.code === 'ENOENT') return null; throw e; }
}
function journal(root, record, action, note = '') {
  const config = path.join(root, 'config.json');
  const target = fs.existsSync(config) ? JSON.parse(fs.readFileSync(config)).journal : path.join(root, 'GAME-SLOT.md');
  fs.appendFileSync(target, `${new Date().toISOString()}｜${record.card}｜${action}｜${record.lanes}路 ${record.resource}｜预计${record.until}｜${note || `pid=${record.pid}`}\n`);
}
function transaction(root, action) {
  fs.mkdirSync(root, {recursive: true});
  const guard = path.join(root, '.guard');
  let fd;
  try { fd = fs.openSync(guard, 'wx'); } catch (e) {
    if (e.code !== 'EEXIST') throw e;
    throw Error(`slot update busy: ${guard}; a crashed guard requires operator inspection of its PID before removal`);
  }
  fs.writeFileSync(fd, String(process.pid));
  try { return action(); } finally { fs.closeSync(fd); fs.unlinkSync(guard); }
}
export function acquire({resources = ['managed'], card, until, pid, lanes = 1}, root = slotRoot()) {
  if (!card || /[\r\n｜]/u.test(card) || !Number.isSafeInteger(pid) || pid < 1 || !alive(pid)
      || !Number.isInteger(lanes) || lanes < 1 || !Number.isFinite(Date.parse(until)) || /[\r\n｜]/u.test(until) || Date.parse(until) <= Date.now())
    throw Error('card, live --pid, positive lanes and future ISO --until required');
  resources = [...new Set(resources.map(valid))];
  return transaction(root, () => {
    for (const resource of resources) {
      const current = read(root, resource);
      if (current && (current.pending_cleanup || alive(current.pid) || Date.parse(current.until) >= Date.now()))
        throw Error(`${resource} occupied by ${current.card}, pid=${current.pid}, until=${current.until}`);
    }
    const token = crypto.randomUUID();
    const records = resources.map(resource => ({resource, card, pid, lanes, until, token, started: new Date().toISOString()}));
    // Journal first: failed journal writes must never grant an unlogged lock.
    for (const record of records) {
      const old = read(root, record.resource);
      if (old) journal(root, old, '结束', '过期锁接管：进程已退出且预计时间已过');
      journal(root, record, '开始');
    }
    for (const record of records) fs.writeFileSync(path.join(root, `${record.resource}.json`), JSON.stringify(record));
    return records;
  });
}
export function requireSlot(resource = 'managed', token = process.env.SPIREAGENT_SLOT_TOKEN, root = slotRoot()) {
  if (root === slotRoot() && listGameProcesses(process.platform, {failClosed: true}).length)
    throw Error('user game is running; no new batch admission');
  const current = read(root, resource);
  if (!current || !token || current.token !== token || !alive(current.pid))
    throw Error(`${resource} lock required; use npm run slot -- run --card CARD --until ISO -- COMMAND`);
  return current;
}
export function release(resources, token, root = slotRoot()) {
  return transaction(root, () => {
    for (const resource of resources) {
      const current = read(root, resource);
      if (!current || !token || current.token !== token) throw Error(`${resource}: owner token required`);
    }
    for (const resource of resources) {
      journal(root, read(root, resource), '结束');
      fs.unlinkSync(path.join(root, `${valid(resource)}.json`));
    }
  });
}
export function finishReservation(resources, token, outcome, root = slotRoot()) {
  if (outcome.error) {
    transaction(root, () => {
      for (const resource of resources) {
        const record = read(root, resource);
        if (!record || record.token !== token) throw Error('reservation owner changed during cleanup');
        fs.writeFileSync(path.join(root, `${resource}.json`), JSON.stringify({...record,
          pending_cleanup: true, child_pid: outcome.child_pid ?? null, cleanup_error: outcome.error}));
        journal(root, record, '排队', '本卡进程树退出未确认，保留占位；核实后凭原token释放');
      }
    });
    throw Error(`resource reservation retained: ${outcome.error}`);
  }
  release(resources, token, root);
}
export async function runReservedCommand(args, {resources, token,
  gamePresent = () => listGameProcesses(process.platform, {failClosed: true}).length > 0,
  intervalMs = 3000}) {
  const owned = ownedCommand(args[0], args.slice(1), {stdio: 'inherit', shell: process.platform === 'win32',
    env: {...process.env, SPIREAGENT_SLOT_TOKEN: token}});
  const onInt = () => { void owned.cancel('SIGINT'); }, onTerm = () => { void owned.cancel('SIGTERM'); };
  process.on('SIGINT', onInt); process.on('SIGTERM', onTerm);
  let interruptedForGame = false;
  // CPU-only setup/comparison can be cancelled as a process tree. Managed gameplay
  // keeps its existing cleanup semantics and rejects the next episode at check().
  const monitor = resources.includes('managed') && resources.includes('cpu') ? setInterval(() => {
    let busy = true;
    try { busy = gamePresent(); } catch { /* Unavailable observation cannot grant CPU work. */ }
    if (busy && !interruptedForGame) {
      interruptedForGame = true;
      console.error('User game appeared: stopping owned setup/comparison process tree.');
      void owned.cancel('SIGTERM');
    }
  }, intervalMs) : null;
  try { return {...await owned.completion, interruptedForGame, child_pid: owned.child.pid}; }
  finally {
    if (monitor) clearInterval(monitor);
    process.off('SIGINT', onInt); process.off('SIGTERM', onTerm);
  }
}
async function main() {
  const [command, ...args] = process.argv.slice(2), options = {};
  const split = args.indexOf('--'), childArgs = split < 0 ? [] : args.splice(split + 1);
  if (split >= 0) args.pop();
  for (let i = 0; i < args.length; i += 2) {
    if (!args[i].startsWith('--') || !args[i + 1]) throw Error('named arguments required');
    options[args[i].slice(2)] = args[i + 1];
  }
  const resources = (options.resources || 'managed').split(',');
  if (command === 'status') {
    console.log(JSON.stringify(['managed', 'cpu', 'training'].map(name => read(slotRoot(), name)), null, 2));
  } else if (command === 'configure') {
    if (!options.journal || !path.isAbsolute(options.journal) || !fs.existsSync(options.journal)) throw Error('existing absolute --journal required');
    transaction(slotRoot(), () => fs.writeFileSync(path.join(slotRoot(), 'config.json'), JSON.stringify({journal: options.journal})));
  } else if (command === 'check') requireSlot(resources[0], options.token || process.env.SPIREAGENT_SLOT_TOKEN);
  else if (command === 'release') release(resources, options.token || process.env.SPIREAGENT_SLOT_TOKEN);
  else if (command === 'acquire' || command === 'run') {
    if ((resources.includes('managed') || resources.includes('cpu')) && listGameProcesses(process.platform, {failClosed: true}).length)
      throw Error('user game is running; no admission');
    if (command === 'run' && !childArgs.length) throw Error('run requires -- COMMAND');
    const records = acquire({resources, card: options.card, until: options.until,
      lanes: Number(options.lanes || 1), pid: command === 'run' ? process.pid : Number(options.pid)});
    if (command === 'acquire') console.log(JSON.stringify(records, null, 2));
    else {
      let outcome;
      try { outcome = await runReservedCommand(childArgs, {resources, token: records[0].token}); }
      catch (error) { outcome = {error: error.message}; }
      finishReservation(resources, records[0].token, outcome);
      process.exitCode = outcome.code ?? 1;
    }
  } else throw Error('usage: slot status|configure|acquire|release|check|run');
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
