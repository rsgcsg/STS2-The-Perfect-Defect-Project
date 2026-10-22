/** Interpret an already observed native cancellation; never infer successful Commit.
 * The recording reader owns original identity/bytes. Strict Managed comparison
 * still has to verify continuity across every retained cancellation checkpoint. */
import { isDeepStrictEqual } from 'node:util';
import { classifyNativePresentation } from './p1-native-presentation.mjs';

const inputOf = row => row.native_input ?? (row.bound_action ? {
  verb: row.bound_action.verb, subject_referent_id: row.bound_action.subject_referent_id ?? null,
  arguments: Object.fromEntries(row.bound_action.arguments.map(arg => [arg.role, arg.referent_id]))
} : null);
const sameOperands = (a, b) => a != null && b != null && a.verb === b.verb
  && (a.subject_referent_id ?? null) === (b.subject_referent_id ?? null)
  && isDeepStrictEqual(a.arguments, b.arguments)
  && (a.action_key == null || b.action_key == null || a.action_key === b.action_key);

function completeInformation(value, reduction = null) {
  if (!value || value.capture_status !== 'complete' || value.missing.length !== 0
    || value.snapshot.completeness.status !== 'complete'
    || value.snapshot.information_policy.includes_hidden_information !== false) return false;
  // H is source context, not the execution decision. A complete popup or settling
  // source need not itself offer a P1 choice. The reader already checked all Reads.
  if (!reduction) return true;
  const nonDecision = `scene:${value.snapshot.interaction.kind}:${value.snapshot.status}`;
  return reduction.missing.every(reason => reason === nonDecision);
}

export function classifyNativeCancellation({ accepted, execution, terminal, humanBasis,
  executionBasis, match, state, core }) {
  if (terminal?.phase !== 'cancelled' || accepted.missing_reason != null
    || (accepted.native_input != null && accepted.bound_action != null)
    || accepted.native_receipt?.kind !== 'game_action_accepted'
    || accepted.native_receipt.carrier_ref !== accepted.action_witness_id || !inputOf(accepted)
    || !humanBasis || !completeInformation(humanBasis)) return null;
  const receipt = { decision_id: accepted.decision_id, action_witness_id: accepted.action_witness_id,
    source_event_id: terminal.event_id, terminal_sequence: terminal.sequence,
    disposition: 'cancelled', phase: execution ? 'cancelled_after_execution' : 'cancelled_before_execution',
    reason: execution ? 'native_cancelled_at_execution' : 'native_cancelled_before_execution',
    accepted_event_id: accepted.event_id, execution_event_id: execution?.event_id ?? null,
    successful_label: false, continuity: 'requires_strict_managed_comparison' };
  if (!execution) return { receipt, checkpoint: null, expected_source_issues: ['accepted_native_action_ended_before_execution'] };
  if (!executionBasis || execution.basis.snapshot_id !== executionBasis.snapshot.snapshot_id
    || execution.execution_snapshot_id !== executionBasis.snapshot.snapshot_id
    || (execution.native_input != null && execution.bound_action != null)
    || !sameOperands(inputOf(accepted), inputOf(execution))) return null;

  let checkpoint = match.reduction;
  let expectedSourceIssues = [];
  if (match.status !== 'matched') {
    const native = execution.native_semantic_capture;
    // Missing selected-action membership is expected only for a genuinely
    // rejected native input. Missing capture/Read/binding remains blocking.
    if (execution.missing_reason !== 'execution_semantic_action_space_missing'
      || native?.schema !== 'sts2.player-environment/process-local-native-semantic-capture-1'
      || native.status !== 'captured' || native.phase !== 'before_execution'
      || native.ui_catalog?.snapshot_id !== executionBasis.snapshot.snapshot_id
      || native.observed_action?.membership !== 'absent' || native.observed_action.semantic_match_count !== 0
      || native.observed_action.native_action_type !== accepted.native_action_type
      || !Array.isArray(native.semantic_actions) || native.semantic_actions.length === 0) return null;
    checkpoint = core.reduceNativeExecution(state, executionBasis, native.semantic_actions);
    expectedSourceIssues = ['execution_semantic_action_space_missing'];
    const input = inputOf(execution);
    const card = executionBasis.snapshot.interaction.content.context.player?.hand?.find(item => item.entity_id === input.subject_referent_id);
    receipt.reason = card?.unplayable_reason ? `native_execution_rejected:${card.unplayable_reason}` : 'native_execution_input_not_admissible';
  }
  if (!completeInformation(executionBasis, checkpoint) || !checkpoint.complete
    || !['decision', 'terminal'].includes(checkpoint.observation.status)) return null;
  return { receipt, checkpoint, expected_source_issues: expectedSourceIssues };
}

