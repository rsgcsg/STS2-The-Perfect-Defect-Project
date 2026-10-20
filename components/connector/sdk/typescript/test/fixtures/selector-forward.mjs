import { readFileSync } from "node:fs";
// Codec-complete synthetic producer seam; exact native production is tested in Host.Tests.
export function selectorForwardPair() {
  const outer = JSON.parse(readFileSync(new URL("./p1-global/synthetic-complete-combat.json", import.meta.url), "utf8"));
  const s = outer.snapshot;
  const card = structuredClone(outer.reads.find((r) => r.kind === "run_deck").response.content.cards[0]);
  card.is_selected = false;
  const after = { ...structuredClone(card), is_upgraded: true, description: "Native upgraded description" };
  s.interaction.kind = "deck_upgrade_selection";
  s.interaction.stage = "selecting";
  s.interaction.content_schema = "sts2.player-environment/surface/deck_upgrade_selection-1";
  s.interaction.content.context = { kind: "rest_site", room_entity_id: "rest-room" };
  s.interaction.content.surface = {
    kind: "deck_upgrade_selection", stage: "selecting", screen_entity_id: "owner-one", request_entity_id: "task-one",
    cards: [card], min_select: 1, max_select: 1, require_manual_confirmation: false,
    selected_count: 0, selected_card_entity_ids: [], selectable_card_entity_ids: [card.entity_id], deselectable_card_entity_ids: [],
    can_confirm: false, cancelable: true, current_controls: [{ entity_id: "toggle", operation: "toggle_upgrade_view" }],
    selector_request: { entity_id: "task-one", owner_entity_id: "owner-one", family: "deck_upgrade", stage: "selecting",
      selection_model: "native_selected_original_set", add_transition: "native_preview_at_max", min_select: 1, max_select: 1,
      require_manual_confirmation: false, displayed_holder_count: null, selected_entity_ids: [], can_finish_now: false,
      can_decline_now: true, mechanics_qualified: true, preview_information_complete: true, preview_projection_qualified: true,
      capability_revision: "sts2.native-selector-public-facts/1", missing: [] },
    preview_options: [{ subject_entity_id: card.entity_id, before: structuredClone(card), after,
      display_context: "native_deck_upgrade_after", after_availability: "available", missing: [] }], enchantment_summary: null
  };
  const ref = s.referents[0];
  s.referents = [
    { ...ref, referent_id: card.entity_id, role: "card" },
    ...["toggle", "confirm", "cancel"].map(id => ({ ...ref, referent_id: id, role: "selector_control" }))
  ];
  const action = s.bound_actions.actions[0];
  s.bound_actions.actions = [
    { ...action, bound_action_id: "select-original", verb: "select", subject_referent_id: card.entity_id, arguments: [], label: "Select" },
    { ...action, bound_action_id: "inspect", verb: "open", subject_referent_id: card.entity_id, arguments: [], label: "Inspect" },
    { ...action, bound_action_id: "toggle-binding", verb: "open", subject_referent_id: "toggle", arguments: [], label: "Toggle" }
  ];
  s.bound_actions.total_count = s.bound_actions.materialized_count = s.bound_actions.actions.length;
  // Piles are irrelevant outside combat and are omitted by this synthetic provider.
  s.reads = s.reads.filter((r) => r.kind !== "combat_piles");
  outer.reads = outer.reads.filter((r) => r.kind !== "combat_piles");
  const preview = structuredClone(outer);
  const p = preview.snapshot.interaction.content.surface;
  p.stage = preview.snapshot.interaction.stage = p.selector_request.stage = "preview";
  p.cards[0].is_selected = p.preview_options[0].before.is_selected = true;
  p.selected_card_entity_ids = p.selector_request.selected_entity_ids = [card.entity_id];
  p.selected_count = 1; p.selectable_card_entity_ids = []; p.can_confirm = true;
  p.selector_request.can_finish_now = true; p.selector_request.can_decline_now = false;
  p.current_controls = [{ entity_id: "confirm", operation: "confirm" }, { entity_id: "cancel", operation: "cancel_preview" }];
  preview.snapshot.bound_actions.actions = [
    { ...action, bound_action_id: "exact-confirm", verb: "confirm", subject_referent_id: "confirm", arguments: [], label: "Confirm" },
    { ...action, bound_action_id: "exact-cancel", verb: "cancel", subject_referent_id: "cancel", arguments: [], label: "Back" }
  ];
  preview.snapshot.bound_actions.total_count = preview.snapshot.bound_actions.materialized_count = 2;
  return { outer, preview };
}

