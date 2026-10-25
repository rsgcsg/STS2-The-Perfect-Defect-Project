# ADR-0018: Explicit compute input availability over the existing Store

Status: Accepted (user-approved connection contract; implementation/runtime qualification remains separate)

Date: 2026-10-25

## Problem and exact grounding

The P1 remote input path added on 2026-10-21 reused full preservation copy for a
training consumer. Consequently raw-original payloads were copied and read back
although the model reads named projections, selection and vocabulary. Repeated
Snapshot reprojection and pre-submit use checks compounded that transport cost.
The 09-15 B workflow already distinguished selected result downloads from raw
preservation; that is useful precedent, not evidence of a P1 worker implementation.
The user explicitly approved: the training host references original recordings by
ID; originals remain in Store/R2. No model, recipe, weights or identity weakening.

## Decision and owning authority

Retain every exact ancestor manifest/parent ID. The installed P1 workload/result
consumer owns its explicit required-payload inventory. Transfer verifies each
required destination payload by complete SHA/length; missing bytes are copied and
read back before publishing manifest references. Raw archive/transfer/index bytes
are not compute requirements. Original import/derivation/preservation retains its
own verification and custody; Curation retains current purpose/exposure authority.
A reference manifest is not a promise that all its ancestor payloads are locally
available. Receipts must distinguish required-input readback from full raw recovery.
The worker independently verifies its actual input and source/lock/target binding.

One explicit read session may reuse exact verified bytes and its own verified
selection. The existing private P1 preparation index remains content/code/lock
bound and still hashes source payloads through Store; the owning Snapshot reader
reproduces selection, aliases, whole groups and partitions. No external checksum
file or exported SQLite row grants trust. Operation scratch is released after
submission or superseded preparation. No new Store, ledger or training controller.

## Alternatives and consequences

Full closure copy remains correct for preservation but is unnecessary compute IO.
Blindly trusting an old receipt or object existence would weaken integrity, so is
not used. Required-byte reads remain real work; the change is not a claim of zero
IO or of a particular runtime before measurement. Retaining manifests preserves
provenance while an omitted payload read correctly reports unavailable.

## Compatibility, verification and rollback

Ordinary publish/copy_artifact and preservation push/pull retain their full-byte
behavior. The explicit input-transfer API is used only with a consumer plan.
Results/checkpoints use that same availability contract, including all checkpoint
payloads the result verifier requires and initial derived-state ancestry.
Tests cover empty compute/receiver Stores, corrupt required bytes, warm reuse,
current Curation rejection, strict source identity and full preservation after a
selective copy. Actual school8-step/R2/cold-read qualification is separate evidence.
Rollback selects the original full-copy implementation for a new Run; it never
mutates old manifests, original bytes, checkpoints or recorded failures.

## Non-goals

No research admission, Human-origin proof, optimizer/model/recipe/weight change,
formal training, raw deletion, hidden retry or weaker hash/identity validation.
