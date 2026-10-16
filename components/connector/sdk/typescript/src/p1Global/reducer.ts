import { decodePlayerRead, decodePlayerSnapshot } from "../protocol.js";
import { hasFinitePublicNumbers } from "../publicCombat.js";
import { completeCombatSurface, completeCombat, completeDeck, completeMap, completePiles, completeSelector } from "./admission.js";
import { canonicalJson, isObject } from "./canonical.js";
import { projectCombatContext } from "./combatProjection.js";
import { createPublicIdentityPolicy, publicFieldPath } from "./publicIdentity.js";
import {
  P1G_INFORMATION_SHAPES, P1G_LIMITS, P1G_OBSERVATION_SCHEMA, P1G_PROFILE, P1G_REVISION,
  type Availability, type HostKind, type P1Choice, type P1Commitments, type P1Observation,
  type P1Part, type SceneFamily
} from "./contracts.js";

/**
 * The single shared P1 semantic core. Pure and deterministic: the live runner and the
 * offline converter call the same functions on the same recorded bytes and must
 * produce byte-identical observations. Hosts contribute native facts and bindings only.
 */

export interface ReadCapture {
  readonly read_id: string;
  readonly kind: string;
  /** Exact decoded read-1 response, or null when the read failed. */
  readonly response: unknown | null;
  readonly error: string | null;
}

export interface P1Basis {
  /** Exact snapshot-1 as delivered by the Host (observe result or receipt successor). */
  readonly snapshot: unknown;
  /** Every Read the snapshot advertises, captured against that snapshot id. */
  readonly reads: readonly ReadCapture[];
}

export interface RewardLedger {
  readonly page: number;
  /** group key -> declined (irreversible P1 commitment for this reward page). */
  readonly declined: readonly string[];
  readonly left: boolean;
  /** Group key the Agent opened and whose card choice is currently displayed. */
  readonly open_group: string | null;
}

export interface P1SessionState {
  readonly episode_id: string;
  readonly host_kind: HostKind;
  readonly occurrence: number;
  readonly last_snapshot_id: string | null;
  /** Digest of the P1 commitments at the last observation: a commitment change is a new occurrence. */
  readonly last_commitments: string | null;
  readonly aliases: Readonly<Record<string, string>>;
  readonly alias_counts: Readonly<Record<string, number>>;
  readonly reward_pages: number;
  readonly reward: RewardLedger | null;
}

export interface NativeStep {
  readonly bound_action_id: string;
  readonly verb: string;
  readonly label: string;
}

/** A declared continuation inside one already chosen P1 intention. */
export interface FollowRule {
  readonly rule_id: "upgrade_confirm" | "enchant_confirm" | "card_reward_select" | "selector_preview_confirm" | "reward_completion_proceed";
  readonly expect_native_kind: string;
  readonly expect_verb: string;
  /** Optional folds stop silently (intention completed) when the successor is not the expected stage. */
  readonly optional: boolean;
}

export type Commitment = "decline_group" | "consume_group" | "decline_open_group" | "leave_reward_page"
  | "open_group" | "consume_open_group";

export interface Lowering {
  /** First native step; null for a pure P1 commitment that performs no native input. */
  readonly first: NativeStep | null;
  readonly follow: readonly FollowRule[];
  /** Applied after the intention's native steps are known delivered (or at once for pure commitments). */
  readonly commitment: Commitment | null;
  readonly group_key: string | null;
  /** For reward.take_card: the previewed option to verify on the opened native selection. */
  readonly option_index: number | null;
  readonly option_definition: string | null;
  /** Private native entity of the previewed option, when the Host keeps it stable across pages. */
  readonly option_entity: string | null;
  /** Frozen full public card state for an exact stable-instance continuation. */
  readonly option_public: string | null;
}

export interface ExcludedNative { readonly bound_action_id: string; readonly verb: string; readonly label: string; readonly rule: string }

export interface Reduction {
  readonly state: P1SessionState;
  readonly observation: P1Observation;
  /** Private: never sent to the Agent. */
  readonly lowering: Readonly<Record<string, Lowering>>;
  /** Declared forced input (exactly one meaningful opportunity, mechanism-qualified). */
  readonly forced_choice_id: string | null;
  /** Private audit: native inputs not offered, with the declared rule. */
  readonly excluded: readonly ExcludedNative[];
  readonly complete: boolean;
  readonly missing: readonly string[];
}

export function createSession(episodeId: string, hostKind: HostKind): P1SessionState {
  return { episode_id: episodeId, host_kind: hostKind, occurrence: 0, last_snapshot_id: null, last_commitments: null,
    aliases: {}, alias_counts: {}, reward_pages: 0, reward: null };
}

