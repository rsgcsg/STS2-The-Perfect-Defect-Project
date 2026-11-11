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

function addRead(basis: any, kind: string, content: unknown, target: string | null = null): void {
  const id = `added-${kind}`; const schema = `sts2.player-environment/read/${kind}-1`;
  basis.snapshot.reads.push({ ...basis.snapshot.reads[0], read_id: id, kind, content_schema: schema, target_referent_id: target });
  basis.reads.push({ read_id: id, kind, error: null, response: { ...basis.reads[0].response, read_id: id, kind,
    content_schema: schema, target_referent_id: target, content } });
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
    configureReduceMemo({ mode: "on", fastChain: true, verifyEvery: 1 });
    let state = reduce(createSession("e", "managed"), frames[0]!).state;
    description = "second";
    // verifyEvery 1 compares every reduction: the stale reused relic differs from the full walk.
    expect(() => reduce(state, frames[1]!)).toThrow(P1ReduceMemoDivergence);
    configureReduceMemo({ mode: "on", fastChain: true, verifyEvery: 64, verifySeed: 7 });
    state = reduce(createSession("e", "managed"), frames[0]!).state;
    description = "third";
    // Uncompared reductions trust the memo; a compared one comes within 64 and throws.
    let thrown: unknown; let at = 1;
    while (thrown === undefined && at < 64) {
      at++;
      try { state = reduce(state, frames[1 + at % 2]!).state; } catch (error) { thrown = error; }
      if (thrown === undefined) expect(reduceMemoStatus().verified).toBe(0);
    }
    expect(thrown).toBeInstanceOf(P1ReduceMemoDivergence);
    expect(at).toBeGreaterThanOrEqual(32);
  });

  it("compares every call site of a fixed-period call pattern", () => {
    // Two sessions take turns (period 2). Session b reads a value that changes on every
    // read, so any compared reduction of b throws; a fixed reduction % 8 would never
    // compare b, which only ever runs on odd reductions.
    for (const seed of [1, 2, 3]) {
      let reads = 0;
      const broken = load("synthetic-complete-combat") as any;
      Object.defineProperty(broken.snapshot.persistent.content.player.relics[0], "description",
        { enumerable: true, get: () => `read ${reads++}` });
      const a = episode("synthetic-complete-combat", 2); const b = [deepFreeze(broken as P1Basis)];
      b.push(nextFrame(b[0]!, 1, "same"));
      configureReduceMemo({ mode: "on", fastChain: true, verifyEvery: 8, verifySeed: seed });
      let left = createSession("a", "managed"); let right = createSession("b", "managed");
      let thrown: unknown;
      for (let step = 0; step < 40 && thrown === undefined; step++) {
        // b runs on the odd reductions, a on the even ones.
        try { right = reduce(right, b[step % 2]!).state; } catch (error) { thrown = error; }
        left = reduce(left, a[step % 2]!).state;
      }
      expect(thrown).toBeInstanceOf(P1ReduceMemoDivergence);
    }
  });

  it("keys entries by path: one frozen card under declared and raw card paths", () => {
    // The same frozen card stands at combat hand[0] (its card_previews[].entity_id is a
    // declared card identity), at scene playable_cards[0] (raw), in a surface_card Read
    // (declared) and in an unknown Read (raw). The scene card makes the surface
    // incomplete; the bytes must still equal the full walk.
    const base = load("synthetic-complete-combat") as any;
    const content = base.snapshot.interaction.content; const hand = content.context.player.hand;
    const card = { ...hand[0], card_previews: [{ ...hand[1], entity_id: "card_preview_native" }] };
    hand[0] = card; content.surface.playable_cards[0] = card;
    addRead(base, "surface_card", card, card.entity_id); addRead(base, "unknown_extension", card);
    const frames = [deepFreeze(base as P1Basis)];
    for (let step = 1; step < 4; step++) frames.push(nextFrame(frames[step - 1]!, step, "same"));
    const off = run([frames], ["managed"]);
    const observation = JSON.parse(off[0]!).observation;
    expect(observation.combat.content.player.hand[0].card_previews[0].entity_id).not.toBe("card_preview_native");
    expect(observation.scene.content.surface.playable_cards[0].card_previews[0].entity_id).toBe("card_preview_native");
    expect(observation.extra_reads.map((read: any) => read.content.card_previews[0].entity_id === "card_preview_native"))
      .toEqual([false, true]);
    configureReduceMemo({ mode: "verify_all", fastChain: true });
    expect(run([frames], ["managed"])).toEqual(off);
  });

  it("keys scene entries by literal path: one frozen card under a card tooltip and another tooltip", () => {
    const base = load("synthetic-complete-combat") as any; const interaction = base.snapshot.interaction;
    interaction.kind = "event_option"; interaction.content_schema = "sts2.player-environment/surface/event_option-1";
    interaction.content.surface.kind = "event_option";
    const card = interaction.content.context.player.hand[0];
    interaction.content.surface.options = [{ entity_id: "opt_a", tooltips: [{ kind: "card", card }] },
      { entity_id: "opt_b", tooltips: [{ kind: "hover", card }] }];
    const frames = [deepFreeze(base as P1Basis)];
    for (let step = 1; step < 4; step++) frames.push(nextFrame(frames[step - 1]!, step, "same"));
    const off = run([frames], ["managed"]);
    const options = JSON.parse(off[0]!).observation.scene.content.surface.options;
    expect(options[0].tooltips[0].card.entity_id).not.toBe(card.entity_id);
    expect(options[1].tooltips[0].card.entity_id).toBe(card.entity_id);
    configureReduceMemo({ mode: "verify_all", fastChain: true });
    expect(run([frames], ["managed"])).toEqual(off);
  });

  it("keys entries by policy: one frozen surface list under combat_turn and event_option", () => {
    // playable_cards[].entity_id is declared only by the combat_turn policy; the next frame
    // keeps the same frozen list at the same literal path under the event_option policy.
    // The list's ids are its own (not hand ids), so only this list declares them.
    const base = load("synthetic-complete-combat") as any;
    base.snapshot.interaction.content.surface.playable_cards = base.snapshot.interaction.content.surface.playable_cards
      .map((card: any, index: number) => ({ ...card, entity_id: `playable_only_${index}` }));
    const first = deepFreeze(base);
    const interaction = first.snapshot.interaction;
    const next = { ...interaction, kind: "event_option", content_schema: "sts2.player-environment/surface/event_option-1",
      content: { ...interaction.content, surface: { ...interaction.content.surface, kind: "event_option" } } };
    const second = deepFreeze({ snapshot: { ...first.snapshot, snapshot_id: `${first.snapshot.snapshot_id}_event`,
      sequence: first.snapshot.sequence + 1, interaction: next }, reads: first.reads.map((read: any) => ({ ...read,
      response: read.response && { ...read.response, expected_snapshot_id: `${first.snapshot.snapshot_id}_event`,
        observed_snapshot_id: `${first.snapshot.snapshot_id}_event` } })) }) as P1Basis;
    expect((second.snapshot as any).interaction.content.surface.playable_cards).toBe(interaction.content.surface.playable_cards);
    const off = run([[first, second]], ["managed"]);
    const ids = off.map(text => JSON.parse(text).observation.scene.content.surface.playable_cards[0].entity_id);
    expect(ids[0]).not.toBe(interaction.content.surface.playable_cards[0].entity_id);
    expect(ids[1]).toBe(interaction.content.surface.playable_cards[0].entity_id);
    // A fresh session for the second frame: a stale declared-id collection would allocate
    // aliases for the list's ids there.
    const fresh = () => [canonicalJson(reduce(createSession("a", "managed"), first)),
      canonicalJson(reduce(createSession("b", "managed"), second))];
    const freshOff = fresh();
    configureReduceMemo({ mode: "verify_all", fastChain: true });
    expect(run([[first, second]], ["managed"])).toEqual(off);
    expect(fresh()).toEqual(freshOff);
  });

  it("rejects a frozen invalid snapshot again on every reduction", () => {
    const base = load("synthetic-complete-combat") as any; base.snapshot.schema = "not-a-player-snapshot";
    const invalid = deepFreeze(base as P1Basis);
    const message = (() => { try { reduce(createSession("e", "managed"), invalid); } catch (error) { return String(error); } return "none"; })();
    expect(message).not.toBe("none");
    for (const mode of ["on", "verify_all"] as const) {
      configureReduceMemo({ mode, fastChain: true });
      const state = reduce(createSession("e", "managed"), episode("synthetic-complete-combat", 1)[0]!).state;
      for (let round = 0; round < 3; round++) expect(() => reduce(state, invalid)).toThrow(message.replace(/^\w*Error: /u, ""));
    }
  });

  it("projects one frozen enemy at two positions as the copied projection does", () => {
    const base = load("synthetic-complete-combat") as any; const context = base.snapshot.interaction.content.context;
    context.enemies[0].health_display = "conflicting"; context.enemies.push(context.enemies[0]);
    const frames = [deepFreeze(base as P1Basis)];
    frames.push(nextFrame(frames[0]!, 1, "same"));
    const off = run([frames], ["managed"]);
    expect(JSON.parse(off[0]!).missing.filter((item: string) => item.includes("health_display"))).toHaveLength(1);
    configureReduceMemo({ mode: "verify_all", fastChain: true });
    expect(run([frames], ["managed"])).toEqual(off);
  });
});
