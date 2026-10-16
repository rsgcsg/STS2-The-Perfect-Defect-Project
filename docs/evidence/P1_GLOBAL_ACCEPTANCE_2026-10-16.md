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
