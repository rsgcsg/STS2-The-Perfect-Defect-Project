#!/usr/bin/env node
/** Thin client for the existing Application recording owner. It attaches to an
 * already running game, sends one command, and never owns gameplay or shutdown. */
import { randomUUID } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync, renameSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { collectionSetup } from '../apps/game-mod/collection-setup.mjs';

const profile = 'human-p1-native-decisions-1';
const kinds = new Set(['start_new_session', 'pause', 'resume', 'close', 'export_p1_native_prefix']);
const origins = new Set(['declared_human', 'agent_native_ui', 'unknown']);
export class RecordingOperationError extends Error {
  constructor(code, delivery, response = null) { super(code); this.code = code; this.delivery = delivery; this.response = response; }
}

export class P1NativeRecordingClient {
  constructor({ address = 'http://127.0.0.1:15528', fetch: request = globalThis.fetch,
    setup = collectionSetup } = {}) {
    const url = new URL(address);
    if (url.protocol !== 'http:' || !['127.0.0.1', '[::1]'].includes(url.hostname)
      || url.username || url.password || url.pathname !== '/' || url.search || url.hash)
      throw new Error('local_recording_application_address_required');
    this.address = url.origin; this.fetch = request; this.setup = setup;
  }
  async status(expectedRuntime = null) {
    const response = await this.fetch(`${this.address}/v1/tasks/recording/status`,
      { signal: AbortSignal.timeout(5000), redirect: 'error' });
    if (!response.ok) throw new RecordingOperationError('recording_status_unavailable', 'not_sent');
    const value = await response.json();
    if (value.schema !== 'sts2.platform/recording-status-1' || typeof value.runtime_instance_id !== 'string'
      || !['ready', 'recording', 'paused', 'closing', 'closed'].includes(value.recording_lifecycle))
      throw new RecordingOperationError('recording_status_invalid', 'not_sent', value);
    if (expectedRuntime !== null && value.runtime_instance_id !== expectedRuntime)
      throw new RecordingOperationError('recording_runtime_changed', 'not_sent', value);
    return value;
  }
  async command({ kind, runtimeInstanceId, sessionId, origin = null, commandId = randomUUID(), setupOptions }, retain = () => {}) {
    if (!kinds.has(kind) || typeof runtimeInstanceId !== 'string' || !runtimeInstanceId
      || !(sessionId === null || typeof sessionId === 'string' && sessionId.length > 0)
      || !/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(commandId)
      || (kind === 'start_new_session' ? !origins.has(origin) : origin !== null))
      throw new RecordingOperationError('explicit_p1_recording_command_required', 'not_sent');
    const before = await this.status(runtimeInstanceId);
    if (before.recording_session_id !== sessionId)
      throw new RecordingOperationError('recording_session_changed', 'not_sent', before);
    const stopStandby = kind === 'close' && before.recording_lifecycle === 'ready'
      && before.recording_session_id === null;
    if (kind !== 'start_new_session' && before.capture_profile_id !== profile && !stopStandby)
      throw new RecordingOperationError('p1_recording_session_required', 'not_sent', before);
    // Consume the existing status/bound contract. There is no "validate" setup command.
    const setup = await this.setup('status', setupOptions);
    if (setup.status !== 'bound' || setup.connected !== true || setup.bound !== true
      || setup.loaded_identity?.runtime_instance_id !== runtimeInstanceId)
      throw new RecordingOperationError('recording_setup_not_bound_to_runtime', 'not_sent', setup);
    const request = { schema: 'sts2.platform/recording-request-1', runtime_instance_id: runtimeInstanceId,
      recording_session_id: sessionId, command: { schema: 'sts2.ai-platform/recording-command-1', command_id: commandId,
        kind, capture_profile_id: kind === 'start_new_session' ? profile : null,
        p1_native_origin: kind === 'start_new_session' ? origin : null } };
    const receipt = { schema: 'sts2.p1/native-recording-operation-1', request, setup,
      delivery: 'prepared', result: null };
    retain(receipt); // durable command identity exists before the only POST
    let response, result;
    try {
      response = await this.fetch(`${this.address}/v1/tasks/recording/command`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
        signal: AbortSignal.timeout(45000), redirect: 'error' });
      result = await response.json();
      // These are the bridge's explicit pre-mutation dispositions. A 500 or
      // native-task timeout does not carry the same assurance.
      const known = response.status === 503 && result.error === 'native_task_not_dispatched' ? 'not_dispatched'
        : response.status === 409 && result.error === 'task_handoff_rejected'
          || response.status === 400 && result.error === 'invalid_recording_request' ? 'rejected' : null;
      if (known) {
        receipt.result = result; receipt.delivery = known;
        throw new RecordingOperationError(result.detail ?? result.error, known, result);
      }
      if (result.schema !== 'sts2.platform/recording-result-1' || result.command_id !== commandId
        || typeof result.accepted !== 'boolean' || typeof result.pending !== 'boolean'
        || result.status?.runtime_instance_id !== runtimeInstanceId)
        throw new Error('recording_command_response_not_exact');
    } catch (error) {
      if (error instanceof RecordingOperationError && ['rejected', 'not_dispatched'].includes(error.delivery)) {
        try { retain(receipt); }
        catch { throw new RecordingOperationError('recording_result_receipt_write_failed', error.delivery, result); }
        throw error;
      }
      receipt.delivery = 'unknown';
      try { retain(receipt); }
      catch { throw new RecordingOperationError('recording_delivery_unknown_receipt_write_failed', 'unknown'); }
      throw new RecordingOperationError('recording_command_delivery_unknown', 'unknown');
    }
    receipt.result = result; receipt.delivery = response.ok && result.accepted ? 'accepted' : 'rejected';
    try { retain(receipt); }
    catch { throw new RecordingOperationError('recording_result_receipt_write_failed', receipt.delivery, result); }
    if (receipt.delivery === 'rejected') throw new RecordingOperationError(result.code ?? 'recording_command_rejected', 'rejected', result);
    return receipt;
  }
}

