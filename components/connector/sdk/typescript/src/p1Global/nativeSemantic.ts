import { decodePublicSourceSnapshot } from "../publicBasis.js";
import { canonicalJson } from "./canonical.js";
import { reduce, reduceNativeExecution, type NativeExecutionAction, type Lowering, type P1Basis, type P1SessionState, type Reduction } from "./reducer.js";

/** An accepted native action already correlated to its original Human-time basis.
 * The recording reader verifies original bytes, order and native provenance.
 * This matcher only interprets those facts through the shared P1 lowering. */
export interface NativeSemanticAcceptance {
  readonly action_witness_id: string;
  readonly basis: { readonly snapshot_id: string };
  readonly bound_action: {
    readonly bound_action_id: string;
    readonly verb: string;
    readonly subject_referent_id?: string | null;
    readonly arguments: readonly { readonly role: string; readonly referent_id: string }[];
  };
  readonly native_receipt: {
    readonly kind: "game_action_accepted" | "semantic_ui_accepted";
    readonly carrier_ref: string;
  };
}

export interface NativeSemanticMatch {
  readonly status: "matched" | "incomplete" | "unmapped" | "ambiguous" | "continuation_required";
  readonly reason: string;
  readonly choice_id: string | null;
  readonly lowering: Lowering | null;
  readonly reduction: Reduction;
}

/** First native-semantic slice: one actual accepted native action, with no
 * unresolved P1 continuation. Accepted is not finished; the caller retains the
 * original lifecycle and obtains a successor before declaring a complete step.
 * No mouse preparations or Source4 publication-prefix classification is used. */
export function matchNativeSemanticChoice(state: P1SessionState, basis: P1Basis,
  accepted: NativeSemanticAcceptance): NativeSemanticMatch {
  const reduction = reduce(state, basis);
  const result = (status: NativeSemanticMatch["status"], reason: string,
    choice: string | null = null, lowering: Lowering | null = null): NativeSemanticMatch =>
    ({ status, reason, choice_id: choice, lowering, reduction });
  if (!reduction.complete || reduction.observation.status !== "decision")
    return result("incomplete", "human_basis_is_not_a_complete_p1_decision");

  const snapshot = decodePublicSourceSnapshot(basis.snapshot).data;
  if (accepted.basis.snapshot_id !== snapshot.snapshot_id)
    return result("unmapped", "accepted_action_has_foreign_human_basis");
  if (!["game_action_accepted", "semantic_ui_accepted"].includes(accepted.native_receipt.kind)
    || accepted.native_receipt.carrier_ref !== accepted.action_witness_id)
    return result("unmapped", "accepted_action_carrier_mismatch");

  const recorded = accepted.bound_action;
  const original = snapshot.bound_actions.actions.filter(action => action.bound_action_id === recorded.bound_action_id);
  if (original.length !== 1 || original[0]!.verb !== recorded.verb
    || (original[0]!.subject_referent_id ?? null) !== (recorded.subject_referent_id ?? null)
    || canonicalJson(original[0]!.arguments) !== canonicalJson(recorded.arguments))
    return result("unmapped", "accepted_action_operands_do_not_match_original_human_basis");

  const choices = Object.entries(reduction.lowering).filter(([, lowering]) =>
    lowering.first?.bound_action_id === recorded.bound_action_id && lowering.first.verb === recorded.verb);
  if (choices.length === 0) return result("unmapped", "accepted_action_has_no_p1_choice");
  if (choices.length !== 1) return result("ambiguous", "accepted_action_needs_intention_or_continuation");
  const [choiceId, lowering] = choices[0]!;
  if (lowering.follow.length !== 0)
    return result("continuation_required", "p1_intention_requires_native_continuation");
  return result("matched", "exact_native_acceptance_matches_shared_p1_lowering", choiceId, lowering);
}

export interface RecordedExecutionInput {
  readonly action_key: string;
  readonly verb: string;
  readonly subject_referent_id: string | null;
  readonly arguments: Readonly<Record<string, string>>;
}

export interface NativeExecutionOriginal {
  readonly action_witness_id: string;
  readonly basis: { readonly snapshot_id: string };
  readonly phase: string;
  readonly execution_snapshot_id?: string | null;
  readonly missing_reason: string | null;
  readonly native_input: RecordedExecutionInput | null;
  readonly bound_action?: NativeSemanticAcceptance["bound_action"] | null;
  readonly execution_action_space: {
    readonly schema: string;
    readonly action_witness_id: string;
    readonly phase: string;
    readonly status: string;
    readonly actions: readonly NativeExecutionAction[];
    readonly observed_action_key: string;
    readonly observed_membership: string;
    readonly observed_match_count: number;
    readonly human_bound_action_id?: string | null;
    readonly human_native_action_key?: string | null;
  };
}

/** Execution interpretation uses the recorded S and native A(S), retaining the
 * original PB1 public delivery catalog unchanged (including an empty catalog). */
