# 近期未合并工作：历史核对、组合审查与 develop 集成

## 本轮目标与边界

用户 2026-10-22 要求仔细检查近期所有未合并工作，深查上层逻辑、decoupling、协议/组件/格式及实际完整链路，先收敛再合并。root 负责原件核对、组合写入、最终审查和集成；原作者只提供既有结果或有界只读复核。工程合并不授权新付费、训练、游戏控制、生产发布、日常升级、资产删除或旧 PR 关闭。

准确基线 `origin/develop=1e5d83100a8222f211a0e633120ea8123dd06427`。独立 topic `codex/p1-reviewed-convergence-20261022` 合并保留原提交：PR211 `164db48c3f90a37d21df35bb0225be3b47b3a791`、PR212 `935e759bc455d2b903cc729de4b06186e8ed02a9`、PR213 `44368a29b99e1a1206dadfdf55cf5786317ed282`。原分支不重写；组合清单和当前入口由 root 解决，最终 head、树、检查与合入回执在本轮 PR 记录。

可验收结果：本批仍适用的源码在同一组合中通过准确检查，责任/语义/版本与操作入口一致；旧独有成果保持可定位，未迁能力如实保留。条件式合并仅在组合审查与所选门通过后执行；到 develop 集成核验即收口，不把正式训练或运行资格暗加为交付。

## 历史决定如何落实

已回查 9月15日统一/解耦、9月28日 Host→适配→交互、10月5日协议语义和数据定义/管理/使用、10月11日新旧组合、10月16–20日 P1 可替换与独立记录/重放、10月20–22日相对抽象、SOP 和持续训练的实际用户消息。原件和有损摘录的来源索引保留在本轮私有审查收据，不提交私人会话。用户原话中的粘贴助手答复不自动构成新决定。

这次不另定抽象。长期权威仍是 [Architecture](../../ARCHITECTURE.md)、[Components](../../COMPONENTS.md)、[唯一链路及 requires/provides](CHAIN_PURPOSES.md) 与 [P1 合同](../../protocols/P1_GLOBAL_R1.zh-CN.md)。核对的事实与影响如下：

| 连接 | 应保留的职责 | 本批实际检查 |
| --- | --- | --- |
| 世界/Host→公开环境→交互 profile | Host 的实现能力、Connector 的公平公开/绑定与 P1 的决策语义各有 owner；Host Runtime 管进程，不管游戏合法性 | PR211 只缓存 loaded Type 的不可变 MethodInfo，Harmony patch 状态每次重读；诊断记录真实 Stopwatch 区间。没有缓存合法性或新增第二个规则引擎 |
| 来源→不可变原件→profile 派生 | 记录来源尽量保事实，不因 P1 改写；原件 provides 经声明投影覆盖 profile requires | PR213 不改 Annotator 原件。native/Source4 各自读来源，最终共用 SDK reduce/match/lowering；LastingCandy 的窄比较保留严格差异。Skip 返回不推造永久拒绝，next/end 仍须真实后继/终局 |
| profile 经验→用途/输入目标→训练 | 存储与用途、表示与学习、数值与任务/资源局部解耦；必要语义依赖须显式版本化 | PR212 缓存命中仍由 Store 验全部原始 payload；freeze 重新读取权威对象。用途仍由原 Curation 账决定，未知谱系不变成科学独立样本；新输入不修改已固定 Snapshot |
| 训练任务→worker/provider→Model→Agent | 用同 Application、Run、ComputeRequest、checkpoint 和产物 owner；模型不是完整 Agent | PR212 的 random/frequency/linear 与 M-std 共用控制/产物链；算法留在研究 owner。DEV 选优、TEST 封存、ACK 提交 RNG 与精确恢复身份分别审查，不新造控制器 |
| GUI/CLI/API→Application | 多入口映射同一业务状态机，页面不是权威 | prepare/freeze/training/summary 沿原应用服务；支持操作和失败/unknown 归 [PROJECT_CONSOLE](../../../python/docs/PROJECT_CONSOLE.md)，不是私有脚本或新 GUI 流程 |

