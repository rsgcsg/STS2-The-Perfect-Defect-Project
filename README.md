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

- [L-N Human learning integration](docs/design/BASELINE_LN_LEARNING_INTEGRATION.zh-CN.md): the actual partial Human input stream, M2 variants and learning requirements; interface support is not dataset qualification.

- [L-N v1 scope and Agent contract](docs/design/BASELINE_LN_V1_SPEC.zh-CN.md): detailed operation commitments, native target/upgrade previews, model-neutral catalog access and loop ownership; proposal, not an implemented profile.

- [P0–P5 complete design and acceptance delivery](docs/design/BASELINE_ACCEPTANCE_PLAN.zh-CN.md),
  [detailed native/abstract scene specification](docs/design/BASELINE_SCENARIO_SPEC.zh-CN.md),
  and [bounded P5 evidence](docs/evidence/BASELINE_P5_EVIDENCE_2026-10-06.md).
  [Three Agent contracts and four concrete Host combinations](docs/design/BASELINE_PROTOCOL_OPTIONS.zh-CN.md)
  now define the detailed choices. D-M2 sequence-N is the reference journey;
  no protocol default has been selected or deployed, and G1 is unaccepted.

- [New baseline foundations: P0 needs and P2 upper design](docs/design/BASELINE_FOUNDATION.zh-CN.md),
  [P1 source/evidence audit](docs/evidence/BASELINE_P1_AUDIT_2026-10-06.md), and
  [stable task IDs and gates](docs/plans/BASELINE_TASKS.zh-CN.md). These are a
  reviewable design packet, not an accepted new gameplay protocol or runtime release.
- [P3 interaction candidates](docs/design/BASELINE_INTERACTION_CANDIDATES.zh-CN.md)
  and [P4 Agent, learning and execution contracts](docs/design/BASELINE_DATA_AGENT_CONTRACTS.zh-CN.md)
  extend the packet with full candidate semantics, model axes and N/Z/O design;
  implementation, experiments and G1 acceptance remain separate.

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
