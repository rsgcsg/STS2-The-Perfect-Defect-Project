# Production runtime seal source candidate

This implements the reviewed accidental-drift scope for one production
`STS2_PLATFORM.dll`. It does not publish a release or qualify a built artifact.
The fixed official repository is `rsgcsg/STS2-The-Perfect-Defect-Project`.
`game-mod/v<version>` and the asset names below are source conventions; no such
release is created by this change. Stable versions and `-rc.N` versions have a
64-character bound. Prereleases are accepted; draft/unpublished releases are not.

## Owners and non-circular evidence

The existing authorized project release issuer must review the same frozen
DLL's exact-process canary bootstrap, bounded Live lifecycle results, and
sanitized evidence record before issuing the external seal. The same final
artifact must then pass the ordinary Steam cold-start/load/run/pause/resume/
boundary-stop/crash-recovery matrix. A bootstrap canary, publication, checksum,
successful preparation or loaded admission alone does not complete that matrix.

The [contract](../../apps/game-mod/runtime-seal-contract.json) binds the exact
Connector source, unified compiled source revision/digest, DLL SHA/MVID and
protocol, plus OS/architecture, Host kind, game version/commit/runtime hash,
game assembly SHA/MVID and Connector-observed Modset fingerprint/scope. There
is no CLR-version claim. The runtime hash is observed by `AssemblyHasher`;
`release_info.main_assembly_hash` is different metadata and is never substituted.
The evidence record is an opaque hashed audit attachment, not an automatically
validated ordinary-launch success statement.

Connector retains its independent exact-game and current Modset gates, native
owner/operand rediscovery, controller/idempotency and unknown-delivery rules.
Workbench selection cannot mint qualification or rebind a model's environment.
This change does not alter M0 registration or M2 training/store ownership.

## Official preparation and existing installation

The source command is:

```text
node apps/game-mod/prepare-runtime-release.mjs <version> <absolute-release-root> [python]
```

It fetches only the fixed repository's exact versioned release. It verifies
the archive `STS2-Platform-<version>-developer-kit.zip`, external
`STS2-Platform-<version>-runtime-seal.json`, sanitized
`STS2-Platform-<version>-runtime-evidence.json`, and `checksums.sha256` against
same-release asset SHA256/size metadata and each other's exact identities.
Every redirect is bounded and HTTPS, with no userinfo or explicit port; the
GitHub repository/tag/asset path is exact and the CDN host is allowlisted.
Raw JSON duplicate keys, floats in integer fields, unknown fields, malformed
identities, oversize/deep documents and stalled downloads fail closed.

Only the official fetch's opaque in-process bundle enters publication. A
caller-supplied local receipt, checksum or boolean cannot replace that fetch.
The Python `verify-runtime-archive` read-only bridge reuses the existing complete
developer-kit ZIP/inventory verifier. It checks the native DLL byte digest and
build provenance; no second ZIP parser or package producer is introduced.
Only after those bindings pass does the existing `prepare` owner create its
immutable checksum-named release/source directory. The sidecar pair and audit
attachments publish as one directory rename. A failure may leave an unqualified
prepared release requiring inspection, but does not install or load it.

The existing prepared-release initialize/deploy/launcher flow remains the owner
of locked dependencies, actual project registration, native installation and
global Workbench launcher CAS/rollback. Deployment re-verifies the package,
prepared source and native tuple before replacing bytes. It archives, installs,
checks and restores both fixed adjacent files with the existing DLL transaction:

- `STS2_PLATFORM.runtime-seal.json`
- `STS2_PLATFORM.release-provenance.json`

Unsealed development deployment removes predecessor sidecars under that same
backup/rollback owner. A sealed owner launch strips inherited game/source canary
variables and requires `supported_exact` plus execution availability at loaded
verification. The game can also cold-start through ordinary Steam without that
owner launcher. Both paths still require real acceptance on the final artifact.

## Loaded admission and limits

Connector freezes the adjacent bounded regular-file pair at process startup,
before serving capabilities. Later file writes cannot qualify an already
running process. A present incomplete/malformed/foreign/stale pair overrides
the old artifact canary and fails closed. An absent pair retains the explicit
development bootstrap behavior; a new ordinary unsealed artifact remains
unqualified. A matching pair admits only the actual loaded identity/environment,
and independent exact-game/current-Modset checks still determine execution.

HTTPS and checksums establish distribution identity within accidental drift and
misconfiguration. They are not digital signatures and do not resist a malicious
authorized publisher or a same-account local writer. Local receipts are checked
for bindings, not presented as tamper-proof credentials. Deployment's caught
failure rollback is not a claim of power-loss atomicity.

Source tests use synthetic raw fixtures and temporary files only. Real package,
issuer review/publication, clean build, install, ordinary cold-load, model load,
Live actions, crash recovery, physical save backup and restoration are separate
authorized gates. None is claimed by this source candidate.
