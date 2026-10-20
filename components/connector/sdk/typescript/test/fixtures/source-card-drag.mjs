import { readFileSync } from 'node:fs';

// Codec-complete synthetic shape of the actual native05 untargeted drag:
// original full Play -> same card still visible/reading in backend hand ->
// consumed TryPlayCard call. No native execution or Human origin is claimed.
export function sourceCardDragPair() {
  const before = JSON.parse(readFileSync(new URL('./p1-global/synthetic-complete-combat.json', import.meta.url), 'utf8'));
  const action = before.snapshot.bound_actions.actions.find(a => a.verb === 'play' && a.label === 'Play Zap');
  const card = before.snapshot.interaction.content.context.player.hand.find(c => c.entity_id === action.subject_referent_id);
  const readId = 'read:surface_card:original-held-card';
  const metadata = { read_id: readId, kind: 'surface_card', target_referent_id: card.entity_id,
    content_schema: 'sts2.player-environment/read/surface_card-1',
    visibility_basis: 'normal_player_visible_surface_card', ordering_semantics: 'single_entity' };
  before.snapshot.reads.push({ ...structuredClone(before.snapshot.reads[0]), ...metadata });
  const response = { ...structuredClone(before.reads[0].response), ...metadata,
    content: { ...structuredClone(card), public_details_revision: '1', display_context: 'hand', parent_read_id: null,
      display_owner_entity_id: before.snapshot.interaction.content.context.player.player_entity_id,
      keywords: [{ name: 'Channel', description: 'Native current Channel detail' },
        { name: 'Lightning', description: 'Native current Lightning detail' }], card_previews: [] },
    completeness: { status: 'complete', visible_information: 'complete_current_player_visible_card_detail',
      interaction_discovery: 'read_only', missing: [], hidden_by_policy: [] } };
  before.reads.push({ read_id: readId, kind: 'surface_card', response, error: null });
  const held = structuredClone(before);
  held.snapshot.snapshot_id += '-held'; held.snapshot.sequence++;
  for (const read of held.reads)
    read.response.expected_snapshot_id = read.response.observed_snapshot_id = held.snapshot.snapshot_id;
  held.snapshot.status = 'settling'; held.snapshot.interaction.stage = 'settling';
  held.snapshot.bound_actions.actions = [];
  held.snapshot.bound_actions.total_count = held.snapshot.bound_actions.materialized_count = 0;
  held.snapshot.interaction.content.surface.can_end_turn = false;
  held.snapshot.interaction.content.surface.playable_cards = [];
  return { before, held, action };
}
