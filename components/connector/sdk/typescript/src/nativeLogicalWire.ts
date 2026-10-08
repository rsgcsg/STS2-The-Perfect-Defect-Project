import { createHash } from "node:crypto";
import { isJsonObject, type JsonObject } from "./json.js";

export function assertNativeLogicalScalar(value: string): void {
  for (let index = 0; index < value.length; index++) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(++index);
      if (!(next >= 0xdc00 && next <= 0xdfff)) throw new Error("native logical string has an unpaired surrogate");
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      throw new Error("native logical string has an unpaired surrogate");
    }
  }
}

export function assertNativeLogicalJson(value: unknown, depth = 0): void {
  if (depth > 64) throw new Error("native logical JSON exceeds the producer's JSON depth limit");
  if (typeof value === "string") { assertNativeLogicalScalar(value); return; }
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "number" && Number.isFinite(value)) return;
  if (Array.isArray(value)) {
    for (const child of value) assertNativeLogicalJson(child, depth + 1);
    return;
  }
  if (isJsonObject(value)) {
    for (const [key, child] of Object.entries(value)) {
      assertNativeLogicalScalar(key);
      assertNativeLogicalJson(child, depth + 1);
    }
    return;
  }
  throw new Error("native logical value is not JSON");
}

/** JSON.parse would erase duplicate fields. Scan decoded member names first,
 * including escaped-equivalent keys, then use the platform JSON parser. */
export function parseNativeLogicalJson(text: string): JsonObject {
  let position = 0;
  const whitespace = () => { while (/\s/u.test(text[position] ?? "") && position < text.length) position++; };
  const quoted = (): string => {
    const begin = position++;
    while (position < text.length) {
      const character = text[position++];
      if (character === "\\") position++;
      else if (character === '"') {
        const result: unknown = JSON.parse(text.slice(begin, position));
        if (typeof result !== "string") throw new Error("native logical JSON requires a string key");
        assertNativeLogicalScalar(result);
        return result;
      }
    }
    throw new Error("native logical JSON has an unterminated string");
  };
  const value = (depth: number): void => {
    if (depth > 64) throw new Error("native logical JSON exceeds the producer's JSON depth limit");
    whitespace();
    const character = text[position];
    if (character === '"') { quoted(); return; }
    if (character === "{" || character === "[") {
      position++;
      const end = character === "{" ? "}" : "]";
      const keys = new Set<string>();
      whitespace();
      if (text[position] === end) { position++; return; }
      for (;;) {
        whitespace();
        if (character === "{") {
          if (text[position] !== '"') throw new Error("native logical JSON requires string fields");
          const key = quoted();
          if (keys.has(key)) throw new Error("native logical JSON contains a duplicate field");
          keys.add(key);
          whitespace();
          if (text[position++] !== ":") throw new Error("native logical JSON field is missing a colon");
        }
        value(depth + 1);
        whitespace();
        const delimiter = text[position++];
        if (delimiter === end) return;
        if (delimiter !== ",") throw new Error("native logical JSON has an invalid delimiter");
      }
    }
    const begin = position;
    while (position < text.length && !/[\s,\]}]/u.test(text[position]!)) position++;
    if (position === begin) throw new Error("native logical JSON has an invalid value");
    JSON.parse(text.slice(begin, position));
  };
  value(0);
  whitespace();
  if (position !== text.length) throw new Error("native logical JSON has trailing data");
  const parsed: unknown = JSON.parse(text);
  if (!isJsonObject(parsed)) throw new Error("native logical response must be a JSON object");
  assertNativeLogicalJson(parsed);
  return parsed;
}

export function freezeNativeLogical<T>(value: T): T {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) freezeNativeLogical(child);
    Object.freeze(value);
  }
  return value;
}

export interface NativeLogicalDigestAction {
  readonly action_id: string;
  readonly kind: "native_input";
  readonly verb: string;
  readonly label: string;
  readonly subject_referent_id: string | null;
  readonly arguments: readonly { readonly role: string; readonly referent_id: string }[];
  readonly effect_domain: string;
}

/** Incremental canonical digest; no JSON serialization, normalization or action sorting. */
export function digestNativeLogicalActions(actions: readonly NativeLogicalDigestAction[]): string {
  const hash = createHash("sha256");
  hash.update("sts2.native-logical.catalog.v1\0", "utf8");
  const count = (number: number) => {
    if (!Number.isInteger(number) || number < 0 || number > 0xffffffff)
      throw new Error("native logical digest count is outside U32");
    const bytes = Buffer.allocUnsafe(4);
    bytes.writeUInt32BE(number);
    hash.update(bytes);
  };
  const text = (string: string) => {
    assertNativeLogicalScalar(string);
    const bytes = Buffer.from(string, "utf8");
    count(bytes.length);
    hash.update(bytes);
  };
  const ids = new Set<string>();
  count(actions.length);
  for (const action of actions) {
    const expected = ["action_id", "kind", "verb", "label", "subject_referent_id", "arguments", "effect_domain"];
    if (!isJsonObject(action) || Object.keys(action).length !== expected.length ||
        expected.some(field => !Object.hasOwn(action, field)) || action.kind !== "native_input" ||
        !Array.isArray(action.arguments) || typeof action.action_id !== "string" || typeof action.verb !== "string" ||
        typeof action.label !== "string" || typeof action.effect_domain !== "string" ||
        action.subject_referent_id !== null && typeof action.subject_referent_id !== "string")
      throw new Error("native logical digest action does not match its strict public fields");
    if (ids.has(action.action_id)) throw new Error("native logical catalog has duplicate action IDs");
    ids.add(action.action_id);
    text(action.action_id); text(action.kind); text(action.verb); text(action.label);
    hash.update(Buffer.from([action.subject_referent_id === null ? 0 : 1]));
    if (action.subject_referent_id !== null) text(action.subject_referent_id);
    count(action.arguments.length);
    const roles = new Set<string>();
    for (const argument of action.arguments) {
      if (!isJsonObject(argument) || Object.keys(argument).length !== 2 ||
          typeof argument.role !== "string" || typeof argument.referent_id !== "string")
        throw new Error("native logical digest argument does not match its strict public fields");
      if (roles.has(argument.role)) throw new Error("native logical action has duplicate argument roles");
      roles.add(argument.role);
      text(argument.role); text(argument.referent_id);
    }
    text(action.effect_domain);
  }
  return hash.digest("hex");
}
