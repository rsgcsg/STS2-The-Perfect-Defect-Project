#!/usr/bin/env node
// P1 Agent bridge: speaks the Runtime's agent-session NDJSON protocol on stdio and runs a
// simple external P1 Agent (sts2.p1/agent-port-1) as a child process.
//
//   session-bridge.mjs <agent-manifest.json> <command> [args...]
//
// The child sees only: hello{protocol, profile, revision, episode_id, host_kind} ->
// ready{agent_id} ; observation{observation, requires_choice:true} -> choose{occurrence, choice_id}
// ; episode_end{reason}. It knows nothing about consumption ids, tokens or native operands.
// Every directive refers to the exact acknowledged basis; the Runtime remains the only
// authority for dispatch, budgets, Stop and evidence.
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { createInterface } from "node:readline";

const [manifestPath, command, ...args] = process.argv.slice(2);
if (!manifestPath || !command) { process.stderr.write("usage: session-bridge.mjs <manifest> <command> [args...]\n"); process.exit(2); }
const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
const send = (value) => process.stdout.write(JSON.stringify(value) + "\r\n");
const reply = (m, message_type, key, value) => send({ schema: m.schema, message_type, session_id: m.session_id,
  recovery_epoch: m.recovery_epoch, request_id: m.request_id, [key]: value });

const child = spawn(command, args, { stdio: ["pipe", "pipe", "inherit"] });
const childLines = createInterface({ input: child.stdout })[Symbol.asyncIterator]();
const toChild = (value) => child.stdin.write(JSON.stringify(value) + "\n");
const fromChild = async (expect) => {
  for (;;) {
    const { value, done } = await childLines.next();
    if (done) throw new Error("p1_agent_exited");
    if (!value.trim()) continue;
    const message = JSON.parse(value);
    if (message.type === expect) return message;
    if (message.type === "abstain") throw new Error(`p1_agent_abstained:${message.reason ?? ""}`);
  }
};
// A dead Agent must surface as a closed port, never as a silent wait.
child.on("exit", (code) => { process.stderr.write(`p1 agent exited (${code})\n`); process.exit(code === 0 ? 0 : 1); });

let version = 0, consumption = null, basis = null, pending = null, unit = null, helloSent = false;
send({ schema: "sts2.policy-runtime/agent-session-1", message_type: "ready", adapter: manifest.adapter });

for await (const line of createInterface({ input: process.stdin })) {
  if (!line.trim()) continue;
  const m = JSON.parse(line);
  if (m.message_type === "consume") {
    const advanced = unit !== m.input.consumption_unit.id;
    pending = { unit: m.input.consumption_unit.id, input: m.input, version: version + Number(advanced),
      consumption: advanced ? `c-${version + 1}` : consumption };
    reply(m, "consumed", "completion", { acquisition_id: m.input.acquisition_id, input_spec: manifest.input.input_spec,
      continuity_token: m.input.continuity_token, previous_consumption_id: consumption, consumption_id: pending.consumption,
      state_version: pending.version, advanced });
  } else if (m.message_type === "consume_ack") {
    ({ unit, version, consumption } = pending); basis = pending.input; pending = null;
  } else if (m.message_type === "next") {
    // The Runtime offers the model view (no envelope). The child protocol carries the
    // semantic occurrence (Runtime state version) and episode id as binding metadata only.
    const observation = { ...basis.observation, envelope: { episode_id: manifest.manifest_id, occurrence: version, host_kind: null } };
    if (!helloSent) {
      helloSent = true;
      toChild({ type: "hello", protocol: "sts2.p1/agent-port-1", profile: observation.profile, revision: observation.revision,
        episode_id: manifest.manifest_id, host_kind: null });
      await fromChild("ready");
    }
    let directive;
    if (observation.status === "terminal") {
      toChild({ type: "episode_end", outcome: observation.terminal?.outcome ?? null, censor_reason: null });
      directive = { type: "close", reason: "native_terminal_observed" };
    } else {
      toChild({ type: "observation", observation, requires_choice: true, forced_choice_id: null });
      const choice = await fromChild("choose");
      directive = { type: "act", basis_acquisition_id: basis.acquisition_id, selection: { kind: "handle", action_id: choice.choice_id }, scores: null };
    }
    reply(m, "directive", "output", { continuity_token: m.input.continuity_token, consumption_id: consumption,
      state_version: version, directive });
  }
}
child.kill();
