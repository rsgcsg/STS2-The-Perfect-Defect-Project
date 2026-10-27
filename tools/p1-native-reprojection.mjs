/** Explicit offline r1.7 derivation over verified immutable originals. The old
 * projection supplies the fixed point/N denominator; failed new inputs retain
 * their slots and reasons without becoming labels or whole-run qualification. */
export function reprojectNativeSemanticOriginals(source, sdk, core, options, convert, targetProjection) {
  const requestedTargets = Boolean(options.includeTargets);
  const inner = { ...options, p1Revision: undefined, includeTargets: true, targetProjection };
  const original = convert(source, sdk, core, inner);
  const nextCore = { ...core, createSession: (episode, host) => core.createSession(episode, host, 'r1.7') };
  const current = convert(source, sdk, nextCore, inner);
  const byId = new Map(current.target_output.points.map(point => [point.point_id, point]));
  const currentCaptures = new Map(current.captures.map(capture =>
    [JSON.stringify([capture.event_id, capture.public_basis?.sha256]), capture]));
  const occurrenceKey = observation => JSON.stringify([observation.envelope.source_snapshot_id, observation.envelope.occurrence]);
  const targetByOccurrence = new Map(original.target_output.points.map(point => [occurrenceKey(point.observation), point]));
  const captureByOccurrence = new Map(original.captures.filter(capture => capture.observation && capture.public_basis)
    .map(capture => [occurrenceKey(capture.observation), capture]));
  // A complete trajectory also retains its final non-label boundary. When the
  // old conversion is partial, its qualified native targets remain the known N
  // denominator; we do not invent the missing old trajectory.
  const sourcePoints = original.trajectory ? original.trajectory.points.map((point, index) => {
    const target = targetByOccurrence.get(occurrenceKey(point.observation));
    if (target && point.chosen === target.chosen) return target;
    const capture = captureByOccurrence.get(occurrenceKey(point.observation));
    if (!capture) throw new Error('original_trajectory_point_has_no_exact_basis');
    return { point_id: `boundary:${index}:${capture.event_id}`, source_event_id: capture.event_id,
      source_sequence: capture.sequence, source_public_basis: capture.public_basis, observation: point.observation,
      chosen: point.chosen, mask: { N: point.chosen !== null, next: false, end: false } };
  }) : original.target_output.points;
  const points = sourcePoints.map((point, index) => {
    const projected = byId.get(point.point_id);
    const capture = currentCaptures.get(JSON.stringify([point.source_event_id, point.source_public_basis.sha256]));
    const inputComplete = capture?.complete === true;
    return { index, point_id: point.point_id, source_event_id: point.source_event_id,
      source_sequence: point.source_sequence, source_public_basis: point.source_public_basis,
      source_revision: point.observation.revision, source_chosen: point.chosen, source_mask: point.mask,
      revision: 'r1.7', observation: projected?.observation ?? capture?.observation ?? null,
      chosen: projected?.chosen ?? null, mask: projected?.mask ?? { N: false, next: false, end: false },
      input_complete: inputComplete, missing: inputComplete ? [] : capture?.missing ?? ['original_point_has_no_reprojection_capture'],
      settled_relic_counter_projection: capture?.settled_relic_counter_projection ?? null };
  });
  const sourceN = points.filter(point => point.source_mask.N).length;
  const ambiguous = current.captures.filter(capture => capture.settled_relic_counter_projection?.facts
    .some(fact => fact.recovery === 'ambiguous_missing_witness'));
  current.p1_revision = 'r1.7';
  current.settled_relic_reprojection = {
    schema: 'sts2.p1/native-settled-relic-reprojection-1', revision: 'r1.7',
    source_content_id: original.source_content_id, source_manifest_sha256: source.verifiedManifestSha256 ?? original.source_manifest_sha256,
    source_session_id: original.source_session_id,
    denominator: { source_revision: 'r1.5', points: points.length, N: sourceN,
      domain: original.trajectory ? 'original_complete_trajectory' : 'original_qualified_native_targets',
      original_trajectory_points: original.trajectory?.points.length ?? null,
      original_target_points: original.target_output.points.length,
      definition: 'fixed original trajectory slots (or known native targets when no complete old trajectory); original N remains the denominator',
      source_accepted: original.accepted_accounting.accepted, source_conversion_complete: original.complete },
    reprojected: { complete_inputs: points.filter(point => point.input_complete).length,
      eligible_N: points.filter(point => point.mask.N).length, ambiguous_captures: ambiguous.length,
      ambiguous_input_points: points.filter(point => point.settled_relic_counter_projection?.facts
        .some(fact => fact.recovery === 'ambiguous_missing_witness')).length },
    recovery_rules: { native_same_capture: 'PB3 exact snapshot/session/build/rule witness',
      legacy_same_snapshot_public_rule: 'KUSARIGAMA 0/1/2; LASTING_CANDY 0/1 only',
      ambiguous_missing_witness: 'KUSARIGAMA 3 or LASTING_CANDY 2 without native witness; no inverse, no later frame, no zero default' },
    points, complete: current.complete && points.every(point => point.input_complete),
    original_bytes_modified: false, runtime_or_training_defaults_changed: false,
    strict_cross_host_comparison: 'not_run'
  };
  if (points.some(point => !point.input_complete)) {
    current.complete = false; current.whole_run_recording_complete = false;
    // The old final boundary still owns a slot; a shorter new tail is not a
    // complete replacement trajectory. Partial observations remain in receipts.
    current.trajectory = null;
  }
  current.projection_claims = { ...current.projection_claims, revision: 'r1.7',
    settled_relic_counters: { mechanisms: ['KUSARIGAMA', 'LASTING_CANDY'],
      witness_metadata_is_model_input: false, source_bytes_modified: false,
      ambiguous_originals_are_complete: false } };
  if (current.trajectory) {
    current.trajectory.projection_claims = current.projection_claims;
    current.trajectory.revision = 'r1.7';
  }
  if (current.target_output) {
    current.target_output.projection_claims = current.projection_claims;
    current.target_output.revision = "r1.7";
  }
  if (current.segment_output) current.segment_output.revision = "r1.7";
  if (!requestedTargets) delete current.target_output;
  return current;
}
