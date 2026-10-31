# 游戏内对局台

对局台是同一组 Application 用例的游戏内映射。界面不拥有合法性、游戏执行、录制原件或训练准入；动作只交给 Policy Runtime，经 Connector 执行并返回原回执。

## 日常操作

1. 进游戏后右上角是主窗口，左上角的“对局台”按钮一直保留录制摘要。主窗口点“打开录制与标记”“打开模型运行”“打开动作列表”打开小窗口；点“收回…”或窗口的 × 收回。
2. 自己打并录制：点“开始本人录制”，明确声明由本人操作。看到“● 录制中”、已记步数和最近保存时间后，可以直接在游戏内点，也可以在动作列表按先后点几个，再点“执行第 1 个”。再点已选动作取消，或点“清空”。
3. 看模型：选模型后点“连续”，每点“一步”只走一步；要接手点“停，我来”。自己走完再点“连续”交回。模型步无论有没有开录制都会记录。
4. 有问题时写一句说明并点“标记问题”。“我点错了”标记上一个已记录的人类动作，不会撤销游戏动作。停采集要点“停录制并保存”，封存完成才算保存完。

主窗口默认只展示状态和窗口管理；展开“在主窗口操作”可使用相同的录制、模型和标记按钮。没有额外模式开关，也没有日常快捷键或模型选择显示开关。

## 窗口与状态

- 主窗口、录制与标记、模型运行、动作列表都能拖动标题、按 — 最小化、按 ↗ 还原、按 −/＋缩放窗口和文字，也能拖动右下角调整尺寸。
- 关闭、最小化、拖动和缩放只是界面操作，**不停止录制或模型**；停止必须点名称里有“停”的业务按钮。关闭主窗口后仍可用左上角“对局台”按钮找回；已经打开的小窗口不受影响。
- 录制窗口和最小化标题显示录制状态与计数。最近记录来自后台确认的持久记录；计数不从当前界面行数推算。后台失联明确写“没在确认保存”，不能继续显示已确认的“录制中”。
- 第一次打开动作列表会先向后台明确提交“准备动作列表”，由原用例准备执行载体；成功接收后，只有窗口打开且展开时才读取全部候选。准备失败不自动重发，可明确点“准备动作列表”发起新请求。模型在走时列表变灰；需要先点“停，我来”。稳定且可执行时才可排序提交，局面键或候选集合改变会清空未执行排序并提示重排。未选的动作不表示差。
- 命令只提交一次。unknown 会保留明确提示并阻止重复决策；读取状态和重新连接不证明原写操作成功。只有原 owner 明确确认恢复，才放开对应的新操作。
- 连接只使用原安装的可信固定启动器、精确工作台配对与本机身份；不会扫描端口或打开兼容浏览器。诊断保留只读状态和原运行 ID 的直接 Human/Stop 恢复。

本轮先不持久化窗口位置、尺寸和开合状态，不提供双击执行或并回主窗口；重开游戏恢复固定布局。主窗口内的操作区可展开使用。

## 旧入口去向清单

| 原入口、按钮、页面 | 对应用例 | 本轮去向 |
|---|---|---|
| 顶栏 Platform | 打开工作区 | 主窗口与常驻对局台摘要 |
| 顶栏“开始本人录制”、录制状态 | 同一 P1 采集用例 | 录制与标记；主窗口展开区同样可用 |
| 顶栏 F8 接管、F9 模型及键盘快捷键 | 人/模型交接 | 删除快捷键和旧字样；模型窗口“连续 / 一步 / 停，我来” |
| 顶栏“录制” | 查看采集 | 主窗口“打开录制与标记” |
| 顶栏“项目”、兼容浏览器 | 原全功能工作台导航 | 删除游戏内 GUI；外部工作台、CLI/API 保留 |
| 顶栏缩小、重置、收起和 Esc | 窗口呈现 | 每窗鼠标最小化、拖动、缩放、×；删除重置和快捷键 |
| 工作台录制页的开始、暂停、继续、Close | 录制生命周期 | 日常保留开始与停录制并保存；高级暂停/继续仍在原 API/CLI |
| Source3/4、捕获格式、来源下拉、操作者代号、ChangeSource | 兼容采集 | 删除游戏内 GUI；原来源/格式能力保留 |
| 兼容录制的计数、最新三条和分页详情 | 查看原件 | 日常窗口显示已确认计数、最近保存；原详细服务与原件不删 |
| 工作台资料页、数据集预览/发布、导入/刷新 | 整理与读数据 | 删除游戏内 GUI；原工作台、CLI/API 保留 |
| 工作台训练页的启动、暂停、取消、恢复 | 训练应用 | 删除游戏内 GUI；原工作台、CLI/API 保留 |
| 工作台模型页的导出、注册、下载、评估 | 模型管理与评估 | 删除游戏内 GUI；原工作台、CLI/API 保留 |
| 工作台实战/对局页选模型、加载并连续、暂停接管、结束 | 同一模型运行应用 | 模型运行窗口；主窗口展开区同样可用，内部准备交后台 |
| H1 混合局开始、结束、设置、交回模型 | 交替记录与控制 | 录制、连续、停的同一后台用例；删除独立模式与自动暂停 GUI |
| Boss/商店/奖励/低把握自动暂停、只评分、Tick | 高级运行能力 | 删除游戏内 GUI；原 API/CLI 保留 |
| Agent Run 旧正常/紧凑面板 | 运行状态与恢复 | 删除旧重复面板；必要状态进模型窗，原 typed 直接恢复进诊断 |
| 一排暂停加载、结束模型、直接 Human、直接 Stop、刷新 | 故障恢复 | 日常“停，我来”；直接恢复和只读刷新收进诊断 |
| 团队、账号、授权、设置、collection/download 表单 | 账号和后台管理 | 删除游戏内 GUI；原工作台、CLI/API 与服务保留 |
| 标记问题、我点错了 | 具名记录上的问题标记 | 录制与标记；主窗口展开区同样可用 |
| 全部合法动作排序 | 人工 P1 选择 | 动作列表；执行仍走原 Runtime → Connector |

