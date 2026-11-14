import { createHash, randomUUID } from "node:crypto";
import { join } from "node:path";
import { performance } from "node:perf_hooks";
import { decodePublicSourceReceipt, type FullPublicBasis } from "@rsgcsg/sts2-connector-client";
import { createSession, reduce, ReduceStrictSampleFailure, modelView, applyCommitment, resolveFollowBasis, P1G_LIMITS,
  type P1SessionState } from "@rsgcsg/sts2-connector-client/p1-global";
import { canonicalJson } from "../evidence.js";
import { GlobalNoProgressWindow } from "../global-no-progress.js";
import { supportsProfileValue, type AgentManifest } from "../agent-session-contracts.js";
import { ResponseBinding } from "./binding.js";
import type { CloseReason, FastEnvCommand, FastEnvConfig, OutcomeReason, PublicFacts, SavePoints } from "./contracts.js";
import { FAST_ENV_LINK_SCHEMA } from "./contracts.js";
import { CanonicalTextMemoDivergence, globalDigestFast, jsonWithPySha256, pyJsonSha256, type CanonicalTextMemo } from "./json-bytes.js";
import type { ColdStartGate } from "./cold-start.js";
import type { DecisionLinkPort } from "./link.js";
import type { FastEvidenceSink, FastGameEvidence, FastGameMeta, FastStepRecord } from "./evidence-sink.js";
import { SampledCheckFailure, SaveMapFailure, type SampledCheckMismatch, type SlotNative, type SlotNativeFactory } from "./host.js";

type Reduction = ReturnType<typeof reduce>;
type OpenCommand = Extract<FastEnvCommand, { cmd: "open" }>;
type StepStatus = "completed" | "interrupted_known" | "not_started" | "unknown";

export interface KernelStats {
  games: number; decisions: number; forced: number; native_steps: number; settle_waits: number; full_checks: number;
  /** L1 omit: combat_end facts read before any model row of the game (no transition to carry them), or past the
   * contract's four per outcome; counted, never sent. */
  combat_end_unattributed: number;
  /** Games and opens ended by compact_state_divergence (Host- or native-detected), counted apart from other unknowns. */
  compact_divergences: number;
  /** E5: games and opens ended by projection_memo_divergence (the Host's sampled memo check), counted apart. */
  projection_memo_divergences: number;
  /** S1: games closed as budget `forced_steps_in_a_row` (more one-option steps in a row than the old chain allows),
   * counted apart from the other budget ends. */
  forced_chain_limit: number;
  capture_ms: number; reduce_ms: number; input_ms: number; link_wait_ms: number; step_ms: number; receipt_ms: number;
  /** Native cold starts and their wait for the node-wide gate. */
  cold_starts: number; cold_start_wait_ms: number;
  /** E4: memo divergences of this process (each one is fatal for the whole process). */
  memo_divergences: number;
  /** E5: the Host projection memo counters of every finished game (summed per native session). */
  projection_memo: { hits: number; misses: number; checks: number };
  /** Save tickets: Host map saves written, refused before any native write (game kept), failed or unknown
   * (game ended, native replaced), and the time spent in them. */
  saves: { written: number; refused: number; failed: number; unknown: number; ms: number };
}
export const emptyStats = (): KernelStats => ({ games: 0, decisions: 0, forced: 0, native_steps: 0, settle_waits: 0, full_checks: 0,
  combat_end_unattributed: 0, compact_divergences: 0, projection_memo_divergences: 0, forced_chain_limit: 0,
  capture_ms: 0, reduce_ms: 0, input_ms: 0, link_wait_ms: 0, step_ms: 0, receipt_ms: 0, cold_starts: 0, cold_start_wait_ms: 0,
  memo_divergences: 0, projection_memo: { hits: 0, misses: 0, checks: 0 }, saves: { written: 0, refused: 0, failed: 0, unknown: 0, ms: 0 } });

/** What every slot of one environment process shares. */
export interface KernelContext {
  config: FastEnvConfig;
  manifest: AgentManifest;
  factory: SlotNativeFactory;
  link: DecisionLinkPort;
  binding: ResponseBinding;
  evidence: FastEvidenceSink;
  stats: KernelStats;
  nextRowId(): number;
  emit(event: string, data: Record<string, unknown>): void;
  /** compact_state_divergence / sampled mismatch: this build's output stops. */
  stopBuildOutput(reason: string, slot: number, gameInstanceId: string | null): void;
  /** Evidence could not be made durable: loud and fatal for the whole process, never a fallback. */
  evidenceFailed(slot: number, gameInstanceId: string | null, reason: string): void;
  stopping(): boolean;
  sleep(ms: number): Promise<void>;
  /** Node-wide bound on native cold starts (start + first reset or map load). */
  coldStart: ColdStartGate;
  /** E4 (null = off): the receipt digest's canonical-text memo. */
  receiptCanonical: CanonicalTextMemo | null;
  /** A memo of this process (E4 receipt text) differed from its full walk: the whole process stops. */
  memoFatal(reason: string): void;
}

/** A game ends here; nothing is ever retried. */
/** Session divergences: the game ends unknown (never retried), is counted apart and stops this build's output.
 * The Host's memo divergence keeps its own close detail, build stop and counter; its pending outcome uses the
 * frozen rl-inference-4 divergence reason (the outcome enum has no separate memo value). */
type SessionDivergence = "compact_state_divergence" | "projection_memo_divergence";
const sessionDivergence = (value: unknown): SessionDivergence | null =>
  value === "compact_state_divergence" || value === "projection_memo_divergence" ? value : null;
const divergenceEnd = (divergence: SessionDivergence) => new GameEnd("not_started", divergence, { replaceNative: true,
  buildStop: divergence, pendingOutcome: { status: "unknown", reason: "compact_state_divergence" } });
const countDivergence = (stats: KernelStats, divergence: SessionDivergence) => {
  if (divergence === "compact_state_divergence") stats.compact_divergences += 1;
  else stats.projection_memo_divergences += 1;
};

