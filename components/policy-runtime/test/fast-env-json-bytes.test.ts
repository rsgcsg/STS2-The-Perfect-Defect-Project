import { describe, expect, it } from "vitest";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { canonicalJson } from "../src/evidence.js";
import { globalDigest } from "../src/global-transport.js";
import { CanonicalTextMemo, CanonicalTextMemoDivergence, canonicalJsonFast, globalDigestFast, pyJsonBytes, pyJsonSha256 } from "../src/fast-env/json-bytes.js";
import { isDeepFrozen } from "@rsgcsg/sts2-connector-client/p1-global";

// Golden bytes and digests from Python `stpd.p1.data.json_bytes(json.loads(JSON.stringify(value)))`:
// the encoder hashes what it parses from the decision line.
const value = { b: 1, B: 2, a_b: 3, aB: 4, ab: 5, Z: [1.5, -0, 0.1, 1e21, 123456789.125, -7, true, false, null],
  "é": "café 中 😀   \u007f \u0001 \"q\" \\ \n\t", "": 1, "😀": 2, "": { y: [], x: {} },
  catalog: [{ choice_id: "c1", kind: "play_card", card: { Name: "Zap", name: "zap", cost: 1 } }] };
const PYTHON = "{\"\":{\"x\":{},\"y\":[]},\"B\":2,\"Z\":[1.5,0,0.1,1e+21,123456789.125,-7,true,false,null],\"aB\":4,\"a_b\":3,\"ab\":5,"
  + "\"b\":1,\"catalog\":[{\"card\":{\"Name\":\"Zap\",\"cost\":1,\"name\":\"zap\"},\"choice_id\":\"c1\",\"kind\":\"play_card\"}],"
  + "\"é\":\"café 中 😀   \u007f \\u0001 \\\"q\\\" \\\\ \\n\\t\",\"\":1,\"😀\":2}";

describe("fast-env json_bytes", () => {
  it("equals Python json_bytes byte for byte, keys by code point", () => {
    expect(pyJsonBytes(value).toString("utf8")).toBe(PYTHON);
    expect(pyJsonSha256(value)).toBe("01a92855d0add2665d1b9715ccdf9a65d3409d2c6f9c1f16d44542f5c5120c4c");
    expect(pyJsonSha256(value.catalog)).toBe("a5f7c48026122d63cc1111ed2e7d1c085289b22b25229575d037048ad3beb53e");
    // The localeCompare canonical form is a different byte string: it is not this definition.
    expect(canonicalJson(value.catalog)).not.toBe(pyJsonBytes(value.catalog).toString("utf8"));
  });

  it("refuses values whose Python spelling would differ instead of hashing them wrong", () => {
    expect(() => pyJsonSha256({ x: 0.00001 })).toThrow("py_json_float_exponent");
    expect(() => pyJsonSha256({ x: -0.000099 })).toThrow("py_json_float_exponent");
    expect(() => pyJsonSha256({ x: Number.NaN })).toThrow("py_json_nonfinite_number");
    expect(() => pyJsonSha256({ x: "\ud800" })).toThrow("py_json_lone_surrogate");
    expect(() => pyJsonSha256({ x: undefined })).toThrow("py_json_non_json_value");
    // Python and JS agree from 1e-4 up, and on 1e+21 and above (both exponent forms).
    expect(pyJsonBytes({ x: [0.0001, 1.5e300] }).toString("utf8")).toBe("{\"x\":[0.0001,1.5e+300]}");
  });
});

// The one-pass writers must spell every value as the definitions they replace. Oracles:
// the pyJson of 3a7c (map/join, regex comparator) and evidence.canonicalJson itself.
const SURROGATE = /[\uD800-\uDFFF]/, LONE = /[\uD800-\uDFFF]/u;
function byCodePoint(left: string, right: string): number {
  if (!SURROGATE.test(left) && !SURROGATE.test(right)) return left < right ? -1 : left > right ? 1 : 0;
  const a = [...left], b = [...right];
  for (let i = 0; i < Math.min(a.length, b.length); i += 1) {
    const d = a[i]!.codePointAt(0)! - b[i]!.codePointAt(0)!;
    if (d !== 0) return d;
  }
  return a.length - b.length;
}
function pyJsonOracle(value: unknown): string {
  if (value === null) return "null";
  switch (typeof value) {
    case "boolean": return value ? "true" : "false";
    case "string": if (LONE.test(value)) throw new TypeError("py_json_lone_surrogate"); return JSON.stringify(value);
    case "number":
      if (!Number.isFinite(value)) throw new TypeError("py_json_nonfinite_number");
      if (value !== 0 && Math.abs(value) < 1e-4) throw new TypeError("py_json_float_exponent");
      return JSON.stringify(value);
    case "object": {
      if (Array.isArray(value)) return `[${value.map(pyJsonOracle).join(",")}]`;
      const keys = Object.keys(value as object).sort(byCodePoint);
      return `{${keys.map(key => `${JSON.stringify(key)}:${pyJsonOracle((value as Record<string, unknown>)[key])}`).join(",")}}`;
    }
    default: throw new TypeError("py_json_non_json_value");
  }
}
const outcome = (write: () => string | undefined): string => {
  try { return `ok:${write()}`; } catch (error) { return `throw:${(error as Error).message}`; }
};

