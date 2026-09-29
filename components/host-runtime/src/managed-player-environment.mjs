import { createHash } from "node:crypto";
import { performance } from "node:perf_hooks";
import {
  SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL,
  decodePlayerRead,
  decodePlayerReceipt,
  decodePlayerSnapshot
} from "@rsgcsg/sts2-connector-client";
import { startManagedCandidateRuntime } from "./managed-candidate.mjs";

const ACTION_LIMIT = 512;
// Pinned native TargetType values supported by the current Managed combat
// inputs. AnyAlly lacks a complete published single-player target domain.
// Potion values must also agree with the exact native binding switch; its
// TargetedNoCreature case needs a non-creature target referent not projected here.
const SUPPORTED_MANAGED_CARD_TARGET_TYPES = new Set([
  "None", "Self", "AnyEnemy", "AllEnemies", "RandomEnemy", "AnyPlayer",
  "AllAllies", "TargetedNoCreature", "Osty"
]);
const SUPPORTED_MANAGED_POTION_TARGET_TYPES = new Set([
  "None", "Self", "AnyEnemy", "AllEnemies", "RandomEnemy", "AllAllies"
]);
const INFORMATION_POLICY = Object.freeze({
  id: "player_visible_v1",
  scope: "Information currently presented by, or normally inspectable through, the local player's game UI.",
  includes_hidden_information: false,
  unknown_field_behavior: "omit_and_mark_incomplete"
});

