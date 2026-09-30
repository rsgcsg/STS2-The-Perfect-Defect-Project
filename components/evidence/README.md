# Platform Evidence

This component verifies typed immutable artifacts and moves their bytes without
owning gameplay, Human action, or research semantics.

For an explicit Managed text-v2 Agent run, the verifier requires the sealed
public `environment_binding` alongside the Host-independent model Manifest.
It recomputes the binding's canonical SHA-256 from the manifest bytes and
checks each admitted service/runtime/game-continuity identity and candidate
build against the recorded v2 observations and ordered controller claim,
dispatch, and release events. A Managed dispatch needs a prior matching claim;
an unconfirmed owner remains unknown or tainted, never a successful release.
Existing Connector records keep their strict original field sets. Verification
checks the producer's typed association; the selected package content digests
and private-profile digest are provenance supplied by the trusted local
application, not independent re-attestation of installed bytes or private
profile contents by Evidence.

Evidence `0.1.0-rc.23` verifies Policy Runtime adapter protocols v1, v2 and v3
in the Policy Manifest and adapter attestation. The opt-in v3 interaction-memory
port requires a declared text-menu representation, just as v2 does. This verifier
checks the existing immutable input/result events; it does not infer model memory
writes, native effects or causal successors from a protocol version. Expected and actual
adapter identities, manifest digests, and all existing typed event checks remain
strict. Port 3 `text_decision_input` also requires exact `observation_context`
metadata: the validated completion's `continuity_token` and nullable
`previous_interaction_request_id`. A non-null request ID must reference one
unused prior confirmed menu/native result in the same continuity and a different,
strictly newer snapshot. New continuity starts empty; retired tokens cannot
return. These are recorded Runtime facts, outside model-visible page text;
absence of history is not permission to infer the last action. Old ports retain
their original exact input payload. It also verifies the additive
`text_observation_not_admitted` Agent-run event against a prior admitted
environment and the strict text-menu snapshot contract. Its reason and snapshot
are observation evidence only, with no decision, delivery or successor binding.
The reason is a Runtime-reported diagnostic, not an independently recomputed
admission decision. Verification does not grant action authority.
The Agent-run verifier also accepts the explicit `text-menu-v2` Policy Manifest
and Connector snapshot/result pair. It checks the complete ordered menu against
the decision digest, public card/target selection and system-menu progression,
then binds each dispatch/result to the selected action and derived request ID.
Native delivery, unknown delivery and observed successor remain separate typed
events; text-menu runs cannot mix in generic Receipt/Successor events, and each
dispatch records exact cumulative menu/native attempt counts. Unknown delivery
has no retry or successor. This is ordinary Agent-run
integrity verification, not Human origin or research admission.
`text_menu_dispatch_cancelled` records the Runtime's `recovery_before_submit`
disposition for one previously recorded dispatch attempt. It consumes that
attempt without inventing a Connector result, native delivery or successor.
Cancellation has exact fields `decision_id` and `reason`; missing attempts,
duplicate dispositions, later results and successors are rejected. Attempt
counters include this preparation and do not prove actual submission. This
verifier checks the producer's ordered evidence, not whether the game executed
an action independently. The event requires Evidence rc.23 or later; older
strict verifiers must not be used to qualify such a run.
Older finalized runs remain readable. The original text-menu-v2 contract was
introduced in rc.18; the cancellation event and protocol-v3 attestation require
rc.23. Earlier strict verifiers reject those newer records rather than silently
ignoring them. Consumer pins must match the event and adapter contracts in use.

```text
producer bundle
  -> typed verifier
  -> checksummed transfer
  -> quarantine or atomic promotion
  -> immutable local object
  -> transfer receipt
```

`human-session-bundle-3` is the current canonical-first collection format. Its
export preserves `canonical-transitions.jsonl`, while the complete raw session
retains compatibility rows, unresolved decisions and failed Human occurrences.
The verifier binds the producer causal audit by hash and independently checks
canonical/trace joins, lineage, exact action/catalog/frame/Read references and
closed-session identity. Zero canonical rows do not prevent transfer of valid
failure evidence; verification does not certify a zero-failure Full Run.

