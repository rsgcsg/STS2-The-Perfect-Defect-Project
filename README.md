# SpireAgent：先从这里看

目标是做出最强、能真实使用的《杀戮尖塔2》Agent，靠采集、数据管理、训练、接入、评估和迭代持续提高能力。环境与平台服务这个目标；P1是当前选定的交互协议，以后可以替换。

## 一套职责，一个当前入口

从[架构职责与局部细分](docs/ARCHITECTURE.md#responsibility-refinement)理解目标、职责和实现；[唯一链路图和连接表](docs/plans/p1-prefreeze-2026-10-18/CHAIN_PURPOSES.md#唯一上层图与连接要求新任务入口2026-10-19)：在线连接游戏、Host、公开环境、交互协议和完整Agent；记录连接不可变原件与派生；研究连接用途、输入/目标、训练、模型和新运行。图和requires/provides只在该处维护。

- **项目现在做到哪、卡在哪、下一步是什么**：[CURRENT](docs/memory/CURRENT.md)。当前任务、正式计数、预检和安装状态都从这里查，不能从设计或旧报告推断通过。
- **执行中的现场与停止点**：只从CURRENT进入其链接的当前任务。任务给准确分支、负责人、进程、验收和停止点；本地检查点是恢复线索，不另派发工作。
- **先前怎么解决过**：[已有解法与适用边界](docs/RESOLVED_ISSUES.zh-CN.md)。遇到同类问题，先读相关行及其原件，再决定复用还是修改；不是重读全部历史。

## 按要做的事找

[日常操作导航](docs/DOCUMENT_MAP.md#按我要做什么进入)按目标进入录制、安装恢复、数据/R2、训练控制、模型/Agent与评估，以及开发测试、协议迁移、发布、账号事故和协作。它只路由到原有操作指南，不另抄一套流程。

新成员先看[共同工作规则](docs/WORKING_RULES.zh-CN.md)和适用的[工程入门](docs/NEW_ENGINEER_GUIDE.md)；Agent另读[协作与续接](docs/AI_COLLABORATION.md)。遇到旧问题，从[已有解法与边界](docs/RESOLVED_ISSUES.zh-CN.md)直接找原代码和证据。历史资产由[原账本](docs/plans/p1-prefreeze-2026-10-18/ASSETS.md)定位，不把旧结果当当前通过。

STS2决定规则和原生执行；Connector决定公开信息、动作绑定和执行时复验；Host Runtime管进程与隔离；Annotator/Evidence管原件；Policy Runtime管受控执行；应用管用户入口；研究管模型、训练与评估。Mod是装载包装，GUI映射同一应用用例，不再造一套控制或数据链。

源码/测试、实际构建安装、真实运行、Human来源与策略质量分别有证据。旧失败和原件保留；合入、正常退出或一个检查通过都不能替其它层授予资格。

开发环境沿用Node20+、uv和项目所需.NET；初始化/命令按[新工程师指南](docs/NEW_ENGINEER_GUIDE.md)与[开发流程](docs/DEVELOPMENT_WORKFLOW.md)，已有依赖不重复安装。可用 `npm run project:context -- --component <name>` 查局部入口。历史仓库导入由 `migration/project-import.json` 保留，不因新版本重写旧记录。
