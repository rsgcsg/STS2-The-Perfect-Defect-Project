import { z } from "zod";
import { isJsonObject, isJsonValue, type JsonObject, type JsonValue } from "./json.js";
import { decodePlayerSnapshot, type DecodedPlayerPayload } from "./protocol.js";
import { SEALED_OBSERVATION_MAX_BYTES, SEALED_OBSERVATION_DEFAULT_CHUNK_BYTES,
  SEALED_OBSERVATION_MIN_CHUNK_BYTES, SEALED_OBSERVATION_MAX_CHUNK_BYTES,
  validateSealedChunkLimit } from "./sealedObservation.js";
import { assembleRetainedBytes, publicBytesSha256 } from "./retainedBytes.js";

export const P1_CAPTURE_PROFILE = "p1-atomic-capture-c1-1" as const;
export const P1_CAPTURE_REVISION = "c1-2" as const;
// Original c1-1 archives preserve their declared unordered-pile semantics.
// New captures retain the native visible order of discard/exhaust.
const captureRevision = z.enum(["c1-1", P1_CAPTURE_REVISION]);
export const P1_CAPTURE_SCOPE = "ordinary_ready_combat_base_and_read_instances" as const;
export const P1_CAPTURE_READ_PROFILE = "p1-atomic-capture-c1-sealed-1" as const;
export const P1_CAPTURE_OBSERVATION_SCHEMA = "sts2.player-environment/p1-atomic-capture-c1-1" as const;
export const P1_CAPTURE_HANDLE_SCHEMA = "sts2.player-environment/p1-atomic-capture-handle-c1-1" as const;
export const P1_CAPTURE_CHUNK_SCHEMA = "sts2.player-environment/p1-atomic-capture-chunk-c1-1" as const;
export const P1_CAPTURE_RELEASE_SCHEMA = "sts2.player-environment/p1-atomic-capture-release-c1-1" as const;
export const P1_CAPTURE_CAPABILITIES_SCHEMA = "sts2.player-environment/p1-atomic-capture-capabilities-c1-1" as const;
export const P1_CAPTURE_RECORD_SCHEMA = "sts2.player-environment/p1-atomic-capture-record-c1-1" as const;
export const P1_CAPTURE_ROUTE = "/api/player-environment/p1-capture";
export const P1_CAPTURE_MAX_READ_INSTANCES = 10_000;
export const P1_CAPTURE_NATIVE_CATALOG_SEMANTICS = "legacy_native_basis_not_p1_semantic_catalog" as const;
export const P1_CAPTURE_FULL_MISSING = ["current_act_map", "p1_semantic_catalog", "p1_protocol_commitments",
  "complete_public_detail_closure", "native_capture_noninterference_qualification"] as const;
export const P1_CAPTURE_SCOPE_REQUIREMENTS = ["ordinary_combat_scope", "current_public_base", "current_parent_combat_context",
  "legacy_native_action_relation", "run_deck", "combat_piles", "surface_card_instances"] as const;

