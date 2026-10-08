import {
  AgentSessionError, NATIVE_SCOPE, sessionDigest, sessionInteger, sessionObject,
  sessionText, supportsProfileValue, validateAgentConsumption, validateAgentManifest, validateAgentScope,
  type AgentConsumeAck, type AgentManifest, type AgentPrefix,
  type AgentScope
} from "./agent-session-contracts.js";
import { agentJsonByteLength, encodeBoundedAgentJson } from "./agent-session-json.js";
import { AgentByteBudget, type AgentByteReservation } from "./agent-session-budget.js";

/** SDK-decoded acquisition; this ledger is not a replacement native-wire verifier. */
export interface AgentAcquisition {
  acquisition_id: string;
  capture: Record<string, unknown>;
  observation: Record<string, unknown>;
  catalog: readonly Record<string, unknown>[] | null;
  publication_index: string | null;
}
interface Known {
  acquisition: AgentAcquisition;
  occurrence: string;
  revision: number;
  included: AgentScope[];
  facts: Partial<Record<AgentScope, string>>;
  bytes: number;
  reservation: AgentByteReservation;
}
function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map(key => `${JSON.stringify(key)}:${stable(record[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}
function freeze<T>(value: T): T {
  if (value !== null && typeof value === "object") {
    Object.freeze(value);
    for (const item of Object.values(value)) freeze(item);
  }
  return value;
}

export class AgentConsumptionLedger {
  private readonly acquisitions = new Map<string, Known>();
  private readonly coherence = new Map<string, Partial<Record<AgentScope, string>>>();
  private readonly factReservations = new Map<string, AgentByteReservation[]>();
  private currentOccurrence: string | null = null;
  private currentRevision = -1;
  private consumedScopes = new Set<AgentScope>();
  private lastConsumptionId: string | null = null;
  private currentVersion = 0;
  private lastPublication = -1n;
  private lastConsumedPublication: string | null = null;
  private receivedCursor: string | null = null;
  private gap: Record<string, unknown> | null = null;
  private requiredGapPending = false;
  private resetBasisAllowed = false;
  private missingScopes: AgentScope[] = [];
  private receivedUnconsumed = 0;
  private readonly consumptionIds = new Set<string>();
  private continuity: string;

  constructor(readonly manifest: AgentManifest, continuityToken: string,
    readonly byteBudget = new AgentByteBudget(manifest.limits.max_retained_acquisition_bytes)) {
    validateAgentManifest(manifest);
    this.continuity = sessionText(continuityToken);
  }

  get stateVersion(): number { return this.currentVersion; }
  get consumptionId(): string | null { return this.lastConsumptionId; }
  get continuityToken(): string { return this.continuity; }

  register(value: AgentAcquisition): void {
    sessionObject(value, ["acquisition_id", "capture", "observation", "catalog", "publication_index"]);
    sessionText(value.acquisition_id);
    if (this.acquisitions.has(value.acquisition_id)) throw new AgentSessionError("duplicate_acquisition_id");
    if (this.acquisitions.size >= this.manifest.limits.max_acquisitions) throw new AgentSessionError("acquisition_capacity");
    const bytes = agentJsonByteLength(value, this.manifest.limits.max_message_bytes);
    const reservation = this.byteBudget.reserve(bytes);
    let scratch: AgentByteReservation | undefined;
    try {
      scratch = this.byteBudget.reserve(bytes);
      const encoded = encodeBoundedAgentJson(value, this.manifest.limits.max_message_bytes);
      this.registerReserved(value, encoded, reservation, scratch);
    } catch (error) { reservation.release(); scratch?.release(); throw error; }
  }

  private registerReserved(value: AgentAcquisition, encoded: Buffer, reservation: AgentByteReservation, scratch: AgentByteReservation): void {
    const capture = sessionObject(value.capture), observation = sessionObject(value.observation);
    if (capture.schema !== "sts2.player-environment/native-logical-capture-1"
      || observation.schema !== "sts2.player-environment/native-logical-observation-1"
      || capture.input_profile !== "native-logical-v1" || observation.input_profile !== "native-logical-v1")
      throw new AgentSessionError("native_profile_mismatch");
    sessionText(capture.capture_id); sessionText(capture.snapshot_id); sessionText(capture.stream_generation);
    sessionDigest(capture.sha256); sessionInteger(capture.byte_count, true, this.manifest.limits.max_capture_bytes);
    if (observation.snapshot_id !== capture.snapshot_id || stable(capture.session) !== stable(observation.session))
      throw new AgentSessionError("capture_observation_binding");
    const session = sessionObject(observation.session, ["runtime_instance_id", "environment_fingerprint"]);
    sessionText(session.runtime_instance_id); sessionText(session.environment_fingerprint);
    const revision = sessionInteger(observation.revision);
    const owner = sessionObject(observation.owner_occurrence);
    sessionText(owner.owner_id); sessionText(owner.occurrence_id); sessionText(owner.binding_revision);
    if (owner.focus_occurrence !== null) sessionText(owner.focus_occurrence);
    if (owner.focus_referent_id !== null) sessionText(owner.focus_referent_id, 65_536);
    if (!["interactive", "settling", "terminal"].includes(String(observation.status)))
      throw new AgentSessionError("native_frame_not_eligible");
    const completeness = sessionObject(observation.completeness);
    const included = validateAgentScope(completeness.included);
    const missing = validateAgentScope(completeness.missing);
    if (!["complete", "partial"].includes(String(completeness.status))
      || included.some(field => missing.includes(field))
      || NATIVE_SCOPE.some(field => !included.includes(field) && !missing.includes(field)))
      throw new AgentSessionError("native_completeness_invalid");
    const descriptor = sessionObject(observation.catalog);
    if (descriptor.snapshot_id !== capture.snapshot_id || descriptor.stream_generation !== capture.stream_generation
      || descriptor.scope_id !== capture.scope_id) throw new AgentSessionError("catalog_capture_binding");
    sessionText(descriptor.catalog_ref);
    const facts: Partial<Record<AgentScope, string>> = {};
    for (const field of included) {
      if (field === "catalog") {
        if (descriptor.status !== "complete" || !Array.isArray(value.catalog)) throw new AgentSessionError("complete_catalog_required");
        const count = sessionInteger(descriptor.total_count, false, this.manifest.limits.max_catalog_actions);
        sessionDigest(descriptor.digest);
        if (count !== value.catalog.length) throw new AgentSessionError("catalog_count_binding");
        const ids = value.catalog.map(action => sessionText(sessionObject(action).action_id, 65_536));
        if (new Set(ids).size !== ids.length) throw new AgentSessionError("duplicate_action_id");
        if (value.catalog.some(action => !supportsProfileValue(this.manifest.support.action_verbs,
          sessionText(sessionObject(action).verb, 65_536)))) throw new AgentSessionError("unsupported_action_verb");
        facts.catalog = stable({ digest: descriptor.digest, total_count: descriptor.total_count,
          ordering_semantics: descriptor.ordering_semantics, actions: value.catalog });
      } else {
        if (field === "referents") {
          if (!Array.isArray(observation.referents)) throw new AgentSessionError("native_referents_required");
        } else if (!(field === "persistent" && Object.hasOwn(observation, field) && observation[field] === null)
          && (observation[field] === null || typeof observation[field] !== "object" || Array.isArray(observation[field]))) {
          throw new AgentSessionError(`native_${field}_required`);
        }
        if (field === "interaction") {
          const interaction = sessionObject(observation.interaction);
          sessionText(interaction.kind); sessionText(interaction.stage);
          sessionObject(interaction.content);
          if (!supportsProfileValue(this.manifest.support.interaction_kinds, String(interaction.kind)))
            throw new AgentSessionError("unsupported_interaction");
        }
        facts[field] = stable(observation[field]);
      }
    }
    if (this.manifest.input.history_mode === "full_reference"
      && (included.length !== 4 || completeness.status !== "complete"
        || completeness.full_reference_complete !== true || missing.length !== 0))
      throw new AgentSessionError("full_reference_input_required");
    if (value.publication_index !== null && !/^(0|[1-9][0-9]*)$/u.test(value.publication_index))
      throw new AgentSessionError("invalid_publication_index");
    const occurrence = stable([capture.stream_generation, observation.snapshot_id,
      owner.occurrence_id, owner.binding_revision, owner.focus_occurrence]);
    const previousFacts = this.coherence.get(occurrence) ?? {};
    for (const field of included) {
      if (previousFacts[field] !== undefined && previousFacts[field] !== facts[field])
        throw new AgentSessionError("same_occurrence_coherence_drift");
      if (previousFacts[field] !== undefined) facts[field] = previousFacts[field];
    }
    const addedFactBytes = included.reduce((sum, field) => sum + (previousFacts[field] === undefined ? Buffer.byteLength(facts[field]!, "utf8") : 0), 0);
    if (addedFactBytes > scratch.bytes) throw new AgentSessionError("canonical_fact_byte_bound");
    scratch.release();
    const factReservation = this.byteBudget.reserve(addedFactBytes);
    // Complete SDK-decoded bytes are bounded before cloning this retained input.
    let cloned: AgentAcquisition;
    try { cloned = JSON.parse(encoded.toString("utf8")) as AgentAcquisition; }
    catch (error) { factReservation.release(); throw error; }
    this.acquisitions.set(value.acquisition_id, { acquisition: freeze(cloned), occurrence, revision, included, facts, bytes: encoded.length, reservation });
    this.coherence.set(occurrence, { ...previousFacts, ...facts });
    const reservations = this.factReservations.get(occurrence) ?? [];
    reservations.push(factReservation); this.factReservations.set(occurrence, reservations);
  }

  get(acquisitionId: string): AgentAcquisition {
    const known = this.acquisitions.get(acquisitionId);
    if (!known) throw new AgentSessionError("unknown_acquisition");
    return known.acquisition;
  }

  release(acquisitionId: string): void {
    const known = this.acquisitions.get(acquisitionId);
    if (!known) throw new AgentSessionError("unknown_acquisition");
    this.acquisitions.delete(acquisitionId);
    known.reservation.release();
    this.dropUnreferencedFacts(known.occurrence);
  }

  noteReceived(cursor: string, eligibleObservations = 0): void {
    this.receivedCursor = sessionText(cursor, 1024);
    this.receivedUnconsumed = sessionInteger(this.receivedUnconsumed + sessionInteger(eligibleObservations));
  }

  accept(value: unknown): AgentConsumeAck {
    const report = validateAgentConsumption(value);
    if (stable(report.input_spec) !== stable(this.manifest.input.input_spec)
      || report.continuity_token !== this.continuity) throw new AgentSessionError("consumption_identity_binding");
    if (report.previous_consumption_id !== this.lastConsumptionId) throw new AgentSessionError("consumption_prefix_binding");
    const known = this.acquisitions.get(report.acquisition_id);
    if (!known) throw new AgentSessionError("unknown_acquisition");
    if (known.revision < this.currentRevision) throw new AgentSessionError("native_revision_regressed");
    const newOccurrence = known.occurrence !== this.currentOccurrence;
    if (newOccurrence && known.revision <= this.currentRevision) throw new AgentSessionError("native_revision_not_newer");
    const added = known.included.filter(field => !this.consumedScopes.has(field));
    const advanced = newOccurrence || (this.manifest.input.consumption_mode === "incremental_view" && added.length > 0);
    if (report.advanced !== advanced) throw new AgentSessionError("consumption_unit_advance_mismatch");
    if (report.state_version !== this.currentVersion + (advanced ? 1 : 0)) throw new AgentSessionError("consumption_state_version");
    if ((!advanced && report.consumption_id !== this.lastConsumptionId)
      || (advanced && this.consumptionIds.has(report.consumption_id))) throw new AgentSessionError("consumption_id_reused_or_changed");
    if (advanced && this.currentVersion === Number.MAX_SAFE_INTEGER) throw new AgentSessionError("state_version_exhausted");
    if (advanced && this.consumptionIds.size >= 65_536) throw new AgentSessionError("consumption_id_capacity");
    const publication = known.acquisition.publication_index;
    const newPublication = publication !== null && BigInt(publication) > this.lastPublication;
    if (this.manifest.input.history_mode === "full_reference") {
      if (this.requiredGapPending || (publication === null && advanced && !this.resetBasisAllowed)) throw new AgentSessionError("full_reference_gap");
      if (publication !== null && BigInt(publication) < this.lastPublication) throw new AgentSessionError("publication_regressed");
      // Runtime feeds promised occurrences serially; the source cursor is not inferred here.
    }
    // No mutation before all report bindings, mode, ordering and version checks pass.
    if (advanced) {
      const previousOccurrence = this.currentOccurrence;
      if (newOccurrence) this.consumedScopes = new Set();
      for (const field of known.included) this.consumedScopes.add(field);
      this.currentOccurrence = known.occurrence;
      this.currentRevision = known.revision;
      this.currentVersion = report.state_version;
      this.lastConsumptionId = report.consumption_id;
      this.consumptionIds.add(report.consumption_id);
      this.resetBasisAllowed = false;
      if (previousOccurrence !== null) this.dropUnreferencedFacts(previousOccurrence);
    }
    if (advanced || newPublication) this.lastConsumedPublication = publication;
    // A source position is consumed once even if its same-unit report is replayed.
    // A new position can still be acknowledged without another Model/state advance.
    if (newPublication) {
      this.lastPublication = BigInt(publication!);
      this.receivedUnconsumed = Math.max(0, this.receivedUnconsumed - 1);
    }
    const completeness = sessionObject(known.acquisition.observation.completeness);
    this.missingScopes = validateAgentScope(completeness.missing);
    return freeze({ consumption_id: report.consumption_id, acquisition_id: report.acquisition_id,
      state_version: report.state_version, advanced, prefix: this.prefix() });
  }

  prefix(): AgentPrefix {
    return { continuity_token: this.continuity, history_mode: this.manifest.input.history_mode,
      consumption_mode: this.manifest.input.consumption_mode, received_cursor: this.receivedCursor,
      consumed_publication_index: this.lastConsumedPublication,
      omissions: { received_unconsumed_count: this.gap === null ? this.receivedUnconsumed : null,
        missing_scopes: [...this.missingScopes], gap: this.gap === null ? null : { ...this.gap } } };
  }

  recordGap(gap: Record<string, unknown>): void {
    this.gap = freeze(JSON.parse(encodeBoundedAgentJson(gap, 4096).toString("utf8")) as Record<string, unknown>);
    this.requiredGapPending = true;
  }

  explicitReset(newContinuity: string): void {
    if (this.manifest.input.gap_policy !== "explicit_reset" || this.gap === null)
      throw new AgentSessionError("reset_requires_declared_gap");
    if (newContinuity === this.continuity) throw new AgentSessionError("fresh_continuity_required");
    this.continuity = sessionText(newContinuity);
    this.currentOccurrence = null; this.currentRevision = -1;
    this.consumedScopes.clear(); this.lastConsumptionId = null; this.currentVersion = 0;
    this.lastPublication = -1n; this.lastConsumedPublication = null;
    for (const known of this.acquisitions.values()) known.reservation.release();
    for (const reservations of this.factReservations.values()) for (const reservation of reservations) reservation.release();
    this.acquisitions.clear(); this.coherence.clear(); this.consumptionIds.clear(); this.factReservations.clear();
    this.requiredGapPending = false;
    this.resetBasisAllowed = true;
    // The gap remains in evidence/prefix. Reset cannot restore full-reference history.
  }

  private dropUnreferencedFacts(occurrence: string): void {
    if (occurrence === this.currentOccurrence || [...this.acquisitions.values()].some(value => value.occurrence === occurrence)) return;
    for (const reservation of this.factReservations.get(occurrence) ?? []) reservation.release();
    this.factReservations.delete(occurrence);
    this.coherence.delete(occurrence);
  }
}
