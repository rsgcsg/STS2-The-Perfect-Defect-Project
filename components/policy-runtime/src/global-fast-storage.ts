import { createHash, randomUUID } from "node:crypto";
import { lstat, mkdir, open, readFile, type FileHandle } from "node:fs/promises";
import { join } from "node:path";
import { gunzip, gzip } from "node:zlib";
import { promisify } from "node:util";
import type { FastEvidenceSink, FastGameClose, FastGameEvidence, FastGameMeta, FastRefusedOpen, FastStepRecord }
  from "./fast-env/evidence-sink.js";

/**
 * Fast-chain evidence (WP6). One directory per game:
 * - `actions.jsonl`: the de-handled action prefix. One `open` line, one `step` line per acted-on
 *   P1 choice (the step record without its `input_line`), a `boundary` line per segment boundary
 *   (each boundary is fsynced) and one `close` line, written last, after any originals are durable.
 * - `native-lines.jsonl.gz` / `input-lines.jsonl.gz`: the originals, only for kept games. A game's
 *   originals are kept when the manager chose it before open (R7, the 5% sample, evaluation) or
 *   when the close shows anything other than a clean finish (failure, unknown, truncation, a
 *   non-completed step, a replaced native). That is decided at close, so originals stay in memory
 *   until then. Nothing is ever deleted or overwritten; any write failure is loud and the game's
 *   evidence stays unclosed, which readers count as unsatisfied.
 */
export const FAST_EVIDENCE_SCHEMA = "sts2.policy-runtime/fast-evidence-1" as const;
export const FAST_ACTIONS_FILE = "actions.jsonl";
export const FAST_NATIVE_LINES_FILE = "native-lines.jsonl.gz";
export const FAST_INPUT_LINES_FILE = "input-lines.jsonl.gz";

const compress = promisify(gzip), expand = promisify(gunzip);
const sha256 = (bytes: Buffer | string) => createHash("sha256").update(bytes).digest("hex");

export interface FastOriginalsFile { path: string; count: number; bytes: number; sha256: string }
export interface FastEvidenceCloseRecord extends FastGameClose {
  kind: "close";
  steps: number;
  native_lines_seen: number;
  /** Why the originals were kept; empty = not kept. */
  retained: string[];
  native_lines: FastOriginalsFile | null;
  input_lines: FastOriginalsFile | null;
}
/** The one-line `actions.jsonl` of a refused open (#427 `FastRefusedOpen`); a refused open is a failure, so its native lines are always kept. */
export interface FastRefusedOpenRecord extends Omit<FastRefusedOpen, "native_lines"> {
  kind: "refused_open";
  schema: typeof FAST_EVIDENCE_SCHEMA;
  retained: string[];
  native_lines: FastOriginalsFile;
}
export type FastStepLine = Omit<FastStepRecord, "input_line"> & { kind: "step" };
export interface FastBoundaryLine { kind: "boundary"; first_row_id: number; last_row_id: number; steps: number }

/** Why a game's originals are kept, or [] for a clean finish not chosen by the manager. */
export function fastRetainReasons(meta: Pick<FastGameMeta, "r7" | "retain_originals">, closed: FastGameClose,
  statuses: ReadonlySet<FastStepRecord["status"]>): string[] {
  const reasons: string[] = [];
  if (meta.r7) reasons.push("r7");
  if (meta.retain_originals) reasons.push("retain_originals");
  if (closed.reason !== "terminal") reasons.push(`close:${closed.reason}`);
  else if (closed.terminal === null) reasons.push("terminal_missing");
  if (closed.native_replaced) reasons.push("native_replaced");
  for (const status of ["interrupted_known", "not_started", "unknown"] as const)
    if (statuses.has(status)) reasons.push(`step:${status}`);
  return reasons;
}

function gameDirectoryName(id: string): string {
  const safe = id.replace(/[^A-Za-z0-9._-]/gu, "_");
  if (!safe || safe === "." || safe === "..") throw new Error("fast_evidence_game_id");
  return safe;
}

