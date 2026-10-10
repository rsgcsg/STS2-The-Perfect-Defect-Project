# Testing And Evidence

The root suite is the portable source and package gate. It does not require
proprietary STS2 files and does not prove installation, loading, mutation, a
journey, Human evidence, or qualification.

```bash
npm ci
npm run check
```

Repository-system checks and routing can also be run directly:

```bash
npm run project:context
npm run project:check
npm run project:closeout
```

`project:check` is part of the root portable gate. `project:closeout` reports
path-based review signals and never rewrites semantic truth.

## Hosted CI contract

### Fast feedback without repeated assurance work

Before changing behavior, state the risky claim and the normal/negative examples
that would falsify it. Develop with the smallest faithful check, then run the
selected component/root gates on a stable candidate. Do not run the whole suite
after each edit or add tests that only mirror implementation/prose. A minor change
with exact existing coverage names that coverage rather than inventing a new suite.

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

GitHub-hosted CI is intentionally a **source/test portability gate**, not an
exact-game or runtime qualification environment.

The workflow always starts a `plan` job. The same local router is available as:

```bash
npm run check:plan -- --base origin/develop
npm run check:plan -- --base origin/develop --run
```

The router uses actual committed Git changes, not a caller-selected test list:

| Scope | Selected checks | Eligible changes |
|---|---|---|
| `docs` | fresh repository/link/governance/identity/BOM/boundary/history checks | modified editorial allowlist or added/modified regular prose Markdown in explicit root documentation surfaces |
| `python` | repository guards plus the entire Python gate on Linux and Windows | only `python/spireagent/`, `python/stpd/`, `python/tests/` additions/modifications/deletions, optionally explicit report/editorial companions |
| `full` | complete root gate on both OSes | Platform, shared contracts/locks, CI/tools, deployment, governance, unknown paths or Git state |
| `reuse` | fresh repository/identity/BOM/history checks, referencing a verified executed receipt | eligible integration/promotion with identical content and check definition |

The docs route accepts non-executable regular Git blobs (mode 100644): modified
editorial allowlist files, added/modified CURRENT, and single-level Markdown files
in root docs/design, docs/plans and docs/evidence. AGENTS and SKILL names
(case-insensitive, including Python report companions),
governance/ADR, component/Python docs, JSON/schema/manifest files, tools, locks,
deletions, symlinks and mode/type changes retain full routing. Mixed executable
changes cannot use docs. The router reads committed raw Git modes; file extensions
alone do not establish eligibility. Changing this router itself still selects full.

Companions are modified editorial allowlist files, CURRENT, PROJECT_CONSOLE, and
added/modified single-file Markdown reports under docs/evidence. They qualify only
alongside a Python owner change; standalone eligible prose instead uses docs,
and evidence deletions still use full. ADR/governance and unknown docs are not companions.

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
It runs repository/identity guards, then the shared SDK/Runtime build and the
short S0 producer/consumer checks before the broad component/Python suites. A
failed repository prerequisite blocks later stages. A failed generated-client
build blocks its S0, Runtime, Workbench and Python consumers; unrelated owner
checks still execute. An Annotator or S0 test failure no longer hides independent
Evidence, Runtime or Python results. It never starts native actions.

The summary distinguishes passed, failed, cancelled and blocked, with exact
commands, exit codes/signals, durations and initial/final Git identity. Full and
Python runs require an unchanged clean candidate. Focused component runs may use
dirty development source, which remains explicitly recorded and cannot become a
frozen full result. Cancellation prevents subsequent execution; any required
failure or blocked stage keeps the aggregate failed. This changes diagnostic
collection, not the dual-OS requirement, supported scope or scientific claims.
CI preserves `.local/checks/workspace-*.json` alongside pytest results, including
on failure; the GitHub summary lists each reached or blocked stage. The existing
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
per-test UI/research selector. Windows is not dropped. Renames use Git's no-renames A/D
representation: both paths must qualify. Type changes, dirty worktrees and unresolved
refs fall back to full. Local uncommitted regression is useful, but final routing uses a
clean committed diff. `npm run check` always runs full.

`portable` is the required aggregate: plan and all selected jobs must succeed;
unselected jobs must be skipped. Failure/cancellation never becomes a PASS. A Python,
docs or reuse result is labelled as such, not as a new full dual-OS execution.

### Integration receipts instead of repeated identical execution

Normal topic PRs always execute. Pushes to protected main/develop and `release/` PRs
to main may reference a fresh executed result from this repository's CI workflow.
Executable main promotion requires full scope, so a Python-only receipt cannot satisfy it.
Manual dispatch and the weekly Sunday 21:17 UTC run always execute full; use dispatch
when a runner/environment change or independent requalification requires fresh evidence.

The shared verifier checks: successful completed original run and current attempt;
repository and event; exact tree (therefore tracked source, tests, workflow and locks);
workflow blob; executed scope and both OS outcomes; actual tested checkout recorded in
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
