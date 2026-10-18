import assert from "node:assert/strict";
import test from "node:test";
import {
  chooseManagedPlayerEnvironmentAction,
  managedCapacityReportStatus,
  managedTerminalOutcome,
  summarizeManagedPlayerEnvironmentCapacityGroup
} from "../src/managed-player-environment-probe.mjs";

import { projectManagedCandidateDecision } from "../src/managed-player-environment.mjs";
import { combatState, renamePrivateCounters } from "./fixtures/managed-fair-combat.mjs";

function snapshot(kind, actions, status = "interactive") {
  return {
    status,
    interaction: { kind },
    bound_actions: { status: "complete", actions }
  };
}

test("managed Player Environment probe policy consumes only canonical actions", () => {
  const play = { bound_action_id: "play", verb: "play", label: "Play Strike" };
  const end = { bound_action_id: "end", verb: "end_turn", label: "End turn" };
  assert.equal(chooseManagedPlayerEnvironmentAction(snapshot("combat_turn", [end, play])), play);
  assert.equal(chooseManagedPlayerEnvironmentAction(snapshot("event_option", [
    { bound_action_id: "b", verb: "activate", label: "B" },
    { bound_action_id: "a", verb: "activate", label: "A" }
  ])).bound_action_id, "a");
  assert.equal(chooseManagedPlayerEnvironmentAction(snapshot("reward_claim", [
    { bound_action_id: "proceed", verb: "activate", label: "Skip remaining rewards and continue" },
    {
      bound_action_id: "take",
      verb: "activate",
      label: "Claim 17 gold",
      subject: { kind: "gold", label: "17 gold" }
    }
  ])).bound_action_id, "take");
  assert.equal(chooseManagedPlayerEnvironmentAction(snapshot("card_reward_selection", [
    { bound_action_id: "skip", verb: "skip", label: "Skip card reward" },
    { bound_action_id: "take-b", verb: "select", label: "Take Zap" },
    { bound_action_id: "take-a", verb: "select", label: "Take Defend" }
  ])).bound_action_id, "take-a");
  assert.equal(chooseManagedPlayerEnvironmentAction(snapshot("treasure_room", [
    { bound_action_id: "skip-relic", verb: "skip", label: "Skip treasure relic" },
    { bound_action_id: "take-relic", verb: "activate", label: "Take Bag" }
  ])).bound_action_id, "take-relic");
  assert.equal(chooseManagedPlayerEnvironmentAction(snapshot("shop_inventory", [], "visible_unsupported")), null);
});


test("managed probe chooses by public combat semantics across runtime and private-counter aliases", () => {
  for (const identityMode of ["crypto", "sequence"]) {
    const chosen = [];
    for (const swapped of [false, true]) {
      for (let runtime = 0; runtime < 8; runtime += 1) {
        const initial = combatState();
        initial.enemies[0].hp = swapped ? 10 : 5;
        initial.enemies[1].hp = swapped ? 5 : 10;
        const state = runtime % 2 === 0 ? initial : renamePrivateCounters(initial);
        const projection = projectManagedCandidateDecision({ state,
          runtimeInstanceId: `same-world-runtime-${runtime}`,
          environmentFingerprint: "same", sequence: runtime < 2 ? 1 : runtime - 1, identityMode });
        const action = chooseManagedPlayerEnvironmentAction(projection.snapshot);
        assert.ok(projection.snapshot.bound_actions.actions.includes(action));
        const request = projection.bindings.get(action.bound_action_id).raw_request;
        const targetOccurrence = state.enemies.findIndex((value) => value.native_ref === request.args.target_ref);
        assert.notEqual(targetOccurrence, -1);
        chosen.push({ swapped, runtime, targetOccurrence, hp: state.enemies[targetOccurrence].hp });
      }
    }
    assert.equal(new Set(chosen.map((value) => value.hp)).size, 1, JSON.stringify(chosen));
    assert.equal(new Set(chosen.filter((value) => !value.swapped).map((value) => value.targetOccurrence)).size, 1);
    assert.notEqual(chosen[0].targetOccurrence, chosen[8].targetOccurrence,
      "different visible HP remains semantic information, not an identity-normalized tie");
  }
});

