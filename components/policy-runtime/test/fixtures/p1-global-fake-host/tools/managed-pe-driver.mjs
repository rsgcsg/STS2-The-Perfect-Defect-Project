#!/usr/bin/env node
// Scripted Host driver for P1 runner tests: same NDJSON commands as the real drivers.
// P1_FAKE_SCENARIO = JSON { reset: snapshot, reads: {read_id: response}, steps: [ {delivery, successor|null, reason_code} ], observe: [snapshot] }
import readline from "node:readline";
import { appendFileSync, readFileSync } from "node:fs";
const scenario = JSON.parse(readFileSync(process.env.P1_FAKE_SCENARIO, "utf8"));
const log = process.env.P1_FAKE_LOG;
let step = 0; let observe = 0; let current = null;
const out = (value) => process.stdout.write(JSON.stringify(value) + "\n");
out({ type: "ready", protocol: "sts2.headless/managed-player-environment-driver-1", fake: true });
for await (const line of readline.createInterface({ input: process.stdin })) {
  const request = JSON.parse(line);
  if (log) appendFileSync(log, JSON.stringify(request) + "\n");
  const id = request.request_id;
  switch (request.command) {
    case "reset": current = scenario.reset; out({ type: "reset_result", request_id: id, snapshot: current }); break;
    case "observe": current = scenario.observe[Math.min(observe++, scenario.observe.length - 1)] ?? current;
      out({ type: "observe_result", request_id: id, snapshot: current }); break;
    case "read": {
      const response = scenario.reads[request.read_id];
      if (!response || request.expected_snapshot_id !== current.snapshot_id) out({ type: "error", request_id: id, code: "driver_request_failed", message: "stale_state" });
      else out({ type: "read_result", request_id: id, read: { ...response, expected_snapshot_id: current.snapshot_id, observed_snapshot_id: current.snapshot_id } });
      break;
    }
    case "step": {
      const plan = scenario.steps[step++];
      if (!plan || plan.hang) break; // no response: unknown delivery from the runner's view
      const receipt = { protocol_version: "1.0.0", schema: "sts2.player-environment/receipt-1", request_id: request.mutation_request_id,
        delivery: plan.delivery, action: { bound_action_id: request.bound_action_id, verb: "play", subject_referent_id: null, arguments: [] },
        reason_code: plan.reason_code ?? null, detail: null, retry: { allowed: false, reason: "test" }, successor: plan.successor ?? null };
      if (plan.successor) current = plan.successor;
      out({ type: "step_result", request_id: id, receipt }); break;
    }
    case "close": out({ type: "close_result", request_id: id, exit: { code: 0 } }); process.exit(0);
    default: out({ type: "error", request_id: id, message: "unsupported" });
  }
}
