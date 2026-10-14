<!-- 规则见 docs/ENGINEERING_RULES.zh-CN.md。T2 填前四项即可；T1 另填下方 T1 部分。 -->

- Tier（`T1`/`T2`/`T3`）及原因：
- Latest head SHA：
- 推进哪个能力数字 / 消除哪个 T1 风险：
- 做了什么：
- 怎么验证（命令、CI、run 或 eval id）：
- 会影响谁：
- 回滚：

## 只有 T1 需要

- 碰到哪条硬核不变量，如何保持：
- 独立审查人：
- 原生改动是否跑过本机 exact-game 门（结果）：
- 合并方式：改到 `components/` 或 `apps/` 用 normal merge。

## 自查

- [ ] 基于最新 `origin/develop`（或写明依赖的 topic）。
- [ ] 一个 PR 一个目的。
- [ ] 没有提交游戏文件、反编译源码、原始数据、权重、凭据或 `.local/`。
