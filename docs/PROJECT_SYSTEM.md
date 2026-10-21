# Project documentation and maintenance system

This document owns the lightweight repository-maintenance system. Its purpose
is to help a new engineer or Codex session find the right truth quickly and to
make cheap drift visible without creating another architecture or identity
authority.

## Source-of-truth hierarchy

Do not put requirements, implementation and evidence in one ranking:

- User goals/authorization and selected contracts say what should happen.
- Native facts, actual source, installed identities and original records say what
  currently happens. They can expose a violated or impossible requirement; they
  do not silently redefine the goal. Tests and CI can also encode wrong assumptions.
- [WORKING_RULES](WORKING_RULES.zh-CN.md) owns shared work practice; the focused
  owners below define architecture, checks, Git and document placement.
- CURRENT and the active task dispatch work. Historical reports, chat summaries,
  private checkpoints and external memory provide leads, never fresh assignments.

If facts and requirements disagree, label the conflict and decide at the owning
boundary. Ask the user when a genuine goal/authority/tradeoff remains unresolved;
otherwise correct the stale owner. Do not create a second status/identity registry.

Initial orientation is AGENTS → README → CURRENT → WORKING_RULES, then the active
task and only relevant owner sections/code. The task names the purpose, output,
acceptance, costs and stop boundary; no active assignment means no automatic
resumption of a historical backlog item. Fresh refs are verified before action.

## Documentation classes and triggers

| Owner | Contains | Update when / keep out |
|---|---|---|
| `README.md`, `DOCUMENT_MAP.md` | Product boundary and short routes by task | Entry points change; no current hashes, work log or copied installation procedure |
| `WORKING_RULES.zh-CN.md` | Shared work practice for people and Agents; task shape, prioritization, bounded costs and stop decisions | A real failure, repeated cost or user decision changes practice; no live task queue or copied technical contracts |
| root/local `AGENTS.md` | Hard invariants, owner routing and change loop | A durable instruction changes; no campaign budget, worker queue or acceptance timeline |
| `NEW_ENGINEER_GUIDE.md`, `NEW_MEMBER_HANDOFF.zh-CN.md` | Role-specific onboarding and links to supported procedures | Setup or user path changes; do not turn every role's checklist into every newcomer's prerequisite |
| `ARCHITECTURE.md`, `COMPONENTS.md`, accepted ADRs | Durable authority, dependency direction and significant decisions | A boundary changes; cite exact protocol owners rather than restating their schemas |
| `DECISIONS.md` | Selected project decisions, rationale and supersession pointers | Current user choices change; owning ADR/spec keeps the contract. `python/docs/memory/DECISIONS.md` retains research/import history, not a competing project queue. |
| owning specification and component contracts | Required outcomes, selected behavior, assumptions, failure/compatibility semantics and decision rationale | Before dependent implementation of a changed design; mark selected but unimplemented behavior explicitly |
| active plan/task/PR; baseline task index | Stable task IDs, dependencies, one writer, acceptance, allowed operations, resources and progress | Dispatch, design selection, review or integration changes; link evidence, do not become a second contract |
| `TESTING.md`, `VERSIONING.md`, workflow, `AI_COLLABORATION.md` | Check selection, identity, Git/release and collaborator lifecycle respectively | Their governed process changes; other guides link instead of creating a second timing or check policy |
| `STATUS.md` | Small cross-project claim/evidence index, with date and exact scope | A capability/build/install/deployment claim changes; old operating combinations are labelled historical, never implied current |
| `memory/CURRENT.md` | Bounded active checkout/task route, blocker and next gate | Active work changes; remove completed detail into its evidence report, aiming below 4 KiB / 80 lines |
| `docs/design/` | Alternatives, exploratory reasoning and superseded designs | A useful hypothesis or tradeoff needs retention; proposals do not become requirements by proximity |
| `RESOLVED_ISSUES.zh-CN.md` | 重复问题的原解法、代码/消费者、证据和适用边界 | 新的重复问题或复用边界改变；不抄全部历史，也不成为当前队列 |
| dated evidence and review receipts | Exact source, method, result, failures and limits for one completed observation | Preserve original results; add a separately scoped correction/follow-up, never rewrite a failed run as a pass |

