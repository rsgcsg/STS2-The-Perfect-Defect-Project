import { isJsonObject, type JsonObject } from "./json.js";
import {
  decodePlayerCapabilities, decodePlayerReceipt, decodePlayerSnapshot,
  type DecodedPlayerPayload, type PlayerEnvironmentCapabilities,
  type PlayerEnvironmentReceipt, type PlayerEnvironmentSnapshot
} from "./protocol.js";

export const REWARD_POTION_PAGE_PROFILE = "ordinary-reward-potion-page-v2" as const;
export const REWARD_POTION_SNAPSHOT_SCHEMA =
  "sts2.player-environment/ordinary-reward-potion-page-snapshot-2" as const;

export type RewardPotionCapabilities = Omit<PlayerEnvironmentCapabilities, "snapshot_schema"> & {
  input_profile: typeof REWARD_POTION_PAGE_PROFILE;
  snapshot_schema: typeof REWARD_POTION_SNAPSHOT_SCHEMA;
};
export type RewardPotionSnapshot = Omit<PlayerEnvironmentSnapshot, "schema"> & {
  input_profile: typeof REWARD_POTION_PAGE_PROFILE;
  schema: typeof REWARD_POTION_SNAPSHOT_SCHEMA;
};
export type RewardPotionReceipt = Omit<PlayerEnvironmentReceipt, "successor"> & {
  input_profile: typeof REWARD_POTION_PAGE_PROFILE;
  successor: RewardPotionSnapshot | null;
};

function requireV2(value: unknown, label: string): JsonObject {
  if (!isJsonObject(value) || value.input_profile !== REWARD_POTION_PAGE_PROFILE) {
    throw new Error(`${label} has no exact reward/potion v2 input profile`);
  }
  return value;
}

function withoutProfile(value: JsonObject): JsonObject {
  const copy = { ...value };
  delete copy.input_profile;
  return copy;
}

export function decodeRewardPotionCapabilities(
  value: unknown
): DecodedPlayerPayload<RewardPotionCapabilities> {
  const raw = requireV2(value, "Capabilities");
  if (raw.snapshot_schema !== REWARD_POTION_SNAPSHOT_SCHEMA) {
    throw new Error("Reward/potion capabilities declare the wrong snapshot schema");
  }
  const normal = withoutProfile(raw);
  normal.snapshot_schema = "sts2.player-environment/snapshot-1";
  const decoded = decodePlayerCapabilities(normal);
  return { raw, data: { ...decoded.data, input_profile: REWARD_POTION_PAGE_PROFILE,
    snapshot_schema: REWARD_POTION_SNAPSHOT_SCHEMA } };
}

