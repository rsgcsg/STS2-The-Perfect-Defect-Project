/** Locate one original known UI input in the current finite catalog. The whole
 * P1 graph and one injective entity mapping must agree before operands map.
 * Never construct an operand or fall back to an index/name-only guess. */
import { isDeepStrictEqual } from 'node:util';
import * as core from '../components/connector/sdk/typescript/dist/p1Global/index.js';
import { recordedPreferencePair } from './recorded-replay-preferences.mjs';
import { comparePublicPointBindings } from '../components/policy-runtime/dist/p1-global/compare.js';

export function locateRecordedNativeInput(witness, actualBasis) {
  const complete = basis => basis?.capture_status === 'complete' && basis.missing?.length === 0
    && basis.snapshot?.completeness?.status === 'complete';
  if (witness.original_terminal_phase !== 'finished' || !complete(witness.basis) || !complete(actualBasis))
    throw new Error('recorded_native_input_requires_known_complete_original');
  const originalActions = witness.basis.snapshot.bound_actions.actions;
  const original = originalActions.filter(action => action.bound_action_id === witness.bound_action.bound_action_id
    && action.verb === witness.bound_action.verb);
  if (original.length !== 1 || !isDeepStrictEqual(original[0], witness.bound_action))
    throw new Error('recorded_native_input_not_exact_original_catalog_member');
  // Fresh locator sessions avoid importing a source alias or reward commitment
  // into the live replay session. They do not change its P1 state or labels.
  const left = core.reduce(core.createSession('native-input-left', 'reference'), witness.basis);
  const right = core.reduce(core.createSession('native-input-right', 'managed'), actualBasis);
  if (!left.complete || !right.complete) throw new Error('recorded_native_input_p1_locator_incomplete');
  const projected = recordedPreferencePair(left.observation, right.observation);
  const graph = comparePublicPointBindings(projected.left, projected.right);
  if (graph.verdict !== 'equal') throw new Error('recorded_native_input_public_graph_difference');
  const policy = core.createPublicIdentityPolicy(projected.left.scene.native_kind,
    projected.left.extra_reads, projected.left.scene.content);
  function input(action, reduction) {
    function alias(value) {
      if (value === null) return null;
      const result = reduction.state.aliases[value];
      if (typeof result !== 'string') throw new Error('recorded_native_input_operand_unbound_or_ambiguous');
      return result;
    }
    return { verb: action.verb, label: action.label, subject: alias(action.subject_referent_id ?? null),
      arguments: action.arguments.map(argument => ({ ...argument, referent_id: alias(argument.referent_id) })) };
  }
  const source = original[0], anchoredLeft = { observation: core.modelView(projected.left), input: input(source, left) };
  const matches = actualBasis.snapshot.bound_actions.actions.filter(action => {
    if (action.verb !== source.verb || action.label !== source.label) return false;
    const graph = core.createPublicGraphComparator({
      alias(value, _key, path) {
        if (path === '$.input.subject' || /^\$\.input\.arguments\[\d+\]\.referent_id$/u.test(path))
          return { kind: 'entity', id: value };
        const p = core.normalizePublicPath(path);
        if (p === 'catalog[].choice_id') return { kind: 'action', id: value };
        return policy.relation(path) ? { kind: 'entity', id: value } : null;
      },
      ...core.createPublicOrderingPolicy(projected.left,
        core.canonicalJson(core.modelView(projected.left)) === core.canonicalJson(core.modelView(projected.right)))
    });
    try {
      graph.compareWhole(anchoredLeft, { observation: core.modelView(projected.right), input: input(action, right) });
      return true;
    } catch (error) {
      if (error instanceof core.PublicGraphDifference && !error.incomparable) return false;
      throw error;
    }
  });
  if (matches.length !== 1) throw new Error('recorded_native_input_current_catalog_not_unique');
  return matches[0];
}
