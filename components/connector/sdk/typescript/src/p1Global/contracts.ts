/**
 * P1 global profile (r1): the one Agent-facing STS2 decision protocol built over the
 * Connector Player Environment contract (snapshot-1, Reads, bound-actions-1, receipt-1),
 * which both the Reference game Mod and the Managed Host already implement.
 *
 * The profile adds no game rules and no legality: every P1 choice lowers to bound
 * actions taken from the Host's current complete catalog, revalidated by the Host at
 * execution. It adds: one aggregated public observation, stable public aliases, a
 * semantic choice catalog, declared mechanical folds, declared forced inputs, explicit
 * irreversible P1 commitments, occurrence accounting and a versioned record/convert path.
 */
export const P1G_PROFILE = "p1-global-r1" as const;
export const P1G_REVISION = "r1.1" as const;
export const P1G_OBSERVATION_SCHEMA = "sts2.p1/observation-1" as const;
export const P1G_RESULT_SCHEMA = "sts2.p1/result-1" as const;
export const P1G_PORT_PROTOCOL = "sts2.p1/agent-port-1" as const;
export const P1G_RECORD_SCHEMA = "sts2.p1/episode-record-1" as const;

/** Distinct meanings; "empty" content is never used to stand in for any of these. */
export type Availability =
  | "available"          // captured from the current native public state
  | "not_applicable"     // this part does not exist in the current scene (e.g. combat on the map)
  | "native_unavailable" // the game itself does not offer this information at this point
  | "unsupported"        // the game offers it, but this Host/profile does not capture it yet
  | "unknown"            // capture failed; never published as a complete observation
  | "terminal";          // the run has ended

export type SceneFamily =
  | "combat" | "map" | "reward" | "card_reward" | "selector" | "upgrade" | "enchant" | "shop"
  | "event" | "rest" | "treasure" | "relic_choice" | "other" | "terminal" | "unsupported";

/** Families whose observations may be published. Information completeness is the Host's
 * declared snapshot/Read completeness plus the shared structural validators below; a
 * family is not withheld merely because it lacks a dedicated validator. Only scenes the
 * Host itself reports unsupported stay unpublished. */
export const P1G_INFORMATION_SHAPES: readonly SceneFamily[] = Object.freeze(["combat", "map", "selector", "reward", "card_reward",
  "upgrade", "enchant", "shop", "event", "rest", "treasure", "relic_choice", "other", "terminal"] as SceneFamily[]);

export type HostKind = "reference" | "managed";

export interface P1Envelope {
  /** Not model input: binding and de-duplication only. */
  readonly episode_id: string;
  readonly occurrence: number;
  readonly host_kind: HostKind;
  readonly source_snapshot_id: string;
  readonly source_sequence: number;
  /** Required public information passes structural admission; not joint native capture or qualification. */
  readonly required_information_complete: boolean;
  /** Exact capture references; binding metadata removed by modelView. */
  readonly read_instances: readonly { readonly read_id: string; readonly kind: string; readonly target_referent_id: string | null }[];
}

export interface P1Part<T> { readonly availability: Availability; readonly content: T | null; readonly source: string | null }

export interface P1Choice {
  readonly choice_id: string;
  /** Semantic kind from the declared table; unmapped native actions stay offered as "native.*". */
  readonly kind: string;
  readonly subject: string | null;
  readonly target: string | null;
  readonly label: string;
  readonly mapped: boolean;
  /** Public native alternative type within the named reward group, never a binding token. */
  readonly option?: string;
}

export interface P1Commitments {
  /** Irreversible P1 reward decisions on the current reward page (group keys). */
  readonly declined_reward_groups: readonly string[];
  /** True once the Agent left the current reward page; remaining rewards were declined. */
  readonly reward_page_left: boolean;
  readonly reward_dispositions?: readonly { readonly group: string; readonly selection_cleanup: "delivered"; readonly native_disposition: "pending_owner_skip" }[];
}

export interface P1Observation {
  readonly schema: typeof P1G_OBSERVATION_SCHEMA;
  readonly profile: typeof P1G_PROFILE;
  readonly revision: typeof P1G_REVISION;
  readonly envelope: P1Envelope;
  readonly status: "decision" | "terminal" | "unsupported";
  readonly scene: {
    readonly family: SceneFamily; readonly native_kind: string; readonly content_schema: string; readonly stage: string;
    readonly prompt: string | null; readonly content: unknown;
  };
  readonly run: P1Part<unknown>;
  readonly player: P1Part<unknown>;
  readonly deck: P1Part<unknown>;
  readonly map: P1Part<unknown>;
  readonly combat: P1Part<unknown>;
  readonly piles: P1Part<unknown>;
  readonly extra_reads: readonly { readonly kind: string; readonly availability: Availability; readonly content: unknown }[];
  readonly commitments: P1Commitments;
  readonly hidden_by_policy: readonly string[];
  readonly catalog: readonly P1Choice[];
  readonly terminal: { readonly outcome: "win" | "loss" | "unknown" } | null;
}

export type P1ResultStatus = "completed" | "interrupted_known" | "not_started" | "unknown";

export interface P1StepRecord {
  readonly index: number; readonly origin: "agent_choice" | "fold" | "forced";
  readonly native_verb: string; readonly label: string;
  readonly delivery: "delivered" | "not_delivered" | "unknown";
  readonly reason_code: string | null;
}

export interface P1Result {
  readonly schema: typeof P1G_RESULT_SCHEMA;
  readonly occurrence: number;
  readonly choice_id: string;
  readonly status: P1ResultStatus;
  readonly steps: readonly P1StepRecord[];
  readonly commitment_delta: Partial<P1Commitments> | null;
}

/** Declared limits; exceeding them is an explicit failure, never truncation. */
export const P1G_LIMITS = Object.freeze({
  observation_bytes: 8 * 1024 * 1024,
  catalog_choices: 4096,
  macro_steps: 8,
  forced_steps_in_a_row: 64
});
