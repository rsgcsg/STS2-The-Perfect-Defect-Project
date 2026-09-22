# Engineering Governance

This document is the canonical guide for **how Platform engineering decisions
are made and qualified**. It complements, but does not replace:

- [Architecture](ARCHITECTURE.md) and [Components](COMPONENTS.md), which define
  the current system and authority boundaries;
- [Development Workflow](DEVELOPMENT_WORKFLOW.md), which owns Git, PR, review,
  release, and collaboration mechanics;
- [Testing and Evidence](TESTING.md), which owns executable gates and evidence
  meanings;
- [Project System](PROJECT_SYSTEM.md), which owns documentation, Skill, routing,
  and anti-drift maintenance;
- [ADR policy and index](adr/README.md), which owns durable decisions;
- [Status](STATUS.md), [Current Context](memory/CURRENT.md), and dated evidence,
  which own current or historical claims in their exact scopes.

```text
current code, contracts, native/runtime facts, exact evidence
  -> canonical docs and accepted ADRs
  -> deterministic tests, CI, and repository rules
  -> repository Skills and Agent workflows
```

Skills control repeatable workflows; they are not a second repository truth.
CI enforces mechanically provable invariants; it does not pretend to judge
whether an abstraction is good or whether a Human canary proves the right claim.

## 1. Goals and source priority

Humans and Agents start from an exact current ref, identify the fact owner before
changing code, use falsifiable hypotheses, repair the first causal defect, run
the cheapest sufficient test, preserve exact identity, and keep Git integration
from changing engineering meaning.

This governance does not require a test for every line, every test on every PR,
a large `AGENTS.md`, a Skill for ordinary work, a numeric architecture score,
or an abstraction for a hypothetical future. Proprietary STS2 files, decompiled
source, raw Human sessions, credentials, local artifacts, and model weights stay
outside Git.

Before a substantive decision:

1. Resolve the owning repository, integration branch, exact base, topic/PR head,
   open work, and overlap.
2. Read current contracts, code, neighboring tests, and canonical docs at that
   ref.
3. For game-bound behavior, inspect the exact STS2/native seam and the evidence
   level required by the claim.
4. Treat conversations, handoffs, worker summaries, old PRs, and external
   repositories as orientation until current authority confirms them.

Use this priority when sources disagree:

```text
exact current code and machine-readable contracts
  -> exact native/runtime facts and current evidence
  -> deterministic tests and GitHub enforcement
  -> accepted ADRs and canonical docs
  -> bounded handoff and historical evidence in scope
  -> conversations, worker output, external examples, general guidance
```

Fix the weaker stale source. Never rewrite stronger evidence to preserve a plan.

## 2. Change classes and required confidence

Classify by the strongest risk or claim, not line count. The required gate is the
maximum of risk, claim strength, blast radius, uncertainty, and irreversibility.

| Class | Typical scope | Minimum additional confidence |
| --- | --- | --- |
| `G0` | docs, governance, portable repository tooling | selected check plan (editorial only may use docs gate), closeout, diff check |
| `G1` | portable implementation within one owner | regression, owning component suite, selected root portable check |
| `G2` | public contract or cross-component behavior | `G1` plus contract/conformance and consumer compatibility |
| `G3` | game-native C#, Harmony, Native Foundation seam | relevant `G2`, exact-game check, clean exact build, artifact identity |
| `G4` | package, install, load, runtime lifecycle | `G3`, install, cold load, runtime verification, rollback |
| `G5` | Human origin, causal correlation, semantic evidence | `G4`, production-shaped cross-layer test, shortest Human canary, final audit |
| `G6` | cloud/infrastructure or service promotion | IaC/policy, ephemeral integration, staging/canary/observation, rollback as claimed |

A PR records its primary class and why lower classes are insufficient.

## 3. Owning fact and four kinds of truth

For every defect or proposal, identify the first incorrect or missing fact, the
layer that should own it, outer layers compensating for it, and projections or
diagnostics that may consume but not re-author it.

One fact has one authority; this does not mean one god object. Native semantic
providers own game truth, Connector owns public delivery truth, Annotator owns
Human/evidence truth, the causal tracker owns ordering and successor, and
projectors provide non-authorizing views.

Keep these categories distinct:

- **Semantic truth**: native state, decision owner, legal semantic actions,
  lifecycle, and Commit.
- **Presentation/input truth**: visible controls, modal/overlay state, input
  ownership, and UI staging.
