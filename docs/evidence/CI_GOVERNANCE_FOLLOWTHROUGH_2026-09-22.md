# CI、治理与 Stage 1a：核验快照及执行分工

日期：2026-09-22。本文是审查记录与有界实施安排，不是 CI 改造验收、已派发任务或新的规则权威。
规则归属仍为 [TESTING](../TESTING.md)、[开发流程](../DEVELOPMENT_WORKFLOW.md)、
[工程治理](../ENGINEERING_GOVERNANCE.md)、[项目系统](../PROJECT_SYSTEM.md) 和
[AI 协作](../AI_COLLABORATION.md)。实现顺序沿用 [Stage 1a 原任务表](../plans/STAGE1A_TASKS.zh-CN.md)。

## 1. 精确核验范围

| 对象 | 本次读取的身份与边界 |
|---|---|
| develop | `d5785d215087719189a3a6bada9addb34b7d95c5` |
| 文档 PR #27 的修改起点 | `docs/stage1a-product-ai-protocol-20260919@e3df2fba5330dd50814919b54252e538f67f92d8`；未合入 develop |
| Stage 1a / PR #31 | `feat/stage1a-local-models@5fe9bef90c9e2b65563c17a5fee04a19ddad8508`；累计范围待独立接受 |
| Runtime / PR #32 | `fix/stage1a-runtime-recovery@1e35e1d54554e9b00685aca709b1f39644fc5688`；Draft/open |
| 已接受增量 PR #29 | 正常合入 Stage 1a 的提交为 `5fe9bef90c9e2b65563c17a5fee04a19ddad8508`；不是整条 Stage 1a 验收 |
| 已执行 CI | [35605794041](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/actions/runs/35605794041)，attempt 1，full，success |
| 回执实际 checkout | `482a688290dfce0d685e3bc8cc6fd346963745cb` |
| 回执 tree / workflow blob | `060156826453fc6a9fe43ddfb9ca97f44a5b831d` / `26be7843a750d11925eb33ff145250cbeae1394a` |

回执中的 plan/Linux/Windows 为 success，docs 未选且 skipped。它只证明该身份的执行；
本报告、文档新提交或未来 fixture 不继承它的绿色。不得为获得新绿色重跑旧候选碰运气。
本轮重新读取 PR/refs/patch 与 CI 状态，并解析已取得的原始 JUnit ZIP/执行回执；
没有重新执行组件/full suite、访问用户电脑或观察生产运行。

## 2. PR #32：限定收口，不重做生产代码

[最终候选测试](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/blob/1e35e1d54554e9b00685aca709b1f39644fc5688/components/policy-runtime/test/policy-runtime.test.ts)
中的 F2 在第二请求 pending 期间先写取消请求的晚响应，再写新请求不同的 digest/count/scores；
未知 request ID 另有拒绝测试。原覆盖缺口已在源码中关闭，不为本次 F1 重写 F2。
Luna 的历史 mutation probe 仍是其报告的证据，不宣称本轮重新执行。

F1 已先用真实 Auto tick 持有 controller，再启动未完成推理。但是 `releaseEntered.resolve()`
位于设置 `releaseGate` 的立即执行 async 函数内，早于 Stop；等待该 promise 不证明 release 已进入。
现有 FakeConnector 的 `releaseController()` 在等待 gate 前递增 `releaseCount`。
最小修正可直接安装未释放的 barrier，并在断开前有界等待该实际调用计数；也可在实际 release 方法内
发 entered 信号。不得新增生产 API 或通过定时 sleep 猜顺序。

保留断开前后 running/held、response 未 finish、cleanup 为零，释放后 stopped/released、
cleanup 恰好一次和不可变证据封存；正常响应分支仍等待真实响应完成。
仅改测试与必要 BOM 身份，使用原 PR #32，一次完成关联诊断、最终身份、检查和候选推送。
新候选需独立审查与自己的适用 CI，不允许自动合并未审修改。

## 3. 已有测量和 CI 改进边界

| 项目 | Linux | Windows |
|---|---:|---:|
| GitHub job 全程 | 5m51s | 31m29s |
| 选定 source/test gate | 4m58s | 28m55s |
| 锁定 Python 环境安装 | 23s | 34s |
| 根 npm ci | 3s | 5s |
| JUnit suite time | 168.347s | 1292.301s |
| JUnit failures / errors / skipped | 0 / 0 / 2 | 0 / 0 / 40 |
| test_token_comparison 模块 testcase-time 合计 | 3.797s | 114.657s |

Job 来源为上述 run 的 job 记录；JUnit 来自 artifact `pytest-Linux-1` / `10641744971`、
`pytest-Windows-1` / `10643320807`。ZIP SHA256 分别为：

- Linux：`43b3309a93d6acc71823ca7a5d4fb3c22aef476106c410c4377c4bd48a510660`。
- Windows：`06747ea0d37689463467ffd04b82506c44a9ac4c9ab817802c5db3d7ce7aaaca`。

执行回执 artifact 为 `portable-execution-receipt-1` / `10643345787`。
模块时间包含 fixture 准备/清理；JUnit 子测试计数不能与框架摘要简单相加。
这些数据定位了 Windows Python 长尾，但不证明杀毒、磁盘或 SQLite 是根因，不证明长期单调变慢。
尚无任何提速百分比或新的平台兼容性验收。