The [baseline task index](plans/BASELINE_TASKS.zh-CN.md) retains historical IDs and
unresolved obligations. CURRENT names the one active plan and its supersession;
an older execution matrix does not independently dispatch current work. Accepted
decisions belong in their owner and linked rationale history. Manifests/BOM/store/
use ledgers remain identity and data-permission owners, not prose status pages.
Do not create a parallel registry or status database for documentation convenience.

### Keeping a change aligned

For each coherent change, update the affected truth and its entry route together:

1. Before implementation, record the real need and chosen behavior in its owning
   spec/task. Retain useful rejected alternatives and the condition for revisiting
   a choice. A short paragraph is enough for a small decision; use an ADR only for
   a durable cross-cutting decision under the existing ADR policy.
2. Change code/contracts/tests at that owner. Update consumer instructions when
   their behavior changes, not merely because a workspace SHA advanced.
3. At independent review/integration, update the existing task row and evidence
   link with exact accepted scope. If a current claim changes, update STATUS;
   if the active next step changes, replace CURRENT's summary. A completed build
   removes a “build pending” statement even when installation is still pending.
4. Run the existing checks and closeout, then review semantics: do the task,
   specification, user-facing instructions and evidence agree? Name remaining
   mismatches with an owner and next gate in the existing task. Do not quietly
   leave conflicting current instructions in separate guides.

The integrating lead is the one current-entry writer; material reconciliation
receives non-author review under AI_COLLABORATION. In the code PR, update CURRENT,
the task and decisions when active state, next work or selected behavior changes.
Keep the parent consumer-visible delivery open after a source subtask finishes.
Unselected private plans remain drafts; adopted conclusions and supersession go
to these owners. Known explicit user decisions bind while docs catch up.
Raw/private evidence stays in authorized storage.
After integration, a reader starting at develop must recover the main line, current
scope, next delivery and remaining obligations without private chat.

