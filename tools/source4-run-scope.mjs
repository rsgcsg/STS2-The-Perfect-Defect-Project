/** Pure interpretation of already verified Source4 lifecycle rows. No native or
 * P1 authority: this selects a recorded run, never repairs or removes originals. */
const stream = kind => kind === 'epoch' ? 'source-attachment-epochs.jsonl' : 'source-boundaries.jsonl';
function reference(kind, row) {
  return { stream: stream(kind), sequence: row.sequence, epoch_id: row.epoch_id ?? row.position.epoch_id,
    ...(kind === 'epoch' ? { starting_position: row.starting_position, predecessor_seal: row.predecessor_seal }
      : { position: row.position }), after_input_ordinal: row.after_input_ordinal,
    transition: row.transition };
}
const index = position => BigInt(position.publication_index);
function beforeOrAt(entry, boundary) {
  const at = index(entry.position), cut = index(boundary.position);
  return at < cut || at === cut && (entry.kind === 'source_observation'
    || BigInt(entry.row.input_prefix_ordinal) <= BigInt(boundary.after_input_ordinal));
}

export function deriveSourceRunScope(source, continuityId) {
  if (typeof continuityId !== 'string' || !continuityId) throw new Error('source4_run_continuity_required');
  const epochs = source.epochs.filter(epoch => epoch.context.game_continuity_id === continuityId);
  if (epochs.length === 0) throw new Error('source4_run_continuity_not_found');
  if (epochs.length !== 1) throw new Error('source4_run_continuity_not_unique');
  const epoch = epochs[0];
  const boundaries = source.boundaries.filter(row => row.position.epoch_id === epoch.epoch_id);
  const exact = kind => boundaries.filter(row => row.kind === kind && row.transition?.kind === kind
    && row.transition.previous_game_continuity_id === continuityId && row.transition.game_continuity_id === continuityId);
  const launches = exact('launch'), terminals = exact('terminal');
  const setup = epoch.transition?.kind === 'setup_handoff'
    && epoch.transition.game_continuity_id === continuityId ? epoch : null;
  const cleanup = source.epochs.find(row => row.previous_epoch_id === epoch.epoch_id && row.transition?.kind === 'cleanup');
  const close = source.boundaries.find(row => row.kind === 'close');
  return { epoch, launches, terminals, setup, boundaries,
    report: { kind: 'selected_native_run_p1_window', game_continuity_id: continuityId, epoch_id: epoch.epoch_id,
      epoch: reference('epoch', epoch), setup: setup ? reference('epoch', setup) : null,
      launches: launches.map(row => reference('boundary', row)),
      terminals: terminals.map(row => reference('boundary', row)),
      end: cleanup ? reference('epoch', cleanup) : close ? reference('boundary', close) : null,
      end_reason: cleanup ? 'original_native_cleanup' : close?.kind ?? 'verified_source_close',
      natural_terminal_observed: false } };
}

/** The caller supplies entries in original epoch/publication/input-prefix order.
 * A missing or possibly active basis starts the retained window conservatively;
 * a later null HUD cannot erase that earlier uncertainty. */
export function classifySourceRunEntry(scope, entry, original, entered) {
  const epochId = entry.row.epoch_id;
  if (epochId !== scope.epoch.epoch_id) return { entered, disposition: 'outside',
    reason: 'original_epoch_has_another_run_continuity', evidence: { selected_epoch: scope.report.epoch, epoch_id: epochId } };
  const inside = reason => ({ entered: true, disposition: original ? 'inside' : 'unresolved', reason,
    evidence: { selected_epoch: scope.report.epoch, public_basis: entry.row.captured_parts.public_basis } });
  if (!original) return inside('selected_run_original_public_basis_unproven');
  if (entered || original.snapshot.persistent?.content?.scope === 'active_single_player_run')
    return inside('selected_run_active_public_facts_or_retained_continuation');
  const input = entry.kind === 'source_input' ? entry.row : null;
  const complete = original.capture_status === 'complete' && original.missing.length === 0
    && original.snapshot.completeness.status === 'complete' && original.snapshot.completeness.missing.length === 0
    && original.snapshot.bound_actions.status === 'complete' && entry.row.gap_after_index == null;
  // PersistentVisibleStateReader emits null without missing only for no active
  // run. Active HUD failures retain persistent_visible_state missing. Require
  // this negative public fact AND the original native Launch cut, not a scene
  // name, epoch label or the first row that happens to reduce successfully.
  const launch = scope.launches.length === 1 ? scope.launches[0] : null;
  const beforeTerminal = scope.terminals.every(row => index(entry.position) < index(row.position));
  if (complete && original.snapshot.persistent === null && launch && beforeOrAt(entry, launch) && beforeTerminal
    && (!input || input.outcome.delivery === 'delivered' && input.outcome.mapping_status === 'exact'
      && input.basis_order.status === 'native_prefix_frozen'))
    return { entered: false, disposition: 'outside', reason: 'original_prelaunch_public_state_has_no_active_run',
      evidence: { selected_epoch: scope.report.epoch, launch: reference('boundary', launch),
        public_basis: entry.row.captured_parts.public_basis } };
  return inside('selected_run_state_cannot_be_excluded_from_p1_window');
}

/** User-approved startup interpretation is scoped to exact native lifecycle
 * originals. It never completes a partial basis or changes the P1 reducer. */
