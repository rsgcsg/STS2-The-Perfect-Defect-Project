# Platform UI and interaction specification

Presentation consumes typed Recording, Policy and Connector services. It never
owns native legality, input delivery, causal proof or research admission.

## Human operation contract

面向人的功能默认提供简单、稳定的游戏内操作入口。复用已有小窗、主要控件和后台服务，
日常只展示完成当前任务需要的操作、状态和下一步；技术身份、任务参数和故障原件放在
按需展开的诊断中。复杂的配置、校验、排队、打包、上传和空间管理由原业务owner在后台
完成，不要求玩家逐个选择内部API或按工程执行顺序点击。GUI仍只是同一用例的映射，
不能为简化显示而隐藏失败、猜测成功、默选Human来源或重投unknown。

交付日常能力必须验证正常安装上的完整用户流程，而不止隔离候选或源码按钮。日常录制
应能从游戏内开始、暂停/继续、结束并保存、查看必要状态和开始下一会话；正常后台服务
不依赖Codex、执行Agent、开发终端或临时工作树存活。关闭面板不停止业务，游戏退出后
已封存材料的后台保存和上传仍按原生命周期完成；再次进入通过既有可信启动/发现机制
接回同一服务。上传完成、本地已保存和可释放副本是不同状态。自动上传、恢复和本地
工作集周转必须在该日常部署上实际验证；本地释放仍受原存储owner、可恢复性及用户
授权约束，不能把需要原件的活跃录制或消费一并清掉。

## Current pre-training scope

2026-11-01 / CARD-1A：日常入口是游戏内 **对局台**。一个主窗口管理状态与打开/收回，
三个小窗口分别负责 **录制与标记、模型运行、动作列表**；按需打开诊断。
所有窗口可拖动、最小化、调尺寸及用鼠标 ± 缩放。关闭/收回/最小化只是表示变化，
不发录制或模型 Stop。主窗口展开操作区映射同一录制、模型、标记、排序用例。
界面无日常键盘快捷键、按场景自动停、模型建议显示/隐藏开关。

- **开始本人录制**是明确的本人来源声明，保持既有跨局待命；不从普通页面打开、
  登录、已连接、兼容格式或 AI 测试推断 Human。AI 工程检查仍用原 API 的
  `agent_native_ui`，不能代点本人录制按钮冒作 Human。
- 录制状态来自 Recorder 的生命周期、真实计数与写入健康；最近条目引用实际原件行。
  明确区分本机已写入、片段封存、录后核验/入库和远端备份。后台不可读或写入不健康
  就显示“没在确认保存/没在保存”，不保留正常红点。关闭所有业务窗后 launcher
  仍显示录制反馈。最近列表有界，计数不以列表行数冒充录制器计数。
  重启后原录制器已待命但尚无会话核来源时，显示“原录制器待命、来源尚未核实”；
  不显示未录制，也不据此默认 Human 或启动另一个本人片段。
- **连续 / 一步 / 停，我来**走同一 LocalModelService。连续使用无自主预算上限的
  原 continuous 配置；不按 Boss、精英、奖励、商店或分数自动停。终局、真实失败、
  unknown 的原 owner 生命周期及安全拒绝仍照实保留。每个模型步始终写 Runtime 原件，
  不依赖本人录制。换模型仅在停着时完成，注册/加载和准确来源绑定由后台完成；
  不修改旧模型、注册或正在用的组合。
- 人可以直接在游戏里用鼠标，也可以在完整 P1 动作列表按先后点几个、执行第一个。
  排序与人/模型切换是不同用例；不用模式开关隔开鼠标。动作目录仅在可见展开的
  动作列表读取；候选不截断、不另做合法性判断。牌名、目标、费用和手牌顺序只从
  已给出的公开内容映射。模型在走时列表只读，并标原模型选择。
- 无模型时，打开动作列表通过显式 `choices_open` 准备既有无模型 GLOBAL 程序的
  Runtime，保持 Human，不调用学习模型或自动出招。GET 目录只读；执行仍是同一
  Runtime 的一次有界人工选择，复用原 consume/ACK、lowering、Connector 与回执。
  人工协议选择记录为 `human_directive`，不能伪装成模型输出或机器验证的 Human。
