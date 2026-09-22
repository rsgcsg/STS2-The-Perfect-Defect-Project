# Stage 1a 窄任务与交付顺序

日期：2026-09-19。状态：架构/产品任务分解；下表不表示已交给执行器或正在运行。
产品验收以 [1a 交付定义](../STAGE1A_PRODUCT_DELIVERY.zh-CN.md)为准，
执行遵守 [AI 协作与五分钟交接](../AI_COLLABORATION.md)。
Luna 是用户指定的实现者，不预设其 GitHub 用户名或云/本机执行能力。

## 调度和基线

本次核读 develop 为 `d5785d215087719189a3a6bada9addb34b7d95c5`，
未合并 1a 源码为 `3e804e5e67b02b2b93b5d81c1033c2dc567f62b1`。
下发前重新核对 refs、已有 PR、实际 writer、脏工作区和部署身份。
先完成 1a-00；不要让另一个 agent 直接写 `feat/stage1a-local-models`。
依赖未合并代码的任务使用经 owner 同意的短期叠加分支，或先审查已有工作再普通合入 develop。
每项完成后只推进下一项已授权任务，不自动启动训练、部署、游戏或下一批模型。
Workshop 的分支、PR 和发布审批原样保留，不能为本清单强行合并或重置。

状态字段分别保存 planning/ready/blocked、dispatch prepared/submitted/acknowledged、
execution not_started/running/terminal、evidence 的实际层级。一个 Issue 不是运行回执。
完成一个修复不自动拥有之前另一个 SHA 的完整检查。具体耗时未知时保守交接；
不为测试清单编造运行号、日志路径或 PASS。

## 任务总览

| ID | 一项可观察交付 | 主要 owner | 前置 |
|---|---|---|---|
| 1a-00 | 本机/远端/Codex 历史与现存运行对账；精确剩余缺口 | Luna 只读＋真人本机访问 | 无 |
| 1a-01 | Runtime 修复包、工作台消费 pin 与真实 Human-only 加载一致 | Policy Runtime 包＋本机安装 | 00 |
| 1a-02 | 慢推理中可安全手动接管，旧意图不能复活 | Policy Runtime 控制生命周期 | 00；先复现 |
| 1a-03 | 固定游戏全流程 surface×模型覆盖矩阵 | Connector/STPD 只读审计 | 00 |
| 1a-04 | 一套有界本机 facade、发现和启动/重连合同 | SpireAgent 应用 | 00 |
| 1a-05 | 游戏内宿主可行性和焦点/认证/非阻塞验证 | In-game UI＋应用 seam | 04 的最小合同 |
| 1a-06 | 游戏内选模型→加载接管→暂停/结束的纵向切片 | UI＋既有 LocalModelService | 01、02、04、05 |
| 1a-07 | 逐场景补齐全流程模型输入与真实执行 | 首个缺事实的 owner | 03；逐个子任务 |
| 1a-08 | 游戏内采集、原始记录、质量与队列闭环 | Annotator 投影＋采集应用 | 04、05 |
| 1a-09 | 游戏内数据集预览、固定分配与保护说明 | Dataset/Hub owner | 08；已有数据可先用 |
| 1a-10 | 游戏内提交/监测/恢复有界本机训练 | 既有 Worker＋应用任务入口 | 09 |
| 1a-11 | 分析→模型产物→本机准备与实战报告导航 | Reporter/模型应用 | 10、06 |
| 1a-12 | 本地/云身份与同对象导航、权限/离线对齐 | Hub 身份＋本机 BFF | 04；按各页面渐进接入 |
| 1a-13 | 黄金旅程、恢复旅程、完整覆盖签收与发布 | Luna＋真人＋架构审查 | 以上 |
| 1b-00 | 合格库存与有界成本 profile 方案 | STPD 数据/训练＋云 owner | 1a 签收后执行；可先做无消费设计 |

这是依赖顺序，不是一次性派出十四个并行 worker。04/05 可与 01/02 的无重叠只读工作
有限并行；UI、生命周期、BOM/依赖 pins 的写入必须各有一个明确 owner。
07 是按实际缺口展开的任务组，不能让一个 PR 实现所有 native 场景。

## 1a-00：首先只读对账，不重新启动长任务

**交给本机 Luna 的第一张任务卡：**

