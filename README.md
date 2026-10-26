# SpireAgent：让《杀戮尖塔 2》Agent 一圈一圈变强

这个项目把**录制 → 保存和选数据 → 训练 → 接入游戏 → 评估 → 改进**接成可重复的闭环。目标是做出强、能实际使用的 Agent，并让每一圈少花时间、不丢已有成果。平台和文档都为这个目标服务。

现在已经有日常真人录制、录后自动入库和小结、R2 保存、学校 GPU 训练入口、模型接入和评估工具。模型强度仍低；Managed 模拟环境有明确差异，不能把它的成绩当真游戏成绩。准确分数、当前任务和下一步只看 [CURRENT](docs/memory/CURRENT.md)。

## 从这四页接手

1. 本页：项目为什么存在。
2. [项目地图](docs/DOCUMENT_MAP.md)：有什么、到什么程度、在哪操作、由哪里负责。
3. [工作规则](docs/WORKING_RULES.zh-CN.md)：新事怎么放、怎么做、何时交用户决定。
4. [CURRENT](docs/memory/CURRENT.md)：本轮正在做什么；顺着它进入唯一当前任务。

先读这四页，再按任务进入原指南；不用重读全部历史。人和 Agent 共用同一业务能力，GUI 是方便人操作的映射，CLI/API/库是受支持的工程入口。

## 最常用的入口

- **我要录一局**：按[日常采集](docs/ANNOTATOR_COLLECTION.md#current-p1-daily-collection)在游戏内开始本人录制；录完查看[自动小结](python/docs/RECOUNT-SOP.zh-CN.md)，正常流程不需要补命令。
- **我要训练或评估**：从[地图](docs/DOCUMENT_MAP.md#按我要做什么进入)选择原操作指南。可读数据不等于获准训练，训练完成也不等于策略变强。
- **我要加功能或修问题**：先找地图中的负责处，再查[已有解法与边界](docs/RESOLVED_ISSUES.zh-CN.md)，按[开发流程](docs/DEVELOPMENT_WORKFLOW.md)和[测试指南](docs/TESTING.md)做最短的忠实验证。
- **我要查以前定过什么或旧东西去哪了**：[决定及替代关系](docs/DECISIONS.md)、[历史资产和本轮增量](docs/plans/CONVERGENCE_DISPOSITION_20261027.md)。保存下来了，不代表已经接入当前能力。

STS2 决定游戏规则；Connector 提供公平信息和合法动作；Host 管运行与隔离；记录和存储保原件；研究负责模型、训练和评估。P1 是当前选用的交互协议，Model 只是完整 Agent 的一部分。细节按需查[架构](docs/ARCHITECTURE.md)、[组件](docs/COMPONENTS.md)、[工程治理](docs/ENGINEERING_GOVERNANCE.md)；专用 Agent 操作见[技能目录](.agents/skills/README.md)。

开发环境沿用 Node 20+、uv 和所需 .NET；首次设置按[工程入门](docs/NEW_ENGINEER_GUIDE.md)，已有依赖不重复安装。运行中的录制、原件、模型和失败记录都要保护；文档 PR 不会自动升级正在用的程序。