export function deriveSourceStartupContext(scope, entries, isCompleteDecision) {
  const selected = entries.filter(entry => entry.row.epoch_id === scope.epoch.epoch_id);
  const closingIndex = selected.findIndex(entry => isCompleteDecision(entry)
    && (entry.kind !== 'source_input' || entry.row.basis_order.status === 'native_prefix_frozen'));
  const prefix = closingIndex < 0 ? selected : selected.slice(0, closingIndex);
  const waiting = prefix.filter(entry => {
    const original = entry.original, snapshot = original?.snapshot;
    return snapshot?.status === 'settling' && snapshot.interaction.kind === 'no_action'
      && snapshot.interaction.stage === 'settling' && snapshot.interaction.content.surface.reason === 'settling';
  });
  const inputs = waiting.length ? prefix.filter(entry => entry.kind === 'source_input') : [];
  const closing = closingIndex < 0 ? null : selected[closingIndex];
  const launch = scope.launches.length === 1 ? scope.launches[0] : null;
  const first = waiting[0];
  const lifecycle = !!scope.setup && !!launch && !!first
    && index(first.position) >= index(scope.epoch.initial_position)
    && beforeOrAt(first, launch)
    && (closing === null || !beforeOrAt(closing, launch)
      || index(closing.position) === index(launch.position));
  const ref = entry => ({ source_kind: entry.kind,
    source_ref: entry.kind === 'source_input' ? entry.row.input_id : entry.position.publication_index,
    position: entry.position, public_basis: entry.row.captured_parts.public_basis });
  const allowed = entry => entry.kind === 'source_observation' && entry.row.gap_after_index == null
    && (entry.row.missing_reason == null || entry.row.missing_reason === 'source_capture_incomplete')
    && entry.original.missing.every(value => value === 'snapshot:partial')
    && entry.original.snapshot.completeness.missing.every(value => value === 'persistent_visible_state')
    && entry.original.snapshot.bound_actions.status === 'complete'
    && entry.original.snapshot.bound_actions.total_count === 0
    && entry.original.snapshot.bound_actions.actions.length === 0;
  const contexts = new Set(lifecycle && closing ? waiting.filter(allowed) : []);
  const frames = waiting.length ? prefix.filter(entry => entry.kind === 'source_observation') : [];
  const originalStart = frames[0]?.row.captured_parts.public_basis?.captured_at ?? first?.row.captured_parts.public_basis?.captured_at ?? null;
  const originalEnd = closing?.row.captured_parts.public_basis?.captured_at ?? null;
  const missing = waiting.length === 0 ? [] : [
    ...(!lifecycle ? ['startup_native_setup_and_launch_not_proven'] : []),
    ...(!closing ? ['startup_complete_decision_not_observed'] : []),
    ...(inputs.length ? ['startup_contains_player_input'] : []),
    ...(waiting.some(entry => !allowed(entry)) ? ['startup_original_information_error_or_gap'] : []),
  ];
  return { contexts, blockedInputs: new Set(inputs),
    report: { kind: 'native_setup_launch_nondecision_context', required: waiting.length > 0,
      complete: missing.length === 0, frame_count: frames.length,
      duration_ms: originalStart && originalEnd ? Date.parse(originalEnd) - Date.parse(originalStart) : waiting.length ? null : 0,
      started_at: originalStart, ended_at: originalEnd,
      setup: scope.report.setup, launch: launch ? reference('boundary', launch) : null,
      frames: frames.map(ref),
      closing_complete_decision: closing ? ref(closing) : null,
      input_count: inputs.length, input_ids: inputs.map(entry => entry.row.input_id), missing,
      decision_count: 0, original_missing_preserved: true } };
}

export function qualifySourceWholeRun(scope, source, report) {
  const nativeNewStart = scope.setup?.transition.start_provenance === 'new' && scope.launches.length === 1
    && scope.launches[0].transition.start_provenance === 'new';
  const nativeTerminal = scope.terminals.length === 1;
  const orderedBoundaries = scope.launches.length === 1 && nativeTerminal
    && scope.launches[0].sequence < scope.terminals[0].sequence;
  const pauses = source.boundaries.some(row => row.kind === 'pause' && row.position.epoch_id === scope.epoch.epoch_id
    || (row.paused_intervals ?? []).some(interval => interval.epoch_id === scope.epoch.epoch_id));
  const gaps = source.observations.some(row => row.epoch_id === scope.epoch.epoch_id && row.gap_after_index != null);
  const unknowns = source.inputs.filter(row => row.epoch_id === scope.epoch.epoch_id && row.outcome.delivery === 'unknown').length;
  const conditions = { bounded_conversion_complete: report.complete, native_new_setup_and_launch: nativeNewStart,
    native_terminal_observed: nativeTerminal, p1_terminal_observed: report.scope.natural_terminal_observed,
    native_launch_before_terminal: orderedBoundaries,
    no_recording_pause: !pauses, no_original_gap: !gaps, no_original_unknown_delivery: unknowns === 0 };
  return { complete: Object.values(conditions).every(Boolean), conditions, original_unknown_inputs: unknowns,
    missing: Object.entries(conditions).filter(([, value]) => !value).map(([name]) => name),
    scope: 'selected_recorded_native_run_boundaries_and_p1_information',
    non_claims: ['not_human_origin_verification', 'not_gameplay_qualification', 'not_native_commit_or_causal_successor_proof'] };
}