- **Delivery truth**: exact binding, execute-time revalidation, and
  delivered/rejected/unknown disposition.
- **Evidence truth**: Human provenance, correlation, session/runtime/artifact
  identity, lifecycle witness, and durable disposition.

A generic `Ready`, `CanPlay`, or `Finished` must not silently represent several
categories.

## 4. Causal change and abstraction admission

Prefer the smallest **clean causal change**, not the smallest textual diff. A
deeper repair is smaller when it removes a family of workarounds, duplicate
state, or recurring failures.

Admit a new abstraction only when at least one is true:

1. two structurally different production use cases require the same seam;
2. several layers repeatedly implement the same high-risk fact incorrectly and
   a deeper owner is demonstrated;
3. a native or external contract already has one coherent owner;
4. local patches were falsified and the same failure family is recurring.

Before extraction, answer:

- What fact does it own, and who is forbidden from owning it again?
- Which real mechanisms prove it is not a one-off?
- Which duplicate state, adapter, or workaround disappears?
- What are its stale, cancel, ambiguous, async, overlap, and unknown failures?
- How is version drift detected, and must the public contract expose it?
- What evidence would prove the abstraction wrong?

If these cannot be answered, use `DO_NOT_ABSTRACT_YET`. Prefer a read-only or
source-test generalization probe against one or two different domains over a
premature production migration. Cross-layer reviews consider native fidelity,
stability, performance, presentation independence, version drift,
non-interference, reproducibility, and maintenance cost without fake scores.

## 5. Native-first and failure semantics

Native-first means using the deepest stable typed seam for a fact STS2 already
owns. It does not mean arbitrary private-method access or consumer-created
native operands.

```text
Snapshot -> BoundAction -> Host-local exact operands
  -> execute-time revalidation -> native delivery -> Receipt
```

Gameplay may continue when evidence is unknown, but evidence fails closed.
`unknown` is a first-class outcome. Never automatically retry an uncertain
mutation, borrow a later frame to backfill proof, or use a different Human
effect to prove an earlier action.

## 6. Testing strategy

Use the cheapest test that can falsify the risky claim, then escalate only when
the claim requires stronger evidence:

```text
source/static -> unit -> component -> contract/integration
  -> cross-layer causal -> exact build -> runtime -> Human
```

### When a new test is required

Add or update a test in the same PR when production behavior changes, a bug is
fixed, a refactor lacks a safe baseline, a public contract changes, a new
abstraction is introduced, or a CI/governance failure escaped.

For a production or Human incident:

```text
preserve the failure
  -> add the lowest-cost faithful regression
  -> fix the owning cause
  -> add a boundary/cross-layer regression when the incident crossed layers
```

The regression should catch the failure family earlier, not only one literal
incident. A separate test is normally unnecessary for prose-only clarification,
dead-code removal whose observable contract is already covered, or a change
with exact existing coverage; the PR names that coverage.

### Test shapes

- **Unit**: deterministic calculations, parsers, validation, codecs, identity
  mapping, and local branches.
- **Component**: several classes inside one owner.
- **Contract/conformance**: public providers, consumers, and external adapters
  against the same contract.
- **Integration**: an A-to-B boundary where independently green units may
  disagree.
- **Cross-layer causal**: Human/root occurrence through native identity, Commit,
  successor, persistence, and the production final auditor.
- **Exact-game**: compilation and conformance against the exact local STS2.
- **Runtime/system**: package, install, load, lifecycle, recovery, and rollback.
- **Journey/E2E**: a few critical workflows; presentation never becomes
  semantic authority.
- **Performance/load/resilience/security**: only when the change or claim owns
  that risk, with claim-adjacent baselines and metrics.

Critical lifecycle fixtures use realistic identities, ordering, duplicate,
stale, cancel, unknown, and final-auditor behavior. Simplified flags or direct
root injection cannot prove a production lifecycle they bypass.

### Where tests run

Every PR runs deterministic, portable, fast, diagnostically clear tests.
Path-conditional jobs are allowed only when routing itself is tested. Broad but
expensive deterministic suites, compatibility, flake detection, soak, and trend
measurement run scheduled or on demand. Proprietary exact-game, Human,
production-secret, destructive production, and raw-session work stays outside
public hosted CI. Release/manual gates own package/install/load/rollback,
production-like E2E, load/resilience, and Human qualification as claimed.

A flaky test is a defect. A diagnostic rerun may locate it, but a second green
result does not automatically satisfy merge. Quarantine requires an owner,
issue, replacement signal, and removal deadline. See [Testing and
Evidence](TESTING.md) for the executable matrix.

