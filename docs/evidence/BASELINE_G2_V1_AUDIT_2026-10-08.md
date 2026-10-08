# 完整 G2／V1 初始独立审计与修复台账

日期：2026-10-08。审计基点 `4256f952e6db3983b259f805121571c8d71a7f6c`。结论：**完整 G2/V1 尚未完成，实施与验证继续；最终批准属于用户。** 本记录不是验收通过回执。[执行矩阵](../plans/BASELINE_G2_V1_EXECUTION_2026-10-08.md)保留全部 required 分母。

## 1. 方法与边界

核对本轮原始需求及修正：上层世界/Host/协议/Agent/Model、三条责任线、信息能力、原生浏览/目标预览、异步输入、记录/转换假设、I/F 关闭、结构模型、大视图、产品与恢复、19 个任务编号、完整补齐及用户审批。相关私有聊天仅用于核意图，不发布原始聊天。

原任务表、REQ/SC、SX/EX、L64、产品 A–H、环境与场景、团队/工作台要求逐项对账。历史材料不自动正确；所保留的真实需求不能因文件标为历史而删除。基点源码、实际 S0 证据、旧产品/恢复候选分别核对，不把旧 CI 或工作者文字升级成新运行证据。

独立视角：`g1_environment_audit`（原生/协议）、`g1_learning_audit`（模型/记录/数据）、`g1_operations_audit`（产品/资源/恢复）、`g1_final_plan_review`（原要求与取消/选择区分）、`v1_cold_engineer`（无旧实现作者上下文的新工程师）。本轮初审为 source/docs/小型既有回执核对，未做新游戏、训练、部署或全量私有语料扫描。lead 复读关键源函数、原需求、实际回执，后续 implementation 另有作者与独立 reviewer。

## 2. 必须修复的缺口

以下锚点固定于审计基点；后续代码行号可能变化。

| 责任 | 已确认事实／依据 | 后果与处理 |
| --- | --- | --- |
| P0–P5/G1 | sole spec 只接受 S0，原任务表仍称 E 未启动；L64/SX21/SC16 没有当前完整证据台账 | 修当前规范、状态及执行矩阵；保留历史有限接受；用户最终批准 G2/V1 |
| E1 原生机制 | `NativeCombatPileSelection.cs:119,270` raw min 额外否决原生 enabled；`NativeSimpleCardSelection.cs:113` manual 条件与 native min0 不同 | exact native/caller 核对后修 owning adapter，原生控制权不变；不让模型弥补 |
| E1 交互/时机 | `TextMenuV2Session.cs:105–153` 是虚拟 staging；`SealedObservationService.cs:20–24` 是 sampled current cursor；`contracts.ts:147–159,414` 仍强制 scores/后继 | 新版本 native/event 合同和通用 Agent 端口，保留旧 profile 身份 |
| E1 终局/规模 | `NativeTextMenuFrameBuilder.cs:89–91` 非进行中清空 leaves；`BoundActionProjection.cs:77–80,124–133` 上限 512 | 补 terminal/summary owner、完整大目录及访问；JSON chunk 不等目录分页已完成 |
| E2 来源/记录 | `HumanTextInputObservation.cs:30–38` 只有五种输入；`RecordingApplication.cs:35–39` 无 operator 来源声明；bundle/import 强制 Human attestation | shared freeze、完整观察/输入流与显式 actor；AI 原生 UI 功能证据不得洗 Human |
| E2 数据/历史 | `structured_sequences.py:90–95,167–170` 只接 agent/synthetic、准入非空 C；旧 Human side stream 不含完整浏览/焦点前缀 | 版本化 InputSpec/ProjectionSpec、no-action/terminal、缺口/假设/来源拒绝；旧 raw 不回填 |
| E3 示范/自主性 | 普通奖励 fallback 给公开 activate/skip，teacher 只找另一组 verb；真实训练在奖励处 abstain，learned open/skip 循环 | 修 typed public teacher 合同及监督覆盖，再独立验证 learned 连续推进；不强制外壳替选 |
| E3/E6 退出证据 | `baseline-s0-runner.mjs:229` 丢弃实际 `episode.close()` 返回 | 新运行保存 Host close receipt，历史缺失保持未知 |
| E4 恢复 | `structured_training.py:111–115` fresh 初始化；worker `:149–165` 拒绝旧输出，`:207–221` 仅最终 checkpoint | 安全边界 checkpoint、exact cursor/W/optimizer/RNG、显式 attempt/fence、累计预算及中断恢复 |
| E4/E6 产品接缝 | 相对旧 develop，S0 未修改 `python/spireagent`；训练、导出、注册、GUI 只识别旧 recipes/profiles | 同一 application service/journal 下静态可信 adapters，补普通库/CLI/API/GUI 真实链 |
| E5 资源/权限 | Hub scheduler 的旧 workloads、本地 conservative journal、旧数据/派生撤销和服务恢复不足以证明新组合 | 指定远端实际任务、use/权限/取消/reconcile/备份恢复与 offline 行为 |
| E6 游戏内体验 | `PlatformLiveUiMod.cs:325` 仅开浏览器，不满足游戏内完整工作台 | 同后台核心用例的 native/外部界面、任务上下文与实际操作，不另造业务 authority |
| V1 覆盖/成本 | S0 60-call 段、少量牌、一个角色/配置；大视图历史材料仅参考 sizing | 全适用机制、角色难度、默认自主主要流程、正常/压力负载、30 attempt 与产品恢复真实验收 |

