# AI collaboration and long-task handoff

Status: owner-requested working agreement, 2026-09-19; clarified 2026-09-21. Source integration and
execution evidence remain separate. This document owns AI task delegation and
waiting behavior; it supplements [Engineering Governance](ENGINEERING_GOVERNANCE.md),
[Development Workflow](DEVELOPMENT_WORKFLOW.md) and [Testing](TESTING.md), not game,
data, authorization or research authority.

## Current owner direction and conversation workflow

Read [single-team authorization and coordinated distribution](STAGE1A_TEAM_OPERATIONS.zh-CN.md)
for the latest product refinement of Stage 1a. One configuration/consent experience reuses
existing identity, membership, consent and use authorities; it is not one universal secret.
The cloud product serves the existing single invited team, not a new multi-tenant service.
Workshop/client updates and Hub promotion share compatibility/release coordination, not
an authority allowing every client to deploy the server. Product installation consent
is not permission for an AI assistant to launch training, paid compute or deployment.

In this conversation the architect plans and gives a narrow prompt; the human relays it
to local Codex-Luna; Luna implements/tests and returns redacted exact evidence; the
architect re-reads source/diffs/checks and accepts, returns a targeted fix, or marks
blocked before giving the next prompt. A human native gate remains separate. The architect
may directly maintain its own reviewed documentation/issue/PR work through GitHub, but
never shares Luna's writable branch or direct-pushes develop/main. Prepared tasks are not
reported as dispatched or running without an actual execution receipt. Issue #28 is the
initial read-only reconciliation packet; do not issue a duplicate broad implementation.

## Roles and actual access

The cloud architect/planner/product-manager assistant owns requirements, architecture,
acceptance criteria, narrow task packets, dependency order and review. Codex-Luna is
the implementation/verification worker selected by the owner, not an assumed GitHub
username, model version or automatically connected execution service. A human owns
account/host access, explicit compute/publication approvals and native Human steps.

The architect reads current source and evidence, writes plans/governance and prepares
reviewable work. Application/native changes, local inspection, tests, installation and
runtime probes are assigned to Luna or a human with the required access. The architect
must not claim to have run a local command merely because a packet contains it.

A repository issue is a durable task packet, NOT proof of dispatch or execution.
Mark delivery separately as prepared, handed_to_operator, submitted, acknowledged,
running or terminal, using an actual tool/task receipt for the latter states. Never
invent a Luna task ID, worker assignment, local terminal, desktop session, or background
monitor. Without an authorized worker interface, hand the exact packet to the human
for delivery. Do not install another integration or expose local credentials to bypass
missing access.

Local Codex history is private workstation state. Inspect only the relevant authorized
project thread, changes and logs. A pasted conversation is user-provided context, not a
fresh read of the local machine and not execution evidence. Correlate its claims with
exact source, tests, run IDs and receipts; redact private paths, prompts, tokens, raw
recordings and model weights before publishing a summary. Do not dump all local history.

## One narrow task, one owning correction

Before issuing a task, refresh remote refs and open work, inspect the relevant files,
and give the worker:

- task ID, one observable outcome, owning layer and change class;
- exact repository, baseline SHA, permitted branch/base and one writer/worktree;
- affected paths, explicit non-goals and dependencies;
- failure semantics, unchanged contracts and identity/rollback requirements;
- the cheapest faithful regression, selected gate and any exact-game/Human gate;
- output format, next decision and stop/handoff conditions.

The worker must re-resolve the live base before edits. A changed baseline or overlapping
writer requires an explicit reconciliation plan, not a force reset or automatic cherry
pick. Normal tasks start at current develop; work depending on unmerged Stage 1a source
must use an explicitly approved dependent branch or wait for reviewed integration.
Do not write into another worker's topic or a running collector checkout. Existing
component-source provenance still requires normal merges.

Prefer one active implementation packet. A second read-only or genuinely disjoint
packet is acceptable when dependencies and ownership are explicit. Do not issue a
single instruction to implement all Stage 1a or scatter coupled lifecycle state across
uncoordinated workers. The lead re-reads the resulting diff, tests and actual receipts.
A worker summary or green ancestor CI alone is not review evidence.

## Mandatory independent review before acceptance