以前遇到过反向 Workbench 依赖、缺后继、旧分支清单漏更新、checkpoint 状态不完整及 CUDA 恢复/分数不一致。当时分别在 `policy_files.bound_path`、原生就绪接缝、正式 package 检查和各自恢复 owner 处理；这次复用相同 owner 与可证伪检查，不搬回旧 controller、不放宽身份或补造历史。直接入口见 [已有解法](../../RESOLVED_ISSUES.zh-CN.md) 和原训练任务的失败记录。

## 全部未合并工作与旧独有成果

实时开放 PR 共8个。近期本地未合 heads 共6个：上述3个 PR 及 PR212 已包含的 agents/data/execution 三个子分支，不重复合入子分支。Git 非祖先或 patch 不同不等于语义未迁；目录同名也不等于功能等价。

| 对象 | 当前处理与理由 |
| --- | --- |
| PR211 | 本批整合其32路径候选，原 native/build/install/load 和 full CI 证据留原准确身份；剩余整体卡顿已由用户接受为已知表现 |
| PR212 | 本批整合其55路径候选；原训练链、恢复/DEV/TEST 与边界非作者复核通过。正式四 Agent 长训、统一 TEST/实战评价、全云无人值守与 GPU 恢复仍未完成 |
| PR213 | 本批整合其15路径候选；原件不改、共享语义核、窄动画规则及反例已独立复核。旧锁漏更新导致的失败保留；最终组合再核所有消费者与 BOM |
| PR24 `0ffb3cf7`、PR25 `6b1d006f` | Workshop L1 元数据/preview 和 L2 exact staging 是独有发布消费者，当前 `workshop/`/stage 入口不存在；不谎称已迁。后续 L3/L4 已留原 [K3 去向](CHAIN_PURPOSES.md#历史24项未来复用的精确入口2026-10-18)。本批不恢复 Workshop 产品目标，也不把旧 root package/README 直接叠回 |
| PR172 `c358d2b4` | 单 Skill 候选。后续 `6e5303d0` 的现 Skill 已明确当前 owner、已有授权、不另定 SOP、诊断和资格分开及未知不重发；保留旧候选供逐项复用，不用旧文件覆盖现行版本。未执行关闭 |
| PR178 `41e20810` | 旧治理/系统图/计划栈；现 [DECISIONS](../../DECISIONS.md) 已明示选择性承接有效决定，WORKING_RULES/Architecture/CHAIN/P1 各有唯一 owner。旧 ENGINEERING_RULES/SYSTEM_MAP/SCOREBOARD 不再是当前入口；不恢复两个当前规范/派工入口，原能力目标不伪称实绩 |
| PR181 `6cc2a0d7` | J-min 独有能力与后续 owner 正逐项复核；不先宣称整栈已迁或直接叠回旧 training controller。旧 CUDA 失败保原对象/条件；最终差额结论补在本节，不因此恢复旧任务预算 |

没有为“干净”删除/移动数据、模型、工作树或分支，没有以盘点替代未完成迁入。旧独有发布/数值能力保留原 PR/ref；未来有真实消费者时先查该原件，再在当前 owner 接回。

## 验证、证据与停止条件

本轮属于 G2 跨组件组合；PR211 的既有 G3/G4 证据绑定其原 bytes，组合源码检查不自动给新的 installed/loaded/runtime/Human 或 Managed 资格。三个原 PR 的准确门、失败/跳过和未完成项分别保留，不拼计为新组合结果。

编辑/冲突解决后先跑 SDK→native 转换与实际包消费者的忠实连接、根规则与 closeout；稳定候选才执行所选 full gate。非作者复核关注组合增量、清单/锁/历史身份和真实调用，不对未变源码重复全审。完整 CI 失败必须定位首错，不空重跑；路由 skipped 不算 passed。

当前状态：独立 topic 组合进行中；尚未合入 develop。后续回执归本批 PR 和私有证据目录。完成本批条件式 merge 后核准确父提交、树、组件身份及集成检查，CURRENT 改为最终交付导航；不启动下一项训练、发布或治理工程。
