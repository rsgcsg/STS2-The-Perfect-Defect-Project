# Initial installation of a developer kit

This is an operator-assisted macOS installation using the existing Platform lifecycle.
The kit is not a standalone installer. One clean, exact project checkout supplies the installer and Python workbench.
Use the repository root for native commands and python/ for collector commands. Do not build native source
as part of installing a published binary kit. Windows/Linux portable CI is not proof that
this native installation procedure works there. Consult the release's supported game/OS tuple.

## Managed preparation (default for new kits)

Use the approved release's exact checkout to run the following entrypoint from
`python/`. Supply the ZIP and the one independently published SHA256; the existing
`combination.json` supplies every component pin. Choose a permanent directory
outside your development checkout. Do not use the currently running release as a
writable development directory.

```bash
uv run --locked --extra cloud python tools/install_developer_kit.py plan --archive /ABS/kit.zip --sha256 APPROVED_ZIP_SHA256 --releases /ABS/releases
uv run --locked --extra cloud python tools/install_developer_kit.py prepare --archive /ABS/kit.zip --sha256 APPROVED_ZIP_SHA256 --releases /ABS/releases
uv run --locked --extra cloud python tools/install_developer_kit.py preflight --directory /ABS/releases/APPROVED_ZIP_SHA256 --game-directory /ABS/game
uv run --locked --extra cloud python tools/install_developer_kit.py initialize --directory /ABS/releases/APPROVED_ZIP_SHA256 --config /ABS/project.json
```

`plan` reads only; `prepare` checks bounded archive paths/inventory, clones the exact
source, verifies lock/combination/manifest, stages the published native bytes and
publishes the directory. Dependencies initialize **after** placement so virtualenv
paths remain stable. Neither step changes the game, profile, queues or cloud.
`preflight` is read-only: it binds the prepared package pins to their published BOM
asset hashes, checks the target platform/architecture against the explicit local game
and verifies declared Node, Python and .NET framework compatibility. It never installs,
starts or loads anything. Exact system-runtime executable/version pins are not currently
part of the kit contract, so a compatible observation still reports
`admission: unqualified` until those identities are deliberately pinned.
The Host archive identity is directly anchored by its BOM version/source/archive/content
hash fields. The Connector Client BOM directly anchors the selected asset name and archive
hash; its source revision and package-content digest remain self-asserted combination
claims, tied to the combination bytes in the verified kit archive but not independently
anchored by the BOM. Preflight reports these as unverified claims.

An optional private Host group is separate from those legacy public package claims.
Derive it from the reviewed Host source tar and the exact source checkout before assembling
the kit:

```bash
uv run --locked --project python python tools/package_developer_kit.py derive-private-host \
  --source-root /ABS/source-checkout \
  --host-source-archive /ABS/approved/rsgcsg-sts2-host-runtime-1.1.0-rc.23.tgz \
  --host-source-archive-sha256 96fcf1c906dacb43819688cf75a4ed11c6fb257efbde734afbd5998b37b1fc5a \
  --platform-bom /ABS/source-checkout/platform-bom.json \
  --platform-bom-sha256 APPROVED_BOM_SHA256 \
  --output-directory /ABS/private-host-candidate
```

The output directory contains `profile.json` and the derived `runtime.tgz`; pass those
files and their printed SHA256 values to the existing `--private-host-profile` and
`--private-host-archive` kit-packaging options. The producer checks the original tar
hash and npm file inventory, compares every original packaged file with the clean
BOM-bound Host source tree, and re-reads the Host and Connector component identities
from that checkout. It copies the locked Connector SDK source to a temporary directory,
installs its lockfile into a new private npm cache with scripts disabled, builds its
`dist`, and uses the locked TypeScript and Zod package integrity. It does not use an
author npm cache, pre-existing SDK `dist`, or `node_modules`.

