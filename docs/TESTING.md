# Testing And Evidence

This document owns test design, creation, maintenance, execution selection and
evidence meanings. [WORKING_RULES](WORKING_RULES.zh-CN.md) owns shared work practice;
the task owns its outcome, authority, cost window and stop boundary. Tests are part
of development: they shorten feedback, make changes safer and expose broken
agreements. They are neither a separate phase after "development complete" nor a
substitute for a useful product. Developers maintain tests with the behavior.

## Use this guide for the current decision

Start with the test lifecycle below. Consult only the relevant details:
[fixtures](#faithful-fixtures-and-test-shapes),
[local feedback](#local-feedback-and-candidate-checks),
[CI selection](#hosted-ci-contract),
[focused commands](#focused-portable-checks), or
[exact-game/runtime gates](#local-exact-game-and-runtime-gates).
This is a reference, not a mandatory full reading or execution list per task.

The root suite is the portable source/package gate. It does not establish a loaded
Mod, successful gameplay, Human origin or strategy quality. Reuse dependencies;
`npm ci` is setup, not a test to repeat on every visit. Use `npm run project:context`
for routing, `npm run project:check` for repository rules and
`npm run project:closeout` for handoff impact. The latter does not certify semantics.
`npm run check` runs the full suite; the committed-diff selector below owns final
required execution. A locally checked draft is not necessarily merge-ready.

## Test design and maintenance

```text
user/consumer outcome -> task acceptance -> changed guarantee and owner
  -> cheapest faithful check -> implementation and feedback
  -> selected candidate gates -> bounded delivery/evidence
```

Requirements say what is wanted; tests are executable checks of selected examples
and invariants, and can encode mistakes. Passing tests does not redefine the goal.
A changed failure requires diagnosis: product, fixture/assertion, environment or
requirement may be wrong. Keep the original observation and repair the actual cause.

Use the existing task/PR acceptance field, not a separate test plan or ledger, to
name the observable guarantee, useful normal case, relevant counterexample,
existing/new coverage and unproved claim. Choose by impact and plausible failure;
not every edit needs all duplicate/stale/cancel/security combinations. A new
capability or bounded experiment is legitimate without a historical failing seed.
For a bug, first reproduce it cheaply where possible; preserve real source/seed/
operation when relevant, without requiring a whole game for a local parser error.

### Add, change or retire coverage

| Situation | Treatment in the same task/PR |
| --- | --- |
| Meaningful changed behavior, escaped bug or public contract | Add/update the cheapest faithful regression where existing coverage is insufficient. For cross-owner changes test the real provider/reader seam; do not duplicate the same assertion at every layer. |
| Exact existing coverage, editorial change, trivial reversible edit or dead code with covered behavior | Reuse and name existing coverage, or explain the simple inspection. Do not invent a test merely to increase counts or mirror the diff. |
| New test | State which plausible failure it detects, why current coverage misses it and who maintains it. Prefer observable outcomes over private structure, mocks calling mocks or exact prose. No universal coverage percentage or fixed unit/integration/E2E ratio. |
| Refactor or semantic change | Preserve externally required behavior; change obsolete expectations only with the intended contract and affected consumer evidence. Characterization of a bug is not a permanent requirement. |
| Duplicate, obsolete, slow or brittle test | Inspect affected current/supported archival consumers and unique coverage, not the entire history. Consolidate, replace or delete it with a reason and retained signal. If the guarantee itself is retired, record that decision and consumer impact; no equivalent replacement is needed for a genuinely removed requirement. |
| Fixture, timeout or tolerance change | Diagnose and measure the affected phase. Do not weaken a product deadline or assertion to get green. Test harness allowances may be corrected with evidence that the actual guarantee remains checked. |
| Flaky or environmental failure | Retain attempts and classify the cause. One diagnostic rerun may distinguish hypotheses; repeated reruns until green are not a fix. A green rerun alone does not erase an unresolved required failure. |
| Temporary quarantine | Record owner, lost signal, substitute evidence or explicit gap, revisit condition/date and review in the existing issue/PR. No silent skip, automatic merge exception or permanent unowned quarantine. Existing required gates remain binding. |

The behavior owner owns its tests, fixtures and relevant consumer contract cases;
the maintainer of CI/runner tools owns scheduling and diagnostic infrastructure.
The lead checks whether the claimed outcome is actually supported. This is role
ownership, not a separate testing department or mandatory extra reviewer per test.

Maintain tests when the behavior changes, an escaped defect appears, or actual
flake/runtime/maintenance cost becomes material. Consolidate one failure family
instead of preserving every incident as another expensive fixture. Repeated
failures prompt a bounded check of the common assumption; they do not automatically
justify a new architecture, policy or test framework. No periodic full-suite cleanup
project is imposed by this guide. Old evidence remains intact even when obsolete
executable tests are removed; preserving evidence is not perpetual API support.

A test change is useful if it increases confidence in a needed guarantee, catches a
real failure earlier, or reduces recurring cost while retaining needed detection.
Test count, coverage, green runs and tokens spent are not product progress.
Review value using observed feedback time, useful failures versus false alarms,
escaped defects and maintenance effort from existing logs; no new metrics service.

### Faithful fixtures and test shapes

Critical lifecycle fixtures preserve realistic identity, ordering, duplicate,
stale, cancel, unknown, persistence and final-auditor behavior. Declare mocked
seams and claims they cannot prove. A stub, simplified flag or direct root
injection can isolate a branch but cannot establish the production lifecycle it
bypasses. Use production serializers or shared versioned contract fixtures at
cross-owner seams, then exercise the real reader and verifier.

Expensive immutable producer output may be built once and copied through the
existing artifact transfer API into private stores per test. Preserve original
bytes, identities and lineage; prove that damaging one copy leaves the baseline
and another copy valid. Do not share writable databases, open handles, lifecycle
state, controllers, process registries or mutable caches. Tests of construction,
startup, migration or recovery still need a fresh instance when that is the risk.
`python/tests/test_token_comparison.py` is an existing producer-to-comparator
example with private copies and an isolation regression, not a claim about all
fixture families or hosted performance.

| Shape | Appropriate risk |
| --- | --- |
| Unit/static | deterministic calculations, parsers, codecs, schemas, bounds, identity mapping and local branches |
| Component | collaborating classes and actual lifecycle/files/process cleanup inside one owner |
| Contract/conformance | public providers, consumers and external adapters against the same versioned contract |
| Short integration | a real A-to-B seam where independently green units may disagree |
| Cross-layer causal | root/native identity through Commit, successor, persistence and the production final auditor |
| Exact-game | compilation and conformance against the exact local STS2 |
| Runtime/system | package, install, load, lifecycle, recovery and rollback |
| Journey/E2E | a few critical workflows; presentation does not become semantic authority |
| Performance/load/resilience/security | a risk owned by the change, with claim-adjacent baselines and metrics |

## Local feedback and candidate checks

### Fast feedback without repeated assurance work

During development use the smallest faithful feedback loop above. Reach a short
real consumer path early for a cross-owner/product change, once its necessary
safety/build prerequisites hold; local unit green is not proof of that connection.
A few representative journeys complement cheap checks, not an E2E run for every
edge case. Debugging repeatedly on the same seed is regression work, not an unseen
strategy evaluation. Freeze one stable candidate for its selected final gates.

Broaden or repeat checks only for a new change, failure, uncovered risk or a required
gate. Repeated failures call for diagnosis of the owning fact and test fidelity,
not assertion weakening or unrelated fixes. Existing selection, exact-tree receipt
reuse and higher evidence gates below remain mandatory; this rule does not permit
skips, copied green results or changing CI routing to make a task cheaper.

Before freezing a candidate or launching expensive gates, run `npm run project:closeout`
against the final assembled documentation. It reuses the existing CURRENT safety and
physical UTF-8 capacity rule, prints the actual size/limit, and rejects violations.
An individually small added paragraph is not evidence that the whole handoff fits.

### Small producer-to-reader checks before final dispatch

For a cross-owner incident, first establish one short normal/error path through
the actual producer and consumer. Generate critical DTOs and records with the
production serializer/owner or a shared versioned contract fixture. Feed those
original bytes to the real SDK, application reader and final verifier as needed;
do not independently handwrite the supposedly matching final/report family.
Declare every mocked seam and its unproved claim. A synthetic Host or promotion
fixture does not establish game legality, native origin or production admission.

Keep these decisions in the existing task packet: first incorrect fact/owner,
exact dependencies, the risky claim and counterexample, the test's real/mocked
seams, and its completion boundary. Source review and the short boundary check
precede a frozen full candidate. Independently green leaf tests alone do not
establish their joined contract. No additional manager, ledger or test framework
is required for this procedure.

On failure, retain the original outcome and useful bounded diagnostics: source,
stage/operation/iteration, producer readiness, actual child exit, original error
and reader disposition. A test must show why it failed before removing temporary
state. Repair an observed cause, then repeat affected checks for the changed
source; do not hide the failure by normalizing protocol bytes, raising deadlines,
reducing pressure/coverage, skipping cases or retrying an unknown native mutation.
Process startup readiness and a request deadline are different observations.
An OS name or a termination offer does not establish an exit tuple: compare the
receipt with an independent observation of the original child/PID/close event;
an unconfirmed exit stays unconfirmed. Text fixtures and source readers use their
declared encoding explicitly, and shared readers are exercised with non-ASCII
content before broad tests rather than relying on the developer's locale.

Distinguish the test runner's whole-fixture timeout from a product deadline or
performance promise. Startup, durable fixture writes and independent final
verification need their own bounded completion allowance. Correcting an
unfounded runner cap requires phase measurements, an unchanged owner deadline
and pressure/storage/durability assertions, plus a negative proving the original
owner timer still fences work. Retain the old timeout and its unknown phases;
label the correction as test strategy, not a performance repair or measured
cross-platform guarantee. Never extend a product deadline to make a test pass.

Before the final dispatch, mechanically check the assembled files and identity
tuple, independently review the complete affected producer/caller/consumer diff,
and freeze the exact candidate. Run its selected gates once, preserving failure,
cancellation and skips. A new required repair invalidates the affected verdict;
follow the owner's declared stop boundary rather than silently starting another
complete experiment. TESTING's existing receipt reuse and evidence ladder remain
the owners of any later integration or stronger claim.

Parallelize independent cheap checks when useful. Avoid duplicate heavy local and
hosted runs that prove the same thing; retain the required local native/runtime
checks. The [passive-wait checkpoint](AI_COLLABORATION.md#five-minute-passive-wait-checkpoint)
changes how the engineer waits, not the execution or acceptance status of a job.

### Measure cost and use safe concurrency

Measure command and phase duration, including collection/setup, producer/build,
assertions and teardown, before choosing a speed repair. Use existing workspace
stage summaries, Python command timings and pytest/JUnit duration diagnostics;
report exact source, OS/tool versions, warm/cold state and fixture size. Optimize
the measured bottleneck without weakening normal/error assertions, pressure,
lineage, lifecycle isolation or cleanup. Local timing does not predict hosted
Linux or Windows performance. There is no universal test percentage or timeout.

Fast targeted feedback and earlier boundary failures are engineering aspirations,
not promised durations. Hosted pytest partitioning below is the selected scheduling
policy; other new lanes still need their own review. Reduce duplicate setup and
unchanged reruns first; broader sharding, new routing or gate movement needs its
own reviewed policy/code change. Broad compatibility, flake/soak and trend checks
may use existing scheduled/on-demand gates where applicable; required PR checks
are not moved there by this recommendation.

Cheap independent read-only checks may run concurrently after their prerequisites.
Concurrent executions need disjoint writable worktrees, environments, temporary
state, stores, processes and ports; reserve shared writers explicitly. Never
rebuild/delete generated SDK or Runtime `dist` while a consumer reads it. The
lead (Root) coordinates the sole heavy local/game/integration/BOM writer slot under
[AI collaboration](AI_COLLABORATION.md#roles-and-actual-access); available workers
do not imply available machine resources. While gates run, continue independent
authorized review or work without mutating the frozen candidate or its inputs.
A dependent consumer waits for the actual contract/build/evidence prerequisite,
not a queued job, stale generated output or another branch's green status.

A bounded local runtime diagnostic may overlap isolated remote CI on the same
frozen candidate once its relevant source review, short-seam checks, local exact
build/artifact, install/load, rollback and stop prerequisites pass. Match this
ordering to the diagnostic's claim and authorization; it is not a blanket waiver
of a task or owner dependency. Do not bypass a failed relevant gate or unknown,
or rebuild local generated output while the runtime consumes it. Selected full
selected Linux portable checks remain required for merge, portability and stronger
promotion. This scheduling rule grants no additional native attempt, Human
recording, training, spending or deployment authority.

## CI responsibility and cost decisions

CI is the shared, repeatable integration check for a submitted candidate. It
establishes that the selected source/contracts/tools work in their declared clean
environments. It does not decide product priorities, prove gameplay/learning,
replace developer investigation, or require full execution after every edit.

| Work stage / trigger | Action and stop boundary |
|---|---|
| Local investigation/edit | Run the cheapest faithful affected test or short consumer path. No remote CI for every intermediate edit; no new workflow for a scratch probe. |
| Stable PR candidate | Run the committed-diff selected scope once. CI covers the actual owner and affected consumers; review addresses requirement/policy meaning that execution cannot prove. |
| Cross-owner/shared input or product CI change | Full when the known smaller scope cannot cover affected consumers: shared contracts/build/locks, Platform changes, deployment or changed product-job environment. Broad impact, not document importance, is the reason. |
| develop integration | Verify its actual diff/result. Reuse only the existing verified identical-content receipt rules, not an ancestor's green label. |
| Release/executable promotion | Validate the affected deliverable and compatibility; main Python executable promotion remains full. Add build/install/runtime/Human/evaluation evidence only for the actual release claim. Docs do not require reinstalling the game. |
| Periodic/manual wider check | The existing schedule/manual full route detects drift and retains broad coverage. It is not another run on every unchanged handoff, nor a substitute for affected PR checks. |

The task names an expected feedback window and expensive-run limit. At that
checkpoint, the lead asks whether the selected work can expose a failure caused
by this change, whether setup is duplicated, and whether the runner is progressing.
An unjustified full selection is a test-strategy defect to resolve within the
relevant task, not an immutable safety rule or an excuse for passive polling.
Check actual job stages once; do not confuse normal execution, a hang, unknown
status and failure. Cancel owned obsolete runs after preserving their status;
cancellation is not success. Reassess before rerunning, extending deadlines or
adding workers. Do not defer an obvious scope mismatch solely to preserve a
self-imposed packet boundary. Keep corrections limited to the actual cause.

Measure feedback/setup/verification cost from existing results when it matters;
no dashboard or cost ledger is required. A few-minute repository check is a target,
not a fabricated guarantee. A long full product suite may be justified by its
change; a management edit must explain why it would need that suite. Changes to
required scope are reviewed code/policy changes, never manual silent skips.

Long local gates use the [workstation capacity SOP](../python/docs/DATA_LIFECYCLE.md#workstation-capacity-before-long-tasks) before spawning and before each stage. The wrapper checks the workspace and actual temporary filesystem, not an old `df` snapshot; direct Python full checks use the same owning storage check. During live evaluation/training the lead must budget overlapping writes or keep the heavy slot serial. A space refusal is a blocked execution, never a green test or evidence of runtime failure. The check is not a filesystem reservation.

## Hosted CI contract

GitHub-hosted CI is intentionally a **source/test portability gate**, not an
exact-game or runtime qualification environment. PR checks are deterministic,
portable and diagnostically clear; path-conditional selection requires tests of
the router itself. Proprietary exact-game, Human, production-secret, destructive
production and raw-session operations stay outside public hosted CI. Release or
manual qualification retains package/install/load/rollback, production-like E2E,
load/resilience and Human gates when the claim requires them.

The workflow always starts a `plan` job. The same local router is available as:

```bash
npm run check:plan -- --base origin/develop
npm run check:plan -- --base origin/develop --run
```

The plan job checks the committed event-base-to-HEAD patch before receipt lookup
or routing outputs, so bad patch hygiene blocks docs, repository, Python, full and reuse lanes.
Local `--run` uses the same barrier. An absent or all-zero event base checks the
selected head's first parent, or the empty tree for an initial commit; invalid
supplied revisions fail before execution. Scope/event rules remain unchanged,
and lane worktree checks still cover changes made during the checks.

The router uses actual committed Git changes, not a caller-selected test list:

| Scope | Selected checks | Eligible changes |
|---|---|---|
| `docs` | fresh repository/link/governance/identity/BOM/boundary/history checks on Linux | explicit regular editorial/management files, root AGENTS and existing report surfaces; human review judges policy meaning |
| `repository` | complete repository/tooling checks on Linux, with locked Node dependencies | explicit project-system/governance/check-planner/workflow-validator tools, their tests and management companions; narrowly verified workflow routing/setup edits |
| `python` | repository guards plus the entire Python gate on Linux | only Python application/research/test owners, optionally declared prose/management companions |
| `full` | complete root gate on Linux | Platform, shared contracts/locks/build inputs, product-job CI changes, deployment, unknown executable paths or uncertain Git state |
| `reuse` | fresh repository checks referencing a verified executed receipt | eligible integration/promotion with identical content and check definition |

The planner owns the exact finite path lists. Regular additions/modifications
(mode100644) of named management docs/templates are not executable product changes.
Nested AGENTS/SKILL, ADR/contract/component docs, unknown paths, deletions outside
the existing Python route, symlinks and type/mode changes still expand to full.
Changes to repository tools run their actual cross-platform tests, not the Python
training suite merely because their filenames start with `check-`.

A workflow change takes repository scope only when its trigger/header, plan job,
Linux product job, Windows product job and Windows checklist job are unchanged, all job names are known,
and the resulting workflow passes the workflow contract. Thus only the existing
repository/docs setup, repository lane and aggregate wiring may differ. Product
commands/environments, partitioning, dependencies, unknown workflow structure or
trigger changes retain full. This narrow check is not a general YAML impact engine.
New routing/guard behavior requires relevant positive/negative regressions and
non-author review; a label alone is not evidence. `docs` and `repository` produce
no new full execution receipt. Failed/cancelled/missing selected work cannot pass.
Both small lanes install locked Node dependencies: AST checks require TypeScript
and cannot rely on packages left in a developer's workspace.

Prose companions do not hide source changes. Mixed ownership chooses the stronger
applicable lane or full when no known scope covers it. The local and hosted planner
use the same committed diff, with the original patch-hygiene barrier. Explicit
`--full`, manual dispatch and the existing periodic full check remain available.

The full and Python scopes also run `npm run check:s0`: the shared SDK and Runtime
builds, bounded S0 runner and native Source3 collector lifecycle tests, and offline
capsule/Runtime dataset joins.
They run before the long Python suite so a local consumer/fixture failure does
not wait behind unrelated training and application regressions. Both gates still
run in full; this ordering grants no test skip or previous-head reuse.
These use synthetic protocol fixtures and never start a game or train on real
records. The S0 consumer tests live under `tools/test`, so Python's normal
`tests/` discovery alone does not cover this cross-owner seam.

The shared consumer precheck builds the Connector SDK and then Policy Runtime, so
its real Agent-session tests also run from a fresh Python-scope checkout without
depending on a previous full component build.

### Execute by dependency and report every selected owner

`tools/check-workspace.mjs` retains the existing full/Python component inventory.
The dependency order is:

```text
repository/identity guards -> Connector SDK build -> Policy Runtime build
  -> actual short S0 normal/error consumer checks -> broader selected owners
```

Dependencies are success conditions, not merely execution order. The current
runner executes stages sequentially; this DAG explains which consumers must wait
and which independent results remain useful, without claiming parallel scheduling.
It runs the short S0 producer/consumer checks before the broad component/Python
suites. A failed repository prerequisite blocks later stages. A failed generated-client
build blocks its S0, Runtime and Python consumers; unrelated owner
checks still execute. An Annotator or S0 test failure no longer hides independent
Evidence, Runtime or Python results. S0 and Python read that generation before
component checks that rebuild SDK/Runtime output; S0's existing test body is run
without invoking its build prehook a second time. Workbench's diagnostic tests are
independent of those generated outputs. It never starts native actions.

The summary distinguishes passed, failed, cancelled and blocked, with exact
commands, exit codes/signals, durations and initial/final Git identity. Full and
Python runs require an unchanged clean candidate. Focused component runs may use
dirty development source, which remains explicitly recorded and cannot become a
frozen full result. Cancellation prevents subsequent execution; any required
failure or blocked stage keeps the aggregate failed. Cancellation terminates the
owned process group/tree and reports unresolved cleanup as a failure, rather than
killing only an npm wrapper and leaving its descendants running. This changes diagnostic
collection, not the selected gate requirement, supported scope or scientific claims.
CI preserves `.local/checks/workspace-*.json` alongside pytest results, including
on failure; synthetic fixture diagnostics retain bounded phases, whitelisted operation
fields and stacks without locals under `python/.local/fixture-diagnostics/`, with no
environment/configuration dump or default MSBuild binlog upload. The GitHub summary lists each reached or blocked stage. The existing
successful execution receipt remains a separate artifact and is never generated
from a failing diagnostic summary.

Choose layers by the claim being falsified:

| Layer | Cheapest useful evidence | What it cannot establish |
| --- | --- | --- |
| Static/unit | schema, calculation, bounds, identity and a local negative | actual cross-owner agreement or native semantics |
| Component | real owner lifecycle, actual files/processes and cleanup | other independently implemented readers |
| Contract/short integration | real producer bytes through the real SDK/reader, normal and negative paths | mocked native execution, lossless exposure, loaded identity |
| Exact build/install/load | current game compilation and exact bytes admitted/observed in the owning runtime | successful continuous gameplay or learning |
| Bounded native journey | one declared real attempt, full failure/stop/accounting and measured runtime | exhaustive mechanisms, Human origin or scientific usefulness |
| Wider qualification | declared coverage, recovery/performance/product/data/model comparisons as needed | guarantees outside its fixed identity, scope and method |

For timing and numeric claims, record the relevant environment and use measured
budgets. A seed is not a universal promise of cross-platform tensor bytes; an
immutable artifact still requires exact byte integrity on every reader. A
requested stop is not proof of graceful termination or a durable checkpoint.
If a test assumes either promise, verify that promise in the owner contract
before adjusting its expected value. Retain the observed mismatch when correcting
the scope of an invalid expectation.

The Python scope still covers installed Platform consumers, application/research tests,
SDK contracts, typecheck, CPU E2E and packaging. Platform never imports Python applications;
repository boundary guards run in both scopes. This is owner-level routing, not yet a
per-test UI/research selector. Windows runs separately as described below. Renames use Git's no-renames A/D
representation: both paths must qualify. Type changes, dirty worktrees and unresolved
refs fall back to full. Local uncommitted regression is useful, but final routing uses a
clean committed diff. `npm run check` always runs full.

`portable` is the required aggregate: plan and all selected Linux/docs/repository jobs must succeed;
unselected jobs must be skipped. Failure/cancellation never becomes a PASS. A Python,
docs, repository or reuse result is labelled as such, not as a new full Linux execution.

The local diagnostic scope may be smaller than merge or release scope. The current
router, every selected leaf and `portable`, plus any stronger active-task
prerequisite, remain binding until a separately reviewed policy/code change alters
them. Management-only and repository-tool changes use the reviewed scopes above;
product PRs retain their full/Python inventories on Linux.
Choosing a relevant scope is not deleting a test or claiming unexecuted coverage.

### Windows cadence and deferred adaptation

Windows 每天检查一次，发布前检查一次，不是 PR 的门。
User decision:2026-10-17. `portable` depends on plan/docs/repository/Linux only.
Current main/develop branch protection requires only `portable`; its name is unchanged,
so no GitHub protection setting change is needed. Windows runs daily at21:17 UTC
(07:17 Australia/Brisbane the next day), and on PRs/pushes to main. Before a release,
run CI via workflow_dispatch with `windows=true` on the exact candidate and inspect
both Windows shard results. Linux can finish independently. No automatic retries.
The schedule becomes active when this workflow reaches the repository default branch.

`windows-failures` reads original Windows job logs and carries one cumulative
checklist as the90-day `windows-failures-history` artifact plus its job summary:
test/failed gate, first/last observed Brisbane date, repeated occurrence and run count.
A failed/cancelled job remains visible even if it produced no test report. History
starts with the first run of this checklist, carries forward while a prior artifact
is available, and keeps failures after a later passing day. Infrastructure/API errors
fail the summary visibly; the summary never changes test outcomes or blocks `portable`.
Download the checklist before a90-day hiatus if that history must be retained.

Windows support scope confirmed by the user on2026-10-17: the local-use and
collection chain below. No new adaptation work is included here:

| Candidate use | Existing grounding |
|---|---|
| Game Mod and ingame UI | `apps/game-mod/README.md`: Windows Mod settings preservation; `components/annotator/tools/workstation-platform.test.mjs`: Windows install/settings tests |
| Connector/SDK and Host Runtime | `components/connector/tools/connector.mjs`: Windows command/path handling; `components/host-runtime/README.md`: experimental Windows route and retained, separately qualified build tuple |
| Annotator, Evidence, upload/download and local Workbench | `components/annotator/tools/workstation-platform.mjs`: Windows paths/settings; `python/docs/B_PIPELINE_HANDOFF.md`: `%LOCALAPPDATA%` Workbench configuration; `python/spireagent/workbench/instance_lock.py`: Windows locking |
| Policy Runtime and external Agent integration | `components/policy-runtime` consumes the same Connector/Host APIs in the existing Windows source/test lane |

This confirmed support scope is not native Windows qualification. Windows Hub hosting,
research/training and new platform targets are not added by this CI change.

### Hosted pytest partition and complete execution proof

Full and Python scopes use two isolated runner leaves, with matrix
`fail-fast: false` and finite per-leaf job limits: Linux55 minutes, Windows75.
Shard1 runs repository guards, S0 consumers, nonpytest Python checks and the selected
component-owner inventory once per OS. Shard2 runs only its complementary pytest
selection. Each isolated runner builds its own SDK/Runtime output for real Python
consumers; generated output is not shared between runners. Local `npm run check`
and Python `check` defaults remain complete and unpartitioned.

Each top-level invocation explicitly loads `tools.pytest_shard` with shard
index/count and a manifest path. It collects the unchanged default testpaths,
normalizes relative POSIX node IDs/files and assigns whole files using the static
`python/tools/pytest-file-costs.json` profile and `lpt-whole-file-gates-1`: descending
integer cost, UTF-8 file tie order, then the lower-load shard (ties shard1).
Shard1 starts with its measured non-pytest gate overhead; shard2 starts at zero.
Parameters, classes and module fixtures stay together. New files join automatically
with a nonzero 30,000 ms scheduling weight; retired profile entries do not select tests.
The reviewed profile uses whole-file testcase milliseconds from retained Linux
JUnit reports (median if multiple runs), plus shard1 non-pytest overhead from the
same run logs. These are scheduling observations, never successful receipts.
Invalid/missing profiles fail closed; weights are not deadlines or performance promises. No manually curated test list, `PYTEST_ADDOPTS`,
automatic inherited environment plugin or conftest filters nested pytest.
The Modal fresh child and other nested invocations keep their full case sets.

Unique OS/shard/attempt artifacts retain the original complete collection,
selection, actual setup/call/teardown and subtest outcomes, terminal execution,
source identities and workspace results. Before sealing a fresh receipt,
`portable` downloads the two Linux current-run artifact directories separately and
verifies clean exact head/tree/workflow/selector and cost-profile Git blob/raw SHA256
plus routing algorithm, run/attempt/scope/OS, identical
same-OS collection, disjoint/exhaustive deterministic selections, and terminal
executed IDs matching every selected item. Existing test-level skips remain
explicit; a missing, duplicate, empty, wholly skipped, failed, cancelled,
uncompleted or foreign-source leaf cannot pass. All selected owner stages must
also pass. Manifest/coverage schema v2 binds this routing definition; Python and JS
independently recompute whole-file assignments from the current complete collection.
A profile change is a source-tree change, so an old tree receipt cannot cover it.
The small existing execution receipt adds only coverage digests/counts/routing identity;
original manifests remain diagnostics. Receipt eligibility, freshness, source
binding, higher evidence gates remain unchanged; required PR coverage is Linux.

This changes scheduling, not coverage or deadlines. Historical timing estimates
are predictions: duplicated session fixtures, collection, setup and other gates
can change the result. Measure real leaf/aggregate duration and runner cost before
claiming a speedup or that the candidate fits its budget.

### Integration receipts instead of repeated identical execution

Normal topic PRs always execute. Pushes to protected main/develop and `release/` PRs
to main may reference a fresh executed result from this repository's CI workflow.
Executable main promotion requires full scope, so a Python-only receipt cannot satisfy it.
Manual dispatch always executes full Linux. The daily21:17 UTC schedule executes full Windows; use dispatch
when a runner/environment change or independent requalification requires fresh evidence.

The shared verifier checks: successful completed original run and current attempt;
repository and event; exact tree (therefore tracked source, tests, workflow and locks);
workflow blob; executed scope and required Linux outcome; actual tested checkout recorded in
the receipt; and a maximum seven-day age from the original run creation (reruns cannot refresh it). It downloads only the named small receipt,
checks the GitHub artifact SHA256 and never executes artifact content. Missing/expired
artifacts, unknown refs, API failure or mismatch select real execution. Reused runs do
not publish another executable receipt, so reuse cannot extend age or form a proof chain.
Only a bounded recent-run search is performed; cache misses are normal.

Every new integration still runs the current repository/identity/BOM/history guards.
Git source provenance is not inferred from tree equality. CI summaries link the original
execution and identify the current checkout. The original run owns the runner image,
versions and logs: reuse does not claim the current Windows image was freshly tested.
Seven days is an explicit freshness policy, not arbitrary environment equivalence.
Production/native/Human and artifact qualification remain separate, never inferred here.

CI continues to run on all PRs and main/develop pushes; release/hotfix branches use their
PR run, avoiding duplicate push runs. Do not skip whole required workflows by path.
Concurrency cancels superseded CI, never a production deployment. The test jobs retain
JUnit timing/skip diagnostics; Python stages print command duration. The successful
aggregate seals the actual checkout in an immutable seven-day execution artifact.

All third-party GitHub Actions are pinned by full commit SHA, checkout fetches
full Git history because identity/history checks require it, and checkout does
not persist write credentials.

`npm run check:ci` is part of the root suite and guards these properties. It
also rejects adding exact-game deploy/load commands to public hosted CI. This is
an evidence boundary, not a convenience restriction: hosted runners do not own
the exact local STS2 installation, admitted Modset, installed artifact, or Human
operator needed to make such claims honestly.

## Focused portable checks

```bash
npm run check:ci
npm run check:identity
npm run check:bom
npm run check:boundaries
npm run check:history
npm --prefix components/connector run check
npm --prefix components/host-runtime run check
npm --prefix components/annotator run test
npm --prefix components/evidence run check
npm --prefix components/policy-runtime run check
npm --prefix apps/workbench run test
npm --prefix apps/ingame-ui run check
npm --prefix apps/game-mod run check
```

The checks have separate meanings:

- CI contract: workflow topology, trigger/concurrency policy, cross-OS aggregate
  gate, action pinning, and hosted/exact-game boundary;
- component identity: path-scoped Git provenance plus component tree, source
  digest, contract digest, version, clean-worktree reporting, and the repository
  EOL policy required to keep byte digests stable across checkout platforms;
- BOM: component source identities, versions, public package pins, retained
  runtime/artifact evidence, and explicit non-claims agree;
- boundary: component dependency direction, active predecessor references,
  local-path leakage, source completeness, and admitted workspace graph;
- migration history: imported predecessor histories still have their exact
  original tree/parent relationship; this is archival integrity, not runtime
  qualification;
- Connector check: public contract/SDK/package/docs/CLI/release tooling and
  portable Connector-local checks;
- Host Runtime check: lifecycle, package, Python consumer, and Host tests;
- Annotator `test`: portable recorder and workstation-tool tests;
- Annotator `check`: portable tests plus exact native compilation against the
  locally installed game and current Connector artifact;
- Evidence check: Python typed verification, immutable store/transfer/receiver
  and failure paths;
- Policy Runtime check: typecheck, tests (including the actual Workbench status
  consumer, command-timeout/replacement boundary, disconnected stop cleanup, and
  direct/proxied HTTP mutation admission), deterministic package build, and clean
  installed-package CPU/CLI smoke with the released Connector SDK;
- Workbench/Live UI/Game Mod portable checks: presentation/service/lifecycle
  source tests that do not claim exact game loading.

## Local exact-game and runtime gates

`npm run build` and `npm run check:exact-game` require the exact local STS2
installation. They build or compile game-bound artifacts; a successful build is
not install, load, Live mutation, Human evidence, or qualification.

Use the smallest evidence ladder required by the change:

| Change class | Minimum additional evidence beyond the selected portable gate |
| --- | --- |
| docs / governance / pure portable tooling | normally none beyond `project:closeout` and `git diff --check` |
| game-bound C# / native seam / unified Mod source | `npm run check:exact-game` plus a clean exact build and source/artifact identity |
| install / lifecycle / runtime packaging | exact build -> install -> cold load -> `verify:loaded` / owning runtime checks -> rollback readiness |
| Human recorder / causal semantics | exact runtime identity plus the bounded Human canary/audit required by the owning evidence contract |
| release | advertised build/package/install/load/runtime gates plus rollback and release evidence; Human/scientific gates only when claimed |

Do not promote a lower row into a higher one. A green GitHub workflow proves
source/test only.

## Merge provenance and component identity

Current component `source_revision` is path-scoped Git commit provenance derived
from `git log -1 -- <component path>`. Component tree and source/contract
digests are separate semantic/content identities.

That distinction has one important Git consequence:

- a normal merge commit preserves the topic commit as the path-scoped component
  source revision;
- squash or rebase integration rewrites that commit provenance even when the
  resulting component tree and content digest are byte-for-byte identical.

The component-identity regression suite proves both cases. Therefore, while the
current BOM/runtime provenance schema carries commit-based component
`source_revision`, **PRs that change any component source path must use a normal
merge commit**. Do not use GitHub `Squash and merge` or `Rebase and merge` for
those PRs. A docs/governance-only PR that changes no component source may still
be squashed.

If the repository later replaces commit provenance with a different stable
identity contract, change the tests, BOM contract, workflow guidance, and merge
policy together. Do not merely weaken `check:bom` after a squash-induced drift.

## Portability notes

The repository pins text materialization with `.gitattributes` as
`* text=auto eol=lf`. This is an identity requirement, not only a style choice:
component source and contract digests hash source bytes, so allowing Windows
`core.autocrlf` to rewrite a clean checkout would make one Git tree acquire a
different digest on another OS. `check:identity` directly guards the repository
EOL rule, and its temporary Git fixture enables `core.autocrlf=true` to prove
that canonical LF still holds. Text parsers that consume external or generated
content should nevertheless remain CRLF-tolerant rather than relying solely on
the checkout rule.

Root Host wrappers retain an explicit nested-npm `--` boundary so profile,
endpoint, and experimental-evidence arguments reach the owning Host CLI on
Windows and POSIX shells.

The Host profile-template test pins its captured file inventory. This prevents
Node-version-specific recursive-copy filter behavior from admitting runtime-only
Windows `logs` or `sentry` files into a reusable profile template. Workspace
package tests use Node's standard automatic discovery rather than depending on
shell-expanded `*.test.mjs` globs, which Windows npm does not expand on the
supported Node 20 baseline.

## Evidence Ladder

Evidence levels are ordered but never implied:

```text
source -> test -> build -> package -> installed -> loaded
       -> Live mutation -> journey -> human_validated -> qualified
```

Predecessor reports and fixtures can test mechanics or migration assumptions,
but cannot qualify a different Platform artifact. Local `.local/` evidence is
not documentation and must not be committed.
