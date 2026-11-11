import { readdirSync, readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import { canonicalJson, configureReduceMemo, createSession, P1ReduceMemoDivergence, reduce, reduceMemoStatus, resolveFollowBasis,
  type P1Basis, type P1SessionState, type Reduction } from "../src/p1Global/index.js";

// E6 (P-REWRITE v3): the reducer may reuse results for a frozen subtree the Host hands over
// again as the same object. Off by default; on must give the same bytes as off.
const dir = new URL("./fixtures/p1-global/", import.meta.url);
const names = readdirSync(dir).filter(name => /^(managed-|reference-|synthetic-)/u.test(name) && !name.includes("71cf"))
  .map(name => name.replace(/\.json$/u, ""));
const load = (name: string): P1Basis => JSON.parse(readFileSync(new URL(`${name}.json`, dir), "utf8")) as P1Basis;
const deepFreeze = <T>(value: T): T => {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const key of Object.keys(value)) deepFreeze((value as Record<string, unknown>)[key]);
  }
  return value;
};
type Mutation = "same" | "combat" | "deck_grows" | "fresh";
const MUTATIONS: Mutation[] = ["same", "combat", "deck_grows", "same", "combat", "fresh", "same"];

/** Adjacent frames as E5 delivers them: a new frozen envelope; unchanged parts are the same frozen objects. */
function nextFrame(previous: P1Basis, step: number, mutation: Mutation): P1Basis {
  const old = previous.snapshot as Record<string, any>;
  const id = `${String(old.snapshot_id).replace(/_m\d+$/u, "")}_m${step}`;
  let interaction = old.interaction;
  const context = interaction.content.context;
  if (mutation === "combat" && context?.kind === "combat" && context.player) {
    const hand = Array.isArray(context.player.hand) && context.player.hand.length > 1 ? context.player.hand.slice(1) : context.player.hand;
    const enemies = Array.isArray(context.enemies) ? context.enemies.map((enemy: any, index: number) =>
      index === 0 ? { ...enemy, block: (Number(enemy.block) || 0) + 1 } : enemy) : context.enemies;
    interaction = { ...interaction, content: { ...interaction.content,
      context: { ...context, enemies, player: { ...context.player, hand, energy: step % 4 } } } };
  }
  const persistent = old.persistent?.content?.player ? { ...old.persistent, content: { ...old.persistent.content,
    player: { ...old.persistent.content.player, gold: step } } } : old.persistent;
  const snapshot = { ...old, snapshot_id: id, sequence: step + 2, persistent, interaction };
  const reads = previous.reads.map(read => {
    const response = read.response as Record<string, any> | null;
    if (!response) return read;
    let content = response.content;
    if (mutation === "deck_grows" && content?.kind === "run_deck" && Array.isArray(content.cards) && content.cards.length > 0) {
      const added = { ...content.cards[0], entity_id: `${content.cards[0].entity_id}_copy${step}` };
      content = { ...content, cards: [added, ...content.cards], card_count: content.cards.length + 1 };
    }
    return { ...read, response: { ...response, expected_snapshot_id: id, observed_snapshot_id: id, content } };
  });
  const basis = { snapshot, reads } as P1Basis;
  return deepFreeze(mutation === "fresh" ? JSON.parse(JSON.stringify(basis)) as P1Basis : basis);
}

function episode(name: string, frames: number): P1Basis[] {
  const out = [deepFreeze(load(name))];
  for (let step = 1; step < frames; step++) out.push(nextFrame(out[step - 1]!, step, MUTATIONS[step % MUTATIONS.length]!));
  return out;
}

/** Runs the frames through one session per episode, interleaving episodes so their alias tables differ. */
function run(episodes: P1Basis[][], hostKinds: ("managed" | "reference")[]): string[] {
  const states: P1SessionState[] = episodes.map((_, index) => createSession(`e${index}`, hostKinds[index]!));
  const out: string[] = [];
  const longest = Math.max(...episodes.map(frames => frames.length));
  for (let step = 0; step < longest; step++) episodes.forEach((frames, index) => {
    const frame = frames[step]; if (!frame) return;
    const reduction: Reduction = reduce(states[index]!, frame);
    states[index] = reduction.state;
    out.push(canonicalJson(reduction));
  });
  return out;
}

afterEach(() => configureReduceMemo({ mode: "off" }));