function currentOptions(raw: JsonObject): Map<string, { kind: string; verb: string }> {
  const interaction = raw.interaction;
  if (!isJsonObject(interaction) || !isJsonObject(interaction.content)
      || !isJsonObject(interaction.content.surface)) {
    throw new Error("Reward/potion current page is malformed");
  }
  const surface = interaction.content.surface;
  const options = new Map<string, { kind: string; verb: string }>();
  const seen = new Set<string>();
  function add(id: unknown, kind: string, verb: string, enabled = true): void {
    if (typeof id !== "string" || id.length === 0 || seen.has(id)) {
      throw new Error("Reward/potion current option identity is missing or duplicated");
    }
    seen.add(id);
    if (enabled) options.set(id, { kind, verb });
  }
  function rows(value: unknown): JsonObject[] {
    if (!Array.isArray(value) || !value.every(isJsonObject)) {
      throw new Error("Reward/potion current option list is incomplete");
    }
    return value;
  }
  const kind = interaction.kind;
  if (kind === "reward_claim" || kind === "card_reward_selection") {
    const openers = rows(surface.openable_potions);
    const slots = new Set<number>();
    for (const opener of openers) {
      if (!Number.isInteger(opener.slot) || (opener.slot as number) < 0
          || slots.has(opener.slot as number)) {
        throw new Error("Reward/potion opener has no unique current slot");
      }
      slots.add(opener.slot as number);
      add(opener.potion_entity_id, "potion_open", "open");
    }
    if (kind === "reward_claim") {
      if (["discardable_potions", "cards", "alternatives", "alternative_effects", "controls"]
          .some((key) => key in surface)
          || typeof surface.can_proceed !== "boolean"
          || typeof surface.proceed_skips_remaining_rewards !== "boolean") {
        throw new Error("Reward/potion outer page has incomplete or legacy proceed controls");
      }
      for (const reward of rows(surface.rewards)) {
        if (typeof reward.enabled !== "boolean") throw new Error("Reward enablement is unknown");
        add(reward.entity_id, "reward", "activate", reward.enabled);
      }
      if (surface.can_proceed) add("<current-page-proceed>", "proceed", "activate");
    } else {
      if (["rewards", "controls", "discardable_potions", "can_proceed",
        "proceed_skips_remaining_rewards"].some((key) => key in surface)) {
        throw new Error("Reward/potion inner page contains another page's content");
      }
      const cards = rows(surface.cards);
      const selectable = surface.selectable_card_entity_ids;
      if (!Array.isArray(selectable) || !selectable.every((id) => typeof id === "string")) {
        throw new Error("Selectable card list is unknown");
      }
      const selected = new Set(selectable);
      if (selected.size !== selectable.length
          || cards.filter((card) => typeof card.entity_id === "string"
            && selected.has(card.entity_id)).length !== selected.size) {
        throw new Error("Selectable card list does not bind the current page");
      }
      for (const card of cards) add(card.entity_id, "card", "select",
        typeof card.entity_id === "string" && selected.has(card.entity_id));
      const alternatives = rows(surface.alternatives);
      const effects = rows(surface.alternative_effects);
      if (alternatives.length !== effects.length) throw new Error("Alternative effects are incomplete");
      alternatives.forEach((option, index) => {
        if (option.index !== index || typeof option.enabled !== "boolean"
            || effects[index]?.entity_id !== option.entity_id
            || effects[index]?.effect !== "return_to_rewards_without_claim") {
          throw new Error("Alternative effect does not bind the current page");
        }
        add(option.entity_id, "alternative", "activate", option.enabled);
      });
    }
  } else if (kind === "potion_popup") {
    if (["use_target_entity_ids", "direct_combat_use", "rewards", "cards",
      "alternatives", "alternative_effects", "openable_potions", "discardable_potions",
      "can_proceed", "proceed_skips_remaining_rewards"].some((key) => key in surface)) {
      throw new Error("Popup v2 must not project future targets or another page's content");
    }
    const controls = rows(surface.controls);
    const kinds = new Set<string>();
    for (const control of controls) {
      if (typeof control.kind !== "string" || kinds.has(control.kind)
          || !["use", "discard", "close"].includes(control.kind)
          || typeof control.enabled !== "boolean") {
        throw new Error("Popup current controls are ambiguous");
      }
      kinds.add(control.kind);
      add(control.entity_id, control.kind,
        control.kind === "close" ? "cancel" : "activate", control.enabled);
    }
    if (typeof surface.potion_entity_id !== "string" || !surface.potion_entity_id
        || typeof surface.can_use !== "boolean" || typeof surface.can_discard !== "boolean"
        || !kinds.has("close") || !controls.some((control) => control.kind === "close" && control.enabled)
        || surface.can_use !== controls.some((control) => control.kind === "use" && control.enabled)
        || surface.can_discard !== controls.some((control) => control.kind === "discard" && control.enabled)) {
      throw new Error("Popup controls do not match enabled native buttons");
    }
  }
  return options;
}