const FAMILY: Readonly<Record<string, SceneFamily>> = {
  combat_turn: "combat",
  combat_hand_card_selection: "selector", card_selection: "selector", card_bundle_selection: "selector",
  native_combat_pile_selection: "selector", native_deck_card_selection: "selector", native_generated_card_choice: "selector",
  native_simple_card_selection: "selector", deck_transform_selection: "selector", deck_card_selection: "selector",
  deck_upgrade_selection: "upgrade", deck_enchant_selection: "enchant",
  card_reward_selection: "card_reward", reward_claim: "reward", reward_completion: "reward",
  map_navigation: "map", event_option: "event", event_dialogue: "event",
  shop_inventory: "shop", shop_room: "shop", rest_site: "rest",
  treasure_room: "treasure", treasure_chest: "treasure", treasure_relic_selection: "treasure", treasure_completion: "treasure", relic_choice: "relic_choice", native_boss_relic_selection: "relic_choice",
  tutorial: "other", game_over: "terminal"
};

/** Verb-level semantics per family, used when no exact (kind, verb, role) row exists. */
const FAMILY_VERB: Readonly<Record<string, Readonly<Record<string, string>>>> = {
  selector: { select: "selector.choose", skip: "selector.skip", confirm: "selector.confirm", cancel: "selector.cancel",
    deselect: "selector.deselect", close: "selector.cancel", activate: "selector.choose" },
  upgrade: { select: "upgrade.choose", confirm: "upgrade.confirm", cancel: "upgrade.back", close: "upgrade.back",
    deselect: "upgrade.deselect" },
  enchant: { select: "enchant.choose", confirm: "enchant.confirm", cancel: "enchant.back", close: "enchant.back",
    deselect: "enchant.deselect" },
  card_reward: { select: "card_reward.take", skip: "card_reward.decline", activate: "card_reward.alternative" },
  event: { activate: "event.choose", select: "event.choose", confirm: "event.continue" },
  shop: { activate: "shop.activate", close: "shop.leave", open: "shop.open" },
  rest: { activate: "rest.choose" },
  treasure: { activate: "treasure.activate", open: "treasure.open", skip: "treasure.skip", select: "treasure.take" },
  relic_choice: { select: "relic.choose", activate: "relic.choose", skip: "relic.skip" },
  map: { activate: "map.travel" },
  other: { activate: "other.continue", confirm: "other.continue", skip: "other.skip" }
};

/** (native kind, verb, subject role) -> P1 kind. Anything else stays offered as native.* (mapped=false). */
const KIND_TABLE: Readonly<Record<string, string>> = {
  "combat_turn|play|playable_card": "combat.play_card",
  "combat_turn|use|usable_potion": "potion.use",
  "combat_turn|activate|usable_potion": "potion.discard",
  "combat_turn|activate|discardable_potion": "potion.discard",
  "combat_turn|end_turn|": "combat.end_turn",
  "map_navigation|activate|option": "map.travel",
  "treasure_chest|activate|treasure_room": "treasure.open",
  "treasure_relic_selection|select|relic": "treasure.take",
  "treasure_relic_selection|skip|treasure_room": "treasure.skip",
  "treasure_completion|close|treasure_room": "treasure.proceed",
  "reward_claim|activate|reward": "reward.claim",
  "reward_claim|activate|": "reward.leave",
  "reward_completion|activate|combat_room": "reward.proceed",
  "card_reward_selection|select|card": "card_reward.take",
  "card_reward_selection|skip|card_reward_alternative": "card_reward.decline",
  "card_selection|select|card": "selector.choose",
  "card_selection|skip|": "selector.skip",
  "card_selection|confirm|": "selector.confirm",
  "card_selection|deselect|card": "selector.deselect",
  "card_selection|cancel|": "selector.cancel",
  "combat_hand_card_selection|select|card": "selector.choose",
  "combat_hand_card_selection|deselect|card": "selector.deselect",
  "combat_hand_card_selection|confirm|": "selector.confirm",
  "combat_hand_card_selection|cancel|": "selector.cancel",
  "deck_upgrade_selection|select|card": "upgrade.choose",
  "deck_upgrade_selection|open|card": "upgrade.preview",
  "deck_card_selection|open|card": "selector.preview",
  "deck_enchant_selection|open|card": "enchant.preview",
  "deck_enchant_selection|select|card": "enchant.choose",
  "deck_enchant_selection|confirm|": "enchant.confirm",
  "deck_enchant_selection|cancel|": "enchant.back",
  "deck_upgrade_selection|confirm|": "upgrade.confirm",
  "deck_upgrade_selection|cancel|": "upgrade.back",
  "event_option|activate|option": "event.choose",
  "rest_site|activate|rest_option": "rest.choose",
  "rest_site|activate|rest_room": "rest.proceed",
  "shop_inventory|activate|shop_card": "shop.buy",
  "shop_inventory|activate|shop_potion": "shop.buy",
  "shop_inventory|activate|shop_relic": "shop.buy",
  "shop_inventory|activate|shop_service": "shop.service",
  "shop_inventory|close|": "shop.leave"
};

// r1.1 preserves native cancellation, deselection and browsing until a
// mechanism-specific full-information/intention proof qualifies their removal.