Owner clarification, 2026-09-21: EVERY Luna return starts as submitted but unverified.
This includes explanations, diagnosis, suggested plans, code, tests, claimed commands,
installations, deployments and measured results. Confidence, a polished summary, a
screenshot saying PASS, or the worker's own review is not acceptance. The same standard
applies to the architect's earlier claims and its own GitHub documentation changes.
Independent review means a new examination of authoritative material, not asking the
same worker to restate that it is correct. It does not imply access to a second machine.

### Review the work actually done

| Review question | Required examination |
|---|---|
| Did it solve the approved task? | Re-read the exact task and current approved design, list its acceptance conditions, and map each to implementation and evidence. Do not lower the target or rewrite the design merely to pass a completed patch. |
| What actually changed? | Refresh base/head and inspect the complete changed-file list and diff, relevant full functions, callers and affected consumers, including tests, fixtures, locks, CI and configuration. Explain the before/after behavior and why the owning cause is repaired. |
| What happened outside Git? | Reconcile reported builds, installs, package publications, service restarts, data/use-ledger mutations, training and spending separately. A clean worktree proves none of these; compare actual authorized effects with the task's non-goals. |
| Are the tests meaningful? | Read assertions and fixtures, check that the failure can be caught on the real path, and inspect mocks, skips, weakened checks and changed tolerances. A regression should expose the old defect where reproducible; state when a before/after run is unavailable. |
| Were the checks really executed? | Inspect the actual command, source/dirty-tree identity, selected scope, run/job/attempt, terminal status and useful log or report. Preserve failures, cancellations, skips and retries. A green aggregate does not prove skipped suites ran. |
| Does the result match the running system? | When the claim requires it, reconcile source, build, package digest, installed identity, loaded Mod/runtime, model and input IDs, service observations and native receipts. Old bytes or another run cannot qualify new code. |
| Did it preserve the product and research boundaries? | Check complete candidates, B/C input semantics, safe Human/Stop, unknown delivery, Human/Agent separation, data lineage and Gold/use protection, as relevant. Training completion, a small dev score, or a single delivered action cannot prove quality or full-scene coverage. |

Trace each load-bearing claim to a concrete source or receipt. An executable command in
a prompt is not evidence that it ran. Review provided private receipts within authorized
scope and identify them as provided evidence, not a direct local observation. If the
architect cannot inspect a necessary artifact or runtime, mark that claim unverified and
request one bounded diagnostic or human gate. Do not invent access or publish raw secrets,
recordings or model weights to make review easier.

Use risk-based reproduction and the cheapest faithful tests, not an automatic full-suite
rerun for every handoff. New diagnostics and long runs require their own bounded task.
The five-minute handoff rule still applies. Native Human steps follow technical checks;
never ask a person to repeat gameplay blindly or treat their 'done' as verified success.

### Acceptance is a separate, scoped decision

The architect returns a review receipt with:

- task and reviewed base/head; the original acceptance conditions;
- actual changes and external effects, with relevant file/function or receipt references;
- independently inspected evidence versus worker-reported, missing or inaccessible evidence;
- findings and their consequences, remaining required gates and an explicit verdict;
- the accepted scope and the one next authorized task, or the blocking question.

Use the plain verdicts: accepted for the stated scope, changes required, blocked by
missing evidence/access, or still running. These are review labels, not new runtime
schemas. A verified source/test gate may be accepted while installation or native/product
acceptance is explicitly pending. Do not call the whole task or Stage 1a complete until
all of its required conditions pass; optional follow-up polish must be distinguished from
missing required work. Acceptance is not automatic permission to merge, publish, deploy,
spend, change production data, or start the next training batch.

New commits or changed inputs, configuration, dependencies or installed artifacts require
an impact review of the previous verdict. Reuse only what remains valid under TESTING.md;
never copy an ancestor's green result or extend an approval to unreviewed changes. The
architect must understand and explain what the implementation does, not merely relay Luna's
conclusion. If it cannot explain a critical path, that path is not accepted yet.

### Design availability and branch discipline

Before implementation, pin the design reference as well as the code reference. The
product-delivery document owns the overall journey, the single-team operations document
owns its newer authorization/distribution/stage-scheduling refinement, and the owning UI
specification/contracts define the relevant interface. Name conflicts or missing detail
explicitly; neither the worker nor reviewer may silently select an easier requirement.
A chat attachment or PDF is not automatically a tracked repository file or an accessible
Luna input. Any required page, state, data-flow or acceptance detail available only there
must be supplied or captured in a reviewable text specification before that implementation
packet is accepted. Requirements remain distinct from current implementation evidence.

