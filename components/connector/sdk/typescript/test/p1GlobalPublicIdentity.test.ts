import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { createSession, reduce, modelView, canonicalJson } from '../src/p1Global/index.js';
const basis = (): any => JSON.parse(readFileSync(new URL('./fixtures/p1-global/synthetic-complete-combat.json', import.meta.url), 'utf8'));
const project = (b: any) => reduce(createSession('public-identity', 'managed'), b);
const input = (r: ReturnType<typeof project>) => canonicalJson(modelView(r.observation));
const hud = (b: any) => b.snapshot.interaction.content.context;
const draw = (b: any) => b.reads.find((r: any) => r.kind === 'combat_piles').response.content.zones.find((z: any) => z.zone === 'draw');
const deck = (b: any) => b.reads.find((r: any) => r.kind === 'run_deck').response.content;
const discard = (b: any) => b.reads.find((r: any) => r.kind === 'combat_piles').response.content.zones.find((z: any) => z.zone === 'discard');
const rename = (v: any, oldId: string, nextId: string): any => typeof v === 'string' ? (v === oldId ? nextId : v)
  : Array.isArray(v) ? v.map(item => rename(item, oldId, nextId)) : v && typeof v === 'object'
    ? Object.fromEntries(Object.entries(v).map(([key, item]) => [key, rename(item, oldId, nextId)])) : v;