/** Continue-only inputs: the native room/page referent they name carries no decision content. */
const SUBJECTLESS_KINDS: ReadonlySet<string> = new Set(["reward.proceed", "reward.leave", "rest.proceed"]);

// r1.1 has no qualified mandatory-input producer proof. Singleton choices remain Agent choices.

interface AliasWork {
  aliases: Record<string, string>; counts: Record<string, number>;
  policy: ReturnType<typeof createPublicIdentityPolicy>; anonymous: Set<string>;
  anonymousViews: { root: string; label: string; aliases: Map<string, string> }[];
}

function aliasFor(work: AliasWork, id: string, path = ""): string {
  if (work.anonymous.has(id)) {
    const view = work.anonymousViews.find(view => path === view.root || path.startsWith(view.root + ".") || path.startsWith(view.root + "["));
    if (view) {
      const existing = view.aliases.get(id);
      if (existing) return existing;
      const alias = `a_${view.label}_${view.aliases.size + 1}`;
      view.aliases.set(id, alias);
      return alias;
    }
  }
  const existing = work.aliases[id];
  if (existing) return existing;
  // Source token spelling/counters never select a model-visible alias.
  const prefix = "e";
  const next = (work.counts[prefix] ?? 0) + 1;
  work.counts[prefix] = next;
  const alias = `${prefix}${next}`;
  work.aliases[id] = alias;
  return alias;
}

function collectIds(value: unknown, ids: Set<string>, policy: AliasWork["policy"], path = "", declarationsOnly = false): void {
  if (typeof value === "string") {
    const relation = policy.relation(path);
    if (relation && (!declarationsOnly || relation === "declared")) ids.add(value);
    return;
  }
  if (Array.isArray(value)) { value.forEach((item, index) => collectIds(item, ids, policy, `${path}[${index}]`, declarationsOnly)); return; }
  if (isObject(value)) for (const key of Object.keys(value).sort())
    collectIds(value[key], ids, policy, publicFieldPath(path, key), declarationsOnly);
}

/** Alias only paths owned by the public schema; all other facts remain literal. */
function aliasTree(value: unknown, work: AliasWork, path: string): unknown {
  if (typeof value === "string") return work.policy.relation(path) ? aliasFor(work, value, path) : value;
  if (Array.isArray(value)) return value.map((item, index) => aliasTree(item, work, `${path}[${index}]`));
  if (isObject(value)) {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort()) out[key] = aliasTree(value[key], work, publicFieldPath(path, key));
    return out;
  }
  return value;
}

function stripIds(value: unknown, policy: AliasWork["policy"], path: string): unknown {
  if (typeof value === "string") return policy.relation(path) ? "" : value;
  if (Array.isArray(value)) return value.map((item, index) => stripIds(item, policy, `${path}[${index}]`));
  if (isObject(value)) {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort()) out[key] = stripIds(value[key], policy, publicFieldPath(path, key));
    return out;
  }
  return value;
}

/**
 * Unordered multisets are presented canonically so no hidden order survives: by public
 * content, then by an already assigned public alias (never by the Host's list position).
 */
function canonicalMultiset(cards: readonly unknown[], work: AliasWork, path: string): unknown[] {
  const key = (card: unknown) => {
    const entity = isObject(card) && typeof card.entity_id === "string" ? card.entity_id : "";
    const alias = work.anonymous.has(entity) ? "" : work.aliases[entity] ?? "";
    return `${canonicalJson(stripIds(card, work.policy, `${path}[0]`))}\u0000${alias.padStart(12, "0")}`;
  };
  return [...cards].sort((a, b) => { const left = key(a); const right = key(b); return left < right ? -1 : left > right ? 1 : 0; });
}

function part<T>(availability: Availability, content: T | null, source: string | null): P1Part<T> {
  return { availability, content, source };
}

interface NativeAction { bound_action_id: string; verb: string; subject_referent_id?: string | null;
  arguments: { role: string; referent_id: string }[]; label: string }
interface NativeReferent { referent_id: string; role: string }