```text
目标：核对 Stage 1a 真实最新状态，给出下一张窄实施任务；本卡不改生产代码或运行状态。
仓库：rsgcsg/STS2-The-Perfect-Defect-Project。
参考 ref：develop@d5785d2；feat/stage1a-local-models@3e804e5。
先刷新远端，再只读检查相关本机 worktrees 的 branch/HEAD/status；不得 reset/stash/clean。

只读范围：
1. 找到该项目相关的最新本地 Codex 线程，核对 B/C 修正、四格小样、真实 Auto 失败、
   Runtime rc5/rc6 的后续；不能只重述用户粘贴的早期日志。
2. 列出现有训练/测试/profile/CI 任务、实际 task/run ID、可用监测页/日志、终态或未知。
   不启动新的训练，不因找不到日志就重跑。
3. 对照 source、package manifest、导出 runtime version、BOM、local-policies 消费 pin、
   现有已安装包和当前 Workbench/Mod/游戏身份，区分 source/built/installed/loaded。
4. 核对四个导出模型、注册表、支持面、旧 taint/Stop/evidence 及录制/outbox 的保留情况。
5. 检查 Runtime Human/Stop 在慢推理队列中的实际代码路径；本卡只报告待复现风险。

回交：一个脱敏基线表、未推送改动与 writer、真实运行任务清单、证据/未知项、
下一卡建议（只选一个 owning 问题）、需要真人的最少操作。
私有日志/模型/凭据保留本机；只分享相关摘要与必要 digest。
停止条件：访问缺失、已有 writer 冲突或需要超过五分钟等待时交接，给出真实监测入口。
禁止：重新训练、自动接管游戏、关闭用户游戏、安装/重启/部署、删除旧包或改写失败证据。
```

云端 architect 没有直接连接该 Mac/Codex 会话时，只能把这张卡交给真人转交 Luna；
不能声称已检索本机历史、已派发或后台持续监测。只读输出不作为新原生资格。

## 1a-01：修复包与消费者的闭环

Owner：`components/policy-runtime` 包身份及 `python/configs/developer/local-policies-v1.json`
消费组合。先以 00 的实际状态决定 rc.6 是否已有新进展，不能覆盖既有发布资产。
最小代码范围：若仍缺，更新正确构建的候选、校验 runtime/package.json/Connector product
identity 一致、下载字节核对、消费者 pin 和安装就绪；保留 rc.5 失败资产。

完成证据：安装包断言＋公共消费 smoke；当前工作台通过精确模型/适配器 attestation 在
Human 模式加载、停止、重载；不启动 Auto，不重训，不重建未变 Mod。
如涉及实际安装/重启，先提交 source/package gate，再交真人批准相应本机步骤。
分类按实际变化取 G2/G4，不把版本声明当安装或游戏通过。

## 1a-02：安全恢复不能排队等长推理

Owner：`components/policy-runtime/src/runtime.ts` 和现有 server/port 生命周期；
Workbench 只使用同一控制意图/epoch，不建另一控制器。
先加忠实测试：第二次 Auto tick 已持有控制、适配器迟迟不返回时，另一客户端 Human/Stop
能否在声明时间内收到真实释放确认。分别测试尚未提交、在途提交、已收到 Receipt。

允许改动：首个控制/取消 owner 的窄修复及相应合同/测试。UI 只显示请求中与已确认的区别。
不要求任意中断 STS2 的原生 Commit；在途结果未知必须保留，不自动重试或清 taint。
回归包含 late result、epoch 变化、worker timeout/exit、重复 Stop、已释放与释放失败。
输出可复现计时、目标上限和限制；所需原生验收另给最短真人卡。
不要把该任务扩成一般训练取消框架。G2，运行生命周期交付按 G4。

## 1a-03/07：全场景审计与逐格完成

03 只读核对 `components/connector` 当前公开合同、Native Foundation seam、
`python/stpd/fullrun/public_inputs.py`、`public_bc.py`、`policy/token_port.py` 和注册 support。
按真实枚举列出完整观察/候选、输入、适配、执行、终局/嵌套边界、数据覆盖与各模型证据。
产品场景至少涵盖地图、战斗/药水、奖励、事件、商店、营火、宝箱及各类选择/终局。
主菜单/进程管理保留 Host/application ownership，不变成模型随意命令。

07 依据矩阵按一个 owning 缺口分卡，例如地图/奖励连续转换、事件嵌套选择、商店移除选择、
休息/宝箱、终局与退出。每卡先明确实际路径与非目标，不默认上述分组能由同一个 owner 修复。
增加 manifest 字符串不是完成输入/执行支持。不能过滤候选、静默截断或内置规则替慢模型玩。
需要新输入/训练时先固定版本、复用获准数据，另开有界训练卡；不自动重跑四格。

每格要求合同负例、候选重排/绑定、允许 Reads、超限处理和实际 native gate；四配置分别
记录评分/执行/延迟资格。分场景 canary 与跨场景旅程都要有；人工 setup 不算自主行动。
未观察场景保留缺口，不削减目标来签收。分类从 G2 到实际受影响的 G3/G4/G5。

## 1a-04/05/06：先把一个真正能用的游戏内切片做出来

04：在 `python/spireagent/workbench` 既有服务上定义只包含下一切片所需的 capability、
状态、模型清单/准备、打开同对象、任务观察和恢复入口。身份绑定、loopback/Origin、
有界 payload、账号切换、无服务启动/重连和双端取消均明确；不开放任意命令/URL/文件读取。
一次应用意图映射已有服务，不新造存储或任务数据库。确切端点名以批准合同为准。

