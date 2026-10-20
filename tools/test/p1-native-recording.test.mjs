import { test } from 'node:test';
import assert from 'node:assert/strict';
import { P1NativeRecordingClient } from '../p1-native-recording.mjs';
const id = '01234567-0123-4123-8123-0123456789ab';
const status = () => ({ schema: 'sts2.platform/recording-status-1', runtime_instance_id: 'runtime-one',
  recording_session_id: 'session-one', recording_lifecycle: 'recording',
  capture_profile_id: 'human-p1-native-decisions-1', closeout_status: 'idle' });
const setup = () => ({ status: 'bound', connected: true, bound: true,
  loaded_identity: { runtime_instance_id: 'runtime-one' } });
function fixture({ current = status(), bound = setup(), send = null } = {}) {
  const calls = [], retained = [], setups = [];
  const client = new P1NativeRecordingClient({ setup: async (kind, options) => {
    setups.push({ kind, options }); return bound;
  }, fetch: async (url, options) => {
    calls.push({ url, ...options });
    if (!options.method) return { ok: true, json: async () => current };
    const result = { schema: 'sts2.platform/recording-result-1', command_id: id, accepted: true,
      pending: false, code: 'recording_started', status: { ...current, recording_session_id: 'new-session' } };
    return send ? send(result) : { ok: true, json: async () => result };
  } });
  const request = { kind: 'start_new_session', runtimeInstanceId: 'runtime-one', sessionId: 'session-one',
    origin: 'declared_human', commandId: id, setupOptions: { recordings_root: '/explicit/root' } };
  return { client, calls, retained, setups, request, retain: value => retained.push(structuredClone(value)) };
}

test('supported P1 client uses existing bound status and sends exactly one native Application command', async () => {
  const f = fixture(), r = await f.client.command(f.request, f.retain);
  assert.equal(r.delivery, 'accepted'); assert.equal(f.calls.length, 2); assert.equal(f.setups[0].kind, 'status');
  const request = JSON.parse(f.calls[1].body);
  assert.equal(request.command.schema, 'sts2.ai-platform/recording-command-1');
  assert.equal(request.command.capture_profile_id, 'human-p1-native-decisions-1');
  assert.equal(request.command.p1_native_origin, 'declared_human');
  assert.equal(f.retained[0].delivery, 'prepared'); assert.equal(f.retained[0].request.command.command_id, id);
  assert.equal(f.retained[1].result.status.recording_session_id, 'new-session');
});

test('prefix and pending close retain the exact owner result without automatic polling or shutdown', async () => {
  for (const kind of ['export_p1_native_prefix', 'close']) {
    const f = fixture({ send: value => ({ ok: true, json: async () => ({ ...value, pending: true,
      p1_native_prefix: { prefix_id: 'prefix-one', manifest_sha256: 'original-digest' } }) }) });
    const result = await f.client.command({ ...f.request, kind, origin: null }, f.retain);
    assert.equal(result.result.pending, true); assert.equal(f.calls.length, 2);
    assert.equal(result.request.command.capture_profile_id, null); assert.equal(result.request.command.p1_native_origin, null);
    assert.equal(result.result.p1_native_prefix.manifest_sha256, 'original-digest');
  }
});

test('wrong runtime, session, profile and blocked setup stop before any POST', async () => {
  for (const change of ['runtime', 'session', 'profile', 'setup', 'setup-runtime']) {
    const current = status(), bound = setup();
    if (change === 'runtime') current.runtime_instance_id = 'other-runtime';
    if (change === 'session') current.recording_session_id = 'other-session';
    if (change === 'profile') current.capture_profile_id = 'native-logical-source-v3';
    if (change === 'setup') bound.status = 'blocked';
    if (change === 'setup-runtime') bound.loaded_identity.runtime_instance_id = 'old-runtime';
    const f = fixture({ current, bound });
    await assert.rejects(f.client.command({ ...f.request, kind: 'close', origin: null }, f.retain),
      error => error.delivery === 'not_sent', change);
    assert.equal(f.calls.filter(call => call.method === 'POST').length, 0, change);
  }
});

test('unknown delivery is retained once, with no retry or cleanup mutation', async () => {
  const f = fixture({ send: () => { throw new Error('connection reset after send'); } });
  await assert.rejects(f.client.command(f.request, f.retain), error => error.delivery === 'unknown');
  assert.deepEqual(f.retained.map(r => r.delivery), ['prepared', 'unknown']);
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 1);
});

test('known rejection and a nonexact reply remain distinct', async () => {
  const rejected = fixture({ send: value => ({ ok: true, json: async () => ({ ...value, accepted: false, code: 'close_pending' }) }) });
  await assert.rejects(rejected.client.command(rejected.request, rejected.retain), error => error.delivery === 'rejected');
  assert.equal(rejected.retained.at(-1).result.accepted, false);
  const wrong = fixture({ send: value => ({ ok: true, json: async () => ({ ...value, command_id: 'different' }) }) });
  await assert.rejects(wrong.client.command(wrong.request, wrong.retain), error => error.delivery === 'unknown');
  assert.equal(wrong.calls.filter(call => call.method === 'POST').length, 1);
});

test('bridge pre-dispatch rejections remain distinct from timeout/unavailable unknown', async () => {
  for (const [status, error, delivery] of [[503, 'native_task_not_dispatched', 'not_dispatched'],
    [409, 'task_handoff_rejected', 'rejected'], [400, 'invalid_recording_request', 'rejected'],
    [504, 'native_task_command_unknown', 'unknown'], [500, 'task_handoff_unavailable', 'unknown']]) {
    const f = fixture({ send: () => ({ ok: false, status, json: async () => ({ error }) }) });
    await assert.rejects(f.client.command(f.request, f.retain), result => result.delivery === delivery, error);
    assert.equal(f.calls.filter(call => call.method === 'POST').length, 1, error);
    assert.equal(f.retained.at(-1).delivery, delivery, error);
  }
});

test('receipt persistence failure cannot send an unrecorded command or disguise a known result', async () => {
  const before = fixture();
  await assert.rejects(before.client.command(before.request, () => { throw new Error('disk full'); }), /disk full/);
  assert.equal(before.calls.filter(call => call.method === 'POST').length, 0);
  const after = fixture(); let writes = 0;
  await assert.rejects(after.client.command(after.request, () => { if (++writes === 2) throw new Error('disk full'); }),
    error => error.delivery === 'accepted' && error.response.accepted === true);
  assert.equal(after.calls.filter(call => call.method === 'POST').length, 1);
});
