# Platform Live UI

## 日常 P1 录制：从游戏内进入

人的默认入口是游戏内 **Platform → 顶栏“录制” → 项目内“录制”页**。
**资料管理**单独浏览和导入资料；技术详情、**兼容录制窗口**及运行恢复在
**诊断**页。顶栏**项目**回同一工作台，**兼容浏览器**是外部备选入口；
CLI/API服务Agent和自动化。这些入口共享RecorderRuntime，不另建状态机。

显式打开顶栏**录制**或**项目**会通过已经安装的固定启动入口确认后台工作台，
需要时启动同一个独立Application，然后继续游戏内页面；不会因此打开外部浏览器。
刷新状态不会反复启动服务；**兼容浏览器**仍明确打开外部页面。
同一已验证配对的后台刷新保留最后已确认的连接文案；首次读取或配对变化才显示
正在读取。读取失败、未连接和过期仍显示实际原因。

适用条件：已按[固定开发工具安装指南](../../python/docs/DEVELOPER_KIT_INSTALL.md)
选择准确的Mod/Application组合并核实安装、装载及本机配对；页面须实际连接该
游戏实例。以下标签对应当前UI源码；源码中的默认按钮或可用能力声明不能证明
旧安装已升级，准确构建、装载与视觉仍须分开核验。配对或服务
不可用时，按页面理由回原安装/`native-access`流程处理，不绕过配对、不停止他人
服务。本段描述源码支持的操作，准确候选的build/install/load、实际点击与Human
短录须分别验收；当前结果从[CURRENT](../../docs/memory/CURRENT.md)进入。

1. 在**谁在操作？**明确选择实际操作者，例如**我本人操作（本人声明）**、
   **AI 操作游戏界面**或**不确定操作来源**；不会默选Human。录制内容默认
   **日常录制（P1，默认）**，不要求旧兼容格式的操作者代号。点**开始录制**，
   核页面变为**正在录制**。Human录制仍由用户亲手玩游戏；高级来源/兼容模式
   只按服务实际提供的选项选择，不因改了显示名改变原来源语义。
2. 需要暂停采集时用**暂停**，核状态为**已暂停**；游戏本身继续。用**继续录制**
   恢复同一会话。刷新、关闭或重开面板只改变显示，不停止后台录制，也不重发命令。
3. 结束时点**结束并保存**（原Close），等待**保存中，请稍等**变为**已保存**，
   再明确选择来源开始下一次。诊断页或原API可核准确session、profile和保存健康；
   “已保存”仍只在lifecycle和closeout均为`closed`时出现。请求返回或零错误不能
   代替完成；下一次须是新的session，不能重复开始旧会话。
