#!/usr/bin/env node
// P5 bounded executable reference, NOT a game Host, trained model or production port.
// Reads only this script for identity; uses synthetic frames and injected toy models.
// No game, network, subprocess, credentials, corpus, weights, timers or storage APIs.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const clone = (x) => structuredClone(x);
const canonical = (x) => JSON.stringify(x, (_, value) => value && typeof value === 'object' && !Array.isArray(value)
  ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))) : value);
const hash = (x) => createHash('sha256').update(canonical(x)).digest('hex');
const requireThat = (value, reason) => { if (!value) throw new Error(reason); };
function validateSubmit(request) {
  requireThat(request && canonical(Object.keys(request).sort()) === canonical(['action', 'catalog', 'eventId', 'generation', 'id'])
    && Object.values(request).every(value => typeof value === 'string' && value.length > 0), 'submit_schema');
}
const commands = [
  { ref: 'begin-b', operation: 'begin_card_play', args: { subject: 'b' } },
  { ref: 'inspect-r', operation: 'inspect_relic', args: { subject: 'r' } },
];
const frame = (seq, extras = {}) => { const value = ({
  generation: 'g1', eventId: `e${seq}`, sequence: seq, clock: seq * 10,
  capture: { scope: 'synthetic_public_fixture', coherent: true },
  public: { page: 'combat', inputReady: true, mainReady: true, choiceReady: false,
    terminalReady: false, pending: [], ...extras },
  actions: clone(commands),
}); if (!value.public.inputReady) value.actions = []; return value; };

class SyntheticEnvironment {
  constructor(initial) {
    this.ledger = new Map(); this.calls = 0; this.permission = true;
    this.deliveryUnknown = false; this.nextFixtureDelivery = 'delivered';
    this.install(initial);
  }
  install(value) {
    requireThat(value.capture.coherent && (value.public.inputReady || value.actions.length === 0)
      && new Set(value.actions.map(a => a.ref)).size === value.actions.length,
      'fixture_capture_or_catalog_invalid');
    this.value = clone(value); this.catalog = hash({ generation: value.generation, sequence: value.sequence, actions: value.actions });
  }
  observe() { return { ...clone(this.value), catalog: { ref: this.catalog, complete: true, count: this.value.actions.length } }; }
  list(ref, start = 0, limit = 128) {
    requireThat(ref === this.catalog, 'catalog_stale');
    requireThat(Number.isInteger(start) && start >= 0 && Number.isInteger(limit) && limit > 0, 'bad_page');
    return { items: clone(this.value.actions.slice(start, start + limit)), total: this.value.actions.length,
      next: start + limit < this.value.actions.length ? start + limit : null };
  }
  allowed(ref, prefix = {}) {
    requireThat(ref === this.catalog, 'catalog_stale');
    return clone(this.value.actions.filter(a => (!prefix.operation || a.operation === prefix.operation)
      && Object.entries(prefix.args ?? {}).every(([k, v]) => a.args[k] === v)));
  }
  resolve(ref, proposal) {
    requireThat(ref === this.catalog, 'catalog_stale');
    requireThat(canonical(Object.keys(proposal).sort()) === canonical(['args', 'operation']), 'proposal_schema');
    const matches = this.value.actions.filter(a => a.operation === proposal.operation && canonical(a.args) === canonical(proposal.args));
    requireThat(matches.length === 1, matches.length ? 'ambiguous' : 'no_match');
    return matches[0].ref;
  }
  submit(request) {
    validateSubmit(request);
    const fingerprint = hash(request);
    if (this.ledger.has(request.id)) {
      const prior = this.ledger.get(request.id);
      requireThat(prior.fingerprint === fingerprint, 'request_id_conflict');
      return clone(prior.result);
    }
    requireThat(this.permission, 'control_revoked');
    requireThat(!this.deliveryUnknown, 'delivery_reconciliation_required');
    requireThat(request.generation === this.value.generation && request.eventId === this.value.eventId
      && request.catalog === this.catalog, 'binding_stale');
    requireThat(this.value.public.inputReady, 'native_input_not_ready');
    requireThat(this.value.actions.some(a => a.ref === request.action), 'not_current_member');
    this.calls += 1;
    const result = { request: request.id, delivery: this.nextFixtureDelivery, effectOutcome: 'not_asserted' };
    if (result.delivery === 'unknown') this.deliveryUnknown = true;
    this.ledger.set(request.id, { fingerprint, result });
    return clone(result);
  }
  stop() { this.permission = false; } // Does not cancel any native effect.
}