function plainObject(value) {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

function canonicalValue(value) {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (!plainObject(value)) return value;
  return Object.fromEntries(Object.keys(value).sort()
    .map((key) => [key, canonicalValue(value[key])]));
}

function digest(value) {
  return createHash("sha256").update(JSON.stringify(canonicalValue(value))).digest("hex");
}

function stableId(prefix, ...parts) {
  return `${prefix}_${digest(parts).slice(0, 24)}`;
}

function supportsManagedCardTargetType(targetType) {
  return SUPPORTED_MANAGED_CARD_TARGET_TYPES.has(targetType);
}

function supportsManagedPotionTargetType(targetType) {
  return SUPPORTED_MANAGED_POTION_TARGET_TYPES.has(targetType);
}

function definitionId(value) {
  const text = String(value ?? "UNKNOWN");
  const entry = text.includes(".") ? text.slice(text.lastIndexOf(".") + 1) : text;
  return entry.trim().replace(/[^a-z0-9]+/giu, "_").replace(/^_+|_+$/gu, "").toUpperCase() || "UNKNOWN";
}

function visibleState({ enabled = null, selected = null } = {}) {
  return {
    visible: true,
    ...(enabled == null ? {} : { enabled }),
    ...(selected == null ? {} : { selected }),
    observation_basis: "native_visible_fact"
  };
}

function createProjectionContext({ state, runtimeInstanceId, sequence, identityMode = "crypto" }) {
  if (!["crypto", "sequence"].includes(identityMode)) {
    throw new TypeError("Managed projection identityMode must be crypto or sequence.");
  }
  const rawStateSha = identityMode === "crypto" ? digest(state) : null;
  const snapshotId = identityMode === "crypto"
    ? `managed_${runtimeInstanceId}_s${sequence}_${rawStateSha}`
    : `managed_${runtimeInstanceId}_s${sequence}`;
  const interactionId = identityMode === "crypto"
    ? `interaction_${runtimeInstanceId}_s${sequence}_${rawStateSha}`
    : `interaction_${runtimeInstanceId}_s${sequence}`;
  const referents = [];
  const actions = [];
  const bindings = new Map();
  let localIdentity = 0;

  function makeId(prefix, ...parts) {
    if (identityMode === "crypto") return stableId(prefix, ...parts);
    localIdentity += 1;
    return `${prefix}_${runtimeInstanceId}_s${sequence}_${localIdentity}`;
  }

  function referent({
    role,
    label,
    properties,
    enabled = null,
    selected = null,
    occurrence = 0,
    id = null,
    includeEntityId = true
  }) {
    const referentId = id ?? makeId("ref", snapshotId, role, properties, occurrence);
    const value = {
      referent_id: referentId,
      role,
      kind: "entity",
      label: label == null ? null : String(label),
      state: visibleState({ enabled, selected }),
      properties_schema: `sts2.player-environment/referent/${role.replace(/[^a-z0-9_]+/giu, "_").toLowerCase()}-1`,
      properties: { ...properties, ...(includeEntityId ? { entity_id: referentId } : {}) }
    };
    referents.push(value);
    return value;
  }

  function action({ verb, subject = null, arguments: actionArguments = [], label, raw }) {
    const descriptor = {
      verb,
      subject_referent_id: subject?.referent_id ?? null,
      arguments: actionArguments.map(({ role, referent }) => ({
        role,
        referent_id: referent.referent_id
      })),
      label
    };
    const boundActionId = makeId("action", snapshotId, descriptor, raw);
    const value = {
      bound_action_id: boundActionId,
      verb,
      interaction_id: interactionId,
      subject_referent_id: descriptor.subject_referent_id,
      arguments: descriptor.arguments,
      label
    };
    actions.push(value);
    bindings.set(boundActionId, Object.freeze({
      expected_raw_state_sha256: rawStateSha,
      raw_request: canonicalValue(raw),
      action: value
    }));
    return value;
  }

  return {
    state,
    sequence,
    rawStateSha,
    snapshotId,
    interactionId,
    referents,
    actions,
    bindings,
    id: makeId,
    referent,
    action
  };
}

function withoutDeck(player) {
  if (!plainObject(player)) return null;
  const {
    deck: _deck,
    native_ref: _nativeRef,
    relics = [],
    potions = [],
    ...visible
  } = player;
  const publicPersistentItem = (item) => {
    const {
      native_ref: _nativeItemRef,
      valid_target_refs: _validTargetRefs,
      binding_supported: _bindingSupported,
      hover_facts_complete: _hoverFactsComplete,
      vars: _dynamicVariables,
      ...publicItem
    } = item;
    return publicItem;
  };
  return {
    ...visible,
    relics: relics.map(publicPersistentItem),
    potions: potions.map(publicPersistentItem)
  };
}

function cardProperties(card) {
  return {
    definition_id: definitionId(card?.id ?? card?.name),
    name: card?.name ?? null,
    type: card?.type ?? "Unknown",
    cost: String(card?.cost ?? "?"),
    rarity: card?.rarity ?? "Unknown",
    is_upgraded: card?.upgraded === true || card?.is_upgraded === true,
    description: card?.description ?? null,
    target_type: card?.target_type ?? null,
    can_play: card?.can_play ?? null,
    stats: card?.stats ?? null,
    keywords: card?.keywords ?? null,
    after_upgrade: card?.after_upgrade ?? null
  };
}

function mapPointType(value) {
  return String(value ?? "unknown").toLowerCase();
}

function mapCoordinateKey(col, row) {
  return `${col},${row}`;
}

function buildPersistentVisibleState(state, snapshotId, makeId = stableId) {
  const run = state.context;
  const player = state.player;
  if (!plainObject(run) || !plainObject(player)) {
    return { content: null, complete: false, missing: ["canonical_persistent_run_identity"] };
  }
  const bosses = Array.isArray(run.bosses) ? run.bosses : [];
  const modifiers = Array.isArray(run.modifiers) ? run.modifiers : [];
  const relics = Array.isArray(player.relics) ? player.relics : [];
  const potions = Array.isArray(player.potions) ? player.potions : [];
  const requiredScalars = [
    run.act,
    run.act_definition_id,
    run.act_name,
    run.total_floor,
    run.ascension,
    player.native_ref,
    player.character_id,
    player.name,
    player.hp,
    player.max_hp,
    player.gold,
    player.max_potion_slots
  ];
  const scalarIdentityComplete = requiredScalars.every((value) => value != null)
    && bosses.every((boss) => typeof boss?.id === "string" && Number.isSafeInteger(boss?.order));
  const entityIdentityComplete = relics.every((relic) =>
    typeof relic?.native_ref === "string" && typeof relic?.id === "string")
    && potions.every((potion) =>
      typeof potion?.native_ref === "string" && typeof potion?.id === "string");
  const hasCompleteHoverFacts = (item) => item?.hover_facts_complete === true
    && Array.isArray(item?.keywords)
    && Array.isArray(item?.card_previews);
  const stableDetailsComplete = [...relics, ...potions, ...modifiers].every(hasCompleteHoverFacts);
  const complete = scalarIdentityComplete && entityIdentityComplete && stableDetailsComplete;
  if (!complete) {
    return {
      content: null,
      complete: false,
      missing: [
        ...(scalarIdentityComplete ? [] : ["canonical_persistent_run_identity"]),
        ...(entityIdentityComplete ? [] : ["native_persistent_entity_identity"]),
        ...(stableDetailsComplete ? [] : ["persistent_hover_or_modifier_facts"])
      ]
    };
  }
  const playerEntityId = makeId("player", snapshotId, player.native_ref);
  return {
    complete: true,
    missing: [],
    content: {
      content_schema: "sts2.player-environment/persistent/run-player-1",
      content: {
        scope: "active_single_player_run",
        run: {
          act: run.act,
          act_definition_id: definitionId(run.act_definition_id),
          act_name: run.act_name,
          floor: run.total_floor,
          ascension: run.ascension,
          bosses: bosses.map((boss) => ({
            definition_id: definitionId(boss.id),
            name: boss.name ?? null,
            order: boss.order
          })),
          modifiers: modifiers.map((modifier, index) => ({
            definition_id: definitionId(modifier.id ?? modifier.name),
            name: modifier.name ?? null,
            description: visibleText(modifier.description),
            keywords: visibleHoverKeywords(modifier.keywords),
            card_previews: visibleHoverCards(modifier.card_previews, snapshotId, `modifier_${index}`, makeId)
          }))
        },
        player: {
          entity_id: playerEntityId,
          character_definition_id: definitionId(player.character_id),
          character_name: player.name,
          hp: player.hp,
          max_hp: player.max_hp,
          gold: player.gold,
          relics: relics.map((relic, index) => ({
            entity_id: makeId("relic", snapshotId, relic.native_ref, index),
            definition_id: definitionId(relic.id),
            name: relic.name ?? null,
            description: relic.description ?? null,
            ...(relic.counter == null ? {} : { counter: relic.counter }),
            keywords: visibleHoverKeywords(relic.keywords),
            card_previews: visibleHoverCards(relic.card_previews, snapshotId, `relic_${index}`, makeId)
          })),
          potions: potions.map((potion, index) => ({
            entity_id: makeId("potion", snapshotId, potion.native_ref, index),
            definition_id: definitionId(potion.id),
            name: potion.name ?? null,
            description: potion.description ?? null,
            slot: potion.slot,
            keywords: visibleHoverKeywords(potion.keywords),
            card_previews: visibleHoverCards(potion.card_previews, snapshotId, `potion_${index}`, makeId)
          })),
          max_potion_slots: player.max_potion_slots
        },
        completeness: {
          player_visible_semantics: "complete_for_strategy_relevant_persistent_single_player_hud",
          sources: [
            "RunState.CurrentActIndex+Act+TotalFloor+AscensionLevel+Modifiers",
            "NTopBar+NTopBarBossIcon+NTopBarFloorIcon+NTopBarHp+NTopBarGold",
            "NRelicInventory+NPotionContainer+LocalContext.GetMe"
          ],
          missing: []
        }
      }
    }
  };
}

function buildVisibleMap(state, ctx) {
  const map = state.visible_map;
  if (!plainObject(map) || map.type !== "map" || !Array.isArray(map.rows)) return null;
  const choices = Array.isArray(state.choices) ? state.choices : [];
  const choicesByCoord = new Map(choices.map((choice) => [
    mapCoordinateKey(choice.col, choice.row),
    choice
  ]));
  const rows = map.rows.flatMap((row) => Array.isArray(row) ? row : []);
  const nodesByCoord = new Map(rows.map((node) => [
    mapCoordinateKey(node.col, node.row),
    node
  ]));
  for (const choice of choices) {
    const key = mapCoordinateKey(choice.col, choice.row);
    nodesByCoord.set(key, { ...(nodesByCoord.get(key) ?? {}), ...choice });
  }
  if (plainObject(map.boss)) {
    const key = mapCoordinateKey(map.boss.col, map.boss.row);
    nodesByCoord.set(key, { ...(nodesByCoord.get(key) ?? {}), ...map.boss, children: [] });
  }
  const rawNodes = [...nodesByCoord.values()];
  const typeByCoord = new Map(rawNodes.map((node) => [
    mapCoordinateKey(node.col, node.row),
    mapPointType(node.type)
  ]));
  const nodes = [];
  const nextOptions = [];
  let nativeIdentityComplete = true;
  let topologyComplete = true;
  for (const node of rawNodes.sort((left, right) => left.row - right.row || left.col - right.col)) {
    const key = mapCoordinateKey(node.col, node.row);
    const choice = choicesByCoord.get(key);
    const children = [...(node.children ?? [])]
      .sort((left, right) => left.row - right.row || left.col - right.col)
      .map((child) => {
      const childKey = mapCoordinateKey(child.col, child.row);
      const pointType = mapPointType(child.type ?? typeByCoord.get(childKey));
      if (child.type == null && !typeByCoord.has(childKey)) topologyComplete = false;
      return { col: child.col, row: child.row, point_type: pointType };
      });
    const pointType = mapPointType(node.type);
    const nodeState = choice != null
      ? "travelable"
      : node.current === true || node.visited === true ? "traveled" : "untravelable";
    let referent;
    if (choice != null) {
      nativeIdentityComplete = nativeIdentityComplete
        && typeof choice.native_ref === "string" && choice.native_ref.length > 0;
      referent = ctx.referent({
        role: "option",
        label: null,
        properties: { col: node.col, row: node.row, point_type: pointType }
      });
      nextOptions.push(referent.properties);
      ctx.action({
        verb: "activate",
        subject: referent,
        label: `Choose ${pointType} at (${node.col},${node.row})`,
        raw: {
          cmd: "action",
          action: "select_map_node",
          args: { col: node.col, row: node.row, map_point_ref: choice.native_ref }
        }
      });
    } else {
      referent = ctx.referent({
        role: "node",
        label: null,
        properties: {
          col: node.col,
          row: node.row,
          point_type: pointType,
          state: nodeState,
          children
        }
      });
    }
    nodes.push({
      col: node.col,
      row: node.row,
      point_type: pointType,
      state: nodeState,
      children,
      entity_id: referent.referent_id
    });
  }
  const coordinate = (value) => value == null ? null : ({
    col: value.col,
    row: value.row,
    point_type: typeByCoord.get(mapCoordinateKey(value.col, value.row)) ?? null
  });
  const current = coordinate(map.current_coord);
  const visited = rawNodes.filter((node) => node.visited === true).map(coordinate);
  return {
    surface: {
      kind: "map_navigation",
      travel_enabled: true,
      traveling: false,
      drawing_mode: "none",
      next_options: nextOptions,
      can_exit_annotation: false
    },
    context: {
      kind: "map",
      act_index: state.context?.act_index,
      ...(current == null ? {} : { current_position: current }),
      visited,
      nodes
    },
    complete: nativeIdentityComplete && topologyComplete && rawNodes.length > 1,
    missing: [
      ...(nativeIdentityComplete ? [] : ["native_map_point_identity"]),
      ...(topologyComplete && rawNodes.length > 1 ? [] : ["full_visible_map_topology"])
    ]
  };
}

function visibleText(value) {
  return value == null
    ? null
    : String(value).replace(/\[\/?[a-z_][a-z0-9_=]*\]/giu, "").replace(/\n/gu, " ");
}

function visibleHoverKeywords(keywords) {
  return keywords.map((keyword) => ({
    name: visibleText(keyword?.name),
    description: visibleText(keyword?.description)
  }));
}

function visibleHoverCards(cards, snapshotId, ownerKey, makeId) {
  return cards.map((card, index) => visibleCombatCard(
    card,
    makeId("hover_card", snapshotId, ownerKey, index, card?.id, card?.is_upgraded)
  ));
}

function visibleCombatCard(card, entityId) {
  return {
    entity_id: entityId,
    definition_id: definitionId(card?.id ?? card?.name),
    name: card?.name ?? null,
    type: card?.type ?? "Unknown",
    cost: String(card?.cost ?? "?"),
    ...(card?.star_cost == null ? {} : { star_cost: String(card.star_cost) }),
    description: visibleText(card?.description),
    rarity: card?.rarity ?? "Unknown",
    is_upgraded: card?.is_upgraded === true || card?.upgraded === true,
    is_selected: false,
    ...(card?.enchantment == null ? {} : {
      enchantment: {
        definition_id: definitionId(card.enchantment_id ?? card.enchantment),
        name: card.enchantment,
        description: card.enchantment_description ?? null,
        amount: card.enchantment_amount ?? 0,
        visibility_basis: "card_hover_semantics"
      }
    }),
    target_type: card?.target_type ?? null,
    can_play: card?.can_play ?? null,
    ...(card?.unplayable_reason == null ? {} : { unplayable_reason: card.unplayable_reason })
  };
}

function visibleStatus(status) {
  return {
    definition_id: definitionId(status?.id ?? status?.name),
    name: status?.name ?? null,
    amount: status?.amount ?? 0,
    type: status?.type ?? "Unknown",
    description: visibleText(status?.description)
  };
}

function visibleIntent(intent) {
  return {
    type: intent?.type ?? "Unknown",
    label: visibleText(intent?.label),
    title: visibleText(intent?.title),
    description: visibleText(intent?.description)
  };
}

function currentSurface(state, ctx) {
  const commonContext = {
    kind: "run",
    run: state.context ?? null,
    player: withoutDeck(state.player)
  };
  const supported = {
    complete: true,
    missing: [],
    visibleInformation: "contract_complete_for_current_native_interaction",
    interactionDiscovery: "derived_from_same_current_native_interaction_as_execution"
  };

  switch (state.decision) {
    case "map_select": {
      const visibleMap = buildVisibleMap(state, ctx);
      if (visibleMap == null) {
        return {
          kind: "map_navigation",
          stage: "ready",
          prompt: null,
          surface: { kind: "map_navigation", stage: "ready", next_options: [] },
          context: { kind: "map", nodes: [], visited: [] },
          complete: false,
          missing: ["full_visible_map_topology"]
        };
      }
      return {
        kind: "map_navigation",
        stage: "ready",
        prompt: null,
        ...visibleMap,
        visibleInformation: "contract_complete_for_visible_singleplayer_map_navigation",
        interactionDiscovery: "derived_from_exact_current_travelable_map_point_controls"
      };
    }
    case "event_choice": {
      const currentOptions = Array.isArray(state.options) ? state.options : [];
      const optionRefs = new Set();
      const complete = typeof state.room_ref === "string" && state.room_ref.length > 0
        && typeof state.event_ref === "string" && state.event_ref.length > 0
        && currentOptions.length > 0 && currentOptions.length <= ACTION_LIMIT
        && currentOptions.every((option, index) => option?.index === index
          && typeof option.native_ref === "string" && option.native_ref.length > 0
          && !optionRefs.has(option.native_ref) && optionRefs.add(option.native_ref)
          && typeof option.is_locked === "boolean"
          && typeof option.title === "string" && option.title.length > 0);
      const options = currentOptions.map((option, index) => {
        const unlocked = option?.is_locked === false;
        const item = ctx.referent({
          role: "option",
          label: option?.title ?? `Unavailable option ${index + 1}`,
          enabled: unlocked && complete,
          occurrence: index,
          properties: {
            index: option?.index ?? index,
            title: option?.title ?? null,
            description: option?.description ?? null,
            text_key: option?.text_key ?? null,
            is_locked: option?.is_locked ?? null,
            variables: option?.vars ?? null
          }
        });
        if (unlocked && complete) {
          ctx.action({
            verb: "activate",
            subject: item,
            label: String(option.title ?? `Choose option ${index + 1}`),
            raw: { cmd: "action", action: "choose_option", args: {
              option_index: option.index, room_ref: state.room_ref,
              event_ref: state.event_ref, option_ref: option.native_ref
            } }
          });
        }
        return item.properties;
      });
      return {
        kind: "event_option",
        stage: "choosing",
        prompt: state.description ?? null,
        surface: {
          kind: "event_option",
          stage: "choosing",
          title: state.event_name ?? null,
          description: state.description ?? null,
          options
        },
        context: { ...commonContext, kind: "event" },
        ...supported,
        complete,
        missing: complete ? [] : ["exact_current_event_option_binding"]
      };
    }
    case "rest_site": {
      const exactOptions = (state.options ?? []).every((option, index) =>
        option.index === index && typeof option.native_ref === "string" && option.native_ref.length > 0);
      const canProceed = state.can_proceed === true;
      const exactProceed = !canProceed || (typeof state.room_ref === "string" && state.room_ref.length > 0);
      const complete = exactOptions && exactProceed;
      const options = (state.options ?? []).map((option, index) => {
        const enabled = option.is_enabled !== false;
        const item = ctx.referent({
          role: "rest_option",
          label: option.name ?? option.option_id ?? `Option ${index + 1}`,
          enabled,
          occurrence: index,
          properties: {
            index: option.index,
            option_id: option.option_id ?? null,
            name: option.name ?? null,
            description: visibleText(option.description),
            is_enabled: enabled
          }
        });
        if (enabled && complete) {
          ctx.action({
            verb: "activate",
            subject: item,
            label: String(option.name ?? option.option_id ?? `Choose option ${index + 1}`),
            raw: { cmd: "action", action: "choose_option", args: {
              option_index: option.index, option_ref: option.native_ref
            } }
          });
        }
        return item.properties;
      });
      if (canProceed && complete) {
        const room = ctx.referent({
          role: "rest_room", label: "Rest site proceed", enabled: true,
          properties: { can_proceed: true }
        });
        ctx.action({
          verb: "activate", subject: room, label: "Proceed",
          raw: { cmd: "action", action: "proceed", args: { room_ref: state.room_ref } }
        });
      }
      return {
        kind: "rest_site",
        stage: "choosing",
        prompt: null,
        surface: { kind: "rest_site", stage: "choosing", options, can_proceed: canProceed },
        context: { ...commonContext, kind: "rest" },
        ...supported,
        complete,
        missing: [
          ...(exactOptions ? [] : ["exact_current_rest_option_identity"]),
          ...(exactProceed ? [] : ["exact_current_rest_room_identity"])
        ]
      };
    }
    case "treasure_chest": {
      const room = ctx.referent({
        role: "treasure_room",
        label: "Treasure chest",
        properties: { stage: "closed" }
      });
      ctx.action({
        verb: "activate",
        subject: room,
        label: "Open treasure chest",
        raw: { cmd: "action", action: "open_treasure", args: { room_ref: state.room_ref } }
      });
      const complete = typeof state.room_ref === "string" && state.room_ref.length > 0;
      return {
        kind: "treasure_chest",
        stage: "closed",
        prompt: null,
        surface: { kind: "treasure_chest", stage: "closed" },
        context: { ...commonContext, kind: "treasure" },
        complete,
        visibleInformation: "contract_complete_for_current_native_treasure_interaction",
        interactionDiscovery: "derived_from_same_current_native_treasure_interaction_as_execution",
        missing: complete ? [] : ["native_treasure_room_identity"]
      };
    }
    case "treasure_relic": {
      const room = ctx.referent({
        role: "treasure_room",
        label: "Opened treasure chest",
        properties: { stage: "relic_selection" }
      });
      const currentRelics = Array.isArray(state.relics) ? state.relics : [];
      const nativeIdentityComplete = Array.isArray(state.relics)
        && currentRelics.length > 0 && currentRelics.length <= ACTION_LIMIT
        && currentRelics.every((relic, index) => relic?.index === index
          && typeof relic.native_ref === "string" && relic.native_ref.length > 0
          && typeof relic.id === "string" && relic.id.length > 0
          && typeof relic.name === "string" && relic.name.length > 0)
        && new Set(currentRelics.map((relic) => relic.native_ref)).size === currentRelics.length;
      const roomIdentityComplete = typeof state.room_ref === "string" && state.room_ref.length > 0;
      const skipFactComplete = typeof state.can_skip === "boolean";
      const complete = roomIdentityComplete && nativeIdentityComplete && skipFactComplete;
      const relics = currentRelics.map((relic, index) => {
        const item = ctx.referent({
          role: "relic",
          label: relic?.name ?? `Unavailable relic ${index + 1}`,
          enabled: complete,
          occurrence: index,
          properties: {
            index: relic?.index ?? index,
            definition_id: relic == null ? null : definitionId(relic.id ?? relic.name),
            name: relic?.name ?? null,
            description: relic?.description ?? null,
            rarity: relic?.rarity ?? null
          }
        });
        if (complete) ctx.action({
          verb: "select",
          subject: item,
          label: `Take ${relic.name ?? `relic ${index + 1}`}`,
          raw: {
            cmd: "action",
            action: "select_treasure_relic",
            args: { room_ref: state.room_ref, relic_ref: relic.native_ref }
          }
        });
        return item.properties;
      });
      if (complete && state.can_skip === true) {
        ctx.action({
          verb: "skip",
          subject: room,
          label: "Skip treasure relic",
          raw: {
            cmd: "action",
            action: "skip_treasure_relic",
            args: { room_ref: state.room_ref }
          }
        });
      }
      return {
        kind: "treasure_relic_selection",
        stage: "choosing",
        prompt: null,
        surface: {
          kind: "treasure_relic_selection",
          stage: "choosing",
          relics,
          can_skip: state.can_skip === true
        },
        context: { ...commonContext, kind: "treasure" },
        complete,
        visibleInformation: "contract_complete_for_current_native_treasure_interaction",
        interactionDiscovery: "derived_from_same_current_native_treasure_interaction_as_execution",
        missing: [
          ...(roomIdentityComplete ? [] : ["native_treasure_room_identity"]),
          ...(nativeIdentityComplete ? [] : ["native_treasure_relic_identity"]),
          ...(skipFactComplete ? [] : ["native_treasure_skip_actionability"])
        ]
      };
    }
    case "treasure_complete": {
      const room = ctx.referent({
        role: "treasure_room",
        label: "Completed treasure room",
        properties: { stage: "complete" }
      });
      ctx.action({
        verb: "close",
        subject: room,
        label: "Proceed to map",
        raw: { cmd: "action", action: "leave_room", args: { room_ref: state.room_ref } }
      });
      const complete = typeof state.room_ref === "string" && state.room_ref.length > 0;
      return {
        kind: "treasure_completion",
        stage: "ready",
        prompt: null,
        surface: { kind: "treasure_completion", stage: "ready" },
        context: { ...commonContext, kind: "treasure" },
        complete,
        visibleInformation: "contract_complete_for_current_native_treasure_interaction",
        interactionDiscovery: "derived_from_same_current_native_treasure_interaction_as_execution",
        missing: complete ? [] : ["native_treasure_room_identity"]
      };
    }
    case "reward_set": {
      const potionSlotsFull = state.potion_slots_full === true;
      const hasPotionReward = (state.rewards ?? []).some((reward) => reward.kind === "potion");
      const nativeIdentityComplete = (state.rewards ?? []).every((reward) =>
        typeof reward.native_ref === "string" && reward.native_ref.length > 0);
      const rewards = (state.rewards ?? []).map((reward, index) => {
        const enabled = !(reward.kind === "potion" && potionSlotsFull);
        const kind = reward.kind === "card_choice" ? "card" : reward.kind ?? "unknown";
        const label = reward.name ?? kind ?? `Reward ${index + 1}`;
        const item = ctx.referent({
          role: "reward",
          label,
          enabled,
          occurrence: index,
          properties: {
            kind,
            label,
            description: reward.description ?? null,
            enabled
          }
        });
        if (enabled) {
          ctx.action({
            verb: "activate",
            subject: item,
            label: `Claim ${label}`,
            raw: {
              cmd: "action",
              action: "select_reward",
              args: { reward_ref: reward.native_ref }
            }
          });
        }
        return item.properties;
      });
      const rawPotions = Array.isArray(state.player?.potions)
        ? state.player.potions.filter((potion) => potion != null)
        : [];
      const discardablePotions = hasPotionReward && potionSlotsFull
        ? rawPotions.map((potion, index) => {
          const entityId = ctx.id("potion", ctx.snapshotId, potion.native_ref, potion.slot ?? index);
          const visiblePotion = {
            entity_id: entityId,
            definition_id: definitionId(potion.id ?? potion.name),
            name: potion.name ?? null,
            description: potion.description ?? null,
            slot: potion.slot ?? index,
            target_type: potion.target_type ?? "Unknown",
            can_use: false,
            automatic: potion.usage === "Automatic"
          };
          const item = ctx.referent({
            role: "potion",
            label: potion.name ?? `Potion ${index + 1}`,
            id: entityId,
            occurrence: index,
            properties: visiblePotion
          });
          ctx.action({
            verb: "activate",
            subject: item,
            label: `Discard ${item.label} from slot ${visiblePotion.slot + 1} to make room`,
            raw: {
              cmd: "action",
              action: "discard_potion",
              args: { potion_slot: visiblePotion.slot, potion_ref: potion.native_ref }
            }
          });
          return visiblePotion;
        })
        : [];
      if (state.is_terminal === true && state.can_proceed === true) {
        ctx.action({
          verb: "activate",
          label: rewards.length === 0 ? "Continue from rewards" : "Skip remaining rewards and continue",
          raw: { cmd: "action", action: "proceed", args: { room_ref: state.room_ref } }
        });
      } else if (state.is_terminal !== true && state.can_skip === true) {
        ctx.action({
          verb: "skip",
          label: "Skip remaining rewards",
          raw: { cmd: "action", action: "skip_rewards" }
        });
      }
      const terminalIdentityComplete = state.is_terminal !== true
        || (typeof state.room_ref === "string" && state.room_ref.length > 0);
      const potionIdentityComplete = discardablePotions.length === 0
        || rawPotions.every((potion) => typeof potion.native_ref === "string" && potion.native_ref.length > 0);
      return {
        kind: "reward_claim",
        stage: "ready",
        prompt: null,
        surface: {
          kind: "reward_claim",
          rewards,
          potion_slots_full: potionSlotsFull,
          discardable_potions: discardablePotions,
          can_proceed: state.is_terminal === true && state.can_proceed === true,
          proceed_skips_remaining_rewards: rewards.length > 0
        },
        context: { kind: "reward_flow", reward_kind: "room_rewards" },
        complete: nativeIdentityComplete && terminalIdentityComplete && potionIdentityComplete,
        visibleInformation: "contract_complete_for_reward_claim",
        interactionDiscovery: "derived_from_same_current_ui_controls_as_execution",
        missing: [
          ...(nativeIdentityComplete ? [] : ["native_reward_identity"]),
          ...(terminalIdentityComplete ? [] : ["terminal_reward_room_identity"]),
          ...(potionIdentityComplete ? [] : ["native_potion_identity"])
        ]
      };
    }
    case "card_reward": {
      const rawCards = state.cards;
      const rawAlternatives = state.alternatives;
      const options = [...(Array.isArray(rawCards) ? rawCards : []),
        ...(Array.isArray(rawAlternatives) ? rawAlternatives : [])];
      const exact = Array.isArray(rawCards) && Array.isArray(rawAlternatives)
        && options.length > 0 && options.length <= ACTION_LIMIT
        && options.every((option) => typeof option?.native_ref === "string"
          && option.native_ref.length > 0
          && Number.isSafeInteger(option.index)
          && option.index >= 0)
        && rawCards.every((option, index) => option.index === index
          && typeof option.id === "string" && option.id.length > 0
          && typeof option.name === "string" && option.name.length > 0)
        && rawAlternatives.every((option, index) => option.index === index
          && typeof option.id === "string" && option.id.length > 0
          && typeof option.name === "string" && option.name.length > 0)
        && new Set(options.map((option) => option.native_ref)).size === options.length;
      if (!exact) return {
        kind: "card_reward_selection", stage: "choosing", prompt: null,
        surface: { kind: "card_reward_selection", stage: "choosing",
          cards: [], alternatives: [], can_skip: false },
        context: { ...commonContext, kind: "reward" },
        complete: false,
        visibleInformation: "native_card_reward_options_incomplete",
        interactionDiscovery: "derived_from_same_current_native_card_reward_callback_as_execution",
        missing: ["exact_current_card_reward_choices"]
      };
      const cards = rawCards.map((card, index) => {
        const item = ctx.referent({
          role: "card",
          label: card?.name ?? `Card ${index + 1}`,
          occurrence: index,
          properties: { index: card?.index ?? index, ...cardProperties(card) }
        });
        ctx.action({
          verb: "select", subject: item, label: `Take ${card.name ?? `card ${index + 1}`}`,
          raw: { cmd: "action", action: "select_card_reward", args: { card_ref: card.native_ref } }
        });
        return item.properties;
      });
      const alternatives = rawAlternatives.map((alternative, index) => {
        const item = ctx.referent({
          role: "card_reward_alternative", label: alternative?.name ?? `Alternative ${index + 1}`,
          occurrence: index,
          properties: { option_id: alternative?.id ?? null, name: alternative?.name ?? null }
        });
        ctx.action({
          verb: alternative.id.toLowerCase() === "skip" ? "skip" : "activate",
          subject: item, label: alternative.name,
          raw: { cmd: "action", action: "select_card_reward_alternative",
            args: { alternative_ref: alternative.native_ref } }
        });
        return item.properties;
      });
      return {
        kind: "card_reward_selection",
        stage: "choosing",
        prompt: null,
        surface: { kind: "card_reward_selection", stage: "choosing", cards, alternatives,
          can_skip: rawAlternatives.some((option) => option.id.toLowerCase() === "skip") },
        context: { ...commonContext, kind: "reward" },
        complete: true,
        visibleInformation: "contract_complete_for_current_native_card_reward",
        interactionDiscovery: "derived_from_same_current_native_card_reward_callback_as_execution",
        missing: []
      };
    }
    case "combat_rewards_complete": {
      const room = ctx.referent({
        role: "combat_room",
        label: state.is_boss === true ? "Completed boss combat" : "Completed combat",
        properties: {
          room_type: state.is_boss === true ? "boss" : "combat",
          rewards_complete: true
        }
      });
      ctx.action({
        verb: "activate",
        subject: room,
        label: state.is_boss === true ? "Proceed from boss rewards" : "Proceed to map",
        raw: { cmd: "action", action: "proceed", args: { room_ref: state.room_ref } }
      });
      return {
        kind: "reward_completion",
        stage: "ready",
        prompt: null,
        surface: {
          kind: "reward_completion",
          stage: "ready",
          is_boss: state.is_boss === true
        },
        context: { ...commonContext, kind: "reward" },
        complete: typeof state.room_ref === "string",
        visibleInformation: "contract_complete_for_current_native_reward_completion",
        interactionDiscovery: "derived_from_same_current_native_reward_interaction_as_execution",
        missing: typeof state.room_ref === "string" ? [] : ["native_combat_room_identity"]
      };
    }
    case "bundle_select": {
      const bundles = (state.bundles ?? []).map((bundle, index) => {
        const item = ctx.referent({
          role: "card_bundle",
          label: `Bundle ${index + 1}`,
          occurrence: index,
          properties: {
            index: bundle.index ?? index,
            cards: (bundle.cards ?? []).map(cardProperties)
          }
        });
        ctx.action({
          verb: "select",
          subject: item,
          label: `Take bundle ${index + 1}`,
          raw: { cmd: "action", action: "select_bundle", args: { bundle_index: bundle.index ?? index } }
        });
        return item.properties;
      });
      return {
        kind: "card_bundle_selection",
        stage: "choosing",
        prompt: null,
        surface: { kind: "card_bundle_selection", stage: "choosing", bundles },
        context: { ...commonContext, kind: "selection" },
        ...supported
      };
    }
    case "deck_upgrade_select": {
      const cards = Array.isArray(state.cards) ? state.cards : [];
      const selectedRefs = Array.isArray(state.selected_refs) ? state.selected_refs : [];
      const cardRefs = cards.map((card) => card.native_ref);
      const stage = state.stage;
      const exact = typeof state.selector_ref === "string" && state.selector_ref.length > 0
        && (stage === "selecting" || stage === "preview")
        && typeof state.prompt === "string" && state.prompt.length > 0
        && Number.isSafeInteger(state.min_select) && state.min_select >= 0
        && Number.isSafeInteger(state.max_select) && state.max_select >= state.min_select
        && cards.every((card, index) => card.index === index
          && typeof card.native_ref === "string" && card.native_ref.length > 0
          && typeof card.is_selected === "boolean"
          && typeof card.is_selectable === "boolean"
          && typeof card.is_deselectable === "boolean")
        && new Set(cardRefs).size === cards.length
        && selectedRefs.every((ref) => typeof ref === "string" && cardRefs.includes(ref))
        && new Set(selectedRefs).size === selectedRefs.length
        && selectedRefs.length <= state.max_select
        && cards.every((card) => card.is_selected === selectedRefs.includes(card.native_ref))
        && typeof state.cancelable === "boolean"
        && typeof state.require_manual_confirmation === "boolean"
        && typeof state.can_cancel_selection === "boolean"
        && typeof state.can_cancel_preview === "boolean"
        && typeof state.can_confirm === "boolean"
        && state.can_cancel_selection === (stage === "selecting" && state.cancelable)
        && state.can_cancel_preview === (stage === "preview")
        && (!state.can_confirm || stage === "preview")
        && (stage !== "preview" || cards.every((card) =>
          !card.is_selectable && !card.is_deselectable));
      const referents = cards.map((card, index) => ctx.referent({
        role: "card",
        label: card.name ?? `Card ${index + 1}`,
        occurrence: index,
        enabled: card.is_selectable || card.is_deselectable,
        selected: card.is_selected,
        id: typeof card.native_ref === "string"
          ? ctx.id("card", ctx.snapshotId, card.native_ref) : null,
        properties: { index, ...cardProperties(card), is_selected: card.is_selected }
      }));
      if (exact) {
        for (const [index, card] of cards.entries()) {
          const subject = referents[index];
          if (card.is_selectable) ctx.action({
            verb: "select", subject, label: `Select ${subject.label}`,
            raw: { cmd: "action", action: "select_upgrade_card", args: {
              selector_ref: state.selector_ref, card_ref: card.native_ref
            } }
          });
          if (card.is_deselectable) ctx.action({
            verb: "deselect", subject, label: `Deselect ${subject.label}`,
            raw: { cmd: "action", action: "deselect_upgrade_card", args: {
              selector_ref: state.selector_ref, card_ref: card.native_ref
            } }
          });
        }
        if (state.can_cancel_selection) ctx.action({
          verb: "cancel", label: "Cancel card upgrade selection",
          raw: { cmd: "action", action: "cancel_upgrade_selection", args: {
            selector_ref: state.selector_ref
          } }
        });
        if (state.can_cancel_preview) ctx.action({
          verb: "cancel", label: "Return to card selection",
          raw: { cmd: "action", action: "cancel_upgrade_preview", args: {
            selector_ref: state.selector_ref
          } }
        });
        if (state.can_confirm) ctx.action({
          verb: "confirm", label: "Confirm card upgrade",
          raw: { cmd: "action", action: "confirm_upgrade_selection", args: {
            selector_ref: state.selector_ref, selected_refs: selectedRefs.join(",")
          } }
        });
      }
      return {
        kind: "deck_upgrade_selection", stage,
        prompt: state.prompt ?? null,
        surface: {
          kind: "deck_upgrade_selection", stage,
          cards: referents.map((item) => item.properties),
          min_select: state.min_select, max_select: state.max_select,
          selected_count: selectedRefs.length,
          selected_card_entity_ids: referents.filter((_, index) => cards[index].is_selected)
            .map((item) => item.referent_id),
          cancelable: state.cancelable,
          require_manual_confirmation: state.require_manual_confirmation,
          can_cancel_selection: state.can_cancel_selection,
          can_cancel_preview: state.can_cancel_preview,
          can_confirm: state.can_confirm
        },
        context: { ...commonContext, kind: "selection" },
        ...supported,
        complete: exact,
        missing: exact ? [] : ["exact_current_deck_upgrade_selection_binding"]
      };
    }
    case "deck_card_select": {
      const cards = Array.isArray(state.cards) ? state.cards : [];
      const selectedRefs = Array.isArray(state.selected_refs) ? state.selected_refs : [];
      const cardRefs = cards.map((card) => card?.native_ref);
      const stage = state.stage;
      const selectedCount = selectedRefs.length;
      const exact = state.origin === "deck_generic"
        && typeof state.selector_ref === "string" && state.selector_ref.length > 0
        && (stage === "selecting" || stage === "preview")
        && typeof state.prompt === "string" && state.prompt.length > 0
        && cards.length > 0 && cards.length <= ACTION_LIMIT - 3
        && Number.isSafeInteger(state.min_select) && state.min_select >= 0
        && Number.isSafeInteger(state.max_select) && state.max_select >= state.min_select
        && state.max_select <= cards.length
        && cards.every((card, index) => card?.index === index
          && typeof card.native_ref === "string" && card.native_ref.length > 0
          && typeof card.name === "string" && card.name.length > 0
          && typeof card.is_selected === "boolean"
          && typeof card.is_selectable === "boolean"
          && typeof card.is_deselectable === "boolean")
        && new Set(cardRefs).size === cards.length
        && selectedRefs.every((ref) => typeof ref === "string" && cardRefs.includes(ref))
        && new Set(selectedRefs).size === selectedCount
        && selectedCount <= state.max_select
        && cards.every((card) => card.is_selected === selectedRefs.includes(card.native_ref))
        && typeof state.cancelable === "boolean"
        && typeof state.require_manual_confirmation === "boolean"
        && typeof state.can_cancel_selection === "boolean"
        && typeof state.can_preview === "boolean"
        && typeof state.can_cancel_preview === "boolean"
        && typeof state.can_confirm === "boolean"
        && state.can_cancel_selection === (stage === "selecting" && state.cancelable)
        && state.can_preview === (stage === "selecting"
          && state.min_select !== state.max_select && selectedCount >= state.min_select)
        && state.can_cancel_preview === (stage === "preview")
        && state.can_confirm === (stage === "preview"
          && selectedCount >= state.min_select && selectedCount <= state.max_select)
        && cards.every((card) => card.is_selectable === (stage === "selecting"
          && !card.is_selected && selectedCount < state.max_select)
          && card.is_deselectable === (stage === "selecting" && card.is_selected));
      const referents = cards.map((card, index) => ctx.referent({
        role: "card",
        label: card?.name ?? `Unavailable card ${index + 1}`,
        occurrence: index,
        enabled: exact && (card.is_selectable || card.is_deselectable),
        selected: card?.is_selected === true,
        id: exact && typeof card?.native_ref === "string"
          ? ctx.id("card", ctx.snapshotId, card.native_ref) : null,
        properties: { index, ...(card == null ? {} : cardProperties(card)),
          is_selected: card?.is_selected === true }
      }));
      if (exact) {
        for (const [index, card] of cards.entries()) {
          const subject = referents[index];
          if (card.is_selectable) ctx.action({
            verb: "select", subject, label: `Select ${subject.label}`,
            raw: { cmd: "action", action: "select_deck_card", args: {
              selector_ref: state.selector_ref, card_ref: card.native_ref
            } }
          });
          if (card.is_deselectable) ctx.action({
            verb: "deselect", subject, label: `Deselect ${subject.label}`,
            raw: { cmd: "action", action: "deselect_deck_card", args: {
              selector_ref: state.selector_ref, card_ref: card.native_ref
            } }
          });
        }
        if (state.can_preview) ctx.action({
          verb: "preview", label: "Preview card selection",
          raw: { cmd: "action", action: "preview_deck_selection", args: {
            selector_ref: state.selector_ref
          } }
        });
        if (state.can_cancel_selection) ctx.action({
          verb: "cancel", label: "Cancel card selection",
          raw: { cmd: "action", action: "cancel_deck_selection", args: {
            selector_ref: state.selector_ref
          } }
        });
        if (state.can_cancel_preview) ctx.action({
          verb: "cancel", label: "Return to card selection",
          raw: { cmd: "action", action: "cancel_deck_preview", args: {
            selector_ref: state.selector_ref
          } }
        });
        if (state.can_confirm) ctx.action({
          verb: "confirm", label: "Confirm card selection",
          raw: { cmd: "action", action: "confirm_deck_selection", args: {
            selector_ref: state.selector_ref, selected_refs: selectedRefs.join(",")
          } }
        });
      }
      return {
        kind: "deck_card_selection", stage, prompt: state.prompt ?? null,
        surface: { kind: "deck_card_selection", stage,
          cards: referents.map((item) => item.properties),
          min_select: state.min_select, max_select: state.max_select,
          selected_count: selectedCount,
          selected_card_entity_ids: referents.filter((_, index) => cards[index]?.is_selected)
            .map((item) => item.referent_id),
          cancelable: state.cancelable,
          require_manual_confirmation: state.require_manual_confirmation,
          can_cancel_selection: state.can_cancel_selection,
          can_preview: state.can_preview,
          can_cancel_preview: state.can_cancel_preview,
          can_confirm: state.can_confirm },
        context: { ...commonContext, kind: "selection" },
        ...supported,
        complete: exact,
        missing: exact ? [] : ["exact_current_deck_card_selection_binding"]
      };
    }
    case "card_select": {
      const cards = Array.isArray(state.cards) ? state.cards : [];
      const referents = cards.map((card, index) => ctx.referent({
        role: "card", label: card?.name ?? `Unavailable card ${index + 1}`,
        occurrence: index, enabled: false,
        properties: { index, ...(card == null ? {} : cardProperties(card)) }
      }));
      return {
        kind: "card_selection", stage: "choosing", prompt: null,
        surface: { kind: "card_selection", stage: "choosing",
          cards: referents.map((item) => item.properties),
          min_select: state.min_select, max_select: state.max_select },
        context: { ...commonContext, kind: "selection" },
        complete: false, missing: ["unknown_native_card_selector_purpose_and_preferences"]
      };
    }
    case "combat_play": {
      const rawHand = state.hand ?? [];
      const rawEnemies = state.enemies ?? [];
      const rawPotions = state.player?.potions ?? [];
      const identityComplete = typeof state.player?.native_ref === "string"
        && [...rawHand, ...rawEnemies, ...rawPotions].every((item) =>
          typeof item?.native_ref === "string" && item.native_ref.length > 0);
      const unsupportedCardTarget = rawHand.some((card) =>
        card.can_play === true && !supportsManagedCardTargetType(card.target_type));
      const unsupportedPotionTarget = rawPotions.some((potion) =>
        potion.binding_supported !== true || !supportsManagedPotionTargetType(potion.target_type));
      const playWindowOpen = state.is_play_phase === true;
      const semanticFactsComplete = typeof state.encounter_type === "string"
        && typeof state.turn_owner === "string"
        && typeof state.is_play_phase === "boolean"
        && Number.isSafeInteger(state.exhaust_pile_count)
        && Number.isSafeInteger(state.orb_slots)
        && Array.isArray(state.orbs)
        && Array.isArray(state.companions)
        && Array.isArray(state.player_statuses)
        && rawEnemies.every((enemy) => typeof enemy.id === "string"
          && Number.isSafeInteger(enemy.combat_id)
          && Array.isArray(enemy.statuses)
          && (enemy.intents ?? []).every((intent) =>
            typeof intent.label === "string"
            && typeof intent.title === "string"
            && typeof intent.description === "string"));

      const enemyEntries = rawEnemies.map((enemy, index) => {
        const properties = {
          entity_id: ctx.id("enemy", ctx.snapshotId, enemy.native_ref),
          combat_id: enemy.combat_id,
          definition_id: definitionId(enemy.id),
          name: enemy.name ?? null,
          hp: enemy.hp,
          max_hp: enemy.max_hp,
          block: enemy.block,
          statuses: (enemy.statuses ?? []).map(visibleStatus),
          intents: (enemy.intents ?? []).map(visibleIntent)
        };
        return {
          raw: enemy,
          referent: ctx.referent({
            role: "enemy",
            label: enemy.name ?? `Enemy ${index + 1}`,
            id: properties.entity_id,
            occurrence: index,
            properties
          })
        };
      });
      const enemyReferentByNative = new Map(enemyEntries.map((entry) => [
        entry.raw.native_ref,
        entry.referent
      ]));
      const handEntries = rawHand.map((card, index) => {
        const entityId = ctx.id("card", ctx.snapshotId, card.native_ref);
        const visibleCard = visibleCombatCard(card, entityId);
        const playable = card.can_play === true;
        const validTargets = (card.valid_target_refs ?? [])
          .map((nativeRef) => enemyReferentByNative.get(nativeRef)?.referent_id)
          .filter(Boolean);
        const option = {
          entity_id: entityId,
          name: card.name ?? null,
          target_entity_ids: validTargets
        };
        return {
          raw: card,
          card: visibleCard,
          option,
          referent: ctx.referent({
            role: playable ? "playable_card" : "hand",
            label: card.name ?? `Card ${index + 1}`,
            id: entityId,
            occurrence: index,
            selected: playable ? null : visibleCard.is_selected,
            properties: playable ? option : visibleCard
          })
        };
      });

      for (const { raw: card, referent } of handEntries.filter(({ raw: item }) =>
        item.can_play === true)) {
        if (card.target_type === "AnyEnemy") {
          const validTargets = new Set(card.valid_target_refs ?? []);
          for (const { raw: enemy, referent: target } of enemyEntries.filter(({ raw: item }) =>
            (item.hp ?? 0) > 0)) {
            if (!validTargets.has(enemy.native_ref)) continue;
            ctx.action({
              verb: "play",
              subject: referent,
              arguments: [{ role: "target", referent: target }],
              label: `Play ${referent.label} -> ${target.label}`,
              raw: {
                cmd: "action",
                action: "play_card",
                args: { card_ref: card.native_ref, target_ref: enemy.native_ref }
              }
            });
          }
        } else if (supportsManagedCardTargetType(card.target_type)) {
          ctx.action({
            verb: "play",
            subject: referent,
            label: `Play ${referent.label}`,
            raw: { cmd: "action", action: "play_card", args: { card_ref: card.native_ref } }
          });
        }
      }

      ctx.action({
        verb: "end_turn",
        label: "End turn",
        raw: { cmd: "action", action: "end_turn", args: { player_ref: state.player?.native_ref } }
      });

      for (const [index, potion] of rawPotions.entries()) {
        const targetIds = (potion.valid_target_refs ?? [])
          .map((nativeRef) => enemyReferentByNative.get(nativeRef)?.referent_id)
          .filter(Boolean);
        const visiblePotion = {
          entity_id: ctx.id("potion", ctx.snapshotId, potion.native_ref),
          name: potion.name ?? null,
          target_entity_ids: targetIds
        };
        const potionReferent = ctx.referent({
          role: "usable_potion",
          label: potion.name ?? `Potion ${index + 1}`,
          id: visiblePotion.entity_id,
          occurrence: index,
          properties: visiblePotion
        });
        if (potion.can_use === true && potion.binding_supported === true) {
          if (potion.target_type === "AnyEnemy") {
            const validTargets = new Set(potion.valid_target_refs ?? []);
            for (const { raw: enemy, referent: target } of enemyEntries) {
              if (!validTargets.has(enemy.native_ref)) continue;
              ctx.action({
                verb: "use",
                subject: potionReferent,
                arguments: [{ role: "target", referent: target }],
                label: `Use ${potionReferent.label} on ${target.label}`,
                raw: {
                  cmd: "action",
                  action: "use_potion",
                  args: {
                    potion_slot: potion.slot,
                    potion_ref: potion.native_ref,
                    target_ref: enemy.native_ref
                  }
                }
              });
            }
          } else if (supportsManagedPotionTargetType(potion.target_type)) {
            ctx.action({
              verb: "use",
              subject: potionReferent,
              label: `Use ${potionReferent.label}`,
              raw: {
                cmd: "action",
                action: "use_potion",
                args: { potion_slot: potion.slot, potion_ref: potion.native_ref }
              }
            });
          }
        }
        if (potion.can_discard === true) {
          ctx.action({
            verb: "activate",
            subject: potionReferent,
            label: `Discard ${potionReferent.label}`,
            raw: {
              cmd: "action",
              action: "discard_potion",
              args: { potion_slot: potion.slot, potion_ref: potion.native_ref }
            }
          });
        }
      }

      const playerEntityId = ctx.id("player", ctx.snapshotId, state.player?.native_ref);
      const playerContext = {
        player_entity_id: playerEntityId,
        block: state.player?.block ?? 0,
        energy: state.energy ?? 0,
        max_energy: state.max_energy ?? 0,
        hand: handEntries.map((entry) => entry.card),
        draw_pile_count: state.draw_pile_count ?? 0,
        discard_pile_count: state.discard_pile_count ?? 0,
        exhaust_pile_count: state.exhaust_pile_count ?? 0,
        statuses: (state.player_statuses ?? []).map(visibleStatus),
        companions: state.companions ?? [],
        potion_states: rawPotions.map((potion) => ({
          entity_id: ctx.id("potion", ctx.snapshotId, potion.native_ref),
          target_type: potion.target_type,
          can_use: potion.can_use === true,
          automatic: potion.usage === "Automatic"
        })),
        orbs: state.orbs ?? [],
        orb_slots: state.orb_slots ?? 0
      };
      ctx.referent({
        role: "player",
        label: null,
        id: playerEntityId,
        properties: playerContext,
        includeEntityId: false
      });
      const complete = identityComplete
        && semanticFactsComplete
        && !unsupportedCardTarget
        && !unsupportedPotionTarget;
      const actionComplete = identityComplete
        && playWindowOpen
        && !unsupportedCardTarget
        && !unsupportedPotionTarget;
      return {
        kind: "combat_turn",
        stage: playWindowOpen ? "ready" : "settling",
        prompt: null,
        surface: {
          kind: "combat_turn",
          can_end_turn: playWindowOpen,
          playable_cards: playWindowOpen
            ? handEntries.filter((entry) => entry.raw.can_play === true)
              .map((entry) => entry.option)
            : [],
          usable_potions: playWindowOpen
            ? rawPotions.filter((potion) => potion.can_use === true)
            .map((potion) => ({
              entity_id: ctx.id("potion", ctx.snapshotId, potion.native_ref),
              name: potion.name ?? null,
              target_entity_ids: (potion.valid_target_refs ?? [])
                .map((nativeRef) => enemyReferentByNative.get(nativeRef)?.referent_id)
                .filter(Boolean)
            }))
            : []
        },
        context: {
          kind: "combat",
          encounter_type: state.encounter_type,
          round: state.round,
          turn_owner: state.turn_owner,
          is_play_phase: state.is_play_phase,
          player: playerContext,
          enemies: enemyEntries.map(({ referent }) => referent.properties),
        },
        actionComplete,
        complete,
        missing: [
          ...(identityComplete ? [] : ["native_combat_operand_identity"]),
          ...(semanticFactsComplete ? [] : ["complete_visible_combat_context"]),
          ...(playWindowOpen ? [] : ["native_combat_play_window_closed"]),
          ...(unsupportedCardTarget ? ["native_unsupported_card_target_type"] : []),
          ...(unsupportedPotionTarget ? ["native_unsupported_potion_target_type_or_binding"] : [])
        ],
        visibleInformation: "contract_complete_for_immediate_combat_turn_including_visible_companions; pile contents available through a separate read-only Player Environment Read",
        interactionDiscovery: "derived_from_same_validator_as_execution",
        hiddenByPolicy: [
          "hidden_rng",
          "draw_pile_true_order",
          "future_enemy_moves",
          "future_rewards",
          "future_events"
        ]
      };
    }
    case "game_over":
      return {
        kind: "game_over",
        stage: "complete",
        prompt: null,
        surface: { kind: "game_over", stage: "complete",
          victory: typeof state.victory === "boolean" ? state.victory : null },
        context: { ...commonContext, kind: "terminal" },
        ...supported,
        complete: typeof state.victory === "boolean",
        missing: typeof state.victory === "boolean" ? [] : ["native_terminal_victory_fact"]
      };
    case "shop": {
      const rawCards = Array.isArray(state.cards) ? state.cards : [];
      const rawRelics = Array.isArray(state.relics) ? state.relics : [];
      const rawPotions = Array.isArray(state.potions) ? state.potions : [];
      const rawItems = [...rawCards, ...rawRelics, ...rawPotions];
      const rawEntries = [...rawItems,
        ...(state.card_removal == null ? [] : [state.card_removal])];
      const entryRefs = rawEntries.map((entry) => entry?.native_ref);
      const catalogComplete = Array.isArray(state.cards) && Array.isArray(state.relics)
        && Array.isArray(state.potions) && Object.hasOwn(state, "card_removal")
        && (state.card_removal === null || plainObject(state.card_removal))
        && rawEntries.length + 1 <= ACTION_LIMIT
        && rawItems.every((entry) => typeof entry?.name === "string" && entry.name.length > 0)
        && rawEntries.every((entry) => entry != null
          && typeof entry.native_ref === "string" && entry.native_ref.length > 0
          && Number.isSafeInteger(entry.cost) && entry.cost >= 0
          && typeof entry.is_stocked === "boolean"
          && typeof entry.can_purchase === "boolean"
          && (!entry.can_purchase || entry.is_stocked))
        && new Set(entryRefs).size === rawEntries.length;
      const cards = rawCards.map((card, index) => {
        const canPurchase = catalogComplete && card?.can_purchase === true;
        const item = ctx.referent({
          role: "shop_card",
          label: card?.name ?? `Unavailable card ${index + 1}`,
          enabled: canPurchase,
          occurrence: index,
          properties: {
            ...cardProperties({ ...card, cost: card?.card_cost }),
            price: card?.cost ?? null,
            is_stocked: card?.is_stocked === true,
            can_purchase: canPurchase,
            on_sale: card?.on_sale === true
          }
        });
        if (canPurchase) {
          ctx.action({
            verb: "activate",
            subject: item,
            label: `Buy ${item.label}`,
            raw: { cmd: "action", action: "buy_card", args: { entry_ref: card.native_ref } }
          });
        }
        return item.properties;
      });
      const relics = rawRelics.map((relic, index) => {
        const canPurchase = catalogComplete && relic?.can_purchase === true;
        const item = ctx.referent({
          role: "shop_relic",
          label: relic?.name ?? `Unavailable relic ${index + 1}`,
          enabled: canPurchase,
          occurrence: index,
          properties: {
            name: relic?.name ?? null,
            description: relic?.description ?? null,
            price: relic?.cost ?? null,
            is_stocked: relic?.is_stocked === true,
            can_purchase: canPurchase
          }
        });
        if (canPurchase) {
          ctx.action({
            verb: "activate",
            subject: item,
            label: `Buy ${item.label}`,
            raw: { cmd: "action", action: "buy_relic", args: { entry_ref: relic.native_ref } }
          });
        }
        return item.properties;
      });
      const potions = rawPotions.map((potion, index) => {
        const canPurchase = catalogComplete && potion?.can_purchase === true;
        const item = ctx.referent({
          role: "shop_potion",
          label: potion?.name ?? `Unavailable potion ${index + 1}`,
          enabled: canPurchase,
          occurrence: index,
          properties: {
            name: potion?.name ?? null,
            description: potion?.description ?? null,
            price: potion?.cost ?? null,
            is_stocked: potion?.is_stocked === true,
            can_purchase: canPurchase
          }
        });
        if (canPurchase) {
          ctx.action({
            verb: "activate",
            subject: item,
            label: `Buy ${item.label}`,
            raw: { cmd: "action", action: "buy_potion", args: { entry_ref: potion.native_ref } }
          });
        }
        return item.properties;
      });
      let cardRemoval = null;
      if (plainObject(state.card_removal)) {
        const canPurchase = catalogComplete && state.card_removal.can_purchase === true;
        const item = ctx.referent({
          role: "shop_service",
          label: "Card removal",
          enabled: canPurchase,
          properties: {
            service: "card_removal",
            price: state.card_removal.cost ?? null,
            is_stocked: state.card_removal.is_stocked === true,
            can_purchase: canPurchase
          }
        });
        if (canPurchase) {
          ctx.action({
            verb: "activate",
            subject: item,
            label: "Buy card removal",
            raw: {
              cmd: "action",
              action: "remove_card",
              args: { room_ref: state.room_ref, entry_ref: state.card_removal.native_ref }
            }
          });
        }
        cardRemoval = item.properties;
      }
      const roomIdentityComplete = typeof state.room_ref === "string" && state.room_ref.length > 0;
      if (roomIdentityComplete) {
        ctx.action({
          verb: "close",
          label: "Leave shop",
          raw: { cmd: "action", action: "leave_shop", args: { room_ref: state.room_ref } }
        });
      }
      const complete = roomIdentityComplete && catalogComplete;
      return {
        kind: "shop_inventory",
        stage: "choosing",
        prompt: null,
        surface: {
          kind: "shop_inventory",
          stage: "choosing",
          cards,
          relics,
          potions,
          card_removal: cardRemoval
        },
        context: { ...commonContext, kind: "shop" },
        complete,
        visibleInformation: "contract_complete_for_current_native_shop_interaction",
        interactionDiscovery: "derived_from_same_current_native_shop_interaction_as_execution",
        missing: [
          ...(roomIdentityComplete ? [] : ["native_shop_owner_identity"]),
          ...(catalogComplete ? [] : ["complete_ordered_shop_catalog_and_actionability"])
        ]
      };
    }
    default:
      return {
        kind: String(state.decision ?? "unknown"),
        stage: "unknown",
        prompt: state.message ?? null,
        surface: { kind: String(state.decision ?? "unknown"), stage: "unknown" },
        context: commonContext,
        complete: false,
        missing: ["unsupported_managed_decision"]
      };
  }
}

function capabilities(actions, referents) {
  const roles = new Map(referents.map((item) => [item.referent_id, item.role]));
  const values = new Map();
  for (const action of actions) {
    const value = {
      verb: action.verb,
      ...(action.subject_referent_id == null ? {} : { subject_role: roles.get(action.subject_referent_id) }),
      arguments: action.arguments.map((argument) => ({ role: argument.role, required: true })),
      availability_basis: "current_native_interaction"
    };
    values.set(JSON.stringify(value), value);
  }
  return [...values.values()];
}

function runDeckRead(snapshotId, state, makeId = stableId) {
  if (!Array.isArray(state.player?.deck)) return null;
  const nativeIdentityComplete = state.player.deck.every((card) =>
    typeof card?.native_ref === "string" && card.native_ref.length > 0);
  const renderedTextComplete = state.player.deck.every((card) =>
    typeof card?.description === "string"
    && card.description.length > 0
    && !/\{[^{}]+\}/u.test(card.description));
  const cardDetailsComplete = state.player.deck.every((card) =>
    typeof card?.name === "string"
    && card.name.length > 0
    && typeof card?.rarity === "string"
    && card.rarity !== "Unknown");
  const complete = nativeIdentityComplete && renderedTextComplete && cardDetailsComplete;
  const missing = [
    ...(nativeIdentityComplete ? [] : ["native_card_entity_identity"]),
    ...(renderedTextComplete ? [] : ["fully_rendered_localized_dynamic_text"]),
    ...(cardDetailsComplete ? [] : ["visible_card_details"])
  ];
  return {
    descriptor: {
      read_id: makeId("read", snapshotId, "run_deck"),
      kind: "run_deck",
      target_referent_id: null,
      content_schema: "sts2.player-environment/read/run_deck-1",
      visibility_basis: "player_openable_run_deck_view",
      snapshot_bound: true,
      ordering_semantics: "unordered_multiset",
      hidden_by_policy: []
    },
    content: {
      kind: "run_deck",
      card_count: state.player.deck.length,
      cards: state.player.deck.map((card) => {
        const entityId = makeId("deck_card", card.native_ref ?? snapshotId, card.id, card.upgraded);
        return {
          ...visibleCombatCard(card, entityId),
          is_selected: false
        };
      })
    },
    completeness: {
      status: complete ? "complete" : "partial",
      visible_information: complete
        ? "complete_for_player_openable_run_deck_view"
        : "managed_candidate_run_deck_projection_incomplete",
      interaction_discovery: "read_only",
      missing,
      hidden_by_policy: []
    }
  };
}

function combatPilesRead(snapshotId, state, makeId = stableId) {
  if (!Array.isArray(state.combat_piles)) return null;
  return {
    descriptor: {
      read_id: makeId("read", snapshotId, "combat_piles"),
      kind: "combat_piles",
      target_referent_id: null,
      content_schema: "sts2.player-environment/read/combat_piles-1",
      visibility_basis: "player_openable_draw_discard_exhaust_pile_views",
      snapshot_bound: true,
      ordering_semantics: "unordered_multiset",
      hidden_by_policy: ["draw_pile_true_order"]
    },
    content: {
      kind: "combat_piles",
      zones: state.combat_piles.map((zone) => ({
        zone: zone.pile,
        card_count: zone.cards?.length ?? 0,
        ordering_semantics: "unordered_multiset",
        cards: (zone.cards ?? []).map((card, index) => visibleCombatCard(
          card,
          makeId("pile_card", snapshotId, zone.pile, card.native_ref, index)
        ))
      }))
    },
    completeness: {
      status: "complete",
      visible_information: "complete_for_player_visible_combat_pile_contents_without_draw_order",
      interaction_discovery: "read_only",
      missing: [],
      hidden_by_policy: ["draw_pile_true_order"]
    }
  };
}

export function projectManagedCandidateDecision({
  state,
  runtimeInstanceId,
  environmentFingerprint,
  sequence,
  identityMode = "crypto",
  validateSdk = true
}) {
  if (!plainObject(state) || state.type !== "decision") {
    throw new TypeError("Managed Player Environment projection requires a raw decision state.");
  }
  if (typeof runtimeInstanceId !== "string" || runtimeInstanceId.length === 0) {
    throw new TypeError("Managed Player Environment projection requires runtimeInstanceId.");
  }
  if (typeof environmentFingerprint !== "string" || environmentFingerprint.length === 0) {
    throw new TypeError("Managed Player Environment projection requires environmentFingerprint.");
  }
  if (!Number.isSafeInteger(sequence) || sequence < 1) {
    throw new TypeError("Managed Player Environment projection requires a positive sequence.");
  }

  const totalStarted = performance.now();
  let stageStarted = totalStarted;
  const ctx = createProjectionContext({ state, runtimeInstanceId, sequence, identityMode });
  const contextMs = performance.now() - stageStarted;
  stageStarted = performance.now();
  const surface = currentSurface(state, ctx);
  const surfaceMs = performance.now() - stageStarted;
  stageStarted = performance.now();
  const persistent = buildPersistentVisibleState(state, ctx.snapshotId, ctx.id);
  const persistentMs = performance.now() - stageStarted;
  const terminal = state.decision === "game_over";
  const actionProjectionComplete = (surface.actionComplete ?? surface.complete) === true
    && ctx.actions.length <= ACTION_LIMIT;
  if (!actionProjectionComplete) {
    ctx.actions.length = 0;
    ctx.bindings.clear();
  }
  stageStarted = performance.now();
  const reads = [
    runDeckRead(ctx.snapshotId, state, ctx.id),
    combatPilesRead(ctx.snapshotId, state, ctx.id)
  ]
    .filter(Boolean);
  const readsMs = performance.now() - stageStarted;
  stageStarted = performance.now();
  const missing = [...new Set([
    ...persistent.missing,
    ...(surface.missing ?? [])
  ])];
  const contractComplete = persistent.complete
    && surface.complete === true
    && missing.length === 0
    && typeof surface.visibleInformation === "string"
    && typeof surface.interactionDiscovery === "string";
  const snapshot = {
    protocol_version: SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL,
    schema: "sts2.player-environment/snapshot-1",
    snapshot_id: ctx.snapshotId,
    sequence,
    observed_at: new Date().toISOString(),
    status: actionProjectionComplete && ctx.actions.length > 0
      ? "interactive"
      : terminal && contractComplete ? "observed" : "visible_unsupported",
    persistent: persistent.content,
    interaction: {
      interaction_id: ctx.interactionId,
      kind: surface.kind,
      stage: surface.stage,
      prompt: surface.prompt,
      content_schema: `sts2.player-environment/surface/${surface.kind.replace(/[^a-z0-9_]+/giu, "_").toLowerCase()}-1`,
      content: { surface: surface.surface, context: surface.context },
      capabilities: actionProjectionComplete ? capabilities(ctx.actions, ctx.referents) : []
    },
    referents: ctx.referents,
    bound_actions: {
      schema: "sts2.player-environment/bound-actions-1",
      status: actionProjectionComplete ? "complete" : "unavailable",
      materialized_count: actionProjectionComplete ? ctx.actions.length : 0,
      total_count: actionProjectionComplete ? ctx.actions.length : (surface.totalCount ?? 0),
      limit: ACTION_LIMIT,
      ordering_semantics: "candidate_id_then_operand_name_then_referent_id",
      actions: actionProjectionComplete ? ctx.actions : []
    },
    reads: reads.map((read) => read.descriptor),
    completeness: {
      status: contractComplete ? "complete" : "partial",
      visible_information: contractComplete
        ? surface.visibleInformation
        : "managed_candidate_projection_is_not_yet_cross_host_qualified",
      interaction_discovery: contractComplete
        ? surface.interactionDiscovery
        : "derived_from_exact_build_managed_decision_state",
      missing,
      hidden_by_policy: surface.hiddenByPolicy ?? ["hidden_rng", "future_rewards", "future_events"]
    },
    session: { runtime_instance_id: runtimeInstanceId, environment_fingerprint: environmentFingerprint },
    information_policy: INFORMATION_POLICY
  };
  const assemblyMs = performance.now() - stageStarted;
  stageStarted = performance.now();
  if (validateSdk) decodePlayerSnapshot(snapshot);
  const validationMs = performance.now() - stageStarted;
  return Object.freeze({
    snapshot,
    bindings: ctx.bindings,
    raw_state_sha256: ctx.rawStateSha,
    reads: new Map(reads.map((read) => [read.descriptor.read_id, read])),
    performance: Object.freeze({
      context_ms: contextMs,
      surface_ms: surfaceMs,
      persistent_ms: persistentMs,
      reads_ms: readsMs,
      assembly_ms: assemblyMs,
      validation_ms: validationMs,
      total_ms: performance.now() - totalStarted
    })
  });
}

function unknownAction(boundActionId) {
  return { bound_action_id: boundActionId, verb: "activate", subject_referent_id: null, arguments: [] };
}

function receipt({ requestId, delivery, action, reasonCode, detail, retry, successor, validateSdk = true }) {
  const value = {
    protocol_version: SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL,
    schema: "sts2.player-environment/receipt-1",
    request_id: requestId,
    delivery,
    action: {
      bound_action_id: action.bound_action_id,
      verb: action.verb,
      subject_referent_id: action.subject_referent_id ?? null,
      arguments: action.arguments ?? []
    },
    reason_code: reasonCode,
    detail,
    retry,
    successor
  };
  if (validateSdk) decodePlayerReceipt(value);
  return value;
}

export class ManagedPlayerEnvironmentSession {
  #process;
  #runtimeInstanceId;
  #environmentFingerprint;
  #character;
  #language;
  #sequence = 0;
  #projection = null;
  #ledger = new Map();
  #tainted = false;
  #taintReason = null;
  #identityMode;
  #validateSdk;
  #performance = new Map();

  constructor({
    process,
    runtimeInstanceId,
    environmentFingerprint,
    character = "Ironclad",
    language = "en",
    identityMode = "crypto",
    validateSdk = true
  }) {
    if (process == null || typeof process.request !== "function") {
      throw new TypeError("ManagedPlayerEnvironmentSession requires a JSON-line process.");
    }
    this.#process = process;
    this.#runtimeInstanceId = runtimeInstanceId;
    this.#environmentFingerprint = environmentFingerprint;
    this.#character = character;
    this.#language = language;
    this.#identityMode = identityMode;
    this.#validateSdk = validateSdk;
  }

  get tainted() {
    return this.#tainted;
  }

  get taintReason() {
    return this.#taintReason;
  }

  performance() {
    return Object.fromEntries([...this.#performance.entries()].map(([name, value]) => [name, { ...value }]));
  }

  async processMetrics(timeoutMs = 10_000) {
    return this.#process.request({ cmd: "process_metrics" }, timeoutMs);
  }

  #record(name, milliseconds) {
    const value = this.#performance.get(name) ?? { count: 0, total_ms: 0, max_ms: 0 };
    value.count += 1;
    value.total_ms += milliseconds;
    value.max_ms = Math.max(value.max_ms, milliseconds);
    this.#performance.set(name, value);
  }

  #makeReceipt(options) {
    const started = performance.now();
    const value = receipt({ ...options, validateSdk: this.#validateSdk });
    this.#record("receipt", performance.now() - started);
    return value;
  }

  async mount({ seed, reset = false, timeoutMs = 10_000 }) {
    if (typeof seed !== "string" || seed.length === 0) throw new TypeError("mount requires seed.");
    if (this.#tainted) throw new Error("A tainted Managed Player Environment session cannot mount another run.");
    const mountStarted = performance.now();
    const state = await this.#process.request({
      cmd: reset ? "reset_run" : "start_run",
      character: this.#character,
      seed,
      lang: this.#language
    }, timeoutMs);
    this.#record("mount_transport_and_native", performance.now() - mountStarted);
    this.#ledger.clear();
    return this.#setState(state, timeoutMs);
  }

  async close({ force = false, timeoutMs = 5_000 } = {}) {
    return this.#process.stop({ request: force ? null : { cmd: "quit" }, timeoutMs, force });
  }

  observe() {
    if (this.#projection == null) throw new Error("No managed run is mounted.");
    return this.#projection.snapshot;
  }

  read({ readId, expectedSnapshotId }) {
    if (this.#projection == null) throw new Error("No managed run is mounted.");
    if (expectedSnapshotId !== this.#projection.snapshot.snapshot_id) {
      throw new Error("stale_snapshot");
    }
    const read = this.#projection.reads.get(readId);
    if (read == null) throw new Error("unknown_read");
    const value = {
      protocol_version: SUPPORTED_PLAYER_ENVIRONMENT_PROTOCOL,
      schema: "sts2.player-environment/read-1",
      read_id: readId,
      expected_snapshot_id: expectedSnapshotId,
      observed_snapshot_id: this.#projection.snapshot.snapshot_id,
      observed_at: new Date().toISOString(),
      kind: read.descriptor.kind,
      target_referent_id: read.descriptor.target_referent_id,
      visibility_basis: read.descriptor.visibility_basis,
      ordering_semantics: read.descriptor.ordering_semantics,
      content_schema: read.descriptor.content_schema,
      content: read.content,
      completeness: read.completeness,
      session: this.#projection.snapshot.session,
      information_policy: INFORMATION_POLICY
    };
    const validationStarted = performance.now();
    if (this.#validateSdk) decodePlayerRead(value);
    this.#record("read_validation", performance.now() - validationStarted);
    return value;
  }

  async submit({ requestId, expectedSnapshotId, boundActionId, timeoutMs = 10_000 }) {
    if (this.#projection == null) throw new Error("No managed run is mounted.");
    for (const [name, value] of Object.entries({ requestId, expectedSnapshotId, boundActionId })) {
      if (typeof value !== "string" || value.length === 0) throw new TypeError(`submit requires ${name}.`);
    }
    const requestKey = JSON.stringify({ expectedSnapshotId, boundActionId });
    const previous = this.#ledger.get(requestId);
    if (previous != null) {
      if (previous.requestKey === requestKey) return previous.receipt;
      return this.#makeReceipt({
        requestId,
        delivery: "not_delivered",
        action: unknownAction(boundActionId),
        reasonCode: "request_id_conflict",
        detail: "The request id was already used for different snapshot/action operands.",
        retry: { allowed: false, reason: "request_identity_is_immutable" },
        successor: this.#projection.snapshot
      });
    }
    if (this.#tainted) {
      const projectionFailed = this.#taintReason === "successor_projection_failed";
      const value = this.#makeReceipt({
        requestId,
        delivery: "not_delivered",
        action: unknownAction(boundActionId),
        reasonCode: projectionFailed
          ? "runtime_tainted_after_successor_projection_failure" : "runtime_tainted_after_unknown",
        detail: "Mutation authority is closed until the process is replaced.",
        retry: { allowed: false, reason: projectionFailed
          ? "successor_projection_failure_requires_process_replacement"
          : "unknown_delivery_requires_process_replacement" },
        successor: null
      });
      this.#ledger.set(requestId, { requestKey, receipt: value });
      return value;
    }
    if (expectedSnapshotId !== this.#projection.snapshot.snapshot_id) {
      const value = this.#makeReceipt({
        requestId,
        delivery: "not_delivered",
        action: unknownAction(boundActionId),
        reasonCode: "stale_snapshot",
        detail: "The current snapshot no longer matches the request.",
        retry: { allowed: true, reason: "observe_current_snapshot_and_choose_again" },
        successor: this.#projection.snapshot
      });
      this.#ledger.set(requestId, { requestKey, receipt: value });
      return value;
    }
    const binding = this.#projection.bindings.get(boundActionId);
    if (binding == null) {
      const value = this.#makeReceipt({
        requestId,
        delivery: "not_delivered",
        action: unknownAction(boundActionId),
        reasonCode: "unknown_bound_action",
        detail: "The action is not part of the complete current projection.",
        retry: { allowed: false, reason: "unadvertised_actions_are_not_authorized" },
        successor: this.#projection.snapshot
      });
      this.#ledger.set(requestId, { requestKey, receipt: value });
      return value;
    }
    let successor;
    try {
      const transportStarted = performance.now();
      successor = await this.#process.request(binding.raw_request, timeoutMs);
      this.#record("action_transport_native_and_raw_extraction", performance.now() - transportStarted);
    } catch (error) {
      this.#tainted = true;
      this.#taintReason = "unknown";
      const value = this.#makeReceipt({
        requestId,
        delivery: "unknown",
        action: binding.action,
        reasonCode: "managed_transport_unknown",
        detail: error instanceof Error ? error.message : String(error),
        retry: { allowed: false, reason: "unknown_delivery_must_not_be_retried" },
        successor: null
      });
      this.#ledger.set(requestId, { requestKey, receipt: value });
      return value;
    }
    if (!plainObject(successor) || successor.type !== "decision") {
      this.#tainted = true;
      this.#taintReason = "unknown";
      const value = this.#makeReceipt({
        requestId,
        delivery: "unknown",
        action: binding.action,
        reasonCode: "managed_execution_unknown",
        detail: plainObject(successor) && successor.type === "error"
          ? String(successor.message ?? "Managed action returned an error after dispatch.")
          : "Managed action did not return a successor decision.",
        retry: { allowed: false, reason: "unknown_delivery_must_not_be_retried" },
        successor: null
      });
      this.#ledger.set(requestId, { requestKey, receipt: value });
      return value;
    }
    let next;
    try {
      next = await this.#setState(successor, timeoutMs);
    } catch (error) {
      // A decision returned from the native action confirms delivery, but a
      // failed successor projection leaves no trustworthy current binding.
      this.#tainted = true;
      this.#taintReason = "successor_projection_failed";
      const value = this.#makeReceipt({
        requestId,
        delivery: "delivered",
        action: binding.action,
        reasonCode: "managed_successor_projection_failed",
        detail: error instanceof Error ? error.message : String(error),
        retry: { allowed: false, reason: "delivered_action_requires_process_replacement" },
        successor: null
      });
      this.#ledger.set(requestId, { requestKey, receipt: value });
      return value;
    }
    const value = this.#makeReceipt({
      requestId,
      delivery: "delivered",
      action: binding.action,
      reasonCode: null,
      detail: "The exact bound input was delivered; no business completion is claimed.",
      retry: { allowed: false, reason: "request_already_delivered" },
      successor: next
    });
    this.#ledger.set(requestId, { requestKey, receipt: value });
    return value;
  }

  async #setState(state, timeoutMs) {
    let enriched = state;
    if (state?.decision === "map_select") {
      const mapReadStarted = performance.now();
      try {
        const visibleMap = await this.#process.request({ cmd: "get_map" }, timeoutMs);
        if (plainObject(visibleMap) && visibleMap.type === "map") {
          enriched = { ...state, visible_map: visibleMap };
        }
      } catch {
        // A failed read-only enrichment never changes known mutation delivery.
      } finally {
        this.#record("map_enrichment", performance.now() - mapReadStarted);
      }
    }
    this.#sequence += 1;
    const projectionStarted = performance.now();
    this.#projection = projectManagedCandidateDecision({
      state: enriched,
      runtimeInstanceId: this.#runtimeInstanceId,
      environmentFingerprint: this.#environmentFingerprint,
      sequence: this.#sequence,
      identityMode: this.#identityMode,
      validateSdk: this.#validateSdk
    });
    this.#record("projection_total", performance.now() - projectionStarted);
    for (const [name, milliseconds] of Object.entries(this.#projection.performance)) {
      if (name === "total_ms") continue;
      this.#record(`projection_${name.replace(/_ms$/u, "")}`, milliseconds);
    }
    return this.#projection.snapshot;
  }
}

export async function startManagedPlayerEnvironmentSession({
  root,
  candidateDirectory,
  diskIdentity,
  character = "Ironclad",
  language = "en",
  requestTimeoutMs = 10_000,
  identityMode = "crypto",
  validateSdk = true,
  quietDiagnostics = false
}) {
  const runtime = await startManagedCandidateRuntime({
    root,
    candidateDirectory,
    diskIdentity,
    requestTimeoutMs,
    quietDiagnostics
  });
  const environmentFingerprint = digest({
    candidate_id: runtime.manifest.candidate_id,
    source_patch_sha256: runtime.build.source_patch_sha256,
    host_artifact_sha256: runtime.build.artifact_sha256,
    runtime_sts2_sha256: runtime.build.runtime_sts2_sha256,
    exact_game: runtime.exactGame
  });
  return {
    session: new ManagedPlayerEnvironmentSession({
      process: runtime.process,
      runtimeInstanceId: runtime.adapterRuntimeInstanceId,
      environmentFingerprint,
      character,
      language,
      identityMode,
      validateSdk
    }),
    runtime,
    environmentFingerprint
  };
}
