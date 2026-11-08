import { createHash, randomUUID } from "node:crypto";
import { performance } from "node:perf_hooks";
import { decodePublicSourceReceipt, type FullPublicBasis } from "@rsgcsg/sts2-connector-client";
import { createSession, reduce, modelView, applyCommitment, resolveFollowBasis, P1G_LIMITS,
  type P1SessionState } from "@rsgcsg/sts2-connector-client/p1-global";
import { canonicalJson } from "../evidence.js";
import { globalDigest } from "../global-transport.js";
import { GlobalNoProgressWindow } from "../global-no-progress.js";
import { supportsProfileValue, type AgentManifest } from "../agent-session-contracts.js";
import { ResponseBinding } from "./binding.js";
import type { CloseReason, FastEnvCommand, FastEnvConfig, OutcomeReason, PublicFacts } from "./contracts.js";
import { FAST_ENV_LINK_SCHEMA } from "./contracts.js";
import { pyJsonSha256 } from "./json-bytes.js";
import type { DecisionLinkPort } from "./link.js";
import type { FastEvidenceSink, FastGameEvidence, FastGameMeta, FastStepRecord } from "./evidence-sink.js";
import { SampledCheckFailure, type SampledCheckMismatch, type SlotNative, type SlotNativeFactory } from "./host.js";

type Reduction = ReturnType<typeof reduce>;
type OpenCommand = Extract<FastEnvCommand, { cmd: "open" }>;
type StepStatus = "completed" | "interrupted_known" | "not_started" | "unknown";

export interface KernelStats {
  games: number; decisions: number; forced: number; native_steps: number; settle_waits: number; full_checks: number;
  /** Games and opens ended by compact_state_divergence (Host- or native-detected), counted apart from other unknowns. */
  compact_divergences: number;
  capture_ms: number; reduce_ms: number; input_ms: number; link_wait_ms: number; step_ms: number; receipt_ms: number;
}
export const emptyStats = (): KernelStats => ({ games: 0, decisions: 0, forced: 0, native_steps: 0, settle_waits: 0, full_checks: 0, compact_divergences: 0,
  capture_ms: 0, reduce_ms: 0, input_ms: 0, link_wait_ms: 0, step_ms: 0, receipt_ms: 0 });

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
}

/** A game ends here; nothing is ever retried. */
class GameEnd extends Error {
  constructor(readonly reason: CloseReason, readonly detail: string,
              readonly options: { replaceNative?: boolean; buildStop?: string; pendingOutcome?: { status: "unknown"; reason: OutcomeReason };
                terminal?: { result: "victory" | "defeat" } | null; sampledMismatch?: SampledCheckMismatch } = {}) {
    super(`${reason}:${detail}`);
  }
}

const sha256 = (text: string) => createHash("sha256").update(text, "utf8").digest("hex");
/** One line of an original error for a close detail (never parsed, never retried on). */
const errorText = (error: unknown) => (error instanceof Error ? `${error.name}:${error.message}` : String(error)).replace(/\s+/gu, " ").slice(0, 240);
const isObj = (value: unknown): value is Record<string, any> => value !== null && typeof value === "object" && !Array.isArray(value);

