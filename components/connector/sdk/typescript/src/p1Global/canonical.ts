import { createHash } from "node:crypto";

/** Deterministic JSON: object keys sorted, arrays kept in their given order. */
export function canonicalJson(value: unknown): string {
  // Ordinary JSON data is written directly; anything else keeps the copy-then-stringify path.
  if (!("toJSON" in OBJECT_PROTO) && !("toJSON" in ARRAY_PROTO)) {
    try { return plainJson(value); } catch (error) { if (error !== NOT_PLAIN) throw error; }
  }
  return JSON.stringify(sortKeys(value));
}

/* plainJson writes exactly JSON.stringify(sortKeys(value)) for a tree of strings,
 * numbers, booleans, null, Array.prototype arrays and Object.prototype / null-prototype
 * objects. Each property is read once, as the copy below reads it once. It throws
 * NOT_PLAIN (and canonicalJson then copies) on anything else: a top-level undefined,
 * function, symbol or bigint; an own toJSON or __proto__ key; an object key starting
 * with a digit (an array-index key keeps numeric order in the copy); any other prototype. */
const NOT_PLAIN = Symbol("not_plain");
const OBJECT_PROTO = Object.prototype;
const ARRAY_PROTO = Array.prototype;
const SIMPLE_KEY = /^[A-Za-z_][A-Za-z0-9_]*$/u;
const KEY_LIMIT = 1 << 14;
const keyTexts = new Map<string, string>();
function keyText(key: string): string {
  let text = keyTexts.get(key);
  if (text === undefined) {
    const code = key.charCodeAt(0);
    if (code >= 48 && code <= 57 || key === "toJSON" || key === "__proto__") throw NOT_PLAIN;
    text = SIMPLE_KEY.test(key) ? `"${key}":` : `${JSON.stringify(key)}:`;
    if (keyTexts.size >= KEY_LIMIT) keyTexts.clear();
    keyTexts.set(key, text);
  }
  return text;
}
function plainJson(value: unknown): string {
  switch (typeof value) {
    case "string": return JSON.stringify(value);
    case "number": return Number.isFinite(value) ? String(value) : "null";
    case "boolean": return value ? "true" : "false";
    case "object": break;
    default: throw NOT_PLAIN;
  }
  if (value === null) return "null";
  if (Array.isArray(value)) {
    if (Object.getPrototypeOf(value) !== ARRAY_PROTO || hasOwn.call(value, "toJSON")) throw NOT_PLAIN;
    const length = value.length;
    if (length === 0) return "[]";
    let out = "[";
    for (let index = 0; index < length; index++) {
      const item = value[index];
      if (index > 0) out += ",";
      out += item === undefined || typeof item === "function" || typeof item === "symbol" ? "null" : plainJson(item);
    }
    return out + "]";
  }
  const proto = Object.getPrototypeOf(value);
  if (proto !== OBJECT_PROTO && proto !== null) throw NOT_PLAIN;
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record);
  for (let index = 1; index < keys.length; index++) if (keys[index - 1]! > keys[index]!) { keys.sort(); break; }
  let out = "{"; let first = true;
  for (let index = 0; index < keys.length; index++) {
    const key = keys[index]!; const label = keyText(key); const item = record[key];
    if (item === undefined || typeof item === "function" || typeof item === "symbol") continue;
    out += first ? label : "," + label;
    first = false;
    out += plainJson(item);
  }
  return out + "}";
}

/* sortKeys returns `value` itself only when a copy would serialize to the same
 * bytes: a plain array (Array.prototype, no own toJSON) or a plain-prototype
 * object whose own keys are already in sorted order, with no undefined value, no
 * own toJSON or __proto__ key (a copy drops them), no accessor property (a copy
 * reads it once, JSON.stringify of the original would read it again), and whose
 * children are all unchanged. Anything else is copied exactly as before. */
