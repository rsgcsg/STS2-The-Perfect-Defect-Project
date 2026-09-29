# Project console: local collection and cloud visibility

The project uses one browser interface in two modes. The local console runs beside the
collector. Its personal project views and the cloud console use the same account and scoped
Hub projections. Its additional local scope shows unuploaded records and the owned queue.
Personal sessions, device upload credentials and native evidence authority remain separate.
[ADR-0005](adr/0005-local-cloud-console.md) preserves the original console boundary;
[ADR-0006](adr/0006-project-members-and-local-models.md) defines current member/admin and local
model capabilities. These pages describe current source, not proof that an older deployed
release has the new UI. Use a qualified exact release combination and its service/Human receipts.

Start with the [default project workflow](B_PIPELINE_HANDOFF.md) to obtain an approved exact
combination, install it and complete daily recording setup once. This guide owns screen meaning and
account/recovery behavior. The project cloud entry is [hub.2-fire-2.com](https://hub.2-fire-2.com/);
cloud project data requires an invited login. A successful deployment does not authorize
collection or make historical receipts current evidence.

## Daily collection

Opening the local Workbench first shows **本机工作台**, a no-login navigation page for
the existing local model view, the registered research workspace, and an
explanation of game-side Human recording. It does not query cloud identity or start any
game, model, recording, or upload operation. Use the top-right cloud link only when you
want shared project data; the link is absent when no Hub is configured. Existing direct
links such as `?view=campaigns` remain available and may require the configured project
account for collection setup and shared authorization.

The recording page describes automatic delivery only from the observed local preference
and delivery-process state. If delivery is not configured, enabled, and running, the UI
does not claim that new recordings will upload automatically. Recording itself remains
under the in-game Mod's explicit Human controls.

Run the approved developer combination through the existing entry:

```bash
uv run --locked python -m spireagent.workbench project doctor --config /ABS/project.json
uv run --locked python -m spireagent.workbench project open --config /ABS/project.json
```

The browser shows the actual loopback address. Device and personal tokens stay in private local files; the device token is injected only
into the delivery child environment. The personal token stays in the local account BFF. It is not pasted into a web form and is never sent to a third-party browser
identity provider. After setup, ordinary uploads do not require a fresh cloud browser login.

The Human uses the qualified game Mod to record and presses Recorder **Close**. The delivery
service packs a sealed collection, uploads its bytes and reconciles the exact remote receipt.
The collection page separately shows recording quality, delivery and research use. A session
may contain several runs or a partial run; the unassigned session context is not another game.

Closing the browser tab does not stop delivery. `project stop --config /ABS/project.json`
stops the owned background service. After a computer restart or an explicit stop, reopen the
same configuration to resume sealed work. This is not an installed OS autostart service.
Local raw sessions and bundles are not automatically deleted after cloud success.

## 本机资料

The catalog offers **录制 / 数据集 / 模型 / 报告 / 全部** shortcuts in the same
workspace. These select metadata categories, reset pagination and the advanced kind
filter, and retain the visible search term and selected recording IDs. **录制** includes
local-verified and received-bundle evidence; a quarantined received bundle remains
visible. Category membership never grants Human origin, training use or model readiness.
Only the existing eligible local-verified sources expose the Human-input selection control.
The GET category filter combines with kind/search before total counts and pagination;
unknown categories are rejected. Switching categories does not publish, import or train.

The local-only **本机资料** page uses the local browser session and needs no project login or Hub
connection. It reads artifact manifests, names, safe metadata, parent IDs, payload roles, hashes
and sizes through the existing local inventory and artifact routes. GET requests do not create
or repair storage, scan other directories, read payload bytes, prepare a dataset, or start training.

If private `project.json` has a `research_workspace` entry, that configured store remains the
selected source. Browsing is read-only; explicit preparation and Dataset commands are separate.
The page does not replace or hide that store with a managed
workspace. With no configured `research_workspace`, the first visit reports that no local
workspace exists; only an explicit **新建本机工作空间** action creates one. The Workbench creates
an empty store and supported SQLite index under its state directory, then atomically records a
versioned registration. Later inventory and artifact reads use those same existing routes, including
after a Workbench restart. Creation does not import data or make it trainable.

An invalid registration, marker, store, or index is shown as unavailable and is never silently
replaced. Interrupted initialization directories are retained and reported; the page does not
adopt arbitrary old directories. Keep private local paths out of Git. An artifact's presence in
this inventory alone does not make it a usable training set, installed model or validated result.

The page also has an explicit **查看录制来源** / **刷新录制来源** action. It uses the registered,
byte-verified CollectionTool and its read-only Game Mod setup status to find the owner-reported
recordings root; it does not read Game Mod configuration files directly or guess a default path.
The action lists only immediate session directories whose recording manifest and matching Close
receipt are present. It never packs, imports, rebinds, starts the game, or contacts Hub. A listed
Close marker is not a verified Evidence bundle or proof of Human origin. Missing tool registration,
unavailable owner status, and an unavailable recordings root are distinct states, not empty lists.
The page keeps the last explicit observation while it refreshes other content; clicking either
source-list action performs a new bounded owner check.

For one listed closed recording, the local browser can explicitly attest Human origin and
request **验证并导入本机**. The checkbox starts unchecked. The authenticated command rechecks the
current Game Mod owner and both Close metadata hashes, invokes the registered byte-verified
CollectionTool, then verifies the produced archive with the typed Evidence verifier before
publishing an immutable `stpd/local-verified-bundle-v1` evidence manifest into the selected
existing local research store. It never edits the raw recording. Stable worker/campaign labels
are private local grouping labels, not account or person identity. The long-running command has
persisted pending/completed/failed/interrupted status; page refresh never retries it.
This local manifest has no Hub receipt and is not itself a Dataset or training input.
The separate purpose-bound Dataset path below rechecks the source before selecting samples.

Selecting that exact local-verified evidence artifact exposes an explicit **预览样本** command.
The local browser POST starts one bounded background read of the selected store; GET only reads
the current in-memory status and never starts projection. The command rechecks the manifest,
transfer inventory, archive, original recording/Close hashes, and typed Evidence bundle. It
reports verified accepted Human input-label counts separately from canonical decision-record
counts and displays nonaccepted input reasons and verified decision dispositions. These counts
may overlap and do not establish complete trajectories, independent runs, Human uniqueness,
research admission, or trainability. The operation publishes no artifact or usage claim, does
not contact Hub, and leaves the store and raw recording untouched. Restart loses this ephemeral
preview status; another explicit command is required to recompute it.

### Prepare the existing library and fix a Dataset

Use **准备本机资料** once when the configured store does not yet have its local curation
owner. This explicit operation attaches the persistent usage ledger in place; it does not
move old recordings, copy model weights, or create a second old/new library. Readable existing
training/test Dataset-purpose assignments are registered; historical model/training use is
not reconstructed or asserted to be clean. Unknown old use restricts Gold claims without
blocking ordinary training/test selection after preparation. An existing legacy Gold
assignment instead requires explicit recovery before preparation can become ready.

From one verified recording's detail, preview its eligible canonical decisions, select
training/test/Gold purpose and explicitly create the Dataset. The result is an immutable
membership selection with source links, not another copy of the raw recording. The
Dataset detail shows sample count, purpose and split status. A set with only one independent
component can be saved, but that does not create an independent development partition.
Accepted Human input labels and canonical decisions remain separate sample types; neither
count may be silently substituted for the other.

The registry is a rebuildable catalog; the curation ledger is the durable use authority.
Refreshing a page neither creates a Dataset nor repeats a publication. A failed or interrupted
operation keeps its identity and reason; an explicit recovery checks for an exact existing
result before publishing anything again. Dataset purpose is not evidence of model quality,
native run independence or complete historical use across other stores.

### Run a local training operation

A claimed **training** Dataset has an explicit no-login command. The default is
D-Simple-S v1: width 48, one layer, two heads, feedforward width 96, three
optimizer steps and two CPU threads. Its engineering allocation admits at most
32 train and 8 development decisions. An explicit experimental D-Simple M2-K1
choice accepts only a published Human observed-input source with the same local
training claim and complete source index. M2 fits a fresh tokenizer on current
complete public pages, retains unlabeled observations and verified settling
provenance, and runs a bounded CPU episode job. It has no development split or
independent evaluation. Two recorder run IDs do not establish two games.

The owner records source/run training use before any derivative or tokenizer fit.
The default path keeps its public compact-v2 BC view and exact public-H bindings.
M2 uses the shared canonical observed-page projection and rejects whole episodes
or pages outside its declared limits instead of truncating. Its immutable run
input retains the tokenizer, observed-event map, projection config and source.

The store has one training slot shared across local profiles. GET, rendering and
refresh never launch work. A pending operation retains its identity; loss of its
supervisor is an unknown outcome, not permission to restart. A failure before a run
exists may be retried explicitly; an existing run with an unknown outcome needs
reconciliation first. Completion verifies the exact result and indexes its model.
The default recipe also indexes its development report; M2 records `not_run`
evaluation status and no evaluation artifact. Links open those artifacts in the
same local library. M2 remains a train-only engineering result, not policy quality
or scientific qualification.

An explicit model-page export can also package a completed M2 train-only model.
The Workbench rechecks its immutable completed run/result/model chain, the published
Human source, and exact training claim and use records for that operation before
starting a private export child. This admission remains valid after later local
trainings replace the current training-status slot. The exported package contains
only pinned model weights, tokenizer, projection/configuration and artifact IDs;
it excludes Human pages, the source map, optimizer state and active memory. Its
status remains `not_run` for independent evaluation. Registration is a separate
explicit POST: it rechecks the exported package and the historical training-use
ledger, requires an installed, explicitly pinned M2 Runtime profile and its
port-2 manifest validator, and probes the Connector SDK's atomic observation
context route. It adds a reviewed selection to the existing local roster without
loading weights or taking game control. The private `text-menu-m2-v1` Runtime
profile is separate from the Connector's unchanged `text-menu-v1` input profile
and the older token Runtime installation. Readiness and loading remain separate;
none of these steps proves independent evaluation or game qualification.

## Cloud login and connection

Choose **打开云端** from the local page, or open the configured Hub's `/app/` from another device.
Cloudflare Access handles browser sign-in; the Hub verifies its signed identity and applies
current Hub `member`/`admin` membership. Device uploads continue through `/v1/*`, independently
of browser cookies. Do not distribute Hub admin/R2/Modal credentials to collectors.

The local collection detail links to the exact remote `upload_id`; the content identity is
checked before remote detail is associated with a local row. The cloud page never reaches
into localhost or remotely controls the game. It can show received records while the collector
computer is off, but it does not know that computer's unuploaded queue. The device page shows an explicit last
contact timestamp when reported; that timestamp is not a current-online promise.

The public Hub landing page contains login and developer-guide links. The guide is bound to
the deployed public source revision (also public in health); no account/device/collection
data appears there. Only its packaged stylesheet is served anonymously. Protected routes are unavailable until
browser authentication is configured. Activation and role configuration are documented in
the [Hub runbook](../deploy/hub/RUNBOOK.md). A running API does not prove a successful browser login.

## Pages and interpretation

In **账号与电脑**, a computer entry means a registered Workbench profile. Two entries can
come from one physical Mac or PC when it deliberately uses two independent configurations.
Use distinct names and private config/state directories for different accounts. Each owner
uses their own device quota; signing out does not transfer a device or its existing data.
Reconnecting an owned profile uses its original account and does not consume another
device slot, even when new-device registration is disabled or the quota is full. An
administrator's project access does not let that account take over another member's
profile. When the browser has the wrong account, the connection page explains this and
offers sign-out; return to the originating local tab and reopen its connection link with
the original account. Keep the existing configuration, credentials and pending queue.

The Hub flow view retains `approval_allowed` and adds nullable `approval_block_reason`:
`different_account`, `device_disabled`, `device_unavailable`, `proof_changed`,
`device_claim_not_allowed`, `enrollment_disabled`, `device_quota_reached`, `expired`,
`flow_invalidated`, or `flow_not_pending`. The same owner eligibility decision gates
approval; reasons disclose no other member's email. Old Hub responses without the field
still show conservative reconnect guidance, not an invented exact reason. If an existing
private local account record contains an email, it is shown only as a historical hint;
missing emails are not inferred from computer names or device IDs.
The local queue belongs to that profile, while shared project views follow its signed-in
account. Multiple profiles can stay open in one browser. They do not isolate files from other
processes running as the same OS user; separate OS accounts provide that boundary.

| Page | Purpose |
|---|---|
| 真人采集（本地） | one saved daily consent, current readiness and persistent upload pause/resume |
| 模型实战（本地） | select and prepare a supported model, start test, take control and end test |
| 数据 | shared recordings, quality/statistics and downloads; local queue stays local |
| 数据集 | preview/select/freeze decisions or merge exact selected parent datasets; durable job progress |
| 训练与模型 | existing jobs, model lineage and downloads; compute still requires its owning budget gate |
| 评估结果 | local Agent reports and explicitly shared project reports, separate from Human collection |
| 账号与电脑（顶栏） | personal login and explicit computer pairing |
| 成员管理（管理员） | invitation, quotas and revocation |
| 设置与诊断 | detailed source/readiness, maintenance and administrator collection explanation settings |

Existing view URLs remain usable. Secondary statistics/download/detail views belong to these
main tasks. See [the unified member flow](UNIFIED_TASK_FLOW.zh-CN.md) for first-use and daily use.

“已录入” means durable canonical decisions. “真实失败” comes from Platform authoritative
disposition, not free-text error matching. Normal cancellations, diagnostics and unsupported
non-decisions are separate. Failed decision evidence may be successfully uploaded and remains
valuable for maintenance. “云端已验收” means receiver integrity/contract verification; it is
not a Human-origin, complete Full-Run, research-admission or model-quality certificate.

Unknown archival quality is shown as unknown. Global quality counters identify missing/partial
summary coverage; the first page is not used to estimate all history. **数据统计** distinguishes
received sessions, native runs and decisions. Facets are limited to actual owner-provided facts;
missing categorization or unuploaded local sessions do not become zero or guessed categories.
Detailed facets count projected canonical-record occurrences only where profiling succeeded;
they expose missing sources/unknown values and do not claim cross-source deduplication or
complete coverage of every recorded native decision. Research admission depends
on a selected input set and exact code, not just one uploaded collection. Dataset references
show actual use without inventing a current admission report for unassessed data.

Upload attempts include normal receipt checks. A stale cloud connection preserves the latest
confirmed receipt and its observation time. No percentage/ETA or historical phase timestamp is
fabricated. The list supports 25/50 rows; the search box explicitly filters only the current page.

## Recording setup and saved state

**真人采集** uses one daily default. Members read the purpose, upload destination and member
access explanation, then deliberately click **同意并开启采集** once. The existing owner records
all required declarations; visiting a page supplies none. The same saved purpose and sharing
scope do not require repeat checkboxes. Administrators maintain the default in
**设置与诊断 → 采集说明设置**. Old activity APIs, authorizations and uploaded evidence remain
readable, but activity publishing/joining is not a member workflow.

**准备 / 继续检查** composes the existing setup, binding and upload owners. It reports the next
necessary operation and never claims a pending stage is ready. Binding requires the game to be
closed; enabling uploads requires current native binding and preflight. Cloud views cannot bind
local files or start the game. Saved **暂停自动上传** survives restart, remains available while
offline or signed out, and does not delete queues. Resuming requires current authorization and
readiness. Exact endpoint/persistence behavior is owned by [collection flow](COLLECTION_FLOW.md).

Refresh and a new browser session read persisted consent/configuration again. They do not
repeat attestation or infer native readiness from a saved file. A stopped game may have a
configured destination without a confirmed loaded identity. A running uploader and a verified
remote receipt are separate observations. Missing or invalid state remains explicit. An existing
attached enrollment remains visible when it is older than the first page or no longer the default.

In **采集记录**, activity names come from the exact verified upload/device/enrollment join.
Unlinked history and not-yet-associated local rows stay explicit; names alone grant neither
sharing permission nor research admission. The current-page filter accepts activity names.

Daily template v2 separates consent/purpose from installed software. Software changes alone do
not renew that consent; every actual tool, native load and outbox keeps an exact identity.
Historical v1 templates keep their original software pins. An existing outbox is not upgraded
by registering another tool, and controlled rollover is not automated in this bounded workflow.
See [maintenance](B_PIPELINE_HANDOFF.md#daily-work-upgrades-and-incidents) before updating.

## Existing data and upgrades

New verified data materializes safe summaries through the owning APIs. Existing immutable
bundles and receipts retain their original IDs. To populate old local metadata, stop the owning
delivery worker and explicitly run the Platform `delivery_cli summarize` command described in
its version-pinned DELIVERY guide, then reopen the project. This re-verifies existing bundles;
it does not repack, upload, reinterpret failed dispositions or change prior receipts.

The Hub operator uses `console-refresh` to index existing verified artifacts. Browser GETs never
perform this work. Updating code or an index is not permission to enroll historical Human data
in a new consent scope. Upgrade the exact developer combination, never just a sibling import.

Category profiles are explicit, bounded owner work. Use `python -m spireagent.hub statistics-refresh
--upload EXACT_UPLOAD_ID` (or repeat `--dataset-id EXACT_DATASET_ID`), together with the deployed
state/store arguments, to materialize up to ten sources. The result distinguishes available
profiles from failed projections. It changes no receipt, Dataset admission or source artifact.
The administrator API `POST /app/api/admin/statistics/refresh` accepts the same bounded
`upload_ids` / `dataset_ids` selection with browser Origin/CSRF proof; it performs CPU work and
is not called by page refresh. No UI request infers categories from action labels.

New consented enrollments bind sharing only after exact receiver verification. For a historical
upload, an owner can use `python -m spireagent.hub collection-sharing --upload EXACT_UPLOAD_ID
--evidence APPROVAL_SHA256 --approve-sharing` only after reviewing its explicit scoped approval;
`--revoke-sharing` prevents subsequent export reads. Neither action rewrites old consent or
deletes data already downloaded. An automatic verifier retry never overwrites a revoked grant.

## Incidents and future work

Inspect the collection detail for the owning error, exact IDs and last confirmed stage. Preserve
the original recording, bundle and receipts. Public issue reports contain reviewed, redacted
summaries; private evidence is transferred only within its authorized scope. Repair the owning
repository, add a regression, publish a new exact candidate and canary it. Do not erase failures
or copy credentials into screenshots, issues or chat.

Ordinary members may use supported project actions and explicitly shareable data exports.
Research job submission remains with its existing CLI; viewing jobs is not launch permission.
Sensitive administrator actions require a current cloud-browser session; the local workbench
links to that screen rather than reusing its upload or personal token as an admin credential.
Membership removal is immediate at Hub authorization, without restarting a roster file. Turning
membership back on does not silently restore previously revoked computers. Jobs, budgets and
provider uncertainty still belong to their existing owners; no UI action bypasses compute
limits. Zero launch budget does not stop an already active provider job.

### Data selection and local model evaluation

**数据下载** freezes a bounded immutable inventory before downloading selected bytes. It does
not build a new training Dataset or silently repack all history. Accepted project recordings
and discoverable datasets are available to authenticated members without another publication
grant. Explicit source withdrawals, revoked membership and sealed test/Gold boundaries remain
effective. Quarantined recordings are not dataset inputs. See [ADR-0009](adr/0009-project-dataset-selection.md)
for the project access policy and compatible reading of existing export inventories.

**模型与评估** keeps cloud artifacts and local execution separate. A downloaded Full-Run model
can appear in the catalog while remaining unsupported for gameplay. Only a reviewed local policy
registry entry selects executable adapter code. Optional Runtime installation uses its exact
package inventory; collectors do not need model dependencies merely to record or browse data.
Readiness checks artifact/config/source hashes, the installed Runtime/Connector, Node, required
checkpoint evidence and backend. Model weights are checked during loading; native compatibility
is checked by the Runtime before decisions. A download or `ready_to_load` does not prove either.

The registered executable selection is the retained **S1 Defect A0 ordinary-combat** lane, bounded
by its exact policy support manifest (`combat_turn`, play/end-turn; no selector or Full Run).
It requires its original checkpoint/support files and CUDA/BF16 backend; a Mac does not become
compatible by installing the UI. Current Full-Run offline views have no qualified online-input
parity/adapter here, so **Full-Run online evaluation remains BLOCKED** until that separate owner
work is implemented and qualified. Do not manufacture missing features or filter the native catalog.

A compatible local selection loads in Human mode first. Shadow, One-Step, Auto, Human and Stop
use typed Platform Runtime requests; deliberate execution is distinct from loading. Cloud pages
never start a local game. An uncertain effectful response is not automatically resent. A workbench
restart requires exact instance recovery before new execution. Local Agent evidence and bounded
operation reports remain separate from Human collection, win-rate evaluation and scientific results.

Runtime commands use the versioned HTTP/2 mutation routes and the process run ID captured at
startup. The Runtime checks that ID before any command, including Stop during Workbench shutdown.
A different process reusing the same port cannot receive the old Workbench's command. An older
HTTP/1 server rejects the new routes; reconnect or upgrade explicitly instead of retrying delivery.
If Stop cannot be confirmed and the Workbench has no owned process handle that proves exit,
shutdown preserves the previous Runtime identity and requires recovery after restart. A failed
recovery observation also retains that requirement; it never turns an uncertain process into idle.

The system page is not an outside-host alerting service. A backup status is not the backup bytes,
and an SQLite restore is not whole-host disaster recovery. Missing evidence is displayed explicitly.

After upgrading checkout/dependencies, `project status` and `project stop` can still
address the predecessor's validated local runtime even if its combination is old.
Starting a new service, doctor and model downloads still require current setup.
Stop first, run explicit replacement setup with the preserved campaign/configuration,
rebuild summaries while delivery is stopped, then reopen. No manual process kill is needed.

The legacy Hub `verify_attempts` field counts operational verification exceptions
within the current retry cycle, not all verification invocations. A successful
verified receipt can correctly have zero. The UI labels it as processing anomalies;
explicit operator re-delivery resets the counter. Quarantine is a separate disposition.

## Download, sign in, bind once

Use the exact developer combination selected in reviewed release notes. Git, Python 3.11 with uv,
Node 20+ and the qualified Mod/fixed collection tool remain explicit prerequisites. The
workbench setup installs the locked `cloud` profile; daily collection needs no Torch,
Transformers, model weights, R2 keys or Cloudflare account. It uses the fixed collection tool.
Initial native installation and rollback still use an operator-retained Platform checkout on
the game computer; see [developer kit installation](DEVELOPER_KIT_INSTALL.md).
Full research/CI environments continue to use `--all-extras`.

1. From the approved STPD checkout run the launch command in the
   [project workflow](B_PIPELINE_HANDOFF.md#download-and-connect-once), retaining its explicit `--config` path.
   The launcher installs locked dependencies, preserves existing project settings and opens
   the same loopback workbench on subsequent launches. An upgrade first stops the exact
   predecessor and explicitly refreshes setup; the launcher never changes branches or rewrites it.
2. Open **账号与电脑**, name this computer, and choose **登录并绑定这台电脑**.
   Follow the displayed cloud link. Use an invited project email and its email verification code.
   The first verified invited login establishes the project profile immediately; device binding
   is a separate explicit step. Public self-registration is disabled.
3. Compare the computer name and pairing code on both pages, then approve. The local page
   obtains the grant itself; there is no copying tokens, callback URL or browser-local secret.
   The **查看范围** selector shows project-wide or one authorized computer's cloud data on
   either surface; **这台电脑** additionally shows the local queue.
4. Stop the workbench, register the trusted fixed tool, then reopen the same configuration,
   as shown in [daily recording setup](B_PIPELINE_HANDOFF.md#set-up-daily-recording-once-reopen-it-thereafter).
   Registration holds the stopped-workbench lock; closing a browser tab is insufficient.
5. Open **真人采集**, read the explanation and deliberately select **同意并开启采集**.
   Follow **准备 / 继续检查** through the necessary game-directory, stopped-game binding and
   fresh native checks. Reuse saved consent thereafter. Complete a bounded Recorder Close-to-receipt
   check before routine recording; no activity or repeated three-checkbox flow is required.

The device may remain authorized when the person logs out. Local logout clears personal pages
and revokes its short-lived personal session when Hub is reachable; otherwise expiry bounds
that remote session. It retains the upload grant. Closing the browser does not stop background
work; restarting the computer requires opening the workbench again. Account switching never
reassigns an existing device, campaign, bundle or outbox to another person.

## Credential recovery

Hub 401/403 becomes Platform's typed `auth_blocked`, separate from R2 transfer rejection or a
native recording failure. Correct the credential for the **same logical device** first. An
operator rotates that device in Hub and supplies a private replacement JSON containing
`hub_url`, `device_id`, `token`. Stop the workbench, then run:

```bash
uv run --locked python -m spireagent.workbench project credential --config /ABS/project.json --credential-file /PRIVATE/replacement.json
uv run --locked python -m spireagent.workbench project open --config /ABS/project.json
```

Choose **恢复已修正授权的上传** in **账号与电脑**. The workbench validates the actual Hub device,
stops the owned delivery child, invokes the versioned Platform `resume-auth` API under its
stopped-worker lock, then restarts the child. Exact archive/seal/upload/receipt identity is
preserved. Other incidents are not cleared; legacy free-text failures require separate owning
audits. Device token rotation never substitutes a new device ID or deletes transport state.

Credential publication fsyncs the file and, on POSIX, its containing directory before
acknowledging a pairing result. Windows retains atomic file replacement and process-restart
recovery; arbitrary filesystem or power-loss survival is not a qualified claim.

## Decision dataset candidate

[ADR-0007](adr/0007-fixed-decision-datasets.md) adds **对局与片段** and dataset creation.
After a shared verified upload, the bounded background worker prepares its run summary.
The game page shows the current available profiles, with incomplete/complete and unknown/win/loss
separated. Failed profiles do not change receiver acceptance. It never calls uploads unique games.

In **数据集**, select received recordings, keep permissive defaults or select optional filters,
then **预览选定记录**. The task updates in place; inspect counts/exclusions/run facts,
and use **按此预览生成固定数据集**. The resulting artifact ID identifies an immutable version.
The generated list appears as soon as its fixed manifest is published. A normal dataset download prepares Parquet on demand; Gold stays sealed. New uploads require a
new preview/build. Existing artifact detail links remain valid. Current project access is checked again; explicit withdrawal also blocks future downloads
of the corresponding derived decision bytes.

This candidate does not start training. The strict old Full-Run loader remains unchanged;
training entry points consume supported purpose-bound selections and reject held-out ancestry.

Member data calls have bounded transport deadlines (10 seconds for reads, 20 seconds
for submissions), separate from the four-second login polling deadline. Export creation
checks remote source manifests and current sharing grants. A lost submission response is
reported as **result unknown**, not a rejected operation; the client never automatically
resubmits it. These deadlines do not weaken source authorization or byte verification.

## Dataset library and preview cleanup

The dataset page opens the **已生成的数据集** library: published names (recovered from
the original durable build request), record counts, contract, split status, date and download.
Search matches names or artifact IDs across the indexed catalog, with pagination. Names
are presentation labels; immutable manifest identity and payloads remain unchanged.
A generated dataset is available to inspect/download, not automatically scientifically
qualified or compatible with every trainer. Strict Full-Run and decision contracts stay separate.

**新建数据集** selects recordings and rules; **预览与任务** shows bounded background
work and an explicit confirmation to generate. Category/run/exclusion reports are collapsed
until requested. Only the owner can remove completed/failed tasks from their own list;
**已移除** restores them. Batch removal affects only the completed previews displayed on
that page. Pending/running jobs cannot be hidden. Removal changes a personal durable
visibility row, never the job state/result, raw recordings, fixed datasets, or failure history.
Visibility is backed up with Operations and requires current membership and browser CSRF.

Native run coverage is distinct from strict research sequence proof. A fresh native start
and native terminal (victory **or defeat**) establish the observed boundaries; known
pause/reload/resume gaps are reported within that interval. Close only seals a recording.
The **对局与片段** view reports boundaries, recording continuity, outcome, and strict sequence
conditions separately. Source profiles refresh once in the existing bounded worker; old
profiles awaiting refresh report unknown, and older immutable selection reports keep their
original strict meaning. Several fragments never manufacture an uninterrupted game.

The game overview, preview detail and task lists return run summaries and
`journal_ref_count`. Full per-decision
references remain in original profiles/artifacts; the overview does not transport that
unbounded inventory or weaken the local response limit.

## Date selection and responsive navigation

**新建数据集** offers local start/end dates, all matching recordings (up to 100), a persistent
selection count and clear selection. Filtering covers the whole catalog before pagination.
The next-step button is above the source table. A newly submitted preview/build is followed
directly; failed previews offer **修改录制选择** while retaining the original failed task.
Dataset tabs show their own loading state immediately; slower old replies cannot replace the
selected tab. Task reads use indexed access rather than fetching remote source manifests.
Full original-byte validation remains in the background worker.

## Bounded dataset processing and stable refresh

The curation candidate adds a purpose selector (training, test, Gold) and an optional
paired training dataset dropdown. The library shows purpose alongside record counts.
Gold has no ordinary raw download; only Gold parents may compose a new Gold version.
Sealing cannot undo historical access. Original recording detail includes paged operation
annotations: flag a problem, exclude from future selections, or restore, with a reason.

New dataset publication fixes a compact selection. Download prepares Parquet in a durable
background task and then exposes that derived artifact. Switching tabs reuses short-lived
metadata scoped to account and viewing scope; mutations and authentication changes clear
that cache. Cached metadata never authorizes bytes or bypasses server isolation checks.

Dataset construction streams sources in archive-digest order and spools canonical rows to
private temporary SQLite storage. The maximum expanded source remains an independent
resource bound; this does not claim arbitrary archive sizes fit the worker. The private
verified index binds receipt archive digest/size and installed projection/code/lock identity.
Rows commit in small batches, with a checksum-bound completion header last; an incomplete
index never admits a source. Damaged or evicted derivatives reverify original bytes.
No HTTP endpoint accepts imported index rows or grants access from cached membership.

A fixed preview checkpoint references exact verified rows, source manifests and rules.
Confirmation checks its logical content identity and current access before publication;
it can skip repeated source download, verification and selection. Existing v1 manifests
and their independent reprojection retain their meanings. Deferred materialization and
new dataset purposes are separate work under root ADR-0010, not claims of this checkpoint.

Cancelled and failed jobs continue under the same ID when explicitly retried, retaining
the previous failure/progress in events. Attempt fences prevent an old worker from
changing a later attempt. A heartbeat is separate from observable work progress.
Cancellation is allowed before the final publication phase; after publication starts,
the UI waits for its outcome instead of claiming already-published bytes were undone.

Automatic dataset refresh retains the mounted panel and unchanged task cards. It does
not reconstruct a creation form. Navigation remains active while a status request is
pending, and a late response cannot update a different page/account. An authentication
denial clears private displayed data; an ordinary transient failure leaves the last
observation with an explicit notice. These are presentation changes, not cached access grants.