`human-session-bundle-1` remains readable. `human-session-bundle-2` additionally
verifies its embedded capture profile, RunJournal, state-bound Read evidence,
required materialized Reads, content-addressed Read blobs, and exact content
identity. Verification does not prove Human origin; the bundle carries an
explicit owner attestation with `machine_verifiable=false`.

V2/V3 capture-profile identity is the SHA-256 of the producer's compact,
schema-ordered JSON. Bundle content identity remains canonical sorted JSON.
These are intentionally distinct so independent consumers verify the exact
producer contract without changing V1 profile semantics. Current Read requirements
are keyed by phase, kind and optional interaction kind; a shop-only Read does
not become mandatory in combat. Existing V1/V2 research consumers require an
explicit V3 adapter update. Platform supplies verified evidence and no training
projection or eligibility policy.

## Check

```bash
npm run check
```

## Verify

```bash
npm run evidence -- verify-human-bundle /path/to/session-bundle
```

## Transfer And Receive

```bash
npm run evidence -- transfer-manifest /path/to/session-bundle \
  --content-id <bundle_content_id> \
  --artifact-type human-session-bundle \
  --output /path/to/transfer-manifest.json

npm run evidence -- receive /path/to/session-bundle /path/to/transfer-manifest.json \
  --root /path/to/evidence-store \
  --verify-type human-session-bundle \
  --receipt /path/to/transfer-receipt.json
```

The receiver validates bytes, then runs the typed verifier inside staging before
promotion. A failed or partial artifact is quarantined and never becomes an
admitted object. Each receive attempt also publishes a non-authorizing
`store-status.json` containing its last receipt so the read-only Workbench can
show operational state without reimplementing verification.

## Automatic closed-session delivery

The opt-in [delivery service](DELIVERY.md) consumes a fixed collection-tool release,
reconciles durable Close receipts, and persists packing/upload/receiver state
outside the game. Its public Python CLI supports background use, status and
explicit stopped-worker credential recovery. Typed Hub 401/403 blocks preserve
exact prepared evidence and upload identity; only `resume_auth`/`resume-auth`
can requeue them after the account owner restores same-device authorization.
Storage failures and historical incidents are not reclassified as login issues.
Evidence transfer is separate from game authority and research admission.

`completed_delivery(config)` holds the stopped worker's existing process lock
and yields an immutable completion receipt for a whole delivery generation.
It checks sealed raw inventory, pinned tool, bundle, archive and verified receipt
identities, then rechecks before releasing the lock. The consumer owns same-consent
configuration rollover and native process readiness; failed Human decisions remain
failed even when their transfer is complete. See the [completion contract](DELIVERY.md#stopped-generation-completion).

## Safe application summaries

`summarize_verified_human_bundle(verified_value)` is the public
`sts2.evidence/human-bundle-summary-1` projection. The V3 verifier materializes
its current disposition counts and native run-boundary facts from the same
already-verified streams; summary reads never rescan raw evidence. Summary
identity includes bundle content, checksum/export digests and verifier schema.
The consuming service separately binds its deployed verifier source identity.

The summary preserves accepted/proved/canonical, normal cancellation, abort,
diagnostic/unsupported invalidation and unresolved counts independently. Real
failures use the Recorder owner's unique decision-failure accounting, not the
number of invalidation rows. Historical unsupported dispositions are `null`,
not zero. V1/V2 remain archival and gain no invented canonical/native facts.
`run-unassigned` remains an explicit unassigned context, not another played run.
Native start/terminal observations and exact native victory/defeat outcome do
not certify uninterrupted Full-Run coverage or research admission.

The [delivery status projection](DELIVERY.md#application-status-projection)
publishes bounded local lists, exact remote IDs, parsed receipts and global
quality counts without exposing local paths, raw data or transport credentials.


### Interrupted recording delivery

Collection tools that declare `interrupted_recovery_schema` can recover unlocked,
explicitly versioned sessions into `outbox/recovered-recordings`. Original files
remain unchanged. Hidden staging directories never enter delivery. The independent
bundle verifier checks the original inventory/prefix hashes and unknown-only
closure; corrupt or torn input remains an incident. Completed-generation checks
also bind the retained original to its recovered copy. Recovery does not establish
Human origin, a native terminal or complete sequence coverage.