class PublicMemory {
  constructor() { this.entries = []; this.hashes = new Map(); this.generation = null; this.cursor = null; }
  consume(input) {
    const digest = hash(input);
    if (this.hashes.has(input.eventId)) {
      requireThat(this.hashes.get(input.eventId) === digest, 'same_event_changed'); return false;
    }
    requireThat(this.generation === null || input.generation === this.generation, 'explicit_reset_required');
    requireThat(this.cursor === null || input.sequence === this.cursor + 1, 'history_gap');
    this.generation = input.generation; this.cursor = input.sequence;
    this.hashes.set(input.eventId, digest); this.entries.push(clone(input.public)); return true;
  }
  read() { return clone(this.entries); }
  digest() { return hash(this.entries); }
}
const boundaryReady = (p) => p.mainReady || p.choiceReady || p.terminalReady;
const awaitBoundary = (o) => ({ kind: 'await', basis: { generation: o.generation, cursor: o.sequence, clock: o.clock },
  triggers: [{ mode: 'until_state', predicate: 'boundary_ready' }], deadlineAfterBasisMs: 100 });
const act = (o, action) => ({ kind: 'act', basis: { generation: o.generation, eventId: o.eventId, catalog: o.catalog.ref }, action });
function allActions(env, o) {
  const rows = []; let cursor = 0;
  while (cursor !== null) { const page = env.list(o.catalog.ref, cursor); rows.push(...page.items); cursor = page.next; }
  requireThat(rows.length === o.catalog.count, 'incomplete_full_scoring_input'); return rows;
}
const toyScores = (rows) => rows.map(a => a.operation === 'begin_card_play' || a.operation === 'confirm_selection' ? 1 : 0);
const toyTiming = (o) => o.public.inputReady ? 'act' : 'await';

// Four Agent compositions. Injected model functions are deterministic test doubles.
class ReferenceAgent {
  constructor(kind) { this.kind = kind; this.memory = new PublicMemory(); this.scored = 0; this.timingCalls = 0; }
  consume(o) { this.memory.consume(o); }
  decide(env, o) {
    const before = this.memory.digest(); let output;
    if (o.actions.length === 0) output = o.public.taskComplete ? { kind: 'abstain', reason: 'task_complete' }
      : { ...awaitBoundary(o), triggers: [{ mode: 'until_state', predicate: 'input_ready' }] };
    else if (this.kind === 'boundary_scorer' && !boundaryReady(o.public)) output = awaitBoundary(o);
    else if (this.kind === 'modular_timing' && (++this.timingCalls, toyTiming(o)) === 'await') output = awaitBoundary(o);
    else if (this.kind === 'generated_command') {
      // This is a canned generator; no LLM or learned policy is invoked.
      const generated = JSON.stringify({ operation: o.actions[0].operation, args: o.actions[0].args });
      output = act(o, env.resolve(o.catalog.ref, JSON.parse(generated)));
    } else {
      const rows = allActions(env, o);
      const proposed = this.kind === 'retrieval_rerank' ? rows.filter(a => a.operation === 'begin_card_play').slice(0, 2) : rows;
      requireThat(proposed.length > 0, 'toy_proposal_empty');
      this.scored += proposed.length;
      const scores = toyScores(proposed);
      const best = scores.indexOf(Math.max(...scores)); output = act(o, proposed[best].ref);
    }
    requireThat(before === this.memory.digest(), 'candidate_scoring_wrote_memory'); return output;
  }
}
function dispatch(env, o, decision, id) {
  if (decision.kind !== 'act') return clone(decision);
  requireThat(canonical(decision.basis) === canonical({ generation: o.generation, eventId: o.eventId, catalog: o.catalog.ref }), 'agent_basis_drift');
  return env.submit({ id, ...decision.basis, action: decision.action });
}

