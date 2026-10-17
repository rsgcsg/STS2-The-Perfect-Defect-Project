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
const CARD_ALTERNATIVE_OUTCOMES = new Set(["do_nothing", "end_selection_and_complete_reward", "end_selection_and_do_not_complete_reward"]);
// Pinned native TargetType values supported by the current Managed combat
// inputs. AnyAlly lacks a complete published single-player target domain.
// Potion values must also agree with the exact native binding switch; its
// TargetedNoCreature case needs a non-creature target referent not projected here.
const SUPPORTED_MANAGED_CARD_TARGET_TYPES = new Set([
  "None", "Self", "AnyEnemy", "AllEnemies", "RandomEnemy", "AnyPlayer",
  "AllAllies", "TargetedNoCreature", "Osty"
]);
const SUPPORTED_MANAGED_POTION_TARGET_TYPES = new Set([
  "None", "Self", "AnyEnemy", "AllEnemies", "RandomEnemy", "AllAllies", "AnyPlayer"
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

function createProjectionContext({
  state, runtimeInstanceId, sequence, identityMode = "crypto", publicEntityAliases
}) {
  if (!["crypto", "sequence"].includes(identityMode)) {
    throw new TypeError("Managed projection identityMode must be crypto or sequence.");
  }
  const rawStateSha = identityMode === "crypto" ? digest(state) : null;
  // This is the existing profile's projection version, not a P1 semantic
  // occurrence. Private state (including hidden order/RNG) never enters it.
  const snapshotId = `managed_${runtimeInstanceId}_s${sequence}`;
  const interactionId = `interaction_${runtimeInstanceId}_s${sequence}`;
  const referents = [];
  const actions = [];
  const bindings = new Map();
  const projectionAliases = new Map();

  function publicId(prefix, scope, ordinal) {
    return identityMode === "crypto"
      ? stableId(prefix, runtimeInstanceId, scope, ordinal)
      : `${prefix}_${runtimeInstanceId}_${scope}_${ordinal}`;
  }

  function makeId(prefix, ...parts) {
    // Private operands can key a lookup, never a public digest or identifier.
    // First use follows public projection order; Draw is normalized before it.
    const key = JSON.stringify(canonicalValue([prefix, ...parts]));
    if (!projectionAliases.has(key)) {
      projectionAliases.set(key, publicId(prefix, `s${sequence}`, projectionAliases.size + 1));
    }
    return projectionAliases.get(key);
  }

  function entityId(kind, nativeRef) {
    if (typeof nativeRef !== "string" || nativeRef.length === 0) {
      return makeId(kind, "unbound", projectionAliases.size);
    }
    const key = JSON.stringify([kind, nativeRef]);
    if (!publicEntityAliases.has(key)) {
      // Only a first PUBLIC appearance allocates this alias. The native token's
      // value/allocation counter is a private map key, not an alias ingredient.
      publicEntityAliases.set(key, publicId(kind, `visible_s${sequence}`, publicEntityAliases.size + 1));
    }
    return publicEntityAliases.get(key);
  }

  function referent({
    role,
    label,
    properties,
    enabled = null,
    selected = null,
    occurrence = 0,
    id = null,
    includeEntityId = true,
    kind = "entity"
  }) {
    const referentId = id ?? makeId("ref", snapshotId, role, properties, occurrence);
    const value = {
      referent_id: referentId,
      role,
      kind,
      label: label == null ? null : String(label),
      state: visibleState({ enabled, selected }),
      properties_schema: properties === null ? null : `sts2.player-environment/referent/${role.replace(/[^a-z0-9_]+/giu, "_").toLowerCase()}-1`,
      properties: properties === null ? null : { ...properties, ...(includeEntityId ? { entity_id: referentId } : {}) }
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
    detailReads: [],
    id: makeId,
    entityId,
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

function buildPersistentVisibleState(state, snapshotId, makeId, entityId) {
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
  const playerEntityId = entityId("player", player.native_ref);
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
            entity_id: entityId("relic", relic.native_ref),
            definition_id: definitionId(relic.id),
            name: relic.name ?? null,
            description: relic.description ?? null,
            ...(relic.counter == null ? {} : { counter: relic.counter }),
            keywords: visibleHoverKeywords(relic.keywords),
            card_previews: visibleHoverCards(relic.card_previews, snapshotId, `relic_${index}`, makeId)
          })),
          potions: potions.map((potion, index) => ({
            entity_id: entityId("potion", potion.native_ref),
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

function currentMapContent(raw, entityId) {
  if (!plainObject(raw) || raw.status !== "captured" || raw.kind !== "map" || !Array.isArray(raw.nodes)
    || !Array.isArray(raw.visited) || !Number.isSafeInteger(raw.act_index) || !("current_position" in raw)) return null;
  const coordinate = value => plainObject(value) && Number.isSafeInteger(value.col) && Number.isSafeInteger(value.row)
    && (value.point_type === null || typeof value.point_type === "string");
  const keys = new Set(), refs = new Set();
  if (raw.nodes.length === 0 || raw.nodes.some(node => !coordinate(node) || typeof node.native_ref !== "string"
      || node.native_ref.length === 0 || typeof node.point_type !== "string" || !node.point_type
      || !["none", "untravelable", "traveled", "travelable"].includes(node.state) || !Array.isArray(node.children)
      || node.children.some(child => !coordinate(child)) || keys.has(mapCoordinateKey(node.col,node.row))
      || refs.has(node.native_ref) || !keys.add(mapCoordinateKey(node.col,node.row)) || !refs.add(node.native_ref))) return null;
  const types = new Map(raw.nodes.map(node => [mapCoordinateKey(node.col,node.row),node.point_type]));
  const typeMatches = value => !types.has(mapCoordinateKey(value.col,value.row)) ? value.point_type === null
    : value.point_type === types.get(mapCoordinateKey(value.col,value.row));
  if (raw.nodes.some(node => node.children.some(child => !keys.has(mapCoordinateKey(child.col,child.row)) || !typeMatches(child)))
    || raw.visited.some(value => !coordinate(value) || !typeMatches(value))
    || raw.current_position !== null && (!coordinate(raw.current_position) || !typeMatches(raw.current_position))) return null;
  const copyCoordinate = value => ({ col: value.col, row: value.row, point_type: value.point_type });
  return { kind: "map", act_index: raw.act_index,
    current_position: raw.current_position === null ? null : copyCoordinate(raw.current_position),
    visited: raw.visited.map(copyCoordinate), nodes: raw.nodes.map(node => ({ entity_id: entityId("map_point",node.native_ref),
      col: node.col, row: node.row, point_type: node.point_type, state: node.state,
      children: node.children.map(copyCoordinate) })) };
}

function mapRead(snapshotId, state, makeId, entityId) {
  if (!plainObject(state.context) || !("current_map" in state.context) || state.decision === "game_over") return null;
  const content = currentMapContent(state.context.current_map, entityId);
  return { descriptor: { read_id: makeId("read",snapshotId,"map"), kind: "map", target_referent_id: null,
      content_schema: "sts2.player-environment/read/map-1", visibility_basis: "player_openable_current_generated_map",
      snapshot_bound: true, ordering_semantics: "native_map_topology", hidden_by_policy: [] },
    content: content ?? { kind: "map" }, completeness: { status: content ? "complete" : "partial",
      visible_information: "current_generated_map_without_action_authority", interaction_discovery: "read_only",
      missing: content ? [] : ["current_native_map_read_unavailable"], hidden_by_policy: [] } };
}

function buildVisibleMap(state, ctx) {
  if (plainObject(state.context?.current_map)) {
    const raw = state.context.current_map;
    const context = currentMapContent(raw, ctx.entityId);
    if (context === null || !Array.isArray(state.choices)) return null;
    const travelable = raw.nodes.filter(node => node.state === "travelable");
    const exact = state.choices.length === travelable.length && state.choices.every(choice =>
      travelable.filter(node => node.native_ref === choice.native_ref && node.col === choice.col && node.row === choice.row).length === 1);
    const nextOptions = exact ? state.choices.map(choice => {
      const node = context.nodes.find(node => node.col === choice.col && node.row === choice.row);
      const item = ctx.referent({ role: "option", id: node.entity_id, label: null,
        properties: { col: node.col, row: node.row, point_type: node.point_type } });
      ctx.action({ verb: "activate", subject: item, label: `Choose ${node.point_type} at (${node.col},${node.row})`,
        raw: { cmd: "action", action: "select_map_node", args: { col: node.col, row: node.row, map_point_ref: choice.native_ref } } });
      return item.properties;
    }) : [];
    return { context, surface: { kind: "map_navigation", travel_enabled: true, traveling: false,
      drawing_mode: "none", next_options: nextOptions, can_exit_annotation: false }, complete: exact,
      missing: exact ? [] : ["current_map_choices_do_not_match_same_native_capture"] };
  }
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
      current_position: current,
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

function visibleCombatCard(card, entityId, depth = 0) {
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
      existing_enchantment: {
        definition_id: definitionId(card.enchantment_id ?? card.enchantment),
        name: card.enchantment,
        description: card.enchantment_description ?? null,
        amount: card.enchantment_amount ?? 0,
        visibility_basis: "card_hover_semantics"
      }
    }),
    target_type: card?.target_type ?? null,
    can_play: card?.can_play ?? null,
    ...(card?.unplayable_reason == null ? {} : { unplayable_reason: card.unplayable_reason }),
    ...(Array.isArray(card?.keywords) ? { keywords: card.keywords.map((tip) => ({
      name: visibleText(tip?.name), description: visibleText(tip?.description)
    })) } : {}),
    ...(depth < 16 && Array.isArray(card?.card_previews) ? { card_previews: card.card_previews.map((preview) =>
      visibleCombatCard(preview, null, depth + 1)) } : {})
  };
}

function visibleCardBase(card, entityId) {
  const { keywords: _keywords, card_previews: _previews, ...base } = visibleCombatCard(card, entityId);
  return base;
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

function visibleHealth(creature) {
  return Object.fromEntries(["hp", "max_hp", "block", "health_bar_visible", "hp_display_mode",
    "hp_numbers_available", "health_display"].map((key) => [key, creature[key]]));
}
function visibleOrb(orb, ctx, selectorProfile = false) {
  return { entity_id: ctx.entityId("orb", orb.native_ref), definition_id: definitionId(orb.id),
    name: visibleText(orb.name), description: visibleText(orb.description),
    passive_value: orb.passive_value, evoke_value: orb.evoke_value,
    queue_index: orb.queue_index, is_next_to_evoke: orb.is_next_to_evoke,
    passive: orb.passive, evoke: orb.evoke,
    ...(selectorProfile ? { keywords: (orb.keywords ?? []).map(tip => ({ name: visibleText(tip.name),
      description: visibleText(tip.description) })) } : {}) };
}
function visibleCompanion(pet, ctx) {
  return { entity_id: ctx.entityId("companion", pet.native_ref), definition_id: definitionId(pet.id),
    name: visibleText(pet.name), is_alive: pet.is_alive, alive: pet.alive, ...visibleHealth(pet),
    statuses: (pet.statuses ?? []).map(visibleStatus) };
}
function canonicalHealthComplete(creature) {
  return plainObject(creature) && typeof creature.health_bar_visible === "boolean"
    && ["normal", "infinite_with_numbers", "infinite_without_numbers"].includes(creature.hp_display_mode)
    && typeof creature.hp_numbers_available === "boolean" && Number.isFinite(creature.block)
    && creature.hp_numbers_available === (creature.health_bar_visible && creature.hp_display_mode !== "infinite_without_numbers")
    && (creature.hp_numbers_available ? Number.isFinite(creature.hp) && Number.isFinite(creature.max_hp)
      : creature.hp === null && creature.max_hp === null);
}
function canonicalHudComplete(hud) {
  return plainObject(hud) && typeof hud.encounter_type === "string" && typeof hud.is_play_phase === "boolean"
    && Number.isSafeInteger(hud.round) && hud.round >= 1 && typeof hud.turn_owner === "string" && hud.turn_owner.length > 0
    && ["energy", "max_energy", "orb_slots"].every(key => Number.isSafeInteger(hud[key]))
    && ["draw_pile_count", "discard_pile_count", "exhaust_pile_count"].every(key => Number.isSafeInteger(hud[key]) && hud[key] >= 0)
    && (hud.stars === null || Number.isSafeInteger(hud.stars))
    && Number.isFinite(plainObject(hud.player) ? hud.player.block : hud.block)
    && Array.isArray(hud.potion_states) && hud.potion_states.every((potion) => typeof potion.native_ref === "string"
      && typeof potion.target_type === "string" && typeof potion.can_use === "boolean" && typeof potion.automatic === "boolean")
    && Array.isArray(hud.orbs) && hud.orbs.every((orb, index) => typeof orb.native_ref === "string"
      && typeof orb.id === "string" && typeof orb.description === "string" && Number.isFinite(orb.passive_value)
      && Number.isFinite(orb.evoke_value) && orb.queue_index === index && orb.is_next_to_evoke === (index === 0))
    && Array.isArray(hud.companions) && hud.companions.every((pet) => canonicalHealthComplete(pet)
      && typeof pet.native_ref === "string" && typeof pet.id === "string" && typeof pet.is_alive === "boolean")
    && Array.isArray(hud.enemies) && hud.enemies.every((enemy) => canonicalHealthComplete(enemy)
      && (enemy.combat_id === null || Number.isSafeInteger(enemy.combat_id) && enemy.combat_id >= 0 && enemy.combat_id <= 4_294_967_295));
}

function canonicalSelectorSurface(state, ctx, commonContext) {
  const facts = state.selector_public;
  const request = facts.selector_request;
  const rawCards = Array.isArray(state.cards) ? state.cards : [];
  const options = Array.isArray(facts.preview_options) ? facts.preview_options : [];
  const selectedRefs = Array.isArray(state.selected_refs) ? state.selected_refs : [];
  const cardRefs = rawCards.map(card => card.native_ref);
  const text = value => typeof value === "string" && value.length > 0;
  const exact = plainObject(request) && text(state.selector_ref)
    && text(request.entity_id) && request.owner_entity_id === state.selector_ref
    && ["deck_upgrade", "deck_card", "deck_enchant", "deck_transform"].includes(request.family)
    && request.family === (state.decision === "deck_upgrade_select" ? "deck_upgrade"
      : state.origin === "deck_enchant" ? "deck_enchant" : state.origin === "deck_transform" ? "deck_transform" : "deck_card")
    && request.stage === state.stage && ["selecting", "preview"].includes(state.stage)
    && Number.isSafeInteger(state.min_select) && state.min_select >= 0
    && Number.isSafeInteger(state.max_select) && state.max_select >= state.min_select
    && request.min_select === state.min_select && request.max_select === state.max_select
    && request.require_manual_confirmation === state.require_manual_confirmation
    && rawCards.length === options.length && rawCards.length <= ACTION_LIMIT - 4
    && cardRefs.every(text) && new Set(cardRefs).size === cardRefs.length
    && selectedRefs.every(ref => cardRefs.includes(ref)) && new Set(selectedRefs).size === selectedRefs.length
    && JSON.stringify(request.selected_entity_ids) === JSON.stringify(selectedRefs)
    && options.every((option, index) => option?.subject_entity_id === cardRefs[index]
      && plainObject(option.before) && option.before.entity_id === cardRefs[index]
      && option.before.is_selected === selectedRefs.includes(cardRefs[index])
      && (option.after === null || plainObject(option.after) && option.after.entity_id === cardRefs[index])
      && Array.isArray(option.missing))
    && rawCards.every(card => typeof card.is_selectable === "boolean" && typeof card.is_deselectable === "boolean")
    && Array.isArray(facts.current_controls) && request.mechanics_qualified === true;
  const missing = [...new Set([...(Array.isArray(facts.missing) ? facts.missing : []),
    ...(Array.isArray(request?.missing) ? request.missing : []),
    ...(exact ? [] : ["exact_current_native_selector_public_binding"])])];
  const ownerId = ctx.entityId("screen", state.selector_ref);
  const requestId = ctx.entityId("native_selector_request", request?.entity_id);
  const previewOptions = options.map((option, index) => {
    const entityId = ctx.entityId("card", cardRefs[index]);
    const before = { ...structuredClone(option.before), entity_id: entityId };
    const after = option.after === null ? null : { ...structuredClone(option.after), entity_id: entityId };
    const subject = ctx.referent({ role: "card", id: entityId, label: before.name,
      selected: before.is_selected, enabled: exact && (rawCards[index].is_selectable || rawCards[index].is_deselectable),
      properties: before });
    if (exact) for (const [field, verb, action] of [
      ["is_selectable", "select", state.decision === "deck_upgrade_select" ? "select_upgrade_card" : "select_deck_card"],
      ["is_deselectable", "deselect", state.decision === "deck_upgrade_select" ? "deselect_upgrade_card" : "deselect_deck_card"]
    ]) if (rawCards[index][field]) ctx.action({ verb, subject, label: `${verb} ${before.name}`,
      raw: { cmd: "action", action, args: { selector_ref: state.selector_ref, card_ref: cardRefs[index] } } });
    for (const [card, context, renderMissing] of [[before, `selector_original:${request?.family}`, option.before_missing ?? option.missing],
      ...(after === null ? [] : [[after, option.display_context, option.after_missing ?? option.missing]])]) {
      const { keywords, card_previews: cardPreviews, ...base } = card;
      ctx.detailReads.push(publicCardDetailRead(ctx, Object.fromEntries(Object.entries(base).filter(([, value]) => value !== null)),
        { keywords, card_previews: cardPreviews }, renderMissing, context, ownerId));
    }
    return { subject_entity_id: entityId, before, after, display_context: option.display_context,
      after_availability: option.after_availability, missing: [...option.missing] };
  });
  const controls = [];
  const confirmAction = state.decision === "deck_upgrade_select" ? "confirm_upgrade_selection" : "confirm_deck_selection";
  const operations = {
    open_preview: ["open", "preview_deck_selection"],
    confirm: ["confirm", confirmAction],
    confirm_selection: ["confirm", confirmAction],
    cancel_preview: ["cancel", state.decision === "deck_upgrade_select" ? "cancel_upgrade_preview" : "cancel_deck_preview"],
    cancel_selection: ["cancel", state.decision === "deck_upgrade_select" ? "cancel_upgrade_selection" : "cancel_deck_selection"]
  };
  if (exact) for (const control of facts.current_controls) {
    const operation = control.operation;
    if (!operations[operation]) { missing.push("unregistered_native_selector_control"); continue; }
    const [verb, action] = operations[operation];
    const id = ctx.entityId("selector_control", `${state.selector_ref}:${operation}`);
    const subject = ctx.referent({ role: "selector_control", id, label: operation,
      enabled: true, kind: "control", properties: null });
    controls.push({ entity_id: id, operation });
    ctx.action({ verb, subject, label: operation, raw: { cmd: "action", action,
      args: { selector_ref: state.selector_ref,
        ...(verb === "confirm" ? { selected_refs: selectedRefs.join(",") } : {}) } } });
  }
  const kind = request?.family === "deck_upgrade" ? "deck_upgrade_selection"
    : request?.family === "deck_enchant" ? "deck_enchant_selection"
      : request?.family === "deck_transform" ? "deck_transform_selection" : "native_deck_card_selection";
  const selectedIds = selectedRefs.map(ref => ctx.entityId("card", ref));
  const complete = exact && missing.length === 0 && request.preview_information_complete === true
    && request.preview_projection_qualified === true;
  return {
    kind, stage: state.stage, prompt: state.prompt ?? null,
    surface: { kind, stage: state.stage, screen_entity_id: ownerId, request_entity_id: requestId,
      prompt: state.prompt ?? null, cards: previewOptions.map(option => option.before),
      selector_request: { ...structuredClone(request), entity_id: requestId, owner_entity_id: ownerId,
        selected_entity_ids: selectedIds }, preview_options: previewOptions,
      enchantment_summary: structuredClone(facts.enchantment_summary ?? null),
      min_select: state.min_select, max_select: state.max_select,
      require_manual_confirmation: state.require_manual_confirmation, selected_count: selectedIds.length,
      selected_card_entity_ids: selectedIds,
      selectable_card_entity_ids: rawCards.filter(card => exact && card.is_selectable).map(card => ctx.entityId("card", card.native_ref)),
      deselectable_card_entity_ids: rawCards.filter(card => exact && card.is_deselectable).map(card => ctx.entityId("card", card.native_ref)),
      can_confirm: controls.some(control => ["confirm", "confirm_selection"].includes(control.operation)),
      cancelable: exact ? state.cancelable : null, current_controls: controls,
      is_peeking: false, showing_upgrade_previews: false,
      ordering_basis: "exact_current_native_grid_logical_list" },
    context: { ...commonContext, kind: "selection" }, complete, actionComplete: exact,
    visibleInformation: "complete_entered_native_logical_grid_list",
    interactionDiscovery: "registered_native_card_model_callbacks_and_current_controls", missing
  };
}

function currentSurface(state, ctx) {
  const commonContext = {
    kind: "run",
    run: plainObject(state.context) ? Object.fromEntries(Object.entries(state.context).filter(([key]) => key !== "current_map")) : null,
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
            enabled,
            // Public stable group identity (alias of the private native reward ref).
            group_id: ctx.entityId("reward_group", reward.native_ref),
            group_relation: ["independent", "linked"].includes(reward.group_relation) ? reward.group_relation : "unavailable",
            // Lawfully viewable candidates of an offered, populated card reward (P1 aggregation).
            ...(kind === "card" ? { card_options: Array.isArray(reward.card_options)
              ? reward.card_options.map((card, optionIndex) => ({ index: card?.index ?? optionIndex,
                ...visibleCombatCard(card, ctx.entityId("card", card?.native_ref)) }))
              : null,
              card_alternatives: Array.isArray(reward.card_alternatives) ? reward.card_alternatives.map(option => ({
                option_id: option.option_id, title: option.title, after_selected: option.after_selected, is_native_skip: option.is_native_skip === true })) : null,
              card_alternatives_complete: reward.card_alternatives_complete === true
                && Array.isArray(reward.card_alternatives)
                && reward.card_alternatives.every(option => typeof option?.option_id === "string" && option.option_id.length > 0
                  && typeof option.title === "string" && option.title.length > 0 && CARD_ALTERNATIVE_OUTCOMES.has(option.after_selected))
                && new Set(reward.card_alternatives.map(option => option.option_id)).size === reward.card_alternatives.length
                && ["can_skip_selection", "can_reroll", "selection_completes_reward"].every(key => typeof reward[key] === "boolean"),
              can_skip_selection: reward.can_skip_selection ?? null,
              can_reroll: reward.can_reroll ?? null,
              selection_completes_reward: reward.selection_completes_reward ?? null,
              native_relic_refresh_subscription: ["attached", "absent"].includes(reward.native_relic_refresh_subscription)
                ? reward.native_relic_refresh_subscription : "unavailable" } : {})
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
          const entityId = ctx.entityId("potion", potion.native_ref);
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
          reward_set_id: typeof state.reward_set_ref === "string" ? ctx.entityId("reward_set", state.reward_set_ref) : null,
          rewards,
          potion_slots_full: potionSlotsFull,
          discardable_potions: discardablePotions,
          can_proceed: state.is_terminal === true && state.can_proceed === true,
          can_leave_with_unclaimed_rewards: state.can_leave_with_unclaimed_rewards ?? null,
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
    case "relic_choice": {
      const relics = Array.isArray(state.relics) ? state.relics : [];
      const exact = state.native_relic_complete === true && typeof state.selector_ref === "string" && state.selector_ref.length > 0
        && state.stage === "choosing" && state.can_skip === true && relics.length > 0 && relics.length + 1 <= ACTION_LIMIT
        && relics.every((relic, index) => relic?.index === index && typeof relic.native_ref === "string" && relic.native_ref.length > 0
          && ["id", "name", "description"].every(key => typeof relic[key] === "string" && relic[key].trim().length > 0)
          && relic.hover_facts_complete === true && Array.isArray(relic.keywords) && Array.isArray(relic.card_previews))
        && new Set(relics.map(relic => relic.native_ref)).size === relics.length;
      const offered = relics.map((relic, index) => {
        const unique = typeof relic?.native_ref === "string" && relics.filter(option => option?.native_ref === relic.native_ref).length === 1;
        const item = ctx.referent({ role: "relic", id: unique ? ctx.entityId("relic", relic.native_ref) : ctx.id("unbound_relic", index),
          label: relic.name, occurrence: index, enabled: exact,
          properties: { definition_id: definitionId(relic.id), name: visibleText(relic.name), description: visibleText(relic.description),
            counter: relic.counter ?? null, keywords: visibleHoverKeywords(relic.keywords ?? []),
            card_previews: visibleHoverCards(relic.card_previews ?? [], ctx.snapshotId, `relic_choice_${index}`, ctx.id) } });
        if (exact) ctx.action({ verb: "select", subject: item, label: `Choose ${relic.name}`,
          raw: { cmd: "action", action: "select_native_relic", args: { selector_ref: state.selector_ref, relic_ref: relic.native_ref } } });
        return item.properties;
      });
      if (exact) ctx.action({ verb: "skip", label: "Skip relic selection", raw: {
        cmd: "action", action: "skip_native_relic", args: { selector_ref: state.selector_ref } } });
      return { kind: "native_boss_relic_selection", stage: "choosing", prompt: null,
        surface: { kind: "native_boss_relic_selection", stage: "choosing", selector_profile: "managed-native-relic-presentation-1",
          relics: offered, selectable_relic_entity_ids: exact ? offered.map(relic => relic.entity_id) : [], can_skip: exact && state.can_skip },
        context: { ...commonContext, kind: "relic_choice" }, complete: exact,
        missing: exact ? [] : ["exact_current_native_relic_presentation", ...(state.native_relic_gap ? [state.native_relic_gap] : [])],
        visibleInformation: "complete_original_native_relic_offer_and_current_public_display",
        interactionDiscovery: "exact_synchronous_RelicSelectCmd_presentation_and_original_object_completion" };
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
      const cardIdentityComplete = rawCards.every(card => typeof card.card_entity_ref === "string" && card.card_entity_ref.length > 0);
      const parentComplete = typeof state.parent_reward_ref === "string" && state.parent_reward_ref.length > 0
        && state.parent_status === "bound" && typeof state.reward_set_ref === "string" && state.reward_set_ref.length > 0
        && typeof state.selection_completes_reward === "boolean";
      const renderMissing = rawCards.flatMap((card, index) => card.render_complete === true
          && ["id", "name", "type", "rarity", "description"].every(key => typeof card[key] === "string" && card[key].length > 0)
          && (typeof card.cost === "string" || Number.isFinite(card.cost)) && typeof card.is_upgraded === "boolean"
          && Array.isArray(card.keywords) && Array.isArray(card.card_previews) ? [] : [
            `card_reward_render:${index}:incomplete`,
            ...(Array.isArray(card.render_missing) ? card.render_missing.map(reason => `card_reward_render:${index}:${reason}`) : [])
          ]);
      const metadataComplete = cardIdentityComplete && parentComplete && renderMissing.length === 0;
      const cards = rawCards.map((card, index) => {
        const item = ctx.referent({
          role: "card",
          id: typeof card.card_entity_ref === "string" ? ctx.entityId("card", card.card_entity_ref) : ctx.id("unbound_card", index),
          label: card?.name ?? `Card ${index + 1}`,
          occurrence: index,
          properties: { index: card?.index ?? index, ...visibleCombatCard(card, typeof card.card_entity_ref === "string" ? ctx.entityId("card", card.card_entity_ref) : ctx.id("unbound_card", index)) }
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
          properties: { option_id: alternative?.option_id ?? alternative?.id ?? null,
            index, label: alternative?.name ?? null, enabled: true, title: alternative?.title ?? null,
            after_selected: alternative?.after_selected ?? null, is_native_skip: alternative?.is_native_skip === true }
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
          parent_status: ["bound", "standalone"].includes(state.parent_status) ? state.parent_status : "unavailable",
          parent_group_id: typeof state.parent_reward_ref === "string" ? ctx.entityId("reward_group", state.parent_reward_ref) : null,
          reward_set_id: typeof state.reward_set_ref === "string" ? ctx.entityId("reward_set", state.reward_set_ref) : null,
          selection_completes_reward: typeof state.selection_completes_reward === "boolean" ? state.selection_completes_reward : null,
          native_relic_refresh_subscription: ["attached", "absent"].includes(state.native_relic_refresh_subscription)
            ? state.native_relic_refresh_subscription : "unavailable",
          can_skip: rawAlternatives.some((option) => option.id.toLowerCase() === "skip"),
          // Same fact as the Reference card reward page: every offered card has its select input.
          selectable_card_entity_ids: cards.map((card) => card.entity_id) },
        context: { ...commonContext, kind: "reward" },
        complete: metadataComplete,
        visibleInformation: "contract_complete_for_current_native_card_reward",
        interactionDiscovery: "derived_from_same_current_native_card_reward_callback_as_execution",
        missing: metadataComplete ? [] : ["exact_card_reward_parent_and_card_identity",
          ...(cardIdentityComplete ? [] : ["native_card_reward_card_identity"]),
          ...(parentComplete ? [] : ["native_card_reward_parent_identity"]),
          ...renderMissing]
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
      if (plainObject(state.selector_public)) return canonicalSelectorSurface(state, ctx, commonContext);
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
          ? ctx.entityId("card", card.native_ref) : null,
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
      if (plainObject(state.selector_public)) return canonicalSelectorSurface(state, ctx, commonContext);
      const cards = Array.isArray(state.cards) ? state.cards : [];
      const selectedRefs = Array.isArray(state.selected_refs) ? state.selected_refs : [];
      const cardRefs = cards.map((card) => card?.native_ref);
      const stage = state.stage;
      const selectedCount = selectedRefs.length;
      // The exact FromDeckForEnchantment callback reuses this selector and is
      // presented as the native enchant screen, naming the enchantment it applies.
      const enchant = state.origin === "deck_enchant";
      const enchantment = enchant && plainObject(state.enchantment)
        && typeof state.enchantment.id === "string" && state.enchantment.id.length > 0
        && typeof state.enchantment.name === "string" && state.enchantment.name.length > 0
        && Number.isSafeInteger(state.enchantment.amount)
        ? { definition_id: state.enchantment.id, name: state.enchantment.name,
          description: typeof state.enchantment.description === "string" ? state.enchantment.description : null,
          amount: state.enchantment.amount }
        : null;
      const selectionKind = enchant ? "deck_enchant_selection" : "deck_card_selection";
      const exact = (state.origin === "deck_generic" || (enchant && enchantment !== null))
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
          ? ctx.entityId("card", card.native_ref) : null,
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
          // Same wire meaning as Reference's open_player_environment_deck_preview.
          // This opens the native confirmation stage; it does not commit the cards.
          verb: "open", label: "Preview card selection",
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
        kind: selectionKind, stage, prompt: state.prompt ?? null,
        surface: { kind: selectionKind, stage,
          ...(enchant ? { enchantment } : {}),
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
        missing: exact ? [] : [`exact_current_${selectionKind}_binding`]
      };
    }
    case "native_card_select": {
      const cards = Array.isArray(state.cards) ? state.cards : [];
      const selected = Array.isArray(state.selected_refs) ? state.selected_refs : [];
      const refs = cards.map((card) => card?.native_ref);
      const choose = state.origin === "choose_card";
      const combat = state.origin === "combat_pile";
      const manual = state.require_manual_confirmation;
      const hud = state.combat_context;
      const text = (value) => typeof value === "string" && value.trim().length > 0
        && !/[{}]|\[img/iu.test(value);
      const cardComplete = (card, depth = 0) => depth < 16 && plainObject(card)
        && ["id", "name", "type", "cost", "rarity", "description", "target_type"].every((key) => text(card[key]))
        && typeof card.is_upgraded === "boolean"
        && Array.isArray(card.keywords) && card.keywords.every((tip) => text(tip?.name) && text(tip?.description))
        && Array.isArray(card.card_previews) && card.card_previews.every((preview) => cardComplete(preview, depth + 1))
        && (card.enchantment == null || text(card.enchantment) && text(card.enchantment_description));
      const statusComplete = (values) => Array.isArray(values) && values.every((value) =>
        text(value?.id) && text(value?.name) && text(value?.description) && Number.isFinite(value?.amount));
      const hudComplete = canonicalHudComplete(hud) && hud.player_ref === state.player?.native_ref
        && Number.isSafeInteger(hud.round) && hud.round >= 1 && text(hud.turn_owner)
        && ["hp", "max_hp", "block", "energy", "max_energy", "draw_pile_count", "discard_pile_count",
          "exhaust_pile_count", "orb_slots"].every((key) => Number.isFinite(hud[key]))
        && Array.isArray(hud.hand) && hud.hand.every((card) => text(card?.native_ref)
          && card.render_complete === true && cardComplete(card))
        && new Set(hud.hand.map((card) => card.native_ref)).size === hud.hand.length
        && Array.isArray(state.combat_piles) && state.combat_piles.length === 3
        && ["draw", "discard", "exhaust"].every((pile) => {
          const zones = state.combat_piles.filter((zone) => zone?.pile === pile);
          return zones.length === 1 && zones[0].ordering_semantics === (pile === "draw" ? "unordered_multiset" : "native_visible_order")
            && Array.isArray(zones[0].cards) && zones[0].cards.length === hud[`${pile}_pile_count`]
            && zones[0].cards.every((card) => card?.render_complete === true && cardComplete(card));
        })
        && statusComplete(hud.statuses) && Array.isArray(hud.orbs) && hud.orbs.every((orb) => text(orb?.native_ref)
          && text(orb?.id) && text(orb?.name) && Number.isFinite(orb.passive) && Number.isFinite(orb.evoke)
          && Array.isArray(orb.keywords) && orb.keywords.every((tip) => text(tip?.name) && text(tip?.description)))
        && Array.isArray(hud.companions) && hud.companions.every((pet) => text(pet?.native_ref) && text(pet?.id)
          && typeof pet.alive === "boolean" && typeof pet.health_bar_visible === "boolean" && text(pet.health_display)
          && (pet.hp === null || Number.isFinite(pet.hp)) && (pet.max_hp === null || Number.isFinite(pet.max_hp))
          && Number.isFinite(pet.block) && statusComplete(pet.statuses))
        && Array.isArray(hud.enemies) && hud.enemies.every((enemy) => text(enemy?.native_ref) && text(enemy?.id)
          && text(enemy?.name) && typeof enemy?.health_bar_visible === "boolean" && text(enemy?.health_display)
          && (enemy.hp === null || Number.isFinite(enemy.hp)) && (enemy.max_hp === null || Number.isFinite(enemy.max_hp))
          && Number.isFinite(enemy.block) && statusComplete(enemy.statuses)
          && Array.isArray(enemy.intents) && enemy.intents.every((intent) =>
            ["type", "title", "description"].every((key) => text(intent?.[key]))
            && (intent.label === "" || text(intent.label))));
      const exact = (choose || combat) && state.native_selector_complete === true
        && Array.isArray(state.native_selector_missing) && state.native_selector_missing.length === 0
        && text(state.selector_ref) && text(state.prompt) && state.stage === "choosing" && state.cancelable === false
        && typeof manual === "boolean" && typeof state.can_skip === "boolean" && typeof state.can_confirm === "boolean"
        && cards.length > 0 && cards.length + 2 <= ACTION_LIMIT && new Set(refs).size === cards.length
        && cards.every((card) => text(card?.native_ref) && card.render_complete === true && cardComplete(card)
          && typeof card.is_selected === "boolean" && typeof card.is_selectable === "boolean"
          && typeof card.is_deselectable === "boolean")
        && selected.length <= 1 && new Set(selected).size === selected.length && selected.every((ref) => refs.includes(ref))
        && cards.every((card) => card.is_selected === selected.includes(card.native_ref)
          && card.is_selectable === (!card.is_selected && selected.length === 0)
          && card.is_deselectable === (manual && card.is_selected))
        && state.max_select === 1 && state.can_confirm === (manual && selected.length === 1)
        && (choose ? !manual && selected.length === 0 && cards.length <= 3 && state.display_pile === "none"
          && state.min_select === (state.can_skip ? 0 : 1)
          : !state.can_skip && state.min_select === 1 && ["draw", "discard", "exhaust"].includes(state.display_pile))
        && hudComplete;
      const publicCard = (card, id, depth = 0) => ({
        ...visibleCombatCard(card, id), is_selected: card?.is_selected === true,
        is_selectable: card?.is_selectable === true, is_deselectable: card?.is_deselectable === true,
        keywords: Array.isArray(card?.keywords) ? card.keywords.map((tip) => ({ name: visibleText(tip?.name), description: visibleText(tip?.description) })) : [],
        card_previews: depth < 16 && Array.isArray(card?.card_previews) ? card.card_previews.map((preview, index) =>
          publicCard(preview, ctx.id("hover_card", id, depth, index), depth + 1)) : []
      });
      const referents = cards.map((card, index) => {
        const unique = typeof card?.native_ref === "string" && refs.filter((ref) => ref === card.native_ref).length === 1;
        const id = unique ? ctx.entityId("card", card.native_ref) : ctx.id("unbound_card", index);
        return ctx.referent({ role: "card", label: card?.name ?? "Unavailable card", occurrence: index, id,
          enabled: exact && (card.is_selectable || card.is_deselectable), selected: card?.is_selected === true,
          properties: publicCard(card, id) });
      });
      if (exact) {
        for (let index = 0; index < cards.length; index++) {
          const card = cards[index]; const referent = referents[index];
          for (const [available, verb, operation] of [[card.is_selectable, "select", "select_native_card"],
            [card.is_deselectable, "deselect", "deselect_native_card"]]) if (available) ctx.action({
              verb, subject: referent, label: `${verb === "select" ? "Select" : "Deselect"} ${referent.label}`,
              raw: { cmd: "action", action: operation, args: { selector_ref: state.selector_ref, card_ref: card.native_ref } }
            });
        }
        if (state.can_skip) ctx.action({ verb: "skip", label: "Skip generated card choice",
          raw: { cmd: "action", action: "skip_native_selection", args: { selector_ref: state.selector_ref } } });
        if (state.can_confirm) ctx.action({ verb: "confirm", label: "Confirm card selection",
          raw: { cmd: "action", action: "confirm_native_selection", args: {
            selector_ref: state.selector_ref, selected_refs: selected.join(",") } } });
      }
      const publicHud = hudComplete ? {
        kind: "combat", encounter_type: hud.encounter_type, is_play_phase: hud.is_play_phase,
        round: hud.round, turn_owner: hud.turn_owner,
        player: { player_entity_id: ctx.entityId("player", hud.player_ref),
          hand: hud.hand.map((card) => publicCard(card, ctx.entityId("card", card.native_ref))),
          ...Object.fromEntries(["hp", "max_hp", "block", "energy", "max_energy", "stars", "draw_pile_count",
            "discard_pile_count", "exhaust_pile_count", "orb_slots"].map((key) => [key, hud[key]])),
          statuses: hud.statuses.map(visibleStatus), orbs: hud.orbs.map((orb) => visibleOrb(orb, ctx, true)),
          potion_states: hud.potion_states.map((potion) => ({ entity_id: ctx.entityId("potion", potion.native_ref),
            target_type: potion.target_type, can_use: potion.can_use, automatic: potion.automatic })),
          companions: hud.companions.map((pet) => visibleCompanion(pet, ctx)) },
        enemies: hud.enemies.map((enemy) => ({ entity_id: ctx.entityId("enemy", enemy.native_ref),
          definition_id: definitionId(enemy.id), name: visibleText(enemy.name), combat_id: enemy.combat_id, ...visibleHealth(enemy),
          statuses: enemy.statuses.map(visibleStatus), intents: enemy.intents.map(visibleIntent) }))
      } : null;
      return { kind: "card_selection", stage: "choosing", prompt: state.prompt ?? null,
        surface: { kind: "card_selection", selector_profile: "managed-command-selector-1", selector_kind: state.origin ?? "unknown", stage: "choosing",
          display_context: state.display_pile ?? null, cards: referents.map((item) => item.properties),
          native_option_count: cards.length, min_select: state.min_select, max_select: state.max_select,
          selected_count: selected.length, selected_card_entity_ids: referents.filter((_, index) => cards[index]?.is_selected).map((item) => item.referent_id),
          require_manual_confirmation: manual, can_skip: state.can_skip, can_confirm: state.can_confirm, cancelable: false },
        context: { kind: "selection", combat: publicHud }, complete: exact,
        missing: exact ? [] : ["exact_native_selector_context_display_or_domain", ...(Array.isArray(state.native_selector_missing) ? state.native_selector_missing : [])],
        visibleInformation: "current_native_selector_cards_and_current_post_prefix_combat_hud",
        interactionDiscovery: "exact_synchronous_native_command_context_and_execute_time_current_domain",
        hiddenByPolicy: ["hidden_rng", "draw_pile_true_order", "unoffered_draw_cards", "future_enemy_moves"] };
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
        potion.binding_supported !== true || !supportsManagedPotionTargetType(potion.target_type)
        || (potion.target_type === "AnyPlayer"
          && (typeof potion.can_use !== "boolean"
            || !Array.isArray(potion.valid_target_refs)
            || potion.valid_target_refs.length > 1
            || potion.valid_target_refs.some((nativeRef) => nativeRef !== state.player?.native_ref)
            || (potion.can_use === true && potion.valid_target_refs.length !== 1))));
      const playWindowOpen = state.is_play_phase === true;
      const semanticFactsComplete = state.combat_hud_complete === true && canonicalHudComplete(state)
        && typeof state.encounter_type === "string"
        && typeof state.turn_owner === "string"
        && typeof state.is_play_phase === "boolean"
        && Number.isSafeInteger(state.exhaust_pile_count)
        && Number.isSafeInteger(state.orb_slots)
        && Array.isArray(state.orbs)
        && Array.isArray(state.companions)
        && Array.isArray(state.player_statuses)
        && rawEnemies.every((enemy) => typeof enemy.id === "string"
          && (enemy.combat_id === null || Number.isSafeInteger(enemy.combat_id))
          && Array.isArray(enemy.statuses)
          && (enemy.intents ?? []).every((intent) =>
            typeof intent.label === "string"
            && typeof intent.title === "string"
            && typeof intent.description === "string"));

      const enemyEntries = rawEnemies.map((enemy, index) => {
        const properties = {
          entity_id: ctx.entityId("enemy", enemy.native_ref),
          combat_id: enemy.combat_id,
          definition_id: definitionId(enemy.id),
          name: enemy.name ?? null,
          ...visibleHealth(enemy),
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
        const entityId = ctx.entityId("card", card.native_ref);
        const visibleCard = visibleCardBase(card, entityId);
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

      const playerEntityId = ctx.entityId("player", state.player?.native_ref);
      const playerContext = {
        player_entity_id: playerEntityId,
        block: state.player?.block,
        energy: state.energy,
        max_energy: state.max_energy,
        ...(state.stars == null ? {} : { stars: state.stars }),
        hand: handEntries.map((entry) => entry.card),
        draw_pile_count: state.draw_pile_count,
        discard_pile_count: state.discard_pile_count,
        exhaust_pile_count: state.exhaust_pile_count,
        statuses: (state.player_statuses ?? []).map(visibleStatus),
        companions: (state.companions ?? []).map((pet) => visibleCompanion(pet, ctx)),
        potion_states: (state.potion_states ?? []).map((potion) => ({
          entity_id: ctx.entityId("potion", potion.native_ref),
          target_type: potion.target_type,
          can_use: potion.can_use,
          automatic: potion.automatic
        })),
        orbs: (state.orbs ?? []).map((orb) => visibleOrb(orb, ctx)),
        orb_slots: state.orb_slots
      };
      const playerReferent = ctx.referent({
        role: "player",
        label: null,
        id: playerEntityId,
        properties: playerContext,
        includeEntityId: false
      });
      const targetEntriesForPotion = (potion) => potion.target_type === "AnyPlayer"
        ? [{ raw: state.player, referent: playerReferent }]
        : enemyEntries;
      const targetRefsForPotion = (potion) => potion.target_type === "AnyPlayer"
        ? (Array.isArray(potion.valid_target_refs) ? potion.valid_target_refs : [])
        : (potion.valid_target_refs ?? []);
      const targetIdsForPotion = (potion) => {
        const byNative = new Map(targetEntriesForPotion(potion).map((entry) => [
          entry.raw.native_ref, entry.referent.referent_id
        ]));
        return targetRefsForPotion(potion)
          .map((nativeRef) => byNative.get(nativeRef)).filter(Boolean);
      };

      for (const [index, potion] of rawPotions.entries()) {
        const targetIds = targetIdsForPotion(potion);
        const visiblePotion = {
          entity_id: ctx.entityId("potion", potion.native_ref),
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
          if (potion.target_type === "AnyEnemy" || potion.target_type === "AnyPlayer") {
            const validTargets = new Set(targetRefsForPotion(potion));
            for (const { raw: candidate, referent: target } of targetEntriesForPotion(potion)) {
              if (!validTargets.has(candidate.native_ref)) continue;
              ctx.action({
                verb: "use",
                subject: potionReferent,
                arguments: [{ role: "target", referent: target }],
                label: `Use ${potionReferent.label} on ${target.label ?? state.player?.name ?? "Player"}`,
                raw: {
                  cmd: "action",
                  action: "use_potion",
                  args: {
                    potion_slot: potion.slot,
                    potion_ref: potion.native_ref,
                    target_ref: candidate.native_ref
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
              entity_id: ctx.entityId("potion", potion.native_ref),
              name: potion.name ?? null,
              target_entity_ids: targetIdsForPotion(potion)
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
          ...(semanticFactsComplete ? [] : ["complete_visible_combat_context",
            ...(typeof state.combat_hud_gap === "string" ? [`combat_hud_gap:${state.combat_hud_gap}`] : [])]),
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
    case "shop_room": {
      const owner = state.presentation_owner;
      const exact = plainObject(owner) && owner.kind === "managed_merchant_presentation" && owner.stage === "room"
        && typeof state.room_ref === "string" && state.room_ref.length > 0
        && typeof state.inventory_ref === "string" && state.inventory_ref.length > 0
        && state.shop_ref === state.room_ref
        && typeof owner.player_ref === "string" && owner.player_ref.length > 0
        && owner.room_ref === state.room_ref && owner.inventory_ref === state.inventory_ref
        && owner.player_ref === state.player?.native_ref
        && typeof state.can_open_inventory === "boolean" && typeof state.can_proceed === "boolean";
      if (exact && state.can_open_inventory) ctx.action({
        verb: "open", label: "Open shop inventory", raw: { cmd: "action", action: "open_shop_inventory",
          args: { room_ref: state.room_ref, inventory_ref: state.inventory_ref } }
      });
      if (exact && state.can_proceed) ctx.action({
        verb: "activate", label: "Leave shop and open map", raw: { cmd: "action", action: "leave_shop",
          args: { room_ref: state.room_ref } }
      });
      return { kind: "shop_room", stage: "ready", prompt: null,
        surface: { kind: "shop_room", shop_session_entity_id: ctx.entityId("shop_session", state.shop_ref),
          can_open_inventory: state.can_open_inventory, can_proceed: state.can_proceed },
        context: { kind: "shop" }, complete: exact,
        visibleInformation: "contract_complete_for_visible_normal_merchant_room_controls",
        interactionDiscovery: "derived_from_exact_merchant_presentation_owner",
        missing: exact ? [] : ["exact_current_merchant_presentation_owner"] };
    }
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
      const roomIdentityComplete = typeof state.room_ref === "string" && state.room_ref.length > 0
        && state.shop_ref === state.room_ref && typeof state.inventory_ref === "string" && state.inventory_ref.length > 0;
      if (roomIdentityComplete) {
        ctx.action({
          verb: "close",
          label: "Close shop inventory",
          raw: { cmd: "action", action: "close_shop_inventory",
            args: { room_ref: state.room_ref, inventory_ref: state.inventory_ref } }
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
          shop_session_entity_id: ctx.entityId("shop_session", state.shop_ref),
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

function runDeckRead(snapshotId, state, makeId, entityId) {
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
        const cardEntityId = entityId("card", card.native_ref);
        // The Reference deck DTO contains no combat legality or inline hover
        // extensions. Rich hover facts are separate same-snapshot Reads below.
        const { target_type: _targetType, can_play: _canPlay,
          keywords: _keywords, card_previews: _previews, ...visible } = visibleCombatCard(card, cardEntityId);
        return visible;
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

function publicCardDetailRead(ctx, visible, details, missing, displayContext, ownerId = null, parentReadId = null) {
  const gaps = [...missing];
  if (!plainObject(details)) gaps.push("current_native_public_card_details_not_captured");
  else for (const field of ["keywords", "card_previews"])
    if (!Array.isArray(details[field])) gaps.push(`current_native_public_card_${field}_not_captured`);
  return {
    descriptor: {
      read_id: ctx.id("read", ctx.snapshotId, "surface_card", displayContext, ownerId, visible.entity_id),
      kind: "surface_card", target_referent_id: visible.entity_id,
      content_schema: "sts2.player-environment/read/surface_card-1",
      visibility_basis: "normal_player_visible_surface_card", snapshot_bound: true,
      ordering_semantics: "single_entity", hidden_by_policy: []
    },
    content: { ...visible, public_details_revision: "1", display_context: displayContext,
      parent_read_id: parentReadId, display_owner_entity_id: ownerId,
      ...(Array.isArray(details?.keywords) ? { keywords: structuredClone(details.keywords) } : {}),
      ...(Array.isArray(details?.card_previews) ? { card_previews: structuredClone(details.card_previews) } : {}) },
    completeness: { status: gaps.length === 0 ? "complete" : "partial",
      visible_information: "complete_current_player_visible_card_detail", interaction_discovery: "read_only",
      missing: [...new Set(gaps)], hidden_by_policy: [] }
  };
}

function deckCardDetailReads(ctx, state, deckRead) {
  if (deckRead === null) return [];
  return state.player.deck.map((card, index) => {
    const visible = deckRead.content.cards[index];
    if (!ctx.referents.some(referent => referent.referent_id === visible.entity_id))
      ctx.referent({ role: "card", id: visible.entity_id, label: visible.name, properties: visible });
    const details = card.public_details;
    const missing = Array.isArray(card.public_details_missing) ? [...card.public_details_missing] : [];
    if (!plainObject(details)) missing.push("current_native_public_card_details_not_captured");
    else for (const field of ["keywords", "card_previews"])
      if (!Array.isArray(details[field])) missing.push(`current_native_public_card_${field}_not_captured`);
    if (card.public_details_complete !== true && missing.length === 0)
      missing.push("current_native_public_card_detail_incomplete");
    return {
      descriptor: {
        read_id: ctx.id("read", ctx.snapshotId, "surface_card", "deck", visible.entity_id),
        kind: "surface_card",
        target_referent_id: visible.entity_id,
        content_schema: "sts2.player-environment/read/surface_card-1",
        visibility_basis: "normal_player_visible_surface_card",
        snapshot_bound: true,
        ordering_semantics: "single_entity",
        hidden_by_policy: []
      },
      content: {
        ...visible,
        public_details_revision: "1",
        display_context: "deck",
        parent_read_id: deckRead.descriptor.read_id,
        display_owner_entity_id: null,
        ...(Array.isArray(details?.keywords) ? { keywords: structuredClone(details.keywords) } : {}),
        ...(Array.isArray(details?.card_previews) ? { card_previews: structuredClone(details.card_previews) } : {})
      },
      completeness: {
        status: missing.length === 0 ? "complete" : "partial",
        visible_information: "complete_current_player_visible_card_detail",
        interaction_discovery: "read_only",
        missing: [...new Set(missing)],
        hidden_by_policy: []
      }
    };
  });
}

function combatPilesRead(ctx, state) {
  if (!Array.isArray(state.combat_piles)) return null;
  const { snapshotId, id: makeId } = ctx;
  const parentReadId = makeId("read", snapshotId, "combat_piles");
  return {
    descriptor: {
      read_id: parentReadId,
      kind: "combat_piles",
      target_referent_id: null,
      content_schema: "sts2.player-environment/read/combat_piles-1",
      visibility_basis: "player_openable_draw_discard_exhaust_pile_views",
      snapshot_bound: true,
      ordering_semantics: "per_zone",
      hidden_by_policy: ["draw_pile_true_order"]
    },
    content: {
      kind: "combat_piles",
      zones: state.combat_piles.map((zone) => {
        // Project the complete allowed card facts BEFORE ordering/aliasing.
        // A stable tie between equal visible values carries no hidden identity:
        // equal occurrences receive separate public slots, without deduplication.
        const cards = (zone.cards ?? []).map((raw) => ({ raw, visible: visibleCardBase(raw, null),
          sort: visibleCombatCard(raw, null) }));
        if (zone.pile === "draw") {
          cards.sort((left, right) => {
            const a = JSON.stringify(canonicalValue(left.sort));
            const b = JSON.stringify(canonicalValue(right.sort));
            return a < b ? -1 : a > b ? 1 : 0;
          });
        }
        return {
          zone: zone.pile,
          card_count: cards.length,
          ordering_semantics: zone.pile === "draw" ? "unordered_multiset" : "native_visible_order",
          cards: cards.map(({ raw, visible }, index) => {
            const { target_type: _target, can_play: _canPlay, ...base } = visible;
            const card = { ...base, entity_id: makeId("pile_card", snapshotId, zone.pile, index, visible) };
            ctx.referent({ role: "card", id: card.entity_id, label: card.name, properties: card });
            ctx.detailReads.push(publicCardDetailRead(ctx, card, raw.public_details,
              Array.isArray(raw.public_details_missing) ? raw.public_details_missing : [], zone.pile, null, parentReadId));
            return card;
          })
        };
      })
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

function handCardDetailReads(ctx, state) {
  const hand = state.decision === "combat_play" ? state.hand : state.combat_context?.hand;
  const playerRef = state.decision === "combat_play" ? state.player?.native_ref : state.combat_context?.player_ref;
  if (!Array.isArray(hand)) return [];
  const owner = ctx.entityId("player", playerRef);
  return hand.map(raw => {
    const card = visibleCardBase(raw, ctx.entityId("card", raw.native_ref));
    if (!ctx.referents.some(referent => referent.referent_id === card.entity_id))
      ctx.referent({ role: "card", id: card.entity_id, label: card.name, properties: card });
    return publicCardDetailRead(ctx, Object.fromEntries(Object.entries(card).filter(([, value]) => value !== null)),
      raw.public_details, Array.isArray(raw.public_details_missing) ? raw.public_details_missing : [], "hand", owner);
  });
}

export function projectManagedCandidateDecision({
  state,
  runtimeInstanceId,
  environmentFingerprint,
  sequence,
  identityMode = "crypto",
  validateSdk = true,
  publicEntityAliases = new Map()
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
  const ctx = createProjectionContext({ state, runtimeInstanceId, sequence, identityMode, publicEntityAliases });
  const contextMs = performance.now() - stageStarted;
  stageStarted = performance.now();
  const surface = currentSurface(state, ctx);
  const surfaceMs = performance.now() - stageStarted;
  stageStarted = performance.now();
  const persistent = buildPersistentVisibleState(state, ctx.snapshotId, ctx.id, ctx.entityId);
  const persistentMs = performance.now() - stageStarted;
  const terminal = state.decision === "game_over";
  const actionProjectionComplete = (surface.actionComplete ?? surface.complete) === true
    && ctx.actions.length <= ACTION_LIMIT;
  if (!actionProjectionComplete) {
    ctx.actions.length = 0;
    ctx.bindings.clear();
  }
  stageStarted = performance.now();
  const deckRead = runDeckRead(ctx.snapshotId, state, ctx.id, ctx.entityId);
  const reads = [
    deckRead,
    mapRead(ctx.snapshotId, state, ctx.id, ctx.entityId),
    combatPilesRead(ctx, state),
    ...deckCardDetailReads(ctx, state, deckRead),
    ...handCardDetailReads(ctx, state),
    ...ctx.detailReads
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
  #publicEntityAliases = new Map();

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
    this.#publicEntityAliases.clear();
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
    if (state?.decision === "map_select" && !plainObject(state.context?.current_map)) {
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
      publicEntityAliases: this.#publicEntityAliases,
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