class GameEnd extends Error {
  constructor(readonly reason: CloseReason, readonly detail: string,
              readonly options: { replaceNative?: boolean; buildStop?: string; pendingOutcome?: { status: "unknown"; reason: OutcomeReason };
                terminal?: { result: "victory" | "defeat" } | null; sampledMismatch?: SampledCheckMismatch; fatal?: string } = {}) {
    super(`${reason}:${detail}`);
  }
}

/** A strict sample of the C1 light reduce path disagreed, wherever reduce ran (the step's own reduce or
 * a follow-rule check): light reduces may already have put the same kind of bad data into other rows, so
 * this build stops producing output (like the Host's SampledCheckFailure), with its own reason and counter. */
const reduceSampleEnd = (error: ReduceStrictSampleFailure) => new GameEnd("env_crash", `reduce:${error.message}`, { replaceNative: true,
  buildStop: "reduce_strict_sample_failed", pendingOutcome: { status: "unknown", reason: "native_crash" } });

/** Memo divergences of this Node process (E4 receipt text memo): the game ends (unknown once an action was
 * delivered), never retried, this build's output stops and the whole process exits fatal. */
type MemoDivergence = "receipt_canonical_memo_divergence";
const memoDivergence = (error: unknown): MemoDivergence | null =>
  error instanceof CanonicalTextMemoDivergence ? "receipt_canonical_memo_divergence" : null;
const memoDivergenceEnd = (kind: MemoDivergence, error: unknown) => new GameEnd("not_started", kind, { replaceNative: true,
  buildStop: kind, fatal: `${kind}:${errorText(error)}`, pendingOutcome: { status: "unknown", reason: "native_crash" } });

const sha256 = (text: string) => createHash("sha256").update(text, "utf8").digest("hex");

/**
 * `full.capture.total_bytes > max`, the same verdict without measuring the bytes when the
 * text cannot reach the limit: total_bytes is the UTF-8 length of serializedObservation and
 * UTF-8 spends at most 3 bytes per UTF-16 code unit (a surrogate pair: 4 bytes for 2 units).
 * The fast-chain Host measures total_bytes only when it is read (R4).
 */
export function captureOverCapacity(full: Pick<FullPublicBasis, "capture" | "serializedObservation">, max: number): boolean {
  return full.serializedObservation.length * 3 > max && full.capture.total_bytes > max;
}
/** One line of an original error for a close detail (never parsed, never retried on). */
const errorText = (error: unknown) => (error instanceof Error ? `${error.name}:${error.message}` : String(error)).replace(/\s+/gu, " ").slice(0, 240);
const isObj = (value: unknown): value is Record<string, any> => value !== null && typeof value === "object" && !Array.isArray(value);

/** Player-visible run facts of one public observation (rl-inference-4 public_facts). */
export function publicFacts(observation: Reduction["observation"]): PublicFacts | null {
  const run = isObj(observation.run?.content) ? observation.run.content : null;
  const player = isObj(observation.player?.content) ? observation.player.content : null;
  const facts = { act: run?.act, floor: run?.floor, hp: player?.hp, max_hp: player?.max_hp };
  return Number.isSafeInteger(facts.act) && facts.act >= 1 && Number.isSafeInteger(facts.floor) && facts.floor >= 0
    && Number.isSafeInteger(facts.hp) && facts.hp >= 0 && Number.isSafeInteger(facts.max_hp) && facts.max_hp >= 1
    ? facts as PublicFacts : null;
}

function terminalResult(observation: Reduction["observation"]): { result: "victory" | "defeat" } | null {
  const outcome = observation.terminal?.outcome;
  return outcome === "win" ? { result: "victory" } : outcome === "loss" ? { result: "defeat" } : null;
}

interface Source { full: FullPublicBasis; frame: FastStepRecord["frame"] }
interface Prepared { source: Source; reduction: Reduction }
/** What a model row adds to its evidence record. */
interface RowRef { rowId: number; stepIndex: number; consumptionId: string; observationSha: string; catalogSha: string; inputLine: string }
/** The outcome of the last model row, sent once the state its step led to is known: at the next
 * model row or the terminal. With L1 omit the one-option steps in between run first; they add to
 * `forced` and their successors' combat_end facts, and `resolved` holds the public facts of the last
 * accepted state, so a game ending inside that chain still reports the row as completed. */
interface PendingOutcome {
  row_id: number; step_index: number; receipt_sha256: string; forced: number;
  combat_end: Record<string, unknown>[];
  resolved: PublicFacts | null;
}
/** rl-inference-4 step_outcome.combat_end maxItems. */
const COMBAT_END_PER_OUTCOME = 4;

/** One game on one slot: the receive/act loop of today's Runtime (receiveGlobal /
 * actGlobal), without the per-event evidence journal and NDJSON agent port. */
class GameRun {
  state: P1SessionState;
  decisions = 0;
  nativeSteps = 0;
  lastStepIndex: number | null = null;
  lastFacts: PublicFacts | null = null;
  #prepared: Prepared | null = null;
  #pendingSource: Source | null = null;
  #pendingOutcome: PendingOutcome | null = null;
  /** Why the last fold ended unknown; the close detail keeps it after `delivery_unknown:`. */
  #unknownCause: string | null = null;
  #unknownDetail(): string { return this.#unknownCause ? `delivery_unknown:${this.#unknownCause}` : "delivery_unknown"; }
  /** A native step was delivered and its successor has not been read and accepted yet. */
  awaitingSuccessor = false;
  #noProgress: GlobalNoProgressWindow | null;
  /** L1 omit: one-option steps executed since the last model row (S1 bound). */
  #forcedInARow = 0;
  readonly #started = performance.now();
  /** Save points of this game: floors already tried (one attempt each) and the mu of the last scored row. */
  #saveTried = new Set<number>();
  #lastMu: string | null = null;

  constructor(private readonly ctx: KernelContext, private readonly slot: SlotKernel, private readonly native: SlotNative,
              readonly gameInstanceId: string, private readonly evidence: FastGameEvidence,
              private readonly gameRef: string, private readonly savePoints: SavePoints | null) {
    this.state = createSession(`fast-${randomUUID()}`, "managed", ctx.manifest.input.global!.revision);
    const progress = ctx.config.no_progress;
    this.#noProgress = progress ? new GlobalNoProgressWindow({ windowDecisions: progress.window_decisions, maxRepeats: progress.max_repeats }) : null;
  }

