import { isJsonObject, type JsonObject } from "./json.js";
import {
  decodePlayerCapabilities,
  decodePlayerReceipt,
  decodePlayerSnapshot,
  type DecodedPlayerPayload,
  type PlayerEnvironmentCapabilities,
  type PlayerEnvironmentReceipt,
  type PlayerEnvironmentSnapshot
} from "./protocol.js";

export const ORDINARY_REWARD_PAGE_PROFILE = "ordinary-reward-page-v1" as const;
export const ORDINARY_REWARD_SNAPSHOT_SCHEMA =
  "sts2.player-environment/ordinary-reward-page-snapshot-1" as const;

export type RewardPageCapabilities = Omit<PlayerEnvironmentCapabilities, "snapshot_schema"> & {
  input_profile: typeof ORDINARY_REWARD_PAGE_PROFILE;
  snapshot_schema: typeof ORDINARY_REWARD_SNAPSHOT_SCHEMA;
};
export type RewardPageSnapshot = Omit<PlayerEnvironmentSnapshot, "schema"> & {
  input_profile: typeof ORDINARY_REWARD_PAGE_PROFILE;
  schema: typeof ORDINARY_REWARD_SNAPSHOT_SCHEMA;
};
export type RewardPageReceipt = Omit<PlayerEnvironmentReceipt, "successor"> & {
  input_profile: typeof ORDINARY_REWARD_PAGE_PROFILE;
  successor: RewardPageSnapshot | null;
};

function requireProfile(value: unknown, label: string): JsonObject {
  if (!isJsonObject(value) || value.input_profile !== ORDINARY_REWARD_PAGE_PROFILE) {
    throw new Error(`${label} has no exact ordinary reward input profile`);
  }
  return value;
}

function withoutProfile(value: JsonObject): JsonObject {
  const copy = { ...value };
  delete copy.input_profile;
  return copy;
}

export function decodeRewardPageCapabilities(value: unknown): DecodedPlayerPayload<RewardPageCapabilities> {
  const raw = requireProfile(value, "Player Environment capabilities");
  if (raw.snapshot_schema !== ORDINARY_REWARD_SNAPSHOT_SCHEMA) {
    throw new Error("Ordinary reward capabilities declare the wrong snapshot schema");
  }
  const normal = withoutProfile(raw);
  normal.snapshot_schema = "sts2.player-environment/snapshot-1";
  const decoded = decodePlayerCapabilities(normal);
  return { raw, data: { ...decoded.data, input_profile: ORDINARY_REWARD_PAGE_PROFILE,
    snapshot_schema: ORDINARY_REWARD_SNAPSHOT_SCHEMA } };
}

export function decodeRewardPageSnapshot(value: unknown): DecodedPlayerPayload<RewardPageSnapshot> {
  const raw = requireProfile(value, "Player Environment observation");
  if (raw.schema !== ORDINARY_REWARD_SNAPSHOT_SCHEMA) {
    throw new Error("Ordinary reward observation has the wrong snapshot schema");
  }
  const interaction = raw.interaction;
  if (!isJsonObject(interaction)
      || typeof interaction.kind !== "string"
      || interaction.content_schema !== `sts2.player-environment/surface/${interaction.kind}-2`
      || !Array.isArray(raw.reads) || raw.reads.length !== 0) {
    throw new Error("Ordinary reward page requires its current-page surface schema and no Reads");
  }
  const supported = interaction.kind === "reward_claim"
    || interaction.kind === "card_reward_selection";
  if (!supported && raw.status !== "visible_unsupported") {
    throw new Error("Ordinary reward profile cannot authorize another page");
  }
  if (raw.status === "visible_unsupported") {
    const catalog = raw.bound_actions;
    if (!isJsonObject(catalog) || !Array.isArray(catalog.actions)
        || catalog.actions.length !== 0 || !Array.isArray(interaction.capabilities)
        || interaction.capabilities.length !== 0) {
      throw new Error("Unsupported ordinary reward page advertised action authority");
    }
  }
  if (interaction.kind === "card_reward_selection" && raw.status !== "visible_unsupported") {
    const content = interaction.content;
    const surface = isJsonObject(content) ? content.surface : null;
    const alternatives = isJsonObject(surface) ? surface.alternatives : null;
    const effects = isJsonObject(surface) ? surface.alternative_effects : null;
    if (!Array.isArray(alternatives) || !Array.isArray(effects)
        || alternatives.length !== effects.length
        || effects.some((effect, index) => !isJsonObject(effect)
          || effect.effect !== "return_to_rewards_without_claim"
          || !isJsonObject(alternatives[index])
          || effect.entity_id !== alternatives[index].entity_id)) {
      throw new Error("Ordinary reward alternative effects do not bind the current menu");
    }
  }
  // Reuse all legacy structural and referent/action validators. The opt-in
  // identity and surface version are checked above and restored in the result.
  const normal = withoutProfile(raw);
  normal.schema = "sts2.player-environment/snapshot-1";
  normal.interaction = { ...interaction, content_schema:
    `sts2.player-environment/surface/${interaction.kind}-1` };
  const decoded = decodePlayerSnapshot(normal);
  return { raw, data: {
    ...decoded.data,
    input_profile: ORDINARY_REWARD_PAGE_PROFILE,
    schema: ORDINARY_REWARD_SNAPSHOT_SCHEMA,
    interaction: { ...decoded.data.interaction, content_schema: interaction.content_schema }
  } };
}

export function decodeRewardPageReceipt(value: unknown): DecodedPlayerPayload<RewardPageReceipt> {
  const raw = requireProfile(value, "Player Environment receipt");
  const successor = raw.successor == null ? null : decodeRewardPageSnapshot(raw.successor).data;
  const normal = withoutProfile(raw);
  if (successor == null) {
    normal.successor = null;
  } else {
    const next = raw.successor;
    if (!isJsonObject(next) || !isJsonObject(next.interaction)) {
      throw new Error("Ordinary reward successor is malformed");
    }
    const normalized: JsonObject = {
      ...next,
      schema: "sts2.player-environment/snapshot-1",
      interaction: { ...next.interaction, content_schema:
        `sts2.player-environment/surface/${successor.interaction.kind}-1` }
    };
    delete normalized.input_profile;
    normal.successor = normalized;
  }
  const decoded = decodePlayerReceipt(normal);
  return { raw, data: { ...decoded.data, input_profile: ORDINARY_REWARD_PAGE_PROFILE, successor } };
}
