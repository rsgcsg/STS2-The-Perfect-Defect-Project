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
The adapter also preserves a complete observed native terminal page with no
menu actions and its exact win/loss fact.

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

The public `sts2-managed-pe-driver` JSONL transport now exposes `text_observe`
and `text_submit` beside raw `observe`/`step`. `text_observe` returns the existing
text observation context shape (`schema`, `snapshot`, `game_continuity_id`) from
one Managed session. `text_submit` requires an advertised `action_id`, exact
`expected_snapshot_id`, `expected_game_continuity_id`, and stable
`mutation_request_id`; the driver supplies the fixed `text-menu-v1` profile to
the adapter. A successful reset rotates the Host-owned Managed episode ID,
including for a repeated seed. A failed reset disables both public action
routes until a successful reset, since native reset may already have replaced
the simulator. Close ends the session. Unknown native delivery cannot be
retried or bypassed by raw `step`; the process must be replaced.
If native delivery is known but successor projection fails, the result retains
`delivered` with no successor and an explicit projection failure; further
mutations are closed until process replacement. A malformed post-offer JSONL
reply likewise quarantines the Python consumer process. These are Managed
JSONL consumer semantics, not a Reference HTTP lease or native
RunState attestation. The locked Connector SDK package has no text context
decoder export yet; this driver emits its existing context shape while the
real adapter and raw snapshot SDK retain their respective checks.

An explicit `input_profile: "text-menu-v2"` on the same JSONL `text_observe`
and `text_submit` commands selects the separate Managed v2 text adapter and
the `*-2` context/Snapshot/result schemas. Omitting the profile preserves v1.
On a complete ready combat page, v2 groups the existing exact `play` leaves
into text-only card and optional target choices; a card without a target goes
straight to card-only confirmation. Back and cancel change only this menu.
The final `play` choice alone submits the current private BoundAction once,
which the Managed native path revalidates. Other complete Managed pages retain
their current native leaves under the v2 schema; unsupported or incomplete
catalogs remain unavailable. A v1 observation does not reset a v2 selection.
Raw mutation, reset, changed source/catalog, and unknown delivery invalidate it.
Both profiles share the driver's episode and mutation-request fences. The
public Python consumer opts in with `observe_text_menu(input_profile="text-menu-v2")`
and `submit_text_menu(..., input_profile="text-menu-v2")`; omitted profile
still selects v1. This does not expose a Connector HTTP/controller lease or
turn historical v1 Human/model inputs into v2 data. The pinned
Connector SDK remains at its older released version. An isolated source-level
cross-check used the PR106 SDK's strict v2 decoder; this package does not
claim to consume that unpublished SDK build.

The JSONL driver's explicit close, stdin EOF, and supported process signals
share one native-child cleanup owner. A Python force-close first closes stdin
so the Node driver can interrupt an in-flight request and reap its child on
Windows as well as Unix; only after a bounded wait does Python terminate Node.
The fallback bounds the client but cannot itself prove native-child cleanup if
the owner fails. The Python client raises a persistent `DriverCleanupError` on
fallback or nonzero driver exit. Startup failures carry the original exception
as `DriverInitializationError.__cause__` and a `cleanup_confirmed` flag. A
valid correlated unknown result is returned unchanged; a later explicit
`close()` exposes any separate cleanup error. A pending or unknown mutation is never reported as
`not_delivered` merely because shutdown suppressed a late reply.

