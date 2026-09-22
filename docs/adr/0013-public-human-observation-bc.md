# ADR-0013: Public Human-observation BC for local game input parity

Status: Accepted for implementation; native runtime qualification pending

Date: 2026-09-18

## Problem and boundary

The first Stage 1a D-Simple-S pilot trained on a semantic execution view. That
view includes execution supplements and native action representations unavailable
verbatim through the online Snapshot interface. Removing those fields at inference
would silently change its input. Keep its model, input and receipt identities intact.

An exact recorded Human observation H and the execution pre-state S have separate
meanings. The canonical S-a-S' contract remains unchanged. Ranking Human choices can
instead use H and H's complete public BoundAction catalog when the owner recorded
an exact Human-to-native binding. This is an explicit BC view, not an assertion H=S,
not a new successor proof and not a conversion of native verbs into public verbs.

## Decision

Add `stpd/public-observation-bc-view-v1` over an existing verified decision allocation.
Retain allocation/dataset parents and all original train/dev assignments. Re-open the
original content-addressed bundles after owner verification; select the exact
`proof_ref` and `human_observation_ref` carried by each research occurrence.
Require matching proof/action/frame snapshot identities, owner `exact_unique`
mapping with a recognized binding basis, and one recorded public BoundAction.
The selected public catalog entry must match binding ID, verb, subject and all
argument roles/referents exactly. Never infer bindings from proximity or label text.

Project the entire interactive, complete public snapshot through
`stpd-public-snapshot-lite-v1`, also used by standalone `score_snapshot`.
Binding IDs stay outside model text. Identical-looking instances retain their exact
supervision binding; no semantic similarity heuristic disambiguates them.
Native-only/missing/incomplete bindings are explicit view exclusions, not deleted
raw decisions. `samples` and `dispositions` payloads reproduce on load, including
every allocation member and its admission or exclusion. Both train and dev must
remain nonempty. No test/Gold release, new use ledger or split resampling occurs.

The shared token runner accepts this additional fixed-allocation view. The public
serializer identity propagates into model/export manifests. Public models reject
the semantic-state scoring entry; historical semantic models reject public-snapshot
scoring. Refit the train-only scratch tokenizer and retrain weights for the new view.
PF retains the pinned tokenizer but gets new input/model artifacts. The pooled S01
compiler does not acquire public-view support implicitly.

## Validation and rollback

Test stale observation identity, missing/native-only binding, non-exact mapping,
changed verb/arguments, incomplete catalogs, changed labels, source reprojection,
candidate permutation, full token lifecycle and export parity. Real-data admission,
model timing and actual Workbench/Mod execution receive separate receipts.

Disable new public-view production to roll back; retain the canonical source,
allocation, old models and all admission reports. Do not weaken native/evidence
verification or replace immutable historical artifacts.

## Same-day compact view and configurable resource budget

The first real public view admitted 65/66 decisions (49 train, 16 dev, including
40 combat turns). Its expanded representation repeated large objects; one scratch
input exceeded the original 8192 budget. The failed preparation and v1 view remain
historical artifacts and the v1 reader retains its original projection.

New production uses `stpd/public-observation-bc-view-v2` with serializer
`stpd-public-snapshot-compact-v2`. State objects/arrays of at least 256 serialized
characters that occur more than once are represented once in a deterministic FACTS
table, with explicit `$stpd_fact` references in STATE. Definitions may refer to smaller
definitions; the finite original tree cannot introduce cycles. Reserved-key collisions
are rejected. An inverse verifies exact tree equality, including array order and
multiplicity. Action texts and complete candidate bindings retain all their content.
This changes input formatting, so it has a new identity and requires new training.

The user clarified that 8192 is not a fixed research requirement. Token preparation,
training and standalone inference now carry a configurable token budget; current CLI
defaults to 16384 via `--max-tokens`. Legacy configs without a field retain 8192. A
larger budget never causes padding to that size or grants a measured memory guarantee.
PF additionally checks the actual pinned backbone context capacity. Larger inputs
can use an explicit larger configuration after a resource check instead of being
discarded or redesigned solely to meet 8192. No silent truncation is introduced.
