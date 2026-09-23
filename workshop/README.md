# Workshop release projection — Layer 1

This directory is the Steam Workshop **release workspace**, not an npm workspace,
runtime component, installer or second Platform implementation. It currently holds
metadata and an original project preview only. There is no staged release and no
build/stage/upload command. Do not upload this scaffold.

## External format authority

Bounded reference check: Mega Crit's official
[sts2-mod-uploader](https://github.com/megacrit/sts2-mod-uploader/tree/d7b7e6b16c413d5a124f474f9e5104ef01f76ab1),
especially its [generated workspace template](https://github.com/megacrit/sts2-mod-uploader/tree/d7b7e6b16c413d5a124f474f9e5104ef01f76ab1/template).
No uploader binary or implementation is vendored. The template supplies the
`workshop.json` field names, private visibility, required `image.png` (under 1 MB),
and uploaded `content/` directory. This reference pin is format provenance, not
approval to run a particular uploader release.

```text
workshop/
  .gitignore       deny-by-default local/output policy
  workshop.json    Workshop listing metadata, private by default
  image.png        original project preview; no game/third-party assets
  README.md        release boundary and next-layer requirements
  content/         GENERATED ONLY; absent in a clean clone until future staging
```

Git cannot retain an empty generated directory. Do not add a tracked placeholder
or manually maintained DLL/manifest under `content/`. All unlisted paths are
ignored, including content, `mod_id.txt`, uploader logs/binaries, credentials,
`.env`, Steam state, game files and `.local` evidence. Do not force-add these files.
Boundary tests also reject non-allowlisted tracked files, including force-added
ones. Keep the uploader and real secrets outside this checkout; ignore rules
are accidental-commit protection, not a secret store or upload payload filter.

## One owner per fact

[`apps/game-mod`](../apps/game-mod/README.md) remains the only game-side production
package authority. Its `mod_manifest.json` owns runtime identity, version,
dependencies and compatibility declarations. Its project/build/source-identity
code owns composition and provenance; its lifecycle owns install/load/rollback.
Workshop metadata does not override or duplicate that runtime manifest.
Workshop `dependencies` means Steam item IDs, not runtime manifest dependencies.
No Steam item is registered here; an empty list makes no third-party compatibility
claim. Private metadata does not authorize creating even a private item.

Future staging must project the exact approved game-mod output without rebuilding,
rewriting its runtime manifest or copying source. The build currently emits
`STS2_PLATFORM.dll`, `STS2_PLATFORM.json` and `build-provenance.json`; the owning
build and release contract, not this listing, defines their identities. Choosing
the final upload inventory and verifying its byte/provenance closure is Layer 2.

Connector, Native Foundation, Annotator, Live UI and Policy Runtime stay in their
current paths. Workbench, immutable Collection Tool, device authorization, consent,
raw recordings and upload queues are not Workshop content. Subscribing will not
itself authorize cloud upload, model execution or research admission.

## Responsibility audit / migration decision

At base `d5785d215087719189a3a6bada9addb34b7d95c5`:

| Existing area | Decision and reason |
| --- | --- |
| game-mod project, build, source identity, manifest | KEEP: native composition and exact package authority, not Steam-specific |
| game-mod lifecycle, loaded checks, collection setup | KEEP: installation, rollback and recording binding owners shared by existing distribution |
| ingame-ui source | KEEP: runtime presentation, never release metadata |
| root lifecycle wrappers and component/BOM checks | KEEP: route to existing owners, not Workshop publication logic |
| Python developer-kit packaging/install | KEEP: composes fixed Mod/tool/application distribution, not Steam-specific |

No existing implementation was physically migrated. Adding a release consumer
does not justify moving its producer or changing component source closure/BOM.

## Checks and non-claims

`npm run check:boundaries` includes portable Workshop metadata, preview and actual
Git ignore/index tests. It needs no Steam, credentials, STS2, uploader or native
build. Root governance, identity/BOM checks and `project:closeout` remain required.
This scaffold changes no runtime/install behavior and proves no upload, Workshop
subscription, installation, loading, Human recording or cloud delivery.

## Next layer inputs (not implemented here)

- Explicit approved game-mod output and its exact provenance/compatibility tuple.
- Reviewed allowlisted payload and byte-preserving staging/verification contract.
- Supported Workshop discovery, duplicate-local-install handling and persistent
  config/raw/queue locations outside updateable Workshop content.
- Fixed Collection Tool compatibility and update/rollback policy.
- Separately authorized uploader release/account/item ownership and visibility;
  no credentials are needed for Layer 1.
- Applicable cold-load and bounded Human evidence before advertising deployment.

Rollback for Layer 1 is a source revert only: no installed or cloud bytes change.
