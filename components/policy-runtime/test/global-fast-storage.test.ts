import { afterEach, describe, expect, it } from "vitest";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  FAST_ACTIONS_FILE, FAST_INPUT_LINES_FILE, FAST_NATIVE_LINES_FILE, FastActionEvidenceSink, fastRetainReasons, readFastGameEvidence
} from "../src/global-fast-storage.js";
import type { FastGameClose, FastGameMeta, FastStepRecord } from "../src/fast-env/evidence-sink.js";

const sha = (text: string) => createHash("sha256").update(text, "utf8").digest("hex");
const roots: string[] = [];
async function root(): Promise<string> { const dir = await mkdtemp(join(tmpdir(), "fast-evidence-")); roots.push(dir); return dir; }
afterEach(async () => { await Promise.all(roots.splice(0).map(dir => rm(dir, { recursive: true, force: true }))); });

function meta(over: Partial<FastGameMeta> = {}): FastGameMeta {
  return { slot: 0, game_ref: "ref-1", game_instance_id: "g/1:a", start: { kind: "new_run" }, r7: false,
    retain_originals: false, native_lines: true, ...over };
}
const line = (seq: number) => JSON.stringify({ seq, text: `frame é ${seq}`, objects: [{ id: seq }] });
function step(row: number | null, frameLine: string | null, over: Partial<FastStepRecord> = {}): FastStepRecord {
  return { row_id: row, step_index: row === null ? null : row - 1, consumption_id: row === null ? null : `c${row}`, occurrence: 3,
    snapshot_id: "snap", frame: frameLine === null ? null : { episode_instance: "e", seq: 1, objects_root_sha256: "0".repeat(64), line_sha256: sha(frameLine) },
    observation_sha256: row === null ? null : "1".repeat(64), catalog_sha256: "2".repeat(64), catalog_len: 2, selected_index: 1,
    choice_json: "{\"kind\":\"card\"}", same_content_ordinal: 0, status: "completed",
    native_steps: [{ fold_index: 0, native_verb: "play", delivery: "delivered", reason_code: null, receipt_sha256: "3".repeat(64) }],
    input_line: row === null ? null : JSON.stringify({ type: "decision", row_id: row, secret_detail: "only-in-originals" }), ...over };
}
function closed(over: Partial<FastGameClose> = {}): FastGameClose {
  return { reason: "terminal", detail: "victory", decisions: 2, native_steps: 3, last_step_index: 1, terminal: { result: "victory" },
    public_facts: { act: 1, floor: 3, hp: 50, max_hp: 70 }, native_replaced: false, ...over };
}

/** Plays a small game: 3 lines, 2 model rows + 1 forced step, one boundary. */
async function play(dir: string, m: FastGameMeta, c: FastGameClose, extra: Partial<FastStepRecord> = {}) {
  const game = new FastActionEvidenceSink(dir).openGame(m);
  const lines = [line(0), line(1), line(2)];
  if (m.native_lines) { game.nativeLine(lines[0]!); game.nativeLine(lines[1]!); }
  game.step(step(1, m.native_lines ? lines[0]! : null));
  game.step(step(null, m.native_lines ? lines[1]! : null));
  await game.segmentBoundary({ first_row_id: 1, last_row_id: 1 });
  if (m.native_lines) game.nativeLine(lines[2]!);
  game.step(step(4, m.native_lines ? lines[2]! : null, extra));
  await game.segmentBoundary({ first_row_id: 4, last_row_id: 4 });
  return { locator: await game.close(c), lines };
}

