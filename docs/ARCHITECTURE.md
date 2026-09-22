> Project migration: application ownership and active layout are defined in
> [MONOREPO_MIGRATION](MONOREPO_MIGRATION.md). Game/evidence authority below remains unchanged.

# Architecture

## Product Boundary

STS2 AI Platform provides a real-game Host, fair-player Player Environment,
native-human evidence recording, model-neutral policy execution lifecycle and
strategy-free integration tools. It does not own policy inference, reward,
models, training or research authority.

## Proposed native logical interaction direction

[ADR-0015](adr/0015-native-logical-interaction.md) records WEB-01's revisable
upper-level proposal. It changes the target observation/action abstraction, not
any current wire schema, installed artifact, or historical Human proof below.
Its status must be read before treating the proposal as an accepted contract.

The target policy input is the current entered native logical page/mode's
authorized content and selection state, its complete applicable action menu,
and model-owned memory of actual history. A page's complete logical list is not
split by scrolling, pixels or virtualized holders; other unopened pages and
hidden outcomes are not automatically aggregated into that input. One semantic
native interaction is an action, not every mouse event or software read.

The ADR's primary contrast is a fully informed single selector with sequential
picks versus multiple reward groups with inspect/defer/re-enter opportunities.
Cancelling a tentative selection, temporarily leaving a reward group, claiming,
and final abandonment are distinct. Their exact native realization remains for
local validation and scoped implementation; the examples are not runtime proof.

Connector still owns observation/delivery, Annotator immutable Human evidence,
STPD research and learned memory, and Policy Runtime bounded execution and
recovery. A future shared capability profile must not become consumer-side
catalog filtering. Receipt observations and UI acceptance do not become causal
successors by renaming. Existing H, execution S, A_public, canonical evidence,
Read-rich inputs and M0 results retain their exact meanings until explicit
versioned migration. Detailed refinements and justified engineering tradeoffs
follow the ADR's review boundary, not an implicit rewrite of history.

## Planes

```text
Native:       STS2 -> Native Foundation -> Connector / Annotator
Environment:  game-side Host <-> Connector <-> consumer
Host control: Host Runtime <-> STS2 process lifecycle
Evidence:     Annotator -> immutable bundle -> verify/store/transfer/receive -> consumer
Policy:       external adapter -> Policy Runtime -> Connector
Operations:   typed application services -> CLI / Workbench / one in-game Live UI
```

The planes may share exact environment identity, but not mutation authority or
transport semantics. The Platform is not a strategy, reward, training, or
research service.

## Recording Application Plane

The native recorder correlation kernel owns one process-local recording state.
It starts `Ready`, and a typed `RecordingService` is the only application
boundary used to query status, issue lifecycle commands, or follow ordered
events. `StartNewSession` creates a fresh session, timeline and append store;
`Pause` blocks new witnesses without discarding already accepted native
lifecycle witnesses. `Close` stops accepting new roots, terminates bounded
native completion bookkeeping, records any still-unresolved final semantic
root as an explicit terminal unknown, flushes the store, and permits another
isolated session in the same STS2 process. Close never waits solely to invent a
successor and never promotes a final observation into `S'` without a trusted
causal boundary.

`RecordingStatus` is a current projection. Its scope section derives from the
active CaptureProfile and store counters, and separately reports recorded,
native-accepted-but-failed-closed, supported-but-not-observed and
declared-out-of-scope outcomes. A native UI attempt that STS2 rejects creates no
HumanDecision and no capture-failure invalidation. The bounded event stream
supports status-first reconnect followed by events after sequence N; a
retention gap requires a new status query. These operational events are not
durable Human Evidence. RunJournal, current decisions, invalidations, Reads,
semantic evidence and bundles remain the current durable Evidence Plane;
historical native-action-ledger streams are retained only for explicit archival
inspection. Audit, pack, verify, store and transfer never run on the game main
thread.

## Canonical Sequential Evidence Boundary

The Recorder's durable trace and a research transition are different products.
The canonical one-step contract is:

```text
S_t + A(S_t) -> A_t -> S_(t+1)
```

