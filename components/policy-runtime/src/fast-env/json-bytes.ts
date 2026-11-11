import { createHash } from "node:crypto";

/**
 * The bytes of Python `stpd.p1.data.json_bytes` (sort_keys, compact separators,
 * ensure_ascii=False, allow_nan=False), so `observation_sha256` here is the same
 * `digest(json_bytes(observation))` the encoder, the dataset and the batch
 * preparation compute. Keys sort by Unicode code point, as Python's do (not by
 * `localeCompare`). Numbers are written as JSON.stringify writes them, which equals
 * Python's text except for a nonzero magnitude below 1e-4 (JS `0.00001`, Python `1e-05`),
 * which is refused rather than hashed wrong.
 */
export function pyJsonBytes(value: unknown): Buffer {
  return Buffer.from(pyJson(value), "utf8");
}

export function pyJsonSha256(value: unknown): string {
  return createHash("sha256").update(pyJson(value), "utf8").digest("hex");
}

/** Any UTF-16 surrogate code unit (no `u` flag: halves of a pair match too). */
const SURROGATE = /[\uD800-\uDFFF]/;
/** With the `u` flag a class matches whole code points, so only a lone surrogate matches. */
const LONE_SURROGATE = /[\uD800-\uDFFF]/u;

function byCodePoint(left: string, right: string): number {
  if (!SURROGATE.test(left) && !SURROGATE.test(right)) return left < right ? -1 : left > right ? 1 : 0;
  const a = [...left], b = [...right];
  for (let index = 0; index < Math.min(a.length, b.length); index += 1) {
    const difference = a[index]!.codePointAt(0)! - b[index]!.codePointAt(0)!;
    if (difference !== 0) return difference;
  }
  return a.length - b.length;
}

/**
 * A string whose JSON spelling is itself in quotes: printable ASCII without `"` or `\`.
 * Anything else (controls, DEL and every non-ASCII unit, surrogates included) takes
 * JSON.stringify, as every string did before; the two spellings agree on this set.
 */