test("managed probe keeps identical public occurrences separately bound and uses their catalog order for ties", () => {
  for (const identityMode of ["crypto", "sequence"]) {
    for (const reversePresentation of [false, true]) {
      for (let runtime = 0; runtime < 4; runtime += 1) {
        const state = combatState();
        if (reversePresentation) {
          state.hand.reverse();
          state.enemies.reverse();
        }
        const projection = projectManagedCandidateDecision({ state,
          runtimeInstanceId: `duplicate-world-runtime-${runtime}`,
          environmentFingerprint: "same", sequence: 1, identityMode });
        const play = projection.snapshot.bound_actions.actions.filter((value) => value.verb === "play");
        assert.equal(play.length, 4); // Two distinct cards, each with two distinct targets.
        assert.equal(new Set(play.map((value) => value.bound_action_id)).size, 4);
        const requests = play.map((value) => projection.bindings.get(value.bound_action_id).raw_request);
        assert.equal(new Set(requests.map((value) => value.args.card_ref)).size, 2);
        assert.equal(new Set(requests.map((value) => value.args.target_ref)).size, 2);
        assert.equal(new Set(requests.map((value) => JSON.stringify(value.args))).size, 4);
        const selected = chooseManagedPlayerEnvironmentAction(projection.snapshot);
        assert.equal(selected, play[0], "a semantic tie keeps the published complete catalog order");
        assert.equal(projection.snapshot.bound_actions.actions.filter((value) => value.verb === "play").length, 4);
        assert.equal(projection.bindings.size, 5); // Four play leaves plus end turn remain offered.
        assert.deepEqual(projection.bindings.get(selected.bound_action_id).raw_request.args,
          { card_ref: state.hand[0].native_ref, target_ref: state.enemies[0].native_ref });
      }
    }
  }
});

test("managed probe records native terminal outcome without treating generic terminal as victory", () => {
  assert.deepEqual(managedTerminalOutcome({
    interaction: {
      kind: "game_over",
      content: { surface: { victory: false } }
    },
    persistent: { content: { run: { act: 1, floor: 11 } } }
  }), { victory: false, act: 1, floor: 11 });
  assert.deepEqual(managedTerminalOutcome({
    interaction: { kind: "game_over", content: { surface: {} } }
  }), { victory: null, act: null, floor: null });
  assert.equal(managedTerminalOutcome(snapshot("combat_turn", [])), null);
});

function capacityWorker(runtimeId, decisions, start, end, status = "bounded_partial_player_environment_measured") {
  return {
    report: {
      status,
      candidate: {
        manifest: { candidate_id: "candidate" },
        build: {
          source_patch_sha256: "patch",
          artifact_sha256: "artifact",
          runtime_sts2_sha256: "game"
        },
        adapter_runtime_instance_id: runtimeId,
        environment_fingerprint: "environment"
      },
      game_identity: { version: "v0", commit: "exact", runtime_main_assembly_hash: 1 },
      episode: {
        failure: null,
        seed_provenance: "game_reported_match",
        episodes_requested: 1,
        episodes_completed: 1,
        canonical_actions_attempted: decisions,
        canonical_actions_delivered: decisions,
        canonical_reads_completed: decisions + 1,
        episodes: [{
          requested_seed: "seed-a",
          game_reported_seed: "seed-a",
          terminal: "game_over",
          terminal_outcome: { victory: false, act: 1, floor: 2 },
          canonical_actions_attempted: decisions,
          canonical_actions_delivered: decisions
        }]
      },
      performance: {
        decision_window_started_ms: start,
        decision_window_ended_ms: end,
        decision_window_started_epoch_ms: 10_000 + start,
        decision_window_ended_epoch_ms: 10_000 + end,
        process_startup_seconds: 1,
        reset_inclusive_decision_window_seconds: (end - start) / 1000,
        delivered_decisions_per_second: decisions / ((end - start) / 1000),
        peak_rss_bytes: 100,
        resource_sample_errors: [],
        stage_totals: {},
        child_process: { cpu_ms: decisions }
      },
      process: { exit: { code: 0 }, diagnostics: [] },
      events: [{
        type: "action",
        episode_index: 0,
        action_index: decisions - 1,
        canonical_action: { label: `last-${runtimeId}` },
        delivery: "delivered",
        reason_code: null,
        detail: "settled"
      }]
    }
  };
}

