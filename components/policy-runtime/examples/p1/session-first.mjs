// Model-less conformance program over the existing Agent session, not a gameplay authority.
import { readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
const manifest = JSON.parse(readFileSync(process.argv[2], 'utf8'));
let unit = null, version = 0, consumption = null, basis = null, pending = null;
const send = value => process.stdout.write(JSON.stringify(value) + '\n');
const reply = (parent, message_type, key, value) => send({ schema: parent.schema, message_type, session_id: parent.session_id,
  recovery_epoch: parent.recovery_epoch, request_id: parent.request_id, [key]: value });
send({ schema: 'sts2.policy-runtime/agent-session-1', message_type: 'ready', adapter: manifest.adapter });
for await (const line of createInterface({ input: process.stdin })) {
  const m = JSON.parse(line);
  if (m.message_type === 'consume') {
    const advanced = m.input.consumption_unit.id !== unit;
    pending = { input: m.input, unit: m.input.consumption_unit.id, version: version + Number(advanced), consumption: advanced ? `consumed-${version + 1}` : consumption };
    reply(m, 'consumed', 'completion', { acquisition_id: m.input.acquisition_id, input_spec: manifest.input.input_spec,
      continuity_token: m.input.continuity_token, previous_consumption_id: consumption,
      consumption_id: pending.consumption, state_version: pending.version, advanced });
  } else if (m.message_type === 'consume_ack') {
    ({ unit, version, consumption } = pending); basis = pending.input; pending = null;
  } else if (m.message_type === 'next') {
    const terminal = basis?.observation.status === 'terminal';
    reply(m, 'directive', 'output', { continuity_token: m.input.continuity_token, consumption_id: consumption, state_version: version,
      directive: terminal ? { type: 'close', reason: 'native_terminal_observed' }
        : { type: 'act', basis_acquisition_id: basis.acquisition_id, selection: { kind: 'handle', action_id: basis.catalog[0].choice_id }, scores: null } });
  }
}