export function decodeRewardPotionSnapshot(value: unknown): DecodedPlayerPayload<RewardPotionSnapshot> {
  const raw = requireV2(value, "Observation");
  if (raw.schema !== REWARD_POTION_SNAPSHOT_SCHEMA || !Array.isArray(raw.reads)
      || raw.reads.length !== 0 || !isJsonObject(raw.interaction)
      || typeof raw.interaction.kind !== "string"
      || raw.interaction.content_schema
        !== `sts2.player-environment/surface/${raw.interaction.kind}-3`) {
    throw new Error("Reward/potion v2 observation requires its exact current-page schema and no Reads");
  }
  const supported = ["reward_claim", "card_reward_selection", "potion_popup"]
    .includes(raw.interaction.kind);
  if (!supported && raw.status !== "visible_unsupported") {
    throw new Error("Reward/potion profile cannot authorize another page");
  }
  if (raw.status === "visible_unsupported") {
    const catalog = raw.bound_actions;
    if (!isJsonObject(catalog) || !Array.isArray(catalog.actions)
        || catalog.actions.length !== 0 || !Array.isArray(raw.interaction.capabilities)
        || raw.interaction.capabilities.length !== 0) {
      throw new Error("Unsupported reward/potion page advertised action authority");
    }
  } else if (raw.status === "interactive") {
    const options = currentOptions(raw);
    const surface = (raw.interaction.content as JsonObject).surface as JsonObject;
    if (!Array.isArray(raw.referents) || !raw.referents.every(isJsonObject)) {
      throw new Error("Reward/potion current referents are malformed");
    }
    const referents = new Map<string, JsonObject>();
    for (const referent of raw.referents) {
      if (typeof referent.referent_id !== "string" || referents.has(referent.referent_id)) {
        throw new Error("Reward/potion current referents are duplicated");
      }
      referents.set(referent.referent_id, referent);
    }
    for (const [id, option] of options) {
      if (option.kind === "proceed") continue;
      const ref = referents.get(id);
      const expectedRole = option.kind === "alternative" ? "option"
        : option.kind === "potion_open" ? "potion"
          : ["use", "discard", "close"].includes(option.kind) ? "control" : option.kind;
      const propertyKey = option.kind === "potion_open" ? "potion_entity_id" : "entity_id";
      if (!ref || ref.kind !== "entity" || ref.role !== expectedRole
          || !isJsonObject(ref.properties) || ref.properties[propertyKey] !== id) {
        throw new Error("Reward/potion action referent does not bind the current option");
      }
      if (option.kind === "potion_open") {
        const opener = (surface.openable_potions as JsonObject[])
          .find((value) => value.potion_entity_id === id);
        if (!opener || ref.properties.slot !== opener.slot) {
          throw new Error("Reward/potion opener referent has a different current slot");
        }
      }
    }
    if (raw.interaction.kind === "potion_popup") {
      const id = surface.potion_entity_id;
      const ref = typeof id === "string" ? referents.get(id) : undefined;
      if (!ref || ref.kind !== "entity" || ref.role !== "potion"
          || !isJsonObject(ref.properties) || ref.properties.potion_entity_id !== id) {
        throw new Error("Popup potion argument has no exact current referent");
      }
    }
    const catalog = raw.bound_actions;
    if (!isJsonObject(catalog) || catalog.status !== "complete"
        || !Array.isArray(catalog.actions)
        || catalog.actions.length !== catalog.total_count
        || catalog.actions.length !== catalog.materialized_count) {
      throw new Error("Reward/potion catalog is incomplete");
    }
    const covered = new Set<string>();
    for (const action of catalog.actions) {
      if (!isJsonObject(action) || !Array.isArray(action.arguments)) {
        throw new Error("Reward/potion action is malformed");
      }
      const subject = action.subject_referent_id;
      if (subject != null && typeof subject !== "string") {
        throw new Error("Reward/potion action subject is malformed");
      }
      const key = subject ?? "<current-page-proceed>";
      const option = options.get(key);
      if (!option || action.verb !== option.verb) {
        throw new Error("Reward/potion action does not bind a current option");
      }
      if (covered.has(key)) {
        throw new Error("Reward/potion action repeats a current option");
      }
      if (raw.interaction.kind === "potion_popup"
          && (option.kind === "use" || option.kind === "discard")) {
        const argument = action.arguments[0];
        if (action.arguments.length !== 1 || !isJsonObject(argument)
            || argument.role !== "potion" || argument.referent_id !== surface.potion_entity_id) {
          throw new Error("Popup action does not bind its exact current potion");
        }
      } else if (action.arguments.length !== 0) {
        throw new Error("Reward/potion action has unexpected operands");
      }
      covered.add(key);
    }
    if (covered.size !== options.size) throw new Error("Reward/potion menu omits a current option");
  } else {
    throw new Error("Reward/potion v2 has no complete current menu");
  }
  const normal = withoutProfile(raw);
  normal.schema = "sts2.player-environment/snapshot-1";
  normal.interaction = { ...raw.interaction, content_schema:
    `sts2.player-environment/surface/${raw.interaction.kind}-1` };
  const decoded = decodePlayerSnapshot(normal);
  return { raw, data: { ...decoded.data, input_profile: REWARD_POTION_PAGE_PROFILE,
    schema: REWARD_POTION_SNAPSHOT_SCHEMA,
    interaction: { ...decoded.data.interaction, content_schema: raw.interaction.content_schema } } };
}

export function decodeRewardPotionReceipt(value: unknown): DecodedPlayerPayload<RewardPotionReceipt> {
  const raw = requireV2(value, "Receipt");
  const successor = raw.successor == null ? null : decodeRewardPotionSnapshot(raw.successor).data;
  const normal = withoutProfile(raw);
  if (successor == null) normal.successor = null;
  else {
    if (!isJsonObject(raw.successor) || !isJsonObject(raw.successor.interaction)) {
      throw new Error("Reward/potion successor is malformed");
    }
    const normalized: JsonObject = { ...raw.successor,
      schema: "sts2.player-environment/snapshot-1",
      interaction: { ...raw.successor.interaction, content_schema:
        `sts2.player-environment/surface/${successor.interaction.kind}-1` } };
    delete normalized.input_profile;
    normal.successor = normalized;
  }
  const decoded = decodePlayerReceipt(normal);
  return { raw, data: { ...decoded.data, input_profile: REWARD_POTION_PAGE_PROFILE, successor } };
}
