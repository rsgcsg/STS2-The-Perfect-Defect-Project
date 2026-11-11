import { canonicalJson } from "./canonical.js";

/**
 * P-REWRITE fast_chain only (design v3 E6): the reducer may reuse its own earlier
 * results for a frozen subtree the Host hands over again as the same object. The
 * switch is off by default; off runs the unchanged reducer walks.
 *
 * - "on": memoized walks; some reductions are also recomputed without the memo and
 *   the two Reductions must be byte-identical. The gap between two compared
 *   reductions is drawn uniformly from ceil(N/2)..N (N = `verifyEvery`, default 64),
 *   so at most N reductions run uncompared and no call pattern with a fixed period
 *   (main reduce plus a follow check, interleaved sessions) can keep one call site
 *   out of the comparison.
 * - "verify_all": the corpus mode; every reduction is compared that way.
 *
 * A difference throws P1ReduceMemoDivergence. Nothing falls back silently and the
 * result is never recomputed and used instead.
 */
export type ReduceMemoMode = "off" | "on" | "verify_all";

export interface ReduceMemoOptions {
  readonly mode: ReduceMemoMode;
  /** Must be true for any mode other than "off": only a fast_chain caller enables this. */
  readonly fastChain?: boolean;
  /** Largest gap between two compared reductions for "on"; default 64. */
  readonly verifyEvery?: number;
  /** Seed of the gap draws (reproducing a run); default a fresh random seed. */
  readonly verifySeed?: number;
}

export interface ReduceMemoStatus {
  readonly mode: ReduceMemoMode;
  readonly verifyEvery: number;
  readonly reductions: number;
  readonly verified: number;
  readonly hits: number;
  readonly misses: number;
}

export class P1ReduceMemoDivergence extends Error {
  readonly code = "p1_reduce_memo_divergence";
  constructor(readonly reduction: number, readonly offset: number) {
    super(`p1_reduce_memo_divergence: reduction ${reduction} differs from the full walk at byte ${offset}`);
    this.name = "P1ReduceMemoDivergence";
  }
}

let mode: ReduceMemoMode = "off";
let verifyEvery = 64;
let reductions = 0;
let verified = 0;
let nextVerify = 0;
let rng = 1;
let active = false;
let verifying = false;
/** @internal hit/miss counters of the memoized walks (benchmark and test evidence). */
export const memoCounters = { hits: 0, misses: 0 };

export function configureReduceMemo(options: ReduceMemoOptions): void {
  if (options === null || typeof options !== "object" || !["off", "on", "verify_all"].includes(options.mode))
    throw new TypeError("reduce memo mode must be off, on or verify_all");
  if (options.mode !== "off" && options.fastChain !== true) throw new Error("p1_reduce_memo_requires_fast_chain");
  const every = options.verifyEvery ?? 64;
  if (!Number.isSafeInteger(every) || every < 1 || every > 65536) throw new TypeError("verifyEvery must be an integer in 1..65536");
  const seed = options.verifySeed ?? Math.floor(Math.random() * 0x100000000);
  if (!Number.isSafeInteger(seed)) throw new TypeError("verifySeed must be a safe integer");
  mode = options.mode; verifyEvery = every; reductions = 0; verified = 0;
  rng = (seed >>> 0) || 0x9e3779b9; nextVerify = gap();
  memoCounters.hits = 0; memoCounters.misses = 0;
}

/** Next gap, uniform in ceil(N/2)..N (xorshift32; only which reductions get compared depends on it). */
function gap(): number {
  rng ^= rng << 13; rng >>>= 0; rng ^= rng >>> 17; rng ^= rng << 5; rng >>>= 0;
  const low = Math.ceil(verifyEvery / 2);
  return low + rng % (verifyEvery - low + 1);
}

export function reduceMemoStatus(): ReduceMemoStatus {
  return { mode, verifyEvery, reductions, verified, hits: memoCounters.hits, misses: memoCounters.misses };
}

/** @internal True only while a memoized reduction runs. */
export function memoActive(): boolean {
  return active;
}

/** @internal True while the memoized run of a verified reduction runs. */
export function memoVerifying(): boolean {
  return active && verifying;
}

/** @internal Deeply frozen inputs only: a frozen object can still hold a mutable child. */
const DEEP_FROZEN = new WeakMap<object, boolean>();
export function deepFrozen(value: object): boolean {
  if (!Object.isFrozen(value)) return false;
  let known = DEEP_FROZEN.get(value);
  if (known === undefined) {
    known = true;
    for (const key of Object.keys(value)) {
      const child = (value as Record<string, unknown>)[key];
      if (child !== null && typeof child === "object" && !deepFrozen(child)) { known = false; break; }
    }
    DEEP_FROZEN.set(value, known);
  }
  return known;
}

/** @internal Pure schema answers for one deeply frozen object, kept while memoized. */
export function memoCheck(memo: WeakMap<object, boolean>, value: unknown, check: (value: unknown) => boolean): boolean {
  if (!active || value === null || typeof value !== "object" || !deepFrozen(value)) return check(value);
  let known = memo.get(value);
  if (known === undefined) { memoCounters.misses++; memo.set(value, known = check(value)); } else memoCounters.hits++;
  return known;
}

/** @internal Runs one reduction under the configured mode. A nested call joins the outer one. */
export function withReduceMemo<T>(run: () => T): T {
  if (mode === "off" || active) return run();
  const reduction = ++reductions;
  const verify = mode === "verify_all" || reduction === nextVerify;
  if (reduction === nextVerify) nextVerify += gap();
  active = true; verifying = verify;
  let fast: T;
  try { fast = run(); } finally { active = false; verifying = false; }
  if (!verify) return fast;
  const full = run();
  const left = canonicalJson(fast); const right = canonicalJson(full);
  if (left !== right) {
    let offset = 0;
    while (offset < left.length && left.charCodeAt(offset) === right.charCodeAt(offset)) offset++;
    throw new P1ReduceMemoDivergence(reduction, offset);
  }
  verified++;
  return fast;
}
