# Architecture

## Product Definition

Host Runtime advances the real Slay the Spire 2 runtime without a normal
display. The shipped route uses the official executable, SceneTree, tasks,
commands, RNG, saves, Mod loader, and native callbacks. Loading selected game
classes or approximating rules is not semantic equivalence.

The stable term `headless` names the no-display CLI/runtime mode. It does not
name a separate current source repository.

## Normal Route

```text
Host Runtime
  discover and fingerprint the installed game
  require an exact supported build for normal start
  launch the official executable with --headless
  manage profile, readiness, resources, logs, stop, and failure

Platform Connector
  expose fair-player Snapshot, Read, and complete finite BoundActions
  keep exact native operands local
  revalidate and deliver native input
  return delivery Receipt and successor Snapshot

Consumer
  choose an advertised action and interpret the successor
  never create game legality or mutate native objects directly
```

REST, stdio, and other transports are delivery mechanisms, not gameplay
authority. The Player Environment contract and SDK are Connector-owned and are
not copied into Host Runtime.

## Route Classification

The shipped Godot process is the Reference Host and has the highest confidence
for semantic ownership. A Managed Host may use the exact game assembly with
narrow presentation adapters, but it remains a separately identified
candidate. It cannot inherit Reference or predecessor runtime authority.

```text
exact game runtime + bounded presentation adapters
-> raw Host-local decision state
-> canonical Player Environment projection and request ledger
```

Raw commands, privileged scenario controls, and native object references never
become a consumer API. Projection, allocation, transport, and supervisor
changes remain Managed Exact while the byte-identical game assembly owns rules,
RNG, effects, and Commit. Persistent reconstruction or short-circuiting of
task/UI lifecycle is Hybrid. Implementing gameplay rules outside the game is a
Simulator.

## Authority And Lifecycle

- STS2 owns rules, RNG, saves, native legality, effects, tasks, commands, and
  Commit.
- Host Runtime owns executable discovery, exact-build admission, profile
  lifecycle, no-display boot, health, resources, reset, recovery, worker
  supervision, and Host evidence.
- Connector owns fair-player Snapshot/Read/action binding, one controller,
  delivery Receipt, and successor.
- Consumers own strategy, projection, rewards, search, and learning.

Every mutation uses one current opaque BoundAction. Connector checks the
snapshot, controller lease, target identity, current actionability, and native
legality at execution. Duplicate request IDs return the same Receipt. An
`unknown` delivery is never retried. Receipt proves input delivery, not an
inferred business transaction.

Process lifecycle is a separate Host plane. Starting, stopping, profile
selection, reset, seed, branch, acceleration, and scenario controls are not
player actions and never enter the fair-player action set. Host controls may
use an exact, process-local diagnostic transport, but it creates no gameplay
authority and is not in the public SDK.

Seed is Host lifecycle state, not a player action. If a development Host accepts
seed or scenario controls, it must bind them to exact runtime provenance and
keep them outside the fair-player contract.

## Profile Boundary

Profile isolation is a Host concern. A Mod cannot guarantee pre-save isolation
when it loads after platform and save initialization. An isolated workflow must
redirect user-data roots before process creation, use an exact observed profile
schema, and verify a distinct runtime/profile generation.

Every runtime command requires exactly one explicit profile mode. Profile
templates bind their payload to exact game identity. Profile and normal
user-data sentinels are evidence of containment, not proof of gameplay
semantics or Steam Cloud server state.

## Release Boundary

A Host Runtime release must pin the Connector package/artifact and exact game
tuple from release metadata. The current source/package candidate is not a
runtime seal when the Host installer pin and root BOM/Connector manifest are
inconsistent. Do not repair that gap by silently accepting a branch, local DLL,
or predecessor release.

A package, boot, loaded identity, mutation, journey, differential, performance,
or qualification result is valid only for the exact source, artifact, game,
Modset, profile, and launch configuration named by its evidence.

## Native speed run configuration

`nativeSpeed` is a closed `normal|fast|instant` run configuration of
the existing shipped native Host. Isolated Reference factory/driver and isolated
`start` launches default to Instant; explicit Normal and Fast remain available.
All three modes use the same process,
Player Environment/Agent protocol, native legality, save and lifecycle owners.
The episode factory's `displayMode: headless|native-ui` selects presentation
and verifies the existing observed `headless|live_ui` identity accordingly.
Both presentations forward the same isolated profile binding. Native UI seed
preparation requires that binding and the existing Host token/exact identity;
its eligibility is independent of whether a speed override is requested.
Unmanaged/shared personal games preserve their native preference and receive
no default override. The experimental `shipped_noninteractive_v01110` execution
profile is a different mechanism and is not a speed alias.

An explicit request requires the Host's existing isolated profile generation,
Steam disabled before initialization, and its expected native user-data path.
The process-local Host control token and exact runtime authorize a bounded
preparation request containing no mode, path, property name or native operand.
On the game main thread, after native prefs load and the startup Instant-to-Fast
clamp, the Connector verifies admitted exact game/Mod identity, actual native
user-data/profile containment and generation, and the exact unobstructed main
menu with no active run or multiplayer owner. It writes only the public typed
`PrefsSave.FastMode` property and immediately reads that property back.

Preparation occurs once before bootstrap gameplay inputs. Repetition of the
same exact preparation verifies and returns the recorded result; conflicting
configuration, missing readiness, shared profile, unknown identity/isolation,
active gameplay, or later mode/profile drift fail closed. No timer reapplies a
mode, no provenance read mutates prefs, and no Fast fallback satisfies Instant.
Read-only private Host provenance records requested/before/effective mode,
readiness and runtime/profile binding. The caller verifies it before bootstrap
and again through the existing post-bootstrap provenance check before handing
the episode to an Agent. These facts never enter player Snapshot/Read or model
input and do not create a public speed action.

Instant is the user-selected project default. Workbench owned preparation resolves
`default` to Instant and records `{mode: instant, basis: project_default}`; it
records actual typed readback separately and invents no qualification reference.
Final-candidate exact build/install/load and all-three-mode live verification
remain required before claiming runtime qualification. This configuration change
does not qualify performance, save/restore or full runs. Normal and Fast remain
supported explicit configurations and reference conditions. Native settings
may save into the isolated working copy on quit; immutable templates and
personal prefs are not edited. A failed configuration blocks the run. If
qualified Instant still fails measured throughput, resource or save/restore
needs, alternate Hosts are evaluated later through the existing contracts.

## Component Boundary

The root Platform owns these components:

- Connector: Player Environment contract, native bindings, SDK, transports,
  and Connector artifact identity.
- Host Runtime: lifecycle, exact compatibility, isolation/reset, supervision,
  benchmarks, traces, differential orchestration, and Host evidence.
- Annotator: native-human witness recording and session bundles.
- SpireAgent: external consumer/policy in its own repository.
- STPD: external research consumer, data, training, and evaluation.

Historical predecessor names and reports remain in migration/history evidence,
not in the current operational path.
