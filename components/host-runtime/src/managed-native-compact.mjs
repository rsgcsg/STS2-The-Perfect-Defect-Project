import { createHash } from "node:crypto";

/**
 * Host consumer of managed-native-compact-1 (contracts/managed-native/README.md).
 *
 * Opt-in for the P-REWRITE fast_chain only: the Host sends configure_output and
 * then rebuilds every full native state from compact frames. It never decides
 * what the state means; it only proves the frames are a faithful encoding of
 * today's full writer and fails closed otherwise. Downstream code reads only the
 * reconstructed state, never delta membership, digests or facts.
 */

export const COMPACT_FORMAT = "managed-native-compact-1";
export const DEFAULT_RECONCILE_INTERVAL = 64;
export const DEFAULT_SAMPLE_RATE = 0.01;

const REF = /^native-[1-9][0-9]*$/u;
const KEY = /^native-[1-9][0-9]*#[a-z][a-z0-9_]{0,31}$/u;
const PILE_VIEW = /#[a-z0-9_]*(pile|draw|discard|exhaust)[a-z0-9_]*$/u;
const SHA = /^[0-9a-f]{64}$/u;
const INSTANCE = /^[A-Za-z0-9._:-]{8,96}$/u;
const PILES = new Set(["draw_pile", "discard_pile", "exhaust_pile"]);

/** compact_state_divergence: no action on this state, the game is closed, originals
 * are kept and this build's output stops. Never resync-and-continue. */
export class CompactStateDivergence extends Error {
  constructor(reason, detail = null) {
    super(`compact_state_divergence:${reason}${detail == null ? "" : `:${detail}`}`);
    this.name = "CompactStateDivergence";
    this.code = "compact_state_divergence";
    this.reason = reason;
  }
}

function diverge(reason, detail) { throw new CompactStateDivergence(reason, detail); }

const plain = (value) => value != null && typeof value === "object" && !Array.isArray(value);
const isPlaceholder = (value) => plain(value) && typeof value.$obj === "string" && Object.keys(value).length === 1;
const sha256 = (text) => createHash("sha256").update(text, "utf8").digest("hex");
const nonNegative = (value) => Number.isSafeInteger(value) && value >= 0;

export function objectsRoot(digests) {
  const hash = createHash("sha256");
  for (const key of [...digests.keys()].sort(byteOrder)) hash.update(`${key}\t${digests.get(key)}\n`, "utf8");
  return hash.digest("hex");
}

function byteOrder(left, right) {
  // Keys are ASCII by the key pattern, so UTF-16 order is byte order.
  return left < right ? -1 : left > right ? 1 : 0;
}

/** Table placeholders reachable directly from a value (not through other bodies). */
function placeholders(value, found = []) {
  if (Array.isArray(value)) {
    for (const item of value) placeholders(item, found);
  } else if (plain(value)) {
    if (isPlaceholder(value)) found.push(value.$obj);
    else for (const key in value) placeholders(value[key], found);
  }
  return found;
}

/** Fair piles carry no instance: no table placeholder inside any pile container. */
function checkPiles(value, inPile = false) {
  if (Array.isArray(value)) {
    for (const item of value) checkPiles(item, inPile);
  } else if (plain(value)) {
    if (inPile && isPlaceholder(value)) diverge("pile_placeholder");
    const pile = inPile || Object.hasOwn(value, "ordering_semantics");
    for (const key in value) checkPiles(value[key], pile || PILES.has(key));
  }
}

// --- raw-byte slicing (sampled path) --------------------------------------------

function skipWhitespace(text, index) {
  let i = index;
  for (;;) {
    const code = text.charCodeAt(i);
    if (code === 0x20 || code === 0x09 || code === 0x0a || code === 0x0d) i += 1;
    else return i;
  }
}

function skipString(text, index) {
  // index points at the opening quote.
  let i = index + 1;
  for (;;) {
    const next = text.indexOf("\"", i);
    if (next < 0) diverge("schema_invalid", "unterminated_string");
    let slashes = 0;
    for (let j = next - 1; j >= index + 1 && text.charCodeAt(j) === 0x5c; j -= 1) slashes += 1;
    if (slashes % 2 === 0) return next + 1;
    i = next + 1;
  }
}

function skipValue(text, index) {
  const i = skipWhitespace(text, index);
  const code = text.charCodeAt(i);
  if (code === 0x22) return skipString(text, i);
  if (code === 0x7b || code === 0x5b) {
    let depth = 0;
    let j = i;
    while (j < text.length) {
      const c = text.charCodeAt(j);
      if (c === 0x22) { j = skipString(text, j); continue; }
      if (c === 0x7b || c === 0x5b) depth += 1;
      else if (c === 0x7d || c === 0x5d) {
        depth -= 1;
        if (depth === 0) return j + 1;
      }
      j += 1;
    }
    diverge("schema_invalid", "unterminated_container");
  }
  let j = i;
  while (j < text.length) {
    const c = text.charCodeAt(j);
    if (c === 0x2c || c === 0x7d || c === 0x5d || c === 0x20 || c === 0x09 || c === 0x0a || c === 0x0d) break;
    j += 1;
  }
  if (j === i) diverge("schema_invalid", "empty_value");
  return j;
}