const id = z.string().min(1).max(128).regex(/^[A-Za-z0-9_.:-]+$/u);
const cursor = z.string().min(1).max(512).regex(/^[A-Za-z0-9_-]+$/u);
const digest = z.string().regex(/^[a-f0-9]{64}$/u);
const timestamp = z.string().datetime({ offset: true });
const session = z.object({ runtime_instance_id: z.string().min(1), environment_fingerprint: z.string().min(1) }).strict();
const byteCount = z.number().int().positive().max(SEALED_OBSERVATION_MAX_BYTES);
const handleSchema = z.object({
  schema: z.literal(P1_CAPTURE_HANDLE_SCHEMA), read_profile: z.literal(P1_CAPTURE_READ_PROFILE),
  input_profile: z.literal(P1_CAPTURE_PROFILE), capture_id: id, source_snapshot_id: id,
  session, generation_id: id, game_continuity_id: id.nullable(), captured_at: timestamp,
  expires_at: timestamp, total_bytes: byteCount, sha256: digest, first_cursor: cursor,
  capture_ordinal: z.number().int().positive().max(Number.MAX_SAFE_INTEGER)
}).strict().superRefine((value, ctx) => {
  if (Date.parse(value.expires_at) <= Date.parse(value.captured_at))
    ctx.addIssue({ code: "custom", message: "capture expires before its capture" });
});
const chunkSchema = z.object({
  schema: z.literal(P1_CAPTURE_CHUNK_SCHEMA), capture_id: id, sha256: digest,
  offset: z.number().int().nonnegative().max(SEALED_OBSERVATION_MAX_BYTES), total_bytes: byteCount,
  data_base64: z.string().min(4).max(4 * Math.ceil(SEALED_OBSERVATION_MAX_CHUNK_BYTES / 3))
    .regex(/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u),
  next_cursor: cursor.nullable(), end: z.boolean()
}).strict().superRefine((value, ctx) => {
  if (value.end !== (value.next_cursor === null)) ctx.addIssue({ code: "custom", message: "chunk end/cursor mismatch" });
});
const releaseSchema = z.object({ schema: z.literal(P1_CAPTURE_RELEASE_SCHEMA), capture_id: id, released: z.literal(true) }).strict();
const availability = z.enum(["available", "native_unavailable", "not_applicable", "unrevealed", "unsupported", "unknown", "settling", "terminal"]);
const missing = z.array(z.string().min(1));
const nonblank = z.string().refine(value => value.trim().length > 0, "must contain public display text");
const nativeInt = z.number().int().min(-2_147_483_648).max(2_147_483_647);
const cardContentSchema = z.object({
  entity_id: z.string().min(1), definition_id: z.string().min(1), name: z.string().nullable().optional(),
  type: z.string().min(1), cost: z.string().min(1), star_cost: z.string().nullable().optional(),
  description: z.string().nullable().optional(), rarity: z.string().min(1),
  is_upgraded: z.boolean(), is_selected: z.boolean(), existing_enchantment: z.object({
    definition_id: z.string().min(1), name: z.string().nullable().optional(),
    description: z.string().nullable().optional(), amount: nativeInt, observation_source: z.string()
  }).strict().nullable().optional(), target_type: z.string().nullable().optional(),
  can_play: z.boolean().nullable().optional(), unplayable_reason: z.string().nullable().optional()
}).strict();
const deckContentSchema = z.object({ kind: z.literal("run_deck"), card_count: nativeInt.nonnegative(),
  cards: z.array(cardContentSchema) }).strict().refine(value => value.card_count === value.cards.length, "deck count mismatch");
const pileContentSchema = z.object({ kind: z.literal("combat_piles"), zones: z.array(z.object({
  zone: z.enum(["draw", "discard", "exhaust"]), card_count: nativeInt.nonnegative(),
  ordering_semantics: z.enum(["unordered_multiset", "native_visible_order"]), cards: z.array(cardContentSchema)
}).strict().refine(value => value.card_count === value.cards.length, "pile count mismatch")) }).strict()
  .refine(value => value.zones.length === 3 && new Set(value.zones.map(zone => zone.zone)).size === 3,
    "complete pile content must include each of the three zones");
// The C1 complete-scope check follows the existing native DTOs in
// LiveHostContracts.cs. Legacy Snapshot decoding remains intentionally generic.
const publicText = z.string().nullable().optional();
const statusContentSchema = z.object({ definition_id: z.string().min(1), name: publicText,
  amount: z.number(), type: z.string().min(1), description: publicText }).passthrough();
const healthFields = {
  hp: z.number().nullable(), max_hp: z.number().nullable(), block: z.number(),
  health_bar_visible: z.boolean(), hp_display_mode: z.string().min(1), hp_numbers_available: z.boolean()
};
const coherentHealthDisplay = (value: { hp: number | null; max_hp: number | null;
  hp_numbers_available: boolean; health_bar_visible: boolean }) => value.hp_numbers_available
    ? value.health_bar_visible && value.hp !== null && value.max_hp !== null
    : value.hp === null && value.max_hp === null;
