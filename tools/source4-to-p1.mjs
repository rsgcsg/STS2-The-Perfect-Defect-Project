#!/usr/bin/env node
/** Offline Source4 adapter. It never connects to a Host or emits Agent protocol rows. */
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, lstatSync, realpathSync, writeFileSync } from 'node:fs';
import { resolve, relative, sep, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const verifier = String.raw`
import json,sys
from sts2_platform_evidence.source_session_bundle_v4 import verify_source_session_bundle_v4
value=verify_source_session_bundle_v4(sys.argv[1]).require_value()
def plain(value):
 from collections.abc import Mapping
 if isinstance(value,Mapping):return {key:plain(item) for key,item in value.items()}
 if isinstance(value,(tuple,list)):return [plain(item) for item in value]
 return value
print(json.dumps({'content_id':value.content_id,'manifest':plain(value.manifest)},separators=(',',':')))
`;

export function readVerifiedSource4(bundle, sourcePython) {
  if (!sourcePython) throw new Error('source4_python_required: select a supported Python >=3.11 interpreter');
  bundle = realpathSync(resolve(bundle));
  const receipt = JSON.parse(execFileSync(sourcePython, ['-c', verifier, bundle], {
    env: { ...process.env, PYTHONPATH: resolve(root, 'components/evidence') },
    encoding: 'utf8', timeout: 30_000, maxBuffer: 8 * 1024 * 1024,
  }));
  const inventory = receipt.manifest.content_identity.raw_file_sha256;
  const raw = realpathSync(resolve(bundle, 'raw'));
  function bytes(path) {
    if (typeof path !== 'string' || path.includes('\\') || path.startsWith('/')
        || path.split('/').some(part => ['', '.', '..'].includes(part)) || !(path in inventory))
      throw new Error('source4_original_path_invalid');
    const file = resolve(raw, path); const actual = realpathSync(file);
    if (!lstatSync(file).isFile() || lstatSync(file).isSymbolicLink()
        || relative(raw, actual).split(sep).includes('..'))
      throw new Error('source4_original_changed_after_verification');
    // Hash the very bytes returned to the consumer; never verify one read and decode another.
    const value = readFileSync(file);
    if (hash(value) !== inventory[path]) throw new Error('source4_original_changed_after_verification');
    return value;
  }
  function rows(path) {
    const text = bytes(path).toString('utf8');
    return text.split('\n').filter(Boolean).map(line => JSON.parse(line));
  }
  const epochs = rows('source-attachment-epochs.jsonl');
  const observations = rows('public-observations.jsonl');
  const inputs = rows('native-input-witnesses.jsonl');
  const boundaries = rows('source-boundaries.jsonl');
  return { bundle, receipt, epochs, observations, inputs, boundaries, bytes };
}

/** Original prefix order is separate from terminal disk append order. */
export function orderedSource4Rows(source) {
  const epochs = new Map(source.epochs.map((row, index) => [row.epoch_id, index]));
  return [
    ...source.observations.map(row => ({ kind: 'source_observation', row, position: row.position, ordinal: null })),
    ...source.inputs.map(row => ({ kind: 'source_input', row, position: row.pre_position, ordinal: BigInt(row.input_prefix_ordinal) })),
  ].sort((a, b) => {
    const epoch = epochs.get(a.row.epoch_id) - epochs.get(b.row.epoch_id);
    if (epoch) return epoch;
    const first = BigInt(a.position.publication_index), second = BigInt(b.position.publication_index);
    if (first !== second) return first < second ? -1 : 1;
    if (a.kind !== b.kind) return a.kind === 'source_observation' ? -1 : 1;
    if (a.ordinal !== null) return a.ordinal < b.ordinal ? -1 : a.ordinal > b.ordinal ? 1 : 0;
    return 0;
  });
}

export async function convertSource4Bundle(options) {
  const source = readVerifiedSource4(options.bundle, options.python);
  const sdkRoot = resolve(options.sdkRoot ?? resolve(root, 'components/connector/sdk/typescript/dist'));
  const core = await import(pathToFileURL(resolve(sdkRoot, 'p1Global/index.js')).href);
  const sdk = await import(pathToFileURL(resolve(sdkRoot, 'index.js')).href);
  if (typeof core.matchSourceP1Choice !== 'function') throw new Error('source4_shared_source_action_matcher_required');
  if (!['reference', 'managed'].includes(options.hostKind)) throw new Error('source4_host_kind_required');
  return convertOriginals(source, sdk, core, options);
}

/** Pure adapter for already verified originals; exported for synthetic semantic regressions only. */
export function convertOriginals(source, sdk, core, options) {
  const manifest = source.receipt.manifest;
  const episodeId = `source4:${manifest.session_id}`;
  let state = core.createSession(episodeId, options.hostKind);
  const report = { schema: 'sts2.p1/source4-conversion-report-1', source_bundle: source.bundle,
    source_content_id: source.receipt.content_id, source_session_id: manifest.session_id,
    host_kind: options.hostKind, captures: [], actions: [], context_spans: [], native_trace: source.inputs,
    source_boundaries: source.boundaries,
    unresolved_inputs: [], unresolved_contexts: [], missing: [], complete: false,
    non_claims: ['not_agent_offer_consume_or_choice', 'not_human_origin_verification',
      'not_native_commit_or_causal_successor_proof', 'not_runtime_qualification'] };
  const trace = []; const covered = new Set(); const refusals = new Map();
  let candidates = []; let publicationsBefore = [];
  for (const entry of orderedSource4Rows(source)) {
    const row = entry.row; const reference = row.captured_parts.public_basis;
    let basis = null; let reduction = null;
    const capture = { source_kind: entry.kind, epoch_id: row.epoch_id, segment_id: row.segment_id,
      source_ref: entry.kind === 'source_input' ? row.input_id : row.position.publication_index,
      public_basis: reference, complete: false, missing: [], observation: null,
      classification: 'unresolved_context' };
    const captureIndex = report.captures.push(capture) - 1;
    if (reference !== null) {
      const bytes = source.bytes(reference.payload_ref);
      if (bytes.length !== reference.byte_count || hash(bytes) !== reference.sha256)
        throw new Error('source4_basis_reference_changed');
      let original;
      try { original = sdk.decodePublicBasis(JSON.parse(bytes.toString('utf8'))); }
      catch { capture.missing.push('p1_public_basis_decoder_refused_original'); }
      if (original !== undefined) {
        basis = { snapshot: original.snapshot, reads: original.reads };
        // Diagnostic reduction is pure. A native UI prefix is not automatically a new P1 decision.
        reduction = core.reduce(state, basis);
        capture.complete = reduction.complete; capture.missing = [...reduction.missing];
        capture.observation = reduction.observation;
        if (entry.kind === 'source_input' && reduction.complete && reduction.observation.status === 'decision') {
          // A stable snapshot ID can have a later observed_at. Bind this candidate to the exact original input bytes.
          const key = [row.epoch_id, row.segment_id, row.input_id, row.input_prefix_ordinal,
            core.canonicalJson(core.commitmentView(state))].join('|');
          if (!candidates.some(candidate => candidate.key === key)) candidates.push({ key, before: state, basis,
            reduction, captureIndex, start: trace.length, epoch: row.epoch_id, segment: row.segment_id });
        } else if (reduction.complete && reduction.observation.status === 'terminal') {
          capture.classification = 'p1_terminal_boundary'; state = reduction.state;
        }
      }
    } else capture.missing.push(row.captured_parts.public_basis_missing_reason);
    if (entry.kind !== 'source_input') {
      publicationsBefore.push({ ...row, basis });
      if (trace.length > 0) {
        trace.at(-1).wrapper.publications_after = [...publicationsBefore];
        settle(captureIndex); // An original cancellation/postcondition may settle only after a later publication.
      }
      continue;
    }
    // Derived wrappers retain exact verified rows; this data is not written back into native originals.
    if (trace.length > 0 && !covered.has(trace.at(-1).wrapper.input_id))
      trace.at(-1).wrapper.publications_after = []; // Same originals now belong to the next input's between-prefix interval.
    const wrapper = { ...row, basis, publications_before: publicationsBefore,
      publications_after: [], source_boundaries: source.boundaries };
    publicationsBefore = [];
    trace.push({ wrapper, captureIndex });
    candidates = candidates.filter(candidate => candidate.epoch === row.epoch_id && candidate.segment === row.segment_id);
    settle(captureIndex);
  }
  function settle(captureIndex) {
    if (trace.length === 0) return;
    const lastInput = trace.at(-1).wrapper;
    const matches = []; const outcomes = [];
    for (const candidate of candidates) {
      const span = trace.slice(candidate.start).map(value => value.wrapper);
      if (span.length === 0) continue;
      const result = core.matchSourceP1Choice(candidate.before, candidate.basis, span);
      outcomes.push({ status: result.status, reason: result.reason });
      if (result.status === 'matched' || result.status === 'context_only') matches.push({ candidate, result, span });
    }
    report.captures[captureIndex].source_matching = outcomes;
    refusals.set(lastInput.input_id, outcomes.find(value => value.status === 'ambiguous')
      ?? outcomes.find(value => value.status === 'incomplete') ?? outcomes[0]
      ?? { status: 'unmapped', reason: 'no_original_complete_p1_boundary_basis' });
    const distinct = new Map(matches.map(value => [core.canonicalJson({ status: value.result.status,
      choice_id: value.result.choice_id, input_ids: value.result.input_ids,
      source_input: value.span[0].input_id }), value]));
    if (distinct.size > 1) {
      report.unresolved_inputs.push({ input_id: lastInput.input_id, status: 'ambiguous', reason: 'multiple_original_p1_choices' });
      return;
    }
    if (distinct.size !== 1) return;
    const { candidate, result, span } = [...distinct.values()][0];
    if (!Array.isArray(result.input_ids) || result.input_ids.length !== span.length
        || result.input_ids.some((id, index) => id !== span[index].input_id || covered.has(id)))
      throw new Error('source4_shared_match_did_not_classify_original_span');
    if (result.status === 'context_only' && result.choice_id !== null)
      throw new Error('source4_context_only_cannot_choose_an_action');
    const lowering = result.status === 'matched' ? candidate.reduction.lowering[result.choice_id] : null;
    if (result.status === 'matched' && (!result.choice_id || !lowering))
      throw new Error('source4_shared_match_choice_not_in_original_lowering');
    for (const id of result.input_ids) covered.add(id);
    report.unresolved_inputs = report.unresolved_inputs.filter(value => !covered.has(value.input_id));
    // Only the supplied, fully verified intermediate span is classified. First-prefix publication history stays unresolved.
    for (let index = candidate.captureIndex; index <= captureIndex; index++)
      if (report.captures[index].classification === 'unresolved_context')
        report.captures[index].classification = 'shared_verified_mechanical_context';
    if (result.status === 'context_only') {
      report.context_spans.push({ source_input_ids: result.input_ids, reason: result.reason,
        proof: 'shared_sdk_original_trace_context_only' });
      // Cancellation is neither a P1 choice nor a commitment. Original unknown delivery remains unchanged.
    } else {
      report.captures[candidate.captureIndex].classification = 'p1_decision_boundary';
      report.actions.push({ choice_id: result.choice_id,
        choice: candidate.reduction.observation.catalog.find(choice => choice.choice_id === result.choice_id) ?? null,
        source_input_ids: result.input_ids, public_snapshot_id: candidate.basis.snapshot.snapshot_id,
        origin: 'verified_source_native_input', proof: 'shared_sdk_original_trace_match' });
      state = core.applyCommitment(candidate.reduction.state, lowering, true);
    }
    candidates = [];
  }
  report.unresolved_inputs.push(...trace.filter(value => !covered.has(value.wrapper.input_id)
    && !report.unresolved_inputs.some(item => item.input_id === value.wrapper.input_id)).map(value => ({ input_id: value.wrapper.input_id,
      status: refusals.get(value.wrapper.input_id)?.status ?? 'unmapped',
      reason: refusals.get(value.wrapper.input_id)?.reason ?? 'no_complete_shared_p1_intention_for_original_trace' })));
  // Required missing is determined at proven P1 boundaries. Unknown intermediate contexts remain explicit and prevent completion.
  for (const capture of report.captures) {
    if (capture.classification === 'unresolved_context') report.unresolved_contexts.push({ source_kind: capture.source_kind,
      source_ref: capture.source_ref, reason: 'shared_mapper_has_not_classified_original_context' });
    if (capture.classification.startsWith('p1_') && capture.missing.length > 0) report.missing.push({
      source_kind: capture.source_kind, source_ref: capture.source_ref, reason: 'p1_boundary_required_information_missing', missing: capture.missing });
  }
  report.complete = report.missing.length === 0 && report.unresolved_inputs.length === 0 && report.unresolved_contexts.length === 0;
  return report;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2); const values = {};
  for (let i = 0; i < args.length; i += 2) {
    if (!['--bundle', '--python', '--host-kind', '--sdk-root', '--output'].includes(args[i]) || !args[i + 1])
      throw new Error('usage: source4-to-p1.mjs --bundle DIR --python PYTHON --host-kind reference|managed [--sdk-root DIST] [--output FILE]');
    values[args[i]] = args[i + 1];
  }
  const report = await convertSource4Bundle({ bundle: values['--bundle'], python: values['--python'],
    hostKind: values['--host-kind'], sdkRoot: values['--sdk-root'] });
  const text = JSON.stringify(report, null, 2) + '\n';
  if (values['--output']) writeFileSync(resolve(values['--output']), text, { flag: 'wx' });
  else process.stdout.write(text);
}
