/** Classify verified finished native browsing without a P1 successful label.
 * The bundle reader retains immutable originals; no native completion is inferred.
 * Endpoint equality includes the whole PB1, every Read and the finite directory. */
const complete = value => value?.capture_status === 'complete' && value.missing.length === 0
  && value.snapshot.completeness.status === 'complete';
const surface = value => value.snapshot.interaction.content.surface;
// Deck-card previews use the same zero-label round trip as upgrade previews.
// Bind the actual native selector callbacks as well as the public request; a
// cancelled purchase or a different selector is not a preview return.
function nativeDeckPreviewPair(row, next, end, secondEnd) {
  const prefix = 'MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NDeckCardSelectScreen.';
  if (row.native_action_type !== `${prefix}OnCardClicked`
      || next.native_action_type !== `${prefix}PreviewCancel.Released`) return false;
  if (!['session_id', 'timeline_id', 'run_id'].every(key =>
    [next, end, secondEnd].every(item => item[key] === row[key]))) return false;
  if (!['selector_owner_witness_id', 'parent_action_witness_id', 'parent_owner_witness_id'].every(key =>
    typeof row.native_witness?.[key] === 'string' && row.native_witness[key].length > 0
      && row.native_witness[key] === next.native_witness?.[key])) return false;
  return [[row, end], [next, secondEnd]].every(([accepted, terminal]) =>
    accepted.native_receipt.carrier_ref === accepted.action_witness_id
      && accepted.native_receipt.selector_callback?.native_callback === accepted.native_action_type
      && accepted.native_receipt.selector_callback.native_acceptance_proven === true
      && accepted.native_receipt.selector_callback.terminal === false
      && terminal.action_witness_id === accepted.action_witness_id
      && terminal.native_completion?.native_callback === accepted.native_action_type
      && terminal.native_completion.terminal === false);
}
const exact = (row, value, state, core) => {
  if (!complete(value) || row.missing_reason != null || row.native_receipt?.kind !== 'semantic_ui_accepted'
      || !row.bound_action || row.native_input != null) return false;
  const match = core.matchNativeSemanticChoice(state, value, row);
  return ['matched', 'ambiguous', 'continuation_required'].includes(match.status)
    || match.status === 'unmapped' && match.reason === 'accepted_action_has_no_p1_choice';
};
export function classifyNativePresentation({ row, next, terminalById, basis, state, core }) {
  const end = terminalById.get(row.decision_id);
  if (end?.phase !== 'finished' || !row.basis || !end.successor_basis) return null;
  const before = basis(row.basis);
  if (!exact(row, before, state, core)) return null;
  const first = surface(before), afterEnd = basis(end.successor_basis);
  let kind, owner, members, after, afterReference, afterEvent, rewardGroup;
  if (next?.basis && end.sequence < next.sequence) {
    const secondEnd = terminalById.get(next.decision_id), middle = basis(next.basis);
    const second = surface(middle);
    if (secondEnd?.phase === 'finished' && secondEnd.successor_basis && exact(next, middle, state, core)) {
      const restored = basis(secondEnd.successor_basis);
      if (first.kind === 'shop_inventory' && row.native_action_type === 'NMerchantInventory.Close'
          && row.bound_action.verb === 'cancel' && second.kind === 'shop_room'
          && next.native_action_type === 'NMerchantRoom.OpenInventory' && next.bound_action.verb === 'open'
          && complete(afterEnd) && core.compareOriginalPresentationBases(afterEnd, middle).equal
          && surface(restored).kind === 'shop_inventory'
          && first.shop_session_entity_id === surface(restored).shop_session_entity_id
          && typeof first.shop_session_entity_id === 'string') {
        kind = 'shop_close_reopen'; owner = first.shop_session_entity_id;
      } else if ((first.kind === 'deck_upgrade_selection'
          || first.kind === 'native_deck_card_selection' && nativeDeckPreviewPair(row, next, end, secondEnd))
          && first.stage === 'selecting'
          && row.bound_action.verb === 'select' && second.kind === first.kind && second.stage === 'preview'
          && first.screen_entity_id === second.screen_entity_id && first.request_entity_id === second.request_entity_id
          && next.bound_action.verb === 'cancel'
          && second.current_controls?.some(control => control.entity_id === next.bound_action.subject_referent_id
            && control.operation === 'cancel_preview')
          && second.selected_card_entity_ids?.length === first.selected_card_entity_ids.length + 1
          && second.selected_card_entity_ids.includes(row.bound_action.subject_referent_id)
          && first.selected_card_entity_ids.every(id => second.selected_card_entity_ids.includes(id))
          && complete(afterEnd) && surface(afterEnd).screen_entity_id === first.screen_entity_id
          && core.compareOriginalPresentationBases(afterEnd, middle).equal) {
        kind = 'selector_preview_return'; owner = first.request_entity_id;
      } else if (first.kind === 'reward_claim' && row.bound_action.verb === 'activate'
          && second.kind === 'card_reward_selection' && ['skip', 'activate'].includes(next.bound_action.verb)) {
        const group = first.rewards?.find(group => group.entity_id === row.bound_action.subject_referent_id);
        const skip = second.alternatives?.find(option => option.entity_id === next.bound_action.subject_referent_id
          && option.is_native_skip === true);
        if (group?.kind === 'card' && group.can_skip_selection === true && core.completeRewardGroup(group) && skip
            // The native open Task may finish before card controls become
            // clickable. Retain that original, but bind both actual child
            // captures through the existing complete intention/world/Read check.
            && complete(afterEnd) && core.matchesRewardIntention(core.rewardIntention(first.reward_set_id, group, before,
              'decline', skip.option_id), afterEnd)
            && core.matchesRewardIntention(core.rewardIntention(first.reward_set_id, group, before,
              'decline', skip.option_id), middle)
            && surface(restored).kind === 'reward_claim' && surface(restored).reward_set_id === first.reward_set_id) {
          kind = 'reward_open_return'; owner = first.reward_set_id; rewardGroup = group;
        }
      }
      if (kind) {
        members = [row.decision_id, next.decision_id]; after = restored;
        afterReference = secondEnd.successor_basis; afterEvent = secondEnd;
      }
    }
    if (!kind && first.kind === 'reward_claim' && row.bound_action.verb === 'activate'
        && row.bound_action.subject_referent_id == null && surface(afterEnd).kind === 'map_navigation'
        && second.kind === 'reward_claim' && second.reward_set_id === first.reward_set_id
        && complete(afterEnd) && complete(middle)) {
      kind = 'reward_map_return'; owner = first.reward_set_id; members = [row.decision_id];
      after = middle; afterReference = next.basis; afterEvent = next;
    }
  }
  if (!kind || !complete(after)) return null;
  const comparison = core.compareOriginalPresentationBases(before, after);
  // User-approved existing stop_relic_refresh exception. No changed value is
  // erased, no successful label is emitted, and strict comparison still sees it.
  let exception = null;
  if (!comparison.equal && kind === 'reward_open_return' && rewardGroup) {
    const groupIndex = first.rewards.indexOf(rewardGroup);
    const refIndex = before.snapshot.referents.findIndex(ref => ref.referent_id === rewardGroup.entity_id);
    const expected = new Set([`$.snapshot.interaction.content.surface.rewards.${groupIndex}.native_relic_refresh_subscription`,
      `$.snapshot.referents.${refIndex}.properties.native_relic_refresh_subscription`]);
    if (refIndex >= 0 && comparison.changes.length === 2 && comparison.changes.every(change =>
      expected.has(change.path) && change.before === 'attached' && change.after === 'absent'))
      exception = { id: 'stop_relic_refresh', disposition: 'explained_native_side_effect',
        strict_comparison: 'preserve_actual_before_and_after_difference' };
  }
  return { kind, owner, decision_ids: members, before: { reference: row.basis, event: row, basis: before },
    after: { reference: afterReference, event: afterEvent, basis: after }, comparison,
    disposition: comparison.equal ? 'presentation' : exception ? 'presentation_exception' : 'presentation_state_changed',
    exception,
    successful_label: false, strict_managed_comparison: 'not_run' };
}
