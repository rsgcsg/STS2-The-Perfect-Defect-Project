# Project documentation and maintenance system

This document owns the lightweight repository-maintenance system. Its purpose
is to help a new engineer or Codex session find the right truth quickly and to
make cheap drift visible without creating another architecture or identity
authority.

## Source-of-truth hierarchy

When sources disagree, use this order:

1. current STS2/runtime evidence and GitHub enforcement for operational facts;
2. public contracts, manifests, `platform-bom.json`, compiler/type config, and
   deterministic tests for machine-readable facts;
3. accepted ADRs and canonical architecture/component/testing/version docs;
4. root and local `AGENTS.md` for hard invariants and navigation;
5. `STATUS.md` for current claims and exact evidence pointers;
6. bounded `docs/memory/CURRENT.md` for active work, blockers, and next gate;
7. tutorials, READMEs, PRs, and dated evidence in their stated scope.

Fix the weaker stale source. Never create a second version, component identity,
or artifact registry to make prose easier to query.

For operational facts, current evidence describes what happened; accepted needs
and contracts describe what should happen. Do not turn a source bug into a new
requirement or declare a proposed design implemented. Label unresolved conflicts
and route them to the owner before dependent work.

The short newcomer/Agent route is README -> CURRENT -> task packet -> owning
specification/component guide -> exact code/tests/evidence. The packet retains
the useful history and call-path map so the next engineer need not repeat broad
discovery. Refresh changing refs and runtime facts; do not assume a copied handoff
is still current.

## Documentation classes and triggers

| Owner | Contains | Update when / keep out |
|---|---|---|
| `README.md`, `DOCUMENT_MAP.md` | Product boundary and short routes by task | Entry points change; no current hashes, work log or copied installation procedure |
| root/local `AGENTS.md` | Hard invariants, owner routing and change loop | A durable instruction changes; no campaign budget, worker queue or acceptance timeline |
| `NEW_ENGINEER_GUIDE.md`, `NEW_MEMBER_HANDOFF.zh-CN.md` | Role-specific onboarding and links to supported procedures | Setup or user path changes; do not turn every role's checklist into every newcomer's prerequisite |
| `ARCHITECTURE.md`, `COMPONENTS.md`, accepted ADRs | Durable authority, dependency direction and significant decisions | A boundary changes; cite exact protocol owners rather than restating their schemas |
| owning specification and component contracts | Required outcomes, selected behavior, assumptions, failure/compatibility semantics and decision rationale | Before dependent implementation of a changed design; mark selected but unimplemented behavior explicitly |
| active plan/task/PR; baseline task index | Stable task IDs, dependencies, one writer, acceptance, allowed operations, resources and progress | Dispatch, design selection, review or integration changes; link evidence, do not become a second contract |
| `TESTING.md`, `VERSIONING.md`, workflow, `AI_COLLABORATION.md` | Check selection, identity, Git/release and collaborator lifecycle respectively | Their governed process changes; other guides link instead of creating a second timing or check policy |
| `STATUS.md` | Small cross-project claim/evidence index, with date and exact scope | A capability/build/install/deployment claim changes; old operating combinations are labelled historical, never implied current |
| `memory/CURRENT.md` | Bounded active checkout/task route, blocker and next gate | Active work changes; remove completed detail into its evidence report, aiming below 4 KiB / 80 lines |
| `docs/design/` | Alternatives, exploratory reasoning and superseded designs | A useful hypothesis or tradeoff needs retention; proposals do not become requirements by proximity |
| dated evidence and review receipts | Exact source, method, result, failures and limits for one completed observation | Preserve original results; add a separately scoped correction/follow-up, never rewrite a failed run as a pass |

The [baseline task index](plans/BASELINE_TASKS.zh-CN.md) owns its stable IDs and
dependencies. Its execution matrix owns active packets and required acceptance;
CURRENT routes to it. Machine-readable manifests/BOM/store/use ledgers remain the
owners of identity and data permissions. No parallel registry or status database
is created for documentation convenience.

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

The lead integrating the change owns this reconciliation; the independent reviewer
checks it. Private chat/checkpoints help execution but cannot be the only place
containing a design needed by the next engineer. Public docs retain the conclusion,
reason and reproducible route; private/raw evidence stays in authorized storage.

A new idea belongs either in the owning specification as a selected decision or
in its existing plan/design section as a hypothesis with rationale and a deciding
example. Record useful engineering judgments, not the transcript. One supported
mode may be sufficient; do not multiply configurations or mandatory experiments
merely to preserve every discussion option. Review needs and actual failures before
retiring an old prescription, and preserve its artifact/compatibility history.

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
already stable. Once admitted, open a separate governance PR, explicitly invoke
`$repo-skill-maintenance`, then use bundled `$skill-creator`. Create one job,
explicit inputs/outputs/non-triggers/stop gates, and positive/negative/overlap
trigger evals. New meta/high-risk Skills start explicit-only when required by
their policy; do not build a separate Skill registry.

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
