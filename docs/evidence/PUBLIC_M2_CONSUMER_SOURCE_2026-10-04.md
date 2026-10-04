# Public M2 consumer source candidate, 2026-10-04

This is a G2 source/contract candidate, with separately prepared package evidence.
It does not install a production Runtime, qualify Native, run the game, submit
training, or establish policy quality. The supervisor owns this consumer branch;
the training supervisor retains the numerical engine, codec and trainer.

## Producer and consumer boundaries

The selected training source is `96c86a2e` (resolve the full ancestor SHA in Git).
The campaign has three PUBLIC M2 scratch configurations, all carry8/window4,
seed1701, reset_each_step=false, five epochs. It changes sample count only:
A06=998, A08=1997, A02=2999. Each publishes epochs 1/3/5. Partial epochs are
explicit engineering selections; epoch5 additionally requires the completed run
result. This batch contains neither matched M0 nor reset-each-step controls.
The historical d6 Public M0 is a separate product route.

`public_m2_export` consumes exact CAS model/run/training-input/checkpoint,
source-view/allocation/dataset, stage/evaluation and terminal-result identities.
It reads weights/tokenizer by payload role, size and SHA, never guessed training
paths. Export verifies checkpoint bytes and binds its actual model tensor digest
and optimizer update count to the exported weights using the existing engine
digest and bounded safetensors checkpoint codec. It validates closed evaluation
metrics and parents; it does not replay dev data or claim recomputed accuracy.

The package inventory is exactly `model.json`, `weights.tensor-tree`, and
`state_tokenizer.json`. Detached export verification has its own schema and
receipt. Config/manifest bindings and owner receipts are outside that inventory.
Extra files, symlinks, wrong payloads, stages, terminal results or tensor binding
fail closed. There is no model conversion or experimental-M2 fallback.

## Admission and publication

Workbench Export requires an already admitted operation. The first export
obtains an owner-held projection receipt, after the existing full verification;
later stages and Register recheck the current owner identity, source index,
claim/revocation, exact source/run exposure, and Gold/test restrictions without
reprojecting all training rows. Export never reserves new training use.
The ledger still has source/run exposure scope, not an immutable dataset-to-
operation binding. Exact immutable CAS lineage supplies that separate relation.

The operation journal is v4. Each completed model also has a persistent receipt
archive, so exporting a later stage does not erase earlier registration proof.
Unknown child/journal outcomes require explicit reconciliation and are not
automatically replayed. A model-specific read-only status query exposes the
correct archived operation. GET does not hash model weights or project rows.

Register composes this proof with the current generic Snapshot Connector
capabilities and a separately validated Runtime package, then publishes one
immutable local selection. Repeated registration reuses an exact matching
selection. A changed binding creates a new selection; it does not replace the
weights or mutate an earlier selection. SQLite verification faults retain the
existing classified storage terminal response and safe local correlation ID.

## Online adapter and Runtime

The independent `stpd.policy.public_m2_cli` provides export, verify-export and
serve. Serve validates the trusted package/config/manifest, selects CPU inference
independently from training device, calls the existing numerical loader and
PublicM2PolicyAdapter, and emits ready/decision Port4 messages. The adapter
receives complete public Snapshot1 catalogs, zero Reads, and the exact compact
renderer/observation-only/no-prior-action/no-feedback profiles. Delivery fields
are control metadata; they do not become model features.

The adapter code pin uses an explicit observed Python import inventory plus
entrypoint/verification/lock inputs, excluding unrelated Workbench and Hub
application code. Runtime-import regression checks that the inventory covers
the actual isolated CPU child. The existing two-line observation/action text
wrapper moves to the existing pure `token_format` leaf and is reexported by
`token_inputs`, byte-for-byte. The nine numerical implementation files remain
unchanged. Shared archive helpers and eager S1 package imports remain declared
dependencies; this is a bounded decoupling, not a fully independent wheel.
Legacy M0 retains its existing conservative code pin and requires explicit
rebind after unrelated Python edits; changing that contract is separate work.

Runtime rc.22 adds the model-neutral capability marker
`PUBLIC_STATEFUL_WORKBENCH_CONTROL_PROFILE` with value
`sts2.policy-runtime/public-stateful-workbench-control-v1`. Public M2 refuses
an older Runtime lacking that capability. M0 has no new marker requirement.
Runtime alone owns the safe segment status (scope/episode/segment, never token),
including One-Step, budget and semantic-cycle closures without epoch changes.
HTTP begin/end require the exact run, game instance and recovery epoch. The
Workbench mirrors this state, requires explicit begin, and keeps Human/Stop
available. It does not infer continuity or automatically retry unknown commands.

## Executed source checks and scope

The consumer tests use real tiny numerical five-epoch CAS runs for all nine
stage positions, real CPU loader/isolated CLI children, exact payload mutations,
checkpoint tensor substitution, persistent archives, real registry/bindings,
and the built Runtime manifest decoder. Owner/Connector/released-package leaves
are explicit synthetic fixtures in that registration test. A separate suite
uses the real curation owner/ledger for admission, revocation and proof reuse.

A cross-component test uses the actual CPU model child, Node Runtime HTTP,
disk AgentRunEvidence and Workbench segment commands; its Connector provides
synthetic public observations and forbids every submission. It scores two
observations, returns Human, creates a fresh segment and explicitly ends/stops.
DOM tests cover stage cards, archived export selection, explicit registration,
and controls that cannot infer segment success from a POST acknowledgment.
Runtime regressions cover old M0 status, token rejection, exact HTTP preconditions,
One-Step closure, budget closure and semantic-cycle closure.

The final head and CI run, exact counts, retained failures and independent review
are recorded in the supervisor handoff/PR. Local scoped mypy checks both win32
and Linux; optional unavailable third-party modules are not a full Python gate.
Required hosted routing is full because contracts, Runtime and locks changed.

## Remaining release and product gates

The Python shipped Runtime pin remains historical until the root release owner
publishes one exact rc.22 package and records the repository/tag/asset, source,
component tree, archive/content/dependency digests and rollback. This branch
must not fabricate a future release URL or mark the old pin compatible.
The root then combines PR159 canonical installer publication and PR160 service
resource isolation with this consumer in one tested kit and owned environment.

Native2129 is reused through its existing compiled source tuple; Python/UI or
Runtime updates do not require a Native rebuild. Official external native seal,
ordinary Steam cold load, current d6 rebind/Load, real nine-stage metadata and
selected M2 Load/explicit segment/stop, launcher close/reopen and repeated
upgrade/rollback still require their owning live receipts. No such acceptance
is claimed by these synthetic, CPU or hosted checks.
