import { deepFreeze } from "./managed-deep-freeze.mjs";

/**
 * E5 projection memo (fast_chain only, off by default).
 *
 * Object identity rule (consumers such as the kernel's canonical-text cache and
 * the SDK reducer memo build on it):
 *
 * - KEPT: a projected subtree is the SAME deep-frozen object as on an earlier
 *   step when it was built by a memoized builder from the same frozen native
 *   input object with the same scalar key. Inputs are frozen only when the
 *   compact reconstructor shares them (compactOutput.shareUnchanged), which
 *   keeps a native object exactly when its carried digest and all its children
 *   are unchanged. So "same input object" means "same native bytes", and the
 *   builder output is a pure function of it plus the key.
 * - REPLACED: every object that carries a snapshot-scoped id (snapshot,
 *   interaction, action, Read envelope, Read descriptor, any content holding a
 *   parent_read_id), every container array/object built per step, and every
 *   subtree whose native input changed or was not frozen. A new object is built
 *   when the native object changes, when the compact instance changes (full
 *   frame or reconcile: the reconstructor shares nothing across them), on a new
 *   mount/restore (aliases and memo are reset together), and on every step
 *   whose input was parsed from a full native line.
 * - Everything the capture hands out is deep-frozen (new objects are frozen at
 *   capture; memoized ones already are). Nobody may mutate a projected object.
 *
 * Safety: frame 0 and then one frame per gap, the gap drawn uniformly from
 * ceil(N/2)..N (N = checkInterval, default 64; same scheme as the SDK reduce
 * memo), are projected again with the memo off on a copy of the alias tables and
 * compared byte for byte. A random gap keeps a bug that only shows on a fixed
 * frame phase from hiding. Any difference throws ProjectionMemoDivergence: the
 * session is tainted with its own reason `projection_memo_divergence`, the step
 * fails with that reason code and the fast chain ends the game as unknown, never
 * retries it, counts it separately and stops build output (like a compact state
 * divergence). checkInterval 1 checks every frame. `checkSeed` reproduces the
 * gap draws; by default it is random.
 */
export const DEFAULT_MEMO_CHECK_INTERVAL = 64;

export class ProjectionMemoDivergence extends Error {
  constructor(detail) {
    super(`projection_memo_divergence: ${detail}`);
    this.name = "ProjectionMemoDivergence";
    this.detail = detail;
  }
}

export function validateProjectionMemo(value, compactOutput) {
  if (value == null) return null;
  if (typeof value !== "object" || Array.isArray(value)) throw new TypeError("projectionMemo must be an object or null.");
  for (const key of Object.keys(value))
    if (key !== "checkInterval" && key !== "checkSeed") throw new TypeError(`projectionMemo.${key} is not supported.`);
  const checkInterval = value.checkInterval ?? DEFAULT_MEMO_CHECK_INTERVAL;
  if (!Number.isSafeInteger(checkInterval) || checkInterval < 1 || checkInterval > 1024)
    throw new TypeError("projectionMemo.checkInterval must be an integer in 1..1024.");
  const checkSeed = value.checkSeed ?? null;
  if (checkSeed !== null && !Number.isSafeInteger(checkSeed)) throw new TypeError("projectionMemo.checkSeed must be a safe integer.");
  // Identity reuse needs inputs that are shared frozen objects; without them the
  // memo can never hit and would only add cost.
  if (compactOutput == null || compactOutput.shareUnchanged !== true)
    throw new TypeError("projectionMemo requires compactOutput with shareUnchanged: true.");
  return Object.freeze({ checkInterval, checkSeed });
}

export class ProjectionMemo {
  #table = new WeakMap();
  #interval;
  #rng;
  #frames = 0;
  #nextCheck = 0;
  hits = 0;
  misses = 0;
  checks = 0;

  constructor({ checkInterval = DEFAULT_MEMO_CHECK_INTERVAL, checkSeed = null } = {}) {
    this.#interval = checkInterval;
    const seed = checkSeed ?? Math.floor(Math.random() * 0x100000000);
    this.#rng = (seed >>> 0) || 0x9e3779b9;
  }

  /** Next gap, uniform in ceil(N/2)..N (xorshift32; it only picks which frames get compared). */
  #gap() {
    let x = this.#rng;
    x ^= x << 13; x >>>= 0; x ^= x >>> 17; x ^= x << 5; x >>>= 0;
    this.#rng = x;
    const low = Math.ceil(this.#interval / 2);
    return low + x % (this.#interval - low + 1);
  }

  get checkInterval() { return this.#interval; }

  /** The cached deep-frozen result of build() for a frozen input object and a scalar key;
   * build() runs (uncached) when the input is not a frozen object. */
  value(site, input, key, build) {
    if (input === null || typeof input !== "object" || !Object.isFrozen(input)) return build();
    let bySite = this.#table.get(input);
    if (bySite === undefined) this.#table.set(input, bySite = new Map());
    const slot = `${site}\u0000${key}`;
    const hit = bySite.get(slot);
    if (hit !== undefined) { this.hits += 1; return hit; }
    const value = deepFreeze(build());
    bySite.set(slot, value);
    this.misses += 1;
    return value;
  }

  /** A frozen native value is already immutable public data: share it instead of cloning. */
  share(value) {
    return value !== null && typeof value === "object" && Object.isFrozen(value) ? value : structuredClone(value);
  }

  /** True on the frames whose memoized projection is compared against a fresh one. */
  checkDue() {
    const due = this.#frames === this.#nextCheck;
    if (due) { this.checks += 1; this.#nextCheck += this.#gap(); }
    this.#frames += 1;
    return due;
  }

  /** New run: entity aliases restart, so no cached subtree may survive. */
  reset() {
    this.#table = new WeakMap();
    this.#frames = 0;
    this.#nextCheck = 0;
  }

  stats() {
    return { hits: this.hits, misses: this.misses, checks: this.checks, check_interval: this.#interval };
  }
}
