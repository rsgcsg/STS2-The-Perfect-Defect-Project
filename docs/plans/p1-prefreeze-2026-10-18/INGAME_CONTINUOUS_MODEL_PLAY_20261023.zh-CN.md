# 游戏内连续模型展示：联合源码候选

当前用户要求是游戏内真实模型下拉、一键持续展示、简单暂停/继续/结束与诚实恢复，
用于会玩的操作者观察模型，不能用大有限数字或后台重授伪装连续。
主管 `/root` 负责范围、训练/费用、原现场和最终验收；D `/root/evaluation_workflow`
独占 Application/native UI/组合pins，T `/root/training_state_derivation` 独占
原 Runtime/Evidence。基线 `origin/develop=93e90df1299fa657ceb9a5a13389e64915914859`，
D topic `codex/ingame-continuous-model-play-20261023`；T稳定topic通过普通merge集成，
保留组件source_revision。source硬止14:20UTC，含最后15分钟收尾。

链路位置为原⑩ Model→完整Agent→同一Environment/Runtime→原报告，属于已授权的
能力/交互语义及其表示映射。联合变更主类G3（游戏内C#），包含G2版本合同及跨owner
消费者：Runtime40、Evidence34、Live UI18、Game Mod60、Workbench4。
GUI/CLI/API继续映射原Application；RuntimeLifecycleOwner仍唯一掌握授权、计数、
取消与释放。模型/InputSpec/weights、游戏合法性、训练方法和最终40分母均不改变。

下游需要真实continuous而非扩大short/extended；上游提供显式mode配置、两profile
startup/status/event-2及typed sealed原件。continuous只含mode，四cap/remaining为null，
计数与elapsed真实。新消费者显式支持历史数字v1与v2，不能把null/缺字段推成无限。
同活App暂停/继续保原run。cleanStop后原Environment先重新核typed Stop/释放/原请求，
再核原Host identity的runtime/profile/seed，保留immutable旧Actor/request/report后
才创建新Actor；不新世界、不重投unknown。相同seed的手动重开仍不等于已证明同native run。
App进程重启可恢复精确原Runtime Human/Stop/封存；丢失Hoststdio不冒充同world继续。

原用户App/Host/游戏、训练/GPU、Store/Ops/Curation、TEST及费用不在本轮写入权内。
性能工作延期。普通本地build/tests允许；PR/hosted CI、发布/安装/加载/现场运行由
root亲核源码与非作者审查后另授。已知固定bridge15528和原Mod lifecycle全STS2进程门
不能支持与旧用户游戏同时冷载新版，不由本任务绕开。

验收：生产者向量→App startup/status和原请求echo；native readyRef+pair分别绑定到
原Application；typed Stop/immutable退休/同Reference新Actor及失败保持fence；原Host
identity变化/缺失拒绝；私有bootstrap仅入准确ownedprofile；真实catalog及简单按钮；
相关Python/C#/JS、准确本地Mod编译、组件与根repository/closeout门、非作者复核。
完整所选hosted full仍待root授权，不能降scope或把跳过计为通过。
所有失败分母、源码/包/安装/加载/真实游戏/质量资格分别报告。

正常使用、条件和恢复唯一归[原模型任务指南](../../../python/docs/LOCAL_MODEL_TASK_FLOW.md#continuous-display-and-changing-model-in-the-same-owned-world)
与[PROJECT_CONSOLE](../../../python/docs/PROJECT_CONSOLE.md)。回滚选回匹配的
Runtime/Evidence/App/Mod组合，保留原件与未知；原运行窗口当前未被本任务替换。
