# STS2 Connector

> This is the Connector component of `rsgcsg/STS2-AI-PLATFORM`. Historical
> standalone releases and evidence retain their original repository identities;
> root Platform status governs new source and release claims.

STS2 Connector exposes the real Slay the Spire 2 UI as one fair-player
Player Environment for external consumers.

```text
STS2 rules, RNG, objects and UI
-> LiveHost visible facts and one current input owner
-> NativeUi exact private binding and native input delivery
-> Snapshot / Read / BoundAction / Receipt
-> localhost REST or optional MCP
-> a strategy-owning consumer
```

The game remains the only rules and effects engine. The Connector does not
expose coordinates, arbitrary reflection, hidden RNG, native operands or a
second legality model. Reads are state-bound and non-authorizing. A complete
finite BoundAction projection is required for input authority; delivery is
revalidated against the current native UI. Unknown delivery is not retryable.

Historical standalone stable/RC releases remain available from the predecessor
repository. Current Platform source versions are Connector `1.2.0-rc.5`, SDK
`1.1.0-rc.1` and Player Environment protocol `1.0.0`. They are source
candidates only until new Platform-built artifacts complete their own build,
install, cold-load and Live gates; predecessor runtime evidence does not
transfer.

## Repository Map

- `host/`: in-game Mod, exact Live binding, REST and Player Environment core.
- `contracts/`: machine-readable protocol and exact Host compatibility authority.
- `sdk/typescript/`: strategy-free strict decoder, REST client and controller session.
- `transports/mcp/`: optional thin MCP-to-REST transport.
- `tools/`: doctor, build, deploy, rollback, identity and conformance checks.
- `docs/`: architecture, protocol, coverage, development and evidence boundaries.

Start with the [new engineer guide](docs/NEW_ENGINEER_GUIDE.md), then read
[Architecture](docs/ARCHITECTURE.md) and the [Protocol](docs/player-environment/PROTOCOL.md).
The [document map](docs/DOCUMENT_MAP.md) separates current design, operation and
dated evidence.

## Developer Quick Start

Requirements: Node.js 20+, npm, .NET 9 SDK, Git and an installed Steam copy of
Slay the Spire 2. Python 3.11+ is needed only for MCP checks.

```bash
git clone https://github.com/rsgcsg/STS2-Connector.git
cd STS2-Connector
npm run bootstrap
npm run doctor
```

`doctor` discovers Steam libraries on supported desktop platforms. Set
`STS2_GAME_DIR` only if discovery cannot locate the exact game installation.

With STS2 fully closed:

```bash
npm run deploy
```

Then cold-start STS2 and verify the process, not just disk bytes:

```bash
npm run verify:loaded
```

See [Installation and rollback](docs/INSTALLATION.md). Never commit game
assemblies, installed artifacts, local runtime data, logs or secrets.

## Consumer Contract

The TypeScript package under `sdk/typescript` strictly validates the wire and
performs transport/control operations only. It also provides a coherence-
checking eager Read aggregator for memoryless consumers; that helper cannot add
facts or authority. Consumers own strategy, normalization and progress policy.
They must not reconstruct native legality or retry an `unknown` receipt.

The process-local witness used by the Platform Annotator may request required
Reads while the Connector owns one authoritative frame. It returns the same
Snapshot plus materialized read-only payloads and never publishes a second wire,
binding or executor.

For an exact local card-reward canary, the process-only
`STS2_CONNECTOR_CARD_REWARD_CANARY_DIAGNOSTICS=1` setting enables bounded private
Godot-log observations of native refresh/button creation and the current screen's
parent/binding status. It is off by default, limited to eight observed screen
generations with an explicit exhaustion marker, and never changes Snapshot,
BoundAction, legality or delivery. A parent ID is reported only if the existing
registry already assigned it; that alone does not prove anyone received an outer
Snapshot. Logs are local diagnostic evidence, not Human origin or loaded
qualification by themselves. `binding_facts_captured` covers the parent and
alternative-button observation only; it does not prove card-holder completeness,
Snapshot readiness, enabled input, native acceptance or a causal successor.

The REST surface is documented in
[Player Environment Protocol](docs/player-environment/PROTOCOL.md). MCP is an
optional adapter over the same endpoints, not another authority.

## Evidence Boundary

Source, tests, build, install, loaded identity, targeted Live gates, ordinary
journey and release support are different evidence levels. Pre-extraction
SpireAgent and RC evidence is predecessor evidence only. Stable release support
must be tied to the exact release SHA-256, MVID, runtime, game and Modset.
Source, build or install never transfers the `v1.0.0` seal to `1.1.0-rc.1`.
See [Current status](docs/STATUS.md) for the precise non-claims.
