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
uv run --locked --extra cloud python tools/install_developer_kit.py initialize --directory /ABS/releases/APPROVED_ZIP_SHA256 --config /ABS/project.json
```

`plan` reads only; `prepare` checks bounded archive paths/inventory, clones the exact
source, verifies lock/combination/manifest, stages the published native bytes and
publishes the directory. Dependencies initialize **after** placement so virtualenv
paths remain stable. Neither step changes the game, profile, queues or cloud.
The verified kit inventory selects the locked environment: collection-only kits
install the `cloud` extra; a kit containing any fixed text Runtime pair also
installs `local-models` (Torch, Tokenizers and Safetensors). It does not install
Qwen/Transformers or download model weights. Opening the local Workbench later
retains these installed optional dependencies. The local home, model setup and
recorded-report reading do not require team login; team account pairing is
optional until a member uses collection sharing or uploads.
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
