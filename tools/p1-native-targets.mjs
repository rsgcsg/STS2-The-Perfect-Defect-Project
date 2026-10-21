/** A single original timeline, separately qualified supervised points and edges.
 * No online action, inferred missing basis, replay claim or memory policy. */
import { isDeepStrictEqual } from 'node:util';
import { nativeIssueSpan } from './p1-native-issue-spans.mjs';

export const nativeTargetProjection = 'native-timeline-targets-1';
export const nativeTargetSchema = 'sts2.p1/native-targets-1';

export function buildNativeP1Targets({ source, report, successful, strictSegments, boundaries }) {
  const identity = source.manifest.content_identity ?? source.manifest;
  const branch = { session_id: identity.session_id, timeline_id: identity.timeline_id ?? null,
    run_id: report.scope.run_id };
  const limit = report.scope.native_event_range?.through_sequence ?? Infinity;
  const events = source.events.filter(row => row.run_id === branch.run_id && row.sequence <= limit);
  const byEvent = new Map(events.map(row => [row.event_id, row]));
  const capturesByEvent = new Map();
  for (const capture of report.captures) {
    const values = capturesByEvent.get(capture.event_id) ?? [];
    values.push(capture); capturesByEvent.set(capture.event_id, values);
  }
  const spans = [];
  for (const span of report.native_presentations.filter(item => item.exception)) {
    const checkpoints = span.continuity_checkpoints.map(point => ({ boundary: point.boundary,
      source_event_id: point.source_event_id, source_sequence: byEvent.get(point.source_event_id)?.sequence,
      source_public_basis: point.source_public_basis }));
    if (checkpoints.length !== 2 || checkpoints.some(point => point.source_sequence == null))
      throw new Error('native_target_effect_original_endpoints_required');
    spans.push({ span_id: `effect:${span.decision_ids[0]}`, kind: 'non_p1_native_effect',
      effect: span.exception.id, decision_ids: span.decision_ids,
      from_sequence: checkpoints[0].source_sequence, through_sequence: checkpoints[1].source_sequence,
      checkpoints, actual_changes: span.comparison.changes,
      N_label: null, ordinary_p1_transition: false,
      memory_crossing_approved: span.exception.id === 'stop_relic_refresh' });
  }
  for (const issue of [...report.blocking_issues, ...report.pending_decisions]) {
    const decisionId = issue.decision_id ?? issue.native_witness?.decision_id;
    const located = nativeIssueSpan({ events, issue, captures: report.captures, boundaries });
    const references = events.filter(row => decisionId != null && row.decision_id === decisionId
      || row.event_id === located.source_issue_event_id).flatMap(row =>
      ['basis', 'execution_basis', 'successor_basis'].filter(role => row[role])
        .map(role => ({ source_event_id: row.event_id, source_sequence: row.sequence,
          role, source_public_basis: row[role] })));
    if (issue.native_witness?.basis) references.push({ source_event_id: located.source_issue_event_id,
      source_sequence: issue.sequence, role: 'native_witness.basis', source_public_basis: issue.native_witness.basis });
    spans.push({ span_id: `missing:${decisionId ?? 'scope'}:${spans.length}`,
      kind: 'unqualified_native_span', reason: issue.reason, ...located,
      original_references: references, N_label: null, ordinary_p1_transition: false,
      model_input: null, memory_crossing_approved: false });
  }
  spans.sort((a, b) => a.from_sequence - b.from_sequence || a.through_sequence - b.through_sequence);

  const journal = source.journal.filter(row => row.run_id === branch.run_id);
  const terminalCapture = report.captures.findLast(item => item.complete
    && item.observation?.status === 'terminal' && byEvent.get(item.event_id)?.run_id === branch.run_id);
  const endProven = !source.recovery && terminalCapture && report.scope.recording_status === 'closed'
    && !report.scope.native_event_range && journal.filter(row => row.kind === 'run_ended_native').length === 1
    && !journal.some(row => ['run_resumed_native', 'run_abandoned_native', 'run_ended_unproved', 'recording_interrupted'].includes(row.kind));
  const endTarget = endProven ? { branch, source_event_id: terminalCapture.event_id,
    source_public_basis: terminalCapture.public_basis,
    native_end: journal.find(row => row.kind === 'run_ended_native'),
    result: terminalCapture.observation.scene.content.context?.result ?? null } : null;

  // A successful lowering alone does not qualify its information/catalog. The
  // converter also records producer defects which can leave the reduced DTO
  // superficially complete. A missing successor only withholds next, not N.
  const inputIssues = report.blocking_issues.filter(issue => issue.reason !== 'accepted_action_without_complete_successor');
  const points = [], excluded = [], actionsByPoint = new Map();
  for (const action of successful) {
    const capture = report.captures[action.capture];
    const decisionIds = new Set([action.decision_id, ...(action.selector_transaction?.decision_ids ?? [])]);
    const defects = inputIssues.filter(issue => decisionIds.has(issue.decision_id ?? issue.native_witness?.decision_id));
    // N is a local supervised choice. It is not a new episode restart and does
    // not require the global reward/selector commitments to be empty.
    if (defects.length || !capture.complete || !capture.public_basis || capture.observation?.status !== 'decision'
      || !action.choice_id || action.result?.status !== 'completed'
      || capture.observation.catalog.filter(choice => choice.choice_id === action.choice_id).length !== 1) {
      if (action.choice_id) excluded.push({ decision_id: action.decision_id,
        source_sequence: capture.sequence, reason: 'incomplete_or_unqualified_N_input_and_label',
        source_issues: defects.map(issue => issue.reason) });
      continue;
    }
    const point = { point_id: action.decision_id, source_sequence: capture.sequence,
      source_event_id: capture.event_id,
      source_decision_id: action.decision_id.startsWith('D:')
        ? action.selector_transaction.anchor_decision_id : action.decision_id,
      source_public_basis: capture.public_basis, observation: capture.observation,
      chosen: action.choice_id, mask: { N: true, next: false, end: endTarget?.result != null },
      ...(action.selector_transaction ? { data_class: 'D', selector_transaction: action.selector_transaction,
        ...(action.decision_id.startsWith('D:') ? { derived_choice_id: action.decision_id } : {}) } : {}) };
    points.push(point); actionsByPoint.set(point.point_id, action);
  }
  points.sort((a, b) => a.source_sequence - b.source_sequence);
  if (terminalCapture?.public_basis) points.push({ point_id: `terminal:${terminalCapture.event_id}`,
    source_sequence: terminalCapture.sequence, source_event_id: terminalCapture.event_id,
    source_decision_id: null, source_public_basis: terminalCapture.public_basis,
    observation: terminalCapture.observation, chosen: null,
    mask: { N: false, next: false, end: endTarget?.result != null } });
  const pointKey = point => point.derived_choice_id ?? point.source_decision_id ?? `terminal:${point.source_event_id}`;
  const pairKey = (a, b) => JSON.stringify([pointKey(a), pointKey(b)]);
  // Preserve the existing qualified ordinary P1 edges. A newly retained N point
  // cannot inherit the preceding point's next flag simply by being appended.
  const strictEdges = new Set(strictSegments.segments.flatMap(segment => segment.points.slice(0, -1)
    .flatMap((point, index) => point.mask.next ? [pairKey(point, segment.points[index + 1])] : [])));
  const edges = points.slice(0, -1).map((from, index) => {
    const to = points[index + 1], action = actionsByPoint.get(from.point_id);
    const blockers = spans.filter(span => !span.memory_crossing_approved && span.from_sequence < to.source_sequence
      && span.through_sequence > from.source_sequence);
    const exactSuccessor = action?.successor?.public_basis?.sha256 === to.source_public_basis.sha256
      && isDeepStrictEqual(action?.successor?.observation, to.observation);
    const approvedEffects = spans.filter(span => span.memory_crossing_approved
      && span.from_sequence < to.source_sequence && span.through_sequence > from.source_sequence);
    const provenance = strictEdges.has(pairKey(from, to))
      ? approvedEffects.length ? 'qualified_p1_edge_with_approved_reward_return' : 'existing_qualified_p1_edge'
      : exactSuccessor ? 'exact_recorded_complete_successor' : null;
    const eligible = blockers.length === 0 && provenance !== null;
    from.mask.next = eligible;
    return { from_point_id: from.point_id, to_point_id: to.point_id, eligible,
      source_range: { from_sequence: from.source_sequence, through_sequence: to.source_sequence },
      source_public_bases: { before: from.source_public_basis, after: to.source_public_basis },
      proof: eligible ? provenance : null,
      reasons: blockers.length ? [...new Set(blockers.map(span => span.effect ?? span.reason))]
        : eligible ? [] : ['no_qualified_P1_edge_for_independently_retained_N_point'],
      blocking_span_ids: blockers.map(span => span.span_id), memory_crossing_approved: eligible };
  });
  return { schema: nativeTargetSchema, projection_id: nativeTargetProjection,
    source_bundle: source.bundle, source_content_id: report.source_content_id,
    source_manifest_sha256: source.verifiedManifestSha256 ?? report.source_manifest_sha256, branch,
    scope: report.scope, declared_origin: report.declared_origin,
    human_origin_attestation: report.human_origin_attestation,
    profile: 'p1-global-r1', revision: points[0]?.observation.revision ?? null,
    projection_claims: report.projection_claims,
    timeline: { branch, same_original_order: true,
      events: events.map(row => ({ source_event_id: row.event_id, source_sequence: row.sequence,
        kind: row.kind, ...(row.phase ? { phase: row.phase } : {}),
        ...(row.decision_id ? { decision_id: row.decision_id } : {}),
        ...(row.action_witness_id ? { action_witness_id: row.action_witness_id } : {}),
        raw_member: 'raw/p1-native-events.jsonl',
        public_bases: (capturesByEvent.get(row.event_id) ?? []).map(capture => ({
          role: capture.origin, source_public_basis: capture.public_basis,
          reduced_complete: capture.complete, missing: capture.missing,
          usage: 'source_context_only_not_a_model_input' })) })),
      journal, spans },
    points, edges, end_target: endTarget, excluded_N_candidates: excluded,
    source_integrity_verified: true, p1_action_chain_complete: spans.length ? false : 'not_established',
    memory_continuity: 'qualified_edges_only_missing_facts_remain_barriers', training_loader_consumed: false,
    nonclaims: ['not_lossless_p1_action_chain', 'not_memory_across_missing_facts',
      'not_host_equivalence', 'not_research_admission'] };
}
