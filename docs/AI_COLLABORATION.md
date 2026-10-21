# AI collaboration and long-task handoff

This document owns AI-specific execution and communication. The shared rules for
engineers, architects, managers and Agents are [WORKING_RULES](WORKING_RULES.zh-CN.md).
They are not additional reading to repeat on every turn. Existing user decisions,
component authorities and exact evidence remain binding. Earlier collaboration
arrangements remain in Git history, not competing active assignments.

## Roles and actual access

One lead owns the outcome, queue, integration and acceptance. Delegate only when a
bounded implementation or independent investigation saves time or reduces a material
risk. One worker has one current assignment; it is not a general assistant collecting
miscellaneous work. Do not spawn merely because slots are available, or create hidden
worker chains. The lead may implement; delegation never transfers accountability.

项目模型偏好：新子代理默认GPT-6.1-Sol/high；简单任务可选其medium/low，或GPT-6-Luna/xhigh、max，由主管按任务与实际可用模型选择。这是用户项目偏好，不是平台可用性或能力保证，不为换模型重启正在运行的任务。更贵模型只用于具体未决问题。总轮次预算不因worker/上下文更换而重置，也不因此授权付费外部操作。

Use a small number of non-overlapping writers on separate branches/worktrees. Reuse a
suitable clean checkout where possible; read-only reviewers need no new worktree.
One owner writes shared contracts, BOM and integration refs. One heavy local/native
slot is the default; increase only for demonstrated available resources. Never stop
another worker or delete its dependencies to obtain resources.

### Reuse or start a worker

同一结果的修复、补证、审查返工和上下文续接，优先复用原执行者；保留原任务范围和已花成本。新的独立问题、大量可独立验收的调查，或需要非作者审查的稳定候选，才另开有界Agent。新开窗口或subagent只是执行方式，不能重置截止、预算、失败分母或验收。

先定共享合同和写入owner，再并行独立结果。两位作者需要改同一状态机，或分别猜共享接口时，先由一个负责人打通最短连接，消费者可并行准备忠实检查。架构有几层、目录有几个组件，不决定开几个Agent。任务包给明确结束行为：达成指定消费者结果即交；检查点到时交真实进度；轮次上限到时停止扩范围、保住实物与运行项并收口。时间到不把未完成改成通过，也不任意杀正常用户服务或另有授权的任务。

用户已授权完整阶段时，主管可在该范围内继续后续检查点，无须每次再次申请。需要其亲手操作或未授权的实质决定，保留具体对象、选项和影响；独立工作照常推进，交付时集中呈现，不让睡眠中的用户充当技术协调者。

已授权父任务未验收且未到硬截止时，主管继续核真实进展、解依赖、审计和交付；派发、恢复或状态报告本身不结束该任务，确有须用户亲手处理的阻塞则具名报告。上下文接续仍按下文五项与原件核对，摘要不作为新授权或新证据。

## Task packet and delivery format

Use the shared five-field task card in WORKING_RULES, inside an existing task/issue/PR.
The [issue template](../.github/ISSUE_TEMPLATE/engineering_task.yml) is optional, not
another required copy. Add AI dispatch details: exact source/design base, writable
paths, actual tool permissions, dependency producer/consumer, resource window and
one completion/stop boundary. A small repair may use a short message plus a task link.

A worker acknowledges the outcome and next action, then works within scope. It may
fix related prerequisites, tests and ordinary failures needed for that outcome. It
must not silently widen to another feature or reinterpret missing evidence as success.
If dependencies change, tell the lead the concrete impact; do independent authorized
work while waiting where useful. Do not make the user relay routine technical messages.
Only use actual available authorized communication tools; never claim an issue or
written prompt has started a worker without a real dispatch receipt.

Every report includes chain position (node/edge), one of the four Architecture change
classes, and actual requires/provides or gap; use the existing CHAIN_PURPOSES diagram.
Return a compact delta: exact head/artifact, changed behavior, actual checks, side
effects, unresolved matters and next consumer. Logs stay at their original locations.
The lead checks critical code and results, integrates useful increments and updates the
single current entry. A source subtask finishing does not close the parent runtime or
user capability. Distinguish submitted, reviewed, integrated and actually exercised.

