import { createHash } from "node:crypto";

/**
 * Test-side receiver of fast-env-link-2 frames, written from the frame format alone (it shares no code
 * with src/fast-env/frame-delta.ts): it slices the parts by `lengths`, rebuilds the observation of each
 * decision line of one game, checks `frame_sha256`, and returns the rebuilt observation text.
 */
const sha = (bytes: Buffer): Buffer => createHash("sha256").update(bytes).digest();

export class FrameReader {
  #base: { rowId: number; values: Map<string, unknown>; texts: Map<string, Buffer | Buffer[]> } | null = null;

  /** `line` is the decision line exactly as sent (without the newline). */
  read(line: string): { observation: Record<string, unknown>; text: string } {
    const raw = Buffer.from(line, "utf8");
    const message = JSON.parse(line);
    const frame = message.input.frame;
    const open = raw.indexOf(',"parts":[');
    if (open < 0 || !line.endsWith("]}")) throw new Error("frame_line_layout");
    const parts: Buffer[] = [];
    let cursor = open + ',"parts":['.length;
    frame.lengths.forEach((length: number, index: number) => {
      if (index) { if (raw[cursor] !== 0x2c) throw new Error("frame_lengths"); cursor += 1; }
      parts.push(raw.subarray(cursor, cursor + length)); cursor += length;
    });
    if (cursor !== raw.length - 2) throw new Error("frame_lengths");
    if ((frame.base_row_id === null) !== (this.#base === null)) throw new Error("frame_base");
    if (this.#base !== null && frame.base_row_id !== this.#base.rowId) throw new Error("frame_base");
    let taken = 0;
    const next = (): Buffer => { const part = parts[taken++]; if (part === undefined) throw new Error("frame_parts"); return part; };
    const values = new Map<string, unknown>(), texts = new Map<string, Buffer | Buffer[]>();
    const digest = createHash("sha256"), pieces: string[] = [];
    frame.keys.forEach((key: string, i: number) => {
      const entry = frame.layout[i];
      let text: Buffer | Buffer[];
      if (entry === "s") text = this.#base!.texts.get(key)!;
      else if (entry === "n") text = next();
      else {
        const old = this.#base?.texts.get(key);
        text = (entry as number[]).map(k => {
          if (k < 0) return next();
          if (!Array.isArray(old) || old[k] === undefined) throw new Error("frame_layout");
          return old[k]!;
        });
      }
      if (text === undefined) throw new Error("frame_layout");
      digest.update(key, "utf8");
      if (Array.isArray(text)) {
        const count = Buffer.alloc(4); count.writeUInt32BE(text.length);
        digest.update(Buffer.from("\0l")); digest.update(count);
        for (const item of text) digest.update(sha(item));
        pieces.push(`"${key}":[${text.map(item => item.toString("utf8")).join(",")}]`);
      } else {
        digest.update(Buffer.from("\0v")); digest.update(sha(text));
        pieces.push(`"${key}":${text.toString("utf8")}`);
      }
      texts.set(key, text);
    });
    if (taken !== parts.length) throw new Error("frame_parts");
    if (digest.digest("hex") !== frame.frame_sha256) throw new Error("frame_sha256");
    const text = `{${pieces.join(",")}}`;
    const observation = JSON.parse(text);
    for (const key of Object.keys(observation)) values.set(key, observation[key]);
    this.#base = { rowId: message.row_id, values, texts };
    return { observation, text };
  }
}
