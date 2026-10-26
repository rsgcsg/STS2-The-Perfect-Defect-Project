# Local model tasks and explicit shared reports

This is the owning orchestration guide for `workbench.local_models`,
`workbench.native_tasks`, `workbench.evaluation_sharing`, and
`hub.live_evaluations`. Root workflow and existing Policy/Evidence contracts
remain authoritative. This flow does not make arbitrary downloaded checkpoints
executable and does not qualify Human data or model game performance.

The common executable `AgentComposition` / immutable `AgentRunRequest` contract
for this same orchestration owner is defined in
[AGENT_EXECUTION_CONTRACT.md](AGENT_EXECUTION_CONTRACT.md). It includes model-less
Teacher and Public M2 admission, request/run association and their different Task
witnesses. This guide continues to own the existing preparation, control, Stop,
sharing and recovery flow; the common contract adds no second execution owner.

## 可续跑的评估链路

这是长期职责定义；本轮补Reference矩阵续跑和显式unknown放行，实际安装/运行与模型效果另验。
链路：录制 → 数据集 → 训练 → R2检查点 → 注册/完整Agent → 评估 → CURRENT顶部的分数板 → 下一轮训练决策。
评估消费Connector/Native/Policy Runtime的公开协议、动作和结果，不拥有合法性、游戏规则或第二个控制器；GUI、CLI、API映射同一Application用例。

|职责|唯一事实及现有owner|本轮最小接缝|
|---|---|---|
|计划|`stpd/evaluation/suites.py`的保留种子；冻结模型/输入、决策方式、角色、难度及运行条件|导入原矩阵和100槽位，原文件不变|
|槽位账本|持久化的槽位、投递事实、原report/收据引用；不依赖调度进程存活|补跨attempt账本，按原计划选择未开槽位|
|执行|`reference_suite.run_suite`及`ReferenceEvaluationService`沿原Model/native owner串行执行|复用原执行和完成局验证，不重写Agent或未知投递|
|环境占用|`LocalEnvironmentService`拥有原生资源身份、worker及Host生命周期|人显式封存unknown；证明无占用、追加记录落盘后才放行|
|证据和存储|原evidence-publish、Store/R2 typed读回与缓存owner|复用开局空间门；R2完整读回且无占用才冷却本机副本|
|报告|从账本和原证据派生的全分母统计；结果可重算|不反写账本；未完成也能报告，不把封存算成功|

这些是职责，不要求六个服务或六套schema；计划冻结、槽位事实追加，报告和缓存可重建。
槽位以计划摘要及原矩阵行绑定模型/输入身份、角色、难度和种子；本地selection只是执行引用，不能代替权重身份。
生命周期（未开/已投递/结束/unknown）与自然胜负、证据是否合格、环境是否释放分别记录，进程退出不能推导胜负。
首次投递前持久化意图；重复`run`只选择从未投递槽位，崩溃或响应丢失先核原请求，不自动重试或重开。
unknown只属于该槽位；新unknown停止当前调度，待人封存后可继续其余槽位，原unknown及失败分母永久保留。
`seal-unknown`是人触发操作：核expected应用/资源/generation/Actor身份，并当场证明worker、Actor、Host和游戏均无占用。
原请求、终态与证据不改删；单独追加封存/释放收据并确认落盘，任何身份、占用或写入证明不足都保持阻塞。
`evaluation plan/run/status/report`是薄账本入口；现有suite及`--completed-report`仍是执行和已完成原局复用入口。
原24负＋1unknown＋75未开按原身份导入；封存跳过unknown不等于将其伪装成completed-report合格局。
空间不足发生在新局投递前时保持未开并正常停止；若已有有副作用投递，按原owner事实记账，不能回写成未开。
冷却只移除有R2完整typed读回、空目录实际恢复/清单比对通过且无句柄的非活动副本；失败原件不因节省空间删除。

可比性分四组：被测对象/计划轴（模型、输入、决策、种子、角色、难度），协议（P1及版本/暴露），世界（Host、游戏、Native），运行条件（画面、速度、App、机器）。
模型等是分组比较轴，不能把不同模型混成一个胜率；配对须共享计划和其余受控条件。producer身份如实保留，不归一化。
不同协议默认不可比；跨Host须有在该协议及声明范围下的等价证据，Reference为基准，本轮不合并Managed。
跨画面/速度/App等运行条件须有明确等价证据才合并策略统计；协议观察和动作流一致的有限配对只证明其覆盖范围，unknown/崩溃率始终按实际条件分报。
现有`--completed-report`严格核同manifest/selection/profile/速度/画面/peer及原finish/保全收据，不因新App修改旧producer或放宽校验；native-ui会改变原生producer，沿已有Reference重新绑定入口处理。
绑定计划槽位的native-ui局计入评估，未绑定的是演示；界面不另长业务链。分数板列计划、开过、胜、负、unknown、替换和未开，并标证据缺口。
该历史 Reference 轮的边界：人显式`replace`追加关联的新槽位且保留原失败；`equivalence`生成有协议/范围的等价记录。后续 Managed 执行已接入同一账本，见下文“Managed 评估、并行、调度与强化学习的位置”；旧计划与失败保持原样。

### 从原矩阵接续（Reference）

使用已选择的干净Application源码、正常锁定Python环境和同一`project.json`；原运行state、模型与包不换。
下列`/ABS/ledger`只放评估计划/账本/attempt原件，不是新Application状态；原矩阵、CSV、report、失败原件均只读。
正常解释器执行 `python -m spireagent.workbench evaluation`，操作如下：

```sh
python -m spireagent.workbench evaluation plan --config /ABS/project.json \
  --matrix /ABS/FROZEN-MATRIX.json --episodes /ABS/EPISODES.csv --ledger /ABS/ledger \
  --output /ABS/import-report.json
python -m spireagent.workbench project native-episode --config /ABS/project.json --action unknown-status
```

操作者核对preview的当前App/原App、资源、generation、run及原字节摘要，将`expected`对象保存为普通JSON，再显式调用：

```sh
python -m spireagent.workbench evaluation seal-unknown --config /ABS/project.json \
  --ledger /ABS/ledger --expected /ABS/expected.json --output /ABS/unknown-release.json
python -m spireagent.workbench evaluation run --config /ABS/project.json --ledger /ABS/ledger
python -m spireagent.workbench evaluation status --ledger /ABS/ledger
python -m spireagent.workbench evaluation report --ledger /ABS/ledger --output /ABS/scoreboard.json
```

