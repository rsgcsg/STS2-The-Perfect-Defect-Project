# E3 structured application composition, source candidate

Task E3, G2 public application/consumer composition (G4 runtime/install acceptance
pending). The source candidate depends explicitly on
`c7fd1a08234e9395c052bb164140c94fec9d8912`, branch
`codex/e3-v1-model-composition`. This report describes portable implementation;
it does not approve G2/V1 or qualify a new native profile.

The first incorrect application fact was that the trusted structured adapter was
advertised as unavailable after its domain package owner existed. The existing
application owners now compose that owner, without downloaded executable choices:

- `LocalModelService.prepare(artifact_id)` retains the Hub's authorized result
  cache. `LocalModelExport.start(artifact_id)` also materializes a structured
  cache result or local Store model into the existing private export slot.
  Only package manifest/weights are read. Private model ancestry is not fetched.
  Exact package payloads, closed inventory, digests and internal model identity
  are rechecked by the domain loader before registration.
- `LocalModelRegistration.register/status` binds the same private registry,
  exact native capability requirements and source digest through
  `stpd.structured_policy_installation`. The ArtifactStore model ID and the
  package's internal model ID remain different identities. The installed
  `text-menu-m2-v2` Runtime is reused; the trusted adapter ID remains
  `stpd-s0-structured-adapter`, with decision-only NDJSON port 2.
- `LocalModelService.catalog/readiness/start/prepare_and_load` expose the S0
  text-v2 compatibility scope, invoke the real installation inspector, and
  resolve absolute structured port arguments. Existing Human-mode startup,
  bounded autonomy, control/Stop and unknown-no-retry owners remain in charge.
- `LocalMemoryEvaluationService.start(model_id, source_id)` dispatches structured
  fixed-model dev/test work in its existing operation file and lock. It checks
  the source-aware curation owner before child launch; the fixed child checks
  the same owner again before domain preparation/execution and fences each
  domain publication against the current operation. Original archive/capsule
  verification, current use/Gold/partition restrictions and model ancestral
  origin/source-group overlap checks precede numerical execution. Missing
  ancestral manifests fail closed; external exposure remains unknown and no
  clean held-out claim is made.
- `local_evaluation.summary` reads the recorded fixed-model report, preserving
  observed/known-label denominators, partition, package/model/input identity,
  zero optimizer updates and the descriptive engineering non-claims.

The fixed evaluation child calls `prepare_structured_evaluation` and
`run_structured_evaluation`. It uses the existing private-child process owner
with a fixed 600-second wall bound and records actual exit information in the
same operation. A missing result
following launch remains `interrupted_unknown`; a subsequent start cannot replay
it. This change introduces no new cancellation promise, journal, training
optimizer, model selection, game authority or native operands.

Scope is the existing structured observation-only M2 S0 text-menu-v2 I/F-off
projection. Full native logical/event input, all-scene policy quality, Human
origin, generalization and complete G2/V1 are not claimed. HTTP/console follow-up
must call these existing application service APIs; no new routes are included.

Validation uses synthetic CPU fixtures and the shared existing developer
interpreter, with this worktree selected explicitly. Actual fixed-evaluation
children use a test-only exact-source bootstrap because the shared interpreter
has another editable checkout; production uses the fixed code-owned module.
Tests cover real package binding/readiness, detached cache installation without
private ancestry, actual separate-process dev/test evaluation and preserved
parent RNG/thread state, original use reservations, overlap/Gold/train/raw-tamper
rejection before launch, package tamper, idempotency and unknown-no-replay.

Portable tests are source evidence. The structured package retains the source-and-lock digest across all STPD and
SpireAgent Python files. Changed application source therefore invalidates old
packages; no historical S0 package inherits this candidate's readiness. A fresh
package must bind the final integrated source.

Exact current Runtime package/SDK validation,
native capabilities/context, Runtime manifest admission and game-bound launch,
control/Stop/unknown behavior still need the installation/runtime owner's exact
candidate gates. No game, installed runtime, real data, training, provider,
deployment, push or merge operation was performed by this packet. Rollback is
reverting this application commit and retaining private caches/exports/journals;
never rewriting immutable artifacts or curation uses.

Executed local receipts: the combined selected application/domain regression
command passed 226 tests in 38.31 seconds; targeted mypy passed eight changed
source files; Ruff passed all changed Python source/test files. Root
`project:check` passed its 11 tests and repository checks; `check:boundaries`
passed its four tests and production dependency scan. `project:closeout` and
`git diff --check` passed. Closeout's automatic base is `origin/develop`, so its
file list also includes the explicitly integrated dependency stack; that broad
list is not this writer's incremental diff. The normal selected portable CI
aggregate remains a lead integration gate, not an inferred PASS.

After the final adapter-owner dispatch, structured selection prefix, exit
assertions and fixed wall-bound changes, the directly affected application and
registration tests passed again: 35 tests in 6.00 seconds. This fresh receipt
covers those final changes; the earlier combined receipt remains scoped to its
then-current tree.

Independent review repair: the private process owner's second exit-callback
argument is `forced`, encompassing callback/read/control failures and deadline
kills. The application receipt now records that exact fact, without a
`timed_out` field. A timeout is recognized only through the process owner's
actual `private_child_timeout` exception code. A real child-start callback
failure regression failed against the original candidate, then passed after
this repair; forced early exit remains unknown and cannot be replayed.

Repair receipt: all 10 structured application tests passed in 3.93 seconds;
Ruff passed the two affected Python files; targeted mypy passed the application
owner; `git diff --check` passed. No broad suite, native operation, push or merge
was performed for the repair.
