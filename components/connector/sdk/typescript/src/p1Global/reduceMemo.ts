import { canonicalJson } from "./canonical.js";

/**
 * P-REWRITE fast_chain only (design v3 E6): the reducer may reuse its own earlier
 * results for a frozen subtree the Host hands over again as the same object. The
 * switch is off by default; off runs the unchanged reducer walks.
 *
 * - "on": memoized walks; every `verifyEvery`-th reduction (default 64) is also
 *   recomputed without the memo and the two Reductions must be byte-identical.
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
  /** Full comparison cadence for "on"; default 64. */
  readonly verifyEvery?: number;
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
  mode = options.mode; verifyEvery = every; reductions = 0; verified = 0;
  memoCounters.hits = 0; memoCounters.misses = 0;
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
  const verify = mode === "verify_all" || reduction % verifyEvery === 0;
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
