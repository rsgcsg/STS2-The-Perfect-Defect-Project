# STS2 Host Runtime

> This component lives in `rsgcsg/STS2-AI-PLATFORM`. **Host Runtime** is the
> component name. `headless` is a stable CLI/runtime compatibility term and is
> not a separate current source repository.

Run the **real installed Slay the Spire 2 process** without a display and
control its normal single-player decisions through a verified fair-player
interface. STS2 remains the rules, RNG, legality, task, and effects engine.

The current route launches the shipped Godot executable with `--headless`,
retains the official SceneTree and Mod loader, and uses the Platform Connector
component for observations, Reads, current BoundActions, delivery Receipts, and
successors. It is not a simulator or a wrapper around a reimplemented game.

## Current Status

The Platform is currently a **source/package candidate**. Current component
versions, source revisions, package checksums, protocol, compatibility tuple,
and non-claims are recorded by the root `platform-bom.json` and the Connector
release manifest. This README intentionally does not duplicate those values.

The candidate is not a runtime-sealed release. In particular, no current
Platform-built artifact is claimed here as installed, loaded, Live-exercised,
human-validated, or qualified. Predecessor Host/Connector reports remain
history and rollback evidence only.

The real game still owns rules, RNG, effects, legality, tasks, commands, and
Commit. The shipped Godot route is the highest-confidence Reference Host; any
Managed route is a separately identified Host candidate and cannot inherit
Reference or predecessor authority.

The experimental Managed text adapter covers current treasure chest, relic
selection, and completion pages with exact room/relic bindings. Its build and
non-claims are recorded in [Managed Exact Candidate](experiments/managed-exact/README.md).
Its bounded shop card-removal path uses an exact staged deck selector while
unscoped card selection remains unavailable.

See [Status](docs/STATUS.md), [Compatibility](docs/COMPATIBILITY.md), and
[Evidence](docs/EVIDENCE.md) for exact scope.

## Requirements

- A legally installed Steam copy of Slay the Spire 2
- Node.js 20 or newer
- Git
- macOS arm64 for the currently supported exact runtime, or an explicitly
  acknowledged experimental tuple for maintainer evidence collection

No game binary, asset, save, or decompiled source is distributed by this
repository.

## Quick Start

```bash
git clone https://github.com/rsgcsg/STS2-AI-PLATFORM.git
cd STS2-AI-PLATFORM
npm ci
npm run check
npm run doctor
npm run host:setup
```

`npm run host:setup` is a game-bound installation operation, not a portable
check. It downloads the pinned immutable Platform Connector release, verifies
the archive checksum and native source/SHA/MVID/protocol identity, delegates to
the Connector installer, and records rollback. Never replace the pin with a
branch or an unverified local DLL.

Fully exit all STS2 processes. The smallest real boot gate is:

```bash
npm run host:probe-shipped -- --shared-profile
```

Maintainers testing the experimental isolated Windows route first let the game
create its own native profile files, then grant only the explicit local Mod and
disclaimer consent required by that exact settings schema:

```bash
node tools/headless.mjs reset-profile --isolated-profile h1-train
npm run bootstrap:profile -- --isolated-profile h1-train
node tools/headless.mjs enable-profile-mods --isolated-profile h1-train --settings-schema 8 --accept-ea-disclaimer
npm run probe:shipped -- --isolated-profile h1-train --experimental-build
```

The bootstrap command does not fabricate a save or claim that Connector was
loaded. `--experimental-build` collects evidence; it does not grant support.

The mutation, lifecycle and measurement gates are deliberately separate:

```bash
npm run probe:menu-control -- --shared-profile
npm run probe:journey -- --shared-profile
npm run bench:capacity -- --workers 1,2,4,8
npm run probe:recovery -- --template vanilla-clean --experimental-build
npm run probe:differential -- --template vanilla-clean --seed H1D1FF01 --experimental-build
npm run soak:reference -- --template vanilla-clean --workers 2 --episodes 2 --actions 8 --experimental-build
npm run drill:update
```

`probe:journey` starts and mutates a standard run with a deterministic test
consumer. It is evidence tooling, not a gameplay agent.

`drill:update` exits nonzero for experimental or changed identities by design.
It generates required gates; it never promotes compatibility.