封存不发送Stop、Host close或证据重发布。原失败快照和当前App确认分别追加；写入/读回/fsync失败不激活放行。
若同App还留有GLOBAL `publication_unknown`，preview一并给出原发布operation/run/字节摘要；同次显式封存保留其完整原字节，再确认放行。原发布仍unknown，同一run禁止重发，后续新run才可接原发布入口。
App重启后，若当前资源仍是该unknown，即使旧确认文件存在也须显式再确认并重查无占用；`run`先停在该检查，不预留新槽。
不同App不能借旧PID/端口无响应推导无占用；现场检查Actor/worker、Reference driver、准确pin的Host游戏进程枚举、状态/模型句柄和本机endpoint明确拒绝连接，任一不可证就拒绝。
ENOSPC可能使关闭收据只留在旧App内存；重启后原closing记录和缺失的关闭事实保持unknown，封存只凭现场无占用证明放行，不补造finish_complete或关闭收据。
新App对旧App资源的finish（含关停）只读返回，不重试关闭；没有自有Runtime的模型服务关停也不覆盖旧会话收据。
`run`成功局沿正常evidence-publish完成R2读回，再经空目录恢复和清单复核冷却非活动副本；冷却失败保留证据并停，不自动重试。
有新unknown就停等显式处理；崩溃后只读原attempt补账，不重发原准备/动作/finish/publish。证据阶段故障可封存账本unknown，原环境closed事实不改。
空间不足在投递前返回`workspace_capacity_insufficient`，余槽未开。创建`ledger/STOP`显式停止；已有有副作用投递按原owner完成收束和原分母记账。
`status/report`可在串行writer运行时读取已原子追加的前缀，不获取长任务写锁，不触发游戏或恢复。
只做了已结束子集时不称完整评估；报告保留每槽原report和R2收据引用，CURRENT分数板从报告派生。回退App后旧封存/账本原件保留，旧版不理解的新放行默认拒绝。

The existing `LocalModelExport.start` owner accepts the explicit closed
`compatibility_profile="native-evidence-rc27-to-rc29-v1"` for a supported original
sampled native Model. Ordinary calls keep strict original export behavior. The
same export operation records original and derived IDs, and journals the derived
identity before immutable publication. It then uses the ordinary native export
and verification path, preserving any previous completed original receipt.
The existing clean-source identity owner captures the committed source/lock
producer and revalidates it immediately before derived publication intent;
dirty source or a changed producer fails before any derived publication.
Workspace inspection and download caches remain read-only. Explicit compatible
publication opens a writable store through the existing Dataset workspace owner,
requiring the same marker-bound root and original Model bytes as the reader.
Missing ownership, mismatched roots or read-only publication access rejects
before derived intent; method names alone never establish write authority.

An operator can call `reconcile_native_compatibility(operation_id,
original_model_id=..., derived_model_id=...)` against the existing export lock.
It preserves the active interrupted journal bytes and writes one immutable
receipt under `model-export-reconciliations`. That receipt binds the exact old
journal bytes/hash, IDs, profile, store root, old producer, current reconciliation
producer and observation time. If already present, the same receipt and original
observation are revalidated and reused. A present derived Manifest is verified
and exported through the ordinary native path, with no republishing; an
incompatible current loader leaves the interruption fenced.

An absent Manifest is recorded only as `manifest_absent_at_reconciliation`.
It does not prove that no publication or orphan payload ever existed, and no
payload is removed. The old outcome remains interrupted. A separately explicit
`start(original_id, compatibility_profile=..., prior_native_reconciliation=
receipt_sha256)` may start a new migration only when the original current journal,
receipt, workspace and IDs still match and the new clean producer differs from
the old one. Its new operation and derived IDs must differ, and the new journal
retains the prior receipt link. A Manifest appearing after the observation blocks
this path until its own bytes are reconciled. No other pending operation or model
family inherits this exception; startup and status never retry a migration.
An interruption after publication intent remains pending/unknown. A caller may
explicitly reconcile the recorded derived ID if its CAS Manifest exists; it
cannot automatically repeat the original migration or infer publication from
current files. This owner-call path adds no service, registry, trainer or UI
operation. Candidate publication is distinct from the original/target numerical
parity and native qualification gates in the native Model guide.

## Prepare and load

### Native episode preparation and explicit finish

For the in-game display on an already running ordinary game, `models.takeover`
uses the same Application preparation and Agent-request owners to attach a fresh
`cold_current_segment`. It does not seed/reset the game or change speed. After
explicit Stop, the original typed clean seal is checked before detaching the old
attached segment and preparing the current game again; changing Model or opening
a new game never rewrites the old request/evidence. Running, tainted or unknown
Actors require their original recovery and cannot be silently replaced. CLI/API
users can use the existing `native-episode attach`, `agent-request` and `run-agent`
steps over the same owners. A normal Game Mod launch with the selected workbench
is the current-game entry; a seeded `prepare-owned` Reference is a different,
explicit mode whose original seed/profile identity checks remain unchanged.

Native Start admits the same declared `ACK_committed_session_rng` used by the
random/frequency/linear baselines. A fresh attached segment creates a fresh Agent
session and seeded RNG; Pause/Continue retains the same Actor and committed state.
The GUI adds no RNG owner. A rejected `current_start_unsupported` is an admission
failure, not an unknown delivery. When a Model POST response is lost, use Stop on
the original task and wait for its actual stopped/sealed result. Only the Model
owner's exact request/binding/run terminal card clears that presentation fence;
ACK, a renewed connection or another task's completion does not. If no admitted
Actor or clean original seal can be proven, keep the uncertainty and use the
existing diagnostics/recovery path instead of repeatedly clicking Start.

Checkpoint publication is CPU/Store work, independent of a running game. Use
`python -m spireagent.workbench project model --config <project.json>
--action export-checkpoint --artifact <checkpoint-id> --checkpoint-selection best|current`; inspect that
same Model operation's completed `last_checkpoint_export`, then use the normal
Model download and registration commands. The recipe adapter owns publication;
GUI/CLI/API only map to it. Download does not establish training ancestry or
qualify gameplay. Registrations label available authoritative selected steps and
distinguish final training results from best/current checkpoint previews.