const combatContextSchema = z.object({
  kind: z.literal("combat"), encounter_type: z.string().min(1), round: nativeInt,
  turn_owner: z.string().min(1), is_play_phase: z.boolean(),
  player: z.object({
    player_entity_id: z.string().min(1), block: z.number(), energy: nativeInt, max_energy: nativeInt,
    stars: nativeInt.nullable().optional(), hand: z.array(cardContentSchema),
    draw_pile_count: nativeInt.nonnegative(), discard_pile_count: nativeInt.nonnegative(), exhaust_pile_count: nativeInt.nonnegative(),
    statuses: z.array(statusContentSchema),
    companions: z.array(z.object({ entity_id: z.string().min(1), definition_id: z.string().min(1),
      name: publicText, is_alive: z.boolean(), ...healthFields, statuses: z.array(statusContentSchema) }).passthrough().refine(coherentHealthDisplay, "incoherent public health display")),
    potion_states: z.array(z.object({ entity_id: z.string().min(1), target_type: z.string().min(1),
      can_use: z.boolean(), automatic: z.boolean() }).passthrough()),
    orbs: z.array(z.object({ entity_id: z.string().min(1), definition_id: z.string().min(1), name: publicText,
      description: publicText, passive_value: z.number(), evoke_value: z.number(), queue_index: nativeInt,
      is_next_to_evoke: z.boolean() }).passthrough()), orb_slots: nativeInt.nullable().optional()
  }).passthrough(),
  enemies: z.array(z.object({ entity_id: z.string().min(1), combat_id: z.number().int().min(0).max(4_294_967_295).nullable().optional(),
    definition_id: z.string().min(1), name: publicText, ...healthFields, statuses: z.array(statusContentSchema),
    intents: z.array(z.object({ type: z.string().min(1), label: publicText, title: publicText,
      description: publicText }).passthrough()) }).passthrough().refine(coherentHealthDisplay, "incoherent public health display"))
}).passthrough();

// The generic legacy persistent decoder checks shape. C1 also preserves the
// native int domains on these DTO fields without guessing gameplay ranges.
const previewIntegerSchema = z.object({ existing_enchantment: z.object({ amount: nativeInt })
  .passthrough().nullable().optional() }).passthrough();
const persistentContextSchema = z.object({
  run: z.object({ act: nativeInt, floor: nativeInt, ascension: nativeInt,
    bosses: z.array(z.object({ order: nativeInt }).passthrough()),
    modifiers: z.array(z.object({ card_previews: z.array(previewIntegerSchema) }).passthrough()) }).passthrough(),
  player: z.object({ gold: nativeInt, max_potion_slots: nativeInt,
    relics: z.array(z.object({ card_previews: z.array(previewIntegerSchema) }).passthrough()),
    potions: z.array(z.object({ slot: nativeInt, card_previews: z.array(previewIntegerSchema) }).passthrough()) }).passthrough(),
  completeness: z.object({ missing: z.array(z.string()).length(0) }).passthrough()
}).passthrough();