describe("E6 reduce memo (fast_chain only)", () => {
  it("is off by default and only a fast_chain caller can turn it on", () => {
    expect(reduceMemoStatus().mode).toBe("off");
    expect(() => configureReduceMemo({ mode: "on" })).toThrow(/requires_fast_chain/u);
    expect(() => configureReduceMemo({ mode: "verify_all", fastChain: false })).toThrow(/requires_fast_chain/u);
    expect(() => configureReduceMemo({ mode: "on", fastChain: true, verifyEvery: 0 })).toThrow(TypeError);
    expect(() => configureReduceMemo({ mode: "sometimes" as never, fastChain: true })).toThrow(TypeError);
    reduce(createSession("e", "managed"), deepFreeze(load("managed-combat_turn")));
    expect(reduceMemoStatus()).toMatchObject({ mode: "off", reductions: 0, hits: 0 });
  });

  it("corpus mode: every reduction on adjacent frames equals the unmemoized walk, byte for byte", () => {
    const corpus = names.map(name => episode(name, 15));
    const kinds = names.map(name => name.startsWith("reference") ? "reference" as const : "managed" as const);
    const expected = run(corpus, kinds);
    configureReduceMemo({ mode: "verify_all", fastChain: true });
    const actual = run(corpus, kinds);
    const status = reduceMemoStatus();
    expect(actual).toEqual(expected);
    expect(status.verified).toBe(status.reductions);
    expect(status.reductions).toBe(expected.length);
    expect(status.hits).toBeGreaterThan(status.misses);
  });

  it("reuses entries across sessions only when every alias still matches", () => {
    // Session b sees other identities first, so its e-numbers differ from session a's.
    const shared = episode("synthetic-complete-combat", 8);
    const other = episode("managed-map_navigation", 2);
    const sequence = (memo: boolean) => {
      configureReduceMemo(memo ? { mode: "verify_all", fastChain: true } : { mode: "off" });
      let a = createSession("a", "managed"); let b = createSession("b", "managed");
      for (const frame of other) b = reduce(b, frame).state;
      const out: string[] = [];
      for (const frame of shared) {
        const left = reduce(a, frame); a = left.state; out.push(canonicalJson(left));
        const right = reduce(b, frame); b = right.state; out.push(canonicalJson(right));
      }
      return out;
    };
    const off = sequence(false);
    expect(sequence(true)).toEqual(off);
    expect(off[0]).not.toBe(off[1]);
  });

  it("keeps the follow-check reduction of a successor basis identical", () => {
    const frames = episode("managed-deck_upgrade_selection", 3);
    const reductions = (memo: boolean) => {
      configureReduceMemo(memo ? { mode: "verify_all", fastChain: true } : { mode: "off" });
      const first = reduce(createSession("e", "managed"), frames[0]!);
      const follows = Object.values(first.lowering).flatMap(lowering => lowering.follow.map(rule =>
        canonicalJson(resolveFollowBasis(rule, lowering, frames[1]!))));
      return [canonicalJson(first), ...follows, canonicalJson(reduce(first.state, frames[1]!))];
    };
    expect(reductions(true)).toEqual(reductions(false));
  });

  it("returns frozen reused parts; the reducer's own fresh top-level containers stay writable", () => {
    configureReduceMemo({ mode: "on", fastChain: true });
    const frames = episode("synthetic-complete-combat", 3);
    let state = createSession("e", "managed");
    let last: Reduction | undefined;
    for (const frame of frames) { last = reduce(state, frame); state = last.state; }
    const player = last!.observation.player.content as Record<string, any>;
    expect(Object.isFrozen(player)).toBe(false);
    expect(Object.isFrozen(player.relics)).toBe(true);
    expect(() => { (player.relics as unknown[]).push("x"); }).toThrow(TypeError);
  });

  it("fails loudly when a reused entry no longer matches the full walk", () => {
    // A frozen object whose accessor changes breaks the immutability the memo relies on.
    let description = "first";
    const base = load("synthetic-complete-combat") as any;
    const relic = base.snapshot.persistent.content.player.relics[0];
    expect(relic).toBeDefined();
    Object.defineProperty(relic, "description", { enumerable: true, get: () => description });
    const frames = [deepFreeze(base as P1Basis)];
    frames.push(nextFrame(frames[0]!, 1, "same"), nextFrame(frames[0]!, 2, "same"));
    configureReduceMemo({ mode: "on", fastChain: true, verifyEvery: 2 });
    let state = reduce(createSession("e", "managed"), frames[0]!).state;
    description = "second";
    // Reduction 2 is a verified one: the stale reused relic differs from the full walk.
    expect(() => reduce(state, frames[1]!)).toThrow(P1ReduceMemoDivergence);
    configureReduceMemo({ mode: "on", fastChain: true, verifyEvery: 64 });
    state = reduce(createSession("e", "managed"), frames[0]!).state;
    description = "third";
    // Unverified reductions trust the memo; the 64th compares and throws.
    for (let index = 2; index < 64; index++) state = reduce(state, frames[1 + index % 2]!).state;
    expect(reduceMemoStatus().verified).toBe(0);
    expect(() => reduce(state, frames[1]!)).toThrow(/p1_reduce_memo_divergence/u);
  });
});
