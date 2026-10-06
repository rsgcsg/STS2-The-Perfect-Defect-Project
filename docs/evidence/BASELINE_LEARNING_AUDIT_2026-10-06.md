# P2 补充与 P3/P4：模型、横轴、Z/O 和交互设计依据

日期：2026-10-06。范围：本轮用户授权的设计工作。只读核对相关历史文件、确切源码和设计报告；没有执行模型训练、私有语料迁移、游戏、安装或 provider 操作。新设计见 [P3](../design/BASELINE_INTERACTION_CANDIDATES.zh-CN.md)和 [P4](../design/BASELINE_DATA_AGENT_CONTRACTS.zh-CN.md)。

本轮写作基点为 6c1a5227c086a6714881b2654ec8932b2a14dcf7；该文档分支原始 develop 基点为 9556d21188b2deea027192567827a339a0ce50f7。产品 e30247defd859dfc75708fea4612c668e93cdbd2、恢复 762907a641f603e1db32155058a70efbf3b11ee5 仍为独立候选。下列源链接明确使用各自版本，历史小样不是当前新协议资格。

## 1. 研究地图与当前实现不是同一张表

| 依据 | 核对结果 | 设计后果 |
| --- | --- | --- |
| [MODEL_DESIGN 家族](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/6c1a5227c086a6714881b2654ec8932b2a14dcf7/python/docs/research/MODEL_DESIGN.zh-CN.md#L96) | A、B、C1/C2、D-Simple/D1/D2 是研究地图；B/C1 相同图和目标不重复实验；D2 两遍/三遍不是多次游戏动作 | P4 保留研究名词，不把所有结构写成已实现或必须跑全矩阵 |
| [横轴与记忆](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/6c1a5227c086a6714881b2654ec8932b2a14dcf7/python/docs/research/MODEL_DESIGN.zh-CN.md#L238) | PF/PL/RF/S；M0/M1/M2；N/Z/O；独立 R。RF 是同架构随机冻结；M1/M2 按职责区分 | 参数训练范围、tokenizer、输入梯度、状态及假设分支要明确，不按 slot 形状判断模型能力 |
| [Stage1a recipes](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/6c1a5227c086a6714881b2654ec8932b2a14dcf7/python/stpd/stage1a_recipes.py#L12) | 已有 B/D-Simple 和 light-action M0 配方，包含不同骨干支持；recipe 存在不是该条件训练通过 | 不把设计目录等同源码 recipe，更不能等同实战结果 |
| [旧 objectives](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/6c1a5227c086a6714881b2654ec8932b2a14dcf7/python/stpd/models/objectives.py#L23) | Scheme1 ranking；S2-Simple 执行分支 Z；历史 S2-SDT 加 anchor。这里没有 O-chain 目标实现 | 复用已存在 N/Z 机制；O 仍需独立实现/标签/验收，不能称当前 M2 已联合训练 Z/O |
| [light-action M2](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/6c1a5227c086a6714881b2654ec8932b2a14dcf7/python/stpd/models/light_action_m2.py#L33)、[training](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/6c1a5227c086a6714881b2654ec8932b2a14dcf7/python/stpd/models/light_action_m2_training.py#L143) | 更新候选无关状态、候选只读，支持 reset 与可选历史条件； inspected training 使用 listwise N，chunk 后 detach | 正式历史与训练窗口不能混用；状态机制可复用，标签资格由数据 owner 负责 |
| [Public M2 协议](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/python/docs/research/PUBLIC_M2_N_ONLY_PROTOCOL.md#L1) | 新候选为 N-only observation-only；原协议含 M0、carry8、独立 reset8、carry1、数据规模 arms；M0/M2 内部编码不完全匹配 | A02/A03 才是同 M2 图 carry/reset 比较；现成 M0 对 M2 不称纯记忆实验 |
| [Public M2 source/consumer](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/docs/evidence/PUBLIC_M2_CONSUMER_SOURCE_2026-10-04.md#L9) | 选定正式 A06/A08/A02 为 carry8/window4 的规模路线，source 消费不代表正式产物或已加载 | 不把三个规模组叫 M0/Reset/记忆对照；原停止交接见 P1，未重新查当前 provider |

相关 Python 源路径检索和数值目标核读没有找到与总纲相同的完整 O-chain 训练链；这是本轮检查范围内的实现缺口结论，不是对所有历史分支/私有实验的全称断言。

## 2. 已有 M2 记录应保留到什么程度

- [2026-09-29 M2 integration](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/6c1a5227c086a6714881b2654ec8932b2a14dcf7/docs/evidence/STAGE1A_M2_INTEGRATION_2026-09-29.md#L54)记录有界 train-only 548-observation 工作及实际 native decisions；明确没有匹配 Reset、独立 dev、memory benefit 或 full-game 资格。
- [2026-09-30 v2 engineering](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/6c1a5227c086a6714881b2654ec8932b2a14dcf7/docs/evidence/STAGE1A_M2_V2_ENGINEERING_2026-09-30.md#L34)记录六步工程训练/导出；因果后继 masks 为 false，parity 使用同一训练 episode，不是科学测试。
- [retrospective dev](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/6c1a5227c086a6714881b2654ec8932b2a14dcf7/docs/evidence/STAGE1A_M2_DEV_EVALUATION_2026-09-29.md#L10)记录 31 条 dev 和 14/31 top-1，同时声明 native-run 独立性未证，没有匹配 Reset 对照。

这些是原报告的历史范围，本轮未重跑。后来的 Public M2 或新协议不能继承这些结果。P4 设计保留机制和可复核产物，不把旧小样删除，也不将它们升级成新基线。

## 3. Z/O 的原始需求比“胜负一个标签”更丰富

[MODEL_DESIGN 的 O 定义](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/6c1a5227c086a6714881b2654ec8932b2a14dcf7/python/docs/research/MODEL_DESIGN.zh-CN.md#L420)明确要求最终胜利、真实失败层数、战斗结束 HP、剩余主动出牌数，以及独立 end-latent。旧设计已要求逐项目 mask，不把血量高/动作少直接定义成最优。

[Stage1a 的后续修订](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/6c1a5227c086a6714881b2654ec8932b2a14dcf7/python/docs/research/STAGE1A.zh-CN.md#L44)进一步明确：历史前缀和 split group；Z 只用执行分支的合格后继；严格 scratch 不暗用 Qwen teacher；O 的未知终点不填 0；偏好需要可比双方；返回/查看不设固定罚分；旧主动出牌数不等于协议交互成本。

本轮 P4 据此保留目标，不把它缩成 win，也不引入任意线性奖励。新增的 run_progress、协议/Read/推理成本是为避免混用计数与删失的明确候选量，不悄悄改变旧 failure_floor/remaining_active_plays 的含义。参数、标签覆盖和统计评价在 P5/实验计划中固定。

## 4. P3 的原生与接口依据

| 已核对源码 | 实际支持的结论 | 不能直接推出 |
| --- | --- | --- |
| [公开牌堆 Read](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/e30247defd859dfc75708fea4612c668e93cdbd2/components/connector/host/LiveHost/PlayerVisibleReadBuilder.cs#L124) | 集合、多重性与 draw-order 隐藏有明确投影 | 所有时点都无副作用可读或所有协议自动等价 |
| [原生牌堆选择](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/6c1a5227c086a6714881b2654ec8932b2a14dcf7/components/connector/host/NativeUi/NativeCombatPileSelection.cs#L58) | 从当前 visible holders 形成候选，零 holders 有拒绝路径 | 需要验证完整逻辑列表、滚动/虚拟化与零选边界；这里不宣称已证实截断 defect |
| [NativePlayerChoiceLineage](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/6c1a5227c086a6714881b2654ec8932b2a14dcf7/components/native-foundation/src/NativeDecisionContracts.cs#L307) | 捕获 CurrentlyRunningAction，并标 parent_observed | 不是确切 child-await 因果证明，不能替代后续绑定审计 |
| [原生生命周期](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/6c1a5227c086a6714881b2654ec8932b2a14dcf7/components/native-foundation/src/NativeActionLifecycleObserver.cs#L17) | started/paused/ready/resumed/cancelled/finished 观察入口可复用 | Finished 不是因果 successor，未观测关系不能后补 |
| [text-menu profile](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/6c1a5227c086a6714881b2654ec8932b2a14dcf7/components/connector/docs/TEXT_MENU_PROFILE.md#L5) | 原生页、协议导航和 v2 暂选有不同 effect domain | 当前 complete 标签不证明新语义完成，旧数据不能只改 schema 迁移 |

P3 用这些真实接缝提出完整候选与拒绝条件，没有在本轮取得新的 exact-game、Human 或跨 Host 资格。SC 的完整矩阵仍需 P5 逐项设计并在 E/V 阶段执行。

## 5. 外部方法的使用范围

本轮查看了 [Ng/Harada/Russell reward shaping 原论文](https://ai.stanford.edu/~ang/papers/shaping-icml99.pdf)和 [Agarwal 等统计评价研究](https://arxiv.org/abs/2108.13264)，只用于提醒额外奖励的条件与少运行数的统计不确定性。没有把论文算法、公式或 benchmark 结果当成本项目实现/训练证据，也没有依据外部文章确定未经测量的胜率、预算或时限。

## 6. 本轮设计变化与不变范围

P2 补齐第二条完整 Agent/学习/评估链；P3 给出完整交互候选、具体消息/时序语义、场景映射和推荐验证顺序；P4 给出数据/目标/横轴/实验/运行以及任务、资源、权限、恢复、分发接口。P5 尚未执行成本测量或原型；G1 尚未接受默认协议、实验配置或实施范围。

当前任务仍只写设计和规范，不自动实施 O 目标、修改旧训练、继续已停止 campaign、合并候选或部署新环境。旧计划与证据保持原义，新合同改变语义时生成新身份。