// Details may supply optional missing text, but cannot contradict facts already
// present in that exact display context. Never compare dynamic deck/hand views.
function preservesSourceCard(source: JsonObject, detail: JsonObject): boolean {
  // Native nullable nontext values are omitted on the wire when null. Detail
  // extraction does not invent enchantments, costs, targets or play legality.
  if (["star_cost", "existing_enchantment", "target_type", "can_play", "unplayable_reason"]
      .some(field => source[field] == null && detail[field] != null)) return false;
  return Object.entries(source).every(([field, fact]) =>
    fact === null ? detail[field] == null || field === "name" || field === "description" :
    (isJsonObject(fact) && isJsonObject(detail[field])
      ? preservesSourceCard(fact, detail[field]) : fact === detail[field]));
}
const readSchema = z.object({
  instance_id: id, kind: z.string().min(1), source_read_id: z.string().min(1).nullable(),
  target_entity_id: z.string().min(1).nullable(), display_context: z.string().min(1),
  parent_read_instance_id: id.nullable(), availability, content_schema: z.string().min(1),
  content: z.custom<JsonValue>(isJsonValue), missing
}).strict().superRefine((value, ctx) => {
  if (value.availability === "available" && (value.content === null || value.missing.length !== 0))
    ctx.addIssue({ code: "custom", message: "available Read has missing content" });
  if (value.availability !== "available") return;
  const issue = (message: string) => ctx.addIssue({ code: "custom", message });
  if (value.kind === "surface_card" && value.content_schema === "sts2.player-environment/p1-current-card-display-c1-1") {
    const parsed = cardContentSchema.safeParse(value.content);
    if (!parsed.success || !nonblank.safeParse(parsed.data.name).success || !nonblank.safeParse(parsed.data.description).success)
      issue("available current card detail lacks its required display fields");
    else {
      if (parsed.data.entity_id !== value.target_entity_id) issue("current card detail target/entity mismatch");
      const enchantment = parsed.data.existing_enchantment;
      if (enchantment && (!nonblank.safeParse(enchantment.name).success || !nonblank.safeParse(enchantment.description).success))
        issue("available enchantment detail lacks current display text");
    }
    if (!["hand", "deck", "draw", "discard", "exhaust"].includes(value.display_context))
      issue("available card detail has an unqualified display context");
  } else if (value.kind === "run_deck" && value.content_schema === "sts2.player-environment/read/run_deck-1") {
    if (!deckContentSchema.safeParse(value.content).success || value.display_context !== "deck" || value.target_entity_id !== null)
      issue("available deck Read is not the complete typed deck content");
  } else if (value.kind === "combat_piles" && value.content_schema === "sts2.player-environment/read/combat_piles-1") {
    if (!pileContentSchema.safeParse(value.content).success || value.display_context !== "combat_piles" || value.target_entity_id !== null)
      issue("available pile Read is not the complete typed pile content");
  } else issue("unknown C1 Read schema cannot advertise available content");
});
const coverageSchema = z.object({
  requirement_id: z.string().min(1), required: z.boolean(), availability,
  read_instance_ids: z.array(id), missing
}).strict().superRefine((value, ctx) => {
  if (new Set(value.read_instance_ids).size !== value.read_instance_ids.length ||
      value.availability === "available" && value.missing.length !== 0)
    ctx.addIssue({ code: "custom", message: "coverage has repeated instances or contradictory availability" });
});
function compatibleSourceCards(left: JsonObject, right: JsonObject): boolean {
  return [...new Set([...Object.keys(left), ...Object.keys(right)])].every(field => {
    const a = left[field] ?? null; const b = right[field] ?? null;
    if ((field === "name" || field === "description") && (a === null || b === null)) return true;
    return isJsonObject(a) && isJsonObject(b) ? compatibleSourceCards(a, b) : a === b;
  });
}
function hasFinitePublicNumbers(value: unknown): boolean {
  const pending: unknown[] = [value];
  const seen = new Set<object>();
  while (pending.length > 0) {
    const item = pending.pop();
    if (typeof item === "number" && !Number.isFinite(item)) return false;
    if (typeof item === "object" && item !== null && !seen.has(item)) {
      seen.add(item); pending.push(...Object.values(item));
    }
  }
  return true;
}
const observationSchema = z.object({
  schema: z.literal(P1_CAPTURE_OBSERVATION_SCHEMA), input_profile: z.literal(P1_CAPTURE_PROFILE),
  revision: captureRevision, capture_scope: z.literal(P1_CAPTURE_SCOPE),
  scope_status: z.enum(["complete", "partial", "unsupported"]), p1_status: z.literal("unsupported"),
  agent_admissible: z.literal(false), creates_mutation_authority: z.literal(false),
  native_catalog_semantics: z.literal(P1_CAPTURE_NATIVE_CATALOG_SEMANTICS),
  occurrence_semantics: z.literal("sampled_current_capture_not_native_occurrence"),
  source_snapshot_id: id, captured_at: timestamp, session,
  native_basis: z.custom<JsonObject>(isJsonObject),
  reads: z.array(readSchema).max(P1_CAPTURE_MAX_READ_INSTANCES), coverage: z.array(coverageSchema)
}).strict().superRefine((value, ctx) => {
  const issue = (message: string) => ctx.addIssue({ code: "custom", message });
  if (!hasFinitePublicNumbers(value)) issue("capture public numbers must be finite JSON values");
  for (const read of value.reads) {
    if (read.kind !== "combat_piles" || read.availability !== "available") continue;
    const parsed = pileContentSchema.safeParse(read.content);
    if (!parsed.success) continue; // The Read's typed validator reports this.
    for (const zone of parsed.data.zones) {
      const expected = value.revision === "c1-1" || zone.zone === "draw"
        ? "unordered_multiset" : "native_visible_order";
      if (zone.ordering_semantics !== expected)
        issue("pile ordering does not match the declared capture revision");
    }
  }
  try {
    const basis = decodePlayerSnapshot(value.native_basis).data;
    if (basis.snapshot_id !== value.source_snapshot_id ||
        basis.session.runtime_instance_id !== value.session.runtime_instance_id ||
        basis.session.environment_fingerprint !== value.session.environment_fingerprint)
      issue("capture native basis/session mismatch");
    const advertised = new Map(basis.reads.map(read => [read.read_id, read]));
    if (advertised.size !== basis.reads.length) issue("advertised Read identities must be unique");
    for (const read of basis.reads) {
      if (read.kind === "combat_piles" && read.ordering_semantics !==
          (value.revision === "c1-1" ? "unordered_multiset" : "per_zone"))
        issue("advertised pile ordering does not match the declared capture revision");
    }
    const capturedSources = new Set<string>();
    for (const read of value.reads) {
      if (read.source_read_id === null) {
        if (read.kind !== "surface_card") issue("aggregate Read lacks its advertised source");
        continue;
      }
      const source = advertised.get(read.source_read_id);
      if (!source || source.kind !== read.kind || source.content_schema !== read.content_schema ||
          (source.target_referent_id ?? null) !== read.target_entity_id || read.parent_read_instance_id !== null)
        issue("captured Read does not match its advertised source kind/schema/target");
      if (capturedSources.has(read.source_read_id)) issue("advertised Read source was reused");
      capturedSources.add(read.source_read_id);
    }
    if (basis.reads.some(read => !capturedSources.has(read.read_id))) issue("advertised Read was not captured");
    if (value.scope_status === "complete") {
      for (const kind of ["run_deck", "combat_piles"])
        if (basis.reads.filter(read => read.kind === kind && (read.target_referent_id ?? null) === null).length !== 1)
          issue("complete capture scope lacks its required advertised aggregate Reads");
    }
    const persistent = basis.persistent?.content;
    const persistentComplete = persistentContextSchema.safeParse(persistent).success;
    if (value.scope_status === "complete" && (!persistentComplete ||
        basis.interaction.content_schema !== "sts2.player-environment/surface/combat_turn-1" ||
        basis.interaction.content.surface.kind !== "combat_turn" || basis.completeness.status !== "complete" ||
        basis.completeness.missing.length !== 0 || basis.interaction.kind !== "combat_turn" ||
        !isJsonObject(basis.interaction.content.context) || basis.interaction.content.context.kind !== "combat" ||
        basis.interaction.content.context.is_play_phase !== true || basis.status !== "interactive" ||
        basis.interaction.stage !== "ready" || basis.bound_actions.status !== "complete"))
      issue("complete capture scope contradicts the native basis");
  } catch { issue("capture native basis must preserve the legacy public Snapshot schema"); }
  const ids = new Set(value.reads.map(read => read.instance_id));
  if (ids.size !== value.reads.length) issue("Read instances must be unique");
  if (value.reads.some(read => read.parent_read_instance_id !== null && !ids.has(read.parent_read_instance_id)) ||
      value.coverage.some(item => item.read_instance_ids.some(id => !ids.has(id)))) issue("dangling Read-instance relation");
  if (new Set(value.coverage.map(item => item.requirement_id)).size !== value.coverage.length) issue("duplicate coverage requirement");
  for (const required of P1_CAPTURE_SCOPE_REQUIREMENTS)
    if (!value.coverage.some(item => item.requirement_id === required && item.required)) issue("missing required capture scope coverage");
  for (const gap of P1_CAPTURE_FULL_MISSING) {
    const item = value.coverage.find(item => item.requirement_id === gap);
    if (!item || !item.required || item.availability !== "unsupported" || !item.missing.includes(gap))
      issue("C1 cannot claim the full P1 requirement is implemented");
  }
  const covered = new Set(value.coverage.filter(item => item.required).flatMap(item => item.read_instance_ids));
  if (value.reads.some(read => !covered.has(read.instance_id)))
    issue("every Read instance must have required coverage");
  if (value.scope_status === "complete" && (value.reads.some(read => read.availability !== "available" || read.missing.length !== 0) ||
      value.coverage.some(item => !(P1_CAPTURE_FULL_MISSING as readonly string[]).includes(item.requirement_id) && item.required &&
        (item.availability !== "available" || item.missing.length !== 0)))) issue("complete capture scope has missing required facts");
  if (value.scope_status === "complete") {
    const expectedDetails: string[] = [];
    const sourceCards = new Map<string, JsonObject[]>();
    const key = (entity: string | null, context: string, parent: string | null) => JSON.stringify([entity, context, parent]);
    const expectCard = (card: z.infer<typeof cardContentSchema>, context: string, parent: string | null) => {
      const display = key(card.entity_id, context, parent);
      expectedDetails.push(display);
      const existing = sourceCards.get(display) ?? [];
      if (existing.some(source => !compatibleSourceCards(source, card)))
        issue("same-context source cards contradict the same entity instance");
      sourceCards.set(display, [...existing, card]);
    };
    const sameIds = (left: readonly string[], right: readonly string[]) =>
      JSON.stringify([...left].sort()) === JSON.stringify([...right].sort());
    for (const kind of ["run_deck", "combat_piles"]) {
      const aggregate = value.reads.filter(read => read.kind === kind && read.availability === "available");
      const coverage = value.coverage.find(item => item.requirement_id === kind);
      if (aggregate.length !== 1 || !coverage || !sameIds(coverage.read_instance_ids, aggregate.map(read => read.instance_id)))
        issue("complete capture scope must bind each required aggregate Read");
      for (const read of aggregate) {
        if (kind === "run_deck") {
          const deck = deckContentSchema.safeParse(read.content);
          if (deck.success) for (const card of deck.data.cards) expectCard(card, "deck", read.instance_id);
        } else {
          const piles = pileContentSchema.safeParse(read.content);
          if (piles.success) for (const zone of piles.data.zones)
            for (const card of zone.cards) expectCard(card, zone.zone, read.instance_id);
        }
      }
    }
    const interaction = value.native_basis.interaction;
    const context = combatContextSchema.safeParse(isJsonObject(interaction) && isJsonObject(interaction.content)
      ? interaction.content.context : null);
    if (!context.success) issue("complete capture scope lacks the typed current parent combat context");
    else {
      const persistent = value.native_basis.persistent;
      if (isJsonObject(persistent) && isJsonObject(persistent.content) && isJsonObject(persistent.content.player) &&
          context.data.player.player_entity_id !== persistent.content.player.entity_id)
        issue("current combat player does not match the persistent player");
      for (const read of value.reads.filter(read => read.kind === "combat_piles")) {
        const piles = pileContentSchema.safeParse(read.content);
        if (piles.success) for (const zone of piles.data.zones) {
          const count = context.data.player[`${zone.zone}_pile_count` as "draw_pile_count" | "discard_pile_count" | "exhaust_pile_count"];
          if (count !== zone.card_count) issue("current combat pile count contradicts its aggregate Read");
        }
      }
      for (const card of context.data.player.hand) expectCard(card, "hand", null);
    }
    const details = value.reads.filter(read => read.kind === "surface_card");
    const observedDetails = new Map<string, JsonObject[]>();
    for (const detail of details) {
      const display = key(detail.target_entity_id, detail.display_context, detail.parent_read_instance_id);
      if (isJsonObject(detail.content)) {
        const existing = observedDetails.get(display) ?? [];
        if (existing.some(content => !compatibleSourceCards(content, detail.content as JsonObject)))
          issue("same-context details contradict the same entity instance");
        observedDetails.set(display, [...existing, detail.content]);
      }
      const sources = sourceCards.get(display);
      const match = isJsonObject(detail.content)
        ? sources?.findIndex(source => preservesSourceCard(source, detail.content as JsonObject)) : -1;
      if (detail.source_read_id !== null || match === undefined || match < 0)
        issue("card detail contradicts its same-context source card");
      else sources!.splice(match, 1);
    }
    const detailedCoverage = value.coverage.find(item => item.requirement_id === "surface_card_instances");
    if (!sameIds(expectedDetails, details.map(read => key(read.target_entity_id, read.display_context, read.parent_read_instance_id))) ||
        !detailedCoverage || !sameIds(detailedCoverage.read_instance_ids, details.map(read => read.instance_id)))
      issue("complete capture scope omits or misbinds a required card display instance");
  }
});
const retentionSchema = z.object({
  schema: z.literal(P1_CAPTURE_CAPABILITIES_SCHEMA), read_profile: z.literal(P1_CAPTURE_READ_PROFILE),
  input_profile: z.literal(P1_CAPTURE_PROFILE), capture_schema: z.literal(P1_CAPTURE_HANDLE_SCHEMA),
  chunk_schema: z.literal(P1_CAPTURE_CHUNK_SCHEMA), release_schema: z.literal(P1_CAPTURE_RELEASE_SCHEMA),
  current_route: z.literal(`${P1_CAPTURE_ROUTE}/current`), read_route: z.literal(`${P1_CAPTURE_ROUTE}/read`),
  release_route: z.literal(`${P1_CAPTURE_ROUTE}/release`), max_capsule_bytes: z.literal(SEALED_OBSERVATION_MAX_BYTES),
  max_retained_bytes: z.literal(64 * 1024 * 1024), max_capsules: z.literal(32), ttl_ms: z.literal(120_000),
  default_chunk_bytes: z.literal(SEALED_OBSERVATION_DEFAULT_CHUNK_BYTES),
  min_chunk_bytes: z.literal(SEALED_OBSERVATION_MIN_CHUNK_BYTES), max_chunk_bytes: z.literal(SEALED_OBSERVATION_MAX_CHUNK_BYTES),
  creates_mutation_authority: z.literal(false), scope: z.literal(P1_CAPTURE_SCOPE)
}).strict();
const capabilitiesSchema = z.object({
  schema: z.literal(P1_CAPTURE_CAPABILITIES_SCHEMA), input_profile: z.literal(P1_CAPTURE_PROFILE),
  revision: captureRevision, supported_capture_scope: z.literal(P1_CAPTURE_SCOPE),
  agent_admissible: z.literal(false), creates_mutation_authority: z.literal(false),
  native_catalog_semantics: z.literal(P1_CAPTURE_NATIVE_CATALOG_SEMANTICS),
  max_read_instances: z.literal(P1_CAPTURE_MAX_READ_INSTANCES),
  required_full_p1_missing: z.array(z.string()).refine(value =>
    value.length === P1_CAPTURE_FULL_MISSING.length && P1_CAPTURE_FULL_MISSING.every(gap => value.includes(gap))),
  retention: retentionSchema
}).strict();

