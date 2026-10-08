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

Local recording import, data preparation, explicit training, model controls and
development-only M2 evaluation are available without a team login, with source
and recipe support bounded by the implemented contracts. After one-time
developer-kit initialization, the installed macOS Mod can open the selected
local Workbench through its fixed user-level launcher. This is not a claim that
the full Stage 1a journey is complete. See the [current in-run implementation](docs/plans/TEXT_STS2_IN_RUN.md)
and [console behavior](python/docs/PROJECT_CONSOLE.md) for exact boundaries.

Logging in does not upload old private data, start training or take over a game.
Team access and downloaded data retain their existing permissions and use/Gold
restrictions; local and cloud views do not create separate copies of authority.

## Start here

- [Current baseline v1 specification](docs/BASELINE_V1_SPEC.zh-CN.md): selected
  abstract layers, full interaction target, I/F-off Agent and user approval gates.
- [Current execution packet](docs/plans/BASELINE_G2_V1_EXECUTION_2026-10-08.md) and
  [CURRENT](docs/memory/CURRENT.md): authorization, owners, evidence and next steps.
- [Completed S0 learning loop](docs/evidence/BASELINE_S0_LEARNING_LOOP_2026-10-08.md):
  real Agent collection, structured-model training/export, native play and exact limits.
- [Architecture](docs/ARCHITECTURE.md), [components](docs/COMPONENTS.md),
  [testing](docs/TESTING.md), [workflow](docs/DEVELOPMENT_WORKFLOW.md),
  [governance](docs/ENGINEERING_GOVERNANCE.md) and [project standards](docs/PROJECT_SYSTEM.md).
- [New member handoff](docs/NEW_MEMBER_HANDOFF.zh-CN.md) and
  [existing Stage1a product requirements](docs/STAGE1A_PRODUCT_DELIVERY.zh-CN.md).
- [Design history](docs/design/BASELINE_DESIGN_HISTORY.zh-CN.md) and
  [document map](docs/DOCUMENT_MAP.md) and [skills](.agents/skills/README.md): earlier proposals, alternatives and exact
  receipts. Historical proposal documents are not additional normative layers.

The first completed instance is a bounded text-menu-v2 compatibility loop. Full
G2/V1 implementation and verification continue against the retained requirements;
only the user can approve those gates. Source/test, installed/loaded execution,
Human origin and scientific results remain separate. The latest owner
has authorized lead-managed implementation and necessary operations within the
execution packet; paid model/training spend is capped at USD 20 in aggregate.

## Workspace

`components/` owns native environment, Host, Connector, Annotator, Evidence and
Policy Runtime. `apps/game-mod` builds one game Mod; `apps/ingame-ui` is its UI.
`python/spireagent` owns Hub, local Workbench, shared console and artifact services.
`python/stpd` owns research projections, models, training and evaluation.
`python/deploy` owns cloud recipes. `tools/` owns the root checks and provenance.
`apps/workbench` retains typed diagnostic APIs for existing consumers; its duplicate
HTML console is retired. The project Workbench is the only user console.

[`workshop/`](workshop/README.md) is the unpublished Steam Workshop release
projection boundary. It contains listing metadata, not another runtime package;
generated content, staging and publication are not implemented by this scaffold.

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