05：限定在 `apps/ingame-ui`/`apps/game-mod` owning seam 与现有 console 上做宿主可行性
验证。只证明导航、焦点、非阻塞、认证返回和状态恢复；比较原生薄视图和嵌入复用方案。
未证明安装/平台/安全代价前不引入 WebView，也不提交一个外部浏览器快捷方式冒充完成。

06：交付一个竖向用例：游戏内找到模型、加载并接管、看到实际状态、暂停/结束和打开
对应报告；操作复用 `LocalModelService`、`NativeTasks`、Policy Runtime 和原生 task bridge。
双端点击、推理中 Pause、录制 Close 未决、游戏换实例、重开面板都要测。
游戏 UI 有源码改动就按 owning G3/G4 重新 exact build/load；不借旧 DLL 的 Human 资格。
外部窗口是可选同一视图，不是必须去另一网站手工拼流程。

## 1a-08/09：采集和数据管理是同一条路径

08：复用 Recorder 应用服务、Workbench delivery、Hub collection/quality 投影，在游戏内
完成启用/暂停/结束采集、查看原始包/决策质量与队列、打开精确云端回执及失败恢复。
区别 accepted/proved/cancelled/diagnostic/unknown，UI 不解析原因字符串自己裁定。
只读页面不封包、不上传；游戏退出后已封存任务的继续/重连由独立 owner 负责。

09：在同一对象上下文里筛选、全选结果、预览、固定 dataset/version/allocation，再送训练。
用途/Gold/成员/来源撤销继续由 Hub 授权；run/decision 隔离来自版本化研究合同。
归档/恢复不删原件或用途账，quality 与本次不选分开；分页/切页保留选择。
需要新增 server 操作必须走已有 curation owner 的合同/回归，不靠 UI label 实现。
两个任务分别交付，不能变成“重写所有数据页面”的大 PR。G2，服务上线另计 G6。

## 1a-10：有界本机训练从工作台启动并可监测

先只支持一个已有 1a 配方的完整竖向流程，再扩到现有四格；不要建通用插件平台。
沿用 `research_cli` 对应 owning services、现有 token/pooled Worker、ArtifactStore、
Reporter 与用途账。输入是已固定的 ID，不让客户端上传任意 Python/命令或解除 Gold。
工作台展示可用配方/支持范围/执行本机/输入版本/资源限制；云执行仍显示未授权或 1b。

训练在独立、可识别的 worker/监督边界运行，不绑页面请求和游戏主线程；UI 退出可继续，
电脑休眠/重启不能被伪装成持续运行。用实际 checkpoint/resume 能力恢复，不伪造暂停支持。
完成证据包含一次真实输入小任务、新进程恢复、状态/失败/日志、结果链及游戏关闭后再观察。
先做 synthetic 故障回归再授权真实训练；任何预计等待超过五分钟任务交给真人监测。
共享 GPU/内存默认不同时跑训练与实时推理；资源争用策略是显式用户选择而非静默降级。

## 1a-11/12：分析、模型与云连接不另起一套产品

11：从任务结果打开现有报告/基线/切片/错误与精确输入，比较只接同协议的结果；未知/样本
不足与低分不同。模型卡贯通数据→配置→checkpoint/result→export→download/installed→
loaded/live/evaluation，模型归档不代表卸载或停用。缓存评分与新输入延迟分开展示。
Agent 报告继续独立于 Human，分享必须沿用明确授权，不自动公开。

12：按实际页面逐个加入本机 BFF 与 Hub 同对象导航、原设备登录/重连、权限及离线状态。
不得共享 admin/R2/Modal 秘钥或让云反向调用 localhost。登录返回原任务；撤销、账号切换、
已有云任务、未上传本机队列分别解释。敏感 admin 操作可外部授权完成，但游戏内有可达入口
和状态回链。不为了展示云连通而无关部署或消费计算预算。
如某服务需要上线，提交一次有界 G6 rollout 计划，执行前由真人核对备份/回滚。

## 1a-13：签收不是再训练一次

先由 Luna 完成适用 portable/contract/package/native gate，列出真实缺口；
再给真人最短、明确的黄金旅程与恢复旅程脚本。监测窗口由执行者生成并验证，不预造路径。
每次 canary 一组问题，不能把真人当反复 reload 的调试循环。

签收包包含：四模型×场景证据、游戏内全任务可达/操作清单、真实小训练/恢复、数据链/权限、
模型及服务身份、手动接管/失败恢复计时、已知限制、备份/回滚和正式发布回执。
必须单独标记 source/test/build/installed/loaded/live/product_accepted；产品可用不等于
科学强度。当前四格小样性能不用于宣称模型优胜或完整通关能力。

1a 阶段门通过后，提出 1b-00 的真实库存、代表输入 profile、成本记录和预算审批任务；
未授权不提交付费任务。后续 AI 研究由错误/覆盖/成本证据决定，不自动恢复历史旧训练。