export function matchNativeExecutionChoice(state: P1SessionState, basis: P1Basis,
  accepted: Omit<NativeSemanticAcceptance, "bound_action"> & {
    readonly bound_action: NativeSemanticAcceptance["bound_action"] | null;
    readonly native_input?: RecordedExecutionInput | null;
  }, execution: NativeExecutionOriginal): NativeSemanticMatch {
  let reduction = reduce(state, basis);
  const result = (status: NativeSemanticMatch["status"], reason: string, choice_id: string | null = null,
    lowering: Lowering | null = null): NativeSemanticMatch => ({ status, reason, choice_id, lowering, reduction });
  const snapshot = decodePublicSourceSnapshot(basis.snapshot).data;
  if (execution.action_witness_id !== accepted.action_witness_id
      || execution.basis.snapshot_id !== snapshot.snapshot_id
      || execution.execution_snapshot_id != null && execution.execution_snapshot_id !== snapshot.snapshot_id
      || accepted.native_receipt.carrier_ref !== accepted.action_witness_id
      || accepted.native_receipt.kind !== "game_action_accepted")
    return result("unmapped", "execution_original_identity_mismatch");
  const space = execution.execution_action_space;
  if (execution.phase !== "before_execution" || execution.missing_reason != null
      || space?.schema !== "sts2.human-annotator/execution-semantic-action-space-3"
      || space.action_witness_id !== accepted.action_witness_id || space.phase !== "before_execution"
      || space.status !== "captured" || space.observed_membership !== "exact_once"
      || space.observed_match_count !== 1 || !Array.isArray(space.actions) || space.actions.length === 0
      || new Set(space.actions.map(action => action.key)).size !== space.actions.length)
    return result("incomplete", "execution_action_space_not_complete_and_exact");
  const original = space.actions.filter(action => action.key === space.observed_action_key);
  if (original.length !== 1) return result("unmapped", "execution_native_key_not_unique");
  const selected = original[0]!;
  if (execution.bound_action) {
    const bound = execution.bound_action;
    const human = accepted.bound_action;
    // As in the native owner, presentation verbs may differ (travel/activate).
    // The declared binding must still name the exact S subject and all operands.
    if (!human || execution.native_input != null || space.human_bound_action_id !== bound.bound_action_id
        || (selected.subject_referent_id ?? null) !== (bound.subject_referent_id ?? null)
        || canonicalJson(selected.arguments) !== canonicalJson(Object.fromEntries(
          bound.arguments.map(argument => [argument.role, argument.referent_id])))
        || human.verb !== bound.verb || (human.subject_referent_id ?? null) !== (bound.subject_referent_id ?? null)
        || canonicalJson([...human.arguments].sort((a, b) => a.role.localeCompare(b.role)))
          !== canonicalJson([...bound.arguments].sort((a, b) => a.role.localeCompare(b.role))))
      return result("unmapped", "execution_public_action_operands_changed");
    return matchNativeSemanticChoice(state, basis, { ...accepted, basis: execution.basis, bound_action: bound });
  }
  if (space.human_native_action_key != null && space.human_native_action_key !== selected.key)
    return result("unmapped", "execution_native_human_key_mismatch");
  const input = accepted.native_input ?? (accepted.bound_action ? {
    action_key: selected.key, verb: accepted.bound_action.verb,
    subject_referent_id: accepted.bound_action.subject_referent_id ?? null,
    arguments: Object.fromEntries(accepted.bound_action.arguments.map(argument => [argument.role, argument.referent_id]))
  } : null);
  if (!input || input.action_key !== selected.key || input.verb !== selected.verb
      || (input.subject_referent_id ?? null) !== (selected.subject_referent_id ?? null)
      || canonicalJson(input.arguments) !== canonicalJson(selected.arguments)
      || execution.native_input != null && (execution.native_input.action_key !== selected.key
        || execution.native_input.verb !== selected.verb
        || execution.native_input.subject_referent_id !== selected.subject_referent_id
        || canonicalJson(execution.native_input.arguments) !== canonicalJson(selected.arguments)))
    return result("unmapped", "execution_native_input_operands_changed");
  if (space.actions.some(action => !["play", "use", "end_turn"].includes(action.verb)
      || !action.key || !action.native_legality_basis || !action.arguments
      || Object.entries(action.arguments).some(([role, id]) => role !== "target" || typeof id !== "string")))
    return result("unmapped", "execution_action_space_has_unsupported_native_action");
  const context = snapshot.interaction.content.context as Record<string, any>;
  const knownIds = new Set<unknown>(snapshot.referents.map(ref => ref.referent_id));
  if (space.actions.some(action => action.verb === "play"
      && !context.player?.hand?.some((card: any) => card.entity_id === action.subject_referent_id)
      || action.verb === "use"
      && !context.player?.potion_states?.some((potion: any) => potion.entity_id === action.subject_referent_id)
      || Object.values(action.arguments).some(id => !knownIds.has(id))))
    return result("incomplete", "execution_catalog_operand_missing_from_same_public_basis");
  reduction = reduceNativeExecution(state, basis, space.actions);
  if (!reduction.complete || reduction.observation.status !== "decision")
    return result("incomplete", "execution_basis_is_not_a_complete_p1_decision");
  const choices = Object.entries(reduction.lowering).filter(([, low]) => low.native_input_key === selected.key);
  if (choices.length !== 1) return result("unmapped", "execution_input_has_no_unique_p1_choice");
  return result("matched", "exact_native_input_matches_original_execution_catalog", choices[0]![0], choices[0]![1]);
}
