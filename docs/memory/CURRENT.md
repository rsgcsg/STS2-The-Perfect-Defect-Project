# Current project context (2026-10-16)

模型方案冻结：开。New learned Model/Input/Target/data/training configurations require
user confirmation. Live GitHub refs, exact source and original evidence override this file.

## Read first

1. [WORKING_RULES](../WORKING_RULES.zh-CN.md) — 推进与管理规则（用户确认，优先于历史流程描述）。
2. [P1 全局 r1](../protocols/P1_GLOBAL_R1.zh-CN.md) — P1 唯一权威（r1.1）。其他 P1 文档是背景。
3. [续作计划](../plans/P1_R1_CONTINUATION_2026-10-16.zh-CN.md) — 停止线和按顺序的真实任务（T1–T6）。

## Where things are

- Integration topic: `claude/p1-complete` (draft PR to develop). It contains Codex's
  p1-global-completion round (040be11e) plus Claude's fixes that made the product path run.
  PR187 is merged; PR188 (r1.0) is superseded by this topic.
- Product path: Policy Runtime (agent-session-1) → Managed service / Reference game.
  External Agents plug in through `examples/p1/session-bridge.mjs` (`sts2.p1/agent-port-1`).
- Suite entry: `node tools/p1-suite.mjs` (report.json with full denominators).

## Current measured state (local source + runtime, not hosted CI)

- Managed (Ironclad en, 10 seeds): 9/10 natural terminal, 0 unknown, 10/10 replay verified.
- Managed (Defect zh, 38 seeds counted): 32/38 natural terminal, 0 unknown, 2519 decisions;
  all 6 stops are T2 (card reward metadata).
- Reference: first full episodes to natural terminal through the product path
  (suite numbers in P1 doc §16).
- Reference→Managed replay: diverges at step 0 on Managed deck card details (T1).

## Next

T1 → T2 → T3 → T4 in the continuation plan, then the stop line → PR to develop → freeze r1.
Decisions waiting for the user are listed in the plan (T6) and the PR body.

## Boundaries that still hold

Each PR merge/closure, deployment, release, and branch/worktree/data deletion needs
separate user approval. No new spending or frozen research change. unknown deliveries
never retry. Preserve raw bytes; derive new versioned interpretations instead of
rewriting originals. Source/test, build, installed, runtime and Human evidence stay separate.
