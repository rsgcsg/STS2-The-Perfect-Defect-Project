# P1：新基线现状与历史依据审计

审计日期：2026-10-06（Australia/Brisbane）。范围：本轮 P0/P1/P2 文档工作的只读事实核对。主阅读文档为 [基础设计](../design/BASELINE_FOUNDATION.zh-CN.md)，派发与依赖见 [任务表](../plans/BASELINE_TASKS.zh-CN.md)。

本文是有范围的审计，不是全仓所有代码、全部私有语料或生产服务的重新资格化。当前源码、未合入候选、历史运行记录、局部复核和设计建议分别列出。没有启动游戏、录制、训练、provider 操作、生产服务探测或数据用途变更。

## 1. 精确锚点与核对方法

| 对象 | 本轮核对值 | 含义 |
| --- | --- | --- |
| 活跃 repository | rsgcsg/STS2-The-Perfect-Defect-Project | 原 STS2-AI-PLATFORM README 已声明历史仓库；不在那里写新设计 |
| develop | 9556d21188b2deea027192567827a339a0ce50f7 | 本轮新 topic 的唯一基线 |
| main | 83e25b1d97b9552211a748a70897a986090dfb86 | 正式发布历史，不代表当前安装 |
| 产品候选 / PR164 | e30247defd859dfc75708fea4612c668e93cdbd2 | 未合并候选；不能将其能力写成 develop 已有 |
| 训练恢复候选 / PR163 | 762907a641f603e1db32155058a70efbf3b11ee5 | 与产品候选不是同一条完整能力链 |

执行了 git fetch --prune、远端 refs 与开放 PR 查询、commit ancestry/tree 比较、确切源码/文档读取和相关小型本地 metadata 核对。未修改上述候选的工作树。证据链接使用不可变 commit，不使用工作站绝对路径。

PR162、PR163、PR164 的 plan、Linux、Windows、portable 均已成功，docs job 按路由跳过：

- [PR162 CI37217640847](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/37217640847)，head 829496cfaf012defd0f169341fef13ee248dc218。
- [PR163 CI37237872538](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/37237872538)，head 762907a641f603e1db32155058a70efbf3b11ee5。
- [PR164 CI37248703003](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/37248703003)，head e30247defd859dfc75708fea4612c668e93cdbd2。

这些是原候选的 source/test 结果，不是本轮文档 head 的 CI，也不证明安装、加载、Human、GPU 或策略质量。交接材料中 PR164 in_progress 已过时；旧冻结快照不改写。

main 与 develop 的共同祖先为 7c4f5deb1062d1b57701230fef8b63cf9b7aa521。main tree 与该祖先均为 fb1d374087be69a61a30ebfb5e90fa2c49b3c4a9；20 个 main 独有提交是 merge 历史，不能当成 20 个独有功能修复，也不需要仅因 ancestry 差异创建空同步 PR。

## 2. 开放工作快照及处置边界

在本轮文档分支推送/创建 PR **之前**，远端为 28 个开放 PR、33 条分支；其中 23 个 PR 编号至少为 140，全部 Draft。下表是这次快照，不是永久实时清单。

| 组 | PR（完整覆盖 28 个） | 源码关系与建议 |
| --- | --- | --- |
| Public M0 与基础整合 | #140、#141、#144、#145、#146、#147、#148 | 已包含于 e302；整体差异审查后吸收或改造，不重复盲合并 |
| 原生/产品生命周期与发行准备 | #149、#152、#153、#154、#156、#158、#159、#160 | 已包含于 e302；source、普通启动与真实模型资格分开 |
| Public M2、Port4 与消费端 | #150、#151、#157、#162、#164 | 已包含于 e302；不因此成为新协议或完成正式训练 |
| campaign 与恢复 | #161、#163 | 独立增量需与消费端协调；不以整份 develop diff 覆盖产品树 |
| 存储性能 | #155 | 独立评价正确性/收益后吸收或明确延期 |
| Workshop 发布 | #24、#25、#26、#30 | 独立渠道工作，不是核心闭环的隐含前置 |
| Workshop consumer | #45 | 依据新发行/采集条件重审，再决定保留、改造或替代 |

本地 ancestry 核验确认 e302 包含表中前 20 个 PR head。762907 与 e302 的共同祖先是 96c86a2e03bcb6b0c9f7c64650b0f20d32c89731；恢复侧独有 4 提交，产品侧独有 74 提交。因此不能称产品候选已经包含恢复候选。

另三条没有开放 PR 的远端分支：codex/m2-linux-pilot-20261004-21f7a04、feat/new01-faithful-page-input、fix/pr131-managed-text-selection-ownership。必须核对唯一增量、运行/证据引用后处置。本轮不关闭 PR、不删分支，也不把分支数当未完成能力数。

## 3. 当前能力与接缝

下表的“存在”仅指注明 anchor 的源码能力。未列新运行证据的行均不能升级为当前实际运行保证。

