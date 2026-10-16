# GLOBAL P1 submitted-candidate acceptance — 2026-10-16

Submitted candidate: PR188, `be67e7a103133ee8270030ffe6a954905722053e`, parent PR187
`ef79a994f1071e423e241ef2642587bfec143e67`. Verdict: **changes required**. This
source audit does not erase successful historical runs or qualify a replacement.
[The active plan](../plans/P1_FORWARD_2026-10-15.zh-CN.md) owns the complete goal.

## Independently reproduced source findings

The semantic reviewer compiled the submitted SDK and Runtime into private temporary
outputs, inspected the actual reducer/comparator/consumers, and ran production-code
counterexamples. The lead inspected those source paths and scripts and independently
reran both reproducer sets. No game or native binding was simulated as qualification.

| Case | Submitted behavior | Required correction |
| --- | --- | --- |
| Combat with no advertised required Reads | complete=true; deck/piles/map unsupported | Required coverage must not be defined only by advertised Reads. |
| Native multi-select offers cancel and deselect | Both removed; complete=true | Preserve meaningful decisions; mechanical fold needs qualification. |
| Card reward without a legal completion action | Local decline leaves empty unsupported catalog | Admit irrevocable decline only with valid obligation/disposal/exit semantics. |
| Different enemy intent or draw-pile contents | Decision comparison reports matching | Compare complete public information and relations. |
| Different same-definition target or declined reward group | Choice/commitment comparison reports matching | Preserve instance multiplicity and one global alias bijection. |
| Singleton map action | Auto forced solely by singleton and kind | Native mandatory/no-other-choice qualification or leave choice to Agent. |
| Two run-deck Read instances | Last silently replaces first | Preserve instances or explicitly reject unsupported multiplicity. |
| Reward preview changed to upgraded same-definition card | Fold still selects | Verify complete relevant preview identity and original group transition. |

Existing shipped reducer tests passed 14/14 during independent inspection. These
passes do not cover the above counterexamples. A first malformed exploratory fold
fixture was correctly rejected; the corrected strict-decoder-accepted fixture
reproduced the actual fold defect. Failed exploratory attempts remain retained.

The product reviewer inspected all nine submitted runner/record modules and ran
actual Node child-process fixtures against the submitted production dist. The lead
inspected the scripts and source and independently reran the first set in a new
private directory, preserving the reviewer's originals:

| Case | Lead reproduction | Meaning |
| --- | --- | --- |
| 150ms total budget, chooser replies after 250ms | One step submitted, then budget censor at about460ms | Expiry does not fence the awaited choice before mutation. |
| Stop during chooser wait | No step, but waits for reply and performs observe before close | Stop admission exists but does not interrupt the wait. |
| Receipt delivery=banana | Result completed and natural terminal | Receipt union/binding is not strictly validated. |
| Missing Agent executable | No reset/snapshot/Read capture | Runner log is not independent no-Agent environment recording. |
| Header-only or truncated original archive | identical, zero/one observations | Reconstruction is not archive completeness verification. |
| All P1 observation rows removed | identical, zero observations | Omitted output obligations are not discovered from source events. |
| Agent outgoing digest changed | identical | Actual offered bytes are not verified by conversion. |
| Recorded core identity changed | identical with core.same=false | Equality and producer/version acceptance must remain distinct. |

The modified archives were disposable copies of an indexed original whose SHA256
matched; no original was changed. The first source reproduction imports the
submitted production modules, not the in-progress repair. No actual game ran.
Additional independent product checks found raw UTF8/framing normalization,
post-result cleanup failure absent from completion status and Stop shrinking the
planned denominator. Their source was inspected; further lead re-execution and
repair regressions remain required. Existing shipped runner tests passed7/7.

## Native and existing runtime evidence

The native reviewer inspected exact native mechanisms and candidate provenance,
verified retained game/Managed SHA and MVID, and traced the production projections.
Findings include selector hand/pile omissions, invalid multi-select preview verb,
Reference potion-discard omission, reward-alternative loss, missing treasure
mapping and missing Managed relic-choice producer. Lead exact native/source and
repair verification remains a separate acceptance step. An existing synchronous
DeckEnchant scope is plausible at the inspected native seam; it is not general
native, installed or runtime qualification.