async function syncDirectory(path: string): Promise<void> {
  const handle = await open(path, "r");
  try { await handle.sync(); } finally { await handle.close(); }
}

async function writeOnce(path: string, bytes: Buffer): Promise<void> {
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
}

async function writeOriginals(directory: string, name: string, lines: readonly string[]): Promise<FastOriginalsFile> {
  const stored = await compress(Buffer.from(lines.map(line => `${line}\n`).join(""), "utf8"), { level: 3 });
  await writeOnce(join(directory, name), stored);
  return { path: name, count: lines.length, bytes: stored.length, sha256: sha256(stored) };
}

/** The production sink of the fast chain; the hot path only buffers strings. */
export class FastActionEvidenceSink implements FastEvidenceSink {
  /** First failed refused-open write; it sticks to the sink (later refusals and every game's
   * boundary/close report it). The per-game hot-path calls never throw for it, so a Host line
   * tap is never interrupted. */
  #failure: string | null = null;
  constructor(private readonly root: string) {}
  openGame(meta: FastGameMeta): FastGameEvidence {
    return new FastGameActionEvidence(this.root, meta, () => this.#failure);
  }

  async refusedOpen(refused: FastRefusedOpen): Promise<string | null> {
    if (this.#failure !== null) throw new Error(`fast_evidence_failed:${this.#failure}`);
    const directory = join(this.root, gameDirectoryName(`refused-open-${refused.game_ref}-${randomUUID()}`));
    try {
      await mkdir(this.root, { recursive: true, mode: 0o700 });
      await mkdir(directory, { mode: 0o700 });
      const native_lines = await writeOriginals(directory, FAST_NATIVE_LINES_FILE, refused.native_lines);
      const { native_lines: _lines, ...rest } = refused;
      const record: FastRefusedOpenRecord = { kind: "refused_open", schema: FAST_EVIDENCE_SCHEMA, ...rest,
        retained: [`refused_open:${refused.reason}`], native_lines };
      // The record is written last: its presence means the originals are durable.
      await writeOnce(join(directory, FAST_ACTIONS_FILE), Buffer.from(`${JSON.stringify(record)}\n`, "utf8"));
      await syncDirectory(directory);
    } catch (error) {
      this.#failure = error instanceof Error ? error.message : String(error);
      throw error;
    }
    return directory;
  }
}

class FastGameActionEvidence implements FastGameEvidence {
  readonly #root: string;
  readonly #directory: string;
  #pending: string[];
  readonly #natives: string[] = [];
  readonly #inputs: string[] = [];
  readonly #statuses = new Set<FastStepRecord["status"]>();
  #steps = 0;
  #handle: FileHandle | null = null;
  #failure: string | null = null;
  #closed = false;
  #busy = false;

  constructor(root: string, private readonly meta: FastGameMeta, private readonly sinkFailure: () => string | null) {
    this.#root = root;
    this.#directory = join(root, gameDirectoryName(meta.game_instance_id));
    this.#pending = [JSON.stringify({ kind: "open", schema: FAST_EVIDENCE_SCHEMA, ...meta })];
  }

  nativeLine(line: string): void {
    this.#usable("native_line");
    if (!this.meta.native_lines) throw new Error("fast_evidence_native_lines_off");
    this.#natives.push(line);
  }

  step(record: FastStepRecord): void {
    this.#usable("step");
    const { input_line, ...rest } = record;
    this.#pending.push(JSON.stringify({ kind: "step", ...rest }));
    this.#statuses.add(record.status);
    this.#steps += 1;
    if (input_line !== null) this.#inputs.push(JSON.stringify({ row_id: record.row_id, step_index: record.step_index, line: input_line }));
  }

  async segmentBoundary(range: { first_row_id: number; last_row_id: number }): Promise<void> {
    this.#usable("boundary");
    await this.#guard(async () => {
      this.#pending.push(JSON.stringify({ kind: "boundary", ...range, steps: this.#steps }));
      await this.#flush();
    });
  }