The derived tar records the original archive SHA and source package-content digest,
per-file source inventory, Host source/tree/digest, producer tool git-blob and SHA256
identities, Node/npm/TypeScript tool versions, selected SDK source/tree/digest and bundle
hash, locked Zod integrity and bundle hash, and the derived package-content/archive
hashes in the external profile. Its internal `private-host-derivation.json` repeats the
input and recipe identity. Derivation requires the clean checkout executing the producer
and rechecks that producer identity after completion. The verifier requires the receipt to
match the profile, every original Host file except the transformed `package.json` to be
byte and mode identical, the exact allowed manifest changes (`dependencies`,
`bundleDependencies`, and `files`), and rejects any file outside the source inventory,
SDK/Zod bundles, shrinkwrap, and derivation receipt. Only the two new derivation-format
profiles carry this receipt; ordinary kits without a private Host group and older
private profile groups remain readable.
Kit consumers verify the receipt against their own clean, exact-revision source clone;
it may be a separate clone from the producer.

The original Host `package.json` selects the public SDK rc1 URL. The derived package
changes that dependency to the SDK package version selected by the kit BOM, adds Zod as
an exact dependency, declares SDK and Zod as `bundleDependencies`, and adds
`npm-shrinkwrap.json` plus the derivation receipt to the package file allowlist. It keeps
Host source files unchanged. The checked-in Host `package-lock.json` is not in the
approved source tar and is not used as the derived lock. The current BOM SDK pairing is
1.3.0-rc.5 from Connector source 89ffa45cb6401827e5f64d6f1927a3843ae6ebf7, tree
98643b4de67073dca4d95da2f9d753f5198a33f0, source digest
6aa96c8bbc6d69f2bf5fd39c52816747fc1c608da559fe690c309742a3729f62. Zod is 3.25.76
with the integrity in the current SDK lockfile. Old rc1 tests or canary evidence do not
qualify this changed pairing.

Before the producer returns, and again when the kit packager consumes a v2 profile, the
exact derived tar must install from a new empty npm cache with explicit `--offline`,
empty user/global npm config, and `--ignore-scripts`. It then imports a side-effect-free
Host source module (the Host package has no package-root import entry), the selected SDK,
and Zod. Host setup/start and game launch are never run by these packaging checks.
The producer needs network access only to obtain the SDK's public lockfile dependencies
into its temporary fresh cache; the resulting tarball itself is tested offline. This
proves the Host/SDK/Zod package closure, not full Node/Python kit offline operation or a
loaded/runtime qualification.

The approved Host rc23 source tar declares the public Connector SDK rc1 release URL.
A private kit candidate instead binds to and bundles the SDK version/source selected by
the current BOM (currently 1.3.0-rc.5); this is a dependency change in the private
distribution. Tests or canary evidence for the old rc1 dependency do not qualify that
new SDK pairing. The actual derived bundle must pass fresh offline install/import checks
and separate selected-profile/runtime validation before it can be called usable.
This is a private kit archive format; it does not modify the Host component source,
rewrite BOM `public_packages`, or relabel a public release.

`prepare` stages an included candidate under
`source/python/.local/private-host-runtime/`; `initialize` verifies and extracts the
fixed Host package there. This does not automatically configure a Managed environment.
After a separately reviewed exact Managed candidate is available, use the existing
stopped-Workbench `environment-profile` command to select it explicitly:

```bash
uv run --locked --extra cloud python -m spireagent.workbench project environment-profile \
  --config /ABS/project.json \
  --candidate-directory /ABS/APPROVED_MANAGED_CANDIDATE \
  --host-package-directory /ABS/releases/APPROVED_ZIP_SHA256/source/python/.local/private-host-runtime/package \
  --host-package-pin /ABS/releases/APPROVED_ZIP_SHA256/source/python/.local/private-host-runtime/host-package-pin.json \
  --input-profile text-menu-v1
```

Kit status reports the candidate package as staged/verified separately from the
public `developer-combination` dependency tuple. It reports environment-profile
selection as unobserved; configuration is not a loaded or running Host. The currently
installed CollectionTool remains its own immutable Annotator/Evidence consumer and is
not rebuilt or relabeled by this kit change. The candidate archive closure does not
qualify the full kit's Node/Python setup or complete offline operation.