/** [key, valueStart, valueEnd] of the members of the JSON object starting at text[index]. */
function members(text, index) {
  const out = [];
  let i = skipWhitespace(text, index);
  if (text.charCodeAt(i) !== 0x7b) diverge("schema_invalid", "object_expected");
  i = skipWhitespace(text, i + 1);
  if (text.charCodeAt(i) === 0x7d) return out;
  for (;;) {
    if (text.charCodeAt(i) !== 0x22) diverge("schema_invalid", "key_expected");
    const keyEnd = skipString(text, i);
    const key = JSON.parse(text.slice(i, keyEnd));
    i = skipWhitespace(text, keyEnd);
    if (text.charCodeAt(i) !== 0x3a) diverge("schema_invalid", "colon_expected");
    const start = skipWhitespace(text, i + 1);
    const end = skipValue(text, start);
    out.push([key, start, end]);
    i = skipWhitespace(text, end);
    const code = text.charCodeAt(i);
    if (code === 0x2c) { i = skipWhitespace(text, i + 1); continue; }
    if (code === 0x7d) return out;
    diverge("schema_invalid", "separator_expected");
  }
}

/** The exact UTF-8 bytes (as a string slice) of every table body in one emitted line. */
export function compactBodySlices(line) {
  const objects = members(line, 0).find(([key]) => key === "objects");
  if (objects == null) diverge("schema_invalid", "objects_missing");
  return new Map(members(line, objects[1]).map(([key, start, end]) => [key, line.slice(start, end)]));
}

// --- structural schema (sampled path) --------------------------------------------

const FRAME_BASE = ["type", "schema", "frame", "episode_instance", "seq", "skeleton", "objects", "object_digests",
  "objects_root_sha256", "facts", "diagnostics"];
const FRAME_KIND = {
  full: new Set([...FRAME_BASE, "base_seq", "start"]),
  delta: new Set([...FRAME_BASE, "base_seq", "removed"]),
  reconcile: new Set([...FRAME_BASE, "base_seq", "upserted", "removed"])
};
const COMBAT_END = {
  combat_index: [1], act: [1], floor: [0], hp_start: [0], hp_end: [0], max_hp_start: [1], max_hp_end: [1],
  potions_used: [0], turns: [0]
};

function objectKey(value) { return typeof value === "string" && KEY.test(value) && !PILE_VIEW.test(value); }

function uniqueKeys(value) {
  return Array.isArray(value) && value.every(objectKey) && new Set(value).size === value.length;
}

/** Hand-written equivalent of managed-native-compact-1.schema.json for frames (the
 * Host carries no JSON-Schema engine). Returns a reason or null. */
