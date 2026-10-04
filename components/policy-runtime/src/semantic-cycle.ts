import { createHash } from "node:crypto";
import type { PlayerEnvironmentSnapshot } from "@rsgcsg/sts2-connector-client";
import { canonicalJson } from "./evidence.js";

/**
 * Conservative public-state projection. Only Connector envelope/handle fields
 * are removed. Opaque persistent, surface, context and referent facts are kept
 * verbatim, including any IDs in those facts that may carry business meaning.
 */
export function publicDecisionFingerprint(snapshot: PlayerEnvironmentSnapshot): string {
  const referents = snapshot.referents.map(({ referent_id: _id, ...fact }) => fact);
  const identities = new Map(snapshot.referents.map((referent) => {
    const { referent_id: _id, ...fact } = referent;
    return [referent.referent_id, canonicalJson(fact)] as const;
  }));
  const referent = (id: string | null | undefined): string | null =>
    id == null ? null : identities.get(id) ?? `unresolved:${id}`;
  const actions = snapshot.bound_actions.actions.map(({
    bound_action_id: _actionId, interaction_id: _interactionId, subject_referent_id, arguments: args, ...fact
  }) => ({ ...fact, subject_referent_id: referent(subject_referent_id),
    arguments: args.map(({ role, referent_id }) => ({ role, referent: referent(referent_id) })) }));
  const { snapshot_id: _snapshotId, sequence: _sequence, observed_at: _observedAt,
    session: _session, interaction, bound_actions, reads, ...stable } = snapshot;
  const { interaction_id: _interactionId, ...interactionFact } = interaction;
  const projected = {
    ...stable, interaction: interactionFact, referents: referents.map(canonicalJson).sort(),
    bound_actions: { ...bound_actions, actions: actions.map(canonicalJson).sort() },
    reads: reads.map(({ read_id: _readId, target_referent_id, ...readFact }) =>
      canonicalJson({ ...readFact, target_referent_id: referent(target_referent_id) })).sort()
  };
  return createHash("sha256").update(canonicalJson(projected)).digest("hex");
}

/** Three completed repetitions, with at most seven retained states. */
export class SemanticCycleGuard {
  private states: string[] = [];

  reset(): void { this.states = []; }

  observeDelivered(source: PlayerEnvironmentSnapshot, successor: PlayerEnvironmentSnapshot):
      { period: 1 | 2; deliveries: number; fingerprint: string } | null {
    const from = publicDecisionFingerprint(source);
    const to = publicDecisionFingerprint(successor);
    if (this.states.at(-1) !== from) this.states = [from];
    this.states.push(to);
    if (this.states.length > 7) this.states.shift();
    for (const period of [1, 2] as const) {
      const deliveries = period * 3;
      if (this.states.length < deliveries + 1) continue;
      const tail = this.states.slice(-(deliveries + 1));
      if (tail.every((value, index) => index < period || value === tail[index - period]))
        return { period, deliveries, fingerprint: to };
    }
    return null;
  }
}
