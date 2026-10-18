# SpireAgent：先从这里看

目标是做出最强、能真实使用的《杀戮尖塔2》Agent，靠采集、数据管理、训练、接入、评估和迭代持续提高能力。环境与平台服务这个目标；P1是当前选定的交互协议，以后可以替换。

## 一张图，两个状态位置

看[唯一链路图和连接表](docs/plans/p1-prefreeze-2026-10-18/CHAIN_PURPOSES.md#唯一上层图与连接要求新任务入口2026-10-19)：在线连接游戏、Host、公开环境、交互协议和完整Agent；记录连接不可变原件与派生；研究连接用途、输入/目标、训练、模型和新运行。图和requires/provides只在该处维护。

- **项目现在做到哪、卡在哪、下一步是什么**：[CURRENT](docs/memory/CURRENT.md)。当前在打通原生记录、P1派生和实际验收；正式计数、预检和安装状态都从这里查，不能从设计或旧报告推断通过。
- **执行中的本地细节**：负责人只维护一个固定的 `work/P1-SEMANTIC-ACTIVE-20261019.md`（本地工作目录中的root ACTIVE，不在Git checkout内）。它补充工作现场，不替代仓库合同；其它worker快照或旧CURRENT不能派发总任务。
- **先前怎么解决过**：[已有解法与适用边界](docs/RESOLVED_ISSUES.zh-CN.md)。遇到同类问题，先读相关行及其原件，再决定复用还是修改；不是重读全部历史。

## 按要做的事找

| 要找什么 | 去哪里 |
|---|---|
| 新成员先了解如何工作 | [AGENTS](AGENTS.md) → [共同工作规则](docs/WORKING_RULES.zh-CN.md)；只读当前任务相关owner，不整本通读历史 |
| 目标、范围、当前验收与录制操作 | [CURRENT](docs/memory/CURRENT.md)链接本轮任务与实际操作单；[P1计划目录](docs/plans/p1-prefreeze-2026-10-18/README.md)是专题资料，不是另一份“现在” |
| 新增来源、换profile、定位哪个组件负责 | [链路连接表](docs/plans/p1-prefreeze-2026-10-18/CHAIN_PURPOSES.md#唯一上层图与连接要求新任务入口2026-10-19) → [Architecture](docs/ARCHITECTURE.md)、[Components](docs/COMPONENTS.md)、[P1合同](docs/protocols/P1_GLOBAL_R1.zh-CN.md) |
| 采集、封包、验证与数据用途 | [Annotator](components/annotator/README.md)、[Evidence](components/evidence/README.md)、[Data/Provenance](python/docs/DATA_AND_PROVENANCE.md)；可校验不等于Human来源或训练准入 |
| Agent运行、工作台与模型接入 | [Policy Runtime](components/policy-runtime/README.md)、[应用/研究地图](python/docs/DOCUMENT_MAP.md)、[Workbench用例](python/docs/PROJECT_CONSOLE.md)；Model只是完整Agent的一部分 |
| 开发、检查、发布与具体目录 | [开发流程](docs/DEVELOPMENT_WORKFLOW.md)、[测试](docs/TESTING.md)、[版本](docs/VERSIONING.md)、[文档地图](docs/DOCUMENT_MAP.md)；按改动选必要检查 |
| 已有结果、旧方案或资产去向 | [问题索引](docs/RESOLVED_ISSUES.zh-CN.md) → 对应证据；其它资料从[文档地图](docs/DOCUMENT_MAP.md)按需找，保留原版本/适用范围 |

STS2决定规则和原生执行；Connector决定公开信息、动作绑定和执行时复验；Host Runtime管进程与隔离；Annotator/Evidence管原件；Policy Runtime管受控执行；应用管用户入口；研究管模型、训练与评估。Mod是装载包装，GUI映射同一应用用例，不再造一套控制或数据链。

源码/测试、实际构建安装、真实运行、Human来源与策略质量分别有证据。旧失败和原件保留；合入、正常退出或一个检查通过都不能替其它层授予资格。

开发环境沿用Node20+、uv和项目所需.NET；初始化/命令按[新工程师指南](docs/NEW_ENGINEER_GUIDE.md)与[开发流程](docs/DEVELOPMENT_WORKFLOW.md)，已有依赖不重复安装。可用 `npm run project:context -- --component <name>` 查局部入口。历史仓库导入由 `migration/project-import.json` 保留，不因新版本重写旧记录。