/** Player-visible run facts of one public observation (rl-inference-3 public_facts). */
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
interface PendingOutcome { row_id: number; step_index: number; receipt_sha256: string; forced: number }

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
  readonly #started = performance.now();

  constructor(private readonly ctx: KernelContext, private readonly slot: SlotKernel, private readonly native: SlotNative,
              readonly gameInstanceId: string, private readonly evidence: FastGameEvidence) {
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

  /** The step of an earlier row resolved; its outcome carries the facts of the state it led to. */
  #flushOutcome(facts: PublicFacts | null, terminal: { result: "victory" | "defeat" } | null): void {
    const pending = this.#pendingOutcome;
    if (!pending) return;
    this.#pendingOutcome = null;
    if (facts === null) {
      this.#outcome(pending.row_id, pending.step_index, "unknown", { reason: "native_crash" });
      throw new GameEnd("not_started", "required_information_unavailable:public_facts");
    }
    this.#outcome(pending.row_id, pending.step_index, "completed", { receipt_sha256: pending.receipt_sha256, public_facts: facts,
      terminal, ...(this.ctx.config.inference.single_choice === "omit" ? { forced_steps: pending.forced } : {}) });
  }

  /** Called with every GameEnd: an unresolved earlier step gets its honest outcome. */
  failPending(end: GameEnd): void {
    const pending = this.#pendingOutcome;
    if (!pending) return;
    this.#pendingOutcome = null;
    const fallback = end.options.pendingOutcome ?? { status: "unknown" as const, reason: "native_crash" as const };
    this.#outcome(pending.row_id, pending.step_index, fallback.status, { reason: fallback.reason });
  }

  async #capture(): Promise<Source> {
    const started = performance.now();
    try {
      const full = await this.native.capture();
      if (full.capture.total_bytes > this.ctx.manifest.limits.max_capture_bytes) throw new GameEnd("not_started", "capture_capacity");
      return { full, frame: this.native.frame() };
    } catch (error) {
      if (error instanceof GameEnd) throw error;
      if (error instanceof SampledCheckFailure) {
        // Same close as any capture failure today, plus: this build stops producing output.
        throw new GameEnd("env_crash", `capture:${error.message}`, { replaceNative: true, buildStop: "sampled_check_failed",
          pendingOutcome: { status: "unknown", reason: "native_crash" }, sampledMismatch: error.mismatch });
      }
      const taint = this.native.taint();
      if (taint.reason === "compact_state_divergence") {
        throw new GameEnd("not_started", "compact_state_divergence", { replaceNative: true, buildStop: "compact_state_divergence",
          pendingOutcome: { status: "unknown", reason: "compact_state_divergence" } });
      }
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
      const reduction = reduce(this.state, source.full.basis);
      this.ctx.stats.reduce_ms += performance.now() - reduceStarted;
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
      this.#flushOutcome(facts, null);
      this.awaitingSuccessor = false;
      this.lastFacts = facts;
      if (this.ctx.stopping() || this.slot.closeRequested) throw new GameEnd("stop", this.ctx.stopping() ? "process_stop" : "close_slot");
      if (this.decisions >= config.budget.max_decisions_per_game) throw new GameEnd("budget", "max_decisions_per_game");
      if (this.nativeSteps >= config.budget.max_native_steps_per_game) throw new GameEnd("budget", "max_native_steps_per_game");
      if (performance.now() - this.#started >= config.budget.game_wall_ms) throw new GameEnd("budget", "game_wall_ms");

      if (catalog.length === 1 && config.inference.single_choice === "omit") {
        stats.forced += 1;
        const forced = this.#pendingOutcome;
        const status = await this.#acted(prepared, 0, null);
        if (forced) forced.forced += 1;
        if (status === "not_started") throw new GameEnd("not_started", "forced_step_not_delivered");
        if (status === "unknown") throw new GameEnd("unknown", this.#unknownDetail(), { replaceNative: true });
        continue;
      }

      if (facts === null) throw new GameEnd("not_started", "required_information_unavailable:public_facts");
      const inputStarted = performance.now();
      const rowId = this.ctx.nextRowId(), stepIndex = this.decisions;
      const view = modelView(observation) as unknown as Record<string, any>;
      const observationJson = JSON.stringify(view), catalogJson = JSON.stringify(catalog);
      // The repo-wide definitions: digest(json_bytes(observation)) / digest(json_bytes(catalog)),
      // the values the encoder and segment validation compute from this line.
      const catalogSha = pyJsonSha256(catalog), inCombat = view.combat?.availability === "available";
      const observationSha = pyJsonSha256(view);
      const row = { row_id: rowId, game_instance_id: this.gameInstanceId, catalog_sha256: catalogSha, catalog_len: catalog.length, in_combat: inCombat };
      const consumptionId = `consumption-${randomUUID().replaceAll("-", "")}`;
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
      receiptSha = globalDigest(decoded);
      stats.receipt_ms += performance.now() - receiptStarted;
      steps += 1; this.nativeSteps += 1; stats.native_steps += 1;
      if (decoded.delivery === "delivered") this.awaitingSuccessor = true;
      record.native_steps.push({ fold_index: steps - 1, native_verb: String(step.verb), delivery: decoded.delivery,
        reason_code: decoded.reason_code ?? null, receipt_sha256: receiptSha });
      if (decoded.delivery === "unknown") { this.#unknownCause = `receipt_delivery_unknown:${decoded.reason_code ?? "none"}`; return "unknown"; }
      if (decoded.delivery === "not_delivered") { status = steps === 1 ? "not_started" : "interrupted_known"; break; }
      if (decoded.reason_code === "compact_state_divergence") {
        if (row) this.#pendingOutcome = { row_id: row.rowId, step_index: row.stepIndex, receipt_sha256: receiptSha, forced: 0 };
        throw new GameEnd("not_started", "compact_state_divergence", { replaceNative: true, buildStop: "compact_state_divergence",
          pendingOutcome: { status: "unknown", reason: "compact_state_divergence" } });
      }
      const rule = lowering.follow[followIndex++];
      // Every known delivery gets an actual successor capture; a failure after a
      // delivered step leaves its row's outcome unknown and ends the game.
      if (row && steps === 1) this.#pendingOutcome = { row_id: row.rowId, step_index: row.stepIndex, receipt_sha256: receiptSha, forced: 0 };
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

  async #replaceNative(): Promise<void> {
    const native = this.#native;
    this.#native = null;
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
    if (!this.#native) {
      try { this.#native = await ctx.factory.start(); nativeTag = "started"; }
      catch (error) { return refuse(`native_start_failed:${error instanceof Error ? error.message : String(error)}`); }
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
      const divergence = native.taint().reason === "compact_state_divergence";
      // The lines this mount emitted (a diverging first frame included) outlive the native.
      const lines: string[] = [];
      if (ctx.config.compact.enabled) { native.attachLines(line => lines.push(line)); native.attachLines(null); }
      await this.#replaceNative();
      if (divergence) { ctx.stats.compact_divergences += 1; ctx.stopBuildOutput("compact_state_divergence", this.index, null); }
      const reason = `reset_failed:${divergence ? "compact_state_divergence" : errorText(error)}`;
      try { await ctx.evidence.refusedOpen({ slot: this.index, game_ref: command.game_ref, start: openStart, reason, native_lines: lines }); }
      catch (failure) { ctx.evidenceFailed(this.index, null, errorText(failure)); }
      return refuse(reason);
    }
    this.gameInstanceId = gameInstanceId;
    this.state = "running";
    ctx.stats.games += 1;
    ctx.emit("opened", { command_id: command.command_id, slot: this.index, game_ref: command.game_ref, game_instance_id: gameInstanceId,
      start, native: nativeTag, mount_ms: Math.round(performance.now() - mountStarted) });
    ctx.link.send({ type: "episode_open", game_instance_id: gameInstanceId, actor_id: ctx.config.inference.actor_id,
      sampling: ctx.config.inference.sampling, single_choice: ctx.config.inference.single_choice, start: openStart });
    const evidence = ctx.evidence.openGame({ slot: this.index, game_ref: command.game_ref, game_instance_id: gameInstanceId,
      start: openStart, r7: command.r7, retain_originals: command.retain_originals, native_lines: ctx.config.compact.enabled });
    if (ctx.config.compact.enabled) native.attachLines(line => evidence.nativeLine(line));
    const game = new GameRun(ctx, this, native, gameInstanceId, evidence);
    this.#game = game;
    let reason: CloseReason, detail: string, terminal: { result: "victory" | "defeat" } | null = null, replace = false;
    try {
      ({ reason, detail, terminal } = await game.play());
    } catch (error) {
      const end = error instanceof GameEnd ? error
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
        if (detail !== "compact_state_divergence") detail = `delivered_successor_unreadable:${detail}`;
      }
      if (end.options.buildStop === "compact_state_divergence") ctx.stats.compact_divergences += 1;
      if (end.options.buildStop) ctx.stopBuildOutput(end.options.buildStop, this.index, gameInstanceId);
      // The originals the sampled check disagreed with go into this game's evidence, and
      // every original of this game is kept, whatever else the close says.
      if (end.options.sampledMismatch) {
        try { evidence.retainSampledCheckMismatch(end.options.sampledMismatch); }
        catch (failure) { ctx.evidenceFailed(this.index, gameInstanceId, errorText(failure)); }
      }
    }
    const fullChecks = native.fullChecks();
    ctx.stats.full_checks += fullChecks;
    ctx.link.send({ type: "episode_close", game_instance_id: gameInstanceId, reason, last_step_index: game.lastStepIndex });
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
    catch (error) { ctx.evidenceFailed(this.index, gameInstanceId, error instanceof Error ? error.message : String(error)); }
    this.#idle();
    ctx.emit("closed", { slot: this.index, game_ref: command.game_ref, game_instance_id: gameInstanceId, reason, detail,
      decisions: game.decisions, native_steps: game.nativeSteps, last_step_index: game.lastStepIndex, terminal,
      public_facts: game.lastFacts, native: replace ? "replaced" : "kept", evidence_dir: evidenceDir });
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