  async close(closed: FastGameClose): Promise<string | null> {
    this.#usable("close");
    this.#closed = true;
    await this.#guard(async () => {
      await this.#flush();
      const retained = fastRetainReasons(this.meta, closed, this.#statuses);
      const keep = retained.length > 0;
      const native_lines = keep && this.meta.native_lines ? await writeOriginals(this.#directory, FAST_NATIVE_LINES_FILE, this.#natives) : null;
      const input_lines = keep ? await writeOriginals(this.#directory, FAST_INPUT_LINES_FILE, this.#inputs) : null;
      const record: FastEvidenceCloseRecord = { kind: "close", ...closed, steps: this.#steps, native_lines_seen: this.#natives.length,
        retained, native_lines, input_lines };
      this.#pending.push(JSON.stringify(record));
      await this.#flush();
      await this.#handle?.close();
      this.#handle = null;
      await syncDirectory(this.#directory);
    });
    this.#natives.length = 0;
    this.#inputs.length = 0;
    return this.#directory;
  }

  #usable(call: string): void {
    if (this.#failure !== null) throw new Error(`fast_evidence_failed:${this.#failure}`);
    if (this.#closed) throw new Error(`fast_evidence_closed:${call}`);
    if (call === "native_line" || call === "step") return;
    const sink = this.sinkFailure();
    if (sink !== null) throw new Error(`fast_evidence_failed:${sink}`);
    if (this.#busy) throw new Error(`fast_evidence_concurrent:${call}`);
  }

  async #guard(work: () => Promise<void>): Promise<void> {
    this.#busy = true;
    try { await work(); }
    catch (error) {
      // The first failure sticks: no later call pretends the evidence is whole.
      this.#failure = error instanceof Error ? error.message : String(error);
      const handle = this.#handle;
      this.#handle = null;
      await handle?.close().catch(() => undefined);
      throw error;
    } finally { this.#busy = false; }
  }

  async #flush(): Promise<void> {
    if (this.#handle === null) {
      await mkdir(this.#root, { recursive: true, mode: 0o700 });
      // Not recursive: an existing game directory is refused, never reused.
      await mkdir(this.#directory, { mode: 0o700 });
      this.#handle = await open(join(this.#directory, FAST_ACTIONS_FILE), "wx", 0o600);
      await syncDirectory(this.#directory);
    }
    const lines = this.#pending;
    this.#pending = [];
    if (lines.length > 0) await this.#handle.appendFile(`${lines.join("\n")}\n`, "utf8");
    await this.#handle.sync();
  }
}

export interface FastGameEvidenceRead {
  /** null only for a refused open (`refused` set, no steps). */
  open: (FastGameMeta & { kind: "open"; schema: string }) | null;
  refused: FastRefusedOpenRecord | null;
  steps: FastStepLine[];
  boundaries: FastBoundaryLine[];
  close: FastEvidenceCloseRecord | null;
  /** closed = whole game durable; unsatisfied = no close (crash): steps after the last boundary
   * (and a torn last line) are not satisfied and the game counts as unknown/env_crash. */
  state: "closed" | "unsatisfied" | "refused";
  /** Steps covered by a durable boundary (all steps when closed). */
  satisfied_steps: number;
  torn_tail: boolean;
  native_lines: string[] | null;
  input_lines: { row_id: number | null; step_index: number | null; line: string }[] | null;
}

async function readOriginals(directory: string, ref: FastOriginalsFile, name: string): Promise<string[]> {
  if (ref.path !== name || !/^[a-f0-9]{64}$/u.test(ref.sha256)) throw new Error("fast_evidence_originals_reference");
  const path = join(directory, name), info = await lstat(path);
  if (!info.isFile() || info.size !== ref.bytes) throw new Error("fast_evidence_originals_file");
  const stored = await readFile(path);
  if (sha256(stored) !== ref.sha256) throw new Error("fast_evidence_originals_bytes");
  const text = (await expand(stored)).toString("utf8");
  const lines = text === "" ? [] : text.slice(0, -1).split("\n");
  if ((text !== "" && !text.endsWith("\n")) || lines.length !== ref.count) throw new Error("fast_evidence_originals_count");
  return lines;
}

/** Reads one game's evidence, checking order, counts, digests and the frame → native-line binding. */
export async function readFastGameEvidence(directory: string): Promise<FastGameEvidenceRead> {
  const text = await readFile(join(directory, FAST_ACTIONS_FILE), "utf8");
  const torn = text !== "" && !text.endsWith("\n");
  const raw = text.split("\n");
  raw.pop();
  const first = raw.length > 0 ? JSON.parse(raw[0]!) as { kind?: unknown; schema?: unknown } : null;
  if (first?.kind === "refused_open") {
    const refused = first as FastRefusedOpenRecord;
    if (refused.schema !== FAST_EVIDENCE_SCHEMA || torn || raw.length !== 1 || refused.retained.length === 0)
      throw new Error("fast_evidence_refused_open");
    const natives = await readOriginals(directory, refused.native_lines, FAST_NATIVE_LINES_FILE);
    return { open: null, refused, steps: [], boundaries: [], close: null, state: "refused", satisfied_steps: 0, torn_tail: false,
      native_lines: natives, input_lines: null };
  }
  let open: FastGameEvidenceRead["open"] = null, close: FastEvidenceCloseRecord | null = null;
  const steps: FastStepLine[] = [], boundaries: FastBoundaryLine[] = [];
  let satisfied = 0;
  for (const [index, line] of raw.entries()) {
    const record = JSON.parse(line) as { kind?: unknown };
    if (index === 0) {
      if (record.kind !== "open" || (record as { schema?: unknown }).schema !== FAST_EVIDENCE_SCHEMA) throw new Error("fast_evidence_open");
      open = record as FastGameEvidenceRead["open"];
      continue;
    }
    if (close !== null) throw new Error("fast_evidence_after_close");
    if (record.kind === "step") steps.push(record as FastStepLine);
    else if (record.kind === "boundary") {
      const boundary = record as FastBoundaryLine;
      if (boundary.steps !== steps.length) throw new Error("fast_evidence_boundary_steps");
      boundaries.push(boundary);
      satisfied = steps.length;
    } else if (record.kind === "close") {
      close = record as FastEvidenceCloseRecord;
      if (close.steps !== steps.length) throw new Error("fast_evidence_close_steps");
    } else throw new Error("fast_evidence_kind");
  }
  if (open === null) throw new Error("fast_evidence_open");
  if (close !== null && torn) throw new Error("fast_evidence_torn_after_close");
  let natives: string[] | null = null, inputs: FastGameEvidenceRead["input_lines"] = null;
  if (close !== null) {
    if ((close.retained.length > 0) !== (close.input_lines !== null)
      || (close.native_lines !== null) !== (close.retained.length > 0 && open.native_lines))
      throw new Error("fast_evidence_retained");
    if (close.native_lines) {
      natives = await readOriginals(directory, close.native_lines, FAST_NATIVE_LINES_FILE);
      if (natives.length !== close.native_lines_seen) throw new Error("fast_evidence_native_count");
      const digests = new Set(natives.map(line => sha256(line)));
      for (const step of steps) if (step.frame !== null && !digests.has(step.frame.line_sha256)) throw new Error("fast_evidence_frame_line");
    }
    if (close.input_lines) inputs = (await readOriginals(directory, close.input_lines, FAST_INPUT_LINES_FILE))
      .map(line => JSON.parse(line) as { row_id: number | null; step_index: number | null; line: string });
    satisfied = steps.length;
  }
  return { open, refused: null, steps, boundaries, close, state: close !== null ? "closed" : "unsatisfied", satisfied_steps: satisfied,
    torn_tail: torn, native_lines: natives, input_lines: inputs };
}