## 7. Evidence discipline

Evidence levels are ordered but not implied:

```text
source -> test -> build -> package -> installed -> loaded
  -> live mutation -> journey -> human_validated -> qualified
```

A changed source, artifact, MVID, runtime, game version, Modset, dataset, or
evaluation protocol creates a new identity unless an authoritative record proves
transfer. CI green is source/test evidence only.

Human is the final non-automatable gate, not the debugger. Before requesting a
canary, complete available source/native audit, focused and cross-layer tests,
exact build, install, cold load, automated probe, identity, and rollback. The
Human step names exact identities, actions, expected evidence, pass/fail,
rollback, and the question being answered.

## 8. Git, PRs, and review

Canonical mechanics remain in [Development Workflow](DEVELOPMENT_WORKFLOW.md).
Every PR has one primary responsibility or one indivisible causal repair, exact
base and latest head, change class, owning fact/layer, scope/non-goals, failure
model, test shape, evidence level, rollback, non-claims, public-contract and
cross-repository pin impact, and the provenance-correct merge method.

Review in this order:

1. Should the change exist?
2. Is the fact owner correct?
3. Is the architecture causal and non-duplicative?
4. Is the failure model complete?
5. Do tests represent the real risk?
6. Are identity and evidence claims honest?
7. Is the implementation maintainable?
8. Style and nits.

A rewritten head invalidates unverified CI and source-identity claims. Merge only the
latest head with its required check. TESTING permits a verified same-content execution
receipt for integration/promotion, with fresh current Git identity checks; it does not
permit borrowing ancestor proof after content changes. Component-source PRs use normal merge while the current
commit-provenance contract remains; governance-only PRs may be squashed.

## 9. Human and Agent collaboration

One local supervisor owns architecture, evidence claims and integration coordination.
Use a small number of independent workers for bounded call-path inventory, log/test
analysis, native research, fixtures or implementation after architecture selection.
The current delegation/observer policy is owned by [AI collaboration](AI_COLLABORATION.md).
No worker is required merely for parallelism; native fidelity and available local resources
limit useful concurrency. The supervisor's own changes also receive independent review.

Workers receive an exact repo/ref, bounded files, one task, non-goals, and
deterministic checks. They return facts/changes, files, checks, and uncertainty.
Worker output is not evidence: the lead re-reads source, diff, tests, refs, and
runtime records. Avoid hidden worker-to-worker chains.

One writer owns one writable branch/worktree. A handoff records repository,
branch, base/head, task/non-goals, changed files, checks, identities, Human
evidence, uncertainty, and next gate. Current repository/runtime facts override
it.

## 10. Skill governance

Repository Skills live under [.agents/skills](../.agents/skills/README.md). Use
a Skill for a reusable, non-obvious, bounded workflow with explicit inputs,
outputs, non-triggers, and stop gates. Ordinary implementation, tests, docs, and
architecture review read canonical docs directly.

Admit a Skill after roughly three independent occurrences, two repeats of the
same high-cost workflow failure, or a stable high-risk workflow whose stop gates
justify early capture. Prefer code/test for behavior, CI for machine invariants,
docs/ADR for durable rules, Status/evidence for mutable claims, and a task/PR for
one-off work.

Update a Skill only for workflow, trigger, authority, stop-gate, tool-interface,
or reproducible Skill failure changes. Never update it for a SHA, PR, session,
artifact, temporary blocker, or ordinary refactor.

## 11. External dependencies

Start with the Platform problem and contract, not an external feature list:

```text
our problem -> our contract -> pinned candidate -> adapter/spike
  -> same conformance suite -> cost/risk -> adopt/partial/reference-only/reject
```

Evaluate exact version/SHA, license, maintenance, security and supply chain,
dependency graph, runtime patching, configuration fingerprint, compatibility,
performance, fallback, and rollback. Count brittle Platform seams actually
removed. A feature-rich project that does not replace enough exact action,
identity, lifecycle, Commit, or successor seams remains reference-only. Keep
external types at an edge adapter, not in Core or the public contract.

## 12. Cloud and service evolution

Keep application truth, IaC truth, deployed cloud state, and runtime observation
distinct. A successful plan does not prove a usable service.

```text
IaC/source -> lint/unit/policy -> plan -> ephemeral provision
  -> integration -> staging -> smoke/E2E -> performance/resilience
  -> canary -> production observed -> qualified
```

