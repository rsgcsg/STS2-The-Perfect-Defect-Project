# Legacy completed-training fixture economy — 2026-10-14

Repository: `rsgcsg/STS2-The-Perfect-Defect-Project`.
Base: `origin/develop` at `458ae23e4d9fc543f50e54b302015853f4d5c024`.
Workstream: `codex/ci-legacy-fixture-economy`; class `G1`, test-only within the
project application's historical completion reader. No contract, version,
cross-repository pin or component production identity changes are required.

## Finding and bounded correction

The original `closed_legacy` function fixture rebuilt the same synthetic closed
26-attempt history for each of 33 tests. Its production `ManifestArtifactStore`,
`LocalBlobStore` and `ObjectStoreRunReporter` calls produced 35 manifests, three
CAS objects, three payload indexes, 104 remote controls, 52 remote indexes, one
run-completion marker and one noncanonical raw journal: 199 files, approximately
233 KB. One measured construction made 199 immutable publication calls, 396
`fsync` calls and 83 store reads. Repeating durable publication was unnecessary
for tests whose risk is reading, rejecting, archiving or transitioning an already
closed history.

The fixture now constructs that entire history once per module through the
unchanged producer code. The actual application reader and completion verifier
check it before reuse. Only immutable tuples of relative names and file bytes
cross test boundaries. Each test writes fresh ordinary files into its own store;
it receives a new `MetadataBlobs` instance and empty reads list, manifest store,
owner, parsed nested journal and `ProjectConfig`. The owner store-path identity
is rebound to the private root and its two-space, newline-terminated raw journal
is rebuilt consistently. No hard links, mutable store, database, handle or cache
is shared. Original template bytes are checked again at module teardown, followed
by the real reader/verifier.

This deliberately copies the complete produced file closure. Artifact lineage
transfer alone does not include the remote control records, request/result
indexes, run-completion marker and owner journal. The construction and immutable
publication seam still runs once; test mutations and archive/transition writes
continue to call their production owners per case.

## Preserved coverage and isolation

All 13 original test functions have identical ASTs; all original 33 collected
node IDs remain. The 26 attempts, checkpoints/resume chain, metadata-only remote
chunk restriction, LF/CRLF raw archival, negative cases, explicit-predecessor
rules and failure-before-pending assertions remain unchanged.

One new isolation case checks both complete private copies with the real reader
and verifier and compares every non-journal file byte to the template. It proves
that corresponding files are not the same file, and config/store identity is
private. Corrupting one copy's call handle is rejected by the control digest
gate; restoring it and corrupting its request index is rejected by the logical
descriptor gate. Rebinding its owner and changing nested stop metadata/raw
journal is rejected by the application recovery gate. The other copy's reads
list and all file bytes remain unchanged and it still verifies. Template hashes
remain unchanged; teardown separately verifies the original on-disk template.

The first execution of the new test expected an invented owner-specific error
code. The actual application reader emits `operation_recovery_required`. That
test-author error and its failed output were preserved, then the expectation was
corrected to the owning reader's existing contract. No production behavior or
original assertion was changed.

## Local measurements and limitations

Host: macOS 15.7.4 arm64; CPython 3.11.15; locked all-extras environment;
pytest 9.1.1, Ruff 0.16.4. Dependencies were prepared once; no cache dropping was
performed. Independent machine work was not controlled.

The first baseline execution passed 33 cases in 10.74 seconds; the first corrected
candidate passed 34 in 1.26 seconds. That single before/after ratio is not used
as a stable estimate. Three additional alternating-order pairs measured the
preserved baseline file against the candidate, including the new isolation case:

| Pair | Baseline pytest / process seconds | Candidate pytest / process seconds |
| --- | --- | --- |
| 1 | 3.308 / 3.959 | 1.048 / 1.714 |
| 2 | 3.284 / 3.697 | 1.017 / 1.631 |
| 3 | 3.265 / 3.700 | 1.014 / 1.434 |

All paired baseline runs passed 33 cases; all candidate runs passed 34, without
skips. Median process time was 3.700 versus 1.631 seconds, about 56% lower on this
host. A separate construction probe measured 0.104 seconds versus 0.014 seconds
for a full private clone, with zero clone `fsync` calls and an initially empty
reads list. These are local warm observations, not portable performance promises.

The existing CI-history analysis records this file's Windows shard-2 JUnit sum
as 90.592 seconds in run `38238260618`, 132.955 in `38514773699`, and 283.142 in
`38572213314`. The last leaf recorded approximately 2965.563 seconds of pytest
before its incomplete/failing gate. JUnit sums include fixture phases and do not
profile Windows I/O. The repeated construction is a measured local cost source;
its share of the observed Windows slowdown and the candidate's Windows benefit
remain unmeasured. No claim is made that this file alone caused or resolves the
whole shard timeout.

## Validation, remaining gates and rollback

Executed: complete affected file, original collection/AST comparison, targeted
Ruff, root dependency boundary checks, `project:closeout` and diff hygiene.
The existing planner's selected gate and
fresh hosted Linux/Windows source/test results remain required for integration;
the bounded author checks do not replace them. Independent review is pending.
The sole heavy local full-gate slot belongs to the separately coordinated owner;
this packet did not run a local full gate or alter CI routing.

Other expensive training and resume tests remain outside this repair. No runtime,
backend fit, GPU, paid job, model quality, native/Human evidence or service
qualification is established. Existing synthetic provider metadata, selected-owner
and recipe/launch mocks retain their previous limits. Revert this test/doc commit
to restore per-test construction; no persisted product data migration is needed.

Private timing, JUnit, I/O, collection and failed-test originals remain in the
task's authorized evidence directory. This public summary contains no private
paths, recordings, credentials, model weights or original private logs.