// Optional facade changes the external protocol: it is NOT an internal Agent policy.
class BoundaryFacade {
  constructor(environment = null) {
    this.environment = environment; this.viewSeq = 0; this.sourceReceipts = [];
    this.bySource = new Map(); this.views = new Map(); this.catalogs = new Map();
  }
  project(o) {
    const visible = boundaryReady(o.public);
    this.sourceReceipts.push({ sourceEvent: o.eventId, supplied: visible });
    if (!visible) return null;
    const key = `${o.generation}:${o.eventId}`, digest = hash(o);
    if (this.bySource.has(key)) {
      const prior = this.bySource.get(key); requireThat(prior.digest === digest, 'source_occurrence_changed');
      return clone(prior.view);
    }
    const out = clone(o); out.sourceEvent = o.eventId;
    out.eventId = `boundary-${++this.viewSeq}`; out.sequence = this.viewSeq;
    out.catalog.ref = hash({ profile: 'boundary-facade-reference-v1', source: o.catalog.ref });
    const binding = { generation: o.generation, eventId: o.eventId, catalog: o.catalog.ref, viewCatalog: out.catalog.ref };
    this.views.set(out.eventId, binding); this.catalogs.set(out.catalog.ref, binding);
    this.bySource.set(key, { digest, view: out }); return clone(out);
  }
  observe() { requireThat(this.environment, 'no_environment'); return this.project(this.environment.observe()); }
  binding(ref) { const value = this.catalogs.get(ref); requireThat(value, 'unknown_view_catalog'); return value; }
  list(ref, start, limit) { return this.environment.list(this.binding(ref).catalog, start, limit); }
  allowed(ref, prefix) { return this.environment.allowed(this.binding(ref).catalog, prefix); }
  resolve(ref, proposal) { return this.environment.resolve(this.binding(ref).catalog, proposal); }
  submit(request) {
    validateSubmit(request);
    const binding = this.views.get(request.eventId);
    requireThat(binding && request.generation === binding.generation && request.catalog === binding.viewCatalog, 'view_binding_mismatch');
    // Preserve the original source basis. NEVER substitute the latest E frame.
    return this.environment.submit({ id: `facade:${request.id}`, generation: binding.generation,
      eventId: binding.eventId, catalog: binding.catalog, action: request.action });
  }
}