删掉的是 GUI 入口和原面板的呈现代码。固定客户端、应用、CLI/API、服务、记录格式和历史数据继续保留；原界面可从 Git 历史恢复，不把旧用户数据改标为新用途。

## 验证与边界

`npm run live-ui:check` 运行边界检查与原 transport/recording/mount 夹具，并验证排序取消、过期目录清空、unknown不重发和独立Stop恢复。原来锁死旧按钮、页签和 F8/F9 的源码匹配测试随已删除 GUI 退役；原服务及证据夹具保留。

Mod 编译、安装/加载、窗口实测、Human 原件和数据用途分别验收。安装和回滚仍走 [Game Mod 生命周期](../game-mod/README.md#lifecycle)，不会由关闭窗口触发。完整交付和截图由本卡报告给出，未实测部分不能以源码或编译替代。

<details>
<summary>历史界面：CARD-1A 之前的操作说明（已被上文取代，不作为当前入口）</summary>

# Platform Live UI

## 日常 P1 录制：打开游戏即可用

沿用户平时的 mac 启动台或 Steam 游戏库打开游戏，点屏幕上的
**开始本人录制**。这个按钮同时明确声明由本人操作并开始 P1 录制；
它直接映射 `RecordingApplicationService`，与 `npm run recording:p1`
使用同一命令和准确的 runtime/session 前置条件，不依赖 Workbench 的配对。
这是一项来源声明，不是机器验证的真人证明。AI 界面操作测试使用原 API 的
`agent_native_ui`，不能点“开始本人录制”冒作 Human。

录制期间小按钮显示**正在录制**和已接受操作数；点它查看详情。顶栏
**录制**打开同一个录制服务的控制，**项目**仍打开配对的工作台。
录制窗口可以显式选 AI 界面/未知来源；P1 不需要兼容格式的操作者代号。
**暂停**仅停采集，游戏继续；**继续**恢复当前会话；**结束并封存**停止
待命并封存，只有后台 lifecycle/closeout 均为 closed 才算完成。
收起面板不停止采集，也不重发命令。

每局终局由原生边界自动封存；下一局自动使用同一 P1 格式和原声明来源。
成功开始/继续会把待命偏好写在录制根的 `recording-standby.json`，只保存
启用与来源，不保存旧 session、动作、运行时或游戏存档。正常保存退出会
封存当前片段；重启后按原生 Continue 或新开局时开启新的录制片段。
退出、续玩、unknown 仍按真实边界保留，不拼造一场完整自然局。
**暂停**或**结束并封存**会取消跨重启待命；在主菜单待命时也可以取消。
偏好损坏会显示待命异常而不自动猜来源；明确开始可重设偏好。
偏好保存失败时，操作结果明确说明当前录制命令已应用、重启设置未保存，
不要重发刚才的命令。

录制的原件和 Close 继续归 Annotator；封存之后由既有应用维护链处理。
**Source3/Source4**控制和资料管理/训练/模型操作仍经配对工作台。
配对未连接时按原安装指南核对选择的工作台、Mod 和 native-access 身份，
不放宽哈希校验、不从多个工作台中随意选一个。新 Mod、Tool、工作台和
本机访问绑定应作为准确组合安装，不能只更新 DLL 后保留旧绑定。

源码、构建、安装/加载、实际点选、Human 整局和训练用途分别验收。
本轮准确对象与剩余验收见[录制稳定任务](../../docs/plans/p1-prefreeze-2026-10-18/HUMAN_RECORDING_STABILITY_20261025.zh-CN.md)。

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
**日常 P1 录制** maps directly to the same RecordingApplicationService used by the supported CLI.
Active Source3/Source4 lifecycle retains its paired Workbench owner regardless of the dropdown.
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

</details>
