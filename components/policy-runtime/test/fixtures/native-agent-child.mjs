// A real programmed conformance consumer. It is not a learned Model or game.
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { createInterface } from "node:readline";
const manifest = JSON.parse(readFileSync(process.argv[2], "utf8"));
const behavior = process.argv[3] ?? "act";
let version = 0, consumption = null, occurrence = null, basis = null, pending = null, next = null;
const send = value => process.stdout.write(JSON.stringify(value) + "\n");
const envelope = (parent, type, key, value, id = parent.request_id) => send({ schema: parent.schema,
  message_type: type, session_id: parent.session_id, recovery_epoch: parent.recovery_epoch, request_id: id, [key]: value });
const report = (parent, input, id = parent.request_id) => {
  const o = input.observation;
  const key = JSON.stringify([o.catalog.stream_generation, o.snapshot_id, o.owner_occurrence.occurrence_id,
    o.owner_occurrence.binding_revision, o.owner_occurrence.focus_occurrence]);
  const advanced = key !== occurrence;
  pending = { key, input, version: version + Number(advanced), consumption: advanced ? `consumed-${version + 1}` : consumption };
  envelope(parent, "consumed", "completion", { acquisition_id: input.acquisition_id, input_spec: manifest.input.input_spec,
    continuity_token: input.continuity_token, previous_consumption_id: consumption,
    consumption_id: pending.consumption, state_version: pending.version, advanced }, id);
};
const directive = (parent, selection) => envelope(parent, "directive", "output", {
  continuity_token: parent.input.continuity_token, consumption_id: consumption, state_version: version, directive: selection });
if (behavior !== "hang_ready") send({ schema: "sts2.policy-runtime/agent-session-1", message_type: "ready", adapter: manifest.adapter });
createInterface({ input: process.stdin }).on("line", line => {
  const m = JSON.parse(line);
  if (m.message_type === "consume") {
    if (behavior === "hang_consume") return;
    report(m, m.input); return;
  }
  if (m.message_type === "consume_ack") {
    if (pending) {
      version = pending.version; consumption = pending.consumption; occurrence = pending.key; basis = pending.input; pending = null;
    }
    if (next && behavior === "query") envelope(next, "query", "input", { method: "resolve",
      arguments: { catalog_ref: basis.observation.catalog.catalog_ref, stream_generation: basis.observation.catalog.stream_generation,
        expression: { verb: "choose", subject_referent_id: "public-0", arguments: [] } } }, `child-${next.request_id}-resolve`);
    if (next && behavior === "current_full") {
      directive(next, { type: "act", basis_acquisition_id: basis.acquisition_id,
        selection: { kind: "handle", action_id: basis.catalog[0].action_id }, scores: null });
      next = null;
    }
    return;
  }
  if (m.message_type === "next") {
    if (behavior === "hang_next") return;
    next = m;
    if (behavior === "query" || behavior === "current_full") {
      envelope(m, "query", "input", { method: "current", arguments: { eager_scope: ["persistent", "interaction", "referents", "catalog"],
        expected_snapshot_id: null } }, `child-${m.request_id}-current`); return;
    }
    if (behavior === "close") { directive(m, { type: "close", reason: "programmed conformance close; no task outcome claim" }); return; }
    if (behavior === "await" || !basis?.catalog?.length) {
      directive(m, { type: "await", after_cursor: m.input.received_cursor, condition: "observation", timeout_ms: 5000 }); return;
    }
    directive(m, { type: "act", basis_acquisition_id: basis.acquisition_id,
      selection: { kind: "handle", action_id: basis.catalog[0].action_id }, scores: null }); return;
  }
  if (m.message_type === "query_result" && (behavior === "query" || behavior === "current_full")) {
    if (m.result.method === "current") {
      if (behavior === "current_full") {
        if (m.result.value.catalog_materialized !== true || m.result.value.catalog?.length !== basis.catalog.length)
          throw new Error("full reference Current must include every candidate");
        report(next, { acquisition_id: m.result.acquisition_id, observation: m.result.value.observation,
          catalog: m.result.value.catalog, continuity_token: next.input.continuity_token }, `child-${next.request_id}-consumed`); return;
      }
      if (m.result.value.catalog_materialized !== false) throw new Error("query must preserve descriptor-only exposure");
      report(next, { acquisition_id: m.result.acquisition_id, observation: m.result.value.observation,
        catalog: null, continuity_token: next.input.continuity_token }, `child-${next.request_id}-consumed`); return;
    }
    const action = m.result.value.action;
    directive(next, { type: "act", basis_acquisition_id: basis.acquisition_id,
      selection: { kind: "expression", expression: { verb: action.verb, subject_referent_id: action.subject_referent_id, arguments: action.arguments } }, scores: null });
    next = null; return;
  }
  if (m.message_type === "export_state") {
    const bytes = Buffer.from(JSON.stringify({ version, consumption, occurrence, basis }));
    envelope(m, "state_exported", "output", { metadata: m.input.expected_metadata,
      payload: { encoding: "base64", byte_count: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex"), data_base64: bytes.toString("base64") } }); return;
  }
  if (m.message_type === "restore_state") {
    const saved = JSON.parse(Buffer.from(m.input.state.payload.data_base64, "base64").toString("utf8"));
    ({ version, consumption, occurrence, basis } = saved);
    envelope(m, "state_restored", "output", { metadata: m.input.expected_metadata });
  }
});