const publicPredicates = {
  boundary_ready: boundaryReady,
  input_ready: p => p.inputReady,
  choice_required: p => p.choiceReady,
};
const publicEventKinds = new Set(['choice_required', 'observation_changed', 'public_result', 'boundary_changed', 'other']);
class AwaitReference {
  constructor() { this.requests = new Map(); }
  evaluate(request, fixture) {
    requireThat(typeof request.id === 'string' && request.id.length > 0
      && Number.isInteger(request.basis.cursor) && request.basis.cursor >= 0
      && Number.isFinite(request.basis.clock) && Array.isArray(request.triggers), 'await_request');
    requireThat(Number.isFinite(request.deadlineAfterBasisMs) && request.deadlineAfterBasisMs >= 0, 'await_deadline');
    for (const t of request.triggers) requireThat(t.mode === 'next_event'
      ? publicEventKinds.has(t.kind) : t.mode === 'until_state' && Object.hasOwn(publicPredicates, t.predicate), 'await_trigger');
    const key = hash(request); const previous = this.requests.get(request.id);
    if (previous) {
      requireThat(previous.key === key, 'await_id_conflict');
      if (previous.result) return clone(previous.result);
    }
    const seal = result => { this.requests.set(request.id, { key, result }); return clone(result); };
    if (!fixture.permission || fixture.generation !== request.basis.generation) return seal({ state: 'aborted' });
    requireThat(Number.isFinite(fixture.now) && request.basis.clock <= fixture.now
      && fixture.latest.clock <= fixture.now, 'fixture_clock_inconsistent');
    requireThat(fixture.basisFrames.some(f => f.generation === request.basis.generation
      && f.sequence === request.basis.cursor && f.clock === request.basis.clock), 'await_basis_mismatch');
    requireThat(fixture.events.every(e => Number.isInteger(e.sequence) && Number.isFinite(e.clock)
      && e.clock <= fixture.now && e.sequence <= fixture.latest.sequence
      && e.generation === fixture.generation), 'fixture_history_inconsistent');
    if (request.basis.cursor < fixture.retainedFrom - 1 || request.basis.cursor > fixture.latest.sequence)
      return seal({ state: 'gap' });
    const ordered = fixture.events.filter(e => e.sequence > request.basis.cursor).sort((a, b) => a.sequence - b.sequence);
    // This reference numbers its OWN published protocol events contiguously.
    // It does not infer native/Human capture completeness from a raw ordinal.
    if (ordered.length !== fixture.latest.sequence - request.basis.cursor
      || ordered.some((e, i) => e.sequence !== request.basis.cursor + i + 1)) return seal({ state: 'gap' });
    requireThat(ordered.every((e, i) => e.clock >= (i ? ordered[i - 1].clock : request.basis.clock)), 'fixture_clock_order');
    const deadline = request.basis.clock + request.deadlineAfterBasisMs;
    const match = ordered.find(e => e.clock <= Math.min(fixture.now, deadline)
      && request.triggers.some(t => t.mode === 'next_event' ? e.kind === t.kind : publicPredicates[t.predicate](e.public)));
    if (match) return seal({ state: 'matched', matchedEvent: match.eventId, latestEvent: fixture.latest.eventId });
    if (fixture.now <= deadline && request.triggers.some(t => t.mode === 'until_state' && publicPredicates[t.predicate](fixture.latest.public)))
      return seal({ state: 'already_true', latestEvent: fixture.latest.eventId });
    if (fixture.now >= deadline) return seal({ state: 'timeout', latestEvent: fixture.latest.eventId });
    this.requests.set(request.id, { key, result: null }); return { state: 'waiting' };
  }
}

