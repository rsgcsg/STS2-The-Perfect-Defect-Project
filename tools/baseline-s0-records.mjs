import { createHash } from "node:crypto";
import { mkdir, open } from "node:fs/promises";
import path from "node:path";
import { INPUT_SPEC } from "./baseline-s0-teacher.mjs";

export const sha256 = bytes => createHash("sha256").update(bytes).digest("hex");
export function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort()
    .map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

/** Serial durable raw capture ledger. Captures alone are NEVER model offers. */
export class S0RawRecords {
  static async create(directory, runId, actor, { maxCaptureBytes = 64 * 1024 * 1024,
    maxCaptures = 4096 } = {}) {
    await mkdir(directory, { recursive: false });
    await mkdir(path.join(directory, "captures"));
    const handle = await open(path.join(directory, "records.jsonl"), "wx");
    return new S0RawRecords(directory, handle, runId, actor, maxCaptureBytes, maxCaptures);
  }
  constructor(directory, handle, runId, actor, maxBytes, maxCaptures) {
    Object.assign(this, { directory, handle, runId, actor, maxBytes, maxCaptures });
    this.index = 0; this.offerCount = 0; this.captureCount = 0; this.captureBytes = 0;
    this.latest = null; this.queue = Promise.resolve(); this.closed = false;
  }
  append(type, payload) {
    if (this.closed) return Promise.reject(new Error("raw_records_closed"));
    const operation = this.queue.then(async () => {
      const record = { schema: "sts2.baseline-s0/raw-record-1", record_index: ++this.index,
        run_id: this.runId, recorded_at: new Date().toISOString(), type, payload };
      await this.handle.writeFile(`${JSON.stringify(record)}\n`);
      await this.handle.sync();
      return record;
    });
    this.queue = operation; // A write failure poisons the ledger and stops collection.
    return operation;
  }
  async capture(full) {
    const { capture, context, serializedSnapshot } = full;
    const bytes = Buffer.from(serializedSnapshot, "utf8");
    if (sha256(bytes) !== capture.sha256 || bytes.length !== capture.total_bytes
      || canonical(JSON.parse(serializedSnapshot)) !== canonical(context.snapshot)
      || capture.source_snapshot_id !== context.snapshot.snapshot_id
      || canonical(capture.session) !== canonical(context.snapshot.session)
      || capture.game_continuity_id !== context.game_continuity_id)
      throw new Error("raw_capsule_identity_mismatch");
    if (this.captureBytes + bytes.length > this.maxBytes || this.captureCount >= this.maxCaptures)
      throw new Error("raw_capture_budget_exceeded");
    if (!/^[A-Za-z0-9_.-]+$/u.test(capture.capture_id)) throw new Error("unsafe_capture_id");
    const filename = `captures/${String(++this.captureCount).padStart(5, "0")}-${capture.capture_id}.json`;
    const capsuleFile = await open(path.join(this.directory, filename), "wx");
    try { await capsuleFile.writeFile(bytes); await capsuleFile.sync(); }
    finally { await capsuleFile.close(); }
    this.captureBytes += bytes.length;
    await this.append("capture", { capture, snapshot_path: filename, bytes: bytes.length,
      sha256: sha256(bytes) });
    this.latest = { capture, snapshot: context.snapshot, snapshot_path: filename };
  }
  beginOffer(input) {
    const latest = this.latest;
    if (!latest || canonical(latest.snapshot) !== canonical(input.bundle.observation))
      throw new Error("policy_offer_has_no_latest_exact_capsule");
    const snapshot = input.bundle.observation, catalog = snapshot.menu_actions;
    const actions = catalog.actions;
    if (snapshot.schema !== "sts2.player-environment/text-menu-snapshot-2"
      || snapshot.input_profile !== "text-menu-v2" || snapshot.status !== "interactive"
      || snapshot.completeness.status !== "complete" || catalog.status !== "complete"
      || !actions.length || catalog.materialized_count !== actions.length || catalog.total_count !== actions.length
      || input.bundle.reads.length !== 0 || input.previous_interaction !== undefined)
      throw new Error("policy_offer_requires_admitted_observation_only_catalog");
    if (input.candidate_count !== actions.length
      || input.candidate_digest !== sha256(Buffer.from(JSON.stringify(actions.map(action => action.action_id)))))
      throw new Error("policy_offer_catalog_binding_mismatch");
    const offerId = `offer-${++this.offerCount}`;
    const committed = this.append("policy_offer", { offer_id: offerId, capture_id: latest.capture.capture_id,
      capture_ordinal: latest.capture.capture_ordinal, snapshot_path: latest.snapshot_path,
      snapshot_sha256: latest.capture.sha256, snapshot_id: latest.snapshot.snapshot_id,
      input_spec: INPUT_SPEC, source_kind: "agent", actor: this.actor,
      continuity_token: input.continuity_token, candidate_digest: input.candidate_digest,
      candidate_count: input.candidate_count, I: false, F: false });
    return { offerId, committed };
  }
  async offer(input) {
    const { offerId, committed } = this.beginOffer(input);
    await committed;
    return offerId;
  }
  async policyResult(offerId, input, value) {
    const snapshot = input.bundle.observation;
    if (value.completion.continuity_token !== input.continuity_token
      || value.completion.snapshot_id !== snapshot.snapshot_id || value.completion.sequence !== snapshot.sequence
      || value.output.candidate_digest !== input.candidate_digest
      || value.output.scores.length !== input.candidate_count
      || value.output.scores.some(score => !Number.isFinite(score))
      || !(value.output.selected_index === null || Number.isInteger(value.output.selected_index)
        && value.output.selected_index >= 0 && value.output.selected_index < input.candidate_count))
      throw new Error("policy_result_binding_mismatch");
    await this.append("policy_result", { offer_id: offerId, output: value.output,
      completion: value.completion, chosen_action_id: value.output.selected_index === null ? null
        : snapshot.menu_actions.actions[value.output.selected_index].action_id });
  }
  async close() {
    if (this.closed) return;
    this.closed = true;
    try { await this.queue; } finally { await this.handle.close(); }
  }
}