- 稳定公开局面或来源绑定变化即清排序；动画未稳定不能执行。排序键绑定完整公开
  观察摘要（排除瞬时采集编号），执行前 Runtime 再取真实局面，即使候选不变、仅
  HP/能量等事实变化也拒绝并要求重排；发出后仍由 Connector 再验。unknown 保留原请求，
  不重发。“停，我来”先处理原 owner；tainted 原 Runtime 只能封存，不能靠换模式
  使旧执行可重试。普通 Stop 后可发新意图，旧未知不会抹掉。
- 开着本人录制切给模型时，后台先封存当前 Human 原件，再交控制；保留录制意图。
  模型停回 Human 后自动开始下一个本人片段。**停录制并保存**只停录制，模型继续；
  **停，我来**只停模型，录制意图保留。切换等当前已发送步骤的原回执，不推断撤销。
  每个原件仍由自己的 Recorder/Runtime owner 负责，不造第二条执行或录制链。
  退出后原目录 owner 确认游戏已停止，便封存本对局台的模型原件和本机索引；这条
  生命周期不依赖录制入库维护开关，关着本人录制看模型也适用。未确认的 Stop 不由观察循环重发。
- **标记问题**保存说明；**我点错了**由后台刷新后定位上一个已记录的人类动作，
  不是 UI 记住的旧行，也不是最新模型步。标记不撤销游戏动作、不自动当负例。
  同一 actor/source/input_method/action/receipt/标记索引引用原件，排序标签另存
  `spireagent/human-ranking-labels-1`，未排到是未评价。

GameDeskApplication 拥有这组用例的编排与标签/原件引用索引；Recorder、LocalModelService、
Runtime、Connector、Store 各自仍拥有原事实。Native GUI 和受认证的 `/api/game-desk`
及 `python -m spireagent.workbench game-desk --help` 调用同一用例。索引的本机 SQLite
事务只保证标签/引用持久化，不代替原件审计、物理 Human 证明或研究准入。
可核实的 native Human 动作沿普通示范流程；模型段只是模型轨迹；混合或展示整局
不算模型成绩。源码、便携检查、构建、实际安装加载、工程操作和真实用户打局分别验。

旧 Agent Run、Source3/4 控件、只评分、浏览器跳转、训练/资料/团队/账号页及恢复按钮排
已退出 Mod 日常 GUI；CLI/API、服务、数据、历史格式和原证据不删。逐入口去向见
[游戏内指南](../apps/ingame-ui/README.md)。正常恢复只显示一个对应按钮，技术详情进诊断。

## Historical direction: complete in-game workbench (2026-09-18)

The following target is retained as history and future design context. It is not
an instruction to implement every cloud/training/team page before this freeze;
future expansion needs a real use case and its owning decision.

The owner requires one unified local/cloud/in-game workbench, with the Mod as its
default entry and an optional external window. All local and cloud-backed functions,
including account login, device binding, recording, datasets, training, models,
evaluation and jobs, must be accessible from the in-game workspace. Cloud is not a
separate user product requiring a disconnected workflow. Local/remote execution
and authority remain explicit, but navigation, task identity and progress are shared.

UI hosting and background services need not run inside the game process. Independent
training/upload tasks survive game exit; game-bound inference stops when its exact
game disappears. The in-game host may use separately hosted pages or native views;
no particular embedded browser implementation is selected by this requirement.
Keep the entry simple, with contextual primary controls and progressive navigation.

Persist login securely on trusted devices with normal refresh/expiry/revocation;
reuse existing enrollment rather than asking for repeated binding. Keep logout,
account switching and device revocation available. Shared identity does not mean
copying a browser cookie or device credential to every surface; each authenticated
entry uses the existing account/session authority and explicit access checks.
This is the delivery target, not a claim about the currently installed UI. An
external-browser shortcut alone does not satisfy it. Today the Mod only presents
Recorder and Runtime controls and tells the user to prepare models elsewhere.