const checks = [], examples = [];
function check(category, name, run) { run(); checks.push({ category, name, status: 'pass' }); }
check('agent_composition', 'four Agent strategies share one E contract', () => {
  for (const kind of ['boundary_scorer', 'modular_timing', 'generated_command', 'retrieval_rerank']) {
    const env = new SyntheticEnvironment(frame(1)); const o = env.observe(); const agent = new ReferenceAgent(kind);
    agent.consume(o); const choice = agent.decide(env, o); const result = dispatch(env, o, choice, `r-${kind}`);
    assert.equal(result.delivery, 'delivered'); assert.equal(env.calls, 1);
    examples.push({ kind, choice: choice.kind, action: choice.action, referenceCandidatesScored: agent.scored });
  }
});
check('agent_composition', 'empty native catalog means wait or explicit task completion, never a fake action', () => {
  const env = new SyntheticEnvironment(frame(1, { inputReady: false, mainReady: false })), o = env.observe();
  for (const kind of ['boundary_scorer', 'modular_timing', 'generated_command', 'retrieval_rerank']) {
    const a = new ReferenceAgent(kind); a.consume(o); assert.equal(a.decide(env, o).kind, 'await');
  }
  env.install(frame(2, { inputReady: false, mainReady: false, taskComplete: true }));
  const done = env.observe(), a = new ReferenceAgent('generated_command'); a.consume(done);
  assert.deepEqual(a.decide(env, done), { kind: 'abstain', reason: 'task_complete' }); assert.equal(env.calls, 0);
});
check('agent_composition', 'known pending effects need not force all Agents to wait', () => {
  const env = new SyntheticEnvironment(frame(1, { mainReady: false, pending: ['request-A'] })); const o = env.observe();
  const b = new ReferenceAgent('boundary_scorer'), e = new ReferenceAgent('modular_timing'); b.consume(o); e.consume(o);
  assert.equal(b.decide(env, o).kind, 'await'); assert.equal(e.decide(env, o).kind, 'act');
  assert.equal(env.observe().actions.length, 2); // No environment catalog pruning.
});
check('agent_composition', 'a blocked child choice wakes a boundary Agent', () => {
  const f = frame(1, { page: 'selector', mainReady: false, choiceReady: true, pending: ['parent-A'] });
  f.actions = [{ ref: 'confirm', operation: 'confirm_selection', args: {} }];
  const env = new SyntheticEnvironment(f), o = env.observe(), a = new ReferenceAgent('boundary_scorer'); a.consume(o);
  assert.equal(a.decide(env, o).action, 'confirm');
});
check('agent_composition', 'facade and internal scheduling expose different histories', () => {
  const env = new SyntheticEnvironment(frame(1)), a = new ReferenceAgent('boundary_scorer'), facade = new BoundaryFacade();
  const exposed = [];
  for (const f of [frame(1), frame(2, { mainReady: false, pending: ['A'] }), frame(3)]) {
    env.install(f); const o = env.observe(); a.consume(o); const v = facade.project(o); if (v) exposed.push(v);
  }
  assert.equal(a.memory.read().length, 3); assert.deepEqual(exposed.map(x => x.sourceEvent), ['e1', 'e3']);
  assert.deepEqual(exposed.map(x => x.sequence), [1, 2]); assert.equal(facade.sourceReceipts.length, 3);
});
check('agent_composition', 'boundary facade supports real reference input-to-source submission', () => {
  const source = new SyntheticEnvironment(frame(1)), facade = new BoundaryFacade(source), agent = new ReferenceAgent('generated_command');
  const view = facade.observe(); agent.consume(view); const decision = agent.decide(facade, view);
  assert.equal(dispatch(facade, view, decision, 'b-submit').delivery, 'delivered'); assert.equal(source.calls, 1);
  assert.equal(facade.observe().eventId, view.eventId); // Observe repeat is not a new target occurrence.
});
check('binding_reference', 'facade rejects malformed requests before normalization', () => {
  const source = new SyntheticEnvironment(frame(1)), facade = new BoundaryFacade(source), view = facade.observe();
  const request = { id: 'x', generation: view.generation, eventId: view.eventId, catalog: view.catalog.ref, action: 'begin-b' };
  assert.throws(() => facade.submit({ ...request, extra: 'ignored' }), /submit_schema/);
  assert.throws(() => facade.submit({ ...request, id: '' }), /submit_schema/); assert.equal(source.calls, 0);
});
check('binding_reference', 'stale boundary views cannot be rebound to the latest source', () => {
  const source = new SyntheticEnvironment(frame(1)), facade = new BoundaryFacade(source), view = facade.observe();
  source.install(frame(2)); facade.observe();
  assert.throws(() => dispatch(facade, view, act(view, 'begin-b'), 'old-view'), /binding_stale/); assert.equal(source.calls, 0);
});
check('binding_reference', 'stale observation is not silently rebound', () => {
  const env = new SyntheticEnvironment(frame(1)), old = env.observe(); env.install(frame(2));
  assert.throws(() => dispatch(env, old, act(old, 'begin-b'), 'old'), /binding_stale/); assert.equal(env.calls, 0);
});
check('binding_reference', 'request retransmission does not execute twice', () => {
  const env = new SyntheticEnvironment(frame(1)), o = env.observe(), d = act(o, 'begin-b');
  assert.deepEqual(dispatch(env, o, d, 'one'), dispatch(env, o, d, 'one')); assert.equal(env.calls, 1);
  assert.throws(() => dispatch(env, o, act(o, 'inspect-r'), 'one'), /request_id_conflict/);
});
check('binding_reference', 'unknown delivery blocks a new submission', () => {
  const env = new SyntheticEnvironment(frame(1)), o = env.observe(); env.nextFixtureDelivery = 'unknown';
  assert.equal(dispatch(env, o, act(o, 'begin-b'), 'u').delivery, 'unknown');
  assert.throws(() => dispatch(env, o, act(o, 'inspect-r'), 'u2'), /delivery_reconciliation_required/); assert.equal(env.calls, 1);
});
check('binding_reference', 'Stop blocks future input without deleting pending effects', () => {
  const env = new SyntheticEnvironment(frame(1, { pending: ['A'] })), o = env.observe(); env.stop();
  assert.throws(() => dispatch(env, o, act(o, 'begin-b'), 'stop'), /control_revoked/);
  assert.deepEqual(env.observe().public.pending, ['A']);
});
check('binding_reference', 'cross-product combinations not in the current relation are rejected', () => {
  const f = frame(1); f.actions = [
    { ref: 'a1', operation: 'inspect_card', args: { subject: 'c1' } },
    { ref: 'a2', operation: 'inspect_relic', args: { subject: 'r1' } },
  ]; const env = new SyntheticEnvironment(f), o = env.observe();
  assert.deepEqual(env.allowed(o.catalog.ref, { operation: 'inspect_card' }).map(a => a.args.subject), ['c1']);
  assert.throws(() => env.resolve(o.catalog.ref, { operation: 'inspect_card', args: { subject: 'r1' } }), /no_match/);
  assert.throws(() => env.resolve(o.catalog.ref, { operation: 'inspect_card', args: { subject: 'c1', extra: true } }), /no_match/);
});
check('binding_reference', 'ambiguous semantic proposal is not resolved by first-match', () => {
  const f = frame(1); f.actions.push({ ...clone(f.actions[0]), ref: 'second-binding' });
  const env = new SyntheticEnvironment(f); assert.throws(() => env.resolve(env.catalog, { operation: 'begin_card_play', args: { subject: 'b' } }), /ambiguous/);
});
check('history_reference', 'transport repeats do not advance memory; actual revisits do', () => {
  const m = new PublicMemory(), o = frame(1); m.consume(o); const prior = m.digest(); assert.equal(m.consume(o), false);
  assert.equal(m.digest(), prior); m.consume(frame(2)); assert.equal(m.read().length, 2);
  assert.throws(() => m.consume(frame(2, { page: 'changed' })), /same_event_changed/);
});
check('history_reference', 'missing event and changed generation require an explicit new segment', () => {
  const m = new PublicMemory(); m.consume(frame(1)); assert.throws(() => m.consume(frame(3)), /history_gap/);
  assert.throws(() => m.consume({ ...frame(2), generation: 'g2' }), /explicit_reset_required/);
});
const waitRequest = (extra = {}) => ({ id: 'w1', basis: { generation: 'g1', cursor: 1, clock: 10 },
  triggers: [{ mode: 'next_event', kind: 'choice_required' }], deadlineAfterBasisMs: 20, ...extra });