The existing Application exposes prepare/status/finish in its local-environment
namespace and continues `/api/local-models/run-agent` for execution. Bodies
select only owned or attached preparation, an intent ID, explicit canonical seed
for the fixed Defect A0 owned start, and the existing closed presentation/speed
choices. They cannot select a URL, executable, module, env, PID, token or path.
An operator-owned `native-host-profile-v1.json` under the existing state root
pins the installed public Host package, game installation/template/endpoint,
exact loaded game/Mod tuple and finite preparation/cleanup limits. Owned
`speed: default` resolves to the user-selected Instant project default; explicit
Normal/Fast/Instant share the same native Host and public contracts. The binding
records `{mode: instant, basis: project_default}` alongside the actual typed native
readback. A default choice is not mode/presentation/package/tuple qualification;
final-candidate live mode verification remains pending until separately recorded.
Owned preparation derives the external Host state directory as
`<ProjectConfig.state_dir>/native-host`; the request body and trusted profile do
not add another caller-supplied path. The operator stages the existing template
at `native-host/profile-templates/defect-a0-s0/{template.json,user-data/}` through
the existing Host template owner. The directory must be canonical, ordinary and
disjoint from the immutable installed package. The reference CLI receives
`--local-root` and its actual ready readback must match both this local root and
its `evidence` child before reset. An older CLI without the capability, or a
wrong/unsafe root, rejects before offering native preparation. Package hashing
still covers every installed file; mutable state is never excluded to make a
modified installation pass. Template absence/drift remains an owning template
failure, not permission to create replacement game state.
The profile's closed `candidate_admission` selects `sealed` or `exact_canary`
separately for game and Connector; only this trusted configuration can forward
the existing process opt-ins. Its `installed_tuple` is checked by the Host before
native spawn and against loaded capabilities before bootstrap. The profile pins
game version/commit/assembly SHA, Host source/artifact SHA/MVID and Modset
fingerprint. The profile's retained `default_speed` field does not supply
qualification authority or override the code-owned project default. Attached
preparation never writes speed or creates a Host, profile or seed; personal game
preferences remain untouched.

`POST /api/local-environment/native/prepare`, `GET /api/local-environment/native`
and `POST /api/local-environment/native/finish` expose that one lifecycle.
The ordinary signed local control client and browser origin/CSRF rules apply.
`POST /api/local-models/request` resolves a closed selection/operation/run profile
and optional opaque `native_episode_ref` through the trusted owner; its effective
v1 request is then submitted to the existing `/api/local-models/run-agent` route.

One native preparation intent binds its canonical body before spawn; same
intent/body returns its original resource operation, changed body conflicts.
The same Environment owner retains immutable per-operation request/body digests
and original terminal results through its existing LocalBlobStore primitive,
with atomic recorded status and one live pointer. Completing A, then B, does not
permit replaying A to reset another world. Finishing historical A returns its
original recorded cleanup and cannot Stop or close active B. Original records
survive service restart; active/partial records have no adopted process handle
and remain recovery-required. There is no automatic history expiry or deletion.
Missing or damaged retained identities fail closed instead of becoming fresh
preparation authority. If an operator erases the whole project state, original
results cannot be reconstructed; that is not an automatic retry/recovery path.
Immutable request and recorded status publication both precede worker/process
startup. Storage failure during finish does not skip exact owned cleanup and is
reported separately from original Actor, native-child and driver exit facts.
The public Host client is registered before readiness/reset awaits so finish
can cancel it without queueing behind preparation. Ready is published only
after exact bootstrap release and unheld-runtime proof. Unknown remains unknown
and blocks Agent admission; no management retry/query framework is provided.
Resource phase and original Actor/run results are distinct. Mode Human or panel
closure does not auto-close the Host. Explicit finish and App shutdown use the
same Actor Stop/seal then owned-close sequence. The existing Managed lifecycle,
records and APIs retain their semantics.

### Continuous display and changing Model in the same owned world

The same Application `agent-request` / `run-agent` flow accepts
`run_profile: "continuous"` only with the explicitly supported Runtime rc40
installation pin. CLI selects `--run-profile continuous`; direct Runtime CLI uses
`--autonomy-mode continuous` and rejects finite cap flags alongside it. Omitted
Application profiles remain the original finite `short`, and `extended`, Source3
collection and frozen evaluation suites retain their finite limits.

Runtime rc40 emits startup/status/event-2 for both modes. Continuous config is
only `{mode: "continuous"}`. Status caps and remaining are null, while actual
submission/call counts and elapsed time remain visible. Human pauses the current
segment and releases control; explicit Auto continues the same run with a new
segment under its retained configuration. Stop ends that Runtime run and seals
its original bytes. Local call/recovery/no-progress limits, unknown delivery and
release uncertainty retain their existing fail-closed behavior; no automatic
rearm, resubmit or new game is added.

The native play page selects a real registered catalog entry. Its ready Reference
is passed separately from the signed local pair to the same Application request
owner, which creates the fresh Reference selection binding. A new explicit Model
start may retire the existing Actor only after the original typed verifier
rechecks its exact stopped run/request, released controller, no taint and no
pending request. The original Host `episode_identity` must also confirm the same
runtime, profile generation/template and requested/actual seed before retirement,
and again before creating the replacement Actor. A changed or unavailable readback
rejects reuse and retains the original Actor/report. The Environment's original
private operation store immutably retains the resource, request and verified
report before the live Actor pointer becomes ready. Persistence failure leaves
creation fenced. No new seed, reset or game closure is part of changing Model.
A same-seed manual restart is not independently proven to be the same native run
by this provenance check; original native generation/epoch gates still apply.

Closing/reopening a panel or renewing its pair keeps the same live App/run.
Restarting the App process permits only exact original Runtime Human/Stop/seal
recovery. Lost Host stdio ownership remains recovery-required; it does not enable
Continue/new Actor or adopt a process by PID. The game may remain manually usable;
that does not confirm its original world or cleanup lifecycle. Explicit native
finish remains separate from Model Stop.

For a source candidate already selected through the original installer, owned
native preparation verifies the returned Host profile path and generation, then
copies the same private launcher/open/native-access bytes to that isolated game
HOME. It creates no credential and never edits the daily selected slot. The
fixed native bridge is paired only when its runtime matches the selected local
Connector endpoint, including a custom loopback Reference port. A different
running game or conflicting existing bootstrap is rejected. Ordinary Host start
and old-save adoption do not gain this owned-preparation route implicitly.