// Faithful synthetic shape of the native deck-card callback below its max:
// only the one card's declared selection decoration changes; no preview opens.
export function selectorReversalPair() {
  const { outer } = selectorForwardPair();
  const surface = outer.snapshot.interaction.content.surface;
  surface.kind = outer.snapshot.interaction.kind = 'native_deck_card_selection';
  outer.snapshot.interaction.content_schema = 'sts2.player-environment/surface/native_deck_card_selection-1';
  surface.min_select = surface.max_select = 2;
  surface.current_controls = []; surface.cancelable = false;
  Object.assign(surface.selector_request, { family: 'deck_card', min_select: 2, max_select: 2,
    can_decline_now: false });
  surface.preview_options[0].after = null;
  surface.preview_options[0].after_availability = 'not_applicable';
  surface.preview_options[0].display_context = 'native_deck_card_before';
  const secondCard = { ...structuredClone(surface.cards[0]), entity_id: 'second-original-card' };
  surface.cards.push(secondCard);
  surface.selectable_card_entity_ids.push(secondCard.entity_id);
  surface.preview_options.push({ ...structuredClone(surface.preview_options[0]),
    subject_entity_id: secondCard.entity_id, before: structuredClone(secondCard) });
  outer.snapshot.bound_actions.actions = outer.snapshot.bound_actions.actions.slice(0, 2);
  outer.snapshot.bound_actions.actions.push({ ...structuredClone(outer.snapshot.bound_actions.actions[0]),
    bound_action_id: 'select-second', subject_referent_id: secondCard.entity_id });
  outer.snapshot.bound_actions.total_count = outer.snapshot.bound_actions.materialized_count = 3;
  const card = surface.cards[0].entity_id;
  outer.snapshot.referents = outer.snapshot.referents.map(ref => structuredClone(ref));
  const ref = outer.snapshot.referents.find(ref => ref.referent_id === card);
  ref.state.selected = false; ref.properties = { ...ref.properties, is_selected: false };
  outer.snapshot.referents.push({ ...structuredClone(ref), referent_id: secondCard.entity_id });
  // Keep the duplicate advertised selector referent and Read decoration too.
  outer.snapshot.referents.push({ ...structuredClone(ref), referent_id: 'selector-original',
    role: 'selector', properties: structuredClone(surface) });
  outer.snapshot.interaction.capabilities = [
    { verb: 'open', subject_role: 'card', arguments: [], availability_basis: 'current_native_interaction' },
    { verb: 'select', subject_role: 'card', arguments: [], availability_basis: 'current_native_interaction' }
  ];
  const selected = structuredClone(outer), selectedSurface = selected.snapshot.interaction.content.surface;
  for (const s of [selectedSurface, selected.snapshot.referents.at(-1).properties]) {
    s.selected_count = 1; s.selected_card_entity_ids = s.selector_request.selected_entity_ids = [card];
    s.selectable_card_entity_ids = [secondCard.entity_id]; s.deselectable_card_entity_ids = [card];
    s.cards[0].is_selected = s.preview_options[0].before.is_selected = true;
  }
  selected.snapshot.referents[0].state.selected = selected.snapshot.referents[0].properties.is_selected = true;
  Object.assign(selected.snapshot.bound_actions.actions[0], { verb: 'deselect', label: 'Deselect', bound_action_id: 'deselect-original' });
  selected.snapshot.interaction.capabilities.unshift({ verb: 'deselect', subject_role: 'card', arguments: [], availability_basis: 'current_native_interaction' });
  function rename(basis, suffix) {
    basis.snapshot.snapshot_id += suffix; basis.snapshot.sequence++;
    for (const read of basis.reads) if (read.response)
      read.response.expected_snapshot_id = read.response.observed_snapshot_id = basis.snapshot.snapshot_id;
    return basis;
  }
  return { outer, selected: rename(selected, '-selected'), restored: rename(structuredClone(outer), '-restored') };
}