const NEEDS_STRINGIFY = /["\\\u0000-\u001f\u007f-￿]/;

function quoted(text: string): string {
  return NEEDS_STRINGIFY.test(text) ? JSON.stringify(text) : `"${text}"`;
}

/** Key order of Python sort_keys: plain sort when no key holds a surrogate (code units = code points). */
function pythonKeyOrder(object: object): string[] {
  const keys = Object.keys(object);
  return keys.some(key => SURROGATE.test(key)) ? keys.sort(byCodePoint) : keys.sort();
}

function pyJson(value: unknown): string {
  switch (typeof value) {
    case "boolean": return value ? "true" : "false";
    case "string":
      if (!NEEDS_STRINGIFY.test(value)) return `"${value}"`;
      if (LONE_SURROGATE.test(value)) throw new TypeError("py_json_lone_surrogate");
      return JSON.stringify(value);
    case "number": {
      if (!Number.isFinite(value)) throw new TypeError("py_json_nonfinite_number");
      if (value !== 0 && Math.abs(value) < 1e-4) throw new TypeError("py_json_float_exponent");
      return JSON.stringify(value);
    }
    case "object": {
      if (value === null) return "null";
      if (Array.isArray(value)) {
        let text = "[";
        for (let index = 0; index < value.length; index += 1) text += (index ? "," : "") + pyJson(value[index]);
        return `${text}]`;
      }
      const keys = pythonKeyOrder(value);
      let text = "{";
      for (let index = 0; index < keys.length; index += 1) {
        const key = keys[index]!;
        text += `${index ? "," : ""}${quoted(key)}:${pyJson((value as Record<string, unknown>)[key])}`;
      }
      return `${text}}`;
    }
    default: throw new TypeError("py_json_non_json_value");
  }
}

const omitted = (value: unknown): boolean => value === undefined || typeof value === "function" || typeof value === "symbol";

/**
 * The bytes of `evidence.canonicalJson` (JSON.stringify of the localeCompare-sorted
 * copy) written in one pass, without building the sorted copy. Same key order, same
 * omissions (undefined, functions and symbols: dropped in objects, null in arrays and
 * holes), same primitive spelling, same key order (array-index keys first, as the copy
 * has them). The one input the sorted copy treats differently, an
 * own `toJSON` function (JSON.stringify of the copy would call it), is refused.
 */
export function canonicalJsonFast(value: unknown): string {
  return canonicalText(value, null);
}

/** The one canonicalJsonFast walk; with a memo, a deeply frozen object's text is kept by identity. */
function canonicalText(value: unknown, memo: CanonicalTextMemo | null): string {
  switch (typeof value) {
    case "string": return quoted(value);
    case "boolean": return value ? "true" : "false";
    case "object": break;
    default: return JSON.stringify(value);
  }
  if (value === null) return "null";
  let kept: string | undefined;
  const frozen = memo !== null && memo.frozen(value);
  if (frozen && (kept = memo.texts.get(value)) !== undefined) { memo.hits += 1; return kept; }
  let text: string;
  if (Array.isArray(value)) {
    text = "[";
    for (let index = 0; index < value.length; index += 1) {
      const item: unknown = value[index];
      text += (index ? "," : "") + (omitted(item) ? "null" : canonicalText(item, memo));
    }
    text += "]";
  } else {
    text = "{";
    let first = true;
    for (const key of canonicalKeyOrder(value)) {
      const item: unknown = (value as Record<string, unknown>)[key];
      if (omitted(item)) {
        if (key === "toJSON" && typeof item === "function") throw new TypeError("canonical_json_to_json_function");
        continue;
      }
      text += `${first ? "" : ","}${quoted(key)}:${canonicalText(item, memo)}`;
      first = false;
    }
    text += "}";
  }
  if (frozen) { memo.texts.set(value, text); memo.misses += 1; }
  return text;
}

/** E4 found a kept text that differs from the full walk: this process's memo is wrong. Fatal, never a fallback. */
export class CanonicalTextMemoDivergence extends Error {
  readonly code = "receipt_canonical_memo_divergence";
  constructor(readonly digest: number, readonly offset: number) {
    super(`receipt_canonical_memo_divergence: digest ${digest} differs from the full walk at byte ${offset}`);
    this.name = "CanonicalTextMemoDivergence";
  }
}

export interface CanonicalTextMemoStatus { check_interval: number; digests: number; verified: number; hits: number; misses: number }

/**
 * P-REWRITE fast_chain only (design v3 E4, off by default): the receipt digest keeps the
 * canonical text of every deeply frozen subtree by object identity. It builds on the E5
 * identity rule (the Host hands an unchanged projected subtree over again as the same
 * deep-frozen object; `isDeepFrozen` is the SDK's one check of that premise) and on the
 * strict receipt decode keeping content: every receipt object is strict, passthrough or
 * opaque and nothing is defaulted or transformed, so the original receipt and its decoded
 * copy have the same canonical text. Only the original keeps the identities.
 *
 * Safety: digest 0 and then one digest per gap, the gap drawn uniformly from ceil(N/2)..N
 * (N = `checkInterval`; the scheme of the E5 projection memo and the E6 reduce memo), are
 * computed again as the old path does, `canonicalJsonFast(decoded)` without the memo, and
 * compared byte for byte. A difference throws CanonicalTextMemoDivergence. N = 1 compares
 * every digest. The comparison is part of the measured cost.
 */
export class CanonicalTextMemo {
  readonly texts = new WeakMap<object, string>();
  readonly frozen: (value: object) => boolean;
  readonly #interval: number;
  #rng: number;
  #digests = 0;
  #nextCheck = 0;
  #verified = 0;
  #all = false;
  hits = 0;
  misses = 0;

  constructor(options: { checkInterval: number; checkSeed?: number | null; isDeepFrozen: (value: object) => boolean }) {
    if (!Number.isSafeInteger(options.checkInterval) || options.checkInterval < 1 || options.checkInterval > 1024)
      throw new TypeError("checkInterval must be an integer in 1..1024");
    this.#interval = options.checkInterval;
    this.frozen = options.isDeepFrozen;
    const seed = options.checkSeed ?? Math.floor(Math.random() * 0x100000000);
    this.#rng = (seed >>> 0) || 0x9e3779b9;
  }

  /** Next gap, uniform in ceil(N/2)..N (xorshift32; it only picks which digests get compared). */
  #gap(): number {
    let x = this.#rng;
    x ^= x << 13; x >>>= 0; x ^= x >>> 17; x ^= x << 5; x >>>= 0;
    this.#rng = x;
    const low = Math.ceil(this.#interval / 2);
    return low + x % (this.#interval - low + 1);
  }

  /** globalDigestFast(decoded), read from the original receipt `raw` (same canonical text). */
  digest(raw: unknown, decoded: unknown): string {
    const index = this.#digests++;
    const text = canonicalText(raw, this);
    if (this.#all || index === this.#nextCheck) {
      if (!this.#all) this.#nextCheck += this.#gap();
      const full = canonicalText(decoded, null);
      if (full !== text) {
        let offset = 0;
        while (offset < text.length && text.charCodeAt(offset) === full.charCodeAt(offset)) offset += 1;
        throw new CanonicalTextMemoDivergence(index, offset);
      }
      this.#verified += 1;
    }
    return createHash("sha256").update(text).digest("hex");
  }

  /** From now on every digest is compared (after a fatal divergence, while the process drains). */
  verifyAll(): void { this.#all = true; }

  status(): CanonicalTextMemoStatus {
    return { check_interval: this.#interval, digests: this.#digests, verified: this.#verified, hits: this.hits, misses: this.misses };
  }
}

const ARRAY_INDEX = /^(?:0|[1-9][0-9]*)$/;
const isArrayIndex = (key: string): boolean => ARRAY_INDEX.test(key) && Number(key) < 4294967295;

/**
 * The key order of canonicalJson's sorted copy: Object.fromEntries puts array-index keys
 * first, ascending, whatever the sort did; the other keys follow in localeCompare order.
 * Object.keys already lists the index keys first and ascending.
 */
function canonicalKeyOrder(object: object): string[] {
  const keys = Object.keys(object);
  if (keys.length < 2) return keys;
  // localeCompare dominates once no copy is built; objects repeat a few shapes, so the
  // order of one exact key list is kept (bounded, checked key by key on every hit).
  const id = keys.join("\u0000"), hit = keyOrders.get(id);
  if (hit && hit.keys.length === keys.length && hit.keys.every((key, index) => key === keys[index])) return hit.order;
  let indices = 0;
  while (indices < keys.length && isArrayIndex(keys[indices]!)) indices += 1;
  const order = keys.slice(0, indices).concat(keys.slice(indices).sort((left, right) => left.localeCompare(right)));
  if (keyOrders.size >= KEY_ORDER_LIMIT) keyOrders.clear();
  keyOrders.set(id, { keys, order });
  return order;
}
const KEY_ORDER_LIMIT = 4096;
const keyOrders = new Map<string, { keys: string[]; order: string[] }>();

/** `globalDigest` (sha256 of `canonicalJson`), the same hex from `canonicalJsonFast`. */
export function globalDigestFast(value: unknown): string {
  return createHash("sha256").update(canonicalJsonFast(value)).digest("hex");
}
