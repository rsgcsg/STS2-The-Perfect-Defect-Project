import { createHash } from "node:crypto";
import { jsonWithPySha256, pyJsonText } from "./json-bytes.js";

/**
 * P-REWRITE design v3 E7, `fast-env-link-2`: a decision line carries only the observation parts
 * that changed since the game's previous decision line. A part is one top-level value of the model
 * view, or one element of a top-level list. The first line of a game is a full frame
 * (`base_row_id: null`); every later line refers to the previous line of the same game.
 *
 * The `frame` object of a line:
 *   base_row_id   null | row_id of the previous decision line of this game
 *   frame_sha256  digest of the whole observation text (below)
 *   keys          the view's top-level keys in insertion order (the encoder's alias order needs it)
 *   layout        per key: "n" = the next part (never a list), "s" = the base's value for the key
 *                 unchanged, or an array for a list value: -1 = the next part, k >= 0 = element k
 *                 of the base's list for the same key
 *   lengths       UTF-8 byte length of each part's text, in part order
 * and the line ends with `"parts":[...]`, the new parts' JSON texts in order.
 *
 * A part's text is its JSON.stringify text; JSON.stringify(view) is `{"k":text,...}` over the keys
 * in order, a list's text `[e1,...]`. frame_sha256 = sha256 over the keys in order of
 * utf8(key) 0x00 then `v` sha256(text) for a value, or `l` uint32be(n) sha256(e1) .. sha256(en)
 * for a list (binary digests). It fixes every key and every part text, so the text the receiver
 * rebuilds equals JSON.stringify(view) byte for byte, and each part is hashed only when it is new.
 *
 * "Unchanged" means the same JSON.stringify text. The receiver checks every line twice: its rebuilt
 * parts must give `frame_sha256`, and the sorted-key `observation_sha256` it encodes must equal this
 * line's, which `encode` builds from per-part cached pyJson bytes. Any difference refuses the row;
 * nothing falls back to a full frame. Here, frame 0 and then one frame per gap drawn uniformly from
 * ceil(N/2)..N (N = `checkInterval`, the E4 scheme) are compared against the old full computation
 * (JSON.stringify(view) and pyJsonSha256(view), via R5 jsonWithPySha256); a difference throws ObservationFrameDivergence.
 */

/** Top-level keys a frame may carry: ASCII, so JSON and Python spell and sort them alike. */
export const FRAME_KEY = /^[A-Za-z0-9_]+$/u;

interface WholePart { readonly list: false; readonly text: string; readonly digest: Buffer; readonly py: Buffer }
interface ListPart {
  readonly list: true; readonly text: string; readonly py: Buffer;
  readonly texts: readonly string[]; readonly digests: readonly Buffer[]; readonly pys: readonly Buffer[];
  /** text -> first element index, built when a later frame first looks an element up. */
  index: Map<string, number> | null;
}
type Part = WholePart | ListPart;

export interface EncodedFrame {
  /** The JSON text of the line's `frame` object. */
  frame: string;
  /** The JSON text of the line's `parts` array. */
  parts: string;
  /** digest(json_bytes(view)): equal to pyJsonSha256(view). */
  observationSha256: string;
  frameSha256: string;
  /** Parts sent / parts in the frame (top-level values plus list elements). */
  sent: number;
  total: number;
}

/** An E7 frame differed from the full computation: this process's frame encoding is wrong. Fatal, never a fallback. */
export class ObservationFrameDivergence extends Error {
  readonly code = "observation_frame_divergence";
  constructor(readonly frameIndex: number, readonly what: "text" | "observation_sha256") {
    super(`observation_frame_divergence: frame ${frameIndex} ${what} differs from the full computation`);
    this.name = "ObservationFrameDivergence";
  }
}

export interface ObservationFramesStatus { check_interval: number; frames: number; verified: number; parts_sent: number; parts_total: number }

const OPEN = Buffer.from("["), CLOSE = Buffer.from("]"), COMMA = Buffer.from(","), EMPTY_LIST = Buffer.from("[]");
const VALUE = Buffer.from("\0v"), LIST = Buffer.from("\0l");
const digestOf = (text: string): Buffer => createHash("sha256").update(text, "utf8").digest();

/** One game's sending side of fast-env-link-2 frames. */
export class ObservationFrames {
  #base: { rowId: number; parts: Map<string, Part> } | null = null;
  readonly #interval: number;
  #rng: number;
  #frames = 0;
  #nextCheck = 0;
  #verified = 0;
  #sent = 0;
  #total = 0;

  constructor(options: { checkInterval: number; checkSeed?: number | null }) {
    if (!Number.isSafeInteger(options.checkInterval) || options.checkInterval < 1 || options.checkInterval > 1024)
      throw new TypeError("checkInterval must be an integer in 1..1024");
    this.#interval = options.checkInterval;
    const seed = options.checkSeed ?? Math.floor(Math.random() * 0x100000000);
    this.#rng = (seed >>> 0) || 0x9e3779b9;
  }

