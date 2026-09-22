# AI collaboration and long-task handoff

Status: owner-requested working agreement, 2026-09-19; refined 2026-09-21 and
2026-09-23 for local supervision, independent review and bounded task observation. Source integration and execution evidence remain separate.
This document owns AI task delegation and waiting behavior; it supplements
[Engineering Governance](ENGINEERING_GOVERNANCE.md),
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

Owner update, 2026-09-23: the local supervisor has taken over current implementation,
planning and acceptance coordination, including unfinished B0. The web architect is no
longer the daily relay and completing B0 is not a prerequisite for this role transfer.
This supersedes the earlier human-relayed architect/Luna conversation arrangement.
The supervisor states material decisions, their evidence and consequences before acting;
broad autonomy does not permit silent changes of product goals or evidence claims.

## Roles and actual access

One local supervisor owns the work queue, architecture decisions, final evidence review
and integration coordination. Use Sol for bounded complex implementation/native research
and Luna for bounded reading, independent review or observing an actual task when useful.
The current owner excludes Astra subagents. Model/tool availability must be observed,
not inferred from these names. Do not delegate merely to fill slots or hide chains of
workers. The lead may implement, but its own implementation and documentation require a
separate review just as worker changes do. The author cannot self-accept a candidate.

Each worker has one task, an exact baseline, bounded files, its own writable worktree and
mutable environment, explicit checks and stop conditions. Reconcile existing A/B/C work
before assigning an overlapping writer; do not replace their branch, worktree or process.
Parallel read-only work and disjoint implementation are allowed under the supervisor's
coordination. Shared contracts, BOM fields and the integration ref each have one writer.
Use one heavy local build/training/profile slot unless resources are explicitly checked;
never stop another worker's process to obtain it.

A repository issue is a durable packet, not proof of dispatch or execution. Record real
prepared/acknowledged/running/terminal states and actual agent or job IDs. No tool receipt
means not started. Never invent a task, terminal, worker, installation or persistent
observer. A human owns required account access and native Human actions. Production
installation, restart, deployment, game control, real-data training/use changes, paid
compute and publication require their scoped authorization; supervisor status alone
provides none. Previously authorized actions do not require repeated permission.

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

Prefer a small number of genuinely independent packets. Assign implementation only
after its owning facts and shared dependencies are clear; native changes first need the
relevant game mechanism checked. Do not scatter coupled lifecycle state across
uncoordinated workers or claim the whole Stage 1a is one indivisible repair. The lead re-reads the resulting diff, tests and actual receipts.
A worker summary or green ancestor CI alone is not review evidence.

## Bounded end-to-end packets and fewer round trips

Owner refinement, 2026-09-21: narrow means one coherent outcome, not one shell command,
one file, or one permission question per substep. A packet should normally include its
bounded prerequisite preparation, implementation, faithful regression, local diagnosis
and correction, light closeout, and one candidate publication when explicitly allowed.
Small directly related fixes inside the declared paths/contracts may be completed before
returning; do not ask the human to relay every local assertion failure. Do not expand into
another product feature, layer redesign, dependency upgrade, or unapproved operation.

Each packet defines an authority envelope and a completion boundary:

- distinguish read-only inspection, private locked developer-environment preparation,
  permitted source edits, commit/push/PR creation, and any explicitly allowed integration;
- name a reusable isolated worktree/environment, exact baseline and allowed refs/paths;
- state expected success and negative cases before coding, including meaningful fixtures;
- allow routine defaults and already authorized prerequisites without another permission
  question; record what was actually installed or executed, not just code changes;
- predeclare the response to missing tools, changed refs, failed checks and long waits;
- stop for a real authority/access conflict, a new owning-layer problem, an unexplained
  identity change, contradictory acceptance conditions or an unresolved unknown outcome.

The default is no merge, production installation/restart/deployment, Steam mutation,
real-data training, new budget or destructive data operation unless the packet explicitly
names and authorizes it. A conditional integration packet may include a previously
independently accepted exact PR merge and preparation of the next Draft PR; it must not
merge the new unreviewed work. No admin bypass, force push or unbounded repair-until-green.
When a failure is within the declared repair scope, diagnose and repair it locally rather
than returning immediately; if it remains unexplained after a bounded diagnostic pass,
return the evidence and smallest blocking question, not a wider speculative rewrite.