export function reduce(state: P1SessionState, basis: P1Basis): Reduction {
  const snapshot = decodePlayerSnapshot(basis.snapshot).raw as Record<string, any>;
  const interaction = snapshot.interaction as Record<string, any>;
  const nativeKind = String(interaction.kind);
  const family: SceneFamily = FAMILY[nativeKind] ?? "other";
  const missing: string[] = [];
  if (!hasFinitePublicNumbers(basis)) missing.push("required:finite_public_numbers");
  if (!P1G_INFORMATION_SHAPES.includes(family)) missing.push(`required:scene_information_qualification:${family}`);
  if (!(nativeKind in FAMILY)) missing.push(`required:scene_contract:${nativeKind}`);
  if (interaction.content_schema !== `sts2.player-environment/surface/${nativeKind}-1`
      || interaction.content?.surface?.kind !== nativeKind) missing.push("required:scene:unsupported_schema_or_kind");
  if (snapshot.status === "interactive" && (snapshot.completeness?.status !== "complete"
      || snapshot.bound_actions?.status !== "complete")) missing.push("required:native_catalog_and_snapshot_complete");

  // Occurrence: the same frozen snapshot re-delivered is not a new decision point.
  const commitmentDigest = canonicalJson(state.reward ?? null);
  const sameSnapshot = state.last_snapshot_id === snapshot.snapshot_id && state.last_commitments === commitmentDigest;
  const occurrence = sameSnapshot ? state.occurrence : state.occurrence + 1;

  // Reward page lifecycle: a new page begins when entering the reward family.
  let rewardPages = state.reward_pages;
  let reward: RewardLedger | null = state.reward;
  const inRewardFlow = family === "reward" || (family === "card_reward" && state.reward !== null && state.reward.open_group !== null);
  if (inRewardFlow) {
    if (reward === null || reward.left) { rewardPages += 1; reward = { page: rewardPages, declined: [], left: false, open_group: null }; }
  } else if (family !== "card_reward") {
    reward = null;
  }

  // Preserve every advertised instance. An aggregate has no implicit last-wins rule.
  if (basis.reads.some(read => !snapshot.reads.some((listed: Record<string, any>) => listed.read_id === read.read_id)))
    missing.push("read:unadvertised_capture");
  const readInstances: { listed: Record<string, any>; read: Record<string, any> | null }[] = [];
  for (const listed of snapshot.reads as Record<string, any>[]) {
    const captures = basis.reads.filter((item) => item.read_id === listed.read_id);
    let read: Record<string, any> | null = null;
    if (captures.length !== 1 || captures[0]!.response == null || captures[0]!.error !== null || captures[0]!.kind !== listed.kind) missing.push(`read:${listed.kind}:${listed.read_id}:missing_or_ambiguous`);
    else {
      const decoded = decodePlayerRead(captures[0]!.response).raw as Record<string, any>;
      if (decoded.read_id !== listed.read_id || decoded.expected_snapshot_id !== snapshot.snapshot_id
          || decoded.observed_snapshot_id !== snapshot.snapshot_id || decoded.kind !== listed.kind
          || decoded.content_schema !== listed.content_schema || decoded.completeness?.status !== "complete"
          || (decoded.target_referent_id ?? null) !== (listed.target_referent_id ?? null)
          || canonicalJson(decoded.session) !== canonicalJson(snapshot.session)
          || canonicalJson(decoded.information_policy) !== canonicalJson(snapshot.information_policy))
        missing.push(`read:${listed.kind}:${listed.read_id}:not_complete_for_snapshot`);
      else read = decoded;
    }
    readInstances.push({ listed, read });
  }
  const aggregate = (kind: string): Record<string, any> | null | undefined => {
    const instances = readInstances.filter((item) => item.listed.kind === kind);
    if (instances.length > 1) { missing.push(`read:${kind}:unsupported_multiplicity`); return null; }
    const read = instances.length === 0 ? undefined : instances[0]!.read;
    if (read && read.content_schema !== `sts2.player-environment/read/${kind}-1`) {
      missing.push(`read:${kind}:unsupported_schema`); return null;
    }
    return read;
  };
  const persistent = snapshot.persistent?.content as Record<string, any> | undefined;
  const context: Record<string, any> = isObject(interaction.content.context) ? interaction.content.context : {};
  const surface: Record<string, any> = isObject(interaction.content.surface) ? interaction.content.surface : {};
  const nestedCombat = context.kind !== "combat" && isObject(context.combat) && context.combat.kind === "combat"
    ? context.combat as Record<string, any> : null;
  const combatSource = family === "combat" || context.kind === "combat" ? context : nestedCombat;
  const publicCombat = combatSource ? projectCombatContext(combatSource) : null;
  if (publicCombat) missing.push(...publicCombat.missing);
  const sceneContext = context.kind === "combat" || context.kind === "map" ? null
    : Object.fromEntries(Object.entries(context).filter(([key]) => !["run", "player"].includes(key) && !(nestedCombat && key === "combat")));
  const deckRead = aggregate("run_deck"); const pilesRead = aggregate("combat_piles"); const mapRead = aggregate("map");
  const extraReadSources = readInstances.filter(({ listed }) => !["run_deck", "combat_piles", "map"].includes(listed.kind))
    .map(({ listed, read }) => ({ kind: String(listed.kind), availability: (read ? "available" : "unknown") as Availability,
      content: read?.content ?? null }));
  const policy = createPublicIdentityPolicy(nativeKind, extraReadSources);
  const rawFacts = {
    player: { content: persistent?.player }, run: { content: persistent?.run },
    scene: { content: { surface, context: sceneContext } }, combat: { content: publicCombat?.content },
    map: { content: context.kind === "map" ? context : mapRead?.content },
    extra_reads: extraReadSources,
    // An ordered native-visible discard/exhaust Read is a public witness too.
    piles: { content: { zones: Array.isArray(pilesRead?.content?.zones) ? pilesRead.content.zones.map((zone: unknown) =>
      isObject(zone) && zone.zone === "draw" ? { ...zone, cards: [] } : zone) : [] } }
  };
  // Allocate legitimate ordered public identities before any unordered view can
  // choose their aliases. Reference-only lists and unknown extensions are not witnesses.
  const distinguished = new Set<string>();
  const { extra_reads: detailWitnesses, ...orderedWitnesses } = rawFacts;
  collectIds(orderedWitnesses, distinguished, policy, "", true);
  // Detail inventory order must not preempt the card's ordered owner presentation.
  collectIds(detailWitnesses, distinguished, policy, "extra_reads", true);
  const anonymous = new Set<string>();
  const unorderedViews = [
    { root: "deck.content", path: "deck.content.cards", label: "deck", cards: deckRead?.content?.cards },
    ...(Array.isArray(pilesRead?.content?.zones) ? pilesRead.content.zones.flatMap((zone: unknown, index: number) =>
      isObject(zone) && zone.zone === "draw" ? [{ root: `piles.content.zones[${index}]`,
        path: `piles.content.zones[${index}].cards`, label: "draw", cards: zone.cards }] : []) : [])
  ];
  for (const view of unorderedViews) if (Array.isArray(view.cards)) {
      const cards: unknown[] = view.cards;
      const equal = new Map<string, string[]>();
      for (const card of cards) if (isObject(card) && typeof card.entity_id === "string") {
        const key = canonicalJson(stripIds(card, policy, `${view.path}[0]`));
        const members = equal.get(key) ?? []; members.push(card.entity_id); equal.set(key, members);
      }
      for (const members of equal.values()) if (members.length > 1)
        for (const id of members) if (!distinguished.has(id)) anonymous.add(id);
  }
  // Child preview identities of an anonymous occurrence are view-local as well.
  for (const view of unorderedViews) if (Array.isArray(view.cards))
    for (const card of view.cards) if (isObject(card) && anonymous.has(String(card.entity_id))) {
      const children = new Set<string>(); collectIds(card, children, policy, `${view.path}[0]`);
      for (const id of children) if (!distinguished.has(id)) anonymous.add(id);
    }
  const work: AliasWork = { aliases: Object.assign(Object.create(null) as Record<string, string>, state.aliases),
    counts: { ...state.alias_counts, a: 0 }, policy, anonymous,
    anonymousViews: unorderedViews.map(view => ({ root: view.root, label: view.label, aliases: new Map() })) };
  for (const id of anonymous) delete work.aliases[id];
  for (const id of distinguished) aliasFor(work, id);

  const terminalScene = family === "terminal";
  const playerPart = persistent?.player
    ? part("available", aliasTree(persistent.player, work, "player.content"), "persistent.player")
    : part(terminalScene ? "terminal" : "unknown", null, null);
  if (!persistent?.player && !terminalScene) missing.push("persistent.player");
  const runPart = persistent?.run
    ? part("available", aliasTree(persistent.run, work, "run.content"), "persistent.run")
    : part(terminalScene ? "terminal" : "unknown", null, null);
  if (!persistent?.run && !terminalScene) missing.push("persistent.run");

  let deckPart: P1Part<unknown>;
  if (deckRead === undefined) deckPart = part(terminalScene ? "terminal" : "unsupported", null, null);
  else if (deckRead === null) deckPart = part("unknown", null, "read:run_deck");
  else {
    const content = deckRead.content as Record<string, any>;
    if (!completeDeck(content)) missing.push("required:deck:invalid_content");
    const cards = Array.isArray(content?.cards) ? canonicalMultiset(content.cards, work, "deck.content.cards") : [];
    deckPart = part("available", aliasTree({ ...content, cards, ordering: "unordered_multiset" }, work, "deck.content"), "read:run_deck");
  }

  let pilesPart: P1Part<unknown>;
  if (family !== "combat" && pilesRead === undefined) pilesPart = part("not_applicable", null, null);
  else if (pilesRead === undefined) pilesPart = part("unsupported", null, null);
  else if (pilesRead === null) pilesPart = part("unknown", null, "read:combat_piles");
  else {
    const content = pilesRead.content as Record<string, any>;
    if (!completePiles(content)) missing.push("required:piles:invalid_content");
    const zones = (Array.isArray(content?.zones) ? content.zones : []).map((zone: unknown, index: number) => {
      if (!isObject(zone)) return zone;
      const cards = Array.isArray(zone.cards) ? zone.cards : [];
      const draw = zone.zone === "draw";
      return { ...zone, cards: draw ? canonicalMultiset(cards, work, `piles.content.zones[${index}].cards`) : cards,
        ordering: draw ? "unordered_multiset" : zone.ordering_semantics === "native_visible_order" ? "native_visible_order" : "unsupported" };
    });
    pilesPart = part("available", aliasTree({ ...content, zones }, work, "piles.content"), "read:combat_piles");
  }

  if (!isObject(interaction.content.context)) missing.push("required:context:invalid_content");
  const combatPart = family === "combat" || context.kind === "combat"
    ? part("available", aliasTree(publicCombat!.content, work, "combat.content"), "interaction.context")
    : nestedCombat ? part("available", aliasTree(publicCombat!.content, work, "combat.content"), "interaction.context.combat")
    : part(terminalScene ? "terminal" : "not_applicable", null, null);
  const mapPart = context.kind === "map"
    ? part("available", aliasTree(context, work, "map.content"), "interaction.context")
    : mapRead?.content?.kind === "map"
      ? part("available", aliasTree(mapRead.content, work, "map.content"), "read:map")
      : part(terminalScene ? "terminal" : mapRead === null ? "unknown" : "unsupported", null, null);
  const combatRequired = family === "combat" || context.kind === "combat" || nestedCombat !== null
    || ["combat_hand_card_selection", "native_combat_pile_selection", "native_generated_card_choice"].includes(nativeKind);
  if (combatRequired && pilesRead === undefined) pilesPart = part("unsupported", null, null);
  // Absence from the legacy advertised Reads is not evidence of native unavailability.
  if (!terminalScene) {
    if (mapPart.availability === "available" && !completeMap(mapPart.content, persistent?.run?.act_index ?? (Number(persistent?.run?.act) - 1)))
      missing.push("required:map:invalid_content");
    if (combatPart.availability === "available") {
      const combat = combatPart.content as Record<string, any>;
      if (!completeCombat(combat) || combat.player?.player_entity_id !== (playerPart.content as Record<string, any>)?.entity_id)
        missing.push("required:combat:invalid_content");
      if (pilesRead && Array.isArray(pilesRead.content?.zones) && pilesRead.content.zones.some((zone: unknown) => !isObject(zone)
          || combat.player?.[`${zone.zone}_pile_count`] !== zone.card_count))
        missing.push("required:piles:combat_count_mismatch");
    }
    if (deckPart.availability !== "available") missing.push("required:deck");
    if (mapPart.availability !== "available") missing.push("required:map");
    if (combatRequired && combatPart.availability !== "available") missing.push("required:combat");
    if (combatRequired && pilesPart.availability !== "available") missing.push("required:piles");
  }

  if (!isObject(interaction.content.surface)) missing.push("required:surface:invalid_content");
  if (nativeKind === "combat_turn" && !completeCombatSurface(surface)) missing.push("required:combat_surface:invalid_content");
  if (["selector", "upgrade", "enchant"].includes(family) && !completeSelector(surface)) missing.push("required:selector:invalid_content");
  const sceneContent: Record<string, unknown> = { surface: aliasTree(surface, work, "scene.content.surface") };
  if (sceneContext && Object.keys(sceneContext).length > 1) sceneContent.context = aliasTree(sceneContext, work, "scene.content.context");

  const extraReads = extraReadSources.map((read, index) => ({ ...read,
    content: read.content === null ? null : aliasTree(read.content, work, `extra_reads[${index}].content`) }));

  // Reward groups: identity must come from the Host (stable group_id); never from labels or positions.
  const rewardItems: Record<string, any>[] = nativeKind === "reward_claim" && Array.isArray(surface.rewards) ? surface.rewards.filter(isObject) : [];
  const groupByReferent = new Map<string, Record<string, any>>();
  for (const item of rewardItems) if (typeof item.entity_id === "string") groupByReferent.set(item.entity_id, item);
  if (rewardItems.some(item => item.kind === "card")) missing.push("required:reward_card_group_qualification");
  const groupIdentityAvailable = rewardItems.length > 0 && rewardItems.every((item) => typeof item.group_id === "string" && item.group_id.length > 0);
  if (rewardItems.length > 0 && !groupIdentityAvailable) missing.push("reward_group_identity:unsupported");

  // Catalog: every native bound action is accounted for; only declared P1 commitments remove one.
  const roles = new Map((snapshot.referents as NativeReferent[]).map((item) => [item.referent_id, item.role]));
  const lowering: Record<string, Lowering> = {};
  const candidates: { choice: Omit<P1Choice, "choice_id">; lowering: Lowering; sort: string }[] = [];
  const declined = new Set(reward?.declined ?? []);
  const excluded: ExcludedNative[] = [];
  const push = (choice: Omit<P1Choice, "choice_id">, low: Lowering) => candidates.push({ choice, lowering: low,
    sort: `${choice.kind}\u0000${choice.subject ?? ""}\u0000${choice.target ?? ""}\u0000${low.group_key ?? ""}\u0000${low.option_index ?? ""}` });
  for (const action of snapshot.bound_actions.actions as NativeAction[]) {
    const subjectId = action.subject_referent_id ?? null;
    const role = subjectId ? roles.get(subjectId) ?? "" : "";
    let kind = KIND_TABLE[`${nativeKind}|${action.verb}|${role}`] ?? FAMILY_VERB[family]?.[action.verb];
    const mapped = kind !== undefined;
    kind ??= `native.${nativeKind}.${action.verb}${role ? `.${role}` : ""}`;
    const first = { bound_action_id: action.bound_action_id, verb: action.verb, label: action.label };
    // The native result is final: inputs on the game-over page only navigate to the run summary.
    if (terminalScene) { excluded.push({ ...first, rule: "terminal:post_result_navigation" }); continue; }
    if (!mapped) missing.push(`required:action_mapping:${nativeKind}.${action.verb}`);
    const target = action.arguments.find((item) => item.role === "target")?.referent_id ?? null;
    const subjectAlias = subjectId ? aliasFor(work, subjectId) : null;
    const targetAlias = target ? aliasFor(work, target) : null;
    const base = { follow: [] as FollowRule[], commitment: null as Lowering["commitment"], group_key: null as string | null, option_index: null as number | null, option_definition: null as string | null, option_entity: null as string | null, option_public: null as string | null };
    if (kind === "reward.claim" && subjectId) {
      const group = groupByReferent.get(subjectId);
      const groupKey = groupIdentityAvailable ? String(group?.group_id) : null;
      if (groupKey && declined.has(groupKey)) continue; // irreversible P1 decline: never re-offered
      if (group?.kind === "card") {
        // card_options alone does not certify Skip/Reroll/hooks, group relationships,
        // native disposal or side-effect-free preview qualification. Preserve the native
        // open action until the producer exposes a complete typed aggregation contract.
        kind = "reward.open_card_group";
        push({ kind, subject: subjectAlias, target: null, label: action.label, mapped: true }, { ...base, first, group_key: groupKey, commitment: "open_group" });
        continue;
      }
      push({ kind, subject: groupKey ? aliasFor(work, groupKey) : subjectAlias, target: null, label: action.label, mapped },
        { ...base, first, group_key: groupKey, commitment: groupKey ? "consume_group" : null });
      continue;
    }
    // A reward page with nothing left to claim only continues: the same mechanical step one Host
    // shows as a separate completion page ("reward.proceed"), so both read as one forced proceed.
    const leavesEmptyPage = kind === "reward.leave" && nativeKind === "reward_claim" && rewardItems.length === 0;
    if (leavesEmptyPage) kind = "reward.proceed";
    let commitment: Lowering["commitment"] = null; let groupKey: string | null = null;
    if (kind === "reward.leave" || leavesEmptyPage) commitment = "leave_reward_page";
    // Native card-page Skip is a return, not an irrevocable group refusal.
    if (kind === "card_reward.decline" && reward?.open_group) kind = "card_reward.return";
    if (kind === "card_reward.take" && reward?.open_group) { commitment = "consume_open_group"; groupKey = reward.open_group; }
    const follow: FollowRule[] = [];
    // No current producer certifies unchanged complete preview facts or the exact
    // owner-exit continuation. Keep preview/confirm/proceed explicit until qualified.
    push({ kind, subject: SUBJECTLESS_KINDS.has(kind) ? null : subjectAlias, target: targetAlias, label: action.label, mapped },
      { ...base, first, follow, commitment, group_key: groupKey });
  }
  candidates.sort((a, b) => (a.sort < b.sort ? -1 : a.sort > b.sort ? 1 : 0));
  if (candidates.length > P1G_LIMITS.catalog_choices) missing.push("catalog_over_limit");
  const catalog: P1Choice[] = candidates.map((item, index) => {
    const choice_id = `c${index + 1}`;
    lowering[choice_id] = item.lowering;
    return { choice_id, ...item.choice };
  });

  // Hosts state the native result differently (Managed surface.victory, Reference context.result).
  const victory = surface?.victory ?? (context.result === "win" ? true : context.result === "loss" ? false : undefined);
  const terminal = terminalScene ? { outcome: (victory === true ? "win" : victory === false ? "loss" : "unknown") as "win" | "loss" | "unknown" } : null;
  const decision = snapshot.status === "interactive" && catalog.length > 0;
  const status: P1Observation["status"] = terminalScene ? "terminal" : decision ? "decision" : "unsupported";
  if (status === "unsupported") missing.push(`scene:${nativeKind}:${snapshot.status}`);

  const commitments: P1Commitments = {
    // Commitments name groups by public alias only (the Host group id never reaches the model).
    declined_reward_groups: [...(reward?.declined ?? [])].map((key) => aliasFor(work, key)).sort(),
    reward_page_left: reward?.left ?? false
  };

  const observation: P1Observation = {
    schema: P1G_OBSERVATION_SCHEMA, profile: P1G_PROFILE, revision: P1G_REVISION,
    envelope: { episode_id: state.episode_id, occurrence, host_kind: state.host_kind,
      source_snapshot_id: String(snapshot.snapshot_id), source_sequence: Number(snapshot.sequence), required_information_complete: false,
      read_instances: readInstances.map(({ listed }) => ({ read_id: String(listed.read_id), kind: String(listed.kind),
        target_referent_id: listed.target_referent_id ?? null })) },
    status,
    scene: { family, native_kind: nativeKind, content_schema: String(interaction.content_schema), stage: String(interaction.stage),
      prompt: typeof interaction.prompt === "string" ? interaction.prompt : null, content: sceneContent },
    run: runPart, player: playerPart, deck: deckPart, map: mapPart, combat: combatPart, piles: pilesPart,
    extra_reads: extraReads, commitments,
    hidden_by_policy: [...new Set<string>([...(snapshot.completeness?.hidden_by_policy ?? []),
      ...readInstances.flatMap(({ read }) => read?.completeness?.hidden_by_policy ?? [])])].sort(),
    catalog, terminal
  };
  if (Buffer.byteLength(canonicalJson(observation), "utf8") > P1G_LIMITS.observation_bytes) missing.push("observation_over_limit");

  // No current producer has a typed, mechanism-qualified mandatory proof.
  const forced: string | null = null;

  const nextState: P1SessionState = {
    ...state, occurrence, last_snapshot_id: String(snapshot.snapshot_id), last_commitments: canonicalJson(reward ?? null),
    aliases: Object.fromEntries(Object.entries(work.aliases).filter(([id]) => !anonymous.has(id))),
    alias_counts: { ...work.counts, a: 0 }, reward_pages: rewardPages, reward
  };
  const complete = missing.length === 0 || (status === "terminal" && missing.every((item) => item.startsWith("read:")));
  return { state: nextState, observation: { ...observation, envelope: { ...observation.envelope, required_information_complete: complete } },
    lowering, forced_choice_id: forced, excluded, complete, missing };
}

