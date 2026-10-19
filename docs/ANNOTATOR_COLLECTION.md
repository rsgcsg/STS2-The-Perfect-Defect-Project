# Annotator collection and incident response

Distribute one versioned Platform package with its BOM, supported exact game
identity, component source/contract digests and assembly SHA/MVID. The collector
starts only after the installed and loaded identities agree. Record each
candidate/cohort separately; a new DLL or source revision receives no Human
qualification from its predecessor. Retain the previous deployment for rollback.

## Default project workflow

For SpireAgent collection, use the [versioned STPD workbench release](https://github.com/rsgcsg/STS2-The-Perfect-Defect/releases)
and its [single project handoff](https://github.com/rsgcsg/STS2-The-Perfect-Defect/blob/main/docs/B_PIPELINE_HANDOFF.md).
That document owns installation, account/computer binding, campaign setup, cloud viewing,
operator deployment and maintenance. This guide owns native capture, the Platform tool
contract and recording incidents; it does not duplicate cloud account or research rules.

The release combination binds the Mod/BOM/game, fixed-tool release ID, Platform Evidence
pin and STPD source/lock. Preserve its previous combination for rollback. Download/check the
published inventory before installing; a newer Git branch is not an automatic upgrade.
The game must load one qualified Mod. The external workbench then supervises the pinned
Evidence delivery service. A project account or device approval grants no Human-origin or
upload consent: the operator still configures an initially empty, dedicated campaign and
obtains that campaign's attestation before opening delivery.

Daily use is **open the configured workbench -> record -> Recorder Close -> inspect receipt**.
Ordinary collectors do not manually audit/pack every session or clone Platform. The owning
audit/pack commands below remain the developer/manual diagnosis route. Closing the browser
does not stop delivery; reopening the same workbench configuration after a reboot resumes
sealed work. Never repoint the campaign at historical recordings to make missing data appear.

The local **采集记录** page shows the queue and linked remote receipt. The cloud page shows
received data and its verification; it cannot see an offline computer's pending queue. Keep
recording quality, delivery status and research admission distinct. Zero real failures is an
owner disposition count, not a promise that every game surface has been exercised.

## Local capture and handoff

Recording and diagnostics stay local by default. For distributed collection,
configure `recording_root` to a persistent user-owned directory and retain
verified bundles outside temporary engineering worktrees before cleanup. The existing Platform launcher
opens Recorder; enable recording before a fresh native run. Keep the full session
through natural terminal and close Recorder normally. Do not pause Recorder,
reload/resume the run or use the console inside the uninterrupted Full-Run qualification gate.
Those paths may have separate bounded tests. Close must finish successfully;
a journal close entry alone is insufficient for close-capability-1 sessions.

Run the owning audit before collection. Preserve the entire original directory,
including failed/unresolved decisions, diagnostics, journal, content objects and
performance profile. Do not delete bad rows to create an all-valid corpus.

```bash
npm run annotator:audit -- /absolute/session-directory
npm run annotator:pack-session -- /absolute/session-directory \
  --worker collector-id --campaign candidate-id --attest-human-origin \
  --output /absolute/new-bundle-directory
npm run evidence -- verify-human-bundle /absolute/new-bundle-directory
```

Use `--attest-human-origin` only with the actual operator's attestation. It is an
explicit non-machine-verifiable statement, not something inferred from audit.
Developer CLI packing requires a clean producer checkout. Automatic delivery
uses a [fixed collection-tool release](../components/evidence/DELIVERY.md), built
from clean source and verified by an independent release ID; it does not depend
on the current developer checkout being clean. Both require a closed, structurally valid
session. A zero-canonical session containing valid failed-decision evidence can
still be bundled. A truncated/corrupt/unclosed session cannot be made verified:
retain its raw bytes and failed audit for explicitly identified incident transfer.

Bundle 3 is the current canonical-first format. Existing record-2 consumers use
explicit `export-compatibility` / `pack-session-compatibility` via Annotator CLI;
that projection is not a complete Full-Run dataset. Pin the consumer contract.
The version-pinned STPD bundle3 adapter, dataset eligibility policy and training/evaluation
checks belong to STPD. Their independent release and admission checks never change the
original recording or turn receiver verification into research qualification.

## P1-native offline conversion and optional segments

The current P1-native lane retains accepted input H, execution S/A(S), lifecycle
and complete public-basis originals in its own sealed bundle. It is separate from
bundle3 and historical Source4. Its owning reader verifies the immutable inventory
before the shared P1 converter interprets it:

```bash
node tools/p1-native-to-p1.mjs --bundle /absolute/p1-native-bundle \
  --run-id run-0001 --output /absolute/new-conversion.json \
  --segments-output /absolute/new-segments.json
```

Outputs must be new paths. Omitting `--segments-output` keeps the existing
whole-conversion behavior. An open administrative export instead uses `--prefix`
and its exact `--manifest-sha256`; it never qualifies as a sealed whole run.

`sts2.p1/native-segments-1` records the original session/timeline/run, source content
identity, original basis references, profile/revision, gaps, discarded choices and
each segment's start/end reasons. A segment starts only at a real complete basis
with complete shared-core observation and no unfinished native parent, reward
commitment or selector continuation. It carries the actual `start_protocol_state`
from the same conversion, rather than resetting the protocol. Unresolved inputs
and stateful projection exceptions cut the segment; unfinished inputs cut through
the observed tail. Pure browsing and qualified cancellation produce no N labels.

Each point keeps `observation`, `chosen` and `mask`. N names only an exact choice
in that point's catalog; next never crosses a gap or segment; end refers only to
the real observed terminal on the same branch. Later recovery segments are D
data with explicit source exceptions. The converter leaves `complete`,
`whole_run_recording_complete` and `trajectory` unchanged by the optional output.

The research loader must preserve these masks, boundaries, version/branch lineage
and protocol state, and bind source and derivation hashes in its snapshot receipt.
`training_loader_consumed=false` means the converter has made no claim that any
training consumer read the artifact. Loader ingestion, research admission, online
Reference behavior and cross-Host conformance remain separate evidence.

## Source4 P1 conversion of one recorded run

Source4 is an explicit recording format, separate from the Human bundle3 lane
above; its [capture/packer owner](../components/annotator/docs/SOURCE_SESSION_PROFILE_V3.md#source4-originals-and-offline-p1-interpretation)
retains menus, native lifecycle boundaries and all recorded inputs. Start Source4
before starting a new native game when a fresh-run witness is required. Close and
verify/pack the full source directory; preserve the resulting raw bundle.

The default converter still interprets every original capture as a **bounded
recorded prefix** (report v2). A menu that is outside P1's active-run contract
therefore remains unresolved in that mode. To interpret one original run, copy
its exact `context.game_continuity_id` from the bundle's
`raw/source-attachment-epochs.jsonl` and select it explicitly:

```bash
node tools/source4-to-p1.mjs --bundle /absolute/source4-bundle \
  --python /absolute/python-3.11-or-newer --host-kind reference \
  --run-continuity ORIGINAL_GAME_CONTINUITY_ID --output /absolute/new-report.json
```

This produces report v3. Every original capture stays in `captures`, annotated
with `scope.disposition` (`inside`, `outside`, `unresolved`), reason and original
epoch/basis/boundary references; all original inputs stay in `native_trace`.
`outside_scope` is not a P1 observation or choice, and its `complete` is null.
Other native runs are outside the selected scope. Within the selected epoch,
only a complete public **no-active-run** fact before the exact native Launch
watermark/input ordinal can establish a prelude; a scene name or failed P1
reduction cannot. Early active-run callbacks before the Launch postfix remain
inside. Missing/partial facts, unknown inputs and every continuation after run
entry, including terminal/summary publications, remain inside or unresolved.
The converter does not truncate at the native terminal watermark.

For multiple runs, invoke the converter once per explicit original continuity;
there is no automatic last-run selection. An absent or non-unique continuity is
an error. The original v2 mode remains available by omitting the option.

Report `complete` concerns only the declared bounded P1 window. The separate
`whole_run.complete` also requires exact new setup plus the same continuity's
native Launch, a later native terminal and complete P1 terminal information,
without a recording pause, original gap or unknown delivery. Its conditions and
missing causes remain explicit. Saved/unknown origins, mid-run attachment and a
complete short prefix never acquire a whole-run claim. These fields do not
qualify Human origin, gameplay coverage, third-act/win requirements or runtime
equivalence; those gates still require their own actual evidence.

## Reports and privacy

A bug report includes package/component/game identities, runtime/session IDs,
expected input and observed disposition, approximate sequence/time, audit errors,
reproduction steps, and whether console/reload/modset changes occurred. Include
bundle content ID and verifier version when available. The issue template asks
for this metadata; do not put raw sessions, saves, user paths or credentials in
public issues. An operator may choose a private issue attachment or configured
Evidence receiver. Automatic upload is opt-in through the fixed-campaign
Evidence delivery service; credentials stay outside config and evidence.
The existing typed Evidence transfer verifies checksums and quarantines rejected
content before atomic promotion. Delivery success is not research admission.

## Detect and route a problem

At the end of each collection, compare the local sealed session with its final receiver
receipt and inspect the owner quality counters. Keep a visible failed/pending record visible;
never clear an outbox, delete raw evidence or repack under a new ID just to remove an error.
The cloud **系统** page is an observation, not an independent outage alarm. The Hub operator
checks health, queue age, disk and backup freshness using the STPD runbook; external alerting
is a separate, explicitly configured and tested service.

| Observed problem | First owner and next action |
| --- | --- |
| Accepted Human decision failed-closed/unresolved, missing parent or native boundary | Platform: retain complete session, exact Mod/game/tool identity and native audit; scope affected evidence before repair |
| Unclosed/corrupt session | Platform: retain raw bytes and failed audit; do not forge Close or upload it as a verified bundle |
| Local seal exists but packing/verification fails | Platform Evidence/tool: retain the original seal, tool release and typed incident |
| Hub authorization blocked | STPD account/device operator first, then Platform's explicit same-device stopped-worker recovery; keep bundle/upload IDs |
| Signed object upload rejected, network failure or remote verification pending | STPD Hub/storage operations and the typed delivery incident; distinguish storage403 from Hub auth403 |
| Cloud receipt exists but Dataset/model use is rejected or absent | STPD research admission; cloud acceptance did not authorize training |
| UI disagrees with an authoritative receipt | Owning UI projection: preserve exact IDs/observation time, compare owner status, fix presentation without changing evidence |

Public reports include reviewed identities, aggregate counts and a minimal reproduction.
Private raw evidence or account details travel only through an authorized private channel.
A release may be withdrawn from the recommended combination while its failed evidence and
old checksums remain immutable and available for diagnosis.

## Correctness incident and repair

1. Preserve the original artifact and evidence hashes. Reproduce against exact
   source/native identity; determine the first incorrect fact and owning layer.
2. Quarantine the affected cohort or affected decision families for downstream
   admission. Record known affected versions and explicit uncertainty. Do not
   mutate archived evidence or label every historical session invalid by guess.
3. Implement the owning fix on a new short-lived branch with the lowest-cost
   faithful regression, full component/root checks and exact-game gates. No
   later-frame workaround or automatic retry of unknown gameplay delivery.
4. Build one new exact package, cold-load, verify and run the bounded regression
   Human gate. Re-run Full-Run qualification when the shared causal path changed.
5. Publish the new version and incident disposition; consumers explicitly update
   their pin. A schema-specific re-audit can produce a new report referencing the
   original hashes, never rewrite the original recording or its old report.

After merge, verify the normal merge preserves path-scoped component identity
and the tested tree. Record exact merge/CI/package/load receipts before claiming
release readiness. Future defects use the same route; merge is not a claim of
exhaustive native surface coverage, crash recovery or scientific validity.
