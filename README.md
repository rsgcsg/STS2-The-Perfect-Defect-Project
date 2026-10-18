# SpireAgent — STS2 Project

Build an STS2 Agent that can play through the real game with reproducible results.
The environment and data pipeline support that goal; neither is an unlimited
platform project. Current work focuses on a usable P1 interaction/recording/Agent
chain. Long term, environment implementations, protocols, data formats, Agents,
models and training methods should be replaceable without rebuilding control,
storage or the user product. Exact progress is in [CURRENT](docs/memory/CURRENT.md).

## Start here

1. [CURRENT](docs/memory/CURRENT.md): current assignment, status and one next action.
2. [Working rules](docs/WORKING_RULES.zh-CN.md): shared rules for every engineer,
   architect, manager and Agent: purpose, deliverable, cost and stop boundary.
3. Follow CURRENT's active task, then its relevant component/specification.
   Old plans and completed reports are background, not a second work queue.

The root [AGENTS](AGENTS.md) gives hard boundaries and task-based reading.
[AI collaboration](docs/AI_COLLABORATION.md) adds delegation/context recovery;
[project system](docs/PROJECT_SYSTEM.md) owns document placement and maintenance.
[Repository Skills](.agents/skills/README.md) provide specialized workflows when relevant.
Do not read the whole design history before a local repair.

## One product, distinct responsibilities

```text
STS2 → Host/Connector → selected environment protocol (currently P1)
     → controlled Runtime → complete Agent → native execution

independent native records → Evidence/verified projection → research data
     → training/model artifact → same Agent/product path → evaluation
```

A Model is an optional part of an Agent; JSON is a format, not an interaction
contract. Native rules, public information/actions, execution control, original
records and research labels have separate owners. See [Architecture](docs/ARCHITECTURE.md)
and [Components](docs/COMPONENTS.md) when changing a boundary. Source/test, actual
runtime, Human origin and strategy quality are different evidence levels.

One Workbench is the user console. The game Mod is its default entry; a browser
opens the same application service, which can run without the game. Local and
team resources retain existing identity/consent/data-use authority. Logging in
must not upload old data, start training or take control automatically. Team
coordination may run on cloud, LAN or a designated computer; a universal scheduler
and fully integrated remote game Host are not established capabilities.

## Workspace and targeted references

- `components/`: Native Foundation, Connector, Host Runtime, Annotator, Evidence,
  Policy Runtime. `apps/game-mod`: one production Mod; `apps/ingame-ui`: native UI.
- `python/spireagent`: Workbench/Hub and shared application infrastructure.
  `python/stpd`: research projections, models, training and evaluation.
  `apps/workbench`: retained diagnostic APIs, not a second user console.
- [P1 contract](docs/protocols/P1_GLOBAL_R1.zh-CN.md) and
  [project map / current pre-freeze plan](docs/plans/p1-prefreeze-2026-10-18/README.md):
  product work only when assigned; implementation, current acceptance and training
  readiness are separate. Earlier P1 plans retain their historical scope.
- [Workflow](docs/DEVELOPMENT_WORKFLOW.md), [Testing](docs/TESTING.md),
  [Governance](docs/ENGINEERING_GOVERNANCE.md), [Versioning](docs/VERSIONING.md).
- [Stage1a product](docs/STAGE1A_PRODUCT_DELIVERY.zh-CN.md),
  [console](python/docs/PROJECT_CONSOLE.md), [in-run delivery](docs/plans/TEXT_STS2_IN_RUN.md):
  established product requirements and bounded implementation.
- [Document map](docs/DOCUMENT_MAP.md), [status/evidence](docs/STATUS.md),
  [design history](docs/design/BASELINE_DESIGN_HISTORY.zh-CN.md): consult as needed.

## Development

Node 20+, uv and the required .NET SDK are used. Reuse installed dependencies;
run setup only when missing or changed:

```sh
npm ci
npm ci --prefix python
npm run setup:python
```

Use `npm run project:context -- --component <name>` for routing. Develop with
relevant checks, then `npm run check:plan -- --base origin/develop --run` on a stable
candidate; `npm run check` is the full portable suite, not an every-edit default.
`npm run project:closeout` checks handoff impact. Game build/install/load, Human
recording and training remain separately scoped operations.

Original Platform/STPD histories remain in Git; `migration/project-import.json`
records the import. Old recordings and models retain their actual producer and
contract identities. A new branch or version never rewrites old evidence.