export async function main(args) {
  const aliases = { start: 'start_new_session', prefix: 'export_p1_native_prefix', pause: 'pause', resume: 'resume', close: 'close' };
  const [command, ...rest] = args, options = {};
  const names = new Set(['--runtime', '--session', '--origin', '--setup', '--receipt', '--command-id']);
  for (let i = 0; i < rest.length; i += 2) {
    if (!names.has(rest[i]) || !rest[i + 1] || Object.hasOwn(options, rest[i])) throw new Error('invalid_recording_cli_options');
    options[rest[i]] = rest[i + 1];
  }
  const client = new P1NativeRecordingClient();
  if (command === 'status') return client.status(options['--runtime'] ?? null);
  if (!aliases[command] || !options['--setup'] || !options['--receipt'] || !Object.hasOwn(options, '--session'))
    throw new Error('usage: status | start|pause|resume|prefix|close --runtime ID --session ID|none --setup CONFIG --receipt NEW_FILE [--origin declared_human|agent_native_ui|unknown]');
  const setupOptions = JSON.parse(readFileSync(resolve(options['--setup']), 'utf8'));
  const file = resolve(options['--receipt']); mkdirSync(dirname(file), { recursive: true });
  let prepared = false;
  return client.command({ kind: aliases[command], runtimeInstanceId: options['--runtime'],
    sessionId: options['--session'] === 'none' ? null : options['--session'], origin: options['--origin'] ?? null,
    commandId: options['--command-id'], setupOptions }, value => {
    const target = prepared ? `${file}.tmp-${randomUUID()}` : file;
    writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`, { flag: 'wx', mode: 0o600, flush: true });
    if (prepared) renameSync(target, file);
    prepared = true;
  });
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  main(process.argv.slice(2)).then(value => process.stdout.write(`${JSON.stringify(value, null, 2)}\n`))
    .catch(error => { process.stderr.write(`${JSON.stringify({ code: error.code ?? error.message,
      delivery: error.delivery ?? 'not_sent', response: error.response ?? null })}\n`); process.exitCode = 1; });