An operator can explicitly change one private text Runtime profile while the
Workbench is closed and port 15527 is free. `developer model --action
initialize-runtime-generation` is the first-install path when the selected
profile's active private file is absent. It requires `--runtime-profile`, an
operator-selected strict `--new-runtime-profile` file, its exact raw SHA256 in
`--expected-new-profile-sha256`, and the matching `--runtime-archive`. It has
no `--expected-active-sha256` or rollback-to-absent operation. The owner checks
the stopped Workbench, lock, recovery state and Runtime port, verifies the
archive, bundled SDK and complete installed generation, then creates the active
name exclusively. A racing active file is never replaced. An unactivated
generation may be explicitly retried only if its installed contents still pass
full verification; unknown or damaged content is not repaired or deleted.
If the selected slot contains a legacy install without an active profile,
initialization rejects it as an unknown recovery state; other slots, existing
model registrations and v1 profile bytes remain unchanged. The profile and
archive are operator-selected inputs, not automatic
kit provenance. Filesystem sync uncertainty reports `BLOCKED` with active-file
readback and a nonzero CLI exit; it does not invent rollback or activation.

`developer model --action
upgrade-runtime-generation` requires `--runtime-profile`, the SHA256 of the
current active profile bytes (`--expected-active-sha256`), an approved strict
profile file (`--new-runtime-profile`) and its raw SHA256
(`--expected-new-profile-sha256`), plus its pinned local archive
(`--runtime-archive`). The owner verifies the archive and a separate installed
generation before archiving the current active profile **as original bytes**
and replacing the one active name. The receipt lists selections sharing that
profile; existing model registrations and manifests are unchanged. The
generation name is the SHA256 of canonical profile JSON under the fixed private
slot, never an operator-supplied path.

`developer model --action rollback-runtime-generation` requires the same
`--runtime-profile` and current `--expected-active-sha256`, plus
`--archived-profile-sha256`. It verifies the archived bytes and their installed
target before switching. A damaged current generation can be rolled back from;
neither operation repairs or deletes an existing generation. Legacy active
profile bytes remain valid and unchanged until an explicit switch. Normal
preparation and offline installation only verify/reuse an active generation.
If replacing the active name is followed by a filesystem sync error, the
command reports `BLOCKED`, nonzero exit, and exact active-profile readback
(`true`, `false`, or `unknown` activation). Inspect that receipt before any
further explicit operation; no automatic rollback occurs.

`LocalModelService.prepare_and_load(selection_id)` accepts only a reviewed registry
selection. It runs existing adapter, artifact, backend and package readiness
checks; missing weights, unsupported hardware or adapter mismatch remain visible.
If the only missing infrastructure is the fixed Runtime package, the existing
hash-pinned Runtime installer prepares it. The existing exact startup and
attestation path then loads the policy in **Human mode**. It never starts a game,
paid compute, training, or an unbounded model download. Inference children set
`HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`; model weights must already exist.
The current S1 adapter's CUDA requirement is unchanged.

This method starts an asynchronous local operation. Existing `status()` reports
`operation.status`, `preparation_stage` and `readiness`. After fixing a displayed
prerequisite, failed preparation can be retried. An active or unresolved Runtime
must first be stopped/recovered before preparing a different one.

Public-snapshot D-Simple M0 registration now defaults to the explicit
`ready-standard-summary-v1` task, shown as **至结算页**. Its config-v2 and adapter
1.1.0 pin the exact `stpd/public-m0-task-spec-v1` descriptor in both config and
manifest. This is a new Agent program composition using the same Model artifact,
renderer and unchanged neural scorer; it does not retrain or rewrite model bytes.
The catalog exposes `task_spec` and `agent_version` for that selection.

The task consumes only a complete interactive generic Snapshot with its original
ordered action catalog. After the normal public representation/binding checks,
the exact standard `game_over` ready summary (win or loss, summary stage, return
enabled, advance disabled, main-menu/timeline destination and typed public
score/floor/ascension) produces full-length zero placeholders and no selected
index. These zeros are **program abstention**, not neural scores. Existing Runtime
Auto handling of `policy_abstained` returns to Human and releases control; the
task does not choose a return-to-menu action. Intro/animation continues through
the original scorer, and malformed summary facts fail closed. Nonterminal inputs
keep the original scorer and complete catalog.

Low-level `bind_public_m0_export` still defaults to `task=None`; config-v1 and
adapter 1.0.0 keep their original task-free behavior. Explicit
`LocalModelRegistration.register(model_id, task=None)` registers that baseline,
and `status(model_id, task=None)` queries it. Default registration/status selects
the declared summary composition and never adopts or mutates a legacy binding.
The catalog labels a newly registered task-free selection **无任务基线**. Each
selection retains its own config/manifest and can be loaded through the existing
common owner. A task descriptor or green source test does not prove a native
summary journey, decision quality, whole-game coverage or G2/V1 acceptance.

## Recorder-to-model handoff

Before `shadow`, `one_step` or `auto`, Workbench reads exact Runtime status and
uses the game Mod's fixed loopback task bridge at `127.0.0.1:15528`:

1. Read the exact Runtime status, then GET its `/v2/environment`. Require the
   exact `sts2.policy-runtime/environment-1` response with this immutable `run_id`,
   the Runtime owner's fresh Connector `runtime_instance_id`, and a safe integer
   `recovery_epoch`. Capture this epoch **before** any native preparation. A 404
   reports `runtime_upgrade_required_for_model_control`; there is no legacy
   Auto/Shadow/One-Step fallback. Human/Stop remain available for exact old sessions.
2. Read capabilities from the Connector endpoint persisted when this Runtime was
   launched. Its instance must match the Runtime environment observation. A
   non-null cached Runtime environment must also agree. Existing `NativeTasks`
   compares that endpoint with the game Mod's task bridge before closing anything.
3. If recording is not ready for a model, POST `/v1/tasks/prepare-model` once,
   carrying the observed `runtime_instance_id`, `recording_session_id` and a new
   UUID `command_id`. The native Recorder owner closes the actual recording.
4. Continue only with `ready_for_model=true` and consistent `ready`/`closed`
   lifecycle. Recheck the game and Runtime identity, then send the existing exact
   Runtime run ID plus `X-STS2-Game-Instance-ID` and `X-STS2-Recovery-Epoch` on
   every model mode and tick mutation. One-Step mode and its later tick use the
   **same original epoch**; preparation or a late response never refreshes it.

Bridge schema is `sts2.platform/task-status-1`. Pending/failed Close does not
acquire control. A lost POST response is unknown and is never automatically
retried. Human/Stop recovery does not depend on this bridge. The client bypasses
ambient HTTP proxies and refuses redirects; native server policy owns bridge
request validation.

The Connector endpoint is persisted in Workbench's session alongside the exact
Runtime startup identity; it is not guessed from the current project config after
recovery. Older sessions without this field can still be returned to Human or
stopped. They report `runtime_connector_binding_required` and must be stopped and
loaded afresh before model control is available. A missing/malformed endpoint,
unsupported identity contract, mismatched game instance or unavailable Connector
blocks model control without retrying Close or changing mode.