A documentation PR being present on GitHub does not put its files into develop, main or
the worker's checkout. State the actual branch/commit and integration status in handoffs.
Do not force-reset or merge another writer's branch just to read a design. Documentation
changes by the architect remain reviewable candidates, not self-certified project gates.

## Mandatory five-minute waiting rule

If any training, encoding, testing/CI, build, download, deployment, profile or other
operation is expected to require MORE THAN FIVE MINUTES of waiting, the AI ends the
current round with an explicit human handoff instead of staying in a polling loop.
The rule applies to an already-running operation when its remaining wait becomes
likely to exceed five minutes. Uncertain long duration is a reason to hand off, not a
reason to promise a completion time. Shorter jobs may also be handed off.

Five minutes is a handoff threshold, not permission to launch, a process timeout or
an instruction to kill a job. It replaces the earlier approximate two-minute waiting
threshold in the Stage 1a topic's narrative workflow; all identity, evidence and
non-automatic-next-batch requirements remain. Do not split one long workload into many
polling calls, start a replacement run, or silently weaken a gate to avoid handoff.

There are two legitimate handoff states:

1. NOT STARTED: execution is unavailable, unauthorized, or cannot be shown to survive
   this interaction. Give the human/Luna the bounded command or task and where its
   actual monitor will be produced. Do not claim a process or monitor already exists.
2. STARTED AND VERIFIED: an authorized independent runner has returned a real job/run
   identity and an accessible status/log location. Verify those once, then hand off.
   No cloud budget, game start, installation or release permission follows from this
   rule. An assistant without a supported execution/delegation capability uses state 1.

The handoff includes:

| Field | Required meaning |
|---|---|
| Work and state | What is being checked; not_started/running/waiting_human/etc. |
| Identity | Repository/branch/exact SHA; job ID if real; data/model/config/checkpoint identities where relevant |
| Monitor | Verified existing page/terminal/log/status file, or explicitly a proposed monitor for an unstarted task |
| Read method | Exact supported view/command and what terminal success/failure/unknown looks like |
| Human action | Keep the executing machine awake, perform bounded native steps, or simply return the terminal result, as applicable |
| Recovery | Owning pause/stop/resume method, side effects and how to preserve failed evidence; unsupported controls stay unsupported |
| Resource scope | Executing machine/provider, authorized budget and whether another job is already active |
| Resume boundary | What will be checked after the human returns; no automatic next major batch |

Do not fabricate progress percentages, ETA, cancellation success or checkpoint safety.
A completed process is not automatically a passed test, valid model or qualified game
run. A network disconnect is not a failed or absent operation. Reconcile status first;
unknown gameplay delivery is never replayed. Cloud jobs also require the provider's
known submission/reconciliation semantics; a missing response is not a new-job license.

A handoff is not complete with only “wait and reply when done.” Make the relevant
monitor and next human action usable. On return, verify the exact terminal record,
source/input identity, exits, test failures/skips, checkpoint/result and evidence scope
before planning the next packet. Do not auto-start the next configuration or another
major training/evaluation job because the prior one ended. A previously authorized
bounded pipeline may complete its declared steps; it cannot expand itself.

Suggested Chinese handoff form:

```text
本轮交接：<任务与实际状态>
执行位置／精确源码：<位置、分支、SHA>
任务身份：<真实 ID；未启动则明确写未启动>
监测窗口：<已验证页面或日志；未创建则不伪造>
查看方法与终态：<方法；成功／失败／未知如何区分>
你现在需要做：<最少必要操作>
暂停／恢复：<现有 owner 入口；尚不支持则说明>
回来时提供：<状态摘要或脱敏回执>
下一步：先核对该结果，不自动启动下一批。
```

## Human and native gates

Do not use a person as the first debugger. Luna first completes source review,
faithful regressions, required exact build/install/load checks and rollback readiness.
The human packet names the exact candidate, minimum actions, expected observations,
monitor, safe stop and which claim the canary can establish. A model-driven action is
Agent evidence, never native Human training data. Pauses, cancellations, unknowns and
failed attempts retain their original records.

A task report states changed files, before/after SHAs, commands actually run, evidence
level, failures/skips, artifact/runtime identities, unresolved risks and next gate.
Do not overwrite immutable assets, restore an old database merely to roll back code,
weaken Gold controls, or imply that a documentation merge deploys clients or services.
