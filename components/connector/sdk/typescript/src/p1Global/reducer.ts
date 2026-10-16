import { decodePlayerRead, decodePlayerSnapshot } from "../protocol.js";
import { canonicalJson, isObject } from "./canonical.js";
import {
  P1G_LIMITS, P1G_OBSERVATION_SCHEMA, P1G_PROFILE, P1G_REVISION,
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
  readonly rule_id: "upgrade_confirm" | "card_reward_select";
  readonly expect_native_kind: string;
  readonly expect_verb: string;
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
  combat_turn: "combat", combat_hand_card_selection: "selector", card_selection: "selector",
  card_bundle_selection: "selector", deck_enchant_selection: "selector", deck_remove_selection: "selector",
  deck_transform_selection: "selector", deck_upgrade_selection: "upgrade",
  card_reward_selection: "card_reward", reward_claim: "reward", reward_completion: "reward",
  map_navigation: "map", event_option: "event", event_dialogue: "event",
  shop_inventory: "shop", shop_room: "shop", rest_site: "rest",
  treasure_room: "treasure", relic_choice: "relic_choice", game_over: "terminal"
};

/** (native kind, verb, subject role) -> P1 kind. Anything else stays offered as native.* (mapped=false). */
const KIND_TABLE: Readonly<Record<string, string>> = {
  "combat_turn|play|playable_card": "combat.play_card",
  "combat_turn|use|usable_potion": "potion.use",
  "combat_turn|activate|usable_potion": "potion.discard",
  "combat_turn|end_turn|": "combat.end_turn",
  "map_navigation|activate|option": "map.travel",
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

/**
 * Forward profile: native inputs that only browse back, cancel or undo a selection are not
 * offered as P1 choices (each P1 choice must move the game forward). They are recorded as
 * excluded with the rule that removed them; nothing else is ever dropped.
 */
const EXCLUDED_KINDS: Readonly<Record<string, string>> = {
  "upgrade.back": "forward_profile:no_cancel_after_choosing_smith",
  "selector.cancel": "forward_profile:no_cancel_of_an_entered_selection",
  "selector.deselect": "forward_profile:selection_is_append_only"
};

/** Declared forced inputs: the only P1 opportunity on these scenes, with no cancel/timing/resource choice. */
const FORCED_KINDS: ReadonlySet<string> = new Set(["reward.proceed", "rest.proceed", "map.travel", "event.choose"]);

const ALIAS_PREFIX: Readonly<Record<string, string>> = {
  card: "c", hover: "h", enemy: "m", relic: "r", potion: "p", player: "pl", ref: "u", companion: "a", orb: "o"
};

interface AliasWork { aliases: Record<string, string>; counts: Record<string, number>; ids: Set<string> }

function aliasFor(work: AliasWork, id: string): string {
  const existing = work.aliases[id];
  if (existing) return existing;
  const head = id.includes("_") ? id.slice(0, id.indexOf("_")) : "x";
  const prefix = ALIAS_PREFIX[head] ?? "x";
  const next = (work.counts[prefix] ?? 0) + 1;
  work.counts[prefix] = next;
  const alias = `${prefix}${next}`;
  work.aliases[id] = alias;
  return alias;
}

const ID_KEY = /(^|_)(entity_id|entity_ids|combat_id|referent_id|player_entity_id|owner_entity_id|orb_entity_id)$/u;

function collectIds(value: unknown, ids: Set<string>, key = ""): void {
  if (typeof value === "string") { if (ID_KEY.test(key)) ids.add(value); return; }
  if (Array.isArray(value)) { for (const item of value) collectIds(item, ids, key); return; }
  if (isObject(value)) for (const [k, v] of Object.entries(value)) collectIds(v, ids, k);
}

/** Replace every native id by its public alias, visiting keys in sorted order (deterministic). */
function aliasTree(value: unknown, work: AliasWork): unknown {
  if (typeof value === "string") return work.ids.has(value) ? aliasFor(work, value) : value;
  if (Array.isArray(value)) return value.map((item) => aliasTree(item, work));
  if (isObject(value)) {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort()) out[key] = aliasTree(value[key], work);
    return out;
  }
  return value;
}

function stripIds(value: unknown, ids: Set<string>): unknown {
  if (typeof value === "string") return ids.has(value) ? "" : value;
  if (Array.isArray(value)) return value.map((item) => stripIds(item, ids));
  if (isObject(value)) {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort()) out[key] = stripIds(value[key], ids);
    return out;
  }
  return value;
}

