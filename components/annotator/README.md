# STS2 Human Annotator

Annotator observes real native Human decisions without changing gameplay.
STS2 owns rules, legality and Commit; Connector owns fair-player state, Reads
and action bindings; Annotator owns exact process-local mapping, immutable
Human evidence and native correlation. STPD owns research admission/training.

## P1 native semantic recording

The `human-p1-native-decisions-1` profile reuses native Human staging, exact owners,
accepted actions and completion callbacks. The same Connector snapshot build freezes
the Human-time `public-basis-1` and every advertised Read instance. Accepted choices,
native lifecycle, later public boundaries and recording issues are stored separately;
an unsubmitted pickup/cancel creates no accepted decision. It does not require
Source4 raw-input classification or the legacy canonical tracker.

Start this profile through the existing recording command API with an explicit
`p1_native_origin`. Closed originals use `audit-p1-native` / `pack-p1-native` in the
Annotator Tool; `tools/p1-native-to-p1.mjs --bundle PATH` calls the shared P1 core.
Engineering origin is retained and default packing does not attest Human origin.
While recording, `export_p1_native_prefix` produces a durable open prefix without
Close or changing pending actions. Its consumer requires the returned manifest hash.
See the [data contract](docs/DATA_CONTRACT.md#p1-native-semantic-profile).
Actual native recording and replay qualification remain separate from source support.

For daily P1 collection attach to the running installed game through the existing
Application API. Run `npm run recording:p1 -- status`, then use its exact runtime
and session (`none` before the first session):

```bash
npm run recording:p1 -- start --runtime RUNTIME --session SESSION_OR_none \
  --origin declared_human --setup /absolute/collection-config.json --receipt /absolute/new-start-receipt.json
npm run recording:p1 -- prefix --runtime RUNTIME --session SESSION \
  --setup /absolute/collection-config.json --receipt /absolute/new-prefix-receipt.json
npm run recording:p1 -- close --runtime RUNTIME --session SESSION \
  --setup /absolute/collection-config.json --receipt /absolute/new-close-receipt.json
```

`collection-config.json` contains the existing setup fields `recordings_root`,
`mod_provenance` and optional `game_dir`; use absolute paths to the persistent
collection root and selected build/installed provenance. The command consumes
`collectionSetup status`'s actual connected/bound runtime. Configure a different
root through the existing `collection-setup.mjs bind` while the game is stopped.
An isolated Host supplies its actual `connector_port` and `native_log_file` in
that same setup config. They select its transport/log; process generation, loaded
assembly and runtime identity checks remain required. Ordinary launches omit them.
No per-game rebinding, private workspace script, stdin command or engineering
deadline is needed. The client does not launch or shut down the user's game.
It saves the UUID/request before its single POST. A pending Close needs a later
read-only status check; unknown delivery needs owner investigation and is not retried.
After an automatic terminal seal, the next run retains P1 profile/origin and gets
its own session; manual Close/Pause still stops continuous recording.

A fixed Collection Tool release declares `p1_native_support` and ships that client,
the existing `audit-p1-native`/`pack-p1-native` Tool commands and the converter's
executable SDK/zod closure. Its new-output conversion entry is
`node RELEASE/p1/tools/p1-native-to-p1.mjs --bundle BUNDLE --output NEW_REPORT
--segments-output NEW_SEGMENTS`; opt in to the approved D interpretation with
`--selector-transactions native-selector-finalized-d-1`.
Originals, derived artifacts and research admission remain distinct. The Python
application/catalog/importer must consume this explicit capability and P1 format;
legacy Human import is not a substitute. Current integration status lives in CURRENT.

The opt-in `--target-projection native-timeline-targets-1 --targets-output NEW_FILE`
produces `sts2.p1/native-targets-1`: one original fact timeline with explicit native
effects/missing spans, complete N inputs/labels, separately qualified adjacent
next edges and same-branch observed end targets. This is a different declared
consumer purpose from episode segments. It preserves the original timeline without
claiming a complete P1 action chain or memory across missing facts. The current
target interpretation treats exactly classified `stop_relic_refresh` reward returns
as continuous: their actual changes and original checkpoints remain in the timeline,
but they do not independently disable `next` or require memory reset. No browsing
N label is added. Other effects, incomplete inputs and unknown actions still block
continuity. Select the exact fixed Tool release when reproducing this interpretation;
older target artifacts retain their original masks. The default,
segments03 and fixed historical training inputs retain their original meaning.

## Canonical profile and existing consumers

Human observation **H** is separate from execution **S + A(S)**. Native
acceptance, execution, Commit and successor proof are separate facts. Nested
inputs are first-class decisions with their own evidence and exact parent/root
lineage; independent native choices retain their real native context.

```text
native Human input -> exact native owner/binding -> semantic trace 4
  -> sole SemanticBoundaryTracker -> canonical transition 3
  -> current canonical export / session bundle 3 -> Evidence verification
  -> version-pinned external consumer
```

Frames, execution catalogs and required Reads are immutable content-addressed
objects. Missing ownership, state, membership, successor or persistence stays
failed closed. No later frame, global current-root lookup, timer, native retry,
input serialization gate or second legality engine supplies missing proof.
Pending means accepted with no terminal disposition; ordinary native cancellation
is separate from unresolved recording failure.

The optional `decision-record-2` projection and explicit compatibility bundle2
remain for real existing consumers. They omit decisions they cannot represent
and never count all successful decisions. Historical readers preserve original
schema meaning; no old evidence is rewritten or upgraded. See
[Data contract](docs/DATA_CONTRACT.md) and [Session bundles](docs/SESSION_BUNDLES.md).

## Operating and auditing

Use the visible **Platform** launcher, then Human Recorder. Start a new isolated
session before native run launch. Minimize shows recorded canonical decisions,
real failures and the latest three entries; expand for lifecycle, health,
coverage and exact evidence. Missing/incomplete failure accounting shows `—`,
not zero. K has no Recorder shortcut.

From the Platform root:

```bash
npm run verify:loaded
npm run annotator:audit -- /absolute/session-directory
npm --prefix components/annotator run audit:native-semantic -- /absolute/session-directory
npm --prefix components/annotator run calibrate:semantic-training -- /absolute/session-directory
npm run annotator:pack-session -- /absolute/session-directory --worker collector-id --campaign candidate-id --attest-human-origin \
  --output /absolute/bundle-directory
```

The exact pack command/options are owned by [Session bundles](docs/SESSION_BUNDLES.md).
Structural audit, canonical status, transport verification and Human Full-Run
qualification are distinct. Zero compatible invalid records does not prove no
lost input. No unchanged-artifact or historical test covers an unseen family.

## Development and support

Use `npm run check` and `npm run check:exact-game` at root. Build/install/load
only an exact clean candidate through Game Mod lifecycle. Keep raw data, saves,
game files, local receipts and credentials outside Git. Automatic upload requires an explicitly configured fixed campaign and
operator attestation through the [Evidence delivery service](../evidence/DELIVERY.md). Distribution, private incident collection and versioned fixes are
specified in [Support and distribution](../../docs/ANNOTATOR_COLLECTION.md).

Read [Status](docs/STATUS.md), [Architecture](docs/ARCHITECTURE.md), the
[Document map](docs/DOCUMENT_MAP.md) and [AGENTS.md](AGENTS.md). Dated evidence
under Platform `docs/evidence` and the component's
[historical evidence](docs/LIVE_EVIDENCE_2026-08-22.md) records predecessor facts.

P1 combat input source retention also accepts a complete `potion_popup` whose
frozen public context is combat. This retains the original Human-time PB and
exact accepted native operands for queued card/End Turn inputs; it does not add
public delivery actions or infer execution legality. Hidden/external-controller,
incomplete-read and noncombat frames remain ineligible. Older sessions with
missing Human frames keep their original gaps; an execution PB is not a repair.