/** Commitment bookkeeping after an intention's native steps finished (status known). */
export function applyCommitment(state: P1SessionState, lowering: Lowering, delivered: boolean): P1SessionState {
  if (!delivered || !lowering.commitment || !state.reward) return state;
  const reward = state.reward;
  const add = (key: string | null) => key ? [...new Set([...reward.declined, key])] : reward.declined;
  switch (lowering.commitment) {
    case "decline_group": return { ...state, reward: { ...reward, declined: add(lowering.group_key) } };
    case "consume_group": return { ...state, reward: { ...reward, open_group: null } };
    case "open_group": return { ...state, reward: { ...reward, open_group: lowering.group_key } };
    case "consume_open_group": return { ...state, reward: { ...reward, open_group: null } };
    case "decline_open_group": return { ...state, reward: { ...reward, open_group: null, declined: add(lowering.group_key) } };
    case "leave_reward_page": return { ...state, reward: { ...reward, left: true, open_group: null } };
    default: return state;
  }
}

/** Mechanical selection metadata is not card state; all other public fields must match. */
function publicCardState(card: Record<string, any>): string {
  const { index: _index, is_selected: _selected, ...publicState } = card;
  return canonicalJson(publicState);
}

/** Follow-up native step for a declared fold, taken only from the successor's own catalog. */
export function resolveFollow(rule: FollowRule, lowering: Lowering, successor: unknown): NativeStep | null {
  const snapshot = decodePlayerSnapshot(successor).raw as Record<string, any>;
  if (snapshot.status !== "interactive" || snapshot.interaction.kind !== rule.expect_native_kind
      || snapshot.bound_actions.status !== "complete" || snapshot.completeness.status !== "complete"
      || snapshot.interaction.content_schema !== `sts2.player-environment/surface/${rule.expect_native_kind}-1`
      || snapshot.interaction.content.surface?.kind !== rule.expect_native_kind) return null;
  const actions = snapshot.bound_actions.actions as NativeAction[];
  if (rule.rule_id === "card_reward_select") {
    // Verify the opened native choice is exactly the previewed option before selecting it.
    const cards = snapshot.interaction.content.surface?.cards as Record<string, any>[] | undefined;
    const byEntity = Array.isArray(cards) && lowering.option_entity
      ? cards.filter((card) => card.entity_id === lowering.option_entity) : [];
    const option = byEntity.length === 1 ? byEntity[0] : undefined;
    if (!option || lowering.option_public == null || publicCardState(option) !== lowering.option_public) return null;
    const matches = actions.filter((item) => item.verb === "select" && item.subject_referent_id === option.entity_id);
    if (matches.length !== 1) return null;
    const only = matches[0]!;
    return { bound_action_id: only.bound_action_id, verb: only.verb, label: only.label };
  }
  // A matching kind plus a unique confirm/continue is not an intention/owner proof.
  // r1.1 emits no such automatic plans while those producer contracts are absent.
  return null;
}

/** Model-visible view: the envelope is binding metadata only. */
export function modelView(observation: P1Observation): Omit<P1Observation, "envelope"> {
  const { envelope: _envelope, ...rest } = observation;
  return rest;
}
