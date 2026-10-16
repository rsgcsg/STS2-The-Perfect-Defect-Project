import { createHash } from "node:crypto";
import { readFile, readdir, lstat } from "node:fs/promises";
import { join } from "node:path";
import { readPublicBasisRecord, decodePublicSourceReceipt, verifyPublicBasis, decodePublicBasisHandle, decodePublicBasisChunk, assemblePublicBasis, type FullPublicBasis } from "@rsgcsg/sts2-connector-client";
import { createSession, reduce, modelView, applyCommitment, resolveFollowBasis, commitmentView, P1G_RESULT_SCHEMA, canonicalJson, type P1SessionState, type Lowering } from "@rsgcsg/sts2-connector-client/p1-global";
import { p1CoreIdentity } from "./p1-global/core-identity.js";
import { validateAgentManifest, validateAgentConsumption, validateAgentDirective, supportsProfileValue } from "./agent-session-contracts.js";
import { globalDigest, assertGlobalIdentity, assertGlobalProgramSupport, type GlobalIdentity } from "./global-transport.js";
import { AgentConsumptionLedger } from "./agent-session-consumption.js";
import type { GlobalArtifact } from "./evidence.js";

function require(condition: unknown, reason: string): asserts condition { if (!condition) throw new Error(reason); }
function exact(value: any, keys: string[]): Record<string, any> {
  require(value && typeof value === "object" && !Array.isArray(value) && canonicalJson(Object.keys(value).sort()) === canonicalJson([...keys].sort()), "global_closed_fields"); return value;
}
const same = (a: unknown, b: unknown) => canonicalJson(a) === canonicalJson(b);
const sha = (v: Uint8Array) => createHash("sha256").update(v).digest("hex");
/** Same compiled-code identity used by the production CLI, supplied by a trusted installed package. */
export async function globalRuntimeCodeDigest(directory: string): Promise<string> {
  const digest = createHash("sha256");
  const names = (await readdir(directory)).filter(name => name.endsWith(".js")).sort(); require(names.length, "runtime_code_absent");
  for (const name of names) digest.update(name).update("\0").update(await readFile(join(directory, name))).update("\0");
  return digest.digest("hex");
}
/** Original source accounting and actual Agent bytes are replayed independently of saved projections.
 * No network, native execution, inference, native qualification or research admission. */