/** Unordered multisets are presented canonically so no hidden order survives. */
function canonicalMultiset(cards: readonly unknown[], ids: Set<string>): unknown[] {
  return [...cards].sort((a, b) => {
    const left = canonicalJson(stripIds(a, ids)); const right = canonicalJson(stripIds(b, ids));
    return left < right ? -1 : left > right ? 1 : 0;
  });
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
  const family: SceneFamily = FAMILY[nativeKind] ?? "unsupported";
  const missing: string[] = [];

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

  // Reads: decode exactly; a listed Read that failed makes its part unknown.
  const readsByKind = new Map<string, Record<string, any> | null>();
  for (const listed of snapshot.reads as { read_id: string; kind: string }[]) {
    const captured = basis.reads.find((item) => item.read_id === listed.read_id);
    if (!captured || captured.response == null) { readsByKind.set(listed.kind, null); missing.push(`read:${listed.kind}`); continue; }
    const decoded = decodePlayerRead(captured.response).raw as Record<string, any>;
    if (decoded.read_id !== listed.read_id || decoded.observed_snapshot_id !== snapshot.snapshot_id
        || decoded.kind !== listed.kind || decoded.completeness?.status !== "complete") {
      readsByKind.set(listed.kind, null); missing.push(`read:${listed.kind}:not_complete_for_snapshot`); continue;
    }
    readsByKind.set(listed.kind, decoded);
  }

  // Collect every native id that may appear, then alias in a fixed traversal order.
  const ids = new Set<string>();
  for (const referent of snapshot.referents as NativeReferent[]) ids.add(referent.referent_id);
  collectIds(snapshot.persistent, ids); collectIds(interaction.content, ids);
  for (const read of readsByKind.values()) if (read) collectIds(read.content, ids);
  const work: AliasWork = { aliases: { ...state.aliases }, counts: { ...state.alias_counts }, ids };

  const persistent = snapshot.persistent?.content as Record<string, any> | undefined;
  const terminalScene = family === "terminal";
  const playerPart = persistent?.player
    ? part("available", aliasTree(persistent.player, work), "persistent.player")
    : part(terminalScene ? "terminal" : "unknown", null, null);
  if (!persistent?.player && !terminalScene) missing.push("persistent.player");
  const runPart = persistent?.run
    ? part("available", aliasTree(persistent.run, work), "persistent.run")
    : part(terminalScene ? "terminal" : "unknown", null, null);
  if (!persistent?.run && !terminalScene) missing.push("persistent.run");

  const deckRead = readsByKind.get("run_deck");
  let deckPart: P1Part<unknown>;
  if (deckRead === undefined) deckPart = part(terminalScene ? "terminal" : "unsupported", null, null);
  else if (deckRead === null) deckPart = part("unknown", null, "read:run_deck");
  else {
    const content = deckRead.content as Record<string, any>;
    const cards = Array.isArray(content?.cards) ? canonicalMultiset(content.cards, ids) : [];
    deckPart = part("available", aliasTree({ ...content, cards, ordering: "unordered_multiset" }, work), "read:run_deck");
  }

  const pilesRead = readsByKind.get("combat_piles");
  let pilesPart: P1Part<unknown>;
  if (family !== "combat" && pilesRead === undefined) pilesPart = part("not_applicable", null, null);
  else if (pilesRead === undefined) pilesPart = part("unsupported", null, null);
  else if (pilesRead === null) pilesPart = part("unknown", null, "read:combat_piles");
  else {
    const content = pilesRead.content as Record<string, any>;
    const zones = (Array.isArray(content?.zones) ? content.zones : []).map((zone: Record<string, any>) => {
      const cards = Array.isArray(zone.cards) ? zone.cards : [];
      const draw = zone.zone === "draw" || zone.ordering_semantics === "unordered_multiset";
      return { ...zone, cards: draw ? canonicalMultiset(cards, ids) : cards,
        ordering: draw ? "unordered_multiset" : "native_visible_order" };
    });
    pilesPart = part("available", aliasTree({ ...content, zones }, work), "read:combat_piles");
  }

  const context = interaction.content.context as Record<string, any>;
  const combatPart = family === "combat" || context.kind === "combat"
    ? part("available", aliasTree(context, work), "interaction.context")
    : part(terminalScene ? "terminal" : "not_applicable", null, null);
  const mapPart = context.kind === "map"
    ? part("available", aliasTree(context, work), "interaction.context")
    : part(terminalScene ? "terminal" : "unsupported", null, null);

  const surface = interaction.content.surface as Record<string, any>;
  const sceneContext = context.kind === "combat" || context.kind === "map" ? null
    : Object.fromEntries(Object.entries(context).filter(([key]) => !["run", "player"].includes(key)));
  const sceneContent: Record<string, unknown> = { surface: aliasTree(surface, work) };
  if (sceneContext && Object.keys(sceneContext).length > 1) sceneContent.context = aliasTree(sceneContext, work);

  const extraReads = [...readsByKind.entries()].filter(([kind]) => kind !== "run_deck" && kind !== "combat_piles")
    .map(([kind, read]) => ({ kind, availability: (read ? "available" : "unknown") as Availability,
      content: read ? aliasTree(read.content, work) : null }));

  // Reward groups: identity must come from the Host (stable group_id); never from labels or positions.
  const rewardItems: Record<string, any>[] = nativeKind === "reward_claim" && Array.isArray(surface.rewards) ? surface.rewards : [];
  const groupByReferent = new Map<string, Record<string, any>>();
  for (const item of rewardItems) if (typeof item.entity_id === "string") groupByReferent.set(item.entity_id, item);
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
    let kind = KIND_TABLE[`${nativeKind}|${action.verb}|${role}`];
    const mapped = kind !== undefined;
    kind ??= `native.${nativeKind}.${action.verb}${role ? `.${role}` : ""}`;
    const first = { bound_action_id: action.bound_action_id, verb: action.verb, label: action.label };
    const target = action.arguments.find((item) => item.role === "target")?.referent_id ?? null;
    const subjectAlias = subjectId ? aliasFor(work, subjectId) : null;
    const targetAlias = target ? aliasFor(work, target) : null;
    const base = { follow: [] as FollowRule[], commitment: null as Lowering["commitment"], group_key: null as string | null, option_index: null as number | null, option_definition: null as string | null, option_entity: null as string | null };
    if (kind === "reward.claim" && subjectId) {
      const group = groupByReferent.get(subjectId);
      const groupKey = groupIdentityAvailable ? String(group?.group_id) : null;
      if (groupKey && declined.has(groupKey)) continue; // irreversible P1 decline: never re-offered
      if (group?.kind === "card") {
        const options = Array.isArray(group.card_options) ? group.card_options as Record<string, any>[] : null;
        if (options && groupKey) {
          // Aggregated forward grain: choose the card directly, or decline the whole group.
          options.forEach((option, index) => push(
            { kind: "reward.take_card", subject: aliasFor(work, groupKey), target: typeof option.entity_id === "string" ? aliasFor(work, option.entity_id) : null,
              label: `${action.label} :: ${String(option.name ?? option.definition_id ?? index)}`, mapped: true },
            { ...base, first, group_key: groupKey, option_index: index, option_definition: String(option.definition_id ?? ""),
              option_entity: typeof option.entity_id === "string" ? option.entity_id : null,
              follow: [{ rule_id: "card_reward_select", expect_native_kind: "card_reward_selection", expect_verb: "select" }],
              commitment: "consume_group" }));
          push({ kind: "reward.decline_group", subject: aliasFor(work, groupKey), target: null, label: `Decline: ${action.label}`, mapped: true },
            { ...base, first: null, group_key: groupKey, commitment: "decline_group" });
          continue;
        }
        kind = "reward.open_card_group"; // Host has no lawful preview: navigation stays an explicit choice
        push({ kind, subject: subjectAlias, target: null, label: action.label, mapped: true }, { ...base, first, group_key: groupKey, commitment: "open_group" });
        continue;
      }
      push({ kind, subject: groupKey ? aliasFor(work, groupKey) : subjectAlias, target: null, label: action.label, mapped },
        { ...base, first, group_key: groupKey, commitment: groupKey ? "consume_group" : null });
      continue;
    }
    const exclusion = EXCLUDED_KINDS[kind];
    if (exclusion) { excluded.push({ ...first, rule: exclusion }); continue; }
    let commitment: Lowering["commitment"] = null; let groupKey: string | null = null;
    if (kind === "reward.leave") commitment = "leave_reward_page";
    if (kind === "card_reward.decline" && reward?.open_group) { commitment = "decline_open_group"; groupKey = reward.open_group; }
    if (kind === "card_reward.take" && reward?.open_group) { commitment = "consume_open_group"; groupKey = reward.open_group; }
    const follow: FollowRule[] = [];
    if (kind === "upgrade.choose" && interaction.stage !== "preview")
      follow.push({ rule_id: "upgrade_confirm", expect_native_kind: "deck_upgrade_selection", expect_verb: "confirm" });
    push({ kind, subject: subjectAlias, target: targetAlias, label: action.label, mapped },
      { ...base, first, follow, commitment, group_key: groupKey });
  }
  candidates.sort((a, b) => (a.sort < b.sort ? -1 : a.sort > b.sort ? 1 : 0));
  if (candidates.length > P1G_LIMITS.catalog_choices) missing.push("catalog_over_limit");
  const catalog: P1Choice[] = candidates.map((item, index) => {
    const choice_id = `c${index + 1}`;
    lowering[choice_id] = item.lowering;
    return { choice_id, ...item.choice };
  });

  const victory = surface?.victory;
  const terminal = terminalScene ? { outcome: (victory === true ? "win" : victory === false ? "loss" : "unknown") as "win" | "loss" | "unknown" } : null;
  const decision = snapshot.status === "interactive" && catalog.length > 0;
  const status: P1Observation["status"] = terminalScene ? "terminal" : decision && family !== "unsupported" ? "decision" : "unsupported";
  if (status === "unsupported") missing.push(`scene:${nativeKind}:${snapshot.status}`);

  const commitments: P1Commitments = {
    declined_reward_groups: [...(reward?.declined ?? [])].sort(),
    reward_page_left: reward?.left ?? false
  };

  const observation: P1Observation = {
    schema: P1G_OBSERVATION_SCHEMA, profile: P1G_PROFILE, revision: P1G_REVISION,
    envelope: { episode_id: state.episode_id, occurrence, host_kind: state.host_kind,
      source_snapshot_id: String(snapshot.snapshot_id), source_sequence: Number(snapshot.sequence) },
    status,
    scene: { family, native_kind: nativeKind, stage: String(interaction.stage),
      prompt: typeof interaction.prompt === "string" ? interaction.prompt : null, content: sceneContent },
    run: runPart, player: playerPart, deck: deckPart, map: mapPart, combat: combatPart, piles: pilesPart,
    extra_reads: extraReads, commitments,
    hidden_by_policy: [...new Set<string>([...(snapshot.completeness?.hidden_by_policy ?? []),
      ...[...readsByKind.values()].flatMap((read) => read?.completeness?.hidden_by_policy ?? [])])].sort(),
    catalog, terminal
  };
  if (canonicalJson(observation).length > P1G_LIMITS.observation_bytes) missing.push("observation_over_limit");

  // reward.leave is forced only when every reward group is already resolved (claimed or declined).
  const allResolved = nativeKind === "reward_claim" && groupIdentityAvailable
    && rewardItems.every((item) => declined.has(String(item.group_id)));
  const forcedKinds = allResolved ? new Set([...FORCED_KINDS, "reward.leave"]) : FORCED_KINDS;
  const forced = status === "decision" && catalog.length === 1 && forcedKinds.has(catalog[0]!.kind)
    ? catalog[0]!.choice_id : null;

  const nextState: P1SessionState = {
    ...state, occurrence, last_snapshot_id: String(snapshot.snapshot_id), last_commitments: canonicalJson(reward ?? null),
    aliases: work.aliases, alias_counts: work.counts, reward_pages: rewardPages, reward
  };
  const complete = missing.length === 0 || (status === "terminal" && missing.every((item) => item.startsWith("read:")));
  return { state: nextState, observation, lowering, forced_choice_id: forced, excluded, complete, missing };
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