See [Roadmap](docs/ROADMAP.md) for the separate H1.0 Core Release,
Training-Ready, and H* route gates.

## STPD Baseline Smoke

The cheap pre-training regression uses the independent Python consumer and the
exact prepared Managed Host:

```bash
npm run experiment:managed -- audit --candidate .local/candidates/<exact-candidate>
npm run smoke:python -- \
  --candidate .local/candidates/<exact-candidate> \
  --max-actions 64 \
  --evidence-file .local/evidence/stpd-environment-smoke/report.json
```

Any incomplete action catalog, unknown/non-delivery, missing successor,
request/action identity mismatch, seed mismatch, or mid-episode environment
identity change fails the command. It is a cheap regression gate, not full
qualification.

## Run As A Service

Keep the first terminal open:

```bash
npm start -- --shared-profile
```

After it prints `"status": "ready"`, another local program can consume the
versioned Player Environment REST/SDK contract. Inspect or stop the exact
recorded process from another terminal:

```bash
npm run status
npm run stop
```

The normal path is:

```text
installed STS2 + official resources + official Mod loader
-> STS2 Headless process lifecycle and exact-build gate
-> STS2 Connector fair-player Snapshot / Read / BoundAction / Receipt
-> program, agent, test harness, or future training/search adapter
```

Headless owns process lifecycle and identity. Connector owns the fair-player
gameplay contract. A consumer owns policy. Future training adapters may encode
observations, masks, and rewards, but those do not become STS2 truth.

The experimental `ManagedTextMenuSessionAdapter` is an in-process opt-in
projection of complete current Managed map, event, rest-site, deck-upgrade, and combat
catalogs to `text-menu-v1`. It admits only those reviewed surfaces and requires
complete finite action counts plus unique, internally referenced bindings; it
preserves every advertised action and argument in source order. The earlier
`ManagedTextMenuMapSessionAdapter` import remains an alias. It keeps exact
native bindings private and rechecks the current page before dispatch.

Event options retain the game's visible order and locked options remain visible
but non-executable. An executable event choice carries the current native room,
event, and option identities as private operands; the native handler rechecks
those identities, the index, and unlocked state inside the delayed callback
immediately before calling the game's choice. A callback failure after dispatch
has unknown delivery and closes mutation authority for that session. An unfinished
event with no options is unsupported. Rest-site option
submission carries the observed native option identity for
execute-time validation. Deck-upgrade selection carries the exact native
invocation preferences and original card references: selection enters a
preview, preview cancellation returns to selection, and confirmation completes
the native wait. Exiting selection is offered only when native preferences
allow it. Combat actions are direct semantic bindings for the existing MPE
play, potion, discard, and end-turn leaves; MPE owns current-phase and target
binding checks. AnyAlly, potion target types outside the exact native binding
set, non-creature targeted potions without a published target referent, and
unrecognized native target types make the combat catalog unavailable. This
text adapter does not model Live Godot's held-card/target
cursor steps, and no cross-Host trajectory equivalence is implied. A completed
rest option leaves the room visible, may leave other options available, and
advertises a separate room-bound Proceed input. A cancelled native option
stays on the rest page; faults or unresolved native delivery taint the Managed
session. This adapter is not a public Connector service: Managed has no
text-menu HTTP route or controller-lease/attribution owner, and this projection
does not qualify Connector text-menu compatibility.

The current rest/upgrade patch has a new macOS build identity in the candidate manifest.
The separately retained Windows build tuple is historical; it does not admit
this changed patch. Windows preparation remains fail-closed until its new
exact build is measured. The source package candidate is not an installed
or published Runtime update.

## Documentation

- [Document map](docs/DOCUMENT_MAP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Operations](docs/OPERATIONS.md)
- [Compatibility](docs/COMPATIBILITY.md)
- [Evidence](docs/EVIDENCE.md)
- [Development](docs/DEVELOPMENT.md)
- [Release policy](docs/RELEASING.md)
- [Measurement contract](docs/MEASUREMENT.md)
- [Security](SECURITY.md)

For an unknown game build, normal `start` fails closed. Maintainers may run an
explicit non-support probe with `--experimental-build`; passing that probe is
new evidence, not automatic compatibility.