Human/Stop immediately invalidate earlier local control intents. Model mutations
are serialized in Workbench; recovery bypasses that queue so it reaches Runtime
without waiting for an earlier model HTTP response. Shutdown uses the same
recovery path. A late success or unknown response cannot replace a newer recovery
result, and a superseded One-Step never sends its later tick. The Runtime owner additionally
advances a shared recovery epoch when either Workbench or the in-game UI requests
Human/Stop. A different caller's recovery therefore rejects delayed model entry
or a follow-up tick even though Workbench's own generation did not change.
Stop during local preparation cancels a late load.

`RuntimeControlBinding` accepts only the typed game identity and safe epoch; it
cannot override the separate immutable Runtime run header. The Runtime validates
these facts against its own Connector and recovery state before effects. Human
and Stop require neither game nor epoch headers, so missing native preparation
cannot prevent recovery. Known structured owner precondition rejections are
reported as failed non-dispatch; malformed responses and lost POST responses
remain unknown. Neither is automatically retried. After an explicit new start
request, a new environment observation may be obtained. Closing the page or a
background status GET does not create another model intent.

## Finalization without a page request

An independent observer starts after attested Runtime load or explicit recovery.
It observes the exact Runtime's stopped lifecycle, or exit of its locally owned
child process, and runs the existing public `verify_agent_run_evidence` handoff.
It does not send gameplay commands or retry unknown actions. A lost connection
to an unowned/recovered process does not prove termination. Missing, unfinalized
or invalid evidence remains failed. GET status/page reads do not generate reports.

Reports cover bounded Runtime operations. Native victory, complete-run coverage
and autonomous-control coverage remain separate facts. `game_outcome=not_measured`
is not a win, a loss, or an incomplete-game verdict.

## Explicit project sharing

A separate **Share this game-test record** button explains that this finalized
Agent run (model/Runtime identity and recorded observations/actions) will be
shared with approved project members. It calls
`EvaluationSharing.share(evaluation_id, authorized=True)`; Human collection
consent is not reused. This module enables neither automatic sharing nor a
persistent sharing preference. A valid personal member session and owned active
device are required. Account changes before submission cancel it. Logging out
while an already submitted request completes cannot retract submitted bytes;
the exact receipt is retained but hidden from a different session's operation view.

`EvaluationSharing.status()` reports `preparing`, `uploading`, `shared`, `failed`
or `unknown`. Call `close()` on application shutdown. A transport timeout never
causes automatic retry. Explicit retries republish the same immutable content;
Hub deduplicates its manifest and audit event.

The authenticated Hub member write route is
`POST /v1/identity/member/live-evaluations`:

```text
{
  schema: "stpd/agent-evaluation-share-v1",
  device_id: owned active device ID,
  share_authorized: true,
  expected: {
    run_id, manifest_id, policy_manifest_sha256, policy_artifact_sha256,
    runtime_version, runtime_code_sha256
  },
  files: { fixed filename: base64 bytes, ... }
}
```

Exactly six files are accepted: `adapter-attestation.json`, `checksums.sha256`,
`events.jsonl`, `evidence-manifest.json`, `manifest.json`, `policy-manifest.json`.
Decoded evidence is limited to 16 MiB, within the existing 32 MiB HTTP body cap.
Oversize evidence is rejected with a clear error, never truncated. This is a
bounded first sharing path, not streaming arbitrary archives.

`LiveEvaluations(upload_service, identity.membership).publish(principal, body)`
checks current membership/device ownership, decodes only the fixed names and
reruns the public verifier on Hub. It derives counts and report fields rather
than trusting submitted wins. Current authorization is rechecked immediately
before immutable publication. Existing ArtifactStore stores the six payloads
and report as `live_evaluation`; Operations records one `live_evaluation_shared`
event and ConsoleIndex indexes it. Nothing enters Human upload tables, and no
second upload ledger is created.

Response schema is `stpd/shared-evaluation-receipt-v1`: `artifact_id`,
`evidence_content_id`, `status=verified`, and derived `report`. Verified means
**member-submitted Agent evidence integrity and internal consistency**. It does
not independently prove an untrusted member's native gameplay, autonomous victory,
research qualification or Human origin. `native_outcome_status`,
`native_run_completeness` and `game_outcome` remain `not_measured`; scientific and
training admission remain `not_claimed`. Exact Hub producer contributes to the
immutable artifact identity. Local receipts are retained by evaluation ID and
artifact ID, so a future verifier/producer can derive a new report without
overwriting prior receipts.

## Integration and tests

Workbench routes retain local Origin/CSRF protection, strict body shapes and
personal-session checks. Construct one sharing service from existing models and
member client; wire prepare/load to the new method, GET share status and explicit
POST share. Existing catalog/status/command routes remain usable. Hub's member
router retains authentication and browser Origin/CSRF, then delegates publication.
Never put mutations into GET or page rendering.

Focused regressions cover actual verifier bytes, exact native Close binding,
pending/unknown transitions, two-client recovery during native preparation or after
One-Step mode, strict shared-epoch headers, old-runtime model-entry rejection with
Human/Stop retained, background finalization, missing
model prerequisites, explicit sharing, member/device revocation including during
verification, invalid bytes/paths/limits, account changes, double clicks, lost
responses and idempotent manual recovery. Portable synthetic tests do not replace
native load, real Human Close or genuine supported-model game operation evidence.


## Stage 1a token models (source implementation, native qualification pending)

The shipped S1 catalog and its CUDA checks retain their original meaning. The
additive trusted adapter `token-v1` runs `python -m stpd.policy.token_port` with a
pinned configuration and public Policy Manifest. It supports the existing public
snapshot exports, CPU/MPS according to the saved model configuration, and a local
pinned Qwen snapshot only for PF. Preparation never downloads a model implicitly.

The optional operator-owned `python/.local/token-policies-v1.json` has schema
`stpd/local-token-policies-v1` and a `policies` array. Entries have exactly `id`,
`label`, `adapter` (`token-v1` only), `manifest`, and `config`; the last two are paths
inside the Python root. They merge into the existing catalog, reject duplicate IDs,
and cannot override a command or the pinned Runtime package. Merely adding an entry
does not load it or authorize gameplay. Raw models and machine paths stay private.