/** Preserve the approved legacy Smith explanation. New recording cancellations
 * must carry their own normally-completed false Task and exact child receipt;
 * an old merchant unknown cannot acquire those facts from this adapter. */
export function classifyNativeSelectorCancellation({ row, acceptedRows, terminalById, basis, state, core }) {
  const parentEnd = terminalById.get(row.decision_id);
  const parentCompletion = parentEnd?.native_completion;
  const smith = row.native_action_type === 'RestSiteSynchronizer.ChooseLocalOption';
  const merchant = row.native_action_type === 'MerchantCardRemovalEntry.OnTryPurchaseWrapper';
  const callback = 'MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.'
    + (smith ? 'NDeckUpgradeSelectScreen' : 'NDeckCardSelectScreen') + '.Close.Released';
  const proof = parentCompletion?.explicit_selector_cancel;
  const parentIndex = acceptedRows.indexOf(row);
  // The durable explicit receipt names the final cancel. A preview/return may
  // precede it; adjacency is only the older Smith compatibility case.
  const childIndex = proof ? acceptedRows.findIndex(item => item.decision_id === proof.child_decision_id) : parentIndex + 1;
  if (childIndex <= parentIndex) return null;
  const child = acceptedRows[childIndex], restored = acceptedRows[childIndex + 1];
  const childEnd = terminalById.get(child?.decision_id);
  const explicit = parentEnd?.phase === 'cancelled' && parentCompletion?.native_task_status === 'RanToCompletion'
    && parentCompletion.native_bool_result === false && proof?.native_callback === callback
    && proof.terminal === true && proof.native_acceptance_proven === true
    && proof.child_decision_id === child?.decision_id && proof.child_action_witness_id === child?.action_witness_id
    && proof.parent_action_witness_id === row.action_witness_id
    && proof.parent_owner_witness_id === parentCompletion.native_operand_witness_id
    && typeof proof.selector_owner_witness_id === 'string' && proof.selector_owner_witness_id.length > 0
    && proof.selector_owner_witness_id === child?.native_witness?.selector_owner_witness_id;
  const legacySmith = smith && parentEnd?.phase === 'unknown' && parentCompletion != null
    && ['native_task_status', 'native_bool_result', 'explicit_selector_cancel'].every(key => !Object.hasOwn(parentCompletion, key));
  // New native completions already retain the actual post-cancel basis. A final
  // cancel needs no later player input to supply an unrelated accepted frame.
  let afterEvent = explicit ? parentEnd : restored;
  let afterReference = explicit ? parentEnd.successor_basis : restored?.basis;
  let afterMissing = explicit ? parentEnd.successor_missing_reason : restored?.missing_reason;
  // Native Task completion can precede the restored controls becoming ready.
  // Keep that settling original; use only the next actual accepted H as the
  // later boundary, before its input, never as a replacement for the Task basis.
  if (explicit && afterReference && afterMissing == null) {
    const immediate = basis(afterReference);
    if (completeInformation(immediate) && immediate.snapshot.status === 'settling'
        && restored?.basis && restored.missing_reason == null
        && restored.sequence > parentEnd.sequence) {
      afterEvent = restored; afterReference = restored.basis; afterMissing = restored.missing_reason;
    }
  }
  if ((!smith && !merchant) || !(explicit || legacySmith)
    || row.native_receipt?.kind !== 'semantic_ui_accepted' || row.native_receipt.carrier_ref !== row.action_witness_id
    || row.bound_action?.verb !== (smith ? 'activate' : 'open') || row.missing_reason != null || !row.basis
    || parentCompletion?.family !== (smith ? 'rest_site' : 'shop_inventory')
    || parentCompletion.kind !== row.native_action_type || parentCompletion.succeeded !== false
    || parentCompletion.action_witness_id !== row.action_witness_id
    || typeof parentCompletion.task_witness_id !== 'string' || parentCompletion.task_witness_id.length === 0
    || typeof row.native_witness?.subject_witness_id !== 'string'
    || parentCompletion.native_operand_witness_id !== row.native_witness.subject_witness_id
    || child?.native_action_type !== callback || child.native_receipt?.kind !== 'semantic_ui_accepted'
    || child.native_receipt.carrier_ref !== child.action_witness_id || child.missing_reason != null
    || child.native_receipt.selector_callback?.native_callback !== callback
    || child.native_receipt.selector_callback.native_acceptance_proven !== true
    || child.native_witness?.parent_action_witness_id !== row.action_witness_id
    || child.native_witness.parent_owner_witness_id !== parentCompletion.native_operand_witness_id
    || child.bound_action?.verb !== 'cancel' || !child.basis || childEnd?.phase !== 'finished'
    || childEnd.native_completion?.native_callback !== callback || childEnd.native_completion.terminal !== true
    || !afterReference || afterMissing != null
    || ![child, parentEnd, childEnd, afterEvent].every(item => item.session_id === row.session_id && item.run_id === row.run_id)
    || !(row.sequence < child.sequence && child.sequence < childEnd.sequence
      && childEnd.sequence < parentEnd.sequence && (explicit || parentEnd.sequence < restored.sequence))) return null;
  // Intervening inputs must themselves be complete zero-effect presentation
  // pairs on this very selector and parent. Never jump over another action.
  for (let index = parentIndex + 1; index < childIndex; index += 2) {
    if (!explicit || index + 1 >= childIndex) return null;
    const pair = acceptedRows.slice(index, index + 2);
    if (!pair.every(item => ['selector_owner_witness_id', 'parent_action_witness_id', 'parent_owner_witness_id']
      .every(key => typeof item.native_witness?.[key] === 'string'
        && item.native_witness[key] === child.native_witness[key])
      && item.session_id === row.session_id && item.run_id === row.run_id
      && item.native_receipt?.carrier_ref === item.action_witness_id
      && item.native_receipt.selector_callback?.native_callback === item.native_action_type
      && item.native_receipt.selector_callback.native_acceptance_proven === true
      && item.native_receipt.selector_callback.terminal === false
      && terminalById.get(item.decision_id)?.native_completion?.native_callback === item.native_action_type
      && terminalById.get(item.decision_id)?.native_completion?.terminal === false
      && terminalById.get(item.decision_id)?.sequence < child.sequence)) return null;
    const presentation = classifyNativePresentation({ row: pair[0], next: pair[1], terminalById, basis, state, core });
    if (presentation?.kind !== 'selector_preview_return' || presentation.disposition !== 'presentation'
      || !presentation.comparison.equal || !isDeepStrictEqual(presentation.decision_ids, pair.map(item => item.decision_id))) return null;
  }
  const before = basis(row.basis), middle = basis(child.basis), after = basis(afterReference);
  if (![before, middle, after].every(value => completeInformation(value))) return null;
  const first = before.snapshot.interaction.content.surface, selector = middle.snapshot.interaction.content.surface;
  if (smith ? first.kind !== 'rest_site' || !first.options?.some(option => option.option_id === 'SMITH'
      && option.entity_id === row.bound_action.subject_referent_id && option.enabled === true)
    : first.kind !== 'shop_inventory' || first.card_removal?.entity_id !== row.bound_action.subject_referent_id)
    return null;
  if (selector.kind !== (smith ? 'deck_upgrade_selection' : 'native_deck_card_selection') || selector.stage !== 'selecting'
    || !Array.isArray(selector.selected_card_entity_ids) || selector.selected_card_entity_ids.length !== 0
    || !selector.current_controls?.some(control => control.entity_id === child.bound_action.subject_referent_id
      && control.operation === 'cancel_selection')
    || !['matched', 'continuation_required'].includes(core.matchNativeSemanticChoice(state, before, row).status)) return null;
  const childMatch = core.matchNativeSemanticChoice(state, middle, child);
  if (childMatch.status !== 'matched' && !(childMatch.status === 'unmapped'
    && childMatch.reason === 'accepted_action_has_no_p1_choice')) return null;
  const comparison = core.compareOriginalPresentationBases(before, after);
  if (!comparison.equal) return null;
  return { decision_ids: [row.decision_id, child.decision_id], before: { event: row, reference: row.basis, basis: before },
    after: { event: afterEvent, reference: afterReference, basis: after }, comparison,
    receipt: { decision_id: row.decision_id, decision_ids: [row.decision_id, child.decision_id],
      action_witness_id: row.action_witness_id, source_event_id: parentEnd.event_id,
      terminal_sequence: parentEnd.sequence, disposition: 'cancelled', phase: 'cancelled_semantic_selector',
      kind: smith ? 'rest_smith_cancel' : 'merchant_removal_cancel',
      reason: 'exact_native_child_cancel_and_equal_public_originals', successful_label: false,
      original_parent_terminal: { event_id: parentEnd.event_id, phase: parentEnd.phase, native_completion: parentCompletion },
      native_child_terminal: { event_id: childEnd.event_id, action_witness_id: child.action_witness_id,
        native_completion: childEnd.native_completion }, comparison, continuity: 'requires_strict_managed_comparison' } };
}