| 事实 ID | 当前事实与依据 | 对新基线的意义 |
| --- | --- | --- |
| FACT-01 | develop 的 [public_inputs](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/9556d21188b2deea027192567827a339a0ce50f7/python/stpd/fullrun/public_inputs.py#L40) 消费当前公开 Snapshot 和完整候选；产品 [M0 port](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/python/stpd/policy/public_m0_port.py#L65) 拒绝非空 Reads | 完整候选不等于完整信息能力；投影裁剪必须进入 P3/P4 分析 |
| FACT-02 | 产品 [Public M2 sequence](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/python/stpd/fullrun/public_m2_sequences.py#L27) 明确 observation-only、no-prior-action、no-feedback；[port](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/python/stpd/policy/public_m2_port.py#L255) 也不提供动作/反馈特征；这些 public-M2 文件不在 develop | 这是特定实验，不是通用有历史 Agent 接口 |
| FACT-03 | develop 的 [observed_input_sequence](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/9556d21188b2deea027192567827a339a0ce50f7/python/stpd/fullrun/observed_input_sequence.py#L324) 对 Human text capture ordinal 排序，隔离未知位置；Agent、Human 的来源和反馈 mask 分开 | 复用顺序/缺口处理；不能把旧 native-text 流与 Public M2 执行相邻链混为一谈 |
| FACT-04 | [Human text import](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/9556d21188b2deea027192567827a339a0ce50f7/python/stpd/fullrun/text_menu_human_import.py#L1) 保留原始 archive；Human witness 不证明 delivery/effect/successor | 记录不是心理知识或统一 S-a-S'；用途需额外判定 |
| FACT-05 | [dataset policy](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/9556d21188b2deea027192567827a339a0ce50f7/python/stpd/fullrun/dataset_policy.py#L14) 与 [local curation](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/9556d21188b2deea027192567827a339a0ce50f7/python/spireagent/workbench/local_curation.py#L274) 处理 held-out ancestry、重叠和未知历史用途 | 下载、解析或重建视图不创造新用途或 clean-heldout 资格 |
| FACT-06 | [save_scene](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/9556d21188b2deea027192567827a339a0ce50f7/python/spireagent/workbench/local_environment.py#L951) 保存固定种子新局配方 | 不能称任意半局/Agent 会话恢复；管理能力须分级 |
| FACT-07 | [Hub scheduler](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/9556d21188b2deea027192567827a339a0ce50f7/python/spireagent/hub/scheduler.py#L98) 限 feature/training，持久化 intent 后提交，未知不自动重发，当前路径最多一个 active job | 有可复用恢复基础；未实现通用多主机/资源放置 |
| FACT-08 | [access](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/9556d21188b2deea027192567827a339a0ce50f7/python/spireagent/hub/access.py#L68) 检查 sealed lineage；产品 [collections](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/python/spireagent/hub/collections.py#L270) 中 received-source withdrawal 的专门 ancestry 检查限定 root dataset | 不能声称撤销对所有派生产物统一传播；先定义规则再实现，不承诺收回已下载字节 |
| FACT-09 | 产品有独立 [M2 evaluation worker](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/python/stpd/workers/public_m2_evaluation.py#L1)、[CLI](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/python/stpd/policy/public_m2_cli.py#L13)、训练和消费代码；普通 [local_training](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/python/spireagent/workbench/local_training.py#L2644) 配方和 [evaluation](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/python/spireagent/workbench/local_evaluation.py#L326) 入口仍受旧 schema 范围限制 | 准确缺口是共同应用服务/普通工作台接缝，不是“没有 M2 训练评估实现” |
| FACT-10 | 产品 [model export](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/python/spireagent/workbench/local_model_export.py#L858) 通过 Registry 发现唯一 epoch stage | 索引与不可变事实的关系应审查；新接口可接确切 stage 再由 owner 验证 |
| FACT-11 | [PlayerVisibleReadBuilder](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/components/connector/host/LiveHost/PlayerVisibleReadBuilder.cs#L103) 直接提供公开牌堆集合并隐藏真实顺序；[OpenPile](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/components/connector/host/PlayerEnvironment/TextMenu/NativeTextMenuInformation.cs#L1162) 实际调用原生按钮；[TextMenuSession](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/components/connector/host/PlayerEnvironment/TextMenu/TextMenuSession.cs#L11) 有虚拟导航状态 | 三种机制可提供复用材料，不等于它们是一套协议或哪一种已选为默认 |
| FACT-12 | 产品 [semantic cycle guard](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/components/policy-runtime/src/semantic-cycle.ts#L34) 检查周期 1/2 的三次重复 | 有界保护不是模型学会重访，也不是所有循环的保证 |

## 4. 历史失败与当前非声明

公开的 [9 月 28 日有界尝试](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/9556d21188b2deea027192567827a339a0ce50f7/docs/evidence/BOUNDED_GAME_ATTEMPT_2026-09-28.md#L99) 记录信息导航循环：16 次尝试中 15 次内部导航、1 次原生返回。这证明工程链可执行但行为没有有效推进，不证明所有后来模型仍有同样失败。

在本聊天前续审计中，另一个旧 Public M0 Agent-run 原始日志存在“奖励列表→卡牌奖励→跳过→奖励列表→重入”，输入均 delivered。使用 e302 公共投影对六个历史帧的局部重放，重复列表/选牌页分别得到相同模型内容（按动作语义忽略候选排列）。这只是历史记录的表示诊断，不是新模型推理、当前运行或全部语料统计。本次未重新操作游戏；原始日志留在私有存储。

用户提供的停止交接材料及小型 owner metadata 支持以下历史界限：

- M0 有训练完成及 CPU 产物链证据；产品最终交接仍未完成新候选下真实模型注册/加载/重开闭环。不能据此称权重损坏，也不能升级为当前已加载。
- 正式三组 Public M2 campaign 最终 accepted 为 0。重新读取的 journal SHA256 为 af4764048552c2502cc53a7568951507efdafc5c83b6e21967b1563853363c8a，保留 deploy_intent，target/handle/result/stop 均未形成；未调用 provider 核对当前外部状态。
- 历史小 pilot 不等于正式三组；正式计划中的三组改变训练规模，不是完整的无记忆/Reset/记忆对照。
- 原始 prepare_unknown:FileNotFoundError 没有足够历史 filename/stack 证据；不能把当前 PATH 假设改写为已证根因。最后执行窗口不足也不是自动延长授权或重新提交的理由。
- 已有 dev 与物理游戏独立性限制保留。schema 更新不能抹去曝光或未知。

材料校验：提供的交接 DOCX SHA256 为 62d9b0ffe07a6817d33eaff4d2daafa3ba1056eb99966b25e8d14dc066f7681f；索引 ZIP SHA256 为 5158506cb9e1412aca65716a9c0cd33408b4f1b41a9f67374ef0a064e75d2245，manifest 所列 9 个文件的 size/hash 全部核对。它们是用户提供的历史索引，不是新的执行授权；原文、私有路径、凭据、原始数据与权重不进入 Git。精确私有位置由资料 owner 保留。

尚未完成：全私有语料的新协议转换审计、当前安装/加载身份复核、当前 provider/billing reconciliation、正式 GPU/原生/Human 资格。P1 的 source/metadata 结论不覆盖这些范围。

## 5. 现有数据怎样进入后续审计

| 来源能力 | 可能保留的用途 | 必须拒绝的自动升级 |
| --- | --- | --- |
| 确切公开观察＋唯一选择 | 原条件单步监督、新表示的有据转换 | Human 完整知识或最优答案 |
| execution-adjacent Public M2 链 | 原 observation-only 实验 | 完整目标交互历史 |
| capture 顺序已知的 Human text | 已证范围的局部序列 | 没有缺口的全程记录 |
| 顺序未知但输入/选择可靠 | 单步样本 | 按 append/timestamp 猜连续记忆边 |
| 已验证 Agent 输入/反馈流 | 该 Agent 实际经历的重建 | Human 来源、任意因果后继 |
| 原生终止与结果证据 | 相应 run 结果 | 任意 per-action reward 或完整自主游戏 |
| 旧 dev/已使用来源 | 显式曝光条件的诊断 | 新 clean held-out |

本轮不改变任何准入、Gold、分组或 use ledger。P4 的迁移报告必须按目标用途给保留/转换/排除数量及原因；“保留 0 条完整轨迹”是允许的结果。

## 6. 当前规范冲突与旧计划的处理

| 材料 | 当前含义 | 后续协调方式 |
| --- | --- | --- |
| [ADR0015](../adr/0015-native-logical-interaction.md) | Accepted 仅覆盖上层方向与条件例子；当前页面、牌堆打开/返回及模型记忆路线均有具体约束 | 最新需求允许重新评估更一般的模型/信息方案；P3–P5 提出 superseding/amendment，不在 P2 偷改旧结论 |
| [产品交付](../STAGE1A_PRODUCT_DELIVERY.zh-CN.md) | 包含真实训练、完整旅程、Qwen/LoRA、N/Z/O 和记忆对照等里程碑 | 新 task ID 不自动取消旧目标；G1/E0 明确保留、替代、延期或退出 |
| [旧 Stage1a 排程](../plans/STAGE1A_TASKS.zh-CN.md) | 历史数字 ID 和四条并行线 | 保留历史映射；新派发只用 P/E/G/V/R，不把两套 ID 混用 |
| 文档索引 | 曾将 ADR0015 标为 Proposed，而 ADR 已限定 Accepted | 本轮修正入口标签，不改变 ADR 内容或扩大接受范围 |

## 7. P1 结论

现有系统有可复用的原生绑定、公开目录、记录验证、不可变产物、用途和恢复机制。主要风险是信息能力在多层被裁剪、历史语义不统一、专用实验与共同应用服务未完整衔接，以及 source/候选/运行/研究状态容易被压成一个“完成”。

因此 P2 应确定责任和连接，P3/P4 再定义具体协议与数据转换。不是整体推倒，也不是先把全部候选合入来迫使新设计服从旧实现。当前 P0/P1/P2 的交付不能称新工程基线完成。
