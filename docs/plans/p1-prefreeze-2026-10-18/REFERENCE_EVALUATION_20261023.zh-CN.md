# Reference 小种子组：同一应用入口

本轮为原四 Agent 训练/消费任务补齐正常可用的小种子组诊断。负责人是
`/root/evaluation_workflow`；主管 `/root` 负责集成、预算和最终验收。独占分支
`codex/reference-evaluation-workflow-20261023` 基于未合入性能候选
`1fadf3f2aa4e4c33dc46293eab2ecb7b0f4d933d`（PR226依赖），不修改原 f7 worker、
现场控制应用、当前训练、训练配方或数值实现。仅 source/test；真实游戏、R2写入、
安装/重启、PR/push/merge由主管另行协调授权与准确对象。

链路位置是现有⑩ Model→完整Agent→评估/新原件；变更类别为表示或传输：既有评估
用例的 GUI/CLI/API 映射及 Application 生命周期。G2覆盖新 API/组控制和跨 owner
admission；不改变 P1、游戏合法性、模型算法、Host合同或最终40项评测协议。
下游需要一个可开始、读状态、局间暂停/继续和停止的少量新种子组；上游继续提供原
readiness、AgentComposition/原请求、原 Actor/Host finish、密封 evaluation 和 Store peer
保存/回读。操作记录仅导航当前组；`attempt/report.json` 是唯一证据报告。

## 实际已有与本轮缺口

历史上已有 `reference_suite.run_suite`，并非从零缺评估：它严格读取旧 ref-20或固定
四Agent40分母，串行经过 prepare→生成原request→run→exact finish→seal→peer回读。
已有 GUI 也已提供 Agent 选择、ready/load、Auto/Human/Stop。原 Host/native owner
支持无窗口/显示游戏窗口及正常/快速/即时。本轮复用这些接口；缺的是少量日常诊断
seed组、Application持有的组生命周期和对应薄入口，不新建控制器或generic scheduler。

Managed并非整体不可用：已有独立 Host/匹配Model单局控制、环境与场景页面及 API；
另有 `evaluate_managed_suite.py` 的 catalog-only random Agent批量环境检查。后者用
Managed reset/observe/step/close与raw-grammar outcome，不消费用户所选Model，也不
提供 Reference 的AgentRun/密封保存同一契约。本轮不把两者拼成未经实现的统一组入口，
不要求先完成已延后的全机制Host等价。正常操作归
[PROJECT_CONSOLE](../../../python/docs/PROJECT_CONSOLE.md#小种子组实战检查reference)。

原证据保存 owner 的已实现范围仅为 native P1 baseline/M-std Model：
`stopped_agent_evidence_context`核原Model、请求与运行，再由原import owner保存并回读。
因此新组只接受这些真实能闭合的selection；Teacher/其它旧Agent保留原单局入口。
这是当前消费者能力边界，不是Model/Host的新业务分类。

## 交付与验收

- `diagnostic_reference` 1.0.0 manifest：1–10局，准确游戏版本、组ID、确定性seed与hash；
  排除全部原保留评测seed，全部分母先于世界请求。最终四Agent40项及旧ref-20不改。
- 同 Application `ReferenceEvaluationService` 调用原suite及Model/native/import owner。
  原models lock与不透明scope reservation挡冲突写操作；实际原Model异步线程继承该scope，
  不靠threadname、PID扫描或第二控制权登记。状态/readiness/原报告仍可读。
- pause只在局已finish、seal并peer回读后生效；resume仅在同应用活着时派下一个未派发
  seed。Stop使用原组ref/原Actor收尾，不追随“最新Actor”。暂停仍计整组上限。
  App关闭先请求组停止，再沿原native/Model关闭；不持Model锁join。
- 保存未知、cleanup未知、原delivery未知或进程丢失时保留原请求/报告与全部分母；
  不自动重发、重播或跨进程继续。恢复先读原owner status/原reconcile；显式group reconcile
  仅核准确原group/report SHA/ref/run/peer、原request/evaluation、cleanup与full typed readback，
  写独立观察引用并退休占用。不同ref/hash、原仍活跃或仅有ACK拒绝；旧report/unknown不改。
- GUI、`evaluate_reference_group.py`与`/api/reference-evaluations/*`映射同一组owner；
  Model页旧单局控件在组持有期间禁用。组不需要用户预加载Actor，存储复用已配置compute
  Store peer或显式原binding，不另定R2配置或保存链。

最低检查是原suite反例、diagnostic producer/hash/config、真实Application HTTP/CLI组控制
与Model互斥、真实Runtime+synthetic World组路径、已有Store原件/peer回读反例和GUI行为。
合成World及单独替换的保存接缝明确披露；它们不证明真实Model/原生游戏/R2现场或质量。
稳定候选由主管安排非作者复核，再决定集成和另行真实Model canary。源码交付后停止本项，
不启动最终40次评估、Managed扩展或训练/模型优化。

## 当前验证记录

原suite 36项、diagnostic manifest/config 4项通过；组Application/API/CLI/恢复反例通过；
原Store typed readback/传输/unknown 9项、console JS 333项通过。
精确最终候选、实际命令与完整结果以本分支交付收据为准，不由静态本页补授资格。
真实Runtime测试曾遇新工作树未构建以及15527现有服务占用，分别保留为测试准备和环境
前提失败；未停或抢原服务。生成当前工作树SDK/Runtime后，既有HTTP/Runtime用例通过；
新增组pause/resume/互斥/reconcile在主管协调释放窗口后通过真实Runtime+synthetic World
检查；该fixture的Model eligibility与peer保存transport显式替换，不授真实Model/R2运行证明。
测试未消耗最终保留seed或真实游戏局数。