### Check economy without lowering required gates

TESTING.md remains the sole owner of executable check selection and valid receipt reuse.
A prompt is not a CI trigger or a reason to run full tests. During implementation, use the
cheapest faithful targeted checks. Group directly related stable changes before one normal
push to the existing PR; do not push every one-line intermediate edit. Separate unrelated
work and do not hide failures to minimize the number of pushes.

At candidate closeout, inspect actual diff, run closeout/hygiene and the existing planner.
Normal topic PRs still execute every selected leaf gate on their current candidate and
require portable. If full is selected, full is required; this policy never authorizes
editing workflow/filters, reducing coverage, adding skips, copying another branch's green
result or marking unfinished jobs successful. Green CI does not replace independent review.
Use the existing exact-tree execution-receipt mechanism only in the integration/release
contexts already permitted by TESTING.md, with its current-identity revalidation.

Do not run an identical long local full suite concurrently with hosted full CI just to
produce a second PASS. Retain specifically required local OS/native/install checks and
state what hosted checks cannot prove. If a published candidate fails, repair only an
already authorized, understood cause, then batch one new candidate; new head requires
fresh applicable checks. Old successful evidence remains limited to its original identity.
No waiting for CI between every file edit, and no acceptance/merge of a failing candidate.

### One evidence return

The normal return is task/precise head, actual changes and side effects, checks with their
source and terminal state, real PR/run links, and remaining unknowns. The architect reads
accessible GitHub material directly; do not require screenshots or a ZIP of the same remote
source. Attach only necessary local-only redacted evidence. A /Users/... path is not an
uploaded attachment. Routine success needs no human reinterpretation or repeated restatement.
Within the envelope, finish the packet before returning; cross the next unreviewed or
unauthorized boundary only after architect review and explicit task authorization.

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

## Five-minute waiting checkpoint and bounded extension

Owner update, 2026-09-23: five minutes remains a passive-wait checkpoint, with a
bounded extension to ten minutes when progress is credible and completion is near.
Active reading, implementation and review are not subject to this elapsed-work limit.
Ordinary authorized repairs should finish locally; the human is not a relay for each
assertion failure or each completed substep.

For an already authorized long CI/test/training job, prefer one real read-only observer
bound to its exact run/head/attempt or local task identity. Declare a finite total window
(for example 45 minutes), use actual terminal notifications or low-frequency bounded
reads, report once at completion/deadline and exit. It must not rerun jobs, mutate code,
launch a new configuration or renew itself through replacement agents. The supervisor
continues independent authorized work rather than passively polling. An observer's
report is verified against the source service before acceptance.

If no persistent observation mechanism is available, say so and provide the real monitor;
do not promise notification after the session ends. A monitoring deadline is not a job
cancellation, a failed CI result or automatic authorization for the next training batch.
Do not kill a job because the conversational round ends. At an actual human/access/
authority boundary, deliver a concrete reviewable handoff and stop dependent actions;
continue independent work when possible instead of ending all work prematurely.

There are two legitimate task states at handoff: NOT STARTED when no verified executor
exists; STARTED AND VERIFIED only after a real job identity and monitor have been read.
A previously authorized bounded pipeline may complete its declared stages; new data,
spending, production effects or unreviewed scope require a separate decision. Long
waiting never creates those permissions.

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

Do not use a person as the first debugger. The assigned implementation worker first
completes source review, faithful regressions and rollback readiness. Exact
build/install/load checks follow their owner and the granted environment permissions;
production installation is not implied by this preparation. The local supervisor
verifies the evidence and obtains independent review before the human packet.
The human packet names the exact candidate, minimum actions, expected observations,
monitor, safe stop and which claim the canary can establish. A model-driven action is
Agent evidence, never native Human training data. Pauses, cancellations, unknowns and
failed attempts retain their original records.

A task report states changed files, before/after SHAs, commands actually run, evidence
level, failures/skips, artifact/runtime identities, unresolved risks and next gate.
Do not overwrite immutable assets, restore an old database merely to roll back code,
weaken Gold controls, or imply that a documentation merge deploys clients or services.
