# End-to-End Data Lifecycle

This guide specifies physical storage, derivation and training-host transfer for
the strict ResearchTransition/combat-v0/Scheme1 lane. Its Parquet/B0 and optional
frozen-Qwen contracts remain unchanged; they are not a required intermediate form
for every record or training target. [Data and Provenance](DATA_AND_PROVENANCE.md#scope-and-connected-routes)
routes public H-BC, Source3 and direct Agent samples to their existing owners.
They use source-specific proof and input contracts over the same immutable
storage/use lifecycle. This guide does not authorize training or move gameplay,
source proof or research-target authority into storage/transfer.

## Authority And Artifact Classes

```text
STS2 + Platform Annotator
  -> immutable Human evidence and exact H/S/A/S' audit
  -> verified Platform bundle
  -> STPD ResearchTransition projection and B0
  -> immutable canonical corpus (CANONICAL_RESEARCH)
  -> versioned text/model view (DERIVED_MODEL_VIEW)
  -> optional frozen-Qwen features (DERIVED_FEATURE_CACHE)
  -> content-addressed training-input manifest
  -> training host verifies bytes + identities
  -> owner/scientific workflow separately authorizes optimizer creation
```

Within this lane, Platform evidence is the reconstruction authority for the Human witness. The canonical STPD
corpus is the research authority for splits, eligibility and model-neutral transitions. Text,
tokens and frozen features are disposable derived artifacts. Receipts, staging success and
cache hits never grant training authorization.

## Canonical Storage Decision

For this lane, `ResearchTransition v0` remains the logical scientific API. Its physical representation is
canonical JSON fields in Zstd-compressed Parquet. The read-only profiler verifies manifest,
file checksum, row hashes and semantic dataset hash before measuring it:

```bash
uv run python tools/profile_data_lifecycle.py <canonical-dataset>
```

Candidate layouts are temporary and must reconstruct every logical record exactly. The
2026-08-29 experiment on the largest available admitted corpus (106 rows) found:

| Layout | Bytes | Relative to current |
|---|---:|---:|
| current canonical columns | 86,148 | 1.000 |
| dictionary on current columns | 87,077 | 1.011 |
| content-addressed state/catalog objects | 96,296 | 1.118 |
| monolithic canonical record JSON | 52,611 | 0.611 |

Object normalization and dictionary encoding were worse at this scale. Monolithic JSON is
smaller, but the gain does not yet justify losing typed scanning, and the frozen 1,962-row
corpus is not physically present on this Mac. No canonical migration is authorized. Reopen
only with a representative immutable corpus, exact reconstruction, read/scan benchmarks and
a material end-to-end win.

## Frozen Feature Artifact

`tools/compile_joint_features.py` compiles Scheme 1 pooled joint features with the exact
frozen `RealQwenBackend`. Its immutable manifest binds source corpus, serializer/input
profile, Qwen/tokenizer/weights identity, compiler operation, shape and every file checksum.

Identical state/action/model-view inputs share one feature row. `samples.parquet` retains
transition, candidate ordering and chosen labels; `features.npy` is memory-mappable. Any
source, serializer, profile, Qwen or compiler change creates a new artifact. The cache can
always be deleted and rebuilt.

The deterministic shape probe for the 106-row corpus produced 572 samples at hidden size
1,024 in 2,432,912 artifact bytes. Extrapolating only the dense tensor to the documented
11,348 candidate samples yields 46,481,408 bytes (44.33 MiB). This is storage engineering
evidence, not measured Qwen throughput; this Mac lacks the full exact weights and an admitted
CUDA training host.

## Training-Host Handoff

Build, stage and verify are separate operations:

```bash
uv run python tools/compile_joint_features.py \
  --dataset <canonical-dataset> \
  --output-root <derived-cache> \
  --qwen-snapshot <verified-qwen-snapshot>

uv run python tools/training_input.py build \
  --dataset <canonical-dataset> \
  --store <source-store> \
  --lane scheme1-pooled-joint \
  --serializer stpd-model-serialization-v1 \
  --input-profile stpd-combat-v0-standard \
  --qwen-identity <qwen-identity.json> \
  --feature-artifact <derived-cache>/joint-features-... \
  --consumer-entry-point "uv run python tools/s1_smoke.py run"

uv run python tools/training_input.py stage \
  --source-store <source-store> \
  --receiver-store <training-host-store> \
  --training-input-id <sha256>

uv run python tools/training_input.py verify \
  --store <training-host-store> \
  --training-input-id <sha256>
```

Build refuses a dirty STPD worktree and binds the exact commit, `uv.lock` checksum and
consumer entry point. The receiver transfers only absent SHA-256 objects, verifies nested
feature/source/model-view bindings and writes a receipt. A new corpus or artifact creates a
new manifest while unchanged objects are reused.

The host receives canonical manifest/Parquet and only declared model-view or feature objects.
It does not receive raw Human sessions, game files, hidden state, unrelated caches, weights
from Git, an implicit "latest" dataset, or authorization to train.

## Invalidation And Retention

| Change | Canonical corpus | Text/token view | Pooled features | Training input |
|---|---|---|---|---|
| new admitted evidence | new corpus | rebuild | rebuild | new manifest; old objects reused |
| split/B0/projection change | new corpus | rebuild | rebuild | new manifest |
| serializer/input profile | unchanged | rebuild | rebuild | new manifest |
| tokenizer/Qwen/feature operation | unchanged | affected view only | rebuild | new manifest |
| STPD source or `uv.lock` | unchanged | verify applicability | verify applicability | new consumer identity |
| transfer retry | unchanged | unchanged | unchanged | zero-byte reuse when complete |

Raw evidence and canonical research follow their owning retention policies. Derived features
and receiver stores are rebuildable caches. Model/checkpoint outputs are separate artifact
classes and never write back into canonical data.

## Evidence Boundary

The measurements and non-claims in this guide describe the 2026-08-29 lane experiment;
current project/runtime evidence is tracked in [root STATUS](../../docs/STATUS.md).
Portable tests prove deterministic identity, tamper rejection, drift rejection, immutable
retry and exact reconstruction. The local experiment proves only the measured 106-row
layout. No remote transfer, full-corpus profile, real-Qwen compile, optimizer run,
model-quality improvement or training-host throughput is claimed.


## Daily P1 Store and R2 operations

Use the normal project's configured `research_workspace.store_dir`; use one exact
artifact ID throughout publication, preservation, restoration and consumption.
The recording application and `python -m spireagent.workbench recordings --help`
share `ManifestArtifactStore` and the existing import-operation journal. Do not
copy data to a private alternate Store or rebuild an already preserved original.
Run offline recording writes with an exclusive operation state directory; the
Application's live state must not be concurrently written by a CLI.

```sh
python -m spireagent.workbench recordings sources --store STORE --state STATE
python -m spireagent.workbench recordings read --store STORE --state STATE --artifact PROJECTION --output projection.json
python -m spireagent.workbench recordings storage preserve --store STORE --state STATE --peer s3 --artifact ORIGINAL
python -m spireagent.workbench recordings storage restore --store STORE --state STATE --peer s3 --artifact ORIGINAL
python -m spireagent.workbench recordings storage verify --store STORE --state STATE --peer s3 --artifact ORIGINAL
python -m spireagent.workbench recordings storage reconcile --store STORE --state STATE
```

R2 credentials stay environment-only (`STPD_S3_BUCKET`, `STPD_S3_ENDPOINT`,
`STPD_S3_PREFIX`, `STPD_S3_REGION`, AWS credential environment variables). The
operation records the selected peer without secrets. `preserve` verifies remote
payload and original archive members; `restore` verifies bytes into the same Store.
A preservation receipt is storage evidence, not Human origin or training approval.
A failed or unknown operation is preserved; use `reconcile` for that exact intent,
not a new upload to another provider. Source metadata `sources` is navigation;
actual lineage and learning masks remain in the original and named projection.

After `read`, use the supported `stpd.p1.dataset.load_projection` or
`inspect_projections(store, [PROJECTION])` reader. Named eligible-N indexes in the
existing CurationLedger do not assert complete raw capture inventory. Store/Curation initialization
and indexing reserve no data purpose. Training preparation records vocabulary
exposure/use before fitting as described in the [remote Scheduler guide](PROJECT_CONSOLE.md#explicit-co-located-remote-scheduler).
Snapshot reservation and actual training
use remain separate explicit owner calls. Unknown seed or restore ancestry stays
unknown and cannot justify independent holdout.

### Same-ID local cache misses

The existing `ManifestArtifactStore` can use one explicitly pinned durable peer.
Configure the existing local Store with the normal command (credentials remain in
its process environment):

```sh
python -m spireagent.workbench cache-configure --store STORE --other-store s3 --max-cache-bytes 2147483648 --retain-recent 2
```

This writes only `STORE/cache-policy.json`, a non-secret storage configuration,
including the exact bucket/endpoint/prefix selected now. It is not a Curation or
transfer ledger. Reopen the normal Application/reader after configuring it. A local
miss on an exact manifest, payload index or chunk reads that same key from that
peer; normal manifest/chunk/whole-payload validation remains mandatory. A corrupt
local object fails and never silently substitutes remote bytes. A writable reader
caches verified bytes in the same Store; a read-only reader consumes the verified
peer bytes without changing local files. An unavailable peer or missing remote key
returns the original typed Store error. All prior artifact IDs and reader entry
points remain unchanged. Setting a budget alone does not delete bytes or prove a
bounded working set; the maintenance/eviction result must separately establish it.

For checkpoint/model/result artifacts, use the existing generic `workbench push`
and `pull --artifact ID --other-store s3` transfer; they retain manifest ancestry
and transfer absent objects. Materialize and verify through the owning model or
checkpoint reader before declaring a result usable. The P1 original-only storage
entry above does not pretend to validate model semantics.

R2 is the long-term destination for project data; local bytes are the bounded
active working set goal. Current automatic cooling only removes verified older
expanded recording copies; compressed Store archives and named projections still
accumulate locally. It does not yet bound the entire Store. Background closed-recording maintenance is separately
configured through `recordings maintenance status|enable|disable --config PROJECT`;
cloud scope must name candidates or a date/origin scope. It remains disabled until
authorized. With `--cool-recent`, the same maintenance owner retains the newest
two completed sessions in that exact scope. Older expanded raw copies cool only
after the compressed local archive, independent peer full readback, exact-ID
restore and the original CollectionTool reader all succeed. An OS-held consumer
lock excludes managed raw import and the native recording owner. Source seals,
catalog metadata, Store archives/manifests, Curation and remote objects remain;
only verified recoverable expanded files are removed. A closed historical copy
can lack the native lease file; the managed consumer lock stays outside raw and
its enclosing bundle, and never enters an immutable inventory.

The same maintenance operation imports each sealed source, preserves its original
to R2, derives every named run from the verified original run journal, and preserves
each named projection with its original parent closure. Empty sessions without an
observed run retain their original without inventing `run-0001`. Completion requires
the configured original and projection readbacks; a derivation failure retains the
already preserved original and reports the failed operation. Capacity observations
come from the native owner's recording volume, carry an observation time and are
refreshed by maintenance rather than every GUI poll. The operation reserves scratch
and destination space before large writes; insufficient space stops that operation
explicitly and keeps the source. A cloud receipt does not guarantee unlimited offline
recording or eliminate the need to monitor the remaining compressed Store growth.
These are import/publication/transfer/materialization checks with a 512 MiB reserve,
not a native Recorder Start admission budget. The current native recording path
reports append/disk-health failure after an actual I/O failure; a proactive low-space
Start stop and a real recording-on-full-disk experiment are not established here.

```sh
python -m spireagent.workbench recordings maintenance enable --config PROJECT --peer s3 --from-now --origin declared_human --cool-recent
python -m spireagent.workbench recordings maintenance status --config PROJECT
python -m spireagent.workbench recordings storage status --config PROJECT --store STORE --state STATE
python -m spireagent.workbench recordings storage restore-expanded --config PROJECT --store STORE --state STATE --peer s3 --artifact ORIGINAL
```

These calls use the open Application and its same durable import/storage
operation. Closing a panel does not stop maintenance. `expanded_state` in the
catalog and maintenance status distinguishes hot, cooling, cold and restoring;
the archive ID remains the read/derive authority. A failed or interrupted cooling
operation does not automatically resume deletion. Use explicit
`restore-expanded` for that artifact/peer; it restores missing exact original
files without overwriting conflicting bytes. Archive existence alone cannot
reconcile partial expanded deletion as success. The newest two and sources
outside scope are never selected for automatic cooling. This is an implemented
source capability; installed readiness and continuous real operation require
their own receipts. Private Curation backup and staged recovery use the owner
below; replacing a live owner is separate.

## Finalized GLOBAL runtime originals

`spireagent.workbench.global_evidence_store` is the thin registration/reader
over the existing `Manifest` and ArtifactStore. It keeps the original
`sts2.policy-runtime/agent-session-run-1` evidence schema and full directory
inventory; it neither converts the run to Human/text-menu/native-logical data
nor creates data use or admission. Application model/live supplies the trusted
same-package Runtime `expected`, including `global_runtime_directory`, and the
real model/Runtime parent artifact IDs. The archive cannot choose executable code.

`publish_global_runtime(store, producer, expected)` preserves the exact compiled
package bytes as a `protocol` parent. `publish_detached_p1_model(store, package,
producer)` preserves an old engineering export's `weights` and `package_manifest`
with its original package schema and explicitly unknown detached ancestry; it
does not invent formal Run/Input/Checkpoint parents. The normal model owner still
checks tensor and InputSpec semantics. Formal model artifacts keep their original
training parents. Evidence registration checks parent bytes, original model
descriptor/package InputSpec, the current trusted consumer binding when present,
and the exact Runtime package/compiled closure against that sealed run.

`publish_global_evidence(..., expected=..., model_artifact_id=...,
runtime_artifact_id=..., scratch=...)` runs the original typed verifier, binds the
copied complete inventory to its verified content ID, streams the existing cold
archive and publishes an `evidence` Manifest. Its `artifact_id` is a different
identity from the Evidence `content_id`. Full source Input/catalog, actual Agent
frames/scores, dispatches, Receipts and typed results stay in the archive.

After registration, use normal generic transfer:

```sh
python -m spireagent.workbench push --store STORE --other-store s3 --artifact EVIDENCE_ARTIFACT
python -m spireagent.workbench pull --store STORE --other-store s3 --artifact EVIDENCE_ARTIFACT
```

`materialize_global_evidence(store, id, expected=..., scratch=...)` is the normal
typed reader/context manager; `verify_global_artifact` returns its bounded receipt.
They restore by ID, verify every original byte and parent, then call the same
Evidence verifier and trusted same-package replay. Expanded read files exist only
inside that call. An old run needs its original compatible trusted Runtime package;
the current/default package is never silently substituted. GLOBAL format readers
remain with Evidence/Runtime, so their old-read/new-write migration does not create
another Store format. A `show` of metadata, transfer exit or generic hash check is
insufficient to declare the run usable. Budget scratch for the compressed archive
plus expanded inventory and retain the disk reserve before beginning.


## Private Curation ledger recovery

`python -m spireagent.workbench curation --help` backs up the configured
`LocalCurationOwner` using SQLite's WAL-aware native backup API. It permits only
the private Curation tables (claims, uses, indexes, annotations, identity, pending
source state and legacy-unknown markers); a shared Hub/account database is rejected.
The registry, Application runtime token, account state and training-operation files
are not included or falsely declared recovered. The source owner must already be
prepared; backup does not initialize or allocate purposes.

```sh
python -m spireagent.workbench curation backup --config PROJECT --store STORE --output SCRATCH
python -m spireagent.workbench push --store STORE --other-store s3 --artifact BACKUP
python -m spireagent.workbench curation restore --store s3 --artifact BACKUP --output NEW_RECOVERY_DIRECTORY
```

`BACKUP` is the exact returned immutable Artifact ID. The backup includes the
private owner's marker, its native SQLite snapshot, and the existing in-place
preparation plan when present. Restore verifies payload hashes, database integrity,
owner identity and supported private tables, then reports `verified_staged` with
claim/use counts. It refuses an existing destination. This proves recoverable bytes;
it never replaces an active ledger, changes source purposes, resumes an unknown
job, restores account credentials, or makes a new training dataset.

For a real owner-loss incident, stop the owning Application and all writers, keep
the original Store/owner identities and paths, and review the staged database,
marker and preparation plan before replacing the missing original files. A moved
Store requires the existing explicit owner-rebind workflow, not editing identity
strings. Reopen `configured_owner(config)` and read the original claims/uses through
its existing ledger; leave unknown legacy exposure intact. Live replacement is an
operator recovery step, not an automatic consequence of a restore command.