export function compactFrameSchemaError(frame) {
  if (!plain(frame)) return "frame";
  if (frame.type !== "compact_state" || frame.schema !== COMPACT_FORMAT) return "type_or_schema";
  const allowed = FRAME_KIND[frame.frame];
  if (allowed == null) return "frame_kind";
  for (const key of Object.keys(frame)) if (!allowed.has(key)) return `property:${key}`;
  for (const key of ["episode_instance", "seq", "skeleton", "objects", "object_digests", "objects_root_sha256", "base_seq"])
    if (!Object.hasOwn(frame, key)) return `required:${key}`;
  if (typeof frame.episode_instance !== "string" || !INSTANCE.test(frame.episode_instance)) return "episode_instance";
  if (!nonNegative(frame.seq)) return "seq";
  if (!plain(frame.skeleton) || frame.skeleton.type !== "decision"
      || typeof frame.skeleton.decision !== "string" || frame.skeleton.decision.length === 0) return "skeleton";
  if (!plain(frame.objects)) return "objects";
  for (const [key, body] of Object.entries(frame.objects)) {
    if (!objectKey(key)) return `object_key:${key}`;
    if (!plain(body) || typeof body.native_ref !== "string" || !REF.test(body.native_ref)) return `object_body:${key}`;
  }
  if (!plain(frame.object_digests)) return "object_digests";
  for (const [key, digest] of Object.entries(frame.object_digests))
    if (!objectKey(key) || typeof digest !== "string" || !SHA.test(digest)) return `object_digest:${key}`;
  if (typeof frame.objects_root_sha256 !== "string" || !SHA.test(frame.objects_root_sha256)) return "objects_root_sha256";
  if (Object.hasOwn(frame, "facts")) {
    const facts = frame.facts;
    if (!plain(facts) || Object.keys(facts).some(key => key !== "combat_end")) return "facts";
    if (Object.hasOwn(facts, "combat_end")) {
      if (!Array.isArray(facts.combat_end) || facts.combat_end.length > 4) return "facts.combat_end";
      for (const fact of facts.combat_end) {
        const keys = [...Object.keys(COMBAT_END), "room_kind", "encounter_id", "result"];
        if (!plain(fact) || Object.keys(fact).length !== keys.length || keys.some(key => !Object.hasOwn(fact, key)))
          return "combat_end_fields";
        for (const [key, [minimum]] of Object.entries(COMBAT_END))
          if (!Number.isSafeInteger(fact[key]) || fact[key] < minimum) return `combat_end.${key}`;
        if (!["monster", "elite", "boss", "event"].includes(fact.room_kind)) return "combat_end.room_kind";
        if (typeof fact.encounter_id !== "string" || fact.encounter_id.length === 0) return "combat_end.encounter_id";
        if (!["victory", "defeat"].includes(fact.result)) return "combat_end.result";
      }
    }
  }
  if (Object.hasOwn(frame, "diagnostics")) {
    const diagnostics = frame.diagnostics;
    const names = ["text_cache_hits", "text_cache_misses", "write_microseconds"];
    if (!plain(diagnostics) || Object.entries(diagnostics).some(([key, value]) => !names.includes(key) || !nonNegative(value)))
      return "diagnostics";
  }
  if (frame.frame === "full") {
    if (frame.base_seq !== null) return "base_seq";
    const start = frame.start;
    if (!plain(start)) return "start";
    if (start.kind === "new_run") { if (Object.keys(start).length !== 1) return "start"; }
    else if (start.kind === "from_save") {
      if (Object.keys(start).length !== 2 || typeof start.save_sha256 !== "string" || !SHA.test(start.save_sha256)) return "start";
    } else return "start";
  } else {
    if (!nonNegative(frame.base_seq)) return "base_seq";
    if (!uniqueKeys(frame.removed)) return "removed";
    if (frame.frame === "reconcile" && !uniqueKeys(frame.upserted)) return "upserted";
  }
  return null;
}

// --- reconstruction ----------------------------------------------------------

/**
 * One episode instance's carried table. accept() takes a parsed frame and, on the
 * sampled path, the exact emitted line; it returns the reconstructed full state or
 * throws CompactStateDivergence. After a divergence the reconstructor stays failed.
 */
export class CompactStateReconstructor {
  #interval;
  #table = new Map();       // key -> body (parsed, never mutated)
  #digests = new Map();     // key -> carried digest
  #children = new Map();    // key -> placeholder keys directly inside the body
  #held = null;
  #anchor = 0;
  #instance = null;
  #start = null;
  #failed = null;
  #frames = 0;

  constructor({ reconcileInterval = DEFAULT_RECONCILE_INTERVAL } = {}) {
    if (!Number.isSafeInteger(reconcileInterval) || reconcileInterval < 1 || reconcileInterval > 1024)
      throw new TypeError("reconcileInterval must be an integer in 1..1024.");
    this.#interval = reconcileInterval;
  }