/** Follow-up native step for a declared fold, taken only from the successor's own catalog. */
export function resolveFollow(rule: FollowRule, lowering: Lowering, successor: unknown): NativeStep | null {
  const snapshot = decodePlayerSnapshot(successor).raw as Record<string, any>;
  if (snapshot.status !== "interactive" || snapshot.interaction.kind !== rule.expect_native_kind) return null;
  const actions = snapshot.bound_actions.actions as NativeAction[];
  if (rule.rule_id === "card_reward_select") {
    // Verify the opened native choice is exactly the previewed option before selecting it.
    const cards = snapshot.interaction.content.surface?.cards as Record<string, any>[] | undefined;
    const byEntity = Array.isArray(cards) && lowering.option_entity
      ? cards.filter((card) => card.entity_id === lowering.option_entity) : [];
    const option = byEntity.length === 1 ? byEntity[0] : Array.isArray(cards) ? cards[lowering.option_index ?? -1] : undefined;
    if (!option || String(option.definition_id ?? "") !== lowering.option_definition) return null;
    const matches = actions.filter((item) => item.verb === "select" && item.subject_referent_id === option.entity_id);
    if (matches.length !== 1) return null;
    const only = matches[0]!;
    return { bound_action_id: only.bound_action_id, verb: only.verb, label: only.label };
  }
  const matches = actions.filter((item) => item.verb === rule.expect_verb);
  if (matches.length !== 1) return null;
  const only = matches[0]!;
  return { bound_action_id: only.bound_action_id, verb: only.verb, label: only.label };
}

/** Model-visible view: the envelope is binding metadata only. */
export function modelView(observation: P1Observation): Omit<P1Observation, "envelope"> {
  const { envelope: _envelope, ...rest } = observation;
  return rest;
}