操作说明归对应现有功能指南。[DOCUMENT_MAP](DOCUMENT_MAP.md#按我要做什么进入)按“我要做什么”路由；组件/Python地图只继续路由，不能另抄一套业务流程。一个操作段须让人或Agent知道使用条件、准确受支持入口、必要输入、结果位置、完成判断和真实失败后的处理。GUI、CLI/API映射同一业务owner；不支持的操作明确写缺口，不为未来功能造教程，也不为每个内部函数写SOP。

新实现接入或受支持入口改变时，同PR更新该操作段和导航；改变合同/理由则更新合同或DECISIONS/ADR；当前工作变化则由集成者替换CURRENT。历史解法保原证据，新的适用边界归RESOLVED_ISSUES。不要求每次同时改全部文档。收敛验收必须同时有职责、当前入口、实际操作、历史复用、接手走查与维护归属；表格登记、链接通过或文档提交本身不宣布父能力已完成。

A new idea belongs either in the owning specification as a selected decision or
in its existing plan/design section as a hypothesis with rationale and a deciding
example. Record useful engineering judgments, not the transcript. One supported
mode may be sufficient; do not multiply configurations or mandatory experiments
merely to preserve every discussion option. Review needs and actual failures before
retiring an old prescription, and preserve its artifact/compatibility history.

### One current route, one owner per fact

The lead integrating a change owns the route update in that same change: README and
DOCUMENT_MAP link durable owners; CURRENT links exactly the current task; task status
links original evidence. Do not merge code while deliberately leaving an entry that
dispatches its superseded task. A private handoff or hub page is a shortcut to this
route, not another current queue; give it a source ref/date and supersession link.
Do not generate/update private mirrors routinely when they add no user value.

When changing an owner, search inbound links and active contradictory wording;
update those affected entries, not every dated report. Historical evidence is immutable
in meaning and stays dated; old plans are labelled background/closed and cannot dispatch.
Git retains old instruction versions; do not duplicate the whole previous handbook into
a new archive each time. Large raw logs stay at their authorized source, with one useful
pointer and exact scope. A stale current sentence is fixed, not buried under an update pile.

At handoff, a fresh reader should answer purpose, current task/non-goals, exact source,
acceptance, stop/escalation and next action. Use a bounded newcomer check after material
routing changes or a real routing failure; not on every PR. If it fails, repair the entry
or missing task fact, not a mandatory full-history reading list. Machine link checks
cannot certify this understanding or prevent all future drift.

Mechanical checks cover links, commands, routing, budgets and identity consistency.
They cannot prove that a requirement is sensible or a status sentence is true.
Semantic freshness is a review responsibility, not a claim produced by `project:check`.

## Code, naming, and formatting authority

Use convention sources in this order:

1. `.editorconfig`, language compiler/type settings, package/build config, and
   deterministic checks;
2. public schema and wire-contract requirements;
3. stable neighboring code and tests in the owning component;
4. component docs/AGENTS for non-obvious invariants;
5. this prose only for cross-language guidance.

The stable repository pattern is PascalCase public C# types/members with normal
C# local naming; camelCase TypeScript/JavaScript values with PascalCase exported
types; snake_case Python; kebab-case JavaScript tool filenames; and existing
test suffixes. Wire JSON/schema vocabulary intentionally keeps its established
snake_case and protocol identifiers even when language-level names differ.

Follow the nearest coherent pattern and keep diffs narrow. Historical mixed
line endings in Connector do not authorize new drift and are not a reason for a
mass-format change. A formatter or linter earns machine enforcement only after
it catches a demonstrated recurring defect, can enter without a mass rewrite,
has low false-positive/CI cost, and does not duplicate compiler/type checks.
Retain existing language gates, including Python Ruff/mypy; do not add a new
blanket linter/formatter suite or mass rewrite merely for consistency.

| Language / material | Current authority and enforcement | Boundary |
| --- | --- | --- |
| All source | root `.editorconfig`: UTF-8, LF, final newline, whitespace and indentation | Editor configuration is not proof every historical file is formatted |
| Python | `python/pyproject.toml`: Ruff E/F/I/UP/B/SIM, line length 100, mypy; executed by `python/tools/project.py` | No claim that `ruff format` is a current mandatory whole-repo gate |
| TypeScript | owning `tsconfig.json`: strict types, unchecked indexing and filename-case checks where configured | No claim an ESLint suite is configured |
| C# | owning project/Directory.Build.props and compiler checks | Nullable, language version and warnings-as-errors differ by project; do not claim global uniformity |
| Documentation | project-system links, commands, routing and instruction budgets | Semantic freshness, clear naming and evidence scope require review |

### Terms and writing

Use Agent for the complete entity speaking a declared interaction contract; Model
for a concrete computational structure with explicit inputs/outputs, optionally
including state. One model call is not necessarily one Agent decision or one
game action. STPD names the current research implementation, not every Agent.
Clarify game Agent, engineering collaborator and Human actor when ambiguous.
Interface placement and task-management capabilities remain design decisions,
not consequences of the word Agent. Logical roles can map to reusable current
components without freezing their names or deployment topology.

Lead prose with what a user or consumer can do and what happens. Explain acronyms
on first use; prefer one precise claim per sentence. Label proposal, accepted
contract, source implementation, executed evidence and unknown separately. Give
scope, conditions and failure behavior for words such as supported, complete,
automatic and default. Keep normal, stale/rejected and unknown examples distinct.
Preserve exact wire identifiers and historical producer/schema IDs rather than
renaming them for style. Link to the owning rule, command or evidence instead of
copying status into several documents.

### Incremental adoption

The existing baseline E0 inventory maps configuration, terms, compatibility and
real drift. Each E1–E6 owning change follows current standards for new/modified
code; no unrelated formatting sweep. Module moves require a reason, consumer and
identity map, migration/rollback and scoped checks. V1/G3 review current routing,
terminology and contract consistency; release preserves archival readability.
Component-guide wording is repaired within its owning identity-aware packet.
These are activities within existing tasks, not a second set of stage IDs.

### Performance as part of interface design

For each substantive data, interaction or model change, state the relevant scale,
time and space costs: objects, candidates, tokens, relations, payloads, retained
versions, round trips, native-thread work, queues and model/training activations.
Separate measured results, arithmetic estimates and untested assumptions. Define
normal, rare-normal and stress behavior, including cancellation and honest limits.
Caching, batching, pagination and chunking must preserve the declared semantics;
information loss, changed exposure or approximate selection needs an explicit
input/Agent contract. Use targeted profiling where it answers a real unknown;
this is not a requirement to benchmark trivial edits or repeat full tests.

## Agent and Codex path

Root `AGENTS.md` contains the common hard shell and map. Connector, Host
Runtime, and Annotator retain mature local guides for their independent
game/runtime/Human boundaries. Evidence, Policy Runtime, Game Mod, Workbench,
and Live UI use the root hard shell plus focused READMEs in V1; adding local
instructions there would change path-scoped component identity without a
demonstrated routing failure. Root plus any common local chain must stay below
the 16 KiB project budget enforced by `project:check`, comfortably below
Codex's 32 KiB default project-instruction cap.

`project:context` prints paths and recommendations, not document contents. Load
only the owning component docs and a matching Skill. Ordinary implementation,
compile fixes, tests, and documentation edits normally require no Skill.

No `.codex/config.toml` is committed in V1: no stable shared setting currently
outweighs trusted-project behavior and personal model/permission differences.

## Skills

A repository Skill is a reusable, non-obvious, bounded workflow. It is not
current state, a component handbook, generic coding advice, or a one-off prompt.

Skill是agent专用的按需任务适配与非显然操作知识：给它何时进入原流程、怎样使用真实工具、需要保留什么证据及何时交还。业务事实、合同和操作SOP仍由代码与原功能指南拥有；Skill链接这些owner，只保少量真正agent特有的差异，不能成为第二SOP、业务权威或新增审批来源。是否保留/增加按真实复用价值决定，过时或重复的合并/淘汰，高频或高成本且非显然的流程可以继续使用；不以数量为目标，也不每个函数建一个Skill。

SOP是在明确职责和合同下，给当前实现组合的可执行操作方法，不是上层真理，也不只是一条help。它说明适用条件、入口/必要参数、结果位置、完成判据和实际失败/Stop/恢复；载体或实现换了就在原owner更新。人和agent共享这份事实，GUI/CLI可以各有操作映射，不复制业务含义。

Initial Skills:

- `repo-skill-maintenance`: explicit-only governance of Skill proposals and
  lifecycle.
- `platform-runtime-qualification`: implicit match only for exact
  source/package/install/load/probe/runtime qualification while preserving
  evidence non-implication.
- `platform-human-evidence`: implicit match only for Human
  preparation/capture/audit/classification with owner/Human stop gates and no
  research admission.

### Propose, admit, and create

`project:closeout` may identify a candidate but never creates one. A proposal
must give the one-line job, positive/negative triggers, inputs, output, stop
condition, recurrence evidence, and why code/test/CI/AGENTS/docs/ADR cannot
encode it more cheaply.

Admission normally needs about three independent occurrences, two repetitions
of the same high-cost failure, or an unusually high-risk workflow that is
already stable. New or substantially expanded Skill proposals use a focused governance
change and `$repo-skill-maintenance`, then bundled `$skill-creator`. A user-authorized
focused correction to an existing Skill stays in its owning task/PR; it does not
require a second admission or approval merely to repair a demonstrated mismatch. Create one job,
explicit inputs/outputs/non-triggers/stop gates, and positive/negative/overlap
trigger evals. Preserve an existing invocation policy unless the user changes it; use the bundled
skill-creator rules for new discovery metadata. Do not build a separate Skill registry.

Update a Skill only when its workflow, trigger boundary, authority/stop gate, or
required external interface changes, or a reproducible Skill failure needs a
regression fix. Do not update one for a SHA, PR, runtime session, current
blocker, or ordinary code change. Deprecate or split when repeated use proves
overlap or excessive scope.

## Mechanical correction loop

```text
AGENTS / project:context
-> task-specific canonical docs
-> optional matching Skill
-> implementation and owning tests
-> selected TESTING / check:plan gate
-> project:check
-> project:closeout
-> pull request
```

Promote lessons to the lowest-cost durable owner: bug to code/test;
machine-checkable invariant to check/CI; architecture to AGENTS/doc/ADR; current
state to CURRENT/PR/evidence; repeated non-obvious workflow to a Skill proposal.

`project:check` hard-fails broken local Markdown routes, invalid AGENTS file or
npm-command references, invalid Skill entrypoints/metadata/references, missing
declared project commands, and excessive instruction chains. It warns on
bounded semantic/freshness heuristics. `project:closeout` reports likely docs,
Status/CURRENT, ADR, contract/BOM/version, evidence/non-claim, governance, and
Skill-candidate impacts; it never rewrites semantic truth.
Before producing that report, closeout reuses governance's CURRENT rule to reject
an unsafe or oversized assembled handoff. Capacity uses the final physical UTF-8
file bytes, including CRLF, and the output shows its size and existing 8 KiB limit.
The mandatory `project:check` also includes that same rule, before later component
and research gates in the root command. Run closeout after assembling the candidate documentation and before expensive gates;
reviewing a small added paragraph does not validate the resulting whole file.

## External-tool decisions

| Mechanism | V1 decision | Reason |
|---|---|---|
| Dependabot version updates | Add | Weekly grouped npm and Actions updates target `develop`; no auto-merge |
| Dependabot security updates | Defer enablement | GitHub targets the default branch (`main`), which currently conflicts with normal `develop` integration |
| Dependency Review | Add | Public-repo vulnerability review composes into existing `portable` PR validation |
| CodeQL | Configure default setup | GitHub-managed supported-language analysis has low repository maintenance |
| Secret scanning/push protection | Keep enabled | Native protection is already active; do not duplicate scanners |
| CODEOWNERS / required review | Defer | Current collaborators do not establish reliable component-specific ownership; a gate could misroute or deadlock |
| actionlint | One-time audit clean; defer dependency | The workflow is small; add permanently only after meaningful recurring findings |
| zizmor | One-time audit clean after checkout hardening; defer blocker | Useful signal, but recurring CI-surface cost does not yet justify a required gate |
| Formatter/linter suite | Reject for V1 | No demonstrated drift class justifies a mass rewrite or duplicate compiler checks |
| GitHub Copilot/inline completion | Optional personal tool | No package, `.vscode`, or duplicate instruction file; build/onboarding/Codex never depend on it |
| MCP, scheduled AI writer, vector DB, docs site, Skill registry, Renovate, new task runner | Reject/defer | Existing repository docs, Git history, npm, and one dependency bot cover current needs |

Optional tools are revisited only against a concrete repository problem,
benefit, recurring cost, CI/runtime/context cost, false-positive risk,
permissions, overlap, and rollback.

## Health signals and escalation

This V1 should survive ordinary work without redesign. Review it before a major
release or when any signal occurs:

- repeated docs/AGENTS drift escapes `project:check`;
- newcomers or Codex repeatedly choose the wrong source or owner;
- an instruction chain approaches 16 KiB;
- Skill count/overlap causes routing confusion or frequent Skill edits;
- a new major component or authority boundary appears;
- Codex/GitHub/toolchain behavior materially changes;
- the same semantic warning recurs across several PRs.

Prefer correcting an existing doc/check or deleting stale machinery. Human
architectural judgment remains required for authority, evidence promotion, and
ambiguous product policy.
