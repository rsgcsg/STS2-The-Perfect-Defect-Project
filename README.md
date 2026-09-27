# SpireAgent — STS2 Project

One repository for the fair-player STS2 environment, Human collection, local/cloud
project tools and STPD research. STS2 owns rules and native execution; project
applications and models consume declared interfaces.

## One Workbench, local and team resources

The game Mod is the default entry to the project Workbench. Its local service
can run independently of the game; the browser is another entry to that same
service. Signing in connects it to the team's shared system: data, model
artifacts/parameters, analysis, compute and other authorized resources. Team
model downloads and analysis are examples, not an exhaustive feature boundary.

The product target is a shared team resource directory and coordination service,
deployable on a cloud server, a LAN server or a designated team computer. Cloud
hosting is one option, not a requirement. Participating local or remote hosts may collect,
store, train, run models, analyze results and exchange authorized artifacts.
Compatible hosts may also run a simulator or a real game through the public
Host/Connector contracts; their behavior and qualification are not assumed
equivalent. Task placement should consider CPU/GPU and memory requirements,
data location, compatibility, availability and cost. Central coordination does
not replace each host's native execution, permissions or lifecycle authority.
This is the direction for integration, not a claim that a general multi-host
scheduler or cloud game Host has already been deployed.

Local recording, data management, training, model use and analysis are intended
to work without a team login. Current implementation is narrower: the no-login
home, local model controls, local artifact inventory and closed-recording catalog
exist; the complete local import/dataset/training/analysis journey is still being
connected. See the [current in-run implementation](docs/plans/TEXT_STS2_IN_RUN.md)
and [console behavior](python/docs/PROJECT_CONSOLE.md) for exact boundaries.

Logging in does not upload old private data, start training or take over a game.
Team access and downloaded data retain their existing permissions and use/Gold
restrictions; local and cloud views do not create separate copies of authority.

## Start here

- [Current work and next gate](docs/memory/CURRENT.md); [Stage 1a product delivery](docs/STAGE1A_PRODUCT_DELIVERY.zh-CN.md).
- [New member and Agent handoff (中文)](docs/NEW_MEMBER_HANDOFF.zh-CN.md): accounts,
  first installation, collection, development/PRs, operations and incident reporting.
- [Default release and migration acceptance](docs/MONOREPO_MIGRATION.md): native recording,
  automatic upload and member download passed the sealed migration Human gate.
- [Architecture and component ownership](docs/ARCHITECTURE.md).
- [Developer workflow](docs/DEVELOPMENT_WORKFLOW.md), [testing](docs/TESTING.md),
  [engineering governance](docs/ENGINEERING_GOVERNANCE.md), [skills](.agents/skills/README.md).
- [Collection, member setup and maintenance](python/docs/B_PIPELINE_HANDOFF.md).
- [Cloud deployment and recovery](python/deploy/hub/RUNBOOK.md).
- [Research and data](python/docs/FULLRUN_RESEARCH.md).

## Workspace

`components/` owns native environment, Host, Connector, Annotator, Evidence and
Policy Runtime. `apps/game-mod` builds one game Mod; `apps/ingame-ui` is its UI.
`python/spireagent` owns Hub, local Workbench, shared console and artifact services.
`python/stpd` owns research projections, models, training and evaluation.
`python/deploy` owns cloud recipes. `tools/` owns the root checks and provenance.
`apps/workbench` retains typed diagnostic APIs for existing consumers; its duplicate
HTML console is retired. The project Workbench is the only user console.

## Developer setup

Install Node 20+, uv, and the .NET SDK required by Platform checks. From this root:

```sh
npm ci
npm ci --prefix python
npm run setup:python
npm run check
```

`npm run check:platform` and `npm run check:python` select existing component gates.
`npm run workbench -- --config /ABS/project.json --hub-url https://hub.2-fire-2.com`
opens the single project collector workflow. Models are separate downloads;
recording and upload still require the user's configured consent and device.
Exact-game build/install/load and Human gates are separate from portable checks.

## Source and data history

Original Platform and STPD commits remain in this history, including prior imported
component histories. `migration/project-import.json` records exact source mapping.
Old recordings, dataset IDs, model manifests and evidence retain their original
producer identity. New source, package, service and scientific claims are separate.