describe("fast-chain WP6 evidence", () => {
  it("writes only the de-handled action prefix for a clean finish that the manager did not choose", async () => {
    const dir = join(await root(), "not", "yet", "made");
    const { locator } = await play(dir, meta(), closed());
    expect(locator).toBe(join(dir, "g_1_a"));
    expect((await readdir(locator!)).sort()).toEqual([FAST_ACTIONS_FILE]);
    const text = await readFile(join(locator!, FAST_ACTIONS_FILE), "utf8");
    expect(text).not.toContain("\"input_line\":");
    expect(text).not.toContain("only-in-originals");
    const read = await readFastGameEvidence(locator!);
    expect(read.open).toMatchObject({ kind: "open", game_instance_id: "g/1:a", r7: false });
    expect(read.steps.map(s => s.row_id)).toEqual([1, null, 4]);
    expect(read.boundaries).toEqual([{ kind: "boundary", first_row_id: 1, last_row_id: 1, steps: 2 },
      { kind: "boundary", first_row_id: 4, last_row_id: 4, steps: 3 }]);
    expect(read.close).toMatchObject({ reason: "terminal", steps: 3, native_lines_seen: 3, retained: [], native_lines: null, input_lines: null });
    expect(read).toMatchObject({ state: "closed", satisfied_steps: 3, torn_tail: false, native_lines: null, input_lines: null });
    // Each action line stays small (design v2 §2.8: about 300 B).
    for (const record of text.trim().split("\n").slice(1, 4)) expect(Buffer.byteLength(record)).toBeLessThan(800);
  });

  it("keeps every original, exactly, for a game the manager chose (R7 / 5% / evaluation)", async () => {
    for (const chosen of [{ r7: true }, { retain_originals: true }]) {
      const dir = await root();
      const { locator, lines } = await play(dir, meta(chosen), closed());
      const read = await readFastGameEvidence(locator!);
      expect(read.close!.retained).toEqual([Object.keys(chosen)[0]]);
      expect(read.native_lines).toEqual(lines);
      expect(read.input_lines!.map(i => [i.row_id, i.step_index, JSON.parse(i.line).row_id])).toEqual([[1, 0, 1], [4, 3, 4]]);
      expect((await readdir(locator!)).sort()).toEqual([FAST_ACTIONS_FILE, FAST_INPUT_LINES_FILE, FAST_NATIVE_LINES_FILE]);
    }
  });

  it("keeps all originals for every failure, unknown and truncation, decided at close", async () => {
    const cases: [Partial<FastGameClose>, Partial<FastStepRecord>, string[]][] = [
      ...(["not_started", "unknown", "stop", "budget", "env_crash"] as const).map(reason =>
        [{ reason, terminal: null }, {}, [`close:${reason}`]] as [Partial<FastGameClose>, Partial<FastStepRecord>, string[]]),
      [{ terminal: null }, {}, ["terminal_missing"]],
      [{ native_replaced: true }, {}, ["native_replaced"]],
      [{}, { status: "unknown" }, ["step:unknown"]],
      [{ reason: "unknown", terminal: null, native_replaced: true }, { status: "interrupted_known" }, ["close:unknown", "native_replaced", "step:interrupted_known"]]
    ];
    for (const [c, s, reasons] of cases) {
      const dir = await root();
      const { locator, lines } = await play(dir, meta(), closed(c), s);
      const read = await readFastGameEvidence(locator!);
      expect(read.close!.retained).toEqual(reasons);
      expect(read.native_lines).toEqual(lines);
      expect(read.input_lines).toHaveLength(2);
    }
  });

  it("keeps the input lines when compact output is off and refuses native lines it was not told about", async () => {
    const dir = await root();
    const { locator } = await play(dir, meta({ native_lines: false }), closed({ reason: "stop", terminal: null }));
    const read = await readFastGameEvidence(locator!);
    expect(read.close).toMatchObject({ retained: ["close:stop"], native_lines: null, native_lines_seen: 0 });
    expect(read.input_lines).toHaveLength(2);
    const game = new FastActionEvidenceSink(dir).openGame(meta({ game_instance_id: "other", native_lines: false }));
    expect(() => game.nativeLine("x")).toThrow("fast_evidence_native_lines_off");
  });

  it("makes each boundary durable before it resolves; a crash leaves an unsatisfied tail, never a pretend close", async () => {
    const dir = await root();
    const game = new FastActionEvidenceSink(dir).openGame(meta());
    game.nativeLine(line(0));
    game.step(step(1, line(0)));
    await game.segmentBoundary({ first_row_id: 1, last_row_id: 1 });
    const gameDir = join(dir, "g_1_a");
    // Durable on disk now (the process may die any time after this).
    expect((await readFile(join(gameDir, FAST_ACTIONS_FILE), "utf8")).trim().split("\n").map(l => JSON.parse(l).kind))
      .toEqual(["open", "step", "boundary"]);
    game.step(step(2, line(0)));
    // The process dies here: the step after the boundary was never flushed.
    let read = await readFastGameEvidence(gameDir);
    expect(read).toMatchObject({ state: "unsatisfied", satisfied_steps: 1, close: null, torn_tail: false });
    // A crash in the middle of an append leaves a torn last line; the reader marks it, not parses it.
    await writeFile(join(gameDir, FAST_ACTIONS_FILE), `${await readFile(join(gameDir, FAST_ACTIONS_FILE), "utf8")}{"kind":"st`);
    read = await readFastGameEvidence(gameDir);
    expect(read).toMatchObject({ state: "unsatisfied", satisfied_steps: 1, torn_tail: true });
  });

  it("fails loudly and stays failed when it cannot write; it never overwrites another game's evidence", async () => {
    const dir = await root();
    const blocker = join(dir, "file");
    await writeFile(blocker, "not a directory");
    const game = new FastActionEvidenceSink(blocker).openGame(meta());
    game.step(step(1, null));
    await expect(game.segmentBoundary({ first_row_id: 1, last_row_id: 1 })).rejects.toThrow();
    expect(() => game.step(step(2, null))).toThrow(/^fast_evidence_failed:/u);
    await expect(game.close(closed())).rejects.toThrow(/^fast_evidence_failed:/u);

    await play(dir, meta(), closed());
    const again = new FastActionEvidenceSink(dir).openGame(meta());
    again.step(step(1, null));
    await expect(again.segmentBoundary({ first_row_id: 1, last_row_id: 1 })).rejects.toThrow(/EEXIST/u);
    expect((await readFastGameEvidence(join(dir, "g_1_a"))).state).toBe("closed");
  });

  it("closes exactly once", async () => {
    const dir = await root();
    const game = new FastActionEvidenceSink(dir).openGame(meta());
    await game.close(closed({ decisions: 0, native_steps: 0, last_step_index: null }));
    await expect(game.close(closed())).rejects.toThrow("fast_evidence_closed:close");
    expect(() => game.step(step(1, null))).toThrow("fast_evidence_closed:step");
    expect(() => game.nativeLine("x")).toThrow("fast_evidence_closed:native_line");
    // No steps and no rows: still one durable open + close.
    expect((await readFastGameEvidence(join(dir, "g_1_a"))).close).toMatchObject({ steps: 0, retained: [] });
  });

  it("rejects changed originals, a frame without its native line, and records after close", async () => {
    const dir = await root();
    const { locator } = await play(dir, meta({ retain_originals: true }), closed());
    const actions = join(locator!, FAST_ACTIONS_FILE), text = await readFile(actions, "utf8");
    await writeFile(join(locator!, FAST_NATIVE_LINES_FILE), Buffer.from("changed"));
    await expect(readFastGameEvidence(locator!)).rejects.toThrow("fast_evidence_originals_file");

    const dir2 = await root();
    const second = await play(dir2, meta({ retain_originals: true }), closed());
    const path2 = join(second.locator!, FAST_ACTIONS_FILE), text2 = await readFile(path2, "utf8");
    await writeFile(path2, text2.replace(sha(line(2)), sha("a line that never came")));
    await expect(readFastGameEvidence(second.locator!)).rejects.toThrow("fast_evidence_frame_line");
    await writeFile(path2, `${text2}${JSON.stringify({ kind: "boundary", first_row_id: 9, last_row_id: 9, steps: 3 })}\n`);
    await expect(readFastGameEvidence(second.locator!)).rejects.toThrow("fast_evidence_after_close");
    expect(text).toContain("\"kind\":\"close\"");
  });

  it("keeps every native line of a refused open as a failure, and reads it back", async () => {
    const dir = await root();
    const sink = new FastActionEvidenceSink(dir);
    const lines = [line(0), line(1)];
    const locator = await sink.refusedOpen({ slot: 2, game_ref: "ref/9", start: { kind: "from_save", save_sha256: "4".repeat(64), act: 2, floor: 18 },
      reason: "reset_failed:compact_state_divergence", native_lines: lines });
    expect(locator!.startsWith(join(dir, "refused-open-ref_9-"))).toBe(true);
    expect((await readdir(locator!)).sort()).toEqual([FAST_ACTIONS_FILE, FAST_NATIVE_LINES_FILE]);
    const read = await readFastGameEvidence(locator!);
    expect(read).toMatchObject({ state: "refused", open: null, close: null, steps: [], native_lines: lines,
      refused: { kind: "refused_open", slot: 2, game_ref: "ref/9", start: { kind: "from_save", act: 2, floor: 18 },
        reason: "reset_failed:compact_state_divergence", retained: ["refused_open:reset_failed:compact_state_divergence"] } });
    // Two refusals of the same game_ref never share or overwrite a directory; no lines is still a record.
    const second = await sink.refusedOpen({ slot: 2, game_ref: "ref/9", start: { kind: "new_run" }, reason: "native_start_changed", native_lines: [] });
    expect(second).not.toBe(locator);
    expect((await readFastGameEvidence(second!)).native_lines).toEqual([]);
    // A changed refusal record or original is refused by the reader.
    await writeFile(join(second!, FAST_ACTIONS_FILE), `${await readFile(join(second!, FAST_ACTIONS_FILE), "utf8")}{}\n`);
    await expect(readFastGameEvidence(second!)).rejects.toThrow("fast_evidence_refused_open");
    await writeFile(join(locator!, FAST_NATIVE_LINES_FILE), Buffer.from("changed"));
    await expect(readFastGameEvidence(locator!)).rejects.toThrow("fast_evidence_originals_file");
  });

  it("a failed refused-open write is loud and sticks: later refusals and every game's boundary and close report it", async () => {
    const dir = await root();
    const blocker = join(dir, "file");
    await writeFile(blocker, "not a directory");
    const sink = new FastActionEvidenceSink(blocker);
    const refused = { slot: 0, game_ref: "r", start: { kind: "new_run" } as const, reason: "reset_failed:x", native_lines: [line(0)] };
    await expect(sink.refusedOpen(refused)).rejects.toThrow();
    await expect(sink.refusedOpen(refused)).rejects.toThrow(/^fast_evidence_failed:/u);
    const game = sink.openGame(meta());
    // The hot path (Host line tap, step) is never interrupted; the durability points report it.
    game.nativeLine(line(0));
    game.step(step(1, line(0)));
    await expect(game.segmentBoundary({ first_row_id: 1, last_row_id: 1 })).rejects.toThrow(/^fast_evidence_failed:/u);
    await expect(game.close(closed())).rejects.toThrow(/^fast_evidence_failed:/u);
  });

  it("names the retain reasons from the manager choice and the close only", () => {
    const none = new Set<FastStepRecord["status"]>(["completed"]);
    expect(fastRetainReasons({ r7: false, retain_originals: false }, closed(), none)).toEqual([]);
    expect(fastRetainReasons({ r7: true, retain_originals: true }, closed(), none)).toEqual(["r7", "retain_originals"]);
    expect(fastRetainReasons({ r7: false, retain_originals: false }, closed(), new Set(["completed", "not_started"]))).toEqual(["step:not_started"]);
  });
});