4. 主控件下方显示同一后台的**自动整理**、**远端备份**、**本地空间周转**和
   **录制磁盘剩余**。开启过的一次性维护配置由后台继续使用，不要求每局再点上传。
   远端完整核验份数只统计当前保存目标的实际完整读回；它不是上传请求数。
   容量是后台最近一次检查的录制磁盘余量，诊断页可看检查时间，缺失时显示暂不可用。
   自动整理未启用或报错时，不能把本机“已保存”当成远端备份完成；保留原件并按
   [原Application资料流程](../../python/docs/PROJECT_CONSOLE.md#本机资料)处理。
5. 原件保存在本机既有录制root，准确路径、CLI与恢复方法由
   [采集指南](../../docs/ANNOTATOR_COLLECTION.md#current-p1-daily-collection)及
   [Annotator P1操作](../../components/annotator/README.md#p1-native-semantic-recording)
   拥有。未启用自动整理时，保留**资料管理**中的手动恢复入口：用**刷新已结束的录制**
   选原候选编号，再用**保存已结束的录制**进入原Store导入。核原operation结果和
   录制artifact读回，不能只据目录出现或日常页“已保存”判定入库成功。派生、上传与用途资格沿
   [原Application资料流程](../../python/docs/PROJECT_CONSOLE.md#本机资料)，不由GUI授予。

失败时保留当前session、原command/operation、保存错误和原件。按钮禁用先看其
owner理由；保存health报错或记账不完整不能当成功继续批量采集。未知提交只查询
原状态并用原恢复/Close路径，不再次点击以重投；重开面板或续配对不能清除unknown。
卡在封存或进程中断时，按
[原Tool恢复](../../components/annotator/README.md#recover-an-interrupted-p1-recording)
保留可验证前缀及真实失败。确认原会话封存后才明确新建下一会话；仍有不确定性
就保留记录，不能据新session倒推原请求成功。

## 兼容录制诊断与Agent Run

There is no `K` shortcut. Close returns to the small launcher; `Esc` closes the
normal workspace. Drag the header to move the workspace and use its corner to
resize the normal view. Closing UI does not stop background tasks.

The compatibility Recorder and Agent Run have a **Minimize** control and a separate, genuinely smaller
440 × 174 view. The arrow restores the normal workspace and its previous size.
The compact Recorder shows only **已录入 / 真实失败 / 最新 3 条**. Its totals come
from Annotator's session counters, not the retained UI list:

- 已录入 is the durable canonical decision count.
- 真实失败 is Annotator's versioned in-scope failure disposition total. Unknown
  older counters display `—`; normal cancellation, abort, diagnostic
  invalidation and unsupported/non-decision dispositions do not inflate it.
- The latest three rows omit native diagnostics and unsupported/non-decisions.
  They retain the owning decision status; pending and cancelled are distinct.

The normal Recorder contains lifecycle controls, session totals, a paginated
retained decision list and details revealed by selecting a row. Exact parent
and causal-root lineage, selector metadata, action and state identities remain
available in those details. Missing metadata stays unavailable. Canonical
recording does not itself establish Full-Run qualification or research admission.

The Recorder retains **兼容语义录制** as its original native Human capability.
**P1 录制（工作台）** controls navigate explicitly to the paired Workbench Data page;
active P1/Source3/Source4 lifecycle always uses that owner, regardless of profile dropdown.
The Workbench defaults to P1; the explicitly selected compatibility formats
`native-logical-source-v3` and `native-logical-source-v4` retain their own semantics
(Source4 retains the public basis for offline P1 conversion). For these Source
formats select a source declaration
(本人操作 / AI 界面操作 / Agent 协议 / 未知来源) and enter an operator ID before
Start; no Human source is selected automatically. IDs use 1–128 ASCII letters,
digits, `_`, `-` or `.`, excluding `.` and `..` alone. Pause before changing the
source, then Resume. ChangeSource carries the observed exact session and source
segment to the Recorder owner. Close remains Closing until the owner finishes
the original accounting and durable drain; a fresh session can then start.

Source views show public observations, inputs, pending inputs, gaps and accounting
health. These are separate from canonical Human decision counters. Source kind
is the operator's declaration with `machine_verifiable=false`; it does not prove
Human origin, physical coverage, Commit, causal successor or research admission.
Legacy controls retain their existing capture format. Source3 entry is a source
candidate until its exact package/native runtime canaries pass.

Workbench Source3 controls and the local TaskBridge share typed command composition;
the diagnostic Recorder does not create a second Source3 mutation path.
The bridge and async model preparation use the existing bounded native queue;
queued cancellation can prove non-dispatch, while uncertain started work is never
automatically retried. The browser and paired native Workbench Data page use the same application methods
and fixed lifecycle API. Source3/Source4 Start/Change require an explicit source
and actor; P1 Start requires the explicit source without a legacy actor field.
Neither form defaults to Human. Refresh never retries an unknown write. Explicit Close and
then a deliberate fresh session after durable Closed retain the earlier uncertainty
notice. A fresh runtime/session isolates the presentation fence; it does not prove
the prior request. Starting or changing away from Agent Protocol while a known
Workbench model is active requires explicit Human/Stop recovery first.

Model preparation uses `/v2/tasks/status` (`task-status-2`) and
`/v2/tasks/prepare-model` (`task-model-request-2` / `task-model-result-2`). Only a
healthy Recording Source3 declared `agent_protocol` may remain Recording with a
fresh compatible `agent-session-status-1` / `native-logical-v1` context. Paused
Agent Protocol remains paused and blocks preparation until explicit Resume/Close;
accounting errors block readiness. Other supported profiles/kinds or known legacy
model input use owner Close and wait for durable completion. The original v1
Ready/Closed readiness invariant is unchanged. Context is caller-provided
compatibility metadata, never authentication, model Consume or Human proof. Runtime
Mode/Tick still carry the original run/game/recovery epoch; preparation cannot grant
a newer epoch to a stale intent.

Shared browser and paired native Workbench model admission uses the existing
LocalModelService intent owner. Every Source lifecycle intent reserves that
application boundary through its final outcome while new non-recovery model
intents reject before admission; Human/Stop/reconcile remain independent. Only
Start/Change away from Agent Protocol requires Human/model-recovery eligibility.
Already active Auto may still Pause/Resume/Close its recording, and an already
admitted model intent is not cancelled by a later Agent Source control. An
unknown Source result keeps the existing notice and model admission fence after
the request returns. Same-runtime refresh cannot release it; exact accepted
durable Close or a separately verified new runtime may release the applicable
scope while retaining the original unknown. External Runtime/SDK callers remain outside this Workbench owner; legacy Human
recording keeps its original native boundary. This does not claim universal
cross-process or every legacy recording-format exclusion.

The **运行状态与恢复** tab shows typed Runtime status and retains independent direct
Human/Stop recovery against its remembered exact run, even with unavailable pairing
or a failed status refresh. Auto/Shadow/OneStep/Tick entries are clearly labelled
navigation to native Workbench play. That page's fixed actions call the existing
LocalModelService with an original run/game/recovery context; stale context rejects
before admission. Genuine Tick advances the current mode once without changing mode,
and OneStep remains Mode plus at most one follow-up Tick. Pair failure never restores
a direct non-recovery HTTP path or adopts an externally started Runtime.

Agent Run diagnostics use only typed Policy Runtime status and recovery controls. Its compact view keeps mode,
controller, policy, last selected action, Receipt and **Return to Human** visible.
It never resolves or submits a gameplay action directly. The Runtime loopback
defaults to `http://127.0.0.1:15527`; modes and model status require a compatible
Policy Runtime with an exact Policy Manifest and artifact.

## Unconfirmed model commands

Model mode and tick POSTs are sent once. A timeout, lost response, malformed
success, or unrecognized error leaves that Runtime run in an explicit
unconfirmed state. Status polling can describe what is currently visible but
cannot erase the uncertainty or re-enable model actions. **暂停并接管** and
**结束测试** remain available with the original exact Runtime run ID, even during
an unavailable status poll. Status reads have a 900 ms timeout; commands have a
45 s budget matching the Workbench and the Runtime's bounded model wait. Human
and Stop are submitted promptly without waiting for an earlier model HTTP
response; Runtime's owner controls effect ordering and recovery fencing. A late
old response cannot send a follow-up tick, overwrite the newer UI intent, or
re-lock a run after confirmed recovery. While the owner is still completing an
in-flight action, the UI honestly remains in recovering state. A confirmed
Human/Stop response clears that UI fence;
a failed recovery does not. An independently loaded Runtime has a different run
identity and does not inherit another run's UI uncertainty. Native unknown
delivery remains governed by Runtime's own taint and is never made retryable by
this presentation state.

Only strict, recognized owner precondition errors establish non-dispatch. The
client does not follow redirects or use ambient proxies for local commands.
Read-only preparation failure does not masquerade as a submitted operation, and
a cancelled local preparation cannot subsequently Close a new Human recording.

## Retention and display cost

The closed workspace performs no status polling. An open Recorder queries only
the typed Annotator status/event service once per second; it does not request a
Connector Snapshot or action catalog. Agent Run requests its typed live status
only while visible. Loaded assembly identity is immutable and cached once.
The UI retains at most 512 action rows; ordered event sequence is the replay
cursor. A reconnect gap refreshes the available batch and labels partial
history. Authoritative session totals are independent of this retention.
Minimized mode does not rebuild the hidden normal decision widgets.

Layout version 5 stores position, normal size, compact mode and selected surface
in local application data. This presentation-only state is fail-soft; it
contains no Human evidence, native operands, model weights or secrets. Old UI
layout files are not migrated because no evidence or consumer contract depends
on their geometry. See the canonical
[UI and interaction specification](../../docs/UI_INTERACTION_SPEC.md).

## Initial scene attachment

Initialization schedules one main-thread deferred mount, then verifies that the
layer and panel entered the captured SceneTree before preparing the panel. The
ready log is published only after preparation succeeds. Root exit cancels an
already queued callback; duplicate initialization/callbacks cannot mount twice.
Attachment or preparation failure disposes the panel and layer once, reports the
failure, and does not retry automatically. Portable lifecycle fixtures cover this
ordering; the exact candidate still needs a cold-start, busy-tree, shutdown and
open-panel runtime canary before the mount is qualified.

## Ownership and validation

This component consumes typed Connector, Policy Runtime and Annotator services.
It owns neither gameplay/recording authority nor packaging, deployment, loaded
identity or rollback. `apps/game-mod` compiles it into the single production
`STS2_PLATFORM` Mod. Portable boundary and event-projection tests run with:

```bash
npm run live-ui:check
```

Use [`apps/game-mod/README.md`](../game-mod/README.md) for the supported exact
build/install/cold-load/rollback path. Source tests and compilation do not prove
rendering, Human origin, policy operation or Full-Run qualification. Final UI
canary checks launcher, both compact views, drag/restore and Recorder counters
against the exact candidate's session audit.
