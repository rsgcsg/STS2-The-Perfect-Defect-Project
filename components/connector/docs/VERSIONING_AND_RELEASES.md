# Versioning And Releases

Four identities are intentionally separate:

1. **Connector source candidate** (`1.3.0-rc.6`; published package identity is
   recorded separately in the Platform BOM): source/product packaging and
   native implementation version.
2. **Player Environment protocol** (`1.0.0`): wire compatibility.
3. **Capabilities**: loaded features, exact game/Modset and observation/input
   availability.
4. **Artifact identity**: DLL SHA-256, MVID, source revision and runtime
   instance.

A release version does not grant support to every game build or Modset. A
protocol-compatible consumer still checks capabilities and exact environment.
SpireAgent and other consumers version independently; they declare a supported
protocol range and a versioned SDK dependency rather than sharing a branch or
release number.

An Annotator Mod dependency `min_version` is a compatibility floor, not an
equality pin to the current Connector source candidate. The Platform BOM still
checks exact selected component source/tree/digest and preserves historical
artifact identities. This additive, read-only observation route does not by
itself establish installed-game compatibility or change Annotator's minimum.

## Compatibility

- Patch releases preserve the stable protocol or advance only compatible
  optional capabilities.
- A protocol shape/meaning change advances the protocol and both Host/SDK
  conformance fixtures.
- A new game assembly or Modset starts unqualified and fails closed until its
  exact compatibility evidence exists.
- A new Host source/artifact also starts unqualified. A local candidate requires
  an exact process-local source canary; this never becomes release support.
- The Mod implementation ID `STS2_MCP` remains stable through major 1 so update
  and rollback find one installation.

## Release Gate

1. clean source and passing deterministic checks;
2. exact-game Host tests and Release build;
3. SDK package dry-run and MCP import check;
4. install with rollback backup;
5. cold-load and exact loaded identity;
6. targeted Read/stale/controller/idempotency/unknown/native-page gates;
7. an ordinary same-artifact Live journey;
8. evidence record tied to source, SHA, MVID, runtime, game and Modset;
9. tag, release artifact and SDK package publication.

`npm run package:release` accepts only a clean current-source Host build and
produces the install-ready Host archive, SDK tarball, machine contract, release
manifest and checksums under `.local/release/v<version>/`. The Git tag and
published assets must all refer to that exact source revision; a branch name or
working-tree build is not a release.

The static release manifest names the single machine-readable compatibility
contract and a separate runtime-seal asset. It does not copy one platform's
runtime identity into a second compatibility table. This also avoids a
circular artifact identity: runtime evidence is produced only after the exact
tagged Host has been built, installed and cold-loaded. A release is not sealed
unless that exact runtime-seal asset is present.

Build, install or predecessor Live evidence cannot skip a later gate.
