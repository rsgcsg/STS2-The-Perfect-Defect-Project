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
and integration coordination. Owner update, 2026-10-09: new workers default to
GPT-6.1-Sol with high reasoning, including implementation and independent review.
Already-running workers may finish their current packets without a model restart.
Astra is reserved for a material architecture, direction or fundamental-risk review;
at most two Astra workers may run simultaneously. It is not the ordinary default.
Model/tool availability must be observed, not inferred from these names.
Do not delegate merely to fill slots or hide chains of
workers. The lead may implement. Material changes, including its own, require non-author
acceptance under the review section below; an ordinary author self-check is not
independent acceptance.

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
names and authorizes it. A conditional integration packet may include an exact
PR merge accepted under its applicable review level and preparation of the next
Draft PR; it must not merge work still lacking required review. No admin bypass,
force push or unbounded repair-until-green.
When a failure is within the declared repair scope, diagnose and repair it locally rather
than returning immediately; if it remains unexplained after a bounded diagnostic pass,
return the evidence and smallest blocking question, not a wider speculative rewrite.

### Check economy without lowering required gates

[TESTING](TESTING.md) owns check selection, execution dependencies and receipt reuse.
Use focused faithful checks while editing, then batch the stable related candidate.
Run closeout/hygiene and every current router-selected gate before acceptance;
do not weaken coverage, borrow an ancestor's green or run a duplicate long local
full suite alongside hosted full without a specific unresolved need. Preserve
required local native/install checks and recheck affected claims after changes.

### One evidence return

The normal return is task/precise head, actual changes and side effects, checks with their
source and terminal state, real PR/run links, and remaining unknowns. The architect reads
accessible GitHub material directly; do not require screenshots or a ZIP of the same remote
source. Attach only necessary local-only redacted evidence. A /Users/... path is not an
uploaded attachment. Routine success needs no human reinterpretation or repeated restatement.
Within the envelope, finish the packet before returning; cross the next unreviewed or
unauthorized boundary only after architect review and explicit task authorization.

## Mandatory independent review before acceptance