Original successful Managed/Reference records remain useful. Independent sample
hash/reconstruction/cleanup checks cover selected records, not every suite row.
The lead separately verified the selected Managed original's index hash while
reproducing archive failures. The reported5/6 paired decision matches used the
lossy signature above and therefore do not establish complete P1 equivalence.

PR187's prior full execution receipt was accepted separately at its exact head;
fresh GitHub refs match. PR188's run38847727149 was still running at audit start.
Neither CI state proves P1 semantics, native recording sufficiency or product
integration. No PR was merged/closed and no new game/install/training was performed
by this initial audit. Local scripts, original logs and reproduction receipts are
retained in the private `p1-global-acceptance-2026-10-16` evidence package; public
regressions belong with their repaired owners rather than copied raw runtime data.


## Approved foundation integration and subsequent bounded source checks

User separately approved PR187 at ef79a994 on2026-10-16. Fresh GitHub head/base/checks
matched the independently reviewed candidate; normal merge41e62cd054f960121129df5d174925846484045b
retains02636a941519ed2db68f0ca46907ef69becc732a and ef79a994f1071e423e241ef2642587bfec143e67
as its two parents. PR188 remains draft/open at be67e7a1, retargeted to develop without
changing its submitted head. No PR188 merge approval is implied.

CI188 run38847727149 subsequently ended with two failed Windows jobs,45/48 owner
stages passing. Two URL.pathname package paths are definite source portability defects;
the two renewal fixtures fail before their intended Await/Close injection point. The
Python access violation requires separate bounded diagnosis; a traceback location alone
is not its root cause. Original ZIP hashes/CRCs and70 indexed files were independently checked.

Shared identity correction ca3897ba was normally integrated and independently reviewed.
Root ran64 SDK tests plus the four original counterexamples: unknown extension text
stays literal, ordered selector identity is unaffected by unordered Draw traversal,
anonymous deck/Draw members do not join through private pointers, and ordered discard
identity persists. These are synthetic production-code source/test checks, not native
qualification or proof of the full global information boundary.

## Codex round closeout for Claude audit and continuation

The user subsequently requested complete engineering closeout of this round, with
Claude auditing and continuing the remaining pre-training P1 work. This is not
acceptance of GLOBAL P1 or permission to freeze an incomplete protocol.

The local integration topic normally merged native producer7908da82, Source4
engine4411e4f3 and converter32429f03, and captured-grid packetba9a18a6. Subsequent
Root integration corrections align is_native_skip, require the actual reward
subscription fact, preserve the still-unimplemented stop_relic_refresh opportunity
as an explicit P1 missing item, and bind the source RewardButton's original verb to
the PE verb only through its exact mechanism/operands. The independent source
conversion review passed36 focused tests and8 probes; actual retained Core golden
CLI remains complete=false/actions0 with original trace unchanged.

The three reward Runtime/replay findings were independently closed at91f3ce5d:
all three unchanged old wrong-behavior assertions fail on the repair, the separate
correct-behavior probes pass4/4, two real interrupted archives pass the Python
verifier and a resealed post-Human false completion is rejected. This is actual
HTTP/stdio/source evidence, not native execution qualification. Renewal fixture
repairde9089c2 preserves400/1500/5000ms limits and arms failure only after the real
Await/Close/held-controller target exists; its9 tests pass locally and non-author
static review accepts the narrower timing claim.

Root's integrated exact-game Host-focused check passes122 tests after correcting
an initially omitted STS2GameDir test reference. Final SDK full check passes430
with typecheck/build/package checks. Integration gates also exposed a detached
Source stage DTO incorrectly located in the Host-service file: it is moved without
semantic change into the existing portable NativeLogical directory so the linked
real-store bridge compiles. The initial failed command and diagnostic are retained.
A TypeScript unknown-to-string check was corrected without changing admissible
values. The first full Runtime check had an unsupported default Python3.9 path and
an obsolete synthetic Managed HUD; the supported interpreter plus explicit current
synthetic HUD fixes preserve production admission and all ownership assertions.
The two affected files then pass60 targeted tests. Final component/repository results
are recorded with exact final source and raw logs in the private handoff index.

