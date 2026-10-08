# Native Agent application source integration

2026-10-08. Workstream: `codex/e6-v1-native-agent-application` in the independent
`SpireAgent-v1-native-workbench` checkout. This is a G2/G3 cross-layer source
increment, with application, Evidence and in-game presentation owners.

## Exact scope and dependencies

The branch began at `fa414996f453db765884a54dc6e00f1fe880f828` and normally merged
the lead's independently accepted producer integrations. The dependency baseline
before this application increment is `0bd830f536eb60e53ff97da2e8ab5619a3ae0b6b`;
the latest source commit is the commit containing this report. Component
provenance must retain normal merge ancestry. There is no cross-repository pin
change: all active owners reside in the same source repository.

The accepted native SDK producer is
`651f9cbd163d177805fa641ca0f05b8dcb424877`. Registration targets the fixed public
13-seam publication profile whose raw definition SHA-256 is
`c060cfd354c6702e10711e6329848836ec133f2117841750e317b6ab27b244cf`.
The required seams are checked as the whole fixed profile, never derived from a
shrinking capability subset. That profile does not establish all-native coverage.
The native Runtime executable and the later lightweight/observed numerical Model
increment are not dependencies accepted by this report.

## Owning facts and behavior

The application previously had only Policy Manifest dispatch, legacy status
projection and legacy Agent-run recovery. It now admits the explicit native
Agent namespace through the same model export journal, own-byte download
verification, registry and primary Runtime installation slot. STPD owns package
validation, numerical semantics and AgentManifest construction. ArtifactID,
package model ID, weights, package digest, InputSpec, adapter source and fixed
publication profile remain separately bound.

The shipped adapter selects only the static `stpd.policy.native_agent` entry
point. Registration uses read-only SDK capabilities and actual installed Runtime
manifest validation; model bytes cannot select executable code. Readiness also
requires the new installed Evidence API. No old installed package fallback is
provided. Human load, explicit takeover, direct Human and Stop use the same
existing application and Runtime command path.

An original pending native request exposes the fixed `models.reconcile` action.
The same Runtime HTTP client binds request ID, Runtime run, game instance and
current recovery epoch before one explicit reconcile POST. Runtime owns the
original Result lookup and terminal evidence. Status refresh does not initiate
lookup, Current, resubmission or Auto. Unknown delivery and taint stay visible.

Evidence adds `verify_agent_session_run_evidence`, a separate typed verifier and
CLI/transfer registry type for native Agent-session runs. It checks strict
namespace, manifests, attestation, immutable file inventory, original request
bindings, consumption metadata and opaque state hashes. It does not decode
numerical memory or confer Human/research/native causality qualification.
Native Stop/restart produces an operational handoff, without fabricating a
legacy Policy, Receipt, successor or numerical evaluation.

The in-game client uses a schema-discriminating operational interface and closed
native DTO. Legacy Policy DTO names and fields remain intact. Agent directive
and Result projection is explicit. Native taint, pending work, uncertain state
or unknown controller prevents execution while retaining direct Human/Stop.

## Source verification

On this increment's working source, with synthetic local fixtures only:

- Relevant application regressions: 212 passed in 44.22 s, comprising 197
  existing tests, 10 native artifact/export/registration unit tests and five
  ephemeral HTTP transport/reconcile tests. The artifact tests use actual tiny
  three-update synthetic Model artifacts; Runtime installation/API admission is
  injected in those tests and is not an installed journey.
- Complete Evidence component check: 220 passed in 8.026 s, including 14 native
  typed verifier tests and unchanged legacy verifier coverage.
- Portable C# production DTO tests: 14 passed, no failures or skips. The new
  pure contract source is linked into the existing portable test project.
- Existing in-game UI boundary tests: 22 passed. A wrapper also compiled the
  portable project successfully; it is not an exact-game/load receipt.
- Ruff on changed application and native test paths passed. Scoped Mypy checks
  of four application files against the current Evidence source passed with
  imported dependency diagnostics suppressed. Direct Mypy against the old
  installed Evidence package rejects the absent new API; strict traversal of
  all Evidence source also exposes pre-existing diagnostics outside this scope.
  This is not a whole Python gate receipt.
- `project:check`: 11 tests passed and zero documentation warnings.
  `project:closeout` and `git diff --check` completed. Closeout reports the full
  integrated baseline and leaves identity/BOM/version review to the lead.

The verifier also consumed a temporary actual Runtime-produced sealed fixture:
15 events covering native attach/consume, programmed opaque state, Auto, original
HTTP 202 pending, Human/release, one original Result reconcile and finalized
Stop. It uses the actual SDK, HTTP and stdio Runtime factory from Runtime base
`f89a5267f43ea206fe39d081f5fd5d3643953ca8` plus the producer's unfrozen draft.
This is compatibility evidence against a draft producer, not an exact frozen
Runtime acceptance receipt and not numerical-state proof.

## Pending gates and rollback

Required next gates: accept and normally merge the frozen actual Runtime and
lightweight Model producers; run actual numerical Python child plus SDK/HTTP
through the same application service journey; review the exact combined source;
complete broader root/package checks and the lead's coordinated exact-game
build. Package pins, BOM, versions, locks and installed candidate identities
belong to the lead's final integration, not this source increment.

Rollback preserves previous private registry, export journals, run records and
sealed evidence, stops the owned Runtime through its existing bound command
path, and selects the previously accepted package/source pair. Unknown delivery
or uncertain exit still requires exact recovery; rollback is not permission to
resubmit. No game, installed Mod, credentials, real recording, provider, paid
compute, real-data training, Human validation, qualification or final G2/V1
acceptance is performed or claimed by this report.