For an opt-in exclusive controller of one Managed episode, the JSONL driver
accepts `claim_control` after `reset`. Its `claim_control_result` returns a
random `control_token` and `control_epoch` bound to the current
`runtime_instance_id` and `game_continuity_id`. While held, every `step`,
`text_submit` (v1 or v2), and `reset` must carry both values; another claim and
uncredentialed or stale mutations fail before native dispatch. Reads remain
available. `release_control` requires the same two values and returns
`release_control_result` with `status: "released"` only after earlier queued
native work has returned its actual delivered or unknown result. A duplicate
release fails. A controller-authorized reset revokes the old episode's
credentials before native mount, even if mount fails. Requests admitted under
an older control state cannot become valid after a claim, release, or reset.
Without a controller, explicit operator mutations retain the existing path.
Native unknown delivery and successor-projection taint remain closed after
release; a tainted session cannot claim a new controller. `close`, EOF, and
signals revoke local admission for cleanup but never report a confirmed
controller release. These commands are an internal Managed session permission
boundary, not a Connector HTTP lease, Connector Modset/MVID identity, or model
gameplay actions. The token appears only in the claim response and caller's
explicit credential fields; observations, episode identity, action results,
and release responses do not echo it.

## One Managed Host service

An explicit Host operator can keep one prepared Managed candidate alive for
multiple local clients. The service creates one native child and one
`ManagedPeDriverSession`; it does not start another game when a client attaches.
The selected candidate and installed game are still admitted by the existing
exact-build start path. For a private developer directory:

```sh
mkdir -m 700 .local/managed-host
node components/host-runtime/tools/managed-host-service.mjs \
  --candidate .local/candidates/<exact-candidate> \
  --client-attachment .local/managed-host/client.json \
  --manager-attachment .local/managed-host/manager.json
```

The CLI emits a token-free ready record. It writes separate, exclusive mode-0600
attachments containing the loopback endpoint, service instance ID, actual Host
package identity and bearer token. The client attachment permits ready,
observation, controller claim, text submission and release; raw `step` requires
the manager bearer. The manager attachment additionally permits explicit reset,
recovery of a crashed controller, and Host close. Keep the manager attachment
out of model processes and browser responses. The Host-owned Python API
`sts2_headless.managed_service` provides `launch_managed_host_service`,
`ManagedHostServiceClient.from_attachment`, and
`ManagedHostServiceManager.from_attachment` for applications that launch and
attach without a terminal. The launcher detaches the Host process; dropping a
client or stopping Policy Runtime does not stop the game. After an offered POST,
lost, malformed, truncated, uncorrelated or generic failure replies raise
`ManagedHostUncertainError`; clients must not retry the native intent as though
it had been rejected. Only a validated explicit Host rejection is known not
applied.

Authenticated `GET /v1/ready` reports the same service, candidate, exact game,
runtime, environment and current episode identity to every client. Its
`text_menu_contracts` entries describe the current v1/v2 snapshot and receipt
schemas, reviewed interaction kinds and emitted action verbs. `game_over` is an
observed terminal kind with zero actions. These lists support registration;
only each complete current menu catalog and its native binding authorize an
action. Every POST
requires that service ID in `X-STS2-Managed-Service-ID`. `POST /v1/command`
forwards the existing Managed observe, read, identity, claim, text and release
commands to the same driver. A claim requires expected runtime and continuity;
while held, text observation and submission require its private token and epoch.
Manager `POST /v1/admin/reset` requires the expected current runtime and
continuity, including explicit `null` before first reset. These checks execute
inside the driver queue so a delayed HTTP body cannot acquire a later episode.
`GET /v1/admin/status` exposes the held epoch but no token. To recover a
disconnected controller, the manager calls `POST /v1/admin/recover-control`
with that exact epoch, runtime and continuity; the service uses its retained
private claim credential and waits for prior native work and an actual release
ack. Recovery never clears unknown-delivery or projection taint. Only explicit
manager `POST /v1/admin/close` or Host process shutdown reaps the child; process
shutdown is not a confirmed controller release. The service is loopback-only,
checks Host, Origin and bearer, and bounds request body size and time. It is a
Host session attachment surface, not a Connector HTTP lease or a gameplay
legality authority.

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
session. This adapter is not a public Connector service: the Managed HTTP
attachment route and exclusive permission belong to Host, not Connector
lease/attribution; this projection does not qualify Connector text-menu
compatibility.

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