describe('P1 current public identity and HUD projection', () => {
  it('refuses complete P1 admission until enriched card display/parent identities have a declared projection', () => {
    const b = basis(), card = hud(b).player.hand[0], id = 'new-public-detail';
    const listed = { ...b.snapshot.reads[0], read_id: id, kind: 'surface_card', target_referent_id: card.entity_id,
      content_schema: 'sts2.player-environment/read/surface_card-1' };
    b.snapshot.reads.push(listed);
    b.reads.push({ read_id: id, kind: 'surface_card', error: null, response: { ...b.reads[0].response,
      read_id: id, kind: listed.kind, target_referent_id: card.entity_id, content_schema: listed.content_schema,
      content: { ...card, public_details_revision: '1', display_context: 'hand', parent_read_id: null,
        display_owner_entity_id: hud(b).player.player_entity_id, keywords: [], card_previews: [] } } });
    const before = JSON.stringify(b), r = project(b);
    const renamed = project(rename(b, hud(b).player.player_entity_id, 'different-private-owner-spelling'));
    expect(input(r) === input(renamed) || !r.complete && !renamed.complete).toBe(true);
    expect(r.complete).toBe(false); expect(r.missing).toContain('required:public_card_display_identity_projection');
    expect(JSON.stringify(b)).toBe(before);
  });
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

  it('keeps unknown identity-looking public extensions literal and out of the witness inventory', () => {
    const a = basis();
    hud(a).extension = { entity_id: draw(a).cards[0].entity_id, group_id: 'literal-public-id-text' };
    hud(a)['enemies[0].entity_id'] = 'literal-key-with-path-punctuation';
    const b = structuredClone(a); draw(b).cards.reverse();
    const left = project(a); const right = project(b);
    expect(left.complete && right.complete).toBe(true); expect(input(left)).toBe(input(right));
    expect((left.observation.combat.content as any).extension).toEqual(hud(a).extension);
    expect((left.observation.combat.content as any)['enemies[0].entity_id']).toBe('literal-key-with-path-punctuation');
    expect(Object.hasOwn(left.state.aliases, hud(a).extension.entity_id)).toBe(false);
  });

  it('allocates fixed public selector identities before an unrelated unordered Draw traversal', () => {
    // Synthetic current native pile-selector DTO + real decoder/reducer. This
    // preserves its fixed public display order while changing only a separate Read.
    const a = basis(); const cards = structuredClone(draw(a).cards.slice(0, 2)); const combat = hud(a);
    Object.assign(a.snapshot.interaction, { kind: 'native_combat_pile_selection', stage: 'selecting',
      content_schema: 'sts2.player-environment/surface/native_combat_pile_selection-1',
      content: { context: { kind: 'selection', combat }, surface: { kind: 'native_combat_pile_selection',
        stage: 'selecting', pile_type: 'draw', cards, min_select: 1, max_select: 1, selected_count: 0,
        selected_card_entity_ids: [], selectable_card_entity_ids: cards.map((c: any) => c.entity_id),
        deselectable_card_entity_ids: [], can_confirm: false, cancelable: false } } });
    a.snapshot.bound_actions.actions = cards.map((c: any, i: number) => ({ ...a.snapshot.bound_actions.actions[0],
      bound_action_id: `action_selector_${i}`, verb: 'select', subject_referent_id: c.entity_id, arguments: [], label: 'Select Defend' }));
    a.snapshot.bound_actions.total_count = a.snapshot.bound_actions.materialized_count = cards.length;
    a.snapshot.referents.push(...cards.map((c: any) => ({ referent_id: c.entity_id, role: 'card', kind: 'entity',
      label: c.name, state: { visible: true, observation_basis: 'native_visible_fact' },
      properties_schema: 'sts2.player-environment/referent/card-1', properties: c })));
    const b = structuredClone(a); draw(b).cards.reverse();
    const left = project(a); const right = project(b);
    expect(left.complete && right.complete).toBe(true); expect(input(left)).toBe(input(right));
    const displayed = (left.observation.scene.content as any).surface.cards;
    expect(left.observation.catalog.map(c => c.subject).sort()).toEqual(displayed.map((c: any) => c.entity_id).sort());
    expect(displayed[0].entity_id).not.toBe(displayed[1].entity_id);
  });

  it('does not join anonymous deck and Draw slots using a secret native pointer', () => {
    const a = basis(); const copies = deck(a).cards.filter((c: any) => c.definition_id === 'DEFEND_DEFECT');
    const drawCopies = draw(a).cards.filter((c: any) => c.definition_id === 'DEFEND_DEFECT');
    draw(a).cards = draw(a).cards.filter((c: any) => c !== drawCopies[1]);
    draw(a).cards[0].entity_id = copies[0].entity_id;
    draw(a).card_count = hud(a).player.draw_pile_count = draw(a).cards.length;
    const b = structuredClone(a); deck(b).cards.reverse();
    const left = project(a); const right = project(b);
    expect(left.complete && right.complete).toBe(true); expect(input(left)).toBe(input(right));
    const deckAliases = (left.observation.deck.content as any).cards.map((c: any) => c.entity_id);
    const drawAlias = (left.observation.piles.content as any).zones[0].cards[0].entity_id;
    expect(deckAliases).not.toContain(drawAlias); // No claimed identity across anonymous displays.
    expect(Object.hasOwn(left.state.aliases, copies[0].entity_id)).toBe(false);
    expect(input(reduce(left.state, a))).toBe(input(left));
  });

  it('treats an ordered visible discard Read as a public identity witness', () => {
    const a = basis(); const shown = deck(a).cards.find((c: any) => c.definition_id === 'DEFEND_DEFECT');
    discard(a).cards = [structuredClone(shown)]; discard(a).card_count = hud(a).player.discard_pile_count = 1;
    const b = structuredClone(a); deck(b).cards.reverse();
    const left = project(a); const right = project(b);
    expect(left.complete && right.complete).toBe(true); expect(input(left)).toBe(input(right));
    const visible = (left.observation.piles.content as any).zones[1].cards[0].entity_id;
    expect(left.state.aliases[shown.entity_id]).toBe(visible);
    expect((left.observation.deck.content as any).cards.some((c: any) => c.entity_id === visible)).toBe(true);
    const later = reduce(left.state, b);
    expect((later.observation.piles.content as any).zones[1].cards[0].entity_id).toBe(visible);
  });

  it('aliases owned recursive card previews while preserving an extension with the same property name', () => {
    const b = basis(); const card = hud(b).player.hand[0];
    b.snapshot.persistent.content.player.relics[0].card_previews = [{ ...structuredClone(card), entity_id: 'preview-parent',
      card_previews: [{ ...structuredClone(card), entity_id: card.entity_id }],
      extension: { entity_id: 'literal-in-card', card_previews: [{ entity_id: 'literal-nested-extension' }] } }];
    const r = project(b); expect(r.complete).toBe(true);
    const preview = (r.observation.player.content as any).relics[0].card_previews[0];
    expect(preview.entity_id).not.toBe('preview-parent');
    expect(preview.card_previews[0].entity_id).toBe((r.observation.combat.content as any).player.hand[0].entity_id);
    expect(preview.extension).toEqual(b.snapshot.persistent.content.player.relics[0].card_previews[0].extension);
  });

  it('keeps typed current card-detail identities and their previews in the same public relation', () => {
    const b = basis(); const card = hud(b).player.hand[0];
    const listed = { ...b.snapshot.reads[0], read_id: 'read_owned_detail', kind: 'surface_card',
      target_referent_id: card.entity_id, content_schema: 'sts2.player-environment/read/surface_card-1' };
    const captured = structuredClone(b.reads[0]); captured.read_id = listed.read_id; captured.kind = listed.kind;
    Object.assign(captured.response, { read_id: listed.read_id, kind: listed.kind, target_referent_id: listed.target_referent_id,
      content_schema: listed.content_schema, content: { ...structuredClone(card), card_previews: [structuredClone(card)],
      extension: { entity_id: 'literal-detail-extension' } } });
    b.snapshot.reads.push(listed); b.reads.push(captured);
    const r = project(b); expect(r.complete).toBe(true);
    const detail = r.observation.extra_reads[0]!.content as any;
    const identity = (r.observation.combat.content as any).player.hand[0].entity_id;
    expect(detail.entity_id).toBe(identity); expect(detail.card_previews[0].entity_id).toBe(identity);
    expect(detail.extension.entity_id).toBe('literal-detail-extension');
  });

});