  /** Next gap, uniform in ceil(N/2)..N (xorshift32; it only picks which frames get compared). */
  #gap(): number {
    let x = this.#rng;
    x ^= x << 13; x >>>= 0; x ^= x >>> 17; x ^= x << 5; x >>>= 0;
    this.#rng = x;
    const low = Math.ceil(this.#interval / 2);
    return low + x % (this.#interval - low + 1);
  }

  /**
   * The frame of decision row `rowId`; from now on it is this game's base. Call it once per
   * decision line actually sent (a game never continues past a row that was not accepted).
   */
  encode(view: Record<string, unknown>, rowId: number): EncodedFrame {
    const base = this.#base?.parts ?? null;
    const parts = new Map<string, Part>();
    const keys: string[] = [], layout: (string | number[])[] = [], sent: string[] = [];
    const frameDigest = createHash("sha256");
    let total = 0;
    for (const key of Object.keys(view)) {
      if (!FRAME_KEY.test(key)) throw new TypeError("observation_frame_key");
      const value = view[key];
      const old = base?.get(key);
      let part: Part;
      if (Array.isArray(value)) {
        const texts: string[] = new Array(value.length);
        for (let i = 0; i < value.length; i += 1) texts[i] = JSON.stringify(value[i]) ?? "null";
        const whole = `[${texts.join(",")}]`;
        total += value.length;
        if (old !== undefined && old.list && old.text === whole) {
          part = old; layout.push("s");
        } else {
          const refs: number[] = new Array(value.length), digests: Buffer[] = new Array(value.length);
          const pys: Buffer[] = new Array(value.length), joined: Buffer[] = [OPEN];
          const index = old !== undefined && old.list ? (old.index ??= indexOf(old.texts)) : null;
          for (let i = 0; i < value.length; i += 1) {
            const at = index?.get(texts[i]!);
            if (at !== undefined) {
              const from = old as ListPart;
              refs[i] = at; digests[i] = from.digests[at]!; pys[i] = from.pys[at]!;
            } else {
              refs[i] = -1; sent.push(texts[i]!); digests[i] = digestOf(texts[i]!); pys[i] = Buffer.from(pyJsonText(value[i]), "utf8");
            }
            if (i) joined.push(COMMA);
            joined.push(pys[i]!);
          }
          joined.push(CLOSE);
          part = { list: true, text: whole, py: value.length ? Buffer.concat(joined) : EMPTY_LIST, texts, digests, pys, index: null };
          layout.push(refs);
        }
      } else {
        const whole = JSON.stringify(value);
        // JSON.stringify drops such a key, pyJson refuses it: refuse it as today's pyJsonSha256(view) did.
        if (whole === undefined) pyJsonText(value);
        total += 1;
        if (old !== undefined && !old.list && old.text === whole) { part = old; layout.push("s"); }
        else {
          part = { list: false, text: whole!, digest: digestOf(whole!), py: Buffer.from(pyJsonText(value), "utf8") };
          sent.push(whole!); layout.push("n");
        }
      }
      parts.set(key, part);
      keys.push(key);
      frameDigest.update(key, "latin1");
      if (part.list) {
        const count = Buffer.alloc(4);
        count.writeUInt32BE(part.digests.length);
        frameDigest.update(LIST); frameDigest.update(count);
        for (const digest of part.digests) frameDigest.update(digest);
      } else {
        frameDigest.update(VALUE); frameDigest.update(part.digest);
      }
    }
    // json_bytes(view): sort_keys over ASCII keys is the code-unit sort.
    const sorted = keys.slice().sort(), observation: Buffer[] = [];
    sorted.forEach((key, i) => { observation.push(Buffer.from(`${i ? "," : "{"}"${key}":`, "latin1"), parts.get(key)!.py); });
    observation.push(Buffer.from(sorted.length ? "}" : "{}", "latin1"));
    const observationSha256 = createHash("sha256").update(Buffer.concat(observation)).digest("hex");
    const frameSha256 = frameDigest.digest("hex");
    const frameIndex = this.#frames++;
    if (frameIndex === this.#nextCheck) {
      this.#nextCheck += this.#gap();
      const text = `{${keys.map(key => `"${key}":${parts.get(key)!.text}`).join(",")}}`;
      const full = jsonWithPySha256(view); // = JSON.stringify(view) and pyJsonSha256(view) (R5)
      if (full.json !== text) throw new ObservationFrameDivergence(frameIndex, "text");
      if (full.sha256() !== observationSha256) throw new ObservationFrameDivergence(frameIndex, "observation_sha256");
      this.#verified += 1;
    }
    const lengths = sent.map(item => Buffer.byteLength(item, "utf8"));
    const frame = `{"base_row_id":${this.#base === null ? "null" : this.#base.rowId},"frame_sha256":"${frameSha256}",`
      + `"keys":${JSON.stringify(keys)},"layout":${JSON.stringify(layout)},"lengths":${JSON.stringify(lengths)}}`;
    this.#base = { rowId, parts };
    this.#sent += sent.length; this.#total += total;
    return { frame, parts: `[${sent.join(",")}]`, observationSha256, frameSha256, sent: sent.length, total };
  }

  status(): ObservationFramesStatus {
    return { check_interval: this.#interval, frames: this.#frames, verified: this.#verified, parts_sent: this.#sent, parts_total: this.#total };
  }
}

function indexOf(texts: readonly string[]): Map<string, number> {
  const index = new Map<string, number>();
  for (let i = 0; i < texts.length; i += 1) if (!index.has(texts[i]!)) index.set(texts[i]!, i);
  return index;
}
