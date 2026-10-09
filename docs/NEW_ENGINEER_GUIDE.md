# New Engineer Guide

Start with README → [CURRENT](memory/CURRENT.md) → the active task packet and
owning specification. This page is the short engineering path to a legitimate
first project PR. Use the [member handoff (中文)](NEW_MEMBER_HANDOFF.zh-CN.md)
only for the accounts, installation, collection or operator role you actually need;
a source contributor does not need to complete every collector/production step.
If you only collect/view SpireAgent data, use the
[collection workbench route](ANNOTATOR_COLLECTION.md#default-project-workflow); building
Platform is not a collector prerequisite.

## 1. Know the boundary

Platform connects the real STS2 runtime to fair-player automation,
native-human evidence, evidence logistics, and model-neutral policy lifecycle.
It does not own strategy, rewards, models, training, research admission, or a
second legality engine. STPD is the research consumer in `python/stpd`;
sharing this repository does not transfer native or evidence authority.

Authority is deliberately split:

- STS2: rules, RNG, effects, native legality, and Commit.
- Connector: player-visible Snapshot/Read, complete finite BoundAction,
  execute-time native binding, Receipt, and successor semantics.
- Host Runtime: process lifecycle, isolation, recovery, and exact identity.
- Annotator: native-human witness correlation and immutable recording evidence.
- Evidence: typed verification and immutable transfer, not Human origin or
  research admission.
- Policy Runtime: model-neutral modes and controller/delivery lifecycle over
  Connector-owned actions, not inference or legality.
- Workbench and Live UI: presentation and bounded application commands.
- External consumers: strategy, research projection, training, and evaluation.

If a change would move one of those responsibilities, stop and read
[Architecture](ARCHITECTURE.md),
[Engineering Governance](ENGINEERING_GOVERNANCE.md), and the relevant ADR.

## 2. Prepare the checkout

Portable CI uses the repository-declared Node, .NET, and Python toolchains. Git
and npm are required.

```bash
git fetch --prune origin
git switch --create chore/platform/my-change origin/develop
npm ci
npm ci --prefix python
npm run setup:python
```

Use a short-lived branch in its own worktree when another person or agent is
already writing in the repository. Never reuse another workstream's writable
branch.

## 3. Establish the portable baseline

```bash
npm run doctor
npm run check
```

The root check is the portable source/test gate and must not require proprietary
STS2 files. Neither command proves build, installation, load, live mutation, a
journey, Human origin, or qualification.

## 4. Choose the owner and change class

Run a bounded context map:

```bash
npm run project:context
npm run project:context -- --component connector
```

Choose the narrowest owner, identify the first incorrect fact, and classify the
change using [Engineering Governance](ENGINEERING_GOVERNANCE.md):

- `G0`: docs/governance/portable repository tooling;
- `G1`: portable implementation;
- `G2`: public contract or cross-component behavior;
- `G3`: game-native source;
- `G4`: package/install/load/runtime lifecycle;
- `G5`: Human/causal evidence;
- `G6`: cloud/infrastructure promotion.

Then read root `AGENTS.md`, the component-local guide when present, the exact
contract and implementation, and neighboring tests. Simple presentation leaves
may use a README instead of a local agent guide.

## 5. Understand evidence language

Evidence levels are distinct:

```text
source -> test -> build -> package -> installed -> loaded
  -> live_exercised -> journey -> human_validated -> qualified
```

Passing one level does not imply the next. A predecessor artifact's report does
not qualify rebuilt bytes.

## 6. Make one legitimate change

1. Confirm the owning fact/layer and dependency direction.
2. Change the smallest clean causal implementation or document.
3. Add a faithful regression when behavior changes; if existing coverage is
   exact, explain why.
4. Follow machine-readable style and the nearest stable code pattern.
5. Run the lowest affected suite while developing; on the stable committed
   candidate, use the selected checks from [TESTING](TESTING.md).
6. Run `npm run project:closeout` and review every reported impact.
7. Update canonical docs or an ADR only when their truth changed.
8. Open a PR to `develop` using the repository template and latest head.

For the completed change:

```bash
npm run check:plan -- --base origin/develop --run
npm run project:closeout
git diff --check
```

The planner selects docs, Python or full checks from actual changes; governance,
contracts and unknown impact still select full. Initial environment verification
above runs full once. Do not repeat it for every edit or copy an earlier PASS.

Game-bound behavior also requires the exact-game and runtime gates named by the
owning component. Do not deploy merely to validate a G0 change.

## Repository Skills

The current repo-owned Skills are indexed in
[`.agents/skills`](../.agents/skills/README.md). Use them for exact runtime
qualification, native-Human evidence, or explicit Skill maintenance. Ordinary
implementation and review normally use canonical docs directly; do not create a
new Skill for one task or mutable project state.

## Common traps

- Inventing legality, native operands, coordinates, indexes, or hidden state in
  a consumer.
- Retrying after an `unknown` delivery or backfilling from a later Human effect.
- Treating presentation state as semantic truth.
- Treating a workspace commit as every component's semantic identity.
- Copying versions or artifact hashes into a second registry.
- Treating fixtures, compilation, or CI as loaded/Human/qualification proof.
- Loading the entire evidence archive for an ordinary change.
- Putting current branch names, long timelines, or transient blockers into
  durable governance docs or Skills.
- Committing `.local/`, game files, raw recordings, credentials, build output,
  installed artifacts, or model weights.

## Ready to work

- [ ] I can explain what Platform owns and what it does not.
- [ ] I selected one owning fact/layer and change class.
- [ ] I know the cheapest faithful regression and required evidence level.
- [ ] I am on a short-lived branch based on current `origin/develop`.
- [ ] I know the focused check, root check, PR target, rollback, and non-claims.

Use the [Document Map](DOCUMENT_MAP.md) when the task needs a different route.
