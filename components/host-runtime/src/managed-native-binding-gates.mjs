import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { startManagedCandidateRuntime } from "./managed-candidate.mjs";
import { projectManagedCandidateDecision } from "./managed-player-environment.mjs";
import { readProjectIdentity } from "./project-identity.mjs";
import { readSystemIdentity } from "./system-identity.mjs";

function safeTimestamp() {
  return new Date().toISOString().replaceAll(":", "-").replaceAll(".", "-");
}

function refused(response, pattern) {
  return response?.type === "error" && pattern.test(String(response.message ?? ""));
}

function firstPlayableCard(state) {
  return (state?.hand ?? []).find((card) => {
    if (card.can_play !== true || typeof card.native_ref !== "string") return false;
    if (card.target_type !== "AnyEnemy") return true;
    const targets = new Set(card.valid_target_refs ?? []);
    return (state.enemies ?? []).some((enemy) => targets.has(enemy.native_ref));
  }) ?? null;
}

function cardRequest(state, card) {
  const args = { card_ref: card.native_ref };
  if (card.target_type === "AnyEnemy") {
    const targets = new Set(card.valid_target_refs ?? []);
    const target = (state.enemies ?? []).find((enemy) => targets.has(enemy.native_ref));
    if (target == null) return null;
    args.target_ref = target.native_ref;
  }
  return { cmd: "action", action: "play_card", args };
}

