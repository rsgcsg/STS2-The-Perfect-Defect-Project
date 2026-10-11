import { createHash, randomUUID } from "node:crypto";
import { canonicalJson } from "./evidence.js";
import type { PolicyDecisionInput, PublicObservationCompletion, PublicPreviousAction, PublicStatefulPolicyDecisionInput } from "./contracts.js";

/** Ordinals describe actual qualified Snapshot inputs, not reads or native successors. */
export class PublicObservationTracker {
  private segment: {
    token: string; episodeId: string; segmentId: string;
    runtimeId: string; environment: string; ordinal: number;
    snapshotId: string; sequence: number; observationDigest: string; candidateDigest: string;
    previousAction: PublicPreviousAction | null;
  } | null = null;

  reset(): void { this.segment = null; }

  prepare(decision: PolicyDecisionInput): PublicStatefulPolicyDecisionInput {
    const snapshot = decision.bundle.observation;
    if (snapshot.schema !== "sts2.player-environment/snapshot-1" || decision.bundle.reads.length !== 0
        || !Number.isSafeInteger(snapshot.sequence) || snapshot.sequence < 0) {
      throw new Error("public observation requires generic Snapshot1 without Reads");
    }
    const { observed_at: _pollTime, ...body } = snapshot;
    const digest = createHash("sha256").update(canonicalJson(body)).digest("hex");
    const previous = this.segment;
    if (!previous) {
      const segmentId = `segment-${randomUUID()}`;
      this.segment = { token: randomUUID(), episodeId: segmentId, segmentId,
        runtimeId: snapshot.session.runtime_instance_id,
        environment: snapshot.session.environment_fingerprint, ordinal: 1,
        snapshotId: snapshot.snapshot_id, sequence: snapshot.sequence,
        observationDigest: digest, candidateDigest: decision.candidate_digest, previousAction: null };
    } else {
      if (previous.runtimeId !== snapshot.session.runtime_instance_id
          || previous.environment !== snapshot.session.environment_fingerprint) {
        throw new Error("public observation environment changed within segment");
      }
      if (snapshot.snapshot_id === previous.snapshotId && snapshot.sequence === previous.sequence) {
        if (digest !== previous.observationDigest || decision.candidate_digest !== previous.candidateDigest)
          throw new Error("same public observation watermark changed");
      } else {
        if (snapshot.sequence <= previous.sequence) throw new Error("public observation sequence is not ascending");
        previous.ordinal += 1;
        previous.snapshotId = snapshot.snapshot_id;
        previous.sequence = snapshot.sequence;
        previous.observationDigest = digest;
        previous.candidateDigest = decision.candidate_digest;
      }
    }
    const current = this.segment!;
    return { decision: { ...decision, bundle: { observation: snapshot, reads: [] } }, control: {
      continuity_token: current.token, episode_scope: "bounded_policy_segment",
      episode_id: current.episodeId, segment_id: current.segmentId,
      observation_ordinal: current.ordinal, previous_action: structuredClone(current.previousAction)
    } };
  }

  offered(input: PublicStatefulPolicyDecisionInput): void {
    if (this.segment?.token === input.control.continuity_token && input.control.previous_action)
      this.segment.previousAction = null;
  }

  remember(previous: PublicPreviousAction): void {
    if (this.segment) this.segment.previousAction = structuredClone(previous);
  }
}

export function validatePublicObservationCompletion(value: unknown,
  input: PublicStatefulPolicyDecisionInput): PublicObservationCompletion {
  if (value === null || typeof value !== "object" || Array.isArray(value))
    throw new Error("public observation completion must be an object");
  const completion = value as Record<string, unknown>;
  const expected = {
    continuity_token: input.control.continuity_token,
    episode_id: input.control.episode_id, segment_id: input.control.segment_id,
    observation_ordinal: input.control.observation_ordinal,
    snapshot_id: input.decision.bundle.observation.snapshot_id,
    sequence: input.decision.bundle.observation.sequence,
    previous_action_request_id: input.control.previous_action?.request_id ?? null
  };
  if (canonicalJson(completion) !== canonicalJson(expected))
    throw new Error("public observation completion watermark mismatch");
  return completion as unknown as PublicObservationCompletion;
}
