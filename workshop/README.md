# Workshop release projection — Layers 1–4A

This directory is the Steam Workshop **release workspace**, not an npm workspace,
runtime component, installer or second Platform implementation. It holds metadata,
an original project preview and generated candidates from explicit approved inputs.
Staging does not build, install or upload. Preparation separates build/proposal
(Phase A) from explicit approval/finalization (Phase B). Layer 4 adds publication
preflight and a separately authorized executor. A prepared or ready candidate is
not an approved or published Steam release. Layer 4A never invokes upload.

## External format authority

Bounded reference check: Mega Crit's official
[sts2-mod-uploader](https://github.com/megacrit/sts2-mod-uploader/tree/d7b7e6b16c413d5a124f474f9e5104ef01f76ab1),
especially its [generated workspace template](https://github.com/megacrit/sts2-mod-uploader/tree/d7b7e6b16c413d5a124f474f9e5104ef01f76ab1/template).
No uploader binary or implementation is vendored. The template supplies the
`workshop.json` field names, private visibility, required `image.png` (under 1 MB),
and uploaded `content/` directory. Layer 4A separately promotes this exact source
commit to a local uploader build candidate, not authorization to publish.

```text
workshop/
  .gitignore       deny-by-default local/output policy
  workshop.json    Workshop listing metadata, private by default
  image.png        original project preview; no game/third-party assets
  README.md        release boundary and next-layer requirements
  content/         GENERATED ONLY; absent in a clean clone until staging
  staging-receipt.json  generated LOCAL receipt, outside the upload payload
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

Staging projects the exact approved game-mod output without rebuilding, rewriting
its runtime manifest or copying source. The build's `package.files` currently
names exactly `STS2_PLATFORM.dll` and `STS2_PLATFORM.json`: these two files are the
entire payload. `build-provenance.json` is required validation input but is not a
runtime file and is NOT uploaded. Its SHA and source/game/artifact facts remain
in the private staging receipt. Known `.NET` output `STS2_PLATFORM.pdb` and
`STS2_PLATFORM.deps.json` may be present in the input directory but are excluded.
Any other input entry, including directories, fails closed. This is an explicit
small input/output allowlist, not recursive copying plus a blacklist.

## Deterministic staging

From a clean committed checkout, select an existing game-mod build output and the
SHA256 of its independently reviewed `build-provenance.json`:

```sh
npm run workshop:stage -- --source "/absolute/approved-output" --provenance-sha256 APPROVED_64_HEX_SHA
```

The default PE reader is the existing built Annotator Tool at
`components/annotator/src/STS2HumanAnnotator.Tool/bin/Release/net9.0/sts2-human-annotator.dll`.
If necessary provide `--identity-tool "/absolute/trusted-tool/sts2-human-annotator.dll"`
from a trusted complete tool distribution. .NET must be available. Staging never
builds or downloads that tool and never executes the candidate Mod assembly.
No approval is inferred from a path, file age or a newly calculated hash: the
operator/next-layer release owner selects the provenance pin. This integrity pin
is not a signature or native/Human qualification.

Validation requires the known provenance schema, clean producer/current source,
the current game-mod source closure via its existing `sourceSetIdentity` /
`sourceSetMatches`, the exact two-file package inventory, and byte-identical
runtime manifest against `apps/game-mod/mod_manifest.json`. The existing Tool's
PE reader independently checks SHA256/MVID against pinned build provenance. Game
identities are retained and structurally checked, not requalified against a running
game. Unrelated workspace commits are permitted when the owning compiled source
identity matches; old native source is rejected, not relabelled as current.

The only output location is this checkout's `workshop/content/`. Before replacement,
the stager checks the output tree/ancestors for links and unsafe file types and
rejects overlapping source/output paths. A local exclusive lock prevents concurrent
stagers. It removes the old generated content and receipt, validates input, writes
only the allowlisted bytes to an isolated sibling directory, re-reads/verifies
bytes and hashes, rechecks source stability, then renames the candidate into place.
Ordinary validation failures leave no candidate/receipt. Unsafe paths or an existing
lock fail before cleanup, preserving the old files; neither failure reports success.
An interrupted process may leave an ignored `.stage-*` directory or lock: inspect
it and confirm no stager is running before removing that generated state. There
is no automatic retry or stale-lock takeover. Do not run an uploader concurrently.

The deterministic JSON receipt records current and producer workspace revisions,
component source identity, game-mod version, source artifact path, SHA/MVID,
manifest/provenance hashes, game identity, ordered payload hashes/sizes and excluded
input sidecars. It has no clock-dependent field; identical inputs at the same paths
and source produce the same bytes. Paths make it local operator data, not a public
receipt or a new runtime authority. Preserve the original approved build/provenance.
Only `content/` is prospective upload payload; never upload the whole workspace.

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

`node --test tools/workshop-stage.test.mjs` runs synthetic portable staging mechanics
fixtures with injected source/PE readers; these are NOT game DLLs or native evidence.
The production CLI always uses the real owner readers, with no fixture bypass flag.
`npm run check:boundaries` includes these tests plus metadata, preview and actual
Git ignore/index tests. Tests need no Steam, credentials, STS2 or uploader.
Root governance, identity/BOM checks and `project:closeout` remain required.
This scaffold changes no runtime/install behavior and proves no upload, Workshop
subscription, installation, loading, Human recording or cloud delivery.

## Layer 3 Phase A: checked build proposal

From a clean committed checkout with the normal locked developer dependencies:

```sh
npm run workshop:prepare -- --build
```

This calls the existing Host workstation discovery and strict process enumeration,
stops if STS2 is running (never kills it), runs `check:repository` and
`game-mod:check`, rechecks source/process state, and invokes `game-mod:build`.
The authoritative build owns exact compilation and provenance. Shared read-only
Layer 2 inspection validates output allowlist, runtime manifest, source closure
and the existing Tool's SHA/MVID result without staging. No second build or
identity implementation is introduced.

The ignored `build-proposal.json` and console result bind exact workspace and
component source, game release/assembly, platform/architecture, Mod version,
DLL SHA/MVID, manifest/provenance SHA, all build file hashes, proposed payload
inventory, and Workshop metadata/image hashes. The result is explicitly
`AWAITING_APPROVAL`, `approved: false`, `checked_build_proposal_only`.
Calculating provenance SHA is not approval. No `content/` or staging receipt is
created or changed by Phase A; any prior staged candidate remains unrelated.
No prepared success is emitted. Output contains private local paths: do not
commit or paste the full proposal publicly.

A failed attempt removes its previous proposal. An exclusive `.prepare.lock`
blocks concurrent attempts; an abruptly terminated attempt may leave that lock
or an incomplete JSON proposal. Neither is success/approval. Verify no prepare
process is active before removing a stale lock; restart Phase A. Never accept a
file merely because it exists. Changes after proposal always require revalidation.

### Phase B: explicit approval, no rebuild

After separately reviewing and approving the Phase A provenance SHA:

```sh
npm run workshop:prepare -- --approve-provenance-sha256 APPROVED_64_HEX_SHA
```

This requires the original unchanged `build-proposal.json` and retained build
directory. It never runs a build. The explicit pin must match proposal and actual
provenance bytes. It revalidates every build file (including excluded sidecars),
the exact compiled source identity, runtime manifest, private listing and preview,
then invokes the existing `workshop:stage` command with that pin. It compares the
entire returned receipt contract, rehashes the output and checks byte equality.

The specifically authorized Phase B tooling evolution may advance workspace HEAD
without rebuilding the approved artifact. A Git ancestor/path gate permits only
the enumerated Workshop prepare/finalize tooling, their tests, boundary test and
README paths. Other source/lock/metadata changes reject. All component identities
and digests must still equal Phase A exactly. The original producer/workspace SHA
is never rewritten; `prepare_workspace_revision` separately identifies the tool
HEAD. This is not a general approval-transfer policy for arbitrary new commits.

Only listing/preview and the exact two-file content directory are candidate upload
inputs. README, ignore policy, proposal, staging/prepared receipts and the active
prepare lock are explicit local support files, not payload. Any other workspace
entry or extra payload fails closed (including mod_id.txt, logs, Steam state,
credentials, provenance, game files and source). No recursive upload is authorized.

The ignored `prepare-receipt.json` records `PREPARED_CANDIDATE`, producer and tool
HEADs, permitted changed paths, proposal SHA, approved provenance SHA, DLL SHA/MVID,
runtime manifest and metadata/preview hashes, exact payload inventory, game/build
identity and Layer 2 staging-receipt schema/hash. It is local release logistics,
not runtime authority or Steam payload. Same unchanged inputs/tool HEAD produce
the same receipt. An ordinary failure removes old prepared success; it preserves
the original proposal and build. New Phase A also invalidates old prepared success.
Interrupted lock/output is not proof; verify no process is active before recovery.

Any non-permitted source, build or metadata drift requires a new Phase A and new
approval, never a rewritten proposal/hash. Phase A alone cannot produce prepared
success. Neither phase authorizes uploader execution or implies installed, loaded,
Workshop subscription or Human qualification.

## Later inputs

- Explicit approved game-mod output and its exact provenance/compatibility tuple.
- The exact prepared receipt and unchanged candidate form the input to a separately
  authorized publication layer; retain producer/tool identities and explicit approval.
- Supported Workshop discovery, duplicate-local-install handling and persistent
  config/raw/queue locations outside updateable Workshop content.
- Fixed Collection Tool compatibility and update/rollback policy.
- Separately authorized uploader release/account/item ownership and visibility;
  no credentials are needed for Layer 1.
- Applicable cold-load and bounded Human evidence before advertising deployment.

Rollback is a source revert and discarding the generated candidate after checking
its path. Re-stage a retained approved build only from its compatible exact source.
No installed or cloud bytes change. Layer 3 is stacked on the exact Layer 2
parent, retaining Layers 1–2 unchanged; do not integrate the Layer 1–4 stack into
develop until the separately authorized final integration.

## Layer 4A: exact uploader and publication preflight

The pinned upstream is `megacrit/sts2-mod-uploader` at
`d7b7e6b16c413d5a124f474f9e5104ef01f76ab1`. Do not follow floating main when
executing. Keep its independent clean clone and all outputs outside this source
checkout, preferably in a private `.local/` release-tool directory. Never vendor
source, binaries or Steam native libraries here or place them in `content/`.

Audit basis: upstream README, global.json, project, release workflow, Program,
UploadCommand, SteamCallResult and Log at that commit. SDK 9 uses net8.0,
Steamworks.NET 15.0.1 (upstream bundled NuGet feed) and the pinned preview
System.CommandLine dependency. Its workflow publishes single-file, trimmed,
RID-specific output. No upstream license file was present at this pin: this
integration builds an operator-local tool, not a redistribution/license claim.
Upstream trim warnings are not runtime qualification. No Steam credentials or
Steam initialization are needed to build or validate the candidate.

```sh
npm run workshop:uploader -- --source /PRIVATE/exact-uploader-source --artifacts /PRIVATE/new-build-directory --rid win-x64 --receipt /PRIVATE/uploader-receipt.json
```

Clone the official repository and detach the exact pin before this command. The
boundary rejects wrong origin/commit, dirty or ignored source additions, existing
build/receipt destinations and unexpected publish inventory. It runs upstream's
`dotnet publish -c Release -r RID -p:PublishTrimmed=true --artifacts-path DIR`,
never the uploader. The receipt binds origin/commit/clean source, SDK, command,
host/RID and **all** publish files and hashes, not just the executable:

- ModUploader executable and PDB;
- platform Steam native library and `steam_appid.txt`;
- template README, content README, image and listing JSON.

The portable verifier supports win-x64, linux-x64, osx-x64 and osx-arm64 inventories;
only an actually built RID is build evidence. win-arm64 is deliberately not admitted
by this wrapper because the upstream project selects `steam_api64.dll` for all
Windows RIDs without proving an ARM64 native tuple.

### Retained prepared candidate, no rebuild or receipt repair

```sh
npm run workshop:publish -- --prepared-root /PRIVATE/retained-layer3-checkout --prepared-receipt-sha256 PREPARED_RECEIPT_SHA --provenance-sha256 APPROVED_PROVENANCE_SHA --uploader-receipt /PRIVATE/uploader-receipt.json --uploader-receipt-sha256 UPLOADER_RECEIPT_SHA --mode create
```

**Default is preflight only.** It emits `READY_TO_PUBLISH_PRIVATE`, not publication.
The original Layer 3 checkout, proposal, build, staging and prepare receipts remain
in place. It must still be clean at its recorded prepare revision. The current
Layer 4 tool must be a descendant with only explicitly enumerated publication
tools/tests/docs and narrowly checked root script additions. All compiled source
identities and metadata still match. Original producer, prepare-tool and current
publication-tool revisions remain separate. No generic source-evolution bypass.

The read-only consumer verifies the complete Layer 3 receipt contract and explicit
receipt/provenance pins; original proposal/build inventory; DLL SHA/MVID through
the existing owner PE reader; runtime manifest; game identity; metadata/preview;
source/staged byte equality; complete Layer 2 receipt; exact two-file payload;
upstream clean source and complete uploader hashes. Drift requires investigation
or a new authorized preparation, never locally repairing hashes/receipts.

Local evidence lives under the original `workshop/.publication/` (deny-by-default
ignored): `ready-SHA.json`, `publication.lock`, and per-attempt directories holding
`attempt.json`, stdout/stderr, official `mod-uploader.log`, and either
`publication-receipt.json` or `unknown.json`. None is upload content. A READY file
is a snapshot only: execution always repeats all validations.

### Layer 4B: separate Human authorization, create/update and unknown outcomes

Only after separate explicit Human authorization, append
`--execute --authorize-readiness-sha256 EXACT_READY_SHA` to the same command.
This is the only real execution route. Hosted CI rejects real execution; tests
inject fake readers/processes through module APIs, never CLI bypass flags.

- **Create:** explicit `--mode create`, private listing, no accepted item and no
  `mod_id.txt`; invoke official `upload -w WORKSPACE` deliberately without ID.
- **Update:** explicit `--mode update --item-id ID`, matching accepted local
  publication history; any present `mod_id.txt` must agree. Always pass official
  `--id ID`. Missing mod_id never implies create; no automatic mode switching.
- The retained `mod_id.txt` is mutable uploader state, never source/candidate
  authority. No import or deletion of unknown state to enable another create.
- An exclusive publication lock and fsynced, append-only attempt identity precede
  invocation. Interrupted locks require manual investigation, not auto takeover.
  An incomplete attempt (including missing outcome after a hard kill) blocks any
  later invocation as unknown. No auto retry, compensation or item deletion.
- Timeout, crash, nonzero, invalid/missing item ID, undecodable output, missing
  final confirmation, input drift or warnings enter `PUBLICATION_OUTCOME_UNKNOWN`.
  Human must inspect Steam, item ID, mod_id and logs. Preserve all evidence.

One narrowly pinned local recovery exists for the exact upstream
`d7b7e6b16c413d5a124f474f9e5104ef01f76ab1` create attempt that exited at
`SteamAPI.InitEx()` with `Could not determine Steam client install directory`.
At that commit, `UploadWorkspace` returns immediately when `InitializeSteam()`
fails, before the logged-in path or `SteamUGC.CreateItem`. After reviewing the
historical attempt and independently confirming the pinned uploader source,
an operator may run the separate **local-only** command:

```sh
npm run workshop:reconcile -- --prepared-root /PRIVATE/retained-layer3-checkout --prepared-receipt-sha256 PREPARED_RECEIPT_SHA --provenance-sha256 APPROVED_PROVENANCE_SHA --uploader-receipt /PRIVATE/uploader-receipt.json --uploader-receipt-sha256 UPLOADER_RECEIPT_SHA --attempt-id EXACT_ATTEMPT_ID
```

It revalidates the prepared candidate, uploader source and complete inventory,
the original ready/attempt/UNKNOWN bytes and the **entire exact** stdout,
stderr and official log. Missing or additional output, an item ID, conflicting
success, different source or any byte drift keeps UNKNOWN. It never contacts
Steam, launches the uploader, rewrites the original files or retries. A proven
case appends `pre-mutation-reconciliation.json` with
`PUBLICATION_FAILED_BEFORE_REMOTE_MUTATION`, the original evidence hashes and
an explicit local-control-flow-only evidence level. Every subsequent preflight
rechecks that proof; a later change to historical evidence blocks again.
The historical attempt remains visible, and a new publication attempt still
requires a **new** exact readiness SHA and separate Human authorization.
Other ambiguous or possibly post-`CreateItem` outcomes remain UNKNOWN and
retry-forbidden unless the separately audited success proof below applies.
This is not independent Steam remote readback or a claim that
the Steam account/item was examined.

Upstream can create an item before failing later. Setters may warn yet return a
successful final exit. Therefore confirmation requires exit 0, no warning/error
output, the exact upstream success line in both current stdout and current log,
matching valid mod_id, and unchanged immutable inputs. Any warning (including a
legal-agreement warning) requires Human review. The wrapper never accepts legal
terms or automatically opens/login Steam itself; upstream upload can open Steam's
item overlay only in separately authorized 4B.

#### Narrow native stderr classification and historical success reconciliation

Empty stderr is accepted. For the pinned uploader and native Windows library
SHA `eb17909a76668cf9ae0b92a618a34a50f6c73d3a6787cb4dd8ce36a8b10bfb75`
(317080 bytes), the only additional accepted stderr is exactly two CRLF lines:
`Setting breakpad minidump AppID = 2868840`, then
`SteamInternal_SetMinidumpSteamID:  Caching Steam ID:  <17-digit-ID> [API loaded no]`.
Both format strings were verified in that DLL; the variable is decimal Steam ID
diagnostic data, not an item ID or authorization. No whitespace trimming, extra
line, alternate app ID, undecodable byte, warning/error/fatal/crash text or unknown
output is accepted. This classifier does not prove upload: exit 0, no signal/error,
no upstream ANSI warning/error, matching exact final success lines in stdout and
the official log, matching mod_id and immutable candidate checks remain required.
The unconditional upstream terms-of-service `Info` notice is not its separate
yellow legal-agreement warning. No terms acceptance is automated.

The earlier wrapper at `5a26e77014e264f9d946026e73b8abb4b00524ed` rejected all
nonempty stderr. A **local-only** recovery can append
`successful-publication-reconciliation.json` without changing its original
UNKNOWN or creating a fake historical publication receipt:

```sh
npm run workshop:reconcile -- --kind uploader-success --prepared-root /PRIVATE/retained-layer3-checkout --prepared-receipt-sha256 PREPARED_RECEIPT_SHA --provenance-sha256 APPROVED_PROVENANCE_SHA --uploader-receipt /PRIVATE/uploader-receipt.json --uploader-receipt-sha256 UPLOADER_RECEIPT_SHA --attempt-id EXACT_ATTEMPT_ID --evidence-sha256 AUDITED_EVIDENCE_INVENTORY_SHA --human-observed-item-id EXACT_ITEM_ID
```

Use the Human flag only after the operator reports that exact item's title,
matching preview and Private visibility on Steam. It records a separate
`human_reported_steam_page_not_api_readback` observation, never machine readback.
Neither the report nor mod_id alone establishes accepted identity.

The audit pin is SHA256 of UTF-8 `JSON.stringify` of the filename-to-SHA256 object
in this order: `attempt.json`, `unknown.json`, `stdout.log`, `stderr.log`,
`mod-uploader.log`, `steam_appid.txt`. Audit those original bytes before approving
the pin; it is not a newly manufactured publication approval. Recovery verifies
the exact old wrapper source hash, its original readiness, current unchanged
prepared/uploader identity and complete success contract. Since the legacy
wrapper did not persist a separate process-result file, exit zero is explicitly
proved by its pinned assertion ordering and exact original stderr failure, not
presented as a contemporaneous process receipt. Unknowns from another failure or
wrapper version are not admitted. This intentionally narrow historical recovery
is not a generic override for failed uploads.

`PUBLICATION_RECONCILED_AS_UPLOADER_SUCCESS` binds the original candidate,
readiness, all historical file hashes, stderr classification, exit-proof basis,
item ID and separate Human observation. Every later preflight recomputes the
proof and rejects tampering. It establishes accepted local item identity, rejects
another create and permits only explicit matching update intent. Update still
requires a new readiness SHA and separate Human execution authorization.
Reconciliation itself has no uploader/network call and never retries. Original
UNKNOWN evidence remains visible; unrelated unresolved UNKNOWN still blocks.

Success evidence is explicitly `PUBLICATION_CONFIRMED_BY_UPLOADER`: it binds the
attempt, all prepared/uploader identities, item ID, operation, requested private
visibility and log hash. It is **not** an independent remote visibility readback,
Workshop subscription, install/load, Human or gameplay qualification. Requested
visibility and remotely observed visibility are not conflated.

`node --test tools/workshop-publish.test.mjs tools/workshop-uploader.test.mjs`
uses only synthetic non-PE fixtures and fake uploader responses/processes. Root
boundary tests also check generated/attempt/receipt/log ignore policy. Layer 4A
stops at READY; no Steam upload, item create/update, deploy, game launch, merge or
stack retarget. Rollback reverts the tooling only; retain original approved bytes
and all attempt/unknown evidence. It cannot undo an external publication.
