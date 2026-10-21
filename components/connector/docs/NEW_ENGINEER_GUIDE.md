# New Engineer Guide

STS2 Connector is the fair-player boundary between a running Slay the Spire 2
process and an external decision system. It observes the real UI, offers
state-bound reads, publishes the complete finite inputs available now, and
delivers one selected input through the game's native path.

It does not decide what is strategically good, predict effects, expose hidden
state, or implement a second copy of STS2 rules.

## Read In This Order

1. `host/PlayerEnvironment/Protocol/PlayerEnvironmentContracts.cs`
2. `host/PlayerEnvironment/Core/PlayerEnvironmentService.cs`
3. `host/PlayerEnvironment/Observation/SnapshotBuilder.cs`
4. `host/PlayerEnvironment/Observation/VisibleInteractionProjection.cs`
5. `host/LiveHost/LiveObservationReader.cs`
6. `host/NativeUi/NativeUiActionContracts.cs`
7. `host/PlayerEnvironment/Projection/BoundActionProjection.cs`
8. `host/PlayerEnvironment/Execution/ActionSubmission.cs`
9. `host/PlayerEnvironment/Reads/ReadService.cs`
10. `host/PlayerEnvironment/Control/ControllerService.cs`
11. `host/PlayerEnvironment/Transport/ConnectorMod.PlayerEnvironment.cs`
12. `sdk/typescript/src/protocol.ts`
13. `sdk/typescript/src/client.ts`
14. `sdk/typescript/src/decisionBundle.ts`

This path is complete without reading SpireAgent or retired connector designs.
Historical migration evidence is kept under `docs/evidence/`, not in the
current design path.

For the current P1 profile, read the owning [r1.5 contract](../../../docs/protocols/P1_GLOBAL_R1.zh-CN.md#6-动作清单)
and `sdk/typescript/src/p1Global/reducer.ts`. Online Runtime and offline native
recording conversion use that same core. A selector Close that returns to its
parent is excluded by the forward profile; native await completion alone is not
a P1 finish. Empty confirmation requires a current native completion binding and
its public finish fact; whole-page refusal requires an explicit native Skip and
`can_skip=true`. Never infer either from `min_select`, button text or `can_decline_now`.
After changing this mapping, build the SDK, run selector/admission regressions,
pin the Tool release, and derive new immutable projections from the originals.
Keep old IDs and gaps; changing only a revision or relabeling an old choice is
not migration or runtime qualification.

## Current Production Path

```text
real STS2 UI and native objects
-> LiveHost: fair-player facts plus exactly one current input owner
-> NativeUi: private exact object/control binding and native input delivery
-> PlayerEnvironment: Snapshot, Read, BoundAction, stale checks and Receipt
-> loopback REST
-> optional thin transport or strategy-free client SDK
-> consumer-owned strategy/progress logic
```

`ConnectorMod` is the in-game composition root. The installed DLL remains
named `STS2_MCP.dll` because `STS2_MCP` is the established Mod implementation
ID, not because MCP owns the architecture. REST is the native transport. MCP
is an optional adapter in `transports/mcp/`.

## Core Terms

- **Snapshot:** one fair-player view bound to a runtime and environment.
- **Interaction:** the single current player input scope and stage.
- **Referent:** a public visible entity or observed control identity.
- **Read:** an advertised, read-only information request bound to a Snapshot.
- **BoundAction:** an opaque finite projection of one exact current native
  binding. Public operands already exist as Referents.
- **Native binding:** Host-private screen, control, game objects and operands.
- **Controller:** the one registered client lease allowed to submit input.
- **Receipt:** `delivered`, `not_delivered`, or `unknown` input-delivery
  evidence. It is not proof of a later business result.
- **Successor:** the immediate post-delivery Snapshot when it can be observed.

## One Action End To End

1. `ActiveInputResolver` establishes zero, one, or multiple native owners.
2. Zero/multiple owners settle or fail closed; exactly one owner may continue.
3. A `LiveHost` reader extracts stable information available to a normal
   player.
4. `NativeUi` captures exact private bindings without putting native objects on
   the wire.
5. `VisibleInteractionProjection` removes Host-private fields.
6. `SnapshotBuilder` derives Referents and advertises Reads.
7. `BoundActionProjection` materializes every finite current input binding.
8. The controller submits `request_id`, `snapshot_id`, `bound_action_id`, and
   its lease.
9. `ActionSubmission` checks idempotency, controller, current Snapshot and
   current action membership.
10. `NativeUi` rediscovers and revalidates the exact native binding immediately
    before calling the game-owned input path.
11. The Host records delivery and returns the same Receipt for duplicate
    `request_id` submissions.

A stale action cannot mutate. An `unknown` delivery cannot be retried
automatically.

## One Read End To End

The Snapshot advertises an opaque Read ID and visibility basis. The consumer
submits that ID with the expected Snapshot. `ReadService` rebuilds current
observation, rejects stale/runtime drift, and returns only the declared
fair-player content. Reads never enter the action ledger and cannot create
authority.

## Where Changes Belong

| Change | Owner |
|---|---|
| identify current UI owner or visible facts | `host/LiveHost/` |
| hold native objects, controls and execute-time validation | `host/NativeUi/` |
| wire contract, Snapshot, Read, BoundAction or Receipt | `host/PlayerEnvironment/` |
| runtime/artifact identity, controller or idempotency | `host/Authority/` |
| strict decode and HTTP/control mechanics | `sdk/typescript/` |
| eager, coherence-checked Read aggregation | `sdk/typescript/decisionBundle.ts` |
| optional protocol translation | `transports/` |
| strategy, scoring, memory or progress policy | consumer repository |

Do not add source-name whitelists, consumer-generated operands, coordinate
input, arbitrary reflection, local legality, hidden RNG, silent fallback, or a
second mutation owner.

## Current Truth

- [Architecture](ARCHITECTURE.md)
- [Protocol](player-environment/PROTOCOL.md)
- [Coverage](player-environment/COVERAGE.md)
- [Information Closure](INFORMATION_CLOSURE.md)
- [Status](STATUS.md)
- [Development](DEVELOPMENT.md)

Source, tests, build, install, loaded identity and Live evidence are separate.
The current standalone artifact is not qualified by a predecessor monorepo run.
