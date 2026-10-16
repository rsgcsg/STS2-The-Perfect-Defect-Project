import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { createSession, reduce, modelView, canonicalJson } from '../src/p1Global/index.js';
const basis = (): any => JSON.parse(readFileSync(new URL('./fixtures/p1-global/synthetic-complete-combat.json', import.meta.url), 'utf8'));
const project = (b: any) => reduce(createSession('public-identity', 'managed'), b);
const input = (r: ReturnType<typeof project>) => canonicalJson(modelView(r.observation));
const hud = (b: any) => b.snapshot.interaction.content.context;
const draw = (b: any) => b.reads.find((r: any) => r.kind === 'combat_piles').response.content.zones.find((z: any) => z.zone === 'draw');
const rename = (v: any, oldId: string, nextId: string): any => typeof v === 'string' ? (v === oldId ? nextId : v)
  : Array.isArray(v) ? v.map(item => rename(item, oldId, nextId)) : v && typeof v === 'object'
    ? Object.fromEntries(Object.entries(v).map(([key, item]) => [key, rename(item, oldId, nextId)])) : v;

describe('P1 current public identity and HUD projection', () => {
  it('native numeric counter and opaque token spelling do not change model input', () => {
    const a = basis(); let b = basis(); hud(b).enemies[0].combat_id = 4_000_001;
    b = rename(b, hud(b).enemies[0].entity_id, 'opaque_different_private_counter_919191');
    const left = project(a); const right = project(b);
    expect(left.complete && right.complete).toBe(true); expect(input(left)).toBe(input(right));
    expect((right.observation.combat.content as any).enemies[0]).not.toHaveProperty('combat_id');
    expect(hud(b).enemies[0].combat_id).toBe(4_000_001); // Original source is not rewritten.
  });
  it('does not rewrite a public display string that happens to equal a source ID', () => {
    const b = basis(); hud(b).enemies[0].name = hud(b).enemies[0].entity_id;
    const r = project(b); const enemy = (r.observation.combat.content as any).enemies[0];
    expect(r.complete).toBe(true); expect(enemy.name).toBe(hud(b).enemies[0].name); expect(enemy.name).not.toBe(enemy.entity_id);
  });
  it('preserves every canonical fact while removing only consistent compatibility aliases', () => {
    const a = basis(); const b = basis(); hud(b).enemies[0].health_display = 'Normal';
    const orb = hud(b).player.orbs[0]; orb.passive = orb.passive_value; orb.evoke = orb.evoke_value;
    expect(input(project(a))).toBe(input(project(b)));
    orb.keywords = [{ name: 'Public keyword', description: 'Current displayed explanation' }];
    const r = project(b); expect(r.complete).toBe(true);
    expect((r.observation.combat.content as any).player.orbs[0].keywords).toEqual(orb.keywords);
    expect(input(project(a))).not.toBe(input(r));
  });
  it.each(['health_display', 'passive', 'evoke', 'alive'])('refuses contradictory %s rather than silently omitting it', key => {
    const b = basis();
    if (key === 'health_display') hud(b).enemies[0].health_display = 'InfiniteWithoutNumbers';
    else if (key === 'alive') hud(b).player.companions = [{ ...hud(b).enemies[0], entity_id: 'companion-1', is_alive: true, alive: false }];
    else hud(b).player.orbs[0][key] = 999;
    const r = project(b); expect(r.complete).toBe(false); expect(r.missing.some(reason => reason.includes('conflicting_alias'))).toBe(true);
  });
  it('an earlier private permutation cannot affect a later identical public presentation', () => {
    const a = basis(); const b = basis(); draw(b).cards.reverse();
    const pa = project(a); const pb = project(b); expect(input(pa)).toBe(input(pb));
    const later = basis(); later.snapshot.snapshot_id += '_next';
    for (const read of later.reads) read.response.expected_snapshot_id = read.response.observed_snapshot_id = later.snapshot.snapshot_id;
    const selected = draw(later).cards.shift(); const oldId = hud(later).player.hand[0].entity_id;
    hud(later).player.hand[0] = { ...selected, can_play: true };
    hud(later).player.draw_pile_count = draw(later).card_count = draw(later).cards.length;
    const next = rename(later, oldId, selected.entity_id);
    const left = reduce(pa.state, next); const right = reduce(pb.state, next);
    expect(left.complete && right.complete).toBe(true); expect(input(left)).toBe(input(right));
  });
  it('retains public enemy continuity while anonymous pile slots never enter session identity', () => {
    const b = basis(); const first = project(b); const enemy = (first.observation.combat.content as any).enemies[0];
    const next = basis(); next.snapshot.snapshot_id += '_changed';
    for (const read of next.reads) read.response.expected_snapshot_id = read.response.observed_snapshot_id = next.snapshot.snapshot_id;
    hud(next).enemies[0].hp--;
    const later = reduce(first.state, next);
    expect(later.complete).toBe(true);
    expect((later.observation.combat.content as any).enemies[0].entity_id).toBe(enemy.entity_id);
    expect(Object.values(first.state.aliases).every(alias => !alias.startsWith('a'))).toBe(true);
    expect(input(reduce(first.state, b))).toBe(input(first));
  });

});
