import { deepFreeze } from "./managed-public-basis.mjs";

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
 * Safety: every `checkInterval` frames (frame 0 included) the session projects
 * the same state again with the memo off on a copy of the alias tables and
 * compares the public bytes; any difference throws ProjectionMemoDivergence,
 * the session is tainted and the step fails. checkInterval 1 checks every frame.
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
    if (key !== "checkInterval") throw new TypeError(`projectionMemo.${key} is not supported.`);
  const checkInterval = value.checkInterval ?? DEFAULT_MEMO_CHECK_INTERVAL;
  if (!Number.isSafeInteger(checkInterval) || checkInterval < 1 || checkInterval > 1024)
    throw new TypeError("projectionMemo.checkInterval must be an integer in 1..1024.");
  // Identity reuse needs inputs that are shared frozen objects; without them the
  // memo can never hit and would only add cost.
  if (compactOutput == null || compactOutput.shareUnchanged !== true)
    throw new TypeError("projectionMemo requires compactOutput with shareUnchanged: true.");
  return Object.freeze({ checkInterval });
}

export class ProjectionMemo {
  #table = new WeakMap();
  #interval;
  #frames = 0;
  hits = 0;
  misses = 0;
  checks = 0;

  constructor({ checkInterval = DEFAULT_MEMO_CHECK_INTERVAL } = {}) {
    this.#interval = checkInterval;
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
    const due = this.#frames % this.#interval === 0;
    this.#frames += 1;
    if (due) this.checks += 1;
    return due;
  }

  /** New run: entity aliases restart, so no cached subtree may survive. */
  reset() {
    this.#table = new WeakMap();
    this.#frames = 0;
  }

  stats() {
    return { hits: this.hits, misses: this.misses, checks: this.checks, check_interval: this.#interval };
  }
}
