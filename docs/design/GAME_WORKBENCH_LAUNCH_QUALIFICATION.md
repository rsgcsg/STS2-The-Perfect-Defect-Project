# Game Workbench launch and persistent qualification — design candidate

Status: investigation and proposed owner boundaries. No runtime seal, signing authority,
Steam setting, permanent environment variable or execution allowlist has been changed.
The current game has active progress; all production packaging, initialization, backup,
loading, stopping, restarting and installation are held by the supervisor.

## User requirement and acceptance

The user requires a working product, including: “每次打开游戏都可以通过工作台加载模型，然后顺利的跑”
and “每次打开都能跑，然后可以停，可以继续”. The original normal HOME and Steam profile2 must
remain in use. A once-loaded Human model or a maintainer-only special launch does not
satisfy ordinary repeated cold starts.

Final acceptance covers normal cold start, the same game Workbench entry, model selection
and terminal load, bounded execution, pause to Human, explicitly authorized fresh resume,
boundary stop, and Workbench/Runtime/game crash recovery. Accepted requests are not success.
Unknown delivery must never be automatically replayed. Environment or qualification drift
must identify the owning repair operation instead of returning a generic incompatibility.

## Observed history and current failure

Read-only local evidence distinguishes separate processes and profiles:

* 2026-10-03 17:28 UTC: the historical isolated profile has Modset fingerprint 223f5bba
  and a process-local exact-source canary. Its loaded Connector source, SHA and MVID are
  the same triple later used under the original HOME.
* 2026-10-03 18:31 UTC: native lifecycle `launch` starts the original-HOME game with its
  explicitly resolved exact-source canary. The paired loaded verification already records
  Modset fingerprint f48f88e2 and `canary_exact`.
* 2026-10-04 06:20 UTC: the current original-HOME process reports that same loaded
  Connector triple and f48f88e2, but `artifact_unqualified` and execution unavailable.
  The existing model manifest requires the historical isolated-profile Modset 223f5bba.
  The last stopped Runtime records five Auto requests, each failing closed on Modset
  fingerprint drift, followed by Stop; no decision or receipt was recorded. Operator
  attribution was not observed. The live game is at a Neow event, so it must remain intact.

These are local observations, not changes made by this design. The complete fingerprint
also includes manager state, ordered manifest/load/source/workshop metadata and loaded
assembly identity; matching only the Host triple does not prove identical Modsets.
No registry, policy manifest or endpoint may be silently rebound to bypass that guard.

## Existing owner contracts and implementation gap

[ExactArtifactCompatibility](../../components/connector/host/Authority/ExactArtifactCompatibility.cs)
accepts one hard-coded sealed v1.0.0 source/SHA/MVID triple. For other artifacts, only the
matching process-local exact-source environment request grants `canary_exact`; without it,
mutation remains unavailable. There is no found official API that qualifies an already
running ordinary game in place.

[VERSIONING_AND_RELEASES](../../components/connector/docs/VERSIONING_AND_RELEASES.md)
already requires an external runtime-seal asset after the exact artifact is installed,
cold-loaded and Live exercised. It explicitly avoids the circularity of embedding the
final artifact's own hash and later evidence into that artifact. The current
[release manifest](../../components/connector/release-manifest.json) has no runtime-seal
asset and marks qualification pending. Current package, install and verify tools carry
the payload, source/build identity, contracts and checksums, but do not generate, install
or consume such a seal. Host Runtime also has no durable qualification store to reuse.
Historical source/artifact qualification does not transfer to the current artifact.

Workbench owns selection and lifecycle presentation. It must not manufacture Connector
execution authority, edit a policy's environment tuple, replace a sealed allowlist, or
treat an arbitrary checksum or caller boolean as qualification.

## Possible stage delivery through the existing native owner

The existing [native lifecycle](../../apps/game-mod/lifecycle.mjs) `launch` is a formal
normal-HOME entry: it rejects an already running game, verifies installed bytes against
installed provenance, resolves a supported exact game/source canary through the existing
compatibility contract, and launches with only process-local canary state. `verify-loaded`
is a separate required observation; `launched` alone is not loaded or qualified.

An explicitly installed user-facing development entry could invoke this immutable,
reviewed native owner with one click and display **development canary** identity. It
would avoid the user manually entering environment values each time. No permanent
environment variable or hidden Steam launch option is needed or authorized. Its game
Workbench entry would still need actual-project registration and a model formally bound
by the registration owner to the live, qualified original-HOME environment.

This is only a stage proposal. No such user entry has been installed or accepted. It must
not be described as ordinary Steam launch qualification, must reject changed installed
artifacts, and must not stop the user's active game automatically. A graphical wrapper,
if approved, should be a narrow native-owner entry, not an arbitrary checkout executor.

## Persistent release path requiring architecture review

The supervisor has selected accidental misconfiguration and identity drift as the
initial threat model. The existing authorized project release publisher, the exact
official versioned GitHub release, and HTTPS delivery can establish distribution
provenance in that model. A checksum obtained from that same release detects corruption
or asset mix-ups; it is not a digital signature or an independent authority. This model
does not resist a malicious publisher or a same-account local writer.

The [historical runtime-seal record](../../components/connector/docs/evidence/STPD_OPERATIONAL_RUNTIME_SEAL_2026-08-22.md)
already describes a versioned identity record produced after exact-artifact cold-load
and Live evidence under an explicit process-local canary. It distinguishes GitHub
publication and anonymous-download verification as distribution gates, not runtime
evidence. That supports investigating the existing release authority before introducing
new signing infrastructure.

The minimal durable direction follows that external-seal release intent:

1. Define the official release issuer and the installer operation that verifies its
   exact versioned asset over HTTPS. The current local-directory installer does not
   establish this provenance; adding a caller-provided checksum or local JSON is
   insufficient. No new signing keys or allowlist entries are created here.
2. Use a versioned external owner-issued seal at a fixed adjacent installed location.
   Bind exact Host source/DLL SHA/MVID/protocol, exact game/runtime platform and identity,
   ordered Modset identity, and the qualification evidence digest and scope.
3. Keep game/runtime/Modset checks independently fail-closed. A missing, malformed,
   unauthorized or stale seal permits observation only; any identity update invalidates
   the old seal. Install, verification and rollback pair the artifact and seal.
4. Bootstrap the same final artifact's bounded Live acceptance through the existing
   explicitly authorized process canary. After formal seal issuance, repeat the complete
   lifecycle through ordinary cold start. Other artifacts' prior evidence cannot qualify it.

Required lowest-level regressions cover exact ordinary admission, each identity mismatch,
absent/malformed/unauthorized seal, stale seal after upgrade, payload/seal installation
mismatch, and paired rollback. Required real acceptance then exercises the complete
cold-start/load/run/pause/resume/stop/crash matrix on the final installed artifact.

Desired user permissions remain the existing per-user mod installation and a safe game
restart. Platform-specific permission details and the verified distribution operation
are unresolved. Changes belong to Connector release packaging/manifest, installation,
verification and paired backup/rollback, plus the Host compatibility reader. Workbench
does not issue the seal. Publishing or adding assets to a public GitHub release is a
separate external action: the current source/draft-PR authorization does not authorize it.
If a later Host contract requires offline resistance to local malicious changes, that
requires an explicitly authorized signing authority and verifier as a separate decision.
This design does not authorize implementation of a new trust root, signing authority,
permanent environment setting, automatic registration or any real game action.
