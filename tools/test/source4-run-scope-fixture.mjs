import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

// Codec-complete synthetic semantic inputs, not a Source bundle/native/Human claim.
const fixture = name => JSON.parse(readFileSync(new URL(
  `../../components/connector/sdk/typescript/test/fixtures/p1-global/${name}.json`, import.meta.url), 'utf8'));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
export const position = (epoch, index) => ({ epoch_id: epoch,
  stream_generation: `generation-${epoch}`, publication_index: String(index) });
const seal = (epoch, index, ordinal) => ({ epoch_id: epoch, stream_generation: `generation-${epoch}`,
  reserved_through: String(index), completed_through: String(index), after_input_ordinal: String(ordinal) });
export const transition = (kind, previous, current, provenance = null) => ({ witness_id: `witness-${kind}`,
  kind, mechanism: `native-fixture-${kind}`, previous_game_continuity_id: previous,
  game_continuity_id: current, start_provenance: provenance, graceful: null,
  victory: kind === 'terminal' ? false : null });

export function menuBasis() {
  const basis = fixture('synthetic-complete-combat'), s = basis.snapshot;
  s.snapshot_id = 'synthetic-main-menu'; s.persistent = null; s.reads = []; basis.reads = []; s.referents = [];
  s.interaction = { interaction_id: 'menu-interaction', kind: 'main_menu', stage: 'choosing', prompt: null,
    content_schema: 'sts2.player-environment/surface/main_menu-1',
    content: { surface: { kind: 'main_menu', stage: 'choosing', options: [{ entity_id: 'singleplayer-button',
      semantic_id: 'singleplayer', label: 'Single Player', description: null, enabled: true,
      action_support: 'actionable', blocked_reason: null }], continue_run: null },
    context: { kind: 'menu', flow: 'root_navigation' } },
    capabilities: [{ verb: 'open', arguments: [], availability_basis: 'current_native_interaction' }] };
  s.bound_actions.actions = [{ bound_action_id: 'menu-open-singleplayer', verb: 'open',
    interaction_id: 'menu-interaction', subject_referent_id: null, arguments: [], label: 'Open Single Player' }];
  s.bound_actions.materialized_count = s.bound_actions.total_count = 1;
  return basis;
}

export function sourceRunFixture({ sameEpochPrelude = false, terminal = true } = {}) {
  const blobs = new Map(), identity = { session_id: 'source-session', timeline_id: 'timeline', segment_id: 'segment' };
  const firstIndex = sameEpochPrelude ? 2 : 1;
  const source = { bundle: 'synthetic-semantic-input', receipt: { content_id: 'synthetic-only',
    manifest: { session_id: identity.session_id } }, epochs: [], observations: [], inputs: [], boundaries: [], bytes: path => blobs.get(path) };
  function parts(basis, epoch) {
    const missing = basis.reads.flatMap(read => read.response === null ? [`read:${read.read_id}:${read.error}`]
      : read.response.completeness.status !== 'complete' || read.response.completeness.missing.length
        ? [`read:${read.read_id}:partial`] : []);
    if (basis.snapshot.completeness.status !== 'complete' || basis.snapshot.completeness.missing.length)
      missing.push('snapshot:partial');
    const partial = missing.length > 0;
    const bytes = Buffer.from(JSON.stringify({ schema: 'sts2.player-environment/public-basis-1', revision: '1',
      capture_scope: 'current_public_snapshot_and_advertised_read_instances', capture_status: partial ? 'partial' : 'complete',
      snapshot: basis.snapshot, reads: basis.reads, missing }));
    const sha = hash(bytes), path = `public-bases/sha256/${sha.slice(0, 2)}/${sha}.bin`;
    blobs.set(path, bytes);
    return { public_basis: { epoch_id: epoch, scope_id: `scope-${epoch}`, stream_generation: `generation-${epoch}`,
      snapshot_id: basis.snapshot.snapshot_id, captured_at: basis.snapshot.observed_at,
      byte_count: bytes.length, sha256: sha, payload_ref: path }, public_basis_missing_reason: null, native_stage: null };
  }
  function publication(basis, epoch, index) {
    const row = { ...identity, epoch_id: epoch, position: position(epoch, index), sequence: source.observations.length + 1,
      source_seam: 'native_owner_ready', source_index: String(index), phase: 'observation', gap_after_index: null,
      captured_parts: parts(basis, epoch) };
    source.observations.push(row); return row;
  }
  function epoch(id, continuity, previous = null, setup = null) {
    return { epoch_id: id, previous_epoch_id: previous, sequence: source.epochs.length + 1, after_input_ordinal: '0',
      context: { game_continuity_id: continuity, stream_generation: `generation-${id}`, scope_id: `scope-${id}` },
      starting_position: position(id, 0), initial_position: position(id, 1),
      predecessor_seal: previous ? seal(previous, 1, 0) : null, transition: setup };
  }
  source.epochs.push(epoch('title', null));
  source.epochs.push(epoch('run', 'run-exact', 'title', transition('setup_handoff', null, 'run-exact', 'new')));
  publication(menuBasis(), 'title', 1);
  if (sameEpochPrelude) publication(menuBasis(), 'run', 1);
  const combat = fixture('synthetic-complete-combat');
  publication(combat, 'run', firstIndex);
  source.inputs.push({ ...identity, epoch_id: 'run', sequence: 1, input_id: 'input-first-p1', input_prefix_ordinal: '1',
    pre_position: position('run', firstIndex), captured_parts: parts(combat, 'run'),
    basis_order: { status: 'native_prefix_frozen', reason_code: null }, outcome: { mapping_status: 'exact', match_count: 1,
      selected_action: { action_id: 'native-catalog-other-id', kind: 'native_input', verb: 'end_turn', label: 'End turn',
        subject_referent_id: null, arguments: [], effect_domain: 'native' }, native_mechanism: 'native_end_turn_ui',
      delivery: 'delivered', reason_code: null, stages: [], native_facts: { origin: 'native_input', missing_reason: null,
        owner_ref: 'end-button', subject_ref: null, arguments: {}, carriers: [
          { kind: 'game_action_submitted', carrier_ref: 'exact-action' },
          { kind: 'game_action_accepted', carrier_ref: 'exact-action' }] } } });
  source.boundaries.push({ ...identity, sequence: 1, boundary_id: 'launch-boundary', kind: 'launch',
    position: position('run', firstIndex), after_input_ordinal: '0', paused_intervals: [],
    transition: transition('launch', 'run-exact', 'run-exact', 'new') });
  if (terminal) {
    const terminalBasis = fixture('reference-game_over');
    terminalBasis.snapshot.session = structuredClone(combat.snapshot.session);
    for (const read of terminalBasis.reads) if (read.response) read.response.session = structuredClone(combat.snapshot.session);
    publication(terminalBasis, 'run', firstIndex + 1);
    source.boundaries.push({ ...identity, sequence: 2, boundary_id: 'terminal-boundary', kind: 'terminal',
      position: position('run', firstIndex + 1), after_input_ordinal: '1', paused_intervals: [],
      transition: transition('terminal', 'run-exact', 'run-exact') });
  }
  source.boundaries.push({ ...identity, sequence: source.boundaries.length + 1, boundary_id: 'close-boundary', kind: 'close',
    position: position('run', firstIndex + (terminal ? 1 : 0)), after_input_ordinal: '1', paused_intervals: [], transition: null });
  return { source, combat, publication, epoch, parts, firstIndex };
}