Current capability gaps remain concrete: aggregate capture and null legacy submit
use different snapshot views; the neutral execution profile and early complete
selector/catalog/dispatch path are not connected. The seven-file revision2 WIP is
saved byte-for-byte separately, with its original base and first failed check;
it is not active source or a tested new API. CapturedGrid source tests do not qualify
its absent Source early caller. Cold pool/other hover families/affliction/active
CalculatedVar, complete Source conversion and Managed independent originals,
record-driven real comparison, operational GLOBAL import and installed product
acceptance remain open. stop_relic_refresh requires its real lowering/postcondition.

One new isolated Managed candidate was built for source/artifact identity inspection:
patch f416005f943fbc4b94e31cd2331228630a8b5b0417b5ee343503b5ff248b754a,
DLL f22a6de46f2f3e3db878715027854bb7c1f0fe7f21017a248996df3dab8649de,
MVID da613413-a0e8-4ca4-ad04-897ae5ad8dc5. The original candidate inspector
accepts its source patch, artifact and unchanged game assembly. This is a new
unqualified build; no game was started. Reflection type aliases repair compilation
against the Managed Godot stubs without changing rendering semantics. The previous
candidate tuple is retained, including the older historical tuple.

The final shared-core safety guard refuses enriched card details until their new
display-owner/parent identity fields have an explicit P1 projection. Its rename-only
regression preserves raw source bytes and rejects a false complete claim. This is
an additional open information-projection task, not support for those details.
Final GLOBAL HTTP/stdio/replay tests pass22/22. Runtime full test stage passes573
with3 skips; the original full command subsequently failed its package stage.
After lock/version-constant repair, build and installed-package smoke checks pass
separately. These composed results do not claim the original command was green.
Evidence's full component check passes381 tests.

No new game, install/load, Human collection, training or hosted CI was started
for this final assembly. Historical candidates, loaded
records, failures and original raw data remain unchanged. PR188's remote head is
still be67e7a1; local integration gates do not belong to that head. PR187 alone was
approved and merged. Final source/contract versions and BOM describe this local
review candidate, not a production release or permission to enter training.

Final additional component checks pass: Host Node359 with5 skips, Host Python29,
Annotator Core424, SourceNative61, portable NativeLogical178, Annotator tools55,
and Game Mod92. Exact-game Mod compiles with zero warnings/errors. SourceNative's
first no-restore attempt lacked assets; the restored command succeeds. The two
Host fixture repairs retain their original assertions and fill the actual separate
potion HUD/list of36 added patch files. Connector product manifests are synchronized
withrc.25. Its static contract guard now checks the complete three-format game-thread
switch; the boundary guard requires the exact game Harmony reference for linked
Native Foundation read qualification and still forbids direct Host Harmony ownership.
Three in-memory negative probes reject missing game-thread dispatch, a missing native
source link and direct Host Harmony use. No runtime source is changed by these probes.
The final private reviewer accepts the bounded closeout delta; full P1 remains open.

Root repository gates and project:closeout pass after synchronizing the Annotator
Mod manifest and narrowing obsolete dependency guards. Native Foundation permits
only named read qualifiers' GetPatchInfo inspection and the exact detached preview's
JsonIgnore exclusions, not patch ownership or JSON I/O. Host's exact public SDK
workspace dependency requires the existing exact bundler and external installed
package check. Negative regressions retain the excluded authority boundaries.
The closeout planner selects the full project gate for this cross-layer candidate;
that whole selected gate was NOT executed in this bounded handoff. Its successful
execution and fresh hosted/full integration checks remain required before merge
acceptance. The component/targeted checks above do not replace that requirement.
