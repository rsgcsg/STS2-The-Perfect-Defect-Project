import { mkdir, open } from "node:fs/promises";
import { join } from "node:path";
import type { CloseReason, PublicFacts } from "./contracts.js";

/**
 * Per-game evidence seam of the K-game process. The record format, durability and
 * which originals are kept belong to WP6 (`evidence.ts` / `global-*-storage.ts`);
 * the process only calls this interface, synchronously on its hot path except at
 * a boundary and at close. Records never carry native operands or Host handles: a
 * decision is its P1 catalog position plus de-handled content and digests.
 */

/** How the instance began, as the link's episode_open says it. */
export type FastGameStartRecord =
  | { kind: "new_run" }
  | { kind: "from_save"; save_sha256: string; act: number; floor: number };

export interface FastGameMeta {
  slot: number;
  game_ref: string;
  game_instance_id: string;
  start: FastGameStartRecord;
  /** Chosen by the manager before open (`R7Sampler.select()`); every frame was schema/digest checked. */
  r7: boolean;
  /** Chosen by the manager before open (the 5% sample, evaluation games): keep every original. */
  retain_originals: boolean;
  /** Whether `nativeLine` will be called (compact output on). */
  native_lines: boolean;
}

/** The native frame a step acted on (compact output on; null otherwise). */
export interface FastFrameRef {
  episode_instance: string;
  seq: number;
  objects_root_sha256: string;
  /** sha256 of the exact emitted line, the same bytes `nativeLine` received. */
  line_sha256: string;
}

export interface FastNativeStepRecord {
  fold_index: number;
  native_verb: string;
  delivery: "delivered" | "not_delivered" | "unknown";
  reason_code: string | null;
  /** Digest of the Host's original receipt, decoded. */
  receipt_sha256: string;
}

/** One P1 choice the process acted on (a model row or a forced single choice). */
export interface FastStepRecord {
  /** null for a forced single choice under `single_choice: "omit"` (no inference row). */
  row_id: number | null;
  step_index: number | null;
  consumption_id: string | null;
  /** P1 observation envelope occurrence of the state the choice was made on. */
  occurrence: number;
  /** Host snapshot the first BoundAction was bound to. */
  snapshot_id: string;
  frame: FastFrameRef | null;
  /** Model rows only: sha256 of the observation JSON sent on the link. */
  observation_sha256: string | null;
  catalog_sha256: string;
  catalog_len: number;
  selected_index: number;
  /** Canonical JSON of the selected P1 choice without its `choice_id`. */
  choice_json: string;
  /** How many earlier catalog entries have exactly this `choice_json`. */
  same_content_ordinal: number;
  /** Delivery of the whole P1 choice (the fold of Host BoundActions). */
  status: "completed" | "interrupted_known" | "not_started" | "unknown";
  native_steps: FastNativeStepRecord[];
  /** Model rows only: the exact `decision` line sent to the aggregator, handed over for every
   * game; which games keep it is the sink's retention policy (WP6). The string is not copied. */
  input_line: string | null;
}

export interface FastGameClose {
  reason: CloseReason;
  detail: string;
  decisions: number;
  native_steps: number;
  last_step_index: number | null;
  terminal: { result: "victory" | "defeat" } | null;
  public_facts: PublicFacts | null;
  /** The native was replaced after this game (unknown delivery, crash, divergence). */
  native_replaced: boolean;
}

export interface FastGameEvidence {
  /** Every compact native line of this game, in order, exact bytes, before the Host checks
   * it (so a diverging line is included). Called only when `meta.native_lines`. */
  nativeLine(line: string): void;
  /** One acted-on P1 choice, after its delivery is known (unknown included). */
  step(record: FastStepRecord): void;
  /** After every `evidence.boundary_rows` model rows of this game and before close; the
   * sink may fsync here. Row ids of one game increase but are not contiguous (they are
   * numbered per process). */
  segmentBoundary(range: { first_row_id: number; last_row_id: number }): Promise<void>;
  /** Exactly once per opened game. Resolves with a locator for the game's evidence. */
  close(closed: FastGameClose): Promise<string | null>;
}

/** A mount that failed before its game existed (reset / load_map refused, the first frame
 * diverged): every compact line the native emitted for it, handed over with the reason. */
export interface FastRefusedOpen {
  slot: number;
  game_ref: string;
  start: FastGameStartRecord;
  reason: string;
  native_lines: string[];
}

export interface FastEvidenceSink {
  openGame(meta: FastGameMeta): FastGameEvidence;
  /** Resolves with a locator, or null when there was nothing to keep. */
  refusedOpen(refused: FastRefusedOpen): Promise<string | null>;
}

const fileName = (id: string) => id.replace(/[^A-Za-z0-9._-]/gu, "_");

async function writeOnce(directory: string, name: string, body: string): Promise<string> {
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const path = join(directory, name);
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(body); await handle.sync(); }
  finally { await handle.close(); }
  return path;
}

/** Interim measurement-only sink (not formal evidence, no retention policy of its own;
 * WP6's sink owns which originals are kept): one JSONL file per game, written and fsynced
 * once when the game closes; native lines and input lines only for R7 / retained games. */
export class MinimalJsonlEvidenceSink implements FastEvidenceSink {
  #refused = 0;
  constructor(private readonly directory: string) {}
  openGame(meta: FastGameMeta): FastGameEvidence {
    const lines: string[] = [JSON.stringify({ kind: "game_open", ...meta })];
    const natives: string[] = [];
    const directory = this.directory;
    return {
      nativeLine(line) { if (meta.retain_originals || meta.r7) natives.push(line); },
      step(record) { lines.push(JSON.stringify({ kind: "step", ...record, input_line: null })); },
      async segmentBoundary(range) { lines.push(JSON.stringify({ kind: "boundary", ...range })); },
      async close(closed) {
        lines.push(JSON.stringify({ kind: "game_close", ...closed, native_lines: natives.length }));
        return writeOnce(directory, `${fileName(meta.game_instance_id)}.jsonl`, `${lines.join("\n")}\n${natives.map(line => `${line}\n`).join("")}`);
      }
    };
  }
  /** Records the refusal and the line count only; keeping the lines is WP6's sink's call. */
  async refusedOpen(refused: FastRefusedOpen): Promise<string | null> {
    const { native_lines: natives, ...rest } = refused;
    return writeOnce(this.directory, `refused-${fileName(refused.game_ref)}-${process.pid}-${++this.#refused}.jsonl`,
      `${JSON.stringify({ kind: "open_refused", ...rest, native_lines: natives.length })}\n`);
  }
}