const waitFixture = (extra = {}) => ({ generation: 'g1', permission: true, retainedFrom: 1, now: 25, basisFrames: [frame(1)],
  latest: frame(2, { choiceReady: true }), events: [{ ...frame(2, { choiceReady: true }), kind: 'choice_required', clock: 20 }], ...extra });
check('await_reference', 'event during inference is replayed at registration', () => {
  const result = new AwaitReference().evaluate(waitRequest(), waitFixture()); assert.equal(result.state, 'matched'); assert.equal(result.matchedEvent, 'e2');
});
check('await_reference', 'already-true level condition differs from waiting for a new edge', () => {
  const f = waitFixture({ now: 15, latest: frame(1, { choiceReady: true }), events: [] });
  assert.equal(new AwaitReference().evaluate(waitRequest(), f).state, 'waiting');
  assert.equal(new AwaitReference().evaluate(waitRequest({ triggers: [{ mode: 'until_state', predicate: 'choice_required' }] }), f).state, 'already_true');
});
check('await_reference', 'events after the absolute basis deadline cannot restart the wait budget', () => {
  const late = { ...frame(2), clock: 35 };
  const f = waitFixture({ now: 40, latest: late, events: [{ ...late, kind: 'choice_required' }] });
  assert.equal(new AwaitReference().evaluate(waitRequest(), f).state, 'timeout');
});
check('await_reference', 'control abort wins before result sealing; replay keeps the sealed result', () => {
  const w = new AwaitReference(), req = waitRequest(); assert.equal(w.evaluate(req, waitFixture({ permission: false })).state, 'aborted');
  assert.equal(w.evaluate(req, waitFixture()).state, 'aborted');
});
check('await_reference', 'retention gaps and past matches are not current-state assertions', () => {
  assert.equal(new AwaitReference().evaluate(waitRequest(), waitFixture({ retainedFrom: 3 })).state, 'gap');
  const result = new AwaitReference().evaluate(waitRequest(), waitFixture({ now: 35, latest: frame(3, { choiceReady: false }), events: [
    { ...frame(2, { choiceReady: true }), kind: 'choice_required' },
    { ...frame(3, { choiceReady: false }), kind: 'other' },
  ] }));
  assert.deepEqual(result, { state: 'matched', matchedEvent: 'e2', latestEvent: 'e3' });
});
check('await_reference', 'only registered own predicates and declared event kinds are accepted', () => {
  for (const predicate of ['constructor', 'toString', '__proto__'])
    assert.throws(() => new AwaitReference().evaluate(waitRequest({ triggers: [{ mode: 'until_state', predicate }] }), waitFixture()), /await_trigger/);
  assert.throws(() => new AwaitReference().evaluate(waitRequest({ triggers: [{ mode: 'next_event', kind: 'private_unknown' }] }), waitFixture()), /await_trigger/);
});
check('await_reference', 'basis clock cannot be shifted to extend an old observation deadline', () => {
  const req = waitRequest({ basis: { generation: 'g1', cursor: 1, clock: 24 } });
  assert.throws(() => new AwaitReference().evaluate(req, waitFixture()), /await_basis_mismatch/);
});
check('await_reference', 'empty, interior-gap and duplicate buffers cannot assert quiet timeout', () => {
  const latest = frame(3);
  for (const events of [[], [{ ...latest, kind: 'other' }], [
    { ...frame(2), kind: 'other' }, { ...frame(2), kind: 'other' },
  ]]) assert.equal(new AwaitReference().evaluate(waitRequest(), waitFixture({ now: 40, latest, events })).state, 'gap');
});
check('scale_reference', '10000 catalog entries remain available without 10000 model scores', () => {
  const f = frame(1); f.actions = Array.from({ length: 10000 }, (_, i) => ({ ref: `a${i}`, operation: 'inspect_card', args: { subject: `c${i}` } }));
  const env = new SyntheticEnvironment(f), o = env.observe();
  const chosen = env.resolve(o.catalog.ref, { operation: 'inspect_card', args: { subject: 'c9999' } });
  assert.equal(chosen, 'a9999'); assert.equal(env.list(o.catalog.ref).total, 10000); assert.equal(env.value.actions.length, 10000);
  examples.push({ kind: 'synthetic_10000_lookup', authorityEntries: 10000, referenceCandidatesScored: 0,
    caveat: 'Host setup and this reference resolve scan are O(N); this is not neural generation or a sublinear performance claim.' });
});