`S_t` is the fair-player semantic state consumed by `A_t`; `A(S_t)` is the
complete same-state semantic action space produced from STS2-owned rules by a
typed Native Foundation provider; and `S_(t+1)` is the next state after the
action and its causally owned automatic continuation. Connector remains the
only public delivery authority and may project an empty `A_public` while input
is settling. `A_public` and `A(S_t)` therefore share native facts but are not
interchangeable evidence. Human observation `H`, native acceptance, lifecycle
completion, and an interactive poll are independently valuable evidence, but
none may be silently relabelled as `S_t` or `S_(t+1)`.

`SemanticBoundaryTracker` is the sole current runtime Human causal authority.
It separately accounts Human Root, exact execution/Commit facts and a later
Successor Boundary. An exact next-Human pre-execution boundary may close the
immediately preceding root; if overlap makes attribution unsafe, gameplay still
continues and the affected canonical transition is omitted rather than guessed.
Typed native owner-ready and legitimate paused PlayerChoice boundaries may also
prove a successor. `GameAction.Finished`, Task completion, UI post-Commit,
periodic status frames, timers, queue-idle and generic later interactivity do
not imply `S'`.

Current Decision records and `canonical-transition` records are non-authorizing
projections from an already `TransitionProved` semantic draft. The current
Decision record retains the frozen Human-time public frame for durable
compatibility and may be omitted when its older interactive-shell constraints
do not fit an otherwise valid modern transition. Current canonical evidence
independently joins the semantic
state, a typed native decision/action catalog captured at the exact action-
binding boundary, exact Human/native correlation, Commit and the tracker-proved
successor. A source-local callback may bind before native admission while a
`GameAction` binds at `BeforeActionExecuted`; both retain their phase and exact
identity. The projection cannot settle a root, authorize legality or a
successor, backfill an unknown or introduce a second causal state machine.
Exact root environment identity retained until projection is metadata only.
Historical Decision V1/V2 and native-action-ledger schemas and validators remain
readable only for prior/additive evidence; the current runtime no longer uses a
mutable native ledger or serialized-evidence admission policy to adjudicate
causality. The `*-2` wire names identify the single current recording format,
not a parallel V2 product.

The current schema-4 timeline is an accounting/lifecycle trace. Exact-source and
exact-runtime analysis found that normal STS2 UI staging withdraws many accepted
affordances before execution, while generic later interactivity does not prove
causal settlement. Schema 4 preserves a content-addressed read-only projection
of the already-authoritative execution semantic state/action fact. ADR 0003
records the withdrawn serialized-input candidate; it is not current gameplay
authority. Native Foundation owns the bounded shared combat semantic catalog
and exact lifecycle adapter. Connector remains the only public state/action and
delivery authority; Annotator records the shared native observation and does
not reconstruct legality or claim a universal successor.

ADR 0005 records the Root / Commit / Successor split and the fail-closed
successor rules. Native lifecycle/task completion never implies `S'`; the
native semantic discriminator is diagnostic-only.

## Hard Shell

STS2 owns rules, RNG, native legality, effects and Commit. Connector publishes
only complete finite Host-bound actions and revalidates exact native operands at
delivery time. Reads are state-bound and non-authorizing. Host control is not a
Player Environment action. Annotator observes accepted native-human actions and
cannot execute them. Unknown delivery is never automatically retried.

The Policy Runtime receives the complete ordered Connector catalog and only
Manifest-required advertised Reads. It cannot filter or invent candidates. Its
adapter returns scores plus an index, and Runtime resolves that index locally
against the same Snapshot before acquiring the one Connector controller. Shadow
never acquires a controller; One-Step returns to Human; Auto hands off on an
unsupported surface, abstention, or not-delivered Receipt. A Receipt successor
is an immediate post-delivery observation, not causal settlement; stable next
decision evidence remains separate. A transport exception after submission or
an `unknown` Receipt taints the run and is never retried. Adapter failure or a
bounded decision timeout returns to Human before controller acquisition.

## Component DAG

