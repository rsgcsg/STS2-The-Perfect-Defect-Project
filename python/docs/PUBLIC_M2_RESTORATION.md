# Public M2 restoration and completed model verification

This source restores the original Public M2 numerical/package consumer within
the existing project model services. It adds no training recipe, provider job,
second Workbench, store authority or native legality engine. Runtime Port4 is a
separate required contract increment; until available, registration reports
`public_m2_runtime_contract_unavailable`. Recognized export support is metadata
discovery, not an executable or loaded model.

## Exact original model

The selected original model is
`17ebf91f16579b63e4924e7b1eaf95cf8195753937c7c6cb06347223f7e51216`.
It is epoch 1, `run_complete:false`, K8/d384, trained with `device=cuda:0`.
Its configured five epochs are not five completed epochs. The original
`public_m2_engine.py`, `public_m2_window.py` and
`light_action_m2_training_data.py` are restored byte-exactly. Together with the
six already-present leaves they retain numerical implementation digest
`8e27fba36e7c45bc3d7ad6a47a3570f7a9481180aed16db186c229f12b13d0f3`.
CPU inference selects its device independently without changing model metadata,
weights, tokenizer, checkpoint, stage or training parents.

The package verifier binds the exact run/input/view/allocation/dataset, partial
epoch stage and matching dev evaluation. A terminal result is required only for
epoch 5. Export verifies the original checkpoint bytes and tensor digest against
the exported weight tensors and optimizer-update count. Metrics checks establish
record consistency, not a recomputed quality evaluation.

The input remains complete public Snapshot1 with `reads=[]`, the original
`public_compact` renderer and byte-action codec. Model `17eb…` permits at most
31 candidates, page capacity 8192 and its recorded token budgets. Unsupported or
oversize decisions fail whole before W advances. Candidates are never truncated
or filtered. Prior-action and feedback inputs remain off; Runtime delivery
metadata cannot silently become either model channel.

## One export owner, multiple completed models

The existing active export journal retains pending/unknown semantics. Known
completed operations additionally live under `model-export-receipts/<model>.json`
in that same owner. Another model taking the active slot does not discard a
completed M0/M2 proof. `status_for_model` and explicit registration resolve the
requested model's record. A pending/unknown operation is never archived as
completed, and a new export cannot bypass a different pending operation.

Explicit repeated Public M0/M2 export requests reuse a completed proof after
rechecking the current owner, immutable manifest edges and exact own package
bytes. The existing cross-process export lock also protects cached activation;
it cannot overwrite another worker's pending operation. Source/model identity
and package drift remain errors. This is reuse of known completion, not resolution
or retry of an unknown original request.

## Current authority versus repeated projection work

The first full projection runs the existing
`LocalCurationOwner.require_training_datasets` path. It reproduces the canonical
curated dataset, verifies source bundles and binds their exact run/source/archive
identities. The owner may then persist a private
`stpd/local-training-projection-receipt-v2` in its existing curation database.
It creates no training reservation or source/run use rows.

The compatibility fingerprint reuses the existing installed Evidence/base
projection identity, adds the finite canonical-curation admission dependencies
and lock, and binds the actual Human verifier descriptor. Old unbound receipts
remain historical. Changed compatibility requires a new full verification;
corruption fails instead of silently reprojecting or accepting caller JSON.

Every reuse checks owner/store identity, immutable source ancestry and archive
pins, current complete source indexes, claimed run scope, related Gold/test
claims and exact original training-use rows. A proof does not override withdrawal
or current authority. A cache hit does not rehash every historical source payload:
the original full export's immutable payload closure and current own-package
byte verification remain separate. The receipt does not grant Human, causal,
lossless-history, quality or scientific qualification.

The observed expensive path was `LocalModelExport.start -> Public M0 lineage ->
require_training_datasets -> _training_dataset_bindings -> load_selection ->
decision/source verification`, followed by the same projection in `_run` and its
separate payload-closure verification. In the lead's original 3330-manifest-store
attempt, preflight took approximately two minutes before returning pending;
total export was still running at approximately five minutes. These are lead
observations, not a measured final duration or a speedup receipt. Focused tests
prove that stable duplicate/reopened-owner checks avoid projection/payload reads;
real-store latency and actual native load/Stop remain separate required checks.