export async function runManagedNativeBindingGates({
  root,
  candidateDirectory,
  diskIdentity,
  seed = "H1NATIVEBINDING01",
  evidenceRoot = null,
  requestTimeoutMs = 10_000
}) {
  const runtime = await startManagedCandidateRuntime({
    root,
    candidateDirectory,
    diskIdentity,
    requestTimeoutMs
  });
  const process = runtime.process;
  const gates = {};
  let failure = null;
  let exit = null;
  try {
    let state = await process.request({
      cmd: "start_run",
      character: "Ironclad",
      seed,
      lang: "zh"
    }, requestTimeoutMs);
    const mapPoint = state?.choices?.[0];
    if (state?.decision !== "map_select" || mapPoint == null) {
      throw new Error("Managed gate did not start at a map decision.");
    }

    const wrongMap = await process.request({
      cmd: "action",
      action: "select_map_node",
      args: { col: mapPoint.col, row: mapPoint.row, map_point_ref: "wrong-native-ref" }
    }, requestTimeoutMs);
    gates.wrong_map_identity_refused = refused(wrongMap, /no longer matches/u);

    const scenario = await process.request({ cmd: "set_player", potions: ["FIRE_POTION"] }, requestTimeoutMs);
    gates.privileged_setup_acknowledged = scenario?.type === "ok";

    state = await process.request({
      cmd: "action",
      action: "select_map_node",
      args: { col: mapPoint.col, row: mapPoint.row, map_point_ref: mapPoint.native_ref }
    }, requestTimeoutMs);
    gates.map_native_commit_reached_combat = state?.decision === "combat_play";
    const visibleIntents = (state?.enemies ?? []).flatMap((enemy) => enemy.intents ?? []);
    gates.native_intent_localization_observed = visibleIntents.length > 0;
    gates.native_intent_localization_formatted = visibleIntents.every((intent) =>
      !/^FORMAT_/u.test(String(intent.label ?? ""))
      && !/\.(?:title|description)$/u.test(String(intent.title ?? ""))
      && !/\.(?:title|description)$/u.test(String(intent.description ?? "")));

    const card = firstPlayableCard(state);
    if (card == null) throw new Error("Managed gate found no natively playable combat card.");
    const wrongCard = await process.request({
      cmd: "action",
      action: "play_card",
      args: { card_ref: "wrong-native-ref" }
    }, requestTimeoutMs);
    gates.wrong_card_identity_refused = refused(wrongCard, /no longer in the current hand/u);

    const potion = state?.player?.potions?.find((entry) => entry?.can_use === true);
    const potionTarget = (state?.enemies ?? []).find((enemy) =>
      (potion?.valid_target_refs ?? []).includes(enemy.native_ref));
    gates.potion_native_actionability_observed = Boolean(potion?.native_ref && potionTarget?.native_ref);
    if (!gates.potion_native_actionability_observed) {
      throw new Error("Managed gate did not expose an actionable Fire Potion with a native target.");
    }

    const wrongPotion = await process.request({
      cmd: "action",
      action: "use_potion",
      args: {
        potion_slot: potion.slot,
        potion_ref: "wrong-native-ref",
        target_ref: potionTarget.native_ref
      }
    }, requestTimeoutMs);
    gates.wrong_potion_identity_refused = refused(wrongPotion, /no longer in the bound belt slot/u);

    state = await process.request({
      cmd: "action",
      action: "use_potion",
      args: {
        potion_slot: potion.slot,
        potion_ref: potion.native_ref,
        target_ref: potionTarget.native_ref
      }
    }, requestTimeoutMs);
    gates.potion_native_commit_returned_successor = state?.type === "decision";
    gates.exact_potion_object_removed = !(state?.player?.potions ?? [])
      .some((entry) => entry?.native_ref === potion.native_ref);

    const currentCard = firstPlayableCard(state);
    const request = currentCard == null ? null : cardRequest(state, currentCard);
    if (request == null) throw new Error("Managed gate could not bind a post-potion card action.");
    const cardRef = currentCard.native_ref;
    state = await process.request(request, requestTimeoutMs);
    gates.card_native_commit_returned_successor = state?.type === "decision";
    gates.exact_card_object_left_hand = !(state?.hand ?? [])
      .some((entry) => entry?.native_ref === cardRef);

    state = await process.request({
      cmd: "reset_run",
      character: "Ironclad",
      seed: `${seed}TREASURE`,
      lang: "zh"
    }, requestTimeoutMs);
    gates.reset_for_treasure_mounted = state?.decision === "map_select";
    state = await process.request({ cmd: "enter_room", type: "treasure" }, requestTimeoutMs);
    gates.treasure_chest_visible = state?.decision === "treasure_chest"
      && typeof state.room_ref === "string";
    if (!gates.treasure_chest_visible) {
      throw new Error("Managed gate did not expose a closed treasure chest.");
    }
    const treasureRoomRef = state.room_ref;
    const relicCountBefore = state.player?.relics?.length ?? null;
    let projection = projectManagedCandidateDecision({
      state,
      runtimeInstanceId: runtime.adapterRuntimeInstanceId,
      environmentFingerprint: "privileged-managed-native-gate",
      sequence: 1
    });
    gates.treasure_chest_canonical_projection_complete = projection.snapshot.status === "interactive"
      && projection.snapshot.bound_actions.actions.length === 1;
    gates.treasure_room_operand_remained_host_local = !JSON.stringify(projection.snapshot)
      .includes(treasureRoomRef);
    const wrongTreasureRoom = await process.request({
      cmd: "action",
      action: "open_treasure",
      args: { room_ref: "wrong-native-ref" }
    }, requestTimeoutMs);
    gates.wrong_treasure_room_identity_refused = refused(wrongTreasureRoom, /no longer current/u);

    const openBinding = [...projection.bindings.values()][0];
    state = await process.request(openBinding.raw_request, requestTimeoutMs);
    gates.treasure_opened_to_relic_selection = state?.decision === "treasure_relic";
    const treasureRelic = state?.relics?.[0];
    gates.treasure_native_relic_identity_observed = typeof treasureRelic?.native_ref === "string";
    if (!gates.treasure_native_relic_identity_observed) {
      throw new Error("Managed gate did not expose an exact native treasure relic.");
    }
    projection = projectManagedCandidateDecision({
      state,
      runtimeInstanceId: runtime.adapterRuntimeInstanceId,
      environmentFingerprint: "privileged-managed-native-gate",
      sequence: 2
    });
    gates.treasure_relic_canonical_projection_complete = projection.snapshot.status === "interactive"
      && projection.snapshot.interaction.kind === "treasure_room" && projection.snapshot.interaction.stage === "relic_choice";
    gates.treasure_relic_operand_remained_host_local = !JSON.stringify(projection.snapshot)
      .includes(treasureRelic.native_ref);
    const wrongTreasureRelic = await process.request({
      cmd: "action",
      action: "select_treasure_relic",
      args: { relic_ref: "wrong-native-ref" }
    }, requestTimeoutMs);
    gates.wrong_treasure_relic_identity_refused = refused(wrongTreasureRelic, /no longer selectable/u);

    const selectBinding = [...projection.bindings.values()]
      .find((binding) => binding.action.verb === "activate");
    if (selectBinding == null) throw new Error("Canonical treasure projection omitted the select action.");
    state = await process.request(selectBinding.raw_request, requestTimeoutMs);
    gates.treasure_native_vote_returned_completion = state?.decision === "treasure_complete";
    gates.treasure_award_changed_player_inventory = Number.isSafeInteger(relicCountBefore)
      && state?.player?.relics?.length === relicCountBefore + 1;
    projection = projectManagedCandidateDecision({
      state,
      runtimeInstanceId: runtime.adapterRuntimeInstanceId,
      environmentFingerprint: "privileged-managed-native-gate",
      sequence: 3
    });
    gates.treasure_completion_canonical_projection_complete = projection.snapshot.status === "interactive"
      && projection.snapshot.interaction.kind === "treasure_room" && projection.snapshot.interaction.stage === "completed";
    const leaveBinding = [...projection.bindings.values()][0];
    state = await process.request(leaveBinding.raw_request, requestTimeoutMs);
    gates.treasure_proceed_returned_map = state?.decision === "map_select";
  } catch (error) {
    failure = error instanceof Error ? error.message : String(error);
  } finally {
    exit = await process.stop({ request: { cmd: "quit" }, timeoutMs: 5_000 });
  }

  const allPassed = failure == null
    && Object.values(gates).length === 26
    && Object.values(gates).every((value) => value === true)
    && exit?.code === 0;
  const report = {
    schema: "sts2.headless/managed-native-binding-gates-1",
    generated_at: new Date().toISOString(),
    status: allPassed ? "pass" : "fail",
    headless: readProjectIdentity(root),
    system_identity: readSystemIdentity(),
    candidate: {
      manifest: runtime.manifest,
      build: runtime.build,
      runtime_identity: runtime.runtimeIdentity,
      adapter_runtime_instance_id: runtime.adapterRuntimeInstanceId
    },
    exact_game: runtime.exactGame,
    scenario: {
      seed,
      setup: "privileged_set_player_fire_potion",
      gates,
      failure
    },
    process: { exit, diagnostics: process.diagnostics },
    non_claims: [
      "Privileged scenario setup is targeted runtime evidence, not a fair-player journey.",
      "These gates do not establish cross-Host semantic equivalence or H1.0 qualification.",
      "A passing delivery gate does not prove business completion beyond the observed successor."
    ]
  };
  let reportFile = null;
  if (evidenceRoot != null) {
    const directory = path.join(evidenceRoot, `managed-native-binding-gates-${safeTimestamp()}`);
    mkdirSync(directory, { recursive: true });
    reportFile = path.join(directory, "report.json");
    writeFileSync(reportFile, `${JSON.stringify(report, null, 2)}\n`);
  }
  return { report, reportFile };
}
