> Project migration: application ownership and active layout are defined in
> [MONOREPO_MIGRATION](MONOREPO_MIGRATION.md). Game/evidence authority below remains unchanged.

# Components

> Proposed extension: [ADR-0015](adr/0015-native-logical-interaction.md) owns the
> native logical-page/action/memory target and the two contrasting selection
> examples. It is a revisable design proposal, not an implemented interface.
> Connector/Native Foundation will define page and operation mappings;
> Annotator/Evidence will define faithful interaction records and compatibility;
> STPD will own current-input/history projections and learned memory;
> Policy Runtime will own bounded execution and confirmed feedback.
> Shared capability scope must be coherent across catalogs, UI, execution and
> capture eligibility, without adding a second legality or causal authority.
> Exact schemas and native seams are follow-up owner work; current boundaries
> and old evidence below are not silently changed by the proposal.

| Component | Path | Owns | Must not own |
|---|---|---|---|
| Native Foundation | `components/native-foundation` | STS2-owned semantic decisions, native lifecycle, process-local owner lineage | transport, evidence, public action authority, input execution |
| Connector | `components/connector` | Player Environment, native binding/execution, REST/MCP, SDK | process lifecycle, strategy, annotation |
| Host Runtime | `components/host-runtime` | discovery, isolation, launch/reset/stop, headless/managed experiments, qualification | gameplay legality, research models |
| Human Annotator | `components/annotator` | native-human witness, one semantic causal tracker, derived current Decision/canonical projections, records, audit/export/bundle, workstation | action authority, research admission, a second causal adjudicator |
| Platform Evidence | `components/evidence` | typed verification, content identity, immutable store, transfer/receiver receipts | research eligibility, corpus policy, mutation |
| Policy Runtime | `components/policy-runtime` | policy process boundary, Human/Shadow/One-Step/Auto, controller lifecycle, stale/Receipt/successor and Agent-run evidence | model inference, legality, native operands, candidate filtering |
| Platform diagnostic API | `apps/workbench` | typed live status, explicit filesystem fallback, bounded Policy Runtime commands | gameplay submission, evidence admission, model loading |
| Platform Live UI | `apps/ingame-ui` | in-game Environment/Policy/Human Data/Diagnostics presentation and typed application commands | packaging/deployment, direct BoundAction submission, legality, recording writes |
| Platform Game Mod | `apps/game-mod` | one manifest/DLL, explicit component initialization, exact build/install/load/rollback provenance | gameplay legality, Human witness semantics, UI domain logic |
| Platform tools | `tools` | composition, component identity, migration/boundary checks | native operands, policy |
| Project applications | `python/spireagent` | one local/cloud console, members/devices, upload/export, operations and shared artifact infrastructure | legality, recording proof, research labels |
| STPD | `python/stpd` | ResearchTransition, Dataset Views, representation, Qwen, training/evaluation | Host implementation or legality |

Native Foundation is deliberately compiled into the game-side Connector Host
and unified Mod rather than published as a second runtime service. Its typed
providers own read-only projections of STS2 semantic state and action space.
Connector consumes those facts to project fair-player visibility and current
deliverability; `A_public` may be empty while input is settling without erasing
the execution semantic action space. Annotator preserves the same typed
observation for evidence without gaining legality or mutation authority.
Inside Annotator, `SemanticBoundaryTracker` is the sole current runtime causal
authority. Current Decision and canonical-transition files are non-authorizing
projections from an already proved semantic transition. The `*-2` wire names
identify the one current recording format. Historical Decision V1/V2 and
native-action-ledger contracts remain readable only for prior/additive evidence
and are not current runtime admission state machines.

Each imported component retains its focused `AGENTS.md`, tests and operational
documentation. Those files add component-specific constraints but cannot
override the root hard shell.

Standalone component tests compile Annotator against the exact Connector build
artifact. The production Game Mod instead compiles both source authorities into
one assembly and disables their standalone initializer attributes; it does not
copy or wrap either implementation.

The versioned Host Runtime package is the public programmatic boundary for its
Node driver/CLI and strategy-free Python client. Consumers provide external
candidate artifacts; they do not import a sibling source checkout.

The versioned Evidence Python package is the portable evidence-integrity
boundary. It verifies current and explicitly archival Human bundles and owns
local transfer mechanics;
research consumers remain responsible for their own admission semantics.

The Policy Runtime is a Connector consumer. A Policy Manifest names exact
model, adapter, representation, Reads, support and environment requirements.
The adapter returns only an ordered score vector and selected index; Runtime
resolves that index against the unchanged current Connector catalog. Workbench
and Live UI issue only typed Runtime or Annotator application commands. STS2
loads one `STS2_PLATFORM` manifest; logical authority does not follow DLL count.
