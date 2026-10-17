# Current project context (2026-10-17)

模型方案冻结：开。Resolve live GitHub refs before acting; current source and original
evidence override this file. New research configurations, spending and per-PR
merge/close still require their existing user approvals.

## Read first

1. [WORKING_RULES](../WORKING_RULES.zh-CN.md) — 日常工作规则（§9 是 P1 专用约定）。
2. [P1 全局 r1](../protocols/P1_GLOBAL_R1.zh-CN.md) — P1 唯一权威（r1.1）。其他 P1 文档是背景。
3. [续作计划](../plans/P1_R1_CONTINUATION_2026-10-16.zh-CN.md) — 停止线和按顺序的真实任务。

## Where things are

- The management baseline (MGMT-1, PR #190) is integrated in develop; it has no
  further assignment.
- P1 product topic: `claude/p1-complete`
  ([PR189](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/189), draft to
  develop). It holds Codex's p1-global-completion round plus Claude's fixes that made
  the product path run. PR187 is merged; PR188 (r1.0) is superseded.
- Product path: Policy Runtime (agent-session-1) → Managed service / Reference game.
  External Agents plug in through `examples/p1/session-bridge.mjs` (`sts2.p1/agent-port-1`).
- Suite entry: `node tools/p1-suite.mjs` (report.json with full denominators).

## Current measured state (local source + runtime, not hosted CI)

- Managed (Ironclad en, 10 seeds): 9/10 natural terminal, 0 unknown, 10/10 replay verified.
- Managed (Defect zh, 38 seeds counted): 32/38 natural terminal, 0 unknown, 2519 decisions;
  all 6 stops are T2 (card reward metadata).
- Reference (Defect zh, first 5 test-1000 seeds): 4/5 natural terminal, 343 decisions,
  5/5 replay verified; the one unknown was fixed and that seed reran to natural terminal.
- Reference→Managed replay: diverges at step 0 on Managed deck card details (T1).
- A separate local T7 candidate (not on this branch) reached 90 decisions, 0 unknown,
  stopped at an upgrade mapping gap; not a completed run or this branch's validation.

## Next

P1 continuation (T1 → T2 → T7 → T3 → T4) is the next product assignment once the user
assigns it. Decisions waiting for the user are listed in the plan (T6) and the PR body.

## Remaining Platform non-claims

GLOBAL P1 r1.1 is not frozen, installed for release, Human-validated or Managed-equivalent.
Random early-loss runs are not full coverage, win coverage or strategy strength. Required
missing information keeps its original denominator; unknown never retries. Each PR
merge/closure, deployment and branch/worktree/data deletion needs separate approval.