Preflight resolves the explicit game directory and the doctor's selected directory to the
same canonical root. This permits normal symlinked ancestors and a selected-directory alias,
while rejecting symlinks and escapes below the canonical game root for the four identity
files it reads. It records the doctor's point-in-time `game_running` observation and may
hash these on-disk files while the game is running; it does not inspect process memory or
claim that the running process loaded those same bytes. This read-only snapshot has no lock
against a concurrent process state change or local file replacement. Deploy still requires
the game to be closed and independently rechecks native game identity before writing.
The immutable kit manifest may select the fixed `python_environment_profile`
`cloud` or `cloud-local-models`. The M0 setup uses `cloud-local-models`, including
when the kit contains no model Runtime pair. This profile controls the locked Python
dependencies only; it does not make a model eligible, select a runtime or establish
model qualification. Initialization and registration use the same centrally approved
extras for the verified profile. Explicit profiles use developer-kit schema v2;
older v1 installers reject v2 kits instead of ignoring the selection. Kits without
an explicit profile retain v1. It does not install Qwen/Transformers or download
model weights. Older kits that omit the field remain supported: their effective
profile is inferred as `cloud-local-models` if any fixed text Runtime pair is bundled,
and `cloud` otherwise. An explicit `cloud` profile cannot accompany a bundled text
Runtime pair. Opening the local Workbench later retains these installed optional
dependencies. The local home, model setup and recorded-report reading do not require
team login; team account pairing is optional until a member uses collection sharing
or uploads.

For kits whose `combination.json` declares
`workbench_launcher_schema: spireagent/workbench-launcher-v1`, successful
initialization normally installs a fixed per-user macOS entry at
`~/Library/Application Support/spireagent/workbench/open`. The game Mod can
invoke it only after an explicit Open action reports the Workbench stopped. The
entry accepts no arguments, verifies its exact kit and bound config through the
release's installer owner, then reuses or opens that Workbench without syncing
dependencies. Ready opens the existing URL; stale or identity-mismatched state
requires operator recovery. Older kits without the capability retain their
existing initialization behavior and do not receive this entry. This entry is
currently macOS-only; Windows is not qualified.

To change the game entry to a different existing project, use the selected approved
release's `install-launcher` entry with `--config /ABS/target/project.json` and
`--expected-launcher-binding-sha256 SHA256_OF_CURRENT_LAUNCHER_JSON` and
`--expected-open-sha256 SHA256_OF_CURRENT_OPEN`. Both compare-and-swap hashes are
required together. Concurrent owner operations are serialized under the install lock.
The installer validates the selected release, source, lock and target project; a changed
or partial prior pair stops replacement. It does not migrate models, saves, queues or
consent, start a service, or redirect a running game's already registered URL. Stop and
reopen the chosen Workbench through its owner and verify the game button's observed URL
separately.

For an environment-only setup, add `--defer-launcher` to `initialize`. Dependency and
runtime setup completes, the result reports `workbench_launcher: deferred`, and the
global launcher files remain untouched. This option is valid only for `initialize`.

Before a reviewed change, the exact existing pair can be retained as a private historical
snapshot. The snapshot records bytes, modes, hashes and the verified release identity. It
is explicitly `launchable: false`; archiving a legacy pair does not qualify it for restore
or execution. A legacy config whose stored combination differs from its archived kit cannot
be prepared as a restore target. The mismatch must be resolved through the approved
Workbench setup flow before creating a launchable target snapshot.

```bash
shasum -a 256 "$HOME/Library/Application Support/spireagent/workbench/launcher.json" \
  "$HOME/Library/Application Support/spireagent/workbench/open"
uv run --locked --extra cloud python tools/install_developer_kit.py backup-launcher \
  --snapshot-directory /ABS/private/launcher-history-2026-10-04 \
  --expected-launcher-binding-sha256 BINDING_SHA256 \
  --expected-open-sha256 OPEN_SHA256
```

`backup-launcher` uses the executing prepared release as its owner. It obtains the prior
release path from the verified current binding, checks that prior release's own archive,
source, lock and Workbench identity, and records the pair without granting launchability.

To make a restore target, first prepare the pair from a verified prepared release and a
private project config. This validates that config against that release's archived
combination, then records the exact owner-generated binding and executable without
publishing them globally:

```bash
uv run --locked --extra cloud python tools/install_developer_kit.py prepare-launcher-target \
  --directory /ABS/releases/TARGET_KIT_SHA256 \
  --config /ABS/target/project.json \
  --snapshot-directory /ABS/private/launcher-target
```