function generator(seed: number) {
  let state = seed >>> 0;
  const next = () => { state = (state * 1664525 + 1013904223) >>> 0; return state / 2 ** 32; };
  const pick = <T,>(items: readonly T[]): T => items[Math.floor(next() * items.length)]!;
  const units = ["a", "Z", "_", "0", " ", "\"", "\\", "/", "\n", "\u0001", "\u001f", "\u007f", "\u0080", "é", "中",
    "😀", "\ud800", "\udfff", " ", "﻿", "A", "b", "B", "aB", "a_b"];
  const text = (asciiOnly: boolean) => {
    let out = "";
    for (let i = Math.floor(next() * 6); i > 0; i -= 1) out += asciiOnly ? pick(["a", "b", "Z", "_", "0", "B"]) : pick(units);
    return out;
  };
  const numbers = [0, -0, 1, -7, 1.5, 0.1, 1e21, 1.5e300, 123456789.125, 0.0001, 1e-7, -0.00009, Number.NaN, Infinity];
  const value = (depth: number, lone: boolean): unknown => {
    const roll = next();
    if (depth > 4 || roll < 0.35) {
      const leaf = next();
      if (leaf < 0.3) return text(false).replace(lone ? /$^/ : /[\ud800-\udfff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g, "x");
      if (leaf < 0.6) return pick(numbers);
      if (leaf < 0.75) return next() < 0.5;
      return null;
    }
    if (roll < 0.6) return Array.from({ length: Math.floor(next() * 5) }, () => value(depth + 1, lone));
    const object: Record<string, unknown> = {};
    for (let i = Math.floor(next() * 6); i > 0; i -= 1) object[(next() < 0.15 ? String(Math.floor(next() * 12)) : text(next() < 0.5))] = value(depth + 1, lone);
    return object;
  };
  return value;
}

const fixtureDir = join(import.meta.dirname, "../../connector/sdk/typescript/test/fixtures/p1-global");
const fixtures = readdirSync(fixtureDir).filter(name => name.endsWith(".json"))
  .map(name => JSON.parse(readFileSync(join(fixtureDir, name), "utf8")) as unknown);

describe("fast-env one-pass writers", () => {
  it("pyJson spells every value, and refuses every value, as the map/join writer did", () => {
    expect(fixtures.length).toBeGreaterThan(5);
    for (const fixture of fixtures) expect(pyJsonSha256(fixture)).toBe(
      createHashHex(pyJsonOracle(fixture)));
    const value = generator(20261111);
    for (let i = 0; i < 4000; i += 1) {
      const sample = value(0, i % 4 === 0);
      expect(outcome(() => pyJsonBytes(sample).toString("utf8"))).toBe(outcome(() => pyJsonOracle(sample)));
    }
    expect(() => pyJsonSha256([1, undefined])).toThrow("py_json_non_json_value");
  });

  it("canonicalJsonFast is the bytes of canonicalJson; globalDigestFast is globalDigest", () => {
    for (const fixture of fixtures) {
      expect(canonicalJsonFast(fixture)).toBe(canonicalJson(fixture));
      expect(globalDigestFast(fixture)).toBe(globalDigest(fixture));
    }
    const value = generator(1110);
    for (let i = 0; i < 4000; i += 1) {
      const sample = value(0, true);
      expect(outcome(() => canonicalJsonFast(sample))).toBe(outcome(() => canonicalJson(sample)));
    }
    // Values JSON never carries still take canonicalJson's spelling.
    const odd = { u: undefined, f: () => 1, s: Symbol("s"), a: [undefined, () => 1, Symbol("t"), , 2], d: new Date(0),
      n: [Number.NaN, -Infinity, -0], nested: { toJSON: "not a function" } };
    expect(canonicalJsonFast(odd)).toBe(canonicalJson(odd));
    expect(canonicalJsonFast(undefined)).toBe(canonicalJson(undefined));
    expect(() => canonicalJsonFast({ toJSON: () => 1 })).toThrow("canonical_json_to_json_function");
  });
});

function createHashHex(text: string): string {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

/** Equal subtrees become one deep-frozen object (what the E5 Host does for unchanged subtrees). */
function intern(value: unknown, table: Map<string, object>): unknown {
  if (value === null || typeof value !== "object") return value;
  const key = `${Array.isArray(value) ? "a" : "o"}${canonicalJson(value)}`;
  const kept = table.get(key);
  if (kept) return kept;
  const copy: any = Array.isArray(value) ? value.map(item => intern(item, table))
    : Object.fromEntries(Object.entries(value).map(([name, item]) => [name, intern(item, table)]));
  table.set(key, Object.freeze(copy));
  return copy;
}

describe("fast-env E4 canonical text memo", () => {
  it("digests exactly globalDigest of the decoded copy, reusing frozen subtrees, and compares every digest at N = 1", () => {
    const table = new Map<string, object>();
    const memo = new CanonicalTextMemo({ checkInterval: 1, checkSeed: 7, isDeepFrozen });
    const value = generator(4404);
    for (let round = 0; round < 3; round += 1) {
      for (const fixture of fixtures) {
        const raw = { top: "receipt", successor: intern(fixture, table) };
        const decoded = JSON.parse(JSON.stringify(raw));
        expect(memo.digest(raw, decoded)).toBe(globalDigest(decoded));
      }
      for (let i = 0; i < 300; i += 1) {
        const sample = JSON.parse(JSON.stringify({ s: value(0, false) }));
        const raw = intern(sample, table);
        expect(memo.digest(raw, sample)).toBe(globalDigest(sample));
      }
    }
    const status = memo.status();
    expect(status).toMatchObject({ check_interval: 1, digests: status.verified });
    expect(status.hits).toBeGreaterThan(0);
    expect(status.misses).toBeGreaterThan(0);
  });

  it("keeps nothing for objects that are not deeply frozen", () => {
    const memo = new CanonicalTextMemo({ checkInterval: 4, isDeepFrozen });
    const shallow = Object.freeze({ child: { n: 1 } });
    const open = { a: [1, 2], b: { c: "x" } };
    for (let i = 0; i < 5; i += 1) {
      expect(memo.digest(open, open)).toBe(globalDigest(open));
      expect(memo.digest(shallow, shallow)).toBe(globalDigest(shallow));
      (shallow.child as { n: number }).n += 1;
    }
    expect(memo.status()).toMatchObject({ hits: 0, misses: 0 });
  });

  it("compares digest 0 and then after random gaps in ceil(N/2)..N", () => {
    for (const [interval, seed] of [[64, 1], [64, 99], [7, 3], [2, 5]] as const) {
      const memo = new CanonicalTextMemo({ checkInterval: interval, checkSeed: seed, isDeepFrozen });
      const digests = 20_000;
      for (let i = 0; i < digests; i += 1) memo.digest(i, i);
      const { verified } = memo.status();
      expect(verified).toBeGreaterThanOrEqual(Math.floor(digests / interval));
      expect(verified).toBeLessThanOrEqual(Math.ceil(digests / Math.ceil(interval / 2)) + 1);
    }
  });

  it("throws CanonicalTextMemoDivergence when the kept text and the full walk differ, never a fallback", () => {
    const memo = new CanonicalTextMemo({ checkInterval: 64, checkSeed: 11, isDeepFrozen });
    expect(() => memo.digest({ a: 1 }, { a: 2 })).toThrow(CanonicalTextMemoDivergence);
    const poisoned = new CanonicalTextMemo({ checkInterval: 8, checkSeed: 11, isDeepFrozen });
    const shared = Object.freeze({ deck: Object.freeze(["Zap", "Dualcast"]) });
    poisoned.texts.set(shared, "{\"deck\":[\"Zap\"]}");
    let thrown: unknown = null;
    for (let i = 0; i < 9 && thrown === null; i += 1) {
      try { poisoned.digest({ step: i, shared }, { step: i, shared: { deck: ["Zap", "Dualcast"] } }); } catch (error) { thrown = error; }
    }
    expect(thrown).toBeInstanceOf(CanonicalTextMemoDivergence);
    expect((thrown as CanonicalTextMemoDivergence).offset).toBeGreaterThan(0);
    expect(() => new CanonicalTextMemo({ checkInterval: 0, isDeepFrozen })).toThrow(/checkInterval/u);
  });

  it("verifyAll (after a fatal divergence) compares every later digest", () => {
    const memo = new CanonicalTextMemo({ checkInterval: 1024, checkSeed: 5, isDeepFrozen });
    memo.digest({ a: 1 }, { a: 1 });
    expect(() => memo.digest({ a: 1 }, { a: 2 })).not.toThrow();
    memo.verifyAll();
    expect(() => memo.digest({ a: 1 }, { a: 2 })).toThrow(CanonicalTextMemoDivergence);
    memo.digest({ b: 1 }, { b: 1 }); memo.digest({ b: 2 }, { b: 2 });
    expect(memo.status()).toMatchObject({ digests: 5, verified: 3 });
  });
});