  get episodeInstance() { return this.#instance; }
  get start() { return this.#start; }
  get failed() { return this.#failed; }
  get frames() { return this.#frames; }
  get tableSize() { return this.#table.size; }

  accept(frame, { line = null, sampled = false } = {}) {
    if (this.#failed != null) throw this.#failed;
    try {
      return this.#accept(frame, line, sampled);
    } catch (error) {
      this.#failed = error instanceof CompactStateDivergence ? error : new CompactStateDivergence("consumer_error", error?.message);
      throw this.#failed;
    }
  }

  #accept(frame, line, sampled) {
    if (!plain(frame) || frame.type !== "compact_state") diverge("not_compact_frame");
    if (sampled) {
      const schema = compactFrameSchemaError(frame);
      if (schema != null) diverge("schema_invalid", schema);
      if (typeof line !== "string") diverge("raw_line_unavailable");
      for (const [key, raw] of compactBodySlices(line))
        if (frame.object_digests[key] !== sha256(raw)) diverge("object_digest_mismatch", key);
    }
    if (!plain(frame.objects) || !plain(frame.object_digests) || !plain(frame.skeleton)) diverge("schema_invalid", "frame_members");
    const kind = frame.frame;
    if (kind === "full") {
      this.#table = new Map(); this.#digests = new Map(); this.#children = new Map();
      this.#instance = frame.episode_instance; this.#anchor = frame.seq; this.#start = frame.start ?? null;
    } else if (kind !== "delta" && kind !== "reconcile") {
      diverge("schema_invalid", "frame_kind");
    } else if (this.#held == null || frame.episode_instance !== this.#instance || frame.base_seq !== this.#held) {
      diverge("base_mismatch");
    } else if (frame.seq !== this.#held + 1) {
      diverge("seq_gap");
    } else if (kind === "delta" && frame.seq - this.#anchor >= this.#interval) {
      diverge("reconcile_overdue");
    }
    const upserts = Object.entries(frame.objects);
    for (const [key, body] of upserts)
      if (!plain(body) || typeof key !== "string" || body.native_ref !== key.split("#")[0]) diverge("object_key_ref_mismatch", key);
    const digestKeys = Object.keys(frame.object_digests);
    if (digestKeys.length !== upserts.length || digestKeys.some(key => !Object.hasOwn(frame.objects, key)))
      diverge("digest_set_mismatch");

    if (kind === "delta") {
      if (!Array.isArray(frame.removed)) diverge("schema_invalid", "removed");
      // The Host forgets a removed key; an object that comes back must be upserted again.
      for (const key of frame.removed) { this.#table.delete(key); this.#digests.delete(key); this.#children.delete(key); }
      for (const [key, body] of upserts) this.#put(key, body, frame.object_digests[key]);
    } else if (kind === "reconcile") {
      if (!Array.isArray(frame.removed) || !Array.isArray(frame.upserted)) diverge("schema_invalid", "reconcile_lists");
      const removed = new Set(frame.removed), upserted = new Set(frame.upserted);
      for (const [key, digest] of this.#digests) {
        if (removed.has(key)) continue;
        if (!Object.hasOwn(frame.object_digests, key)) diverge("reconcile_mismatch", `missing:${key}`);
        if (!upserted.has(key) && frame.object_digests[key] !== digest) diverge("reconcile_mismatch", `digest:${key}`);
      }
      for (const key of digestKeys)
        if (!upserted.has(key) && (removed.has(key) || !this.#digests.has(key))) diverge("reconcile_mismatch", `new:${key}`);
      this.#table = new Map(); this.#digests = new Map(); this.#children = new Map();
      for (const [key, body] of upserts) this.#put(key, body, frame.object_digests[key]);
      this.#anchor = frame.seq;
    } else {
      for (const [key, body] of upserts) this.#put(key, body, frame.object_digests[key]);
    }

    checkPiles(frame.skeleton);
    // Stored bodies are immutable; each was pile-checked when it was upserted.
    for (const [, body] of upserts) checkPiles(body);
    this.#reachableOrDiverge(frame.skeleton);
    if (objectsRoot(this.#digests) !== frame.objects_root_sha256) diverge("root_mismatch");
    this.#held = frame.seq;
    this.#frames += 1;
    return this.#inline(frame.skeleton);
  }

  #put(key, body, digest) {
    this.#table.set(key, body);
    this.#digests.set(key, digest);
    this.#children.set(key, placeholders(body));
  }

  #reachableOrDiverge(skeleton) {
    // white = absent, 1 = on the current path, 2 = done.
    const color = new Map();
    const visit = (key) => {
      const state = color.get(key);
      if (state === 2) return;
      if (state === 1) diverge("placeholder_cycle", key);
      if (!this.#table.has(key)) diverge("unresolved_placeholder", key);
      color.set(key, 1);
      for (const child of this.#children.get(key)) visit(child);
      color.set(key, 2);
    };
    for (const key of placeholders(skeleton)) visit(key);
    if (color.size !== this.#table.size) {
      const orphan = [...this.#table.keys()].find(key => !color.has(key));
      diverge("orphan_object", orphan);
    }
  }

  #inline(value) {
    if (Array.isArray(value)) return value.map(item => this.#inline(item));
    if (!plain(value)) return value;
    if (isPlaceholder(value)) return this.#inline(this.#table.get(value.$obj));
    const out = {};
    for (const key in value) out[key] = this.#inline(value[key]);
    return out;
  }
}

/** Pre-drawn sampled-check schedule for one episode instance: decided when the
 * instance starts (before any of its content is seen), never from game content. */
export function compactSampleSchedule({ rate = DEFAULT_SAMPLE_RATE, all = false, random = Math.random } = {}) {
  if (!(typeof rate === "number" && rate >= 0 && rate <= 1)) throw new TypeError("sample rate must be in [0, 1].");
  if (all || rate >= 1) return () => true;
  if (rate === 0) return (seq) => seq === 0;
  const offset = Math.floor(random() * 0x1_0000_0000) >>> 0;
  const threshold = Math.round(rate * 0x1_0000_0000);
  // Full frames (seq 0 of the instance) are always checked; others by a fixed hash of seq.
  return (seq) => seq === 0 || ((Math.imul(seq ^ offset, 0x9e3779b1) >>> 0) < threshold);
}
