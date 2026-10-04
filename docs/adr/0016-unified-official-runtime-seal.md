# ADR-0016: Unified official-release runtime seal

Status: Proposed

Date: 2026-10-04

## Problem and exact grounding

An ordinary game launch cannot retain a development process canary. The existing
hard-coded historical artifact tuple cannot qualify new unified DLL bytes, and
Workbench cannot create Connector execution authority. A persistent identity
route is needed without rebuilding the same DLL after its qualification.

## Decision

Use the existing authorized official release publisher and a fixed versioned
external seal/provenance pair for the unified production artifact. The initial
threat model is accidental drift and misconfiguration. HTTPS and same-release
checksums establish distribution identity in that scope; they are not signatures
and do not resist a malicious issuer or same-account local writer.

The same frozen artifact first receives explicitly authorized exact-process
canary bootstrap evidence. The issuer separately reviews that evidence and
publishes the external identity record. The installer fetches only the fixed
official repository/version/asset route. After paired installation, the same
artifact must pass the ordinary Steam lifecycle matrix. These are separate
gates; bootstrap, distribution or loaded admission alone is not final success.

## Owning fact and authority

The release issuer owns the qualification record. Game Mod owns official
preparation, exact native install/verification and paired rollback. Its Python
bridge reuses the existing complete kit ZIP verifier. Connector freezes the
bounded fixed adjacent pair before capabilities are served and checks actual
loaded source/SHA/MVID/protocol and current environment. Independent exact-game,
current Modset, controller and native action legality remain authoritative.
Workbench owns presentation and model selection only. Research owns formal
model registration; no policy/environment rebinding is inferred.

## Alternatives considered

Permanent environment variables or hidden Steam launch options preserve a
development opt-in instead of ordinary admission. Rebuilding a hard-coded DLL
allowlist after testing changes the tested bytes. PKI introduces a different
issuer/key lifecycle and is deferred until a broader threat model requires it.
Arbitrary local JSON, a caller checksum or a boolean cannot replace official
fetch provenance in the preparation operation.

## Evidence and falsification

The [source contract and remaining gates](../design/PRODUCTION_RUNTIME_SEAL.md)
define the exact fields and bounded source regressions. Shared raw fixtures
cover duplicate keys, malformed documents and tuple drift. Synthetic installation
tests exercise the existing owner's exact paired restoration. This ADR remains
proposed; source tests do not constitute native build/install/load, publisher
review, ordinary launch or Live/crash acceptance.

## Consequences and tradeoffs

Changing any qualified DLL/environment identity invalidates its old record.
The evidence digest is an audit attachment, not an automated proof that an
ordinary launch matrix passed. No CLR-version or same-user tamper-proof claim
is made. The Modset hash comes from the existing Connector algorithm; the
installer does not derive an alternative fingerprint.

## Compatibility and migration

No Player Environment wire version changes. Existing historical sealed tuples
and explicit canary bootstrap remain. A present broken pair overrides canary
artifact admission and fails closed; a missing pair leaves new ordinary
artifacts unqualified. No saves, project profiles or registered model identities
are automatically migrated or overwritten.

## Rollback

The existing native owner archives and restores DLL, manifest, both sidecars,
component configuration and supported settings. Caught failure restoration is
not power-loss atomicity. Changing the loaded pair requires a new game process.
Global Workbench launcher replacement retains its own guarded paired rollback.

## Non-goals

No publication, production installation or qualification is authorized by this
ADR. No generic remote executor, ZIP parser, installer, gameplay controller,
model training pipeline, signing infrastructure or automatic retry of unknown
delivery is introduced.