`stpd/token-policy-config-v1` pins the absolute `export_path`, `export_manifest_sha256`,
`model_id` and nullable `qwen_snapshot`. Its own file digest/path/schema are pinned by
`adapter_config.stage1a.config`. The public artifact references the export envelope;
that immutable envelope binds model/weights/tokenizer identities, verified at loading.
`representation` must match the saved public serializer. Semantic-execution exports
are rejected. No extra Reads, filtered catalogs or native operands are introduced.

The adapter code scope `python-owner-source-and-lock-v1` conservatively hashes all
Python sources under `stpd/` and `spireagent/`, `uv.lock` and the existing Qwen pin.
This is a broad reproducibility pin, not a minimal import closure or a new component
semantic identity. Code changes require explicit manifest regeneration; documentation,
private exports and registry edits do not. S1's existing code digest remains unchanged.

Before live registration, use the current qualified environment identity and explicitly
review supported interactions/verbs; never copy an old environment pin just to load.
Runtime remains responsible for exact environment compatibility, complete public
schema admission, timeouts, controller acquisition and delivery. The token process
checks action-ID order/count and manifest equality, then returns finite scores and an
index. Unexpected requests fail without a partial decision. Workbench continues to
load in Human mode and use the existing Recorder handoff and Stop lifecycle.

## Unified workbench target and restart repair

The [root UI specification](../../docs/UI_INTERACTION_SPEC.md) owns the accepted
unified local/cloud/in-game workbench target: all functions and login accessible
from the Mod by default, optional external presentations, independent background
services, and one account/task/data system. The current Mod is not yet this full
workbench. Cloud-backed tasks remain in the same user workflow.

Runtime port readiness now distinguishes a live listener from closed-connection
TIME_WAIT using platform-appropriate bind semantics, without REUSEPORT or killing
unrelated processes. Runtime startup/attestation remains authoritative if another
process races the check. Operator-local model catalogs must not leak into test
fixtures; tests read the shipped registry explicitly before adding synthetic entries.

After an application/package upgrade, explicit **结束测试** can retire an older
session using its sealed Agent-run evidence even when its model registration or
Runtime package is no longer current. This requires exact persisted startup
identity, a successful owning Evidence verification, a terminal `stopped` event,
and a free Runtime port. The old session is archived by content hash and its
original taint retained; a new evaluation is separate from prior reports. Missing,
unsealed, mismatched or tampered evidence and an occupied port cannot use this
path. It sends no gameplay request, performs no automatic action retry, and does
not turn port absence into proof of past delivery. Normal live recovery retains
its exact-runtime checks. Successful retirement permits a new explicit load.

## Common recorded Agent task outcome

Local generic and native handoffs add the versioned application DTO
`agent_task_outcome.schema=spireagent/agent-task-outcome-v1`. Their existing
outer schemas, scalar fields, immutable evaluation IDs and evidence scopes stay
compatible. New derived reports have new content-based IDs; old evaluations are
not rewritten. The canonical executable shape is
`spireagent.workbench.agent_task_outcome.AgentTaskOutcome`.

The DTO keeps independent facts:

| Axis | Values and meaning |
| --- | --- |
| `task_completion.status` | `completed`: exact supported original Task/Agent composition, qualified endpoint witness and declared program end join. `unproved`: known Task and bound input, but no qualifying completion witness. `unknown`: Task/composition/input binding unavailable or conflicting. Neither unproved nor unknown asserts that the game never completed. |
| `operation_end.status` | `task_ended`, `censored`, `failed`, or `unknown`, projected from recorded program end, stop/budget, fail-closed and uncertainty facts. A proved summary followed by cleanup failure retains completed Task and failed operation end. |
| `terminal_observation` | Original public win/loss, conflicting observations, absence from available inputs, or unavailable input. Ready summary is a separate predicate. Receipt, HP, a later Current and process exit are not substitutes. |
| `agent_composition` | Original manifest identity/hash, Agent or policy, adapter, runtime-declared Model artifact reference, input/profile and exact Task/program metadata when bound. These refs do not carry private artifact paths or grant use/control. |
| `evidence` | Owning verifier status/content identity and original events file identity. Integrity/internal consistency is not native-world authenticity or scientific qualification. |
| `strategy_quality` | Always `not_assessed`; recorded endpoint/loss and mechanical Task completion do not assess gameplay strategy. |

Public M2 Port4 records full qualified public input and exact decision/control
association. Its supported ready-summary Task is read from the sealed Policy
Manifest and uses the existing domain predicate; matching declared program-zero
abstention proves recorded mechanical completion. Terminal win/loss remains an
observation and does not prove numerical W behavior. Public M0 Port1 has a closed
legacy mechanical Goal mapping for the sealed `stpd-public-m0-decision-adapter`,
`decision-only-ndjson-1` and exact owning `ready-standard-summary-v1` TaskSpec.
After the existing typed Evidence verifier and bounded immutable-file checks,
the application may join a prior sealed Snapshot1 `successor` or
`receipt.successor` to a later original decision. The body must be complete,
interactive and public under the existing input validator, from the same recorded
Runtime/environment and exact Snapshot ID. All prior bodies for that ID must
agree except their top-level observation time; future observations never supply
the join. The independently derived complete ordered candidate digest and count
must match the full zero-placeholder score vector, null selection/resolved action
and abstain disposition. The owning ready-summary predicate must hold.

Mechanical completion additionally requires the exact `policy_abstained` handoff
and its terminal Stop tail. Only controller release may precede that handoff;
after it, controller release and recorded cleanup failure/taint may precede Stop.
Later decisions, modes, submissions or other substantive activity invalidate the
earlier end join. Recorded failure/uncertainty/budget precedence remains separate
in `operation_end`; completed Task followed by failed cleanup stays completed
Task and failed operation. Qualified original terminal win/loss observation is
independent of whether this declared Task/end join succeeds.

The witness basis is `prior_sealed_public_observation`, not consumed input,
Port1 input instrumentation, numerical inference/history or native causal proof.
Missing, future-only, conflicting, foreign, incomplete, unsupported or mismatched
facts remain unknown/unproved. Ordinary M0 abstention/Stop without the original
witness is never promoted to completion. A new application projection receives
its own content-based evaluation ID; prior evaluations and sealed inputs remain
immutable. No live Current, replay, extra witness writer or caller backfill is used.
Existing text terminal observations retain their original meaning and do not
invent a Task descriptor.

