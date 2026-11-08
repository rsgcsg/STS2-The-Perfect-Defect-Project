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

function pyJson(value: unknown): string {
  if (value === null) return "null";
  switch (typeof value) {
    case "boolean": return value ? "true" : "false";
    case "string":
      if (LONE_SURROGATE.test(value)) throw new TypeError("py_json_lone_surrogate");
      return JSON.stringify(value);
    case "number": {
      if (!Number.isFinite(value)) throw new TypeError("py_json_nonfinite_number");
      if (value !== 0 && Math.abs(value) < 1e-4) throw new TypeError("py_json_float_exponent");
      return JSON.stringify(value);
    }
    case "object": {
      if (Array.isArray(value)) return `[${value.map(pyJson).join(",")}]`;
      const keys = Object.keys(value as object).sort(byCodePoint);
      return `{${keys.map(key => `${JSON.stringify(key)}:${pyJson((value as Record<string, unknown>)[key])}`).join(",")}}`;
    }
    default: throw new TypeError("py_json_non_json_value");
  }
}
