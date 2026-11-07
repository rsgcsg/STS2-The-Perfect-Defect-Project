import { createHash } from "node:crypto";

/** Deterministic JSON: object keys sorted, arrays kept in their given order. */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortKeys(value));
}

/* sortKeys returns `value` itself when a copy would serialize to the same bytes:
 * a plain-prototype object whose own keys are already in sorted order, with no
 * undefined value, no own toJSON and no own __proto__ key (a copy drops it), and
 * whose children are unchanged (arrays likewise). JSON.stringify then emits
 * exactly what the sorted copy would. */
function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) {
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
    if (reuse) return record;
    const out: Record<string, unknown> = {};
    for (let index = 0; index < sortedKeys.length; index++)
      if (children[index] !== undefined) out[sortedKeys[index]!] = children[index];
    return out;
  }
  return value;
}

export function sha256Hex(text: string | Uint8Array): string {
  return createHash("sha256").update(text).digest("hex");
}

export function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