const hasOwn = Object.prototype.hasOwnProperty;
const accessor = (value: object, key: PropertyKey) => Object.getOwnPropertyDescriptor(value, key)?.get !== undefined;
function plainArray(value: unknown[]): boolean {
  if (Object.getPrototypeOf(value) !== Array.prototype || hasOwn.call(value, "toJSON")) return false;
  for (let index = 0; index < value.length; index++) if (accessor(value, index)) return false;
  return true;
}
function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) {
    // Checked before any read, so the fallback reads every element exactly once.
    if (!plainArray(value)) return value.map(sortKeys);
    let out: unknown[] | null = null;
    for (let index = 0; index < value.length; index++) {
      if (!(index in value)) { if (out) out.length = index + 1; continue; }
      const item = value[index]; const sorted = sortKeys(item);
      if (out === null && sorted !== item) out = value.slice(0, index);
      if (out !== null) out[index] = sorted;
    }
    if (out !== null) out.length = value.length;
    return out ?? value;
  }
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const keys = Object.keys(record);
    const proto = Object.getPrototypeOf(record);
    let reuse = proto === Object.prototype || proto === null;
    const sortedKeys = keys.slice().sort();
    const children: unknown[] = new Array(sortedKeys.length);
    for (let index = 0; index < sortedKeys.length; index++) {
      const key = sortedKeys[index]!; const item = record[key];
      if (reuse && (key !== keys[index] || item === undefined || key === "toJSON" || key === "__proto__")) reuse = false;
      const sorted = item === undefined ? undefined : sortKeys(item);
      if (sorted !== item) reuse = false;
      children[index] = sorted;
    }
    // Values were read once above; a copy is built from them, as before.
    if (reuse && !keys.some(key => accessor(record, key))) return record;
    const out: Record<string, unknown> = {};
    for (let index = 0; index < sortedKeys.length; index++)
      if (children[index] !== undefined) out[sortedKeys[index]!] = children[index];
    return out;
  }
  return value;
}

/** Exactly `Buffer.byteLength(canonicalJson(value), "utf8") > limit`, without building the text when a
 * cheap upper bound already fits: each UTF-16 unit of a string or key is at most 6 UTF-8 bytes in JSON
 * (a \u00XX escape), a finite number at most 25 characters, null/true/false at most 5. Any value that
 * is not plain JSON data, or a bound above the limit, takes the exact path (same result, same errors). */
export function canonicalJsonExceedsBytes(value: unknown, limit: number): boolean {
  if (value !== null && typeof value === "object" && !("toJSON" in OBJECT_PROTO) && !("toJSON" in ARRAY_PROTO)) {
    try { return boundBytes(value, limit) > limit && Buffer.byteLength(canonicalJson(value), "utf8") > limit; }
    catch (error) { if (error !== NOT_PLAIN) throw error; }
  }
  return Buffer.byteLength(canonicalJson(value), "utf8") > limit;
}
/** An upper bound of the canonical UTF-8 length; stops early once it passes `limit`. */
function boundBytes(value: unknown, limit: number): number {
  switch (typeof value) {
    case "string": return 2 + 6 * value.length;
    case "number": case "boolean": case "undefined": return 25;
    case "object": break;
    default: throw NOT_PLAIN;
  }
  if (value === null) return 4;
  let total = 2;
  if (Array.isArray(value)) {
    if (Object.getPrototypeOf(value) !== ARRAY_PROTO || hasOwn.call(value, "toJSON")) throw NOT_PLAIN;
    for (let index = 0; index < value.length && total <= limit; index++) total += 1 + boundBytes(value[index], limit);
    return total;
  }
  const proto = Object.getPrototypeOf(value);
  if (proto !== OBJECT_PROTO && proto !== null) throw NOT_PLAIN;
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record);
  for (let index = 0; index < keys.length && total <= limit; index++) {
    const key = keys[index]!;
    if (key === "toJSON" || key === "__proto__") throw NOT_PLAIN;
    total += 4 + 6 * key.length + boundBytes(record[key], limit);
  }
  return total;
}

export function sha256Hex(text: string | Uint8Array): string {
  return createHash("sha256").update(text).digest("hex");
}

export function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