export type P1CaptureHandle = z.infer<typeof handleSchema>;
export type P1CaptureChunk = z.infer<typeof chunkSchema>;
export type P1CaptureRelease = z.infer<typeof releaseSchema>;
export type P1CaptureCapabilities = z.infer<typeof capabilitiesSchema>;
export type P1CapturedObservation = z.infer<typeof observationSchema>;
export interface FullP1Capture {
  capture: P1CaptureHandle;
  /** Exact source public UTF-8 bytes represented as text. Not a reconstructed projection. */
  serializedObservation: string;
  observation: P1CapturedObservation;
}
export interface P1CaptureReader {
  readP1Capture(input: { captureId: string; cursor: string; maxBytes?: number }): Promise<DecodedPlayerPayload<P1CaptureChunk>>;
}
function decode<T>(value: unknown, schema: z.ZodType<T>, label: string): DecodedPlayerPayload<T> {
  if (!isJsonObject(value)) throw new Error(`${label} is not an object`);
  const result = schema.safeParse(value);
  if (!result.success) throw new Error(`${label} failed strict decoding: ${result.error.message}`);
  return { raw: value, data: result.data };
}
export const decodeP1CaptureHandle = (value: unknown) => decode(value, handleSchema, "P1 C1 capture handle");
export const decodeP1CaptureChunk = (value: unknown) => decode(value, chunkSchema, "P1 C1 capture chunk");
export const decodeP1CaptureRelease = (value: unknown) => decode(value, releaseSchema, "P1 C1 capture release");
export const decodeP1CaptureCapabilities = (value: unknown) => decode(value, capabilitiesSchema, "P1 C1 capture capabilities");
export const decodeP1CapturedObservation = (value: unknown) => decode(value, observationSchema, "P1 C1 observation");