Native sampled-current completion requires the original stored observation,
accepted consume/ACK, matching Close watermark and supported original Task.
Terminal win/loss observation uses the acknowledged original body independently
of Close, so Await, ordinary abstention or an operator Stop can retain an observed
terminal result without proving Task completion. Stop before a completed Next
retains the owning verifier's recorded uncertainty in the operation axis.
Native Task metadata is read only when existing registered owner metadata and
package bytes match the sealed Agent artifact and Agent/InputSpec identities;
no current/default Task or version-only lookup is substituted. Legacy native
full-reference runs retain capture references/ACKs but no sealed original body;
completion and terminal facts remain unknown where that body is missing. No new
Runtime witness, backfill, current-game lookup, NN loader or weight read occurs.

Interpreted files are rechecked against the owning verifier's accepted inventory.
Projection currently bounds events to 64 MiB, one native observation to 16 MiB and
package metadata to 1 MiB; exceeding that application projection envelope reports
an explicit unknown reason instead of fabricating or truncating a witness.
Terminal references retain the latest original witness for each result/readiness
combination; repeated screens do not enlarge the DTO or conceal conflicting results.
Actor/Human classification, data/training admission, legality, native delivery,
causal successor and research qualification remain with their existing owners.
An original `fail_closed` or `runtime_tainted` event establishes a recorded
operation failure and retains its reason. This does not settle native delivery,
the successor, or uncertain cleanup. Taint/pending/unknown control without a
corresponding failure event remains unknown. Recorded stopped-budget facts are
retained in the independent operation axis.
Task/package/body projection errors leave recorded operation facts available
when the original events still match the accepted inventory. Changed events
cannot support any axis. The generic handoff freezes and hash-checks one events
byte sequence for both the common DTO and existing scalar counters/observations,
so reopening a changed file cannot mix foreign facts with the old evidence ID.
A reasonless final Stop retains the exact immediately preceding operative
`handoff_to_human` reason and event sequence; only controller-release cleanup may
intervene. Controller and budget facts still come from the final Stop.
A later mode, decision, submission, consumption, directive or unknown
substantive event blocks reuse of an older handoff. Explicit Stop reasons and
existing failure, uncertainty and budget precedence remain unchanged; the reason
does not prove Task completion or a terminal game result.

## Paired checkpoint Reference evaluation

The existing owned native-episode preparation also accepts `character` and
`ascension` together. Omission retains the original Defect A0 contract. Explicit
configuration supports Defect, Ironclad, Silent, Regent and Necrobinder at A0–A10;
Host selects through its native bootstrap and verifies the actual initial public
character/ascension. The original template is an isolated starting profile, not a
claim that every selected run is Defect A0. General scenarios carry a bound
`fresh_seeded_reference_scenario` initialization and exact `scenario`; old bindings
remain readable. CLI uses the locked Python environment:

```sh
python -m spireagent.workbench project native-episode --config /ABS/project.json \
  --action prepare-owned --intent-id NEW_32_HEX --native-seed CANONICAL_SEED \
  --display-mode headless --native-speed instant --character Silent --ascension 0
```

Use the same `agent-request`, `run-agent`, status and `native-finish` chain; changing
character never chooses a separate Runtime or bypasses Model negotiation. Unknown
original preparation/delivery is not retried. Finish and confirm the exact prior
Actor/Host before the next intent.

`stpd.evaluation.suites.checkpoint_reference_manifest` produces the fixed fresh
checkpoint comparison manifests: ten Defect A0 seeds, two Defect A10 seeds, and
two A0 seeds per other character. They are reserved from collection/training.
`tools/evaluate_reference_suite.py --manifest ... --manifest-sha256 ... --selections
/ABS/checkpoints.json` accepts checkpoint labels mapped to distinct normally
registered selections. Every checkpoint uses the same seeds and configuration;
exact Model/package/current-vs-best/step identities belong in the frozen input
index. Use `--run-profile continuous` with finite per-episode, suite and cleanup
seconds for full games; short/extended profiles retain their existing limits.
Headless/native-ui and speed remain Reference configurations.

Reports retain all planned, failed, censored, unknown and unstarted episodes.
Sealed public-source metrics bind original source hashes and runtime to floor,
HP/character/ascension and terminal `game_over` context.result; no HP or process
exit invents a win/loss. Metrics and typed Task completion are separate. Model
quality is a paired research comparison, not Human or research admission. Store/R2
preservation follows the existing evidence-publish owner, never a second registry.


For serial follow-up attempts, `--completed-report /ABS/report.json` freezes and
references an already finished original with the same manifest, selection,
Reference configuration and peer. Its exact closed finish and publication
receipt must still match their hashes. It is retained as `reused` with its original
run/identity, never relabeled as a newly executed episode. Unknown or unfinished
world offers reject before any new preparation; reconcile through the original
Environment first. The new run directory stays distinct. `--max-episodes` limits
new offers only. Keep original report directories available for their referenced
files; Store/R2 remains the durable recording owner.

Repeated GLOBAL publication reuses exact immutable parent manifests already in
the peer, so each new run does not re-stream the training checkpoint ancestry.
New original bytes still receive full peer readback and typed verification.
Ordinary `copy_artifact` and empty-directory recovery keep full lineage byte
verification; publication reuse is not a claim that such a recovery was performed.


### Checkpoint Reference execution speed

`checkpoint_reference` uses the same owned Reference suite and may explicitly select
normal, fast or instant native speed. The report freezes that requested speed and
every preparation verifies the typed effective readback; completed report reuse
requires identical speed and presentation. The fixed ref-20/four-agent benchmark
continues to require instant. For STS2 v0.111.0, vanilla PunchOff's persistent
animation loop awaits Cmd.Wait, which does not yield in native Instant mode; both
headless and native-ui Instant can starve or crash the process. Use an explicit
Fast cohort for this checkpoint evaluation. Preserve Instant failures separately,
never retry their unknown deliveries or silently mix conditions. No timing hook,
Native rule patch, partial Read acceptance or automatic speed fallback is added.

Suite reports have a separate bounded 64MiB reader for completed-report reuse; manifests and individual request/evaluation originals retain the 1MiB limit. Per-observation metric details remain in the immutable game-metrics.json artifact; the aggregate row stores only the summary and its exact artifact reference. This keeps long Fast cohorts recoverable without copying every observation into every aggregate report.

### Managed 评估、并行、调度与强化学习的位置

链路位置：①世界/Host 的实现替换；②Connector 提供公开环境、③P1 提供观察与完整动作目录、④Agent 做决定。Managed 和 Reference 使用同一 P1 Runtime 与模型包。注册按 Host 分开，是环境要求、运行身份与清理责任不同；不是再造模型、策略或另一套 P1。