Use one application backend and task/state model, exposed through two presentations:
the in-game workspace and the existing external Workbench. Do not create separate
model registries, dataset stores, training jobs, account state or control owners.
The native UI consumes model-neutral typed application APIs; Python owns loading,
research and long-running tasks. Keep that work off the game's main thread and do
not import STPD/Qwen into the Mod. Exact loopback service discovery and bounded
commands need a reviewed contract; never scan arbitrary ports or accept commands
or URLs from untrusted manifests. Backend lifecycle/startup and unavailable-service
recovery must be part of the user flow, not a terminal command left to the player.

Primary game page: selected model, readiness, one **Load and take over** action,
**Pause and take over manually**, **End session**, and compact current activity.
The main action prepares/loads the chosen model as needed, checks current game
binding, completes the existing Recorder handoff, and starts Runtime Auto. It is
idempotent at the application-intent level; repeated clicks cannot create duplicate
loads or replay uncertain native submissions. Pause must remain available during
loading and inference. Both presentations show the same state and cancellation.
One-step and score-only remain secondary diagnostics, not required normal steps.

The workspace also needs navigable recording/history, data/datasets, training/jobs,
models/evaluation and settings pages, progressively disclosed rather than packed
onto the game-control page. Data-intensive operations remain backend jobs; tables
are paginated and incremental status updates preserve selection, scroll and focus.
An **Open external workbench** shortcut is useful during migration and optional
after the in-game workflow is complete. Closing the panel does not stop a task.

Auto is continuous operation across supported decision surfaces, not merely one
play. Advertise **Full-game takeover** only after map, rewards, shop, events,
selectors, combat and terminal handling have a supported model/input/execution
path and bounded integration evidence. Stage 1a registrations currently admit only
whole combat-turn decisions. Until expanded, label their control **Combat takeover**
and explain a handoff on unsupported surfaces. Never silently filter actions,
invent a fallback strategy, retry unknown delivery or describe AI actions as Human
training data. A weak policy can still run continuously; strategy quality and
interface coverage are separate acceptance results.

Delivery order: repair the observed native-run blockers and report verification;
connect in-game discovery/model selection/load/control and external shortcut;
extend supported surfaces and verify continuous takeover; then bring the remaining
Workbench pages into the same in-game navigation. Every increment retains working
manual recovery. Installed UI and native canary evidence must be refreshed for Mod
changes; existing load evidence does not transfer to a rebuilt UI.

## Historical workspace

A small visible Platform launcher opens Human Recorder or Agent Run. There is
no K shortcut. Escape/Close hide the workspace; controls, drag and resize handles
capture pointer input only within their bounds. Position, size, selected tab and
compact preference are local presentation state, versioned and fail-soft.

Normal Recorder shows lifecycle, primary counts, recording controls and decisions.
Detailed accounting is optional. Selecting a row opens a bounded evidence
inspector: decision/root/parent/native owner, action, state/catalog/Read facts.
Unavailable facts remain unavailable. Child success never proves parent success.

## Historical compact modes

Recorder compact genuinely reduces panel bounds. Its content is only recorded
canonical actions, real recording failures and latest three decision entries.
Restore/drag remain presentation affordances. Counters come from the owner, never
from the bounded feed or compatible legacy record count. Missing or incomplete
accounting shows `—`; it cannot become zero.

Real failures are in-scope accepted Human decisions whose recording failed
closed, lacks a proved successor, or lost canonical persistence. Native cancel,
pre-Commit abort, presentation-only cancel, unsupported actions and internal
native diagnostic invalidations are separate non-success dispositions. Status4
and event batch2 carry that distinction; UI does not classify reason strings.

Agent compact shows observed Policy mode/controller and latest action/Receipt,
with Return to Human through the existing typed Policy service. It introduces
no gameplay button, legality reconstruction or automatic retry. Missing Policy
service remains unavailable; Human is the safe initial mode.

## Historical performance and evidence

Closed workspace performs no UI polling. Recorder reads Recording status/events
without fetching Connector Snapshot/HTTP. Agent polls only while visible. Loaded
assembly identity is cached because it is immutable for that process. The feed
is bounded; a reconnect gap rereads retained events and declares lost history.
Totals remain owner-provided even when older rows are evicted.

Loaded UI, launcher toggle and compact bounds are separate verification facts.
None qualifies Human action capture. Current schema counts, exact candidate
identity, native run boundaries and immutable offline audit own the final gate.