export async function replayGlobalAgentRun(directory: string) {
  const json = async (name: string) => JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(await readFile(join(directory, name))));
  const run = await json("manifest.json"), manifest = validateAgentManifest(await json("agent-manifest.json"));
  require(manifest.input.profile === "p1-global-r1" && manifest.input.global, "global_profile_required");
  require(run.schema === "sts2.policy-runtime/agent-session-run-1" && run.agent_manifest_sha256 === globalDigest(manifest)
    && run.ended_at !== null && ["stopped", "tainted", "completed"].includes(run.status), "global_run_not_closed");
  require(manifest.input.global.core_sha256 === p1CoreIdentity().module_digest, "global_core_identity_changed");
  const attestation = await json("adapter-attestation.json");
  require(attestation.run_id === run.run_id && attestation.agent_manifest_sha256 === run.agent_manifest_sha256
    && same(attestation.expected, manifest.adapter) && attestation.status === "attested" && same(attestation.actual, manifest.adapter), "global_adapter_identity");
  const rawEvents = await readFile(join(directory, "events.jsonl"));
  require(rawEvents.length > 0 && rawEvents.length <= 128 * 1024 * 1024 && rawEvents.at(-1) === 10, "global_incomplete_journal");
  const events: { schema: string; sequence: number; recorded_at: string; kind: string; payload: any }[] = new TextDecoder("utf-8", { fatal: true }).decode(rawEvents).trim().split("\n").map(line => JSON.parse(line));
  require(events.length <= 65536 && events.at(-1)?.kind === "stopped", "global_stop_required");
  const referenced = new Set<string>();
  const artifact = async (ref: GlobalArtifact, role: GlobalArtifact["role"]): Promise<Buffer> => {
    exact(ref, ["role", "path", "bytes", "sha256"]);
    require(ref.role === role && /^[a-f0-9]{64}$/u.test(ref.sha256) && ref.path === `global-${ref.sha256}.bin`
      && Number.isSafeInteger(ref.bytes) && ref.bytes > 0 && ref.bytes <= 96 * 1024 * 1024, "global_artifact_reference");
    const path = join(directory, ref.path), stat = await lstat(path); require(stat.isFile() && !stat.isSymbolicLink() && stat.size === ref.bytes, "global_artifact_file");
    const bytes = await readFile(path); require(sha(bytes) === ref.sha256, "global_artifact_bytes"); referenced.add(ref.path); return bytes;
  };
  let state: P1SessionState | undefined, identity: GlobalIdentity | undefined, epoch = -1, session: string | undefined, lastSource = 0;
  let lastDirective: any = null, active: { acquisition: string; choice: string; lowering: Lowering; occurrence: number; epoch: number; deliveries: any[]; dispatches: any[]; postSources: Map<string, number[]>; errors: Set<string> } | null = null;
  let observations = 0, consumed = 0, results = 0, unknown = false, stopped = false, episode: string | null = null;
  const sources = new Map<number, { full: FullPublicBasis; reduction?: ReturnType<typeof reduce>; classified: boolean; valid: boolean; before?: P1SessionState }>();
  let mode: string | null = null, control = "released", permanentlyFenced = false;
  let ledger: AgentConsumptionLedger | undefined;
  const durableAcks = new Map<string, any>();
  const inputs = new Map<string, { input: any; source: number; occurrence: number }>();
  const offers = new Map<string, any>(), consumedFrames = new Map<string, any>(), acknowledgements = new Set<string>();
  const captureRequests = new Map<string, { terminal: boolean; handle?: any; chunks: ReturnType<typeof decodePublicBasisChunk>[]; completed?: FullPublicBasis }>();
  const classifiedReceipts = new Set<string>();
  const directives: any[] = [], responses = new Map<string, Buffer[]>(), dispatches = new Map<string, any>();
  const fields: Record<string, string[]> = {
    global_episode_bound: ["game_continuity_id", "source_ordinal"], global_source_rejected: ["source_ordinal", "reason"], global_source_retained: ["source_ordinal", "disposition"],
    global_capture_requested: ["request_id"], global_capture_error: ["request_id", "reason"],
    global_initialized: ["identity", "core"], global_transport_response: ["artifact", "operation"], global_source: ["ordinal", "artifact", "capture_request_id"],
    global_observation: ["source_ordinal", "occurrence", "complete", "missing", "status", "excluded", "observation_sha256"],
    global_input: ["acquisition_id", "source_ordinal", "occurrence", "artifact", "unit"], global_agent_frame: ["direction", "artifact"],
    global_consumed: ["request_id", "report", "acknowledgement"], global_choice: ["acquisition_id", "occurrence", "choice_id", "origin"],
    global_dispatch: ["request_id", "acquisition_id", "source_ordinal", "choice_id", "step_index", "bound_action", "expected_snapshot_id"],
    global_receipt: ["request_id", "source_ordinal", "receipt"], global_dispatch_error: ["request_id", "sent", "reason"], global_result: ["acquisition_id", "result"],
    agent_directive: ["output"], controller_acquired: ["controller"], controller_released: ["controller"], controller_release_unknown: ["controller", "reason"],
    mode_changed: ["mode", "autonomy_budget", "controller"], autonomy_budget_exhausted: ["reason", "autonomy_budget", "controller"],
    handoff_to_human: ["reason", "autonomy_budget", "controller"], fail_closed: ["reason", "agent_state"], runtime_tainted: ["reason", "retry"],
    stopped: ["autonomy_budget", "controller", "pending_request", "agent_state"]
  };
  for (const [index, event] of events.entries()) {
    exact(event, ["schema", "sequence", "recorded_at", "kind", "payload"]);
    require(event.schema === "sts2.policy-runtime/agent-session-event-1" && event.sequence === index + 1 && Number.isFinite(Date.parse(event.recorded_at)) && !stopped, "global_journal_sequence");
    const p = event.payload, extra = event.kind === "global_transport_response" ? p.operation === "step" ? ["request_id"] : ["capture_request_id"] : [];
    require(fields[event.kind], "global_unknown_event_kind"); exact(p, ["session_id", "recovery_epoch", ...fields[event.kind]!, ...extra]);
    require(typeof p.session_id === "string" && (!session || session === p.session_id) && Number.isSafeInteger(p.recovery_epoch) && p.recovery_epoch >= epoch, "global_context_order");
    session = p.session_id; epoch = p.recovery_epoch;
    if (event.kind === "global_initialized") {
      require(!identity && same(p.core, p1CoreIdentity()), "global_initial_identity"); identity = p.identity;
      const expected = manifest.requirements.environment; require("kind" in expected && identity, "global_environment_contract");
      assertGlobalIdentity(identity, expected); assertGlobalProgramSupport(identity, manifest); state = createSession(run.run_id, identity.kind);
    } else if (event.kind === "global_capture_requested") {
      require(identity && typeof p.request_id === "string" && !captureRequests.has(p.request_id), "global_capture_request_identity");
      captureRequests.set(p.request_id, { terminal: false, chunks: [] });
    } else if (event.kind === "global_capture_error") {
      const request = captureRequests.get(p.request_id); require(request && typeof p.reason === "string", "global_capture_error_identity"); request.terminal = true;
    } else if (event.kind === "global_source") {
      require(identity && state && p.ordinal === ++lastSource, "global_source_order");
      const full = await readPublicBasisRecord(new TextDecoder("utf-8", { fatal: true }).decode(await artifact(p.artifact, "source")));
      const request = captureRequests.get(p.capture_request_id);
      require(request && !request.terminal && request.completed && same(request.completed.capture, full.capture)
        && request.completed.serializedObservation === full.serializedObservation, "global_source_not_original_transport"); request.terminal = true;
      const valid = same(full.capture.session, identity.session) && (identity.game_continuity_id === null || identity.game_continuity_id === full.capture.game_continuity_id)
        && (episode === null || episode === full.capture.game_continuity_id);
      sources.set(p.ordinal, { full, classified: false, valid });
    } else if (event.kind === "global_episode_bound") {
      const source = sources.get(p.source_ordinal);
      require(episode === null && source?.valid && typeof p.game_continuity_id === "string" && p.game_continuity_id.length
        && source.full.capture.game_continuity_id === p.game_continuity_id, "global_episode_binding"); episode = p.game_continuity_id;
    } else if (event.kind === "global_source_rejected") {
      const source = sources.get(p.source_ordinal); require(source && !source.classified && typeof p.reason === "string", "global_source_rejection"); source.classified = true;
    } else if (event.kind === "global_source_retained") {
      const source = sources.get(p.source_ordinal); require(source?.valid && p.disposition === "pending_next_receive", "global_pending_source"); source.classified = true;
      require(active && active.dispatches.length === active.deliveries.length
        && active.deliveries.at(-1)?.delivery === "delivered", "global_post_source_without_delivery");
      const dispatch = active.dispatches.at(-1)!;
      const prior = active.postSources.get(dispatch.request_id) ?? [];
      require(p.source_ordinal > (prior.at(-1) ?? dispatch.source_ordinal), "global_post_source_order");
      active.postSources.set(dispatch.request_id, [...prior, p.source_ordinal]);
    } else if (event.kind === "global_observation") {
      const source = sources.get(p.source_ordinal); require(source?.valid && state && !source.reduction, "global_observation_source");
      source.before = state;
      const reduction = reduce(state, source.full.basis); source.reduction = reduction; source.classified = true; observations++;
      require(p.observation_sha256 === globalDigest(reduction.observation) && p.occurrence === reduction.observation.envelope.occurrence
        && p.complete === reduction.complete && p.status === reduction.observation.status && same(p.missing, reduction.missing) && same(p.excluded, reduction.excluded), "global_projection_changed");
      const snapshot = source.full.basis.snapshot;
      if (snapshot.status !== "settling" && !(snapshot.status === "observed" && reduction.observation.status !== "terminal")
        && source.full.basis.capture_status === "complete" && reduction.complete && reduction.observation.status !== "unsupported") state = reduction.state;
    } else if (event.kind === "global_input") {
      const source = sources.get(p.source_ordinal), r = source?.reduction; require(r?.complete && (episode !== null || r.observation.status === "terminal") && source!.full.basis.capture_status === "complete" && !inputs.has(p.acquisition_id), "global_input_source_incomplete");
      require(supportsProfileValue(manifest.support.interaction_kinds, r.observation.scene.native_kind), "global_interaction_unsupported");
      const input = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(await artifact(p.artifact, "public_input")));
      exact(input, ["acquisition_id", "input_spec", "continuity_token", "previous_consumption_id", "observation", "catalog", "consumption_unit"]);
      require(input.acquisition_id === p.acquisition_id && input.consumption_unit.id === p.unit && same(input.input_spec, manifest.input.input_spec)
        && p.occurrence === r.observation.envelope.occurrence && same(input.observation, modelView(r.observation)) && same(input.catalog, r.observation.catalog), "global_actual_public_input_changed");
      ledger ??= new AgentConsumptionLedger(manifest, input.continuity_token);
      require(input.previous_consumption_id === ledger.consumptionId && input.continuity_token === ledger.continuityToken, "global_input_ledger_prefix");
      ledger.registerGlobal({ acquisition_id: p.acquisition_id, capture: { source_ordinal: p.source_ordinal, source_sha256: source!.full.capture.sha256 },
        observation: input.observation, catalog: input.catalog, catalog_materialized: true, publication_index: String(p.occurrence) }, source!.full.basis, source!.before!);
      inputs.set(p.acquisition_id, { input, source: p.source_ordinal, occurrence: p.occurrence });
    } else if (event.kind === "global_agent_frame") {
      require(p.direction === "in" || p.direction === "out", "global_frame_direction");
      const bytes = await artifact(p.artifact, p.direction === "in" ? "agent_in" : "agent_out");
      require(bytes.at(-1) === 10 && bytes.subarray(0, -1).indexOf(10) === -1, "global_agent_frame_boundary");
      let frame: any;
      try { frame = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); }
      catch { require(p.direction === "in" && run.status !== "completed", "global_invalid_agent_frame"); continue; }
      require(frame.schema === "sts2.policy-runtime/agent-session-1", "global_agent_frame_schema");
      if (frame.message_type === "ready") { require(p.direction === "in" && same(frame.adapter, manifest.adapter), "global_ready"); continue; }
      require(frame.session_id === session && Number.isSafeInteger(frame.recovery_epoch) && frame.recovery_epoch <= epoch, "global_frame_context");
      if (p.direction === "out" && frame.message_type === "consume") {
        const original = inputs.get(frame.input.acquisition_id); require(original && same(original.input, frame.input) && !offers.has(frame.request_id), "global_offer_not_original"); offers.set(frame.request_id, frame);
      } else if (p.direction === "in" && frame.message_type === "consumed") {
        const offer = offers.get(frame.request_id); const report = validateAgentConsumption(frame.completion);
        require(offer && same(report.input_spec, offer.input.input_spec) && report.acquisition_id === offer.input.acquisition_id
          && report.continuity_token === offer.input.continuity_token && report.previous_consumption_id === offer.input.previous_consumption_id && !consumedFrames.has(frame.request_id), "global_consumption_not_offered");
        consumedFrames.set(frame.request_id, report);
      } else if (p.direction === "out" && frame.message_type === "consume_ack") {
        require(durableAcks.has(frame.request_id) && same(frame.completion, durableAcks.get(frame.request_id)) && !acknowledgements.has(frame.completion.acquisition_id), "global_ack_without_consumption"); acknowledgements.add(frame.completion.acquisition_id);
      } else if (p.direction === "in" && frame.message_type === "directive") directives.push(validateAgentDirective(frame.output));
      else require(p.direction === "out" && frame.message_type === "next", "global_unimplemented_program_frame");
    } else if (event.kind === "global_consumed") {
      const frame = consumedFrames.get(p.request_id); require(frame && ledger && !durableAcks.has(p.request_id) && same(frame, p.report), "global_durable_ack_without_original_report");
      ledger.noteReceived(p.acknowledgement.prefix.received_cursor, 1);
      const ack = ledger.accept(frame); require(same(ack, p.acknowledgement), "global_durable_ack_changed"); durableAcks.set(p.request_id, ack); consumed++;
    } else if (event.kind === "agent_directive") {
      const index = directives.findIndex(d => same(d, p.output)); require(index >= 0 && ledger && p.output.continuity_token === ledger.continuityToken && p.output.consumption_id === ledger.consumptionId && p.output.state_version === ledger.stateVersion, "global_directive_not_from_agent"); directives.splice(index, 1); lastDirective = p.output;
    } else if (event.kind === "global_choice") {
      require(mode !== null && mode !== "human" && mode !== "shadow" && !permanentlyFenced && !unknown && !active && lastDirective?.directive.type === "act" && lastDirective.directive.basis_acquisition_id === p.acquisition_id
        && lastDirective.directive.selection.kind === "handle" && lastDirective.directive.selection.action_id === p.choice_id && acknowledgements.has(p.acquisition_id), "global_choice_not_acknowledged");
      const input = inputs.get(p.acquisition_id), reduction = input && sources.get(input.source)?.reduction;
      require(input && reduction && supportsProfileValue(manifest.support.action_verbs,
        reduction.observation.catalog.find(c => c.choice_id === p.choice_id)?.kind ?? "") && p.occurrence === input.occurrence && p.origin === "agent_choice" && reduction.lowering[p.choice_id], "global_choice_not_in_catalog");
      active = { acquisition: p.acquisition_id, choice: p.choice_id, lowering: reduction.lowering[p.choice_id]!, occurrence: input.occurrence, epoch: p.recovery_epoch,
        deliveries: [], dispatches: [], postSources: new Map(), errors: new Set() };
    } else if (event.kind === "global_dispatch") {
      require(active && mode !== null && mode !== "human" && mode !== "shadow" && control === "held" && !permanentlyFenced && !unknown && p.acquisition_id === active.acquisition && p.choice_id === active.choice && p.step_index === active.dispatches.length && !dispatches.has(p.request_id), "global_dispatch_sequence");
      const source = sources.get(p.source_ordinal); require(source?.valid, "global_dispatch_source"); source.classified = true;
      const bound = (source.full.basis.snapshot.bound_actions as any).actions.find((a: any) => a.bound_action_id === p.bound_action.bound_action_id);
      if (p.step_index > 0) {
        const prior = active.dispatches.at(-1)!;
        require(active.deliveries.at(-1)?.delivery === "delivered"
          && active.postSources.get(prior.request_id)?.at(-1) === p.source_ordinal, "global_fold_not_current_post_source");
      }
      const step = p.step_index === 0 ? active.lowering.first : active.lowering.follow[p.step_index - 1]
        && source.full.basis.capture_status === "complete"
        && resolveFollowBasis(active.lowering.follow[p.step_index - 1]!, active.lowering, source.full.basis);
      require(bound && step && step.bound_action_id === bound.bound_action_id && same(bound, p.bound_action) && p.expected_snapshot_id === source.full.basis.snapshot.snapshot_id, "global_dispatch_not_shared_lowering");
      active.dispatches.push(p); dispatches.set(p.request_id, p);
    } else if (event.kind === "global_transport_response") {
      const bytes = await artifact(p.artifact, "response"); require(["capture", "step"].includes(p.operation), "global_transport_operation");
      if (p.operation === "step") { require(dispatches.has(p.request_id), "global_response_without_dispatch"); responses.set(p.request_id, [...(responses.get(p.request_id) ?? []), bytes]); }
      else {
        const request = captureRequests.get(p.capture_request_id); require(request && !request.terminal, "global_capture_response_identity");
        let wire: any; try { wire = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); } catch { continue; }
        if (wire.schema === "sts2.player-environment/public-basis-handle-1") {
          require(!request.handle, "global_capture_handle_reused"); request.handle = decodePublicBasisHandle(wire);
        } else if (wire.schema === "sts2.player-environment/public-basis-chunk-1") {
          const chunk = decodePublicBasisChunk(wire);
          require(request.handle && !request.completed, "global_capture_chunk_without_handle");
          request.chunks.push(chunk);
          if (chunk.end) {
            let offset = 0;
            request.completed = await assemblePublicBasis(request.handle, async () => {
              const value = request.chunks[offset++]; require(value, "global_capture_original_chunk_missing"); return value;
            });
            require(offset === request.chunks.length, "global_capture_extra_original_chunk");
          }
        } else if (wire.result?.type === "capture_result") {
          const full = wire.result.full; request.completed = await verifyPublicBasis(full.capture, full.serializedObservation);
          require(same(full.basis, request.completed.basis), "global_managed_capture_mirror");
        }
      }
    } else if (event.kind === "global_receipt") {
      const dispatch = dispatches.get(p.request_id); require(active && dispatch
        && active.dispatches.at(-1)?.request_id === p.request_id && !classifiedReceipts.has(p.request_id)
        && active.deliveries.length + 1 === active.dispatches.length
        && p.source_ordinal === dispatch.source_ordinal, "global_receipt_without_dispatch");
      const receipt = decodePublicSourceReceipt(p.receipt).data; const { interaction_id: _i, label: _l, ...bound } = dispatch.bound_action;
      // Same rule as the Runtime: a request-level not_delivered rejection echoes only the bound action id.
      require(receipt.request_id === p.request_id && (receipt.delivery === "not_delivered"
        ? receipt.action.bound_action_id === bound.bound_action_id
        : same({ ...receipt.action, subject_referent_id: receipt.action.subject_referent_id ?? null }, { ...bound, subject_referent_id: bound.subject_referent_id ?? null })), "global_receipt_binding");
      require((responses.get(p.request_id) ?? []).some(bytes => { try { const wire = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); return same(wire.result?.receipt ?? wire, receipt); } catch { return false; } }), "global_receipt_not_original_bytes");
      classifiedReceipts.add(p.request_id); active.deliveries.push(receipt); if (receipt.delivery === "unknown") unknown = true;
    } else if (event.kind === "global_dispatch_error") {
      require(active && typeof p.sent === "boolean" && !active.errors.has(p.request_id), "global_dispatch_error_unbound");
      active.errors.add(p.request_id);
      if (p.sent && !classifiedReceipts.has(p.request_id)) { require(dispatches.has(p.request_id), "global_unknown_without_original_dispatch"); unknown = true; active.deliveries.push({ delivery: "unknown" }); }
    } else if (event.kind === "global_result") {
      require(active && p.acquisition_id === active.acquisition, "global_result_unbound");
      exact(p.result, ["schema", "occurrence", "choice_id", "status", "steps", "commitment_delta"]);
      require(p.result.schema === P1G_RESULT_SCHEMA && p.result.occurrence === active.occurrence
        && p.result.choice_id === active.choice && Array.isArray(p.result.steps)
        && p.result.steps.length === active.deliveries.length, "global_result_prefix_changed");
      for (const [i, step] of p.result.steps.entries()) {
        exact(step, ["index", "origin", "native_verb", "label", "delivery", "reason_code"]);
        const receipt = active.deliveries[i], dispatch = active.dispatches[i];
        require(dispatch && step.index === i && step.origin === (i ? "fold" : "agent_choice")
          && step.native_verb === dispatch.bound_action.verb && step.label === dispatch.bound_action.label
          && step.delivery === receipt.delivery && step.reason_code === (receipt.request_id
            ? receipt.reason_code ?? null : "response_unclassified"), "global_result_step_changed");
      }
      require(["completed", "interrupted_known", "not_started", "unknown"].includes(p.result.status)
        && (p.result.status === "unknown") === active.deliveries.some(r => r.delivery === "unknown"), "global_result_knowledge");
      const delivered = active.deliveries.filter(r => r.delivery === "delivered").length;
      if (p.result.status === "not_started") require(delivered === 0, "global_delivered_relabelled_not_started");
      if (p.result.status === "interrupted_known") require(delivered > 0, "global_interruption_without_delivered_prefix");
      let delta = null;
      if (p.result.status === "completed") {
        require(mode !== null && mode !== "human" && mode !== "shadow" && !permanentlyFenced
          && p.recovery_epoch === active.epoch && active.errors.size === 0, "global_completion_after_handoff_or_failure");
        require(state && (!active.lowering.first ? active.deliveries.length === 0
          : delivered === active.deliveries.length && delivered === active.dispatches.length && delivered > 0), "global_completion_unproved");
        for (const dispatch of active.dispatches) {
          const postOrdinal = active.postSources.get(dispatch.request_id)?.at(-1);
          require(postOrdinal && sources.get(postOrdinal)?.full.basis.capture_status === "complete", "global_completed_post_source_missing_or_partial");
        }
        if (active.lowering.first) {
          const nextRule = active.lowering.follow[delivered - 1];
          if (nextRule) {
            const last = active.dispatches.at(-1)!;
            const post = sources.get(active.postSources.get(last.request_id)!.at(-1)!)!;
            require(nextRule.optional && post.full.basis.capture_status === "complete"
              && !resolveFollowBasis(nextRule, active.lowering, post.full.basis), "global_required_fold_omitted");
          }
          require(delivered <= 1 + active.lowering.follow.length, "global_extra_fold");
        }
        state = applyCommitment(state, active.lowering, true);
        if (active.lowering.commitment) delta = commitmentView(state);
      }
      require(same(p.result.commitment_delta, delta), "global_commitment_delta_changed");
      results++; active = null;
    } else if (event.kind === "mode_changed") {
      require(identity && ["human", "auto", "one_step", "shadow"].includes(p.mode)
        && p.controller === control && (!permanentlyFenced || p.mode === "human"), "global_mode_transition");
      if (p.mode !== "human") require(p.autonomy_budget?.state === "active" && p.autonomy_budget.remaining_ms > 0,
        "global_mode_without_active_budget");
      mode = p.mode;
    } else if (event.kind === "controller_acquired") {
      require(mode !== null && mode !== "human" && mode !== "shadow" && !permanentlyFenced
        && p.controller === "held", "global_control_without_authorization"); control = "held";
    } else if (event.kind === "controller_released") {
      require(p.controller === "released", "global_release_state"); control = "released";
    } else if (event.kind === "controller_release_unknown") {
      require(p.controller === "unknown" && run.tainted === true && run.status === "tainted", "global_unconfirmed_release_state");
      control = "unknown"; permanentlyFenced = true;
    } else if (["handoff_to_human", "autonomy_budget_exhausted"].includes(event.kind)) {
      require(p.controller === control && control !== "held", "global_handoff_control_state"); mode = "human";
    } else if (event.kind === "runtime_tainted") {
      require(p.retry === false && run.tainted === true, "global_taint_state"); mode = "human"; permanentlyFenced = true;
    } else if (event.kind === "fail_closed") { mode = "human";
    } else if (event.kind === "stopped") {
      require(index === events.length - 1 && p.pending_request === null && p.controller === control
        && control !== "held" && run.mode === "human", "global_final_closure"); stopped = true;
    }
  }
  require(stopped && !active && [...captureRequests.values()].every(r => r.terminal) && [...sources.values()].every(s => s.classified), "global_unaccounted_original_source_or_operation");
  const files = (await readdir(directory)).filter(name => /^global-/u.test(name)); require(files.every(name => referenced.has(name)) && referenced.size === files.length, "global_unreferenced_or_missing_original_artifact");
  return { schema: "sts2.policy-runtime/global-replay-1", run_id: run.run_id, sources: sources.size, observations, consumed, results,
    unknown_delivery: unknown, profile: manifest.input.profile, core: p1CoreIdentity(), verdict: "verified" as const,
    scope: "original_public_basis_semantic_replay_and_actual_agent_dispatch_joins", native_qualification: false, human_origin: false, research_admission: false };
}