test("canonical capacity aggregation requires one artifact and distinct runtimes", () => {
  const summary = summarizeManagedPlayerEnvironmentCapacityGroup([
    capacityWorker("runtime-a", 100, 100, 2_100),
    capacityWorker("runtime-b", 100, 110, 2_110)
  ], 3);
  assert.equal(summary.status, "measured_canonical_partial_unqualified");
  assert.equal(summary.delivered_canonical_decisions, 200);
  assert.equal(summary.completed_canonical_reads, 202);
  assert.equal(summary.aggregate_reset_inclusive_canonical_decisions_per_second, 200 / 2.01);
  assert.ok(Math.abs(summary.child_cpu_seconds - 0.2) < 1e-12);
  assert.equal(summary.reliability.cross_worker_episode_consistency, "pass");
  assert.equal(summary.workers[0].last_action.canonical_action.label, "last-runtime-a");
  assert.equal(summary.workers[0].last_action.detail, "settled");
  const completeSummary = summarizeManagedPlayerEnvironmentCapacityGroup([
    capacityWorker("runtime-c", 100, 100, 2_100, "bounded_player_environment_measured"),
    capacityWorker("runtime-d", 100, 110, 2_110, "bounded_player_environment_measured")
  ], 3);
  assert.equal(completeSummary.status, "measured_canonical_unqualified");
  const trainingSummary = summarizeManagedPlayerEnvironmentCapacityGroup([
    capacityWorker("runtime-e", 100, 100, 2_100, "bounded_training_profile_measured"),
    capacityWorker("runtime-f", 100, 110, 2_110, "bounded_training_profile_measured")
  ], 3);
  assert.equal(trainingSummary.status, "measured_training_profile_unqualified");
  const divergentSummary = summarizeManagedPlayerEnvironmentCapacityGroup([
    capacityWorker("runtime-g", 100, 100, 2_100),
    capacityWorker("runtime-h", 101, 110, 2_110)
  ], 3);
  assert.equal(divergentSummary.status, "measurement_incomplete");
  assert.equal(divergentSummary.reliability.cross_worker_episode_consistency, "fail");
  assert.throws(() => summarizeManagedPlayerEnvironmentCapacityGroup([
    capacityWorker("runtime-a", 1, 0, 1),
    capacityWorker("runtime-a", 1, 0, 1)
  ], 1), /distinct runtime instance IDs/u);
});

test("capacity report aggregation preserves complete, partial, and unrecorded evidence", () => {
  assert.equal(managedCapacityReportStatus([
    { status: "measured_canonical_unqualified" },
    { status: "measured_canonical_unqualified" }
  ]), "measured_canonical_unqualified");
  assert.equal(managedCapacityReportStatus([
    { status: "measured_canonical_unqualified" },
    { status: "measured_canonical_partial_unqualified" }
  ]), "measured_canonical_partial_unqualified");
  assert.equal(managedCapacityReportStatus([
    { status: "measured_training_profile_unqualified" },
    { status: "measured_training_profile_unqualified" }
  ]), "measured_training_profile_unqualified");
  assert.equal(managedCapacityReportStatus([
    { status: "measured_training_profile_unqualified" },
    { status: "measured_canonical_unqualified" }
  ]), "measurement_incomplete");
});