Only a `launchable: true` target snapshot passes restore validation. Restore rechecks the
target kit archive, source, lock, Workbench digest and config combination, then compares
both currently installed file hashes under the owner lock:

```bash
uv run --locked --extra cloud python tools/install_developer_kit.py restore-launcher \
  --snapshot-directory /ABS/private/launcher-target \
  --snapshot-manifest-sha256 TARGET_MANIFEST_SHA256 \
  --expected-launcher-binding-sha256 CURRENT_BINDING_SHA256 \
  --expected-open-sha256 CURRENT_OPEN_SHA256
```

A caught publication exception attempts to restore both prior file byte sequences and
modes. If either rollback write fails, the owner reports `launcher_recovery_required` and
leaves any historical snapshot intact. The two-file update is not crash-atomic: forced
termination or power loss can leave an identity mismatch. Launcher identity checks fail
closed rather than execute a mismatched release.

A kit may additionally contain an independently approved `text-runtime/profile.json`
and `text-runtime/runtime.tgz`. Its inventory and external ZIP SHA256 bind both;
the packager verifies the archive through the ordinary bundled Runtime installer in
a disposable directory. `prepare` stages these bytes in the release source's
ignored `.local/` area. For such a kit, `initialize` creates a local no-login
project profile if absent, using the config path's directory for state, then
installs the text Runtime in its separate `models/text-menu-v1` slot through the
selected release's CLI. It does not register a model, load weights or start a
Runtime. A kit without these files provides no text Runtime preparation; its
original initialization behavior remains. If initialization fails after profile
creation, retain and inspect that profile and retry only after checking the
reported failure and stopped Workbench state.
An independently supplied `m2-runtime/profile.json` and `m2-runtime/runtime.tgz`
pair is optional and separately inventoried. The profile schema is
`stpd/local-text-m2-runtime-v1`; its archive hash and bundled closure are checked
through the same installer. `prepare` stages it under ignored `.local/`, while
`initialize` installs it to `models/text-menu-m2-v1`, distinct from the older
text Runtime. Missing one member of either pair, a different archive hash, or
staged-byte drift blocks that candidate. Neither optional pair is synthesized
from the source checkout or a release URL; without an approved pair its profile
remains unavailable. The M2 pair enables only later explicit model registration
and readiness checks, not automatic model loading or qualification.
An optional `m2-v2-runtime/profile.json` and `m2-v2-runtime/runtime.tgz` pair
has its own inventory entry and `stpd/local-text-m2-runtime-v2` profile schema.
The packager checks the exact archive and all five text-menu-v2 Connector SDK
methods in a temporary installation before publishing the kit. `prepare` stages
the pair under ignored `.local/`; the Workbench's explicit **检查 v2 记忆运行环境**
action reads it only from the selected verified release and installs it into
`models/text-menu-m2-v2`. A missing pair stays unavailable. `initialize` also
installs an included v2 pair through the selected release's CLI and its same
verified profile and archive. Existing v1 pairs keep their prior slots. The v2
pair does not supply model weights or
establish native or policy qualification.
A prepared directory is never overwritten; `status --directory ...` rechecks its
original archive, source, tool and staged bytes. Failed temporary preparation is
removed; initialization can be retried in the same directory while its Workbench
is stopped. Always pass the same private profile path, including before it exists;
initialization holds its existing Workbench lock when present. Keep the original archive and all older in-use release directories.

With STS2 fully closed, the same entrypoint can run `deploy --directory ...
--game-directory /ABS/game`. It checks actual game/dependency bytes against native
provenance and calls the existing lifecycle, which checks source/artifact identity
and retains rollback. It does not rebuild, launch or claim a loaded game. Use the
cold-load commands below from that permanent source directory.

After creating the private Workbench profile, stop it and run
`register --directory ... --config /ABS/project.json` through this entrypoint.
The selected release's registration owner enforces the stopped-profile lock and
refuses replacing a different registration. Daily consent and upload activation
still require the member's team account. For existing tools/queues use the existing explicit
collection-upgrade prepare/activate procedure: pending evidence is never rewritten.
A Workbench-only change does not require Mod deployment when published Mod/tool
bytes and their necessary contracts remain unchanged.

