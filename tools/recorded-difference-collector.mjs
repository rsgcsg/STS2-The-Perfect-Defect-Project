/** Exhaustive diagnostic walk; strict equivalence comparator is unchanged.
 * Bind identities only from unique public declarations. Ambiguous unordered
 * groups retain both complete groups rather than inventing a leaf pairing. */
import { isDeepStrictEqual } from 'node:util';
import * as core from '../components/connector/sdk/typescript/dist/p1Global/index.js';
import { comparePublicPointBindings } from '../components/policy-runtime/dist/p1-global/compare.js';
const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
const keys = ['definition_id', 'character_definition_id', 'option_id', 'col', 'row', 'kind',
  'slot', 'inventory_index', 'combat_id', 'index', 'zone'];
const norm = core.normalizePublicPath;
function declarations(observation) {
  const policy = core.createPublicIdentityPolicy(observation.scene.native_kind, observation.extra_reads, observation.scene.content);
  const result = new Map();
  function walk(value, path, parent) {
    if (typeof value === 'string' && policy.relation(path) === 'declared') {
      const entry = result.get(value) ?? { id: value, objects: [], paths: new Set() };
      entry.objects.push(parent); entry.paths.add(norm(path)); result.set(value, entry);
    } else if (Array.isArray(value)) value.forEach((v, i) => walk(v, `${path}[${i}]`, value));
    else if (object(value)) for (const [key, child] of Object.entries(value)) walk(child, `${path}.${key}`, value);
  }
  walk(core.modelView(observation), '$.observation', null);
  return { entries: result, policy };
}
function identityFacts(entry) {
  const facts = {};
  for (const key of keys) {
    const values = [...new Set(entry.objects.map(x => x?.[key]).filter(x => x !== undefined && x !== null && typeof x !== 'object'))];
    if (values.length === 1) facts[key] = values[0];
  }
  return facts;
}
export function differenceCategory(path, phase = 'observation') {
  if (phase === 'result' || /terminal|outcome|reward_page_left|declined_reward_groups/u.test(path)) return 'reward_or_result';
  if (/showing_upgrade_previews/u.test(path)) return 'upgrade_toggle_in_model_input';
  if (/catalog|legal|playable|can_play|can_skip|enabled|available|availability/u.test(path)) return 'legal_actions_or_availability';
  if (/selectable_.*entity_ids|deselectable_.*entity_ids|selected_.*entity_ids/u.test(path)) return 'selector_membership_or_order';
  if (/\.map\.|map_|next_options/u.test(path)) return 'map_state_or_source';
  if (/counter/u.test(path)) return 'relic_counter';
  if (/reward|gold|potion/u.test(path)) return 'reward_or_resources';
  if (/combat|piles|hand|enemy|enemy_id|hp|block|energy|orb/u.test(path)) return 'combat_state_or_order';
  if (/card|preview|description|keywords/u.test(path)) return 'card_or_text_model_input';
  if (/commitments/u.test(path)) return 'protocol_commitment';
  return 'other_model_input';
}
export function collectObservationDifferences(reference, actual) {
  const left = declarations(reference), right = declarations(actual), forward = new Map(), reverse = new Map();
  function bind(a, b) {
    if (forward.has(a) && forward.get(a) !== b || reverse.has(b) && reverse.get(b) !== a) return false;
    forward.set(a, b); reverse.set(b, a); return true;
  }
  const strict = comparePublicPointBindings(reference, actual);
  const rawChanged = core.canonicalJson(core.modelView(reference)) !== core.canonicalJson(core.modelView(actual));
  if (strict.verdict === 'equal') return { differences: [], differences_complete: true,
    strict, entity_bindings: strict.bindings, raw_model_input_changed: rawChanged,
    model_input_frame_check_required: rawChanged };

  // A unique proposal in BOTH directions is evidence. Greedy consumption of an
  // ambiguous duplicate must not turn the remaining duplicate into a false match.
  const proposals = [...left.entries.values()].map(entry => {
    const facts = identityFacts(entry), names = Object.keys(facts);
    const candidates = [...right.entries.values()].filter(other => {
      if (entry.paths.size !== other.paths.size || ![...entry.paths].every(path => other.paths.has(path))) return false;
      const actual = identityFacts(other);
      return names.every(key => actual[key] === facts[key]);
    });
    return [entry.id, candidates.map(x => x.id)];
  });
  for (const [id, candidates] of proposals) if (candidates.length === 1
    && proposals.filter(([, values]) => values.includes(candidates[0])).length === 1) bind(id, candidates[0]);
  const actions = new Map(), reverseActions = new Map();
  const canonicalSubject = (id, side) => id == null ? null : side === 'left' ? id : reverse.get(id) ?? `unbound-right:${id}`;
  const actionProposals = reference.catalog.map(choice => [choice.choice_id, actual.catalog.filter(other =>
    choice.kind === other.kind && canonicalSubject(choice.subject, 'left') === canonicalSubject(other.subject, 'right')
    && canonicalSubject(choice.target, 'left') === canonicalSubject(other.target, 'right')
    && choice.option === other.option).map(x => x.choice_id)]);
  for (const [id, candidates] of actionProposals) if (candidates.length === 1
    && actionProposals.filter(([, ids]) => ids.includes(candidates[0])).length === 1) {
    actions.set(id, candidates[0]); reverseActions.set(candidates[0], id);
  }
  const ordering = core.createPublicOrderingPolicy(reference, false), differences = [];
  let work = 0, complete = true;
  function add(path, a, b, reason, leftPresent = true, rightPresent = true) {
    const category = differenceCategory(path);
    differences.push({ path, left: a ?? null, right: b ?? null, left_present: leftPresent, right_present: rightPresent,
      reason, classification: 'blocking', category, affects_model_input: true,
      existing_comparison_exception: category === 'upgrade_toggle_in_model_input' ? 'M060' : null });
  }
  function primitive(value, path, side) {
    if (typeof value !== 'string') return value;
    if (norm(path) === 'catalog[].choice_id') return side === 'left' ? `choice:${value}`
      : `choice:${reverseActions.get(value) ?? `unbound-right:${value}`}`;
    if ((side === 'left' ? left.policy : right.policy).relation(path)) return side === 'left'
      ? `entity:${value}` : `entity:${reverse.get(value) ?? `unbound-right:${value}`}`;
    return value;
  }
  function identityOf(item, path, side) {
    if (!object(item)) return primitive(item, path, side);
    if (item.kind && object(item.content)) {
      const contentId = identityOf(item.content, `${path}.content`, side);
      if (contentId !== null) return `${item.kind}:${contentId}`;
    }
    if (typeof item.choice_id === 'string' && /catalog/u.test(path)) return primitive(item.choice_id, `${path}.choice_id`, side);
    if (typeof item.entity_id === 'string' && (side === 'left' ? left.policy : right.policy).relation(`${path}.entity_id`))
      return primitive(item.entity_id, `${path}.entity_id`, side);
    const stable = Object.fromEntries(keys.filter(key => item[key] !== undefined).map(key => [key, item[key]]));
    if (Object.keys(stable).length) return core.canonicalJson(stable);
    if (item.kind !== undefined) return `kind:${item.kind}`;
    return null;
  }
  function literalSame(a, b, path) {
    if (!isDeepStrictEqual(a, b)) return false;
    function conflict(value, at) {
      if (typeof value === 'string') {
        if (norm(at) === 'catalog[].choice_id') return actions.has(value) && actions.get(value) !== value;
        return left.policy.relation(at) && forward.has(value) && forward.get(value) !== value;
      }
      if (Array.isArray(value)) return value.some((child, i) => conflict(child, `${at}[${i}]`));
      return object(value) && Object.entries(value).some(([key, child]) => conflict(child, `${at}.${key}`));
    }
    return !conflict(a, path);
  }
  function walk(a, b, path, parentA, parentB) {
    if (++work > 500000) { complete = false; add(path, a, b, 'diagnostic_comparison_work_bound'); return; }
    if (literalSame(a, b, path)) {
      // Literal arrays/objects already preserve all visible values and edges.
      // Known cross-frame alias conflicts remain covered by the retained formal
      // graph verdict, while unchanged objects must not become false diffs.
      return;
    }
    if (!object(a) && !Array.isArray(a) && !object(b) && !Array.isArray(b)) {
      if (!isDeepStrictEqual(primitive(a, path, 'left'), primitive(b, path, 'right'))) {
        if (typeof a === 'string' && typeof b === 'string' && (left.policy.relation(path) || right.policy.relation(path))
            && !forward.has(a) && !reverse.has(b)) {
          complete = false; add(path, a, b, 'entity_alignment_ambiguous');
        } else add(path, a, b, 'value_changed');
      }
      return;
    }
    if (Array.isArray(a) && Array.isArray(b)) {
      // Ordering policy expects indexed array parents normalized to [] but keeps
      // the $.observation root. Reconstruct that exact owner path below.
      const ownerPath = path.replace(/\[\d+\]/gu, '[]');
      const start = ordering.unorderedSuffix(ownerPath) ?? (ordering.unordered(ownerPath, parentA) ? 0 : null);
      if (start !== null) {
        if (a.length !== b.length) add(path, { count: a.length }, { count: b.length }, 'collection_multiplicity_changed');
        for (let i = 0; i < start; i++) walk(a[i], b[i], `${path}[${i}]`, a, b);
        const remaining = new Set(b.slice(start).map((_, i) => i + start));
        for (let i = start; i < a.length; i++) {
          const id = identityOf(a[i], `${path}[${i}]`, 'left');
          const matches = [...remaining].filter(j => id !== null && id === identityOf(b[j], `${path}[${j}]`, 'right'));
          if (matches.length !== 1) {
            complete = false; add(path, a.slice(start), b.slice(start), 'unordered_alignment_ambiguous'); return;
          }
          const j = matches[0]; remaining.delete(j); walk(a[i], b[j], `${path}[${i}]`, a, b);
        }
        for (const j of remaining) add(`${path}[+${j}]`, undefined, b[j], 'collection_member_added', false, true);
      } else {
        for (let i = 0; i < Math.max(a.length, b.length); i++) {
          if (i >= a.length || i >= b.length) add(`${path}[${i}]`, a[i], b[i], 'collection_member_missing', i < a.length, i < b.length);
          else walk(a[i], b[i], `${path}[${i}]`, a, b);
        }
      }
      return;
    }
    if (object(a) && object(b)) {
      for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
        const p = core.publicFieldPath(path, key);
        if (!Object.hasOwn(a, key) || !Object.hasOwn(b, key)) add(p, a[key], b[key], 'field_missing', Object.hasOwn(a, key), Object.hasOwn(b, key));
        else walk(a[key], b[key], p, a, b);
      }
      return;
    }
    add(path, a, b, 'type_changed');
  }
  walk(core.modelView(reference), core.modelView(actual), '$.observation', null, null);
  // Keep any graph-only discrepancy not resolved by the field walk. Never
  // reclassify a failed formal graph comparison as equal after local alignment.
  if (!differences.length) {
    complete = false; add(strict.difference?.path ?? '$.observation', core.modelView(reference), core.modelView(actual),
      strict.difference?.reason ?? 'unresolved_global_relation_difference');
  }
  return { differences, differences_complete: complete, strict,
    entity_bindings: [...forward].map(([left, right]) => ({ kind: 'entity', left, right })), raw_model_input_changed: rawChanged, model_input_frame_check_required: false };
}

/** Display-only native preferences are reported only when the actual P1 model
 * view omits the field on BOTH sides. M060 is therefore still blocking whenever
 * that Boolean is present in P1; the old strict-comparison exception is a tag. */
export function collectNativeDisplayDifferences(referenceBasis, actualBasis, reference, actual) {
  const a = referenceBasis.snapshot.interaction.content.surface, b = actualBasis.snapshot.interaction.content.surface;
  if (a?.kind !== b?.kind) return [];
  const left = reference?.scene.content?.surface ?? {}, right = actual?.scene.content?.surface ?? {};
  return ['showing_upgrade_previews', 'is_peeking', 'can_toggle_upgrade_view'].flatMap(key =>
    typeof a?.[key] === 'boolean' && typeof b?.[key] === 'boolean' && a[key] !== b[key]
      && !Object.hasOwn(left, key) && !Object.hasOwn(right, key)
      ? [{ path: `$.native.surface.${key}`, left: a[key], right: b[key], reason: 'native_ui_preference_omitted_from_both_model_views',
        classification: 'display', category: 'display_only_native_preference', affects_model_input: false,
        affects_legal_p1_actions: false, repair: 'register_only' }] : []);
}