完整环境覆盖、训练覆盖、learned 自主到达、保护机制和策略质量均保留独立结论。没有检查某个原生机制时标待核，不凭上述列表宣称全部代码都缺失。

## 3. 已选修复与复用原则

- 保留现有 native exact leaf、controller、request fingerprint、执行复验与 unknown 禁重发；新 profile 不建第二执行/合法性引擎。
- 扩 immutable bytes/cursor/store 的真实能力，不把 S0 捕获序号直接当完整曝光历史；短暂 required 值当时冻结，大资料按声明时点捕获。
- 结构模型、safe checkpoint codec、ArtifactStore/RunReporter/use ledger 继续复用；新增训练恢复不得引入平行任务账本。
- 应用静态 recipe/Agent adapters 与共同 service 提供能力；下载 manifest 不指定 executable，GUI 不理解研究内部 schema 来替服务判定。
- 旧 e302 产品候选只取经核对的唯一机制：长期服务 resource/TMP 隔离、平台 fsync、拒绝 POST body 等独立修复另审；不盲合整个 M0/M2 stack。
- 旧数据按真实资格形成局部或假设化视图；采集能力的 AI 功能验证和真实 Human 来源分开。研发授权不覆盖任意 Gold 用途。

## 4. 外部一手资料的边界性参考

| 资料 | 本轮参考点 | 不能由此推导 |
| --- | --- | --- |
| [Kubernetes API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/) | 固定版本分页、有限 watch 历史、过期明确返回 gap 并重新建立当前视图 | 游戏并非 MVCC store；它不能替我们证明 native capture 完整或 causal successor |
| [PyTorch checkpoint 指南](https://docs.pytorch.org/tutorials/beginner/saving_loading_models.html) | 恢复需要模型之外的 optimizer/训练位置与正确模式 | 保存权重不证明序列/W/RNG/预算连续；仍用项目安全 codec，不引入 pickle 加载 |
| [Gymnasium episode 边界](https://gymnasium.farama.org/v1.0.0/api/env/) | 自然 termination 与外部 truncation 分开，影响学习目标 | task budget Stop 不是 STS2 自然死亡；通用 RL API 不证明本项目因果转换资格 |

这些是设计参考，没有新增 Kubernetes/Gymnasium 或其他运行依赖。

## 5. 当前回执与下一个门槛

初审之后，完整矩阵和 owning correction 已开始。selector、reward teacher/Host exit、structured resume 各有独立 writer；共享 wire、application contracts 和集成由 lead 协调。具体提交、检查、安装、实际样本与费用只在发生后写入，不能提前填 PASS。

最终须独立审核全部实现与原件、新工程师按公开操作入口复现，提供完整阅读材料；用户尚未批准 G2/V1。