async function verifyP1Capture(capture: P1CaptureHandle, serializedObservation: string): Promise<FullP1Capture> {
  const bytes = new TextEncoder().encode(serializedObservation);
  if (bytes.length !== capture.total_bytes || await publicBytesSha256(bytes) !== capture.sha256)
    throw new Error("P1 capture byte count or digest mismatch");
  const observation = decodeP1CapturedObservation(JSON.parse(serializedObservation)).data;
  if (observation.source_snapshot_id !== capture.source_snapshot_id ||
      observation.session.runtime_instance_id !== capture.session.runtime_instance_id ||
      observation.session.environment_fingerprint !== capture.session.environment_fingerprint ||
      Date.parse(observation.captured_at) !== Date.parse(capture.captured_at))
    throw new Error("P1 capture source/session mismatch");
  return { capture, serializedObservation, observation };
}

/** Transport, recording and comparison share this one frozen payload. No Actor admission/input. */
export async function assembleP1Capture(capture: P1CaptureHandle, reader: P1CaptureReader,
  maxBytes = SEALED_OBSERVATION_DEFAULT_CHUNK_BYTES): Promise<FullP1Capture> {
  capture = decodeP1CaptureHandle(capture).data;
  validateSealedChunkLimit(maxBytes);
  const bytes = await assembleRetainedBytes(capture, async input =>
    decodeP1CaptureChunk((await reader.readP1Capture(input)).data).data, maxBytes);
  return verifyP1Capture(capture, new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes));
}

/** Archival copy of the exact same bytes; does not retain/extend a live cursor.
 * Existing application storage can write this string without a second capture. */
export function serializeP1CaptureRecord(full: FullP1Capture): string {
  return JSON.stringify({ schema: P1_CAPTURE_RECORD_SCHEMA,
    capture: decodeP1CaptureHandle(full.capture).data, serialized_observation: full.serializedObservation });
}

/** Read a saved capture without any native/HTTP access or inference. Expired
 * live handles stay expired; archived public bytes remain independently verified. */
export async function readP1CaptureRecord(serializedRecord: string): Promise<FullP1Capture> {
  if (new TextEncoder().encode(serializedRecord).length > 6 * SEALED_OBSERVATION_MAX_BYTES + 16_384)
    throw new Error("P1 capture record exceeds its bound");
  const record = z.object({ schema: z.literal(P1_CAPTURE_RECORD_SCHEMA), capture: handleSchema,
    serialized_observation: z.string().min(1).max(SEALED_OBSERVATION_MAX_BYTES) }).strict().parse(JSON.parse(serializedRecord));
  return verifyP1Capture(record.capture, record.serialized_observation);
}
