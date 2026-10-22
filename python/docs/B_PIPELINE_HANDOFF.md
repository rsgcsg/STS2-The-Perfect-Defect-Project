# Project workflow: download, collect, view and maintain

This is the default developer workflow for the whole project.
All Python commands below run from `python/` of the one project checkout; native
`npm run game-mod:*` commands run at its repository root. Download/checkout once.
The [unified release acceptance](../../docs/MONOREPO_MIGRATION.md) records the deployed baseline.
The B pipeline is its current implementation: one Platform game Mod, one project workbench,
one protected cloud portal and
separately downloaded models. [B operations](CLOUD_PIPELINE_B.md) owns data/worker commands;
the [Hub runbook](../deploy/hub/RUNBOOK.md) owns deployment and recovery commands;
the [console guide](PROJECT_CONSOLE.md) explains the screens and account/device behavior.
Do not maintain a separate set of setup instructions in a campaign or chat transcript.

The project cloud entry is [hub.2-fire-2.com](https://hub.2-fire-2.com/). Its account page is
protected; it is not a public dataset browser. Select the exact supported combination from
reviewed release notes, then follow this guide. A branch name or an old private candidate ZIP
is not a release identity. Current bounded qualification is recorded in
[the monorepo acceptance](../../docs/MONOREPO_MIGRATION.md); older workflow reports remain historical.

Use the [root workflow](../../docs/DEVELOPMENT_WORKFLOW.md) for branches, releases,
compatibility and maintenance policy, and the [complete member/Agent handoff](../../docs/NEW_MEMBER_HANDOFF.zh-CN.md)
for first setup. Dated evidence remains scoped to its original release. Source documentation
does not imply a GPU/model or arbitrary-version qualification.

## The everyday path

For the current local P1 recording/Store destination, use the final P1 section below.
It requires neither a Hub login nor a campaign/Gold workflow merely to preserve an original.
The following retained member/Hub destination additionally owns device, sharing and upload consent;
its account rules must not be copied into the local P1 path or silently removed from real Hub callers.


| When | What to do | What confirms success |
|---|---|---|
| first install | obtain the approved Mod/tool and exact project checkout; run the launcher below | local workbench opens; project doctor passes |
| first connection | log in with an invited email; then separately compare the pairing code and approve this computer | the profile is visible immediately after verified login; the same named computer appears after approval |
| before first recording | open **录制与上传**, confirm daily Human/upload/project-sharing consent, prepare local files, bind with the game closed, then launch and enable uploads | persisted setup, current native root/identity check and delivery doctor pass; each is a separate stage |
| each collection | reopen the same workbench configuration, record in the game, press Recorder **Close** | collection detail reaches **云端已验收** with an exact remote receipt |
| inspect data | use **采集记录／数据统计／数据下载**; use **这台电脑** for the local queue | counts/coverage, upload receipt, explicit sharing and research use remain separate |
| finish or reboot | use `project stop` when stopping delivery; open the same configuration after reboot | retained sealed work resumes under the same device and IDs |
| upgrade or report a problem | follow the upgrade/incident procedure below | old evidence is retained and a new exact candidate has its own checks |

An uploaded collection is not automatically a Dataset or a training launch. The ordered
research gates below remain explicit, with compute launch budget zero until authorized.

## Download and connect once

1. Choose the reviewed release from [Project Releases](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/releases), or an
   explicitly non-stable developer combination. Verify its
   published hashes and supported operating system. Obtain this project at the release's exact Git
   commit, the single qualified Platform Mod, and the entire fixed collection-tool directory.
   Use an exact Git checkout for this developer distribution; a generated source ZIP does not
   carry the checkout identity used by the reviewed workflow.
2. Install Git, Python 3.11/uv, Node 20+ and the collection tool's declared .NET runtime once.
   The game must already be owned and installed. Install the Mod using the Platform release's
   exact install/load instructions; do not copy game files from another developer. Initial native
   installation is operator-assisted using a same exact project checkout; see
   [developer kit installation](DEVELOPER_KIT_INSTALL.md). The kit has no standalone installer.
3. From that clean project checkout, in its `python/` directory, open the workbench. Use the same explicit config path in
   all commands; the launcher's user-state default and the lower-level CLI default differ.
   The launcher normally uses `%LOCALAPPDATA%/spireagent/workbench/project.json` on Windows,
   or `~/.local/share/spireagent/workbench/project.json` elsewhere. Use the resolved absolute
   path consistently, and a separate private directory for another account. Replace
   `/ABS/project.json` below with that terminal's selected path; quote paths containing spaces:

   ```bash
   python tools/open_workbench.py --config /ABS/project.json --hub-url https://hub.2-fire-2.com
   ```

   The launcher installs the locked `cloud` profile and exact Node dependencies. It does not
   select a Git revision, install the game Mod, attach a recording directory or authorize a
   recording scope. It preserves existing configuration. Everyday collection uses the fixed tool;
   it does not read Platform source. Keep the operator's installation checkout for native lifecycle
   and rollback. Collectors need no model weights, Torch, R2 keys, Cloudflare account or admin token.
4. An administrator first adds the member email in the cloud **成员管理** page; no invitation
   mail is sent automatically. Open **账号与电脑 → 登录并绑定这台电脑**. Use that email and verification code,
   compare the displayed computer name/pairing code and approve in the cloud. Return to the
   local page; it stores credentials privately without copying tokens. The account profile
   appears at verified login, independently of device approval. Public self-signup is disabled.
   Hub is the only project membership roster. Login authorizes account views; the separate device grant permits background
   upload even after personal logout. It does not authorize recording or attest Human origin.
   A device is one Workbench registration, not a hardware fingerprint. Normally reuse one
   config on a computer. To use another account on the same physical computer, choose a
   separate private config/state directory and a distinguishable device name; do not copy
   device credentials or reuse another owner's recording/outbox paths.
5. Complete **录制与上传** below. The administrator provides a daily default; members do not
   need to create or select a topic activity. Native Mod installation still follows the approved
   Platform release. Save the launcher command with its exact `--config` path as this terminal's
   normal entry, and perform a bounded first Close-to-receipt check before routine collection.

Developers changing code and researchers running the full gate still use
`uv sync --locked --all-extras` and `npm ci`; the lighter collector setup does not replace CI.

<a id="attach-a-campaign-once-reopen-it-thereafter"></a>

## Set up daily recording once; reopen it thereafter

The cloud administrator opens **录制与上传 → 日常录制默认设置** and publishes a name,
description and consent text. This recommends one immutable template to new collectors; it
neither grants consent nor changes an existing local configuration. **高级选项：专题活动与已有授权**
contains optional topic activities and previous declarations.

First register the approved fixed tool. Registration requires the workbench to be stopped;
closing its browser tab is insufficient. Use the same private configuration created by the
launcher and the release's independently approved tool ID:

```bash
uv run --locked python -m spireagent.workbench project stop --config /ABS/project.json
uv run --locked python -m spireagent.workbench project collection-tool --config /ABS/project.json \
  --tool-directory /ABS/kit/collection-tool --tool-release-id EXACT_APPROVED_ID
uv run --locked python -m spireagent.workbench project open --config /ABS/project.json
```

Keep Node 20+ and the tool's declared .NET runtime on the local PATH. Register the complete
fixed directory, including its packaged setup helper and provenance. A missing helper requires
an approved tool update; a browser-provided path or release ID is not a replacement trust source.
Registration verifies bytes and saves the selected tool; it starts no game or upload.

In the **local** workbench:

1. Open **录制与上传**. Read the daily recording description and separately confirm Human origin,
   upload permission and project-member sharing, then choose **确认授权并继续**. These declarations
   are saved by Hub for this owned active device. They are never prechecked or inferred from login.
2. Choose **准备本机录制配置**. The service creates fresh private recording/outbox directories and
   an inactive configuration. Reopening the page reads the saved result; it does not create
   another enrollment or adopt historical recordings.
3. Close STS2, enter this computer's game installation directory and choose **绑定本机录制目录**.
   The packaged Platform helper checks the installation and binds its recording destination.
   This can report configured while the game is stopped; it is not proof of a loaded game.
4. Start STS2 and refresh. Proceed only when the current native connection and exact recording
   root are confirmed. Choose **启用本机上传**; the local service repeats native/preflight checks
   before attaching the configuration and starting delivery. Cloud pages cannot perform this step.
5. Record through the game's Recorder, press **Close**, and open **采集记录**. Confirm the exact
   cloud receipt for this first session. Home and **录制与上传** show setup and background status;
   the individual record shows packaging, upload and receiver acceptance.

A failed or unknown native check remains blocked. Keep incomplete preparation and its errors
for inspection. A bind request cannot silently replace a different attached recording config.
Do not bypass that guard with a different device, copied directory or manual outbox edits.

Daily templates use `stpd/collection-activity-v2`: purpose, consent and upload destinations are
separate from software identity. A software-only update does not itself require another Human
consent declaration. Changed purpose/sharing/consent publishes a new immutable template and
requires deliberate consent for that scope. Historical v1 templates retain their original exact
game/Mod/tool/source requirements; they are not silently converted to v2. This distinction does
not make an existing outbox upgradeable in place; see the maintenance procedure below.

An enrollment alone does not prove which remote upload belongs to it. Sharing admission requires
the exact verified upload/device and bundle campaign identity, or a separately reviewed historical
grant. Names in **采集记录** show verified association only; unknown association is not guessed from
timestamps, the current default or a similarly named campaign. Association does not grant access.
The local queue list stays available offline and does not query a cloud association for every row.
Open its detail to see the exact matched Hub association and dated cloud observation; a missing
local list label is not a failed enrollment.

For daily reuse, run the saved launcher command or `project open --config /ABS/project.json`.
The selected configuration and consent persist. After Recorder **Close**, delivery packages and
sends the sealed session automatically. Keep the workbench process running until the remote
receipt is observed. Its browser tab can close independently; there is no OS autostart service.

## One system, separate owners

| Owner | Delivers | Does not decide |
|---|---|---|
| Platform | one game Mod; fixed collection tool; Evidence package/outbox; policy lifecycle | research admission, model, training, GPU provider |
| Project applications (`spireagent`) | one Workbench/Hub, accounts, storage and provider operations | native or research truth |
| Research (`stpd`) | projection/Dataset, training/evaluation and model policy adapter | native rules, legal actions, Human/Commit/successor truth |
| Operator | exact approved combination, device access, budget, backup and deployment | rewriting failed evidence or changing a frozen experiment retrospectively |
| Model artifact | immutable weights, representation/support manifest and provenance | installing an unpinned environment or activating gameplay automatically |

Public OCI images contain public source and dependencies only. Runtime secrets, recordings,
operational databases and model weights are external. Public download grants no Hub/R2/Modal
permission. A private registry is a supported future deployment choice, requiring separate
read-only pull credentials and adapter/rotation tests; it is not a second training system.
Do not put private payloads in image layers and then delete them in a later layer.

## Release builder and operator responsibilities

B remains a developer distribution, not a one-click player installer. Platform developers and
release builders need its source; the operator also retains an exact Platform checkout on the
game computer for initial binary installation, lifecycle and rollback. The daily workbench uses
the fixed tool without reading that checkout. Components keep explicit contracts and artifact
identities within the one repository. The reviewed combination links artifacts without
floating dependencies or lockstep updates; root workflow owns release coordination.

The release builder produces the fixed collection-tool directory once, from a clean exact
Platform checkout, using its `publish:collection-tool` command. Distribute the entire directory,
its inventory and its independently pinned release ID. This command creates a local release;
it does **not** upload GitHub assets or prove any native Mod was installed. An adjacent manifest
alone is not trust. The operator records the approved release ID outside the downloaded files.

A reviewable terminal handoff contains:

- exact Platform workspace/component/BOM and installed Mod identity; exact collection-tool
  inventory/release ID and required .NET runtime; never the game binaries;
- exact project application commit/Python lock and developer combination, public Evidence dependency pin;
- public Hub URL and explicit HTTPS upload-host allowlist;
- Hub project membership invitation and browser-approved device enrollment (legacy private token
  provisioning remains an operator recovery route);
- immutable daily/optional-activity enrollment and explicit Human/upload/project-sharing declarations;
  private configuration, dedicated recording/outbox paths and startup/stop/status commands;
- supported OS and tested gate, known limitations, rollback and incident instructions.

Do not distribute a developer's `.local`, `.env`, home directory, SQLite or outbox. Never give
collectors R2 access keys, the Hub admin token, registry publish credentials or Modal credentials.
A copied public handoff is a template; it cannot grant consent on behalf of another operator.

Publish the reviewed combination, inventories, hashes and supported OS in versioned release
notes before advertising a download. Include the exact Hub OCI digest/source/lock and rollback
pair separately from the terminal Mod/tool identity. A local generated kit is a candidate until
its distribution location and inventory are verified. The cloud landing page's guide follows
the deployed source; a Git merge alone does not update the running service or collectors.

## Build the reviewed developer download

Use the maintained offline builder from the clean exact project checkout. Obtain the Mod,
manifest, current distribution BOM and fixed tool from the independently reviewed Platform
release. Expected hashes/release ID come from that release's trusted inventory; do not simply
trust a manifest downloaded beside unknown bytes. The operator checks compatibility and OS
qualification separately. See the [metadata correction](evidence/B_WORKFLOW_PACKAGING_IDENTITY_CORRECTION_2026-09-13.md)
for why BOM file bytes and tool component/workspace revisions are distinct.

```bash
uv run --locked python tools/package_developer_kit.py \
  --mod-dll /ABS/STS2_PLATFORM.dll --mod-dll-sha256 APPROVED_DLL_SHA256 \
  --mod-manifest /ABS/STS2_PLATFORM.json --mod-manifest-sha256 APPROVED_MANIFEST_SHA256 \
  --platform-bom /ABS/platform-bom.json --platform-bom-sha256 APPROVED_BOM_FILE_SHA256 \
  --collection-tool /ABS/collection-tool --tool-release-id APPROVED_TOOL_RELEASE_ID \
  --output /ABS/new-developer-kit.zip
```

The output directory must exist outside tracked source. The builder rejects changed, extra
or symlinked tool files and existing outputs, and reads no credentials or cloud services.
It preserves the original tool manifest/embedded BOM, packages only named public inputs,
and binds the runtime dependency combination by bytes rather than inventing its rules.
Its receipt reports packaging facts; it cannot certify Human origin, install/load, CI or
service readiness. Identical inputs/source produce identical ZIP bytes.

After final gates, publish that new archive, its SHA256/size, `combination.json` inventory
and independently reviewed release notes as immutable GitHub assets. Download it back and
compare its hash before recommending it. Consumers verify the downloaded archive SHA256
against the trusted release before extraction, for example:

```bash
python -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('/ABS/new-developer-kit.zip').read_bytes()).hexdigest())"
```

The exact Git checkout is still the workbench executable entry; the ZIP contains the Mod,
fixed tool and identity/usage files, not a second copy of the workbench source or an installer.
A corrected release uses a new tag/asset identity; never overwrite the previous archive.

## First terminal and real-upload gate

1. Complete source review/root gates/latest-head CI in the unified repository. Prepare exact Mod
   and tool artifacts; an unmerged candidate is explicitly non-stable. Update consumer pins only
   after the referenced artifact is durable and reviewed. Requalify an affected Hub image at its
   new source/lock; old cloud receipts remain historical. No merge transfers runtime evidence.
2. Separate qualification from collection: preserve the synthetic Hub state/prefix and prepare
   fresh collection state/prefix with budget zero, private permissions, backup/status checks and
   no configured GPU target. The public service being healthy is not a Human-upload permission.
3. Invite the account, bind one revocable device through the workbench, obtain explicit
   Human/upload/project-sharing consent, then prepare its local recording config. Use a **dedicated
   initially empty recording root** and a separate new outbox. The current delivery service
   discovers every sealed session under its configured root: do not aim it at a historical
   archive. Bind the game recording destination through Platform's supported profile/runtime
   configuration. Do not infer campaign membership from timestamps or copy/mutate sealed files.
4. Confirm the exact currently loaded native identity/root, then explicitly enable uploads.
   The local activation runs the owning delivery preflight before starting its child. Inspect
   source/tool/pin/.NET/config/token checks and the discovered-session count; failed checks block
   activation. File preparation alone starts no collection or upload.
5. With the saved declaration and exact workbench/delivery process in place, the Human records
   a new bounded session and presses Recorder Close. The agent never supplies gameplay or
   the Human's consent declaration.
6. Verify the local seal, immutable outbox/archive IDs, public Hub receipt and R2 manifest/bytes.
   Repeat a client restart to recover the same receipt. Confirm failed native decisions remain
   failures, pending means pending and no historical session was unexpectedly enrolled.
7. Retain local raw evidence and the verified bundle; do not auto-delete either. Back up the
   operations DB and record exact source, loaded runtime, tool, campaign and upload identities.
   Mark only this bounded Close-to-remote gate passed. Collection at other terminals/OS versions
   needs the same preflight and a bounded first upload.

Current terminal commands are in [B operations](CLOUD_PIPELINE_B.md#developer-terminal).
`project open` supervises delivery; closing a dashboard tab does not stop it. Use `project stop`
for its owned background process. Offline sealed work reconciles when the same config reopens.
The Human can keep playing offline; upload state is not native decision validity.

## Daily work, upgrades and incidents

Before distributing a new combination, stop the owned local process, retain its configuration,
credentials, raw sessions, bundles and outbox. Prepare a new durable checkout at the reviewed
exact revision and retain the old checkout for rollback. Use explicit replacement setup with
the same state/campaign settings, run doctor,
then reopen. The launcher does not silently replace a changed combination. Recheck local/cloud
identity and receipt continuity; repeat the owning native or delivery canary when affected.
Roll back with the recorded compatible source/tool/config pair, never by rewriting evidence.

For a project application configuration/combination refresh, stop first and preserve every configured value:

```bash
uv run --locked python -m spireagent.workbench project stop --config /ABS/project.json
uv run --locked python -m spireagent.workbench project setup --replace-config --config /ABS/project.json \
  --state-dir /ABS/existing-workbench-state --hub-url https://hub.2-fire-2.com \
  --delivery-config /ABS/existing-delivery.json --platform-url http://127.0.0.1:PORT
uv run --locked python -m spireagent.workbench project doctor --config /ABS/project.json
uv run --locked python -m spireagent.workbench project open --config /ABS/project.json
```

Use the actual existing delivery path and platform URL; omit an option only if it was deliberately
unconfigured. Replacement setup uses the supplied values, not a merge with omitted settings.
This refresh does not rewrite the Platform delivery config or change an existing queue's tool.

**Existing outboxes keep their exact tool release.** Preserve that complete tool directory with
the queue. `project collection-tool --replace-tool` changes the registration for new preparation;
it does not change a saved preparation, replace the uploader's tool, or migrate pending work.
A still-valid attached tool continues to serve its existing queue. Do not edit the queue's release
ID or point a fresh uploader at the old recording archive to force an upgrade.

For a software-only update of an attached **v2 daily enrollment**, the operator uses the
stopped-workbench `collection-upgrade` procedure. It keeps the old raw data, queue and tool;
only a newly prepared generation receives future recordings. No new member, device, template
or consent declaration is created. This is an explicit maintenance command, not an automatic
upgrade when the game version changes. Legacy v1 constraints remain unchanged.

1. Stop the game and workbench. Upgrade to the approved project application combination with the existing
   config values as above. Install the approved compatible Mod through the owner lifecycle,
   keeping the game closed. Register its complete tool with `--replace-tool`.
2. Use the existing enrollment ID from the collection settings' technical details. Prepare:

   ```bash
   uv run --locked python -m spireagent.workbench project collection-upgrade --config /ABS/project.json \
     --enrollment-id EXISTING_ID --tool-release-id NEW_APPROVED_ID --phase prepare \
     --game-directory /ABS/Steam/GameDirectory
   ```

   Evidence must prove that every old session is sealed, enrolled and **verified** with matching
   raw/bundle/archive/receipt bytes, under the stopped-worker lock. A verified bundle containing
   an honest recording failure is retained and does not block retirement. Pending, rejected,
   unknown, tampered or unsealed work blocks this command. Resolve the actual owner failure;
   do not delete files or change queue identities to force success.
3. Preparation creates fresh `generations/<tool-release>/` recording/outbox paths under the
   same enrollment and binds that root through the stopped-game owner. It leaves the active
   ProjectConfig unchanged and starts no upload. Cold-load and verify the new Mod through the
   Platform lifecycle. Do not begin recording yet.
4. Activate the exact prepared generation:

   ```bash
   uv run --locked python -m spireagent.workbench project collection-upgrade --config /ABS/project.json \
     --enrollment-id EXISTING_ID --tool-release-id NEW_APPROVED_ID --phase activate
   uv run --locked python -m spireagent.workbench project open --config /ABS/project.json
   ```

   Activation rechecks old completion, the unchanged proposal/config, fresh new roots, current
   native binding and doctor before atomically selecting the new delivery config. Failure leaves
   the old active pointer and all files intact; no newer directory is selected by timestamp.
   The command does not itself start delivery. Reopening starts the one selected worker.
   Run the bounded native/Close-to-receipt canary required by the release.

The local queue view follows its selected generation; old records remain in the cloud's member
collection view and in their retained local directories. Use the old exact config/tool to inspect
an old outbox. Preserve `upgrade.json`, old/new config and lifecycle receipts for recovery.
If preparation is interrupted after native binding, keep the game/workbench stopped and inspect
that exact generation; a prepared directory is not activation evidence. Never resume normal
recording until current-root and activation checks pass. Reverting software also requires its
compatible native/tool/config pair and explicit stopped-process checks.

| Event | Required workflow | Retained evidence |
|---|---|---|
| ordinary code change | owner branch/PR, falsifying regression, common gate, fresh actual-diff review, latest CI | exact source/test receipt |
| Platform native or Mod change | Platform exact build/install/cold-load and owning Human gate when applicable | old/new BOM, artifact and loaded identity; no gameplay claim from portable tests |
| collection-tool/Evidence change | verify new bytes and affected contracts; retain an existing queue's tool; explicit v2 completed-queue generation rollover | old tool, old outbox and original failed receipts |
| Hub/worker update | pause dispatch, verified private backup, exact source/lock/image, compatible schema, fresh service smoke | old image/config, closed snapshot, new load receipt |
| failed upload | inspect typed incident; fix transport or owning verifier; explicit retry only where supported | original bytes, intent, receipt and error code |
| native recording defect | report to Platform with exact version and private session evidence | original failed decision/lineage; no cloud-side repair of native truth |
| research projection/model defect | report to STPD; new versioned derived artifact/run after regression | immutable source and previous results, including failures |
| lost/revoked device | revoke its token; issue a new device credential via operator procedure | source data and audit events are not deleted |
| Hub loss | restore private closed DB into fresh state, paused/budget zero, reconcile actual remote attempts | tested recovery receipt and old state; Registry is not a DB backup |
| ambiguous GPU submission | reconcile provider-owned execution/stop truth before new attempt | durable attempt/fence; elapsed time is not proof of stop |

Use local typed status for everyday diagnosis; it is not a substitute for an independently
configured alert destination. Do not claim off-host outage notification until that destination
and failure delivery have actually been tested. Whole-host recovery includes external config,
secrets, certificate state and provider access; a SQLite restore alone does not prove it.
Retain complete incident evidence privately. Public issues/PRs contain redacted findings and
identities, never raw sessions or credentials. Reproduce on the exact version, fix the owner,
add a regression, publish new artifacts, then canary before expanding distribution.

The operator performs a daily check until an external alert channel is separately configured:

| Check | Where | Action when missing or wrong |
|---|---|---|
| cloud reachability and exact producer | public `/health`, then authenticated **系统** | inspect DNS/TLS/host and the deployed digest; a healthy API does not prove login |
| stuck delivery or native failures | **采集记录** detail, last observation, receipt and local queue | keep original bytes; route by the first failing owner rather than re-recording a success |
| budget, pending age and disk pressure | **作业／系统**, `hubctl status` and host disk metrics in the runbook | keep dispatch paused/budget zero; investigate before increasing limits |
| backup freshness and timer | runbook `maintenance.py status` and `systemctl list-timers stpd-backup.timer` | inspect the failed stage and verify a new off-host backup/readback |
| recoverability | isolated restore drill after deployment/schema change and on the operator's review schedule | retain image/config/secret recovery material; prove restored state before trusting it |

Backup failure/status is persisted, but no email/webhook/off-host outage alert is promised.
The console cannot announce its own dead host. Save a redacted issue with exact combination,
OS, timestamp, collection/upload/receipt IDs, typed error and reproduction; link private evidence
through the authorized channel. Never include credentials, signed URLs or raw Human payloads.

One Hub with SQLite and one active verifier is the deliberate initial scale. Measure queue age,
verification wall time, disk/scratch and worker memory before raising limits. Many collectors
upload directly to R2; they do not each need another Hub. If demand outgrows this measured
capacity, replace the staging/provider/operational mechanism behind existing contracts. Do
not add a second native ledger, research admission service or scheduler source of truth.

## When to freeze the first Full-Run training plan

Freeze the **pipeline and protocol skeleton now**: ownership, immutable IDs, candidate-catalog
contract, complete-run accounting, independent split rules, budgets/stop conditions, evidence
levels and test-set isolation. This does not choose a winning model or establish sufficient data.
The historical combat-v0 10-configuration study and S1 checkpoint are separate preserved lanes;
they are not silently promoted to a Full-Run v1 protocol.

Freeze the **complete executable v1 experiment plan after** real collection/admission profiling
and a separately authorized bounded worker engineering canary, **before** architecture comparison,
selection training or inspecting final holdout outcomes. Keep the pipeline repair PR free of
model-selection changes. The research plan is a reviewed STPD protocol/config PR with:

- immutable admitted Dataset/source IDs, rights/attestation, complete run counts and coverage;
- whole-run/duplicate-component train/dev/test split and sealed test/Gold access policy;
- exact game/Platform/tool/STPD, representation/tokenizer/Qwen/weight pins and supported domains;
- measured candidate/token distributions and fixed representation/overflow policy; no hidden
  truncation or legality reconstruction to make large examples fit;
- primary task, baseline/control families, seeds, optimizer/schedule/steps, checkpoints and
  deterministic resume semantics; Human imitation is not an optimal-action label;
- resource/elapsed/credit limits, cancellation/retry/unknown policy and engineering stop gates;
- predefined evaluation metrics, uncertainty reporting, selection rule and failure thresholds;
- model artifact/adapter support contract and bounded local Shadow/One-Step/Auto evaluation plan.

Three independent run components are only the current technical minimum for train/dev/test,
not evidence of a statistically useful population. Choose collection targets from measured
surface/selector/potion/event/rest/rapid-path coverage and run diversity, not an arbitrary
record total. Additional data creates a new frozen Dataset; it never changes an in-flight Run.
A later protocol amendment creates a new identity and explains what must be re-evaluated.

## Ordered remaining gates

| Gate | Exit evidence | Human/external dependency |
|---|---|---|
| engineering/operations closeout | exact reviewed sources, current CI, deployed image, backup/status and terminal preflight | existing authorized host/accounts only |
| first real Close-to-cloud | new immutable session -> identical local/remote IDs and receipt; no unexplained enrollment | Human gameplay and scoped upload/attestation |
| real Dataset freeze | independent complete runs, coverage/profile/admission/split report | enough authorized real data |
| bounded worker canary | exact image/Qwen/input, budgeted invoke, result readback, checkpoint/recovery | explicit compute allowance and qualified weights |
| full v1 protocol freeze | reviewed executable plan above; sealed test policy | scientific scope/budget approval |
| v1 training and offline selection | immutable results and predefined dev/control criteria | authorized compute; no policy-quality inference from job completion |
| local model evaluation | exact model download + compatible adapter; Platform-owned runtime canary | separately authorized Human/runtime gate |
| cloud game/inference/RL | separate ADR, authoritative environment/license/resource qualification | outside B v1; no simulated legality shortcut |

Training can finish without a supported Full-Run gameplay adapter. Downloading a checkpoint or
inspecting `project policy` never loads a model into the game. Build and qualify that adapter
against the complete Platform catalog before claiming the last mile is complete.

## Capacity and admission are stage-specific

The Hub transport accepts at most 512 MiB compressed, 2 GiB expanded and 50,000 files.
The current STPD bundle3 research adapter independently bounds expanded input to 256 MiB
and 20,000 files. A structurally verified receipt can therefore precede a size-related
projection rejection. Expose both stages; measure the first real complete-run bundles before
changing limits, and rerun resource/abuse regressions for any increase. Do not turn a transport
success or an automatic retry into research admission.

## Shared local and cloud workflow

Use the [project console guide](PROJECT_CONSOLE.md) for screen interpretation and
[ADR-0006](adr/0006-project-members-and-local-models.md) for current permissions and ownership. The same console serves
local device status and the protected cloud portal. The invited account gives both shells the
same authorized project view; an independently retained device credential owns background
upload. The cloud cannot control the collector or inspect its unuploaded local queue.
Deployment of code does not qualify a Cloudflare Access application or an actual
browser login. The Hub runbook records that external activation gate. Deliberate member exports,
activity enrollment and local model controls are separate from viewing pages; none bypasses
consent, native compatibility or compute authorization. Opening a page never starts a GPU.


## Partial recordings and practical maintenance

A failed decision does not make every other decision worthless. Keep the original recording
and all owner dispositions, upload it when transport/integrity/consent checks pass, and inspect
**采集记录 → 详情** for canonical, real failures, cancellations, diagnostics and owner reasons.
The project data statistics aggregate known quality counts; unknown is not zero. The current
**incidents** endpoint covers quarantined/failed delivery, not every recording defect. Check
collection quality too. There is no automatic issue creation or tested off-host notification.

For maintenance, keep one issue per owning failure mechanism with affected source/game/Mod
versions, private evidence/receipt references, decision IDs, reason counts and regression/fix
release. Public issues use redacted aggregates. Deduplicate reports without deleting evidence.
A correction produces a new artifact/report; it never makes an old failed receipt green.

The current implementation preserves and projects partial verified sources, but
`fullrun.data.admit` still requires complete runs and rejects unresolved/failed-closed sources.
It does **not** yet automatically admit the good subset of such a source for training.
The separate decision-dataset contract is specified by [ADR-0007](adr/0007-fixed-decision-datasets.md).
It adds preview/build and a reproducing loader without changing that strict training entry point:

- Admit a decision only from its own proved state, complete execution catalog, exact choice
  and necessary Commit/successor evidence; exclude the failed decision and dependent evidence.
- Preserve original run/occurrence IDs and parent/root lineage. Missing parent evidence or
  uncertain scope excludes affected descendants; a global integrity/identity failure blocks
  the whole unverifiable source. Never renumber surviving rows to disguise a gap.
- For history, memory and multi-step targets, stop at a gap and restart only at an independently
  proved boundary. Never stitch the previous successor to the next retained state. Outcome
  labels need their own witnessed terminal/run linkage; missing start is not a fabricated start.
- Freeze included/excluded IDs, reasons, source hashes, selection-policy version and counts in
  the derived manifest. Keep all parts of a run and duplicate-related components in one split.
  Report coverage and selection bias; rare failures must not disappear from benchmark reports.

The [decision-dataset closeout](evidence/DECISION_DATASETS_CLOSEOUT_2026-09-15.md) records its
bounded deployed selection/download acceptance. Research training use remains a separate gate;
selection/download tests are not evidence of a completed training run.
Do not relax the existing Full-Run contract to implement it. Collection can continue meanwhile;
verified storage, useful projection, training eligibility and complete-run certification are
four different facts. The [bounded acceptance](evidence/B_WORKFLOW_BOUNDED_ACCEPTANCE_2026-09-15.md)
records the owner's engineering release scope without claiming continuous Full-Run PASS.


## P1 originals and named projections in the existing local Store

Open the same fixed installed workbench with `project open --config /ABS/project.json`.
Keep its existing Store/registry binding and register a complete immutable CollectionTool by
its exact release ID. Opening/refreshing the page sends no Recorder command. Tool setup must
match the currently installed/loaded Mod; an older tool may still verify historical data while
its setup probe correctly reports `installed_artifact_mismatch` for the new Mod. Do not weaken
that identity check. Credentials remain environment-only; S3 reads use the configured bucket,
endpoint and prefix, not a guessed object key.

The unique application core is `workbench.p1_recording_store` (original/named publication,
materialize/query/catalog), `p1_recording_storage` (existing Store copy/readback) and the existing
`LocalRecordingImporter` operation. GUI, CLI and API are thin callers. `P1RecordingMaintenance`
only composes those operations for sealed candidates; Recorder retains its sole native lifecycle.
The registry/catalog are rebuildable indexes, not another source or a research-use ledger.

Enabled P1 maintenance preserves the original first, derives each observed named run
from its verified journal, then preserves every configured named projection and its
parent closure before marking that source complete. A no-run empty recording is
preserved without a fabricated run identity. The shared native read lease permits
the original C# packer to read the immutable lease file while excluding a native
writer; the managed consumer lock remains exclusive. Cooling reduces expanded raw
copies only. Compressed Store archives/projections still grow and are not silently
evicted; low-space failures retain the sealed source and original operation.

| Data | Explicit schema/profile and payload role |
|---|---|
| Original | P1-native bundle; `archive` + `transfer`; original producer/declaration/raw hashes/Close/journal retained; optional exact `preservation` parent |
| Old 03/default | `sts2.p1/native-segments-1`; `segments`; projection `native-selector-finalized-d-1` or `native-input-v1` |
| B* | `sts2.p1/native-targets-1`; `targets`; projection `native-timeline-targets-1`; timeline/effects/exclusions/masks retained |

New fixed Tools also classify an exact same-card select/deselect round trip on
the native combat-pile or simple-grid selector as zero-label presentation. Both
callbacks must be accepted, finished and nonterminal, on the same native selector,
parent and subject, with complete equal whole PB1/Reads/catalog endpoints. This
does not create a new P1 action, multi-card transaction or inferred Commit.
Smith/removal cancellation can include independently proved preview/return pairs
before its exact final child receipt. Missing native parent facts remain gaps.
The conversion receipt's `source_conversion_complete` is the converter report's
`complete` boolean, distinct from whole-run completeness and all N/next/end masks.
Existing originals and named projections stay immutable: select a new fixed Tool,
derive a new named projection through the same owner, preserve/read back it through
the Store/R2 operations below, and use its explicit ID in the existing reader.
Do not union superseded aliases into a clean-data count or change use/split admission.

Both named formats have one `source` parent, exact source content/branch/run, fixed Tool/Core
identity, `conversion_summary`, and derivation provenance. Select `(schema, projection)` to
obtain the role; never silently read targets as segments. Old artifacts are immutable and are
not automatically counted again when a newer view exists. Training selects/deduplicates explicit
native decisions; consecutive points or a complete archive do not establish a complete P1 chain,
replay conformance, Human origin, memory continuity or research admission.

From `python/` of the exact installed checkout (absolute bindings below are placeholders):

```bash
python -m spireagent.workbench recordings import-bundle --store /ABS/store --state /ABS/state --tool /ABS/fixed-tool --tool-release RELEASE_ID --bundle /ABS/sealed-bundle
python -m spireagent.workbench recordings derive --store /ABS/store --state /ABS/state --tool /ABS/fixed-tool --tool-release RELEASE_ID --artifact ORIGINAL_ID --projection native-timeline-targets-1
python -m spireagent.workbench recordings read --store /ABS/store --state /ABS/state --artifact PROJECTION_ID --output /ABS/projection.json
python -m spireagent.workbench recordings index --store /ABS/store --state /ABS/state --artifact PROJECTION_ID
python -m spireagent.workbench recordings sources --store /ABS/store --state /ABS/state --filter 'character="DEFECT"' --filter ascension=0 --filter 'result="loss"'
python -m spireagent.workbench recordings storage restore --store /ABS/store --state /ABS/state --peer s3 --artifact PRESERVATION_ID
```

These operations were exercised on real sealed data, including an empty-local-Store R2 restore
with every archive member hashed. Local `preserve`/`verify` and failure/reopen paths also have
bounded fixtures; that does not qualify a new production cloud upload or unattended daily run.
`import-bundle` accepts `--preserved-manifest`, `--preserved-archive`, `--preserved-index` to
reuse an exact archive without repacking. `publish-projection` accepts an existing owner output
via `--input`, `--expected-sha256`, `--derivation-receipt` and the same source/tool/projection flags;
it checks full bytes/identity and publishes through the same core, avoiding a second conversion.
The receipt binds source content, Tool release, output SHA/full readback and original diagnostics.

Storage actions are `preserve`, `restore`, `verify`, `reconcile`; peers are an explicit local
Store directory or the environment-selected S3 Store. Receipts record exact IDs, non-secret
location, full-member readback time and scratch/persistent/free-space budgets. An existing
`preservation` parent can locate the same archive before its semantic wrapper is published remotely.
`copy_artifact` transfers the exact parent closure and uses one payload temporary; materializing an
original additionally needs compressed and expanded space. Neither path promises zero disk usage.
Capacity checks live before the shared publication/copy/materialize, not only in the GUI.

`recordings reconcile` handles the saved import/derive/index intent;
`recordings storage reconcile` reads the saved storage destination only. Neither retries a write.
Known absence/incomplete data is saved as failure; transport/read uncertainty remains unknown.
The same operation file retains maintenance policy, receipts and other unresolved native intents
across CLI use and restart. Use a separate stopped state for offline writes: the running service
holds its state lock, and its GUI/API use the same serial importer.

Catalog indexing publishes a small analysis referencing the exact source/projection and payload
SHA/event/public-basis evidence; it rewrites neither original nor supervision. Character/ascension
come from available public facts; 03 results come from same-branch consistent segment end targets,
B* from its explicit end target. Missing/conflicting facts remain unknown. JSON integer filters
reject booleans; boolean filters reject integers. Seed/root/ancestry, language, mode or game version
are not inferred from session names or an empty modifier list. This display/search metadata is not
a split authority: the research consumer still requires its explicit immutable lineage contract.

Control still calls the same running Application:

```bash
python -m spireagent.workbench recordings control --config /ABS/project.json status
python -m spireagent.workbench recordings maintenance status --config /ABS/project.json
```

`start --origin declared_human`, `close`, and advanced `pause`/`resume`/`prefix` intentionally
change collection; they are not page refresh operations and collection pause does not pause the game.
Reopen the same config after a browser close/reboot; do not repeat setup or restart Recorder.

Maintenance defaults disabled. S3 enable requires an explicit candidate allowlist or a closed-time
cutoff plus origin declarations. The existing `maintenance` entry supports `--candidate-id` and
`--from-now --origin`; the GUI selects future sealed `declared_human` sources from service enable time.
This is scope selection, not proof of Human origin. Each new dispatch rechecks the current policy,
including a previously selected active job after policy change/restart; an in-flight/unknown original
operation keeps its original intent and must be resolved before anything else. Excluded old/engineering
sources do not get uploaded. Do not enable an entire recordings root to test a named permission.

At the 2026-10-20 audit stop, the normal application is installed and maintenance remains disabled;
full formal catalog→background cloud maintenance and recent-two-game hot-set cooling are not yet
qualified. Cooling needs exact recoverability, consumer release and named authorization; this code
does not delete original/history or stop a user's game. P1-aware initialization of the same existing
CurationOwner is also pending: preserve-only P1 data must not be forced through legacy
ResearchTransition preview or silently adopted into a second ledger. Actual populations remain
unassigned until lineage/use facts and deliberate admission are present.
The current preparation now indexes verified named P1 eligible-N projections through this same
owner, without claiming complete raw inventory or allocating a purpose. See the current
[Store/R2 operations](DATA_LIFECYCLE.md#daily-p1-store-and-r2-operations) and
[private Curation recovery](DATA_LIFECYCLE.md#private-curation-ledger-recovery); the historical
10-20 pending statement above is not an instruction to create another owner.


### 固定Tool与选定Host上下文

`project collection-tool`可用`--host-lifecycle <absolute current.json>`及`--native-log-file <absolute godot.log>`成对注册。它们是现Host公开上下文引用，凭据不进入注册文件。Catalog和CollectionSetup调用同一`inspect_collection_destination`，薄传既有公开setup CLI的日志和端口；原setup继续验证Mod/加载/代次。显式引用缺失、前后pid/runtime/endpoint变化会阻断，不退回默认Steam实例。未配置上下文的旧消费者与game-stopped bind/upgrade保持原合同。

游戏停止时，正常Catalog仍可按Tool报告并经验证的configured root只读查看库存；这不声称存在活动Host，也不授予Start、绑定或其它变更。活动Host仍须匹配选定PID、runtime和endpoint；配置在读取期间变化仍拒绝。

非GUI只读目录入口为`LocalRecordingCatalog(ProjectConfig.load(config_path)).read()`；它只观察当前固定Tool/原生owner、封存元数据及未封目录，不pack、复制raw、发送录制命令或赋予Human/研究资格。正常安装的2fa3723d已实际返回ready/current_runtime/33候选/3未封；Evidence精确来源8682d7e7，Tool为d25c0ab1。当前正常服务后台维护保持disabled。

当前源码已沿同一个长期Application和`LocalTrainingService`提供受支持的[非GUI训练控制](PROJECT_CONSOLE.md#non-gui-training-control)：`python -m spireagent.workbench training --help`。CLI与GUI调用同一任务状态和原有权限、预算、Stop/恢复owner；关闭客户端不丢任务，unknown不自动重投。`LocalTrainingService`和默认local选择只声明CPU；显式remote沿同指南的[既有Scheduler选择](PROJECT_CONSOLE.md#explicit-co-located-remote-scheduler)，实际GPU资格仍须另验。2026-10-21开发配置已实际走通start/status/pause/同Run resume/completed/reconcile及cancel保unknown；原固定安装2fa3723d未升级，不能把源码/隔离运行证据当该安装已支持新路由。
