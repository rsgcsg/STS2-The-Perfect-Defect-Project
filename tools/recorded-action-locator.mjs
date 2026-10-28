/** Diagnostic-only location of an already recorded native input. Whole-state
 * equality is measured separately. Return one CURRENT bound action; never build
 * operands or choose between indistinguishable candidates. */
import { isDeepStrictEqual } from 'node:util';
import * as core from '../components/connector/sdk/typescript/dist/p1Global/index.js';
const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
const stableKeys = ['definition_id', 'character_definition_id', 'option_id', 'combat_id',
  'col', 'row', 'inventory_index', 'slot', 'index', 'kind', 'point_type'];
const transient = new Set(['entity_id', 'referent_id', 'group_id', 'screen_entity_id',
  'room_entity_id', 'interaction_id', 'bound_action_id', 'snapshot_id', 'read_id']);
function publicObjects(basis) {
  const result = new Map();
  function add(id, value, path) {
    if (typeof id !== 'string') return;
    const entry = result.get(id) ?? { id, objects: [], roles: new Set(), paths: [] };
    entry.objects.push(value); entry.paths.push(path); result.set(id, entry);
  }
  function visit(value, path) {
    if (Array.isArray(value)) value.forEach((child, i) => visit(child, `${path}[${i}]`));
    else if (object(value)) {
      if (typeof value.entity_id === 'string') add(value.entity_id, value, path);
      for (const [key, child] of Object.entries(value)) visit(child, `${path}.${key}`);
    }
  }
  const snap = basis.snapshot;
  visit(snap.persistent, '$.persistent'); visit(snap.interaction?.content, '$.interaction.content');
  for (const read of basis.reads ?? []) if (read.response?.content) visit(read.response.content, `$.read:${read.kind}`);
  for (const ref of snap.referents ?? []) {
    add(ref.referent_id, ref.properties ?? {}, '$.referents');
    result.get(ref.referent_id)?.roles.add(ref.role);
  }
  return result;
}
function facts(entry) {
  const result = {};
  for (const key of stableKeys) {
    const values = [...new Set(entry.objects.map(value => value[key]).filter(value =>
      value !== undefined && value !== null && typeof value !== 'object'))];
    if (values.length === 1) result[key] = values[0];
  }
  return result;
}
function literalFingerprint(entry) {
  function clean(value) {
    if (Array.isArray(value)) return value.map(clean);
    if (!object(value)) return value;
    return Object.fromEntries(Object.entries(value).filter(([key]) => !transient.has(key)).map(([key, child]) => [key, clean(child)]));
  }
  return entry.objects.map(clean).filter(x => Object.keys(x).length > 0);
}
/** Public entity bindings persist across this diagnostic episode. */
export class RecordedActionLocator {
  constructor() { this.forward = new Map(); this.reverse = new Map(); }
  bind(left, right) {
    if (this.forward.has(left) && this.forward.get(left) !== right || this.reverse.has(right) && this.reverse.get(right) !== left)
      throw new Error('recorded_action_identity_conflict');
    this.forward.set(left, right); this.reverse.set(right, left);
  }
  candidates(leftId, left, right) {
    if (leftId == null) return [null];
    if (this.forward.has(leftId)) return right.has(this.forward.get(leftId)) ? [this.forward.get(leftId)] : [];
    const source = left.get(leftId); if (!source) return [];
    const required = facts(source), keys = Object.keys(required);
    let matches = [...right.values()].filter(candidate => {
      if (this.reverse.has(candidate.id)) return false;
      const actual = facts(candidate);
      // Strong public identity facts, with shared domains. Incidental schema
      // omissions cannot erase a recorded definition or coordinate.
      return keys.length > 0 && keys.every(key => actual[key] === required[key]);
    });
    if (keys.length === 0) {
      const originals = literalFingerprint(source);
      matches = [...right.values()].filter(candidate => !this.reverse.has(candidate.id)
        && literalFingerprint(candidate).some(value => originals.some(original => isDeepStrictEqual(original, value))));
    }
    if (matches.length > 1) {
      // A declared visible hand order is an operand witness; deck transport order
      // and referent enumeration are not. No arbitrary array-index fallback.
      const hand = source.paths.find(path => /\.context\.player\.hand\[\d+\]$/u.test(path));
      if (hand) {
        const positioned = matches.filter(candidate => candidate.paths.includes(hand));
        if (positioned.length === 1) matches = positioned;
      }
    }
    return matches.map(value => value.id);
  }
  observe(referenceBasis, actualBasis) {
    const left = publicObjects(referenceBasis), right = publicObjects(actualBasis);
    const proposals = [...left.keys()].filter(id => !this.forward.has(id)).map(id =>
      [id, this.candidates(id, left, right)]);
    for (const [id, matches] of proposals) {
      if (matches.length === 1 && proposals.filter(([, other]) => other.includes(matches[0])).length === 1)
        this.bind(id, matches[0]);
    }
    return { left, right };
  }
  locate(witness, actualBasis) {
    if (!['finished', 'cancelled'].includes(witness.original_terminal_phase))
      throw new Error('recorded_action_original_unknown_or_unfinished');
    const original = witness.basis;
    const native = witness.native_input;
    const catalog = actualBasis.snapshot.bound_actions;
    if (catalog?.status !== 'complete' || catalog.materialized_count !== catalog.actions?.length
        || catalog.total_count !== catalog.actions.length)
      throw new Error('recorded_action_current_catalog_incomplete');
    const source = witness.bound_action ?? (native ? { verb: native.verb,
      subject_referent_id: native.subject_referent_id ?? null,
      arguments: Object.entries(native.arguments).map(([role, referent_id]) => ({ role, referent_id })), label: native.label } : null);
    if (!source || !original) throw new Error('recorded_action_original_binding_missing');
    if (original.snapshot.interaction.kind !== actualBasis.snapshot.interaction.kind)
      throw new Error('recorded_action_original_surface_unavailable');
    const originals = original.snapshot.bound_actions?.actions ?? [];
    if (witness.bound_action) {
      if (!originals.some(input => isDeepStrictEqual(input, source)))
        throw new Error('recorded_action_not_original_catalog_member');
    } else {
      const space = witness.original_execution_action_space;
      const selected = space?.actions?.filter(input => input.key === native.action_key) ?? [];
      if (space?.status !== 'captured' || space.observed_membership !== 'exact_once' || space.observed_match_count !== 1
          || space.observed_action_key !== native.action_key || selected.length !== 1 || selected[0].verb !== native.verb
          || (selected[0].subject_referent_id ?? null) !== (native.subject_referent_id ?? null)
          || !isDeepStrictEqual(selected[0].arguments, native.arguments))
        throw new Error('recorded_action_original_native_catalog_unproven');
    }
    const { left, right } = this.observe(original, actualBasis);
    const subject = this.candidates(source.subject_referent_id ?? null, left, right);
    const args = source.arguments.map(arg => ({ role: arg.role, ids: this.candidates(arg.referent_id, left, right) }));
    let matches = actualBasis.snapshot.bound_actions.actions.filter(input => input.verb === source.verb
      && subject.includes(input.subject_referent_id ?? null) && input.arguments.length === args.length
      && args.every(arg => input.arguments.some(actual => actual.role === arg.role && arg.ids.includes(actual.referent_id))));
    if (matches.length > 1 && typeof source.label === 'string') matches = matches.filter(input => input.label === source.label);
    let semanticSubjectless = false;
    if (matches.length === 0 && witness.bound_action && source.arguments.length === 0) {
      // SDK explicitly declares some control operands subjectless (shop.open,
      // for example). Reference may bind the visible entry while Managed binds
      // its unique control without a subject. Reuse that existing declaration;
      // do not erase a card/target operand just because a label happens to match.
      const a = core.reduce(core.createSession('recorded-input-control-left', 'reference'), original);
      const b = core.reduce(core.createSession('recorded-input-control-right', 'managed'), actualBasis);
      if (a.complete && b.complete) {
        const kinds = new Set(a.observation.catalog.filter(choice => choice.mapped && choice.subject === null && choice.target === null
          && a.lowering[choice.choice_id]?.first?.bound_action_id === source.bound_action_id).map(choice => choice.kind));
        const ids = new Set(b.observation.catalog.filter(choice => choice.mapped && choice.subject === null && choice.target === null
          && kinds.has(choice.kind)).map(choice => b.lowering[choice.choice_id]?.first?.bound_action_id));
        matches = actualBasis.snapshot.bound_actions.actions.filter(input => ids.has(input.bound_action_id)
          && input.verb === source.verb && input.arguments.length === 0);
        semanticSubjectless = matches.length === 1;
      }
    }
    if (matches.length !== 1) throw new Error(matches.length ? 'recorded_action_current_catalog_ambiguous' : 'recorded_action_not_executable');
    const input = matches[0];
    if (!semanticSubjectless && source.subject_referent_id != null) this.bind(source.subject_referent_id, input.subject_referent_id);
    for (const arg of source.arguments) this.bind(arg.referent_id, input.arguments.find(item => item.role === arg.role).referent_id);
    return { input, evidence: { kind: 'recorded_public_operands_to_unique_current_binding',
      original_action_id: source.bound_action_id ?? null, original_native_input_key: native?.action_key ?? null, current_action_id: input.bound_action_id,
      sdk_subjectless_first_step: semanticSubjectless, whole_observation_equivalence_claim: false } };
  }
}