Use isolated namespaces, synthetic data, TTL/budget controls, and guaranteed
cleanup. Build and fingerprint once, then promote the same immutable artifact.
Untrusted PRs receive no production secrets; prefer short-lived identity,
protected environments, approval for destructive operations, clean runners,
redacted logs, canary exposure, automatic halt, and rollback.

Keep cheap checks on PRs, broader integration at merge/staging, full regression
or compatibility on scheduled lanes, load/chaos/security on demand or release,
and production verification behind canary and SLO guards.

## 13. Enforcement and incidents

Govern changes and claims, not people:

- **L0 machine reject**: deterministic CI/ruleset violation.
- **L1 fix plus regression**: repair the owning cause and encode the escaped
  invariant at the cheapest faithful gate.
- **L2 merged defect**: stop stacking on bad integration; revert or narrowly
  fix-forward, invalidate affected artifacts/evidence, and restore CI.
- **L3 repeated failure family**: stop surface patches; require contract or
  architecture review, a canonical rule, and cross-layer/generalization test.
- **L4 repeated process bypass**: add targeted approval, CODEOWNERS, merge queue,
  or stronger branch restrictions when team growth or evidence justifies them.
- **L5 evidence/security incident**: stop propagation, rotate/revoke, invalidate
  false claims, preserve an incident record, and add a guard. Never silently
  rewrite historical evidence.

Postmortems are blameless and ask which system condition allowed the error.

## 14. Health signals and definition of done

CI green is necessary, not sufficient. Review trends in PR lead time/stale age,
latest-head CI duration/flake, post-merge breakage/revert rate, escaped
cross-layer defects, repeated failure families, stale-evidence misuse, Human
gates discovering automatable defects, Skill false triggers/overlap, and any
`unknown` promoted to success. Metrics improve the system; they do not rank
people.

Before recommending merge, confirm exact repo/base/latest head and overlap,
change class and owning fact, no duplicate authority or hidden retry/backfill,
faithful regression and lowest affected suite, selected root/latest-head remote gate,
higher evidence only when required, contract/pin/identity/docs/ADR/Skill impact,
rollback, non-claims, and provenance-correct merge method.

Governance succeeds when expensive defects are blocked at cheaper gates without
turning normal development into ceremony.

## Appendix: review and handoff records

An architecture review records problem/exact grounding, facts and falsifiable
hypotheses, owning fact/layer, abstraction/native-seam admission, failure model,
tests, evidence level, decision (`KEEP`, `DELETE`, `EXTRACT`, `PROJECT`,
`REPLACE`, or `DEFER`), rollback, and non-claims.

A Human or Agent handoff records repository, branch, base/head/PR, task and
non-goals, current exact truth/authority, changed files/checks, build/runtime or
Human identity, uncertainty, and next gate, ending with: current repository and
runtime evidence override this handoff.

An external-dependency review records the problem/Platform contract, exact
candidate/version/SHA/artifact/license, maintenance/security/supply chain, same
conformance result, Platform seams removed, adapter/config/runtime/performance
cost, fallback/rollback, and verdict (`ADOPT`, `PARTIAL`, `OPTIONAL`,
`REFERENCE_ONLY`, or `REJECT`).

## Small-team delivery principles

Ordinary work completes at reviewed develop integration, unless the task explicitly
includes publication or deployment. Main is the formal publication source, not the
running-service pointer. Batch compatible changes for an intentional release; keep
unchanged artifacts and accepted installations pinned. Do not rebuild the system
for an unrelated source or documentation edit.

Use one existing authority per fact: game/native semantics, Connector contracts,
Annotator evidence, Hub membership/operations, STPD research admission, immutable
release composition and actual deployment receipts. A shared repository does not
merge those responsibilities. Prefer existing owner APIs over a parallel ledger,
compatibility registry, retry tracker or another service.

Choose tests by risk and dependency, not line count. Behavioral bug fixes need the
lowest-cost regression that fails on the original bug. Public changes need producer,
consumer and error-path coverage; ordinary prose corrections need existing document
checks, not new tests mirroring prose. Alter expectations only when the intended
contract changes, explain why and retain archival consumers. Failed evidence stays
failed; classify environment failures separately from assertions.

The check planner is conservative automation, not permission to skip native,
Human, security, research or release requirements. Changes to its own logic,
contracts or governance take the full route. REVIEW the dependency effect when
shared console, locks, identity or packaging changes. Unknown impact means full.