[Issue #33](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/issues/33) 是唯一 CI 改进待办。
首个实现 PR 只处理已测得的一个 fixture 家族；`test_token_comparison.py` 每个测试重复生成两个
一步合成训练结果，可先区分 setup/call/teardown 后消除不必要的重复。保留真实生产链样例、全部
损坏/错 cohort 负例和各测试独立的可变 store；不共享可变单例、不关闭被测持久化、不减覆盖。
该文件目前在未合并 Stage 1a 上，不能假装 develop 已有；默认等相关源码接受并集成后从最新
origin/develop 开独立 topic。提前使用依赖分支需另给明确基线/owner，不由待办暗中授权。

廉价检查前置、Platform/Python 双系统拆分属于后续独立调度 PR，不混进 Runtime 或 fixture 优化。
只有确有必要才拆；router、aggregate、receipt 及其反例测试须一起维护。所有被选叶子仍必需，
缺失/失败/取消/意外跳过必须使 portable 失败，保留双系统身份/EOL验证和未知变化 full fallback。
先不增加逐测试影响分析、无限并行、监控服务或第二套任务系统。

## 4. 实施分工与 PR 次序

| 工作 | 放在哪里 | 完成边界 |
|---|---|---|
| 规则入口、当前状态、此次核验记录 | 架构师维护现有 [PR #27](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/27) | 独立审查＋最新门禁＋合入 develop 后才成为默认仓库入口 |
| F1 时序与最终身份 | Luna 继续 [PR #32](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/32) | 当前候选 source/test/package 范围接受；不等于安装/原生通过 |
| Stage 1a 累计源码 | 继续 [PR #31](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/31) | 审余下累计范围，接入已接受 #32 后核对最终组合，正常合入 develop |
| CI 提速 | Issue #33；实际实现时新 topic/PR | 可比较的耗时、覆盖/隔离和适用门禁；不是只交建议 |
| 包/消费者、原生恢复、游戏内产品 | 原 1a 任务；每个可独立交付使用新 topic/PR | 精确需要的安装/原生/产品证据，不借旧产物资格 |

文档与产品源分支可以分别审查；CI 改进不是全体 1a 的前置。只有一个实现者时先结束活跃 Runtime
任务，再按实际空档安排一个 CI 包；有第二个真实可用且无重叠的 writer 才并行实现。BOM、UI生命周期
和根 workflow/router 各有唯一写入 owner。不得虚构 worker 已派发、持续后台审查或 CI 监控。

接入顺序不是把四个 PR 一起勾成成功：#32 接受后正常合入其 Stage 1a base；#31 对新的累计 head
补充影响审查；#27 可独立收敛，但 develop 移动后，依赖它的候选须按现有流程核对/合入新 base，
重新确认实际 tested tree 与身份。凡需要重新执行，由 TESTING 的现行选择/回执机制决定。
普通任务到 develop 集成为止，不顺手创建 release/main 晋级，不给 main/develop 直推或跳过审查。

随后优先把 rc.7 候选包、消费者 pin 和明确授权的最短恢复 gate 接通；已有 rc.6 消费/UI 和旧四格
试验不重做。1a-03 场景矩阵与 1a-04/05 facade/宿主验证可在明确依赖后有限并行，先交付 1a-06
游戏内选模型→加载接管→暂停/结束→对应报告，再扩展原任务表中的采集、数据、训练和分析链路。
任何安装、启动游戏、付费、训练、Steam 或云端修改仍需要相应窄任务明确授权。

## 5. 本批怎么使用规则，而不是再造治理

本地/同任务 Draft 中可以先有失败回归并完成关联修复；不得隐藏失败、堆无关功能，或把红候选
当作已接受基线/合入 develop。集成线若失败，按工程治理先回退或有界 fix-forward。
执行者在已授权任务内完成实现、回归、直接关联诊断、BOM与PR元数据，不为每个正常子步骤往返问询。

沿用五分钟被动等待检查点及有条件的十分钟上限；这不是编码时限或杀进程指令。已知长 CI 获得
真实 run/monitor 后即交接，不循环轮询、不在本机并跑相同 full。任务卡明确允许的操作不因交接
被取消，也不因 CI 通过而扩展成下一批操作。TESTING 仍是检查选择唯一权威。

阻塞意见须对应原验收条件、具体路径/后果和最小修复；无当前证据的架构猜测、风格偏好和可选
完善另列非阻塞，不持续给既有任务追加交付要求。新重大安全/正确性事实仍可阻塞，但须说明证据。
已有精确覆盖不为形式增加重复测试；发生行为/合同变化、逃逸缺陷或不安全重构时，按治理补忠实回归。

新规则先进入唯一 canonical owner，再维护入口、适用测试/检查和独立审查；合入后新任务显式读
新 ref，旧会话重新读取。路径/索引门禁不能证明人或 AI 读懂规则，聊天等待也不能靠 CI 假装强制。
此次未改变分支保护、CI选择器或执行器权限；它们如需增强，应各走实际受影响的窄任务。

附件 software-engineering-architect Skill 暂为 reference-only，不原样导入或列为必用。
重复经验先进入最低成本的既有代码/测试/CI/文档 owner；稳定、重复且非显然的新流程再按现有
Skill 准入处理，不为当前 SHA/PR 创建 Skill。此次没有安装任何新 Skill。

## 6. 本次文档交付的限制与回滚

原对话审查 Markdown 只是输入材料；这里保留可公开核验的快照、结论与任务链接，不复制原始日志、
私有工作区、模型权重或完整 JUnit 到 Git，也不要求执行者能访问对话附件或本机 sandbox 路径。
README/CURRENT 提供入口，固定事实留本报告，变化的实施状态看关联 PR/Issue 和实际回执。

文档候选经 GitHub API 原子提交并读回差异；本环境 Git clone 因 DNS 失败，没有完整 checkout，
因此不声称执行了 project:check、project:closeout、check:plan 或全仓 git diff --check。
最新提交的 hosted CI 及独立文档审查仍需完成，状态在 PR #27 更新；不能复用 #32 的测试通过。
回滚只撤销这次文档修改，不移动旧 tag、不改变生产组合、原始数据或历史失败记录。
