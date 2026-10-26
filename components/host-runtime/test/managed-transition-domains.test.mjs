import assert from 'node:assert/strict';
import test from 'node:test';
import { decodePlayerSnapshot } from '@rsgcsg/sts2-connector-client';
import { projectManagedCandidateDecision } from '../src/managed-player-environment.mjs';
import { combatState } from './fixtures/managed-fair-combat.mjs';
import { selector } from './fixtures/managed-native-selector.mjs';
const identity = { runtimeInstanceId: 'transition-domain', environmentFingerprint: 'test', sequence: 1 };
const project = state => projectManagedCandidateDecision({ state, ...identity });

test('known enemy turn retains complete public facts and an empty settling action domain', () => {
  const state = combatState(); state.turn_owner = 'enemy'; state.is_play_phase = false;
  const value = project(state), snapshot = decodePlayerSnapshot(value.snapshot).data;
  assert.equal(snapshot.status, 'settling');
  assert.equal(snapshot.completeness.status, 'complete');
  assert.deepEqual(snapshot.completeness.missing, []);
  assert.equal(snapshot.bound_actions.status, 'complete');
  assert.deepEqual(snapshot.bound_actions.actions, []);
  assert.equal(value.bindings.size, 0);
  assert.deepEqual(snapshot.interaction.content.surface.playable_cards, []);
  assert.equal(snapshot.interaction.content.surface.can_end_turn, false);
  const incomplete = structuredClone(state); delete incomplete.player_statuses;
  assert.equal(project(incomplete).snapshot.completeness.status, 'partial');
  assert.equal(project(incomplete).snapshot.status, 'visible_unsupported');
});

test('exact native run-domain generated choice requires no invented combat HUD', () => {
  const state = selector({ choose: true });
  state.selector_domain = 'run'; state.combat_context = null; state.combat_piles = null;
  const value = project(state), snapshot = decodePlayerSnapshot(value.snapshot).data;
  assert.equal(snapshot.status, 'interactive');
  assert.equal(snapshot.completeness.status, 'complete');
  assert.equal(snapshot.interaction.content.context.kind, 'selection');
  assert.equal(snapshot.bound_actions.actions.length, state.cards.length);
  assert.deepEqual([...value.bindings.values()].map(x => x.raw_request.args.card_ref), state.cards.map(x => x.native_ref));
  const unspecified = structuredClone(state); delete unspecified.selector_domain;
  assert.equal(project(unspecified).snapshot.completeness.status, 'partial');
  const missingCard = structuredClone(state); delete missingCard.cards[0].public_details;
  assert.equal(project(missingCard).snapshot.completeness.status, 'partial');
});