Submitted work is not accepted evidence. The lead verifies actual code and records;
its own verification is an author self-check, never independent acceptance.
Material changes under [Governance section2](ENGINEERING_GOVERNANCE.md#2-change-classes-and-required-confidence)
require a non-author; uncertain impact is material. Ordinary internal/editorial
changes may be accepted by the author's short PR self-check: behavior/consumer,
actual checks, effects, rollback and remaining work. Peer sampling is optional.
No separate reviewer/report is required for every trivial edit; self-check is not
independent acceptance. The current selected checks remain mandatory.

### Review the work actually done

Read the original task, current design, full changed-file list/diff, affected
functions, producer and actual consumers. Map acceptance conditions to evidence;
do not lower them to fit a patch. Check fixtures, assertions, mocks, skips, locks
and changed tolerances against production serialization and failure behavior.
For a trust boundary, the reviewer chooses a falsifying example or metamorphic
property independently, traces it through the producer/consumer and records the
observed result. Existing faithful cases may be reused; source reasoning or an
unexecuted case must be labelled, not presented as a passed test. No novel failing
test is demanded merely for ceremony; uncovered material claims remain pending.

Inspect actual command/head/scope/run/terminal results and external side effects.
Installation, native/Human, data use, spending and model quality require their own
records; clean Git and portable green do not prove them. Preserve failed attempts,
missing access and uncertainty. Reproduce only what is needed to test the claim,
without a duplicate full suite or indiscriminate whole-archive hash walk.

### Acceptance is a separate, scoped decision

Return reviewed base/head, task/consumer and conditions, personally inspected
code/originals versus worker-reported, inaccessible or missing evidence,
findings, actual effects/checks, accepted scope and remaining owner/gate. Label
accepted for the stated scope, changes required, missing evidence/access or running.
A source-only subtask may complete while the parent real-consumer capability stays
open. Acceptance is not additional merge, release, deployment, training or spending
or data-mutation authority; execute actions already authorized without requesting
them again. An unexplained critical path cannot be accepted. Changed source,
inputs, dependencies or artifacts require impact review and current
applicable checks under TESTING, not automatic transfer of an old verdict.

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
changes follow the same materiality rule; an author self-check never certifies a
higher capability or an independent gate.

## Task packet and delivery format

Owner refinement, 2026-10-06: use one stable task ID and one consumer-visible
outcome. Historical baseline IDs remain in the [P/E/G/V/R index](plans/BASELINE_TASKS.zh-CN.md);
CURRENT and its active plan dispatch current work;
subtasks retain the parent ID. Baseline task gates and engineering change classes
are separate fields. Explanatory groupings never renumber active work.

Use the [engineering task form](../.github/ISSUE_TEMPLATE/engineering_task.yml)
or the same fields in an existing issue/PR. Scale detail to risk; a small packet
can be short, but must not omit its owning fact, dependencies or acceptance.

| Packet | Required content |
| --- | --- |
| Goal and basis | real need, task ID, accepted design/version, evidence and relevant historical decision |
| Placement and scope | exact repo/base, owner, one writable worktree, paths, reusable mechanism, non-goals |
| Dependencies and authority | producer/consumer contracts, shared writer, allowed operations/resources, unresolved decisions |
| Validation | normal and negative examples, cheapest faithful checks, required evidence level, rollback |
| Delivery | artifact/code/report, next consumer and use path, completion and stop conditions |

The receiver rechecks changed refs/dependencies before editing. Independent
investigation can run in parallel; provider/consumer implementation can run in
parallel once shared semantics and examples are stable. One owner writes a shared
contract, lifecycle state machine or integration ref. A changed interface triggers
an impact update for its consumers; no worker changes semantics merely to make
its local test pass. Use real useful concurrency, not a target worker count.
Freeze a common base and shared normal/negative examples before dependent implementation;
workers can independently advance their owner paths without waiting for unrelated packets.
The supervisor alone reconciles shared contract/ref/BOM changes and the local native slot.
Accept useful increments by their next consumer's usable capability, not by the number of
workers, commits or green tests. Native diagnosis, faithful old-model recovery, and data
admission can proceed independently when each one's real prerequisites hold; none is a
universal prerequisite merely because it was scheduled first. Resource budgets are one
aggregate envelope for the round, never a fresh allowance per worker or resumed packet.

Return exact head/artifact identity, behavior changed, actual checks and side
effects, evidence scope, limitations, next consumer and remaining gate. Distinguish
submitted, independently accepted, integrated and runtime-qualified. A claimed
completion without usable artifacts and a consumer handoff is not delivery.

### Resuming without selecting an obsolete task

Keep one bounded active handoff in the existing task/PR or its private execution
checkpoint: task ID, latest authorized goal, exact worktree/base/head, writable
paths, current attempt/monitor, completed artifacts and one next action. Replace
its active summary when the task changes; archive older details instead of
prepending an ever-growing history. Never use an old worker name, session title
or earlier completed canary as the current assignment.

On restart/context loss, first resolve that active packet and reconcile live refs,
original operation status and other writers. If the active assignment is missing,
ask the lead for it while doing only independent read-only recovery; do not replay
a prior experiment. Selected design and acceptance reasoning needed by another
engineer must also be in the canonical spec/task, not only the private checkpoint.

## Five-minute passive-wait checkpoint

Owner update, 2026-10-06: after about five minutes of only passive waiting, end
that wait cycle. Switch to independent useful work, perform a bounded diagnosis,
or provide a resumable handoff if nothing independent remains. Do not silently
extend the old ten-minute waiting allowance or replace observers to wait forever.
Active reading, implementation and review are not subject to this elapsed-work limit.
Ordinary authorized repairs should finish locally; the human is not a relay for each
assertion failure or each completed substep.

Stopping conversational waiting is not cancelling CI, training or an external job,
not evidence that it failed, and not a passed result. Preserve its exact identity
and real monitor; never promise later notification without an actual mechanism.

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

A normal user update can be six short fields: goal, completed work, result,
failure/cause, next step and needed user decision. Link the existing task/evidence
for exact identities and recovery; do not copy its full history into every update.
Running/waiting work still supplies the real monitor and recovery fields above.

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