```text
STS2 installation and native runtime
  -> Native Foundation
     -> STS2-owned semantic decisions, lifecycle, and owner lineage
  -> Connector component
     -> Player Environment contract, native UI implementation, SDK, and release identity
  -> Host Runtime component
     -> lifecycle, exact-build admission, probes
     -> public Connector SDK + pinned Connector Host release
  -> Annotator component
     -> Host workstation seam + exact Connector witness artifact
     -> native-human witness recording and session evidence
  -> Evidence component
     -> current and explicit archival verification, content identity, immutable local logistics
  -> Policy Runtime component
     -> model-neutral policy process and Connector consumer lifecycle
     -> append-only Agent-run evidence through Platform Evidence semantics
  -> Workbench application
     -> typed live status, explicit partial fallback, bounded Runtime commands
  -> Platform Live UI
     -> one in-game shell over typed Connector/Runtime/Annotator services
  -> Platform Game Mod
     -> one manifest/DLL packages Connector + Annotator + Live UI source
     -> exact build/install/load identity and rollback only

External consumers -> public Connector SDK / Host Runtime package
STPD              -> public packages + version-pinned Evidence + thin Policy Adapter
SpireAgent        -> consumer integration and policy
```

Native Foundation is semantic/lifecycle infrastructure, not a service,
transport, evidence store, or executor. Connector may filter its facts by
fair-player visibility and deliverability but cannot create semantic legality;
Annotator may observe them but cannot authorize input. See
[Native Foundation](NATIVE_FOUNDATION.md).

The Connector gameplay authority is one component-local path. Host Runtime
owns process lifecycle and may consume the public SDK; it must install the
Connector artifact named by the current Platform BOM/release authority, not an
unrelated branch or predecessor release. Annotator may use the explicitly
declared Platform composition seams for exact native witnessing, but it does
not create a second Connector build or action authority. STPD consumes public
packages and verified evidence artifacts, never Platform implementation
internals. STS2 sees one `STS2_PLATFORM` Mod, while Connector, Annotator and UI
retain separate source provenance and authority inside that assembly. Evidence
validates artifact integrity and transport; STPD alone owns
research admission, splits, labels, B0 and training authorization. STPD owns
checkpoint/Qwen/projection/scoring support but no controller or Connector
lifecycle. Workbench and Live UI own presentation and bounded application
commands, never domain authority or direct gameplay submission.

Portable boundary tests require the Host installer to consume a versioned
Platform Connector release and require Annotator to use only the declared Host
workstation seam plus the exact component-local Connector witness artifact.

## Identity

`workspace_revision` identifies an atomic Platform checkout.
`source_revision` is the latest Git commit that changed a component path;
`component_tree_revision` identifies that path's exact Git tree, and
`component_source_digest_sha256` covers its tracked and non-ignored source
bytes. These identities remain stable across unrelated component changes.
Public contract and artifact identities are separate. Artifact SHA/MVID and
exact loaded runtime remain the final byte/runtime authorities.

## Project applications and research in the same checkout

The one user console is `python/spireagent/console`, served locally by Workbench and
remotely by Hub. Only Hub owns project membership and device authorization; local
credentials authorize one device and are never cloud administrator credentials.
The retained `apps/workbench` endpoint is a diagnostic API, not a second user UI.

Shared JSON, package/source identity and artifact storage live below applications in
`spireagent`. They import neither application UI nor STPD research. Research never
imports Hub or Workbench. Hub composes explicit research jobs, permission services and
a provider-neutral compute interface; the Modal adapter retains provider call handles.
Workbench supervises the policy process; S1 requirements and launch arguments belong
to its model adapter. Unknown delivery never becomes an automatic retry.

One `python/uv.lock` governs the Python environment. Hub installs cloud/data dependencies;
worker images additionally install training dependencies. These are explicit build
profiles of the same source, not two product backends. Image digest and profile remain
part of deployment identity. The CPU Hub cannot be submitted as a GPU worker.

## Unified workbench delivery direction

The accepted UI target is one local/cloud/in-game workbench with the Mod as the
default entry for all functions, including login. Independent local/cloud services
retain their responsibilities and run without the game where appropriate. External
windows are alternate presentations of the same task/data/account system, not a
second product. See [UI specification](UI_INTERACTION_SPEC.md#accepted-direction-complete-in-game-workbench-2026-09-18)
for the target and phased implementation; the currently installed Mod is not yet
the complete workbench. No gameplay, credential or research authority moves into UI.
