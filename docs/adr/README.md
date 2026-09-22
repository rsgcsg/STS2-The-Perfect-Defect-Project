# Architecture Decision Records

ADRs preserve durable decisions that would otherwise be repeatedly rediscovered
or accidentally reversed. They complement current code and evidence; they do
not override a later exact fact without an explicit superseding decision.

## When an ADR is required

Write or update an ADR when a change alters an authority boundary, public
semantic contract, evidence or identity model, long-lived runtime dependency,
cross-component dependency direction, long-term architecture route, cloud
topology, or durable Git/release semantics.

Ordinary bug fixes, small internal refactors, test additions, prose
clarifications, and implementation already decided by an accepted ADR normally
do not need a new ADR.

## Status and lifecycle

Use one explicit status near the top of every ADR:

- `Proposed`: under review and not yet authoritative;
- `Accepted`: current decision for its stated scope;
- `Superseded by ADR-NNNN`: retained as history but no longer current;
- `Rejected`: evaluated and not adopted;
- `Deprecated`: still present for compatibility but scheduled for removal.

Do not rewrite an accepted ADR to make history look cleaner. Add a superseding
ADR or an explicit amendment with date, reason, and affected scope.

## Required shape

An ADR should contain the decision, context/problem, fact and authority owner,
considered alternatives, consequences/tradeoffs, evidence or falsification,
compatibility/migration, rollback, and non-goals. Bind exact mutable identities
in a dated evidence report or PR rather than turning the ADR into a current-state
ledger.

## Index

- [ADR-0001: Consolidate the STS2 Environment Platform](0001-consolidate-environment-platform.md)
- [ADR-0002: Model-neutral Policy Runtime](0002-model-neutral-policy-runtime.md)
- [ADR-0003: Serialize Human input for canonical one-step evidence](0003-serialize-human-input-for-canonical-one-step-evidence.md)
- [ADR-0004: Native Foundation and Ritsu route](0004-native-foundation-and-ritsu-route.md)
- [ADR-0005: Human Root, Commit, and Successor evidence](0005-human-root-commit-successor-evidence.md)

- [ADR-0006: Decision occurrences within causal roots](0006-decision-occurrences-within-causal-roots.md)

- [ADR-0007: Canonical collection and explicit recording disposition](0007-canonical-collection-and-disposition.md)

- [ADR-0008: Release-bound closed-session delivery](0008-release-bound-closed-session-delivery.md)

- [ADR-0009: Scoped checks and verified integration receipts](0009-scoped-checks-and-integration-receipts.md)

- [ADR-0010: Dataset curation and continuous per-run recording](0010-dataset-curation-and-continuous-recording.md)

- [ADR-0011: Fixed decision allocation and portable S01](0011-fixed-decision-allocation-and-portable-s01.md)
- [ADR-0012: Stage 1a token inputs and frozen-query execution](0012-stage1a-token-input-and-query-execution.md)
- [ADR-0013: Public Human-observation BC](0013-public-human-observation-bc.md)
- [ADR-0014: One observation, all B/C action readouts](0014-packed-bc-readouts.md)

- [ADR-0015: Native logical observation, actions, and memory (Accepted: upper-level scope)](0015-native-logical-interaction.md)

## New ADR template

```markdown
# ADR-NNNN: Decision title

Status: Proposed

Date: YYYY-MM-DD

## Problem and exact grounding
## Decision
## Owning fact and authority
## Alternatives considered
## Evidence and falsification
## Consequences and tradeoffs
## Compatibility and migration
## Rollback
## Non-goals
```

Keep numbering monotonic. A missing or unindexed ADR fails repository governance
checks.
