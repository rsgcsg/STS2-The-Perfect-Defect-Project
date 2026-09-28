# Platform Evidence

This component verifies typed immutable artifacts and moves their bytes without
owning gameplay, Human action, or research semantics.

Evidence `0.1.0-rc.18` verifies the additive
`text_observation_not_admitted` Agent-run event against a prior admitted
environment and the strict text-menu snapshot contract. Its reason and snapshot
are observation evidence only, with no decision, delivery or successor binding.
The reason is a Runtime-reported diagnostic, not an independently recomputed
admission decision. Verification does not grant action authority.
Older finalized runs remain readable. A verifier pinned to an earlier strict
Evidence version rejects runs containing this new event, so consumers must pin
the rc.18 candidate before claiming verification of such runs.

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
