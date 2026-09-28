# Local model registration engineering boundary, 2026-09-28

This packet connects an already verified text-menu small-B export to the existing
local model catalog. It does not add another model runner or change native rules.
Base is develop `34c6fca858fdb95a29c1831033a9e17f6b53981c` (PR #73 integrated).
The PR and its exact-head checks own candidate source/test status; this note does
not predict their outcome.

## User path and ownership

Model detail → explicit export/check → explicit registration → existing model
list → explicit readiness/load → existing Human/Shadow/One-Step/Auto controls.
Registration itself never installs, loads, starts, changes mode or submits an
in-game action. Refresh only reads metadata. An uncertain registration response
is reconciled by GET, not replayed. Changed source requires a new explicit binding;
old registrations/manifests are retained. No raw data or model weights enter Git.

The application verifies the same model and selected artifact store, rechecks
export bytes and the existing scorer contract, then asks the installed Runtime's
exact bundled Connector SDK for typed current text-menu capabilities. An immutable
config/manifest uses the existing STPD binder. Atomic private registry publication
makes the model visible to the existing lifecycle; an unpublished partial folder
is not a usable selection. Concurrent publication uses one existing file lock.
The registry is local configuration, not a claim that an environment remains valid.
Runtime retains execution-time identity, complete-menu admission and delivery rules.

## Finite support template and its limits

The 62 verbs come from Connector
`host/PlayerEnvironment/Core/PlayerEnvironmentService.cs` text-menu capabilities.
The 36 in-run kinds are source-reviewed from LiveObservationReader's readers,
PlayerEnvironment SnapshotBuilder's specialized providers, and NativeTextMenu's
card-operation, potion and information-page branches. Public capabilities do not
advertise a universal kind list; this is an application support declaration, not
an inferred native qualification certificate.

Startup/single-player/character menus, terminal GameOver, tutorial with unresolved
in-run ownership, settling, unsupported and unresolved-information pages are not
admitted. `token_port.py` checks the actual kind and every current menu verb;
`text_menu_inputs.py` requires interactive, complete current-page input. Unknown
members reject the whole decision instead of filtering the legal menu. No future
unopened page contents are inserted. Native selectors remain one selection at a
time. Legacy `claims.selector=false` is retained unchanged: Runtime does not use
that field to veto native selector kinds; the separate complete-catalog and
selected-index contract remains authoritative. Its historical wording is not a
new selector-quality claim.

## Existing application and package observations

[PR #73 activation receipt](https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project/pull/73#issuecomment-5861133764)
records the explicit application switch to clean `0401ebf`, unchanged profile and
353 artifact IDs, and the actual Safari export of model `0ad03dfd...`. The owner
activation script waited for prior PID exit and successfully acquired/released
the instance lock before opening the candidate. SQLite stat metadata changed;
complete filesystem identity is not claimed. Browser interaction is root-tool
observed, not an independent reviewer's replay.

The real model came from one fixed three-step CPU run of 31 accepted Human input
labels (train 26/dev 5 under existing split rules). The separate 23 canonical
transitions are not added to that count. Native-run independence remains unknown;
dev Top-1 is 0/5. Export operation `d90fac89d706480bac06ef51253dbd49` completed with
538877 payload bytes. Historical training identity is not rewritten to this packet.

A read-only current-page standalone score on 2026-09-28 observed seven combat-page
menu actions. All seven scores were finite and matched the original action order;
one CPU two-thread invocation took about 0.030 seconds. No action was submitted,
no Runtime policy was registered/loaded, and this is not a latency SLA or strategy
quality result.

A private Policy Runtime 0.1.0-rc.10 archive was built and checked from the accepted
source: revision `60d97f7235a4cf8ce4618362c0eca6802eca0e6b`, component tree
`673bff7bf3435c6e51561569e5f17f0fa18b111e`, source digest
`31b9c5c3bf1d9d69570475bc22e37735d6dd7781385d823f461f29c77b48fb41`.
Archive SHA256 is `16e825fbd74b17c6ca065619bc9a272f0bb0577d6c5277570b377de50fbaa954`.
The component check passed typecheck, 121 tests, build, deterministic packaging and
synthetic installed smoke. The package is prepared, not yet production-installed
or loaded by this note. The legacy rc.6 consumer pin remains unchanged.

## Remaining evidence

Source fixtures and isolated package smoke do not establish actual registration,
load, Human/Stop behavior in this installed game, full-scene delivery, learned
memory benefit, strategy quality or full-run completion. Subsequent exact-head CI,
application activation and runtime observations must be recorded separately.