  #outcome(rowId: number, stepIndex: number, status: "completed" | "not_started" | "unknown", extra: Record<string, unknown>): void {
    this.ctx.link.send({ type: "step_outcome", row_id: rowId, game_instance_id: this.gameInstanceId, step_index: stepIndex, status, ...extra });
  }

  /** The evidence record of one P1 choice; native steps and status are filled by #acted. */
  #stepRecord(prepared: Prepared, index: number, row: RowRef | null): FastStepRecord {
    const catalog = prepared.reduction.observation.catalog;
    const content = (item: (typeof catalog)[number]) => { const { choice_id: _handle, ...rest } = item; return canonicalJson(rest); };
    const selected = catalog[index]!, choiceJson = content(selected);
    let ordinal = 0;
    for (let j = 0; j < index; j += 1) {
      const other = catalog[j]!;
      if (other.kind === selected.kind && other.label === selected.label && content(other) === choiceJson) ordinal += 1;
    }
    return { row_id: row?.rowId ?? null, step_index: row?.stepIndex ?? null, consumption_id: row?.consumptionId ?? null,
      occurrence: prepared.reduction.observation.envelope.occurrence, snapshot_id: String(prepared.source.full.basis.snapshot.snapshot_id),
      frame: prepared.source.frame, observation_sha256: row?.observationSha ?? null, catalog_sha256: row?.catalogSha ?? pyJsonSha256(catalog),
      catalog_len: catalog.length, selected_index: index, choice_json: choiceJson, same_content_ordinal: ordinal,
      status: "not_started", native_steps: [], input_line: row?.inputLine ?? null };
  }

  /** Acts on one P1 choice and hands its record to evidence, whatever the outcome. */
  async #acted(prepared: Prepared, index: number, row: RowRef | null): Promise<StepStatus> {
    const record = this.#stepRecord(prepared, index, row);
    try {
      record.status = await this.#act(prepared, prepared.reduction.observation.catalog[index]!.choice_id,
        row ? { rowId: row.rowId, stepIndex: row.stepIndex } : null, record);
    } catch (error) {
      // A step handed to the Host without a usable successor: its effect is not known.
      record.status = record.native_steps.length > 0 ? "unknown" : "not_started";
      this.#recorded(record);
      throw error;
    }
    this.#recorded(record);
    if (this.#boundaryRows >= this.ctx.config.evidence.boundary_rows) await this.boundary();
    return record.status;
  }

  #boundaryFirst: number | null = null;
  #boundaryLast: number | null = null;
  #boundaryRows = 0;

  #recorded(record: FastStepRecord): void {
    this.evidence.step(record);
    if (record.row_id === null) return;
    this.#boundaryFirst ??= record.row_id;
    this.#boundaryLast = record.row_id;
    this.#boundaryRows += 1;
  }

  /** Durability point for the rows recorded since the last one (also called before close). */
  async boundary(): Promise<void> {
    if (this.#boundaryFirst === null || this.#boundaryLast === null) return;
    const range = { first_row_id: this.#boundaryFirst, last_row_id: this.#boundaryLast };
    this.#boundaryFirst = null; this.#boundaryLast = null; this.#boundaryRows = 0;
    // Evidence that cannot be made durable ends the game loudly; the game never continues unrecorded.
    try { await this.evidence.segmentBoundary(range); }
    catch (error) { throw new GameEnd("env_crash", `evidence_boundary:${error instanceof Error ? error.message : String(error)}`); }
  }

  /** Native combat_end facts read since the last call, carried by the pending row (S2: across a forced chain). */
  #collectCombatEnd(): void {
    const facts = this.native.takeCombatEnd();
    if (facts.length === 0) return;
    const pending = this.#pendingOutcome;
    if (!pending) { this.ctx.stats.combat_end_unattributed += facts.length; return; }
    for (const fact of facts) {
      if (pending.combat_end.length < COMBAT_END_PER_OUTCOME) pending.combat_end.push(fact);
      else this.ctx.stats.combat_end_unattributed += 1;
    }
  }

  #completed(pending: PendingOutcome, facts: PublicFacts, terminal: { result: "victory" | "defeat" } | null): void {
    this.#outcome(pending.row_id, pending.step_index, "completed", { receipt_sha256: pending.receipt_sha256, public_facts: facts, terminal,
      ...(this.ctx.config.inference.single_choice === "omit" ? { forced_steps: pending.forced } : {}),
      ...(pending.combat_end.length > 0 ? { combat_end: pending.combat_end } : {}) });
  }

  /** The step of an earlier row resolved; its outcome carries the facts of the state it led to. */
  #flushOutcome(facts: PublicFacts | null, terminal: { result: "victory" | "defeat" } | null): void {
    this.#collectCombatEnd();
    const pending = this.#pendingOutcome;
    if (!pending) return;
    this.#pendingOutcome = null;
    if (facts === null) {
      this.#outcome(pending.row_id, pending.step_index, "unknown", { reason: "native_crash" });
      throw new GameEnd("not_started", "required_information_unavailable:public_facts");
    }
    this.#completed(pending, facts, terminal);
  }

  /** L1 omit: a one-option state was accepted after the pending row's step. That row's step is
   * resolved (its successor was read and accepted), but its outcome waits for the end of the chain. */
  #resolveInChain(facts: PublicFacts | null): void {
    this.#collectCombatEnd();
    const pending = this.#pendingOutcome;
    if (pending && facts !== null) pending.resolved = facts;
  }

  /** Called with every GameEnd: an unresolved earlier step gets its honest outcome. A row whose
   * successor was already accepted inside a forced chain is completed, with the facts of the last
   * accepted state, whatever ended the game after it (Stop, budget, a forced step's failure). */
  failPending(end: GameEnd): void {
    // combat_end facts read by the step that ended the game: that step's successor was never
    // accepted, so they ride on no outcome; counted, never lost silently and never sent.
    this.ctx.stats.combat_end_unattributed += this.native.takeCombatEnd().length;
    const pending = this.#pendingOutcome;
    if (!pending) return;
    this.#pendingOutcome = null;
    if (pending.resolved !== null) { this.#completed(pending, pending.resolved, null); return; }
    // An outcome that is not completed carries no combat_end: its facts are counted, not sent.
    this.ctx.stats.combat_end_unattributed += pending.combat_end.length;
    const fallback = end.options.pendingOutcome ?? { status: "unknown" as const, reason: "native_crash" as const };
    this.#outcome(pending.row_id, pending.step_index, fallback.status, { reason: fallback.reason });
  }

  async #capture(): Promise<Source> {
    const started = performance.now();
    try {
      const full = await this.native.capture();
      if (captureOverCapacity(full, this.ctx.manifest.limits.max_capture_bytes)) throw new GameEnd("not_started", "capture_capacity");
      return { full, frame: this.native.frame() };
    } catch (error) {
      if (error instanceof GameEnd) throw error;
      if (error instanceof SampledCheckFailure) {
        // Same close as any capture failure today, plus: this build stops producing output.
        throw new GameEnd("env_crash", `capture:${error.message}`, { replaceNative: true, buildStop: "sampled_check_failed",
          pendingOutcome: { status: "unknown", reason: "native_crash" }, sampledMismatch: error.mismatch });
      }
      const divergence = sessionDivergence(this.native.taint().reason);
      if (divergence !== null) throw divergenceEnd(divergence);
      throw new GameEnd("env_crash", `capture:${error instanceof Error ? error.message : String(error)}`,
        { replaceNative: true, pendingOutcome: { status: "unknown", reason: "native_crash" } });
    } finally { this.ctx.stats.capture_ms += performance.now() - started; }
  }

  async #receive(): Promise<Prepared> {
    const since = performance.now();
    for (;;) {
      const source = this.#pendingSource ?? await this.#capture();
      this.#pendingSource = null;
      const reduceStarted = performance.now();
      let reduction: Reduction;
      try { reduction = reduce(this.state, source.full.basis); }
      finally { this.ctx.stats.reduce_ms += performance.now() - reduceStarted; }
      const snapshot = source.full.basis.snapshot;
      if (snapshot.status === "settling" || snapshot.status === "observed" && reduction.observation.status !== "terminal") {
        if (performance.now() - since >= this.ctx.config.settling_timeout_ms) throw new GameEnd("not_started", "managed_settling_stalled");
        this.ctx.stats.settle_waits += 1;
        await this.ctx.sleep(50);
        continue;
      }
      if (source.full.basis.capture_status !== "complete" || !reduction.complete || reduction.observation.status === "unsupported"
          || source.full.capture.game_continuity_id === null && reduction.observation.status !== "terminal")
        throw new GameEnd("not_started", "required_information_unavailable");
      if (!supportsProfileValue(this.ctx.manifest.support.interaction_kinds, reduction.observation.scene.native_kind))
        throw new GameEnd("not_started", "interaction_unsupported");
      this.state = reduction.state;
      const prior = this.#prepared;
      if (prior && prior.reduction.observation.envelope.occurrence === reduction.observation.envelope.occurrence
          && canonicalJson(modelView(reduction.observation)) !== canonicalJson(modelView(prior.reduction.observation)))
        throw new GameEnd("not_started", "same_occurrence_drift");
      this.#prepared = { source, reduction };
      return this.#prepared;
    }
  }

  async play(): Promise<{ reason: CloseReason; detail: string; terminal: { result: "victory" | "defeat" } | null }> {
    const { config, stats, link } = this.ctx;
    for (;;) {
      const prepared = await this.#receive();
      const observation = prepared.reduction.observation;
      const catalog = observation.catalog;
      const facts = publicFacts(observation);
      if (observation.status === "terminal" || catalog.length === 0) {
        const terminal = terminalResult(observation);
        this.#flushOutcome(facts, terminal);
        this.lastFacts = facts;
        return { reason: "terminal", detail: observation.status, terminal };
      }
      // L1 omit: a one-option state is not a row. The pending row's outcome waits for the next
      // row or the terminal (B1); its forced steps and combat_end facts accumulate meanwhile (S2).
      const forcedState = catalog.length === 1 && config.inference.single_choice === "omit";
      if (forcedState) this.#resolveInChain(facts);
      else this.#flushOutcome(facts, null);
      this.awaitingSuccessor = false;
      this.lastFacts = facts;
      if (this.ctx.stopping() || this.slot.closeRequested) throw new GameEnd("stop", this.ctx.stopping() ? "process_stop" : "close_slot");
      if (this.decisions >= config.budget.max_decisions_per_game) throw new GameEnd("budget", "max_decisions_per_game");
      if (this.nativeSteps >= config.budget.max_native_steps_per_game) throw new GameEnd("budget", "max_native_steps_per_game");
      if (performance.now() - this.#started >= config.budget.game_wall_ms) throw new GameEnd("budget", "game_wall_ms");

      if (this.savePoints && facts !== null && observation.scene.native_kind === "map_navigation"
          && this.savePoints.floors.includes(facts.floor) && !this.#saveTried.has(facts.floor)) {
        this.#saveTried.add(facts.floor);
        await this.#save(this.savePoints.directory, facts);
        // A fresh capture of the same state: the Host revalidates it and the same-occurrence check holds it equal.
        continue;
      }

      if (forcedState) {
        // S1: the old chain's bound on one-option steps in a row (P1G_LIMITS.forced_steps_in_a_row);
        // past it the game closes as a budget end, never an endless loop.
        if (this.#forcedInARow >= P1G_LIMITS.forced_steps_in_a_row) {
          stats.forced_chain_limit += 1;
          throw new GameEnd("budget", "forced_steps_in_a_row");
        }
        this.#forcedInARow += 1;
        stats.forced += 1;
        const status = await this.#acted(prepared, 0, null);
        if (status === "not_started") throw new GameEnd("not_started", "forced_step_not_delivered");
        if (status === "unknown") throw new GameEnd("unknown", this.#unknownDetail(), { replaceNative: true });
        // Counted once its successor was captured (an interrupted fold still ran native steps).
        if (this.#pendingOutcome) this.#pendingOutcome.forced += 1;
        continue;
      }
      this.#forcedInARow = 0;

      if (facts === null) throw new GameEnd("not_started", "required_information_unavailable:public_facts");
      const inputStarted = performance.now();
      const rowId = this.ctx.nextRowId(), stepIndex = this.decisions;
      const view = modelView(observation) as unknown as Record<string, any>;
      // R5: the link text and the json_bytes digest of the observation from one write
      // (= JSON.stringify(view) and pyJsonSha256(view), in the same order of evaluation).
      const observationText = jsonWithPySha256(view);
      const observationJson = observationText.json, catalogJson = JSON.stringify(catalog);
      // The repo-wide definitions: digest(json_bytes(observation)) / digest(json_bytes(catalog)),
      // the values the encoder and segment validation compute from this line.
      const catalogSha = pyJsonSha256(catalog), inCombat = view.combat?.availability === "available";
      const observationSha = observationText.sha256();
      const row = { row_id: rowId, game_instance_id: this.gameInstanceId, catalog_sha256: catalogSha, catalog_len: catalog.length, in_combat: inCombat };
      const consumptionId = `consumption-${randomUUID().replaceAll("-", "")}`;
      // combat_end facts read before this row with no earlier row to carry them are not this row's.
      this.#collectCombatEnd();
      const line = `{"schema":${JSON.stringify(FAST_ENV_LINK_SCHEMA)},"type":"decision","row_id":${rowId},`
        + `"game_instance_id":${JSON.stringify(this.gameInstanceId)},"step_index":${stepIndex},"consumption_id":"${consumptionId}",`
        + `"catalog_len":${catalog.length},"catalog_sha256":"${catalogSha}","observation_sha256":"${observationSha}",`
        + `"in_combat":${inCombat},"floor":${facts.floor},"input":{"input_spec":${JSON.stringify(this.ctx.manifest.input.input_spec)},`
        + `"observation":${observationJson},"catalog":${catalogJson}}}`;
      stats.input_ms += performance.now() - inputStarted;
      this.decisions += 1; stats.decisions += 1; this.lastStepIndex = stepIndex;

      const waitStarted = performance.now();
      const result = await link.decide(rowId, this.gameInstanceId, line, config.inference.timeout_ms);
      stats.link_wait_ms += performance.now() - waitStarted;
      const notStarted = (reason: OutcomeReason, close: CloseReason, detail: string): never => {
        this.#outcome(rowId, stepIndex, "not_started", { reason });
        throw new GameEnd(close, detail);
      };
      // Stop (WP0 contract: control is released only after every in-flight row resolved, by
      // reply or timeout): a row that resolves during Stop is never acted on, whatever came back.
      if (this.ctx.stopping() && result.kind !== "disconnected") notStarted("stop_before_send", "stop", "stop_before_send");
      if (result.kind === "timeout") notStarted("inference_timeout", "not_started", "inference_timeout");
      if (result.kind === "cancelled") notStarted("stop_before_send", "stop", "stop_before_send");
      if (result.kind === "disconnected") throw new GameEnd("not_started", "link_disconnected");
      if (result.kind === "invalid") notStarted("response_binding_mismatch", "not_started", `response_binding_mismatch:invalid_reply:${result.detail}`);
      if (result.kind !== "reply") throw new GameEnd("not_started", "link_result");
      const verdict = this.ctx.binding.check(row, result.reply, config.inference.sampling);
      if (verdict.verdict !== "accept") notStarted(verdict.reason, "not_started", `${verdict.reason}:${verdict.detail}`);
      if (result.reply.type === "scored") this.#lastMu = result.reply.mu_snapshot_sha256;
      if (this.ctx.stopping() || this.slot.closeRequested) notStarted("stop_before_send", "stop", "stop_before_send");
      const index = (verdict as { index: number }).index, choice = catalog[index]!;
      if (this.#noProgress?.check(observation, choice).exhausted) notStarted("stop_before_send", "budget", "no_progress");

      const status = await this.#acted(prepared, index, { rowId, stepIndex, consumptionId,
        observationSha: observationSha, catalogSha, inputLine: line });
      if (status === "not_started") notStarted("native_not_delivered", "not_started", "native_not_delivered");
      if (status === "unknown") {
        this.#outcome(rowId, stepIndex, "unknown", { reason: "delivery_unknown" });
        throw new GameEnd("unknown", this.#unknownDetail(), { replaceNative: true });
      }
    }
  }

  /** One Host map save at a settled map state (save tickets). A save is side work: the game goes on after a
   * save or a refusal before any native write; a failed or unknown save ends the game (the Host cleared the
   * episode) and is never retried. */
  async #save(directory: string, facts: PublicFacts): Promise<void> {
    const { config, stats } = this.ctx;
    const name = `${config.env_process_id}.${this.gameRef}.f${facts.floor}.${randomUUID().slice(0, 8)}.json`.replaceAll(":", "_");
    const path = join(directory, name);
    const started = performance.now();
    let result;
    try { result = await this.native.saveMap(path); }
    catch (error) {
      stats.saves.ms += performance.now() - started;
      const unknown = error instanceof SaveMapFailure && error.outcome === "unknown";
      if (unknown) stats.saves.unknown += 1; else stats.saves.failed += 1;
      // The Host keeps whatever it reserved or wrote at this path for diagnosis: the close names it.
      const code = error instanceof SaveMapFailure ? error.message : `save_failed:${errorText(error)}`;
      throw new GameEnd("env_crash", `${code} floor=${facts.floor} path=${path}`, { replaceNative: true });
    }
    const ms = performance.now() - started;
    stats.saves.ms += ms;
    const where = { slot: this.slot.index, game_ref: this.gameRef, game_instance_id: this.gameInstanceId, floor: facts.floor };
    if (result.kind === "refused") {
      stats.saves.refused += 1;
      this.ctx.emit("save_refused", { ...where, reason: result.reason });
      return;
    }
    stats.saves.written += 1;
    // The producing model is the mu of the last scored row (null before the first one).
    this.ctx.emit("saved", { ...where, save: result.save, public_facts: facts, ascension: config.host.ascension,
      character: config.host.character, mu_snapshot_sha256: this.#lastMu, decisions: this.decisions, save_ms: Math.round(ms) });
  }

  #newPending(row: { rowId: number; stepIndex: number }, receiptSha: string): PendingOutcome {
    return { row_id: row.rowId, step_index: row.stepIndex, receipt_sha256: receiptSha, forced: 0, combat_end: [], resolved: null };
  }

  /** actGlobal's fold over one P1 choice: Host BoundActions only, each revalidated by the Host. */
  async #act(prepared: Prepared, choiceId: string, row: { rowId: number; stepIndex: number } | null, record: FastStepRecord): Promise<StepStatus> {
    const { stats } = this.ctx;
    const choice = prepared.reduction.observation.catalog.find(item => item.choice_id === choiceId);
    const lowering = prepared.reduction.lowering[choiceId];
    if (!choice || !lowering || !supportsProfileValue(this.ctx.manifest.support.action_verbs, choice.kind))
      throw new GameEnd("not_started", "choice_not_in_catalog");
    let source = prepared.source, next = lowering.first, followIndex = 0, steps = 0, status: StepStatus = "completed";
    let receiptSha = "";
    while (next) {
      if (steps >= P1G_LIMITS.macro_steps) { status = "interrupted_known"; break; }
      if (this.ctx.stopping() && steps > 0) { status = "interrupted_known"; break; }
      const step = next;
      const bound = (source.full.basis.snapshot.bound_actions as { actions: Record<string, unknown>[] }).actions
        .find(action => action.bound_action_id === step.bound_action_id);
      if (!bound) { status = steps ? "interrupted_known" : "not_started"; break; }
      const requestId = `request-${randomUUID()}`;
      const stepStarted = performance.now();
      let raw: unknown;
      try {
        raw = await this.native.step({ requestId, expectedSnapshotId: String(source.full.basis.snapshot.snapshot_id), boundActionId: step.bound_action_id });
      } catch (error) {
        // Handed to the Host: whether the native received it is not known.
        stats.step_ms += performance.now() - stepStarted;
        this.#unknownCause = `host_step_failed:${errorText(error)}`;
        return "unknown";
      }
      const receiptStarted = performance.now();
      stats.step_ms += receiptStarted - stepStarted;
      let decoded;
      try {
        decoded = decodePublicSourceReceipt(raw).data;
        const expected = { bound_action_id: bound.bound_action_id, verb: bound.verb, subject_referent_id: bound.subject_referent_id ?? null, arguments: bound.arguments };
        const actual = { ...decoded.action, subject_referent_id: decoded.action.subject_referent_id ?? null };
        const matches = decoded.delivery === "not_delivered" ? actual.bound_action_id === expected.bound_action_id
          : canonicalJson(expected) === canonicalJson(actual);
        if (decoded.request_id !== requestId || !matches) throw new Error("receipt_binding");
      } catch (error) {
        stats.receipt_ms += performance.now() - receiptStarted;
        this.#unknownCause = `receipt_unreadable:${errorText(error)}`;
        return "unknown";
      }
      const textMemo = this.ctx.receiptCanonical;
      let textMemoFailure: unknown = null;
      if (textMemo === null) receiptSha = globalDigestFast(decoded); // = globalDigest(decoded), one pass
      else {
        // E4: the same digest, read from the original receipt (its frozen subtrees keep their text).
        try { receiptSha = textMemo.digest(raw, decoded); }
        catch (error) {
          if (memoDivergence(error) === null) { stats.receipt_ms += performance.now() - receiptStarted; throw error; }
          // The receipt was read; only the memo is wrong. The step is recorded with the full walk's digest.
          textMemoFailure = error;
          receiptSha = globalDigestFast(decoded);
        }
      }
      stats.receipt_ms += performance.now() - receiptStarted;
      steps += 1; this.nativeSteps += 1; stats.native_steps += 1;
      if (decoded.delivery === "delivered") this.awaitingSuccessor = true;
      record.native_steps.push({ fold_index: steps - 1, native_verb: String(step.verb), delivery: decoded.delivery,
        reason_code: decoded.reason_code ?? null, receipt_sha256: receiptSha });
      if (textMemoFailure !== null) {
        if (row) this.#pendingOutcome = this.#newPending(row, receiptSha);
        throw memoDivergenceEnd("receipt_canonical_memo_divergence", textMemoFailure);
      }
      if (decoded.delivery === "unknown") { this.#unknownCause = `receipt_delivery_unknown:${decoded.reason_code ?? "none"}`; return "unknown"; }
      if (decoded.delivery === "not_delivered") { status = steps === 1 ? "not_started" : "interrupted_known"; break; }
      const divergence = sessionDivergence(decoded.reason_code);
      if (divergence !== null) {
        if (row) this.#pendingOutcome = this.#newPending(row, receiptSha);
        throw divergenceEnd(divergence);
      }
      const rule = lowering.follow[followIndex++];
      // Every known delivery gets an actual successor capture; a failure after a
      // delivered step leaves its row's outcome unknown and ends the game.
      if (row && steps === 1) this.#pendingOutcome = this.#newPending(row, receiptSha);
      if (row && this.#pendingOutcome) this.#pendingOutcome.receipt_sha256 = receiptSha;
      const since = performance.now();
      source = await this.#capture();
      while (source.full.basis.snapshot.status === "settling") {
        if (performance.now() - since >= this.ctx.config.settling_timeout_ms) throw new GameEnd("not_started", "managed_settling_stalled");
        stats.settle_waits += 1; await this.ctx.sleep(50); source = await this.#capture();
      }
      this.#pendingSource = source;
      if (source.full.basis.capture_status !== "complete") throw new GameEnd("not_started", "post_delivery_capture_incomplete");
      if (!rule) break;
      next = resolveFollowBasis(rule, lowering, source.full.basis) ?? null;
      if (!next && !rule.optional) status = "interrupted_known";
    }
    if (status === "completed") {
      try { this.state = applyCommitment(this.state, lowering, true); }
      catch { status = steps ? "interrupted_known" : "not_started"; }
    }
    return status;
  }
}

export type SlotState = "idle" | "opening" | "running";

/** One slot = one native child and its games, isolated from the other slots. */
export class SlotKernel {
  state: SlotState = "idle";
  closeRequested = false;
  gameInstanceId: string | null = null;
  #native: SlotNative | null = null;
  #current: Promise<void> | null = null;
  #game: GameRun | null = null;
  #metrics: Promise<void> | null = null;

  constructor(readonly index: number, private readonly ctx: KernelContext) {}

  get decisions(): number { return this.#game?.decisions ?? 0; }

  /** Starts a game without waiting for it; refusals are reported as events. */
  open(command: OpenCommand, buildStopped: boolean): void {
    const refuse = (reason: string) => this.ctx.emit("open_refused", { command_id: command.command_id, slot: this.index, game_ref: command.game_ref, reason });
    if (this.state !== "idle") return refuse("slot_busy");
    if (this.ctx.stopping()) return refuse("stopping");
    if (buildStopped) return refuse("build_output_stopped");
    this.state = "opening";
    this.closeRequested = false;
    const current: Promise<void> = this.#run(command).catch(error => {
      // A kernel bug, not a game outcome: loud, and the slot never silently reopens.
      this.ctx.emit("slot_failed", { slot: this.index, game_ref: command.game_ref, reason: error instanceof Error ? error.message : String(error) });
    }).finally(() => { if (this.#current === current) { this.#idle(); this.#current = null; } });
    this.#current = current;
  }

  /** The slot is free again before its closing event goes out, so the manager may reopen it at once. */
  #idle(): void { this.state = "idle"; this.gameInstanceId = null; this.#game = null; this.closeRequested = false; }

  /** E5 counters: the Host session's totals are per native; only the growth since the last game is added. */
  #memoSeen = { hits: 0, misses: 0, checks: 0 };
  #countProjectionMemo(native: SlotNative): void {
    const now = native.projectionMemo?.() ?? null;
    if (now === null) return;
    const total = this.ctx.stats.projection_memo, seen = this.#memoSeen;
    total.hits += now.hits - seen.hits; total.misses += now.misses - seen.misses; total.checks += now.checks - seen.checks;
    this.#memoSeen = { ...now };
  }

  async #replaceNative(): Promise<void> {
    const native = this.#native;
    this.#native = null;
    this.#memoSeen = { hits: 0, misses: 0, checks: 0 };
    if (native) await native.shutdown(true).catch(() => undefined);
  }

  async #run(command: OpenCommand): Promise<void> {
    const { ctx } = this;
    const refuse = (reason: string) => { this.#idle(); ctx.emit("open_refused", { command_id: command.command_id, slot: this.index, game_ref: command.game_ref, reason }); };
    // A between-games metrics read still holds the native's one request slot.
    if (this.#metrics) await this.#metrics;
    const mountStarted = performance.now();
    let nativeTag: "started" | "reused" = "reused";
    // A Host map load needs a process that never mounted a run.
    if (command.start.kind === "from_save") await this.#replaceNative();
    // A cold start holds one node-wide gate place through its first reset (or map load).
    let releaseColdStart: (() => void) | null = null;
    if (!this.#native) {
      const waitStarted = performance.now();
      releaseColdStart = await ctx.coldStart.acquire(() => ctx.stopping());
      ctx.stats.cold_start_wait_ms += performance.now() - waitStarted;
      if (!releaseColdStart) return refuse("stopping");
      ctx.stats.cold_starts += 1;
      try { this.#native = await ctx.factory.start(); nativeTag = "started"; }
      catch (error) { releaseColdStart(); return refuse(`native_start_failed:${error instanceof Error ? error.message : String(error)}`); }
    }
    const native = this.#native;
    const openStart: FastGameMeta["start"] = command.start.kind === "from_save"
      ? { kind: "from_save", save_sha256: command.start.save.sha256, act: command.start.save.act, floor: command.start.save.floor }
      : { kind: "new_run" };
    let gameInstanceId: string, start: Record<string, unknown>;
    try {
      const episode = command.start.kind === "from_save"
        ? await native.loadSave({ save: command.start.save, r7: command.r7 })
        : await native.reset({ seed: command.seed, initialProgress: command.initial_progress, r7: command.r7 });
      gameInstanceId = episode.gameInstanceId ?? `fast-${randomUUID().replaceAll("-", "")}`;
      // The native's own word on how this instance began; without compact output, the command's.
      start = episode.start ?? (command.start.kind === "from_save"
        ? { kind: "from_save", save_sha256: command.start.save.sha256 } : { kind: "new_run" });
      const expectedSave = command.start.kind === "from_save" ? command.start.save.sha256 : null;
      if (start.kind !== command.start.kind || (expectedSave !== null && start.save_sha256 !== expectedSave))
        throw new Error("native_start_changed");
    } catch (error) {
      releaseColdStart?.();
      const divergence = sessionDivergence(native.taint().reason);
      // The lines this mount emitted (a diverging first frame included) outlive the native.
      const lines: string[] = [];
      // Compact off, the only line can be the native's own divergence report: kept all the same.
      native.attachLines(line => lines.push(line)); native.attachLines(null);
      this.#countProjectionMemo(native);
      await this.#replaceNative();
      if (divergence !== null) { countDivergence(ctx.stats, divergence); ctx.stopBuildOutput(divergence, this.index, null); }
      const reason = `reset_failed:${divergence ?? errorText(error)}`;
      try { await ctx.evidence.refusedOpen({ slot: this.index, game_ref: command.game_ref, start: openStart, reason, native_lines: lines }); }
      catch (failure) { ctx.evidenceFailed(this.index, null, errorText(failure)); }
      return refuse(reason);
    }
    releaseColdStart?.();
    this.gameInstanceId = gameInstanceId;
    this.state = "running";
    ctx.stats.games += 1;
    ctx.emit("opened", { command_id: command.command_id, slot: this.index, game_ref: command.game_ref, game_instance_id: gameInstanceId,
      start, native: nativeTag, mount_ms: Math.round(performance.now() - mountStarted) });
    // ascension: the session's level, which the Host checked against the native run at mount.
    ctx.link.send({ type: "episode_open", game_instance_id: gameInstanceId, actor_id: ctx.config.inference.actor_id,
      sampling: ctx.config.inference.sampling, single_choice: ctx.config.inference.single_choice, start: openStart,
      ascension: ctx.config.host.ascension,
      // An R7 game: the assembler archives every mu its segments bind (option d).
      audit_retain_mu: command.r7 });
    const evidence = ctx.evidence.openGame({ slot: this.index, game_ref: command.game_ref, game_instance_id: gameInstanceId,
      seed: command.seed, start: openStart, r7: command.r7, retain_originals: command.retain_originals, native_lines: true });
    // Always attached: compact off, the native's divergence report (#433 S3) is still an original.
    native.attachLines(line => evidence.nativeLine(line));
    const game = new GameRun(ctx, this, native, gameInstanceId, evidence, command.game_ref, command.save_points);
    this.#game = game;
    let reason: CloseReason, detail: string, terminal: { result: "victory" | "defeat" } | null = null, replace = false;
    // Any evidence failure of this game: its `closed` event says so, so the manager never counts or trains on it.
    let evidenceFailed = false;
    const failEvidence = (failure: unknown) => { evidenceFailed = true; ctx.evidenceFailed(this.index, gameInstanceId, errorText(failure)); };
    try {
      ({ reason, detail, terminal } = await game.play());
    } catch (error) {
      const memo = error instanceof GameEnd ? null : memoDivergence(error);
      const end = error instanceof GameEnd ? error : memo !== null ? memoDivergenceEnd(memo, error)
        : error instanceof ReduceStrictSampleFailure ? reduceSampleEnd(error)
        : new GameEnd("env_crash", `kernel:${error instanceof Error ? error.message : String(error)}`, { replaceNative: true });
      game.failPending(end);
      ({ reason, detail } = end);
      replace = end.options.replaceNative === true || native.taint().tainted;
      // Delivered, then no readable successor (settling stalled, incomplete capture, capacity,
      // missing facts, or a successor frame that diverged): the effect is not known, so the
      // game is unknown and the native goes. A divergence before any delivered action (no
      // action's outcome is in question) stays not_started; both stop this build's output.
      if (game.awaitingSuccessor && reason === "not_started") {
        reason = "unknown"; replace = true;
        if (sessionDivergence(detail) === null && !end.options.fatal) detail = `delivered_successor_unreadable:${detail}`;
      }
      const divergence = sessionDivergence(end.options.buildStop);
      if (divergence !== null) countDivergence(ctx.stats, divergence);
      if (end.options.buildStop) ctx.stopBuildOutput(end.options.buildStop, this.index, gameInstanceId);
      if (end.options.fatal) { ctx.stats.memo_divergences += 1; ctx.memoFatal(end.options.fatal); }
      // The originals the sampled check disagreed with go into this game's evidence, and
      // every original of this game is kept, whatever else the close says.
      if (end.options.sampledMismatch) {
        try { evidence.retainSampledCheckMismatch(end.options.sampledMismatch); }
        catch (failure) { failEvidence(failure); }
      }
    }
    const fullChecks = native.fullChecks();
    ctx.stats.full_checks += fullChecks;
    this.#countProjectionMemo(native);
    if (!replace) {
      try { await native.release(); } catch { replace = true; }
    }
    native.attachLines(null);
    if (replace) await this.#replaceNative();
    let evidenceDir: string | null = null;
    try {
      // The rows recorded since the last boundary.
      await game.boundary();
      evidenceDir = await evidence.close({ reason, detail, decisions: game.decisions, native_steps: game.nativeSteps,
        last_step_index: game.lastStepIndex, terminal, public_facts: game.lastFacts, native_replaced: replace, full_checks: fullChecks });
    }
    catch (error) { failEvidence(error); }
    // The decision link hears the close only after this game's evidence is closed: a game whose
    // evidence failed gets no terminal label and its unsealed rows are sealed truncated
    // (`env_crash` on the link; the evidence and the `closed` event keep the real reason). Per
    // D1=A the learner then excludes the game: segments not yet used are dropped; a segment
    // already selected into a prefetched batch counts as used and is only counted.
    ctx.link.send({ type: "episode_close", game_instance_id: gameInstanceId, reason: evidenceFailed ? "env_crash" : reason,
      last_step_index: game.lastStepIndex });
    this.#idle();
    ctx.emit("closed", { slot: this.index, game_ref: command.game_ref, game_instance_id: gameInstanceId, reason, detail,
      decisions: game.decisions, native_steps: game.nativeSteps, last_step_index: game.lastStepIndex, terminal,
      public_facts: game.lastFacts, native: replace ? "replaced" : "kept", evidence_dir: evidenceDir, evidence_failed: evidenceFailed });
  }

  /** Waits for the current game (bounded by the inference and native request timeouts). */
  async drain(): Promise<void> { await this.#current; }

  async shutdown(): Promise<void> {
    await this.drain();
    const native = this.#native;
    this.#native = null;
    if (native) await native.shutdown(false).catch(() => native.shutdown(true).catch(() => undefined));
  }

  /** Only between games: the Host's native channel carries one request at a time, so a
   * metrics read during a game would collide with a step and make its delivery unknown.
   * An open that arrives while a read is in flight waits for it before mounting. */
  async nativeCpuMs(): Promise<number | null> {
    if (!this.#native || this.state !== "idle") return null;
    const read = this.#native.nativeCpuMs().catch(() => null);
    const held = read.then(() => undefined);
    this.#metrics = held;
    void held.finally(() => { if (this.#metrics === held) this.#metrics = null; });
    return read;
  }
}

export { ResponseBinding };
