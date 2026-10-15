# SpireAgent 工程指南（给人和 AI）

## 使命

做一个能稳定、连续地玩真实 STS2 并不断变强的 Agent，同时让项目易理解、易修改、易验证。
能力进展看 `docs/SCOREBOARD.md`；当前选择和下一步看 `docs/memory/CURRENT.md`。

## 先读什么

1. `docs/memory/CURRENT.md` 和活动计划 `docs/plans/P1_ROUND1_PLAN_2026-10-14.zh-CN.md`：当前组合、冻结开关、下一步。
2. `docs/SYSTEM_MAP.zh-CN.md`：分层、每层统一什么、当前组件和工具。
3. `docs/ENGINEERING_RULES.zh-CN.md`：日常规则（两层制、评测、文档写法、角色和决策权、任务包）。
4. 要改 T1 范围时，再读 `docs/ENGINEERING_GOVERNANCE.md`、`docs/TESTING.md`、
   `docs/DEVELOPMENT_WORKFLOW.md` 和所属组件的 AGENTS/README。
5. 职责细节：`docs/ARCHITECTURE.md`、`docs/COMPONENTS.md`。

实时事实（分支、PR、CI、安装、运行）以 GitHub 和原始记录为准，文档可能过时。

## 硬核不变量（T1，永远严格）

- STS2 拥有规则、RNG、原生合法性与 Commit；不读隐藏状态，不做坐标/反射式输入。
- Connector 发布完整有限的合法动作目录并在执行前复验；消费者不自建合法性、不构造原生操作数。
- 回执分 delivered / not_delivered / unknown；unknown 永不自动重试。
- Agent 只得到声明过的公平公开信息；特权信息只能进训练标签，不能进学生输入。
- 原始录制不可变、来源可追溯；不覆盖、不删除失败记录。
- 不提交游戏文件、反编译源码、原始 Human 数据、权重、凭据或 `.local/`。

## 日常流程

- 从最新 `origin/develop` 开短 topic 分支，一个任务一个分支一个 worktree，PR 合入 develop。
- 不 direct-push `main`/`develop`，不 force-push，不共用写分支。
- PR 改到 `components/` 或 `apps/` 用 normal merge；其余可 squash。
- 按 `docs/ENGINEERING_RULES.zh-CN.md` 第 2 节按行为影响判断 T1/T2/T3；CI 选择由 `check:plan` 决定，组件源身份照常更新。
- 每轮结束用规则第 5 节的 10 行报告模板；“做到了”必须附 commit、run 或 eval id。
- 自主决定的事在 `docs/DECISIONS.md` 记一行；规则第 6 节列出的事先问用户。
- 角色：用户定目标和关键决定；计划与集成 owner 维护当前组合、规范和审查；实施与独立审查者调查、审计、实现、开 PR。
  角色不绑定具体模型；作者不独立验收自己写的关键规则或公共契约。

## 文档

`AGENTS.md` ≤ 120 行，`docs/memory/CURRENT.md` ≤ 4 KB，规范文档 ≤ 300 行。一个事实只写一处。
数字与单位、单词之间加空格，哈希最多 8 位。不写成段的免责和非声明。
历史写进 `docs/log/`；不再有效的计划标明替代入口后归档，不按年龄自动移动或删除。
