# G1-RC1 设计审计与外部依据

日期：2026-10-07。对象：[G1总册](../design/BASELINE_G1_REVIEW_PACKET.zh-CN.md)、[合同](../design/BASELINE_G1_CONTRACTS.zh-CN.md)、[路线](../design/BASELINE_G1_EXECUTION_ROADMAP.zh-CN.md)及纳入详细材料。审计起点`04d4b3d4836fa029771edb99e1e08528f6e70429`；develop核对为`9556d21188b2deea027192567827a339a0ce50f7`。本文不是runtime qualification或G1批准书。

## 1. 方法、历史和范围

重新读取本项目当前对话的相关连续用户请求、现行AGENTS/README/CURRENT/Architecture/Components/治理/Testing/Workflow，以及P0–P5、A总旅程、蓝图、capture/queue/learning、Stage1a产品/团队/环境计划。只读取相关项目历史，没有扫描/导出其他私有对话。用户原意保留：三条链联合、完整封装Agent、案例可推翻协议、原生记录可声明假设转换、动态H不是完整稳定GUI、完整合法目录不等于模型必须全评分、G1必须用户理解后批准。

执行了fetch/prune与PR/ref核对；现有产品e302与恢复762907保持各自身份，不合并、不修改其工作树。读取相关源函数/测试和原生源审查记录；本轮没有新增native执行、Human数据处理、游戏/服务探测、训练、安装、云调用或部署。旧CI/历史Human资料仅保留原范围，不转移给本候选。

三个独立只读领域审计分别检查环境/记录、学习/研究和执行/产品；root对关键引用重新核对，并将发现写成可实施合同和分阶段验收。之后另做完成候选的独立整体审查。审查者输出不是自动事实，最终以源码/文档diff/实际检查及精确产物为依据。

## 2. 发现与处理

| ID/级别 | 原缺口或易误读点 | 本轮处理 | 后续证据 |
| --- | --- | --- | --- |
| GA01/P1 | success capture ordinal连续不能证明没有失败捕获；Snapshot.Sequence也非事件cursor | C04独立publication与capture attempt/failure、曝光和落盘位置 | E1/E2 gap/失败/乱append回归，G2/V1捕获 |
| GA02/P1 | old Runtime等待stableSuccessor；结果没稳定时返回unknown，不适合作A双维状态 | C07固定delivery与outcome/proof分开，旧端口需版本化适配 | E1/E3已知投递/未知效果/child及恢复 |
| GA03/P1 | Human text五verb不够新A曝光历史，不能邻接canonical补齐 | C09新CaptureSpec与T/R/H/D及独立mask | E2实采/资格，非本轮编造样本数 |
| GA04/P1 | 旧GD表只列待定，没有首个可实施配方/状态消费 | C10选择LightAction内核AG01、scratch K1 N及固定Timing、renderer/consume/TBPTT | E3/E4数值与训用一致，G2真实链 |
| GA05/P2 | “无N事件trainer待实现”容易抹掉已有DSimple能力 | 明确DSimple已有advance/burn-in/TBPTT；缺新A及LightAction接缝 | E0复用审查、E4适配回归 |
| GA06/P1 | 进程内request缓存被想象成跨重启恰好一次 | C07有限retention、fingerprint、跨进程核对缺失时保留未知 | E1故障注入，V1实际恢复 |
| GA07/P1 | 旧四模型全场景与默认模型产品路线冲突，1a研究易被无声推后 | D13/路线对账明确替代建议；保留约10k和各对照义务，用户需批准 | E4/V1研究门及R1产品，不能只G3就宣布全部1a完成 |
| GA08/P1 | 科学实验完全排在G3后会过晚暴露数据/学习问题 | G2后限定范围pilot与V1并行，G3后稳定规模研究 | 独立授权、匹配配置与范围报告 |
| GA09/P1 | 共享访问/归档不等于全派生撤销或离线回收 | C13按原件/view/job/checkpoint/model/report及离线许可逐项定义 | E5 lineage/use/断网/取消/发布验收 |
| GA10/P2 | provider结束、result验证、可运行产物与任务UI混在一起 | C12/C13 typed用例、共享层task seam、validator与journal分责 | E4–E6失败/半产物/重复意图 |
| GA11/P1 | 稳定接入、主要流程自主和策略强容易一个gate代替 | 路线分G2/V1/G3/研究门；场景setup与from-start区分 | 机制矩阵＋固定Agent连续attempt＋独立实验 |
| GA12/P2 | 指标无阈值会空泛，编毫秒数又会冒充实测 | 有限工程样本、沿用已建议Stop目标、pilot→冻结→确认；语义零容忍单列 | G2/V1实测，未测保持未证 |
| GA13/P2 | C07一句等待旧请求结束与允许known-pending再次输入冲突 | 区分真正新策略选择与旧请求补偿重试；已知delivery不等outcome结束 | E1/E3 pending连续输入/unknown阻断 |
| GA14/P2 | C08初稿要求非空trigger，与已纳入综合方案pure timer冲突 | 允许空trigger加有限deadline；非空集合可提前wake | Await纯timer/过期deadline回归 |
| GA15/P2 | 路线图把可选pilot画成V1硬依赖 | 改为可选诊断反馈虚线；科学结果非G3前置 | 阶段依赖审查 |

