# Platform Project System

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

| Owner | Contains | Update when |
|---|---|---|
| `README.md` | Durable zero-context boundary and routes | Product/component map or primary route changes |
| `NEW_ENGINEER_GUIDE.md` | First-day tutorial | Supported setup or first-change path changes |
| `ARCHITECTURE.md`, `COMPONENTS.md`, ADRs | Durable authority and dependency direction | Accepted architecture changes |
| `TESTING.md`, `VERSIONING.md`, workflow | Stable evidence, identity, Git, PR, release process | Their governed process changes |
| `STATUS.md` | Current exact claims and evidence index | A claim or exact evidence boundary changes |
| `memory/CURRENT.md` | Short active work, blocker, next gate | Active work changes; remove completed detail |
| dated evidence and PRs | Exact historical proof and review record | Never rewritten to imply transfer |

Durable docs do not list current topic branches or reproduce evidence
timelines. CURRENT points to the PR/report instead of copying it. Historical
evidence remains searchable but is outside default newcomer/Codex context.

The [baseline task index](plans/BASELINE_TASKS.zh-CN.md) owns its stable IDs and
dependencies; CURRENT owns only the active packet and next gate. Design proposals,
accepted norms, implementation status and historical evidence have separate labels.
Update affected routes/consumer documentation with the change and link to the fact
owner instead of copying status strings. Existing project checks catch mechanical
drift; semantic freshness and truthful status remain review responsibilities.

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
V1 retains `.editorconfig`, compiler/type checking, and tests as style
enforcement.

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
-> doctor / root check
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
