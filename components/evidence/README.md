# Platform Evidence

This component verifies typed immutable artifacts and moves their bytes without
owning gameplay, Human action, or research semantics.

The additive `SourceSessionBundleVerifier` verifies the explicit
`source-session-bundle-v1` descriptor through `verify_source_session_bundle(path)`.
It binds exact public observation and full catalogue bytes, source declaration
segments, original input scopes and the durable close receipt. A declared source
is never machine proof of Human origin; this format cannot enter the strict
Human bundle verifier. Native exposure, causal transition proof and research
admission remain separate gates.

The additive `SourceSessionBundleV2Verifier` verifies `source-session-bundle-v2`
through `verify_source_session_bundle_v2(path)` or `verify-source-bundle-v2`.
It checks immutable attachment epochs, original actor/input bindings, native
position coverage and all final drain seals. Typed receive uses
`--verify-type source-session-bundle-v2` and verifies staged bytes and their
content identity before promotion. Source v1 and Human readers retain their
original schemas; Source v2 does not attest Human origin or research admission.

The `0.1.0-rc.25` source candidate adds `SourceSessionBundleV3Verifier`,
`verify_source_session_bundle_v3(path)` and `verify-source-bundle-v3`. It checks
original input ordinals and actor/pause/epoch cuts over the same raw evidence
pipeline. An input at the exact Resume watermark needs its original ordinal
after that Resume cut; Source2 and publication pause rules remain unchanged.
The verifier preserves missing captures and gaps, which research admission must
evaluate separately. V3 neither attests Human origin nor qualifies continuous
history. Consumers need the frozen package/pin containing this API; changing
the source tree alone does not update an installed verifier.

The `0.1.0-rc.26` candidate adds typed sampled-current Agent-session evidence
and authenticated Source3 collection-tool capability discovery/packing. Sampled
runs retain original observation/catalog bytes and exact consumption/ACK bindings;
a null publication index does not fabricate full publication history. Legacy
full-reference verification remains unchanged. Collection-tool support is checked
against its immutable inventory; a source declaration is not Human attestation.
Consumers need this candidate's actual locked package before using these APIs;
source tests alone do not qualify a loaded runtime or research data.

The `0.1.0-rc.27` source candidate adds the optional owned-Current execution
policy, exact recorded dispatch/result joins, bounded known-stale accounting
and the public typed terminal summary described below. Legacy manifests retain
their original grammar. Production consumers require this candidate's exact
external pin, lock and installed verifier; source acceptance does not complete
that promotion or prove live continuation eligibility.

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

Evidence `0.1.0-rc.24` verifies Policy Runtime adapter protocols v1, v2 and v3
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

Native logical Agent sessions use the separate public
`verify_agent_session_run_evidence(directory, expected)` verifier and
`sts2-evidence verify-agent-session-run DIRECTORY` command. Its registry type is
`policy-runtime-agent-session-run`, and its run schema is
`sts2.policy-runtime/agent-session-run-1`. It verifies the closed Agent Manifest,
adapter attestation, event order, session/acquisition identities, original
submission/result/reconcile bindings, controller and budget facts, sticky taint,
and sealed checksums. Declared opaque state files require exact inventory, byte
hashes and durable consumption metadata. The legacy Policy-run verifier retains
its original namespace and rejects native Agent fields.

Consumption metadata follows the Runtime ledger's occurrence, revision,
authorized scope, fresh consumption ID and publication-prefix rules. A new
Current capture of the same unit can be neutral and retain an earlier consumed
publication; a declared incremental view can advance for newly included scopes.
Every received publication view, including terminal and unavailable entries,
contributes to omission metadata. Its kind does not declare task completion.
Act and submission must follow an acknowledged input and durable watermark;
pending, unknown or outstanding submissions cannot authorize replacements.
The original-request-bound `native_submission_not_started` event closes a
recorded intent only when Runtime knows the SDK submission never started. It is
not a Native Result or Receipt. A generic failure reason cannot replace that
fact, and an unclosed intent cannot produce a clean final run.

This verification establishes the producer's typed operational evidence. It
does not decode numerical Model state, inspect the artifact path, prove complete
native capture or causal effects, establish Human origin, or admit research data.
Checksummed transfer uses this same typed verifier; generic file integrity cannot
replace it. An installed consumer needs a release containing this additive API;
the older rc.24 package does not acquire it from a source checkout edit.

### Opt-in owned Current and known stale results

The optional `sts2.policy-runtime/agent-execution-policy-1` manifest extension
selects reader-owned Current and bounded fresh decisions under the closed
[Agent Session contract](../policy-runtime/docs/AGENT_SESSION_PROTOCOL.md).
It requires sampled-current/once-per-occurrence, no state recovery, and both
`current` and `current_owned` methods. Legacy manifests retain their exact
five-field Next and event/pending shapes. New policy Next has exactly one extra
nullable `operational_outcome` field.

Opted-in submissions record `dispatch_binding`. Every full terminal result must
join its original runtime, client session, controller lease and generation.
Pending/reconciliation records retain that binding, including after Human/Stop.
Both terminal branches share original-result deduplication and classification:
identical repeated terminals count once; changed original bodies fail. All exact
`not_started`/`stale_snapshot_or_binding` results count, including threshold and
Stop/deadline arrivals. Only delivered results reset consecutive count; total
never resets. A pre-submit closure or pending lookup is not a full terminal.

The closed `native_stale_decision_deferred` event follows its original result,
has exact rebuilt counts strictly below both ceilings, and matches recorded
Auto/epoch/known/no-pending/untainted accepted prefix. Notifications repeat the
entire original result through unchanged readiness. An Act on the refused basis
fails; only a new advanced ACK and matching completed Next retire the notification.
These records do not prove private Teacher correction or numerical W contents.
Readiness witnesses additionally constrain their recorded occurrence, revision
and available catalog/owner metadata. Discarded queries do not retain full
public input bytes, so complete normalized coherence remains Runtime's required
check; this metadata validation is not a substitute for that guard.

A verified opted-in value exposes a fresh `terminal_summary` dict copy; legacy
values return `None`. Schema is `sts2.evidence/agent-session-terminal-summary-1`.
Fields are `run_id`, `content_id`, `original_submission_count`,
`terminal_result_count`, `known_delivered`, `known_stale_rejections`,
`consecutive_known_stale_rejections`,
`proof_scope: recorded_dispatch_and_terminal_results` and
`live_eligibility_proved: false`. Submission count is durable intent count, not
an independently inferred SDK-started/used-budget count. Counts reflect recorded
full terminals: they invent no pending/absent result or admitted research N.

Evidence validates recorded mode/epoch/prefix/dispatch/counter facts. Held-only
controller events and deferral records have no live lease-expiry or current
remaining-budget snapshot, so verification cannot independently prove live
continuation eligibility. Those mandatory Runtime guards require their source
tests and final native admission. Root must promote the version, exact external
pin/lock and installed verifier before production use; no source-checkout
fallback is introduced for the installed older package.

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

[Explicit carrier recovery](CARRIER_RECOVERY.md) creates a separate exact-inventory
Human bundle carrier when only enumerated unlisted metadata polluted a directory.
`sts2_platform_evidence.carrier_recovery.recover_human_bundle_carrier` preserves
original bytes, manifest/attestation and source identities, records the original
carrier and excluded metadata hashes outside the recovered bundle, and uses the
unchanged typed verifier and receiver. It is an explicit operation, never an
automatic import fallback or a new Human/use/Gold authority. Directory input is
the first supported form; no permissive archive extraction is added.

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
