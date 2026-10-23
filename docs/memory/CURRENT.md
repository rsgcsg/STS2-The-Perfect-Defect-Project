# 当前：游戏内连续模型展示候选，原现场不变

用户要求真实模型下拉、持续展示、简单暂停/继续/结束与诚实恢复。D/T从
`origin/develop=93e90df`独占联合source候选，沿原Application/Environment/Runtime/
Evidence接线；范围、负责人、验收与停止点归
[本轮任务](../plans/p1-prefreeze-2026-10-18/INGAME_CONTINUOUS_MODEL_PLAY_20261023.zh-CN.md)。
普通本地源码检查不授安装/加载/实战或模型质量；原用户App/Host/游戏及训练保留。
Runtime40/Evidence34/Live UI18/Mod60/Workbench4必须匹配，旧安装不自动升级。
性能优化已延期；App重启丢失Host管道时只恢复准确Human/Stop/封存，不能冒称继续同世界。
原首批训练、最终TEST、40次Reference和完整R2交付仍未完成。

# 历史已延期：隔离性能候选，原训练继续

用户批准在原首批训练继续时完成性能候选、必要内部工具和真实 TRAIN 短实验，
新增独立 USD10 上限（按需花费），学校累计40小时不变。root 的独占分支
`codex/training-performance-switch-20261023` 基于 `origin/develop=58d445f5`；
范围、阶段验收及成本检查点归[本轮任务](../plans/p1-prefreeze-2026-10-18/TRAINING_PERFORMANCE_20261023.zh-CN.md)。
候选与原 f7 worker/31fa 控制应用隔离。用户须先看结果并进一步明确同意，才能
停止原训练、选定实际切换点或使用新版；开发和实验不构成现场切换许可。
真实TRAIN短测：row吞吐1.19倍，experimental-packed约9.13倍但改变FP32轨迹；
只授同轮更新窗口收益，不授总墙时/质量。正常新Run配置/保存恢复/模型2消费者正在
完成稳定候选复核；原f7跨源码完整迁移仍未实现/验证，不能直接在下一检查点切换。
性能工程在原recipe/model、Store和应用/Compute各负责层落实，生命周期/SOP归
[原训练指南](../../python/docs/FULLRUN_TRAINING.md#training-performance-belongs-to-the-existing-chain)，不新增控制器。

## 已集成：训练自动接续与首批训练连续性

用户已批准把有界自动接续用于当前首批四 Agent 训练。root 在 develop
`33de5875` 的独占 `codex/training-auto-continuation-20261023` 实现同一
Application/Ops/Scheduler 的持久接续，准确范围、验收及停止条件归
[本轮任务](../plans/p1-prefreeze-2026-10-18/TRAINING_CONTINUATION_20261023.zh-CN.md)。
已有 f7 worker/Run/输入/配方继续，控制应用候选不替换其 producer。
source/test、当前部署及实际自动跨段分别验收；目前不称自动接管完成。
原首批训练、统一 TEST、40次 Reference 与最终 R2 交付仍未完成。

## 历史：首批四 Agent 的 Modal 启动修复


root 在 `origin/develop=0519ec9f` 的独占 topic
`codex/four-agent-modal-wrapper-import-20261023` 修复本批真实云端启动失败。
唯一错误事实是序列化 `compute` 引用了完整 `ModalTarget`，导致注入解释器在
进入项目 venv 前导入不到 `stpd`；原调用、失败日志和取消记录保留，不重投。
本次 G6 修复将 timeout 同原 target ID 一样冻结为原始值，复用既有 wrapper/
worker/Store/Ops 链和原资源上限；模型、输入、配方、原 Run 均不改写。
准确 SDK pickle 的隔离冷解码反例、原部署资源/命令回归、非作者审查和选定
CI 通过后按本批已授权流程集成，再以新源码、镜像、target 和 Run 实测。
源码通过不授云端训练资格；首批完整训练、固定 TEST 和40次实战仍未完成。
操作和恢复继续归[原云端指南](../../python/deploy/cloud-worker/README.md)。

## 历史：四 Agent 首训的消费者与恢复候选

用户已另行批准首批随机、频次、线性和 M-std 的真实训练、同一冻结 TEST 比较、
正常 Model 下载注册及新十种子 × 四 Agent 的 Reference 评价；root 是本批数据、
预算、远端、游戏及集成唯一写入负责人。完整首训与全部40次评价仍未完成。
先前[录制与数据修复](../plans/p1-prefreeze-2026-10-18/DATA_RECORDING_REPAIR_20261022.zh-CN.md)
已交新版固定25来源投影；来源亲缘未知，不据观察组划分声称独立科学 holdout。

本候选在既有 Application/Store/Ops/worker/Model 链补齐 own-byte 下载、checkpoint
诊断导出、四结果显式 TEST 开封和40次 Reference 分母；合并已审查的学校 SSH
启动数值环境与观察锁修复、训练 SnapshotReader 复用、周期完整保存/超时前软暂停，
以及保持完整输入、同随机 draw 和参数结构的节点聚合优化。具体操作归原
[P1 训练](../../python/docs/P1_T0.md)、[工作台](../../python/docs/PROJECT_CONSOLE.md)
和[评价指南](../../python/docs/MANAGED_SUITE_EVALUATION.md)，本导航不另建控制或用途账。

原可复用工程轮的预算和终止状态保留在[原任务](../plans/p1-prefreeze-2026-10-18/REUSABLE_TRAINING_20261022.zh-CN.md)，
不能当本批新授权或新运行通过证据。本候选 source/test 复核与完整 CI 必须绑定 live PR216
准确头；子 PR 的绿色不替代组合门。真实原 source 校准、数值恢复、冻结新组合、
正式训练、R2冷读回、CPU消费、原生游戏和质量均按各自准确对象保留收据，不能互授资格。
周期保存与软暂停不保证硬超时前一定保存成功；unknown 原请求不重投。

## 历史：近期未合并工作的组合审查与 develop 集成

Resolve live GitHub refs before relying on this snapshot. 最新用户原话和现场证据 override this file。

本批唯一收口原件是
[本批审查与集成](../plans/p1-prefreeze-2026-10-18/REVIEWED_INTEGRATION_20261022.zh-CN.md)，
root 是唯一集成写入者；基线 `origin/develop=1e5d83100a8222f211a0e633120ea8123dd06427`。
在独立 topic 组合 PR211、PR212、PR213，先核历史职责与实际连接，解决组合清单/入口冲突，
组合源码与定向检查已收齐；最终 head、完整 CI 与是否已合入，以本批 PR 的实际回执为准。
单个原分支的绿色不代替组合检查，合入后本轮即结束，不自动启动下一项任务。

原交付分开保留：[录制优化](../plans/p1-prefreeze-2026-10-18/RECORDING_ROUND2_20261022.zh-CN.md)
是读取热点及诊断改善；用户已接受剩余整体卡顿为已知表现，不宣称整体帧尾已修。
[可复用训练](../plans/p1-prefreeze-2026-10-18/REUSABLE_TRAINING_20261022.zh-CN.md)
提供同 Application/Store/Curation/Run/worker/Model 链与三种 baseline；四 Agent 正式长训和统一评估仍未完成。
PR213 在同一 SDK 核实现获批的同奖励 LastingCandy 2→0 显示比较；原件、模型输入、严格差异保留，
原生 Skip 不造永久 decline，不补造缺失 next/end；该源码尚未日常安装。

日常 rc58 的精确安装/冷载回执归原录制任务；本批集成不重新部署、运行游戏或发起付费任务。
PR212 的真实 CUDA 只证明其原 source 的 step1/DEV/checkpoint；同 Run 恢复请求未执行，
保留 cancelling/result_unavailable，不能称 GPU 恢复、Model 或性能通过。
旧五小时结果由[原任务](../plans/p1-prefreeze-2026-10-18/FIVEH_EXECUTION_20261022.zh-CN.md)恢复，
不借旧 rc56/57/空间快照给新组合资格，不复活已结束派工或预算。

P1 可替换协议、Host/Host Runtime 区分、来源与 profile requires、GUI/CLI 映射同一用例，
沿 README → Architecture/Components/唯一链路图 → 原操作指南维护，不新增一套规范或控制链。
旧 PR24/25/172/178/181 的保留、替代和未迁能力归本批任务；未授权删除或关闭它们。

## Remaining Platform non-claims

Managed 全机制等价、完整 Human/研究资格、模型质量、正式训练、统一 TEST 与实战评价仍未授通过。
本批门序：组合检查与非作者复核 → 固定候选的 full CI → 条件式 develop 合并及集成身份核验。
源码复核及定向消费者检查已通过；最终执行结果和合入身份归本批 PR，不由此静态导航推断。
