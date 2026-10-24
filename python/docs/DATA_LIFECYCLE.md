# End-to-End Data Lifecycle

## 项目存储：真身、工位与本机删除条件

R2 是项目数据唯一的长期真身；GitHub 是代码、文档和规则的权威版本；Mac 是可替换工位。
存储沿[同一职责链](../../docs/ARCHITECTURE.md#responsibility-refinement)：原件/派生/用途/消费各守其合同；换存储不改来源、训练准入或动作含义。
GUI、CLI、API、Agent 共用现 Store/transfer/reader，不另建归档账本；[日常操作](#daily-p1-store-and-r2-operations)给同 ID 保全和恢复。

| 类型 | 长期真身 | 本机保留上限/范围 | 何时可删本机副本 |
|---|---|---|---|
| 原始录制 | R2 不可变原件及父引用 | 当前录制及现维护策略最新两份；活动原件保护 | 本轮不删 recordings；另获批准且完整空目录读回、原 reader 和无占用核验后才冷却副本 |
| 数据集/派生/输入 | R2 具名 artifact 与完整祖先 | 当前实验固定输入；可逐出块受 Store 显式预算约束 | 同 ID 全量读回和实际消费者通过，无活动读者且有批准 |
| 模型/检查点 | R2 全状态与来源绑定 | 当前使用、恢复和回滚集，逐项具名；现 trim 保护此类 | 独有产物先发布、空目录恢复及模型/checkpoint reader 通过，另获批准 |
| 评估结果/证据/任务原件 | R2 不可变 artifact | 当前任务、未封存/unknown 原件及最近恢复所需 | 全分母与失败原件纳入可恢复清单，空目录核验且获批准；unknown 不重投 |
| 已安装 release/工具 | GitHub 版本/发布及可重建来源；独有构建证据归 R2 | 在用、上一可回滚版本及其他被具名任务引用者 | 核实际进程/配置/安装绑定、可恢复发布后，清单获批；不按版本号猜闲置 |
| 依赖/缓存 | 锁文件/源码/原下载源 | 活动环境及有界缓存；不用的重建副本不作长期资产 | 有收口文件且满 7 天的可重建部分可清；整目录仍先批，不动活动依赖 |
| Codex 任务目录 | 源码/文档归 GitHub；独有证据/原件归 R2 | 当前任务、具名恢复工作集；收口写结果/原件去向和可删范围 | Git 独有提交、未跟踪/ignored 原件先归位，按收口与清单审批；不用日期推断可删 |
| 对话历史 | 用户原话长期归 R2 可验证备份；当前 sessions 仍是本机原件 | 保留可检索原话；未验证工具兼容前不改扩展名/压缩 | 本轮不删 sessions；备份读回不等于 Codex 支持 .gz，压缩须另验正常检索 |
| 游戏安装/反编译资料 | 仅本地合法安装/可重建来源 | 当前游戏与必要开发资料；不进 R2、不进 Git | 本轮保留；换工位需重装，属于恢复目标的明确例外 |
| 用途账/运行操作账/配置 | 项目数据账备份归 R2；非秘密配置/恢复说明归 GitHub | 当前原 owner 状态，禁止并发修改活账 | 按原账备份/恢复 SOP；凭据仅原授权环境提供，不归档到报告/Git |

本地 Store 位置以实际 ProjectConfig 为准，不假定在 ~/.local/share/spireagent；缓存预算只约束可逐出块，保护对象/展开副本另计。
长任务在生产者写入前、评估每局新游戏前检查各实际写入卷的工作量加保留余量；不足应拒绝或停在局间，并保存未启动分母。
评估/训练活动期间，重测试须通过同卷空间检查并由负责人排期；空间检查不是磁盘配额，不能承诺抵御其他进程无限写入。
删除先审批清单；独有内容先 R2 空目录完整读回和真实消费者验证；运行中东西不动；合并须用户同意。
Modal/学校 GPU 是执行节点，工作集临时，产出回同一 R2；Elements 是可重建冷副本，不能成为项目数据另一真身。
换新 Mac 能否按 GitHub/R2/配置完整恢复仍是待验目标，不能由本页或上传成功宣称已证明。

## Existing research lane scope

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


## Workstation capacity before long tasks

Use the normal entry, on its actual source and data volumes. The existing
`require_workspace_space` still owns scratch/destination accounting; its 512 MiB
transfer reserve is not the complete long-task budget. New admission uses a 2 GiB
operating margin plus the workload below. `SPIREAGENT_MIN_FREE_BYTES` explicitly
sets that margin, in nonnegative integer bytes, for the process being started.
An invalid value rejects admission. Changing a shell does not reconfigure an
already running Application or game; normal upgrade/launch remains separate.

| Supported entry | Work allowance added to the margin | Insufficient space |
|---|---|---|
| Reference `run_suite` before every new game | 2 GiB for next-episode capture/packing; suite report volume checked | Suite stops, remaining rows stay `not_started`, no new world/Actor offer or unknown |
| Application `prepare_native_episode` for a new owned world | Same next-episode allowance on actual Application state volume | Reject before replacing resource intent/starting Host; exact old intent replay still returns its original fact |
| Native Recorder shared `RecordingSessionStore` writer | Same 2 GiB next-session allowance on the recording directory's actual filesystem | All current Create variants reject before making a session directory; API/lifecycle retains its normal explicit rejected-start result |
| P1 `prepare_p1_data` | Selected projection payload sizes plus two 64 MiB cache/transaction budgets | Reject before cache creation/inspection or use reservation; no unrelated raw-ancestor download required |
| Local training start/resume | Actual owner operation/scratch volume, equal artifact allowance on Store, plus missing named P1 input/checkpoint chunks; legacy start uses 512 MiB each | Reject before cold payload read, pending intent/use/child; retained attempts still count in existing cumulative limits |
| Remote training prepare | 512 MiB temporary + 512 MiB persistent plus named input/checkpoint cold reads; purpose ledger and actual transfer scratch volumes checked | First-consumption refusal precedes purpose verification/vocabulary fit/use; subsequent full-closure transfer retains exact budget and failure intent, no provider submission |
| `npm run check` / Python full gate | 6 GiB planned build/test work plus operating margin; workspace and actual temp filesystem checked again before each stage | Refuse before heavy subprocess; no automatic cleanup or modification of existing jobs |

The 2 GiB next-session work allowance rounds up the observed 563,136,269-byte
capture prefix and simultaneous archive/extraction copies. The existing
checkpoint/input planning case was about 2.7 GiB; the full gate allows 6 GiB of
work for that envelope and build/test scratch. These are conservative planning
inputs, not measured upper bounds for arbitrary complete games, datasets or test
fixtures. Before larger work, derive the extra margin from that task's measured
peak and existing scratch/output limits; record the selected bytes and conditions
in its task receipt. An artificial margin of `1152921504606846976` verifies
rejection without filling a disk. Restore the selected normal margin afterward.

P1 read planning uses immutable manifest descriptions before `read_payload`: the
snapshot selection, its direct projection payloads, the run's vocabulary/features,
and the exact selected checkpoint. Derived checkpoints include their original;
initial derivation uses the existing explicit source checkpoint DAG. Missing local
chunks are estimated by the same Store index/stat helper, with no tensor read or
raw-source expansion during planning. The later remote transfer is different: it
verifies full ancestry and may rehydrate missing source chunks. Its preflight also
budgets that local CAS growth; a failed prepared transfer keeps its original intent.

Hosted Linux CI places `TMPDIR/TMP/TEMP` in its checkout's ignored
`.local/portable-tmp` before the selected gate. The observed system temp filesystem
had less than the unchanged 8 GiB requirement even while the workspace had ample
capacity. This selects the actual write volume; it does not lower the margin or
skip admission. Each isolated runner owns and discards its own scratch.

Preflight does not allocate filesystem blocks. One owner schedules shared heavy
writes and includes other active work; independently passing checks do not grant
concurrent capacity. A single unbounded recording or an unrelated process can
still exhaust space. The Runtime's separate single-run capture limit is not fixed
by adding free disk, and old unknown results remain unchanged.

At task close, write `CLOSEOUT.md` with `closed_at`, result/source/PR links, exact
R2 artifact IDs and normal restore entry, allowed rebuildable leaves and required
retention. After seven full days the rebuildable leaves may be cleared only when
inactive, untracked and reconstructible from retained sources/locks. Whole task
folders still require a reviewed deletion table. This is the existing work rule,
not another cleanup service or permission to remove unique data. A new task reads
the closeout and verifies current processes/Git/receipts before acting.

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
including each exact bucket/endpoint/prefix selected now. At most two existing
locations may be pinned. Use `--additional-peer` for a second local location,
or `--additional-peer-config BINDING.json` for the existing non-secret R2 peer
binding returned by recording maintenance status (kind/bucket/endpoint/prefix/region). The current
recording prefix `p1-native-preservation/v1` and compute prefix `stpd` are distinct;
configure both when that Store consumes both. Only an exact `object_not_found`
permits checking the next declared location; transport or integrity failures stop
the read. No provider, bucket, prefix or artifact is guessed. It is not a Curation or
transfer ledger. Reopen the normal Application/reader after configuring it. A local
miss on an exact manifest, payload index or chunk reads that same key from that
peer; normal manifest/chunk/whole-payload validation remains mandatory. A corrupt
local object fails and never silently substitutes remote bytes. A writable reader
caches verified bytes in the same Store; a read-only reader consumes the verified
peer bytes without caching data objects. An unavailable peer or missing remote key
returns the original typed Store error. All prior artifact IDs and reader entry
points remain unchanged. `cache-configure` prepares per-payload lease files for existing publications;
new writable publication prepares its lease during payload verification. Read-only
consumers only open an existing shared lease and do not create files. A missing
lease returns `cache_read_lease_missing`; have the writable Store owner configure
or publish/verify that payload first.

```sh
python -m spireagent.workbench cache-trim --store STORE
```

The same recording-maintenance owner schedules this trim when enabled and idle,
using its persisted operation/receipt. It removes local chunk copies only after
remote manifest ancestry and complete referenced payload readback succeed. Recent
artifacts, newest two maintained recording originals, all local models/checkpoints/
results, explicit pins, shared chunks referenced by them and active readers remain
hot. OS-held payload leases cover a reader generator's lifetime and release on
process death; remote readback does not hold a Store-wide exclusive lock. Manifests,
payload indexes, Curation and application operation files remain local. A missing
remote object retains the local chunk and appears in `unverified_chunks`.

The receipt reports actual before/after payload bytes, protected identities, busy
chunks and `within_budget`. Metadata, retained models/checkpoints, unverified data
or active readers can keep the Store above its target; this is reported rather than
deleting protected data. Maintenance reports `cache_over_budget` while protected bytes prevent its target.
An active or not-yet-preserved chunk schedules another actual readback after a
60-second backoff; an unchanged retained-only overage waits for changed data or
policy. Time schedules that check and never counts as successful verification.
Reading cold data can temporarily exceed the working-set target, and the next idle
maintenance pass trims it. This bounds evictable Store payloads as well as expanded
recording copies; it does not guarantee an unlimited offline buffer or constant
whole-disk usage. Use the capacity reserve for new recording, input/checkpoint and
installation scratch, and stop or increase capacity if protected bytes exceed it.

For checkpoint/model/result artifacts, use the existing generic `workbench push`
and `pull --artifact ID --other-store s3` transfer; they retain manifest ancestry
and transfer absent objects. Materialize and verify through the owning model or
checkpoint reader before declaring a result usable. The P1 original-only storage
entry above does not pretend to validate model semantics.

R2 is the long-term destination for project data; local bytes are the bounded
active working set goal. Expanded-copy cooling removes verified older recording copies. The optional
Store cache policy above additionally trims recoverable compressed/raw, derived
and input payload chunks; protected model/checkpoint/result bytes and metadata
remain resident and can prevent reaching its target. Background closed-recording maintenance is separately
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


## Private Scheduler and provider-handle recovery

The normal `operations` entry preserves the existing `Operations.backup` SQLite
snapshot in the same ManifestArtifactStore. It includes jobs, fenced attempts,
original provider handles, outcomes and operation events. The snapshot is paused;
local lease hashes are cleared in the copy and deleted database pages are vacuumed.
The copy also clears only the rebuildable `console_artifacts`,
`console_artifact_policy` and `console_lineage` projections; their owner can rebuild
them from Store manifests with `ConsoleIndex.artifact_closure`. The live database
is unchanged. Account/device/upload/collection/visibility and unknown tables must
still be empty, otherwise publication is rejected. Restore retains the same strict
private-scope validation. Control tokens, environment credentials and target/secret
configuration are not exported. Curation remains the separate original private owner and uses
its backup command above.

```sh
python -m spireagent.workbench operations backup --config PROJECT --store STORE --output SCRATCH
python -m spireagent.workbench push --store STORE --other-store s3 --artifact BACKUP
python -m spireagent.workbench operations restore --store s3 --artifact BACKUP --output NEW_RECOVERY_DIRECTORY
```

Restore checks content hashes, SQLite integrity/schema, private scope and paused
state in a new directory. It never replaces a live owner or contacts a provider.
Keep the original source/target/compute-Store binding, stop the original writer,
review this staged owner, and reconcile its exact recorded handles before enabling
dispatch. An unknown external outcome remains unknown; restoring bytes does not
permit resubmission. Backup availability and actual worker recovery are distinct.
