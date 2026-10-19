/** Interpret an already observed native cancellation; never infer successful Commit.
 * The recording reader owns original identity/bytes. Strict Managed comparison
 * still has to verify continuity across every retained cancellation checkpoint. */
import { isDeepStrictEqual } from 'node:util';

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