应用层的评估计划和槽位账本负责调度。单个协调者先持久化槽位预留，再分配给独占 Host、Runtime、注册目录和证据目录的执行者；结果仍回原有报告与 ArtifactStore。unknown 只记在自己的槽位，不重投；确认清理后，其他独立槽位继续。未确认进程清理时停止新派发，已运行邻局正常收尾，避免把仍占用资源的进程算成空闲容量。Host Runtime 继续只管生命周期、隔离与恢复；Policy Runtime 继续只管受控执行、预算与 Stop。

现有 `EvaluationLedger` 的同一 `create/run/status/report` 命令以 `--host managed` 选择 Managed 执行者。每局仍通过原 `run_suite`、正常 `export.register`、P1 Runtime CLI、`stpd.p1.agent`、typed Evidence 与 Store/R2；新接口不执行另一套动作循环。Reference 的原 `plan/run --config` 行为保持。旧 `stpd.evaluation.managed_suite` 是直接选择完整 BoundAction ID 的 catalog-only 工程可靠性资产，没有完整 P1 模型输入、Model 任务绑定和 Runtime 证据链；其循环不能并作 P1 模型评估。原资产与用途保留。

冻结执行 JSON（`spireagent/managed-evaluation-execution-v1`）需要正常打包的 Host/Runtime 目录和完整 package pin、准确候选目录、已核对的 `initial_progress`、Python 路径、`export.adapter_identity()`、原 Model artifact ID 与包目录、实际源码 `Producer`、Store/peer 与空间下限。`producer` 是预期版本，执行前必须与干净源码和原 uv.lock 相符；不能用旧配置给新代码冒标来源。`store="s3"` 使用冻结的 peer 位置，环境只提供凭据；本地 Store 则指定自己的绝对目录。配置和附件保持私有，不复制凭据到报告。复制既有规范 manifest 后只改 Host，保留规范种子，不编辑旧计划。

```sh
python -m spireagent.workbench.evaluation_ledger create --host managed \
  --ledger /ABS/new-ledger --manifest /ABS/managed-ref-20.json \
  --manifest-sha256 SHA --selections /ABS/selections.json --execution /ABS/execution.json
python -m spireagent.workbench.evaluation_ledger run --host managed \
  --ledger /ABS/new-ledger --workers 1 --max-episodes 1 \
  --cloud-environment /ABS/private-cloud.env
python -m spireagent.workbench.evaluation_ledger report \
  --ledger /ABS/new-ledger --output /ABS/report.json
```

先确认这一槽自然终局、完整 typed 读回、controller release 和 Host/service 正常关闭，才继续其余槽位。调度命令结束不等于每槽通过；报告保留全部 unknown/censored/unstarted。增加 `--workers N` 只改变同一账本的并发容量，协调者持有 OS lock，worker 不写槽位事件。恢复只派发 `not_started`，崩溃后的 reservation 先读原报告，未知状态不会回队列。STOP 文件、总时限、空间下限或未确认清理均停止新派发。每个并行比较使用预先冻结的独立试验账本；原失败和 unknown 分母不被另一次试验覆盖。

本机库和命令是先做的部署方式；以后按需云CPU执行者、持久调度进程复用同一计划/槽位/结果接口。现有 Hub 仅有 feature/training 作业且每DB一个活动任务，不能直接声称已支持评估池；将来只复用其队列、租约和预算思想，不另引一套业务记录。云部署和常驻服务器未实现。

冻结执行文件可选 `inference={"mode":"per_actor"}`（默认）或 `{"mode":"central_batch","max_batch":4,"window_ms":2}`；这是同一 Agent 的评分实现，不能按名字推断速度。集中方式由当前评估命令持有一次性本机队列，复用研究模型原有 `MStd.forward_batch`，只接收完整公开 P1 观察；各 Actor 仍独立运行原有 consume/ACK/next、选择、Runtime、Host 和证据链。原包、InputSpec、projection 和 adapter 源码摘要逐项绑定，不改变观察排序、不省略候选、不共享 Actor 状态。一次评分失败不重试、不静默回退；坏输入只回到对应 Actor。连接能力仅在子进程环境中，私有附件只有非秘密的队列位置和身份。命令等待所有 Actor 收尾后关闭自己的队列，留下 `inference-pool-summary.json`；它没有独立服务入口、注册表或常驻进程。每局报告记录方式与附件摘要，比较试验先冻结同一模型/输入和独立账本，保留全部失败分母。实际带推理吞吐与内存上限由准确版本报告确定，Host-only 测速不能替代。

08-21的6–8个Managed实例约1373–1687决策/秒属于旧准确版本的Host-only容量；真游戏1/2/4实例约0.482/0.976/1.868决策/秒也是历史条件。两者都不是当前 P1 模型吞吐或本机新上限。真游戏多开需按当前用户授权登记游戏占用，核独立 profile、端口、原生运行身份、存档、Steam 与正常关闭。用户要录制时立即让出；工程产物根和 Annotator 配置必须隔离于 Human 录制与自动维护扫描根。具体当前测量留在准确版本的实验报告，不从历史数字继承资格。

强化学习在研究侧：在线P1对局及原记录链产出→Store→明确用途/划分→派生→训练→新检查点→同一评估链。奖励、学习算法、采样/更新比例、Human与自我对局的权重及验收由研究方案定义，不能由Host或调度自行决定。吞吐预算应从目标对局/决策量、当前带推理成本、记录/存储和学习器耗时算起。已有“先Human示范、后审议强化学习”的方向，未批准本轮RL配置；本轮不训练、不实现RL、不启动云机器。

不同Host的结果保留各自世界条件。当前尚无可合并统计的等价资格；单局绝大多数公开观测匹配或旧测速不授权合并胜率、unknown分母或性能曲线。

### Original account progress at an owned native start

Environment copies the actual isolated `saves/progress.save` bytes immediately
after reset/seed verification and before bootstrap handoff. It retains a private
resource-generation descriptor even if the file is unknown; it never falls back
to normalized baseline JSON or a personal profile. The GLOBAL rc49 Runtime
registration accepts the descriptor plus its frozen SHA, binds it to its current
Reference runtime, and seals the raw artifact with its original. The normal Store
and R2 archive/readback then carry it without a second storage path. Older pinned
Runtime registrations keep their version and require ordinary re-registration
against rc49 for this private archive addition. No old original is backfilled.
The new Human P1 native recorder also copies raw progress at its observed native
start; resumes remain unknown. Neither path exposes progress in P1, changes
recording control, or qualifies cross-Host pooling.