The manual staging instructions below remain the recovery path for old released
kits whose exact source predates this entrypoint. Do not transplant new executable
files into an old pinned checkout to make its source appear current.

## Verify and stage the approved bytes

1. Verify the ZIP SHA256 against its independently approved release receipt before extraction.
   Read `combination.json`, the release's exact refs, game identity, qualification and rollback.
   Obtain the project checkout at the release commit in one durable directory.
2. Install Git, Python 3.11/uv, Node 20+ and the fixed tool's declared .NET runtime.
   The member owns a local game installation; game files are never distributed in the kit.
3. Fully close the game and stop the existing workbench. Preserve its configuration, complete
   old tool, raw recordings and outbox. This installation does not migrate a queue.
4. In the clean project checkout run `npm ci`. Compare the tracked
   `apps/game-mod/mod_manifest.json` to the kit's `mod/STS2_PLATFORM.json`; stop on differences.
   Stage only these ignored outputs, creating their parent directories if necessary:

| Kit file | Destination within the retained project checkout |
|---|---|
| `mod/STS2_PLATFORM.dll` | `apps/game-mod/bin/Release/net9.0/STS2_PLATFORM.dll` |
| `collection-tool/game-mod/build-provenance.json` | `apps/game-mod/bin/Release/net9.0/build-provenance.json` |
| `collection-tool/sts2-human-annotator.dll` | `components/annotator/src/STS2HumanAnnotator.Tool/bin/Release/net9.0/sts2-human-annotator.dll` |
| `collection-tool/sts2-human-annotator.deps.json` | same destination directory, same name |
| `collection-tool/sts2-human-annotator.runtimeconfig.json` | same destination directory, same name |
| `collection-tool/STS2HumanAnnotator.Core.dll` | same destination directory, same name |

Require `git status --porcelain` to remain empty. Set `STS2_GAME_DIR` to the actual Steam game
directory containing `SlayTheSpire2.app`, not the app itself. Do not set recording-root or
status-path overrides. Use the packaged tool's `identity` command to verify the Mod and the
installed game's `sts2.dll` SHA256/MVID against build provenance. Check the game release and
the pinned GodotSharp/Harmony bytes too. A version label alone is not the qualification tuple.
The owner lifecycle validates source/artifact provenance; this independent game-byte comparison
is also required. A mismatch requires compatibility qualification, not a forced install.

## Deploy and cold-load through the existing owner

From that exact project checkout, run these commands separately and retain their receipts:

```bash
npm run game-mod:doctor
npm run game-mod:deploy
npm run game-mod:doctor
npm run game-mod:launch
npm run game-mod:verify-loaded
```

The first doctor must show the intended directory and no running game. Deploy backs up
managed files/configuration and verifies installed identity. Launch selects no gameplay.
Verify-loaded must confirm the exact current process, loaded Modset, components and execution
admission. Do not run a build command here: it would replace the supplied release artifact.

On first macOS installation, a Human may need to enable Mods and exactly `STS2_PLATFORM` in
the native game interface, then fully close and repeat launch/verify-loaded. The lifecycle
does not edit macOS Mod-enable settings. Do not invent a settings file or remove unrelated Mods.
Initial Recorder status storage may point inside the retained project checkout; do not delete
or relocate that checkout after installation. Later workbench binding preserves this status path.

## Continue collection and retain recovery

Follow [the everyday workflow](B_PIPELINE_HANDOFF.md): register the whole fixed tool while the
workbench is stopped, reopen the same private profile, confirm daily consent, prepare, bind
with the game closed, cold-load, check the current root and explicitly activate uploads.
The member's first real Close-to-receipt check is separate from installation evidence.

For rollback, stop delivery and close the game. From the same retained project checkout and
game directory run `npm run game-mod:rollback`. It uses the recorded installed provenance and
backup; it does not accept an arbitrary backup path. Preserve old/new receipts. Cold-load the
restored compatible pair with its owning checkout and verify it. A first install may roll back
to no Platform installation. Native macOS Mod-enable choices are outside the file rollback.

Do not rewrite old queues, historical failures, consent or loaded identities to force an
upgrade. Use the [maintenance procedure](B_PIPELINE_HANDOFF.md#daily-work-upgrades-and-incidents).