const categories = Object.fromEntries([...new Set(checks.map(x => x.category))].map(k => [k, checks.filter(x => x.category === k).length]));
const report = {
  schema: 'ln-agent-protocol-reference-1', fixtureVersion: 1,
  evidence: 'synthetic_reference_only',
  sourceSha256: createHash('sha256').update(readFileSync(fileURLToPath(import.meta.url))).digest('hex'),
  runtime: { node: process.version, platform: process.platform, arch: process.arch },
  categories, checks, examples,
  limitations: [
    'Not a production port, complete native simulator, learned M2, language model, RL run or Human data qualification.',
    'Await is evaluated over a supplied deterministic event history; no concurrent broker, native capture or real timer is exercised.',
    'Boundary readiness is a fixture fact; no native quiescence or H capture accuracy is proved.',
    'All protocols and consumers use synthetic public objects; no private corpus or proprietary card text is accessed.',
    'Counts refer to toy-model callbacks; no latency, token, GPU-cost or strategy-quality result is measured.',
  ],
};
if (process.argv.slice(2).some(x => x !== '--json')) throw new Error('only --json is supported');
if (process.argv.includes('--json')) process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
else process.stdout.write(`L-N reference: ${checks.length} checks passed; categories ${JSON.stringify(categories)}. Synthetic only.\n`);