上述为设计层已处理的发现，不代表相关source defect已修。没有修改生产代码来让本轮审计看起来通过。

## 3. 直接源码核对入口

以下均固定在审计基点。表中“存在”只指source。相关测试仅阅读，未在本轮执行Torch/native/runtime suites。

| 源证据 | 直接事实及设计影响 |
| --- | --- |
| [ProcessLocalTextMenuWitness](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/04d4b3d4836fa029771edb99e1e08528f6e70429/components/connector/host/PlayerEnvironment/Witness/ProcessLocalTextMenuWitness.cs#L31) | failed freeze不消耗ordinal，Snapshot.Sequence是revision；不能证明无漏采 |
| [TextMenuV2Executor](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/04d4b3d4836fa029771edb99e1e08528f6e70429/components/connector/host/PlayerEnvironment/TextMenu/TextMenuV2Executor.cs#L49) | request/fingerprint缓存、当前snapshot复验；只证当前进程机制 |
| [Runtime stable successor](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/04d4b3d4836fa029771edb99e1e08528f6e70429/components/policy-runtime/src/runtime.ts#L619) | 已知投递后等待stable successor；新A不原样继承这套总状态 |
| [Human输入白名单](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/04d4b3d4836fa029771edb99e1e08528f6e70429/components/annotator/src/STS2HumanAnnotator.Core/HumanTextInputObservation.cs#L28) | 五verb及partial流，新capture不能只改名字 |
| [LightAction advance/score](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/04d4b3d4836fa029771edb99e1e08528f6e70429/python/stpd/models/light_action_m2.py#L153) | caller-qualified prior/feedback、共享writer与候选只读，可复用内核 |
| [DSimple序列训练](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/04d4b3d4836fa029771edb99e1e08528f6e70429/python/stpd/models/dsimple_sequence_training.py#L187) | burn-in、无N advance；300后episode TBPTT说明carry近似 |
| [LightAction旧trainer](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/04d4b3d4836fa029771edb99e1e08528f6e70429/python/stpd/models/light_action_m2_training.py#L141) | 每step N、feedback=None，新A需适配 |
| [来源分组](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/04d4b3d4836fa029771edb99e1e08528f6e70429/python/stpd/data/splits.py#L25) | deterministic root/salt可复用；新来源闭包定义仍须业务合同 |
| [compute seam](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/04d4b3d4836fa029771edb99e1e08528f6e70429/python/spireagent/compute.py#L1) | provider-neutral接口仍引用stpd request/receipt；通用task合同应下沉共享层后注册research validator |
| [scheduler](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/04d4b3d4836fa029771edb99e1e08528f6e70429/python/spireagent/hub/scheduler.py#L129) | 持久intent、unknown不重发；不是通用多Host调度 |
| [trusted model registration](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/04d4b3d4836fa029771edb99e1e08528f6e70429/python/spireagent/workbench/local_models.py#L378) | 本地注册只选可信adapter，下载manifest不能指定执行程序 |
| [exports](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/04d4b3d4836fa029771edb99e1e08528f6e70429/python/spireagent/hub/exports.py#L164) | inventory/payload重核访问，不证明派生/离线统一撤回 |

原生当前公开holder与logical Hand目录、H捕获seam、同步append热路径等进一步依据保留在已纳入的queue/capture/learning审查；没有重新把旧源码检查当新live证据。产品候选和recovery候选按P1 source map择项复核，不以整树相同/CI绿代替接口审查。

## 4. 外部规范、实现和研究：借鉴什么、不照搬什么

本轮实际读取下列一手页面；网页内容可更新，表中记录的是审查时用途，不声明永远最新。未安装这些框架或新增依赖。外部项目性能不可转移为STS2成绩。

| 外部依据 | 对当前设计的具体帮助 | 不采用的推论/部署 |
| --- | --- | --- |
| [CloudEvents v1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md) | 事件身份与来源配对、重传去重和格式/传输分开，启发C04 | 它不是本项目game causality、顺序/完整capture或exactly-once证明；不必引入broker |
| [OpenTelemetry Logs Data Model](https://opentelemetry.io/docs/specs/otel/logs/data-model/) | occurrence与collection时间分开，支持本项目不混capture/publish/exposure/append | 诊断trace id不是Human因果parent；其时间戳不替代monotonic Await clock |
| [W3C PROV-DM](https://www.w3.org/TR/prov-dm/) | entity/activity/agent与derivation帮助组织来源责任；支持产物/作业分开 | lineage图本身不证明科学标签或因果，不能因同作业使用就任意认定派生 |
| [RLlib env-to-module pipeline](https://docs.ray.io/en/latest/rllib/env-to-module-connector.html) | 明确环境到模块的转换接缝；对应ProtocolTrace/AgentInputSpec及训用一致性 | 不直接照搬默认wrapper、fixed step或接受其隐藏自动变换；不安装Ray作为G2前置 |
| [Sample Factory同步/异步实现说明](https://www.samplefactory.dev/07-advanced-topics/sync-async/) | 明确吞吐与policy lag/sample efficiency的取舍；支持先同步reference再独立比较异步 | 不因异步名字更先进就默认大规模上；其环境表现非本项目结论 |
| [Ray Tune concepts](https://docs.ray.io/en/latest/tune/key-concepts.html) | trial、scheduler、checkpoint分责用于设计实验执行服务 | 不把早停开发筛选与等预算最终比较混同；现有store/journal无需先换掉 |
| [Kubernetes Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/) | 官方提示job配置仍可能重复启动程序，terminal/cancel需要实际终止事实；支持应用intent/fence/validator | 不部署Kubernetes，不把provider单任务配置当恰好一次执行保证 |
| [rliable实现](https://github.com/google-research/rliable)、[评价论文](https://arxiv.org/abs/2108.13264) | 以不确定性和对照方法代替单次点估计；用于R2统计方案 | 仓库页面显示2025-10-15已归档；仅方法/实现参照，不承诺维护；不强套跨任务IQM |
| [Gymnasium Env](https://gymnasium.farama.org/v1.0.0/api/env/) | termination/truncation分开是RL数据必要语义之一 | 不把异步A直接当普通step MDP；game/combat/task/unknown还须独立 |
| [DAgger](https://arxiv.org/abs/1011.0686)、[CQL](https://arxiv.org/abs/2006.04779)、[Decision Transformer](https://arxiv.org/abs/2106.01345) | 支持示范到闭环分布偏移、离线支持范围、回报条件生成三种消费者设计 | 不声称采用这些算法、已有对应数据或任何最优保证；详见AJ05–08 |

方法性结论转化为本项目的合同/反例，才有工程价值；“参考了很多框架”不是设计验收指标。当前最少复用组合仍是本项目现有组件＋A接缝，外部大框架待规模瓶颈出现后再做有界适配评估。

## 5. 候选验证与独立审查记录

本节记录最终候选实际结果；G1用户批准始终另列。当前新增文档是设计/源码审计，未新增生产测试；旧25项Agent合成reference及38项历史probe保持其原身份/限制，不能记作本轮完成native测试。

- 领域独立审查：环境/捕获、学习/研究、执行/产品各完成只读源码检查；发现见GA01–12，root已追查关键源并写入C合同/路线。
- 最终整体审查：独立合同review返回`READY_FOR_BOUNDED_DESIGN_SUBMISSION`，独立路线/产品/研究review返回bounded PASS；两者均在GA13–15修订后复核，无未处理的P1/P2设计finding。没有沿用上一轮A旅程PASS，用户G1仍未批准。
- 文档/治理检查17 fixtures及0 warnings通过；首次CURRENT缺三条固定安全短语被门禁拒绝，已恢复原要求后重跑通过。manifest覆盖/hash、64操作、15合同、16决定、21机制卡、14消费者、19任务ID及12个固定源锚点通过结构检查；新增案例不是生产测试。最终内容变更后重验manifest。
- 本轮真实游戏/Human/数值训练/云/部署/性能/科学实验：均未执行。

两位最终reviewer和root一致核对的三主文件SHA256如下。审计回执填写与manifest重生属于行政封存，不改变这三份被审内容。完整21文件content-set digest见manifest；manifest文件自身SHA与content-set digest是两个不同值，不混用。

```text
BASELINE_G1_REVIEW_PACKET.zh-CN.md
17bc15598491a214ce9c924f19d34a0966f09333def2b0d39675a959e3275ad3
BASELINE_G1_CONTRACTS.zh-CN.md
80a3f9d6b5ab09e8ab7d55b6745b14e9f4c6e79bc380629c6c9cedad136a1730
BASELINE_G1_EXECUTION_ROADMAP.zh-CN.md
a2a0576673dd0d8e102cc27b155b1668f6da5a9b6e528e8acf27a7761a1967ef
```

## 6. 剩余不确定性属于哪一层

| 未知 | 为何不伪称解决 | owner/gate |
| --- | --- | --- |
| 新A捕获覆盖和旧原件可用量 | 没有读取/迁移新完整语料；五verb已知限制 | E2/G2/V1 |
| Frame实际一致性、短暂预览、Recorder成本 | source保证有限；热路径需修改和实测 | E1/E2/G2/V1 |
| 新LightAction序列/状态与真实推理预算 | 内核存在不证明新组合数值/性能 | E3/E4/G2/V1 |
| reference Agent完整主要流程的自主性 | 不能从接口全覆盖推导策略不循环/不会早死 | V1默认Agent子门/G3 |
| 多Host/远端作业、撤销传播、产品无终端旅程 | 单段source存在，端到端产品尚未验证 | E5/E6/V1/R1 |
| 约10k合格来源与各研究比较 | 既有目标保留，库存/预算/标签不预设 | E4/V1研究义务/R2 |
| “最强”与稳定优势 | 比较域/数据/基线与实验共同决定，无必然性 | R2持续评价 |

这些未知均有设计响应与后续证据入口。若实现表明当前合同不可行，则回到G1变更记录修改相应决定，不把难题藏在自动fallback里。用户批准前G1为NOT_ACCEPTED。
