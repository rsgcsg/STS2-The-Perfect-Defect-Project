import { decodePublicSourceSnapshot } from "../publicBasis.js";
import { canonicalJson } from "./canonical.js";
import { reduce, type Lowering, type P1Basis, type P1SessionState, type Reduction } from "./reducer.js";

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