## Resuming without selecting an obsolete task

Before acting after context loss or handoff, read CURRENT and the linked active task.
Privately saved checkpoints are recovery hints, not new instructions. Confirm:

1. latest user goal and stop/non-goals, including corrections since the old checkpoint;
2. exact checkout/branch/head and changed dependencies;
3. completed work and last verified evidence;
4. any actual running process/job and who owns it;
5. one next action and its reason.

If the assignment cannot be recovered, ask the lead/user rather than resurrecting an
old task from a title, worker name or completed report. Preserve source and perform
only relevant read-only recovery in the meantime. Do not repeat the old discovery
phase, launch another collector or rebuild already verified inputs without a reason.
Keep one short checkpoint per current assignment; replace its active summary rather
than prepending endless history. Durable requirements and acceptance must also be in
the repository/task so work is not dependent on one private conversation.

新的独立阶段使用清楚的当批任务名称和当前状态指针；同一有界修复仍可复用原执行者，
不为改名而重启。旧轮私人检查点只保留历史地位，恢复时先以当前任务、准确工作树和
实际运行项相互核对；发现冲突先纠正指针，不按旧文件继续实施。主管须在确认正在做
用户要的同一结果之后派发或验收，不能用旧轮完成消息替代本轮现场。

## Mandatory independent review before acceptance

Material changes under [Governance](ENGINEERING_GOVERNANCE.md#2-change-classes-and-required-confidence)
receive one appropriately scoped non-author review of a stable candidate. Ordinary
editorial/internal changes use the author's short self-check; do not require another
agent for every wording change or commit. A reviewer is not an extra implementation
owner or an excuse to re-audit the whole project.

Read the intended outcome, actual diff, affected producer/consumer and relevant original
results. Choose a meaningful counterexample when the claim needs one; reuse faithful
existing cases instead of inventing a new test for ceremony. State facts, inferences
and unverified areas separately. A request for broader review needs a concrete risk
and cost rationale. Style preferences and hypothetical extensions do not block a
working bounded delivery.

Return accepted scope or actionable findings with evidence and owning correction.
After fixes, recheck those findings and changed dependencies; do not restart full
review on an unchanged base. An unrelated new finding goes to the existing backlog;
a serious directly affected defect may block acceptance, with the reason explained.
A changed head requires impact assessment, not blind acceptance or automatic total
invalidation. [TESTING](TESTING.md) owns exact-content check reuse and required gates.
Review completion does not grant merge/deploy/training permission.

## Five-minute passive-wait checkpoint

After about five minutes of only waiting, switch to useful independent work, diagnose
once, or return a resumable status when nothing else is available. This is not a
five-minute limit on implementation or long authorized tests. Do not cancel another
job or claim it failed because a chat round ended.

For a needed long job, use one observer bound to the exact head/run/attempt with a
finite total window and terminal notification or low-frequency reads. It does not
rerun, mutate, spend more or renew itself through replacement workers. Keep running,
succeeded, failed, timed out and unknown distinct. A report is verified against the
owning service before acceptance. Never promise a later notification without a real
mechanism. Repeatedly polling unchanged output is not progress.

## Honest escalation and stop

When effort no longer produces useful evidence or the purpose is lost, use the shared
reassessment rule; do not add another audit or worker automatically. Technical unknowns
get a bounded investigation; unresolved product/authority/risk choices go to the user
with evidence, options and a recommendation. Existing authorization persists, and
ordinary permitted repairs need no repeated permission.

Reach the stated stop boundary, hand over the usable result and stop. Do not continue
an unassigned backlog item, reopen a completed packet, or interpret a reviewer comment
as permission for a new workstream. If work is incomplete, say exactly which requirement
is unmet and why; never rename the requirement or treat elapsed time as approval.
