import { join } from "node:path";
import {
  applyCommitment, canonicalJson, createSession, reduce, resolveFollowBasis, sha256Hex,
  P1G_LIMITS,
  type Lowering, type P1Basis, type P1Observation, type P1Result, type P1SessionState, type P1StepRecord, type ReadCapture
} from "@rsgcsg/sts2-connector-client/p1-global";
import { AgentPort, AgentPortError, type AgentLaunch } from "./agent-port.js";
import { HostDriver, HostDriverError, type HostLaunch } from "./host-driver.js";
import { EpisodeRecorder } from "./recorder.js";
import { p1CoreIdentity } from "./core-identity.js";
import { buildP1Result } from "../p1-result.js";

export interface EpisodeOptions {
  readonly seed: string;
  readonly episodeId: string;
  readonly recordDir: string;
  readonly host: HostLaunch;
  readonly agent: AgentLaunch;
  readonly maxDecisions: number;
  readonly maxEpisodeSeconds: number;
  readonly settleTimeoutMs: number;
  readonly stop: { requested: boolean };
}

export interface EpisodeRow {
  seed: string; episode_id: string; started: boolean;
  terminal_outcome: "win" | "loss" | null; censor_reason: string | null; unknown_delivery: boolean;
  decisions: number; agent_choices: number; forced_steps: number; fold_steps: number; native_steps: number;
  occurrences: number; max_floor: number | null; max_act: number | null;
  families: Record<string, number>; choice_kinds: Record<string, number>; unmapped_choices: number;
  incomplete_reasons: string[]; elapsed_seconds: number;
  record: { path: string; lines: number; sha256: string } | null; host_close: unknown; agent: unknown;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Basis reference recorded with each observation so the converter can rebuild it from exact bytes. */
interface BasisRefs { snapshot_seq: number; snapshot_path: "snapshot" | "receipt.successor"; read_seqs: { read_id: string; seq: number | null }[] }

export async function runEpisode(options: EpisodeOptions): Promise<EpisodeRow> {
  const started = Date.now();
  const recordPath = join(options.recordDir, `${options.episodeId}.p1record.jsonl`);
  const recorder = new EpisodeRecorder(recordPath, {
    episode_id: options.episodeId, seed: options.seed, host_kind: options.host.kind,
    character: options.host.character, limits: P1G_LIMITS, p1_core: p1CoreIdentity(),
    budgets: { max_decisions: options.maxDecisions, max_episode_seconds: options.maxEpisodeSeconds }
  });
  const row: EpisodeRow = {
    seed: options.seed, episode_id: options.episodeId, started: false, terminal_outcome: null, censor_reason: null,
    unknown_delivery: false, decisions: 0, agent_choices: 0, forced_steps: 0, fold_steps: 0, native_steps: 0,
    occurrences: 0, max_floor: null, max_act: null, families: {}, choice_kinds: {}, unmapped_choices: 0,
    incomplete_reasons: [], elapsed_seconds: 0, record: null, host_close: null, agent: null
  };
  const host = new HostDriver(options.host, recorder);
  const agent = new AgentPort(options.agent, recorder);
  let state: P1SessionState = createSession(options.episodeId, options.host.kind);
  const deadline = started + options.maxEpisodeSeconds * 1000;

  // Reads advertised by the snapshot, captured against exactly that snapshot id.
  async function capture(snapshot: Record<string, any>, snapshotRef: { seq: number; path: BasisRefs["snapshot_path"] }) {
    const reads: ReadCapture[] = []; const refs: BasisRefs["read_seqs"] = [];
    for (const listed of snapshot.reads as { read_id: string; kind: string }[]) {
      try {
        const response = await host.request({ command: "read", read_id: listed.read_id, expected_snapshot_id: snapshot.snapshot_id });
        reads.push({ read_id: listed.read_id, kind: listed.kind, response: response.read, error: null });
        refs.push({ read_id: listed.read_id, seq: recorderSeq() });
      } catch (error) {
        reads.push({ read_id: listed.read_id, kind: listed.kind, response: null, error: String((error as Error).message) });
        refs.push({ read_id: listed.read_id, seq: null });
      }
    }
    const basis: P1Basis = { snapshot, reads };
    return { basis, refs: { snapshot_seq: snapshotRef.seq, snapshot_path: snapshotRef.path, read_seqs: refs } as BasisRefs };
  }
  // The recorder's last sequence number is the env_response just appended.
  const recorderSeq = () => (recorder as unknown as { seq: number }).seq;

  async function stable(snapshot: Record<string, any>, ref: { seq: number; path: BasisRefs["snapshot_path"] }) {
    const until = Date.now() + options.settleTimeoutMs;
    let delay = 100;
    while ((snapshot.status === "settling" || (snapshot.status === "observed" && snapshot.interaction?.kind !== "game_over"))
           && Date.now() < until) {
      await sleep(delay); delay = Math.min(delay * 2, 1000);
      const observed = await host.request({ command: "observe" });
      snapshot = observed.snapshot; ref = { seq: recorderSeq(), path: "snapshot" };
    }
    return { snapshot, ref };
  }

  try {
    await host.start();
    await agent.start({ episode_id: options.episodeId, host_kind: options.host.kind });
    row.agent = agent.identity;
    const reset = await host.request({ command: "reset", seed: options.seed }, 300_000);
    row.started = true;
    let current = await stable(reset.snapshot, { seq: recorderSeq(), path: "snapshot" });
    let forcedRun = 0;

    for (;;) {
      if (options.stop.requested) { row.censor_reason = "stop_requested"; break; }
      if (Date.now() > deadline) { row.censor_reason = "episode_wall_budget"; break; }
      if (row.decisions >= options.maxDecisions) { row.censor_reason = "decision_budget"; break; }

      // A consistent bundle needs every Read bound to the same current snapshot. If the game
      // moved on (stale), take a fresh observation and capture again (reads have no effect).
      let captured = await capture(current.snapshot, current.ref);
      for (let attempt = 0; attempt < 8 && captured.basis.reads.some((read) => read.response === null); attempt++) {
        await sleep(100 * (attempt + 1));
        const observed = await host.request({ command: "observe" });
        current = await stable(observed.snapshot, { seq: recorderSeq(), path: "snapshot" });
        captured = await capture(current.snapshot, current.ref);
      }
      const { basis, refs } = captured;
      const reduction = reduce(state, basis);
      state = reduction.state;
      const observation: P1Observation = reduction.observation;
      const canonical = canonicalJson(observation);
      recorder.append("p1_observation", { occurrence: observation.envelope.occurrence, status: observation.status,
        family: observation.scene.family, native_kind: observation.scene.native_kind, complete: reduction.complete,
        missing: reduction.missing, excluded_native: reduction.excluded, basis_refs: refs, sha256: sha256Hex(canonical), observation });
      row.occurrences = observation.envelope.occurrence;
      row.families[observation.scene.family] = (row.families[observation.scene.family] ?? 0) + 1;
      const run = observation.run.content as Record<string, any> | null;
      if (run) { row.max_floor = Math.max(row.max_floor ?? 0, Number(run.floor ?? 0)); row.max_act = Math.max(row.max_act ?? 0, Number(run.act ?? 0)); }

      if (observation.status === "terminal") {
        row.terminal_outcome = observation.terminal?.outcome === "win" ? "win" : observation.terminal?.outcome === "loss" ? "loss" : null;
        if (row.terminal_outcome === null) row.censor_reason = "terminal_outcome_unknown";
        agent.inform(observation, null);
        break;
      }
      if (observation.status !== "decision") {
        // The Host itself reports this surface as visible but unsupported (or not a decision point).
        const hostMissing = (current.snapshot.completeness?.missing ?? []) as string[];
        row.censor_reason = current.snapshot.status === "visible_unsupported" ? "host_unsupported_surface" : "no_decision_surface";
        row.incomplete_reasons.push(...reduction.missing, ...hostMissing.map((item) => `host:${item}`)); break;
      }
      if (!reduction.complete) { row.censor_reason = "incomplete_observation"; row.incomplete_reasons.push(...reduction.missing); break; }

      // Choose: declared forced input, or the Agent through its port.
      let choiceId: string; let origin: "agent_choice" | "forced";
      if (reduction.forced_choice_id && forcedRun < P1G_LIMITS.forced_steps_in_a_row) {
        choiceId = reduction.forced_choice_id; origin = "forced"; forcedRun += 1;
        agent.inform(observation, choiceId);
      } else {
        choiceId = await agent.choose(observation); origin = "agent_choice"; forcedRun = 0;
        row.agent_choices += 1;
      }
      row.decisions += 1;
      const choice = observation.catalog.find((item) => item.choice_id === choiceId)!;
      row.choice_kinds[choice.kind] = (row.choice_kinds[choice.kind] ?? 0) + 1;
      if (!choice.mapped) row.unmapped_choices += 1;
      if (origin === "forced") row.forced_steps += 1;
      const lowering: Lowering = reduction.lowering[choiceId]!;
      recorder.append("p1_choice", { occurrence: observation.envelope.occurrence, choice_id: choiceId, choice_kind: choice.kind, origin });

      // Execute: first native step, then declared follow steps, each from the current catalog.
      const steps: P1StepRecord[] = [];
      let status: P1Result["status"] = "completed";
      let nextSnapshot: { snapshot: Record<string, any>; ref: { seq: number; path: BasisRefs["snapshot_path"] } } | null = null;
      const plan: { step: { bound_action_id: string; verb: string; label: string } | null; origin: P1StepRecord["origin"] }[] =
        lowering.first ? [{ step: lowering.first, origin }] : [];
      let expectedSnapshotId = String(current.snapshot.snapshot_id);
      let followIndex = 0;
      while (plan.length > 0) {
        const next = plan.shift()!;
        if (!next.step) break;
        if (options.stop.requested) { status = steps.length === 0 ? "not_started" : "interrupted_known"; break; }
        const mutationId = host.newMutationId();
        recorder.append("p1_step", { occurrence: observation.envelope.occurrence, index: steps.length, origin: next.origin,
          bound_action_id: next.step.bound_action_id, verb: next.step.verb, mutation_request_id: mutationId });
        let receipt: Record<string, any>;
        try {
          receipt = (await host.request({ command: "step", mutation_request_id: mutationId,
            expected_snapshot_id: expectedSnapshotId, bound_action_id: next.step.bound_action_id }, options.host.stepTimeoutMs ?? 120_000)).receipt;
        } catch (error) {
          // A missing/failed step response is an unknown delivery: fence, never retry.
          steps.push({ index: steps.length, origin: next.origin, native_verb: next.step.verb, label: next.step.label, delivery: "unknown", reason_code: "step_response_missing" });
          status = "unknown"; break;
        }
        row.native_steps += 1;
        if (next.origin === "fold") row.fold_steps += 1;
        const delivery = receipt?.delivery as P1StepRecord["delivery"];
        steps.push({ index: steps.length, origin: next.origin, native_verb: next.step.verb, label: next.step.label,
          delivery: delivery ?? "unknown", reason_code: receipt?.reason_code ?? null });
        if (delivery === "unknown" || !delivery) { status = "unknown"; break; }
        if (delivery === "not_delivered") { status = steps.length === 1 ? "not_started" : "interrupted_known"; break; }
        const successor = receipt.successor
          ? { snapshot: receipt.successor, ref: { seq: recorderSeq(), path: "receipt.successor" as const } }
          : { snapshot: (await host.request({ command: "observe" })).snapshot, ref: { seq: recorderSeq(), path: "snapshot" as const } };
        nextSnapshot = await stable(successor.snapshot, successor.ref);
        expectedSnapshotId = String(nextSnapshot.snapshot.snapshot_id);
        const rule = lowering.follow[followIndex];
        if (rule) {
          followIndex += 1;
          const successorBasis = await capture(nextSnapshot.snapshot, nextSnapshot.ref);
          const follow = resolveFollowBasis(rule, lowering, successorBasis.basis);
          if (!follow) { if (!rule.optional) status = "interrupted_known"; break; } // new situation: back to the Agent
          plan.push({ step: follow, origin: "fold" });
        }
      }
      if (status === "unknown") { row.unknown_delivery = true; row.censor_reason = "unknown_delivery"; }
      if (status === "completed") state = applyCommitment(state, lowering, true);
      const result = buildP1Result({ occurrence: observation.envelope.occurrence,
        choice_id: choiceId, status, steps, commitment_delta: status === "completed" && lowering.commitment
          ? { declined_reward_groups: (state.reward?.declined ?? []).map((key) => state.aliases[key] ?? "unaliased").sort(),
            reward_page_left: state.reward?.left ?? false } : null });
      recorder.append("p1_result", { result });
      agent.result(result);
      if (status === "unknown") break;
      if (nextSnapshot) current = nextSnapshot;
      else {
        // Pure P1 commitment or known non-delivery: re-observe; the reducer decides the occurrence.
        const observed = await host.request({ command: "observe" });
        current = await stable(observed.snapshot, { seq: recorderSeq(), path: "snapshot" });
      }
    }
  } catch (error) {
    const code = error instanceof HostDriverError || error instanceof AgentPortError ? error.code : "runner_error";
    row.censor_reason ??= code;
    row.incomplete_reasons.push(`${code}:${String((error as Error).message).slice(0, 300)}`);
  } finally {
    recorder.append("episode_end", { row: { ...row, record: null } });
    await agent.end({ outcome: row.terminal_outcome, censor_reason: row.censor_reason }).catch(() => undefined);
    row.host_close = await host.close();
    row.elapsed_seconds = (Date.now() - started) / 1000;
    row.record = recorder.close({ outcome: row.terminal_outcome, censor_reason: row.censor_reason });
  }
  return row;
}
